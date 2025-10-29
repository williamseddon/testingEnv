# ---------- Star Walk v4 — Best-in-class Workbench + Focused-Human Symptomization ----------
# Streamlit 1.38+
# Optional: openai, openpyxl, plotly, scikit-learn

import io, os, re, json, difflib, math, time, random
from typing import List, Tuple, Optional, Dict, Any
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# ---------- Optional libs ----------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

try:
    import plotly.express as px
    _HAS_PX = True
except Exception:
    _HAS_PX = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    _HAS_SK = True
except Exception:
    _HAS_SK = False

# ---------- App Config ----------
APP = {
    "PAGE_TITLE": "Star Walk — Symptomize (v4)",
    "DATA_SHEET_DEFAULT": "Star Walk scrubbed verbatims",
    "HIDDEN_SHEET_APPROVALS": "__StarWalk_Approved",   # legacy approvals (2-column)
    "HIDDEN_SHEET_CONFIG": "__StarWalk_Config",        # JSON config (label cards, calibration)
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),
    "EMB_MODEL": "text-embedding-3-small",
    "CHAT_FAST": "gpt-4o-mini",
    "CHAT_STRICT": "gpt-4.1",
    "ETA_TOKS_PER_CHAR": 0.25,
    "COST_PER_1K_INPUT": 0.00015,
    "COST_PER_1K_OUTPUT": 0.0006,
    # Self-consistency gates
    "SC_LOW": 0.72,
    "SC_HIGH": 0.85,
    "SC_VOTES": 3,     # run N verifies
    "SC_AGREE": 2,     # at least M must be positive and share a quote substring
}

st.set_page_config(layout="wide", page_title=APP["PAGE_TITLE"], page_icon="✨")

# ---------- Theme & CSS ----------
st_html("""
<script>
  (function(){
    try {
      document.documentElement.setAttribute('data-theme','light');
      document.body && document.body.setAttribute('data-theme','light');
      localStorage.setItem('theme','light');
    } catch(e){}
  })();
</script>
""", height=0)

st.markdown("""
<style>
  :root{
    --text:#0f172a; --muted:#475569; --muted2:#64748b;
    --border:#cbd5e1; --border-strong:#90a7c1; --tile:#f8fafc; --card:#ffffff; --app:#f6f8fc;
    --ok:#059669; --warn:#d97706; --bad:#dc2626; --info:#2563eb; --ring:#3b82f6;
  }
  html, body, .stApp{ background:var(--app); color:var(--text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, Noto Sans, "Apple Color Emoji","Segoe UI Emoji"; }
  .block-container{ padding-top:.5rem; padding-bottom:1.2rem; max-width: 1400px; }
  .app-header{ display:flex; align-items:center; justify-content:space-between; padding:10px 14px;
    background:linear-gradient(90deg, var(--card) 0% 60%, transparent 60% 100%);
    border-radius:14px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,.06); }
  .app-title{ font-size: clamp(20px,2.6vw,34px); font-weight:800; margin:0; }
  .app-sub{ margin:2px 0 0 0; color:var(--muted); font-weight:500 }
  .brand{ height:40px; opacity:.9 }
  .stepbar{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:10px 0 16px 0;}
  .step{ padding:6px 10px; border-radius:999px; border:1.5px solid var(--border); background:var(--card); font-weight:700; cursor:pointer; }
  .step.active{ border-color:var(--ring); box-shadow:0 0 0 2px rgba(59,130,246,.2) inset; }
  .pill{ display:inline-flex; gap:6px; align-items:center; padding:6px 10px; border-radius:999px; border:1.5px solid var(--border); background:var(--tile); font-weight:700; }
  .toolbar{ position:sticky; top:62px; z-index:50; background:var(--card); padding:8px 12px; border:1px solid var(--border); border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,.05); margin:8px 0 12px 0; display:flex; gap:10px; flex-wrap:wrap; }
  .card{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:14px; box-shadow:0 4px 12px rgba(0,0,0,.04) }
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
  .chip{padding:6px 10px;border-radius:999px;border:1.5px solid var(--border);background:var(--tile);font-weight:700;font-size:.9rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .review{ white-space: pre-wrap; background:var(--tile); border:1.5px solid var(--border); border-radius:12px; padding:12px }
  mark{ background:#fff2a8; padding:0 .15em; border-radius:3px }
  .queue-row{ display:flex; gap:10px; align-items:center; justify-content:space-between; padding:8px 10px; border:1px solid var(--border); border-radius:10px; background:var(--tile) }
  .queue-row.active{ outline:2px solid var(--ring) }
  .status-dot{ width:8px; height:8px; border-radius:999px; display:inline-block }
  .dot-off{ background:#cbd5e1 }
  .dot-sug{ background:#2563eb }
  .dot-applied{ background:#16a34a }
  .sticky-cta{ position: sticky; bottom: 12px; display:flex; gap:8px; justify-content:flex-end; padding-top:8px; }
  .tip{ color:var(--muted); font-size:12px }
</style>
""", unsafe_allow_html=True)

# ---------- Header ----------
def header():
    st.markdown("""
    <div class="app-header">
      <div>
        <div class="app-title">Star Walk — Symptomize</div>
        <div class="app-sub">Upload, queue, label with AI, verify, approve, and export with confidence.</div>
      </div>
      <img class="brand" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg"/>
    </div>
    """, unsafe_allow_html=True)

def stepbar(current: int, missing: int, model: str, strict: float, eta: int, cost: float):
    steps = ["Upload","Source","Configure","Primer","Workbench","Export"]
    st.markdown(
        "<div class='stepbar'>" +
        "".join([f"<span class='step {'active' if (idx+1)==current else ''}'>{name}</span>"
                 for idx, name in enumerate(steps)]) +
        "</div>",
        unsafe_allow_html=True
    )
    st.markdown(
        f"<div class='toolbar'>"
        f"<span class='pill'>Missing <b>{missing}</b></span>"
        f"<span class='pill'>Model <b>{model}</b></span>"
        f"<span class='pill'>Strict <b>{strict:.2f}</b></span>"
        f"<span class='pill'>ETA <b>~{eta}s</b></span>"
        f"<span class='pill'>Cost est <b>${cost:.3f}</b></span>"
        "</div>", unsafe_allow_html=True
    )

# ---------- Upload ----------
uploaded = st.sidebar.file_uploader("📁 Upload Excel (.xlsx)", type=["xlsx"], accept_multiple_files=False)
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0); st.session_state["uploaded_bytes"] = uploaded.read(); uploaded.seek(0)

if not uploaded:
    header()
    st.info("Upload a workbook in the sidebar to start.")
    st.stop()

def _read_main_sheet(_upl):
    try:
        try:
            return pd.read_excel(_upl, sheet_name=APP["DATA_SHEET_DEFAULT"])
        except ValueError:
            return pd.read_excel(_upl)
    except Exception as e:
        st.error(f"Could not read the Excel file: {e}"); st.stop()

df = _read_main_sheet(uploaded)

# ---------- Identify Symptom Columns ----------
explicit_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1] + 1)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()
if not SYMPTOM_COLS:
    st.error("Couldn't locate Symptom 1–20 columns (K–AD)."); st.stop()

if SYMPTOM_COLS and not all(isinstance(c, str) and str(c).lower().startswith("symptom ") for c in SYMPTOM_COLS):
    st.warning("Symptom columns inferred by position. Verify headers to avoid writing into the wrong columns.")

verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
lengths = verb_series.str.len()
q1 = lengths.quantile(0.25) if not lengths.empty else 0
q3 = lengths.quantile(0.75) if not lengths.empty else 0

is_empty = df[SYMPTOM_COLS].isna() | (df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"","NA","N/A","NONE","NULL","-"}))
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# ---------- Robust Symptoms Source ----------
import io as _io
def _norm(s: str) -> str:
    if s is None: return ""
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool: return "symptom" in _norm(name)

def _col_score(colname: str, want: str) -> int:
    n = _norm(colname)
    if not n: return 0
    synonyms = {
        "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
        "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues"],
    }
    return max((1 for token in synonyms[want] if token in n), default=0)

def _extract_from_df(df_sheet: pd.DataFrame):
    debug = {"strategy": None, "columns": list(df_sheet.columns)}
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
    # Type+Item
    type_col, item_col = None, None
    for c in df_sheet.columns:
        if _norm(c) in {"type","category","class","label"}: type_col = c
        if _norm(c) in {"item","symptom","name","term","entry","value"}: item_col = c
    if type_col and item_col:
        t = df_sheet[type_col].astype(str).str.strip().str.lower()
        i = df_sheet[item_col].astype(str).str.strip()
        dels = i[t.str.contains("delight|pro|positive", na=False)].dropna().tolist()
        dets = i[t.str.contains("detract|con|negative", na=False)].dropna().tolist()
        dels = [x for x in dels if x]; dets = [x for x in dets if x]
        if dels or dets:
            debug.update({"strategy":"type+item","type_col":type_col,"item_col":item_col})
            return dels, dets, debug
    # First two non-empty
    non = []
    for c in df_sheet.columns:
        vals = [str(x).strip() for x in df_sheet[c].dropna().tolist() if str(x).strip()]
        if vals: non.append((c, vals))
        if len(non) >= 2: break
    if non:
        dels = non[0][1]; dets = non[1][1] if len(non) > 1 else []
        return dels, dets, {"strategy":"first-two-nonempty","picked_cols":[c for c,_ in non[:2]]}
    return [], [], {"strategy":"none","columns":list(df_sheet.columns)}

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> Optional[str]:
    names = xls.sheet_names; cands = [n for n in names if _looks_like_symptom_sheet(n)]
    return (min(cands, key=lambda n: len(_norm(n))) if cands else names[0]) if names else None

def load_hidden_approvals(xls: pd.ExcelFile, hidden_sheet: str) -> tuple[list[str], list[str]]:
    dels_extra, dets_extra = [], []
    try:
        if hidden_sheet in xls.sheet_names:
            hdf = pd.read_excel(xls, sheet_name=hidden_sheet)
            if "Approved Delighters" in hdf.columns:
                dels_extra = [str(x).strip() for x in hdf["Approved Delighters"].dropna().tolist() if str(x).strip()]
            if "Approved Detractors" in hdf.columns:
                dets_extra = [str(x).strip() for x in hdf["Approved Detractors"].dropna().tolist() if str(x).strip()]
            if not (dels_extra or dets_extra) and len(hdf.columns) >= 1:
                cols = list(hdf.columns)
                c1 = hdf[cols[0]].dropna().astype(str).str.strip().tolist(); dels_extra = [x for x in c1 if x]
                if len(cols) > 1:
                    c2 = hdf[cols[1]].dropna().astype(str).str.strip().tolist(); dets_extra = [x for x in c2 if x]
    except Exception: pass
    return dels_extra, dets_extra

def load_config_json(raw_bytes: bytes, config_sheet: str) -> dict:
    """Optional JSON from hidden config sheet."""
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw_bytes))
        if config_sheet not in xls.sheet_names: return {}
        df_cfg = pd.read_excel(xls, sheet_name=config_sheet, header=None)
        if df_cfg.empty: return {}
        blob = str(df_cfg.iloc[0,0] or "").strip()
        return json.loads(blob) if blob else {}
    except Exception:
        return {}

raw_bytes = st.session_state.get("uploaded_bytes", b"")
sheet_names = []
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes)); sheet_names = _xls_tmp.sheet_names
except Exception: pass

auto_sheet = autodetect_symptom_sheet(_xls_tmp) if sheet_names else None
st.sidebar.markdown("### 🧾 Symptoms Source")
chosen_sheet = st.sidebar.selectbox("Choose sheet with Delighters/Detractors", options=sheet_names if sheet_names else ["(no sheets)"],
                                    index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0))

# Manual columns (optional)
symp_cols_preview = []
if sheet_names:
    try:
        _df_symp_prev = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name=chosen_sheet)
        symp_cols_preview = list(_df_symp_prev.columns)
    except Exception:
        _df_symp_prev = pd.DataFrame(); symp_cols_preview = []

manual_cols = st.sidebar.checkbox("Manual columns", value=False) if symp_cols_preview else False
picked_del_col = st.sidebar.selectbox("Delighters column", options=["(none)"]+symp_cols_preview, index=0) if manual_cols else None
picked_det_col = st.sidebar.selectbox("Detractors column", options=["(none)"]+symp_cols_preview, index=0) if manual_cols else None
if picked_del_col == "(none)": picked_del_col = None
if picked_det_col == "(none)": picked_det_col = None

def load_symptom_lists_robust(raw_bytes: bytes, user_sheet: Optional[str]=None, user_del_col: Optional[str]=None, user_det_col: Optional[str]=None):
    meta: Dict[str, Any] = {"sheet":None,"strategy":None,"columns":[],"note":""}
    if not raw_bytes: meta["note"] = "No raw bytes"; return [], [], meta
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw_bytes))
    except Exception as e:
        meta["note"] = f"Could not open Excel: {e}"; return [], [], meta
    sheet = user_sheet or autodetect_symptom_sheet(xls)
    if not sheet: meta["note"] = "No sheets found"; return [], [], meta
    meta["sheet"] = sheet
    try:
        s = pd.read_excel(xls, sheet_name=sheet)
    except Exception as e:
        meta["note"] = f"Could not read sheet '{sheet}': {e}"; return [], [], meta
    if user_del_col or user_det_col:
        dels = s.get(user_del_col, pd.Series(dtype=str)) if user_del_col in s.columns else pd.Series(dtype=str)
        dets = s.get(user_det_col, pd.Series(dtype=str)) if user_det_col in s.columns else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"manual-columns","columns":list(s.columns)})
    else:
        dels, dets, info = _extract_from_df(s); meta.update(info)
    try:
        delX, detX = load_hidden_approvals(xls, APP["HIDDEN_SHEET_APPROVALS"])
        if delX: dels = list(dict.fromkeys(dels + delX))
        if detX: dets = list(dict.fromkeys(dets + detX))
    except Exception: pass
    return dels, dets, meta

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes, user_sheet=chosen_sheet if sheet_names else None, user_del_col=picked_del_col, user_det_col=picked_det_col
)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DELIGHTERS_SET, ALLOWED_DETRACTORS_SET = set(ALLOWED_DELIGHTERS), set(ALLOWED_DETRACTORS)

if ALLOWED_DELIGHTERS or ALLOWED_DETRACTORS:
    st.sidebar.success(f"Loaded {len(ALLOWED_DELIGHTERS)} delighters, {len(ALLOWED_DETRACTORS)} detractors (sheet: '{SYM_META.get('sheet','?')}', mode: {SYM_META.get('strategy','?')}).")
else:
    st.sidebar.warning("No clear Delighters/Detractors lists detected. Will use conservative fallback.")

# ---------- Controls ----------
st.sidebar.markdown("### ⚙️ Configure")
batch_n = st.sidebar.slider("Batch size", 1, 50, min(20, max(1, missing_count)) if missing_count else 10)
model_choice = st.sidebar.selectbox("Model", [APP["CHAT_FAST"], "gpt-4o", APP["CHAT_STRICT"], "gpt-5"], index=0)
strictness = st.sidebar.slider("Strictness (higher=fewer, more precise)", 0.55, 0.95, 0.80, 0.01)
require_evidence = st.sidebar.checkbox("Require textual evidence", value=True)
evidence_hits_required = st.sidebar.selectbox("Min evidence tokens", options=[1, 2], index=1 if strictness >= 0.8 else 0)
order = st.sidebar.selectbox("Processing order", ["Original","Shortest first","Longest first"], index=2)
use_semantic = st.sidebar.checkbox("Semantic recall boost (embeddings)", value=True)
semantic_threshold = st.sidebar.slider("Min semantic similarity", 0.50, 0.90, 0.72, 0.01)
primer_enabled = st.sidebar.checkbox("Build Global Primer (recommended)", value=True)
verify_pass = st.sidebar.checkbox("Verify Pass (quotes required)", value=True)
verify_topk = st.sidebar.slider("Verify up to K candidates per polarity", 2, 12, 8, 1)
skeptic_pass = st.sidebar.checkbox("Skeptic pass (look for contradictions)", value=True)
self_consistency = st.sidebar.checkbox("Self-consistency (2/3) on borderline", value=True)
speed_mode = st.sidebar.checkbox("⚡ Speed mode", value=False)
if speed_mode and model_choice != APP["CHAT_FAST"]:
    st.sidebar.info(f"Speed mode suggests '{APP['CHAT_FAST']}'.")
    model_choice = APP["CHAT_FAST"]; order = "Shortest first" if order=="Longest first" else order

# ---------- ETA & Cost ----------
sel_lengths = [len(verb_series.iloc[i]) for i in (missing_idx[:min(batch_n, missing_count)] if missing_count else [])]
chars_est = int(pd.Series(sel_lengths).median()) if sel_lengths else int(max(200, (q1 + q3) / 2))
tok_est = max(1, int(chars_est * APP["ETA_TOKS_PER_CHAR"]))
MODEL_TPS = {"gpt-4o-mini": 55, "gpt-4o": 25, "gpt-4.1": 16, "gpt-5": 12}
MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
rt = (min(batch_n, missing_count) * (MODEL_LAT.get(model_choice, 1.0) + tok_est / max(8, MODEL_TPS.get(model_choice, 12))))
rt *= (1.0 + 0.15 * (evidence_hits_required - 1))
eta_secs = int(round(rt))
rough_cost = min(batch_n, missing_count) * (tok_est/1000.0 * (APP["COST_PER_1K_INPUT"] + APP["COST_PER_1K_OUTPUT"]))

# ---------- API Key ----------
api_key = (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
_has_key = bool(api_key)
if missing_count and not _HAS_OPENAI: st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
if missing_count and _HAS_OPENAI and not _has_key: st.warning("Set a non-empty OPENAI_API_KEY (env or secrets).")

# ---------- Session State ----------
def _ss_default(key, val):
    if key not in st.session_state: st.session_state[key] = val

_ss_default("ui_step", 5 if uploaded else 1)
_ss_default("symptom_suggestions", [])
_ss_default("sug_selected", set())
_ss_default("approved_new_delighters", set()); _ss_default("approved_new_detractors", set())
_ss_default("PRIMER", None)
_ss_default("history", []); _ss_default("pending_changes", [])
_ss_default("sug_by_row", {})
_ss_default("applied_rows", set())
_ss_default("cursor_pos", 0)
_ss_default("queue_filter_q", ""); _ss_default("queue_filter_stars", [])
_ss_default("dry_run", False)
_ss_default("novel_counts", Counter())  # cross-row recurrence
_ss_default("label_cards", {})          # from config/primer
_ss_default("calibration", {"per_label": {}, "exclusive_pairs": []})

# ---------- Helpers (names, canonicalization) ----------
def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

ALIAS_CANON = {
    "initial difficulty":"Learning curve",
    "hard to learn":"Learning curve",
    "setup difficulty":"Learning curve",
    "noisy startup":"Startup noise",
    "too loud":"Loud",
}
# Seed anti-cues & exclusives (expanded later by primer/config)
SEED_ANTI = {
    "Loud": ["quiet","silent","low noise","not loud","noise low"],
    "Quiet": ["loud","noisy","buzz","whine","rattle","too loud"],
    "Heavy": ["lightweight","light weight","not heavy"],
    "Weak suction": ["strong suction","powerful suction","great suction"],
    "Strong suction": ["weak suction","poor suction","not strong suction"],
    "Short battery life": ["long battery","great battery","lasts long"],
    "Long battery life": ["short battery","poor battery","doesn't last"],
}

SEED_EXCLUSIVES = [
    ("Loud","Quiet"),
    ("Weak suction","Strong suction"),
    ("Short battery life","Long battery life"),
]

def canonicalize(name: str) -> str:
    nn = (name or "").strip(); base = _normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k) == base: return v
    return nn

def _highlight_terms(text: str, allowed: List[str]) -> str:
    out = text
    for t in sorted(set(allowed), key=len, reverse=True):
        if not t.strip(): continue
        try: out = re.sub(rf"(\b{re.escape(t)}\b)", r"<mark>\1</mark>", out, flags=re.IGNORECASE)
        except re.error: pass
    return out

# ---------- OpenAI helpers ----------
def _openai_json(model: str, sys_prompt: str, user_obj: dict, api_key: str, temp: float=0.2) -> dict:
    client = OpenAI(api_key=api_key)
    use_responses = bool(re.match(r"^(gpt-4\.1|gpt-5)", model))
    if use_responses:
        out = client.responses.create(
            model=model,
            response_format={"type":"json_object"},
            input=[{"role":"system","content":sys_prompt},{"role":"user","content":json.dumps(user_obj)}],
        )
        content = out.output_text or "{}"
    else:
        req = {"model":model,"messages":[{"role":"system","content":sys_prompt},{"role":"user","content":json.dumps(user_obj)}],
               "response_format":{"type":"json_object"},"temperature": float(temp)}
        out = client.chat.completions.create(**req); content = out.choices[0].message.content or "{}"
    return json.loads(content)

def _openai_json_safe(model: str, sys_prompt: str, user_obj: dict, api_key: str, temp: float=0.2, retries: int=3) -> dict:
    last = None
    for _ in range(retries):
        try:
            data = _openai_json(model, sys_prompt, user_obj, api_key, temp=temp)
            if not isinstance(data, dict): raise ValueError("Non-dict JSON")
            return data
        except Exception as e:
            last = e
            time.sleep(0.4 + 0.2*_)
    raise RuntimeError(f"LLM JSON call failed after {retries} attempts: {last}")

# ---------- Embeddings ----------
EMB_MODEL = APP["EMB_MODEL"]
@st.cache_resource(show_spinner=False)
def _build_label_index(labels: List[str], _api_key: str):
    if not (_HAS_OPENAI and _api_key and labels): return None
    texts = list(dict.fromkeys([canonicalize(x) for x in labels if x])); 
    if not texts: return None
    client = OpenAI(api_key=_api_key)
    vecs = client.embeddings.create(model=EMB_MODEL, input=texts).data
    M = np.array([v.embedding for v in vecs], dtype="float32")
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
    return (texts, M)

def _ngram_candidates(text: str, max_ngrams: int = 256) -> List[str]:
    ws = re.findall(r"[a-z0-9]{3,}", (text or "").lower()); ngrams, seen = [], set()
    for n in (1,2,3,4,5):
        for i in range(len(ws)-n+1):
            s = " ".join(ws[i:i+n])
            if len(s)>=4 and s not in seen:
                ngrams.append(s); seen.add(s)
                if len(ngrams) >= max_ngrams: break
        if len(ngrams) >= max_ngrams: break
    return ngrams

def _semantic_support(review: str, label_index, _api_key: str, topk: int=20, min_sim: float=0.68) -> Dict[str, float]:
    if (not label_index) or (not review): return {}
    labels, L = label_index; cands = _ngram_candidates(review)
    if not cands: return {}
    client = OpenAI(api_key=_api_key)
    data = client.embeddings.create(model=EMB_MODEL, input=cands).data
    X = np.array([d.embedding for d in data], dtype="float32"); X /= (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
    S = X @ L.T; best_idx = S.argmax(axis=1); best_sim = S[np.arange(len(cands)), best_idx]
    buckets = {}
    for j, sim in zip(best_idx, best_sim):
        if sim >= min_sim:
            lab = labels[int(j)]
            if sim > buckets.get(lab, 0.0): buckets[lab] = float(sim)
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1])[:topk])

def _embed_texts(texts: List[str], api_key: str) -> np.ndarray:
    if not texts: return np.zeros((0,1536), dtype="float32")
    client = OpenAI(api_key=api_key); B, vecs = 128, []
    for i in range(0, len(texts), B):
        chunk = texts[i:i+B]; out = client.embeddings.create(model=EMB_MODEL, input=chunk).data
        vecs.extend([d.embedding for d in out])
    M = np.array(vecs, dtype="float32"); M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
    return M

def _topk_idxs(sim_row: np.ndarray, k: int) -> np.ndarray:
    k = int(min(k, sim_row.shape[-1])); return np.argpartition(-sim_row, k-1)[:k]

# ---------- Primer (extended) ----------
@st.cache_resource(show_spinner=True)
def build_global_primer(
    all_reviews: List[str],
    stars: List[Optional[float]],
    allowed_del: List[str],
    allowed_det: List[str],
    api_key: str,
):
    texts = [str(t or "").strip() for t in all_reviews]
    X = _embed_texts(texts, api_key)
    n = len(texts); n_clusters = max(5, min(25, n//300 or 8))
    if _HAS_SK and n >= max(20, n_clusters * 2):
        km = KMeans(n_clusters=n_clusters, n_init="auto", random_state=42); labels = km.fit_predict(X)
    else: labels = np.zeros(n, dtype=int); n_clusters = 1
    clusters=[]
    if _HAS_SK:
        tf = TfidfVectorizer(min_df=3,max_features=8000,ngram_range=(1,2),token_pattern=r"[a-zA-Z0-9]{3,}")
        TF = tf.fit_transform(texts); vocab = np.array(tf.get_feature_names_out())
        for c in range(n_clusters):
            idxs = np.where(labels==c)[0]
            if not len(idxs): clusters.append({"id": c, "top_terms": [], "mean_stars": None, "size": 0}); continue
            v = np.asarray(TF[idxs].mean(axis=0)).ravel(); top = v.argsort()[-12:][::-1]
            mean_star = float(np.nanmean([stars[i] for i in idxs if stars[i] is not None])) if idxs.size else None
            clusters.append({"id":int(c),"top_terms":vocab[top].tolist(),"mean_stars":mean_star,"size":int(idxs.size)})
    else:
        clusters = [{"id":0,"top_terms":[],"mean_stars":float(np.nanmean([s for s in stars if s is not None])) if stars else None,"size":n}]

    # Guides + lexicon
    all_allowed = [canonicalize(x) for x in (allowed_del + allowed_det) if x]
    L = _embed_texts(all_allowed, api_key); sim = X @ L.T

    label_guides: Dict[str, Dict[str, Any]] = {}
    lexicon: Dict[str, Dict[str, List[str]]] = {}
    client = OpenAI(api_key=api_key)
    for j, label in enumerate(all_allowed):
        support_idx = _topk_idxs(sim[:, j], 20); quotes = [texts[i][:300] for i in support_idx if len(texts[i]) >= 20][:6]
        msg = [{"role":"system","content":"You write compact taxonomy notes for product feedback."},
               {"role":"user","content": json.dumps({"label":label,"examples":quotes})}]
        try:
            out = client.chat.completions.create(model=APP["CHAT_FAST"], messages=msg, temperature=0.2, response_format={"type":"json_object"})
            data = json.loads(out.choices[0].message.content or "{}")
        except Exception: data = {}
        definition = data.get("definition") or f"{label}: user-reported theme."
        syns = [canonicalize(s) for s in (data.get("synonyms") or []) if s and len(s) < 40][:8]
        toks = [t for t in (data.get("evidence_tokens") or []) if isinstance(t,str) and 3<=len(t)<=24][:10]
        # expand with seeds
        anti = SEED_ANTI.get(label, [])
        label_guides[label] = {"definition": definition, "examples": data.get("examples") or quotes[:3]}
        lexicon[label] = {"synonyms": syns, "evidence_tokens": toks, "negative_cues": anti, "require_any": [], "forbid_any": []}

    # exclusives seed
    exclusive_pairs = list(SEED_EXCLUSIVES)

    # Cluster -> label priors
    cluster_priors: Dict[int, List[str]] = {}
    for c in range(n_clusters):
        idxs = np.where(labels==c)[0]
        if not len(idxs): cluster_priors[c] = []; continue
        s = sim[idxs].mean(axis=0); top = np.argsort(-s)[:15]
        cluster_priors[int(c)] = [all_allowed[int(j)] for j in top]

    # Brief
    chunks, CH, buf = [], 4000, []
    for t in texts:
        if not t: continue
        buf.append(t[:600])
        if sum(len(x) for x in buf) >= CH: chunks.append("\n\n".join(buf)); buf=[]
    if buf: chunks.append("\n\n".join(buf))
    partials=[]
    for ch in chunks[:12]:
        try:
            out = client.chat.completions.create(model=APP["CHAT_FAST"],
                messages=[{"role":"system","content":"Summarize recurring product themes succinctly."},{"role":"user","content":ch}],
                temperature=0.2)
            partials.append(out.choices[0].message.content or "")
        except Exception: pass
    brief_text = "\n".join(partials[:8])[:4000]
    try:
        out = client.chat.completions.create(model=APP["CHAT_FAST"],
            messages=[{"role":"system","content":"Write a 6–10 bullet product brief: key use cases, delights, pain points, vocabulary, edge cases."},
                      {"role":"user","content":brief_text}], temperature=0.2)
        brief = out.choices[0].message.content or ""
    except Exception:
        brief = "High-level product brief unavailable."

    return {"brief":brief,"clusters":clusters,"lexicon":lexicon,"label_guides":label_guides,
            "cluster_priors":cluster_priors,"review_cluster":list(map(int, labels.tolist())),"allowed_order":all_allowed,
            "exclusive_pairs": exclusive_pairs}

# ---------- Build Index / Primer / Config ----------
LABEL_INDEX = _build_label_index(ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS, api_key) if (_HAS_OPENAI and _has_key) else None
PRIMER = st.session_state.get("PRIMER")
CONFIG = load_config_json(raw_bytes, APP["HIDDEN_SHEET_CONFIG"])

if primer_enabled and _HAS_OPENAI and _has_key and PRIMER is None:
    with st.status("Building Global Primer…", expanded=False) as s:
        PRIMER = build_global_primer(verb_series.tolist(), df.get("Star Rating", pd.Series(dtype=float)).tolist(),
                                     ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, api_key)
        st.session_state["PRIMER"] = PRIMER; s.update(label="Global Primer ready ✔")

# ---------- Label Cards & Calibration ----------
def build_label_cards(allowed: List[str]) -> Dict[str, dict]:
    cards = {}
    lex = (PRIMER or {}).get("lexicon", {})
    guides = (PRIMER or {}).get("label_guides", {})
    for lab in [canonicalize(x) for x in allowed]:
        lx = lex.get(lab, {})
        gd = guides.get(lab, {})
        cards[lab] = {
            "label": lab,
            "definition": gd.get("definition",""),
            "positive_cues": list(dict.fromkeys(([lab] + lx.get("synonyms",[]) + lx.get("evidence_tokens",[])))),
            "negative_cues": list(dict.fromkeys(SEED_ANTI.get(lab, []) + lx.get("negative_cues",[]))),
            "require_any": lx.get("require_any", []),
            "forbid_any": lx.get("forbid_any", []),
            "exclusive_with": [],
        }
    # exclusives from primer seed + config
    exclusives = set(tuple(sorted(pair)) for pair in (PRIMER or {}).get("exclusive_pairs", []))
    exclusives |= set(tuple(sorted(pair)) for pair in CONFIG.get("exclusive_pairs", []))
    for a,b in exclusives:
        if a in cards and b in cards:
            cards[a]["exclusive_with"].append(b)
            cards[b]["exclusive_with"].append(a)
    # user config label cards (override/merge)
    for lab, card in CONFIG.get("label_cards", {}).items():
        L = canonicalize(lab)
        if L not in cards:
            cards[L] = {"label": L, "definition":"", "positive_cues":[], "negative_cues":[], "require_any":[], "forbid_any":[], "exclusive_with":[]}
        for k,v in card.items():
            if isinstance(v, list):
                cards[L][k] = list(dict.fromkeys(cards[L].get(k, []) + v))
            else:
                cards[L][k] = v
    return cards

ALL_ALLOWED = [canonicalize(x) for x in (ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS)]
LABEL_CARDS = build_label_cards(ALL_ALLOWED)
st.session_state["label_cards"] = LABEL_CARDS
CALIB = CONFIG.get("calibration", {"per_label": {}, "exclusive_pairs": []})
st.session_state["calibration"] = CALIB

def label_threshold(label: str, base: float) -> float:
    delta = 0.0
    try:
        delta = float(CALIB.get("per_label", {}).get(label, {}).get("delta", 0.0))
    except Exception:
        delta = 0.0
    return max(0.0, min(0.97, base + delta))

# ---------- Evidence engine 2.1 (phrase, negation, intensity, comparatives, context) ----------
_NEGATORS = re.compile(r"\b(no|not|never|hardly|barely|scarcely|doesn['’]t|don['’]t|isn['’]t|wasn['’]t|cannot|can['’]t|won['’]t|without)\b", re.I)
_INTENS = re.compile(r"\b(too|very|extremely|really|super|so|quite|highly|pretty)\b", re.I)
_COMP_OPPOSITE = re.compile(r"\b(less|fewer|lower|quieter|smaller|lighter)\b.+?\bthan\b", re.I)
_COMP_SAME = re.compile(r"\b(more|higher|louder|bigger|stronger|longer)\b.+?\bthan\b", re.I)

def _find_spans(pattern: str, text: str) -> list[tuple[int,int]]:
    return [(m.start(), m.end()) for m in re.finditer(pattern, text, flags=re.I)]

def _span_has_negation(text: str, span: tuple[int,int], window: int=24) -> bool:
    a,b=span; left, right = max(0,a-window), min(len(text),b+window)
    return bool(_NEGATORS.search(text[left:right]))

def _ctx_ok(text: str, card: dict, span: Optional[tuple[int,int]]=None, window: int=40) -> bool:
    req = [w for w in (card.get("require_any") or []) if w]
    forb = [w for w in (card.get("forbid_any") or []) if w]
    if not req and not forb: return True
    scope = text if span is None else text[max(0,span[0]-window): min(len(text), span[1]+window)]
    if req and not any(re.search(rf"\b{re.escape(w)}\b", scope, re.I) for w in req): return False
    if forb and any(re.search(rf"\b{re.escape(w)}\b", scope, re.I) for w in forb): return False
    return True

def _evidence_report(symptom: str, text: str, card: Optional[dict]=None) -> dict:
    """
    Returns {
      'hits': [{'token':..., 'span':(a,b), 'negated':bool, 'type':'phrase'|'token', 'intense':bool, 'comparative':'opp'|'same'|None}],
      'score_tokens': int, 'score_phrases': int, 'total_hits': int, 'intensity': int, 'comparative_penalty': int, 'quote': str
    }
    """
    if not symptom or not text:
        return {"hits": [], "score_tokens": 0, "score_phrases": 0, "total_hits": 0, "intensity": 0, "comparative_penalty":0, "quote": ""}

    card = card or {}
    text_lc = text
    toks = [t for t in _normalize_name(symptom).split() if len(t)>=3]
    phrases=set()
    if len(toks)>=2:
        for n in range(min(5,len(toks)),1,-1):
            for i in range(len(toks)-n+1): phrases.add(" ".join(toks[i:i+n]))
    # add positive cues as phrases
    for cue in (card.get("positive_cues") or []):
        cue_n = _normalize_name(cue)
        if " " in cue_n and len(cue_n)>=4: phrases.add(cue_n)

    hits=[]; score_tokens=0; score_phrases=0; best_span=None; intense_ct=0
    # phrases first
    for p in sorted(phrases, key=len, reverse=True):
        patt = rf"\b{re.escape(p)}\b"
        for span in _find_spans(patt, text_lc):
            if not _ctx_ok(text_lc, card, span): continue
            neg=_span_has_negation(text_lc, span)
            intense = bool(_INTENS.search(text_lc[max(0,span[0]-14):min(len(text_lc),span[1]+14)]))
            comp = 'opp' if _COMP_OPPOSITE.search(text_lc[max(0,span[0]-40):min(len(text_lc),span[1]+40)]) else ('same' if _COMP_SAME.search(text_lc[max(0,span[0]-40):min(len(text_lc),span[1]+40)]) else None)
            hits.append({"token":p,"span":span,"negated":neg,"type":"phrase","intense":intense,"comparative":comp})
            if not neg:
                score_phrases += 2
                if intense: intense_ct += 1
                best_span = best_span or span

    # tokens
    for t in toks:
        patt = rf"\b{re.escape(t)}\b"
        for span in _find_spans(patt, text_lc):
            if not _ctx_ok(text_lc, card, span): continue
            neg=_span_has_negation(text_lc, span)
            intense = bool(_INTENS.search(text_lc[max(0,span[0]-14):min(len(text_lc),span[1]+14)]))
            comp = 'opp' if _COMP_OPPOSITE.search(text_lc[max(0,span[0]-40):min(len(text_lc),span[1]+40)]) else ('same' if _COMP_SAME.search(text_lc[max(0,span[0]-40):min(len(text_lc),span[1]+40)]) else None)
            hits.append({"token":t,"span":span,"negated":neg,"type":"token","intense":intense,"comparative":comp})
            if not neg:
                score_tokens += 1
                if intense: intense_ct += 1
                best_span = best_span or span

    # negative cues (contradictions)
    neg_cues = (card.get("negative_cues") or [])
    comp_pen = 0
    for cue in neg_cues:
        patt = rf"\b{re.escape(cue)}\b"
        for span in _find_spans(patt, text_lc):
            if _ctx_ok(text_lc, card, span):
                comp_pen += 2  # treat as opposite evidence present nearby

    # quote
    quote=""
    if best_span:
        a,b=best_span; left, right = max(0,a-60), min(len(text_lc), b+60); quote = text_lc[left:right].strip()

    return {
        "hits": hits,
        "score_tokens": score_tokens,
        "score_phrases": score_phrases,
        "total_hits": sum(1 for h in hits if not h["negated"]),
        "intensity": intense_ct,
        "comparative_penalty": comp_pen,
        "quote": quote[:160],
    }

def fuse_confidence(llm_conf: float, sem_sim: float, ev: dict, stars: Optional[float], polarity: str) -> float:
    llm = max(0.0, min(1.0, float(llm_conf or 0))); sem = max(0.0, min(1.0, float(sem_sim or 0)))
    evp = 0.0
    if ev:
        evp = 0.2*(ev.get("score_tokens",0)) + 0.35*(ev.get("score_phrases",0)) + 0.15*(ev.get("intensity",0))
        evp -= 0.25*(ev.get("comparative_penalty",0))
        evp = max(0.0, min(1.0, evp))
    prior=0.0
    if stars is not None and not pd.isna(stars):
        if polarity=="delighter": prior = 0.07 if stars>=4.0 else (-0.07 if stars<=2.0 else 0.0)
        else: prior = 0.07 if stars<=2.0 else (-0.07 if stars>=4.0 else 0.0)
    base = 0.45*llm + 0.30*sem + 0.25*evp
    return max(0.0, min(1.0, base + prior))

# ---------- Verify & Skeptic ----------
def _verify_label(review: str, card: dict, api_key: str, model: str, temp: float=0.0) -> dict:
    """Return {'present':bool,'confidence':float,'quotes':[...],'reason':str}"""
    if not (_HAS_OPENAI and api_key): 
        return {"present": False, "confidence": 0.0, "quotes": [], "reason": ""}
    sys = "Decide if the claim is supported ONLY if you can paste verbatim quotes from the review. Respond JSON."
    claim = f"The product exhibits: {card.get('label','').strip()}."
    user = {
        "review": str(review or "")[:4000],
        "claim": claim,
        "definition": card.get("definition",""),
        "positive_cues": card.get("positive_cues",[])[:16],
        "negative_cues": card.get("negative_cues",[])[:16],
        "format": {"present":"bool","confidence":"0..1","quotes":"list[str]","reason":"<=140 chars"}
    }
    data = _openai_json_safe(model, sys, user, api_key, temp=temp)
    # sanitize
    return {
        "present": bool(data.get("present", False)),
        "confidence": float(data.get("confidence", 0.0)),
        "quotes": [q for q in (data.get("quotes",[]) or []) if isinstance(q,str) ][:4],
        "reason": str(data.get("reason",""))[:160]
    }

def _skeptic_label(review: str, card: dict, api_key: str, model: str) -> dict:
    """Try to find contradictions/negations. Return {'contradicts':bool,'quote':str}"""
    # Cheap deterministic pass using negative cues; optional LLM when available
    negs = card.get("negative_cues", [])
    for n in negs:
        if re.search(rf"\b{re.escape(n)}\b", review, flags=re.I):
            return {"contradicts": True, "quote": n}
    if not (_HAS_OPENAI and api_key):
        return {"contradicts": False, "quote": ""}
    sys = "Is there text that contradicts the claim? Quote it if yes. JSON only."
    user = {
        "review": str(review)[:4000],
        "claim": f"The product exhibits: {card.get('label','')}.",
        "format": {"contradicts":"bool","quote":"<=120 chars"}
    }
    data = _openai_json_safe(model, sys, user, api_key, temp=0.0)
    return {"contradicts": bool(data.get("contradicts", False)), "quote": str(data.get("quote",""))[:140]}

# ---------- Propose + Full Scoring Pipeline ----------
def _propose_labels(review: str, stars, allowed_del: List[str], allowed_det: List[str], min_conf: float,
                    evidence_hits_required: int, row_index: Optional[int]):
    """Return raw candidates from LLM or conservative fallback."""
    # Semantic candidates
    sem_supp: Dict[str, float] = {}
    if _HAS_OPENAI and _has_key and LABEL_INDEX and use_semantic:
        try:
            sem_supp = _semantic_support(review, LABEL_INDEX, api_key, topk=20, min_sim=semantic_threshold)
        except Exception:
            sem_supp = {}

    # Primer payload for LLM
    primer_payload = None
    if PRIMER:
        cluster_id = None
        try:
            if row_index is not None: cluster_id = int(PRIMER["review_cluster"][row_index])
        except Exception: cluster_id = None
        candidate_labels = PRIMER["cluster_priors"].get(int(cluster_id), [])[:20] if cluster_id is not None else (PRIMER.get("allowed_order") or [])[:20]
        guides = {lab: PRIMER["label_guides"].get(lab, {}) for lab in candidate_labels}
        primer_payload = {"product_brief": (PRIMER.get("brief") or "")[:1200], "label_guides": guides}

    # LLM propose
    dels_raw, dets_raw = [], []
    if _HAS_OPENAI and _has_key:
        sys_prompt = """
You label a user review with symptoms from provided lists.
Return JSON: {"delighters":[{"name":"...","confidence":0.0}], "detractors":[{"name":"...","confidence":0.0}]}
Rules: at most 10 per polarity; only from provided lists; omit if uncertain; prefer items with quotable evidence.
"""
        user = {
            "review": review[:4000],
            "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
            "allowed_delighters": allowed_del[:120],
            "allowed_detractors": allowed_det[:120],
            "semantic_candidates": sorted(list(sem_supp.keys())),
            "primer": primer_payload,
        }
        try:
            data = _openai_json_safe(model_choice, sys_prompt, user, api_key, temp=0.1)
            dels_raw = data.get("delighters", []) or []
            dets_raw = data.get("detractors", []) or []
            # normalize if strings
            for k in (dels_raw, dets_raw):
                for i, x in enumerate(k):
                    if isinstance(x, str): k[i] = {"name": x, "confidence": 0.6}
        except Exception:
            dels_raw, dets_raw = [], []

    # Conservative fallback
    if not (dels_raw or dets_raw):
        STOP_TOKS = {"app","out","bad","hot","cold","set"}
        def _fallback_pick(allowed: List[str]) -> List[dict]:
            scored=[]; text=" "+review.lower()+" "
            for a in allowed:
                a_can=canonicalize(a); toks=[t for t in _normalize_name(a_can).split() if len(t)>=4 and t not in STOP_TOKS]
                if PRIMER:
                    meta=(PRIMER.get("lexicon",{}).get(a_can, {}) or {})
                    toks+=[t for t in (meta.get("evidence_tokens") or []) if len(t)>=4]
                    toks+=[t for t in (meta.get("synonyms") or []) if len(t)>=4]
                if not toks: continue
                hits=[t for t in toks if re.search(rf"\b{re.escape(t)}\b", text)]
                score=len(hits)/max(1,len(toks))
                if (not require_evidence) or (len(hits)>=evidence_hits_required):
                    if score>=min_conf-0.05: scored.append({"name":a_can,"confidence":0.60+0.4*score})
            return scored
        dels_raw = _fallback_pick(allowed_del); dets_raw = _fallback_pick(allowed_det)

    return dels_raw, dets_raw, sem_supp

def _resolve_conflicts(cands: List[dict], cards: Dict[str,dict]) -> Tuple[List[dict], List[tuple]]:
    """Deduplicate near-duplicates and enforce exclusives across all labels."""
    out = []
    conflicts = []
    # Deduplicate by name similarity
    for s in cands:
        n_norm = _normalize_name(s["name"])
        if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(t["name"])).ratio() > 0.88 for t in out):
            out.append(s)
    # Exclusives
    present = {x["name"]: x for x in out}
    for a in list(present.keys()):
        for b in cards.get(a, {}).get("exclusive_with", []):
            if b in present and a in present:
                conflicts.append((a,b))
                # keep higher fused_conf
                if present[a]["fused_conf"] >= present[b]["fused_conf"]:
                    del present[b]
                else:
                    del present[a]
    return list(present.values()), conflicts

def _self_consistency(review: str, card: dict, api_key: str, model: str, base_quotes: List[str]) -> dict:
    """2/3 agreement on presence + overlapping quotes."""
    votes = []
    for k in range(APP["SC_VOTES"]-1):  # we already have one verify; add extras
        res = _verify_label(review, card, api_key, model, temp=0.0)
        votes.append(res)
    # Count positives with overlapping quotes
    def overlaps(a_list, b_list):
        for a in a_list:
            for b in b_list:
                if (a and b) and (a in b or b in a): return True
        return False
    positives = 1  # base verify assumed positive to trigger this
    for r in votes:
        if r.get("present") and overlaps(r.get("quotes",[]), base_quotes):
            positives += 1
    return {"agree": positives >= APP["SC_AGREE"], "extra_votes": votes}

def _score_pipeline(
    review: str,
    stars,
    allowed_del: List[str],
    allowed_det: List[str],
    min_conf: float,
    evidence_hits_required: int,
    row_index: Optional[int],
) -> Tuple[List[str], List[str], List[str], List[str], dict]:
    """Focused-human pipeline. Returns lists and extras for UI."""
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], [], {}

    # --- Propose
    dels_raw, dets_raw, sem_supp = _propose_labels(review, stars, allowed_del, allowed_det, min_conf, evidence_hits_required, row_index)

    text = review or ""
    stars_f = float(stars) if (stars is not None and (not pd.isna(stars))) else None

    def _score_side(items, allowed_set, polarity: str):
        scored = []
        for it in items:
            nm = canonicalize(it.get("name","").strip()); 
            if not nm: continue
            card = st.session_state["label_cards"].get(nm, {"label": nm, "positive_cues":[nm], "negative_cues":[], "require_any":[], "forbid_any":[]})
            sem_c = float(sem_supp.get(nm, 0.0))
            ev = _evidence_report(nm, text, card)
            # evidence gate / semantic override
            passes_evidence = True
            if require_evidence:
                passes_evidence = (ev.get("total_hits",0) >= int(evidence_hits_required)) or (ev.get("score_phrases",0) >= 2)
            sem_override = sem_c >= max(semantic_threshold, 0.72)
            if not (passes_evidence or sem_override): 
                continue
            # Verify with quotes (required if enabled)
            llm_c = float(it.get("confidence", 0.0) or 0)
            vres = {"present": True, "confidence": llm_c, "quotes": [], "reason": ""}
            if verify_pass and _HAS_OPENAI and _has_key:
                vres = _verify_label(text, card, api_key, model_choice, temp=0.0)
                if not vres["present"] or not vres.get("quotes"):
                    # reject if no quote or false
                    continue
                llm_c = max(llm_c, vres.get("confidence", llm_c))
                # normalize quote to exact substring check
                quotes_ok = [q for q in vres.get("quotes", []) if q and (q in text)]
                if not quotes_ok: 
                    continue
                ev["quote"] = quotes_ok[0]

            # Skeptic pass (reduce confidence if contradiction found)
            sk = {"contradicts": False, "quote": ""}
            if skeptic_pass:
                sk = _skeptic_label(text, card, api_key, model_choice) if (_HAS_OPENAI and _has_key) else sk
                if sk["contradicts"]:
                    llm_c = min(llm_c, 0.35)  # heavy penalty

            fused = fuse_confidence(llm_c, sem_c, ev, stars_f, polarity)

            # Self-consistency for borderline candidates
            sc = {"agree": True, "extra_votes": []}
            if self_consistency and (APP["SC_LOW"] <= fused <= APP["SC_HIGH"]) and verify_pass and (_HAS_OPENAI and _has_key):
                base_quotes = vres.get("quotes", [])
                sc = _self_consistency(text, card, api_key, model_choice, base_quotes)
                if not sc["agree"]:
                    fused = min(fused, 0.68)  # push below common thresholds

            # Final min threshold per label
            thr = label_threshold(nm, min_conf)
            if fused < thr: 
                continue

            scored.append({
                "name": nm, "fused_conf": fused, "llm_conf": llm_c, "sem_sim": sem_c,
                "evidence": ev, "quote": ev.get("quote",""),
                "novel": nm not in allowed_set,
                "polarity": polarity, "verify": vres, "skeptic": sk, "selfcons": sc
            })
        return scored

    dels_scored = _score_side(dels_raw, set(allowed_del), "delighter")
    dets_scored = _score_side(dets_raw, set(allowed_det), "detractor")

    # Join to resolve cross-exclusives
    all_scored = dels_scored + dets_scored
    all_scored_sorted = sorted(all_scored, key=lambda x: -x["fused_conf"])
    resolved, conflicts = _resolve_conflicts(all_scored_sorted, st.session_state["label_cards"])

    # Split back by polarity
    dels_kept = [x for x in resolved if x["polarity"]=="delighter"][:10]
    dets_kept = [x for x in resolved if x["polarity"]=="detractor"][:10]

    # Novelty gating (recurrent)
    novel_dels = []; novel_dets=[]
    for k in dels_kept:
        if k["novel"] and (k["sem_sim"]>=0.75 or k["evidence"].get("score_phrases",0)>=2 or k["evidence"].get("total_hits",0)>=3):
            st.session_state["novel_counts"][k["name"]] += 1
            if st.session_state["novel_counts"][k["name"]] >= 2:
                novel_dels.append(k["name"])
    for k in dets_kept:
        if k["novel"] and (k["sem_sim"]>=0.75 or k["evidence"].get("score_phrases",0)>=2 or k["evidence"].get("total_hits",0)>=3):
            st.session_state["novel_counts"][k["name"]] += 1
            if st.session_state["novel_counts"][k["name"]] >= 2:
                novel_dets.append(k["name"])

    allowed_dels = [k["name"] for k in dels_kept if not k["novel"]]
    allowed_dets = [k["name"] for k in dets_kept if not k["novel"]]

    extras = {"delighters_scored": dels_kept, "detractors_scored": dets_kept, "conflicts": conflicts}
    return allowed_dels, allowed_dets, novel_dels[:5], novel_dets[:5], extras

# ---------- Build Index / Primer (finalize) ----------
LABEL_INDEX = _build_label_index(ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS, api_key) if (_HAS_OPENAI and _has_key) else None
if primer_enabled and _HAS_OPENAI and _has_key and st.session_state.get("PRIMER") is None:
    with st.status("Building Global Primer…"):
        PRIMER = build_global_primer(verb_series.tolist(), df.get("Star Rating", pd.Series(dtype=float)).tolist(),
                                     ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, api_key)
        st.session_state["PRIMER"] = PRIMER

# ---------- UI Shell ----------
header()
# sort order
if order != "Original":
    missing_idx = sorted(missing_idx, key=lambda i: (len(verb_series.iloc[i]) if i < len(verb_series) else 0), reverse=(order=="Longest first"))
stepbar(5, missing_count, model_choice, strictness, eta_secs, rough_cost)

# ---------- Queue Helpers ----------
def _queue_filtered():
    q = st.session_state["queue_filter_q"].lower().strip()
    stars = set(st.session_state["queue_filter_stars"])
    out=[]
    for i in missing_idx:
        txt = str(verb_series.iloc[i])
        star = df.get("Star Rating", pd.Series(dtype=float)).iloc[i] if "Star Rating" in df.columns else None
        if q and q not in txt.lower(): continue
        if stars and star not in stars: continue
        out.append(i)
    return out

def _append_suggestion(sug: dict):
    st.session_state["symptom_suggestions"].append(sug)
    st.session_state["sug_by_row"][sug["row_index"]] = sug
    if len(st.session_state["symptom_suggestions"]) > 2000:
        st.session_state["symptom_suggestions"] = st.session_state["symptom_suggestions"][-2000:]

def _apply_row(row_idx: int, s: dict):
    dets_final = (s["detractors"] + s.get("approve_novel_det", []))[:10]
    dels_final = (s["delighters"] + s.get("approve_novel_del", []))[:10]
    for j, name in enumerate(dets_final, start=1):
        col = f"{APP['SYMPTOM_PREFIX']}{j}"
        if col in df.columns:
            old = df.at[row_idx, col]
            if str(old) != str(name):
                st.session_state["pending_changes"].append({"row": int(row_idx), "column": col, "old": None if pd.isna(old) else str(old), "new": str(name)})
            df.at[row_idx, col] = name
    for j, name in enumerate(dels_final, start=11):
        col = f"{APP['SYMPTOM_PREFIX']}{j}"
        if col in df.columns:
            old = df.at[row_idx, col]
            if str(old) != str(name):
                st.session_state["pending_changes"].append({"row": int(row_idx), "column": col, "old": None if pd.isna(old) else str(old), "new": str(name)})
            df.at[row_idx, col] = name
    for n in s.get("approve_novel_del", []): 
        if n: st.session_state["approved_new_delighters"].add(n)
    for n in s.get("approve_novel_det", []): 
        if n: st.session_state["approved_new_detractors"].add(n)
    st.session_state["applied_rows"].add(row_idx)

# ---------- Workbench (3 panes) ----------
c_left, c_mid, c_right = st.columns([2.1, 4.2, 3.0], gap="small")

# LEFT: Queue & Filters
with c_left:
    st.markdown("#### Queue & Filters")
    st.session_state["queue_filter_q"] = st.text_input("Search", value=st.session_state["queue_filter_q"], placeholder="Find text…")
    star_opts = sorted(pd.Series(df.get("Star Rating", pd.Series(dtype=float))).dropna().unique().tolist())
    st.session_state["queue_filter_stars"] = st.multiselect("Star", options=star_opts, default=st.session_state["queue_filter_stars"])
    queue = _queue_filtered()
    if not queue:
        st.info("No rows match current filters.")
    else:
        st.session_state["cursor_pos"] = max(0, min(st.session_state["cursor_pos"], len(queue)-1))
        for k, ridx in enumerate(queue[:250]):
            txt = str(verb_series.iloc[ridx]); star = df.get("Star Rating", pd.Series(dtype=float)).iloc[ridx] if "Star Rating" in df.columns else None
            sugg = st.session_state["sug_by_row"].get(ridx)
            applied = (ridx in st.session_state["applied_rows"])
            dot = "dot-applied" if applied else ("dot-sug" if sugg else "dot-off")
            active = (k == st.session_state["cursor_pos"])
            st.markdown(
                f"<div class='queue-row {'active' if active else ''}'>"
                f"<div><span class='status-dot {dot}'></span> <b>Row {ridx}</b> • ⭐ {star if not pd.isna(star) else '-'} • {len(txt)} chars</div>"
                f"<div style='opacity:.85'>{txt[:48]}…</div>"
                f"</div>", unsafe_allow_html=True
            )
            cols = st.columns(3)
            with cols[0]:
                if st.button("Select", key=f"sel_{ridx}", use_container_width=True):
                    st.session_state["cursor_pos"] = k; st.experimental_rerun()
            with cols[1]:
                if st.button("Run", key=f"run_{ridx}", use_container_width=True, disabled=not ((_HAS_OPENAI and _has_key) or True)):
                    review_txt = str(verb_series.iloc[ridx]); stars = df.get("Star Rating", pd.Series(dtype=float)).iloc[ridx]
                    dels, dets, novel_dels, novel_dets, extras = _score_pipeline(
                        review_txt, stars, ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, strictness,
                        evidence_hits_required=evidence_hits_required, row_index=int(ridx)
                    )
                    _append_suggestion({"row_index":int(ridx),"stars": float(stars) if not pd.isna(stars) else None,
                                        "review":review_txt,"delighters":dels,"detractors":dets,
                                        "novel_delighters":novel_dels,"novel_detractors":novel_dets,
                                        "approve_novel_del":[],"approve_novel_det":[],"explain":extras})
                    st.experimental_rerun()
            with cols[2]:
                if st.button("Apply", key=f"apply_{ridx}", use_container_width=True, disabled=(st.session_state["sug_by_row"].get(ridx) is None)):
                    _apply_row(ridx, st.session_state["sug_by_row"][ridx])
                    if st.session_state["pending_changes"]:
                        st.session_state["history"].append({"changes": st.session_state["pending_changes"]}); st.session_state["pending_changes"]=[]
                    st.success(f"Applied row {ridx}")

# MID: Review canvas
with c_mid:
    st.markdown("#### Review")
    queue = _queue_filtered()
    if not queue:
        st.info("Adjust your filters on the left to see items.")
    else:
        ridx = queue[st.session_state["cursor_pos"]]
        review_txt = str(verb_series.iloc[ridx]); stars = df.get("Star Rating", pd.Series(dtype=float)).iloc[ridx] if "Star Rating" in df.columns else None
        sug = st.session_state["sug_by_row"].get(ridx)
        if sug:
            terms_hl = sug["delighters"] + sug["detractors"]
            st.markdown(f"<div class='review'>{_highlight_terms(review_txt, terms_hl)}</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='review'>{review_txt}</div>", unsafe_allow_html=True)

        cols = st.columns(3)
        with cols[0]:
            if st.button("Run on this row", type="primary", key="run_current"):
                dels, dets, novel_dels, novel_dets, extras = _score_pipeline(
                    review_txt, stars, ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, strictness,
                    evidence_hits_required=evidence_hits_required, row_index=int(ridx)
                )
                _append_suggestion({"row_index":int(ridx),"stars": float(stars) if not pd.isna(stars) else None,
                                    "review":review_txt,"delighters":dels,"detractors":dets,
                                    "novel_delighters":novel_dels,"novel_detractors":novel_dets,
                                    "approve_novel_del":[],"approve_novel_det":[],"explain":extras})
                st.experimental_rerun()
        with cols[1]:
            if st.button("Re-run stricter (verify+skeptic)", key="rerun_strict", disabled=(sug is None)):
                dels, dets, novel_dels, novel_dets, extras = _score_pipeline(
                    review_txt, stars, ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, min(0.95, strictness+0.1),
                    evidence_hits_required=max(2, evidence_hits_required), row_index=int(ridx)
                )
                sug.update({"delighters":dels,"detractors":dets,"novel_delighters":novel_dels,"novel_detractors":novel_dets,"explain":extras})
                st.experimental_rerun()
        with cols[2]:
            st.caption(f"Row {ridx} • ⭐ {stars if not pd.isna(stars) else '-'} • {len(review_txt)} chars")

        st.markdown("##### Suggestions")
        cdet, cdel = st.columns(2)
        if sug:
            with cdet:
                st.write("**Detractors (≤10)**")
                if sug["detractors"]:
                    st.markdown("<div class='chips'>"+"".join([f"<span class='chip neg'>{x}</span>" for x in sug["detractors"]])+"</div>", unsafe_allow_html=True)
                else: st.code("–")
                if sug["novel_detractors"]:
                    st.info("Novel detractors (approve to add):")
                    approves=[]
                    for j,name in enumerate(sug["novel_detractors"]):
                        if st.checkbox(name, key=f"novdet_{ridx}_{j}"): approves.append(name)
                    sug["approve_novel_det"] = approves
            with cdel:
                st.write("**Delighters (≤10)**")
                if sug["delighters"]:
                    st.markdown("<div class='chips'>"+"".join([f"<span class='chip pos'>{x}</span>" for x in sug["delighters"]])+"</div>", unsafe_allow_html=True)
                else: st.code("–")
                if sug["novel_delighters"]:
                    st.info("Novel delighters (approve to add):")
                    approves=[]
                    for j,name in enumerate(sug["novel_delighters"]):
                        if st.checkbox(name, key=f"novdel_{ridx}_{j}"): approves.append(name)
                    sug["approve_novel_del"] = approves
        else:
            st.info("No suggestions yet. Click **Run on this row**.")

        # Sticky CTAs
        st.markdown("<div class='sticky-cta'>", unsafe_allow_html=True)
        colA, colB, colC = st.columns([1,1,1])
        with colA:
            if st.button("⬅ Previous", use_container_width=True, disabled=(st.session_state["cursor_pos"] <= 0)):
                st.session_state["cursor_pos"] -= 1; st.experimental_rerun()
        with colB:
            if st.button("Apply to DataFrame", type="primary", use_container_width=True, disabled=(sug is None)):
                _apply_row(ridx, sug)
                if st.session_state["pending_changes"]:
                    st.session_state["history"].append({"changes": st.session_state["pending_changes"]}); st.session_state["pending_changes"]=[]
                st.success(f"Applied row {ridx}")
        with colC:
            if st.button("Apply & Next ➡", use_container_width=True, disabled=(sug is None or st.session_state["cursor_pos"] >= len(queue)-1)):
                _apply_row(ridx, sug)
                if st.session_state["pending_changes"]:
                    st.session_state["history"].append({"changes": st.session_state["pending_changes"]}); st.session_state["pending_changes"]=[]
                st.session_state["cursor_pos"] += 1; st.experimental_rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# RIGHT: Why & Primer
with c_right:
    st.markdown("#### Why & Primer")
    queue = _queue_filtered()
    if queue:
        ridx = queue[st.session_state["cursor_pos"]]; sug = st.session_state["sug_by_row"].get(ridx)
        if sug and sug.get("explain"):
            st.write("**Detractors — details**")
            for item in sug["explain"].get("detractors_scored", []):
                cols = st.columns([0.46,0.18,0.18,0.18])
                with cols[0]: st.markdown(f"🔴 **{item['name']}**"); 
                with cols[1]: st.caption(f"Final: **{item['fused_conf']:.2f}**")
                with cols[2]: st.caption(f"LLM: {item['llm_conf']:.2f}")
                with cols[3]: st.caption(f"Sem: {item['sem_sim']:.2f}")
                ev = item["evidence"]; negs = sum(1 for h in ev.get("hits",[]) if h.get("negated"))
                st.caption(f"Hits: {ev.get('total_hits',0)} • Phrases: {ev.get('score_phrases',0)} • Intensity: {ev.get('intensity',0)} • Negations: {negs}")
                if item.get("quote"): st.caption(f"“{item['quote']}”")
                if item.get("verify"): st.caption(f"Verify: {item['verify'].get('confidence',0):.2f} — {item['verify'].get('reason','')}")
                if item.get("skeptic",{}).get("contradicts"): st.caption(f"⚠️ Skeptic quote: “{item['skeptic'].get('quote','')}”")
                if item.get("selfcons",{}).get("extra_votes"): st.caption(f"Self-consistency votes: {sum(1 for v in item['selfcons']['extra_votes'] if v.get('present'))+1}/{APP['SC_VOTES']}")
            st.divider()
            st.write("**Delighters — details**")
            for item in sug["explain"].get("delighters_scored", []):
                cols = st.columns([0.46,0.18,0.18,0.18])
                with cols[0]: st.markdown(f"🟢 **{item['name']}**")
                with cols[1]: st.caption(f"Final: **{item['fused_conf']:.2f}**")
                with cols[2]: st.caption(f"LLM: {item['llm_conf']:.2f}")
                with cols[3]: st.caption(f"Sem: {item['sem_sim']:.2f}")
                ev = item["evidence"]; negs = sum(1 for h in ev.get("hits",[]) if h.get("negated"))
                st.caption(f"Hits: {ev.get('total_hits',0)} • Phrases: {ev.get('score_phrases',0)} • Intensity: {ev.get('intensity',0)} • Negations: {negs}")
                if item.get("quote"): st.caption(f"“{item['quote']}”")
                if item.get("verify"): st.caption(f"Verify: {item['verify'].get('confidence',0):.2f} — {item['verify'].get('reason','')}")
                if item.get("skeptic",{}).get("contradicts"): st.caption(f"⚠️ Skeptic quote: “{item['skeptic'].get('quote','')}”")
                if item.get("selfcons",{}).get("extra_votes"): st.caption(f"Self-consistency votes: {sum(1 for v in item['selfcons']['extra_votes'] if v.get('present'))+1}/{APP['SC_VOTES']}")
            if sug["explain"].get("conflicts"):
                st.warning("Conflicts resolved: " + ", ".join([f"{a}↔{b}" for a,b in sug["explain"]["conflicts"]]))
        else:
            st.caption("Run on the current row to see per-label rationale.")

    st.divider()
    st.write("**Primer Snapshot**")
    if PRIMER:
        st.markdown(PRIMER.get("brief","(no brief)"))
        with st.expander("Top cluster terms"):
            df_terms = pd.DataFrame(PRIMER.get("clusters", []))
            if not df_terms.empty:
                st.dataframe(df_terms[["id","top_terms","mean_stars","size"]])
    else:
        st.caption("Primer not built or unavailable.")

# ---------- Export & Tools ----------
st.divider()
st.markdown("### Export & Tools")

cols_tools = st.columns([1,1,1,1])
with cols_tools[0]:
    if st.button("↩️ Undo last apply"):
        if st.session_state["history"]:
            last = st.session_state["history"].pop()
            for ch in reversed(last["changes"]):
                df.at[ch["row"], ch["column"]] = ch["old"]
            st.success(f"Reverted {len(last['changes'])} cell(s).")
        else:
            st.info("Nothing to undo.")
with cols_tools[1]:
    if st.button("📤 Download change log (CSV)"):
        log=[]
        for k, h in enumerate(st.session_state["history"], start=1):
            for ch in h["changes"]:
                log.append({"batch":k, **ch})
        if log:
            log_df = pd.DataFrame(log); csv = log_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download changes.csv", data=csv, file_name="changes.csv", mime="text/csv")
        else: st.info("No changes recorded yet.")
with cols_tools[2]:
    st.session_state["dry_run"] = st.checkbox("Dry run (disable Excel writes/downloads)", value=st.session_state["dry_run"])
with cols_tools[3]:
    if st.button("🔄 Reset session"):
        for k in ["symptom_suggestions","sug_selected","approved_new_delighters","approved_new_detractors",
                  "PRIMER","history","pending_changes","sug_by_row","applied_rows","cursor_pos","novel_counts"]:
            if k in st.session_state: del st.session_state[k]
        st.success("Session state cleared."); st.experimental_rerun()

# ---------- Export (preserve formatting when possible) ----------
def offer_downloads(dry_run_flag: bool = False):
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first."); return
    if dry_run_flag:
        st.info("Dry run enabled. Downloads disabled."); return

    raw = st.session_state["uploaded_bytes"]
    formatted_ok=False; formatted_bytes=None
    if _HAS_OPENPYXL:
        try:
            bio=io.BytesIO(raw); wb=load_workbook(bio); data_sheet=APP["DATA_SHEET_DEFAULT"]
            if data_sheet not in wb.sheetnames: data_sheet = wb.sheetnames[0]
            ws=wb[data_sheet]
            headers={ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name): return headers.get(name)
            df_reset=df.reset_index(drop=True)
            for df_row_idx, row in df_reset.iterrows():
                excel_row=2+df_row_idx
                for c in SYMPTOM_COLS:
                    ci=col_idx(c)
                    if ci is None: continue
                    val=row.get(c, None)
                    if pd.isna(val) or (str(val).strip()==""): ws.cell(row=excel_row, column=ci, value=None)
                    else: ws.cell(row=excel_row, column=ci, value=str(val))

            # Approvals sheet
            HS = APP["HIDDEN_SHEET_APPROVALS"]
            if HS not in wb.sheetnames:
                wh=wb.create_sheet(HS); wh.sheet_state="hidden"
                wh.cell(row=1,column=1,value="Approved Delighters"); wh.cell(row=1,column=2,value="Approved Detractors")
            else:
                wh=wb[HS]; 
                if not wh.cell(row=1,column=1).value: wh.cell(row=1,column=1,value="Approved Delighters")
                if not wh.cell(row=1,column=2).value: wh.cell(row=1,column=2,value="Approved Detractors")
            exist_del, exist_det=set(), set()
            try:
                r=2
                while True:
                    v=wh.cell(row=r,column=1).value
                    if v is None: break
                    v=str(v).strip()
                    if v: exist_del.add(v); r+=1
            except Exception: pass
            try:
                r=2
                while True:
                    v=wh.cell(row=r,column=2).value
                    if v is None: break
                    v=str(v).strip()
                    if v: exist_det.add(v); r+=1
            except Exception: pass
            new_del=set([n for n in st.session_state.get("approved_new_delighters", set()) if n])
            new_det=set([n for n in st.session_state.get("approved_new_detractors", set()) if n])
            final_del=sorted(exist_del.union(new_del)); final_det=sorted(exist_det.union(new_det))
            max_len=max(len(final_del), len(final_det), 1)
            for r in range(2, 2+max_len+200): wh.cell(row=r,column=1,value=None); wh.cell(row=r,column=2,value=None)
            for i,v in enumerate(final_del, start=2): wh.cell(row=i,column=1,value=v)
            for i,v in enumerate(final_det, start=2): wh.cell(row=i,column=2,value=v)

            # Config sheet (JSON)
            HC = APP["HIDDEN_SHEET_CONFIG"]
            cfg = {
                "label_cards": st.session_state.get("label_cards", {}),
                "calibration": st.session_state.get("calibration", {}),
                "exclusive_pairs": (PRIMER or {}).get("exclusive_pairs", [])
            }
            blob = json.dumps(cfg, ensure_ascii=False)
            if HC in wb.sheetnames:
                wcfg = wb[HC]
            else:
                wcfg = wb.create_sheet(HC); wcfg.sheet_state = "hidden"
            wcfg.cell(row=1, column=1, value=blob[:32760])  # Excel cell limit safeguard

            out_bio=io.BytesIO(); wb.save(out_bio); formatted_bytes=out_bio.getvalue(); formatted_ok=True
        except Exception as e:
            st.warning(f"Format-preserving save failed; falling back. Reason: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET_DEFAULT"], index=False)
            allowed_df = pd.DataFrame({"Delighters": pd.Series(ALLOWED_DELIGHTERS), "Detractors": pd.Series(ALLOWED_DETRACTORS)})
            allowed_df.to_excel(xlw, sheet_name="Allowed Symptoms (session)", index=False)
            appr_df = pd.DataFrame({"Approved Delighters": pd.Series(sorted(list(st.session_state.get("approved_new_delighters", set())))),
                                    "Approved Detractors": pd.Series(sorted(list(st.session_state.get("approved_new_detractors", set()))))})
            appr_df.to_excel(xlw, sheet_name=APP["HIDDEN_SHEET_APPROVALS"], index=False)
            cfg_df = pd.DataFrame([[json.dumps({
                "label_cards": st.session_state.get("label_cards", {}),
                "calibration": st.session_state.get("calibration", {}),
                "exclusive_pairs": (PRIMER or {}).get("exclusive_pairs", [])
            }, ensure_ascii=False)]])
            cfg_df.to_excel(xlw, sheet_name=APP["HIDDEN_SHEET_CONFIG"], index=False, header=False)
        basic_bytes = out2.getvalue()
    except Exception as e:
        st.error(f"Basic writer failed: {e}")

    cols = st.columns([1,1])
    with cols[0]:
        if formatted_ok and formatted_bytes:
            st.download_button("⬇️ Download updated (preserve formatting)", data=formatted_bytes,
                               file_name="starwalk_symptomized_formatted.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("Format-preserving version unavailable.")
    with cols[1]:
        if basic_bytes:
            st.download_button("⬇️ Download updated (basic)", data=basic_bytes,
                               file_name="starwalk_symptomized.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads(dry_run_flag=st.session_state["dry_run"])
