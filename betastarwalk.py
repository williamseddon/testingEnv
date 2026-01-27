# starwalk_ui.py
# Streamlit 1.38+
"""
Star Walk Analysis Dashboard

Update (Jan 2026):
- AI Assistant now relies ONLY on OpenAI + the information shown on the current page
  (current filters, on-page aggregates, and an explicit, user-previewable evidence pack).
- Removed local semantic search / TF-IDF / BM25 / reranker / embeddings-based retrieval.
- Improved AI grounding: strict "use only provided context" rules + evidence ID citations.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from wordcloud import STOPWORDS  # keep for stopword set; not rendering WC
import re
import html as _html
import os
import json
import textwrap
import warnings
import smtplib
from email.message import EmailMessage
from streamlit.components.v1 import html as st_html  # for custom HTML blocks
from urllib.parse import quote
from io import BytesIO
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from collections import Counter

# Timezone
try:
    from zoneinfo import ZoneInfo  # py3.9+
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = None

warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# ---------- Optional text fixer ----------
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# ---------- OpenAI SDK ----------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}


def model_supports_temperature(model_id: str) -> bool:
    if not model_id:
        return True
    if model_id in NO_TEMP_MODELS:
        return False
    return not model_id.startswith("gpt-5")


# ---------- Page config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Force Light Mode ----------
st_html(
    """
<script>
(function () {
  function setLight() {
    try {
      document.documentElement.setAttribute('data-theme','light');
      document.body && document.body.setAttribute('data-theme','light');
      window.localStorage.setItem('theme','light');
    } catch (e) {}
  }
  setLight();
  new MutationObserver(setLight).observe(
    document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] }
  );
})();
</script>
""",
    height=0,
)

# ---------- Global CSS ----------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }

  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }
  html[data-theme="dark"], body[data-theme="dark"]{
    --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.72); --muted-2:rgba(255,255,255,.64);
    --border-strong:rgba(255,255,255,.22); --border:rgba(255,255,255,.16); --border-soft:rgba(255,255,255,.10);
    --bg-app:#0b0e14; --bg-card:rgba(255,255,255,.06); --bg-tile:rgba(255,255,255,.04);
    --ring:#60a5fa; --ok:#34d399; --bad:#f87171;
  }

  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  section[data-testid="stSidebar"] .block-container { padding-top:.6rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

  .metrics-grid { display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:17px; }
  @media (max-width:1100px){ .metrics-grid { grid-template-columns:1fr; } }
  .metric-card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .metric-card h4{ margin:.2rem 0 .7rem 0; font-size:1.05rem; color:var(--text); }
  .metric-row{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .metric-box{ background:var(--bg-tile); border:1.6px solid var(--border); border-radius:12px; padding:12px; text-align:center; color:var(--text); }
  .metric-label{ color:var(--muted); font-size:.85rem; }
  .metric-kpi{ font-weight:800; font-size:1.8rem; letter-spacing:-0.01em; margin-top:2px; color:var(--text); }

  .review-card{ background:var(--bg-card); border-radius:12px; padding:16px; margin:16px 0 24px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .review-card p{ margin:.25rem 0; line-height:1.5; }
  .badges{ display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
  .badge{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:600; font-size:.85rem; border:1.5px solid transparent; }
  .badge.pos{ background:#ecfdf5; border-color:#86efac; color:#065f46; }
  .badge.neg{ background:#fef2f2; border-color:#fca5a5; color:#7f1d1d; }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }

  .soft-panel{
    background:var(--bg-card);
    border-radius:14px;
    padding:14px 16px;
    box-shadow:0 0 0 1.2px var(--border-strong), 0 6px 12px rgba(15,23,42,0.05);
    margin:10px 0 14px;
  }
  .small-muted{ color:var(--muted); font-size:.9rem; }
  .kpi-pill{
    display:inline-block; padding:4px 10px; border-radius:999px;
    border:1.3px solid var(--border);
    background:var(--bg-tile);
    font-weight:650; margin-right:8px;
  }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------- Utilities ----------
_BAD_CHARS_REGEX = re.compile(r"[ÃÂâï€™]")
PII_PAT = re.compile(r"[\w\.-]+@[\w\.-]+|\+?\d[\d\-\s]{6,}\d")


def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x):
        return pd.NA if keep_na else ""
    s = str(x)
    if s.isascii():
        s = s.strip()
        if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
            return pd.NA if keep_na else ""
        return s
    if _HAS_FTFY:
        try:
            s = _ftfy_fix(s)
        except Exception:
            pass
    if _BAD_CHARS_REGEX.search(s):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                s = repaired
        except Exception:
            pass
    for bad, good in {
        "â€™": "'",
        "â€˜": "‘",
        "â€œ": "“",
        "â€\x9d": "”",
        "â€“": "–",
        "â€”": "—",
        "Â": "",
    }.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return pd.NA if keep_na else ""
    return s


def _mask_pii(s: str) -> str:
    try:
        return PII_PAT.sub("[redacted]", s or "")
    except Exception:
        return s or ""


def esc(x) -> str:
    return _html.escape("" if pd.isna(x) else str(x))


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
                v = str(v).strip()
                if v and v not in seen:
                    seen.add(v)
                    vals.append(v)
    return vals


def is_valid_symptom_value(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return False
    return not bool(re.fullmatch(r"[\W_]+", s))


def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    m = filtered_df[cols].melt(value_name="symptom", var_name="col").drop(columns=["col"])
    m["symptom"] = m["symptom"].map(lambda v: clean_text(v, keep_na=True)).astype("string").str.strip()
    m = m[m["symptom"].map(is_valid_symptom_value)]
    if m.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    counts = m["symptom"].value_counts()

    if "Star Rating" in filtered_df.columns:
        stars = pd.to_numeric(filtered_df["Star Rating"], errors="coerce")
        avg_map = {}
        for s_val in counts.index:
            rows = filtered_df[cols].isin([s_val]).any(axis=1)
            avg_map[s_val] = round(float(stars[rows].mean()), 1) if rows.any() else None
    else:
        avg_map = {s_val: None for s_val in counts.index}

    total_rows = len(filtered_df) or 1
    out = pd.DataFrame(
        {
            "Item": counts.index.str.title(),
            "Avg Star": [avg_map[s_val] for s_val in counts.index],
            "Mentions": counts.values,
            "% Total": (counts.values / total_rows * 100).round(1).astype(str) + "%",
        }
    )
    return out.sort_values("Mentions", ascending=False, ignore_index=True)


def highlight_html(text: str, keyword: str | None) -> str:
    safe = _html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error:
            pass
    return safe


def _infer_product_label(df_in: pd.DataFrame, fallback_filename: str) -> str:
    base = os.path.splitext(fallback_filename or "Uploaded File")[0]
    if "Model (SKU)" in df_in.columns:
        s = df_in["Model (SKU)"].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if not s.empty:
            top = s.value_counts().head(3).index.tolist()
            if len(top) == 1:
                return str(top[0])
            return " / ".join([str(x) for x in top])
    return base


def _trim_snippet(s: str, max_len: int = 420) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _detect_trends(df_in: pd.DataFrame, symptom_cols: list[str], min_mentions: int = 3):
    # Note: this is an on-page analytic derived from the current filtered dataset.
    if "Review Date" not in df_in.columns or "Star Rating" not in df_in.columns:
        return []
    d = df_in.copy()
    d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
    d = d.dropna(subset=["Review Date"])
    if d.empty:
        return []

    last_date = d["Review Date"].max()
    last_m = pd.Period(last_date, freq="M")
    prev_m = last_m - 1

    last = d[d["Review Date"].dt.to_period("M") == last_m]
    prev = d[d["Review Date"].dt.to_period("M") == prev_m]

    out = []
    if len(prev) > 0:
        pct = ((len(last) - len(prev)) / max(1, len(prev))) * 100
        if (len(last) - len(prev)) >= 5 and pct >= 50:
            out.append(f"Review volume increased from {len(prev)} → {len(last)} in {last_m.strftime('%b %Y')} (↑{pct:.0f}%).")
    elif len(last) >= 5:
        out.append(f"Review volume jumped to {len(last)} in {last_m.strftime('%b %Y')} (from 0 in prior month).")

    cols = [c for c in symptom_cols if c in d.columns]
    if cols and (not last.empty):
        def _sym_counts(frame: pd.DataFrame):
            vals = []
            for c in cols:
                s = frame[c].astype("string").map(lambda v: clean_text(v, keep_na=True)).astype("string").str.strip()
                s = s[s.map(is_valid_symptom_value)]
                vals.extend([str(x) for x in s.tolist()])
            return Counter(vals)

        c_last = _sym_counts(last)
        c_prev = _sym_counts(prev) if not prev.empty else Counter()

        for sym, cnt in c_last.most_common(12):
            prev_cnt = c_prev.get(sym, 0)
            if prev_cnt == 0 and cnt >= min_mentions:
                out.append(f"New/returning theme: **{str(sym).title()}** with {cnt} mentions in {last_m.strftime('%b %Y')} (0 prior month).")
            elif prev_cnt > 0:
                inc = cnt - prev_cnt
                inc_pct = (inc / prev_cnt) * 100.0
                if inc >= min_mentions and inc_pct >= 50:
                    out.append(f"Spike: **{str(sym).title()}** mentions {prev_cnt} → {cnt} in {last_m.strftime('%b %Y')} (↑{inc_pct:.0f}%).")

    return out[:8]


# ---------- Anchors ----------
def anchor(id_: str):
    st.markdown(f"<div id='{id_}'></div>", unsafe_allow_html=True)


def scroll_to(id_: str):
    st.markdown(
        f"""
        <script>
        (function(id){{
          function jump(){{
            const el = document.getElementById(id);
            if(el) {{
              el.scrollIntoView({{behavior:'smooth', block:'start'}});
              window.location.hash = id;
            }}
          }}
          setTimeout(jump, 0); setTimeout(jump, 150); setTimeout(jump, 300); setTimeout(jump, 600);
        }})('{id_}');
        </script>
        """,
        unsafe_allow_html=True,
    )


# ---------- File Upload ----------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx", "csv"])

if uploaded_file and st.session_state.get("last_uploaded_name") != uploaded_file.name:
    st.session_state["last_uploaded_name"] = uploaded_file.name
    st.session_state["force_scroll_top_once"] = True

if not uploaded_file:
    st.info("Please upload an Excel file to get started.")
    st.stop()


# ---------- Load & clean (cached) ----------
@st.cache_data(show_spinner=False)
def _load_clean_excel(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    try:
        if file_name.lower().endswith(".csv"):
            df_local = pd.read_csv(BytesIO(file_bytes))
        else:
            bio = BytesIO(file_bytes)
            try:
                df_local = pd.read_excel(bio, sheet_name="Star Walk scrubbed verbatims")
            except ValueError:
                bio2 = BytesIO(file_bytes)
                xls = pd.ExcelFile(bio2)
                candidate = None
                for sh in xls.sheet_names:
                    try:
                        sample = pd.read_excel(xls, sheet_name=sh, nrows=1)
                        cols = [str(c).strip().lower() for c in sample.columns]
                        if any(c == "verbatim" or c.startswith("verbatim") for c in cols):
                            candidate = sh
                            break
                    except Exception:
                        continue
                if candidate:
                    df_local = pd.read_excel(xls, sheet_name=candidate)
                else:
                    bio3 = BytesIO(file_bytes)
                    df_local = pd.read_excel(bio3)
    except Exception as e:
        raise RuntimeError(f"Could not read file: {e}")

    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        if col in df_local.columns:
            df_local[col] = df_local[col].astype("string").str.upper()

    if "Star Rating" in df_local.columns:
        df_local["Star Rating"] = pd.to_numeric(df_local["Star Rating"], errors="coerce")

    all_symptom_cols = [c for c in df_local.columns if str(c).startswith("Symptom")]
    for c in all_symptom_cols:
        df_local[c] = df_local[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

    if "Verbatim" in df_local.columns:
        df_local["Verbatim"] = df_local["Verbatim"].astype("string").map(clean_text)

    if "Review Date" in df_local.columns:
        df_local["Review Date"] = pd.to_datetime(df_local["Review Date"], errors="coerce")

    return df_local


try:
    df = _load_clean_excel(uploaded_file.getvalue(), uploaded_file.name)
except Exception as e:
    st.error(str(e))
    st.stop()


# ---------- Sidebar filters ----------
st.sidebar.header("🔍 Filters")

with st.sidebar.expander("🗓️ Timeframe", expanded=False):
    tz_today = datetime.now(_NY_TZ).date() if _NY_TZ else datetime.today().date()
    timeframe = st.selectbox(
        "Select Timeframe",
        options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"],
        key="tf",
    )
    today = tz_today
    start_date, end_date = None, None
    if timeframe == "Custom Range":
        sel = st.date_input(
            label="Date Range",
            value=(today - timedelta(days=30), today),
            min_value=datetime(2000, 1, 1).date(),
            max_value=today,
            label_visibility="collapsed",
        )
        if isinstance(sel, tuple) and len(sel) == 2:
            start_date, end_date = sel
        else:
            start_date = end_date = sel
    elif timeframe == "Last Week":
        start_date, end_date = today - timedelta(days=7), today
    elif timeframe == "Last Month":
        start_date, end_date = today - timedelta(days=30), today
    elif timeframe == "Last Year":
        start_date, end_date = today - timedelta(days=365), today

filtered = df.copy()
if start_date and end_date and "Review Date" in filtered.columns:
    dt = pd.to_datetime(filtered["Review Date"], errors="coerce")
    end_inclusive = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    filtered = filtered[(dt >= pd.Timestamp(start_date)) & (dt <= end_inclusive)]

with st.sidebar.expander("🌟 Star Rating", expanded=False):
    selected_ratings = st.multiselect("Select Star Ratings", options=["All"] + [1, 2, 3, 4, 5], default=["All"], key="sr")
if "All" not in selected_ratings and "Star Rating" in filtered.columns:
    filtered = filtered[filtered["Star Rating"].isin(selected_ratings)]

with st.sidebar.expander("🌍 Standard Filters", expanded=False):
    filtered, _ = apply_filter(filtered, "Country", "Country", key="f_Country")
    filtered, _ = apply_filter(filtered, "Source", "Source", key="f_Source")
    filtered, _ = apply_filter(filtered, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
    filtered, _ = apply_filter(filtered, "Seeded", "Seeded", key="f_Seeded")
    filtered, _ = apply_filter(filtered, "New Review", "New Review", key="f_New Review")

detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
existing_detractor_columns = [c for c in detractor_columns if c in filtered.columns]
existing_delighter_columns = [c for c in delighter_columns if c in filtered.columns]
detractor_symptoms = collect_unique_symptoms(filtered, existing_detractor_columns)
delighter_symptoms = collect_unique_symptoms(filtered, existing_delighter_columns)

with st.sidebar.expander("🩺 Review Symptoms", expanded=False):
    selected_delighter = st.multiselect(
        "Select Delighter Symptoms",
        options=["All"] + sorted(delighter_symptoms),
        default=["All"],
        key="delight",
    )
    selected_detractor = st.multiselect(
        "Select Detractor Symptoms",
        options=["All"] + sorted(detractor_symptoms),
        default=["All"],
        key="detract",
    )
if "All" not in selected_delighter and existing_delighter_columns:
    filtered = filtered[filtered[existing_delighter_columns].isin(selected_delighter).any(axis=1)]
if "All" not in selected_detractor and existing_detractor_columns:
    filtered = filtered[filtered[existing_detractor_columns].isin(selected_detractor).any(axis=1)]

with st.sidebar.expander("🔎 Keyword", expanded=False):
    keyword = st.text_input(
        "Keyword to search (in review text)",
        value="",
        key="kw",
        help="Case-insensitive match in review text. Cleans â€™ → '",
    )
    if keyword and "Verbatim" in filtered.columns:
        mask_kw = filtered["Verbatim"].astype("string").fillna("").str.contains(keyword.strip(), case=False, na=False)
        filtered = filtered[mask_kw]

core_cols = {"Country", "Source", "Model (SKU)", "Seeded", "New Review", "Star Rating", "Review Date", "Verbatim"}
symptom_cols = set([f"Symptom {i}" for i in range(1, 21)])
with st.sidebar.expander("📋 Additional Filters", expanded=False):
    additional_columns = [c for c in df.columns if c not in (core_cols | symptom_cols)]
    if additional_columns:
        for column in additional_columns:
            filtered, _ = apply_filter(filtered, column, column, key=f"f_{column}")
    else:
        st.info("No additional filters available.")

with st.sidebar.expander("📄 Review List", expanded=False):
    rpp_options = [10, 20, 50, 100]
    default_rpp = st.session_state.get("reviews_per_page", 10)
    rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
    rpp = st.selectbox("Reviews per page", options=rpp_options, index=rpp_index, key="rpp")
    if rpp != default_rpp:
        st.session_state["reviews_per_page"] = rpp
        st.session_state["review_page"] = 0

# Clear filters
if st.sidebar.button("🧹 Clear all filters"):
    for k in [
        "tf",
        "sr",
        "kw",
        "delight",
        "detract",
        "rpp",
        "review_page",
        "llm_model",
        "llm_model_label",
        "llm_temp",
        "ai_enabled",
        "ai_snippet_cap",
        "ai_evidence_scope",
        "ask_q",
        "product_summary_text",
        "chat_history",
    ] + [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()

# ---------- AI Assistant ----------
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
    _label = st.selectbox("Model", options=[l for (l, _) in _model_choices], index=_default_idx, key="llm_model_label")
    st.session_state["llm_model"] = dict(_model_choices)[_label]

    temp_supported = model_supports_temperature(st.session_state["llm_model"])
    st.session_state["llm_temp"] = st.slider(
        "Creativity (temperature)",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.get("llm_temp", 0.2)),
        step=0.1,
        disabled=not temp_supported,
        help=("Lower = more deterministic, higher = more creative. Some models ignore this."),
    )
    if not temp_supported:
        st.caption("ℹ️ This model uses a fixed temperature; the slider is disabled.")

with st.sidebar.expander("🔒 Privacy & AI", expanded=True):
    ai_enabled = st.toggle(
        "Enable AI (send page context to OpenAI)",
        value=True,
        key="ai_enabled",
        help="When on and you press Send/Generate, the app sends ONLY the page context pack (filters + on-page aggregates + a previewable evidence sample) to OpenAI.",
    )
    st.session_state["ai_evidence_scope"] = st.radio(
        "Evidence scope",
        options=["Current page only", "Sample from filtered set (previewed below)"],
        index=1,
        key="ai_evidence_scope",
        help="Controls which review snippets are included in the evidence pack.",
    )
    st.session_state["ai_snippet_cap"] = st.number_input(
        "Max review snippets to send",
        min_value=10,
        max_value=200,
        value=int(st.session_state.get("ai_snippet_cap", 60)),
        step=10,
        key="ai_snippet_cap",
    )
    st.caption("Emails/phone numbers are masked before sending. No remote calls happen until you press Send / Generate.")

with st.sidebar.expander("🔑 OpenAI Key (optional override)", expanded=False):
    st.text_input("OPENAI_API_KEY override", value="", type="password", key="api_key_override")
    st.caption("Leave blank to use .streamlit/secrets.toml or environment variable.")

api_key_override = (st.session_state.get("api_key_override") or "").strip()
api_key = api_key_override or st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

if not _HAS_OPENAI:
    st.info("To enable the AI assistant, add `openai` to requirements and set `OPENAI_API_KEY`.")
elif not api_key:
    st.info("Set `OPENAI_API_KEY` (env or .streamlit/secrets.toml) to use the AI assistant.")

# ---------- Dataset Overview ----------
product_label = _infer_product_label(filtered, uploaded_file.name)
st.markdown("## 🧾 Dataset Overview")
st.markdown(
    f"""
<div class="soft-panel">
  <div><span class="kpi-pill">File</span> <b>{esc(uploaded_file.name)}</b></div>
  <div style="margin-top:6px;"><span class="kpi-pill">Product guess</span> <b>{esc(product_label)}</b></div>
  <div class="small-muted" style="margin-top:8px;">
    Overview is based on the <b>current filtered</b> dataset.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# Precompute symptom summaries once for reuse (charts + tables + AI context)
detractors_results_full = analyze_delighters_detractors(filtered, existing_detractor_columns)
delighters_results_full = analyze_delighters_detractors(filtered, existing_delighter_columns)

# Watchouts derived from filtered dataset
_all_sym_cols = [c for c in [f"Symptom {i}" for i in range(1, 21)] if c in filtered.columns]
trend_watchouts = _detect_trends(filtered, symptom_cols=_all_sym_cols, min_mentions=3)

if trend_watchouts:
    with st.expander("⚠️ Watchouts & Recent Movement", expanded=False):
        st.markdown("\n".join([f"- {t}" for t in trend_watchouts]))

# ---------- Helpers: AI context pack (ONLY on-page information) ----------
def _metrics_snapshot(df_in: pd.DataFrame) -> dict:
    s = pd.to_numeric(df_in.get("Star Rating"), errors="coerce")
    total = int(len(df_in))
    out = {
        "product_guess": product_label,
        "total_reviews": total,
        "avg_star": float(s.mean()) if total and "Star Rating" in df_in.columns else 0.0,
        "median_star": float(s.median()) if total and "Star Rating" in df_in.columns else 0.0,
        "pct_1_2": float((s <= 2).mean() * 100) if total and "Star Rating" in df_in.columns else 0.0,
        "star_counts": s.value_counts().sort_index().to_dict() if "Star Rating" in df_in.columns else {},
    }
    if "Review Date" in df_in.columns:
        dmin = pd.to_datetime(df_in["Review Date"], errors="coerce").min()
        dmax = pd.to_datetime(df_in["Review Date"], errors="coerce").max()
        out["date_min"] = None if pd.isna(dmin) else pd.to_datetime(dmin).strftime("%Y-%m-%d")
        out["date_max"] = None if pd.isna(dmax) else pd.to_datetime(dmax).strftime("%Y-%m-%d")
    return out


def _top_table(df_tbl: pd.DataFrame, k: int = 8) -> list[dict]:
    if df_tbl is None or df_tbl.empty:
        return []
    out = []
    for _, r in df_tbl.head(k).iterrows():
        out.append(
            {
                "item": str(r.get("Item", "")),
                "mentions": int(r.get("Mentions", 0)),
                "avg_star": None if pd.isna(r.get("Avg Star")) else float(r.get("Avg Star")),
                "pct_total": str(r.get("% Total", "")),
            }
        )
    return out


def _monthly_volume_and_mean(df_in: pd.DataFrame, bucket: str = "M", limit: int = 24) -> list[dict]:
    if "Review Date" not in df_in.columns or "Star Rating" not in df_in.columns:
        return []
    d = df_in.copy()
    d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
    d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
    d = d.dropna(subset=["Review Date", "Star Rating"])
    if d.empty:
        return []
    if bucket == "W":
        d["bucket"] = d["Review Date"].dt.to_period("W-MON").dt.start_time
    else:
        d["bucket"] = d["Review Date"].dt.to_period("M").dt.to_timestamp()
    g = d.groupby("bucket")["Star Rating"].agg(count="count", mean="mean").reset_index().sort_values("bucket")
    if limit and len(g) > limit:
        g = g.tail(int(limit))
    rows = [{"bucket": r["bucket"].strftime("%Y-%m-%d"), "count": int(r["count"]), "mean": float(r["mean"])} for _, r in g.iterrows()]
    return rows


def _filters_snapshot(additional_cols: list[str]) -> dict:
    # Pull directly from session_state (these are the "current filter criteria")
    def _get(key, default=None):
        v = st.session_state.get(key, default)
        # normalize tuples from date_input etc.
        if isinstance(v, tuple):
            return [str(x) for x in v]
        return v

    snap = {
        "timeframe": _get("tf"),
        "custom_start_date": str(start_date) if start_date else None,
        "custom_end_date": str(end_date) if end_date else None,
        "star_ratings": _get("sr", ["All"]),
        "keyword": (_get("kw") or "").strip(),
        "country": _get("f_Country", ["ALL"]),
        "source": _get("f_Source", ["ALL"]),
        "model_sku": _get("f_Model (SKU)", ["ALL"]),
        "seeded": _get("f_Seeded", ["ALL"]),
        "new_review": _get("f_New Review", ["ALL"]),
        "delighter_symptoms": _get("delight", ["All"]),
        "detractor_symptoms": _get("detract", ["All"]),
        "additional_filters": {},
    }
    for col in additional_cols or []:
        snap["additional_filters"][col] = _get(f"f_{col}", ["ALL"])
    return snap


def _row_to_evidence_dict(row: pd.Series, eid: str, det_cols: list[str], del_cols: list[str]) -> dict:
    txt = clean_text(row.get("Verbatim", ""))
    txt = _mask_pii(txt)
    txt = _trim_snippet(txt, 460)

    def _sym_list(cols: list[str], limit: int = 8) -> list[str]:
        vals = []
        for c in cols:
            v = row.get(c, pd.NA)
            if pd.isna(v):
                continue
            s = str(v).strip()
            if not s or s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE", "-"}:
                continue
            vals.append(s.title())
        # de-dupe preserve order
        out = []
        seen = set()
        for v in vals:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out[:limit]

    date_val = row.get("Review Date", pd.NaT)
    date_str = None
    if pd.notna(date_val):
        try:
            date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
        except Exception:
            date_str = None

    star_val = row.get("Star Rating", pd.NA)
    try:
        star_int = int(star_val) if pd.notna(star_val) else None
    except Exception:
        star_int = None

    meta = {}
    for c in ["Source", "Country", "Model (SKU)", "Seeded", "New Review"]:
        if c in row.index:
            v = row.get(c, pd.NA)
            if pd.notna(v) and str(v).strip():
                meta[c] = str(v).strip()

    return {
        "id": eid,
        "star_rating": star_int,
        "review_date": date_str,
        "meta": meta,
        "detractor_symptoms": _sym_list(det_cols, 8),
        "delighter_symptoms": _sym_list(del_cols, 8),
        "verbatim": txt,
    }


def _select_evidence_rows(
    df_in: pd.DataFrame,
    paginated_df: pd.DataFrame,
    det_cols: list[str],
    del_cols: list[str],
    top_det_items: list[str],
    top_del_items: list[str],
    max_n: int,
    scope: str,
) -> pd.DataFrame:
    if df_in.empty:
        return df_in.head(0)

    scope = (scope or "").lower()
    max_n = int(max(1, max_n))

    # Always start with what the user can already see (current page)
    selected_idx = []
    if not paginated_df.empty:
        selected_idx.extend(list(paginated_df.index))

    if scope.startswith("current page"):
        return df_in.loc[selected_idx].head(max_n) if selected_idx else df_in.head(0)

    # Otherwise, expand with a deterministic sample from the filtered dataset
    remain = max_n - len(selected_idx)
    if remain <= 0:
        return df_in.loc[selected_idx].head(max_n)

    d = df_in.copy()

    # Prefer having Review Date ordering for "recent" coverage
    if "Review Date" in d.columns:
        d["__rd"] = pd.to_datetime(d["Review Date"], errors="coerce")
    else:
        d["__rd"] = pd.NaT

    # Helper: deterministic rank key
    def _rank_key(row_: pd.Series) -> str:
        base = f"{row_.get('Verbatim','')}\n{row_.get('Star Rating','')}\n{row_.get('Review Date','')}"
        return hashlib.md5(base.encode("utf-8", errors="ignore")).hexdigest()

    d["__rk"] = d.apply(_rank_key, axis=1)

    # 1) Ensure coverage across star ratings (up to 8 per star)
    if "Star Rating" in d.columns:
        for star in [1, 2, 3, 4, 5]:
            if remain <= 0:
                break
            sub = d[pd.to_numeric(d["Star Rating"], errors="coerce") == star].copy()
            if sub.empty:
                continue
            sub = sub.sort_values(["__rd", "__rk"], ascending=[False, True])
            take = min(8, remain)
            picks = [i for i in sub.head(take).index.tolist() if i not in selected_idx]
            selected_idx.extend(picks)
            remain = max_n - len(selected_idx)

    # 2) Ensure coverage of top symptoms (1 review per item)
    def _pick_by_symptom(items: list[str], cols: list[str]):
        nonlocal remain, selected_idx
        if remain <= 0 or not items or not cols:
            return
        # Build lower-case compare table once for speed
        tmp = d[cols].astype("string").fillna("").apply(lambda s: s.str.strip().str.lower())
        for it in items[:10]:
            if remain <= 0:
                break
            it_l = str(it).strip().lower()
            if not it_l:
                continue
            mask = tmp.eq(it_l).any(axis=1)
            sub = d[mask].copy()
            if sub.empty:
                continue
            sub = sub.sort_values(["__rd", "__rk"], ascending=[False, True])
            for idx in sub.index.tolist():
                if idx not in selected_idx:
                    selected_idx.append(idx)
                    remain = max_n - len(selected_idx)
                    break

    _pick_by_symptom(top_det_items, det_cols)
    _pick_by_symptom(top_del_items, del_cols)

    # 3) Fill remainder with most-recent deterministic
    if remain > 0:
        rest = d[~d.index.isin(selected_idx)].sort_values(["__rd", "__rk"], ascending=[False, True]).head(remain)
        selected_idx.extend(rest.index.tolist())

    return df_in.loc[selected_idx].head(max_n)


def build_ai_context_pack(
    df_filtered: pd.DataFrame,
    df_paginated: pd.DataFrame,
    additional_cols: list[str],
    det_cols: list[str],
    del_cols: list[str],
    top_det_tbl: pd.DataFrame,
    top_del_tbl: pd.DataFrame,
    max_snippets: int,
    evidence_scope: str,
) -> dict:
    metrics = _metrics_snapshot(df_filtered)
    filters = _filters_snapshot(additional_cols)
    top_det = _top_table(top_det_tbl, 10)
    top_del = _top_table(top_del_tbl, 10)

    top_det_items = [r["item"] for r in top_det if r.get("item")]
    top_del_items = [r["item"] for r in top_del if r.get("item")]

    chosen = _select_evidence_rows(
        df_in=df_filtered,
        paginated_df=df_paginated,
        det_cols=det_cols,
        del_cols=del_cols,
        top_det_items=top_det_items,
        top_del_items=top_del_items,
        max_n=max_snippets,
        scope=evidence_scope,
    )

    evidence = []
    for i, (_, row) in enumerate(chosen.iterrows(), start=1):
        evidence.append(_row_to_evidence_dict(row, f"E{i}", det_cols, del_cols))

    monthly = _monthly_volume_and_mean(df_filtered, bucket="M", limit=24)

    pack = {
        "page_version": "2026-01",
        "filters": filters,
        "metrics": metrics,
        "top_detractors": top_det,
        "top_delighters": top_del,
        "watchouts": trend_watchouts[:8],
        "monthly_volume_and_mean": monthly,
        "evidence_reviews": evidence,
        "scope_notes": {
            "analysis_scope": "All insights must reflect the CURRENT filtered dataset in this UI.",
            "evidence_pack_note": (
                "Only the evidence reviews listed here were shared as verbatim examples. "
                "Use aggregate tables for quantification; cite evidence IDs (E#) for examples."
            ),
        },
    }
    return pack


def render_ai_context_preview(ctx: dict):
    with st.expander("🔎 AI context (exactly what is sent to OpenAI)", expanded=False):
        st.caption("This is the ONLY context the LLM receives. If it's not here, the assistant should not claim it.")
        st.json(ctx)
        if ctx.get("evidence_reviews"):
            st.markdown("**Evidence reviews (verbatim snippets)**")
            for r in ctx["evidence_reviews"][: min(25, len(ctx["evidence_reviews"]))]:
                meta = r.get("meta", {})
                meta_s = " • ".join([f"{k}:{v}" for k, v in meta.items()]) if meta else ""
                st.markdown(
                    f"- **{r.get('id')}** — {r.get('star_rating','?')}★ • {r.get('review_date') or '-'}"
                    + (f" • {meta_s}" if meta_s else "")
                    + f"\n  - “{esc(r.get('verbatim',''))}”"
                )
        if ctx.get("evidence_reviews") and len(ctx["evidence_reviews"]) > 25:
            st.caption("Showing first 25 evidence snippets above; all evidence is in the JSON.")


# ---------- Preset Questions ----------
anchor("askdata-anchor")
st.markdown("## 🤖 Ask your data")

if "ask_q" not in st.session_state:
    st.session_state["ask_q"] = ""

if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []  # list of {role, content}

st.markdown(
    """
<div class="soft-panel">
  <div style="font-weight:800; font-size:1.05rem;">⚡ Preset Insight Questions</div>
  <div class="small-muted" style="margin-top:4px;">
    Click one to prefill the question box (then click <b>Send</b>). No remote calls until you press Send.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

b1, b2, b3, b4, b5, b6 = st.columns([1, 1, 1, 1, 1, 1])
if b1.button("Executive Summary", use_container_width=True):
    st.session_state["ask_q"] = (
        "Provide an executive summary of this product based on the CURRENT filtered reviews. "
        "Quantify key points (counts/percentages), list top delighters and detractors, and include 4–6 evidence quotes."
    )
    scroll_to("askdata-anchor")
if b2.button("Top Delighters", use_container_width=True):
    st.session_state["ask_q"] = "What are the top delighters? Quantify mentions and include evidence quotes."
    scroll_to("askdata-anchor")
if b3.button("Top Detractors", use_container_width=True):
    st.session_state["ask_q"] = "What are the top detractors? Quantify mentions and include evidence quotes."
    scroll_to("askdata-anchor")
if b4.button("Biggest Improvements", use_container_width=True):
    st.session_state["ask_q"] = (
        "What are the biggest improvement opportunities for this product? "
        "Quantify how common each issue is, and recommend 3–6 actions prioritized by impact."
    )
    scroll_to("askdata-anchor")
if b5.button("Trends / Watchouts", use_container_width=True):
    st.session_state["ask_q"] = "Are there any emerging trends or watchouts? Quantify changes and include evidence."
    scroll_to("askdata-anchor")
if b6.button("Seeded vs Organic", use_container_width=True):
    st.session_state["ask_q"] = "Compare Seeded vs Organic. Quantify the avg rating gap, volumes, and key theme differences."
    scroll_to("askdata-anchor")

st.markdown(
    '<div class="callout warn">⚠️ AI can make mistakes. The assistant is required to ground all claims in the on-page context. '
    "Double-check important conclusions.</div>",
    unsafe_allow_html=True,
)

# ---------- AI Product Summary (button-triggered) ----------
with st.expander("🪄 AI Product Summary (narrative, button-triggered)", expanded=False):
    st.caption("Generates a concise narrative summary grounded ONLY in the current page context. No remote call until you click.")

    if st.button("Generate / Refresh AI Product Summary", key="gen_product_summary"):
        if not ai_enabled or (not _HAS_OPENAI) or (not api_key):
            st.warning("AI is disabled or no API key available.")
        else:
            ctx = build_ai_context_pack(
                df_filtered=filtered,
                df_paginated=pd.DataFrame(),  # use sampling from filtered set
                additional_cols=additional_columns,
                det_cols=existing_detractor_columns,
                del_cols=existing_delighter_columns,
                top_det_tbl=detractors_results_full,
                top_del_tbl=delighters_results_full,
                max_snippets=min(int(st.session_state.get("ai_snippet_cap", 60)), 120),
                evidence_scope="Sample from filtered set (previewed below)",
            )
            render_ai_context_preview(ctx)

            selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
            llm_temp = float(st.session_state.get("llm_temp", 0.2))

            sys_ctx = (
                "You are StarWalk, a senior consumer insights lead and product review expert.\n"
                "You MUST follow these hard rules:\n"
                "1) Use ONLY facts in CONTEXT_JSON.\n"
                "2) If you mention a number, it must come from CONTEXT_JSON (metrics/top tables/monthly rows).\n"
                "3) Use evidence snippets for examples: quote IDs like [E1], [E2].\n"
                "4) If evidence is insufficient, say so and recommend adjusting filters/evidence scope.\n"
                "5) Keep output exec-friendly, specific, and action-oriented.\n"
            )

            user_prompt = (
                "Create a stakeholder-ready product review summary for the CURRENT filtered dataset.\n\n"
                "Required sections:\n"
                "1) Executive summary (2–4 bullets)\n"
                "2) What customers love (top delighters; quantify)\n"
                "3) What customers dislike (top detractors; quantify)\n"
                "4) Watchouts / movement over time (if present)\n"
                "5) Recommendations (prioritized; be specific)\n"
                "6) Evidence (4–8 short quotes; cite IDs like [E3])\n\n"
                f"CONTEXT_JSON:\n{json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))}"
            )

            try:
                client = OpenAI(api_key=api_key, timeout=90, max_retries=2)
                req = {
                    "model": selected_model,
                    "messages": [{"role": "system", "content": sys_ctx}, {"role": "user", "content": user_prompt}],
                }
                if model_supports_temperature(selected_model):
                    req["temperature"] = llm_temp

                with st.spinner("Generating summary…"):
                    resp = client.chat.completions.create(**req)
                st.session_state["product_summary_text"] = resp.choices[0].message.content
            except Exception as e:
                st.error(f"Could not generate summary: {e}")

    if st.session_state.get("product_summary_text"):
        st.markdown(st.session_state["product_summary_text"])

# ---------- Conversation (optional) ----------
if st.session_state.get("chat_history"):
    with st.expander("🧠 Conversation (this session)", expanded=False):
        for m in st.session_state["chat_history"][-12:]:
            if m.get("role") == "user":
                st.markdown(f"**You:** {esc(m.get('content',''))}")
            else:
                st.markdown(f"**Assistant:**\n\n{m.get('content','')}")

cA, cB = st.columns([1, 1])
with cA:
    if st.button("🧹 Clear conversation", disabled=not bool(st.session_state.get("chat_history"))):
        st.session_state["chat_history"] = []
        st.rerun()
with cB:
    st.caption("Tip: If an answer feels under-evidenced, increase 'Max review snippets to send' or narrow filters.")

# ---------- Ask form ----------
with st.form("ask_ai_form", clear_on_submit=True):
    q = st.text_area("Ask a question", key="ask_q", height=90)
    send = st.form_submit_button("Send")

if send and q.strip():
    q = q.strip()

    if not ai_enabled or (not _HAS_OPENAI) or (not api_key):
        st.warning("AI is disabled or no API key available.")
    else:
        # Build on-page context pack (previewable)
        # Note: paginated is defined later in the Reviews section; we compute it here too for AI.
        if "review_page" not in st.session_state:
            st.session_state["review_page"] = 0
        reviews_per_page = st.session_state.get("reviews_per_page", 10)
        total_reviews_count = len(filtered)
        total_pages = max((total_reviews_count + reviews_per_page - 1) // reviews_per_page, 1)
        st.session_state["review_page"] = min(st.session_state.get("review_page", 0), max(total_pages - 1, 0))
        current_page = st.session_state["review_page"]
        start_index = current_page * reviews_per_page
        end_index = start_index + reviews_per_page
        paginated_for_ai = filtered.iloc[start_index:end_index]

        ctx = build_ai_context_pack(
            df_filtered=filtered,
            df_paginated=paginated_for_ai,
            additional_cols=additional_columns,
            det_cols=existing_detractor_columns,
            del_cols=existing_delighter_columns,
            top_det_tbl=detractors_results_full,
            top_del_tbl=delighters_results_full,
            max_snippets=int(st.session_state.get("ai_snippet_cap", 60)),
            evidence_scope=str(st.session_state.get("ai_evidence_scope", "")),
        )

        render_ai_context_preview(ctx)

        selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
        llm_temp = float(st.session_state.get("llm_temp", 0.2))

        SYS_PROMPT = (
            "You are StarWalk, a senior consumer insights lead and product review expert.\n\n"
            "Hard rules (must follow):\n"
            "- Use ONLY facts in CONTEXT_JSON. Do NOT use outside knowledge.\n"
            "- If you mention a number, it must appear in CONTEXT_JSON.\n"
            "- When giving examples, cite evidence snippet IDs like [E1], [E2].\n"
            "- If evidence is limited or missing for a claim, say so explicitly.\n"
            "- Keep answers grounded, structured, and action-oriented.\n"
            "- Always assume the analysis scope is the CURRENT filtered dataset.\n\n"
            "Style:\n"
            "- Prefer bullets and short sections.\n"
            "- Separate 'What we know' (quantified) from 'Hypotheses' (if any).\n"
            "- End with 3–6 prioritized recommendations.\n"
        )

        # Keep a small rolling history; do NOT rely on old assistant numbers.
        history = st.session_state.get("chat_history", [])[-6:]
        messages = [{"role": "system", "content": SYS_PROMPT}]
        for m in history:
            if m.get("role") in {"user", "assistant"} and isinstance(m.get("content"), str):
                messages.append({"role": m["role"], "content": m["content"]})

        user_msg = f"CONTEXT_JSON:\n{json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))}\n\nQUESTION:\n{q}"
        messages.append({"role": "user", "content": user_msg})

        try:
            client = OpenAI(api_key=api_key, timeout=90, max_retries=2)
            req = {"model": selected_model, "messages": messages}
            if model_supports_temperature(selected_model):
                req["temperature"] = llm_temp
            with st.spinner("Thinking…"):
                resp = client.chat.completions.create(**req)
            final_text = resp.choices[0].message.content

            # Persist chat
            st.session_state["chat_history"] = st.session_state.get("chat_history", []) + [
                {"role": "user", "content": q},
                {"role": "assistant", "content": final_text},
            ]

            st.markdown(f"<div class='chat-q'><b>User:</b> {esc(q)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)

        except Exception as e:
            st.error(f"AI request failed: {e}")

st.markdown("---")

# ---------- Metrics ----------
st.markdown("## ⭐ Star Rating Metrics")
st.caption("All metrics below reflect the **currently filtered** dataset.")

def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float((s <= 2).mean() * 100) if not s.empty else 0.0

def section_stats(sub: pd.DataFrame) -> tuple[int, float, float]:
    cnt = len(sub)
    if cnt == 0 or "Star Rating" not in sub.columns:
        return 0, 0.0, 0.0
    avg = float(pd.to_numeric(sub["Star Rating"], errors="coerce").mean())
    pct = pct_12(sub["Star Rating"])
    return cnt, avg, pct

# Robust Seeded split
if "Seeded" in filtered.columns:
    seed_mask = filtered["Seeded"].astype("string").str.upper().eq("YES")
else:
    seed_mask = pd.Series(False, index=filtered.index)

all_cnt, all_avg, all_low = section_stats(filtered)
org = filtered[~seed_mask]
seed = filtered[seed_mask]
org_cnt, org_avg, org_low = section_stats(org)
seed_cnt, seed_avg, seed_low = section_stats(seed)

def card_html(title, count, avg, pct):
    return textwrap.dedent(f"""
    <div class="metric-card">
      <h4>{_html.escape(title)}</h4>
      <div class="metric-row">
        <div class="metric-box">
          <div class="metric-label">Count</div>
          <div class="metric-kpi">{count:,}</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Avg ★</div>
          <div class="metric-kpi">{avg:.1f}</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">% 1–2★</div>
          <div class="metric-kpi">{pct:.1f}%</div>
        </div>
      </div>
    </div>
    """).strip()

st.markdown(
    (
        '<div class="metrics-grid">'
        f'{card_html("All Reviews", all_cnt, all_avg, all_low)}'
        f'{card_html("Organic (non-Seeded)", org_cnt, org_avg, org_low)}'
        f'{card_html("Seeded", seed_cnt, seed_avg, seed_low)}'
        "</div>"
    ),
    unsafe_allow_html=True,
)

# ---------- Monthly Volume + Avg ★ chart ----------
st.markdown("### 📊 Monthly Review Volume + Avg ★")
if "Review Date" not in filtered.columns or "Star Rating" not in filtered.columns:
    st.info("Need 'Review Date' and 'Star Rating' columns to compute this chart.")
else:
    cA, cB, cC = st.columns([1, 1, 1])
    with cA:
        show_seeded_split = st.toggle("Split Seeded vs Organic", value=False, key="month_split_seeded")
    with cB:
        show_volume_month = st.toggle("Show Volume Bars", value=True, key="month_show_volume")
    with cC:
        month_freq = st.selectbox("Bucket", ["Month", "Week"], index=0, key="month_bucket")

    d = filtered.copy()
    d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
    d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
    d = d.dropna(subset=["Review Date", "Star Rating"])
    if d.empty:
        st.warning("No data after cleaning dates/ratings.")
    else:
        if month_freq == "Week":
            d["bucket"] = d["Review Date"].dt.to_period("W-MON").dt.start_time
        else:
            d["bucket"] = d["Review Date"].dt.to_period("M").dt.to_timestamp()

        overall = d.groupby("bucket")["Star Rating"].agg(count="count", mean="mean").reset_index().sort_values("bucket")

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        if show_volume_month:
            fig.add_trace(
                go.Bar(
                    x=overall["bucket"],
                    y=overall["count"],
                    name="Review volume",
                    opacity=0.30,
                    marker=dict(color="rgba(15, 23, 42, 0.35)"),
                ),
                secondary_y=False,
            )
        fig.add_trace(
            go.Scatter(
                x=overall["bucket"],
                y=overall["mean"],
                name="Avg ★",
                mode="lines+markers",
                line=dict(width=3),
                marker=dict(size=6),
                hovertemplate="Bucket: %{x|%Y-%m-%d}<br>Avg ★: %{y:.2f}<extra></extra>",
            ),
            secondary_y=True,
        )

        if show_seeded_split and "Seeded" in d.columns:
            d2 = d.copy()
            d2["Seeded"] = d2["Seeded"].astype("string").str.upper().fillna("NO")
            for label, mask in [("Organic", d2["Seeded"].ne("YES")), ("Seeded", d2["Seeded"].eq("YES"))]:
                sub = d2[mask].groupby("bucket")["Star Rating"].agg(mean="mean").reset_index().sort_values("bucket")
                if not sub.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=sub["bucket"],
                            y=sub["mean"],
                            name=f"{label} Avg ★",
                            mode="lines",
                            line=dict(width=2, dash="dot"),
                            hovertemplate=f"{label}<br>Bucket: %{{x|%Y-%m-%d}}<br>Avg ★: %{{y:.2f}}<extra></extra>",
                        ),
                        secondary_y=True,
                    )

        fig.update_yaxes(title_text="Review volume", secondary_y=False, rangemode="tozero")
        fig.update_yaxes(title_text="Avg ★", secondary_y=True, range=[1.0, 5.2])
        fig.update_layout(
            template="plotly_white",
            hovermode="x unified",
            margin=dict(l=50, r=50, t=40, b=40),
            legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------- Top Delighters/Detractors bar charts ----------
st.markdown("### 🧩 Top Delighters & Detractors (Bars)")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Top Detractors (by mentions)**")
    det = detractors_results_full.head(10)
    if det.empty:
        st.info("No detractor symptoms available.")
    else:
        fig_det = go.Figure(
            go.Bar(
                x=det["Mentions"][::-1],
                y=det["Item"][::-1],
                orientation="h",
                opacity=0.85,
                hovertemplate="%{y}<br>Mentions: %{x}<extra></extra>",
            )
        )
        fig_det.update_layout(template="plotly_white", margin=dict(l=140, r=20, t=20, b=20), height=420)
        st.plotly_chart(fig_det, use_container_width=True)
with c2:
    st.markdown("**Top Delighters (by mentions)**")
    deli = delighters_results_full.head(10)
    if deli.empty:
        st.info("No delighter symptoms available.")
    else:
        fig_del = go.Figure(
            go.Bar(
                x=deli["Mentions"][::-1],
                y=deli["Item"][::-1],
                orientation="h",
                opacity=0.85,
                hovertemplate="%{y}<br>Mentions: %{x}<extra></extra>",
            )
        )
        fig_del.update_layout(template="plotly_white", margin=dict(l=140, r=20, t=20, b=20), height=420)
        st.plotly_chart(fig_del, use_container_width=True)

# ---------- Avg ★ Over Time by Region — Cumulative (Weighted Over Time) ----------
st.markdown("### 📈 Cumulative Avg ★ Over Time by Region (Weighted)")

if "Review Date" not in filtered.columns or "Star Rating" not in filtered.columns:
    st.info("Need 'Review Date' and 'Star Rating' columns to compute this chart.")
else:
    c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 1.1, 0.9, 0.9])
    with c1:
        bucket_label = st.selectbox("Bucket size", ["Day", "Week", "Month"], index=2, key="region_bucket")
        _freq_map = {"Day": "D", "Week": "W", "Month": "M"}
        freq = _freq_map[bucket_label]
    with c2:
        _candidates = [c for c in ["Country", "Source", "Model (SKU)"] if c in filtered.columns]
        region_col = st.selectbox("Region field", options=_candidates or ["(none)"], key="region_col")
    with c3:
        top_n = st.number_input("Top regions by volume", 1, 15, value=5, step=1, key="region_topn")
    with c4:
        organic_only = st.toggle("Organic Only", value=False, help="Exclude reviews where Seeded == YES")
    with c5:
        show_volume = st.toggle(
            "Show Volume",
            value=True,
            help="Adds subtle bars + a right axis showing review count per bucket.",
        )

    if region_col not in filtered.columns or region_col == "(none)":
        st.info("No region column found for this chart.")
    else:
        d = filtered.copy()
        if organic_only and "Seeded" in d.columns:
            d = d[d["Seeded"].astype("string").str.upper().ne("YES")]

        d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
        d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
        d = d.dropna(subset=["Review Date", "Star Rating"])

        if d.empty:
            st.warning("No data available for the current selections.")
        else:
            counts = d[region_col].astype("string").str.strip().replace({"": pd.NA}).dropna()
            top_regions = counts.value_counts().head(int(top_n)).index.tolist()

            regions_available = sorted(
                r for r in d[region_col].astype("string").dropna().unique().tolist()
                if str(r).strip() != ""
            )
            chosen_regions = st.multiselect(
                "Regions to plot",
                options=regions_available,
                default=[r for r in top_regions if r in regions_available],
                key="region_pick",
            )

            if chosen_regions:
                d = d[d[region_col].astype("string").isin(chosen_regions)]

            if d.empty:
                st.warning("No data after region selection.")
            else:
                freq_eff = "W-MON" if freq == "W" else freq

                tmp = (
                    d.assign(_region=d[region_col].astype("string"))
                    .groupby([pd.Grouper(key="Review Date", freq=freq_eff), "_region"])["Star Rating"]
                    .agg(bucket_sum="sum", bucket_count="count")
                    .reset_index()
                    .sort_values(["_region", "Review Date"])
                )
                tmp["cum_sum"] = tmp.groupby("_region")["bucket_sum"].cumsum()
                tmp["cum_cnt"] = tmp.groupby("_region")["bucket_count"].cumsum()
                tmp["Cumulative Avg ★"] = tmp["cum_sum"] / tmp["cum_cnt"]

                overall = (
                    d.groupby(pd.Grouper(key="Review Date", freq=freq_eff))["Star Rating"]
                    .agg(bucket_sum="sum", bucket_count="count")
                    .reset_index()
                    .sort_values("Review Date")
                )
                overall["cum_sum"] = overall["bucket_sum"].cumsum()
                overall["cum_cnt"] = overall["bucket_count"].cumsum()
                overall["Cumulative Avg ★"] = overall["cum_sum"] / overall["cum_cnt"]

                fig = go.Figure()

                if show_volume and not overall.empty:
                    fig.add_trace(
                        go.Bar(
                            x=overall["Review Date"],
                            y=overall["bucket_count"],
                            name="Review volume",
                            yaxis="y2",
                            opacity=0.30,
                            marker=dict(color="rgba(15, 23, 42, 0.35)", line=dict(width=0)),
                            hovertemplate="Review volume<br>Bucket end: %{x|%Y-%m-%d}<br>Reviews: %{y}<extra></extra>",
                            showlegend=False,
                        )
                    )

                plot_regions = chosen_regions or tmp["_region"].unique().tolist()
                for reg in plot_regions:
                    sub = tmp[tmp["_region"] == reg]
                    if sub.empty:
                        continue
                    fig.add_trace(
                        go.Scatter(
                            x=sub["Review Date"],
                            y=sub["Cumulative Avg ★"],
                            mode="lines+markers",
                            name=str(reg),
                            line=dict(width=2),
                            marker=dict(size=5),
                            hovertemplate=(
                                f"{region_col}: {reg}<br>"
                                "Bucket end: %{x|%Y-%m-%d}<br>"
                                "Cumulative Avg ★: %{y:.3f}<br>"
                                "Cum. Reviews: %{customdata}<extra></extra>"
                            ),
                            customdata=sub["cum_cnt"],
                        )
                    )

                if not overall.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=overall["Review Date"],
                            y=overall["Cumulative Avg ★"],
                            mode="lines",
                            name="Overall",
                            line=dict(width=3, dash="dash"),
                            hovertemplate=(
                                "Overall<br>"
                                "Bucket end: %{x|%Y-%m-%d}<br>"
                                "Cumulative Avg ★: %{y:.3f}<br>"
                                "Cum. Reviews: %{customdata}<extra></extra>"
                            ),
                            customdata=overall["cum_cnt"],
                        )
                    )

                _tickformat = {"D": "%b %d, %Y", "W": "%b %d, %Y", "M": "%b %Y"}[freq]
                fig.update_xaxes(tickformat=_tickformat, automargin=True)
                fig.update_yaxes(automargin=True)

                title_bucket = {"D": "Daily", "W": "Weekly", "M": "Monthly"}[freq]
                title_org = " • Organic Only" if organic_only else ""
                fig.update_layout(
                    title=f"<b>{title_bucket} Cumulative (Weighted) Avg ★ by {region_col}{title_org}</b>",
                    xaxis=dict(title="Date", showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
                    yaxis=dict(title="Cumulative Avg ★", showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
                    yaxis2=dict(
                        title="Review volume",
                        overlaying="y",
                        side="right",
                        showgrid=False,
                        rangemode="tozero",
                        autorange=True,
                        visible=bool(show_volume),
                    ),
                    barmode="overlay",
                    hovermode="x unified",
                    plot_bgcolor="white",
                    template="plotly_white",
                    margin=dict(l=60, r=(60 if show_volume else 40), t=70, b=100),
                    legend=dict(orientation="h", yanchor="top", y=-0.28, xanchor="center", x=0.5, bgcolor="rgba(255,255,255,0)"),
                )

                ys = []
                for tr in fig.data:
                    if getattr(tr, "yaxis", "y") not in (None, "y"):
                        continue
                    if getattr(tr, "y", None) is not None:
                        try:
                            ys.extend([float(v) for v in tr.y if v is not None])
                        except Exception:
                            pass

                if ys:
                    y_min, y_max = min(ys), max(ys)
                    pad = max(0.1, (y_max - y_min) * 0.08)
                    lo = max(1.0, y_min - pad)
                    hi = min(5.2, y_max + pad)
                    fig.update_layout(yaxis_range=[lo, hi])

                fig.update_traces(cliponaxis=False, selector=dict(type="scatter"))
                st.plotly_chart(fig, use_container_width=True)

# ---------- Country × Source breakdown ----------
st.markdown("### 🧭 Country × Source Breakdown")
if "Country" in filtered.columns and "Source" in filtered.columns and "Star Rating" in filtered.columns:
    cA, cB, cC = st.columns([1, 1, 1])
    with cA:
        top_countries = st.number_input("Top Countries", 3, 30, value=10, step=1, key="cx_top_countries")
    with cB:
        top_sources = st.number_input("Top Sources", 3, 30, value=10, step=1, key="cx_top_sources")
    with cC:
        show_heatmap = st.toggle("Show Avg ★ Heatmap", value=False, key="cx_show_heatmap")

    d = filtered.copy()
    d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
    d = d.dropna(subset=["Star Rating"])
    if not d.empty:
        topC = d["Country"].astype("string").value_counts().head(int(top_countries)).index.tolist()
        topS = d["Source"].astype("string").value_counts().head(int(top_sources)).index.tolist()
        d = d[d["Country"].astype("string").isin(topC) & d["Source"].astype("string").isin(topS)]

        pivot = d.groupby(["Country", "Source"])["Star Rating"].agg(count="count", mean="mean").reset_index()
        pivot["mean"] = pivot["mean"].round(2)

        wide_count = pivot.pivot(index="Country", columns="Source", values="count").fillna(0).astype(int)
        wide_mean = pivot.pivot(index="Country", columns="Source", values="mean").round(2)

        with st.expander("📋 Table (Count + Avg ★)", expanded=True):
            st.caption("Count table shown first, Avg ★ table shown second.")
            st.markdown("**Counts**")
            st.dataframe(wide_count, use_container_width=True)
            st.markdown("**Avg ★**")
            st.dataframe(wide_mean, use_container_width=True)

        if show_heatmap:
            hm = wide_mean.copy()
            fig_hm = go.Figure(
                data=go.Heatmap(
                    z=hm.values,
                    x=list(hm.columns),
                    y=list(hm.index),
                    hovertemplate="Country=%{y}<br>Source=%{x}<br>Avg ★=%{z}<extra></extra>",
                )
            )
            fig_hm.update_layout(template="plotly_white", margin=dict(l=80, r=20, t=30, b=40), height=520)
            st.plotly_chart(fig_hm, use_container_width=True)
    else:
        st.info("No rating data available after filtering.")
else:
    st.info("Need Country, Source, and Star Rating columns to compute this breakdown.")

# ---------- Symptom Tables ----------
st.markdown("### 🩺 Symptom Tables")
detractors_results = detractors_results_full.head(50)
delighters_results = delighters_results_full.head(50)

view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0)

def _styled_table(df_in: pd.DataFrame):
    if df_in.empty:
        return df_in
    def colstyle(v):
        if pd.isna(v):
            return ""
        try:
            v = float(v)
            if v >= 4.5:
                return "color:#065F46;font-weight:600;"
            if v < 4.5:
                return "color:#7F1D1D;font-weight:600;"
        except Exception:
            pass
        return ""
    return df_in.style.applymap(colstyle, subset=["Avg Star"]).format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"})

if view_mode == "Split":
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("All Detractors")
        st.dataframe(_styled_table(detractors_results) if not detractors_results.empty else detractors_results, use_container_width=True, hide_index=True)
    with c2:
        st.subheader("All Delighters")
        st.dataframe(_styled_table(delighters_results) if not delighters_results.empty else delighters_results, use_container_width=True, hide_index=True)
else:
    tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
    with tab1:
        st.dataframe(_styled_table(detractors_results) if not detractors_results.empty else detractors_results, use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(_styled_table(delighters_results) if not delighters_results.empty else delighters_results, use_container_width=True, hide_index=True)

st.markdown("---")

# ---------- Reviews ----------
st.markdown("### 📝 All Reviews")

if not filtered.empty:
    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes, file_name="filtered_reviews.csv", mime="text/csv")

if "review_page" not in st.session_state:
    st.session_state["review_page"] = 0
reviews_per_page = st.session_state.get("reviews_per_page", 10)
total_reviews_count = len(filtered)
total_pages = max((total_reviews_count + reviews_per_page - 1) // reviews_per_page, 1)

st.session_state["review_page"] = min(st.session_state.get("review_page", 0), max(total_pages - 1, 0))

current_page = st.session_state["review_page"]
start_index = current_page * reviews_per_page
end_index = start_index + reviews_per_page
paginated = filtered.iloc[start_index:end_index]

if paginated.empty:
    st.warning("No reviews match the selected criteria.")
else:
    for _, row in paginated.iterrows():
        review_text = row.get("Verbatim", pd.NA)
        review_text = "" if pd.isna(review_text) else clean_text(review_text)
        display_review_html = highlight_html(review_text, st.session_state.get("kw", ""))

        date_val = row.get("Review Date", pd.NaT)
        if pd.isna(date_val):
            date_str = "-"
        else:
            try:
                date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
            except Exception:
                date_str = "-"

        def chips(row_i, columns, css_class):
            items = []
            for c in columns:
                val = row_i.get(c, pd.NA)
                if pd.isna(val):
                    continue
                s = str(val).strip()
                if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}:
                    continue
                items.append(f'<span class="badge {css_class}">{_html.escape(s)}</span>')
            return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

        delighter_message = chips(row, existing_delighter_columns, "pos")
        detractor_message = chips(row, existing_detractor_columns, "neg")

        star_val = row.get("Star Rating", 0)
        try:
            star_int = int(star_val) if pd.notna(star_val) else 0
        except Exception:
            star_int = 0

        st.markdown(
            f"""
            <div class="review-card">
                <p><strong>Source:</strong> {esc(row.get('Source'))} | <strong>Model:</strong> {esc(row.get('Model (SKU)'))}</p>
                <p><strong>Country:</strong> {esc(row.get('Country'))} | <strong>Date:</strong> {esc(date_str)}</p>
                <p><strong>Rating:</strong> {'⭐' * star_int} ({esc(row.get('Star Rating'))}/5)</p>
                <p><strong>Review:</strong> {display_review_html}</p>
                <div><strong>Delighter Symptoms:</strong> {delighter_message}</div>
                <div><strong>Detractor Symptoms:</strong> {detractor_message}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# Pager
p1, p2, p3, p4, p5 = st.columns([1, 1, 2, 1, 1])
with p1:
    if st.button("⏮ First", disabled=current_page == 0):
        st.session_state["review_page"] = 0
        st.rerun()
with p2:
    if st.button("⬅ Prev", disabled=current_page == 0):
        st.session_state["review_page"] = max(current_page - 1, 0)
        st.rerun()
with p3:
    showing_from = 0 if total_reviews_count == 0 else start_index + 1
    showing_to = min(end_index, total_reviews_count)
    st.markdown(
        f"<div style='text-align:center;font-weight:700;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
        unsafe_allow_html=True,
    )
with p4:
    if st.button("Next ➡", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = min(current_page + 1, total_pages - 1)
        st.rerun()
with p5:
    if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = total_pages - 1
        st.rerun()

st.markdown("---")

# ---------- Feedback ----------
anchor("feedback-anchor")
st.markdown("## 💬 Submit Feedback")
st.caption("Tell us what to improve. We care about making this tool user-centric.")

def send_feedback_via_email(subject: str, body: str) -> bool:
    try:
        host = st.secrets.get("SMTP_HOST")
        port = int(st.secrets.get("SMTP_PORT", 587))
        user = st.secrets.get("SMTP_USER")
        pwd = st.secrets.get("SMTP_PASS")
        sender = st.secrets.get("SMTP_FROM", user or "")
        to = st.secrets.get("SMTP_TO", "wseddon@sharkninja.com")
        if not (host and port and sender and to):
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            if user and pwd:
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as e:
        st.info(f"Could not send via SMTP: {e}")
        return False

with st.form("feedback_form", clear_on_submit=True):
    name = st.text_input("Your name (optional)")
    email = st.text_input("Your email (optional)")
    message = st.text_area("Feedback / feature request", placeholder="Type your feedback here…", height=140)
    submitted = st.form_submit_button("Submit feedback")
if submitted:
    if not message.strip():
        st.warning("Please enter some feedback before submitting.")
    else:
        body = f"Name: {name or '-'}\nEmail: {email or '-'}\n\nFeedback:\n{message}"
        ok = send_feedback_via_email("Star Walk — Feedback", body)
        if ok:
            st.success("Thanks! Your feedback was sent.")
        else:
            quoted = quote(message or "", safe="")
            st.link_button(
                "Open email to wseddon@sharkninja.com",
                url=f"mailto:wseddon@sharkninja.com?subject=Star%20Walk%20Feedback&body={quoted}",
            )

# one-time scroll on fresh upload
if st.session_state.get("force_scroll_top_once"):
    st.session_state["force_scroll_top_once"] = False
    st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)

