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

# Optional: high-quality text fixer for mojibake & emojis
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

# Global CSS polish (compact sidebar + highlight + nice badges/cards)
st.markdown(
    """
    <style>
    /* main area */
    .block-container { padding-top: 0.6rem; padding-bottom: 1rem; }
    /* sidebar: start higher + compact */
    section[data-testid="stSidebar"] .block-container { padding-top: 0.2rem; padding-bottom: 0.6rem; }
    section[data-testid="stSidebar"] label { font-size: 0.95rem; }
    section[data-testid="stSidebar"] .stButton>button { width: 100%; }
    /* keyword highlight */
    mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
    /* review card */
    .review-card { border:1px solid #e6e6e6; background:#fff; border-radius:12px; padding:18px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
    .review-card p { margin:.25rem 0; line-height:1.45; }
    .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
    .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:0.95rem; }
    .badge.pos { background:#CFF7D6; color:#085a2a; }
    .badge.neg { background:#FBD3D0; color:#7a0410; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Dashboard Title
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
    """Styles cells: Green for ratings 4.5 and above, red for below 4.5."""
    if isinstance(value, (float, int)):
        if value >= 4.5:
            return "color: green;"
        elif value < 4.5:
            return "color: red;"
    return ""

def apply_filter(
    dataframe: pd.DataFrame,
    column_name: str,
    filter_name: str,
    key: str | None = None,
    ui=None,
):
    """Render a multiselect in the provided container (expander/column/sidebar)."""
    ui = ui or st.sidebar
    options = ["ALL"]
    if column_name in dataframe.columns:
        col = dataframe[column_name].astype("string")
        options += sorted([x for x in col.dropna().unique().tolist() if str(x).strip() != ""])
    selected_filter = ui.multiselect(
        f"Select {filter_name}",
        options=options,
        default=["ALL"],
        key=key
    )
    if "ALL" not in selected_filter and column_name in dataframe.columns:
        return dataframe[dataframe[column_name].astype("string").isin(selected_filter)], selected_filter
    return dataframe, ["ALL"]

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

def clean_text(x: str) -> str:
    """Strong text normalizer: ftfy when available; handles â€™, â€” , stray Â, CP1252/UTF-8 mixups; preserves emoji."""
    if x is None:
        return ""
    s = str(x)
    # 1) ftfy handles a lot (if installed)
    if _HAS_FTFY:
        try:
            s = _ftfy_fix(s)
        except Exception:
            pass
    # 2) heuristic recode if classic mojibake bytes are present
    if any(ch in s for ch in ("Ã", "Â", "â", "ï", "€", "™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                s = repaired
        except Exception:
            pass
    # 3) targeted replacements
    fixes = {
        "â€™": "'", "â€˜": "‘", "â€œ": "“", "â€": "”",
        "â€“": "–", "â€”": "—", "â€¢": "•", "â€¦": "…",
        "Â": ""
    }
    for bad, good in fixes.items():
        s = s.replace(bad, good)
    return s.strip()

def is_valid_symptom_value(x) -> bool:
    s = "" if x is None else str(x).strip()
    if not s or s.lower() in {"nan", "none", "null", "n/a"}:
        return False
    # drop punctuation-only strings
    return not bool(re.fullmatch(r"[\W_]+", s))

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
    s = s[s.map(is_valid_symptom_value)]
    if s.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])

    unique_items = pd.unique(s.to_numpy())
    results = []
    total_rows = len(filtered_df)

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
    """Escape to safe HTML then wrap keyword matches with <mark> (case-insensitive)."""
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
    kw = keyword.strip()
    verb_col = "Verbatim" if "Verbatim" in df.columns else None
    mask = pd.Series([False] * len(df))
    if verb_col:
        verb = df[verb_col].astype("string").fillna("").map(clean_text)
        mask = verb.str.contains(kw, case=False, na=False)
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

        # Numeric
        if "Star Rating" in verbatims.columns:
            verbatims["Star Rating"] = pd.to_numeric(verbatims["Star Rating"], errors="coerce")

        # Symptom columns -> clean strings
        all_symptom_cols = [c for c in verbatims.columns if c.startswith("Symptom")]
        for c in all_symptom_cols:
            verbatims[c] = verbatims[c].astype("string").map(clean_text)

        # Review text & date
        if "Verbatim" in verbatims.columns:
            verbatims["Verbatim"] = verbatims["Verbatim"].astype("string").map(clean_text)
        if "Review Date" in verbatims.columns:
            verbatims["Review Date"] = pd.to_datetime(verbatims["Review Date"], errors="coerce")

        # ------------------ SIDEBAR (compact + collapsed) ------------------
        st.sidebar.header("🔍 Filters")

        # Timeframe
        with st.sidebar.expander("🗓️ Timeframe", expanded=False) as x_time:
            timeframe = x_time.selectbox(
                "Select Timeframe",
                options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"],
                key="tf"
            )
            today = datetime.today()
            start_date, end_date = None, None
            if timeframe == "Custom Range":
                start_date, end_date = x_time.date_input(
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
        with st.sidebar.expander("🌟 Star Rating", expanded=False) as x_rating:
            selected_ratings = x_rating.multiselect(
                "Select Star Ratings",
                options=["All"] + [1, 2, 3, 4, 5],
                default=["All"],
                key="sr"
            )
        if "All" not in selected_ratings and "Star Rating" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[filtered_verbatims["Star Rating"].isin(selected_ratings)]

        # Standard Filters (ensure these are INSIDE the expander)
        with st.sidebar.expander("🌍 Standard Filters", expanded=False) as x_std:
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Country", "Country", key="f_Country", ui=x_std)
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Source", "Source", key="f_Source", ui=x_std)
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)", ui=x_std)
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Seeded", "Seeded", key="f_Seeded", ui=x_std)
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "New Review", "New Review", key="f_New Review", ui=x_std)

        # Delighters/Detractors
        detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
        delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
        existing_detractor_columns = [c for c in detractor_columns if c in filtered_verbatims.columns]
        existing_delighter_columns = [c for c in delighter_columns if c in filtered_verbatims.columns]
        detractor_symptoms = collect_unique_symptoms(filtered_verbatims, existing_detractor_columns)
        delighter_symptoms = collect_unique_symptoms(filtered_verbatims, existing_delighter_columns)

        with st.sidebar.expander("😊 Delighters & 😠 Detractors", expanded=False) as x_sym:
            selected_delighter = x_sym.multiselect(
                "Select Delighter Symptoms",
                options=["All"] + sorted(delighter_symptoms),
                default=["All"],
                key="delight"
            )
            selected_detractor = x_sym.multiselect(
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
        with st.sidebar.expander("🔎 Keyword", expanded=False) as x_kw:
            keyword = x_kw.text_input("Keyword to search (in review text)", value="", key="kw",
                                      help="Case-insensitive match in the Review text. Cleaned for â€™ → '")
            if keyword:
                filtered_verbatims = apply_keyword_filter(filtered_verbatims, keyword)

        # Additional Filters: anything not in standard/symptoms/core goes here (e.g., Hair Type)
        core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
        symptom_cols = set([f"Symptom {i}" for i in range(1,21)])
        additional_columns = [c for c in verbatims.columns if c not in (core_cols | symptom_cols)]
        with st.sidebar.expander("📋 Additional Filters", expanded=False) as x_add:
            if len(additional_columns) > 0:
                for column in additional_columns:
                    filtered_verbatims, _ = apply_filter(filtered_verbatims, column, column, key=f"f_{column}", ui=x_add)
            else:
                x_add.info("No additional filters available.")

        # Review list UI
        with st.sidebar.expander("📄 Review List", expanded=False) as x_list:
            rpp_options = [10, 20, 50, 100]
            default_rpp = st.session_state.get("reviews_per_page", 10)
            rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
            reviews_per_page_select = x_list.selectbox("Reviews per page", options=rpp_options, index=rpp_index, key="rpp")
            if reviews_per_page_select != default_rpp:
                st.session_state["reviews_per_page"] = reviews_per_page_select
                st.session_state["review_page"] = 0

        # Clear-all moved to bottom so other items slide up
        st.sidebar.markdown("---")
        if st.sidebar.button("🧹 Clear all filters", help="Reset all filters to defaults."):
            # remove common keys and any dynamic f_* filter keys
            for k in ["tf","sr","kw","delight","detract","rpp","review_page"] + [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
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
        # Delighters and Detractors Analysis (responsive)
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

                def render_chips(row, columns, css_class):
                    items = []
                    for c in columns:
                        if c in row and pd.notna(row[c]) and is_valid_symptom_value(row[c]):
                            txt = clean_text(str(row[c]))
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

        # Build texts
        detractors_text = build_wordcloud_text(filtered_verbatims, existing_detractor_columns)
        delighters_text = build_wordcloud_text(filtered_verbatims, existing_delighter_columns)

        # Configure robust stopwords
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
        if det_png:
            st.image(det_png, use_column_width=True)
        else:
            st.info("Not enough detractor text to build a word cloud.")

        st.markdown("#### 😊 Delighters")
        del_png = make_wordcloud_png(delighters_text, "Greens")
        if del_png:
            st.image(del_png, use_column_width=True)
        else:
            st.info("Not enough delighter text to build a word cloud.")

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")
