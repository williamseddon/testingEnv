# ---------- Star Walk — Upload + Symptomize (Enhanced UX, Accuracy & Approvals) ----------
# Streamlit 1.38+

import io
import os
import re
import json
import difflib
from typing import List, Tuple

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

# ---------------- Global CSS ----------------
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
  html, body, .stApp { background: var(--bg-app); font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif; color: var(--text); }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  .hero-wrap{ position:relative; overflow:hidden; border-radius:14px; min-height:120px; margin:.25rem 0 1rem 0; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%); }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:10px 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.1vw,40px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:46px; width:auto; display:block; opacity:.92; }
  .card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); }
  .muted{ color:var(--muted); }
  .kpi{ display:flex; gap:14px; flex-wrap:wrap }
  .pill{ padding:8px 12px; border-radius:999px; border:1.5px solid var(--border); background:var(--bg-tile); font-weight:700 }
  .review-quote { white-space:pre-wrap; background:var(--bg-tile); border:1.5px solid var(--border); border-radius:12px; padding:8px 10px; }
  mark { background:#fff2a8; padding:0 .15em; border-radius:3px; }
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

# ---------------- Upload ----------------
st.sidebar.header("📁 Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

# Persist raw bytes for formatting-preserving save
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

# Load main sheet
try:
    try:
        df = pd.read_excel(uploaded, sheet_name="Star Walk scrubbed verbatims")
    except ValueError:
        df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Could not read the Excel file: {e}")
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
is_empty = df[SYMPTOM_COLS].isna() | (df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"}))
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
st.sidebar.markdown("### 🧾 Symptoms Source")
raw_bytes = st.session_state.get("uploaded_bytes", b"")

sheet_names = []
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes))
    sheet_names = _xls_tmp.sheet_names
except Exception:
    pass

auto_sheet = autodetect_symptom_sheet(_xls_tmp) if sheet_names else None
chosen_sheet = st.sidebar.selectbox(
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
    st.sidebar.caption("Detected columns:")
    st.sidebar.write(", ".join(map(str, symp_cols_preview)))
    manual_cols = st.sidebar.checkbox("Manually choose Delighters/Detractors columns", value=False)
    if manual_cols:
        picked_del_col = st.sidebar.selectbox("Delighters column", options=["(none)"] + symp_cols_preview, index=0)
        picked_det_col = st.sidebar.selectbox("Detractors column", options=["(none)"] + symp_cols_preview, index=0)
        if picked_del_col == "(none)": picked_del_col = None
        if picked_det_col == "(none)": picked_det_col = None

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes, user_sheet=chosen_sheet if sheet_names else None, user_del_col=picked_del_col, user_det_col=picked_det_col
)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

if ALLOWED_DELIGHTERS or ALLOWED_DETRACTORS:
    st.sidebar.success(
        f"Loaded {len(ALLOWED_DELIGHTERS)} delighters, {len(ALLOWED_DETRACTORS)} detractors (sheet: '{SYM_META.get('sheet','?')}', mode: {SYM_META.get('strategy','?')})."
    )
else:
    st.sidebar.warning(
        f"Didn't find clear Delighters/Detractors lists in '{SYM_META.get('sheet','?')}'. Using conservative keyword fallback. Adjust options above if needed."
    )

# ---------------- Top KPIs & Actions ----------------

st.markdown("### Status")
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<div class='pill'>🧾 Total reviews: <b>{len(df)}</b></div>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<div class='pill'>❌ Missing symptoms: <b>{missing_count}</b></div>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<div class='pill'>✂ IQR chars: <b>{int(IQR)}</b></div>", unsafe_allow_html=True)
with colD:
    st.caption("Estimates scale by model, token budget and text length; indicative only.")

left, mid, right = st.columns([2,2,3])
with left:
    batch_n = st.slider("How many to process this run", 1, 20, min(10, max(1, missing_count)) if missing_count else 10)
with mid:
    model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=0)
with right:
    strictness = st.slider("Strictness (higher = fewer, more precise)", 0.55, 0.95, 0.75, 0.01, help="Confidence + evidence threshold; also reduces near-duplicates.")

# Additional accuracy knobs
acc1, acc2, acc3 = st.columns([2,2,3])
with acc1:
    require_evidence = st.checkbox("Require textual evidence", value=True, help="Rejects a pick unless at least N key tokens from the symptom appear in the review text.")
with acc2:
    evidence_hits_required = st.selectbox("Min evidence tokens", options=[1,2], index=1 if strictness>=0.8 else 0)
with acc3:
    process_longest_first = st.checkbox("Process longest reviews first", value=True)

# Speed mode (reduce latency): forces smaller model & shorter batch hint
speed_col = st.container()
with speed_col:
    speed_mode = st.checkbox("⚡ Speed mode (optimize for latency)", value=False, help="Uses a faster model and sorts by shorter reviews first. Accuracy settings still apply.")
    if speed_mode:
        if model_choice != "gpt-4o-mini":
            st.info("Speed mode suggests 'gpt-4o-mini' for fastest responses.")
        process_longest_first = False

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

# ---------------- Session State ----------------

st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("approved_new_delighters", set())
st.session_state.setdefault("approved_new_detractors", set())

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

# Model call (JSON-only) with evidence guardrails

def _llm_pick(review: str, stars, allowed_del: list[str], allowed_det: list[str], min_conf: float, evidence_hits_required: int = 1):
    """Return (allowed_delighters, allowed_detractors, novel_delighters, novel_detractors)."""
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], []

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
        "review": review[:4000],
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del[:120],
        "allowed_detractors": allowed_det[:120]
    }

    dels, dets, novel_dels, novel_dets = [], [], [], []

    if _HAS_OPENAI and api_key:
        try:
            client = OpenAI(api_key=api_key)
            req = {
                "model": model_choice,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user)}
                ],
                "response_format": {"type": "json_object"}
            }
            # GPT-5 rejects non-default temperature; omit for that family
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.2
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
            return (
                _dedupe_keep_top(dels, 10, min_conf),
                _dedupe_keep_top(dets, 10, min_conf),
                _dedupe_keep_top(novel_dels, 5, max(0.70, min_conf)),
                _dedupe_keep_top(novel_dets, 5, max(0.70, min_conf))
            )
        except Exception:
            pass

    # Conservative keyword fallback (no-API)
    text = " " + review.lower() + " "
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

    return pick_from_allowed(allowed_del), pick_from_allowed(allowed_det), [], []

# ---------------- Run Symptomize ----------------

can_run = missing_count > 0 and ((not _HAS_OPENAI) or (api_key is not None))

col_runA, col_runB, col_runC = st.columns([2,2,3])
with col_runA:
    run = st.button(
        f"✨ Symptomize next {min(batch_n, missing_count)} review(s)",
        disabled=not can_run,
        help="Runs on the next batch of reviews missing symptoms."
    )
with col_runB:
    enable_all = st.checkbox("Enable ALL (bulk)")
    run_all = st.button(
        f"⚡ Symptomize ALL {missing_count} missing",
        disabled=(not can_run) or missing_count==0 or (not enable_all),
        help="Processes every review that has empty Symptom 1–20. Uses many API calls."
    )
with col_runC:
    st.caption("Tip: Use batch mode first to review accuracy, then run ALL.")

if (run or run_all) and missing_idx:
    todo = missing_idx if run_all else missing_idx[:batch_n]
    progress = st.progress(0)
    status = st.empty()
    for i, idx in enumerate(todo, start=1):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        dels, dets, novel_dels, novel_dets = _llm_pick(
            review_txt,
            stars,
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            strictness
        )
        st.session_state["symptom_suggestions"].append({
            "row_index": int(idx),
            "stars": float(stars) if pd.notna(stars) else None,
            "review": review_txt,
            "delighters": dels,
            "detractors": dets,
            "novel_delighters": novel_dels,
            "novel_detractors": novel_dets,
            "approve_novel_del": [],
            "approve_novel_det": [],
        })
        progress.progress(i/len(todo))
        status.info(f"Processed {i}/{len(todo)}")
    status.success("Finished generating suggestions! Review below, then Apply to write into the sheet.")
    st.rerun()

# ---------------- Review & Approve ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## 🔍 Review & Approve Suggestions")

    # Bulk actions
    with st.expander("Bulk actions", expanded=True):
        c1,c2,c3,c4 = st.columns([1,1,2,3])
        with c1:
            if st.button("Select all"):
                st.session_state["sug_selected"] = set(range(len(sugs)))
        with c2:
            if st.button("Clear selection"):
                st.session_state["sug_selected"] = set()
        with c3:
            if st.button("Keep only non-empty"):
                st.session_state["sug_selected"] = {i for i,s in enumerate(sugs) if s["delighters"] or s["detractors"]}
        with c4:
            max_apply = st.slider("Max rows to apply now", 1, len(sugs), min(20, len(sugs)))

    for i, s in enumerate(sugs):
        label = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i==0)):
            # Select
            checked = i in st.session_state["sug_selected"]
            if st.checkbox("Select for apply", value=checked, key=f"sel_{i}"):
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            # Full review with highlights of allowed terms
            if s["review"]:
                highlighted = _highlight_terms(s["review"], ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS)
                st.markdown("**Full review:**")
                st.markdown(f"<div class='review-quote'>{highlighted}</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Full review:** (empty)")

            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors (≤10)**")
                st.code("; ".join(s["detractors"]) if s["detractors"] else "–")
            with c2:
                st.write("**Delighters (≤10)**")
                st.code("; ".join(s["delighters"]) if s["delighters"] else "–")

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

    if st.button("✅ Apply selected to DataFrame"):
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

