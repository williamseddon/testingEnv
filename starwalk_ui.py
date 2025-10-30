# ========================= Star Walk QE v8.1 — Safe Boot =========================
# Streamlit >= 1.38

import io, os, re, json, time, random, hashlib
from typing import List, Tuple, Optional, Dict, Any
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
import streamlit as st

# -------------------- App constants --------------------
APP = {
    "TITLE": "Star Walk QE — v8.1 (Safe Boot)",
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
:root{
  --fg:#0f172a; --muted:#64748b; --bd:#e2e8f0; --card:#ffffff;
}
@media (prefers-color-scheme: dark){
  :root{ --fg:#e5e7eb; --muted:#9ca3af; --bd:#374151; --card:#0a1020; }
}
.block-container{padding-top:.6rem; padding-bottom:1rem; max-width:1480px}
.header,.card{background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:14px}
.title{font-size:clamp(20px,2.6vw,32px); font-weight:800}
.sub{color:var(--muted)}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.pill{display:inline-flex;gap:6px;align-items:center;padding:6px 10px;border-radius:999px;border:1.5px solid var(--bd);background:var(--card);font-weight:700;color:var(--fg)}
.skel{height:340px}
</style>
""", unsafe_allow_html=True)

# -------------------- Safe utilities --------------------
def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def canonicalize(name: str) -> str:
    ALIAS_CANON = {
        "initial difficulty":"Learning curve",
        "hard to learn":"Learning curve",
        "setup difficulty":"Learning curve",
        "noisy startup":"Startup noise",
        "too loud":"Loud",
        "vacuum sucks":"Poor performance",  # idiom
    }
    nn=(name or "").strip(); base=_normalize_name(nn)
    for k,v in ALIAS_CANON.items():
        if _normalize_name(k)==base: return v
    return nn

def header(meta: dict):
    st.markdown(f"""
    <div class="header">
      <div class="title">{APP["TITLE"]}</div>
      <div class="sub">Safe Boot: never-crash UX · evidence-driven tagging · reliability & safety · export.</div>
      <div style="margin-top:8px"><span class="pill">Mode: {meta.get('run_mode','—')}</span>
      <span class="pill">Model: {meta.get('model','—')}</span>
      <span class="pill">Run: {meta.get('run_id','—')}</span></div>
    </div>
    """, unsafe_allow_html=True)

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
        fig = px.bar(df_plot, x=x, y=y, title=title, text=y)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=height, margin=dict(l=10,r=10,t=40,b=10),
                          xaxis_title=None, yaxis_title=None, showlegend=False)
        fig.update_layout(xaxis={"categoryorder":"total descending"})
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.dataframe(df_plot, use_container_width=True, height=height)

# -------------------- Data access --------------------
@st.cache_data(show_spinner=False)
def read_excel_sheet(uploaded_bytes: bytes, sheet_name: Optional[str]):
    bio = io.BytesIO(uploaded_bytes)
    try:
        if sheet_name:
            return pd.read_excel(bio, sheet_name=sheet_name)
        else:
            return pd.read_excel(bio)
    except Exception as e:
        return None

def demo_df(rows=30):
    # Minimal demo data to always load
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
    # Precreate 1–20 symptom cols
    for j in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1):
        data[f"{APP['SYMPTOM_PREFIX']}{j}"] = ""
    df = pd.DataFrame(data)
    # Seed a few tags for viewability
    df.at[0,"Symptom 11"]="Lightweight"; df.at[0,"Symptom 12"]="Easy cleanup"
    df.at[1,"Symptom 1"]="Poor performance"; df.at[1,"Symptom 2"]="Intermittent power"
    return df

# -------------------- Review module (scoped & safe) --------------------
def render_symptomization_review(df, SYMPTOM_COLS, APP):
    import pandas as pd
    from collections import Counter
    from contextlib import contextmanager

    ss = st.session_state
    if "REVIEW_UNDO_STACK" not in ss: ss["REVIEW_UNDO_STACK"] = []

    def _stars_bucket(v):
        try: s=float(v)
        except Exception: return "NA"
        if s<=2.0: return "1–2"
        if s>=4.0: return "4–5"
        return "3"

    def _row_symptoms(row):
        detr, deli = [], []
        for j in range(1, 11):
            c=f"Symptom {j}"; 
            if c in row and str(row[c]).strip(): detr.append(str(row[c]).strip())
        for j in range(11, 21):
            c=f"Symptom {j}"; 
            if c in row and str(row[c]).strip(): deli.append(str(row[c]).strip())
        return detr, deli

    def _has_evidence(row):
        voc=str(row.get(APP["VOC_QUOTE_COL"], "") or "").strip() if APP["VOC_QUOTE_COL"] in df.columns else ""
        relq=str(row.get(APP["RELIABILITY_QUOTE_COL"], "") or "").strip() if APP["RELIABILITY_QUOTE_COL"] in df.columns else ""
        safq=str(row.get(APP["SAFETY_EVIDENCE_COL"], "") or "").strip() if APP["SAFETY_EVIDENCE_COL"] in df.columns else ""
        return bool(voc or relq or safq)

    @contextmanager
    def _undoable(name):
        try:
            cols = list(SYMPTOM_COLS)
            for c in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                      APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                      APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                      APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
                if c in df.columns: cols.append(c)
            snap=df[cols].copy(deep=True); yield
            ss["REVIEW_UNDO_STACK"].append((name, snap))
            if len(ss["REVIEW_UNDO_STACK"])>10: ss["REVIEW_UNDO_STACK"]=ss["REVIEW_UNDO_STACK"][-10:]
            st.success(f"✔ {name} (undo available)")
        except Exception as e:
            st.error(f"{name} failed: {e}")

    def _undo_last():
        if not ss["REVIEW_UNDO_STACK"]:
            st.info("Nothing to undo."); return
        name, snap = ss["REVIEW_UNDO_STACK"].pop()
        for c in snap.columns: df[c]=snap[c]
        st.warning(f"↩ Undid: {name}")

    # Build metrics
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

    # UI
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
            det, deL = _row_symptoms(row)
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
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lab)
                        if cc2.button("➡️ to Delighter", key=f"mv_d_{ridx}_{lab}"):
                            with _undoable(f"Move {lab} to Delighter"):
                                # clear
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                # add
                                for j in range(11,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and not str(df.at[ridx,c]).strip():
                                        df.at[ridx,c]=lab; break
                        if cc3.button("🗑️", key=f"rm_d_{ridx}_{lab}"):
                            with _undoable(f"Remove {lab}"):
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""; break
                else:
                    st.caption("—")
            with c2:
                st.write("**Delighters**")
                if deL:
                    for lab in deL:
                        cc1,cc2,cc3 = st.columns([5,1,1])
                        cc1.write(lab)
                        if cc2.button("➡️ to Detractor", key=f"mv_l_{ridx}_{lab}"):
                            with _undoable(f"Move {lab} to Detractor"):
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""
                                for j in range(1,11):
                                    c=f"Symptom {j}"
                                    if c in df.columns and not str(df.at[ridx,c]).strip():
                                        df.at[ridx,c]=lab; break
                        if cc3.button("🗑️", key=f"rm_l_{ridx}_{lab}"):
                            with _undoable(f"Remove {lab}"):
                                for j in range(1,21):
                                    c=f"Symptom {j}"
                                    if c in df.columns and str(df.at[ridx,c]).strip()==lab: df.at[ridx,c]=""; break
            if st.button("↩ Undo last change"):
                _undo_last()

    with tab_labels:
        def per_label(df_, side: str):
            rng = range(1,11) if side=="Detractor" else range(11,21)
            label_counts=Counter(); low=Counter(); high=Counter(); evid=Counter()
            for _,r in df_.iterrows():
                sb = "NA"
                try:
                    s=float(r.get("Star Rating", np.nan))
                    if not np.isnan(s):
                        sb = "1–2" if s<=2 else ("4–5" if s>=4 else "3")
                except Exception: pass
                ev = False
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
        det_tbl = per_label(df, "Detractor")
        del_tbl = per_label(df, "Delighter")
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

# -------------------- Core classify stubs (safe) --------------------
def classify_stub(df: pd.DataFrame, rows: list[int], allowed_det: List[str], allowed_del: List[str],
                  require_evidence=True) -> None:
    # Safe placeholder classification that never crashes; replace with your v8 classifier if desired
    for ridx in rows:
        text = str(df.at[ridx, "Verbatim"]) if "Verbatim" in df.columns else ""
        stars = df.at[ridx, "Star Rating"] if "Star Rating" in df.columns else None
        det, deL = [], []
        t = " "+text.lower()+" "
        # tiny lexical examples
        if re.search(r"\b(shuts off|cuts out|turns off|won't turn on|no power)\b", t): det.append("Intermittent power")
        if re.search(r"\b(weak|poor|bad|sucks)\b", t): det.append("Poor performance")
        if re.search(r"\b(light|lightweight|easy)\b", t): deL.append("Lightweight")
        if re.search(r"\b(quiet|low noise|silent)\b", t): deL.append("Quiet")
        det = [d for d in [canonicalize(x) for x in det] if d in set(allowed_det or [])][:10]
        deL = [d for d in [canonicalize(x) for x in deL] if d in set(allowed_del or [])][:10]
        # write to df
        for j in range(1, 11):
            c=f"Symptom {j}"; 
            if c in df.columns: df.at[ridx, c] = (det[j-1] if j-1<len(det) else "")
        for j in range(11, 21):
            c=f"Symptom {j}";
            if c in df.columns: df.at[ridx, c] = (deL[j-11] if j-11<len(deL) else "")
        # minimal safety/reliability evidence
        if "hot" in t or "burn" in t or "smoke" in t: 
            df.at[ridx, APP["SAFETY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["SAFETY_EVIDENCE_COL"]] = "Mentions heat/smoke."
        if "shuts off" in t or "turns off" in t:
            df.at[ridx, APP["RELIABILITY_FLAG_COL"]] = "Yes"
            df.at[ridx, APP["RELIABILITY_MODE_COL"]] = "Intermittent power"
            df.at[ridx, APP["RELIABILITY_QUOTE_COL"]] = "User reports unit shuts off."

# -------------------- Main --------------------
def main():
    # Sidebar
    st.sidebar.header("📁 Upload Excel (.xlsx)")
    uploaded = st.sidebar.file_uploader("Upload workbook", type=["xlsx"])
    safe_demo = st.sidebar.checkbox("Safe Boot (Demo if no file)", value=True)
    st.session_state["VIZ_MODE"] = st.sidebar.radio("Visualization", ["Tables","Charts"], index=0, horizontal=True)

    # Allowed lists (minimal UI & robust fallback)
    st.sidebar.markdown("### 🧾 Symptoms Source")
    allowed_det = st.sidebar.text_area("Detractors (one per line)", "Poor performance\nIntermittent power\nLoud\nLearning curve")
    allowed_del = st.sidebar.text_area("Delighters (one per line)", "Lightweight\nEasy cleanup\nQuiet\nDock convenience")

    # Data load
    df = None
    sheet_names = []
    if uploaded is not None:
        uploaded.seek(0)
        raw = uploaded.read()
        uploaded.seek(0)
        # Try primary sheet; else first sheet
        df = read_excel_sheet(raw, APP["DATA_SHEET"])
        if df is None:
            df = read_excel_sheet(raw, None)
    if df is None:
        if safe_demo:
            df = demo_df(rows=30)
            st.info("Safe Boot demo data loaded (toggle off to require file).")
        else:
            st.warning("Please upload a workbook to continue."); return

    # Ensure symptom and QE columns
    exp_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1]+1)]
    for c in exp_cols:
        if c not in df.columns: df[c] = ""
    for col in [APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"]]:
        if col not in df.columns: df[col] = ""

    # Header
    RUN_META = {"run_id": str(int(time.time())), "model": "safe", "run_mode": "Manual"}
    header(RUN_META)

    # KPIs
    vs = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
    lens = vs.str.len()
    kpis = [("Total reviews", len(df)), ("Avg chars", int(lens.mean()) if len(lens) else 0),
            ("Stars col", "present" if "Star Rating" in df.columns else "—")]
    st.markdown("<div class='kpis'>" + "".join([f"<span class='pill'>{k}: <b>{v}</b></span>" for k,v in kpis]) + "</div>", unsafe_allow_html=True)

    # Actions
    cA,cB,cC = st.columns([1,1,1])
    with cA:
        sample_n = st.number_input("Sample N", min_value=1, max_value=len(df), value=min(20, len(df)))
    with cB:
        run_sample = st.button("✨ Run Sample (safe)")
    with cC:
        run_all = st.button("⚡ Run ALL (safe)")

    # Run (safe local classifier so app NEVER blocks on APIs)
    idxs = []
    if run_sample:
        idxs = list(range(int(sample_n)))
    if run_all:
        idxs = list(range(len(df)))
    if idxs:
        classify_stub(df, idxs,
                      [l.strip() for l in allowed_det.splitlines() if l.strip()],
                      [l.strip() for l in allowed_del.splitlines() if l.strip()],
                      require_evidence=True)
        st.success(f"Processed {len(idxs)} rows.")

    # Snapshot (stable)
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

    st.markdown("**Top Detractors**")
    safe_bar_or_table(df_det, "Detractor", "Count", "Detractors Pareto", height=320, as_table_default=True)
    st.markdown("**Top Delighters**")
    safe_bar_or_table(df_del, "Delighter", "Count", "Delighters Pareto", height=320, as_table_default=True)

    # Review+
    render_symptomization_review(df, exp_cols, APP)

    # Export
    st.divider(); st.markdown("### ⬇️ Export")
    fmt_ok=False; fmt_bytes=None
    try:
        if _HAS_OPENPYXL and uploaded is not None:
            uploaded.seek(0); raw = uploaded.read(); uploaded.seek(0)
            bio=io.BytesIO(raw); wb=load_workbook(bio)
            data_sheet=APP["DATA_SHEET"] if APP["DATA_SHEET"] in wb.sheetnames else wb.sheetnames[0]
            ws=wb[data_sheet]
            # ensure headers
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
            out=io.BytesIO(); wb.save(out); fmt_bytes=out.getvalue(); fmt_ok=True
    except Exception as e:
        st.warning(f"Format-preserving export not available: {e}")

    basic_bytes=None
    try:
        out2=io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET"], index=False)
        basic_bytes=out2.getvalue()
    except Exception as e:
        st.error(f"Basic export failed: {e}")

    col1,col2 = st.columns(2)
    with col1:
        if fmt_ok and fmt_bytes:
            st.download_button("⬇️ Download (preserve formatting)", data=fmt_bytes,
                               file_name="starwalk_safe_formatted.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("Format-preserving export unavailable.")
    with col2:
        if basic_bytes:
            st.download_button("⬇️ Download (basic)", data=basic_bytes,
                               file_name="starwalk_safe.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Final safety net: never crash the app
        st.error("The app hit a non-fatal error but stayed alive.")
        try: st.write(str(e))
        except Exception: pass
