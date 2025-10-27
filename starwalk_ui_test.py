# starwalk_ui.py
# Streamlit 1.38+

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
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

# Optional FAISS for fast vector similarity
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}

def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

# ---------- Page config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Force Light Mode ----------
st_html("""
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
""", height=0)

# ---------- Global CSS ----------
GLOBAL_CSS = """
<style>
  /* =====================
     Global CSS — Light-first
     ===================== */
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }

  /* ---- Design tokens (light) ---- */
  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }

  /* ---- Dark tokens (kept for safety; app forced light) ---- */
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

  /* ---- Metric Cards ---- */
  .metrics-grid { display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:17px; }
  @media (max-width:1100px){ .metrics-grid { grid-template-columns:1fr; } }
  .metric-card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .metric-card h4{ margin:.2rem 0 .7rem 0; font-size:1.05rem; color:var(--text); }
  .metric-row{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .metric-box{ background:var(--bg-tile); border:1.6px solid var(--border); border-radius:12px; padding:12px; text-align:center; color:var(--text); }
  .metric-label{ color:var(--muted); font-size:.85rem; }
  .metric-kpi{ font-weight:800; font-size:1.8rem; letter-spacing:-0.01em; margin-top:2px; color:var(--text); }

  /* ---- Review Cards ---- */
  .review-card{ background:var(--bg-card); border-radius:12px; padding:16px; margin:16px 0 24px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .review-card p{ margin:.25rem 0; line-height:1.5; }

  /* ---- Hero ---- */
  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:150px; margin:.25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  #hero-canvas{ position:absolute; left:0; top:0; width:55%; height:100%; display:block; }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.3vw,42px); font-weight:800; margin:0; font-family:inherit; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); font-family:inherit; }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:48px; width:auto; display:block; }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }
</style>
"""

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


# ---------- Utilities ----------
def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    if _HAS_FTFY:
        try: s = _ftfy_fix(s)
        except Exception: pass
    if any(ch in s for ch in ("Ã","Â","â","ï","€","™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip(): s = repaired
        except Exception: pass
    for bad, good in {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def esc(x) -> str:
    """HTML-escape any dynamic value used inside unsafe_allow_html blocks."""
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
                    seen.add(v); vals.append(v)
    return vals

def is_valid_symptom_value(x) -> bool:
    if pd.isna(x): return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>","NA","N/A","NULL","NONE"}: return False
    return not bool(re.fullmatch(r"[\W_]+", s))

def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    s = (filtered_df[cols].stack(dropna=True)
         .map(lambda v: clean_text(v, keep_na=True)).dropna()
         .astype("string").str.strip())
    s = s[s.map(is_valid_symptom_value)]
    if s.empty: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    unique_items = pd.unique(s.to_numpy())
    results, total_rows = [], len(filtered_df)
    for item in unique_items:
        item_str = str(item).strip()
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0: continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean() if "Star Rating" in filtered_df.columns else np.nan
        pct = (count / total_rows * 100) if total_rows else 0
        results.append({"Item": item_str.title(),
                        "Avg Star": round(avg_star,1) if pd.notna(avg_star) else None,
                        "Mentions": count,
                        "% Total": f"{round(pct,1)}%"})
    if not results: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)

def highlight_html(text: str, keyword: str | None) -> str:
    safe = _html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error:
            pass
    return safe


# LLM helpers ---------------------------------------------------
@st.cache_resource(show_spinner=False)
def build_vector_index(texts: list[str], api_key: str, model: str = "text-embedding-3-small"):
    if not _HAS_OPENAI or not texts:
        return None
    client = OpenAI(api_key=api_key)
    embs = []
    batch = 512
    for i in range(0, len(texts), batch):
        chunk = texts[i:i+batch]
        resp = client.embeddings.create(model=model, input=chunk)
        embs.extend([np.array(d.embedding, dtype=np.float32) for d in resp.data])
    if not embs: return None
    mat = np.vstack(embs).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    mat_norm = mat / norms
    if _HAS_FAISS:
        index = faiss.IndexFlatIP(mat_norm.shape[1])
        index.add(mat_norm)
        return {"backend":"faiss","index":index,"texts":texts}
    return (mat, norms, texts)

def vector_search(query: str, index, api_key: str, top_k: int = 8):
    if not _HAS_OPENAI or index is None: return []
    client = OpenAI(api_key=api_key)
    qemb = client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding
    q = np.array(qemb, dtype=np.float32)
    qn = np.linalg.norm(q) + 1e-8
    qn_vec = q / qn
    if isinstance(index, dict) and index.get("backend") == "faiss":
        D, I = index["index"].search(qn_vec.reshape(1,-1), top_k)
        sims = D[0].tolist(); idxs = I[0].tolist(); texts = index["texts"]
        return [(texts[i], float(sims[j])) for j, i in enumerate(idxs) if i != -1]
    mat, norms, texts = index
    sims = (mat @ q) / (norms.flatten() * qn)
    idx = np.argsort(-sims)[:top_k]
    return [(texts[i], float(sims[i])) for i in idx]

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
        unsafe_allow_html=True
    )

# ---------- File Upload ----------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

if uploaded_file and st.session_state.get("last_uploaded_name") != uploaded_file.name:
    st.session_state["last_uploaded_name"] = uploaded_file.name
    st.session_state["force_scroll_top_once"] = True

if not uploaded_file:
    st.info("Please upload an Excel file to get started.")
    st.stop()

# ---------- Load & clean ----------
try:
    st.markdown("---")
    try:
        df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
    except ValueError:
        # Fallback to first sheet if the named sheet doesn't exist
        df = pd.read_excel(uploaded_file)

    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.upper()
    if "Star Rating" in df.columns:
        df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

    all_symptom_cols = [c for c in df.columns if c.startswith("Symptom")]
    for c in all_symptom_cols:
        df[c] = df[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

    if "Verbatim" in df.columns:
        df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
    if "Review Date" in df.columns:
        df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")
except Exception as e:
    st.error(f"An error occurred: {e}")
    st.stop()

# ---------- Sidebar filters ----------
st.sidebar.header("🔍 Filters")

with st.sidebar.expander("🗓️ Timeframe", expanded=False):
    timeframe = st.selectbox("Select Timeframe",
                             options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"],
                             key="tf")
    today = datetime.today().date()
    start_date, end_date = None, None
    if timeframe == "Custom Range":
        start_date, end_date = st.date_input(
            label="Date Range",
            value=(today - timedelta(days=30), today),
            min_value=datetime(2000, 1, 1).date(),
            max_value=today,
            label_visibility="collapsed"
        )
    elif timeframe == "Last Week":  start_date, end_date = today - timedelta(days=7), today
    elif timeframe == "Last Month": start_date, end_date = today - timedelta(days=30), today
    elif timeframe == "Last Year":  start_date, end_date = today - timedelta(days=365), today

filtered = df.copy()
if start_date and end_date and "Review Date" in filtered.columns:
    filtered = filtered[
        (filtered["Review Date"] >= pd.Timestamp(start_date)) &
        (filtered["Review Date"] <= pd.Timestamp(end_date))
    ]

with st.sidebar.expander("🌟 Star Rating", expanded=False):
    selected_ratings = st.multiselect("Select Star Ratings", options=["All"] + [1,2,3,4,5],
                                      default=["All"], key="sr")
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
    selected_delighter = st.multiselect("Select Delighter Symptoms",
                                        options=["All"] + sorted(delighter_symptoms),
                                        default=["All"], key="delight")
    selected_detractor = st.multiselect("Select Detractor Symptoms",
                                        options=["All"] + sorted(detractor_symptoms),
                                        default=["All"], key="detract")
if "All" not in selected_delighter and existing_delighter_columns:
    filtered = filtered[filtered[existing_delighter_columns].isin(selected_delighter).any(axis=1)]
if "All" not in selected_detractor and existing_detractor_columns:
    filtered = filtered[filtered[existing_detractor_columns].isin(selected_detractor).any(axis=1)]

with st.sidebar.expander("🔎 Keyword", expanded=False):
    keyword = st.text_input("Keyword to search (in review text)", value="", key="kw",
                            help="Case-insensitive match in review text. Cleans â€™ → '")
    if keyword and "Verbatim" in filtered.columns:
        mask_kw = filtered["Verbatim"].astype("string").fillna("").str.contains(keyword.strip(), case=False, na=False)
        filtered = filtered[mask_kw]

core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
symptom_cols = set([f"Symptom {i}" for i in range(1,21)])
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
    for k in ["tf","sr","kw","delight","detract","rpp","review_page","llm_model","llm_model_label","llm_temp"] + \
             [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
        if k in st.session_state: del st.session_state[k]
    st.rerun()

# Spacer + divider before LLM assistant
st.sidebar.write("")
st.sidebar.markdown("---")

# ---------- LLM settings ----------
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
        help=("Lower = more deterministic, higher = more creative. Some models ignore this.")
    )
    if not temp_supported:
        st.caption("ℹ️ This model uses a fixed temperature; the slider is disabled.")

# ---------- Ask your data (LLM) — moved up, with disclaimer ----------
anchor("askdata-anchor")
st.markdown("## 🤖 Ask your data")
st.markdown(
    '<div class="callout warn">⚠️ AI can make mistakes. Numbers are computed from the filtered data via tools, '
    'but always double-check important conclusions.</div>',
    unsafe_allow_html=True
)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI:
    st.info("To enable this panel, add `openai` to your requirements and redeploy. Then set `OPENAI_API_KEY`.")
elif not api_key:
    st.info("Set your `OPENAI_API_KEY` (in env or .streamlit/secrets.toml) to chat with the filtered data.")
else:
    verb_series = filtered.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str).map(clean_text)
    index = build_vector_index(verb_series.tolist(), api_key)

    # Ask form; no state is kept after this run
    with st.form("ask_ai_form", clear_on_submit=True):
        q = st.text_area("Ask a question", value="", height=80)
        send = st.form_submit_button("Send")

    if send and q.strip():
        q = q.strip()
        # retrieval
        retrieved = vector_search(q, index, api_key, top_k=8) if index else []
        quotes = []
        for txt, sim in retrieved[:5]:
            s = (txt or "").strip()
            if len(s) > 0:
                if len(s) > 320: s = s[:317] + "…"
                quotes.append(f"• “{s}”")
        quotes_text = "\n".join(quotes) if quotes else "• (No close review snippets retrieved.)"

        # ------ Safe helpers for pandas.query ------
        def _safe_query(qs: str) -> bool:
            if not qs or len(qs) > 200: return False
            bad = ["__", "@", "import", "exec", "eval", "os.", "pd.", "open(", "read", "write", "globals", "locals", "`"]
            if any(t in qs.lower() for t in bad): return False
            return bool(re.fullmatch(r"[A-Za-z0-9_ .<>=!&|()'\"-]+", qs))

        # tools
        def pandas_count(query: str) -> dict:
            try:
                if not _safe_query(query): return {"error":"unsafe query"}
                res = filtered.query(query)  # default engine (numexpr where possible)
                return {"count": int(len(res))}
            except Exception as e:
                return {"error": str(e)}

        def pandas_mean(column: str, query: str | None = None) -> dict:
            try:
                if column not in filtered.columns: return {"error": f"Unknown column {column}"}
                d = filtered if not query else (filtered.query(query) if _safe_query(query) else None)
                if d is None: return {"error":"unsafe query"}
                return {"mean": float(pd.to_numeric(d[column], errors='coerce').mean())}
            except Exception as e:
                return {"error": str(e)}

        def symptom_stats(symptom: str) -> dict:
            cols = [c for c in [f"Symptom {i}" for i in range(1,21)] if c in filtered.columns]
            if not cols: return {"count":0,"avg_star":None}
            mask = filtered[cols].isin([symptom]).any(axis=1)
            d = filtered[mask]
            return {"count": int(len(d)), "avg_star": float(pd.to_numeric(d["Star Rating"], errors="coerce").mean()) if len(d) and "Star Rating" in d.columns else None}

        def keyword_stats(term: str) -> dict:
            if "Verbatim" not in filtered.columns: return {"count": 0, "pct": 0.0}
            ser = filtered["Verbatim"].astype("string").fillna("")
            cnt = int(ser.str.contains(term, case=False, na=False).sum())
            pct = (cnt / max(1,len(filtered))) * 100.0
            return {"count": cnt, "pct": pct}

        def get_metrics_snapshot():
            try:
                return {
                    "total_reviews": int(len(filtered)),
                    "avg_star": float(pd.to_numeric(filtered.get("Star Rating"), errors="coerce").mean()) if len(filtered) and "Star Rating" in filtered.columns else 0.0,
                    "low_star_pct_1_2": float((pd.to_numeric(filtered.get("Star Rating"), errors="coerce") <= 2).mean() * 100) if len(filtered) and "Star Rating" in filtered.columns else 0.0,
                    "star_counts": pd.to_numeric(filtered.get("Star Rating"), errors="coerce").value_counts().sort_index().to_dict() if "Star Rating" in filtered.columns else {},
                }
            except Exception as e:
                return {"error": str(e)}

        # local fallback
        def _local_answer_fallback() -> str:
            try:
                parts = []
                total = int(len(filtered))
                avg = float(pd.to_numeric(filtered.get("Star Rating"), errors="coerce").mean()) if total and "Star Rating" in filtered.columns else 0.0
                low_pct = float((pd.to_numeric(filtered.get("Star Rating"), errors="coerce") <= 2).mean() * 100) if total and "Star Rating" in filtered.columns else 0.0
                parts.append(f"**Snapshot** — {total} reviews; avg ★ {avg:.1f}; % 1–2★ {low_pct:.1f}%.")

                detr = analyze_delighters_detractors(filtered, [c for c in existing_detractor_columns]).head(5)
                deli = analyze_delighters_detractors(filtered, [c for c in existing_delighter_columns]).head(5)
                def fmt(df):
                    if df.empty: return "None"
                    return "; ".join([f"{r['Item']} (avg ★ {r['Avg Star']}, {int(r['Mentions'])} mentions)" for _, r in df.iterrows()])
                parts.append("**Top detractors:** " + fmt(detr))
                parts.append("**Top delighters:** " + fmt(deli))
                return "\n\n".join(parts)
            except Exception as e:
                return f"(Fallback summary error: {e})"

        # compose single-turn request
        selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
        llm_temp = float(st.session_state.get("llm_temp", 0.2))
        tools = [
            {"type":"function","function":{
                "name":"pandas_count","description":"Count rows matching a pandas query over the CURRENT filtered dataset.",
                "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
            {"type":"function","function":{
                "name":"pandas_mean","description":"Mean of a numeric column (optional pandas query).",
                "parameters":{"type":"object","properties":{"column":{"type":"string"},"query":{"type":"string"}},"required":["column"]}}},
            {"type":"function","function":{
                "name":"symptom_stats","description":"Mentions + avg star for a symptom across Symptom 1–20.",
                "parameters":{"type":"object","properties":{"symptom":{"type":"string"}},"required":["symptom"]}}},
            {"type":"function","function":{
                "name":"keyword_stats","description":"Count + % of reviews with term in Verbatim.",
                "parameters":{"type":"object","properties":{"term":{"type":"string"}},"required":["term"]}}},
            {"type":"function","function":{
                "name":"get_metrics_snapshot","description":"Return current page metrics.",
                "parameters":{"type":"object","properties":{}}}},
        ]
        sys_ctx = (
            "You are a helpful analyst for customer reviews. Use ONLY the provided context and tool results.\n"
            "Include short quotes from retrieved snippets when illustrative. Prefer tools for exact numbers.\n\n"
            "RETRIEVED_SNIPPETS:\n" + (quotes_text or "(none)") + f"\n\nROW_COUNT={len(filtered)}"
        )

        try:
            client = OpenAI(api_key=api_key)
            req = {"model": selected_model,
                   "messages":[{"role":"system","content":sys_ctx},{"role":"user","content":q}],
                   "tools": tools}
            if model_supports_temperature(selected_model): req["temperature"] = llm_temp
            with st.spinner("Thinking..."):
                first = client.chat.completions.create(**req)
            msg = first.choices[0].message
            tool_msgs = []
            if msg.tool_calls:
                for call in msg.tool_calls:
                    name = call.function.name
                    args = json.loads(call.function.arguments or "{}")
                    out = {"error":"unknown tool"}
                    if name == "pandas_count": out = pandas_count(args.get("query",""))
                    if name == "pandas_mean":  out = pandas_mean(args.get("column",""), args.get("query"))
                    if name == "symptom_stats": out = symptom_stats(args.get("symptom",""))
                    if name == "keyword_stats": out = keyword_stats(args.get("term",""))
                    if name == "get_metrics_snapshot": out = get_metrics_snapshot()
                    tool_msgs.append({"tool_call_id": call.id, "role":"tool", "name": name, "content": json.dumps(out)})
            if tool_msgs:
                follow = {"model": selected_model,
                          "messages":[
                              {"role":"system","content":sys_ctx},
                              {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                              *tool_msgs
                          ]}
                if model_supports_temperature(selected_model): follow["temperature"] = llm_temp
                res2 = client.chat.completions.create(**follow)
                final_text = res2.choices[0].message.content
            else:
                final_text = msg.content
        except Exception:
            final_text = _local_answer_fallback()

        # show the single Q/A (not stored)
        st.markdown(f"<div class='chat-q'><b>User:</b> {esc(q)}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)

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

# Robust Seeded split (handles missing column)
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
        '</div>'
    ),
    unsafe_allow_html=True,
)

# Distribution chart (guard for missing column)
if "Star Rating" in filtered.columns:
    star_counts = pd.to_numeric(filtered["Star Rating"], errors="coerce").dropna().value_counts().sort_index()
else:
    star_counts = pd.Series([], dtype="int")
total_reviews = len(filtered)
percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
star_labels = [f"{int(star)} stars" for star in star_counts.index]

fig_bar_horizontal = go.Figure(go.Bar(
    x=star_counts.values, y=star_labels, orientation="h",
    text=[f"{value} reviews ({percentages.get(idx, 0)}%)"
          for idx, value in zip(star_counts.index, star_counts.values)],
    textposition="auto",
    marker=dict(color=["#EF4444", "#F59E0B", "#EAB308", "#10B981", "#22C55E"]),
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


# ---------- Symptom Tables ----------
st.markdown("### 🩺 Symptom Tables")
detractors_results = analyze_delighters_detractors(filtered, existing_detractor_columns).head(20)
delighters_results = analyze_delighters_detractors(filtered, existing_delighter_columns).head(20)

view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0)

def _styled_table(df_in: pd.DataFrame):
    if df_in.empty: return df_in
    def colstyle(v):
        if pd.isna(v): return ""
        try:
            v = float(v)
            if v >= 4.5: return "color:#065F46;font-weight:600;"
            if v < 4.5:  return "color:#7F1D1D;font-weight:600;"
        except: pass
        return ""
    return df_in.style.applymap(colstyle, subset=["Avg Star"]) \
                      .format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"})

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

# ---------- Reviews ----------
st.markdown("### 📝 All Reviews")

if not filtered.empty:
    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes,
                       file_name="filtered_reviews.csv", mime="text/csv")

if "review_page" not in st.session_state: st.session_state["review_page"] = 0
reviews_per_page = st.session_state.get("reviews_per_page", 10)
total_reviews_count = len(filtered)
total_pages = max((total_reviews_count + reviews_per_page - 1) // reviews_per_page, 1)
current_page = min(max(st.session_state["review_page"], 0), total_pages - 1)
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
        if pd.isna(date_val): date_str = "-"
        else:
            try: date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
            except Exception: date_str = "-"

        def chips(row, columns, css_class):
            items = []
            for c in columns:
                val = row.get(c, pd.NA)
                if pd.isna(val): continue
                s = str(val).strip()
                if not s or s.upper() in {"<NA>","NA","N/A","-"}: continue
                items.append(f'<span class="badge {css_class}">{_html.escape(s)}</span>')
            return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

        delighter_message = chips(row, existing_delighter_columns, "pos")
        detractor_message = chips(row, existing_detractor_columns, "neg")

        star_val = row.get("Star Rating", 0)
        try: star_int = int(star_val) if pd.notna(star_val) else 0
        except Exception: star_int = 0

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
            unsafe_allow_html=True
        )

# Pager
p1, p2, p3, p4, p5 = st.columns([1,1,2,1,1])
with p1:
    if st.button("⏮ First", disabled=current_page == 0):
        st.session_state["review_page"] = 0; st.rerun()
with p2:
    if st.button("⬅ Prev", disabled=current_page == 0):
        st.session_state["review_page"] = max(current_page - 1, 0); st.rerun()
with p3:
    showing_from = 0 if total_reviews_count == 0 else start_index + 1
    showing_to = min(end_index, total_reviews_count)
    st.markdown(
        f"<div style='text-align:center;font-weight:700;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
        unsafe_allow_html=True,
    )
with p4:
    if st.button("Next ➡", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = min(current_page + 1, total_pages - 1); st.rerun()
with p5:
    if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = total_pages - 1; st.rerun()

st.markdown("---")

# ---------- Feedback (anchor fallback) ----------
anchor("feedback-anchor")
st.markdown("## 💬 Submit Feedback")
st.caption("Tell us what to improve. We care about making this tool user-centric.")

def send_feedback_via_email(subject: str, body: str) -> bool:
    try:
        host = st.secrets.get("SMTP_HOST")
        port = int(st.secrets.get("SMTP_PORT", 587))
        user = st.secrets.get("SMTP_USER")
        pwd  = st.secrets.get("SMTP_PASS")
        sender = st.secrets.get("SMTP_FROM", user or "")
        to = st.secrets.get("SMTP_TO", "wseddon@sharkninja.com")
        if not (host and port and sender and to):
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            if user and pwd: s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
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
        if ok: st.success("Thanks! Your feedback was sent.")
        else:
            st.link_button("Open email to wseddon@sharkninja.com",
                           url=f"mailto:wseddon@sharkninja.com?subject=Star%20Walk%20Feedback&body={_html.escape(message)}")

# one-time scroll on fresh upload
if st.session_state.get("force_scroll_top_once"):
    st.session_state["force_scroll_top_once"] = False
    st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)

