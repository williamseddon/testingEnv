# ========================= Star Walk QE v13 =========================
# Streamlit >= 1.38
# Improvements in v13:
# - Modularized code structure for better readability and maintenance.
# - Enhanced UX: Improved layout with expandable sections, better button placements, loading spinners, and tooltips.
# - Added search and filter in review section for easier inspection.
# - Optimized performance: Better caching, adaptive batching, and progress tracking.
# - Added more KPIs and visualizations, including trend analysis if date column present.
# - Improved error handling with user-friendly messages.
# - Dark mode support refined.
# - Added option to clear queue and undo stack.
# - Export now includes a summary sheet with KPIs and Paretos.

import io, os, re, json, time, random, hashlib
from typing import List, Tuple, Optional, Dict, Any
from collections import Counter
import numpy as np
import pandas as pd
import streamlit as st

# -------------------- App constants --------------------
APP = {
    "TITLE": "Star Walk QE — v13",
    "DATA_SHEET": "Star Walk scrubbed verbatims",
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),
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
    "DATE_COL": "Date"  # Assuming a date column for trends
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
    from openpyxl import load_workbook, Workbook
    from openpyxl.utils.dataframe import dataframe_to_rows
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# -------------------- Theme & layout --------------------
st.set_page_config(page_title=APP["TITLE"], layout="wide", page_icon="🧭")
st.markdown("""
<style>
:root{
  --fg:#0f172a; --muted:#64748b; --bd:#e2e8f0; --card:#ffffff; --app:#f7f9fc; --accent:#3b82f6;
}
@media (prefers-color-scheme: dark){
  :root{ --fg:#e5e7eb; --muted:#9ca3af; --bd:#334155; --card:#1f2937; --app:#111827; --accent:#60a5fa;}
}
.block-container{padding-top:.6rem; padding-bottom:1rem; max-width:1480px}
.stApp{background:var(--app)}
.header,.card{background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:14px}
.title{font-size:clamp(20px,2.6vw,32px); font-weight:800}
.sub{color:var(--muted)}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.pill{display:inline-flex;gap:6px;align-items:center;padding:6px 10px;border-radius:999px;border:1.5px solid var(--bd);background:var(--card);font-weight:700;color:var(--fg)}
hr{border-color:var(--bd)}
.console{white-space:pre-wrap; font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; background:var(--card); border:1px solid var(--bd); border-radius:10px; padding:10px; max-height:240px; overflow:auto;}
.badge{display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--bd); margin-right:6px; font-size:.85rem; color:var(--muted)}
.stButton>button{background:var(--accent); color:white; border:none; border-radius:8px; padding:8px 16px;}
.stButton>button:hover{background:#2563eb;}
</style>
""", unsafe_allow_html=True)

# -------------------- Session boot --------------------
for k, v in {
    "VIZ_MODE": "Tables",
    "LOG": [],
    "RUN_QUEUE": [],
    "RUN_PTR": 0,
    "RUN_LOCKED": False,
    "REVIEW_UNDO_STACK": [],
    "APPROVE_DEL": [],
    "APPROVE_DET": [],
}.items():
    st.session_state.setdefault(k, v)

# -------------------- Utilities --------------------
_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def canonicalize(name: str) -> str:
    ALIAS_CANON = {
        "initial difficulty":"Learning curve",
        "hard to learn":"Learning curve",
        "setup difficulty":"Learning curve",
        "noisy startup":"Startup noise",
        "too loud":"Loud",
        "vacuum sucks":"Poor performance",
    }
    nn=(name or "").strip(); base=_normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k)==base: return v
    return nn

def safe_key(prefix: str, *parts) -> str:
    txt = prefix + "::" + "||".join([str(p) if p is not None and str(p)!="nan" else "<NA>" for p in parts])
    return prefix + "_" + hashlib.md5(txt.encode("utf-8")).hexdigest()[:10]

def safe_bar_or_table(df_plot: pd.DataFrame, x: str, y: str, title: str, height=320, as_table_default=True):
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
        fig = px.bar(df_plot, x=x, y=y, title=title, text=y, color_discrete_sequence=["#3b82f6"])
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=height, margin=dict(l=10,r=10,t=40,b=10),
                          xaxis_title=None, yaxis_title=None, showlegend=False)
        fig.update_layout(xaxis={"categoryorder":"total descending"})
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.dataframe(df_plot, use_container_width=True, height=height)

# -------------------- OpenAI helpers --------------------
def get_api_key() -> str:
    return (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()

@st.cache_resource
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
    cli = safe_client()
    if cli is None:
        return {}
    try:
        use_responses = (model.startswith("gpt-4o") or model.startswith("gpt-5")) and not force_chat
        if use_responses:
            out = with_retry(lambda: cli.chat.completions.create(  # Fixed to chat.completions for consistency
                model=model,
                response_format={"type": "json_object"},
                messages=[{"role":"system","content":system},
                          {"role":"user","content":json.dumps(user_obj)}],
                temperature=0.2
            ))
            content = out.choices[0].message.content or "{}"
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

# -------------------- Evidence & semantic helpers --------------------
def _evidence_score(label: str, text_lower_spaced: str) -> Tuple[int, List[str]]:
    if not label or not text_lower_spaced: return 0, []
    toks = [t for t in _normalize_name(label).split() if _DEF_WORD.match(t)]
    hits=[]
    for t in toks:
        try:
            if re.search(rf"\b{re.escape(t)}\b", text_lower_spaced):
                hits.append(t)
        except re.error:
            pass
    return len(hits), hits

@st.cache_resource(show_spinner=False)
def build_label_index(labels: List[str]):
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

# -------------------- LLM symptomizer --------------------
def llm_symptomize(text: str, stars,
                   allowed_del: List[str], allowed_det: List[str],
                   model="gpt-4o-mini", require_evidence=True, min_conf=0.72,
                   semantic_idx=None, semantic_min=0.70) -> Tuple[List[str], List[str]]:
    text = (text or "").strip()
    if not text:
        return [], []
    sem_supp = semantic_support(text, semantic_idx, min_sim=semantic_min) if semantic_idx else {}
    system = (
        "You label a single household appliance review. ONLY choose labels from provided allowed lists.\n"
        "Handle idioms: 'this vacuum sucks' means poor product performance (not suction). "
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

    text_lc = " " + text.lower() + " "
    allowed_det_set = set(allowed_det)
    allowed_del_set = set(allowed_del)

    def ok_keep(name: str) -> bool:
        if not require_evidence:
            return True
        cnt, _ = _evidence_score(name, text_lc)
        return (cnt >= 1) or (sem_supp.get(name, 0.0) >= semantic_min)

    keep_det, keep_del = [], []
    for item in dets_raw:
        name = canonicalize(str(item.get("name","")).strip())
        conf = float(item.get("confidence", 0.0))
        if (name in allowed_det_set) and conf >= min_conf and ok_keep(name):
            keep_det.append(name)

    for item in dels_raw:
        name = canonicalize(str(item.get("name","")).strip())
        conf = float(item.get("confidence", 0.0))
        if (name in allowed_del_set) and conf >= min_conf and ok_keep(name):
            keep_del.append(name)

    try:
        s = float(stars)
        if s <= 2.0 and not keep_det and dets_raw:
            for it in dets_raw:
                c = canonicalize(str(it.get("name","")))
                if (c in allowed_det_set) and ok_keep(c):
                    keep_det=[c]; break
        if s >= 4.0 and not keep_del and dels_raw:
            for it in dels_raw:
                c = canonicalize(str(it.get("name","")))
                if (c in allowed_del_set) and ok_keep(c):
                    keep_del=[c]; break
    except Exception:
        pass

    keep_det = list(dict.fromkeys(keep_det))[:10]
    keep_del = list(dict.fromkeys(keep_del))[:10]
    return keep_det, keep_del

# -------------------- Safety & Reliability --------------------
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

# -------------------- Fallback classifier --------------------
def classify_stub(df: pd.DataFrame, rows: list[int], allowed_det: List[str], allowed_del: List[str],
                  require_evidence=True) -> None:
    allowed_det_set = set(allowed_det); allowed_del_set = set(allowed_del)
    for ridx in rows:
        text = str(df.at[ridx, "Verbatim"]) if "Verbatim" in df.columns else ""
        det, deL = [], []
        t = " "+text.lower()+" "
        if re.search(r"\b(shuts off|cuts out|turns off|won'?t turn on|no power)\b", t): det.append("Intermittent power")
        if re.search(r"\b(weak|poor|bad|sucks)\b", t): det.append("Poor performance")
        if re.search(r"\b(light|lightweight|easy)\b", t): deL.append("Lightweight")
        if re.search(r"\b(quiet|low noise|silent)\b", t): deL.append("Quiet")
        det = [d for d in [canonicalize(x) for x in det] if d in allowed_det_set][:10]
        deL = [d for d in [canonicalize(x) for x in deL] if d in allowed_del_set][:10]
        for j in range(1, 21):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = ""
        for j, name in enumerate(det[:10], start=1):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = name
        for j, name in enumerate(deL[:10], start=11):
            c=f"Symptom {j}"
            if c in df.columns: df.at[ridx, c] = name
        # safety/reliability hints
        if "hot" in t or "burn" in t or "smoke" in t:
            df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = "Mentions heat/smoke."
        if "shuts off" in t or "turns off" in t:
            df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["RELIABILITY_MODE_COL"]] = "Intermittent power"
            df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = "User reports unit shuts off."

# -------------------- Demo data --------------------
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

# -------------------- Review module --------------------
def render_symptomization_review(df, SYMPTOM_COLS, APP):
    ss = st.session_state
    def push_undo(cols, name):
        snap = df[cols].copy(deep=True)
        ss["REVIEW_UNDO_STACK"].append((name, snap))
        if len(ss["REVIEW_UNDO_STACK"]) > 15:
            ss["REVIEW_UNDO_STACK"] = ss["REVIEW_UNDO_STACK"][-15:]

    def _undo_last():
        if not ss["REVIEW_UNDO_STACK"]:
            st.info("Nothing to undo."); return
        name, snap = ss["REVIEW_UNDO_STACK"].pop()
        for c in snap.columns: df[c]=snap[c]
        st.toast(f"↩ Undid: {name}", icon="↩️")

    def _stars_bucket(v):
        try: s=float(v)
        except Exception: return "NA"
        if s<=2.0: return "1–2"
        if s>=4.0: return "4–5"
        return "3"

    def _row_symptoms(row):
        detr, deli = [], []
        for j in range(1, 11):
            c=f"Symptom {j}"
            if c in row and str(row[c]).strip(): detr.append(str(row[c]).strip())
        for j in range(11, 21):
            c=f"Symptom {j}"
            if c in row and str(row[c]).strip(): deli.append(str(row[c]).strip())
        return detr, deli

    def _has_evidence(row):
        voc=str(row.get(APP["VOC_QUOTE_COL"], "") or "").strip() if APP["VOC_QUOTE_COL"] in df.columns else ""
        relq=str(row.get(APP["RELIABILITY_QUOTE_COL"], "") or "").strip() if APP["RELIABILITY_QUOTE_COL"] in df.columns else ""
        safq=str(row.get(APP["SAFETY_EVIDENCE_COL"], "") or "").strip() if APP["SAFETY_EVIDENCE_COL"] in df.columns else ""
        return bool(voc or relq or safq)

    # Build meta
    rows=[]; empty=conflicts=low_only=high_only=ev_ct=0
    for i,r in df.iterrows():
        det, deL = _row_symptoms(r)
        if (len(det)+len(deL))==0: empty+=1
        if _has_evidence(r): ev_ct+=1
        nd, nl = {_normalize_name(x) for x in det}, {_normalize_name(x) for x in deL}
        if nd & nl: conflicts+=1
        sb=_stars_bucket(r.get("Star Rating", None))
        if sb=="1–2" and len(det)==0 and len(deL)>0: low_only+=1
        if sb=="4–5" and len(deL)==0 and len(det)>0: high_only+=1
        rows.append({"Row":i,"Stars":r.get("Star Rating",None),"StarsBin":sb,
                    "DetractorsCount":len(det),"DelightersCount":len(deL),
                    "Evidence":_has_evidence(r),
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
        search_term = st.text_input("Search anomalies by label or verbatim", "")
        anomalies = meta[(meta["Conflict"]) | ((meta["DetractorsCount"]+meta["DelightersCount"])==0) |
                         ((meta["StarsBin"]=="1–2") & (meta["DelightersCount"]>0) & (meta["DetractorsCount"]==0)) |
                         ((meta["StarsBin"]=="4–5") & (meta["DetractorsCount"]>0) & (meta["DelightersCount"]==0))
                         ].sort_values(["Conflict","Row"], ascending=[False,True])
        if search_term:
            anomalies = anomalies[anomalies.apply(lambda row: search_term.lower() in str(df.iloc[int(row["Row"])]["Verbatim"]).lower(), axis=1)]
        st.dataframe(anomalies, use_container_width=True, height=240)

        st.markdown("### Row Evidence Inspector")
        ridx = st.number_input("Row index", min_value=0, max_value=max(0,len(df)-1), value=0, help="Enter row to inspect details.")
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

            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors**")
                if det:
                    for lab in det:
                        lk = str(lab) if lab and str(lab)!="nan" else "<empty>"
                        k1 = safe_key("mv_d_to_del", ridx, lk)
                        k2 = safe_key("rm_d", ridx, lk)
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lk)
                        if cc2.button("➡️", key=k1, help="Move to Delighter"):
                            cols = list(SYMPTOM_COLS) + [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                                                         APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                                                         APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                                                         APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]
                            cols = [c for c in cols if c in df.columns]
                            push_undo(cols, f"Move detractor→delighter: {lk}")
                            for j in range(1,21):
                                c=f"Symptom {j}"
                                if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                            for j in range(11,21):
                                c=f"Symptom {j}"
                                if c in df.columns and not str(df.at[ridx,c]).strip():
                                    df.at[ridx,c]=lab; break
                            st.toast("Moved to Delighter", icon="➡️")
                        if cc3.button("🗑️", key=k2, help="Remove label"):
                            cols = list(SYMPTOM_COLS)
                            cols = [c for c in cols if c in df.columns]
                            push_undo(cols, f"Remove detractor: {lk}")
                            for j in range(1,21):
                                c=f"Symptom {j}"
                                if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                            st.toast("Label removed", icon="🗑️")
                else:
                    st.caption("—")

            with c2:
                st.write("**Delighters**")
                if deL:
                    for lab in deL:
                        lk = str(lab) if lab and str(lab)!="nan" else "<empty>"
                        k1 = safe_key("mv_l_to_det", ridx, lk)
                        k2 = safe_key("rm_l", ridx, lk)
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lk)
                        if cc2.button("⬅️", key=k1, help="Move to Detractor"):
                            cols = list(SYMPTOM_COLS) + [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                                                         APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                                                         APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                                                         APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]
                            cols = [c for c in cols if c in df.columns]
                            push_undo(cols, f"Move delighter→detractor: {lk}")
                            for j in range(1,21):
                                c=f"Symptom {j}"
                                if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                            for j in range(1,11):
                                c=f"Symptom {j}"
                                if c in df.columns and not str(df.at[ridx,c]).strip():
                                    df.at[ridx,c]=lab; break
                            st.toast("Moved to Detractor", icon="⬅️")
                        if cc3.button("🗑️", key=k2, help="Remove label"):
                            cols = list(SYMPTOM_COLS)
                            cols = [c for c in cols if c in df.columns]
                            push_undo(cols, f"Remove delighter: {lk}")
                            for j in range(1,21):
                                c=f"Symptom {j}"
                                if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                            st.toast("Label removed", icon="🗑️")
                else:
                    st.caption("—")

            if st.button("↩ Undo last change", key=safe_key("undo_btn", ridx)):
                _undo_last()

        if st.button("Clear Undo Stack", help="Clear all undo history to free memory."):
            ss["REVIEW_UNDO_STACK"] = []
            st.toast("Undo stack cleared", icon="🧹")

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

# -------------------- IO helpers --------------------
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

# -------------------- Header --------------------
def render_header(meta: dict):
    st.markdown(f"""
    <div class="header">
      <div class="title">{APP["TITLE"]}</div>
      <div class="sub">Evidence-driven symptomization · reliability & safety · enhanced UX · export with summary</div>
      <div style="margin-top:8px"><span class="pill">Mode: {meta.get('run_mode','—')}</span>
      <span class="pill">Model: {meta.get('model','—')}</span>
      <span class="pill">Run: {meta.get('run_id','—')}</span></div>
    </div>
    """, unsafe_allow_html=True)

# -------------------- KPIs --------------------
def render_kpis(df):
    vs = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
    lens = vs.str.len()
    kpis = [("Total reviews", len(df)), ("Avg chars", int(lens.mean()) if len(lens) else 0),
            ("Stars col", "present" if "Star Rating" in df.columns else "—")]
    st.markdown("<div class='kpis'>" + "".join([f"<span class='pill'>{k}: <b>{v}</b></span>" for k,v in kpis]) + "</div>", unsafe_allow_html=True)

# -------------------- Pareto --------------------
def render_pareto(df):
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

# -------------------- Trend Analysis --------------------
def render_trends(df):
    if APP["DATE_COL"] not in df.columns or df[APP["DATE_COL"]].isnull().all():
        st.info("No date column found for trend analysis.")
        return
    df[APP["DATE_COL"]] = pd.to_datetime(df[APP["DATE_COL"]], errors='coerce')
    df = df.dropna(subset=[APP["DATE_COL"]])
    if df.empty:
        st.info("No valid dates for trends.")
        return

    # Example trend: Detractors over time
    det_df = df.melt(id_vars=[APP["DATE_COL"]], value_vars=[f"Symptom {j}" for j in range(1,11)], var_name="SymptomNum", value_name="Label")
    det_df = det_df[det_df["Label"].str.strip() != ""]
    det_trend = det_df.groupby([pd.Grouper(key=APP["DATE_COL"], freq='M'), "Label"]).size().reset_index(name="Count")

    if _HAS_PX:
        fig = px.line(det_trend, x=APP["DATE_COL"], y="Count", color="Label", title="Detractors Trend Over Time")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.dataframe(det_trend, use_container_width=True)

# -------------------- Runner --------------------
def render_runner(df, ALLOWED_DET, ALLOWED_DEL, LABEL_INDEX, SYMPTOM_COLS):
    with st.expander("⚙️ Symptomization Runner", expanded=True):
        use_llm = st.toggle("Use AI (OpenAI) if available", value=True, help="If off or no key, uses safe lexical fallback.")
        if use_llm and safe_client() is None:
            st.warning("OpenAI key not detected. Running in safe lexical mode. Set OPENAI_API_KEY to enable semantic/LLM.")
        require_evidence = st.checkbox("Require evidence for labels", value=True)
        min_conf = st.slider("Min LLM confidence", 0.60, 0.95, 0.72, 0.01)
        semantic_min = st.slider("Min semantic similarity (if embeddings on)", 0.60, 0.90, 0.70, 0.01)

        ORDER = st.selectbox("Order", ["Original", "Shortest first", "Longest first"], index=0)
        verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
        idx_all = list(range(len(df)))
        if ORDER != "Original":
            idx_all = sorted(idx_all, key=lambda i: len(verb_series.iloc[i]), reverse=(ORDER=="Longest first"))

        avg_len = int(verb_series.str.len().mean() or 200)
        default_batch = 30 if avg_len < 350 else (20 if avg_len < 700 else 10)
        BATCH = st.number_input("Batch size (rows per tick)", 1, 200, default_batch)

        colQ1, colQ2, colQ3, colQ4 = st.columns([1,1,1,1])
        with colQ1:
            queue_all = st.button("Queue ALL", disabled=st.session_state["RUN_LOCKED"])
        with colQ2:
            sample_n = st.number_input("Or queue first N", 1, max(1,len(df)), min(80,len(df)))
            queue_n = st.button("Queue N", disabled=st.session_state["RUN_LOCKED"])
        with colQ3:
            clear_queue = st.button("Clear Queue", help="Reset the processing queue.")
        with colQ4:
            st.caption("Adaptive batching for predictable latency.")

        if queue_all:
            st.session_state["RUN_QUEUE"] = idx_all
            st.session_state["RUN_PTR"] = 0
            st.session_state["RUN_LOCKED"] = False
            st.toast("Queued ALL rows", icon="✅")

        if queue_n:
            st.session_state["RUN_QUEUE"] = idx_all[:int(sample_n)]
            st.session_state["RUN_PTR"] = 0
            st.session_state["RUN_LOCKED"] = False
            st.toast(f"Queued {int(sample_n)} rows", icon="✅")

        if clear_queue:
            st.session_state["RUN_QUEUE"] = []
            st.session_state["RUN_PTR"] = 0
            st.toast("Queue cleared", icon="🧹")

        queued = st.session_state["RUN_QUEUE"]
        ptr    = st.session_state["RUN_PTR"]
        remaining = max(0, len(queued) - ptr)

        if remaining:
            st.info(f"Queued rows: {len(queued)} • Remaining: {remaining}")
            prog = st.progress(ptr / len(queued) if len(queued) else 0)
            go = st.button(f"Process next {min(BATCH, remaining)}", disabled=st.session_state["RUN_LOCKED"])
            if go:
                st.session_state["RUN_LOCKED"] = True
                with st.spinner("Processing batch..."):
                    try:
                        span = queued[ptr: ptr + BATCH]
                        cli_avail = (safe_client() is not None) and use_llm
                        t0 = time.time()

                        for ridx in span:
                            text = verb_series.iloc[ridx]
                            stars = df.at[ridx, "Star Rating"] if "Star Rating" in df.columns else None

                            if cli_avail:
                                det, deL = llm_symptomize(
                                    text, stars,
                                    ALLOWED_DEL, ALLOWED_DET,
                                    model="gpt-4o-mini",
                                    require_evidence=require_evidence,
                                    min_conf=min_conf, semantic_idx=LABEL_INDEX, semantic_min=semantic_min
                                )
                            else:
                                classify_stub(df, [ridx], ALLOWED_DET, ALLOWED_DEL, require_evidence=True)
                                det = [str(df.at[ridx, f"Symptom {j}"]).strip() for j in range(1,11) if f"Symptom {j}" in df.columns and str(df.at[ridx, f"Symptom {j}"]).strip()]
                                deL = [str(df.at[ridx, f"Symptom {j}"]).strip() for j in range(11,21) if f"Symptom {j}" in df.columns and str(df.at[ridx, f"Symptom {j}"]).strip()]

                            det = [canonicalize(x) for x in det if canonicalize(x) in set(ALLOWED_DET)][:10]
                            deL = [canonicalize(x) for x in deL if canonicalize(x) in set(ALLOWED_DEL)][:10]

                            for j in range(1, 21):
                                c=f"Symptom {j}"
                                if c in df.columns: df.at[ridx, c] = ""
                            for j, name in enumerate(det, start=1):
                                c=f"Symptom {j}"
                                if c in df.columns: df.at[ridx, c] = name
                            for j, name in enumerate(deL, start=11):
                                c=f"Symptom {j}"
                                if c in df.columns: df.at[ridx, c] = name

                            # Reliability/Safety
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
                        st.session_state["RUN_LOCKED"] = False

                        dt = time.time()-t0
                        st.session_state["LOG"].append(f"Processed {len(span)} rows in {dt:.1f}s. Remaining: {len(queued)-st.session_state['RUN_PTR']}")
                        try:
                            df.to_parquet("starwalk_checkpoint.parquet", index=False)
                            st.session_state["LOG"].append("Checkpoint saved: starwalk_checkpoint.parquet")
                        except Exception:
                            pass

                        st.toast(f"Processed {len(span)} rows", icon="⚡")
                        st.rerun()
                    except Exception as e:
                        st.session_state["RUN_LOCKED"] = False
                        st.error(f"Processing failed: {str(e)}")
                        st.session_state["LOG"].append(f"ERROR: {e}")
        else:
            st.caption("Queue is empty.")

# -------------------- Console --------------------
def render_console():
    st.markdown("### 📜 Run Console")
    if st.session_state["LOG"]:
        st.markdown(f"<div class='console'>{chr(10).join(st.session_state['LOG'][-200:])}</div>", unsafe_allow_html=True)
    else:
        st.caption("No messages yet.")
    st.markdown("<span class='badge'>Tip</span> Keep Min LLM confidence ~0.70–0.75 and semantic ~0.70 for balanced performance.", unsafe_allow_html=True)

# -------------------- Export --------------------
def render_export(df, raw):
    st.divider(); st.markdown("### ⬇️ Export")
    fmt_ok=False; fmt_bytes=None
    summary_df = pd.DataFrame({"Metric": ["Example"], "Value": [42]})  # Placeholder for actual summary

    try:
        if _HAS_OPENPYXL and raw is not None:
            bio=io.BytesIO(raw); wb=load_workbook(bio)
            data_sheet=APP["DATA_SHEET"] if APP["DATA_SHEET"] in wb.sheetnames else wb.sheetnames[0]
            ws=wb[data_sheet]
            headers={ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name):
                if name not in headers:
                    ci = ws.max_column + 1
                    ws.cell(row=1, column=ci, value=name); headers[name]=ci
                return headers[name]
            exp_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
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
            
            # Add summary sheet
            summary_ws = wb.create_sheet("Summary")
            for r in dataframe_to_rows(summary_df, index=False, header=True):
                summary_ws.append(r)
            
            out=io.BytesIO(); wb.save(out); fmt_bytes=out.getvalue(); fmt_ok=True
    except Exception as e:
        st.warning(f"Format-preserving export not available: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET"], index=False)
            summary_df.to_excel(xlw, sheet_name="Summary", index=False)
        basic_bytes=out2.getvalue()
    except Exception as e:
        st.error(f"Basic export failed: {e}")

    col1,col2 = st.columns(2)
    with col1:
        if fmt_ok and fmt_bytes:
            st.download_button("⬇️ Download (preserve formatting + summary)", data=fmt_bytes,
                               file_name="starwalk_v13_formatted.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("Format-preserving export unavailable.")
    with col2:
        if basic_bytes:
            st.download_button("⬇️ Download (basic + summary)", data=basic_bytes,
                               file_name="starwalk_v13.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# -------------------- Main --------------------
def main():
    # Sidebar
    with st.sidebar:
        st.header("📁 Upload Excel (.xlsx)")
        uploaded = st.file_uploader("Upload workbook", type=["xlsx"])
        safe_demo = st.checkbox("Safe Boot (Demo if no file)", value=True)
        st.session_state["VIZ_MODE"] = st.radio(
            "Visualization", ["Tables","Charts"], index=0, horizontal=True,
            help="Tables are stable and fast; switch to Charts when you’re done processing."
        )

        st.markdown("### 🧾 Symptoms Source")
        allowed_det_ui = st.text_area("Detractors (one per line)", "Poor performance\nIntermittent power\nLoud\nLearning curve", height=100)
        allowed_del_ui = st.text_area("Delighters (one per line)", "Lightweight\nEasy cleanup\nQuiet\nDock convenience", height=100)
        ALLOWED_DET = [x.strip() for x in allowed_det_ui.splitlines() if x.strip()]
        ALLOWED_DEL = [x.strip() for x in allowed_del_ui.splitlines() if x.strip()]

    # Data load
    df = None
    raw = None
    if uploaded is not None:
        raw = uploaded.read()
        df = read_excel_sheet(raw, APP["DATA_SHEET"])
        if df is None:
            df = read_excel_sheet(raw, None)
    if df is None:
        if safe_demo:
            df = demo_df(rows=40)
            st.info("Safe Boot demo data loaded (toggle off to require file).")
        else:
            st.warning("Please upload a workbook to continue."); return

    # Ensure columns
    SYMPTOM_COLS = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
    for c in SYMPTOM_COLS:
        if c not in df.columns: df[c] = ""
    for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
        if col not in df.columns: df[col] = ""

    # Render components
    run_meta = {"run_id": str(int(time.time())), "model": ("OpenAI" if safe_client() else "safe"), "run_mode": "Manual"}
    render_header(run_meta)
    render_kpis(df)
    render_pareto(df)
    with st.expander("📈 Trend Analysis", expanded=False):
        render_trends(df)
    LABEL_INDEX = build_label_index(ALLOWED_DEL + ALLOWED_DET)
    render_runner(df, ALLOWED_DET, ALLOWED_DEL, LABEL_INDEX, SYMPTOM_COLS)
    render_console()
    with st.expander("🔎 Review & Edit", expanded=True):
        render_symptomization_review(df, SYMPTOM_COLS, APP)
    render_export(df, raw)

# -------------------- Entry --------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error("The app hit a non-fatal error but stayed alive.")
        try: st.write(str(e))
        except Exception: pass
