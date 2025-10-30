# ========================= Star Walk QE v8 — optimized & UX-stable =========================
# Streamlit >= 1.38
# Run: streamlit run app.py

import io, os, re, json, time, random, hashlib
from typing import List, Tuple, Optional, Dict, Any
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# ---------- Optional deps ----------
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

# ---------- App constants ----------
APP = {
    "TITLE": "Star Walk QE — v8",
    "DATA_SHEET": "Star Walk scrubbed verbatims",
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),
    "APPROVED_SHEET": "__StarWalk_Approved",
    "CONFIG_SHEET": "__StarWalk_Config",
    "AUDIT_SHEET": "__Audit_Log",
    "RUNINFO_SHEET": "__Run_Info",
    "EMB_MODEL": "text-embedding-3-small",
    # QE columns:
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
}

random.seed(42); np.random.seed(42)

# ---------- Page & theme ----------
st.set_page_config(page_title=APP["TITLE"], layout="wide", page_icon="🧭")
st.markdown("""
<style>
:root{
  --fg:#0f172a; --muted:#64748b; --bd:#e2e8f0; --tile:#f8fafc; --card:#ffffff; --accent:#2563eb;
  --ok:#059669; --bad:#dc2626; --warn:#d97706;
}
@media (prefers-color-scheme: dark){
  :root{
    --fg:#e5e7eb; --muted:#9ca3af; --bd:#374151; --tile:#0b1220; --card:#0a1020; --accent:#60a5fa;
    --ok:#34d399; --bad:#f87171; --warn:#fbbf24;
  }
}
.block-container{padding-top:.6rem; padding-bottom:1rem; max-width:1480px}
.header,.card{background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:14px; box-shadow:0 6px 16px rgba(2,6,23,.06)}
.title{font-size:clamp(20px,2.6vw,32px); font-weight:800}
.sub{color:var(--muted)}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.pill{display:inline-flex;gap:6px;align-items:center;padding:6px 10px;border-radius:999px;border:1.5px solid var(--bd);background:var(--card);font-weight:700;color:var(--fg)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--bd);background:rgba(96,165,250,.15);font-size:.85rem;color:var(--fg)}
.small{font-size:.92rem;color:var(--muted)}
</style>
""", unsafe_allow_html=True)

def header(meta: dict):
    st.markdown(f"""
    <div class="header">
      <div class="title">{APP["TITLE"]}</div>
      <div class="sub">Fast progressive labeling • stable UI • evidence-driven reliability & safety • audit export.</div>
      <div class="small" style="margin-top:8px">
        <span class="badge">Mode: {meta.get('run_mode','—')}</span>
        <span class="badge">Model: {meta.get('model','—')}</span>
        <span class="badge">Run: {meta.get('run_id','—')}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ---------- Upload ----------
uploaded = st.sidebar.file_uploader("📁 Upload Excel (.xlsx)", type=["xlsx"])
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0); st.session_state["uploaded_bytes"] = uploaded.read(); uploaded.seek(0)

if not uploaded:
    header({"run_mode":"—","model":"—","run_id":"—"})
    st.info("Upload a workbook to begin."); st.stop()

def read_data_sheet(upl):
    try:
        try:
            return pd.read_excel(upl, sheet_name=APP["DATA_SHEET"])
        except ValueError:
            return pd.read_excel(upl)
    except Exception as e:
        st.error(f"Could not read the Excel file: {e}"); st.stop()

df = read_data_sheet(uploaded)
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
stars_series = df.get("Star Rating", pd.Series(dtype=float))

# symptom columns
exp_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
SYMPTOM_COLS = [c for c in exp_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30: SYMPTOM_COLS = df.columns[10:30].tolist()
if not SYMPTOM_COLS: st.error("Couldn't locate Symptom 1–20 columns (K–AD)."); st.stop()
if SYMPTOM_COLS and not all(str(c).lower().startswith("symptom ") for c in SYMPTOM_COLS):
    st.warning("Symptom columns inferred by position; please verify headers.")

# ensure QE columns
for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
            APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
            APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
            APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
    if col not in df.columns: df[col] = ""

# ---------- Allowed lists ----------
import io as _io
def _norm(s: str) -> str: return re.sub(r"[^a-z]+", "", str(s).lower()).strip() if s is not None else ""
def _looks_like_symptom_sheet(name: str) -> bool: return "symptom" in _norm(name)

def _col_score(col: str, want: str) -> int:
    n=_norm(col)
    syn = {
        "delighters":["delight","delighters","pros","positive","positives","likes","good"],
        "detractors":["detract","detractors","cons","negative","negatives","dislikes","bad","issues"],
    }
    return max((1 for t in syn[want] if t in n), default=0)

def _extract_from_df(dfs: pd.DataFrame):
    debug={"strategy":None,"columns":list(dfs.columns)}
    best_del,best_det=None,None
    for c in dfs.columns:
        if _col_score(str(c),"delighters"): best_del=c if best_del is None else best_del
        if _col_score(str(c),"detractors"): best_det=c if best_det is None else best_det
    if best_del is not None or best_det is not None:
        dels = [str(x).strip() for x in dfs.get(best_del, pd.Series(dtype=str)).dropna() if str(x).strip()] if best_del else []
        dets = [str(x).strip() for x in dfs.get(best_det, pd.Series(dtype=str)).dropna() if str(x).strip()] if best_det else []
        if dels or dets: debug.update({"strategy":"fuzzy-headers","best_del":best_del,"best_det":best_det}); return dels, dets, debug
    # type+item
    type_col=item_col=None
    for c in dfs.columns:
        if _norm(c) in {"type","category","class","label"}: type_col=c
        if _norm(c) in {"item","symptom","name","term","entry","value"}: item_col=c
    if type_col and item_col:
        t=dfs[type_col].astype(str).str.strip().str.lower()
        i=dfs[item_col].astype(str).str.strip()
        dels=[x for x in i[t.str.contains("delight|pro|positive", na=False)].dropna() if x]
        dets=[x for x in i[t.str.contains("detract|con|negative", na=False)].dropna() if x]
        if dels or dets: debug.update({"strategy":"type+item"}); return dels, dets, debug
    # first two non-empty
    non=[]
    for c in dfs.columns:
        vals=[str(x).strip() for x in dfs[c].dropna() if str(x).strip()]
        if vals: non.append((c, vals))
        if len(non)>=2: break
    if non: return non[0][1], (non[1][1] if len(non)>1 else []), {"strategy":"first-two-nonempty","picked":[c for c,_ in non[:2]]}
    return [], [], {"strategy":"none","columns":list(dfs.columns)}

def autodetect_sheet(xls: pd.ExcelFile) -> Optional[str]:
    names=xls.sheet_names; cands=[n for n in names if _looks_like_symptom_sheet(n)]
    return (min(cands, key=lambda n: len(_norm(n))) if cands else names[0]) if names else None

raw_bytes = st.session_state.get("uploaded_bytes", b"")
sheet_names=[]
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes))
    sheet_names=_xls_tmp.sheet_names
except Exception:
    pass
auto_sheet = autodetect_sheet(_xls_tmp) if sheet_names else None

st.sidebar.markdown("### 🧾 Symptoms Source")
chosen_sheet = st.sidebar.selectbox("Sheet with Delighters/Detractors",
    options=sheet_names if sheet_names else ["(no sheets)"],
    index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0))

def load_allowed(raw: bytes, sheet: Optional[str]=None):
    meta={"sheet":None,"strategy":None,"note":""}; dels=[]; dets=[]; CFG={"thr":{}, "sense":[]}
    if not raw: return [], [], meta, CFG
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw))
    except Exception as e:
        meta["note"]=f"Could not open Excel: {e}"; return [], [], meta, CFG
    sname = sheet or autodetect_sheet(xls); meta["sheet"]=sname
    try:
        s = pd.read_excel(xls, sheet_name=sname)
    except Exception as e:
        meta["note"]=f"Could not read sheet '{sname}': {e}"; return [], [], meta, CFG
    d1, d2, info = _extract_from_df(s); meta.update(info); dels+=d1; dets+=d2
    # approvals
    try:
        if APP["APPROVED_SHEET"] in xls.sheet_names:
            hdf = pd.read_excel(xls, sheet_name=APP["APPROVED_SHEET"])
            if "Approved Delighters" in hdf.columns: dels += [str(x).strip() for x in hdf["Approved Delighters"].dropna() if str(x).strip()]
            if "Approved Detractors" in hdf.columns: dets += [str(x).strip() for x in hdf["Approved Detractors"].dropna() if str(x).strip()]
    except Exception: pass
    # config optional
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
                        "cue": str(row[0]), "default": str(row[1]) if pd.notna(row[1]) else "",
                        "route_rx": str(row[2]) if pd.notna(row[2]) else "",
                        "route_label": str(row[3]) if pd.notna(row[3]) else "",
                        "avoid": str(row[4]) if pd.notna(row[4]) else "",
                    })
        except Exception: pass
    # dedupe
    dels=list(dict.fromkeys([x for x in dels if x]))
    dets=list(dict.fromkeys([x for x in dets if x]))
    return dels, dets, meta, CFG

ALLOWED_DEL, ALLOWED_DET, SYM_META, CFG = load_allowed(raw_bytes, chosen_sheet if sheet_names else None)
ALLOWED_DEL_SET, ALLOWED_DET_SET = set(ALLOWED_DEL), set(ALLOWED_DET)
CFG_THR = CFG.get("thr", {})
CFG_SENSE = CFG.get("sense", [])

# ---------- Controls ----------
st.sidebar.markdown("### ⚡ Run Mode")
run_mode = st.sidebar.radio("Speed vs depth", ["Quick Scan (instant)", "Standard", "Deep Audit (strict)"], index=1)
strictness = st.sidebar.slider("Strictness (higher=fewer)", 0.55, 0.95, 0.62, 0.01)
semantic_threshold = st.sidebar.slider("Min semantic similarity", 0.50, 0.90, 0.58, 0.01)
evidence_required = st.sidebar.checkbox("Require evidence quotes", value=True)

st.sidebar.markdown("### 🧩 Reliability & Safety")
enable_reliability = st.sidebar.checkbox("Detect Reliability Failures", value=True)
sev_floor = st.sidebar.slider("Min severity to record", 1, 5, 2)
safety_detect = st.sidebar.checkbox("Detect Safety Risk", value=True)
safety_strict = st.sidebar.slider("Safety strictness", 0.55, 0.95, 0.60, 0.01)

viz_mode = st.sidebar.radio("Visualization mode", ["Charts", "Tables"], horizontal=True, index=0)
chunk_n = st.sidebar.slider("Process chunk size", 10, 500, 120, 10)

api_key = (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
_HAS_KEY = bool(api_key)
if not _HAS_OPENAI: st.warning("Install `openai` to enable AI features.")
if _HAS_OPENAI and not _HAS_KEY: st.warning("Set OPENAI_API_KEY to enable AI features.")

SAFE_MODELS = ["gpt-4o-mini","gpt-4o","gpt-4.1"]
def resolve_model(sel: str) -> str:
    if sel in SAFE_MODELS: return sel
    st.toast(f"Model '{sel}' not supported; using 'gpt-4o-mini'.", icon="⚠️")
    return "gpt-4o-mini"
model_choice = resolve_model(st.sidebar.selectbox("Model", ["gpt-4o-mini","gpt-4o","gpt-4.1","gpt-5"], index=0))

RUN_META = {"run_id": str(int(time.time())), "model": model_choice, "run_mode": run_mode}
header(RUN_META)

# ---------- Session state ----------
st.session_state.setdefault("feed_events", [])
st.session_state.setdefault("quick_labels", {})  # ridx -> {"dets":[...], "dels":[...]}
st.session_state.setdefault("RUN_CANCELLED", False)
st.session_state.setdefault("RUN_PAUSED", False)

# ---------- Helpers & lexicons ----------
def _normalize_name(s: str) -> str: return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
ALIAS_CANON = {
    "initial difficulty":"Learning curve",
    "hard to learn":"Learning curve",
    "setup difficulty":"Learning curve",
    "noisy startup":"Startup noise",
    "too loud":"Loud",
    "vacuum sucks":"Poor performance",  # idiom fix
}
def canonicalize(name: str) -> str:
    nn=(name or "").strip(); base=_normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k)==base: return v
    return nn

FAILURE_MODES = {
    "Won’t power on":["won't turn on","no power","dead on arrival","doesn't start","won't start"],
    "Intermittent power":["cuts out","turns off randomly","shuts off","stops mid use"],
    "Motor stalls / weak":["motor stalls","stalls","weak suction","loses power","bogs down","poor performance","performance issue"],
    "Battery won’t hold charge":["battery dies fast","won't charge","doesn't hold charge","battery failure"],
    "Overheats / thermal shutoff":["overheat","too hot","thermal","shuts off hot"],
    "Leaks / water ingress":["leaks","water in","condensation","moisture","drips"],
    "Sensor / indicator faulty":["indicator wrong","sensor error","light stuck","filter light stuck"],
    "Controls unresponsive":["button not working","controls unresponsive","switch broken","dial broken"],
    "Charging/base dock issue":["dock issue","charging base","won't dock","contacts not"],
}
COMPONENT_HINTS = {
    "Power supply / PCB":["no power","won't turn on","short","fuse","pcb","board"],
    "Battery pack":["battery","charge","charging","won't charge","holds charge"],
    "Motor / impeller":["motor","stall","suction","rpm","whine","grind","performance"],
    "Thermal protection":["overheat","thermal","hot","heat"],
    "Seals / water path":["leak","water","condensation","seal","o-ring"],
    "UI / Buttons":["button","switch","dial","control"],
    "Dock / Contacts":["dock","contacts","charging base","pins"],
    "Sensors / Indicators":["indicator","sensor","light","led"],
}
SUG_ACTION_TYPES = ["Design change","Firmware/Calibration","Instructions/Onboarding","Packaging/Accessories","Service/Replacement","Policy/Warranty","App/Connectivity"]
OWNER_HINTS = {"Design change":"PD","Firmware/Calibration":"PD","Instructions/Onboarding":"CX","Packaging/Accessories":"NPI","Service/Replacement":"CX","Policy/Warranty":"CX","App/Connectivity":"PD"}

SAFETY_CUES_POS = ["burn","smoke","fire","flame","melt","shock","sparks","short circuit","overheat","explode","explosion","toxic","hazardous","dangerous","injury","cut","laceration","electric shock","caught fire"]
SAFETY_CUES_NEG = ["no fire","not dangerous","safe","safely","no risk"]

def with_retry(fn, *, tries=3, base=0.5, factor=1.8, jitter=0.2):
    for i in range(tries):
        try: return fn()
        except Exception:
            if i==tries-1: raise
            time.sleep(base*(factor**i) + random.uniform(0, jitter))

def _short_sig(text: str) -> str:
    return hashlib.sha1((text or "").encode()).hexdigest()[:16]

def _canon_text(t: str) -> str:
    t=(t or "").lower(); t=re.sub(r"[^a-z0-9\s]", " ", t); t=re.sub(r"\s+"," ", t).strip(); return t

def _sig32(t: str) -> str:
    return hashlib.md5(_canon_text(t).encode()).hexdigest()[:8]

@lru_cache(maxsize=100_000)
def _similar_key(sig: str, text: str) -> str:
    return f"{sig[:4]}:{len(_canon_text(text))//80}"

# ---------- OpenAI helpers ----------
def safe_user_payload(obj: dict, max_chars=5800):
    s=json.dumps(obj)
    if len(s)<=max_chars: return obj
    if "review" in obj: obj["review"]=str(obj["review"])[:max(2000, max_chars//2)]
    return obj

def verify_json(model: str, sys_prompt: str, user_obj: dict, api_key: str) -> dict:
    if not (_HAS_OPENAI and _HAS_KEY): return {}
    client = OpenAI(api_key=api_key)
    payload = safe_user_payload(user_obj)
    # primary
    try:
        out = with_retry(lambda: client.chat.completions.create(
            model=model, temperature=0.0,
            messages=[{"role":"system","content":sys_prompt},
                      {"role":"user","content":json.dumps(payload)}],
            response_format={"type":"json_object"},
        ))
        content = (out.choices[0].message.content or "{}")
        return json.loads(content)
    except Exception:
        pass
    # fallback
    try:
        out = with_retry(lambda: client.chat.completions.create(
            model=model, temperature=0.0,
            messages=[{"role":"system","content":sys_prompt+" Respond ONLY with valid JSON."},
                      {"role":"user","content":json.dumps(payload)}],
        ))
        content = (out.choices[0].message.content or "{}")
        m = re.search(r"\{.*\}", content, re.S)
        if m: return json.loads(m.group(0))
    except Exception:
        pass
    return {}

def openai_embed(texts: List[str], api_key: str) -> np.ndarray:
    if not (_HAS_OPENAI and _HAS_KEY) or not texts: return np.zeros((0,1536), dtype="float32")
    client = OpenAI(api_key=api_key)
    out = with_retry(lambda: client.embeddings.create(model=APP["EMB_MODEL"], input=texts[:256]))
    M = np.array([d.embedding for d in out.data], dtype="float32")
    M /= (np.linalg.norm(M, axis=1, keepdims=True)+1e-8)
    return M

# ---------- Embeddings index ----------
@st.cache_resource(show_spinner=False)
def build_label_index(labels: List[str], _api_key: str):
    if not (_HAS_OPENAI and _HAS_KEY and labels): return None
    texts = list(dict.fromkeys([canonicalize(x) for x in labels if x]))
    if not texts: return None
    M = openai_embed(texts, _api_key)
    return (texts, M)

def _ngram_candidates(text: str, max_ngrams: int = 256) -> List[str]:
    ws = re.findall(r"[a-z0-9]{3,}", (text or "").lower()); ngrams=[]; seen=set()
    for n in (1,2,3,4,5):
        for i in range(len(ws)-n+1):
            s=" ".join(ws[i:i+n])
            if len(s)>=4 and s not in seen:
                ngrams.append(s); seen.add(s)
                if len(ngrams)>=max_ngrams: break
        if len(ngrams)>=max_ngrams: break
    return ngrams

@st.cache_resource(show_spinner=False)
def cached_label_index(ALLOWED_DEL: List[str], ALLOWED_DET: List[str], _api_key: str):
    return build_label_index(ALLOWED_DEL + ALLOWED_DET, _api_key)

LABEL_INDEX = cached_label_index(ALLOWED_DEL, ALLOWED_DET, api_key)

def semantic_support(review: str, label_index, _api_key: str, min_sim: float) -> Dict[str, float]:
    if (not label_index) or (not review): return {}
    labels, L = label_index; cands = _ngram_candidates(review)
    if not cands: return {}
    X = openai_embed(cands, _api_key)
    if X.shape[0]==0: return {}
    S = X @ L.T; best_idx = S.argmax(axis=1); best_sim = S[np.arange(len(cands)), best_idx]
    buckets={}
    for j, sim in zip(best_idx, best_sim):
        if sim >= min_sim:
            lab = labels[int(j)]
            if sim > buckets.get(lab, 0.0): buckets[lab] = float(sim)
    return buckets

def per_label_threshold(label: str, base: float) -> float:
    d = CFG_THR.get(label)
    if d: return max(0.55, min(0.90, float(d.get("min_conf", base))))
    return base

def per_label_sem_min(label: str, base: float) -> float:
    d = CFG_THR.get(label)
    if d: return max(0.50, min(0.90, float(d.get("sem_min", base))))
    return base

def resolve_intent_with_senses(text: str, candidate_label: str, allowed_set: set) -> Optional[str]:
    t = " " + (text or "").lower() + " "
    t = t.replace(" vacuum sucks ", " poor performance ")
    cand = canonicalize(candidate_label)
    if cand in allowed_set: return cand
    for r in CFG_SENSE:
        cue=r.get("cue",""); avoid=r.get("avoid",""); route_rx=r.get("route_rx","")
        try:
            if cue and re.search(cue, t, re.I):
                if avoid and re.search(avoid, t, re.I): continue
                if route_rx and r.get("route_label") in allowed_set:
                    if re.search(route_rx, t, re.I): return r["route_label"]
                d=r.get("default",""); 
                if d in allowed_set: return d
        except re.error:
            continue
    return cand if cand in allowed_set else None

# ---------- Evidence & fusion ----------
def best_quote_for_label(text: str, label: str, llm_quotes: List[str]) -> str:
    keys = [k for k in re.findall(r"[a-z0-9]{3,}", label.lower()) if len(k)>=3]
    t=text or ""
    for k in sorted(set(keys), key=len, reverse=True):
        m=re.search(rf".{{0,60}}\b{re.escape(k)}\b.{{0,60}}", t, re.I)
        if m: return m.group(0).strip()[:160]
    for q in llm_quotes:
        if q and q in t: return q[:160]
    return ""

def fuse_conf(llm_conf: float, sem_sim: float, has_quote: bool, stars: Optional[float], polarity: str) -> float:
    llm=max(0.0, min(1.0, float(llm_conf or 0))); sem=max(0.0, min(1.0, float(sem_sim or 0)))
    prior=0.0
    if stars is not None and not pd.isna(stars):
        if polarity=="delighter": prior = 0.07 if stars>=4.0 else (-0.07 if stars<=2.0 else 0.0)
        else: prior = 0.07 if stars<=2.0 else (-0.07 if stars>=4.0 else 0.0)
    evp=0.10 if has_quote else 0.0
    return max(0.0, min(1.0, 0.50*llm + 0.30*sem + evp + prior))

def effective_threshold(base_thr: float, has_quote: bool) -> float:
    thr=float(base_thr) - (0.08 if has_quote else 0.0)
    return max(0.55, min(0.90, thr))

# ---------- Propose candidates ----------
def propose_candidates(review: str, allowed: List[str], sem_min: float) -> List[dict]:
    sem_supp={}
    if _HAS_OPENAI and _HAS_KEY and LABEL_INDEX:
        try: sem_supp = semantic_support(review, LABEL_INDEX, api_key, min_sim=sem_min)
        except Exception: sem_supp={}
    items=[]
    if _HAS_OPENAI and _HAS_KEY and run_mode != "Quick Scan (instant)":
        sys = 'Return JSON {"labels":[{"name":"", "confidence":0.0}]}. Choose <=10 from allowed_list; omit if unsure.'
        user = {"review": review[:4000], "allowed_list": allowed[:200]}
        data = verify_json(model_choice, sys, user, api_key)
        items = data.get("labels", []) or []
        for i,x in enumerate(items):
            if isinstance(x, str): items[i]={"name":x,"confidence":0.6}
    # lexical fallback & quick
    if not items:
        text = " "+review.lower()+" "
        for a in allowed:
            ac=canonicalize(a)
            toks=[t for t in _normalize_name(ac).split() if len(t)>=3]
            if len(toks)>=2 and all(re.search(rf"\b{re.escape(t)}\b", text) for t in toks[:2]):
                items.append({"name":ac,"confidence":0.62})
            elif len(toks)==1 and re.search(rf"\b{re.escape(toks[0])}\b", text):
                items.append({"name":ac,"confidence":0.60})
    for it in items: it["_sem"]=float(sem_supp.get(canonicalize(it.get("name","")), 0.0))
    return items

# ---------- Safety & Reliability ----------
@lru_cache(maxsize=200_000)
def cached_verify(sig: str, claim: str, model: str, api_key: str) -> tuple[bool, float, tuple[str,...]]:
    if not (_HAS_OPENAI and _HAS_KEY): return (False, 0.0, tuple())
    client = OpenAI(api_key=api_key)
    sys = "Only mark present if you can paste exact quotes. Return JSON: {present:bool, confidence:0..1, quotes:list[str]}."
    data = verify_json(model, sys, {"review_sig": sig, "claim": claim}, api_key)
    quotes = tuple(q for q in (data.get("quotes",[]) or []) if isinstance(q,str) and q)
    return (bool(data.get("present", False) and len(quotes)>0), float(data.get("confidence", 0.0)), quotes[:4])

def detect_safety(review: str) -> Tuple[bool, str]:
    if not review.strip(): return False, ""
    pos_hit = any(re.search(rf"\b{re.escape(w)}\b", review, re.I) for w in SAFETY_CUES_POS)
    neg_hit = any(re.search(rf"\b{re.escape(w)}\b", review, re.I) for w in SAFETY_CUES_NEG)
    flag=False; quote=""
    if pos_hit and not neg_hit:
        for w in SAFETY_CUES_POS:
            m=re.search(rf".{{0,60}}\b{re.escape(w)}\b.{{0,60}}", review, re.I)
            if m: quote=m.group(0).strip()[:160]; flag=True; break
    if _HAS_OPENAI and _HAS_KEY and run_mode != "Quick Scan (instant)":
        present, conf, quotes = cached_verify(_short_sig(review), "The review reports a safety-related risk.", model_choice, api_key)
        if present: flag=True; quote = (list(quotes)[0] if quotes else quote)
        elif flag: flag = (conf > safety_strict)
    return flag, quote

def detect_reliability(review: str) -> dict:
    if not review.strip(): return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}
    # lexical scout
    mode_scores=defaultdict(int); mode_quote=""
    for mode, cues in FAILURE_MODES.items():
        for c in cues:
            if re.search(rf"\b{re.escape(c)}\b", review, re.I):
                mode_scores[mode]+=1
                if not mode_quote:
                    m=re.search(rf".{{0,60}}\b{re.escape(c)}\b.{{0,60}}", review, re.I)
                    if m: mode_quote=m.group(0).strip()[:160]
    if run_mode == "Quick Scan (instant)" or not (_HAS_OPENAI and _HAS_KEY):
        if not mode_scores: return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}
        top_mode=max(mode_scores.items(), key=lambda kv: kv[1])[0]
        comp=""
        for k,hits in COMPONENT_HINTS.items():
            if any(re.search(rf"\b{re.escape(h)}\b", review, re.I) for h in hits): comp=k; break
        sev = 3 if re.search(r"\b(overheat|fire|shock|smoke)\b", review, re.I) else 2
        rpn = sev * (2 if mode_scores[top_mode]>=2 else 1)
        return {"present": True, "mode":top_mode, "component":comp, "severity":sev, "rpn":rpn, "quote":mode_quote}
    sys=("From the review, identify a reliability failure if present. "
         "Return JSON {present,bool, mode,str, component,str, severity,int(1-5), rpn,int, quotes:list[str]}.")
    data = verify_json(model_choice, sys, {"review": review[:4000], "failure_modes": list(FAILURE_MODES.keys()), "component_hints": list(COMPONENT_HINTS.keys())}, api_key)
    present=bool(data.get("present", False))
    mode=str(data.get("mode",""))[:80]; component=str(data.get("component",""))[:80]
    severity=int(data.get("severity", 0) or 0); rpn=int(data.get("rpn", 0) or (severity*2))
    quotes=[q for q in (data.get("quotes",[]) or []) if isinstance(q,str) and q and (q in review)]
    if present and quotes and severity>=sev_floor:
        return {"present": True, "mode":mode, "component":component, "severity":severity, "rpn":rpn, "quote":quotes[0][:160]}
    return {"present": False, "mode":"", "component":"", "severity":"", "rpn":"", "quote":""}

def extract_suggestion_and_csat(review: str, stars: Optional[float]) -> dict:
    if not (_HAS_OPENAI and _HAS_KEY) or not review.strip() or run_mode=="Quick Scan (instant)":
        return {"suggestion":"", "action_type":"", "owner":"", "csat_impact":"", "quote":""}
    sys=("Extract one actionable customer suggestion (<=170 chars). "
         "Classify action_type from: Design change, Firmware/Calibration, Instructions/Onboarding, Packaging/Accessories, "
         "Service/Replacement, Policy/Warranty, App/Connectivity. "
         "Estimate csat_impact in -1..+1. Return JSON {suggestion, action_type, csat_impact, quote}.")
    data=verify_json(model_choice, sys, {"review": review[:4000], "action_types": SUG_ACTION_TYPES}, api_key)
    suggestion=str(data.get("suggestion",""))[:170]
    a_type=str(data.get("action_type","")); quote=str((data.get("quote") or ""))[:160]
    if quote and quote not in review: quote=""
    csat=float(data.get("csat_impact", 0.0) or 0.0); csat=max(-1.0, min(1.0, csat))
    owner=OWNER_HINTS.get(a_type, "")
    return {"suggestion":suggestion,"action_type":a_type,"owner":owner,"csat_impact":round(csat,2),"quote":quote}

# ---------- Classification ----------
def fuse_and_keep(text: str, items: List[dict], polarity: str, allowed_set: set, stars: Optional[float]) -> Tuple[List[str], List[Tuple[str,float,float,str]]]:
    kept=[]
    for it in items:
        raw=it.get("name","").strip()
        if not raw: continue
        mapped=resolve_intent_with_senses(text, raw, allowed_set)
        if not mapped: continue
        sem_for_label=max(float(it.get("_sem",0.0)), per_label_sem_min(mapped, semantic_threshold)-0.0)
        conf=it.get("confidence",0.6)
        quotes=[]; has_quote=False
        if _HAS_OPENAI and _HAS_KEY and run_mode!="Quick Scan (instant)":
            present,vconf,vquotes=cached_verify(_short_sig(text), f"The product sentiment reflects: {mapped}.", model_choice, api_key)
            if not present: 
                present,vconf,vquotes=cached_verify(_short_sig(text), f"The user complaint/praise maps to: {mapped}.", model_choice, api_key)
            if not present: 
                continue
            quotes=list(vquotes); has_quote=bool(quotes); conf=max(conf, vconf)
        fused=fuse_conf(conf, sem_for_label, has_quote, stars, polarity)
        thr=effective_threshold(per_label_threshold(mapped, strictness), has_quote)
        if fused < thr: continue
        quote=best_quote_for_label(text, mapped, quotes)
        if evidence_required and not (quote and quote.strip()): continue
        kept.append((mapped,fused,sem_for_label,quote))
    final=[]
    for n,c,s,q in sorted(kept, key=lambda x:-x[1]):
        n_norm=_normalize_name(n)
        if not any(_normalize_name(t[0])==n_norm for t in final):
            final.append((n,c,s,q))
    names=[n for n,_,_,_ in final[:10]]
    return names, final

def classify_symptoms(review: str, stars: Optional[float]) -> Tuple[List[str], List[str], str, List[Dict[str,Any]]]:
    text = review or ""
    det_items = propose_candidates(text, list(ALLOWED_DET_SET), semantic_threshold if run_mode!="Quick Scan (instant)" else 0.0)
    del_items = propose_candidates(text, list(ALLOWED_DEL_SET), semantic_threshold if run_mode!="Quick Scan (instant)" else 0.0)

    dets, det_aud = fuse_and_keep(text, det_items, "detractor", ALLOWED_DET_SET, stars)
    dels, del_aud = fuse_and_keep(text, del_items, "delighter", ALLOWED_DEL_SET, stars)

    voc=""
    for _,_,_,q in det_aud + del_aud:
        if q: voc=q; break

    audit=[]
    for n,c,s,q in det_aud: audit.append({"Kind":"Detractor","Label":n,"Conf":round(c,3),"SemSim":round(s,3),"Quote":q})
    for n,c,s,q in del_aud: audit.append({"Kind":"Delighter","Label":n,"Conf":round(c,3),"SemSim":round(s,3),"Quote":q})
    return dets, dels, voc, audit

# ---------- Writes ----------
def write_symptoms(ridx: int, dets: List[str], dels: List[str]):
    for j in range(1, 11):
        col=f"{APP['SYMPTOM_PREFIX']}{j}"; val = dets[j-1] if j-1 < len(dets) else ""
        if col in df.columns: df.at[ridx, col]=val
    for j in range(11, 21):
        col=f"{APP['SYMPTOM_PREFIX']}{j}"; val = dels[j-11] if j-11 < len(dels) else ""
        if col in df.columns: df.at[ridx, col]=val

def write_safety(ridx: int, flag: bool, quote: str):
    df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes" if flag else ""
    df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = quote

def write_reliability(ridx: int, info: dict):
    cols=[APP["RELIABILITY_FLAG_COL"], APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"],
          APP["RELIABILITY_SEV_COL"], APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"]]
    if not info or not info.get("present"):
        for c in cols: df.at[ridx, c] = ""
        return
    df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
    df.at[ridx, APP["RELIABILITY_MODE_COL"]] = info.get("mode","")
    df.at[ridx, APP["RELIABILITY_COMP_COL"]] = info.get("component","")
    df.at[ridx, APP["RELIABILITY_SEV_COL"]] = int(info.get("severity", 0) or 0) or ""
    df.at[ridx, APP["RELIABILITY_RPN_COL"]] = int(info.get("rpn", 0) or 0) or ""
    df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = info.get("quote","")

def write_suggestion_and_csat(ridx: int, sug: dict):
    if not sug: return
    df.at[ridx, APP["SUGGESTION_SUM_COL"]] = sug.get("suggestion","")
    df.at[ridx, APP["SUGGESTION_TYPE_COL"]] = sug.get("action_type","")
    df.at[ridx, APP["SUGGESTION_OWNER_COL"]] = sug.get("owner","")
    ci=sug.get("csat_impact","")
    if isinstance(ci,float): ci=round(ci,2)
    df.at[ridx, APP["CSAT_IMPACT_COL"]] = ci
    df.at[ridx, APP["VOC_QUOTE_COL"]] = sug.get("quote","")

# ---------- Dedupe clusters ----------
def build_clusters(indexes: list[int]) -> dict[str, list[int]]:
    groups=defaultdict(list)
    for ridx in indexes:
        txt=str(verb_series.iloc[ridx]); sig=_sig32(txt); key=_similar_key(sig, txt)
        groups[key].append(ridx)
    return groups

# ---------- Live feed ----------
def _emit_event(row, phase, dets, dels, extra=""):
    st.session_state["feed_events"].append({"t": int(time.time()), "row": row, "phase": phase, "dets": dets, "dels": dels, "x": extra})
    st.session_state["feed_events"] = st.session_state["feed_events"][-200:]

# ---------- Process rows (per-row try/except, pause/cancel) ----------
AUDIT_ROWS: List[dict] = []

def is_easy_pass(dets, dels, safety_flag, rinfo_present):
    return (len(dets) + len(dels) >= 4) and (not safety_flag) and (not rinfo_present)

def process_rows(indexes: List[int]):
    progress = st.progress(0.0); status = st.empty()
    safety_ct = reliab_ct = 0; total=len(indexes)

    for k, ridx in enumerate(indexes, start=1):
        if st.session_state.get("RUN_CANCELLED"): st.warning("Run cancelled by user."); break
        while st.session_state.get("RUN_PAUSED"):
            time.sleep(0.25); st.experimental_rerun()

        try:
            text = str(verb_series.iloc[ridx]); stars = stars_series.iloc[ridx] if "Star Rating" in df.columns else None

            if run_mode == "Quick Scan (instant)":
                det_items=propose_candidates(text, list(ALLOWED_DET_SET), sem_min=0.0)
                del_items=propose_candidates(text, list(ALLOWED_DEL_SET), sem_min=0.0)
                dets=[canonicalize(i.get("name","")) for i in det_items][:5]
                dels=[canonicalize(i.get("name","")) for i in del_items][:5]
                st.session_state["quick_labels"][ridx]={"dets":dets,"dels":dels}
                _emit_event(ridx, "quick", len(dets), len(dels))
            else:
                dets, dels, voc_q, audit_local = classify_symptoms(text, stars)
                write_symptoms(ridx, dets, dels)

                sflag=False; rinfo={"present": False}
                if safety_detect:
                    sflag, squote=detect_safety(text); write_safety(ridx, sflag, squote); safety_ct += int(sflag)
                if enable_reliability:
                    rinfo=detect_reliability(text); write_reliability(ridx, rinfo); reliab_ct += int(bool(rinfo.get("present")))

                sug=extract_suggestion_and_csat(text, stars); write_suggestion_and_csat(ridx, sug)

                for r in audit_local: r["Row"]=ridx; AUDIT_ROWS.append(r)

                # skip deep for easy passes (already handled: we only run one pass)
                _emit_event(ridx, "standard" if run_mode=="Standard" else "deep", len(dets), len(dels),
                            extra=("S+" if sflag else "") + (" R+" if rinfo.get("present") else ""))

        except Exception as e:
            st.warning(f"Row {ridx}: skipped due to error: {e}")
            _emit_event(ridx, "error", 0, 0, str(e))

        progress.progress(k/max(1,total)); status.text(f"Processing {k}/{total} • Mode: {run_mode}")

    status.empty()
    return safety_ct, reliab_ct

# ---------- KPI + actions ----------
lengths = verb_series.str.len()
kpis = [("Total reviews", len(df)), ("Avg chars", int(lengths.mean()) if len(lengths) else 0),
        ("Stars col", "present" if "Star Rating" in df.columns else "—")]
st.markdown("<div class='kpis'>" + "".join([f"<span class='pill'>{k}: <b>{v}</b></span>" for k,v in kpis]) + "</div>", unsafe_allow_html=True)

colx,coly,colz = st.columns([1,1,1])
with colx:
    if st.button("⏸️ Pause"): st.session_state["RUN_PAUSED"]=True
with coly:
    if st.button("▶️ Resume"): st.session_state["RUN_PAUSED"]=False
with colz:
    if st.button("🛑 Cancel run", type="secondary"): st.session_state["RUN_CANCELLED"]=True

def estimate_eta_rows(n, mode):
    per = 0.35 if mode=="Quick Scan (instant)" else (0.9 if mode=="Standard" else 1.6)
    t=int(n*per); m,s=divmod(t,60); return f"~{m}m {s}s" if m else f"~{s}s"
st.info(f"Mode: **{run_mode}** • Estimated time for selected rows: {estimate_eta_rows(len(df), run_mode)}")

run_all = st.button("✨ Run on ALL", type="primary", use_container_width=True)
c1,c2 = st.columns([1,1])
with c1:
    sample_n = st.number_input("Sample N", min_value=1, max_value=len(df), value=min(50, len(df)))
with c2:
    run_sample = st.button("Run Sample")

# Live feed
with st.expander("📡 Live Feed", expanded=True):
    events = st.session_state.get("feed_events", [])[-60:]
    if events:
        f = pd.DataFrame(events)
        f["When"]=pd.to_datetime(f["t"], unit="s").dt.strftime("%H:%M:%S")
        f["Phase"]=f["phase"].map({"quick":"⚡ Quick","standard":"⚙️ Std","deep":"🔎 Deep","error":"⛔ Error"}).fillna(f["phase"])
        st.dataframe(f[["When","row","Phase","dets","dels","x"]], use_container_width=True, height=220)
    else:
        st.caption("Run to see live updates.")

def run_chunked(indexes: List[int], chunk: int):
    safety_total = reli_total = 0
    # dedupe clusters first to skip near-duplicates
    clusters = build_clusters(indexes)
    exemplars = [v[0] for v in clusters.values()]
    st.caption(f"Dedupe: {len(indexes)} → {len(exemplars)} exemplars")

    for i in range(0, len(exemplars), chunk):
        if st.session_state.get("RUN_CANCELLED"): break
        sub = exemplars[i:i+chunk]
        sct, rct = process_rows(sub)
        safety_total += sct; reli_total += rct
        # propagate exemplar results to siblings
        for key, rows in clusters.items():
            ex = rows[0]
            if ex in sub and len(rows)>1:
                for sib in rows[1:]:
                    for j in range(1, 21):
                        src=f"Symptom {j}"
                        if src in df.columns: df.at[sib, src] = df.at[ex, src]
                    for c in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                              APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                              APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                              APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
                        if c in df.columns: df.at[sib, c] = df.at[ex, c]
        st.success(f"Chunk {i//chunk + 1}: {len(sub)} exemplars • Safety+{sct} • Reliab+{rct}")
    st.info("Done.")
    return safety_total, reli_total

if run_all or run_sample:
    idxs = list(range(len(df))) if run_all else random.sample(list(range(len(df))), int(sample_n))
    st.session_state["RUN_CANCELLED"]=False
    sct, rct = run_chunked(idxs, chunk_n)
    st.success(f"Processed • Safety flags: {sct} • Reliability flags: {rct}")

if run_mode == "Quick Scan (instant)":
    if st.button("⬆️ Apply Quick Scan results to sheet"):
        for ridx, packs in st.session_state["quick_labels"].items():
            write_symptoms(ridx, packs.get("dets", []), packs.get("dels", []))
        st.success(f"Applied {len(st.session_state['quick_labels'])} quick rows to sheet.")

# ---------- Snapshot (stable) ----------
CHARTS = {"det": st.container(), "del": st.container(), "rel": st.container()}

# reserve space (skeleton)
with CHARTS["det"]:
    st.markdown("**Top Detractors**"); st.markdown("<div style='height:340px'></div>", unsafe_allow_html=True)
with CHARTS["del"]:
    st.markdown("**Top Delighters**"); st.markdown("<div style='height:340px'></div>", unsafe_allow_html=True)
with CHARTS["rel"]:
    st.markdown("**Reliability Modes**"); st.markdown("<div style='height:340px'></div>", unsafe_allow_html=True)

def counts_from_symptom_cols(df: pd.DataFrame, cols: list[str], label: str):
    vals=[]
    for c in cols:
        if c in df.columns:
            vals += [str(x).strip() for x in df[c].dropna() if str(x).strip()]
    if not vals: return pd.DataFrame({label:[], "Count":[]})
    ct=Counter(vals)
    out=pd.DataFrame({label:list(ct.keys()), "Count":list(ct.values())}).sort_values(["Count",label], ascending=[False,True])
    return out.head(12)

def render_bar_stable(container, df_plot, x, y, title):
    with container:
        if df_plot is None or df_plot.empty:
            st.info("No data yet."); return
        if viz_mode=="Tables" or not _HAS_PX:
            st.dataframe(df_plot.head(12), use_container_width=True, height=320); return
        try:
            fig = px.bar(df_plot, x=x, y=y, title=title, text=y)
            fig.update_traces(textposition="outside", cliponaxis=False)
            fig.update_layout(height=320, margin=dict(l=10,r=10,t=40,b=10),
                              xaxis_title=None, yaxis_title=None, showlegend=False)
            fig.update_layout(xaxis={"categoryorder":"total descending"})
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            st.dataframe(df_plot.head(12), use_container_width=True, height=320)

det_cols=[f"Symptom {i}" for i in range(1,11) if f"Symptom {i}" in df.columns]
del_cols=[f"Symptom {i}" for i in range(11,21) if f"Symptom {i}" in df.columns]
df_det = counts_from_symptom_cols(df, det_cols, "Detractor") if det_cols else pd.DataFrame({"Detractor":[], "Count":[]})
df_del = counts_from_symptom_cols(df, del_cols, "Delighter") if del_cols else pd.DataFrame({"Delighter":[], "Count":[]})

if APP["RELIABILITY_FLAG_COL"] in df.columns and APP["RELIABILITY_MODE_COL"] in df.columns:
    tmp = df[df[APP["RELIABILITY_FLAG_COL"]].astype(str).str.lower().eq("yes")]
    rel_modes = (tmp[APP["RELIABILITY_MODE_COL"]].value_counts().reset_index()
                 .rename(columns={"index":"Failure Mode", APP["RELIABILITY_MODE_COL"]:"Count"})) if not tmp.empty else pd.DataFrame({"Failure Mode":[], "Count":[]})
else:
    rel_modes = pd.DataFrame({"Failure Mode":[], "Count":[]})

render_bar_stable(CHARTS["det"], df_det, "Detractor", "Count", "Detractors Pareto")
render_bar_stable(CHARTS["del"], df_del, "Delighter", "Count", "Delighters Pareto")
render_bar_stable(CHARTS["rel"], rel_modes, "Failure Mode", "Count", "Reliability Modes")

# ---------- QA Review & Table ----------
tab_qa, tab_raw = st.tabs(["✅ QA Review", "🧾 Table"])
with tab_qa:
    s_text = st.text_input("Search in Verbatim/Evidence/Labels", "")
    subset=df.copy()
    if s_text.strip():
        s=s_text.lower().strip(); mask = subset["Verbatim"].str.lower().str.contains(s, na=False)
        for c in SYMPTOM_COLS:
            if c in subset.columns: mask |= subset[c].astype(str).str.lower().str.contains(s, na=False)
        for c in [APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"]]:
            if c in subset.columns: mask |= subset[c].astype(str).str.lower().str.contains(s, na=False)
        subset=subset[mask]
    st.caption(f"{len(subset)} rows")
    cols_show=[c for c in ["Verbatim","Star Rating"] + SYMPTOM_COLS +
               [APP["SAFETY_FLAG_COL"], APP["RELIABILITY_FLAG_COL"], APP["SUGGESTION_SUM_COL"],
                APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]] if c in subset.columns]
    st.dataframe(subset[cols_show].head(400), use_container_width=True, height=480)

with tab_raw:
    st.dataframe(df, use_container_width=True, height=560)

# ---------- Export ----------
st.divider(); st.markdown("### ⬇️ Export Updated Workbook")

def offer_downloads():
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first."); return
    raw = st.session_state["uploaded_bytes"]

    def _cell(v):
        if v is None or (isinstance(v,float) and np.isnan(v)) or (str(v).strip()==""): return None
        return str(v)

    fmt_ok=False; fmt_bytes=None
    try:
        if _HAS_OPENPYXL:
            bio=io.BytesIO(raw); wb=load_workbook(bio)
            data_sheet=APP["DATA_SHEET"] if APP["DATA_SHEET"] in wb.sheetnames else wb.sheetnames[0]
            ws=wb[data_sheet]
            headers={ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name):
                if name not in headers:
                    ci = ws.max_column + 1
                    ws.cell(row=1, column=ci, value=name); headers[name]=ci
                return headers[name]
            for c in SYMPTOM_COLS: col_idx(c)
            extras=[APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                    APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                    APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                    APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]
            for c in extras: col_idx(c)
            df_reset=df.reset_index(drop=True)
            for r_i, row in df_reset.iterrows():
                excel_row=2+r_i
                for c in SYMPTOM_COLS: ws.cell(row=excel_row, column=headers[c], value=_cell(row.get(c, None)))
                for c in extras: ws.cell(row=excel_row, column=headers[c], value=_cell(row.get(c, "")))
            # audit
            try:
                if APP["AUDIT_SHEET"] in wb.sheetnames: del wb[APP["AUDIT_SHEET"]]
                ws_a=wb.create_sheet(APP["AUDIT_SHEET"])
                aud=pd.DataFrame(AUDIT_ROWS) if len(AUDIT_ROWS) else pd.DataFrame(columns=["Row","Kind","Label","Quote","SemSim","Conf"])
                for j,col in enumerate(aud.columns, start=1): ws_a.cell(row=1, column=j, value=col)
                for i,(_,r) in enumerate(aud.iterrows(), start=2):
                    for j,col in enumerate(aud.columns, start=1): ws_a.cell(row=i, column=j, value=_cell(r[col]))
            except Exception: pass
            # run info
            try:
                if APP["RUNINFO_SHEET"] in wb.sheetnames: del wb[APP["RUNINFO_SHEET"]]
                ws_r=wb.create_sheet(APP["RUNINFO_SHEET"])
                runinfo=pd.DataFrame([{"Run ID":RUN_META["run_id"], "Mode":RUN_META["run_mode"], "Model":RUN_META["model"],
                                       "Strictness":strictness, "SemanticMin":semantic_threshold, "EvidenceRequired": evidence_required}])
                for j,col in enumerate(runinfo.columns, start=1): ws_r.cell(row=1, column=j, value=col)
                for i,(_,r) in enumerate(runinfo.iterrows(), start=2):
                    for j,col in enumerate(runinfo.columns, start=1): ws_r.cell(row=i, column=j, value=_cell(r[col]))
            except Exception: pass
            out=io.BytesIO(); wb.save(out); fmt_bytes=out.getvalue(); fmt_ok=True
    except Exception as e:
        st.warning(f"Format-preserving save failed; using basic writer. Reason: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET"], index=False)
            aud=pd.DataFrame(AUDIT_ROWS) if len(AUDIT_ROWS) else pd.DataFrame(columns=["Row","Kind","Label","Quote","SemSim","Conf"])
            aud.to_excel(xlw, sheet_name=APP["AUDIT_SHEET"], index=False)
            runinfo=pd.DataFrame([{"Run ID":RUN_META["run_id"], "Mode":RUN_META["run_mode"], "Model":RUN_META["model"],
                                   "Strictness":strictness, "SemanticMin":semantic_threshold, "EvidenceRequired": evidence_required}])
            runinfo.to_excel(xlw, sheet_name=APP["RUNINFO_SHEET"], index=False)
        basic_bytes=out2.getvalue()
    except Exception as e:
        st.error(f"Basic writer failed: {e}")

    c1,c2 = st.columns(2)
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
