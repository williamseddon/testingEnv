# Write the enhanced Streamlit app to a file for download
code = r'''# ---------- Star Walk — Upload + Symptomize (Enhanced UX, 14" UI & Real-time + Speed) ----------
# Streamlit 1.38+

import io
import os
import re
import json
import difflib
import time
import hashlib
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# Optional OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Optional: preserve workbook formatting
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------------- Force Light Mode ----------------
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

# ---------------- Global CSS (14" compact) ----------------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 80px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }
  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#94a3b8; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626; --warn:#b45309;
    --gap-sm:10px; --gap-md:16px; --gap-lg:24px;
  }
  html, body, .stApp { background: var(--bg-app); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, sans-serif; color: var(--text); }
  .block-container { padding-top:.6rem; padding-bottom:.9rem; max-width: 1280px; }
  .hero-wrap{ position:relative; overflow:hidden; border-radius:12px; min-height:92px; margin:.15rem 0 .6rem 0; box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05); background:linear-gradient(90deg, var(--bg-card) 0% 60%, transparent 60% 100%); }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:8px 14px; color:var(--text); }
  .hero-title{ font-size:clamp(18px,2.3vw,30px); font-weight:800; margin:0; line-height:1.1; }
  .hero-sub{ margin:2px 0 0 0; color:var(--muted); font-size:clamp(11px,1vw,14px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:36%; }
  .sn-logo{ height:36px; width:auto; display:block; opacity:.92; }
  .card{ background:var(--bg-card); border-radius:12px; padding:12px; box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05); }
  .muted{ color:var(--muted); }
  .pill{ padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:var(--bg-tile); font-weight:700; font-size:12px; }
  .kpi{ display:flex; gap:10px; flex-wrap:wrap }
  .review-quote { white-space:pre-wrap; background:var(--bg-tile); border:1px solid var(--border); border-radius:10px; padding:8px 10px; font-size:13px; }
  mark { background:#fff2a8; padding:0 .15em; border-radius:3px; }
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
  .chip{padding:5px 9px;border-radius:999px;border:1px solid var(--border);background:var(--bg-tile);font-weight:700;font-size:.85rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .badge{display:inline-flex;gap:6px;align-items:center;padding:3px 8px;border-radius:999px;border:1px solid var(--border);font-size:12px;background:var(--bg-card)}
  .badge.ok{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .badge.warn{border-color:#FDECC8;background:#FFF7ED;color:#7C2D12}
  .badge.bad{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .tight > div[data-testid="stHorizontalBlock"]{ gap: 8px !important; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------------- Header ----------------
st.markdown(
    """
    <div class="hero-wrap">
      <div class="hero-inner">
        <div>
          <div class="hero-title">Star Walk — Symptomize Reviews</div>
          <div class="hero-sub">Upload, detect missing symptoms, and let AI suggest precise delighters & detractors (with human approval).</div>
        </div>
        <div class="hero-right"><img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"/></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ========================== SIDEBAR ==========================
with st.sidebar:
    st.header("📁 Upload Star Walk File")
    uploaded = st.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

    st.markdown("---")
    st.subheader("⚙️ Run Settings")
    speed_mode = st.toggle("Speed mode", value=False, help="Uses faster model & shorter reviews first (does not shorten text).")
    strictness = st.slider("Strictness", 0.55, 0.95, 0.75, 0.01,
                           help="Confidence + evidence threshold; also reduces near-duplicates.")
    require_evidence = st.checkbox("Require textual evidence", value=True)
    evidence_hits_required = st.selectbox("Min evidence tokens", options=[1,2], index=1 if strictness>=0.8 else 0)

    st.caption("Advanced")
    model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=0)
    max_output_tokens = st.number_input("Max output tokens", 64, 2000, 400, 32,
                                        help="Limits LLM output to keep latency low.")
    candidate_cap = st.slider("Cap candidates per review", 10, 120, 40, 5,
                              help="Prefilter allowed lists per review to reduce tokens. This does NOT shorten the review text.")
    api_concurrency = st.slider("API concurrency", 1, 8, 4, help="Parallelize requests (watch rate limits).")

    st.info("Reviews are sent in full — no truncation applied.")

# Persist raw bytes for formatting-preserving save
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

# ---------------- Load main sheet ----------------
@st.cache_data(show_spinner=False)
def _load_main_sheet(_uploaded: io.BytesIO) -> pd.DataFrame:
    try:
        try:
            return pd.read_excel(_uploaded, sheet_name="Star Walk scrubbed verbatims")
        except ValueError:
            return pd.read_excel(_uploaded)
    except Exception as e:
        raise RuntimeError(f"Could not read the Excel file: {e}")

try:
    df = _load_main_sheet(uploaded)
except Exception as e:
    st.error(str(e))
    st.stop()

# ---------------- Identify Symptom Columns ----------------
explicit_cols = [f"Symptom {i}" for i in range(1,21)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()  # K–AD fallback
if not SYMPTOM_COLS:
    st.error("Couldn't locate Symptom 1–20 columns (K–AD).")
    st.stop()

# Missing symptom rows
is_empty = df[SYMPTOM_COLS].isna() | (
    df[SYMPTOM_COLS]
    .astype(str)
    .applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"})
)
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Review length IQR for ETA
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
q1 = verb_series.str.len().quantile(0.25) if not verb_series.empty else 0
q3 = verb_series.str.len().quantile(0.75) if not verb_series.empty else 0
IQR = (q3 - q1) if (q3 or q1) else 0

# ---------------- Load symptom dictionary from "Symptoms" sheet (robust + user overrides) ----------------
import io as _io

# Helpers to robustly find sheets/columns
def _norm(s: str) -> str:
    if s is None: return ""
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    return "symptom" in _norm(name)

def _col_score(colname: str, want: str) -> int:
    n = _norm(colname)
    if not n: return 0
    synonyms = {
        "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
        "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues"],
    }
    return max((1 for token in synonyms[want] if token in n), default=0)

def _extract_from_df(df_sheet: pd.DataFrame):
    """Try multiple layouts and return (delighters, detractors, debug)."""
    debug = {"strategy": None, "columns": list(df_sheet.columns)}
    # Strategy 1: fuzzy headers
    best_del = None; best_det = None
    for c in df_sheet.columns:
        if _col_score(str(c), "delighters"): best_del = c if best_del is None else best_del
        if _col_score(str(c), "detractors"): best_det = c if best_det is None else best_det
    if best_del is not None or best_det is not None:
        dels_ser = df_sheet.get(best_del, pd.Series(dtype=str)) if best_del is not None else pd.Series(dtype=str)
        dets_ser = df_sheet.get(best_det, pd.Series(dtype=str)) if best_det is not None else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels_ser.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets_ser.dropna().tolist() if str(x).strip()]
        if dels or dets:
            debug.update({"strategy":"fuzzy-headers","best_del_col":best_del,"best_det_col":best_det})
            return dels, dets, debug
    # Strategy 2: Type/Category + Item
    type_col = None; item_col = None
    for c in df_sheet.columns:
        if _norm(c) in {"type","category","class","label"}: type_col = c
        if _norm(c) in {"item","symptom","name","term","entry","value"}: item_col = c
    if type_col is not None and item_col is not None:
        t = df_sheet[type_col].astype(str).str.strip().str.lower()
        i = df_sheet[item_col].astype(str).str.strip()
        dels = i[t.str.contains("delight|pro|positive", na=False)]
        dets = i[t.str.contains("detract|con|negative", na=False)]
        dels = [x for x in dels.dropna().tolist() if x]
        dets = [x for x in dets.dropna().tolist() if x]
        if dels or dets:
            debug.update({"strategy":"type+item","type_col":type_col,"item_col":item_col})
            return dels, dets, debug
    # Strategy 3: first two non-empty columns
    non_empty_cols = []
    for c in df_sheet.columns:
        vals = [str(x).strip() for x in df_sheet[c].dropna().tolist() if str(x).strip()]
        if vals:
            non_empty_cols.append((c, vals))
        if len(non_empty_cols) >= 2: break
    if non_empty_cols:
        dels = non_empty_cols[0][1]
        dets = non_empty_cols[1][1] if len(non_empty_cols) > 1 else []
        debug.update({"strategy":"first-two-nonempty","picked_cols":[c for c,_ in non_empty_cols[:2]]})
        return dels, dets, debug
    return [], [], {"strategy":"none","columns":list(df_sheet.columns)}


def autodetect_symptom_sheet(xls: pd.ExcelFile) -> str | None:
    names = xls.sheet_names
    cands = [n for n in names if _looks_like_symptom_sheet(n)]
    if cands:
        return min(cands, key=lambda n: len(_norm(n)))
    return names[0] if names else None


@st.cache_data(show_spinner=False)
def load_symptom_lists_robust(raw_bytes: bytes, user_sheet: str | None = None, user_del_col: str | None = None, user_det_col: str | None = None):
    meta = {"sheet": None, "strategy": None, "columns": [], "note": ""}
    if not raw_bytes:
        meta["note"] = "No raw bytes provided"
        return [], [], meta
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw_bytes))
    except Exception as e:
        meta["note"] = f"Could not open Excel: {e}"
        return [], [], meta
    sheet = user_sheet or autodetect_symptom_sheet(xls)
    if not sheet:
        meta["note"] = "No sheets found"
        return [], [], meta
    meta["sheet"] = sheet
    try:
        s = pd.read_excel(xls, sheet_name=sheet)
    except Exception as e:
        meta["note"] = f"Could not read sheet '{sheet}': {e}"
        return [], [], meta
    if user_del_col or user_det_col:
        dels = s.get(user_del_col, pd.Series(dtype=str)) if user_del_col in s.columns else pd.Series(dtype=str)
        dets = s.get(user_det_col, pd.Series(dtype=str)) if user_det_col in s.columns else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"manual-columns","columns":list(s.columns)})
        return dels, dets, meta
    dels, dets, info = _extract_from_df(s)
    meta.update(info)
    return dels, dets, meta

# ---- Symptoms sheet picker (UI) ----
raw_bytes = st.session_state.get("uploaded_bytes", b"")

sheet_names = []
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes))
    sheet_names = _xls_tmp.sheet_names
except Exception:
    pass

auto_sheet = autodetect_symptom_sheet(_xls_tmp) if sheet_names else None

with st.sidebar:
    chosen_sheet = st.selectbox(
        "Choose the sheet that contains Delighters/Detractors",
        options=sheet_names if sheet_names else ["(no sheets detected)"],
        index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0)
    )

    # Preview columns for manual selection
    symp_cols_preview = []
    if sheet_names:
        try:
            _df_symp_prev = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name=chosen_sheet)
            symp_cols_preview = list(_df_symp_prev.columns)
        except Exception:
            _df_symp_prev = pd.DataFrame()
            symp_cols_preview = []

    manual_cols = False
    picked_del_col = None
    picked_det_col = None

    if symp_cols_preview:
        st.caption("Detected columns:")
        st.write(", ".join(map(str, symp_cols_preview)))
        manual_cols = st.checkbox("Manually choose Delighters/Detractors columns", value=False)
        if manual_cols:
            picked_del_col = st.selectbox("Delighters column", options=["(none)"] + symp_cols_preview, index=0)
            picked_det_col = st.selectbox("Detractors column", options=["(none)"] + symp_cols_preview, index=0)
            if picked_del_col == "(none)": picked_del_col = None
            if picked_det_col == "(none)": picked_det_col = None

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes, user_sheet=chosen_sheet if sheet_names else None, user_del_col=picked_del_col, user_det_col=picked_det_col
)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

with st.sidebar:
    if ALLOWED_DELIGHTERS or ALLOWED_DETRACTORS:
        st.success(
            f"Loaded {len(ALLOWED_DELIGHTERS)} delighters, {len(ALLOWED_DETRACTORS)} detractors (sheet: '{SYM_META.get('sheet','?')}', mode: {SYM_META.get('strategy','?')})."
        )
    else:
        st.warning(
            f"Didn't find clear Delighters/Detractors lists in '{SYM_META.get('sheet','?')}'. Using conservative keyword fallback. Adjust options above if needed."
        )

# ========================== TOP KPIs ==========================
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<span class='pill'>🧾 Total reviews: <b>{len(df)}</b></span>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<span class='pill'>❌ Missing symptoms: <b>{missing_count}</b></span>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<span class='pill'>✂ IQR chars: <b>{int(IQR)}</b></span>", unsafe_allow_html=True)
with colD:
    st.caption("Estimates scale by model, token budget and text length; indicative only.")

left, right = st.columns([1.4, 2.6], gap="small")

with left:
    st.markdown("### Run Controller")
    batch_n = st.slider("How many to process this run", 1, 30, min(12, max(1, missing_count)) if missing_count else 12)

    # ETA (heuristic) — token-aware
    MODEL_TPS = {"gpt-4o-mini": 55, "gpt-4o": 25, "gpt-4.1": 16, "gpt-5": 12}
    MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
    rows = min(batch_n, missing_count)
    chars_est = max(200, int((q1+q3)/2)) if (q1 or q3) else 400
    tok_est = int(chars_est/4)
    rt = rows * (MODEL_LAT.get(model_choice,1.0) + tok_est/max(8, MODEL_TPS.get(model_choice,12)))
    eta_secs = int(round(rt))
    st.caption(f"Will attempt {rows} rows • Rough ETA: ~{eta_secs}s")

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    if missing_count and not _HAS_OPENAI:
        st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
    if missing_count and _HAS_OPENAI and not api_key:
        st.warning("Set `OPENAI_API_KEY` (env or secrets) to enable AI labeling.")

    st.markdown("---")
    can_run = missing_count > 0 and ((not _HAS_OPENAI) or (api_key is not None))

    col_runA, col_runB = st.columns([1,1])
    with col_runA:
        run = st.button(
            f"✨ Symptomize next {min(batch_n, missing_count)}",
            disabled=not can_run,
            help="Runs on the next batch of reviews missing symptoms.",
            use_container_width=True,
        )
    with col_runB:
        enable_all = st.checkbox("Enable ALL (bulk)")
        run_all = st.button(
            f"⚡ Symptomize ALL {missing_count}",
            disabled=(not can_run) or missing_count==0 or (not enable_all),
            help="Processes every review that has empty Symptom 1–20. Uses many API calls.",
            use_container_width=True,
        )
    st.caption("Tip: Use batch mode first to review accuracy, then run ALL.")

with right:
    st.markdown("### Live Processing")
    if "live_rows" not in st.session_state:
        st.session_state["live_rows"] = []  # list of dict rows for the live table
    live_table_ph = st.empty()
    live_detail_ph = st.empty()
    live_progress = st.progress(0)

# ---------------- Session State (non-UI) ----------------

st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("approved_new_delighters", set())
st.session_state.setdefault("approved_new_detractors", set())
st.session_state.setdefault("pick_cache", {})

# ---------------- Helpers ----------------

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()

# Canonical aliases to avoid near-duplicate picks (extendable)
ALIAS_CANON = {
    "initial difficulty": "Learning curve",
    "hard to learn": "Learning curve",
    "setup difficulty": "Learning curve",
    "noisy startup": "Startup noise",
    "too loud": "Loud",
}

def canonicalize(name: str) -> str:
    nn = (name or "").strip()
    base = _normalize_name(nn)
    for k, v in ALIAS_CANON.items():
        if _normalize_name(k) == base:
            return v
    return nn

# Evidence scoring: count token hits from symptom within the review
_def_word = re.compile(r"[a-z0-9]{3,}")

def _evidence_score(symptom: str, text: str) -> tuple[int, list[str]]:
    if not symptom or not text:
        return 0, []
    toks = [t for t in _normalize_name(symptom).split() if _def_word.match(t)]
    hits = []
    for t in toks:
        try:
            if re.search(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE):
                hits.append(t)
        except re.error:
            pass
    return len(hits), hits

# Conservative dedupe + cut to N with canonicalization and similarity guard

def _dedupe_keep_top(items: list[tuple[str, float]], top_n: int = 10, min_conf: float = 0.60) -> list[str]:
    # canonicalize and filter by confidence
    canon_pairs: list[tuple[str, float]] = []
    for (n, c) in items:
        if c >= min_conf and n:
            canon_pairs.append((canonicalize(n), c))
    kept: list[tuple[str, float]] = []
    for n, c in sorted(canon_pairs, key=lambda x: -x[1]):
        n_norm = _normalize_name(n)
        if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(k)).ratio() > 0.88 for k, _ in kept):
            kept.append((n, c))
        if len(kept) >= top_n:
            break
    return [n for n, _ in kept]

# Highlight allowed terms in review for quick verification (true word boundaries)

def _highlight_terms(text: str, allowed: list[str]) -> str:
    out = text
    for t in sorted(set(allowed), key=len, reverse=True):
        if not t.strip():
            continue
        try:
            out = re.sub(rf"(\b{re.escape(t)}\b)", r"<mark>\1</mark>", out, flags=re.IGNORECASE)
        except re.error:
            pass
    return out

# ---- Speed helpers: cached client, candidate prefilter, and caching ----
@st.cache_resource(show_spinner=False)
def _get_openai_client_cached(key: str):
    return OpenAI(api_key=key)

def _hash_list(lst: list[str]) -> str:
    return hashlib.sha1("\x1f".join(lst).encode("utf-8")).hexdigest()

def _prefilter_candidates(review: str, allowed: list[str], cap: int = 40) -> list[str]:
    """Quickly score allowed terms by evidence hits; keep top-N to cut tokens (does not shorten review text)."""
    text = (review or "").lower()
    scored = []
    for a in allowed:
        toks = [t for t in _normalize_name(a).split() if len(t) > 2]
        if not toks:
            continue
        hits = sum(1 for t in toks if f" {t} " in f" {text} ")
        if hits:
            scored.append((a, hits/len(toks)))
    # if nothing hits, fall back to the first few allowed to avoid empty lists
    if not scored:
        return allowed[:cap]
    scored.sort(key=lambda x: x[1], reverse=True)
    keep = [s[0] for s in scored[:cap]]
    return keep

# Model call (JSON-only) with evidence guardrails

def _llm_pick(review: str, stars, allowed_del: list[str], allowed_det: list[str], min_conf: float,
              evidence_hits_required: int = 1, candidate_cap: int = 40, max_output_tokens: int = 400):
    """Return (allowed_delighters, allowed_detractors, novel_delighters, novel_detractors).
    Faster via: prefiltered candidates, cached client, small max tokens, local cache.
    NOTE: The full review text is sent; no truncation applied.
    """
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], []

    # Prefilter candidates to trim tokens massively (does not touch review text)
    allowed_del_f = _prefilter_candidates(review, allowed_del, cap=candidate_cap)
    allowed_det_f = _prefilter_candidates(review, allowed_det, cap=candidate_cap)

    # Cache key based on inputs
    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    cache_key = "|".join([
        str(model_choice), str(min_conf), str(evidence_hits_required),
        _hash_list(sorted(allowed_del_f)), _hash_list(sorted(allowed_det_f)),
        hashlib.sha1((review or "").encode("utf-8")).hexdigest(), str(stars)
    ])
    pcache = st.session_state.get("pick_cache", {})
    if cache_key in pcache:
        return pcache[cache_key]

    sys_prompt = (
        """
You are labeling a single user review.
Choose up to 10 delighters and up to 10 detractors ONLY from the provided lists.
Return JSON exactly like:
{"delighters":[{"name":"...","confidence":0.0}], "detractors":[{"name":"...","confidence":0.0}]}

Rules:
1) If not clearly present, OMIT it.
2) Prefer precision over recall; avoid stretch matches.
3) Avoid near-duplicates (use canonical terms, e.g., 'Learning curve' not 'Initial difficulty').
4) If stars are 1–2, bias to detractors; if 4–5, bias to delighters; otherwise neutral.
        """
    )

    user =  {
        "review": review,  # <-- full text, no truncation
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del_f[:120],
        "allowed_detractors": allowed_det_f[:120]
    }

    dels, dets, novel_dels, novel_dets = [], [], [], []

    if _HAS_OPENAI and api_key:
        try:
            client = _get_openai_client_cached(api_key)
            req = {
                "model": model_choice,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user)}
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": max_output_tokens,
            }
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.0
            out = client.chat.completions.create(**req)
            content = out.choices[0].message.content or "{}"
            data = json.loads(content)
            dels_raw = data.get("delighters", []) or []
            dets_raw = data.get("detractors", []) or []
            dels_pairs = [(canonicalize(d.get("name", "")), float(d.get("confidence", 0))) for d in dels_raw if d.get("name")]
            dets_pairs = [(canonicalize(d.get("name", "")), float(d.get("confidence", 0))) for d in dets_raw if d.get("name")]
            # Evidence filter (guardrail)
            text = review or ""
            dels_pairs = [p for p in dels_pairs if _evidence_score(p[0], text)[0] >= evidence_hits_required]
            dets_pairs = [p for p in dets_pairs if _evidence_score(p[0], text)[0] >= evidence_hits_required]
            for n, c in dels_pairs:
                if n in ALLOWED_DELIGHTERS_SET: dels.append((n, c))
                else: novel_dels.append((n, c))
            for n, c in dets_pairs:
                if n in ALLOWED_DETRACTORS_SET: dets.append((n, c))
                else: novel_dets.append((n, c))
            result = (
                _dedupe_keep_top(dels, 10, min_conf),
                _dedupe_keep_top(dets, 10, min_conf),
                _dedupe_keep_top(novel_dels, 5, max(0.70, min_conf)),
                _dedupe_keep_top(novel_dets, 5, max(0.70, min_conf))
            )
            st.session_state["pick_cache"][cache_key] = result
            return result
        except Exception:
            pass

    # Conservative keyword fallback (no-API)
    text = " " + (review or "").lower() + " "
    def pick_from_allowed(allowed: list[str]) -> list[str]:
        scored = []
        for a in allowed:
            a_can = canonicalize(a)
            toks = [t for t in _normalize_name(a_can).split() if len(t) > 2]
            if not toks:
                continue
            hits = [t for t in toks if f" {t} " in text]
            score = len(hits) / len(toks)
            if len(hits) >= evidence_hits_required and score >= min_conf:
                scored.append((a_can, 0.60 + 0.4 * score))
        return _dedupe_keep_top(scored, 10, min_conf)

    result = (pick_from_allowed(allowed_del_f), pick_from_allowed(allowed_det_f), [], [])
    st.session_state["pick_cache"][cache_key] = result
    return result

# ---------------- Live renderer helpers ----------------

def _render_live_table():
    rows = st.session_state.get("live_rows", [])
    if not rows:
        live_table_ph.info("Nothing processed yet. Click Symptomize to start.")
        return
    live_df = pd.DataFrame(rows, columns=[
        "Row #", "Stars", "Len", "Detractors #", "Delighters #", "Status"
    ])
    live_table_ph.dataframe(
        live_df,
        use_container_width=True,
        hide_index=True,
        height=320,
    )

# ---------------- Run Symptomize (with real-time UI + parallelism) ----------------

if (run or run_all) and missing_idx:
    # Determine todo order
    if speed_mode:
        # process shortest reviews first (does not shorten text, only ordering)
        missing_idx_sorted = sorted(missing_idx, key=lambda i: len(str(df.loc[i].get("Verbatim",""))))
    else:
        missing_idx_sorted = missing_idx

    todo = missing_idx_sorted if run_all else missing_idx_sorted[:batch_n]

    # Seed the queue view
    st.session_state["live_rows"] = [
        [int(idx),
         (float(df.loc[idx].get("Star Rating")) if pd.notna(df.loc[idx].get("Star Rating")) else None),
         int(len(str(df.loc[idx].get("Verbatim", "")))),
         0, 0,
         "queued"]
        for idx in todo
    ]
    _render_live_table()

    def _process_one(idx: int):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        return idx, _llm_pick(
            review_txt,
            stars,
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            strictness,
            evidence_hits_required=evidence_hits_required,
            candidate_cap=candidate_cap,
            max_output_tokens=max_output_tokens,
        )

    with st.status("Processing reviews…", expanded=True) as status_box:
        completed = 0
        live_progress.progress(0)
        if st.session_state["live_rows"]:
            st.session_state["live_rows"][0][-1] = "processing"
            _render_live_table()

        with ThreadPoolExecutor(max_workers=api_concurrency) as ex:
            futures = {ex.submit(_process_one, idx): idx for idx in todo}
            for fut in as_completed(futures):
                idx, (dels, dets, novel_dels, novel_dets) = fut.result()
                # find display row by df idx
                try:
                    row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                except ValueError:
                    row_pos = None
                if row_pos is not None:
                    st.session_state["live_rows"][row_pos][3] = len(dets)
                    st.session_state["live_rows"][row_pos][4] = len(dels)
                    st.session_state["live_rows"][row_pos][-1] = "done"

                # Save suggestion bundle
                st.session_state["symptom_suggestions"].append({
                    "row_index": int(idx),
                    "stars": float(df.loc[idx].get("Star Rating")) if pd.notna(df.loc[idx].get("Star Rating")) else None,
                    "review": str(df.loc[idx].get("Verbatim", "") or "").strip(),
                    "delighters": dels,
                    "detractors": dets,
                    "novel_delighters": novel_dels,
                    "novel_detractors": novel_dets,
                    "approve_novel_del": [],
                    "approve_novel_det": [],
                })
                completed += 1
                live_progress.progress(completed/len(todo))
                status_box.write(f"Completed {completed}/{len(todo)} — df idx {idx}: det {len(dets)} / del {len(dels)}")
                try:
                    st.toast(f"df idx {idx}: det {len(dets)} / del {len(dels)}")
                except Exception:
                    pass
                _render_live_table()

        status_box.update(label="Finished generating suggestions! Review below, then Apply to write into the sheet.", state="complete")
    st.rerun()

# ---------------- Review & Approve ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## 🔍 Review & Approve Suggestions")

    # Allowed lists viewer (compact)
    with st.expander("📚 View allowed symptom palettes (from 'Symptoms' sheet)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Allowed Detractors** ({len(ALLOWED_DETRACTORS)}):")
            if ALLOWED_DETRACTORS:
                st.markdown("<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in ALLOWED_DETRACTORS]) + "</div>", unsafe_allow_html=True)
            else:
                st.caption("None detected")
        with c2:
            st.markdown(f"**Allowed Delighters** ({len(ALLOWED_DELIGHTERS)}):")
            if ALLOWED_DELIGHTERS:
                st.markdown("<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in ALLOWED_DELIGHTERS]) + "</div>", unsafe_allow_html=True)
            else:
                st.caption("None detected")

    # Fast bulk actions using direct session updates (no per-checkbox loops)
    with st.expander("Bulk actions", expanded=True):
        c1,c2,c3,c4,c5 = st.columns([1,1,1,2,3])
        if "sug_selected" not in st.session_state:
            st.session_state["sug_selected"] = set()
        total = len(sugs)
        with c1:
            if st.button("Select all (fast)"):
                st.session_state["sug_selected"] = set(range(total))
                for i in range(total):
                    st.session_state[f"sel_{i}"] = True
        with c2:
            if st.button("Clear all"):
                st.session_state["sug_selected"] = set()
                for i in range(total):
                    st.session_state[f"sel_{i}"] = False
        with c3:
            if st.button("Invert"):
                newset = set()
                for i in range(total):
                    cur = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])  # current visual value
                    cur = not cur
                    st.session_state[f"sel_{i}"] = cur
                    if cur:
                        newset.add(i)
                st.session_state["sug_selected"] = newset
        with c4:
            if st.button("Only with suggestions"):
                keep = {i for i,s in enumerate(sugs) if s["delighters"] or s["detractors"]}
                st.session_state["sug_selected"] = keep
                for i in range(total):
                    st.session_state[f"sel_{i}"] = (i in keep)
        with c5:
            max_apply = st.slider("Max rows to apply now", 1, total, min(20, total))

    for i, s in enumerate(sugs):
        label = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i==0)):
            # Selection checkbox bound to session key
            default_checked = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
            checked = st.checkbox("Select for apply", value=default_checked, key=f"sel_{i}")
            if checked:
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            # Full review with highlights
            if s["review"]:
                highlighted = _highlight_terms(s["review"], ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS)
                st.markdown("**Full review:**")
                st.markdown(f"<div class='review-quote'>{highlighted}</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Full review:** (empty)")

            # Pretty chips for suggestions
            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors (≤10)**")
                if s["detractors"]:
                    html = "<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in s["detractors"]]) + "</div>"
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.code("–")
            with c2:
                st.write("**Delighters (≤10)**")
                if s["delighters"]:
                    html = "<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in s["delighters"]]) + "</div>"
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.code("–")

            # Novel candidates with approval toggles
            if s["novel_detractors"] or s["novel_delighters"]:
                st.info("Potential NEW symptoms (not in your list). Approve to add & allow.")
                c3,c4 = st.columns(2)
                with c3:
                    if s["novel_detractors"]:
                        st.write("**Novel Detractors (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_detractors"]):
                            if st.checkbox(name, key=f"novdet_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_det"] = picks
                with c4:
                    if s["novel_delighters"]:
                        st.write("**Novel Delighters (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_delighters"]):
                            if st.checkbox(name, key=f"novdel_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_del"] = picks

    # Apply button
    if st.button("✅ Apply selected to DataFrame", use_container_width=True):
        picked = [i for i in st.session_state["sug_selected"]]
        if not picked:
            st.warning("Nothing selected.")
        else:
            picked = picked[:max_apply]
            for i in picked:
                s = sugs[i]
                ri = s["row_index"]
                dets_final = (s["detractors"] + s.get("approve_novel_det", []))[:10]
                dels_final = (s["delighters"] + s.get("approve_novel_del", []))[:10]
                # write to df
                for j, name in enumerate(dets_final, start=1):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                for j, name in enumerate(dels_final, start=11):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                # accumulate approved-new for workbook append later
                for n in s.get("approve_novel_del", []):
                    st.session_state["approved_new_delighters"].add(n)
                for n in s.get("approve_novel_det", []):
                    st.session_state["approved_new_detractors"].add(n)
            st.success(f"Applied {len(picked)} row(s) to DataFrame.")

# ---------------- Novel Symptoms Review Center ----------------
# Aggregate proposals across all suggestions for a single review hub
pending_novel_del = {}
pending_novel_det = {}
for _s in st.session_state.get("symptom_suggestions", []):
    for name in _s.get("novel_delighters", []):
        if name: pending_novel_del[name] = pending_novel_del.get(name, 0) + 1
    for name in _s.get("novel_detractors", []):
        if name: pending_novel_det[name] = pending_novel_det.get(name, 0) + 1

_total_novel = len(pending_novel_del) + len(pending_novel_det)
if _total_novel:
    with st.expander(f"🧪 Review New Symptoms ({_total_novel} pending)", expanded=True):
        tabs = st.tabs(["Novel Detractors", "Novel Delighters"])

        def _review_table(pending_dict, kind):
            if not pending_dict:
                st.caption("No proposals.")
                return
            add_all = st.checkbox(f"Approve all {kind}", key=f"approve_all_{kind}")
            for i, (name, count) in enumerate(sorted(pending_dict.items(), key=lambda x: (-x[1], x[0]))):
                cols = st.columns([0.07, 0.58, 0.20, 0.15])
                with cols[0]:
                    approve = st.checkbox("", value=add_all, key=f"nov_{kind}_{i}_approve")
                with cols[1]:
                    new_name = st.text_input("Name", value=name, key=f"nov_{kind}_{i}_name", label_visibility="collapsed")
                with cols[2]:
                    st.write(f"Seen in **{count}** rows")
                with cols[3]:
                    st.caption(kind[:-1].capitalize())
                if approve:
                    if kind=="detractors":
                        st.session_state["approved_new_detractors"].add(new_name.strip())
                    else:
                        st.session_state["approved_new_delighters"].add(new_name.strip())

        with tabs[0]:
            _review_table(pending_novel_det, "detractors")
        with tabs[1]:
            _review_table(pending_novel_del, "delighters")

        if st.button("Apply approvals now (update allowed lists)"):
            for n in list(st.session_state["approved_new_detractors"]):
                if n and n not in ALLOWED_DETRACTORS_SET:
                    ALLOWED_DETRACTORS.append(n); ALLOWED_DETRACTORS_SET.add(n)
            for n in list(st.session_state["approved_new_delighters"]):
                if n and n not in ALLOWED_DELIGHTERS_SET:
                    ALLOWED_DELIGHTERS.append(n); ALLOWED_DELIGHTERS_SET.add(n)
            st.success("Allowed lists updated for this session.")

# ---------------- Download Updated Workbook ----------------

def offer_downloads():
    st.markdown("### ⬇️ Download Updated Workbook")
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first.")
        return
    raw = st.session_state["uploaded_bytes"]
    # Try formatting-preserving write of symptom columns, and append approved novel symptoms
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = "Star Walk scrubbed verbatims"
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name): return headers.get(name)
            # Write symptoms only (data begins row 2)
            for df_row_idx, row in df.reset_index(drop=True).iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci:
                        ws.cell(row=excel_row, column=ci).value = row.get(c, None)
            # Append approved novel items into Symptoms sheet if present
            symptoms_sheet_name = None
            for n in wb.sheetnames:
                if n.strip().lower() in {"symptoms","symptom","symptom sheet","symptom tab"}:
                    symptoms_sheet_name = n; break
            if symptoms_sheet_name:
                ss = wb[symptoms_sheet_name]
                # map headers
                sh = {ss.cell(row=1, column=ci).value: ci for ci in range(1, ss.max_column+1)}
                del_col = sh.get("Delighters") or sh.get("delighters")
                det_col = sh.get("Detractors") or sh.get("detractors")
                if del_col:
                    existing = set()
                    for r in range(2, ss.max_row+1):
                        v = ss.cell(row=r, column=del_col).value
                        if v and str(v).strip(): existing.add(str(v).strip())
                    for item in sorted(st.session_state["approved_new_delighters"]):
                        if item not in existing:
                            ss.append([None]*(del_col-1) + [item])
                if det_col:
                    existing = set()
                    for r in range(2, ss.max_row+1):
                        v = ss.cell(row=r, column=det_col).value
                        if v and str(v).strip(): existing.add(str(v).strip())
                    for item in sorted(st.session_state["approved_new_detractors"]):
                        if item not in existing:
                            row = [None]*(det_col-1) + [item]
                            ss.append(row)
            out = io.BytesIO()
            wb.save(out)
            st.download_button("Download updated workbook (.xlsx)", data=out.getvalue(), file_name="StarWalk_updated.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        except Exception:
            pass
    # Fallback
    out2 = io.BytesIO()
    with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
    st.download_button("Download updated workbook (.xlsx) — no formatting", data=out2.getvalue(), file_name="StarWalk_updated_basic.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads()
'''
path = "/mnt/data/star_walk_app.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(code)

import os, textwrap
size = os.path.getsize(path)
print(f"Saved {path} ({size} bytes)")



