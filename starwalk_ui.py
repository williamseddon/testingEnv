# ========================= Star Walk QE v11 =========================
# Streamlit >= 1.38
# - Rock-solid UX (light/dark), stable KPIs, responsive chunk runner
# - Evidence-first, idiom-aware symptomization (LLM optional)
# - Reliability/Safety extraction (rules + LLM verify)
# - Symptoms sheet autodetect + Hidden Approvals read/merge/write
# - Novel Terms Lab: propose → review → approve → persist
# - Plotly-safe (auto fallbacks to tables), no jitter
# - Defensive guards against missing deps and bad keys
# ===================================================================

import io, os, re, json, time, random
from typing import List, Tuple, Optional, Dict, Any
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import streamlit as st

# -------------------- App constants --------------------
APP = {
    "TITLE": "Star Walk QE — v11",
    "DATA_SHEET": "Star Walk scrubbed verbatims",
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),
    "SYMPTOMS_SHEET_HINT": "Symptoms",
    "APPROVED_SHEET": "__StarWalk_Approved",
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

# -------------------- Optional deps (lazy) --------------------
_HAS_PX = False
try:
    import plotly.express as px
    _HAS_PX = True
except Exception:
    _HAS_PX = False

_HAS_OPENAI = False
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

_HAS_OPENPYXL = False
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# -------------------- Theme & layout --------------------
st.set_page_config(page_title=APP["TITLE"], layout="wide", page_icon="🧭")
st.markdown("""
<style>
:root{ --fg:#0f172a; --muted:#64748b; --bd:#e2e8f0; --card:#ffffff; --app:#f7f9fc; --accent:#2563eb; }
@media (prefers-color-scheme: dark){
  :root{ --fg:#e5e7eb; --muted:#9ca3af; --bd:#334155; --card:#0b1020; --app:#0a0f1b; --accent:#3b82f6;}
}
* { scrollbar-width:thin }
.block-container{padding-top:.6rem; padding-bottom:1rem; max-width:1480px}
.stApp{background:var(--app)}
.header,.card{background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:14px}
.title{font-size:clamp(20px,2.6vw,32px); font-weight:800}
.sub{color:var(--muted)}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.pill{display:inline-flex;gap:6px;align-items:center;padding:6px 10px;border-radius:999px;border:1.5px solid var(--bd);background:var(--card);font-weight:700;color:var(--fg)}
hr{border-color:var(--bd)}
.console{white-space:pre-wrap; font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; background:var(--card); border:1px solid var(--bd); border-radius:10px; padding:10px; max-height:260px; overflow:auto;}
.badge{display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--bd); margin-right:6px; font-size:.85rem; color:var(--muted)}
.section-title{font-weight:800; font-size:1.05rem}
</style>
""", unsafe_allow_html=True)

# -------------------- Safe utilities --------------------
_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def canonicalize(name: str) -> str:
    ALIAS_CANON = {
        "initial difficulty": "Learning curve",
        "hard to learn": "Learning curve",
        "setup difficulty": "Learning curve",
        "noisy startup": "Startup noise",
        "too loud": "Loud",
        # idioms / intent:
        "vacuum sucks": "Poor performance",  # idiom maps to performance, not suction
        "sucks": "Poor performance",
    }
    nn=(name or "").strip(); base=_normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k)==base: return v
    return nn

def header(meta: dict):
    st.markdown(f"""
    <div class="header">
      <div class="title">{APP["TITLE"]}</div>
      <div class="sub">Evidence-driven symptomization · reliability & safety · stable UX · export</div>
      <div style="margin-top:8px">
        <span class="pill">Mode: {meta.get('run_mode','—')}</span>
        <span class="pill">Model: {meta.get('model','—')}</span>
        <span class="pill">Run: {meta.get('run_id','—')}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

def safe_bar_or_table(df_plot: pd.DataFrame, x: str, y: str, title: str, height=320, as_table_default=True):
    """Never crash: tables fallback if Plotly not available or errors."""
    if df_plot is None or df_plot.empty:
        st.info("No data yet."); return
    viz_mode = st.session_state.get("VIZ_MODE", "Tables")
    if viz_mode == "Tables" or (as_table_default and not _HAS_PX):
        st.dataframe(df_plot, use_container_width=True, height=height)
        return
    if not _HAS_PX:
        st.dataframe(df_plot, use_container_width=True, height=height)
        return
    try:
        fig = px.bar(df_plot, x=x, y=y, title=title, text=y)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=height, margin=dict(l=10,r=10,t=40,b=10),
                          xaxis_title=None, yaxis_title=None, showlegend=False)
        fig.update_layout(xaxis={"categoryorder":"total descending"})
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.dataframe(df_plot, use_container_width=True, height=height)

# -------------------- OpenAI + resilience helpers --------------------
def get_api_key() -> str:
    return (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()

def safe_client():
    if not _HAS_OPENAI:
        return None
    key = get_api_key()
    if not key:
        return None
    try:
        return OpenAI(api_key=key)
    except Exception:
        return None

def with_retry(fn, tries=3, backoff=0.75):
    last = None
    for t in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(backoff * (t + 1))
    raise last

def verify_json(model: str, system: str, user_obj: dict, force_chat=False) -> dict:
    """
    Calls OpenAI with JSON schema enforcement. Returns {} if anything fails.
    """
    cli = safe_client()
    if cli is None:
        return {}
    try:
        use_responses = (model.startswith("gpt-4.1") or model.startswith("gpt-5")) and not force_chat
        if use_responses:
            out = with_retry(lambda: cli.responses.create(
                model=model,
                response_format={"type":"json_object"},
                input=[{"role":"system","content":system},
                       {"role":"user","content":json.dumps(user_obj)}]
            ))
            content = out.output_text or "{}"
        else:
            out = with_retry(lambda: cli.chat.completions.create(
                model=model,
                messages=[{"role":"system","content":system},
                          {"role":"user","content":json.dumps(user_obj)}],
                temperature=0.2,
                response_format={"type":"json_object"},
            ))
            content = out.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception:
        return {}

# -------------------- Evidence + semantic helpers --------------------
_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

def _evidence_score(label: str, text: str) -> Tuple[int, List[str]]:
    if not label or not text: return 0, []
    toks = [t for t in _normalize_name(label).split() if _DEF_WORD.match(t)]
    hits=[]
    for t in toks:
        try:
            if re.search(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE):
                hits.append(t)
        except re.error:
            pass
    return len(hits), hits

@st.cache_resource(show_spinner=False)
def build_label_index(labels: List[str]):
    """Return (labels, L2-norm embedding matrix) or None if no API."""
    cli = safe_client()
    if cli is None or not labels:
        return None
    texts = list(dict.fromkeys([canonicalize(x) for x in labels if x]))
    out = cli.embeddings.create(model=APP["EMB_MODEL"], input=texts).data
    M = np.array([d.embedding for d in out], dtype="float32")
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
    return (texts, M)

def ngram_candidates(text: str, max_ngrams=256) -> List[str]:
    ws = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    out=[]; seen=set()
    for n in (1,2,3,4):
        for i in range(len(ws)-n+1):
            s=" ".join(ws[i:i+n])
            if len(s)>=4 and s not in seen:
                seen.add(s); out.append(s)
                if len(out)>=max_ngrams: return out
    return out

def semantic_support(review: str, label_index, topk=20, min_sim=0.70) -> Dict[str,float]:
    cli = safe_client()
    if cli is None or label_index is None or not review:
        return {}
    labels, L = label_index
    cands = ngram_candidates(review)
    if not cands: return {}
    data = cli.embeddings.create(model=APP["EMB_MODEL"], input=cands).data
    X = np.array([d.embedding for d in data], dtype="float32")
    X /= (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
    S = X @ L.T
    best_idx = S.argmax(axis=1)
    best_sim = S[np.arange(len(cands)), best_idx]
    buckets={}
    for j, sim in zip(best_idx, best_sim):
        if sim >= min_sim:
            lab = labels[int(j)]
            buckets[lab] = max(buckets.get(lab, 0.0), float(sim))
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1])[:topk])

# -------------------- Symptomizer (LLM with evidence & idiom guard) --------------------
def llm_symptomize(text: str, stars, allowed_del: List[str], allowed_det: List[str],
                   model="gpt-4o-mini", require_evidence=True, min_conf=0.72,
                   semantic_idx=None, semantic_min=0.70) -> Tuple[List[str], List[str]]:
    text = (text or "").strip()
    if not text:
        return [], []
    # Semantic prefilter (optional)
    sem_supp = {}
    try:
        sem_supp = semantic_support(text, semantic_idx, min_sim=semantic_min) if semantic_idx else {}
    except Exception:
        sem_supp = {}

    system = (
        "You label a single household appliance review. ONLY choose labels from provided allowed lists.\n"
        "Handle idioms: 'this vacuum sucks' = poor product performance (not suction).\n"
        "Reject labels without textual support. Return JSON {delighters:[{name,confidence}], detractors:[{name,confidence}]}"
    )
    user = {
        "review": text[:4000],
        "stars": float(stars) if stars is not None and str(stars) != "nan" else None,
        "allowed_delighters": allowed_del[:120],
        "allowed_detractors": allowed_det[:120],
        "semantic_candidates": sorted(list(sem_supp.keys())),
        "rules": {"max_delighters":10, "max_detractors":10,
                  "require_evidence": require_evidence, "min_confidence": min_conf}
    }

    data = verify_json(model, system, user)
    dels_raw = data.get("delighters", []) or []
    dets_raw = data.get("detractors", []) or []

    # Evidence + allow-list enforcement
    text_lc = " " + text.lower() + " "
    keep_del, keep_det = [], []
    for item in dets_raw:
        name = canonicalize(str(item.get("name","")).strip())
        conf = float(item.get("confidence", 0.0))
        if name in set(allowed_det):
            ok = (not require_evidence)
            if require_evidence:
                hits,_ = _evidence_score(name, text_lc)
                ok = hits >= 1 or sem_supp.get(name, 0.0) >= semantic_min
            if ok and conf >= min_conf: keep_det.append(name)
    for item in dels_raw:
        name = canonicalize(str(item.get("name","")).strip())
        conf = float(item.get("confidence", 0.0))
        if name in set(allowed_del):
            ok = (not require_evidence)
            if require_evidence:
                hits,_ = _evidence_score(name, text_lc)
                ok = hits >= 1 or sem_supp.get(name, 0.0) >= semantic_min
            if ok and conf >= min_conf: keep_del.append(name)

    # Star-prior tiebreak (never without evidence/semantic support)
    try:
        s = float(stars)
        if s <= 2.0 and not keep_det and dets_raw:
            for it in dets_raw:
                c = canonicalize(str(it.get("name","")))
                if c in allowed_det and (sem_supp.get(c,0)>=semantic_min or _evidence_score(c, text_lc)[0]>=1):
                    keep_det=[c]; break
        if s >= 4.0 and not keep_del and dels_raw:
            for it in dels_raw:
                c = canonicalize(str(it.get("name","")))
                if c in allowed_del and (sem_supp.get(c,0)>=semantic_min or _evidence_score(c, text_lc)[0]>=1):
                    keep_del=[c]; break
    except Exception:
        pass

    keep_det = list(dict.fromkeys(keep_det))[:10]
    keep_del = list(dict.fromkeys(keep_del))[:10]
    return keep_det, keep_del

# -------------------- Safety & Reliability extraction --------------------
def detect_safety_reliability(text: str) -> Dict[str, Any]:
    t = " " + (text or "").lower() + " "
    out = {
        "safety": False, "safety_quote": "",
        "reliability": False, "mode": "", "component": "", "severity": None, "rpn": None, "rel_quote": ""
    }
    # fast rules
    if any(k in t for k in ["smoke", "burn", "sparks", "fire", "shock", "burning smell", "overheat", "overheats"]):
        out["safety"] = True
        out["safety_quote"] = "Mentions potential hazard (smoke/overheat/etc.)"
    if any(k in t for k in ["shuts off", "turns off", "won't turn on", "won’t turn on", "cut out", "no power"]):
        out["reliability"] = True
        out["mode"] = "Intermittent power"
    if "battery" in t and any(k in t for k in ["die", "drain", "won't hold", "won’t hold"]):
        out["reliability"] = True
        out["mode"] = "Battery won’t hold charge"

    # LLM verify (optional)
    cli = safe_client()
    if cli:
        sys = ("Extract reliability and safety cues. If not clearly mentioned, leave empty. "
               "Return JSON: {safety:boolean, reliability:boolean, mode:str, component:str, severity:int|null, rpn:int|null, quote:str}")
        js = verify_json("gpt-4o-mini", sys, {"review": text}, force_chat=True)
        if isinstance(js, dict):
            out["safety"] = bool(js.get("safety", out["safety"]))
            out["reliability"] = bool(js.get("reliability", out["reliability"]))
            out["mode"] = js.get("mode", out["mode"]) or out["mode"]
            out["component"] = js.get("component", out["component"]) or out["component"]
            sev = js.get("severity", None); rpn = js.get("rpn", None)
            out["severity"] = int(sev) if isinstance(sev, (int,float)) else out["severity"]
            out["rpn"] = int(rpn) if isinstance(rpn, (int,float)) else out["rpn"]
            q = js.get("quote","") or js.get("rel_quote","")
            out["rel_quote"] = q or out["rel_quote"]
    return out

# -------------------- Local lexical fallback --------------------
def classify_stub(df: pd.DataFrame, rows: list[int], allowed_det: List[str], allowed_del: List[str]) -> None:
    for ridx in rows:
        text = str(df.at[ridx, "Verbatim"]) if "Verbatim" in df.columns else ""
        det, deL = [], []
        t = " "+text.lower()+" "
        if re.search(r"\b(shuts off|cuts out|turns off|won'?t turn on|no power)\b", t): det.append("Intermittent power")
        if re.search(r"\b(weak|poor|bad|sucks)\b", t): det.append("Poor performance")
        if re.search(r"\b(light|lightweight|easy)\b", t): deL.append("Lightweight")
        if re.search(r"\b(quiet|low noise|silent)\b", t): deL.append("Quiet")
        det = [d for d in [canonicalize(x) for x in det] if d in set(allowed_det or [])][:10]
        deL = [d for d in [canonicalize(x) for x in deL] if d in set(allowed_del or [])][:10]
        for j in range(1, 21):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = ""
        for j, name in enumerate(det[:10], start=1):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = name
        for j, name in enumerate(deL[:10], start=11):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = name
        if "hot" in t or "burn" in t or "smoke" in t:
            df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = "Mentions heat/smoke."
        if "shuts off" in t or "turns off" in t:
            df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["RELIABILITY_MODE_COL"]] = "Intermittent power"
            df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = "User reports unit shuts off."

# -------------------- Demo data (always loads) --------------------
def demo_df(rows=30):
    data = {
        "Verbatim":[
            "Love the lightweight design and easy cleanup. Dock works great.",
            "This vacuum sucks... not in a good way. Loses power and shuts off.",
            "Filter light stuck on even after cleaning. Motor feels weak.",
            "Battery dies fast and gets hot. Smelled like burning once.",
            "Fantastic hair pickup and quiet on low.",
        ] * max(1, rows//5),
        "Star Rating":[5, 1, 2, 1, 5] * max(1, rows//5),
    }
    for j in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1):
        data[f"{APP['SYMPTOM_PREFIX']}{j}"] = ""
    df = pd.DataFrame(data)
    df.at[0,"Symptom 11"]="Lightweight"; df.at[0,"Symptom 12"]="Easy cleanup"
    df.at[1,"Symptom 1"]="Poor performance"; df.at[1,"Symptom 2"]="Intermittent power"
    for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
        df[col] = ""
    return df

# -------------------- Symptoms sheet loader (autodetect + Hidden Approvals) --------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z]+", "", str(s or "").lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    n = _norm(name); return "symptom" in n or _norm(APP["SYMPTOMS_SHEET_HINT"]) in n

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> Optional[str]:
    names = xls.sheet_names
    cands = [n for n in names if _looks_like_symptom_sheet(n)]
    if cands:
        return min(cands, key=lambda n: len(_norm(n)))
    return names[1] if len(names) >= 2 else names[0] if names else None

def _col_score(colname: str, want: str) -> int:
    n = _norm(colname)
    if not n: return 0
    synonyms = {
        "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
        "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues"],
    }
    return max((1 for token in synonyms[want] if token in n), default=0)

def _extract_from_df(df_sheet: pd.DataFrame) -> Tuple[List[str], List[str], Dict[str,Any]]:
    debug={"strategy":None,"columns":list(df_sheet.columns)}
    # Strategy 1: fuzzy headers
    best_del = None; best_det = None
    for c in df_sheet.columns:
        if _col_score(str(c),"delighters"): best_del = c if best_del is None else best_del
        if _col_score(str(c),"detractors"): best_det = c if best_det is None else best_det
    if best_del is not None or best_det is not None:
        dels_ser = df_sheet.get(best_del, pd.Series(dtype=str)) if best_del is not None else pd.Series(dtype=str)
        dets_ser = df_sheet.get(best_det, pd.Series(dtype=str)) if best_det is not None else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels_ser.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets_ser.dropna().tolist() if str(x).strip()]
        if dels or dets:
            debug.update({"strategy":"fuzzy-headers","best_del_col":best_del,"best_det_col":best_det})
            return dels, dets, debug
    # Strategy 2: Type/Item
    type_col=item_col=None
    for c in df_sheet.columns:
        if _norm(c) in {"type","category","class","label"}: type_col=c
        if _norm(c) in {"item","symptom","name","term","entry","value"}: item_col=c
    if type_col is not None and item_col is not None:
        t = df_sheet[type_col].astype(str).str.strip().str.lower()
        i = df_sheet[item_col].astype(str).str.strip()
        dels = i[t.str.contains("delight|pro|positive", na=False)]
        dets = i[t.str.contains("detract|con|negative", na=False)]
        dels = [x for x in dels.dropna().tolist() if x]; dets = [x for x in dets.dropna().tolist() if x]
        if dels or dets:
            debug.update({"strategy":"type+item","type_col":type_col,"item_col":item_col})
            return dels, dets, debug
    # Strategy 3: first two non-empty columns
    non_empty=[]
    for c in df_sheet.columns:
        vals=[str(x).strip() for x in df_sheet[c].dropna().tolist() if str(x).strip()]
        if vals: non_empty.append((c,vals))
        if len(non_empty)>=2: break
    if non_empty:
        dels = non_empty[0][1]; dets = non_empty[1][1] if len(non_empty)>1 else []
        debug.update({"strategy":"first-two-nonempty","picked_cols":[c for c,_ in non_empty[:2]]})
        return dels, dets, debug
    return [], [], {"strategy":"none","columns":list(df_sheet.columns)}

def load_hidden_approvals(xls: pd.ExcelFile) -> Tuple[List[str], List[str]]:
    dels_extra, dets_extra = [], []
    try:
        if APP["APPROVED_SHEET"] in xls.sheet_names:
            hdf = pd.read_excel(xls, sheet_name=APP["APPROVED_SHEET"])
            if "Approved Delighters" in hdf.columns:
                dels_extra = [str(x).strip() for x in hdf["Approved Delighters"].dropna().tolist() if str(x).strip()]
            if "Approved Detractors" in hdf.columns:
                dets_extra = [str(x).strip() for x in hdf["Approved Detractors"].dropna().tolist() if str(x).strip()]
            if not (dels_extra or dets_extra) and len(hdf.columns)>=1:
                cols=list(hdf.columns); c1 = hdf[cols[0]].dropna().astype(str).str.strip().tolist()
                dels_extra=[x for x in c1 if x]
                if len(cols)>1:
                    c2 = hdf[cols[1]].dropna().astype(str).str.strip().tolist()
                    dets_extra=[x for x in c2 if x]
    except Exception:
        pass
    return dels_extra, dets_extra

def load_symptom_lists_robust(raw_bytes: bytes,
                              user_sheet: Optional[str]=None,
                              user_del_col: Optional[str]=None,
                              user_det_col: Optional[str]=None) -> Tuple[List[str], List[str], Dict[str,Any], List[str]]:
    """Return (delighters, detractors, meta, sheet_names)"""
    meta={"sheet":None,"strategy":None,"columns":[],"note":""}
    sheet_names=[]
    if not raw_bytes:
        meta["note"]="No bytes"; return [], [], meta, sheet_names
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes))
        sheet_names = xls.sheet_names
    except Exception as e:
        meta["note"]=f"Excel open failed: {e}"; return [], [], meta, sheet_names
    sheet = user_sheet or autodetect_symptom_sheet(xls)
    if not sheet:
        meta["note"]="No sheets"; return [], [], meta, sheet_names
    meta["sheet"]=sheet
    try:
        s = pd.read_excel(xls, sheet_name=sheet)
    except Exception as e:
        meta["note"]=f"Read sheet failed: {e}"; return [], [], meta, sheet_names
    if user_del_col or user_det_col:
        dels = s.get(user_del_col, pd.Series(dtype=str)) if user_del_col in s.columns else pd.Series(dtype=str)
        dets = s.get(user_det_col, pd.Series(dtype=str)) if user_det_col in s.columns else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"manual-columns","columns":list(s.columns)})
    else:
        dels, dets, info = _extract_from_df(s); meta.update(info)
    # merge hidden approvals
    try:
        dels_extra, dets_extra = load_hidden_approvals(xls)
        if dels_extra: dels = list(dict.fromkeys(dels + dels_extra))
        if dets_extra: dets = list(dict.fromkeys(dets + dets_extra))
    except Exception:
        pass
    return dels, dets, meta, sheet_names

# -------------------- Novel Terms discovery --------------------
def discover_novel_terms(texts: List[str], allowed_all: set[str], top_k=30) -> List[Tuple[str,int]]:
    """
    Very fast unsupervised n-gram frequency for candidates not in allowed list.
    Filters to 1-3 gram tokens >= 4 chars, not numeric-only.
    """
    counts=defaultdict(int)
    for t in texts:
        ws = re.findall(r"[a-z0-9]{3,}", (t or "").lower())
        for n in (1,2,3):
            for i in range(len(ws)-n+1):
                g=" ".join(ws[i:i+n]).strip()
                if len(g) < 4: continue
                if g.isdigit(): continue
                if canonicalize(g) in allowed_all: continue
                counts[g]+=1
    c_sorted = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    # basic de-dup via canonical similarity
    kept=[]; seen=set()
    for g,c in c_sorted:
        base = _normalize_name(g)
        if any(_normalize_name(x)==base for x,_ in kept): continue
        kept.append((g,c))
        if len(kept)>=top_k: break
    return kept

# -------------------- Review module (scoped & safe) --------------------
def render_symptomization_review(df, SYMPTOM_COLS, APP):
    ss = st.session_state
    if "REVIEW_UNDO_STACK" not in ss: ss["REVIEW_UNDO_STACK"] = []

    def _stars_bucket(v):
        try: s=float(v)
        except Exception: return "NA"
        if s<=2.0: return "1–2"
        if s>=4.0: return "4–5"
        return "3"

    def _undo_last():
        if not ss["REVIEW_UNDO_STACK"]:
            st.info("Nothing to undo."); return
        name, snap = ss["REVIEW_UNDO_STACK"].pop()
        for c in snap.columns: df[c]=snap[c]
        st.warning(f"↩ Undid: {name}")

    def _snapshot_cols():
        cols = list(SYMPTOM_COLS)
        for c in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                  APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                  APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                  APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
            if c in df.columns: cols.append(c)
        return cols

    # Build metrics & anomalies
    rows=[]; empty=conflicts=low_only=high_only=ev_ct=0
    for i,r in df.iterrows():
        det = [str(r.get(f"Symptom {j}","")).strip() for j in range(1,11) if str(r.get(f"Symptom {j}","")).strip()]
        deL = [str(r.get(f"Symptom {j}","")).strip() for j in range(11,21) if str(r.get(f"Symptom {j}","")).strip()]
        if (len(det)+len(deL))==0: empty+=1
        ev = any(str(r.get(c,"")).strip() for c in [APP["VOC_QUOTE_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SAFETY_EVIDENCE_COL"]] if c in df.columns)
        if ev: ev_ct+=1
        nd, nl = {_normalize_name(x) for x in det}, {_normalize_name(x) for x in deL}
        if nd & nl: conflicts+=1
        sb=_stars_bucket(r.get("Star Rating", None))
        if sb=="1–2" and len(det)==0 and len(deL)>0: low_only+=1
        if sb=="4–5" and len(deL)==0 and len(det)>0: high_only+=1
        rows.append({"Row":i,"Stars":r.get("Star Rating",None),"StarsBin":sb,
                     "DetractorsCount":len(det),"DelightersCount":len(deL),
                     "Evidence":ev,
                     "Safety": str(r.get(APP["SAFETY_FLAG_COL"],"")).lower()=="yes" if APP["SAFETY_FLAG_COL"] in df.columns else False,
                     "Reliability": str(r.get(APP["RELIABILITY_FLAG_COL"],"")).lower()=="yes" if APP["RELIABILITY_FLAG_COL"] in df.columns else False,
                     "Conflict": bool(nd & nl)})
    meta=pd.DataFrame(rows)
    base=len(df) if len(df) else 1
    kpis={"Rows":len(df),"Empty rows":empty,"Evidence rate":round(ev_ct/base,3),
          "Conflict rate":round(conflicts/base,3),"Low★ only delighters":low_only,"High★ only detractors":high_only}

    st.divider()
    tab_rev, tab_labels = st.tabs(["🔎 Symptomization Review+", "🏷️ Label Drilldown"])

    with tab_rev:
        kc = st.columns(6)
        items=[("Rows",kpis["Rows"]),("Empty rows",kpis["Empty rows"]),
               ("Evidence rate", f"{int(kpis['Evidence rate']*100)}%"),
               ("Conflict rate", f"{int(kpis['Conflict rate']*100)}%"),
               ("Low★ only delighters",kpis["Low★ only delighters"]),
               ("High★ only detractors",kpis["High★ only detractors"])]
        for c,(k,v) in zip(kc,items): c.metric(k,v)

        st.markdown("**Anomalies & Conflicts**")
        anomalies = meta[(meta["Conflict"]) | ((meta["DetractorsCount"]+meta["DelightersCount"])==0) |
                         ((meta["StarsBin"]=="1–2") & (meta["DelightersCount"]>0) & (meta["DetractorsCount"]==0)) |
                         ((meta["StarsBin"]=="4–5") & (meta["DetractorsCount"]>0) & (meta["DelightersCount"]==0))
                        ].sort_values(["Conflict","Row"], ascending=[False,True])
        st.dataframe(anomalies, use_container_width=True, height=240)

        st.markdown("### Row Evidence Inspector")
        ridx = st.number_input("Row index", min_value=0, max_value=max(0,len(df)-1), value=0)
        if 0 <= ridx < len(df):
            row = df.iloc[int(ridx)]
            det = [str(row.get(f"Symptom {j}","")).strip() for j in range(1,11) if str(row.get(f"Symptom {j}","")).strip()]
            deL = [str(row.get(f"Symptom {j}","")).strip() for j in range(11,21) if str(row.get(f"Symptom {j}","")).strip()]
            st.write("**Verbatim**"); st.code(str(row.get("Verbatim",""))[:1200], language="text")
            eq = ""
            for c in [APP["VOC_QUOTE_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SAFETY_EVIDENCE_COL"]]:
                if c in df.columns:
                    eq = str(row.get(c,"") or "").strip()
                    if eq: break
            st.write("**Evidence**"); st.info(eq if eq else "—")

            cols=_snapshot_cols(); snap=df[cols].copy(deep=True)

            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors**")
                if det:
                    for lab in det:
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lab)
                        if cc2.button("➡️ to Delighter", key=f"mv_d_{ridx}_{lab}"):
                            try:
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                for j in range(11,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and not str(df.at[ridx,c]).strip():
                                        df.at[ridx,c]=lab; break
                                st.session_state["REVIEW_UNDO_STACK"].append((f"Move {lab} to Delighter", snap))
                                st.success("Moved (undo available)")
                            except Exception as e:
                                st.error(f"Move failed: {e}")
                        if cc3.button("🗑️", key=f"rm_d_{ridx}_{lab}"):
                            try:
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                st.session_state["REVIEW_UNDO_STACK"].append((f"Remove {lab}", snap))
                                st.success("Removed (undo available)")
                            except Exception as e:
                                st.error(f"Remove failed: {e}")
                else:
                    st.caption("—")
            with c2:
                st.write("**Delighters**")
                if deL:
                    for lab in deL:
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lab)
                        if cc2.button("➡️ to Detractor", key=f"mv_l_{ridx}_{lab}"):
                            try:
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                for j in range(1,11):
                                    c=f"Symptom {j}"
                                    if c in df.columns and not str(df.at[ridx,c]).strip():
                                        df.at[ridx,c]=lab; break
                                st.session_state["REVIEW_UNDO_STACK"].append((f"Move {lab} to Detractor", snap))
                                st.success("Moved (undo available)")
                            except Exception as e:
                                st.error(f"Move failed: {e}")
                        if cc3.button("🗑️", key=f"rm_l_{ridx}_{lab}"):
                            try:
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                st.session_state["REVIEW_UNDO_STACK"].append((f"Remove {lab}", snap))
                                st.success("Removed (undo available)")
                            except Exception as e:
                                st.error(f"Remove failed: {e}")

            if st.button("↩ Undo last change"):
                _undo_last()

    with tab_labels:
        def per_label(df_, rng):
            label_counts=Counter(); low=Counter(); high=Counter(); evid=Counter()
            for _,r in df_.iterrows():
                try:
                    s=float(r.get("Star Rating", np.nan))
                    sb = "1–2" if (not np.isnan(s) and s<=2) else ("4–5" if (not np.isnan(s) and s>=4) else "3")
                except Exception: sb="NA"
                ev=False
                for c in [APP["VOC_QUOTE_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SAFETY_EVIDENCE_COL"]]:
                    if c in df_.columns and str(r.get(c,"")).strip(): ev=True; break
                for j in rng:
                    c=f"Symptom {j}"
                    if c in df_.columns:
                        v=str(r.get(c,"")).strip()
                        if v:
                            label_counts[v]+=1
                            if ev: evid[v]+=1
                            if sb=="1–2": low[v]+=1
                            if sb=="4–5": high[v]+=1
            out=[]
            for lab, ct in label_counts.most_common():
                out.append({"Label":lab,"Count":ct,
                            "Evidence%": round((evid[lab]/ct)*100,1) if ct else 0.0,
                            "Low★%": round((low[lab]/ct)*100,1) if ct else 0.0,
                            "High★%": round((high[lab]/ct)*100,1) if ct else 0.0})
            return pd.DataFrame(out)

        colA,colB = st.columns(2)
        det_tbl = per_label(df, range(1,11))
        del_tbl = per_label(df, range(11,21))
        with colA:
            st.markdown("**Top Detractors — Evidence & Star mix**")
            if det_tbl.empty: st.info("No detractors yet.")
            else:
                det_tbl = det_tbl.sort_values(["Count","Label"], ascending=[False,True])
                st.dataframe(det_tbl, use_container_width=True, height=360)
        with colB:
            st.markdown("**Top Delighters — Evidence & Star mix**")
            if del_tbl.empty: st.info("No delighters yet.")
            else:
                del_tbl = del_tbl.sort_values(["Count","Label"], ascending=[False,True])
                st.dataframe(del_tbl, use_container_width=True, height=360)

# -------------------- Cached excel reader --------------------
@st.cache_data(show_spinner=False)
def read_excel_sheet(uploaded_bytes: bytes, sheet_name: Optional[str]):
    bio = io.BytesIO(uploaded_bytes)
    try:
        if sheet_name:
            return pd.read_excel(bio, sheet_name=sheet_name)
        else:
            return pd.read_excel(bio)
    except Exception:
        return None

# -------------------- Main --------------------
def main():
    # Sidebar
    st.sidebar.header("📁 Upload Excel (.xlsx)")
    uploaded = st.sidebar.file_uploader("Upload workbook", type=["xlsx"])
    safe_demo = st.sidebar.checkbox("Safe Boot (Demo if no file)", value=True)
    st.session_state["VIZ_MODE"] = st.sidebar.radio("Visualization", ["Tables","Charts"], index=0, horizontal=True)

    # Allowed lists (UI baseline; sheet/approvals will merge in)
    st.sidebar.markdown("### 🧾 Symptoms Source")
    allowed_det_ui = st.sidebar.text_area("Detractors (one per line)", "Poor performance\nIntermittent power\nLoud\nLearning curve")
    allowed_del_ui = st.sidebar.text_area("Delighters (one per line)", "Lightweight\nEasy cleanup\nQuiet\nDock convenience")
    ALLOWED_DET = [x.strip() for x in allowed_det_ui.splitlines() if x.strip()]
    ALLOWED_DEL = [x.strip() for x in allowed_del_ui.splitlines() if x.strip()]

    # Data load
    df = None; raw = None
    if uploaded is not None:
        uploaded.seek(0); raw = uploaded.read(); uploaded.seek(0)
        df = read_excel_sheet(raw, APP["DATA_SHEET"])
        if df is None:
            df = read_excel_sheet(raw, None)
    if df is None:
        if safe_demo:
            df = demo_df(rows=40)
            st.info("Safe Boot demo data loaded (toggle off to require file).")
        else:
            st.warning("Please upload a workbook to continue."); return

    # Ensure symptom and QE columns exist
    exp_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
    for c in exp_cols:
        if c not in df.columns: df[c] = ""
    for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
        if col not in df.columns: df[col] = ""

    # Header
    run_meta = {"run_id": str(int(time.time())), "model": ("OpenAI" if safe_client() else "safe"), "run_mode": "Manual"}
    header(run_meta)

    # ======== (Optional) Sheet autodetect + Hidden Approvals merge ========
    loaded_from_sheet=False; sheet_names=[]; meta={}
    chosen_sheet=None; picked_del_col=None; picked_det_col=None
    if raw is not None:
        try:
            xls = pd.ExcelFile(io.BytesIO(raw)); sheet_names = xls.sheet_names
        except Exception:
            sheet_names=[]
        auto_sheet = autodetect_symptom_sheet(pd.ExcelFile(io.BytesIO(raw))) if sheet_names else None

        st.sidebar.markdown("#### Sheet Picker (optional)")
        chosen_sheet = st.sidebar.selectbox(
            "Sheet that contains Delighters/Detractors",
            options=(sheet_names if sheet_names else ["(no sheets detected)"]),
            index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0)
        ) if sheet_names else None

        symp_cols_preview=[]
        if chosen_sheet:
            try:
                _df_symp_prev = pd.read_excel(io.BytesIO(raw), sheet_name=chosen_sheet)
                symp_cols_preview = list(_df_symp_prev.columns)
                st.sidebar.caption("Detected columns: " + ", ".join(map(str, symp_cols_preview)))
            except Exception:
                symp_cols_preview=[]
        manual_cols=False
        if symp_cols_preview:
            manual_cols = st.sidebar.checkbox("Manually choose Delighters/Detractors columns", value=False)
            if manual_cols:
                picked_del_col = st.sidebar.selectbox("Delighters column", options=["(none)"] + symp_cols_preview, index=0)
                picked_det_col = st.sidebar.selectbox("Detractors column", options=["(none)"] + symp_cols_preview, index=0)
                if picked_del_col == "(none)": picked_del_col=None
                if picked_det_col == "(none)": picked_det_col=None

        dels, dets, meta, _ = load_symptom_lists_robust(
            raw, user_sheet=chosen_sheet if chosen_sheet else None,
            user_del_col=picked_del_col, user_det_col=picked_det_col
        )
        if dels or dets:
            loaded_from_sheet=True
            # merge: sheet first, then UI extras
            ALLOWED_DEL = list(dict.fromkeys(dels + ALLOWED_DEL))
            ALLOWED_DET = list(dict.fromkeys(dets + ALLOWED_DET))
            st.sidebar.success(
                f"Loaded {len(dels)} delighters, {len(dets)} detractors "
                f"(sheet: '{meta.get('sheet','?')}', mode: {meta.get('strategy','?')})."
            )
        else:
            if sheet_names:
                st.sidebar.warning(
                    f"Couldn’t detect clear Delighters/Detractors in '{meta.get('sheet','?')}'. Using UI lists."
                )

    # Build label index (semantic boost) if key available
    LABEL_INDEX = build_label_index(ALLOWED_DEL + ALLOWED_DET)

    # KPIs
    vs = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
    lens = vs.str.len()
    kpis = [("Total reviews", len(df)),
            ("Avg chars", int(lens.mean()) if len(lens) else 0),
            ("Stars col", "present" if "Star Rating" in df.columns else "—"),
            ("Source", "Sheet+Approvals" if loaded_from_sheet else "Manual UI")]
    st.markdown("<div class='kpis'>" + "".join([f"<span class='pill'>{k}: <b>{v}</b></span>" for k,v in kpis]) + "</div>", unsafe_allow_html=True)

    # Quick Pareto (stable)
    det_vals=[]; del_vals=[]
    for j in range(1,11):
        c=f"Symptom {j}"
        if c in df.columns:
            det_vals += [str(x).strip() for x in df[c].dropna() if str(x).strip()]
    for j in range(11,21):
        c=f"Symptom {j}"
        if c in df.columns:
            del_vals += [str(x).strip() for x in df[c].dropna() if str(x).strip()]

    det_ct = Counter(det_vals)
    del_ct = Counter(del_vals)
    df_det = (pd.DataFrame({"Detractor": list(det_ct.keys()), "Count": list(det_ct.values())})
              .sort_values(["Count","Detractor"], ascending=[False,True]).head(12))
    df_del = (pd.DataFrame({"Delighter": list(del_ct.keys()), "Count": list(del_ct.values())})
              .sort_values(["Count","Delighter"], ascending=[False,True]).head(12))

    left, right = st.columns([1,1])
    with left:
        st.markdown("**Top Detractors**")
        safe_bar_or_table(df_det, "Detractor", "Count", "Detractors Pareto", height=320, as_table_default=True)
    with right:
        st.markdown("**Top Delighters**")
        safe_bar_or_table(df_del, "Delighter", "Count", "Delighters Pareto", height=320, as_table_default=True)

    # ======== Symptomization Runner ========
    st.divider()
    st.markdown("### ⚙️ Symptomization Runner")
    use_llm = st.toggle("Use AI (OpenAI) if available", value=True, help="If off or no key, uses safe lexical fallback.")
    require_evidence = st.checkbox("Require evidence for labels", value=True)
    min_conf = st.slider("Min LLM confidence", 0.60, 0.95, 0.72, 0.01)
    semantic_min = st.slider("Min semantic similarity (if embeddings on)", 0.60, 0.90, 0.70, 0.01)

    BATCH = st.number_input("Batch size (rows per tick)", 1, 200, 30)
    ORDER = st.selectbox("Order", ["Original", "Shortest first", "Longest first"], index=0)
    idx_all = list(range(len(df)))
    if ORDER != "Original":
        idx_all = sorted(idx_all, key=lambda i: len(vs.iloc[i]), reverse=(ORDER=="Longest first"))

    # session queue + console
    if "RUN_QUEUE" not in st.session_state: st.session_state["RUN_QUEUE"] = []
    if "RUN_PTR" not in st.session_state: st.session_state["RUN_PTR"] = 0
    if "LOG" not in st.session_state: st.session_state["LOG"] = []
    def log(msg):
        st.session_state["LOG"].append(msg)
        if len(st.session_state["LOG"])>300: st.session_state["LOG"]=st.session_state["LOG"][-300:]

    c1,c2,c3 = st.columns([1,1,2])
    with c1:
        if st.button("Queue ALL"):
            st.session_state["RUN_QUEUE"] = idx_all
            st.session_state["RUN_PTR"] = 0
            log(f"Queued {len(idx_all)} rows."); st.toast("Queued ALL rows", icon="✅")
    with c2:
        sample_n = st.number_input("Or queue first N", 1, max(1,len(df)), min(100,len(df)))
        if st.button("Queue N"):
            st.session_state["RUN_QUEUE"] = idx_all[:int(sample_n)]
            st.session_state["RUN_PTR"] = 0
            log(f"Queued first {int(sample_n)} rows."); st.toast(f"Queued {int(sample_n)} rows", icon="✅")
    with c3:
        st.caption("Runs in chunks so the UI stays responsive and charts don’t jump.")

    queued = st.session_state["RUN_QUEUE"]; ptr = st.session_state["RUN_PTR"]
    remaining = max(0, len(queued) - ptr)

    if remaining:
        st.info(f"Queued rows: {len(queued)} • Remaining: {remaining}")
        prog = st.progress(ptr / len(queued))
        go = st.button(f"Process next {min(BATCH, remaining)}")
        if go:
            span = queued[ptr: ptr + BATCH]
            cli_avail = (safe_client() is not None)
            t0=time.time()
            for ridx in span:
                text = vs.iloc[ridx]
                stars = df.at[ridx, "Star Rating"] if "Star Rating" in df.columns else None
                if use_llm and cli_avail:
                    det, deL = llm_symptomize(text, stars, ALLOWED_DEL, ALLOWED_DET,
                                              model="gpt-4o-mini", require_evidence=require_evidence,
                                              min_conf=min_conf, semantic_idx=LABEL_INDEX, semantic_min=semantic_min)
                    # clear then write
                    for j in range(1, 21):
                        c=f"Symptom {j}"
                        if c in df.columns: df.at[ridx, c] = ""
                    for j, name in enumerate(det[:10], start=1):
                        c=f"Symptom {j}"
                        if c in df.columns: df.at[ridx, c] = name
                    for j, name in enumerate(deL[:10], start=11):
                        c=f"Symptom {j}"
                        if c in df.columns: df.at[ridx, c] = name
                else:
                    classify_stub(df, [ridx], ALLOWED_DET, ALLOWED_DEL)

                # Safety/Reliability
                sr = detect_safety_reliability(text)
                if sr["safety"]:
                    df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes"
                    if sr["safety_quote"]:
                        df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = sr["safety_quote"]
                if sr["reliability"]:
                    df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
                    if sr["mode"]: df.at[ridx, APP["RELIABILITY_MODE_COL"]] = sr["mode"]
                    if sr["component"]: df.at[ridx, APP["RELIABILITY_COMP_COL"]] = sr["component"]
                    if sr["severity"] is not None: df.at[ridx, APP["RELIABILITY_SEV_COL"]] = int(sr["severity"])
                    if sr["rpn"] is not None: df.at[ridx, APP["RELIABILITY_RPN_COL"]] = int(sr["rpn"])
                    if sr["rel_quote"]: df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = sr["rel_quote"]

            st.session_state["RUN_PTR"] = ptr + len(span)
            prog.progress(st.session_state["RUN_PTR"] / len(queued))
            dt = time.time()-t0
            log(f"Processed {len(span)} rows in {dt:.1f}s. Remaining: {len(queued)-st.session_state['RUN_PTR']}")
            st.toast(f"Processed {len(span)} rows", icon="⚡")
            st.rerun()
    else:
        st.caption("Queue is empty.")

    # Console
    st.markdown("### 📜 Run Console")
    if st.session_state["LOG"]:
        st.markdown(f"<div class='console'>{chr(10).join(st.session_state['LOG'][-200:])}</div>", unsafe_allow_html=True)
    else:
        st.caption("No messages yet.")
    st.markdown("<span class='badge'>Tip</span> Increase confidence to reduce false positives; keep semantic similarity ≈0.70–0.75 for balanced recall.", unsafe_allow_html=True)

    # ======== Novel Terms Lab (discover → review → approve) ========
    st.divider()
    st.markdown("### 🧪 Novel Terms Lab")
    allowed_all = set([canonicalize(x) for x in (ALLOWED_DEL + ALLOWED_DET)])
    unlabeled_idx = []
    for i, r in df.iterrows():
        has_any = False
        for j in range(1,21):
            c=f"Symptom {j}"
            if c in df.columns and str(r.get(c,"")).strip():
                has_any=True; break
        if not has_any: unlabeled_idx.append(i)
    sample_scope = st.slider("Scope (first N reviews to scan)", 20, max(20, len(df)), min(200, len(df)))
    texts_scope = [str(df.at[i,"Verbatim"]) for i in range(min(sample_scope, len(df))) if "Verbatim" in df.columns]
    proposals = discover_novel_terms(texts_scope, allowed_all, top_k=40)
    if proposals:
        st.caption("Proposed phrases not in your allowed lists (frequency):")
        df_prop = pd.DataFrame(proposals, columns=["Term","Count"]).head(20)
        st.dataframe(df_prop, use_container_width=True, height=240)
        st.markdown("**Approve into allowed lists**")
        c1,c2 = st.columns(2)
        add_det = c1.multiselect("Add as Detractors", [t for t,_ in proposals], default=[])
        add_del = c2.multiselect("Add as Delighters", [t for t,_ in proposals], default=[])
        if "NOVEL_DET" not in st.session_state: st.session_state["NOVEL_DET"]=set()
        if "NOVEL_DEL" not in st.session_state: st.session_state["NOVEL_DEL"]=set()
        if st.button("✅ Approve selected"):
            for t in add_det: st.session_state["NOVEL_DET"].add(canonicalize(t))
            for t in add_del: st.session_state["NOVEL_DEL"].add(canonicalize(t))
            st.success("Approved for this session. They will be written into __StarWalk_Approved on export.")
    else:
        st.caption("No novel phrases detected in the chosen scope.")

    # ======== Review+ ========
    render_symptomization_review(df, exp_cols, APP)

    # ======== Export (format-preserving + Hidden Approvals writeback) ========
    st.divider(); st.markdown("### ⬇️ Export")
    st.markdown("**Session Approvals (optional quick add)**")
    sess_del_add = st.text_input("Add Approved Delighters (comma-separated)", "")
    sess_det_add = st.text_input("Add Approved Detractors (comma-separated)", "")
    approved_del_session = [canonicalize(x.strip()) for x in sess_del_add.split(",") if x.strip()]
    approved_det_session = [canonicalize(x.strip()) for x in sess_det_add.split(",") if x.strip()]
    # also include Novel Terms Lab
    approved_del_session += sorted(list(st.session_state.get("NOVEL_DEL", set())))
    approved_det_session += sorted(list(st.session_state.get("NOVEL_DET", set())))

    fmt_ok=False; fmt_bytes=None
    try:
        if _HAS_OPENPYXL and uploaded is not None:
            uploaded.seek(0); raw = uploaded.read(); uploaded.seek(0)
            bio=io.BytesIO(raw); wb=load_workbook(bio)

            # Data sheet
            data_sheet=APP["DATA_SHEET"] if APP["DATA_SHEET"] in wb.sheetnames else wb.sheetnames[0]
            ws=wb[data_sheet]
            headers={ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name):
                if name not in headers:
                    ci = ws.max_column + 1
                    ws.cell(row=1, column=ci, value=name); headers[name]=ci
                return headers[name]
            for c in exp_cols: col_idx(c)
            for c in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                      APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                      APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                      APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
                col_idx(c)
            df_reset=df.reset_index(drop=True)
            for r_i,row in df_reset.iterrows():
                excel_row=2+r_i
                for c in exp_cols + [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                      APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                      APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                      APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
                    v=row.get(c,"")
                    ws.cell(row=excel_row, column=headers[c], value=(None if str(v).strip()=="" else str(v)))

            # Hidden approvals sheet writeback (merge previous + session approvals + UI lists)
            if APP["APPROVED_SHEET"] not in wb.sheetnames:
                wh = wb.create_sheet(APP["APPROVED_SHEET"])
                wh.sheet_state = "hidden"
                wh.cell(row=1, column=1, value="Approved Delighters")
                wh.cell(row=1, column=2, value="Approved Detractors")
            else:
                wh = wb[APP["APPROVED_SHEET"]]
                if not wh.cell(row=1, column=1).value: wh.cell(row=1, column=1, value="Approved Delighters")
                if not wh.cell(row=1, column=2).value: wh.cell(row=1, column=2, value="Approved Detractors")

            # read existing approvals
            exist_del, exist_det = set(), set()
            try:
                r=2
                while True:
                    v=wh.cell(row=r, column=1).value
                    if v is None: break
                    v=str(v).strip()
                    if v: exist_del.add(v)
                    r+=1
            except Exception: pass
            try:
                r=2
                while True:
                    v=wh.cell(row=r, column=2).value
                    if v is None: break
                    v=str(v).strip()
                    if v: exist_det.add(v)
                    r+=1
            except Exception: pass

            # Merge: existing + UI + session + novel (already in approved_*_session)
            final_del = sorted(set(list(exist_del) + [*ALLOWED_DEL] + approved_del_session))
            final_det = sorted(set(list(exist_det) + [*ALLOWED_DET] + approved_det_session))

            # Clear & write back
            max_len = max(len(final_del), len(final_det), 1)
            for r in range(2, 2 + max_len + 200):
                wh.cell(row=r, column=1, value=None)
                wh.cell(row=r, column=2, value=None)
            for i,v in enumerate(final_del, start=2):
                wh.cell(row=i, column=1, value=v)
            for i,v in enumerate(final_det, start=2):
                wh.cell(row=i, column=2, value=v)

            out=io.BytesIO(); wb.save(out); fmt_bytes=out.getvalue(); fmt_ok=True
    except Exception as e:
        st.warning(f"Format-preserving export not available: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET"], index=False)
            # snapshot allowed lists + approvals for transparency
            allowed_df = pd.DataFrame({"Delighters": pd.Series(ALLOWED_DEL), "Detractors": pd.Series(ALLOWED_DET)})
            allowed_df.to_excel(xlw, sheet_name="Allowed Symptoms (session)", index=False)
            appr_df = pd.DataFrame({
                "Approved Delighters": pd.Series(sorted(set(approved_del_session))),
                "Approved Detractors": pd.Series(sorted(set(approved_det_session))),
            })
            appr_df.to_excel(xlw, sheet_name="__StarWalk_Approved", index=False)
        basic_bytes=out2.getvalue()
    except Exception as e:
        st.error(f"Basic export failed: {e}")

    col1,col2 = st.columns(2)
    with col1:
        if fmt_ok and fmt_bytes:
            st.download_button("⬇️ Download (preserve formatting)", data=fmt_bytes,
                               file_name="starwalk_v11_formatted.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("Format-preserving export unavailable.")
    with col2:
        if basic_bytes:
            st.download_button("⬇️ Download (basic)", data=basic_bytes,
                               file_name="starwalk_v11.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error("The app hit a non-fatal error but stayed alive.")
        try: st.write(str(e))
        except Exception: pass
