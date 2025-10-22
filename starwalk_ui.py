# starwalk_ui.py
# Streamlit 1.38+

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
from wordcloud import STOPWORDS  # kept only for stopword set; not rendering WC
import re
import html as _html
import os
import json
import textwrap
import warnings
import hashlib
import smtplib
from email.message import EmailMessage
from streamlit.components.v1 import html as st_html

warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# ---------- Optional high-quality text fixer ----------
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

# Optional FAISS for fast vector search
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

# ---------- Global CSS ----------
st.markdown(
    """
    <style>
      :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
      .block-container { padding-top: .75rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] .block-container { padding-top: .5rem; }
      section[data-testid="stSidebar"] .stButton>button { width: 100%; }
      section[data-testid="stSidebar"] .stSelectbox label, 
      section[data-testid="stSidebar"] .stMultiSelect label { font-size: .95rem; }
      section[data-testid="stSidebar"] .stExpander { border-radius: 10px; }

      mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }

      /* Cards (grouped look with more gray) */
      .review-card { border:1px solid #E5E7EB; background:#FFFFFF; border-radius:12px; padding:16px; }
      .review-card p { margin:.25rem 0; line-height:1.45; }

      .metrics-grid { display:grid; grid-template-columns: repeat(3, minmax(260px, 1fr)); gap:20px; }
      @media (max-width: 1100px){ .metrics-grid { grid-template-columns: 1fr; } }

      .metric-card {
        background:#F7F9FC;
        border:1px solid #E3E8F0;
        border-radius:14px;
        padding:14px 16px;
        box-shadow:0 1px 2px rgba(16,24,40,0.04);
      }
      .metric-card h4 { margin:.3rem 0 .6rem 0; font-size:1.05rem; color:#111827; }

      .metric-row { display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; }
      .metric-box {
        background:#F2F5FA;
        border:1px solid #E3E8F0;
        border-radius:12px;
        padding:12px;
        text-align:center;
      }
      .metric-label { color:#6b7280; font-size:.85rem; }
      .metric-kpi { font-weight:800; font-size: 1.8rem; margin-top:2px; }

      /* Pager */
      .pager { margin: 22px 0 14px; display:grid; grid-template-columns: 140px 140px 1fr 140px 140px; gap:18px; align-items:center; }
      .pager .center { text-align:center; font-weight:700; }

      /* Chat bubbles */
      .chat-q { background:#F5F7FB; border:1px solid #E5E7EB; border-radius:14px; padding:10px 12px; }
      .chat-a { background:#FFF8EB; border:1px solid #FBE3B2; border-radius:14px; padding:12px 12px; }

      /* Badges */
      .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
      .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:.95rem; }
      .badge.pos { background:#E7F8EE; color:#065F46; }
      .badge.neg { background:#FDECEC; color:#7F1D1D; }

      /* Hero */
      .hero-wrap {
        position: relative; overflow: hidden; border-radius: 14px;
        border: 1px solid #eee; height: 150px; margin: .25rem 0 1rem 0;
        background: linear-gradient(90deg,#ffffff 0%,#ffffff 55%,#f7f7f7 55%,#f7f7f7 100%);
      }
      #hero-canvas { position:absolute; left:0; top:0; width:55%; height:100%; }
      .hero-inner {
        position: absolute; inset: 0; display:flex; align-items:center; justify-content:space-between;
        padding: 0 18px;
      }
      .hero-title { font-size: clamp(22px, 3.3vw, 42px); font-weight: 800; margin:0; }
      .hero-sub { margin: 4px 0 0 0; color:#667085; font-size: clamp(12px, 1.1vw, 16px); }
      .hero-left { display:flex; gap:16px; align-items:center; }
      .sn-logo { width: 170px; height:auto; }
      .hero-right { display:flex; align-items:center; justify-content:flex-end; width:40%; }

      /* Section dividers */
      .section-divider { height:1px; background:#eee; margin:24px 0 14px; }
      .mini-caption { color:#6b7280; font-size:.9rem; margin-bottom:.4rem; }

      /* Dark theme readability */
      @media (prefers-color-scheme: dark){
        .review-card, .metric-card, .metric-box, .chat-q, .chat-a {
          background: rgba(255,255,255,0.06) !important;
          border-color: rgba(255,255,255,0.18) !important;
        }
        .metric-label { color: rgba(255,255,255,0.75); }
        .hero-wrap { border-color: rgba(255,255,255,0.18); }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Hero ----------
def render_hero():
    sharkninja_svg = """
    <svg class="sn-logo" viewBox="0 0 520 90" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="SharkNinja">
      <g fill="#111">
        <text x="0" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="800" font-size="52">Shark</text>
        <rect x="225" y="12" width="4" height="66" rx="2" fill="#222"/>
        <text x="245" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="900" font-size="52">NINJA</text>
      </g>
    </svg>
    """.strip()

    st_html(
        f"""
        <div class="hero-wrap" id="top-hero">
          <canvas id="hero-canvas"></canvas>
          <div class="hero-inner">
            <div class="hero-left">
              <div>
                <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
                <div class="hero-sub">Insights, trends, and ratings — fast.</div>
              </div>
            </div>
            <div class="hero-right">{sharkninja_svg}</div>
          </div>
        </div>
        <script>
        (function(){{
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

          let N = 140;
          let stars = Array.from({{length:N}}, () => ({{
            x: Math.random()*w, y: Math.random()*h,
            r: 0.6 + Math.random()*1.4, s: 0.3 + Math.random()*0.9
          }}));
          function tick(){{
            ctx.clearRect(0,0,w,h);
            for(const s of stars){{
              ctx.beginPath();
              ctx.arc(s.x, s.y, s.r, 0, Math.PI*2);
              ctx.fillStyle = 'rgba(255,200,50,.9)';
              ctx.fill();
              s.x += 0.12*s.s; if(s.x > w) s.x = 0;
            }}
            requestAnimationFrame(tick);
          }}
          tick();
        }})();
        </script>
        """,
        height=160,
    )

render_hero()

# ---------- Utilities ----------
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

def highlight_html(text: str, keyword: str | None) -> str:
    safe = _html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error: pass
    return safe

# LLM helpers ---------------------------------------------------
def _hash_series_for_cache(s: pd.Series) -> str:
    data = "|".join(map(str, s.fillna("").tolist()))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

@st.cache_resource(show_spinner=False)
def build_vector_index(texts: list[str], api_key: str, model: str = "text-embedding-3-small"):
    """
    Returns a FAISS index (if available) or (emb_matrix, norms, texts) tuple for cosine similarity search.
    """
    if not _HAS_OPENAI:
        return None
    if not texts:
        return None
    client = OpenAI(api_key=api_key)
    batch = 512
    embs = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i+batch]
        resp = client.embeddings.create(model=model, input=chunk)
        embs.extend([np.array(d.embedding, dtype=np.float32) for d in resp.data])
    if not embs:
        return None
    mat = np.vstack(embs).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    mat_norm = mat / norms
    if _HAS_FAISS:
        index = faiss.IndexFlatIP(mat_norm.shape[1])
        index.add(mat_norm)
        return {"backend":"faiss","index":index,"texts":texts}
    else:
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
        sims = D[0].tolist()
        idxs = I[0].tolist()
        texts = index["texts"]
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
          setTimeout(jump, 0);
          setTimeout(jump, 150);
          setTimeout(jump, 300);
          setTimeout(jump, 600);
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
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
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
    mask = filtered[existing_delighter_columns].isin(selected_delighter).any(axis=1)
    filtered = filtered[mask]
if "All" not in selected_detractor and existing_detractor_columns:
    mask = filtered[existing_detractor_columns].isin(selected_detractor).any(axis=1)
    filtered = filtered[mask]

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

# LLM settings (model picker) and anchor buttons
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
              "Some models (e.g., GPT-5 family) use a fixed temperature and ignore this setting.")
    )
    if not temp_supported:
        st.caption("ℹ️ This model uses a fixed sampling temperature; the slider is disabled.")

    if st.button("Go to Ask AI"):
        scroll_to("askdata-anchor")

# ---- Submit Feedback (modal first; falls back to anchor) ----
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

_DIALOG_AVAILABLE = hasattr(st, "dialog")

if _DIALOG_AVAILABLE:
    @st.dialog("💬 Submit Feedback", width="large")
    def open_feedback_dialog():
        st.caption("Tell us what to improve. We care about making this tool user-centric.")
        with st.form("feedback_modal_form", clear_on_submit=True):
            name = st.text_input("Your name (optional)", key="fb_modal_name")
            email = st.text_input("Your email (optional)", key="fb_modal_email")
            message = st.text_area("Feedback / feature request", height=160, key="fb_modal_msg")
            send_modal = st.form_submit_button("Submit feedback")
        if send_modal:
            if not message.strip():
                st.warning("Please enter some feedback before submitting.")
                st.stop()
            body = f"Name: {name or '-'}\\nEmail: {email or '-'}\\n\\nFeedback:\\n{message}"
            ok = send_feedback_via_email("Star Walk — Feedback", body)
            if ok:
                st.success("Thanks! Your feedback was sent.")
            else:
                st.info("Email sending isn’t configured. Opening your mail client instead.")
                st.link_button(
                    "Open email to wseddon@sharkninja.com",
                    url=f"mailto:wseddon@sharkninja.com?subject=Star%20Walk%20Feedback&body={_html.escape(message)}"
                )
            st.stop()  # close dialog

# Button outside the expander (per request)
if st.sidebar.button("Submit Feedback", key="submit_feedback_sidebar"):
    if _DIALOG_AVAILABLE:
        open_feedback_dialog()
    else:
        scroll_to("feedback-anchor")

st.markdown("---")

# ---------- Metrics ----------
st.markdown("## ⭐ Star Rating Metrics")
st.caption("All metrics below reflect the **currently filtered** dataset.")

def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty: return 0.0
    return float((s <= 2).mean() * 100)

def section_stats(sub: pd.DataFrame) -> tuple[int, float, float]:
    cnt = len(sub)
    avg = float(sub["Star Rating"].mean()) if cnt else 0.0
    pct = pct_12(sub["Star Rating"]) if cnt else 0.0
    return cnt, avg, pct

all_cnt, all_avg, all_low = section_stats(filtered)
org = filtered[filtered.get("Seeded","").astype("string").str.upper() != "YES"]
seed = filtered[filtered.get("Seeded","").astype("string").str.upper() == "YES"]
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

# Distribution chart
star_counts = filtered["Star Rating"].value_counts().sort_index()
total_reviews = len(filtered)
percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
star_labels = [f"{int(star)} stars" for star in star_counts.index]

mc1, mc2 = st.columns(2)
with mc1: st.metric("Total Reviews", f"{total_reviews:,}")
with mc2: st.metric("Avg Star Rating", f"{all_avg:.1f}", delta_color="inverse")

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

st.markdown("---")

# ---------- Country Breakdown ----------
st.markdown("### 🌍 Country-Specific Breakdown")
if "Country" in filtered.columns and "Source" in filtered.columns:
    new_review_filtered = filtered[filtered["New Review"].astype("string").str.upper() == "YES"]
    country_source_stats = (
        filtered.groupby(["Country", "Source"])
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
        filtered.groupby("Country")
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

# ---------- Symptom Tables ----------
st.markdown("### 🩺 Symptom Tables")
detractors_results = analyze_delighters_detractors(filtered, existing_detractor_columns).head(20)
delighters_results = analyze_delighters_detractors(filtered, existing_delighter_columns).head(20)

view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0)

def _styled_table(df_in: pd.DataFrame):
    if df_in.empty: return df_in
    return df_in.style.applymap(style_rating_cells, subset=["Avg Star"]) \
                      .format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"})

if view_mode == "Split":
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("All Detractors")
        if detractors_results.empty: st.write("None")
        else: st.dataframe(_styled_table(detractors_results), use_container_width=True, hide_index=True)
    with c2:
        st.subheader("All Delighters")
        if delighters_results.empty: st.write("None")
        else: st.dataframe(_styled_table(delighters_results), use_container_width=True, hide_index=True)
else:
    tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
    with tab1:
        if detractors_results.empty: st.write("None")
        else: st.dataframe(_styled_table(detractors_results), use_container_width=True, hide_index=True)
    with tab2:
        if delighters_results.empty: st.write("None")
        else: st.dataframe(_styled_table(delighters_results), use_container_width=True, hide_index=True)

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

        def render_chips(row, columns, css_class):
            items = []
            for c in columns:
                val = row.get(c, pd.NA)
                if pd.isna(val): continue
                s = str(val).strip()
                if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}: continue
                items.append(f'<span class="badge {css_class}">{_html.escape(s)}</span>')
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

# Pager with spacing
st.markdown(
    f"""
    <div class="pager">
      <div style="display:flex;justify-content:flex-start;">
        <form action="" method="post">
        </form>
      </div>
    </div>
    """, unsafe_allow_html=True
)
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
        f"<div class='center'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
        unsafe_allow_html=True,
    )
with p4:
    if st.button("Next ➡", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = min(current_page + 1, total_pages - 1); st.rerun()
with p5:
    if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
        st.session_state["review_page"] = total_pages - 1; st.rerun()

st.markdown("---")

# ---------- Ask your data (LLM) ----------
anchor("askdata-anchor")
st.markdown("## 🤖 Ask your data")
st.caption("Ask questions about the **currently filtered** reviews. We’ll combine programmatic stats with semantic search over verbatims.")

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI:
    st.info("To enable this panel, add `openai` to your requirements and redeploy. Then set `OPENAI_API_KEY`.")
elif not api_key:
    st.info("Set your `OPENAI_API_KEY` (in env or .streamlit/secrets.toml) to chat with the filtered data.")
else:
    # Build (or reuse cached) vector index for current filtered set
    verb_series = filtered.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str).map(clean_text)
    index = build_vector_index(verb_series.tolist(), api_key)

    # Prepare messages (compact display)
    st.session_state.setdefault("qa_messages", [])
    if st.session_state.get("qa_messages") and len(st.session_state["qa_messages"]) > 12:
        st.session_state["qa_messages"] = st.session_state["qa_messages"][-12:]

    # Controls row
    cc1, cc2 = st.columns([1,1])
    with cc1:
        if st.button("Start new chat"):
            archives = st.session_state.get("qa_archives", [])
            if st.session_state["qa_messages"]:
                archives.append(st.session_state["qa_messages"])
                st.session_state["qa_archives"] = archives
            st.session_state["qa_messages"] = []
            st.experimental_rerun()
    with cc2:
        if st.button("Clear chat"):
            st.session_state["qa_messages"] = []
            st.experimental_rerun()

    # Show last few exchanges; older behind an expander
    older = st.session_state["qa_messages"][:-2]
    latest = st.session_state["qa_messages"][-2:]
    if older:
        with st.expander("Show previous Q&A"):
            for role, content in older:
                st.markdown(f"<div class='chat-{'q' if role=='user' else 'a'}'><b>{role.title()}:</b> {content}</div>", unsafe_allow_html=True)
    for role, content in latest:
        st.markdown(f"<div class='chat-{'q' if role=='user' else 'a'}'><b>{role.title()}:</b> {content}</div>", unsafe_allow_html=True)

    # Input form (NOT pinned)
    with st.form("ask_ai_form", clear_on_submit=False):
        q = st.text_area("Ask a question",
                         value=st.session_state.get("ask_ai_text", ""),
                         height=80,
                         help="Questions about the CURRENT filtered reviews and the tables above.")
        send = st.form_submit_button("Send")

    if send and q.strip():
        st.session_state["ask_ai_text"] = q
        st.session_state["qa_messages"].append(("user", q))

        # Retrieve top matching verbatims for richer answers
        retrieved = vector_search(q, index, api_key, top_k=8) if index else []
        quotes = []
        for txt, sim in retrieved[:5]:
            s = (txt or "").strip()
            if len(s) > 0:
                if len(s) > 320: s = s[:317] + "…"
                quotes.append(f"• “{s}”")
        quotes_text = "\n".join(quotes) if quotes else "• (No close review snippets retrieved.)"

        # Tools for exact stats
        def pandas_count(query: str) -> dict:
            try:
                if ";" in query or "__" in query: return {"error": "disallowed pattern"}
                res = filtered.query(query, engine="python")
                return {"count": int(len(res))}
            except Exception as e:
                return {"error": str(e)}

        def pandas_mean(column: str, query: str | None = None) -> dict:
            try:
                if column not in filtered.columns:
                    return {"error": f"Unknown column {column}"}
                d = filtered
                if query: d = d.query(query, engine="python")
                return {"mean": float(pd.to_numeric(d[column], errors='coerce').mean())}
            except Exception as e:
                return {"error": str(e)}

        def symptom_stats(symptom: str) -> dict:
            cols = existing_detractor_columns + existing_delighter_columns
            if not cols: return {"count": 0, "avg_star": None}
            mask = filtered[cols].isin([symptom]).any(axis=1)
            d = filtered[mask]
            return {"count": int(len(d)), "avg_star": float(pd.to_numeric(d["Star Rating"], errors="coerce").mean()) if len(d) else None}

        def keyword_stats(term: str) -> dict:
            if "Verbatim" not in filtered.columns: return {"count": 0, "pct": 0.0}
            ser = filtered["Verbatim"].astype("string").fillna("")
            cnt = int(ser.str.contains(term, case=False, na=False).sum())
            pct = (cnt / max(1,len(filtered))) * 100.0
            return {"count": cnt, "pct": pct}

        # Page-insight helpers
        def get_metrics_snapshot():
            try:
                return {
                    "total_reviews": int(len(filtered)),
                    "avg_star": float(pd.to_numeric(filtered.get("Star Rating"), errors="coerce").mean()) if len(filtered) else 0.0,
                    "low_star_pct_1_2": float((pd.to_numeric(filtered.get("Star Rating"), errors="coerce") <= 2).mean() * 100) if len(filtered) else 0.0,
                    "star_counts": pd.to_numeric(filtered.get("Star Rating"), errors="coerce").value_counts().sort_index().to_dict() if "Star Rating" in filtered else {},
                }
            except Exception as e:
                return {"error": str(e)}

        def get_top_items(kind: str = "detractors", top_n: int = 10):
            try:
                if kind.lower().startswith("del"):
                    df_res = analyze_delighters_detractors(filtered, existing_delighter_columns)
                else:
                    df_res = analyze_delighters_detractors(filtered, existing_detractor_columns)
                return df_res.head(int(top_n)).to_dict(orient="records")
            except Exception as e:
                return {"error": str(e)}

        def country_overview(country: str | None = None):
            try:
                if "Country" not in filtered.columns: return {"error":"No Country column"}
                sub = filtered[filtered["Country"] == country] if country else filtered
                data = sub.groupby("Country").agg(Average_Rating=("Star Rating","mean"),
                                                  Review_Count=("Star Rating","count")).reset_index()
                return data.to_dict(orient="records")
            except Exception as e:
                return {"error": str(e)}

        # Local fallback if model fails
        def _local_answer_fallback(question: str) -> str:
            try:
                parts = []
                total = int(len(filtered))
                avg = float(pd.to_numeric(filtered.get("Star Rating"), errors="coerce").mean()) if total else 0.0
                low_pct = float((pd.to_numeric(filtered.get("Star Rating"), errors="coerce") <= 2).mean() * 100) if total else 0.0
                parts.append(f"**Snapshot** — {total} reviews; avg ★ {avg:.1f}; % 1–2★ {low_pct:.1f}%.")

                detr = analyze_delighters_detractors(filtered, existing_detractor_columns).head(5)
                deli = analyze_delighters_detractors(filtered, existing_delighter_columns).head(5)
                def _fmt_rows(df):
                    if df.empty: return "None"
                    return "; ".join([f"{r['Item']} (avg ★ {r['Avg Star']}, {int(r['Mentions'])} mentions)" for _, r in df.iterrows()])
                parts.append("**Top detractors:** " + _fmt_rows(detr))
                parts.append("**Top delighters:** " + _fmt_rows(deli))

                key = None
                tokens = re.findall(r"[A-Za-z]{4,}", (question or "").lower())
                common = {"about","from","with","what","when","where","which","that","this","they","them","good","bad","great","poor","very","more"}
                for t in tokens:
                    if t not in common:
                        key = t; break

                quotes = []
                if "Verbatim" in filtered.columns:
                    ser = filtered["Verbatim"].astype("string").fillna("")
                    sample = ser[ser.str.contains(re.escape(key), case=False, na=False)].head(4).tolist() if key else ser.head(3).tolist()
                    for s in sample:
                        s2 = s.strip()
                        if len(s2) > 300: s2 = s2[:297] + "…"
                        if s2: quotes.append(f"• “{s2}”")
                if quotes:
                    parts.append("**Example quotes:**\n" + "\n".join(quotes))
                return "\n\n".join(parts)
            except Exception as e:
                return f"(Fallback summary error: {e})"

        # Build system/context for the LLM
        def context_blob(df_in: pd.DataFrame, n=25) -> str:
            if df_in.empty: return "No rows after filters."
            parts = [f"ROW_COUNT={len(df_in)}"]
            if "Star Rating" in df_in:
                parts.append(f"STAR_COUNTS={df_in['Star Rating'].value_counts().sort_index().to_dict()}")
            cols_keep = [c for c in ["Review Date","Country","Source","Model (SKU)","Star Rating","Verbatim"] if c in df_in.columns]
            smp = df_in[cols_keep].sample(min(n, len(df_in)), random_state=7)
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

        selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
        llm_temp = float(st.session_state.get("llm_temp", 0.2))

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
                "name":"symptom_stats",
                "description":"Get mentions count and average star rating for a symptom across detractor/delighter columns.",
                "parameters":{"type":"object","properties":{"symptom":{"type":"string"}},"required":["symptom"]}
            }},
            {"type":"function","function":{
                "name":"keyword_stats",
                "description":"Count and percentage of reviews whose Verbatim contains a term (case-insensitive).",
                "parameters":{"type":"object","properties":{"term":{"type":"string"}},"required":["term"]}
            }},
            {"type":"function","function":{
                "name":"get_metrics_snapshot",
                "description":"Return page metrics currently shown: total reviews, avg star, low-star %, and star counts.",
                "parameters":{"type":"object","properties":{}}
            }},
            {"type":"function","function":{
                "name":"get_top_items",
                "description":"Return top detractors or delighters with Avg Star and Mentions.",
                "parameters":{"type":"object","properties":{"kind":{"type":"string"},"top_n":{"type":"integer"}}}
            }},
            {"type":"function","function":{
                "name":"country_overview",
                "description":"Overview by country for Average_Rating and Review_Count. Optional filter by a specific country.",
                "parameters":{"type":"object","properties":{"country":{"type":"string"}}}
            }},
        ]

        sys_ctx = (
            "You are a helpful analyst for customer reviews. Use ONLY the provided context and tool results.\n"
            "When relevant, include short quotes from retrieved snippets to illustrate what customers said.\n"
            "If exact numbers are needed, prefer calling tools. If unknown, say you don't know.\n\n"
            "RETRIEVED_SNIPPETS:\n" + quotes_text + "\n\n"
            "CONTEXT:\n" + context_blob(filtered)
        )

        client = OpenAI(api_key=api_key)
        first_kwargs = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": sys_ctx},
                *[{"role": r, "content": c} for (r,c) in st.session_state["qa_messages"]],
            ],
            "tools": tools,
        }
        if model_supports_temperature(selected_model):
            first_kwargs["temperature"] = llm_temp

        # First call with robust fallback
        try:
            with st.spinner("Thinking..."):
                first = client.chat.completions.create(**first_kwargs)
        except Exception as e:
            final_text = _local_answer_fallback(q)
            st.session_state['qa_messages'].append(('assistant', final_text))
            st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)
            st.caption(':information_source: Shown local summary because the model call failed. ' + str(e))
            scroll_to("askdata-anchor")
        else:
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
                    if name == "get_top_items": out = get_top_items(args.get("kind","detractors"), int(args.get("top_n", 10)))
                    if name == "country_overview": out = country_overview(args.get("country"))
                    tool_msgs.append({"tool_call_id": call.id, "role":"tool",
                                      "name": name, "content": json.dumps(out)})

            if tool_msgs:
                follow_kwargs = {
                    "model": selected_model,
                    "messages": [
                        {"role":"system","content": sys_ctx},
                        *[{"role": r, "content": c} for (r,c) in st.session_state["qa_messages"]],
                        {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                        *tool_msgs
                    ],
                }
                if model_supports_temperature(selected_model):
                    follow_kwargs["temperature"] = llm_temp

                try:
                    follow = client.chat.completions.create(**follow_kwargs)
                    final_text = follow.choices[0].message.content
                except Exception as e:
                    final_text = _local_answer_fallback(q)
                    st.session_state['qa_messages'].append(('assistant', final_text))
                    st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)
                    st.caption(':information_source: Shown local summary because the tool-enabled call failed. ' + str(e))
                    scroll_to("askdata-anchor")
                else:
                    st.session_state["qa_messages"].append(("assistant", final_text))
                    st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)
                    scroll_to("askdata-anchor")
            else:
                final_text = msg.content if msg and getattr(msg, 'content', None) else _local_answer_fallback(q)
                st.session_state["qa_messages"].append(("assistant", final_text))
                st.markdown(f"<div class='chat-a'><b>Assistant:</b> {final_text}</div>", unsafe_allow_html=True)
                scroll_to("askdata-anchor")

st.markdown("---")

# ---------- Feedback (anchor fallback) ----------
anchor("feedback-anchor")
st.markdown("## 💬 Submit Feedback")
st.caption("Tell us what to improve. We care about making this tool user-centric.")

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
            st.info("Email sending isn’t configured. Opening your mail client instead.")
            st.link_button("Open email to wseddon@sharkninja.com",
                           url=f"mailto:wseddon@sharkninja.com?subject=Star%20Walk%20Feedback&body={_html.escape(message)}")

# ---------- One-time scroll behaviors ----------
if st.session_state.get("force_scroll_top_once"):
    st.session_state["force_scroll_top_once"] = False
    st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)

