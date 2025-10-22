# starwalk_ui.py — Streamlit 1.38+
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from googletrans import Translator
import io, re, html, os, warnings, json, asyncio, smtplib, hashlib
from email.message import EmailMessage
from streamlit.components.v1 import html as st_html
import numpy as np

# Silence openpyxl warning
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# Optional text repair
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# Optional OpenAI SDK (LLM)
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

EMBED_MODEL = "text-embedding-3-small"  # vector index model
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}
def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ------------- Global CSS -------------
st.markdown(
    """
    <style>
      :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
      .block-container { padding-top: .5rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] .block-container { padding-top: .25rem; padding-bottom: .6rem; }
      section[data-testid="stSidebar"] label { font-size: .95rem; }
      section[data-testid="stSidebar"] .stButton>button { width: 100%; }
      section[data-testid="stSidebar"] .divider { margin: 10px 0 8px 0; border-top: 1px dashed #d9d9df; height: 1px; }

      mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
      .review-card { border:1px solid #e6e6e6; background:#fafafa; border-radius:12px; padding:16px; }
      .review-card p { margin:.25rem 0; line-height:1.45; }
      .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
      .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:.95rem; }
      .badge.pos { background:#CFF7D6; color:#085a2a; }
      .badge.neg { background:#FBD3D0; color:#7a0410; }

      .hero-wrap { position: relative; overflow: hidden; border-radius: 14px; background: radial-gradient(1100px 320px at 8% -18%, #fff8d9 0%, #ffffff 55%, #ffffff 100%); border: 1px solid #eee; height: 150px; margin-top: .25rem; margin-bottom: 1rem; }
      #hero-canvas { position:absolute; inset:0; width:100%; height:100%; z-index:1; }
      .hero-inner { position:absolute; inset:0; display:grid; align-content:center; justify-items:center; text-align:center; pointer-events:none; z-index:2; }
      .hero-title-row { display:flex; align-items:center; gap:14px; justify-content:space-between; width: min(1100px, 92%); margin: 0 auto; }
      .hero-title { font-size: clamp(24px, 4vw, 44px); font-weight: 800; letter-spacing:.4px; margin:0; }
      .hero-sub { margin: 4px 0 0 0; color:#667085; font-size: clamp(12px, 1.1vw, 16px); }
      .sn-logo { width: 148px; height:auto; }

      .metrics-grid { display:grid; grid-template-columns: repeat(3, minmax(260px,1fr)); gap:16px; }
      .metric-card { background:#fff; border:1px solid #e9e9ee; border-radius:14px; padding:14px 16px; box-shadow: 0 1px 0 rgba(16,24,40,.02), 0 1px 3px rgba(16,24,40,.04); }
      .metric-card h4 { margin:0 0 10px; font-weight:800; font-size:1.05rem; }
      .metric-row { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; }
      .metric-box { background:#f7f7fb; border:1px solid #eee; border-radius:10px; padding:10px 12px; }
      .metric-label { color:#667085; font-size:.85rem; margin-bottom:4px; }
      .metric-kpi { font-weight:800; font-size:1.6rem; }

      .table-wrap { width:100%; overflow-x:auto; }
      .table-wrap table { width:100% !important; border-collapse:collapse; }
      .symptom-table th, .symptom-table td { padding: 8px 10px; }

      .pager { margin: 18px 0 28px 0; }
      .pager-zone .stButton>button { padding: 6px 18px; margin: 6px 8px; border-radius: 10px; }

      .ask-wrap { border:1px solid #ececf2; background:#f8f9fb; border-radius:12px; padding:14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------- Hero (stars left, logo right) -------------
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
            <div class="hero-title-row">
              <div style="width:220px"></div>
              <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
              <div>{sharkninja_svg}</div>
            </div>
            <p class="hero-sub">Insights, trends, and ratings — fast.</p>
          </div>
        </div>
        <script>
        (function(){{
          const c = document.getElementById('hero-canvas');
          const ctx = c.getContext('2d', {{alpha:true}});
          const DPR = window.devicePixelRatio || 1;
          let w=0,h=0;
          function resize(){{ const r=c.getBoundingClientRect(); w=Math.max(300,r.width|0); h=Math.max(120,r.height|0); c.width=w*DPR; c.height=h*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); }}
          window.addEventListener('resize', resize, {{passive:true}}); resize();
          const N = Math.max(120, Math.floor(w/12));
          function randLeft(){{ return Math.pow(Math.random(), 1.9) * (w*0.7); }}
          const stars = Array.from({{length:N}}, () => ({{ x:randLeft(), y:Math.random()*h, r:.6+Math.random()*1.4, s:.3+Math.random()*0.9 }}));
          function tick(){{ ctx.clearRect(0,0,w,h); for(const s of stars){{ ctx.beginPath(); ctx.arc((s.x%w+w)%w,(s.y%h+h)%h,s.r,0,Math.PI*2); ctx.fillStyle='rgba(255,200,50,.9)'; ctx.fill(); s.x=(s.x+0.10*s.s)%w; }} requestAnimationFrame(tick); }} tick();
        }})();
        </script>
        """,
        height=160,
    )

render_hero()

# ------------- Utils -------------
def clean_text(x: str, keep_na: bool=False) -> str:
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
    for bad, good in {"â€™": "'", "â€˜": "‘", "â€œ": "“", "â€\x9d": "”", "â€“": "–", "â€”": "—", "Â": ""}.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def style_rating_cells(v):
    if isinstance(v,(float,int)):
        if v>=4.5: return "color: green;"
        if v<4.5:  return "color: red;"
    return ""

def apply_filter(df, column_name, label, key=None):
    options=["ALL"]
    if column_name in df.columns:
        options += sorted([x for x in df[column_name].astype("string").dropna().unique().tolist() if str(x).strip()!=""])
    selected = st.multiselect(f"Select {label}", options=options, default=["ALL"], key=key)
    if "ALL" not in selected and column_name in df.columns:
        return df[df[column_name].astype("string").isin(selected)], selected
    return df, ["ALL"]

def collect_unique_symptoms(df, cols):
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
    if not s or s.upper() in {"<NA>","NA","N/A","NULL","NONE"}: return False
    return not bool(re.fullmatch(r"[\W_]+", s))

def analyze_delighters_detractors(df, cols):
    cols = [c for c in cols if c in df.columns]
    if not cols: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    s = (df[cols].stack(dropna=True).map(lambda v: clean_text(v, keep_na=True)).dropna().astype("string").str.strip())
    s = s[s.map(is_valid_symptom_value)]
    if s.empty: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    uniq = pd.unique(s.to_numpy())
    out, total = [], len(df)
    for item in uniq:
        mask = df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count==0: continue
        avg = df.loc[mask, "Star Rating"].mean()
        pct = (count/total*100) if total else 0
        out.append({"Item": str(item).title(), "Avg Star": round(avg,1) if pd.notna(avg) else None,
                    "Mentions": count, "% Total": f"{round(pct,1)}%"})
    if not out: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    return pd.DataFrame(out).sort_values(by="Mentions", ascending=False, ignore_index=True)

def highlight_html(text: str, keyword: str|None) -> str:
    safe = html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error: pass
    return safe

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

def apply_keyword_filter(df, kw):
    if not kw or kw.strip()=="": return df
    if "Verbatim" not in df.columns: return df
    verb = df["Verbatim"].astype("string").fillna("").map(clean_text)
    return df[verb.str.contains(kw.strip(), case=False, na=False)]

def send_feedback_email(subject: str, body: str) -> tuple[bool, str]:
    cfg = st.secrets.get("SMTP", None)
    if not cfg:
        return False, "SMTP not configured in secrets."
    try:
        host = cfg.get("HOST"); port = int(cfg.get("PORT", 587))
        user = cfg.get("USER"); pwd = cfg.get("PASSWORD")
        from_addr = cfg.get("FROM") or user
        to_addr = cfg.get("TO") or "wseddon@sharkninja.com"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(body)

        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if user and pwd: s.login(user, pwd)
            s.send_message(msg)
        return True, "Sent"
    except Exception as e:
        return False, str(e)

def _truncate_sentence(t: str, max_chars: int = 360) -> str:
    t = (t or "").strip()
    if len(t) <= max_chars: return t
    cut = t[:max_chars]
    p = cut.rfind(".")
    if p >= max_chars * 0.5: cut = cut[:p+1]
    return cut + "…"

# ---------- Vector index helpers ----------
def _fingerprint_filtered(df: pd.DataFrame) -> str:
    # small stable hash for cache key
    if df.empty or "Verbatim" not in df.columns:
        return "empty"
    s = df["Verbatim"].fillna("").astype(str)
    if "Review Date" in df.columns:
        s = s + "|" + df["Review Date"].astype(str).fillna("")
    raw = "||".join(s.tolist())
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

@st.cache_resource(show_spinner=False)
def build_index_cached(fingerprint: str, texts: tuple[str, ...], model: str, api_key: str):
    """Return L2-normalized embedding matrix (n, d) for given texts."""
    client = OpenAI(api_key=api_key)
    if len(texts) == 0:
        return np.zeros((0, 1536), dtype="float32")
    vecs = []
    bs = 128
    for i in range(0, len(texts), bs):
        resp = client.embeddings.create(model=model, input=list(texts[i:i+bs]))
        vecs.extend([d.embedding for d in resp.data])
    arr = np.array(vecs, dtype="float32")
    arr /= (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
    return arr  # cached by fingerprint

def cosine_topk(matrix: np.ndarray, q: np.ndarray, k: int) -> np.ndarray:
    # matrix: (n,d) already normalized; q: (d,)
    sims = matrix @ q  # cosine
    k = max(1, min(k, len(matrix)))
    return np.argsort(-sims)[:k], sims

# ------------- Upload -------------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

# Prevent initial auto scroll after fresh upload
if uploaded_file and st.session_state.get("last_uploaded_name") != uploaded_file.name:
    st.session_state["last_uploaded_name"] = uploaded_file.name
    st.session_state["force_scroll_top_once"] = True

if uploaded_file:
    try:
        st.markdown("---")
        df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")

        for col in ["Country","Source","Model (SKU)","Seeded","New Review"]:
            if col in df.columns: df[col] = df[col].astype("string").str.upper()
        if "Star Rating" in df.columns:
            df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

        sym_cols_all = [c for c in df.columns if c.startswith("Symptom")]
        for c in sym_cols_all:
            df[c] = df[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")
        if "Verbatim" in df.columns:
            df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
        if "Review Date" in df.columns:
            df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")

        # -------- Sidebar Filters --------
        st.sidebar.header("🔍 Filters")

        with st.sidebar.expander("🗓️ Timeframe", expanded=False):
            timeframe = st.selectbox("Select Timeframe",
                                     ["All Time","Last Week","Last Month","Last Year","Custom Range"],
                                     key="tf")
            today = datetime.today()
            start_date = end_date = None
            if timeframe=="Custom Range":
                start_date, end_date = st.date_input(
                    "Date Range", value=(datetime.today()-timedelta(days=30), datetime.today()),
                    min_value=datetime(2000,1,1), max_value=datetime.today()
                )
            elif timeframe=="Last Week":  start_date, end_date = today-timedelta(days=7), today
            elif timeframe=="Last Month": start_date, end_date = today-timedelta(days=30), today
            elif timeframe=="Last Year":  start_date, end_date = today-timedelta(days=365), today

        filtered = df.copy()
        if start_date and end_date and "Review Date" in filtered.columns:
            filtered = filtered[(filtered["Review Date"]>=pd.Timestamp(start_date)) & (filtered["Review Date"]<=pd.Timestamp(end_date))]

        with st.sidebar.expander("🌟 Star Rating", expanded=False):
            selected_ratings = st.multiselect("Select Star Ratings", ["All",1,2,3,4,5], default=["All"], key="sr")
        if "All" not in selected_ratings and "Star Rating" in filtered.columns:
            filtered = filtered[filtered["Star Rating"].isin(selected_ratings)]

        with st.sidebar.expander("🌍 Standard Filters", expanded=False):
            filtered, _ = apply_filter(filtered, "Country", "Country", key="f_Country")
            filtered, _ = apply_filter(filtered, "Source", "Source", key="f_Source")
            filtered, _ = apply_filter(filtered, "Model (SKU)", "Model (SKU)", key="f_Model")
            filtered, _ = apply_filter(filtered, "Seeded", "Seeded", key="f_Seeded")
            filtered, _ = apply_filter(filtered, "New Review", "New Review", key="f_NewReview")

        det_cols = [f"Symptom {i}" for i in range(1,11)]
        del_cols = [f"Symptom {i}" for i in range(11,21)]
        ex_det = [c for c in det_cols if c in filtered.columns]
        ex_del = [c for c in del_cols if c in filtered.columns]
        det_opts = collect_unique_symptoms(filtered, ex_det)
        del_opts = collect_unique_symptoms(filtered, ex_del)

        with st.sidebar.expander("🩺 Review Symptoms", expanded=False):
            sel_del = st.multiselect("Select Delighter Symptoms", ["All"]+sorted(del_opts), default=["All"], key="delight")
            sel_det = st.multiselect("Select Detractor Symptoms", ["All"]+sorted(det_opts), default=["All"], key="detract")
        if "All" not in sel_del and ex_del:
            filtered = filtered[filtered[ex_del].isin(sel_del).any(axis=1)]
        if "All" not in sel_det and ex_det:
            filtered = filtered[filtered[ex_det].isin(sel_det).any(axis=1)]

        with st.sidebar.expander("🔎 Keyword", expanded=False):
            keyword = st.text_input("Keyword to search (in review text)", value="", key="kw",
                                    help="Case-insensitive; cleans mis-encoded punctuation (e.g., â€™ → ').")
            if keyword: filtered = apply_keyword_filter(filtered, keyword)

        core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
        symptom_set = set([f"Symptom {i}" for i in range(1,21)])
        with st.sidebar.expander("📋 Additional Filters", expanded=False):
            extra = [c for c in df.columns if c not in (core_cols | symptom_set)]
            if extra:
                for c in extra:
                    filtered, _ = apply_filter(filtered, c, c, key=f"f_{c}")
            else:
                st.info("No additional filters available.")

        with st.sidebar.expander("📄 Review List", expanded=False):
            rpp_options = [10,20,50,100]
            default_rpp = st.session_state.get("reviews_per_page", 10)
            rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
            rpp = st.selectbox("Reviews per page", rpp_options, index=rpp_index, key="rpp")
            if rpp != default_rpp:
                st.session_state["reviews_per_page"] = rpp
                st.session_state["review_page"] = 0

        # Clear all (higher up)
        if st.sidebar.button("🧹 Clear all filters", help="Reset filters to defaults."):
            for k in ["tf","sr","kw","delight","detract","rpp","review_page",
                      "llm_model","llm_model_label","llm_temp","ask_main_text"] + \
                     [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
                if k in st.session_state: del st.session_state[k]
            st.rerun()

        # Divider above LLM
        st.sidebar.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        # LLM controls (ask UI is in MAIN)
        with st.sidebar.expander("🤖 AI Assistant (LLM)", expanded=False):
            _choices = [
                ("Fast & economical – 4o-mini", "gpt-4o-mini"),
                ("Balanced – 4o", "gpt-4o"),
                ("Advanced – 4.1", "gpt-4.1"),
                ("Most advanced – GPT-5", "gpt-5"),
                ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
            ]
            _default_model = st.session_state.get("llm_model","gpt-4o-mini")
            _idx = next((i for i,(_,mid) in enumerate(_choices) if mid==_default_model), 0)
            label = st.selectbox("Model", options=[l for (l,_) in _choices], index=_idx, key="llm_model_label")
            st.session_state["llm_model"] = dict(_choices)[label]

            temp_supported = model_supports_temperature(st.session_state["llm_model"])
            st.session_state["llm_temp"] = st.slider(
                "Creativity (temperature)", 0.0, 1.0, float(st.session_state.get("llm_temp",0.2)), 0.1,
                disabled=not temp_supported,
                help=("Controls randomness: lower = more deterministic, higher = more creative. "
                      "Some models (e.g., GPT-5 family) use a fixed temperature.")
            )
            if not temp_supported:
                st.caption("ℹ️ This model uses a fixed temperature; slider disabled.")
            if st.button("Go to AI Assistant"): st.session_state["assistant_scroll_pending"]=True

        if st.sidebar.button("✉️ Submit Feedback"): st.session_state["feedback_scroll_pending"]=True

        # -------- Star Rating Metrics --------
        st.markdown("### ⭐ Star Rating Metrics")
        st.caption("All metrics below reflect the **currently filtered** dataset.")

        def _calc(df_):
            total = len(df_)
            avg = float(df_["Star Rating"].mean()) if total else 0.0
            denom = int(df_["Star Rating"].notna().sum())
            low = int(df_.loc[df_["Star Rating"].isin([1,2])].shape[0]) if denom else 0
            pct_low = (low/denom*100.0) if denom else 0.0
            return total, avg, pct_low

        seeded_mask = filtered["Seeded"].astype("string").str.upper().eq("YES") if "Seeded" in filtered.columns else pd.Series(False, index=filtered.index)
        df_all, df_org, df_seed = filtered, filtered.loc[~seeded_mask], filtered.loc[seeded_mask]
        (tot_all, avg_all, low_all)   = _calc(df_all)
        (tot_org, avg_org, low_org)   = _calc(df_org)
        (tot_seed, avg_seed, low_seed)= _calc(df_seed)

        def card_html(title, count, avg, pct):
            return f"""
            <div class="metric-card">
              <h4>{title}</h4>
              <div class="metric-row">
                <div class="metric-box"><div class="metric-label">Count</div><div class="metric-kpi">{count:,}</div></div>
                <div class="metric-box"><div class="metric-label">Avg ★</div><div class="metric-kpi">{avg:.1f}</div></div>
                <div class="metric-box"><div class="metric-label">% 1–2★</div><div class="metric-kpi">{pct:.1f}%</div></div>
              </div>
            </div>"""
        st.markdown(
            f"""<div class="metrics-grid">
              {card_html("All Reviews", tot_all, avg_all, low_all)}
              {card_html("Organic (non-Seeded)", tot_org, avg_org, low_org)}
              {card_html("Seeded", tot_seed, avg_seed, low_seed)}
            </div>""", unsafe_allow_html=True,
        )

        # Distribution chart
        star_counts = filtered["Star Rating"].value_counts().sort_index()
        total_reviews = len(filtered)
        percentages = ((star_counts/total_reviews*100).round(1)) if total_reviews else (star_counts*0)
        star_labels = [f"{int(s)} stars" for s in star_counts.index]
        fig = go.Figure(go.Bar(
            x=star_counts.values, y=star_labels, orientation="h",
            text=[f"{v} reviews ({percentages.get(i,0)}%)" for i,v in zip(star_counts.index, star_counts.values)],
            textposition="auto",
            marker=dict(color=["#FFA07A","#FA8072","#FFD700","#ADFF2F","#32CD32"]),
            hoverinfo="y+x+text"
        ))
        fig.update_layout(title="<b>Star Rating Distribution</b>",
                          xaxis=dict(title="Number of Reviews", showgrid=False),
                          yaxis=dict(title="Star Ratings", showgrid=False),
                          template="plotly_white", margin=dict(l=40,r=40,t=45,b=40))
        st.plotly_chart(fig, use_container_width=True)

        # -------- Country Breakdown --------
        st.markdown("### 🌍 Country-Specific Breakdown")
        if "Country" in filtered.columns and "Source" in filtered.columns:
            new_rev = filtered[filtered["New Review"].astype("string").str.upper()=="YES"]
            cs = (filtered.groupby(["Country","Source"])
                          .agg(Average_Rating=("Star Rating","mean"), Review_Count=("Star Rating","count")).reset_index())
            nrs = (new_rev.groupby(["Country","Source"])
                          .agg(New_Review_Average=("Star Rating","mean"), New_Review_Count=("Star Rating","count")).reset_index())
            cs = cs.merge(nrs, on=["Country","Source"], how="left")
            overall = (filtered.groupby("Country")
                               .agg(Average_Rating=("Star Rating","mean"), Review_Count=("Star Rating","count")).reset_index())
            overall_new = (new_rev.groupby("Country")
                                  .agg(New_Review_Average=("Star Rating","mean"), New_Review_Count=("Star Rating","count")).reset_index())
            overall = overall.merge(overall_new, on="Country", how="left"); overall["Source"]="Overall"

            def color_num(v):
                if pd.isna(v): return ""
                try: v=float(v)
                except: return ""
                if v>=4.5: return "color: green;"
                if v<4.5:  return "color: red;"
                return ""
            def fmt_r(v): return "-" if pd.isna(v) else f"{v:.1f}"
            def fmt_c(v): return "-" if pd.isna(v) else f"{int(v):,}"

            for country in overall["Country"].unique():
                st.markdown(f"#### {country}")
                cd = cs[cs["Country"]==country]; ov = overall[overall["Country"]==country]
                comb = pd.concat([cd, ov], ignore_index=True)
                comb["Sort_Order"] = comb["Source"].apply(lambda x: 1 if x=="Overall" else 0)
                comb = comb.sort_values("Sort_Order").drop(columns=["Sort_Order"])
                comb = comb.drop(columns=["Country"]).rename(columns={
                    "Average_Rating":"Avg Rating","Review Count":"Review Count",
                    "New_Review_Average":"New Review Average","New_Review_Count":"New Review Count"
                })
                def bold_overall(row): return ["font-weight:bold;"]*len(row) if row["Source"]=="Overall" else [""]*len(row)
                styled = (comb.style
                          .format({"Avg Rating":fmt_r,"Review Count":fmt_c,"New Review Average":fmt_r,"New Review Count":fmt_c})
                          .applymap(color_num, subset=["Avg Rating","New Review Average"])
                          .apply(bold_overall, axis=1)
                          .set_properties(**{"text-align":"center"})
                          .set_table_styles([
                              {"selector":"th","props":[("text-align","center")]},
                              {"selector":"td","props":[("text-align","center")]}]))
                st.markdown(styled.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.warning("Country or Source data is missing in the uploaded file.")

        st.markdown("---")

        # -------- Symptom Tables --------
        st.markdown("### 🩺 Symptom Tables")
        det_tbl = analyze_delighters_detractors(filtered, ex_det).head(20)
        del_tbl = analyze_delighters_detractors(filtered, ex_del).head(20)

        def _styled_html(df_):
            if df_.empty: return "<em>No data.</em>"
            styled = (df_.style.applymap(style_rating_cells, subset=["Avg Star"])
                             .format({"Avg Star":"{:.1f}","Mentions":"{:.0f}"}).hide(axis="index"))
            return f"<div class='table-wrap symptom-table'>{styled.to_html(escape=False)}</div>"

        view_mode = st.radio("View mode", ["Split","Tabs"], horizontal=True, index=0)
        if view_mode=="Split":
            c1,c2 = st.columns([1,1])
            with c1: st.subheader("All Detractors"); st.markdown(_styled_html(det_tbl), unsafe_allow_html=True)
            with c2: st.subheader("All Delighters"); st.markdown(_styled_html(del_tbl), unsafe_allow_html=True)
        else:
            t1,t2 = st.tabs(["All Detractors","All Delighters"])
            with t1: st.markdown(_styled_html(det_tbl), unsafe_allow_html=True)
            with t2: st.markdown(_styled_html(del_tbl), unsafe_allow_html=True)

        st.markdown("---")

        # -------- All Reviews --------
        st.markdown("### 📝 All Reviews")
        translator = Translator()

        if not filtered.empty:
            csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes,
                               file_name="filtered_reviews.csv", mime="text/csv")

        translate_all = st.button("Translate All Reviews to English")

        rpp = st.session_state.get("reviews_per_page", 10)
        if "review_page" not in st.session_state: st.session_state["review_page"]=0
        total = len(filtered)
        total_pages = max((total+rpp-1)//rpp, 1)
        current = min(max(st.session_state["review_page"],0), total_pages-1)
        start, end = current*rpp, current*rpp + rpp
        page_df = filtered.iloc[start:end]

        if page_df.empty:
            st.warning("No reviews match the selected criteria.")
        else:
            for _, row in page_df.iterrows():
                text = row.get("Verbatim", pd.NA)
                text = "" if pd.isna(text) else clean_text(text)
                text = safe_translate(translator, text) if translate_all else text

                date_val = row.get("Review Date", pd.NaT)
                if pd.isna(date_val): date_str="-"
                else:
                    try: date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
                    except Exception: date_str="-"

                html_text = highlight_html(text, st.session_state.get("kw",""))

                def chips(r, cols, css):
                    items=[]
                    for c in cols:
                        v=r.get(c, pd.NA)
                        if pd.isna(v): continue
                        s=str(v).strip()
                        if not s or s.upper() in {"<NA>","NA","N/A","-"}: continue
                        items.append(f'<span class="badge {css}">{html.escape(s)}</span>')
                    return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

                del_msgs = chips(row, ex_del, "pos"); det_msgs = chips(row, ex_det, "neg")
                star_val = row.get("Star Rating", 0)
                try: star_int = int(star_val) if pd.notna(star_val) else 0
                except: star_int=0

                st.markdown(
                    f"""
                    <div class="review-card">
                      <p><strong>Source:</strong> {row.get('Source','')} | <strong>Model:</strong> {row.get('Model (SKU)','')}</p>
                      <p><strong>Country:</strong> {row.get('Country','')}</p>
                      <p><strong>Date:</strong> {date_str}</p>
                      <p><strong>Rating:</strong> {'⭐'*star_int} ({row.get('Star Rating','')}/5)</p>
                      <p><strong>Review:</strong> {html_text}</p>
                      <div><strong>Delighter Symptoms:</strong> {del_msgs}</div>
                      <div><strong>Detractor Symptoms:</strong> {det_msgs}</div>
                    </div>
                    """, unsafe_allow_html=True
                )

        # Pagination
        st.markdown("<div class='pager'>", unsafe_allow_html=True)
        c1,c2,c3,c4,c5 = st.columns([1,1,2,1,1])
        with c1:
            st.markdown("<div class='pager-zone'>", unsafe_allow_html=True)
            if st.button("⏮ First", disabled=current==0): st.session_state["review_page"]=0; st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div class='pager-zone'>", unsafe_allow_html=True)
            if st.button("⬅ Prev", disabled=current==0): st.session_state["review_page"]=max(current-1,0); st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with c3:
            showing_from = 0 if total==0 else start+1
            showing_to = min(end, total)
            st.markdown(f"<div style='text-align:center;font-weight:bold;margin-top:8px;'>Page {current+1} of {total_pages} • Showing {showing_from}–{showing_to} of {total}</div>", unsafe_allow_html=True)
        with c4:
            st.markdown("<div class='pager-zone'>", unsafe_allow_html=True)
            if st.button("Next ➡", disabled=current>=total_pages-1): st.session_state["review_page"]=min(current+1,total_pages-1); st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with c5:
            st.markdown("<div class='pager-zone'>", unsafe_allow_html=True)
            if st.button("Last ⏭", disabled=current>=total_pages-1): st.session_state["review_page"]=total_pages-1; st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # -------- Build/Cache VECTOR INDEX for CURRENT FILTER --------
        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        vector_ready = False
        vector_index = None
        vec_texts = []
        vec_meta = []
        if _HAS_OPENAI and api_key and "Verbatim" in filtered.columns and not filtered.empty:
            vec_texts = filtered["Verbatim"].fillna("").map(clean_text).tolist()
            fingerprint = _fingerprint_filtered(filtered)
            vector_index = build_index_cached(fingerprint, tuple(vec_texts), EMBED_MODEL, api_key)
            # metadata aligned with vec_texts order
            filt_reset = filtered.reset_index(drop=True)
            for i, r in filt_reset.iterrows():
                try:
                    dt = "-" if pd.isna(r.get("Review Date", pd.NaT)) else pd.to_datetime(r.get("Review Date")).strftime("%Y-%m-%d")
                except Exception:
                    dt = "-"
                vec_meta.append({
                    "text": vec_texts[i],
                    "stars": None if pd.isna(r.get("Star Rating", None)) else float(r.get("Star Rating")),
                    "date": dt,
                    "country": str(r.get("Country","")),
                    "source": str(r.get("Source","")),
                    "model": str(r.get("Model (SKU)",""))
                })
            vector_ready = vector_index is not None and vector_index.shape[0] == len(vec_texts)

        # -------- ASK UI (MAIN) --------
        st.markdown("<div id='assistant-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 🤖 Ask the Assistant")
        with st.form("ask_form"):
            st.markdown("<div class='ask-wrap'>", unsafe_allow_html=True)
            prompt = st.text_input("Hey! Ask anything about the CURRENTLY FILTERED reviews 🙂",
                                   key="ask_main_text",
                                   placeholder="e.g., What do first-time users in UK dislike most?")
            ask_clicked = st.form_submit_button("Ask")
            st.markdown("</div>", unsafe_allow_html=True)

        st.session_state.setdefault("qa_messages", [
            {"role":"system","content":"You are a helpful analyst of review data. When asked for metrics (counts, percentages, averages, top lists, or symptom/keyword mentions), you MUST call the provided tools to compute exact numbers on the CURRENT filtered dataset. If something is unknown, say so rather than guessing."}
        ])

        # ----- Stats frames for tools
        def _build_symptom_frame(filtered: pd.DataFrame) -> pd.DataFrame:
            if filtered is None or filtered.empty:
                return pd.DataFrame(columns=["symptom","kind","mentions","avg_star","percent_total"])
            det_cols_local = [c for c in filtered.columns if re.fullmatch(r"Symptom [1-9]|Symptom 10", c)]
            del_cols_local = [c for c in filtered.columns if re.fullmatch(r"Symptom (1[1-9]|20)", c)]
            rows=[]; total=len(filtered)
            def add_rows(cols, kind):
                if not cols: return
                s = (filtered[cols].stack(dropna=True)
                     .map(lambda v: clean_text(v, keep_na=True)).dropna().astype("string").str.strip())
                s = s[s.map(is_valid_symptom_value)]
                if s.empty: return
                for val, cnt in s.value_counts().items():
                    mask = filtered[cols].isin([val]).any(axis=1)
                    avg = filtered.loc[mask, "Star Rating"].mean()
                    rows.append({
                        "symptom": str(val), "kind": kind, "mentions": int(cnt),
                        "avg_star": float(avg) if pd.notna(avg) else None,
                        "percent_total": float((cnt/total*100) if total else 0)
                    })
            add_rows(det_cols_local, "detractor"); add_rows(del_cols_local, "delighter")
            return pd.DataFrame(rows)

        symptom_frame = _build_symptom_frame(filtered)

        # ---- TOOLS (including semantic_search) ----
        def overall_stats_tool() -> dict:
            def _calc(df_):
                total = len(df_); avg = float(df_["Star Rating"].mean()) if total else 0.0
                denom = int(df_["Star Rating"].notna().sum())
                low = int(df_.loc[df_["Star Rating"].isin([1,2])].shape[0]) if denom else 0
                pct_low = (low/denom*100.0) if denom else 0.0
                return total, avg, pct_low
            mask = filtered["Seeded"].astype("string").str.upper().eq("YES") if "Seeded" in filtered.columns else pd.Series(False, index=filtered.index)
            df_all, df_org, df_seed = filtered, filtered.loc[~mask], filtered.loc[mask]
            (tot_all, avg_all, low_all)   = _calc(df_all)
            (tot_org, avg_org, low_org)   = _calc(df_org)
            (tot_seed, avg_seed, low_seed)= _calc(df_seed)
            star_counts_local = filtered["Star Rating"].value_counts().sort_index().to_dict()
            return {"all":{"count":tot_all,"avg":avg_all,"pct_1_2":low_all,"star_counts":star_counts_local},
                    "organic":{"count":tot_org,"avg":avg_org,"pct_1_2":low_org},
                    "seeded":{"count":tot_seed,"avg":avg_seed,"pct_1_2":low_seed}}

        def symptom_stats_tool(query: str, kind: str="any", exact: bool=False, top: int|None=None) -> dict:
            sf = symptom_frame
            if sf.empty: return {"results": [], "total_reviews": int(len(filtered))}
            qq = (query or "").strip()
            if qq:
                view = sf if kind=="any" else sf[sf["kind"]==kind]
                if exact: view = view[view["symptom"].str.lower()==qq.lower()]
                else:     view = view[view["symptom"].str.lower().str.contains(re.escape(qq.lower()))]
            else:
                view = sf if kind=="any" else sf[sf["kind"]==kind]
            if top is not None: view = view.sort_values("mentions", ascending=False).head(int(top))
            return {"results":[{"symptom":r["symptom"],"kind":r["kind"],"mentions":int(r["mentions"]),
                                "percent_total":float(r["percent_total"]),
                                "avg_star":None if pd.isna(r["avg_star"]) else float(r["avg_star"])}
                               for _,r in view.iterrows()],
                    "total_reviews": int(len(filtered))}

        def keyword_stats_tool(keyword: str) -> dict:
            if not keyword or "Verbatim" not in filtered.columns:
                return {"error":"keyword missing or no Verbatim column"}
            mask = filtered["Verbatim"].astype("string").str.contains(keyword, case=False, na=False)
            dfk = filtered[mask]
            return {"keyword":keyword, "mentions":int(mask.sum()),
                    "percent_total":float((mask.sum()/len(filtered)*100) if len(filtered) else 0),
                    "avg_star": None if dfk.empty else float(dfk["Star Rating"].mean()),
                    "total_reviews": int(len(filtered))}

        def review_snippets_tool(keyword: str|None=None, symptom_query: str|None=None, kind: str="any", exact: bool=False, n: int=5) -> dict:
            rows = filtered
            if rows.empty: return {"results": [], "total_reviews": 0}
            # keyword
            if keyword and "Verbatim" in rows.columns:
                mask_kw = rows["Verbatim"].astype("string").str.contains(keyword, case=False, na=False)
                rows_kw = rows[mask_kw]
            else: rows_kw = rows.iloc[0:0]
            # symptom
            if symptom_query:
                det_cols_local = [c for c in rows.columns if re.fullmatch(r"Symptom [1-9]|Symptom 10", c)]
                del_cols_local = [c for c in rows.columns if re.fullmatch(r"Symptom (1[1-9]|20)", c)]
                cols_pick = det_cols_local + del_cols_local
                if kind=="detractor": cols_pick = det_cols_local
                elif kind=="delighter": cols_pick = del_cols_local
                if cols_pick:
                    if exact:
                        mask_sym = rows[cols_pick].astype("string").apply(lambda s: s.str.lower()==symptom_query.lower()).any(axis=1)
                    else:
                        mask_sym = rows[cols_pick].astype("string").apply(lambda s: s.str.lower().str.contains(re.escape(symptom_query.lower()), na=False)).any(axis=1)
                    rows_sym = rows[mask_sym]
                else: rows_sym = rows.iloc[0:0]
            else: rows_sym = rows.iloc[0:0]

            if keyword and symptom_query: combined = pd.concat([rows_kw, rows_sym], axis=0).drop_duplicates()
            elif keyword: combined = rows_kw
            elif symptom_query: combined = rows_sym
            else: combined = rows

            if combined.empty: return {"results": [], "total_reviews": int(len(filtered))}
            if "Review Date" in combined.columns:
                combined = combined.sort_values("Review Date", ascending=False)
            else:
                combined = combined.sort_values("Star Rating", ascending=True)

            t = Translator(); out=[]
            for _, r in combined.head(int(max(1,n))).iterrows():
                txt = clean_text(r.get("Verbatim","")); txt = safe_translate(t, txt)
                try: dt = "-" if pd.isna(r.get("Review Date", pd.NaT)) else pd.to_datetime(r.get("Review Date")).strftime("%Y-%m-%d")
                except Exception: dt = "-"
                out.append({"text": _truncate_sentence(txt), "stars": None if pd.isna(r.get("Star Rating",None)) else float(r.get("Star Rating")),
                            "date": dt, "country": str(r.get("Country","")), "source": str(r.get("Source","")), "model": str(r.get("Model (SKU)",""))})
            return {"results": out, "total_reviews": int(len(filtered))}

        # NEW: semantic vector search over Verbatim
        def semantic_search_tool(query: str, k: int = 5) -> dict:
            if not query or not vector_ready:
                return {"results": [], "total_reviews": int(len(filtered)), "error": "vector index not available or empty query"}
            client = OpenAI(api_key=api_key)
            emb = client.embeddings.create(model=EMBED_MODEL, input=[query]).data[0].embedding
            q = np.array(emb, dtype="float32"); q /= (np.linalg.norm(q) + 1e-9)
            idxs, sims = cosine_topk(vector_index, q, int(k))
            t = Translator()
            results=[]
            for i in idxs:
                m = vec_meta[int(i)]
                txt = _truncate_sentence(safe_translate(t, m["text"]))
                results.append({
                    "text": txt, "similarity": float(sims[int(i)]),
                    "stars": m["stars"], "date": m["date"], "country": m["country"],
                    "source": m["source"], "model": m["model"]
                })
            return {"results": results, "total_reviews": int(len(filtered))}

        tools = [
            {"type":"function","function":{"name":"overall_stats","description":"Overall counts/averages/low-star% for current filtered dataset (and organic vs seeded).","parameters":{"type":"object","properties":{}}}},
            {"type":"function","function":{"name":"symptom_stats","description":"Stats for symptoms; kind any|delighter|detractor; exact match optional; top N.","parameters":{"type":"object","properties":{"query":{"type":"string"},"kind":{"type":"string","enum":["any","delighter","detractor"],"default":"any"},"exact":{"type":"boolean","default":False},"top":{"type":"integer"}}}}},
            {"type":"function","function":{"name":"keyword_stats","description":"Mentions/percent/avg star for a keyword in Verbatim.","parameters":{"type":"object","properties":{"keyword":{"type":"string"}},"required":["keyword"]}}},
            {"type":"function","function":{"name":"review_snippets","description":"Return short translated quotes matching a keyword and/or a symptom.","parameters":{"type":"object","properties":{"keyword":{"type":"string"},"symptom_query":{"type":"string"},"kind":{"type":"string","enum":["any","delighter","detractor"],"default":"any"},"exact":{"type":"boolean","default":False},"n":{"type":"integer","default":5}}}}},
            {"type":"function","function":{"name":"semantic_search","description":"Semantic vector search over Verbatim on CURRENT filtered dataset; returns top-k most relevant quotes with metadata.","parameters":{"type":"object","properties":{"query":{"type":"string"},"k":{"type":"integer","default":5}},"required":["query"]}}}
        ]

        def call_tool(name, args):
            try:
                if name=="overall_stats":  return overall_stats_tool()
                if name=="symptom_stats":  return symptom_stats_tool(args.get("query",""), args.get("kind","any"), bool(args.get("exact",False)), args.get("top"))
                if name=="keyword_stats":  return keyword_stats_tool(args.get("keyword",""))
                if name=="review_snippets": return review_snippets_tool(args.get("keyword"), args.get("symptom_query"), args.get("kind","any"), bool(args.get("exact",False)), int(args.get("n",5)))
                if name=="semantic_search": return semantic_search_tool(args.get("query",""), int(args.get("k",5)))
                return {"error":"unknown tool"}
            except Exception as e:
                return {"error": str(e)}

        # ---- Ask flow
        if not _HAS_OPENAI:
            st.info("To enable Q&A, add `openai` to requirements and redeploy, then set `OPENAI_API_KEY`.")
        elif not api_key:
            st.info("Set your `OPENAI_API_KEY` (env or .streamlit/secrets.toml) to chat with the filtered data.")
        else:
            client = OpenAI(api_key=api_key)

            if ask_clicked and (prompt or "").strip():
                user_q = prompt.strip()
                st.session_state['qa_messages'].append({"role":"user","content": user_q})

                sys_ctx = (
                    "You analyze reviews with tool access. For metrics (counts, %, averages, top items), call `overall_stats`, `symptom_stats`, or `keyword_stats`. "
                    "When the user asks what customers *say* about a topic or wants *examples/quotes*, FIRST call `semantic_search` with the user's topic to retrieve the most relevant reviews; "
                    "you MAY also call `review_snippets` or `keyword_stats` to augment with exact percentages. "
                    "Always include 1–5 concise quotes (date, country, stars) when the question is qualitative."
                )

                model_id = st.session_state.get("llm_model","gpt-4o-mini")
                temp = float(st.session_state.get("llm_temp",0.2))
                first_kwargs = {"model": model_id,
                                "messages": [*st.session_state["qa_messages"], {"role":"system","content": sys_ctx}],
                                "tools": tools}
                if model_supports_temperature(model_id): first_kwargs["temperature"]=temp

                try:
                    first = client.chat.completions.create(**first_kwargs)
                except Exception as e:
                    if "temperature" in str(e).lower() and ("unsupported" in str(e).lower() or "does not support" in str(e).lower()):
                        first_kwargs.pop("temperature", None)
                        first = client.chat.completions.create(**first_kwargs)
                    else:
                        raise

                msg = first.choices[0].message
                tool_msgs=[]
                if msg.tool_calls:
                    for call in msg.tool_calls:
                        name = call.function.name
                        args = json.loads(call.function.arguments or "{}")
                        out = call_tool(name, args)
                        tool_msgs.append({"tool_call_id": call.id, "role":"tool", "name": name, "content": json.dumps(out)})

                if tool_msgs:
                    follow_kwargs = {"model": model_id,
                                     "messages": [*st.session_state["qa_messages"],
                                                  {"role":"system","content": sys_ctx},
                                                  {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                                                  *tool_msgs]}
                    if model_supports_temperature(model_id): follow_kwargs["temperature"]=temp
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

                st.session_state["qa_messages"].append({"role":"assistant","content": final_text})
                st.session_state["assistant_scroll_pending"] = True

            # Compact render: latest pair inline, older collapsed
            msgs_no_sys = [m for m in st.session_state["qa_messages"] if m["role"]!="system"]
            inline_msgs = msgs_no_sys if len(msgs_no_sys)<=2 else msgs_no_sys[-2:]
            older_msgs = [] if len(msgs_no_sys)<=2 else msgs_no_sys[:-2]
            if older_msgs:
                with st.expander(f"Previous Q&A (history) — {len(older_msgs)} older", expanded=False):
                    for m in older_msgs:
                        with st.chat_message(m["role"]): st.markdown(m["content"])
                    if st.button("Clear history (keeps latest pair)"):
                        st.session_state["qa_messages"] = [st.session_state["qa_messages"][0], *inline_msgs]
                        st.experimental_rerun()
            for m in inline_msgs:
                with st.chat_message(m["role"]): st.markdown(m["content"])
            st.markdown("<div id='assistant-last'></div>", unsafe_allow_html=True)

        # -------- Feedback (LAST) --------
        st.markdown("<div id='feedback-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 💬 Submit Feedback / Feature Requests")
        with st.form("feedback_form"):
            fb = st.text_area("We care about making this tool user-centric — tell us what to improve or build next:", height=140,
                              placeholder="Feature ideas, UI nits, bugs, data questions…")
            email = st.text_input("Your email (optional)", value="")
            sent = st.form_submit_button("Submit Feedback")
        if sent:
            subject = "Star Walk Dashboard • Feedback"
            body = f"From: {email or 'anonymous'}\\n\\n{fb}"
            ok, info = send_feedback_email(subject, body)
            if ok: st.success("Thanks! Your feedback was sent. 🙌")
            else:
                mailto = f"mailto:wseddon@sharkninja.com?subject=Star%20Walk%20Dashboard%20Feedback&body={body.replace(' ','%20')}"
                st.info("Could not send via SMTP (not configured). You can click below to send via your email client.")
                st.link_button("Open email draft to wseddon@sharkninja.com", mailto)

        # One-time scroll behaviors
        if st.session_state.get("force_scroll_top_once"):
            st.session_state["force_scroll_top_once"]=False
            st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)
        if st.session_state.get("assistant_scroll_pending"):
            st.session_state["assistant_scroll_pending"]=False
            st.markdown("<script>const el=document.getElementById('assistant-last')||document.getElementById('assistant-anchor'); if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}</script>", unsafe_allow_html=True)
        if st.session_state.get("feedback_scroll_pending"):
            st.session_state["feedback_scroll_pending"]=False
            st.markdown("<script>const el=document.getElementById('feedback-anchor'); if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}</script>", unsafe_allow_html=True)

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")


