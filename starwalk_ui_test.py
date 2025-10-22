# starwalk_ui.py

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
import warnings
import json

# Silence openpyxl validation warning spam
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# Optional: high-quality text fixer (safe if not installed)
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# Try importing OpenAI SDK; app still runs without it
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

# ---------- Page config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Global CSS ----------
st.markdown(
    """
    <style>
    .block-container { padding-top: 0.6rem; padding-bottom: 1rem; }
    section[data-testid="stSidebar"] .block-container { padding-top: 0.2rem; padding-bottom: 0.6rem; }
    section[data-testid="stSidebar"] label { font-size: 0.95rem; }
    section[data-testid="stSidebar"] .stButton>button { width: 100%; }
    mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
    .review-card { border:1px solid #e6e6e6; background:#fafafa; border-radius:10px; padding:16px; }
    .review-card p { margin:.25rem 0; line-height:1.45; }
    .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
    .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:0.95rem; }
    .badge.pos { background:#CFF7D6; color:#085a2a; }
    .badge.neg { background:#FBD3D0; color:#7a0410; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Title ----------
st.markdown(
    """
    <h1 style="text-align:center;margin-bottom:.25rem;">🌟 Star Walk Analysis Dashboard</h1>
    <p style="text-align:center;font-size:16px;color:#666;margin-top:0;">Insights, trends, and ratings — fast.</p>
    """,
    unsafe_allow_html=True,
)

# ---------- Utils ----------
def style_rating_cells(value):
    if isinstance(value, (float, int)):
        if value >= 4.5: return "color: green;"
        if value < 4.5:  return "color: red;"
    return ""

def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    if _HAS_FTFY:
        try: s = _ftfy_fix(s)
        except Exception: pass
    if any(ch in s for ch in ("Ã", "Â", "â", "ï", "€", "™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip(): s = repaired
        except Exception: pass
    for bad, good in {
        "â€™": "'", "â€˜": "‘", "â€œ": "“", "â€\x9d": "”",
        "â€“": "–", "â€”": "—", "Â": ""
    }.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return pd.NA if keep_na else ""
    return s

def apply_filter(df: pd.DataFrame, column_name: str, label: str, key: str | None = None):
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
                    seen.add(item); vals.append(item)
    return vals

def is_valid_symptom_value(x) -> bool:
    if pd.isna(x): return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}: return False
    return not bool(re.fullmatch(r"[\W_]+", s))

def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols: return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    s = (filtered_df[cols].stack(dropna=True)
         .map(lambda v: clean_text(v, keep_na=True)).dropna()
         .astype("string").str.strip())
    s = s[s.map(is_valid_symptom_value)]
    if s.empty: return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    unique_items = pd.unique(s.to_numpy())
    results, total_rows = [], len(filtered_df)
    for item in unique_items:
        item_str = str(item).strip()
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0: continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean()
        pct = (count / total_rows * 100) if total_rows else 0
        results.append({"Item": item_str.title(),
                        "Avg Star": round(avg_star, 1) if pd.notna(avg_star) else None,
                        "Mentions": count,
                        "% Total": f"{round(pct, 1)}%"})
    if not results: return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)

def build_wordcloud_text(df: pd.DataFrame, cols: list[str]) -> str:
    cols = [c for c in cols if c in df.columns]
    if not cols: return ""
    s = (df[cols].stack(dropna=True)
         .map(lambda v: clean_text(v, keep_na=True)).dropna()
         .astype("string").str.strip())
    s = s[s != ""]
    return " ".join(s.tolist())

def highlight_html(text: str, keyword: str | None) -> str:
    safe = html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error: pass
    return safe

# translation helpers
async def _translate_async_call(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if asyncio.iscoroutine(res): res = await res
        return getattr(res, "text", text)
    except Exception:
        return text

def safe_translate(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if hasattr(res, "text"): return res.text
        if asyncio.iscoroutine(res):
            try: return asyncio.run(_translate_async_call(translator, text))
            except RuntimeError:
                loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                try: return loop.run_until_complete(_translate_async_call(translator, text))
                finally: loop.close()
    except Exception: pass
    return text

def apply_keyword_filter(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if not keyword or keyword.strip() == "": return df
    if "Verbatim" not in df.columns: return df
    verb = df["Verbatim"].astype("string").fillna("").map(clean_text)
    mask = verb.str.contains(keyword.strip(), case=False, na=False)
    return df[mask]

# ---------- File upload ----------
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

        # Symptom columns (preserve true NA)
        all_symptom_cols = [c for c in verbatims.columns if c.startswith("Symptom")]
        for c in all_symptom_cols:
            verbatims[c] = verbatims[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

        # Clean review text + dates
        if "Verbatim" in verbatims.columns:
            verbatims["Verbatim"] = verbatims["Verbatim"].astype("string").map(clean_text)
        if "Review Date" in verbatims.columns:
            verbatims["Review Date"] = pd.to_datetime(verbatims["Review Date"], errors="coerce")

        # ---------- Sidebar filters ----------
        st.sidebar.header("🔍 Filters")

        # Timeframe
        with st.sidebar.expander("🗓️ Timeframe", expanded=False):
            timeframe = st.selectbox("Select Timeframe",
                                     options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"],
                                     key="tf")
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
            elif timeframe == "Last Week":  start_date, end_date = today - timedelta(days=7), today
            elif timeframe == "Last Month": start_date, end_date = today - timedelta(days=30), today
            elif timeframe == "Last Year":  start_date, end_date = today - timedelta(days=365), today

        filtered_verbatims = verbatims.copy()
        if start_date and end_date and "Review Date" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[
                (filtered_verbatims["Review Date"] >= pd.Timestamp(start_date)) &
                (filtered_verbatims["Review Date"] <= pd.Timestamp(end_date))
            ]

        # Star rating
        with st.sidebar.expander("🌟 Star Rating", expanded=False):
            selected_ratings = st.multiselect("Select Star Ratings", options=["All"] + [1,2,3,4,5],
                                              default=["All"], key="sr")
        if "All" not in selected_ratings and "Star Rating" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[filtered_verbatims["Star Rating"].isin(selected_ratings)]

        # Standard Filters
        with st.sidebar.expander("🌍 Standard Filters", expanded=False):
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Country", "Country", key="f_Country")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Source", "Source", key="f_Source")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Seeded", "Seeded", key="f_Seeded")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "New Review", "New Review", key="f_New Review")

        # 🩺 Review Symptoms (sidebar)
        detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
        delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
        existing_detractor_columns = [c for c in detractor_columns if c in filtered_verbatims.columns]
        existing_delighter_columns = [c for c in delighter_columns if c in filtered_verbatims.columns]
        detractor_symptoms = collect_unique_symptoms(filtered_verbatims, existing_detractor_columns)
        delighter_symptoms = collect_unique_symptoms(filtered_verbatims, existing_delighter_columns)

        with st.sidebar.expander("🩺 Review Symptoms", expanded=False):
            selected_delighter = st.multiselect("Select Delighter Symptoms",
                                                options=["All"] + sorted(delighter_symptoms),
                                                default=["All"], key="delight")
            selected_detractor = st.multiselect("Select Detractor Symptoms",
                                                options=["All"] + sorted(detractor_symptoms),
                                                default=["All"], key="detract")
        if "All" not in selected_delighter and existing_delighter_columns:
            mask = filtered_verbatims[existing_delighter_columns].isin(selected_delighter).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]
        if "All" not in selected_detractor and existing_detractor_columns:
            mask = filtered_verbatims[existing_detractor_columns].isin(selected_detractor).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]

        # Keyword filter
        with st.sidebar.expander("🔎 Keyword", expanded=False):
            keyword = st.text_input("Keyword to search (in review text)", value="", key="kw",
                                    help="Case-insensitive match in Review text. Cleans â€™ → '")
            if keyword: filtered_verbatims = apply_keyword_filter(filtered_verbatims, keyword)

        # Additional Filters
        core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
        symptom_cols = set([f"Symptom {i}" for i in range(1,21)])
        with st.sidebar.expander("📋 Additional Filters", expanded=False):
            additional_columns = [c for c in verbatims.columns if c not in (core_cols | symptom_cols)]
            if additional_columns:
                for column in additional_columns:
                    filtered_verbatims, _ = apply_filter(filtered_verbatims, column, column, key=f"f_{column}")
            else:
                st.info("No additional filters available.")

        # Reviews per page
        with st.sidebar.expander("📄 Review List", expanded=False):
            rpp_options = [10, 20, 50, 100]
            default_rpp = st.session_state.get("reviews_per_page", 10)
            rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
            rpp = st.selectbox("Reviews per page", options=rpp_options, index=rpp_index, key="rpp")
            if rpp != default_rpp:
                st.session_state["reviews_per_page"] = rpp
                st.session_state["review_page"] = 0

        # 🤖 LLM settings (model + temperature)
        with st.sidebar.expander("🤖 AI Assistant (LLM)", expanded=False):
            # Label → model-id pairs (ordered)
            _model_choices = [
                ("Fast & economical – 4o-mini", "gpt-4o-mini"),
                ("Balanced – 4o", "gpt-4o"),
                ("Advanced – 4.1", "gpt-4.1"),
                ("Most advanced – GPT-5", "gpt-5"),
                ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
            ]
            _default_model = st.session_state.get("llm_model", "gpt-4o-mini")
            _default_idx = next((i for i, (_, mid) in enumerate(_model_choices) if mid == _default_model), 0)
            _label = st.selectbox("Model", options=[l for (l, _) in _model_choices], index=_default_idx,
                                  key="llm_model_label")
            st.session_state["llm_model"] = dict(_model_choices)[_label]
            st.session_state["llm_temp"] = st.slider(
                "Creativity (temperature)", 0.0, 1.0, st.session_state.get("llm_temp", 0.2), 0.1
            )

        # Clear All at bottom
        st.sidebar.markdown("---")
        if st.sidebar.button("🧹 Clear all filters", help="Reset all filters to defaults."):
            for k in ["tf","sr","kw","delight","detract","rpp","review_page","llm_model","llm_model_label","llm_temp"] + \
                     [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
                if k in st.session_state: del st.session_state[k]
            st.rerun()

        st.markdown("---")

        # ========== ⭐ Metrics ==========
        st.markdown("### ⭐ Star Rating Metrics")
        total_reviews = len(filtered_verbatims)
        avg_rating = filtered_verbatims["Star Rating"].mean() if total_reviews else 0.0
        star_counts = filtered_verbatims["Star Rating"].value_counts().sort_index()
        percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
        star_labels = [f"{int(star)} stars" for star in star_counts.index]

        mc1, mc2 = st.columns(2)
        with mc1: st.metric("Total Reviews", f"{total_reviews:,}")
        with mc2: st.metric("Avg Star Rating", f"{avg_rating:.1f}", delta_color="inverse")

        fig_bar_horizontal = go.Figure(go.Bar(
            x=star_counts.values, y=star_labels, orientation="h",
            text=[f"{value} reviews ({percentages.get(idx, 0)}%)"
                  for idx, value in zip(star_counts.index, star_counts.values)],
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

        # ========== 🌍 Country Breakdown ==========
        st.markdown("### 🌍 Country-Specific Breakdown")
        if "Country" in filtered_verbatims.columns and "Source" in filtered_verbatims.columns:
            new_review_filtered = filtered_verbatims[
                filtered_verbatims["New Review"].astype("string").str.upper() == "YES"
            ]
            country_source_stats = (
                filtered_verbatims.groupby(["Country", "Source"])
                .agg(Average_Rating=("Star Rating", "mean"), Review_Count=("Star Rating", "count"))
                .reset_index()
            )
            new_review_stats = (
                new_review_filtered.groupby(["Country", "Source"])
                .agg(New_Review_Average=("Star Rating", "mean"), New_Review_Count=("Star Rating", "count"))
                .reset_index()
            )
            country_source_stats = country_source_stats.merge(new_review_stats, on=["Country","Source"], how="left")
            country_overall = (
                filtered_verbatims.groupby("Country")
                .agg(Average_Rating=("Star Rating","mean"), Review_Count=("Star Rating","count"))
                .reset_index()
            )
            overall_new_review_stats = (
                new_review_filtered.groupby("Country")
                .agg(New_Review_Average=("Star Rating","mean"), New_Review_Count=("Star Rating","count"))
                .reset_index()
            )
            country_overall = country_overall.merge(overall_new_review_stats, on="Country", how="left")
            country_overall["Source"] = "Overall"

            def color_numeric(val):
                if pd.isna(val): return ""
                try: v = float(val)
                except Exception: return ""
                if v >= 4.5: return "color: green;"
                if v < 4.5:  return "color: red;"
                return ""

            def fmt_rating(v): return "-" if pd.isna(v) else f"{v:.1f}"
            def fmt_count(v):  return "-" if pd.isna(v) else f"{int(v):,}"

            for country in country_overall["Country"].unique():
                st.markdown(f"#### {country}")
                country_data = country_source_stats[country_source_stats["Country"] == country]
                overall_data = country_overall[country_overall["Country"] == country]
                combined = pd.concat([country_data, overall_data], ignore_index=True)
                combined["Sort_Order"] = combined["Source"].apply(lambda x: 1 if x == "Overall" else 0)
                combined = combined.sort_values(by="Sort_Order", ascending=True).drop(columns=["Sort_Order"])
                combined = combined.drop(columns=["Country"]).rename(columns={
                    "Source": "Source",
                    "Average_Rating": "Avg Rating",
                    "Review_Count": "Review Count",
                    "New_Review_Average": "New Review Average",
                    "New_Review_Count": "New Review Count"
                })

                def bold_overall(row):
                    if row["Source"] == "Overall":
                        return ["font-weight: bold;"] * len(row)
                    return [""] * len(row)

                styled = (
                    combined.style
                    .format({"Avg Rating": fmt_rating, "Review Count": fmt_count,
                             "New Review Average": fmt_rating, "New Review Count": fmt_count})
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

        # ========== 🩺 Symptom Tables ==========
        st.markdown("### 🩺 Symptom Tables")
        detractors_results = analyze_delighters_detractors(filtered_verbatims, existing_detractor_columns)
        delighters_results = analyze_delighters_detractors(filtered_verbatims, existing_delighter_columns)
        detractors_results = detractors_results.head(20)
        delighters_results = delighters_results.head(20)

        view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0)

        def _styled_table(df: pd.DataFrame):
            return df.style.applymap(style_rating_cells, subset=["Avg Star"])\
                           .format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"})\
                           .hide(axis="index")

        if view_mode == "Split":
            c1, c2 = st.columns([1, 1])
            with c1:
                st.subheader("All Detractors")
                st.dataframe(_styled_table(detractors_results) if not detractors_results.empty else detractors_results,
                             use_container_width=True, hide_index=True)
            with c2:
                st.subheader("All Delighters")
                st.dataframe(_styled_table(delighters_results) if not delighters_results.empty else delighters_results,
                             use_container_width=True, hide_index=True)
        else:
            tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
            with tab1:
                st.dataframe(_styled_table(detractors_results) if not detractors_results.empty else detractors_results,
                             use_container_width=True, hide_index=True)
            with tab2:
                st.dataframe(_styled_table(delighters_results) if not delighters_results.empty else delighters_results,
                             use_container_width=True, hide_index=True)

        st.markdown("---")

        # ========== 📝 Reviews ==========
        st.markdown("### 📝 All Reviews")
        translator = Translator()

        if not filtered_verbatims.empty:
            csv_bytes = filtered_verbatims.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes,
                               file_name="filtered_reviews.csv", mime="text/csv")

        translate_all = st.button("Translate All Reviews to English")

        reviews_per_page = st.session_state.get("reviews_per_page", 10)
        if "review_page" not in st.session_state: st.session_state["review_page"] = 0

        def rerun_top(): st.rerun()

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
                if pd.isna(date_val): date_str = "-"
                else:
                    try: date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
                    except Exception: date_str = "-"

                display_review_html = highlight_html(translated_review, st.session_state.get("kw", ""))

                def render_chips(row, columns, css_class):
                    items = []
                    for c in columns:
                        val = row.get(c, pd.NA)
                        if pd.isna(val): continue
                        s = str(val).strip()
                        if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}: continue
                        items.append(f'<span class="badge {css_class}">{html.escape(s)}</span>')
                    return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

                delighter_message = render_chips(row, existing_delighter_columns, "pos")
                detractor_message = render_chips(row, existing_detractor_columns, "neg")

                star_val = row.get("Star Rating", 0)
                try: star_int = int(star_val) if pd.notna(star_val) else 0
                except Exception: star_int = 0

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

        c1, c2, c3, c4, c5 = st.columns([1, 1, 2, 1, 1])
        with c1:
            if st.button("⏮ First", disabled=current_page == 0):
                st.session_state["review_page"] = 0; rerun_top()
        with c2:
            if st.button("⬅ Prev", disabled=current_page == 0):
                st.session_state["review_page"] = max(current_page - 1, 0); rerun_top()
        with c3:
            showing_from = 0 if total_reviews_count == 0 else start_index + 1
            showing_to = min(end_index, total_reviews_count)
            st.markdown(
                f"<div style='text-align:center;font-weight:bold;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
                unsafe_allow_html=True,
            )
        with c4:
            if st.button("Next ➡", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = min(current_page + 1, total_pages - 1); rerun_top()
        with c5:
            if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = total_pages - 1; rerun_top()

        st.markdown("---")

        # ========== 🤖 Ask your data (auto-scroll to newest answer) ==========
        st.markdown("<div id='askdata-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 🤖 Ask your data")
        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

        # flag to trigger scroll only after a fresh response
        st.session_state.setdefault("ask_scroll_pending", False)

        if not _HAS_OPENAI:
            st.info("To enable this panel, add `openai` to requirements and redeploy. Then set `OPENAI_API_KEY`.")
        elif not api_key:
            st.info("Set your `OPENAI_API_KEY` (in env or .streamlit/secrets.toml) to chat with the filtered data.")
        else:
            client = OpenAI(api_key=api_key)

            if "qa_messages" not in st.session_state:
                st.session_state.qa_messages = [
                    {"role": "system", "content":
                        "You are a helpful analyst. Use ONLY the provided context from the CURRENT filtered dataset. "
                        "Call tools when you need exact counts/means. If unknown, say you don't know."}
                ]

            def context_blob(df: pd.DataFrame, n=25) -> str:
                if df.empty: return "No rows after filters."
                parts = [f"ROW_COUNT={len(df)}"]
                if "Star Rating" in df:
                    parts.append(f"STAR_COUNTS={df['Star Rating'].value_counts().sort_index().to_dict()}")
                cols_keep = [c for c in ["Review Date","Country","Source","Model (SKU)","Star Rating","Verbatim"] if c in df.columns]
                smp = df[cols_keep].sample(min(n, len(df)), random_state=7)
                for _, r in smp.iterrows():
                    try: date_str = pd.to_datetime(r.get("Review Date")).strftime("%Y-%m-%d")
                    except Exception: date_str = str(r.get("Review Date","")) or ""
                    parts.append(str({
                        "date": date_str,
                        "country": str(r.get("Country","")),
                        "source": str(r.get("Source","")),
                        "model": str(r.get("Model (SKU)","")),
                        "stars": str(r.get("Star Rating","")),
                        "text": clean_text(str(r.get("Verbatim","")))
                    }))
                return "\n".join(parts)

            def pandas_count(query: str) -> dict:
                try:
                    if ";" in query or "__" in query: return {"error": "disallowed pattern"}
                    res = filtered_verbatims.query(query, engine="python")
                    return {"count": int(len(res))}
                except Exception as e:
                    return {"error": str(e)}

            def pandas_mean(column: str, query: str | None = None) -> dict:
                try:
                    if column not in filtered_verbatims.columns:
                        return {"error": f"Unknown column {column}"}
                    df = filtered_verbatims
                    if query: df = df.query(query, engine="python")
                    return {"mean": float(df[column].mean())}
                except Exception as e:
                    return {"error": str(e)}

            tools = [
                {"type":"function","function":{
                    "name":"pandas_count",
                    "description":"Count rows matching a pandas query over the CURRENT filtered dataset. Wrap columns with spaces in backticks.",
                    "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}
                }},
                {"type":"function","function":{
                    "name":"pandas_mean",
                    "description":"Compute mean of a numeric column (optionally with a pandas query).",
                    "parameters":{"type":"object","properties":{"column":{"type":"string"},"query":{"type":"string"}},"required":["column"]}
                }},
            ]

            # Render chat history
            for m in st.session_state.qa_messages:
                if m["role"] != "system":
                    with st.chat_message(m["role"]):
                        st.markdown(m["content"])

            # Friendly placeholder
            user_q = st.chat_input("Hey! Ask me anything about these filtered reviews 🙂")
            if user_q:
                st.session_state.qa_messages.append({"role": "user", "content": user_q})
                with st.chat_message("user"):
                    st.markdown(user_q)

                sys_ctx = ("CONTEXT:\n" + context_blob(filtered_verbatims) +
                           "\n\nINSTRUCTIONS: Prefer calling tools for exact numbers. "
                           "If unknown from context+tools, say you don't know.")

                # Use selected model + temperature from sidebar
                selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
                llm_temp = float(st.session_state.get("llm_temp", 0.2))

                try:
                    first = client.chat.completions.create(
                        model=selected_model,
                        temperature=llm_temp,
                        messages=[*st.session_state.qa_messages, {"role":"system","content": sys_ctx}],
                        tools=tools,
                    )
                    msg = first.choices[0].message
                    if msg.tool_calls:
                        tool_msgs = []
                        for call in msg.tool_calls:
                            name = call.function.name
                            args = json.loads(call.function.arguments or "{}")
                            out = {"error":"unknown tool"}
                            if name == "pandas_count": out = pandas_count(args.get("query",""))
                            if name == "pandas_mean":  out = pandas_mean(args.get("column",""), args.get("query"))
                            tool_msgs.append({"tool_call_id": call.id, "role":"tool",
                                              "name": name, "content": json.dumps(out)})
                        follow = client.chat.completions.create(
                            model=selected_model,
                            temperature=llm_temp,
                            messages=[
                                *st.session_state.qa_messages,
                                {"role":"system","content": sys_ctx},
                                {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                                *tool_msgs
                            ],
                        )
                        final_text = follow.choices[0].message.content
                    else:
                        final_text = msg.content

                    st.session_state.qa_messages.append({"role":"assistant","content": final_text})
                    with st.chat_message("assistant"):
                        st.markdown(final_text)
                        # anchor we can scroll to after the response renders
                        st.markdown("<div id='askdata-last'></div>", unsafe_allow_html=True)
                    # trigger one-time scroll
                    st.session_state["ask_scroll_pending"] = True

                except Exception as e:
                    err = f"LLM error: {e}"
                    st.session_state.qa_messages.append({"role":"assistant","content": err})
                    with st.chat_message("assistant"):
                        st.error(err)
                        st.markdown("<div id='askdata-last'></div>", unsafe_allow_html=True)
                    st.session_state["ask_scroll_pending"] = True

        st.markdown("---")

        # ========== ☁️ Word Clouds ==========
        st.markdown("### 🌟 Word Cloud for Delighters and Detractors")
        detractors_text = build_wordcloud_text(filtered_verbatims, existing_detractor_columns)
        delighters_text = build_wordcloud_text(filtered_verbatims, existing_delighter_columns)

        custom_stopwords = set(STOPWORDS) | {"na","n/a","none","null","etc","amp","https","http"}

        @st.cache_data(show_spinner=False)
        def make_wordcloud_png(text: str, colormap: str, width: int, height: int, max_words: int, stops: tuple) -> bytes | None:
            text = (text or "").strip()
            if not text: return None
            try:
                wc = WordCloud(
                    background_color="white",
                    colormap=colormap,
                    width=width, height=height,
                    max_words=max_words,
                    contour_width=2,
                    collocations=False,
                    normalize_plurals=True,
                    stopwords=set(stops),
                    regexp=r"[A-Za-zÀ-ÖØ-öø-ÿ'’\-]+",
                    random_state=42,
                    scale=2,
                ).generate(text)
            except ValueError:
                return None
            import matplotlib.pyplot as _plt
            fig = _plt.figure(figsize=(10, 5))
            _plt.imshow(wc, interpolation="bilinear"); _plt.axis("off")
            buf = io.BytesIO(); _plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0); _plt.close(fig)
            return buf.getvalue()

        det_png = make_wordcloud_png(detractors_text, "Reds", 1600, 800, 180, tuple(custom_stopwords))
        st.markdown("#### 😠 Detractors")
        if det_png: st.image(det_png, use_container_width=True)
        else:       st.info("Not enough detractor text to build a word cloud.")

        del_png = make_wordcloud_png(delighters_text, "Greens", 1600, 800, 180, tuple(custom_stopwords))
        st.markdown("#### 😊 Delighters")
        if del_png: st.image(del_png, use_container_width=True)
        else:       st.info("Not enough delighter text to build a word cloud.")

        # ---------- Auto-scroll to the latest LLM answer (only after submit) ----------
        if st.session_state.get("ask_scroll_pending"):
            st.session_state["ask_scroll_pending"] = False
            st.markdown(
                """
                <script>
                const last = document.getElementById('askdata-last');
                if (last) { last.scrollIntoView({behavior:'smooth', block:'start'}); }
                </script>
                """,
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")

