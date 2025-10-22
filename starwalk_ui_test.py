# starwalk_ui.py
# Streamlit 1.38+ recommended

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
import hashlib
import numpy as np
import urllib.parse
import smtplib
from email.message import EmailMessage
from streamlit.components.v1 import html as st_html  # hero canvas

# ---------------------------
# Openpyxl warning silencer
# ---------------------------
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# ---------------------------
# Optional text repair (ftfy)
# ---------------------------
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# ---------------------------
# OpenAI SDK (optional)
# ---------------------------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

# ---------------------------
# LLM capability helpers
# ---------------------------
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}  # add more if needed
def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

# ---------------------------
# Page config & session defaults
# ---------------------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")
st.session_state.setdefault("show_ask", False)  # only open Ask section on button click

# ---------------------------
# Global CSS
# ---------------------------
st.markdown(
    """
    <style>
      :root { scroll-padding-top: 96px; }
      .block-container { padding-top: 1rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] .block-container { padding-top: .25rem; padding-bottom: .6rem; }
      section[data-testid="stSidebar"] label { font-size: .95rem; }
      section[data-testid="stSidebar"] .stButton>button { width: 100%; }

      mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
      .review-card { border:1px solid #e6e6e6; background:#fafafa; border-radius:12px; padding:16px; }
      .review-card p { margin:.25rem 0; line-height:1.45; }
      .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
      .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:.95rem; }
      .badge.pos { background:#CFF7D6; color:#085a2a; }
      .badge.neg { background:#FBD3D0; color:#7a0410; }

      /* Hero layout: stars LEFT, logo RIGHT */
      .hero-wrap {
        position: relative;
        overflow: hidden;
        border-radius: 14px;
        border: 1px solid #eee;
        height: 150px;
        margin-top: .25rem;
        margin-bottom: 1rem;
        display: grid;
        grid-template-columns: minmax(420px, 1fr) 260px;
        background: #fff;
      }
      .hero-left {
        position: relative;
        padding: 12px 14px;
        background: radial-gradient(1100px 320px at 8% -18%, #fff8d9 0%, #ffffff 55%, #ffffff 100%);
      }
      #hero-canvas { position:absolute; inset:0; width:100%; height:100%; }
      .hero-title { position: relative; z-index: 1; margin:0; padding-top: 8px;
        font-size: clamp(22px, 3.6vw, 40px); font-weight: 800; letter-spacing:.3px; }
      .hero-sub { position: relative; z-index: 1; margin:6px 0 0 0; color:#667085;
        font-size: clamp(12px, 1.05vw, 16px); }

      .hero-right {
        display:flex; align-items:center; justify-content:center; padding-right: 10px;
      }
      .sn-logo-img { height: 28px; width: auto; display:block; opacity:.92; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------
# Minimalist hero with starfield LEFT + SharkNinja data-URI SVG RIGHT
# ---------------------------
def _sn_logo_data_uri() -> str:
    svg = '''
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 90">
      <g fill="#111">
        <text x="0" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="800" font-size="52">Shark</text>
        <rect x="225" y="12" width="4" height="66" rx="2" fill="#222"/>
        <text x="245" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="900" font-size="52">NINJA</text>
      </g>
    </svg>
    '''.strip()
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)

def render_hero():
    sn_uri = _sn_logo_data_uri()
    st_html(
        f"""
        <div class="hero-wrap" id="top-hero">
          <div class="hero-left">
            <canvas id="hero-canvas"></canvas>
            <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
            <p class="hero-sub">Insights, trends, and ratings — fast.</p>
          </div>
          <div class="hero-right">
            <img src="{sn_uri}" alt="SharkNinja" class="sn-logo-img"/>
          </div>
        </div>
        <script>
        (function() {{
          const c = document.getElementById('hero-canvas');
          const ctx = c.getContext('2d', {{alpha:true}});
          const DPR = window.devicePixelRatio || 1;
          let w=0,h=0;
          function resize(){{
            const r = c.getBoundingClientRect();
            w = Math.max(300, r.width|0);
            h = Math.max(120, r.height|0);
            c.width = w * DPR; c.height = h * DPR;
            ctx.setTransform(DPR,0,0,DPR,0,0);
          }}
          window.addEventListener('resize', resize, {{passive:true}});
          resize();
          const N = Math.max(120, Math.floor(w/10)); // dense, left
          const stars = Array.from({{length:N}}, () => ({{
            x: Math.random()*w, y: Math.random()*h, r: 0.6 + Math.random()*1.4, s: 0.3 + Math.random()*0.9
          }}));
          let mx=.5, my=.5;
          c.addEventListener('pointermove', e => {{
            const r = c.getBoundingClientRect();
            mx = (e.clientX - r.left) / r.width;
            my = (e.clientY - r.top) / r.height;
          }});
          function tick(){{
            ctx.clearRect(0,0,w,h);
            const grd = ctx.createLinearGradient(w*0.66,0,w,0);
            grd.addColorStop(0,'rgba(255,255,255,0)'); grd.addColorStop(1,'rgba(255,255,255,1)');
            for(const s of stars){{
              const px = s.x + (mx-0.5)*16*s.s;
              const py = s.y + (my-0.5)*10*s.s;
              ctx.beginPath(); ctx.arc((px%w+w)%w, (py%h+h)%h, s.r, 0, Math.PI*2);
              ctx.fillStyle = 'rgba(255,200,50,.9)'; ctx.fill();
              s.x = (s.x + 0.12*s.s) % w;
            }}
            ctx.fillStyle = grd; ctx.fillRect(w*0.66,0,w*0.34,h);
            requestAnimationFrame(tick);
          }}
          tick();
        }})();
        </script>
        """,
        height=160,
    )

render_hero()

# ---------------------------
# Utilities
# ---------------------------
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

# Translation helpers
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

def dataset_signature(df: pd.DataFrame) -> str:
    texts = df.get("Verbatim")
    if texts is None:
        texts = pd.Series([], dtype="string")
    else:
        texts = texts.astype("string").fillna("")
    joined = "|".join(texts.tolist())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()

def filters_summary_text() -> str:
    """Summarize current filters from session state."""
    parts = []
    tf = st.session_state.get("tf")
    if tf: parts.append(f"Timeframe: {tf}")
    sr = st.session_state.get("sr")
    if sr and isinstance(sr, list) and "All" not in sr:
        parts.append(f"Star Ratings: {', '.join(map(str, sr))}")
    kw = st.session_state.get("kw")
    if kw: parts.append(f"Keyword: {kw}")
    delighters = st.session_state.get("delight")
    detractors = st.session_state.get("detract")
    if delighters and "All" not in delighters: parts.append(f"Delighters: {', '.join(delighters)}")
    if detractors and "All" not in detractors: parts.append(f"Detractors: {', '.join(detractors)}")
    extra = []
    for k, v in st.session_state.items():
        if k.startswith("f_") and isinstance(v, list) and "ALL" not in v:
            extra.append(f"{k[2:]}: {', '.join(map(str, v))}")
    if extra:
        parts.append("Additional: " + " | ".join(extra))
    return "\n".join(parts) if parts else "None"

# ---------------------------
# File upload
# ---------------------------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

# Prevent auto-scroll when a new file is uploaded
if uploaded_file:
    if st.session_state.get("last_uploaded_name") != uploaded_file.name:
        st.session_state["last_uploaded_name"] = uploaded_file.name
        st.session_state["force_scroll_top_once"] = True

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

        # ---------------------------
        # Sidebar filters
        # ---------------------------
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

        # Standard filters
        with st.sidebar.expander("🌍 Standard Filters", expanded=False):
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Country", "Country", key="f_Country")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Source", "Source", key="f_Source")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "Seeded", "Seeded", key="f_Seeded")
            filtered_verbatims, _ = apply_filter(filtered_verbatims, "New Review", "New Review", key="f_New Review")

        # Symptoms
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

        # >>> Moved here: Clear filters (higher up, just under Review List)
        if st.sidebar.button("🧹 Clear all filters", help="Reset all filters to defaults."):
            for k in ["tf","sr","kw","delight","detract","rpp","review_page","llm_model","llm_model_label",
                      "llm_temp","show_ask","scroll_target_id","llm_rag","llm_evidence"] + \
                     [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
                if k in st.session_state: del st.session_state[k]
            st.rerun()

        # Sidebar Ask button → open section & page-anchor
        st.sidebar.markdown("---")
        if st.sidebar.button("💬 Ask me anything"):
            st.session_state["show_ask"] = True
            st.session_state["scroll_target_id"] = "askdata-anchor"
            st.rerun()

        # LLM settings + RAG toggles
        with st.sidebar.expander("🤖 AI Assistant (LLM)", expanded=False):
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

            temp_supported = model_supports_temperature(st.session_state["llm_model"])
            st.session_state["llm_temp"] = st.slider(
                "Creativity (temperature)",
                min_value=0.0, max_value=1.0,
                value=float(st.session_state.get("llm_temp", 0.2)),
                step=0.1,
                disabled=not temp_supported,
                help=("Controls randomness: lower = more deterministic, higher = more creative. "
                      "Some models (e.g., GPT-5 family) use a fixed temperature and ignore this setting."),
            )
            if not temp_supported:
                st.caption("ℹ️ This model uses a fixed sampling temperature; the slider is disabled.")

            st.session_state["llm_rag"] = st.checkbox(
                "Use retrieval (RAG) on filtered reviews", value=st.session_state.get("llm_rag", True),
                help="Pull the top-matching reviews into context using embeddings (best) or TF-IDF (fallback)."
            )
            st.session_state["llm_evidence"] = st.checkbox(
                "Show evidence in answers", value=st.session_state.get("llm_evidence", True),
                help="Append exact review snippets used so you can verify answers."
            )

        # Bottom of sidebar: Submit feedback anchor button
        st.sidebar.markdown("---")
        if st.sidebar.button("📝 Submit feedback"):
            st.session_state["scroll_target_id"] = "feedback-anchor"
            st.rerun()

        st.markdown("---")

        # ---------------------------
        # ⭐ Metrics
        # ---------------------------
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

        # ---------------------------
        # 🌍 Country Breakdown
        # ---------------------------
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

        # ---------------------------
        # 🩺 Symptom Tables
        # ---------------------------
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

        # ---------------------------
        # 📝 Reviews
        # ---------------------------
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
                f"<div style='text-align:center;font-weight:bold;'>Page {current_page + 1} of {total_pages} • "
                f"Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
                unsafe_allow_html=True,
            )
        with c4:
            if st.button("Next ➡", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = min(current_page + 1, total_pages - 1); rerun_top()
        with c5:
            if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
                st.session_state["review_page"] = total_pages - 1; rerun_top()

        st.markdown("---")

        # ===========================
        # 🔎 Build / use RAG index
        # ===========================
        def build_rag_index(df: pd.DataFrame, api_key: str | None) -> dict | None:
            texts = df.get("Verbatim")
            if texts is None or texts.empty:
                return None
            docs = texts.astype("string").fillna("").map(clean_text).tolist()
            ids = list(range(len(docs)))

            # Preferred: OpenAI embeddings (object-safe)
            if api_key and _HAS_OPENAI:
                cli = OpenAI(api_key=api_key)
                emb_vectors = []
                for i in range(0, len(docs), 128):
                    chunk = docs[i:i+128]
                    resp = cli.embeddings.create(model="text-embedding-3-small", input=chunk)
                    for item in resp.data:
                        vec = np.array(getattr(item, "embedding", None), dtype=np.float32)
                        emb_vectors.append(vec)
                mat = np.vstack(emb_vectors)
                norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
                mat = mat / norms
                return {"ids": ids, "texts": docs, "emb": mat}

            # Fallback: TF–IDF with explicit vocab + idf
            vocab = {}
            vecs = []
            for t in docs:
                tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", t.lower())
                counts = {}
                for tok in tokens:
                    if tok not in vocab: vocab[tok] = len(vocab)
                    idx = vocab[tok]
                    counts[idx] = counts.get(idx, 0) + 1
                vecs.append(counts)
            dfreq = {}
            for v in vecs:
                for idx in v.keys():
                    dfreq[idx] = dfreq.get(idx, 0) + 1
            n = len(vecs)
            idf = np.zeros((len(vocab),), dtype=np.float32)
            for idx, dfc in dfreq.items():
                idf[idx] = np.log((1+n)/(1+dfc)) + 1.0
            dense = np.zeros((n, len(vocab)), dtype=np.float32)
            for i, counts in enumerate(vecs):
                for idx, cnt in counts.items():
                    dense[i, idx] = cnt * idf[idx]
            norms = np.linalg.norm(dense, axis=1, keepdims=True) + 1e-9
            dense = dense / norms
            return {"ids": ids, "texts": docs, "emb": dense, "vocab": vocab, "idf": idf}

        def rag_topk(index: dict, q: str, api_key: str | None, k: int = 30) -> list[tuple[int, float]]:
            if index is None or not q.strip(): return []
            qtext = clean_text(q)
            if api_key and _HAS_OPENAI and index["emb"].shape[1] > 0 and "vocab" not in index:
                cli = OpenAI(api_key=api_key)
                qv = np.array(
                    cli.embeddings.create(model="text-embedding-3-small", input=[qtext]).data[0].embedding,
                    dtype=np.float32
                )
                qv = qv / (np.linalg.norm(qv) + 1e-9)
                sims = (index["emb"] @ qv)
            else:
                vocab = index.get("vocab", {})
                idf = index.get("idf", None)
                if not vocab: return []
                qvec = np.zeros((len(vocab),), dtype=np.float32)
                for tok in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", qtext.lower()):
                    if tok in vocab:
                        qvec[vocab[tok]] += 1.0
                if idf is not None:
                    qvec = qvec * idf
                qvec = qvec / (np.linalg.norm(qvec) + 1e-9)
                sims = (index["emb"] @ qvec)
            top = np.argsort(-sims)[:k]
            return [(int(i), float(sims[i])) for i in top]

        # ---------------------------
        # 🤖 Ask your data (chat) — render only when requested
        # ---------------------------
        if st.session_state.get("show_ask"):
            st.markdown("<div id='askdata-anchor'></div>", unsafe_allow_html=True)
            st.markdown("### 🤖 Ask your data")
            api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

            # Build RAG index when needed or filters changed
            if st.session_state.get("llm_rag", True):
                sig = dataset_signature(filtered_verbatims)
                idx = st.session_state.get("rag_index")
                if idx is None or idx.get("sig") != sig:
                    built = build_rag_index(filtered_verbatims, api_key)
                    st.session_state["rag_index"] = {"sig": sig, "index": built}

            if not _HAS_OPENAI:
                st.info("To enable the assistant, add `openai` to requirements and redeploy. Then set `OPENAI_API_KEY`.")
            elif not api_key:
                st.info("Set your `OPENAI_API_KEY` (env or .streamlit/secrets.toml) to chat with the filtered data.")
            else:
                client = OpenAI(api_key=api_key)

                if "qa_messages" not in st.session_state:
                    st.session_state.qa_messages = [
                        {"role": "system", "content":
                            "You are a helpful analyst. Use ONLY the provided context from the CURRENT filtered dataset. "
                            "Call tools when you need exact counts/means. If unknown, say you don't know."}
                    ]

                # Base context
                def context_blob_base(df: pd.DataFrame, n=25) -> str:
                    if df.empty: return "No rows after filters."
                    parts = [f"ROW_COUNT={len(df)}"]
                    if "Star Rating" in df.columns:
                        parts.append(f"STAR_COUNTS={df['Star Rating'].value_counts().sort_index().to_dict()}")
                    cols_keep = [c for c in ["Review Date","Country","Source","Model (SKU)","Star Rating","Verbatim"]
                                 if c in df.columns]
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

                def context_blob_with_evidence(question: str, df: pd.DataFrame, top_k=30) -> tuple[str, list[dict]]:
                    evidence_rows = []
                    ctx = context_blob_base(df, n=20)
                    if not st.session_state.get("llm_rag", True):
                        return ctx, evidence_rows
                    rag_state = st.session_state.get("rag_index", {})
                    rag = rag_state.get("index")
                    if rag is None:
                        return ctx, evidence_rows
                    hits = rag_topk(rag, question, api_key, k=top_k)
                    texts = rag["texts"]
                    for idx_i, score in hits:
                        txt = texts[idx_i]
                        if not txt.strip(): continue
                        row = df.iloc[idx_i]
                        try: date_str = pd.to_datetime(row.get("Review Date")).strftime("%Y-%m-%d")
                        except Exception: date_str = str(row.get("Review Date","")) or ""
                        evidence_rows.append({
                            "i": int(idx_i),
                            "score": round(float(score), 3),
                            "date": date_str,
                            "country": str(row.get("Country","")),
                            "source": str(row.get("Source","")),
                            "model": str(row.get("Model (SKU)","")),
                            "stars": str(row.get("Star Rating","")),
                            "text": clean_text(txt)
                        })
                    ev_text = "\n".join([f"EVIDENCE {e['i']} (score={e['score']}, date={e['date']}, "
                                         f"country={e['country']}, stars={e['stars']}): {e['text']}"
                                         for e in evidence_rows[:top_k]])
                    ctx2 = ctx + ("\n\nTOP_MATCHING_REVIEWS:\n" + ev_text if ev_text else "")
                    return ctx2, evidence_rows

                # Tools
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

                def keyword_examples(keyword: str, limit: int = 5) -> dict:
                    try:
                        v = filtered_verbatims.get("Verbatim")
                        if v is None: return {"examples": []}
                        kw = keyword.strip()
                        if not kw: return {"examples": []}
                        mask = v.astype("string").fillna("").str.contains(kw, case=False, na=False)
                        rows = filtered_verbatims[mask].head(limit)
                        ex = []
                        for _, r in rows.iterrows():
                            try: date_str = pd.to_datetime(r.get("Review Date")).strftime("%Y-%m-%d")
                            except Exception: date_str = str(r.get("Review Date","")) or ""
                            ex.append({
                                "date": date_str,
                                "country": str(r.get("Country","")),
                                "stars": str(r.get("Star Rating","")),
                                "text": clean_text(str(r.get("Verbatim","")))
                            })
                        return {"examples": ex}
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
                    {"type":"function","function":{
                        "name":"keyword_examples",
                        "description":"Return a few verbatim review examples containing a given keyword.",
                        "parameters":{"type":"object","properties":{
                            "keyword":{"type":"string"},
                            "limit":{"type":"integer","default":5}
                        },"required":["keyword"]}
                    }},
                ]

                # render prior messages
                for m in st.session_state.qa_messages:
                    if m["role"] != "system":
                        with st.chat_message(m["role"]):
                            st.markdown(m["content"])

                user_q = st.chat_input("Hey! Ask me anything about these filtered reviews 🙂")
                if user_q:
                    st.session_state.qa_messages.append({"role": "user", "content": user_q})
                    with st.chat_message("user"):
                        st.markdown(user_q)

                    ctx_text, evidence = context_blob_with_evidence(user_q, filtered_verbatims, top_k=30)
                    sys_ctx = ("CONTEXT:\n" + ctx_text +
                               "\n\nINSTRUCTIONS: Prefer calling tools for exact numbers. "
                               "Cite evidence IDs if shown. If unknown, say you don't know.")

                    selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
                    llm_temp = float(st.session_state.get("llm_temp", 0.2))

                    first_kwargs = {
                        "model": selected_model,
                        "messages": [*st.session_state.qa_messages, {"role": "system", "content": sys_ctx}],
                        "tools": tools,
                    }
                    if model_supports_temperature(selected_model):
                        first_kwargs["temperature"] = llm_temp

                    try:
                        first = client.chat.completions.create(**first_kwargs)
                    except Exception as e:
                        if "temperature" in str(e).lower() and ("unsupported" in str(e).lower() or "does not support" in str(e).lower()):
                            first_kwargs.pop("temperature", None)
                            first = client.chat.completions.create(**first_kwargs)
                        else:
                            raise

                    msg = first.choices[0].message
                    if msg.tool_calls:
                        tool_msgs = []
                        for call in msg.tool_calls:
                            name = call.function.name
                            args = json.loads(call.function.arguments or "{}")
                            out = {"error":"unknown tool"}
                            if name == "pandas_count": out = pandas_count(args.get("query",""))
                            if name == "pandas_mean":  out = pandas_mean(args.get("column",""), args.get("query"))
                            if name == "keyword_examples": out = keyword_examples(args.get("keyword",""), args.get("limit",5))
                            tool_msgs.append({"tool_call_id": call.id, "role":"tool",
                                              "name": name, "content": json.dumps(out)})

                        follow_kwargs = {
                            "model": selected_model,
                            "messages": [
                                *st.session_state.qa_messages,
                                {"role":"system","content": sys_ctx},
                                {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                                *tool_msgs
                            ],
                        }
                        if model_supports_temperature(selected_model):
                            follow_kwargs["temperature"] = llm_temp

                        try:
                            follow = client.chat.completions.create(**follow_kwargs)
                        except Exception as e:
                            if "temperature" in str(e).lower() and ("unsupported" in str(e).lower() or "does not support" in str(e).lower()):
                                follow_kwargs.pop("temperature", None)
                                follow = client.chat.completions.create(**follow_kwargs)
                            else:
                                raise

                        final_text = follow.choices[0].message.content
                    else:
                        final_text = msg.content

                    if st.session_state.get("llm_evidence", True) and evidence:
                        ev_md = "\n\n**Evidence (top matches):**\n"
                        for e in evidence[:8]:
                            ev_md += f"- **ID {e['i']}** · {e['date']} · {e['country']} · ⭐ {e['stars']}: {e['text'][:220]}...\n"
                        final_text = (final_text or "") + ev_md

                    st.session_state.qa_messages.append({"role":"assistant","content": final_text})
                    with st.chat_message("assistant"):
                        st.markdown(final_text)
                    st.session_state["scroll_target_id"] = "askdata-anchor"  # keep anchor sticky on answer
                    st.rerun()

            # Close panel
            if st.button("Close Ask panel"):
                st.session_state["show_ask"] = False
                st.session_state.pop("scroll_target_id", None)
                st.rerun()

        st.markdown("---")

        # ---------------------------
        # ☁️ Word Clouds (resilient)
        # ---------------------------
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

        # ---------------------------
        # 💌 Feedback / Feature Requests (BOTTOM)
        # ---------------------------
        st.markdown("<div id='feedback-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 💌 Submit feedback & feature requests")
        st.caption("We care about your voice. Tell us what would make this better.")

        def send_feedback_email(subject: str, body: str, to_addr: str) -> tuple[bool, str]:
            host = st.secrets.get("SMTP_HOST", os.getenv("SMTP_HOST"))
            port = int(st.secrets.get("SMTP_PORT", os.getenv("SMTP_PORT", "587")))
            user = st.secrets.get("SMTP_USER", os.getenv("SMTP_USER"))
            pwd  = st.secrets.get("SMTP_PASS", os.getenv("SMTP_PASS"))
            try:
                if not (host and user and pwd and to_addr):
                    return False, "SMTP not configured"
                msg = EmailMessage()
                msg["From"] = user
                msg["To"] = to_addr
                msg["Subject"] = subject
                msg.set_content(body)
                with smtplib.SMTP(host, port, timeout=12) as s:
                    s.starttls()
                    s.login(user, pwd)
                    s.send_message(msg)
                return True, "sent"
            except Exception as e:
                return False, str(e)

        with st.form("feedback_form"):
            col_a, col_b = st.columns(2)
            with col_a:
                fb_name = st.text_input("Your name (optional)")
            with col_b:
                fb_reply = st.text_input("Your email (optional)")
            fb_type = st.selectbox("Type", ["Feature request", "Bug report", "Question", "Praise", "Other"])
            fb_text = st.text_area("Message", placeholder="What would make this tool more useful for you?")
            fb_include = st.checkbox("Include current filter summary", value=True)
            submitted = st.form_submit_button("Send feedback ✉️")

            if submitted:
                filt_summary = filters_summary_text() if fb_include else "Skipped"
                row_count = len(filtered_verbatims)
                subject = f"[StarWalk Feedback] {fb_type} — {fb_name or 'Anonymous'}"
                body = (
                    f"From: {fb_name or 'Anonymous'}\n"
                    f"Reply-to: {fb_reply or 'N/A'}\n"
                    f"Type: {fb_type}\n"
                    f"Dataset rows (after filters): {row_count}\n"
                    f"Filters:\n{filt_summary}\n\n"
                    f"Message:\n{fb_text}\n"
                )
                ok, info = send_feedback_email(subject, body, "wseddon@sharkninja.com")
                if ok:
                    st.success("Thanks! Your feedback was sent. 💙")
                else:
                    # Fallback: open mail client with prefilled subject/body
                    st.warning("Email service not configured. Opening your mail client instead.")
                    mailto = (
                        "mailto:wseddon@sharkninja.com?"
                        f"subject={urllib.parse.quote(subject)}&"
                        f"body={urllib.parse.quote(body[:2000])}"
                    )
                    st.link_button("Open mail client", mailto, use_container_width=False)

        # ---------------------------
        # One-time scroll behaviors (top / anchors)
        # ---------------------------
        if st.session_state.get("force_scroll_top_once"):
            st.session_state["force_scroll_top_once"] = False
            st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)

        target = st.session_state.pop("scroll_target_id", None)
        if target:
            st.markdown(
                f"<script>const el=document.getElementById('{target}');"
                f"if(el) el.scrollIntoView({{behavior:'smooth',block:'start'}});</script>",
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")


