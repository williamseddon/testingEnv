import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from wordcloud import WordCloud, STOPWORDS
import matplotlib.pyplot as plt
from googletrans import Translator
import io
import asyncio
import re
import html
import os
from openai import OpenAI  # LLM

# Optional: robust text fixer for mojibake/emojis (safe if not installed)
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# ---------------------------------------
# Page config
# ---------------------------------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# Global CSS (compact sidebar + highlight + nicer cards)
st.markdown(
    """
    <style>
    .block-container { padding-top: 0.6rem; padding-bottom: 1rem; }
    section[data-testid="stSidebar"] .block-container { padding-top: 0.2rem; padding-bottom: 0.6rem; }
    section[data-testid="stSidebar"] label { font-size: 0.95rem; }
    section[data-testid="stSidebar"] .stButton>button { width: 100%; }
    mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
    .review-card { border:1px solid #e6e6e6; background:#fafafa; border-radius:10px; padding:16px; }
    .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
    .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:0.95rem; }
    .badge.pos { background:#CFF7D6; color:#085a2a; }
    .badge.neg { background:#FBD3D0; color:#7a0410; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Title
st.markdown(
    """
    <h1 style="text-align: center; margin-bottom:.25rem;">🌟 Star Walk Analysis Dashboard</h1>
    <p style="text-align: center; font-size: 16px; color:#666; margin-top:0;">
        Insights, trends, and ratings — fast.
    </p>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------
# Utilities
# ---------------------------------------
def style_rating_cells(value):
    if isinstance(value, (float, int)):
        if value >= 4.5:
            return "color: green;"
        elif value < 4.5:
            return "color: red;"
    return ""

def clean_text(x: str) -> str:
    """Fix common mojibake like â€™, stray CP1252/UTF mixups; preserve emoji."""
    if x is None:
        return ""
    s = str(x)
    if _HAS_FTFY:
        try:
            s = _ftfy_fix(s)
        except Exception:
            pass
    if any(ch in s for ch in ("Ã", "Â", "â", "ï", "€", "™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                s = repaired
        except Exception:
            pass
    fixes = {"â€™": "'", "Â": ""}
    for a,b in fixes.items():
        s = s.replace(a,b)
    return s.strip()

def apply_filter(df: pd.DataFrame, column_name: str, label: str, key: str | None = None):
    """Renders inside the *current* container (expander) — no st.sidebar.* calls here."""
    options = ["ALL"]
    if column_name in df.columns:
        col = df[column_name].astype("string")
        options += sorted([x for x in col.dropna().unique().tolist() if str(x).strip() != ""])
    selected = st.multiselect(f"Select {label}", options=options, default=["ALL"], key=key)
    if "ALL" not in selected and column_name in df.columns:
        return df[df[column_name].astype("string").isin(selected)], selected
    return df, ["ALL"]

def collect_unique_symptoms(df: pd.DataFrame, cols: list[str]) -> list[str]:
    vals, seen = [], set()
    for c in cols:
        if c in df.columns:
            s = df[c].astype("string").str.strip().dropna()
            for v in pd.unique(s.to_numpy()):
                item = str(v).strip()
                if item and item not in seen:
                    seen.add(item)
                    vals.append(item)
    return vals

def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    s = (
        filtered_df[cols]
        .stack(dropna=True)
        .astype("string")
        .map(clean_text)
        .str.strip()
        .dropna()
    )
    s = s[s != ""]
    if s.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    unique_items = pd.unique(s.to_numpy())
    results, total_rows = [], len(filtered_df)
    for item in unique_items:
        item_str = str(item).strip()
        if not item_str:
            continue
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0:
            continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean()
        pct = (count / total_rows * 100) if total_rows else 0
        results.append({
            "Item": item_str.title(),
            "Avg Star": round(avg_star, 1) if pd.notna(avg_star) else None,
            "Mentions": count,
            "% Total": f"{round(pct, 1)}%",
        })
    if not results:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)

def build_wordcloud_text(df: pd.DataFrame, cols: list[str]) -> str:
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return ""
    s = (
        df[cols]
        .stack(dropna=True)
        .astype("string")
        .map(clean_text)
        .str.strip()
        .dropna()
    )
    s = s[s != ""]
    return " ".join(s.tolist())

def highlight_html(text: str, keyword: str | None) -> str:
    safe = html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error:
            pass
    return safe

# ---- Translation helpers (robust to coroutine return) ----
async def _translate_async_call(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if asyncio.iscoroutine(res):
            res = await res
        return getattr(res, "text", text)
    except Exception:
        return text

def safe_translate(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if hasattr(res, "text"):
            return res.text
        if asyncio.iscoroutine(res):
            try:
                return asyncio.run(_translate_async_call(translator, text))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(_translate_async_call(translator, text))
                finally:
                    loop.close()
    except Exception:
        pass
    return text

def apply_keyword_filter(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if not keyword or keyword.strip() == "":
        return df
    verb_col = "Verbatim" if "Verbatim" in df.columns else None
    if not verb_col:
        return df
    verb = df[verb_col].astype("string").fillna("").map(clean_text)
    mask = verb.str.contains(keyword.strip(), case=False, na=False)
    return df[mask]

# ---------------------------------------
# File Upload
# ---------------------------------------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

if uploaded_file:
    try:
        st.markdown("---")
        verbatims = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")

        # Normalize known string columns
        for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
            if col in verbatims.columns:
                verbatims[col] = verbatims[col].astype("string").str.upper()

        # Numerics
        if "Star Rating" in verbatims.columns:
            verbatims["Star Rating"] = pd.to_numeric(verbatims["Star Rating"], errors="coerce")

        # Symptom columns
        all_symptom_cols = [c for c in verbatims.columns if c.startswith("Symptom")]
        for c in all_symptom_cols:
            verbatims[c] = verbatims[c].astype("string").map(clean_text)

        # Clean text + dates
        if "Verbatim" in verbatims.columns:
            verbatims["Verbatim"] = verbatims["Verbatim"].astype("string").map(clean_text)
        if "Review Date" in verbatims.columns:
            verbatims["Review Date"] = pd.to_datetime(verbatims["Review Date"], errors="coerce")

        # ---------------- Sidebar (all collapsed) ----------------
        st.sidebar.header("🔍 Filters")

        # Timeframe
        with st.sidebar.expander("🗓️ Timeframe", expanded=False):
            timeframe = st.selectbox(
                "Select Timeframe",
                options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"],
                key="tf"
            )
            today = datetime.today()
            start_date, end_date = None, None
            if timeframe == "Custom Range":
                start_date, end_date = st.date_input(
                    label="Date Range",
                    value=(datetime.today() - timedelta(days=30), datetime.today()),
                    min_value=datetime(2000, 1, 1),
                    max_value=datetime.today(),
                    label_visibility="collapsed"
                )
            elif timeframe == "Last Week":
                start_date, end_date = today - timedelta(days=7), today
            elif timeframe == "Last Month":
                start_date, end_date = today - timedelta(days=30), today
            elif timeframe == "Last Year":
                start_date, end_date = today - timedelta(days=365), today

        filtered_verbatims = verbatims.copy()
        if start_date and end_date and "Review Date" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[
                (filtered_verbatims["Review Date"] >= pd.Timestamp(start_date)) &
                (filtered_verbatims["Review Date"] <= pd.Timestamp(end_date))
            ]

        # Star rating
        with st.sidebar.expander("🌟 Star Rating", expanded=False):
            selected_ratings = st.multiselect(
                "Select Star Ratings",
                options=["All"] + [1, 2, 3, 4, 5],
                default=["All"],
                key="sr"
            )
        if "All" not in selected_ratings and "Star Rating" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[filtered_verbatims["Star Rating"].isin(selected_ratings)]

        # Standard Filters (render inside this expander)
        with st.sidebar.expander("🌍 Standard Filters", expanded=False):
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Country", "Country", key="f_Country")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Source", "Source", key="f_Source")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Seeded", "Seeded", key="f_Seeded")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "New Review", "New Review", key="f_New Review")

        # Delighters/Detractors
        detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
        delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
        existing_detractor_columns = [c for c in detractor_columns if c in filtered_verbatims.columns]
        existing_delighter_columns = [c for c in delighter_columns if c in filtered_verbatims.columns]
        detractor_symptoms = collect_unique_symptoms(filtered_verbatims, existing_detractor_columns)
        delighter_symptoms = collect_unique_symptoms(filtered_verbatims, existing_delighter_columns)

        with st.sidebar.expander("😊 Delighters & 😠 Detractors", expanded=False):
            selected_delighter = st.multiselect(
                "Select Delighter Symptoms",
                options=["All"] + sorted(delighter_symptoms),
                default=["All"],
                key="delight"
            )
            selected_detractor = st.multiselect(
                "Select Detractor Symptoms",
                options=["All"] + sorted(detractor_symptoms),
                default=["All"],
                key="detract"
            )
        if "All" not in selected_delighter and existing_delighter_columns:
            mask = filtered_verbatims[existing_delighter_columns].isin(selected_delighter).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]
        if "All" not in selected_detractor and existing_detractor_columns:
            mask = filtered_verbatims[existing_detractor_columns].isin(selected_detractor).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]

        # Keyword
        with st.sidebar.expander("🔎 Keyword", expanded=False):
            keyword = st.text_input(
                "Keyword to search (in review text)",
                value="",
                key="kw",
                help="Case-insensitive match in the Review text. Cleaned for â€™ → '"
            )
            if keyword:
                filtered_verbatims = apply_keyword_filter(filtered_verbatims, keyword)

        # Additional Filters (non-core, non-symptom columns — e.g., Hair Type)
        core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
        symptom_cols = set([f"Symptom {i}" for i in range(1,21)])
        additional_columns = [c for c in verbatims.columns if c not in (core_cols | symptom_cols)]
        with st.sidebar.expander("📋 Additional Filters", expanded=False):
            if len(additional_columns) > 0:
                for column in additional_columns:
                    filtered_verbatims, _ = apply_filter(filtered_verbatims, column, column, key=f"f_{column}")
            else:
                st.info("No additional filters available.")

        # Review list UI
        with st.sidebar.expander("📄 Review List", expanded=False):
            rpp_options = [10, 20, 50, 100]
            default_rpp = st.session_state.get("reviews_per_page", 10)
            rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
            rpp = st.selectbox("Reviews per page", options=rpp_options, index=rpp_index, key="rpp")
            if rpp != default_rpp:
                st.session_state["reviews_per_page"] = rpp
                st.session_state["review_page"] = 0

        # Clear-all at the bottom (so everything else is higher)
        st.sidebar.markdown("---")
        if st.sidebar.button("🧹 Clear all filters", help="Reset all filters to defaults."):
            for k in ["tf","sr","kw","delight","detract","rpp","review_page"] + \
                     [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()

        st.markdown("---")

        # ---------------------------------------
        # Metrics Summary
        # ---------------------------------------
        st.markdown(
            """
            ### ⭐ Star Rating Metrics
            <p style="text-align: center; font-size: 14px; color: gray;">
                A summary of customer feedback and review distribution.
            </p>
            """,
            unsafe_allow_html=True
        )

        total_reviews = len(filtered_verbatims)
        avg_rating = filtered_verbatims["Star Rating"].mean() if total_reviews else 0.0
        star_counts = filtered_verbatims["Star Rating"].value_counts().sort_index()
        percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
        star_labels = [f"{int(star)} stars" for star in star_counts.index]

        mc1, mc2 = st.columns(2)
        with mc1:
            st.metric("Total Reviews", f"{total_reviews:,}")
        with mc2:
            st.metric("Avg Star Rating", f"{avg_rating:.1f}", delta_color="inverse")

        fig_bar_horizontal = go.Figure(go.Bar(
            x=star_counts.values,
            y=star_labels,
            orientation="h",
            text=[f"{value} reviews ({percentages.get(idx, 0)}%)" for idx, value in zip(star_counts.index, star_counts.values)],
            textposition="auto",
            marker=dict(color=["#FFA07A", "#FA8072", "#FFD700", "#ADFF2F", "#32CD32"]),
            hoverinfo="y+x+text"
        ))
        fig_bar_horizontal.update_layout(
            title="<b>Star Rating Distribution</b>",
            xaxis=dict(title="Number of Reviews", showgrid=False),
            yaxis=dict(title="Star Ratings", showgrid=False),
            plot_bgcolor="white",
            template="plotly_white",
            margin=dict(l=40, r=40, t=45, b=40)
        )
        st.plotly_chart(fig_bar_horizontal, use_container_width=True)

        # ---------------------------------------
        # Country-Specific Breakdown
        # ---------------------------------------
        st.markdown("### 🌍 Country-Specific Breakdown")

        if "Country" in filtered_verbatims.columns and "Source" in filtered_verbatims.columns:
            new_review_filtered = filtered_verbatims[
                filtered_verbatims["New Review"].astype("string").str.upper() == "YES"
            ]

            country_source_stats = (
                filtered_verbatims
                .groupby(["Country", "Source"])
                .agg(Average_Rating=("Star Rating", "mean"), Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            new_review_stats = (
                new_review_filtered
                .groupby(["Country", "Source"])
                .agg(New_Review_Average=("Star Rating", "mean"), New_Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            country_source_stats = country_source_stats.merge(new_review_stats, on=["Country", "Source"], how="left")

            country_overall = (
                filtered_verbatims
                .groupby("Country")
                .agg(Average_Rating=("Star Rating", "mean"), Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            overall_new_review_stats = (
                new_review_filtered
                .groupby("Country")
                .agg(New_Review_Average=("Star Rating", "mean"), New_Review_Count=("Star Rating", "count"))
                .reset_index()
            )
            country_overall = country_overall.merge(overall_new_review_stats, on="Country", how="left")
            country_overall["Source"] = "Overall"

            def color_numeric(val):
                if pd.isna(val): return ""
                try:
                    v = float(val)
                except Exception:
                    return ""
                if v >= 4.5: return "color: green;"
                if v < 4.5:  return "color: red;"
                return ""

            def formatter_rating(v):  return "-" if pd.isna(v) else f"{v:.1f}"
            def formatter_count(v):   return "-" if pd.isna(v) else f"{int(v):,}"

            for country in country_overall["Country"].unique():
                st.markdown(f"#### {country}")

                country_data = country_source_stats[country_source_stats["Country"] == country]
                overall_data = country_overall[country_overall["Country"] == country]

                combined_country_data = pd.concat([country_data, overall_data], ignore_index=True)
                combined_country_data["Sort_Order"] = combined_country_data["Source"].apply(lambda x: 1 if x == "Overall" else 0)
                combined_country_data = combined_country_data.sort_values(by="Sort_Order", ascending=True).drop(columns=["Sort_Order"])
                combined_country_data = combined_country_data.drop(columns=["Country"]).rename(columns={
                    "Source": "Source",
                    "Average_Rating": "Avg Rating",
                    "Review_Count": "Review Count",
                    "New_Review_Average": "New Review Average",
                    "New_Review_Count": "New Review Count"
                })

                def bold_overall(row):
                    if row.name == len(combined_country_data) - 1:
                        return ["font-weight: bold;" for _ in row]
                    return [""] * len(row)

                styled = (
                    combined_country_data.style
                    .format({
                        "Avg Rating": formatter_rating,
                        "Review Count": formatter_count,
                        "New Review Average": formatter_rating,
                        "New Review Count": formatter_count,
                    })
                    .applymap(color_numeric, subset=["Avg Rating", "New Review Average"])
                    .apply(bold_overall, axis=1)
                    .set_properties(**{"text-align": "center"})
                    .set_table_styles([
                        {"selector": "th", "props": [("text-align", "center")]},
                        {"selector": "td", "props": [("text-align", "center")]},
                    ])
                )
                st.markdown(styled.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.warning("Country or Source data is missing in the uploaded file.")

        st.markdown("---")

        # ---------------------------------------
        # Delighters and Detractors Analysis
        # ---------------------------------------
        st.markdown("### 🌟 Delighters and Detractors Analysis")

        detractors_results = analyze_delighters_detractors(filtered_verbatims, existing_detractor_columns)
        delighters_results = analyze_delighters_detractors(filtered_verbatims, existing_delighter_columns)

        view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0)

        def _styled_table(df: pd.DataFrame):
            return df.style.applymap(style_rating_cells, subset=["Avg Star"]).format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"}).hide(axis="index")

        if view_mode == "Split":
            c1, c2 = st.columns([1, 1])
            with c1:
                st.subheader("All Detractors")
                if detractors_results.empty:
                    st.write("No detractor symptoms found.")
                else:
                    st.dataframe(_styled_table(detractors_results), use_container_width=True, hide_index=True)
            with c2:
                st.subheader("All Delighters")
                if delighters_results.empty:
                    st.write("No delighter symptoms found.")
                else:
                    st.dataframe(_styled_table(delighters_results), use_container_width=True, hide_index=True)
        else:
            tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
            with tab1:
                if detractors_results.empty:
                    st.write("No detractor symptoms found.")
                else:
                    st.dataframe(_styled_table(detractors_results), use_container_width=True, hide_index=True)
            with tab2:
                if delighters_results.empty:
                    st.write("No delighter symptoms found.")
                else:
                    st.dataframe(_styled_table(delighters_results), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ---------------------------------------
        # Reviews (translate, highlight, download all, pagination)
        # ---------------------------------------
        translator = Translator()
        st.markdown("### 📝 All Reviews")

        if not filtered_verbatims.empty:
            csv_bytes = filtered_verbatims.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="⬇️ Download ALL filtered reviews (CSV)",
                data=csv_bytes,
                file_name="filtered_reviews.csv",
                mime="text/csv",
                help="Exports all rows that match the active filters, regardless of pagination."
            )

        translate_all = st.button("Translate All Reviews to English")

        reviews_per_page = st.session_state.get("reviews_per_page", 10)
        if "review_page" not in st.session_state:
            st.session_state["review_page"] = 0

        def scroll_to_top():
            st.rerun()

        total_reviews_count = len(filtered_verbatims)
        total_pages = max((total_reviews_count + reviews_per_page - 1) // reviews_per_page, 1)
        current_page = min(max(st.session_state["review_page"], 0), total_pages - 1)
        start_index = current_page * reviews_per_page
        end_index = start_index + reviews_per_page
        paginated_reviews = filtered_verbatims.iloc[start_index:end_index]

        if paginated_reviews.empty:
            st.warning("No reviews match the selected criteria.")
        else:
            for _, row in paginated_reviews.iterrows():
                review_text = row.get("Verbatim", pd.NA)
                review_text = "" if pd.isna(review_text) else clean_text(review_text)

                translated_review = safe_translate(translator, review_text) if translate_all else review_text

                date_val = row.get("Review Date", pd.NaT)
                if pd.isna(date_val):
                    date_str = "-"
                else:
                    try:
                        if isinstance(date_val, (pd.Timestamp, datetime)):
                            date_str = date_val.strftime("%Y-%m-%d")
                        else:
                            parsed = pd.to_datetime(date_val, errors="coerce")
                            date_str = "-" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")
                    except Exception:
                        date_str = "-"

                display_review_html = highlight_html(translated_review, keyword)

                # nicer chips
                def render_chips(row, columns, css_class):
                    items = []
                    for c in columns:
                        if c in row and pd.notna(row[c]):
                            txt = clean_text(str(row[c])).strip()
                            if txt:
                                items.append(f'<span class="badge {css_class}">{html.escape(txt)}</span>')
                    if not items:
                        return "<i>None</i>"
                    return f'<div class="badges">{"".join(items)}</div>'

                delighter_message = render_chips(row, existing_delighter_columns, "pos")
                detractor_message = render_chips(row, existing_detractor_columns, "neg")

                star_val = row.get("Star Rating", 0)
                try:
                    star_int = int(star_val) if pd.notna(star_val) else 0
                except Exception:
                    star_int = 0

                st.markdown(
                    f"""
                    <div class="review-card">
                        <p><strong>Source:</strong> {row.get('Source', '')} | <strong>Model:</strong> {row.get('Model (SKU)', '')}</p>
                        <p><strong>Country:</strong> {row.get('Country', '')}</p>
                        <p><strong>Date:</strong> {date_str}</p>
                        <p><strong>Rating:</strong> {'⭐' * star_int} ({row.get('Star Rating', '')}/5)</p>
                        <p><strong>Review:</strong> {display_review_html}</p>
                        <div><strong>Delighter Symptoms:</strong> {delighter_message}</div>
                        <div><strong>Detractor Symptoms:</strong> {detractor_message}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # Pagination controls
        c1, c2, c3, c4, c5 = st.columns([1, 1, 2, 1, 1])
        with c1:
            if st.button("⏮ First", disabled=current_page == 0):
                st.session_state["review_page"] = 0
                scroll_to_top()
        with c2:
            if st.button("⬅ Prev", disabled=current_page == 0):
                st.session_state["review_page"] = max(current_page - 1, 0)
                scroll_to_top()
        with c3:
            showing_from = 0 if total_reviews_count == 0 else start_index + 1
            showing_to = min(end_index, total_reviews_count)
            st.markdown(
                f"<div style='text-align: center; font-weight: bold;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
                unsafe_allow_html=True,
            )
        with c4:
            if st.button("Next ➡", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = min(current_page + 1, total_pages - 1)
                scroll_to_top()
        with c5:
            if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = total_pages - 1
                scroll_to_top()

        st.markdown("---")

        # ---------------------------------------
        # Word Clouds (robust + cached)
        # ---------------------------------------
        st.markdown("### 🌟 Word Cloud for Delighters and Detractors")

        detractors_text = build_wordcloud_text(filtered_verbatims, existing_detractor_columns)
        delighters_text = build_wordcloud_text(filtered_verbatims, existing_delighter_columns)

        custom_stopwords = set(STOPWORDS) | {"na", "n/a", "none", "null", "etc", "amp", "https", "http"}

        @st.cache_data(show_spinner=False)
        def make_wordcloud_png(text: str, colormap: str, width: int = 1600, height: int = 800) -> bytes | None:
            text = (text or "").strip()
            if not text:
                return None
            try:
                wc = WordCloud(
                    background_color="white",
                    colormap=colormap,
                    width=width,
                    height=height,
                    max_words=180,
                    contour_width=2,
                    collocations=False,
                    normalize_plurals=True,
                    stopwords=custom_stopwords,
                    regexp=r"[A-Za-zÀ-ÖØ-öø-ÿ'’\-]+",
                    random_state=42,
                    scale=2,
                ).generate(text)
            except ValueError:
                return None
            import matplotlib.pyplot as _plt
            fig = _plt.figure(figsize=(10, 5))
            _plt.imshow(wc, interpolation="bilinear")
            _plt.axis("off")
            buf = io.BytesIO()
            _plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
            _plt.close(fig)
            return buf.getvalue()

        st.markdown("#### 😠 Detractors")
        det_png = make_wordcloud_png(detractors_text, "Reds")
        st.image(det_png, use_column_width=True) if det_png else st.info("Not enough detractor text to build a word cloud.")

        st.markdown("#### 😊 Delighters")
        del_png = make_wordcloud_png(delighters_text, "Greens")
        st.image(del_png, use_column_width=True) if del_png else st.info("Not enough delighter text to build a word cloud.")

        # ---------------------------------------
        # 🤖 Ask your data (LLM chat)
        # ---------------------------------------
        st.markdown("### 🤖 Ask your data")

        _openai_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        if not _openai_key:
            st.info("Add your OpenAI key in .streamlit/secrets.toml as OPENAI_API_KEY (or env var) to use the chat.")
        else:
            client = OpenAI(api_key=_openai_key)

            if "qa_messages" not in st.session_state:
                st.session_state.qa_messages = [
                    {"role": "system", "content":
                        "You are a helpful data analyst for SharkNinja. "
                        "Answer ONLY using the provided filtered dataset and derived metrics. "
                        "If you don't have enough info, say you don't know. "
                        "Use the tools to compute exact counts/means over the CURRENT filtered dataframe."}
                ]

            def _active_filters_summary() -> str:
                keys = [k for k in st.session_state.keys() if k.startswith("f_") or k in {"tf","sr","kw","delight","detract"}]
                items = []
                for k in sorted(keys):
                    v = st.session_state.get(k)
                    if v in (None, "", [], ["ALL"]):
                        continue
                    items.append(f"{k}={v}")
                return ", ".join(items) if items else "None"

            def _context_from_df(df: pd.DataFrame, sample_reviews: int = 30) -> str:
                if df.empty:
                    return "No rows after filters."
                total = len(df)
                by_star = df["Star Rating"].value_counts(dropna=True).sort_index().to_dict() if "Star Rating" in df else {}
                countries = df["Country"].value_counts().head(10).to_dict() if "Country" in df else {}
                sources = df["Source"].value_counts().head(10).to_dict() if "Source" in df else {}
                cols_keep = [c for c in ["Review Date","Country","Source","Model (SKU)","Star Rating","Verbatim"] if c in df.columns]
                sample = df[cols_keep].sample(min(sample_reviews, total), random_state=7) if total>0 else pd.DataFrame(columns=cols_keep)
                lines = [
                    f"ACTIVE_FILTERS: {_active_filters_summary()}",
                    f"ROW_COUNT: {total}",
                    f"STAR_COUNTS: {by_star}",
                    f"TOP_COUNTRIES(10): {countries}",
                    f"TOP_SOURCES(10): {sources}",
                    "SAMPLE_REVIEWS:"
                ]
                for _, r in sample.iterrows():
                    date_str = ""
                    if "Review Date" in r and pd.notna(r["Review Date"]):
                        try:
                            date_str = pd.to_datetime(r["Review Date"]).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = str(r["Review Date"])
                    rowtxt = {
                        "date": date_str,
                        "country": str(r.get("Country", "")),
                        "source": str(r.get("Source", "")),
                        "model": str(r.get("Model (SKU)", "")),
                        "stars": str(r.get("Star Rating", "")),
                        "text": clean_text(str(r.get("Verbatim","")))
                    }
                    lines.append(str(rowtxt))
                return "\n".join(lines)

            # Tools for exact computations
            def pandas_count(query: str) -> dict:
                """Count rows in the CURRENT filtered_verbatims matching a pandas.query string."""
                try:
                    if ";" in query or "__" in query:
                        return {"error": "Query contains disallowed patterns"}
                    res = filtered_verbatims.query(query, engine="python")
                    return {"count": int(len(res))}
                except Exception as e:
                    return {"error": str(e)}

            def pandas_mean(column: str, query: str | None = None) -> dict:
                try:
                    if column not in filtered_verbatims.columns:
                        return {"error": f"Unknown column {column}"}
                    df = filtered_verbatims
                    if query:
                        df = df.query(query, engine="python")
                    return {"mean": float(df[column].mean())}
                except Exception as e:
                    return {"error": str(e)}

            def count_text_contains(keyword: str, column: str = "Verbatim", query: str | None = None) -> dict:
                try:
                    if column not in filtered_verbatims.columns:
                        return {"error": f"Unknown column {column}"}
                    df = filtered_verbatims
                    if query:
                        df = df.query(query, engine="python")
                    mask = df[column].astype("string").fillna("").map(clean_text).str.contains(keyword, case=False, na=False)
                    return {"count": int(mask.sum())}
                except Exception as e:
                    return {"error": str(e)}

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "pandas_count",
                        "description": "Count rows in the filtered dataset that match a pandas query. "
                                       "Wrap columns with spaces in backticks, e.g., `Country` == 'UK' and `Star Rating` <= 2",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "pandas_mean",
                        "description": "Compute mean of a numeric column in the (optionally) queried filtered dataset.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "query": {"type": "string"}
                            },
                            "required": ["column"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "count_text_contains",
                        "description": "Count rows where a text column contains a keyword (case-insensitive). "
                                       "Optional pandas query can further filter.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string"},
                                "column": {"type": "string", "default": "Verbatim"},
                                "query": {"type": "string"}
                            },
                            "required": ["keyword"]
                        }
                    }
                }
            ]

            # Render chat history
            for m in st.session_state.qa_messages:
                if m["role"] != "system":
                    with st.chat_message(m["role"]):
                        st.markdown(m["content"])

            user_q = st.chat_input("Ask a question about the currently FILTERED reviews…")
            if user_q:
                st.session_state.qa_messages.append({"role": "user", "content": user_q})
                with st.chat_message("user"):
                    st.markdown(user_q)

                context_blob = _context_from_df(filtered_verbatims)
                prompt = (
                    "CONTEXT BELOW.\n"
                    f"{context_blob}\n\n"
                    "INSTRUCTIONS:\n"
                    "- Prefer calling tools when you need exact counts/means.\n"
                    "- If you compute with a tool, explain briefly how you filtered.\n"
                    "- If the context does not contain the answer and a tool call is not enough, say you don't know."
                )

                try:
                    completion = client.chat.completions.create(
                        model="gpt-4o-mini",
                        temperature=0.2,
                        messages=[*st.session_state.qa_messages, {"role": "system", "content": prompt}],
                        tools=tools
                    )
                    msg = completion.choices[0].message
                    if msg.tool_calls:
                        tool_outputs = []
                        import json
                        for call in msg.tool_calls:
                            fn = call.function.name
                            args = {}
                            if call.function.arguments:
                                try:
                                    args = json.loads(call.function.arguments)
                                except Exception:
                                    args = {}
                            if fn == "pandas_count":
                                out = pandas_count(args.get("query",""))
                            elif fn == "pandas_mean":
                                out = pandas_mean(args.get("column",""), args.get("query"))
                            elif fn == "count_text_contains":
                                out = count_text_contains(args.get("keyword",""), args.get("column","Verbatim"), args.get("query"))
                            else:
                                out = {"error": f"Unknown tool {fn}"}
                            tool_outputs.append({"tool_call_id": call.id, "role": "tool", "name": fn, "content": json.dumps(out)})

                        follow = client.chat.completions.create(
                            model="gpt-4o-mini",
                            temperature=0.2,
                            messages=[
                                *st.session_state.qa_messages,
                                {"role": "system", "content": prompt},
                                {"role": "assistant", "tool_calls": msg.tool_calls, "content": None},
                                *tool_outputs
                            ]
                        )
                        final_text = follow.choices[0].message.content
                    else:
                        final_text = msg.content

                    st.session_state.qa_messages.append({"role": "assistant", "content": final_text})
                    with st.chat_message("assistant"):
                        st.markdown(final_text)

                except Exception as e:
                    err = f"LLM error: {e}"
                    st.session_state.qa_messages.append({"role": "assistant", "content": err})
                    with st.chat_message("assistant"):
                        st.error(err)

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")
