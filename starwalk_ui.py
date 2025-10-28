# starwalk_ui.py
# Streamlit 1.38+

import streamlit as st
from streamlit.components.v1 import html as st_html
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
import io, os, re, json, textwrap, warnings
from email.message import EmailMessage

# Optional libraries
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

try:
    import openpyxl  # for formatting-preserving Excel writeback
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# -------------------------- Model helpers --------------------------
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}

def model_supports_temperature(model_id: str) -> bool:
    if not model_id:
        return True
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

# rough tokens/min for ETA display (heuristics)
MODEL_SPEED = {
    "gpt-4o-mini": 100000,
    "gpt-4o": 60000,
    "gpt-4.1": 45000,
    "gpt-5": 75000,  # hypothetical
    "gpt-5-chat-latest": 75000,
}

# -------------------------- Page config --------------------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# -------------------------- Force Light Mode --------------------------
st_html("""
<script>
(function () {
  function setLight() {
    try {
      document.documentElement.setAttribute('data-theme','light');
      if (document.body) document.body.setAttribute('data-theme','light');
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

# -------------------------- Global CSS --------------------------
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
  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }

  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

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

  .badge{ display:inline-flex; align-items:center; gap:.4ch; padding:6px 10px; border-radius:10px; font-weight:600; font-size:.9rem;
          border:1.4px solid var(--border); background:var(--bg-tile); color:var(--text); margin-right:6px; margin-bottom:6px; }
  .badge.pos{ border-color:#7ed9b3; background:#e9fbf3; color:#0b4f3e; }
  .badge.neg{ border-color:#f6b4b4; background:#fff1f2; color:#7f1d1d; }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# -------------------------- Hero --------------------------
def render_hero():
    logo_html = (
        '<img class="sn-logo" '
        'src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" '
        'alt="SharkNinja logo" />'
    )
    HERO = f"""
      <div class="hero-wrap" id="top-hero">
        <canvas id="hero-canvas"></canvas>
        <div class="hero-inner">
          <div>
            <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
            <div class="hero-sub">Insights, trends, and ratings — fast.</div>
          </div>
          <div class="hero-right">{logo_html}</div>
        </div>
      </div>
      <script>
      (function(){{
        const c = document.getElementById('hero-canvas');
        if(!c) return;
        const ctx = c.getContext('2d', {{alpha:true}});
        const DPR = window.devicePixelRatio || 1;
        let w=0,h=0;
        function resize(){{
          const r = c.getBoundingClientRect();
          w = Math.max(300, r.width|0); h = Math.max(120, r.height|0);
          c.width = w * DPR; c.height = h * DPR;
          ctx.setTransform(DPR,0,0,DPR,0,0);
        }}
        window.addEventListener('resize', resize, {{passive:true}});
        resize();
        let N = 120;
        let stars = Array.from({{length:N}}, () => ({{ x: Math.random()*w, y: Math.random()*h, r: 0.6 + Math.random()*1.4, s: 0.3 + Math.random()*0.9 }}));
        function tick(){{
          ctx.clearRect(0,0,w,h);
          for(const s of stars){{
            ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI*2);
            ctx.fillStyle = 'rgba(255,200,50,.9)'; ctx.fill();
            s.x += 0.12*s.s; if(s.x > w) s.x = 0;
          }}
          requestAnimationFrame(tick);
        }}
        tick();
      }})();
      </script>
    """
    st_html(HERO, height=160)

render_hero()

# -------------------------- Utilities --------------------------
def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    if _HAS_FTFY:
        try: s = _ftfy_fix(s)
        except Exception: pass
    # common mojibake fixes
    repl = {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}
    for bad, good in repl.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return pd.NA if keep_na else ""
    return s

def escape_md(s: str) -> str:
    if s is None: return ""
    # light markdown escaping for preview
    return re.sub(r'([_*`>])', r'\\\1', str(s))

def is_empty_symptoms_row(row: pd.Series, symptom_cols: list[str]) -> bool:
    for c in symptom_cols:
        if c in row and pd.notna(row[c]) and str(row[c]).strip():
            return False
    return True

def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols:
        return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    s = (filtered_df[cols].stack(dropna=True)
         .map(lambda v: clean_text(v, keep_na=True)).dropna()
         .astype("string").str.strip())
    if s.empty:
        return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    unique_items = pd.unique(s.to_numpy())
    results, total_rows = [], len(filtered_df)
    for item in unique_items:
        item_str = str(item).strip()
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0: 
            continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean() if "Star Rating" in filtered_df.columns else np.nan
        pct = (count / total_rows * 100) if total_rows else 0
        results.append({
            "Item": item_str,
            "Avg Star": round(avg_star,1) if pd.notna(avg_star) else None,
            "Mentions": count,
            "% Total": f"{round(pct,1)}%"
        })
    if not results:
        return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)

def robust_contains(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    t = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    p = " " + re.sub(r"[^a-z0-9]+", " ", phrase.lower()).strip() + " "
    return p in t

def conservative_keyword_filter(review: str, candidate: str) -> bool:
    """Require strong lexical support to avoid 'stretch' assignments."""
    if not review or not candidate:
        return False
    # If full phrase appears, accept
    if robust_contains(review, candidate):
        return True
    # Otherwise require at least two non-stopword tokens from candidate present in review
    tokens = [w for w in re.findall(r"[a-zA-Z0-9]+", candidate.lower()) if len(w) > 3]
    if len(tokens) < 2:
        return False
    hits = 0
    rlow = review.lower()
    for t in tokens:
        if re.search(rf"\\b{re.escape(t)}\\b", rlow):
            hits += 1
        if hits >= 2:
            return True
    return False

SIMILAR_GROUPS = [
    {"learning curve", "initial difficulty", "steep learning curve"},
    {"frizz free", "not effective - frizz fighting", "frizz control"},
    {"price/value", "price mismatch"},
]

def canonicalize_symptom(sym: str, canon_list: list[str]) -> str:
    """Return best canonical label from canon_list if sym is in a 'similar group'."""
    s_low = sym.strip().lower()
    for grp in SIMILAR_GROUPS:
        if s_low in grp:
            # pick first matching available canonical in canon_list
            for c in canon_list:
                if c.strip().lower() in grp:
                    return c
            # fallback to an arbitrary stable representative
            return next(iter(grp))
    return sym

def dedupe_preserving_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        key = x.strip().lower()
        if key and key not in seen:
            seen.add(key); out.append(x)
    return out

def estimate_seconds(model: str, reviews: list[str]) -> float:
    # crude: ~4 chars/token; speed tokens/min → tokens/sec
    tpm = MODEL_SPEED.get(model, 50000)
    tps = tpm / 60.0
    tokens = sum(max(50, len(r)/4.0) for r in reviews) + 400  # overhead
    return tokens / max(1.0, tps)

# -------------------------- LLM Symptomizer --------------------------
def llm_symptomize(
    client: OpenAI,
    model: str,
    api_key: str,
    review_text: str,
    delighters: list[str],
    detractors: list[str],
    temperature: float | None
) -> dict:
    """Ask the LLM to pick up to 10 delighters and 10 detractors from provided lists only."""
    prompt = textwrap.dedent(f"""
    You will label a single customer review for a hair tool.

    ONLY choose symptoms from the provided canonical lists. Do not invent new items.
    If nothing applies confidently, return empty lists.

    Return strict JSON with this schema:
    {{
      "delighters": ["<items from list>"],
      "detractors": ["<items from list>"],
      "notes": "<short rationale>"
    }}

    Canonical Delighters list (examples; choose from exactly these):
    {json.dumps(delighters, ensure_ascii=False)}

    Canonical Detractors list (examples; choose from exactly these):
    {json.dumps(detractors, ensure_ascii=False)}

    Review:
    ---
    {review_text}
    ---
    """).strip()

    req = {
        "model": model,
        "messages": [
            {"role":"system","content":"You are a careful labeler. Be precise; favor precision over recall. If not confident, leave empty."},
            {"role":"user","content": prompt}
        ],
        "response_format": {"type": "json_object"},
    }
    if model_supports_temperature(model) and temperature is not None:
        req["temperature"] = float(temperature)

    try:
        resp = client.chat.completions.create(**req)
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        return {
            "delighters": [str(x).strip() for x in data.get("delighters", []) if str(x).strip()],
            "detractors": [str(x).strip() for x in data.get("detractors", []) if str(x).strip()],
            "notes": str(data.get("notes","")).strip()
        }
    except Exception as e:
        return {"delighters": [], "detractors": [], "notes": f"LLM error: {e}"}

def refine_with_conservative_rules(
    review: str,
    chosen: list[str],
    canon: list[str],
) -> list[str]:
    """Apply stretch-avoidance & canonicalization & conservative keyword gate."""
    # Canonicalize similar terms
    canon_map = {c.lower(): c for c in canon}
    refined = []
    for c in chosen:
        c2 = canonicalize_symptom(c, canon)
        refined.append(c2)
    refined = dedupe_preserving_order(refined)
    # Keep only those that pass conservative lexical evidence
    refined = [x for x in refined if x.lower() in canon_map or conservative_keyword_filter(review, x)]
    # Also, if candidate not in canon_map but passed keyword, keep as "new candidate" separately (handled outside)
    return refined

def run_symptomize_batch(
    df: pd.DataFrame,
    row_locs: list[int],
    delighters: list[str],
    detractors: list[str],
    model: str,
    api_key: str | None,
    temperature: float | None,
    max_per_side: int = 10,
    progress_label: str = "Symptomizing…"
):
    """Process selected rows in-place; returns list of new symptom candidates (not in canon)."""
    if not row_locs:
        return []

    if not _HAS_OPENAI or not api_key:
        st.error("OpenAI not configured. Set OPENAI_API_KEY to use Symptomize.")
        return []

    client = OpenAI(api_key=api_key)

    pb = st.progress(0, text=progress_label)
    new_candidates = {"delighters": set(), "detractors": set()}

    symptom_cols = [f"Symptom {i}" for i in range(1, 21)]

    rows_text = []
    for i in row_locs:
        txt = clean_text(df.iloc[i].get("Verbatim", ""))
        rows_text.append(txt)

    # ETA helper
    secs = estimate_seconds(model, rows_text)
    approx = f"~{int(max(1, round(secs)))}s estimated"
    st.caption(f"Estimated time for this batch: {approx} (model: {model})")

    for idx, i in enumerate(row_locs, start=1):
        row = df.iloc[i]
        review_text = clean_text(row.get("Verbatim", ""))

        # 1) LLM selection constrained to lists
        result = llm_symptomize(
            client=client,
            model=model,
            api_key=api_key or "",
            review_text=review_text,
            delighters=delighters,
            detractors=detractors,
            temperature=temperature if model_supports_temperature(model) else None
        )
        chosen_pos = result["delighters"]
        chosen_neg = result["detractors"]

        # 2) Conservative lexical gate & canonicalization
        pos_final = refine_with_conservative_rules(review_text, chosen_pos, delighters)[:max_per_side]
        neg_final = refine_with_conservative_rules(review_text, chosen_neg, detractors)[:max_per_side]

        # 3) Collect "new" candidates the model named but aren't in canon
        for x in chosen_pos:
            if x.strip() and x.strip().lower() not in [y.lower() for y in delighters]:
                if conservative_keyword_filter(review_text, x):
                    new_candidates["delighters"].add(x.strip())
        for x in chosen_neg:
            if x.strip() and x.strip().lower() not in [y.lower() for y in detractors]:
                if conservative_keyword_filter(review_text, x):
                    new_candidates["detractors"].add(x.strip())

        # 4) Write into Symptom 1–20 slots (pos first, then neg). Keep any remainder empty.
        combined = pos_final + neg_final
        for j, col in enumerate(symptom_cols, start=0):
            df.at[df.index[i], col] = combined[j] if j < len(combined) else ""

        # Preview card
        with st.expander(f"Review {i+1} – preview & result", expanded=False):
            st.markdown(f"**Full review:**\n\n> {escape_md(review_text) if review_text else '_(empty)_'}")
            if pos_final:
                st.markdown("**Delighters (selected):**  " + "  ".join([f"<span class='badge pos'>{escape_md(x)}</span>" for x in pos_final]), unsafe_allow_html=True)
            if neg_final:
                st.markdown("**Detractors (selected):**  " + "  ".join([f"<span class='badge neg'>{escape_md(x)}</span>" for x in neg_final]), unsafe_allow_html=True)
            if result.get("notes"):
                st.caption("Notes: " + result["notes"])

        pb.progress(idx/len(row_locs), text=f"{progress_label} {idx}/{len(row_locs)}")

    pb.progress(1.0, text="Done")
    return [{"type": "delighters", "items": sorted(new_candidates["delighters"])},
            {"type": "detractors", "items": sorted(new_candidates["detractors"])}]

# -------------------------- Sidebar: Upload & Filters --------------------------
st.sidebar.header("📁 Upload Star Walk Excel")
uploaded_file = st.sidebar.file_uploader("Upload .xlsx", type=["xlsx"], accept_multiple_files=False)

if not uploaded_file:
    st.info("Please upload an Excel file to get started.")
    st.stop()

# Keep original bytes for formatting-preserving writeback
uploaded_bytes = uploaded_file.read()
uploaded_buffer = io.BytesIO(uploaded_bytes)

# Load main sheet (prefer named sheet)
MAIN_SHEET_CANDIDATES = ["Star Walk scrubbed verbatims", "Star Walk", "Sheet1"]
try:
    xl = pd.ExcelFile(uploaded_buffer)
    main_sheet = next((s for s in xl.sheet_names if s in MAIN_SHEET_CANDIDATES), xl.sheet_names[0])
    df = pd.read_excel(io.BytesIO(uploaded_bytes), sheet_name=main_sheet)
except Exception as e:
    st.error(f"Failed to load Excel: {e}")
    st.stop()

# Clean minimal columns we use
for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
    if col in df.columns:
        df[col] = df[col].astype("string").str.upper()
if "Star Rating" in df.columns:
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

if "Verbatim" in df.columns:
    df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
if "Review Date" in df.columns:
    df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")

# Load Symptoms sheet (canon lists)
symptom_lists = {"delighters": [], "detractors": []}
try:
    xl2 = pd.ExcelFile(io.BytesIO(uploaded_bytes))
    sym_sheet_name = next((s for s in xl2.sheet_names if s.strip().lower() == "symptoms"), None)
    if sym_sheet_name:
        sy = pd.read_excel(io.BytesIO(uploaded_bytes), sheet_name=sym_sheet_name)
        # Try common column names
        del_col = next((c for c in sy.columns if str(c).strip().lower() in {"delighters","delighter","positives"}), None)
        det_col = next((c for c in sy.columns if str(c).strip().lower() in {"detractors","detractor","negatives"}), None)
        if del_col:
            symptom_lists["delighters"] = [str(x).strip() for x in sy[del_col].dropna().astype(str) if str(x).strip()]
        if det_col:
            symptom_lists["detractors"] = [str(x).strip() for x in sy[det_col].dropna().astype(str) if str(x).strip()]
    else:
        st.warning("Couldn't find a 'Symptoms' sheet. Using conservative fallback only.")
except Exception as e:
    st.warning(f"Symptoms sheet load issue: {e}")

if "__canon_delighters__" not in st.session_state:
    st.session_state["__canon_delighters__"] = symptom_lists["delighters"]
if "__canon_detractors__" not in st.session_state:
    st.session_state["__canon_detractors__"] = symptom_lists["detractors"]

# -------------------------- Sidebar: Filters --------------------------
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

def apply_filter(df_: pd.DataFrame, column_name: str, label: str, key: str | None = None):
    options = ["ALL"]
    if column_name in df_.columns:
        col = df_[column_name].astype("string")
        options += sorted([x for x in col.dropna().unique().tolist() if str(x).strip() != ""])
    selected = st.multiselect(f"Select {label}", options=options, default=["ALL"], key=key)
    if "ALL" not in selected and column_name in df_.columns:
        return df_[df_[column_name].astype("string").isin(selected)], selected
    return df_, ["ALL"]

with st.sidebar.expander("🌍 Standard Filters", expanded=False):
    filtered, _ = apply_filter(filtered, "Country", "Country", key="f_Country")
    filtered, _ = apply_filter(filtered, "Source", "Source", key="f_Source")
    filtered, _ = apply_filter(filtered, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
    filtered, _ = apply_filter(filtered, "Seeded", "Seeded", key="f_Seeded")
    filtered, _ = apply_filter(filtered, "New Review", "New Review", key="f_New Review")

with st.sidebar.expander("🔎 Keyword", expanded=False):
    keyword = st.text_input("Keyword in review text", value="", key="kw")
    if keyword and "Verbatim" in filtered.columns:
        mask_kw = filtered["Verbatim"].astype("string").fillna("").str.contains(keyword.strip(), case=False, na=False)
        filtered = filtered[mask_kw]

# -------------------------- LLM Settings --------------------------
st.sidebar.header("🤖 AI Settings")
_model_choices = [
    ("Fast & economical – 4o-mini", "gpt-4o-mini"),
    ("Balanced – 4o", "gpt-4o"),
    ("Advanced – 4.1", "gpt-4.1"),
    ("Most advanced – GPT-5", "gpt-5"),
    ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
]
_default_model = st.session_state.get("llm_model", "gpt-4o-mini")
_default_idx = next((i for i, (_, mid) in enumerate(_model_choices) if mid == _default_model), 0)
_label = st.sidebar.selectbox("Model", options=[l for (l, _) in _model_choices], index=_default_idx, key="llm_model_label")
st.session_state["llm_model"] = dict(_model_choices)[_label]

temp_supported = model_supports_temperature(st.session_state["llm_model"])
if temp_supported:
    st.session_state["llm_temp"] = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, float(st.session_state.get("llm_temp", 0.2)), 0.1)
else:
    st.sidebar.caption("This model uses a fixed temperature; slider disabled.")
    st.session_state["llm_temp"] = None

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

# -------------------------- Symptomize CTA --------------------------
symptom_cols = [f"Symptom {i}" for i in range(1, 21)]
present_cols = [c for c in symptom_cols if c in filtered.columns]

with st.container():
    st.markdown("## 🧩 Reviews Needing Symptoms")
    if present_cols:
        empty_mask = filtered[present_cols].apply(
            lambda r: all((pd.isna(v) or str(v).strip() in {"", "-", "NA", "N/A"}) for v in r), axis=1
        )
        missing_idx_filtered = filtered.index[empty_mask].tolist()
        need_cnt = len(missing_idx_filtered)
        st.markdown(f"**{need_cnt}** reviews have empty **Symptom 1–20**.")
        colA, colB, colC, colD = st.columns([2,2,2,2])
        batch_n = colA.slider("Batch size", min_value=1, max_value=20, value=min(10, max(1, need_cnt)))
        process_all = colB.checkbox("Process all missing", value=False, disabled=(need_cnt==0))
        show_lengths = colC.checkbox("Show length stats (IQR)", value=True)
        run_btn = colD.button("🚀 Symptomize now", disabled=(need_cnt==0))

        # IQR & length stats
        if show_lengths and "Verbatim" in filtered.columns:
            lengths = filtered["Verbatim"].fillna("").astype(str).map(len)
            if len(lengths):
                q1, q3 = np.percentile(lengths.values, [25, 75])
                iqr = q3 - q1
                st.caption(f"Chars per review — Q1: {int(q1)}, Q3: {int(q3)}, IQR: {int(iqr)}, Max: {int(lengths.max())}, Median: {int(lengths.median())}")

        if run_btn and need_cnt > 0:
            # Map filtered indices to original df locations
            target_labels = missing_idx_filtered if process_all else missing_idx_filtered[:batch_n]
            row_locs = [df.index.get_loc(lbl) for lbl in target_labels]

            # Symptomize
            new_candidates = run_symptomize_batch(
                df=df,
                row_locs=row_locs,
                delighters=st.session_state["__canon_delighters__"],
                detractors=st.session_state["__canon_detractors__"],
                model=st.session_state["llm_model"],
                api_key=api_key,
                temperature=st.session_state.get("llm_temp", None),
                max_per_side=10,
                progress_label="Symptomizing…"
            )

            # New symptom approval flow
            if new_candidates:
                with st.expander("🆕 New symptom candidates (approve to add to lists)", expanded=True):
                    add_pos = []
                    add_neg = []
                    for group in new_candidates:
                        if not group["items"]:
                            continue
                        if group["type"] == "delighters":
                            st.markdown("**Delighters:**")
                            for it in group["items"]:
                                if st.checkbox(f"Approve: {it}", key=f"new_pos_{it}"):
                                    add_pos.append(it)
                        else:
                            st.markdown("**Detractors:**")
                            for it in group["items"]:
                                if st.checkbox(f"Approve: {it}", key=f"new_neg_{it}"):
                                    add_neg.append(it)
                    if st.button("✅ Add approved to canonical lists"):
                        # Add to session canon lists
                        if add_pos:
                            st.session_state["__canon_delighters__"] = dedupe_preserving_order(st.session_state["__canon_delighters__"] + add_pos)
                        if add_neg:
                            st.session_state["__canon_detractors__"] = dedupe_preserving_order(st.session_state["__canon_detractors__"] + add_neg)
                        st.success("Approved items added to canonical lists (in-session).")
                        st.caption("Note: to persist in the Excel 'Symptoms' sheet, use the Excel download and update your source file.")
            st.success("Batch completed. Tables below reflect in-memory updates.")
            # Optional: immediately refresh filtered reference
            filtered = df.copy()
    else:
        st.info("Symptom 1–20 columns are not present; please ensure your file includes these columns.")

st.markdown("---")

# -------------------------- Metrics --------------------------
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
      <h4>{title}</h4>
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

# -------------------------- Reviews list --------------------------
st.markdown("## 📝 All Reviews (current filters)")
if "review_page" not in st.session_state: st.session_state["review_page"] = 0
reviews_per_page = st.session_state.get("reviews_per_page", 10)
with st.expander("List controls", expanded=False):
    rpp = st.selectbox("Reviews per page", options=[10,20,50,100], index=[10,20,50,100].index(reviews_per_page))
    if rpp != reviews_per_page:
        st.session_state["reviews_per_page"] = rpp
        st.session_state["review_page"] = 0

total_reviews_count = len(filtered)
total_pages = max((total_reviews_count + st.session_state["reviews_per_page"] - 1) // st.session_state["reviews_per_page"], 1)
current_page = min(max(st.session_state["review_page"], 0), total_pages - 1)
start_index = current_page * st.session_state["reviews_per_page"]
end_index = start_index + st.session_state["reviews_per_page"]
paginated = filtered.iloc[start_index:end_index]

if paginated.empty:
    st.warning("No reviews match the selected criteria.")
else:
    for _, row in paginated.iterrows():
        review_text = clean_text(row.get("Verbatim", ""))
        date_val = row.get("Review Date", pd.NaT)
        date_str = "-"
        if pd.notna(date_val):
            try: date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
            except Exception: pass
        star_val = row.get("Star Rating", 0)
        try: star_int = int(star_val) if pd.notna(star_val) else 0
        except Exception: star_int = 0

        pos_badges, neg_badges = [], []
        for c in symptom_cols:
            if c in row and pd.notna(row[c]) and str(row[c]).strip():
                val = str(row[c]).strip()
                if val.lower() in [x.lower() for x in st.session_state["__canon_delighters__"]]:
                    pos_badges.append(val)
                elif val.lower() in [x.lower() for x in st.session_state["__canon_detractors__"]]:
                    neg_badges.append(val)
                else:
                    # unknown class → negative by default style
                    neg_badges.append(val)

        st.markdown(
            f"""
            <div class="review-card">
              <p><strong>Source:</strong> {row.get('Source','-')} | <strong>Model:</strong> {row.get('Model (SKU)','-')}</p>
              <p><strong>Country:</strong> {row.get('Country','-')} | <strong>Date:</strong> {date_str}</p>
              <p><strong>Rating:</strong> {'⭐'*star_int} ({row.get('Star Rating','-')}/5)</p>
              <p><strong>Review:</strong><br>{escape_md(review_text)}</p>
              <p><strong>Delighters:</strong> {" ".join([f"<span class='badge pos'>{escape_md(x)}</span>" for x in pos_badges])}</p>
              <p><strong>Detractors:</strong> {" ".join([f"<span class='badge neg'>{escape_md(x)}</span>" for x in neg_badges])}</p>
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

# -------------------------- Download section --------------------------
st.markdown("## ⬇️ Download Updated Data")

# CSV (values only)
csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
st.download_button("Download CSV (values only)", csv_bytes, file_name="starwalk_updated.csv", mime="text/csv")

# Excel (best-effort formatting preservation)
def write_symptoms_back_to_workbook(original_bytes: bytes, df_updated: pd.DataFrame, sheet_name: str) -> bytes:
    """Open the original workbook and write only Symptom 1–20 values back into the main sheet by header match."""
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl not installed.")
    wb = openpyxl.load_workbook(io.BytesIO(original_bytes))
    if sheet_name not in wb.sheetnames:
        # fallback to first sheet if mismatch
        sheet_name = wb.sheetnames[0]
    ws = wb[sheet_name]

    # map headers
    header_row = 1
    col_index_by_name = {}
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row, column=col).value
        if isinstance(v, str):
            col_index_by_name[v.strip()] = col

    symptom_cols_present = [c for c in symptom_cols if c in col_index_by_name]
    if not symptom_cols_present:
        # nothing to write
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    # Write each row by DataFrame order (assumes header row=1, data starts at row=2)
    for r in range(len(df_updated)):
        excel_row = header_row + 1 + r
        for c in symptom_cols_present:
            val = df_updated.iloc[r].get(c, "")
            ws.cell(row=excel_row, column=col_index_by_name[c], value=None if (pd.isna(val) or str(val).strip()=="") else str(val))

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

try:
    excel_out = write_symptoms_back_to_workbook(uploaded_bytes, df, main_sheet)
    st.download_button(
        "Download Excel (preserve original formatting where possible)",
        data=excel_out,
        file_name="starwalk_updated.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
except Exception as e:
    st.warning(f"Excel download unavailable: {e}")

# -------------------------- Tips --------------------------
with st.expander("💡 Tips & Notes", expanded=False):
    st.markdown("""
- **Accuracy over stretch**: The app applies conservative lexical checks and dedupes near-duplicates
  (e.g., prefers **“learning curve”** over **“initial difficulty”**).
- **New symptoms**: Any model-suggested labels not in the canonical list are surfaced for your approval.
  Approved items join the session’s canonical lists and can be saved back by downloading Excel and updating your master “Symptoms” sheet.
- **ETA** is a heuristic; actual time depends on network & account throughput.
- To improve precision further, keep your **Symptoms** labels short, specific, and non-overlapping.
""")
