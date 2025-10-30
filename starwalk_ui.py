# ---------- Star Walk v6.2 — QE Console (Reliability + Evidence + Config + Savepoint) ----------
# Streamlit 1.38+
# Optional deps: openai, openpyxl, plotly, scikit-learn, langdetect

import io, os, re, json, difflib, random, time, tempfile, hashlib
from typing import List, Tuple, Optional, Dict, Any
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# ========================= Optional libs =========================
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

try:
    from langdetect import detect
    _HAS_LANG = True
except Exception:
    _HAS_LANG = False

# ========================= App constants =========================
APP = {
    "PAGE_TITLE": "Star Walk QE — v6.2",
    "DATA_SHEET_DEFAULT": "Star Walk scrubbed verbatims",
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),  # 1–10 detractors, 11–20 delighters
    "EMB_MODEL": "text-embedding-3-small",
    "CHAT_FAST": "gpt-4o-mini",
    "CHAT_STRICT": "gpt-4.1",

    # Added columns
    "SAFETY_FLAG_COL": "Safety Risk?",
    "SAFETY_EVIDENCE_COL": "Safety Evidence",
    "RELIABILITY_FLAG_COL": "Reliability Failure?",
    "RELIABILITY_MODE_COL": "Failure Mode",
    "RELIABILITY_COMP_COL": "Suspected Component",
    "RELIABILITY_SEV_COL": "Severity (1-5)",
    "RELIABILITY_RPN_COL": "RPN",
    "RELIABILITY_QUOTE_COL": "Reliability Quote",
    "SUGGESTION_SUM_COL": "Customer Suggestion",
    "SUGGESTION_TYPE_COL": "Action Type",
    "SUGGESTION_OWNER_COL": "Owner (hint)",
    "CSAT_IMPACT_COL": "CSAT Impact (est.)",
    "VOC_QUOTE_COL": "VOC Quote",
    "CONFIG_SHEET": "__StarWalk_Config",
    "APPROVED_SHEET": "__StarWalk_Approved",
    "AUDIT_SHEET": "__Audit_Log",
    "RUNINFO_SHEET": "__Run_Info",
}

# Deterministic base
random.seed(42); np.random.seed(42)

# ========================= Page & CSS =========================
st.set_page_config(layout="wide", page_title=APP["PAGE_TITLE"], page_icon="🛠️")
st_html("""<script>try{document.documentElement.setAttribute('data-theme','light');localStorage.setItem('theme','light')}catch(e){}</script>""", height=0)
st.markdown("""
<style>
:root{ --fg:#0f172a; --muted:#64748b; --bd:#e2e8f0; --tile:#f8fafc; --card:#ffffff; --accent:#2563eb; --ok:#059669; --bad:#dc2626; --warn:#d97706 }
.stApp, .block-container{ background:#f6f8fc; color:var(--fg) }
.block-container{ padding-top:.6rem; padding-bottom:1.1rem; max-width:1480px }
.header{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border:1px solid var(--bd); border-radius:16px; background:var(--card); box-shadow:0 6px 16px rgba(2,6,23,.06) }
.title{ font-size:clamp(20px,2.6vw,32px); font-weight:800 }
.sub{ color:var(--muted) }
.kpis{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 }
.pill{ display:inline-flex; gap:6px; align-items:center; padding:6px 10px; border-radius:999px; border:1.5px solid var(--bd); background:var(--tile); font-weight:700 }
.card{ background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:14px; box-shadow:0 6px 16px rgba(2,6,23,.06) }
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
.chip{padding:6px 10px;border-radius:999px;border:1.5px solid var(--bd);background:var(--tile);font-weight:700}
.chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
.chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
.review{ white-space:pre-wrap; background:var(--tile); border:1px solid var(--bd); border-radius:12px; padding:10px }
.toast{ font-size:.92rem; color:var(--muted) }
.badge{ display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--bd); background:#eef2ff; font-size:.85rem }
.small{ color:var(--muted); font-size:.9rem }
button[kind="primary"]{ border-radius:12px }
</style>
""", unsafe_allow_html=True)

def header(run_meta: dict):
    st.markdown(f"""
    <div class="header">
      <div>
        <div class="title">Star Walk QE — Reliability + Evidence</div>
        <div class="sub">Symptomize with idiom-aware intent, attach verbatim evidence, and export audit-ready results.</div>
      </div>
      <div class="small">
        <span class="badge">Profile: {run_meta.get('profile','Balanced')}</span>
        <span class="badge">Model: {run_meta.get('model','-')}</span>
        <span class="badge">Run: {run_meta.get('run_id','-')}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ========================= Upload & base df =========================
uploaded = st.sidebar.file_uploader("📁 Upload Excel (.xlsx)", type=["xlsx"])
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0); st.session_state["uploaded_bytes"] = uploaded.read(); uploaded.seek(0)

if not uploaded:
    header({"profile":"—","model":"—","run_id":"—"})
    st.info("Upload a workbook to begin.")
    st.stop()

def read_sheet(_upl):
    try:
        try:
            return pd.read_excel(_upl, sheet_name=APP["DATA_SHEET_DEFAULT"])
        except ValueError:
            return pd.read_excel(_upl)
    except Exception as e:
        st.error(f"Could not read the Excel file: {e}"); st.stop()

df = read_sheet(uploaded)
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
stars_series = df.get("Star Rating", pd.Series(dtype=float))

# locate Symptom columns
explicit_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30: SYMPTOM_COLS = df.columns[10:30].tolist()
if not SYMPTOM_COLS: st.error("Couldn't locate Symptom 1–20 columns (K–AD)."); st.stop()
if SYMPTOM_COLS and not all(str(c).lower().startswith("symptom ") for c in SYMPTOM_COLS):
    st.warning("Symptom columns inferred by position; verify headers to avoid mis-writes.")

# Ensure added columns exist
for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
            APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
            APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
            APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
    if col not in df.columns:
        df[col] = ""

# ========================= Symptoms list (Delighters/Detractors) =========================
import io as _io
def _norm(s: str) -> str:
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip() if s is not None else ""

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
        dels = [str(x).strip() for x in df_sheet.get(best_del, pd.Series(dtype=str)).dropna().tolist() if str(x).strip()] if best_del else []
        dets = [str(x).strip() for x in df_sheet.get(best_det, pd.Series(dtype=str)).dropna().tolist() if str(x).strip()] if best_det else []
        if dels or dets:
            debug.update({"strategy":"fuzzy-headers","best_del_col":best_del,"best_det_col":best_det}); return dels, dets, debug
    # Type+Item
    type_col = None; item_col = None
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
            debug.update({"strategy":"type+item","type_col":type_col,"item_col":item_col}); return dels, dets, debug
    # First two non-empty
    non = []
    for c in df_sheet.columns:
        vals = [str(x).strip() for x in df_sheet[c].dropna().tolist() if str(x).strip()]
        if vals: non.append((c, vals))
        if len(non) >= 2: break
    if non: return non[0][1], (non[1][1] if len(non) > 1 else []), {"strategy":"first-two-nonempty","picked_cols":[c for c,_ in non[:2]]}
    return [], [], {"strategy":"none","columns":list(df_sheet.columns)}

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> Optional[str]:
    names = xls.sheet_names; cands = [n for n in names if _looks_like_symptom_sheet(n)]
    return (min(cands, key=lambda n: len(_norm(n))) if cands else names[0]) if names else None

raw_bytes = st.session_state.get("uploaded_bytes", b"")
sheet_names=[]
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes))
    sheet_names=_xls_tmp.sheet_names
except Exception:
    pass

auto_sheet = autodetect_symptom_sheet(_xls_tmp) if sheet_names else None
st.sidebar.markdown("### 🧾 Symptoms Source")
chosen_sheet = st.sidebar.selectbox("Sheet with Delighters/Detractors", options=sheet_names if sheet_names else ["(no sheets)"],
                                    index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0))

def load_symptom_lists_robust(raw_bytes: bytes, user_sheet: Optional[str]=None):
    meta={"sheet":None,"strategy":None,"columns":[],"note":""}
    if not raw_bytes: meta["note"]="No raw bytes"; return [], [], meta, {}
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw_bytes))
    except Exception as e:
        meta["note"]=f"Could not open Excel: {e}"; return [], [], meta, {}
    sheet = user_sheet or autodetect_symptom_sheet(xls); meta["sheet"]=sheet
    try:
        s = pd.read_excel(xls, sheet_name=sheet)
    except Exception as e:
        meta["note"]=f"Could not read sheet '{sheet}': {e}"; return [], [], meta, {}
    dels, dets, info = _extract_from_df(s); meta.update(info)

    # Hidden approvals (merge)
    try:
        if APP["APPROVED_SHEET"] in xls.sheet_names:
            hdf = pd.read_excel(xls, sheet_name=APP["APPROVED_SHEET"])
            if "Approved Delighters" in hdf.columns:
                dels += [str(x).strip() for x in hdf["Approved Delighters"].dropna() if str(x).strip()]
            if "Approved Detractors" in hdf.columns:
                dets += [str(x).strip() for x in hdf["Approved Detractors"].dropna() if str(x).strip()]
            dels=list(dict.fromkeys(dels)); dets=list(dict.fromkeys(dets))
    except Exception:
        pass

    # Config (thresholds & sense rules)
    CFG={"thr":{}, "sense":[]}
    if APP["CONFIG_SHEET"] in xls.sheet_names:
        try:
            cfgdf = pd.read_excel(xls, sheet_name=APP["CONFIG_SHEET"])
            block=None
            for _, row in cfgdf.iterrows():
                v=str(row[0]).strip() if pd.notna(row[0]) else ""
                if v.startswith("[") and v.endswith("]"):
                    block=v.strip("[]"); continue
                if block=="LabelThresholds" and pd.notna(row[0]):
                    CFG["thr"][str(row[0]).strip()]={"min_conf":float(row[1]),"sem_min":float(row[2])}
                if block=="SenseRules" and pd.notna(row[0]):
                    CFG["sense"].append({
                        "cue": str(row[0]),
                        "default": str(row[1]) if pd.notna(row[1]) else "",
                        "route_rx": str(row[2]) if pd.notna(row[2]) else "",
                        "route_label": str(row[3]) if pd.notna(row[3]) else "",
                        "avoid": str(row[4]) if pd.notna(row[4]) else "",
                    })
        except Exception:
            pass

    return dels, dets, meta, CFG

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META, CFG = load_symptom_lists_robust(raw_bytes, user_sheet=chosen_sheet if sheet_names else None)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DEL_SET, ALLOWED_DET_SET = set(ALLOWED_DELIGHTERS), set(ALLOWED_DETRACTORS)
st.session_state["CFG_THR"] = CFG.get("thr", {})
CFG_SENSE = CFG.get("sense", [])

# ========================= Controls =========================
st.sidebar.markdown("### ⚙️ Configure")
strictness = st.sidebar.slider("Strictness (higher=fewer)", 0.55, 0.95, 0.62, 0.01)
semantic_threshold = st.sidebar.slider("Min semantic similarity", 0.50, 0.90, 0.58, 0.01)
evidence_required = st.sidebar.checkbox("Require evidence quotes", value=True)
skeptic_pass = st.sidebar.checkbox("Skeptic pass (contradiction scan)", value=True)
overwrite_all = st.sidebar.checkbox("Overwrite existing cells", value=True)

with st.sidebar.expander("🎚️ Quality profile", expanded=False):
    prof = st.radio("Preset", ["Balanced","High Recall","High Precision"], index=0)
    if prof == "High Recall":
        strictness = 0.60; semantic_threshold = 0.56; evidence_required = True
    elif prof == "High Precision":
        strictness = 0.72; semantic_threshold = 0.66; evidence_required = True

st.sidebar.markdown("### 🧩 Reliability & Safety")
enable_reliability = st.sidebar.checkbox("Detect Reliability Failures", value=True)
sev_floor = st.sidebar.slider("Min severity to record", 1, 5, 2)
safety_detect = st.sidebar.checkbox("Detect Safety Risk", value=True)
safety_strict = st.sidebar.slider("Safety strictness", 0.55, 0.95, 0.60, 0.01)

# API
api_key = (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
_has_key = bool(api_key)
if not _HAS_OPENAI: st.warning("Install `openai` to enable AI features.")
if _HAS_OPENAI and not _has_key: st.warning("Set OPENAI_API_KEY to enable AI features.")

# ========================= Helpers & Lexicons =========================
def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

ALIAS_CANON = {
    "initial difficulty":"Learning curve",
    "hard to learn":"Learning curve",
    "setup difficulty":"Learning curve",
    "too loud":"Loud",
    "noisy startup":"Startup noise",
}
def canonicalize(name: str) -> str:
    nn = (name or "").strip(); base = _normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k) == base: return v
    return nn

# Reliability seeds
FAILURE_MODES = {
    "Won’t power on": ["won't turn on","no power","dead on arrival","doesn't start","won't start"],
    "Intermittent power": ["cuts out","turns off randomly","shuts off","stops mid use"],
    "Motor stalls / weak": ["motor stalls","stalls","weak suction","loses power","bogs down"],
    "Battery won’t hold charge": ["battery dies fast","won't charge","doesn't hold charge","battery failure"],
    "Overheats / thermal shutoff": ["overheat","too hot","thermal","shuts off hot"],
    "Leaks / water ingress": ["leaks","water in","condensation","moisture","drips"],
    "Sensor / indicator faulty": ["indicator wrong","sensor error","light stuck","filter light stuck"],
    "Controls unresponsive": ["button not working","controls unresponsive","switch broken","dial broken"],
    "Charging/base dock issue": ["dock issue","charging base","won't dock","contacts not"],
}
COMPONENT_HINTS = {
    "Power supply / PCB": ["no power","won't turn on","short","fuse","pcb","board"],
    "Battery pack": ["battery","charge","charging","won't charge","holds charge"],
    "Motor / impeller": ["motor","stall","suction","rpm","whine","grind"],
    "Thermal protection": ["overheat","thermal","hot","heat"],
    "Seals / water path": ["leak","water","condensation","seal","o-ring"],
    "UI / Buttons": ["button","switch","dial","control"],
    "Dock / Contacts": ["dock","contacts","charging base","pins"],
    "Sensors / Indicators": ["indicator","sensor","light","led"],
}

SUG_ACTION_TYPES = ["Design change","Firmware/Calibration","Instructions/Onboarding","Packaging/Accessories","Service/Replacement","Policy/Warranty","App/Connectivity"]
OWNER_HINTS = {"Design change":"PD","Firmware/Calibration":"PD","Instructions/Onboarding":"CX","Packaging/Accessories":"NPI","Service/Replacement":"CX","Policy/Warranty":"CX","App/Connectivity":"PD"}

SAFETY_CUES_POS = ["burn","burnt","burning","smoke","smoking","fire","flame","melt","melting","shock","sparks","spark","short circuit","overheat","overheating","explode","explosion","toxic","hazardous","dangerous","injury","cut","laceration","electric shock","shock hazard","safety issue","safety risk","caught fire"]
SAFETY_CUES_NEG = ["no fire","not dangerous","safe","safely","no risk"]
SAFETY_VERIFY_CLAIM = "The review reports a safety-related risk or incident with the product."

# ========================= Retry & Caching =========================
def with_retry(fn, *, tries=4, base=0.6, factor=2.0, jitter=0.2):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries-1: raise
            time.sleep(base*(factor**i) + random.uniform(0, jitter))

def _sig(txt:str)->str: return hashlib.sha1((txt or "").encode()).hexdigest()

# ========================= OpenAI helpers =========================
def verify_json(model: str, sys_prompt: str, user_obj: dict, api_key: str) -> dict:
    if not (_HAS_OPENAI and api_key): return {}
    client = OpenAI(api_key=api_key)
    out = with_retry(lambda: client.chat.completions.create(
        model=model, temperature=0.0,
        messages=[{"role":"system","content":sys_prompt},{"role":"user","content":json.dumps(user_obj)}],
        response_format={"type":"json_object"},
    ))
    try: return json.loads(out.choices[0].message.content or "{}")
    except Exception: return {}

@lru_cache(maxsize=200_000)
def cached_verify(review_text: str, claim: str, model: str, api_key: str) -> tuple[bool, float, tuple[str,...]]:
    if not (_HAS_OPENAI and api_key): return (False, 0.0, tuple())
    sys = "Only mark present if you can paste exact quotes. Return JSON: {present:bool, confidence:0..1, quotes:list[str], reason:str<=140}."
    data = verify_json(model, sys, {"review": review_text[:4000], "claim": claim}, api_key)
    quotes = [q for q in (data.get("quotes",[]) or []) if isinstance(q,str) and q and (q in review_text)]
    return (bool(data.get("present", False) and len(quotes)>0), float(data.get("confidence", 0.0)), tuple(quotes[:4]))

def skeptic_check(review: str, claim: str) -> dict:
    if not (_HAS_OPENAI and _has_key): return {"contradicts": False, "quote": ""}
    sys = "Is there text contradicting the claim? Return JSON {contradicts:bool, quote:str<=120}."
    data = verify_json(model_choice, sys, {"review": review[:4000], "claim": claim}, api_key)
    return {"contradicts": bool(data.get("contradicts", False)), "quote": str(data.get("quote",""))[:120]}

def openai_embed(texts: List[str]) -> np.ndarray:
    if not (_HAS_OPENAI and _has_key) or not texts: return np.zeros((0,1536), dtype="float32")
    client = OpenAI(api_key=api_key)
    out = with_retry(lambda: client.embeddings.create(model=APP["EMB_MODEL"], input=texts))
    M = np.array([d.embedding for d in out.data], dtype="float32"); M/= (np.linalg.norm(M, axis=1, keepdims=True)+1e-8)
    return M

# ========================= Embeddings index =========================
def _ngram_candidates(text: str, max_ngrams: int = 256) -> List[str]:
    ws = re.findall(r"[a-z0-9]{3,}", (text or "").lower()); ngrams, seen=[], set()
    for n in (1,2,3,4,5):
        for i in range(len(ws)-n+1):
            s=" ".join(ws[i:i+n])
            if len(s)>=4 and s not in seen:
                ngrams.append(s); seen.add(s)
                if len(ngrams)>=max_ngrams: break
        if len(ngrams)>=max_ngrams: break
    return ngrams

@st.cache_resource(show_spinner=False)
def build_label_index(labels: List[str], _api_key: str):
    if not (_HAS_OPENAI and _api_key and labels): return None
    texts = list(dict.fromkeys([canonicalize(x) for x in labels if x]))
    if not texts: return None
    M = openai_embed(texts)
    return (texts, M)

def semantic_support(review: str, label_index, _api_key: str, min_sim: float) -> Dict[str, float]:
    if (not label_index) or (not review): return {}
    labels, L = label_index; cands = _ngram_candidates(review)
    if not cands: return {}
    X = openai_embed(cands); 
    if X.shape[0]==0: return {}
    S = X @ L.T; best_idx = S.argmax(axis=1); best_sim = S[np.arange(len(cands)), best_idx]
    buckets={}
    for j, sim in zip(best_idx, best_sim):
        if sim >= min_sim:
            lab = labels[int(j)]
            if sim > buckets.get(lab, 0.0): buckets[lab] = float(sim)
    return buckets

LABEL_INDEX = build_label_index(ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS, api_key) if (_HAS_OPENAI and _has_key) else None

# ========================= Config-driven thresholds & sense rules =========================
def per_label_threshold(label: str, base: float) -> float:
    cfg = st.session_state.get("CFG_THR", {})
    if label in cfg:
        return max(0.55, min(0.90, float(cfg[label]["min_conf"])))
    return base

def per_label_sem_min(label: str, base: float) -> float:
    cfg = st.session_state.get("CFG_THR", {})
    if label in cfg:
        return max(0.50, min(0.90, float(cfg[label]["sem_min"])))
    return base

def resolve_intent_with_senses(text: str, candidate_label: str, allowed_set: set) -> Optional[str]:
    t = " " + (text or "").lower() + " "
    cand = canonicalize(candidate_label)
    if cand in allowed_set: return cand
    for r in CFG_SENSE:
        try:
            if r["cue"] and re.search(r["cue"], t, re.I):
                if r.get("avoid") and re.search(r["avoid"], t, re.I): 
                    continue
                if r.get("route_rx") and re.search(r["route_rx"], t, re.I) and r.get("route_label") in allowed_set:
                    return r["route_label"]
                if r.get("default") in allowed_set:
                    return r["default"]
        except re.error:
            continue
    return cand if cand in allowed_set else None

# ========================= Evidence & fusion =========================
def best_quote_for_label(text: str, label: str, llm_quotes: List[str]) -> str:
    keys = [k for k in re.findall(r"[a-z0-9]{3,}", label.lower()) if len(k) >= 3]
    t = text or ""
    for k in sorted(set(keys), key=len, reverse=True):
        m = re.search(rf".{{0,60}}\b{re.escape(k)}\b.{{0,60}}", t, re.I)
        if m: return m.group(0).strip()[:160]
    for q in llm_quotes:
        if q and q in t: return q[:160]
    return ""

def fuse_conf(llm_conf: float, sem_sim: float, has_quote: bool, stars: Optional[float], polarity: str) -> float:
    llm = max(0.0, min(1.0, float(llm_conf or 0))); sem = max(0.0, min(1.0, float(sem_sim or 0)))
    prior=0.0
    if stars is not None and not pd.isna(stars):
        if polarity=="delighter": prior = 0.07 if stars>=4.0 else (-0.07 if stars<=2.0 else 0.0)
        else: prior = 0.07 if stars<=2.0 else (-0.07 if stars>=4.0 else 0.0)
    evp = 0.10 if has_quote else 0.0
    return max(0.0, min(1.0, 0.50*llm + 0.30*sem + evp + prior))

def effective_threshold(base_thr: float, has_quote: bool) -> float:
    thr = float(base_thr) - (0.08 if has_quote else 0.0)
    return max(0.55, min(0.90, thr))

# ========================= Candidate proposal (symptoms) =========================
def propose_candidates(review: str, allowed: List[str], sem_min: float) -> List[dict]:
    sem_supp = {}
    if _HAS_OPENAI and _has_key and LABEL_INDEX:
        try: sem_supp = semantic_support(review, LABEL_INDEX, api_key, min_sim=sem_min)
        except Exception: sem_supp = {}
    items=[]
    if _HAS_OPENAI and _has_key:
        sys = 'Return JSON {"labels":[{"name":"", "confidence":0.0}]}. Choose <=10 from allowed_list that fit; omit if unsure.'
        user = {"review": review[:4000], "allowed_list": allowed[:120]}
        data = verify_json(model_choice, sys, user, api_key)
        items = data.get("labels", []) or []
        for i,x in enumerate(items):
            if isinstance(x, str): items[i] = {"name": x, "confidence": 0.6}
    # fallback lexical
    if not items:
        text = " "+review.lower()+" "
        for a in allowed:
            a_can=canonicalize(a)
            if re.search(rf"\b{re.escape(a_can.lower())}\b", text): items.append({"name": a_can, "confidence": 0.65})
    for it in items: it["_sem"] = float(sem_supp.get(canonicalize(it.get("name","")), 0.0))
    return items

# ========================= Safety & Reliability =========================
def detect_safety(review: str) -> Tuple[bool, str]:
    if not review.strip(): return False, ""
    pos_hit = any(re.search(rf"\b{re.escape(w)}\b", review, re.I) for w in SAFETY_CUES_POS)
    neg_hit = any(re.search(rf"\b{re.escape(w)}\b", review, re.I) for w in SAFETY_CUES_NEG)
    flag=False; quote=""
    if pos_hit and not neg_hit:
        flag=True; quote = next((m.group(0).strip()[:160] for w in SAFETY_CUES_POS for m in [re.search(rf".{{0,60}}\b{re.escape(w)}\b.{{0,60}}", review, re.I)] if m), "")
    if _HAS_OPENAI and _has_key:
        present, conf, quotes = cached_verify(review, SAFETY_VERIFY_CLAIM, model_choice, api_key)
        if present: flag=True; quote = (list(quotes)[0] if quotes else quote)
        else: flag = flag and (conf > safety_strict)
    return flag, quote

def detect_reliability(review: str) -> dict:
    if not review.strip(): return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}
    # lexical scout
    mode_scores=defaultdict(int); mode_quote=""
    for mode, cues in FAILURE_MODES.items():
        for c in cues:
            if re.search(rf"\b{re.escape(c)}\b", review, re.I):
                mode_scores[mode]+=1; mode_quote = mode_quote or (re.search(rf".{{0,60}}\b{re.escape(c)}\b.{{0,60}}", review, re.I).group(0) if re.search(rf"\b{re.escape(c)}\b", review, re.I) else "")
    if not (_HAS_OPENAI and _has_key):
        if not mode_scores: return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}
        top_mode = max(mode_scores.items(), key=lambda kv: kv[1])[0]
        comp = ""
        for k, hits in COMPONENT_HINTS.items():
            if any(re.search(rf"\b{re.escape(h)}\b", review, re.I) for h in hits): comp = k; break
        sev = 3 if re.search(r"\b(overheat|fire|shock|smoke)\b", review, re.I) else 2
        rpn = sev * (2 if mode_scores[top_mode]>=2 else 1)
        return {"present": True, "mode": top_mode, "component": comp, "severity": sev, "rpn": rpn, "quote": (mode_quote[:160] if mode_quote else "")}
    sys = ("From the review, identify a reliability failure if present. "
           "Return JSON {present,bool, mode,str, component,str, severity,int(1-5), rpn,int, quotes:list[str]}. "
           "Severity: 1 cosmetic, 3 partial loss, 5 safety-critical/unusable.")
    data = verify_json(model_choice, sys, {"review": review[:4000], "failure_modes": list(FAILURE_MODES.keys()), "component_hints": list(COMPONENT_HINTS.keys())}, api_key)
    present = bool(data.get("present", False))
    mode = str(data.get("mode",""))[:80]
    component = str(data.get("component",""))[:80]
    severity = int(data.get("severity", 0) or 0)
    rpn = int(data.get("rpn", 0) or (severity*2))
    quotes = [q for q in (data.get("quotes",[]) or []) if isinstance(q,str) and q and (q in review)]
    if present and quotes:
        return {"present": True, "mode": mode, "component": component, "severity": severity, "rpn": rpn, "quote": quotes[0][:160]}
    return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}

def extract_suggestion_and_csat(review: str, stars: Optional[float]) -> dict:
    if not (_HAS_OPENAI and _has_key) or not review.strip():
        return {"suggestion":"", "action_type":"", "owner":"", "csat_impact":"", "quote":""}
    sys = ("Extract a single actionable customer suggestion (<=170 chars). "
           "Classify action_type from: Design change, Firmware/Calibration, Instructions/Onboarding, Packaging/Accessories, "
           "Service/Replacement, Policy/Warranty, App/Connectivity. "
           "Estimate csat_impact in -1..+1. Return JSON {suggestion, action_type, csat_impact, quote}.")
    data = verify_json(model_choice, sys, {"review": review[:4000], "action_types": SUG_ACTION_TYPES}, api_key)
    suggestion = str(data.get("suggestion",""))[:170]
    a_type = str(data.get("action_type",""))
    quote = str((data.get("quote") or ""))[:160]
    if quote and quote not in review: quote = ""
    csat = float(data.get("csat_impact", 0.0) or 0.0); csat = max(-1.0, min(1.0, csat))
    owner = OWNER_HINTS.get(a_type, "")
    return {"suggestion": suggestion, "action_type": a_type, "owner": owner, "csat_impact": csat, "quote": quote}

# ========================= Symptom classification =========================
def classify_symptoms(review: str, stars: Optional[float]) -> Tuple[List[str], List[str], str, dict]:
    text = review or ""
    det_items = propose_candidates(text, ALLOWED_DETRACTORS, semantic_threshold)
    del_items = propose_candidates(text, ALLOWED_DELIGHTERS, semantic_threshold)

    audit_local = {"dets":[], "dels":[]}
    def score_side(items: List[dict], polarity: str, allowed_set: set):
        kept=[]
        for it in items:
            raw = it.get("name","").strip()
            if not raw: continue
            mapped = resolve_intent_with_senses(text, raw, allowed_set)
            if not mapped: continue

            # per-label sims/thresholds
            sem_for_label = max(float(it.get("_sem",0.0)), per_label_sem_min(mapped, semantic_threshold)-0.0)

            quotes=[]; has_quote=False; conf=it.get("confidence",0.6)
            if _HAS_OPENAI and _has_key:
                present, vconf, vquotes = cached_verify(review, f"The product exhibits: {mapped}.", model_choice, api_key)
                if not present:
                    present, vconf, vquotes = cached_verify(review, f"The user sentiment corresponds to the symptom: {mapped}.", model_choice, api_key)
                if not present:
                    continue
                quotes = list(vquotes); has_quote = bool(quotes); conf = max(conf, vconf)

            fused = fuse_conf(conf, sem_for_label, has_quote, stars, polarity)
            thr = effective_threshold(per_label_threshold(mapped, strictness), has_quote)
            if fused < thr: continue

            quote = best_quote_for_label(text, mapped, quotes)
            if evidence_required and not quote: 
                continue  # hard evidence gate
            kept.append((mapped, fused, quote))
            audit_local["dets" if polarity=="detractor" else "dels"].append((mapped, fused, sem_for_label, quote))

        # dedupe near-dupes
        out=[]; best_quote=""
        for n,c,q in sorted(kept, key=lambda x: -x[1]):
            n_norm=_normalize_name(n)
            if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(t[0])).ratio()>0.88 for t in out):
                out.append((n,c,q))
                if q and not best_quote:
                    best_quote=q
        return [n for n,_,_ in out[:10]], best_quote

    dets, q1 = score_side(det_items, "detractor", ALLOWED_DET_SET)
    dels, q2 = score_side(del_items, "delighter", ALLOWED_DEL_SET)

    # Safety-net sweep
    def safety_net(current: List[str], allowed_set: set, polarity: str):
        selected=set(current)
        priors=[]
        if _HAS_OPENAI and _has_key and LABEL_INDEX:
            sem = semantic_support(text, LABEL_INDEX, api_key, min_sim=max(0.55, semantic_threshold-0.03))
            priors = [l for l,_ in sorted(sem.items(), key=lambda kv:-kv[1])][:6]
        candidates=[canonicalize(p) for p in priors if p in allowed_set and p not in selected]
        rescued=[]
        for m in candidates:
            present, vconf, vquotes = cached_verify(review, f"The product exhibits: {m}.", model_choice, api_key) if (_HAS_OPENAI and _has_key) else (False,0.0,tuple())
            if present:
                quote = best_quote_for_label(text, m, list(vquotes))
                if quote:
                    rescued.append((m, 0.70, quote))
                    audit_local["dets" if polarity=="detractor" else "dels"].append((m, 0.70, 0.0, quote))
        out = [(x,0.71,"") for x in current] + rescued
        out = list({n:(n,c,q) for n,c,q in out}.values())
        out = sorted(out, key=lambda x:-x[1])[:10]
        any_quote = next((q for _,_,q in out if q), "")
        return [n for n,_,_ in out], any_quote

    dets, _ = safety_net(dets, ALLOWED_DET_SET, "detractor")
    dels, _ = safety_net(dels, ALLOWED_DEL_SET, "delighter")

    voc_quote = q1 or q2 or ""
    return dets, dels, voc_quote, audit_local

# ========================= Write helpers =========================
def write_symptoms(ridx: int, dets: List[str], dels: List[str], overwrite: bool):
    for j in range(1, 11):
        col=f"{APP['SYMPTOM_PREFIX']}{j}"; val = dets[j-1] if j-1 < len(dets) else ""
        if col in df.columns and (overwrite or not str(df.at[ridx, col]).strip()): df.at[ridx, col] = val
    for j in range(11, 21):
        col=f"{APP['SYMPTOM_PREFIX']}{j}"; val = dels[j-11] if j-11 < len(dels) else ""
        if col in df.columns and (overwrite or not str(df.at[ridx, col]).strip()): df.at[ridx, col] = val

def write_safety(ridx: int, flag: bool, quote: str, overwrite: bool):
    if overwrite or not str(df.at[ridx, APP["SAFETY_FLAG_COL"]]).strip():
        df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes" if flag else ""
    if overwrite or not str(df.at[ridx, APP["SAFETY_EVIDENCE_COL"]]).strip():
        df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = quote

def write_reliability(ridx: int, info: dict, overwrite: bool):
    if not info or not info.get("present"): 
        if overwrite:
            for k in [APP["RELIABILITY_FLAG_COL"], APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"],
                      APP["RELIABILITY_SEV_COL"], APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"]]:
                df.at[ridx, k] = ""
        return
    df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
    if overwrite or not str(df.at[ridx, APP["RELIABILITY_MODE_COL"]]).strip(): df.at[ridx, APP["RELIABILITY_MODE_COL"]] = info.get("mode","")
    if overwrite or not str(df.at[ridx, APP["RELIABILITY_COMP_COL"]]).strip(): df.at[ridx, APP["RELIABILITY_COMP_COL"]] = info.get("component","")
    sev = int(info.get("severity", 0) or 0)
    if overwrite or not str(df.at[ridx, APP["RELIABILITY_SEV_COL"]]).strip(): df.at[ridx, APP["RELIABILITY_SEV_COL"]] = sev if sev>=sev_floor else ""
    rpn = int(info.get("rpn", 0) or 0)
    if overwrite or not str(df.at[ridx, APP["RELIABILITY_RPN_COL"]]).strip(): df.at[ridx, APP["RELIABILITY_RPN_COL"]] = rpn if sev>=sev_floor else ""
    if overwrite or not str(df.at[ridx, APP["RELIABILITY_QUOTE_COL"]]).strip(): df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = info.get("quote","")

def write_suggestion_and_csat(ridx: int, sug: dict, overwrite: bool):
    if not sug: return
    if overwrite or not str(df.at[ridx, APP["SUGGESTION_SUM_COL"]]).strip(): df.at[ridx, APP["SUGGESTION_SUM_COL"]] = sug.get("suggestion","")
    if overwrite or not str(df.at[ridx, APP["SUGGESTION_TYPE_COL"]]).strip(): df.at[ridx, APP["SUGGESTION_TYPE_COL"]] = sug.get("action_type","")
    if overwrite or not str(df.at[ridx, APP["SUGGESTION_OWNER_COL"]]).strip(): df.at[ridx, APP["SUGGESTION_OWNER_COL"]] = sug.get("owner","")
    ci = sug.get("csat_impact","")
    if isinstance(ci, float): ci = round(ci, 2)
    if overwrite or not str(df.at[ridx, APP["CSAT_IMPACT_COL"]]).strip(): df.at[ridx, APP["CSAT_IMPACT_COL"]] = ci
    if overwrite or not str(df.at[ridx, APP["VOC_QUOTE_COL"]]).strip(): df.at[ridx, APP["VOC_QUOTE_COL"]] = sug.get("quote","")

# ========================= Savepoint & Schema Guard =========================
SAVEPOINT_PATH = None
def create_savepoint(raw_bytes: bytes) -> str:
    global SAVEPOINT_PATH
    p = os.path.join(tempfile.gettempdir(), f"starwalk_savepoint_{int(time.time())}.xlsx")
    with open(p, "wb") as f: f.write(raw_bytes)
    SAVEPOINT_PATH = p; return p

def restore_savepoint() -> Optional[bytes]:
    if SAVEPOINT_PATH and os.path.exists(SAVEPOINT_PATH):
        with open(SAVEPOINT_PATH, "rb") as f: return f.read()
    return None

def build_header_map(ws):
    return {str(ws.cell(row=1, column=c).value).strip(): c
            for c in range(1, ws.max_column+1)
            if ws.cell(row=1, column=c).value is not None}

def require_headers(header_map: dict, required: list[str]) -> tuple[bool, list[str]]:
    missing=[h for h in required if h not in header_map]
    return (len(missing)==0, missing)

# ========================= UI Header & KPIs =========================
RUN_META = {"run_id": str(int(time.time())), "config_version":"v6.2", "model":"-", "profile": prof}
model_choice = st.sidebar.selectbox("Model", [APP["CHAT_FAST"], "gpt-4o", APP["CHAT_STRICT"], "gpt-5"], index=0)
RUN_META["model"] = model_choice
header(RUN_META)

lengths = verb_series.str.len()
kpis = [("Total reviews", len(df)), ("Avg chars", int(lengths.mean()) if len(lengths) else 0), ("Stars col", "present" if "Star Rating" in df.columns else "—")]
st.markdown("<div class='kpis'>" + "".join([f"<span class='pill'>{k}: <b>{v}</b></span>" for k,v in kpis]) + "</div>", unsafe_allow_html=True)

# ========================= Run controls =========================
c_run1, c_run2, c_run3 = st.columns([1.2, 1, 1])
with c_run1:
    run_all = st.button("✨ Classify ALL reviews", type="primary", use_container_width=True)
with c_run2:
    sample_n = st.number_input("Or sample N rows", min_value=1, max_value=len(df), value=min(60, len(df)))
    run_sample = st.button("Run sample", use_container_width=True)
with c_run3:
    undo = st.button("↩️ Undo last write (restore savepoint)", use_container_width=True)

if undo:
    raw = restore_savepoint()
    if raw:
        st.success("Savepoint restored. Re-upload this restored file if needed.")
    else:
        st.warning("No savepoint found in this session.")

# ========================= Processing =========================
AUDIT_ROWS = []  # will be exported

def chip_html(text: str, quote: str, kind: str):
    cls = "pos" if kind=="delight" else "neg"
    q = (quote or "").replace('"','&quot;')
    return f"<span class='chip {cls}' title=\"{q}\">{text}</span>"

def process_rows(indexes: List[int], overwrite: bool=True):
    progress = st.progress(0.0)
    status = st.empty()
    rescued_total = 0
    safety_ct, reliab_ct = 0, 0
    for k, ridx in enumerate(indexes, start=1):
        text = str(verb_series.iloc[ridx])
        stars = stars_series.iloc[ridx] if "Star Rating" in df.columns else None

        # Normalize non-English (optional safety)
        if _HAS_LANG:
            try:
                if detect(text) != "en" and _HAS_OPENAI and _has_key:
                    # lightweight translate: keep meaning only
                    sys = "Translate to English preserving meaning only. Return JSON {text}."
                    tr = verify_json(model_choice, sys, {"text": text[:2000]}, api_key)
                    text = tr.get("text", text)
            except Exception:
                pass

        # Symptoms with evidence
        dets, dels, voc_q, audit_local = classify_symptoms(text, stars)
        rescued_total += max(0, len(audit_local.get("dets",[]))+len(audit_local.get("dels",[])) - len(dets)-len(dels))
        write_symptoms(ridx, dets, dels, overwrite)

        # Safety
        if safety_detect:
            sflag, squote = detect_safety(text); write_safety(ridx, sflag, squote, overwrite); safety_ct += int(sflag)

        # Reliability
        if enable_reliability:
            rinfo = detect_reliability(text); write_reliability(ridx, rinfo, overwrite); reliab_ct += int(bool(rinfo.get("present")))

        # Suggestion + CSAT
        sdict = extract_suggestion_and_csat(text, stars); write_suggestion_and_csat(ridx, sdict, overwrite)

        # VOC quote
        if voc_q and not str(df.at[ridx, APP["VOC_QUOTE_COL"]]).strip():
            df.at[ridx, APP["VOC_QUOTE_COL"]] = voc_q

        # Audit rows
        for (lab, fused, semv, quote) in audit_local.get("dets", []):
            AUDIT_ROWS.append({"Row": ridx, "Kind":"Detractor", "Label": lab, "Quote": quote, "SemSim": round(semv,3), "Conf": round(fused,3)})
        for (lab, fused, semv, quote) in audit_local.get("dels", []):
            AUDIT_ROWS.append({"Row": ridx, "Kind":"Delighter", "Label": lab, "Quote": quote, "SemSim": round(semv,3), "Conf": round(fused,3)})

        progress.progress(k/len(indexes))
        status.text(f"Processing {k}/{len(indexes)}…")

    status.empty()
    return safety_ct, reliab_ct, rescued_total

if run_all or run_sample:
    idxs = list(range(len(df))) if run_all else random.sample(list(range(len(df))), int(sample_n))
    sct, rct, rescued = process_rows(idxs, overwrite_all)
    st.success(f"Processed {len(idxs)} reviews • Safety flags: {sct} • Reliability flags: {rct} • Rescued labels (safety net): {rescued}")
    st.markdown(f"<div class='toast'>None-missed proof: <b>{rescued}</b> labels added by safety-net with quotes ✅</div>", unsafe_allow_html=True)

# ========================= Mini dashboard =========================
st.divider()
st.markdown("## 📊 Field Snapshot")
det_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(1,11) if f"{APP['SYMPTOM_PREFIX']}{i}" in df.columns]
del_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(11,21) if f"{APP['SYMPTOM_PREFIX']}{i}" in df.columns]
def value_counts_multicol(cols: List[str]) -> pd.DataFrame:
    vals=[]
    for c in cols:
        vals += [str(x).strip() for x in df[c].dropna().tolist() if str(x).strip()]
    if not vals: return pd.DataFrame({"Item":[], "Count":[]})
    ct = Counter(vals); return pd.DataFrame({"Item": list(ct.keys()), "Count": list(ct.values())}).sort_values("Count", ascending=False)

pareto_det = value_counts_multicol(det_cols)
pareto_del = value_counts_multicol(del_cols)
rel_modes = df[df[APP["RELIABILITY_FLAG_COL"]]=="Yes"][APP["RELIABILITY_MODE_COL"]].value_counts().reset_index()
rel_modes.columns = ["Failure Mode","Count"]

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**Top Detractors**")
    st.plotly_chart(px.bar(pareto_det.head(12), x="Item", y="Count", title="Detractors Pareto") if _HAS_PX and len(pareto_det) else None, use_container_width=True)
    if not _HAS_PX or not len(pareto_det): st.table(pareto_det.head(12))
with c2:
    st.markdown("**Top Delighters**")
    st.plotly_chart(px.bar(pareto_del.head(12), x="Item", y="Count", title="Delighters Pareto") if _HAS_PX and len(pareto_del) else None, use_container_width=True)
    if not _HAS_PX or not len(pareto_del): st.table(pareto_del.head(12))
with c3:
    st.markdown("**Reliability Modes**")
    st.plotly_chart(px.bar(rel_modes.head(12), x="Failure Mode", y="Count", title="Reliability Modes") if _HAS_PX and len(rel_modes) else None, use_container_width=True)
    if not _HAS_PX or not len(rel_modes): st.table(rel_modes.head(12))

# ========================= Export =========================
st.divider()
st.markdown("### ⬇️ Export Updated Workbook")

def offer_downloads():
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first."); return
    raw = st.session_state["uploaded_bytes"]
    # create savepoint before we touch anything
    try:
        create_savepoint(raw)
    except Exception:
        pass

    fmt_ok=False; fmt_bytes=None
    if _HAS_OPENPYXL:
        try:
            bio=io.BytesIO(raw); wb=load_workbook(bio)
            data_sheet=APP["DATA_SHEET_DEFAULT"]
            if data_sheet not in wb.sheetnames: data_sheet = wb.sheetnames[0]
            ws=wb[data_sheet]

            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            # ensure required headers exist (create missing extras)
            def col_idx(name):
                if name not in headers:
                    ci = ws.max_column + 1
                    ws.cell(row=1, column=ci, value=name); headers[name]=ci
                return headers[name]

            # schema guard (only for columns that already exist in df)
            must_have = [c for c in SYMPTOM_COLS if c in df.columns]
            ok, missing = True, []
            if not must_have: ok=False; missing.append("Symptom 1–20")
            if not ok:
                st.error(f"Missing headers in workbook: {', '.join(missing)}. Export aborted."); return

            df_reset=df.reset_index(drop=True)
            for r_i, row in df_reset.iterrows():
                excel_row = 2 + r_i
                # symptoms
                for c in SYMPTOM_COLS:
                    ci = col_idx(c); v = row.get(c, None)
                    ws.cell(row=excel_row, column=ci, value=(None if (pd.isna(v) or str(v).strip()=="") else str(v)))
                # added fields
                extra_cols = [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"],
                              APP["RELIABILITY_FLAG_COL"], APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"],
                              APP["RELIABILITY_SEV_COL"], APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"],
                              APP["SUGGESTION_SUM_COL"], APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"],
                              APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]
                for col in extra_cols:
                    ci = col_idx(col); v = row.get(col, "")
                    ws.cell(row=excel_row, column=ci, value=(None if (pd.isna(v) or str(v).strip()=="") else str(v)))

            # Audit Log
            try:
                aud = pd.DataFrame(AUDIT_ROWS) if len(AUDIT_ROWS) else pd.DataFrame(columns=["Row","Kind","Label","Quote","SemSim","Conf"])
                if APP["AUDIT_SHEET"] in wb.sheetnames:
                    del wb[APP["AUDIT_SHEET"]]
                ws_a = wb.create_sheet(APP["AUDIT_SHEET"])
                for j, col in enumerate(aud.columns, start=1):
                    ws_a.cell(row=1, column=j, value=col)
                for i, (_, r) in enumerate(aud.iterrows(), start=2):
                    for j, col in enumerate(aud.columns, start=1):
                        ws_a.cell(row=i, column=j, value=str(r[col]))
            except Exception:
                pass

            # Run Info
            try:
                runinfo = pd.DataFrame([{"Run ID": RUN_META["run_id"], "Config": RUN_META["config_version"], "Model": RUN_META["model"], "Profile": RUN_META["profile"], "Strictness": strictness, "SemanticMin": semantic_threshold, "EvidenceRequired": evidence_required}])
                if APP["RUNINFO_SHEET"] in wb.sheetnames:
                    del wb[APP["RUNINFO_SHEET"]]
                ws_r = wb.create_sheet(APP["RUNINFO_SHEET"])
                for j, col in enumerate(runinfo.columns, start=1):
                    ws_r.cell(row=1, column=j, value=col)
                for i, (_, r) in enumerate(runinfo.iterrows(), start=2):
                    for j, col in enumerate(runinfo.columns, start=1):
                        ws_r.cell(row=i, column=j, value=str(r[col]))
            except Exception:
                pass

            out=io.BytesIO(); wb.save(out); fmt_bytes=out.getvalue(); fmt_ok=True
        except Exception as e:
            st.warning(f"Format-preserving save failed; fallback used. Reason: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET_DEFAULT"], index=False)
            aud = pd.DataFrame(AUDIT_ROWS) if len(AUDIT_ROWS) else pd.DataFrame(columns=["Row","Kind","Label","Quote","SemSim","Conf"])
            aud.to_excel(xlw, sheet_name=APP["AUDIT_SHEET"], index=False)
            runinfo = pd.DataFrame([{"Run ID": RUN_META["run_id"], "Config": RUN_META["config_version"], "Model": RUN_META["model"], "Profile": RUN_META["profile"], "Strictness": strictness, "SemanticMin": semantic_threshold, "EvidenceRequired": evidence_required}])
            runinfo.to_excel(xlw, sheet_name=APP["RUNINFO_SHEET"], index=False)
        basic_bytes = out2.getvalue()
    except Exception as e:
        st.error(f"Basic writer failed: {e}")

    c1, c2 = st.columns(2)
    with c1:
        if fmt_ok and fmt_bytes:
            st.download_button("⬇️ Download updated (preserve formatting)", data=fmt_bytes,
                               file_name="starwalk_qe_formatted.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("Format-preserving version unavailable.")
    with c2:
        if basic_bytes:
            st.download_button("⬇️ Download updated (basic)", data=basic_bytes,
                               file_name="starwalk_qe.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads()
