# ---------- Star Walk v2 — Upload + Symptomize (Enhanced UX, Accuracy, Explainability) ----------
# Requires: Streamlit 1.38+
# Optional: openai, openpyxl, plotly, scikit-learn
# Notes:
# - Preserves your original workbook I/O strategies and session-state structure
# - Adds negation-aware evidence, fused confidence, verify pass, explain UI, undo, dry-run, and logs

import io
import os
import re
import json
import difflib
from typing import List, Tuple, Optional, Dict, Any

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html
from collections import defaultdict

# ---------------- Optional OpenAI ----------------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# ---------------- Optional: preserve workbook formatting ----------------
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ---------------- Optional: small viz ----------------
try:
    import plotly.express as px
    _HAS_PX = True
except Exception:
    _HAS_PX = False

# ---------------- Optional: clustering/topic extraction ----------------
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    _HAS_SK = True
except Exception:
    _HAS_SK = False

# ---------------- App Config ----------------
APP = {
    "PAGE_TITLE": "Star Walk — Symptomize Reviews (v2)",
    "DATA_SHEET_DEFAULT": "Star Walk scrubbed verbatims",
    "HIDDEN_SHEET": "__StarWalk_Approved",
    "SYMPTOM_PREFIX": "Symptom ",
    "SYMPTOM_RANGE": (1, 20),  # 1–10 detractors, 11–20 delighters
    "EMB_MODEL": "text-embedding-3-small",
    "CHAT_DEFAULT": "gpt-4o-mini",
    "CHAT_FAST": "gpt-4o-mini",
    "CHAT_STRICT": "gpt-4.1",
    "ETA_TOKS_PER_CHAR": 0.25,        # rough tokenization
    "COST_PER_1K_INPUT": 0.00015,     # adjust to your pricing
    "COST_PER_1K_OUTPUT": 0.0006,     # adjust to your pricing
}

# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title=APP["PAGE_TITLE"])

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
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:10px 18px; color: var(--text); }
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
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
  .chip{padding:6px 10px;border-radius:999px;border:1.5px solid var(--border);background:var(--bg-tile);font-weight:700;font-size:.9rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}

  /* Sticky toolbar */
  .sticky-toolbar {
    position: sticky; top: 62px; z-index: 50;
    display:flex; gap:10px; align-items:center; flex-wrap:wrap;
    padding:8px 10px; background:var(--bg-card);
    border:1px solid var(--border); border-radius:10px;
    box-shadow:0 4px 12px rgba(0,0,0,0.05); margin:8px 0 16px 0;
  }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--border);
    background:var(--bg-tile); font-size:12px; }
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
st.sidebar.header("📁 Step 1 — Upload")
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
        df = pd.read_excel(uploaded, sheet_name=APP["DATA_SHEET_DEFAULT"])
    except ValueError:
        df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Could not read the Excel file: {e}")
    st.stop()

# ---------------- Identify Symptom Columns ----------------
explicit_cols = [f"{APP['SYMPTOM_PREFIX']}{i}" for i in range(APP["SYMPTOM_RANGE"][0], APP["SYMPTOM_RANGE"][1] + 1)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()  # K–AD fallback
if not SYMPTOM_COLS:
    st.error(f"Couldn't locate {APP['SYMPTOM_PREFIX']}1–{APP['SYMPTOM_RANGE'][1]} columns (K–AD).")
    st.stop()

# Warn when using positional fallback
if SYMPTOM_COLS and not all(isinstance(c, str) and str(c).lower().startswith("symptom ") for c in SYMPTOM_COLS):
    st.warning("Symptom columns inferred by position. Verify headers to avoid writing into the wrong columns.")

# Missing symptom rows
is_empty = df[SYMPTOM_COLS].isna() | (
    df[SYMPTOM_COLS]
    .astype(str)
    .applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"})
)
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Basic review text stats
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
lengths = verb_series.str.len()
q1 = lengths.quantile(0.25) if not lengths.empty else 0
q3 = lengths.quantile(0.75) if not lengths.empty else 0
IQR = (q3 - q1) if (q3 or q1) else 0

# ---------------- Load symptom dictionary from "Symptoms" sheet (robust + user overrides + hidden cache) ----------------
import io as _io
HIDDEN_SHEET = APP["HIDDEN_SHEET"]  # hidden cache sheet for approved items

# Helpers to robustly find sheets/columns
def _norm(s: str) -> str:
    if s is None:
        return ""
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    return "symptom" in _norm(name)

def _col_score(colname: str, want: str) -> int:
    n = _norm(colname)
    if not n:
        return 0
    synonyms = {
        "delighters": ["delight", "delighters", "pros", "positive", "positives", "likes", "good"],
        "detractors": ["detract", "detractors", "cons", "negative", "negatives", "dislikes", "bad", "issues"],
    }
    return max((1 for token in synonyms[want] if token in n), default=0)

def _extract_from_df(df_sheet: pd.DataFrame):
    """Try multiple layouts and return (delighters, detractors, debug)."""
    debug = {"strategy": None, "columns": list(df_sheet.columns)}
    # Strategy 1: fuzzy headers
    best_del = None
    best_det = None
    for c in df_sheet.columns:
        if _col_score(str(c), "delighters"):
            best_del = c if best_del is None else best_del
        if _col_score(str(c), "detractors"):
            best_det = c if best_det is None else best_det
    if best_del is not None or best_det is not None:
        dels_ser = df_sheet.get(best_del, pd.Series(dtype=str)) if best_del is not None else pd.Series(dtype=str)
        dets_ser = df_sheet.get(best_det, pd.Series(dtype=str)) if best_det is not None else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels_ser.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets_ser.dropna().tolist() if str(x).strip()]
        if dels or dets:
            debug.update({"strategy": "fuzzy-headers", "best_del_col": best_del, "best_det_col": best_det})
            return dels, dets, debug
    # Strategy 2: Type/Category + Item
    type_col = None
    item_col = None
    for c in df_sheet.columns:
        if _norm(c) in {"type", "category", "class", "label"}:
            type_col = c
        if _norm(c) in {"item", "symptom", "name", "term", "entry", "value"}:
            item_col = c
    if type_col is not None and item_col is not None:
        t = df_sheet[type_col].astype(str).str.strip().str.lower()
        i = df_sheet[item_col].astype(str).str.strip()
        dels = i[t.str.contains("delight|pro|positive", na=False)]
        dets = i[t.str.contains("detract|con|negative", na=False)]
        dels = [x for x in dels.dropna().tolist() if x]
        dets = [x for x in dets.dropna().tolist() if x]
        if dels or dets:
            debug.update({"strategy": "type+item", "type_col": type_col, "item_col": item_col})
            return dels, dets, debug
    # Strategy 3: first two non-empty columns
    non_empty_cols = []
    for c in df_sheet.columns:
        vals = [str(x).strip() for x in df_sheet[c].dropna().tolist() if str(x).strip()]
        if vals:
            non_empty_cols.append((c, vals))
        if len(non_empty_cols) >= 2:
            break
    if non_empty_cols:
        dels = non_empty_cols[0][1]
        dets = non_empty_cols[1][1] if len(non_empty_cols) > 1 else []
        debug.update({"strategy": "first-two-nonempty", "picked_cols": [c for c, _ in non_empty_cols[:2]]})
        return dels, dets, debug
    return [], [], {"strategy": "none", "columns": list(df_sheet.columns)}

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> Optional[str]:
    names = xls.sheet_names
    cands = [n for n in names if _looks_like_symptom_sheet(n)]
    if cands:
        return min(cands, key=lambda n: len(_norm(n)))
    return names[0] if names else None

def load_hidden_approvals(xls: pd.ExcelFile) -> tuple[list[str], list[str]]:
    dels_extra, dets_extra = [], []
    try:
        if HIDDEN_SHEET in xls.sheet_names:
            hdf = pd.read_excel(xls, sheet_name=HIDDEN_SHEET)
            # Prefer explicit headers
            if "Approved Delighters" in hdf.columns:
                dels_extra = [str(x).strip() for x in hdf["Approved Delighters"].dropna().tolist() if str(x).strip()]
            if "Approved Detractors" in hdf.columns:
                dets_extra = [str(x).strip() for x in hdf["Approved Detractors"].dropna().tolist() if str(x).strip()]
            # Fallback to first two non-empty columns
            if not (dels_extra or dets_extra) and len(hdf.columns) >= 1:
                cols = list(hdf.columns)
                c1 = hdf[cols[0]].dropna().astype(str).str.strip().tolist()
                dels_extra = [x for x in c1 if x]
                if len(cols) > 1:
                    c2 = hdf[cols[1]].dropna().astype(str).str.strip().tolist()
                    dets_extra = [x for x in c2 if x]
    except Exception:
        pass
    return dels_extra, dets_extra

def load_symptom_lists_robust(
    raw_bytes: bytes,
    user_sheet: Optional[str] = None,
    user_del_col: Optional[str] = None,
    user_det_col: Optional[str] = None,
):
    meta: Dict[str, Any] = {"sheet": None, "strategy": None, "columns": [], "note": ""}
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
        meta.update({"strategy": "manual-columns", "columns": list(s.columns)})
    else:
        dels, dets, info = _extract_from_df(s)
        meta.update(info)
    # merge hidden approvals
    try:
        dels_extra, dets_extra = load_hidden_approvals(xls)
        if dels_extra:
            dels = list(dict.fromkeys(dels + dels_extra))
        if dets_extra:
            dets = list(dict.fromkeys(dets + dets_extra))
    except Exception:
        pass
    return dels, dets, meta

# ---- Symptoms sheet picker (UI) ----
st.sidebar.markdown("### 🧾 Step 2 — Symptoms Source")
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
    index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0),
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
picked_del_col: Optional[str] = None
picked_det_col: Optional[str] = None

if symp_cols_preview:
    st.sidebar.caption("Detected columns:")
    st.sidebar.write(", ".join(map(str, symp_cols_preview)))
    manual_cols = st.sidebar.checkbox("Manually choose Delighters/Detractors columns", value=False)
    if manual_cols:
        picked_del_col = st.sidebar.selectbox("Delighters column", options=["(none)"] + symp_cols_preview, index=0)
        picked_det_col = st.sidebar.selectbox("Detractors column", options=["(none)"] + symp_cols_preview, index=0)
        if picked_del_col == "(none)":
            picked_del_col = None
        if picked_det_col == "(none)":
            picked_det_col = None

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes,
    user_sheet=chosen_sheet if sheet_names else None,
    user_del_col=picked_del_col,
    user_det_col=picked_det_col,
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

# ---------------- Step 3 — Configure & Primer ----------------
st.markdown("### Step 3 — Configure & Primer")

left, mid, right = st.columns([2, 2, 3])
with left:
    batch_n = st.slider(
        "How many to process this run",
        1, 20,
        min(10, max(1, missing_count)) if missing_count else 10,
    )
with mid:
    model_choice = st.selectbox("Model", [APP["CHAT_FAST"], "gpt-4o", APP["CHAT_STRICT"], "gpt-5"], index=0)
with right:
    strictness = st.slider(
        "Strictness (higher = fewer, more precise)",
        0.55, 0.95, 0.80, 0.01,
        help="Confidence + evidence threshold; also reduces near-duplicates.",
    )

acc1, acc2, acc3 = st.columns([2, 2, 3])
with acc1:
    require_evidence = st.checkbox(
        "Require textual evidence",
        value=True,
        help="Rejects a pick unless evidence tokens appear in the text (or semantic override).",
    )
with acc2:
    evidence_hits_required = st.selectbox(
        "Min evidence tokens", options=[1, 2], index=1 if strictness >= 0.8 else 0
    )
with acc3:
    order = st.selectbox("Processing order", ["Original", "Shortest first", "Longest first"], index=2)

# Semantic recall boost UI
sem_col1, sem_col2 = st.columns([2, 2])
with sem_col1:
    use_semantic = st.checkbox(
        "Semantic recall boost (embeddings)",
        value=True,
        help="Use embeddings to propose likely labels even if exact words aren't present.",
    )
with sem_col2:
    semantic_threshold = st.slider("Min semantic similarity", 0.50, 0.90, 0.70, 0.01)

# Global Primer toggle
primer_row1 = st.container()
with primer_row1:
    primer_enabled = st.checkbox(
        "Step 0: Build a Global Primer first",
        value=True,
        help="Reads all reviews once to learn product, topics, and label synonyms/examples.",
    )

# Speed mode (reduce latency)
speed_col = st.container()
with speed_col:
    speed_mode = st.checkbox(
        "⚡ Speed mode (optimize for latency)",
        value=False,
        help="Uses a faster model and sorts by shorter reviews first. Accuracy settings still apply.",
    )
    if speed_mode and model_choice != APP["CHAT_FAST"]:
        st.info(f"Speed mode suggests '{APP['CHAT_FAST']}' for fastest responses.")
        model_choice = APP["CHAT_FAST"]
        if order == "Longest first":
            order = "Shortest first"

# Sort missing_idx for preview/ETA
if order != "Original":
    missing_idx = sorted(
        missing_idx,
        key=lambda i: (len(verb_series.iloc[i]) if i < len(verb_series) else 0),
        reverse=(order == "Longest first"),
    )

# ETA & cost estimates (heuristic, token-aware)
MODEL_TPS = {"gpt-4o-mini": 55, "gpt-4o": 25, "gpt-4.1": 16, "gpt-5": 12}
MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
rows = min(batch_n, missing_count)

sel_lengths = [len(verb_series.iloc[i]) for i in (missing_idx[:rows] if rows else [])]
chars_est = int(pd.Series(sel_lengths).median()) if sel_lengths else int(max(200, (q1 + q3) / 2))
tok_est = max(1, int(chars_est * APP["ETA_TOKS_PER_CHAR"]))
rt = rows * (MODEL_LAT.get(model_choice, 1.0) + tok_est / max(8, MODEL_TPS.get(model_choice, 12)))
rt *= (1.0 + 0.15 * (evidence_hits_required - 1))
eta_secs = int(round(rt))
rough_cost = rows * (tok_est/1000.0 * (APP["COST_PER_1K_INPUT"] + APP["COST_PER_1K_OUTPUT"]))

st.markdown(
    f"<div class='sticky-toolbar'>"
    f"<span class='badge'>Missing: {missing_count}</span>"
    f"<span class='badge'>Batch: {rows}</span>"
    f"<span class='badge'>Model: {model_choice}</span>"
    f"<span class='badge'>Strictness: {strictness:.2f}</span>"
    f"<span class='badge'>ETA ~{eta_secs}s</span>"
    f"<span class='badge'>Cost est ~${rough_cost:.3f}</span>"
    f"</div>",
    unsafe_allow_html=True
)

# Optional: histogram of review lengths with IQR shading
if _HAS_PX and len(lengths):
    fig = px.histogram(lengths, nbins=30, title="Review length distribution (characters)")
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    try:
        fig.add_vrect(x0=q1, x1=q3, fillcolor="#dbeafe", opacity=0.4, line_width=0)
        fig.add_vline(x=q1, line_dash="dot")
        fig.add_vline(x=q3, line_dash="dot")
    except Exception:
        pass
    st.plotly_chart(fig, use_container_width=True)

# ---------------- Additional accuracy knobs ----------------
ver1, ver2, ver3 = st.columns([2,2,3])
with ver1:
    verify_pass = st.checkbox(
        "Enable Verify Pass (2-stage)",
        value=False,
        help="Second LLM call to verify top candidates with quoted evidence; slower but more precise.",
    )
with ver2:
    verify_topk = st.slider("Verify up to K candidates per polarity", 2, 12, 8, 1)
with ver3:
    st.caption("Tip: Enable verify when you care about precision over speed; keep K modest.")

# ---------------- Browse missing rows ----------------
with st.expander("🔎 Browse missing rows", expanded=False):
    q = st.text_input("Search in review text", "")
    star_filter = st.multiselect(
        "Filter by star rating",
        options=sorted(pd.Series(df.get("Star Rating", pd.Series(dtype=float))).dropna().unique().tolist())
    )
    preview_rows = []
    for i in missing_idx[:600]:  # show first 600 for performance
        t = verb_series.iloc[i]
        star = df.get("Star Rating", pd.Series(dtype=float)).iloc[i] if "Star Rating" in df.columns else None
        if q and q.lower() not in str(t).lower():
            continue
        if star_filter and star not in set(star_filter):
            continue
        preview_rows.append({"row_index": int(i), "stars": star, "length": len(str(t)), "snippet": str(t)[:160]})
    if preview_rows:
        st.dataframe(pd.DataFrame(preview_rows))
    else:
        st.caption("No rows match the current filters.")

# ---------------- Allowed lists viewer ----------------
with st.expander("📚 View allowed symptom palettes (from 'Symptoms' sheet)", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Allowed Detractors** ({len(ALLOWED_DETRACTORS)}):")
        if ALLOWED_DETRACTORS:
            st.markdown(
                "<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in ALLOWED_DETRACTORS]) + "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("None detected")
    with c2:
        st.markdown(f"**Allowed Delighters** ({len(ALLOWED_DELIGHTERS)}):")
        if ALLOWED_DELIGHTERS:
            st.markdown(
                "<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in ALLOWED_DELIGHTERS]) + "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("None detected")

# ---------------- API key handling ----------------
api_key = (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
_has_key = bool(api_key)
if missing_count and not _HAS_OPENAI:
    st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
if missing_count and _HAS_OPENAI and not _has_key:
    st.warning("Set a non-empty OPENAI_API_KEY (env or secrets) to enable AI labeling.")

# ---------------- Session State ----------------
st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("approved_new_delighters", set())
st.session_state.setdefault("approved_new_detractors", set())
st.session_state.setdefault("PRIMER", None)
st.session_state.setdefault("history", [])          # change history (batches)
st.session_state.setdefault("pending_changes", [])  # current batch changes

# ---------------- Helpers ----------------
def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

# Canonical aliases to avoid near-duplicate picks (extendable)
ALIAS_CANON = {
    "initial difficulty": "Learning curve",
    "hard to learn": "Learning curve",
    "setup difficulty": "Learning curve",
    "noisy startup": "Startup noise",
    "too loud": "Loud",
}
# Some light anti-token defaults for common oppositions (extend as needed)
ANTI_TOKENS = {
    "Loud": ["quiet", "silent", "low noise", "not loud"],
    "Quiet": ["loud", "noisy", "buzz", "whine", "rattle"],
    "Heavy": ["lightweight", "light weight", "not heavy"],
    "Weak suction": ["strong suction", "powerful suction"],
    "Strong suction": ["weak suction", "poor suction"],
}

def canonicalize(name: str) -> str:
    nn = (name or "").strip()
    base = _normalize_name(nn)
    for k, v in ALIAS_CANON.items():
        if _normalize_name(k) == base:
            return v
    return nn

# True word-boundary highlighter
def _highlight_terms(text: str, allowed: List[str]) -> str:
    out = text
    for t in sorted(set(allowed), key=len, reverse=True):
        if not t.strip():
            continue
        try:
            out = re.sub(rf"(\b{re.escape(t)}\b)", r"<mark>\1</mark>", out, flags=re.IGNORECASE)
        except re.error:
            pass
    return out

# --------- OpenAI helpers (Chat & Responses support) + Safe Wrapper ---------
def _openai_json_label(model: str, sys_prompt: str, user_obj: dict, api_key: str) -> dict:
    """
    Returns parsed JSON dict or raises. Supports both Chat Completions and Responses API.
    """
    client = OpenAI(api_key=api_key)
    use_responses = bool(re.match(r"^(gpt-4\.1|gpt-5)", model))

    if use_responses:
        out = client.responses.create(
            model=model,
            response_format={"type": "json_object"},
            input=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(user_obj)},
            ],
        )
        content = out.output_text or "{}"
    else:
        req: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(user_obj)},
            ],
            "response_format": {"type": "json_object"},
        }
        if not str(model).startswith("gpt-5"):
            req["temperature"] = 0.2
        out = client.chat.completions.create(**req)
        content = out.choices[0].message.content or "{}"
    return json.loads(content)

def _openai_json_label_safe(model: str, sys_prompt: str, user_obj: dict, api_key: str, retries: int = 3) -> dict:
    last_err = None
    for _ in range(retries):
        try:
            data = _openai_json_label(model, sys_prompt, user_obj, api_key)
            if not isinstance(data, dict):
                raise ValueError("Non-dict JSON returned")
            data.setdefault("delighters", [])
            data.setdefault("detractors", [])
            # normalize shapes
            for k in ("delighters", "detractors"):
                if isinstance(data[k], list):
                    for i,x in enumerate(data[k]):
                        if isinstance(x, str):
                            data[k][i] = {"name": x, "confidence": 0.6}
                else:
                    data[k] = []
            return data
        except Exception as e:
            last_err = e
    raise RuntimeError(f"LLM JSON labeling failed after {retries} attempts: {last_err}")

# --------- Embedding helpers (semantic recall + primer) ---------
EMB_MODEL = APP["EMB_MODEL"]

@st.cache_resource(show_spinner=False)
def _build_label_index(labels: List[str], _api_key: str):
    if not (_HAS_OPENAI and _api_key and labels):
        return None
    texts = list(dict.fromkeys([canonicalize(x) for x in labels if x]))
    if not texts:
        return None
    client = OpenAI(api_key=_api_key)
    vecs = client.embeddings.create(model=EMB_MODEL, input=texts).data
    M = np.array([v.embedding for v in vecs], dtype="float32")
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
    return (texts, M)

def _ngram_candidates(text: str, max_ngrams: int = 256) -> List[str]:
    ws = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    ngrams: List[str] = []
    add = ngrams.append
    seen = set()
    for n in (1, 2, 3, 4, 5):
        for i in range(len(ws) - n + 1):
            s = " ".join(ws[i : i + n])
            if len(s) >= 4 and s not in seen:
                add(s)
                seen.add(s)
                if len(ngrams) >= max_ngrams:
                    break
        if len(ngrams) >= max_ngrams:
            break
    return ngrams

def _semantic_support(review: str, label_index, _api_key: str, topk: int = 20, min_sim: float = 0.68) -> Dict[str, float]:
    """Return {label: best_cosine_sim} for labels semantically supported by review text."""
    if (not label_index) or (not review):
        return {}
    labels, L = label_index
    cands = _ngram_candidates(review)
    if not cands:
        return {}
    client = OpenAI(api_key=_api_key)
    data = client.embeddings.create(model=EMB_MODEL, input=cands).data
    X = np.array([d.embedding for d in data], dtype="float32")
    X /= (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
    S = X @ L.T  # cosine similarity
    best_idx = S.argmax(axis=1)
    best_sim = S[np.arange(len(cands)), best_idx]
    buckets: Dict[str, float] = {}
    for j, sim in zip(best_idx, best_sim):
        if sim >= min_sim:
            lab = labels[int(j)]
            if sim > buckets.get(lab, 0.0):
                buckets[lab] = float(sim)
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1])[:topk])

# ---- Primer embedding utilities ----
def _embed_texts(texts: List[str], api_key: str) -> np.ndarray:
    """Batched embeddings -> (N, D) L2-normalized."""
    if not texts:
        return np.zeros((0, 1536), dtype="float32")
    client = OpenAI(api_key=api_key)
    B, vecs = 128, []
    for i in range(0, len(texts), B):
        chunk = texts[i : i + B]
        out = client.embeddings.create(model=EMB_MODEL, input=chunk).data
        vecs.extend([d.embedding for d in out])
    M = np.array(vecs, dtype="float32")
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
    return M

def _topk_idxs(sim_row: np.ndarray, k: int) -> np.ndarray:
    k = int(min(k, sim_row.shape[-1]))
    return np.argpartition(-sim_row, k - 1)[:k]

@st.cache_resource(show_spinner=True)
def build_global_primer(
    all_reviews: List[str],
    stars: List[Optional[float]],
    allowed_del: List[str],
    allowed_det: List[str],
    api_key: str,
):
    """
    Returns:
      brief (str),
      clusters: [{id, top_terms, mean_stars, size}],
      lexicon: {label: {"synonyms":[...], "evidence_tokens":[...]}},
      label_guides: {label: {"definition": str, "examples": [...]}},
      cluster_priors: {cluster_id: [top_label,...]},
      review_cluster: list[int],
      allowed_order: list[str]
    """
    texts = [str(t or "").strip() for t in all_reviews]

    # --- Embeddings for corpus ---
    X = _embed_texts(texts, api_key)  # (N, D)

    # --- Clustering (semantic topics) ---
    n = len(texts)
    n_clusters = max(5, min(25, n // 300 or 8))
    if _HAS_SK and n >= max(20, n_clusters * 2):
        km = KMeans(n_clusters=n_clusters, n_init="auto", random_state=42)
        labels = km.fit_predict(X)
    else:
        labels = np.zeros(n, dtype=int)
        n_clusters = 1

    # --- Top terms per cluster (TF-IDF) ---
    clusters = []
    if _HAS_SK:
        tf = TfidfVectorizer(min_df=3, max_features=8000, ngram_range=(1, 2), token_pattern=r"[a-zA-Z0-9]{3,}")
        TF = tf.fit_transform(texts)  # (N, V)
        vocab = np.array(tf.get_feature_names_out())
        for c in range(n_clusters):
            idxs = np.where(labels == c)[0]
            if not len(idxs):
                clusters.append({"id": c, "top_terms": [], "mean_stars": None, "size": 0})
                continue
            v = np.asarray(TF[idxs].mean(axis=0)).ravel()
            top = v.argsort()[-12:][::-1]
            mean_star = float(np.nanmean([stars[i] for i in idxs if stars[i] is not None])) if idxs.size else None
            clusters.append({"id": int(c), "top_terms": vocab[top].tolist(), "mean_stars": mean_star, "size": int(idxs.size)})
    else:
        clusters = [{"id": 0, "top_terms": [], "mean_stars": float(np.nanmean([s for s in stars if s is not None])) if stars else None, "size": n}]

    # --- Label guides (definitions, synonyms, examples) ---
    all_allowed = [canonicalize(x) for x in (allowed_del + allowed_det) if x]
    L = _embed_texts(all_allowed, api_key)  # (M, D)
    sim = X @ L.T  # (N, M)

    label_guides: Dict[str, Dict[str, Any]] = {}
    lexicon: Dict[str, Dict[str, List[str]]] = {}
    client = OpenAI(api_key=api_key)
    for j, label in enumerate(all_allowed):
        support_idx = _topk_idxs(sim[:, j], 20)  # top-20 nearest reviews
        quotes = [texts[i][:300] for i in support_idx if len(texts[i]) >= 20][:6]
        msg = [
            {"role": "system", "content": "You write compact taxonomy notes for product feedback."},
            {"role": "user", "content": json.dumps({"label": label, "examples": quotes})},
        ]
        try:
            out = client.chat.completions.create(
                model=APP["CHAT_FAST"],
                messages=msg,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            data = json.loads(out.choices[0].message.content or "{}")
        except Exception:
            data = {}
        definition = data.get("definition") or f"{label}: user-reported theme."
        syns = data.get("synonyms") or []
        toks = data.get("evidence_tokens") or []
        exs = data.get("examples") or quotes[:3]
        syns = [canonicalize(s) for s in syns if s and len(s) < 40][:8]
        toks = [t for t in toks if isinstance(t, str) and 3 <= len(t) <= 24][:10]
        label_guides[label] = {"definition": definition, "examples": exs}
        lexicon[label] = {"synonyms": syns, "evidence_tokens": toks}

    # --- Cluster -> label priors ---
    cluster_priors: Dict[int, List[str]] = {}
    for c in range(n_clusters):
        idxs = np.where(labels == c)[0]
        if not len(idxs):
            cluster_priors[c] = []
            continue
        s = sim[idxs].mean(axis=0)  # (M,)
        top = np.argsort(-s)[:15]
        cluster_priors[int(c)] = [all_allowed[int(j)] for j in top]

    # --- Product brief ---
    chunks, CH = [], 4000
    buf: List[str] = []
    for t in texts:
        if not t:
            continue
        buf.append(t[:600])
        if sum(len(x) for x in buf) >= CH:
            chunks.append("\n\n".join(buf))
            buf = []
    if buf:
        chunks.append("\n\n".join(buf))
    partials: List[str] = []
    for ch in chunks[:12]:
        try:
            out = client.chat.completions.create(
                model=APP["CHAT_FAST"],
                messages=[
                    {"role": "system", "content": "Summarize recurring product themes succinctly."},
                    {"role": "user", "content": ch},
                ],
                temperature=0.2,
            )
            partials.append(out.choices[0].message.content or "")
        except Exception:
            pass
    brief_text = "\n".join(partials[:8])[:4000]
    try:
        out = client.chat.completions.create(
            model=APP["CHAT_FAST"],
            messages=[
                {
                    "role": "system",
                    "content": "Write a 6–10 bullet product brief: key use cases, delights, pain points, vocabulary, and edge cases.",
                },
                {"role": "user", "content": brief_text},
            ],
            temperature=0.2,
        )
        brief = out.choices[0].message.content or ""
    except Exception:
        brief = "High-level product brief unavailable (summary step failed)."

    return {
        "brief": brief,
        "clusters": clusters,
        "lexicon": lexicon,
        "label_guides": label_guides,
        "cluster_priors": cluster_priors,
        "review_cluster": list(map(int, labels.tolist())),
        "allowed_order": all_allowed,
    }

# -------------- Build indices (semantic + primer) --------------
LABEL_INDEX = None
if _HAS_OPENAI and _has_key:
    LABEL_INDEX = _build_label_index(ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS, api_key)

PRIMER = st.session_state.get("PRIMER")
if primer_enabled and _HAS_OPENAI and _has_key:
    with st.status("Building Global Primer…", expanded=False) as s:
        PRIMER = build_global_primer(
            verb_series.tolist(),
            df.get("Star Rating", pd.Series(dtype=float)).tolist(),
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            api_key,
        )
        st.session_state["PRIMER"] = PRIMER
        s.update(label="Global Primer ready ✔")

# ---------------- Evidence & Confidence (v2) ----------------
_NEGATORS = re.compile(r"\b(no|not|never|hardly|barely|scarcely|doesn['’]t|don['’]t|isn['’]t|wasn['’]t|cannot|can['’]t|won['’]t|without)\b", re.I)

def _find_spans(pattern: str, text: str) -> list[tuple[int, int]]:
    spans = []
    for m in re.finditer(pattern, text, flags=re.I):
        spans.append((m.start(), m.end()))
    return spans

def _span_has_negation(text: str, span: tuple[int,int], window: int = 24) -> bool:
    a, b = span
    left = max(0, a - window)
    right = min(len(text), b + window)
    return bool(_NEGATORS.search(text[left:right]))

def _evidence_report(symptom: str, text: str, primer=None) -> dict:
    """
    Returns {
      'hits': [{'token':..., 'span':(a,b), 'negated':bool, 'type':'phrase'|'token'}],
      'score_tokens': int, 'score_phrases': int, 'total_hits': int, 'quote': str
    }
    """
    if not symptom or not text:
        return {"hits": [], "score_tokens": 0, "score_phrases": 0, "total_hits": 0, "quote": ""}

    text_lc = text
    toks = [t for t in _normalize_name(symptom).split() if len(t) >= 3]
    phrases = set()
    if len(toks) >= 2:
        for n in range(min(5, len(toks)), 1, -1):
            for i in range(len(toks)-n+1):
                phrases.add(" ".join(toks[i:i+n]))
    # primer synonyms
    if primer:
        meta = primer.get("lexicon", {}).get(canonicalize(symptom), {})
        for s in (meta.get("synonyms") or []):
            s = _normalize_name(s)
            if " " in s and len(s) >= 4:
                phrases.add(s)

    hits = []
    score_tokens = 0
    score_phrases = 0
    best_span = None

    # Phrase hits
    for p in sorted(phrases, key=len, reverse=True):
        patt = rf"\b{re.escape(p)}\b"
        for span in _find_spans(patt, text_lc):
            neg = _span_has_negation(text_lc, span)
            hits.append({"token": p, "span": span, "negated": neg, "type": "phrase"})
            if not neg:
                score_phrases += 2
                best_span = best_span or span

    # Token hits
    for t in toks:
        patt = rf"\b{re.escape(t)}\b"
        for span in _find_spans(patt, text_lc):
            neg = _span_has_negation(text_lc, span)
            hits.append({"token": t, "span": span, "negated": neg, "type": "token"})
            if not neg:
                score_tokens += 1
                best_span = best_span or span

    # Quote snippet around best span
    quote = ""
    if best_span:
        a, b = best_span
        left = max(0, a-60)
        right = min(len(text_lc), b+60)
        quote = text_lc[left:right].strip()

    return {
        "hits": hits,
        "score_tokens": score_tokens,
        "score_phrases": score_phrases,
        "total_hits": sum(1 for h in hits if not h["negated"]),
        "quote": quote[:160],
    }

def fuse_confidence(llm_conf: float, sem_sim: float, ev_report: dict, stars: Optional[float], polarity: str) -> float:
    """Blend multiple signals into stable [0,1] confidence."""
    llm = max(0.0, min(1.0, float(llm_conf or 0)))
    sem = max(0.0, min(1.0, float(sem_sim or 0)))
    evp = 0.0
    if ev_report:
        evp = min(1.0, 0.2 * ev_report.get("score_tokens", 0) + 0.35 * ev_report.get("score_phrases", 0))
    prior = 0.0
    if stars is not None and not pd.isna(stars):
        if polarity == "delighter":
            prior = 0.07 if stars >= 4.0 else (-0.07 if stars <= 2.0 else 0.0)
        else:
            prior = 0.07 if stars <= 2.0 else (-0.07 if stars >= 4.0 else 0.0)
    base = 0.45*llm + 0.30*sem + 0.25*evp
    return max(0.0, min(1.0, base + prior))

# ---------------- Verify Pass (optional) ----------------
def _verify_label_safe(review: str, label: str, definition: str, synonyms: List[str], api_key: str, model: str) -> dict:
    """
    Ask the model to verify presence with quoted evidence.
    Returns: {"present": bool, "confidence": float, "evidence": ["tok",...], "quote": str}
    """
    if not (_HAS_OPENAI and api_key):
        return {"present": False, "confidence": 0.0, "evidence": [], "quote": ""}
    sys = "Decide if the label applies to the review ONLY if you can quote concrete evidence. Return JSON."
    user = {
        "review": str(review or "")[:4000],
        "label": label,
        "definition": definition,
        "synonyms": synonyms[:12],
        "format": {"present":"bool","confidence":"0..1","evidence":"list[str]","quote":"<=120 chars"}
    }
    try:
        data = _openai_json_label_safe(model, sys, user, api_key)
        return {
            "present": bool(data.get("present", False)),
            "confidence": float(data.get("confidence", 0.0)),
            "evidence": [str(x) for x in data.get("evidence",[]) if isinstance(x,str)][:6],
            "quote": str(data.get("quote",""))[:160]
        }
    except Exception:
        return {"present": False, "confidence": 0.0, "evidence": [], "quote": ""}

# --------- LRU for suggestions to avoid session bloat ---------
MAX_SUG_CACHE = 2000
def _append_suggestion(sug: dict):
    sugs = st.session_state["symptom_suggestions"]
    sugs.append(sug)
    if len(sugs) > MAX_SUG_CACHE:
        del sugs[: len(sugs) - MAX_SUG_CACHE]

# --------- Model call (JSON-only) with semantic + primer guardrails ---------
def _llm_pick(
    review: str,
    stars,
    allowed_del: List[str],
    allowed_det: List[str],
    min_conf: float,
    evidence_hits_required: int = 1,
    row_index: Optional[int] = None,
    verify_pass_enabled: bool = False,
    verify_topk: int = 8,
):
    """Return (allowed_delighters, allowed_detractors, novel_delighters, novel_detractors, extras)."""
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], [], {}

    # Semantic support (nearest labels by embeddings)
    sem_supp: Dict[str, float] = {}
    if _HAS_OPENAI and _has_key and LABEL_INDEX and use_semantic:
        try:
            sem_supp = _semantic_support(review, LABEL_INDEX, api_key, topk=20, min_sim=semantic_threshold)
        except Exception:
            sem_supp = {}

    # Primer payload (brief + label guides subset)
    primer_payload = None
    if PRIMER:
        cluster_id = None
        try:
            if row_index is not None:
                cluster_id = int(PRIMER["review_cluster"][row_index])
        except Exception:
            cluster_id = None
        if cluster_id is not None:
            candidate_labels = PRIMER["cluster_priors"].get(int(cluster_id), [])[:20]
        else:
            candidate_labels = (PRIMER.get("allowed_order") or [])[:20]
        guides = {lab: PRIMER["label_guides"].get(lab, {}) for lab in candidate_labels}
        primer_payload = {"product_brief": (PRIMER.get("brief") or "")[:1200], "label_guides": guides}

    sys_prompt = (
        """
You are labeling a single user review for symptoms, with access to a global product primer.
Choose up to 10 delighters and up to 10 detractors ONLY from the provided lists.
Use semantic_candidates and label_guides to recognize paraphrases and context (negation, sarcasm, setup/usage specifics).
Return JSON exactly like:
{"delighters":[{"name":"...","confidence":0.0}], "detractors":[{"name":"...","confidence":0.0}]}

Rules:
1) If not clearly present, OMIT it.
2) Prefer precision over recall; avoid stretch matches.
3) Avoid near-duplicates (canonical terms).
4) If stars are 1–2, bias to detractors; if 4–5, bias to delighters; otherwise neutral.
        """
    )

    user = {
        "review": review[:4000],
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del[:120],
        "allowed_detractors": allowed_det[:120],
        "semantic_candidates": sorted(list(sem_supp.keys())),
        "primer": primer_payload,
    }

    # ---------------- Propose (LLM or conservative fallback) ----------------
    dels_raw, dets_raw = [], []
    if _HAS_OPENAI and _has_key:
        try:
            data = _openai_json_label_safe(model_choice, sys_prompt, user, api_key)
            dels_raw = data.get("delighters", []) or []
            dets_raw = data.get("detractors", []) or []
        except Exception:
            dels_raw, dets_raw = [], []
    # fallback conservative if blank
    if not (dels_raw or dets_raw):
        STOP_TOKS = {"app", "out", "bad", "hot", "cold", "set"}
        def _fallback_pick(allowed: List[str]) -> List[dict]:
            scored: List[dict] = []
            text = " " + review.lower() + " "
            for a in allowed:
                a_can = canonicalize(a)
                toks = [t for t in _normalize_name(a_can).split() if len(t) >= 4 and t not in STOP_TOKS]
                if PRIMER:
                    meta = PRIMER.get("lexicon", {}).get(a_can, {})
                    toks += [t for t in (meta.get("evidence_tokens") or []) if len(t) >= 4]
                    toks += [t for t in (meta.get("synonyms") or []) if len(t) >= 4]
                if not toks:
                    continue
                hits = [t for t in toks if re.search(rf"\b{re.escape(t)}\b", text)]
                score = len(hits) / max(1, len(toks))
                if (not require_evidence) or (len(hits) >= evidence_hits_required):
                    if score >= min_conf - 0.05:
                        scored.append({"name": a_can, "confidence": 0.60 + 0.4 * score})
            return scored
        dels_raw = _fallback_pick(allowed_del)
        dets_raw = _fallback_pick(allowed_det)

    # ---------------- Score & Filter (evidence + semantic + stars) ----------------
    text = review or ""
    stars_f = float(stars) if (stars is not None and (not pd.isna(stars))) else None

    def _score_candidates(items, allowed_set, semantic_dict, polarity: str):
        scored = []
        for it in items:
            nm = canonicalize(it.get("name","").strip())
            if not nm:
                continue
            llm_c = float(it.get("confidence", 0) or 0)
            sem_c = float(semantic_dict.get(nm, 0.0))
            # evidence
            evr = _evidence_report(nm, text, PRIMER if require_evidence else None)

            # anti-token penalty
            anti_list = ANTI_TOKENS.get(nm, [])
            anti_hit = any(re.search(rf"\b{re.escape(a)}\b", text, flags=re.IGNORECASE) for a in anti_list) if anti_list else False

            fused = fuse_confidence(llm_c, sem_c, evr, stars_f, polarity)
            if anti_hit:
                fused -= 0.08

            # evidence gate & semantic override
            passes_evidence = True
            if require_evidence:
                passes_evidence = evr.get("total_hits", 0) >= int(evidence_hits_required)
            sem_override = sem_c >= max(semantic_threshold, 0.72)

            if (passes_evidence or sem_override):
                scored.append({
                    "name": nm,
                    "fused_conf": max(0.0, min(1.0, fused)),
                    "llm_conf": llm_c,
                    "sem_sim": sem_c,
                    "evidence": evr,
                    "quote": evr.get("quote",""),
                    "novel": nm not in allowed_set,
                    "polarity": polarity,
                })
        # sort by fused confidence descending
        return sorted(scored, key=lambda x: -x["fused_conf"])

    dels_scored = _score_candidates(dels_raw, ALLOWED_DELIGHTERS_SET, sem_supp, "delighter")
    dets_scored = _score_candidates(dets_raw, ALLOWED_DETRACTORS_SET, sem_supp, "detractor")

    # ---------------- Optional Verify Pass ----------------
    if verify_pass_enabled and _HAS_OPENAI and _has_key and PRIMER:
        client_model = APP["CHAT_FAST"] if model_choice == APP["CHAT_FAST"] else model_choice
        def _verify_block(scored_list):
            top = scored_list[:verify_topk]
            verified = []
            for item in top:
                guide = PRIMER["label_guides"].get(item["name"], {})
                res = _verify_label_safe(
                    review,
                    item["name"],
                    guide.get("definition",""),
                    (PRIMER["lexicon"].get(item["name"], {}) or {}).get("synonyms", []),
                    api_key,
                    client_model,
                )
                # If present False, dramatically downweight
                adj_llm = item["llm_conf"]
                if not res["present"]:
                    adj_llm = min(adj_llm, 0.20)
                else:
                    adj_llm = max(adj_llm, res.get("confidence", adj_llm))
                    if res.get("quote"):
                        item["quote"] = res["quote"]
                # recompute fused
                fused_new = fuse_confidence(adj_llm, item["sem_sim"], item["evidence"], stars_f, item["polarity"])
                item["llm_conf"] = adj_llm
                item["fused_conf"] = max(0.0, min(1.0, fused_new))
                verified.append(item)
            # keep verified for top-k; keep unverified remainder as-is
            remain = scored_list[verify_topk:]
            return sorted(verified + remain, key=lambda x: -x["fused_conf"])
        dels_scored = _verify_block(dels_scored)
        dets_scored = _verify_block(dets_scored)

    # ---------------- Resolve near-duplicates & exclusives ----------------
    def _finalize(scored_list, top_n=10, min_conf=0.60):
        out = []
        for s in scored_list:
            if s["fused_conf"] < min_conf:
                continue
            n_norm = _normalize_name(s["name"])
            if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(t["name"])).ratio() > 0.88 for t in out):
                out.append(s)
            if len(out) >= top_n:
                break
        return out

    dels_kept = _finalize(dels_scored, 10, min_conf)
    dets_kept = _finalize(dets_scored, 10, min_conf)

    # split into allowed vs novel
    allowed_dels = [k["name"] for k in dels_kept if not k["novel"]]
    allowed_dets = [k["name"] for k in dets_kept if not k["novel"]]
    novel_dels   = [k["name"] for k in dels_kept if k["novel"] and (k["sem_sim"] >= 0.75 or k["evidence"].get("total_hits",0) >= 2)]
    novel_dets   = [k["name"] for k in dets_kept if k["novel"] and (k["sem_sim"] >= 0.75 or k["evidence"].get("total_hits",0) >= 2)]

    extras = {"delighters_scored": dels_kept, "detractors_scored": dets_kept}
    return allowed_dels, allowed_dets, novel_dels[:5], novel_dets[:5], extras

# ---------------- Run Symptomize ----------------
can_run = missing_count > 0 and ((not _HAS_OPENAI) or (_has_key))

# Top action bar
col_runA, col_runB, col_runC = st.columns([2, 2, 3])
with col_runA:
    run = st.button(
        f"✨ Symptomize next {min(batch_n, missing_count)} review(s)",
        disabled=not can_run,
        help="Runs on the next batch of reviews missing symptoms.",
    )
with col_runB:
    enable_all = st.checkbox("Enable ALL (bulk)")
    run_all = st.button(
        f"⚡ Symptomize ALL {missing_count} missing",
        disabled=(not can_run) or missing_count == 0 or (not enable_all),
        help="Processes every review that has empty Symptom 1–20. Uses many API calls.",
    )
with col_runC:
    st.caption("Tip: Use batch mode first to review accuracy, then run ALL.")

# Process
if (run or run_all) and missing_idx:
    todo = missing_idx if run_all else missing_idx[:batch_n]
    progress = st.progress(0)
    status = st.empty()
    for i, idx in enumerate(todo, start=1):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        dels, dets, novel_dels, novel_dets, extras = _llm_pick(
            review_txt,
            stars,
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            strictness,
            evidence_hits_required=evidence_hits_required,
            row_index=int(idx),
            verify_pass_enabled=verify_pass,
            verify_topk=verify_topk,
        )
        _append_suggestion(
            {
                "row_index": int(idx),
                "stars": float(stars) if pd.notna(stars) else None,
                "review": review_txt,
                "delighters": dels,
                "detractors": dets,
                "novel_delighters": novel_dels,
                "novel_detractors": novel_dets,
                "approve_novel_del": [],
                "approve_novel_det": [],
                "explain": extras,
            }
        )
        progress.progress(i / len(todo))
        status.info(f"Processed {i}/{len(todo)}")
    status.success("Finished generating suggestions! Review below, then Apply to write into the sheet.")
    st.rerun()

# ---------------- Review & Approve ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## Step 4 — 🔍 Review & Approve Suggestions")

    # Auto-approval rules
    with st.expander("⚙️ Auto-approval rules", expanded=False):
        auto_enable = st.checkbox("Enable auto-approval for high-confidence rows", value=False)
        auto_min_conf = st.slider("Min fused confidence", 0.60, 0.95, 0.82, 0.01)
        auto_min_hits = st.slider("Min total evidence hits", 0, 5, 1, 1)

    # Bulk actions using direct session updates
    with st.expander("Bulk actions", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1, 2, 3, 2])
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
                    cur = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
                    cur = not cur
                    st.session_state[f"sel_{i}"] = cur
                    if cur: newset.add(i)
                st.session_state["sug_selected"] = newset
        with c4:
            if st.button("Only with suggestions"):
                keep = {i for i, s in enumerate(sugs) if s["delighters"] or s["detractors"]}
                st.session_state["sug_selected"] = keep
                for i in range(total):
                    st.session_state[f"sel_{i}"] = i in keep
        with c5:
            max_apply = st.slider("Max rows to apply now", 1, total, min(20, total))
        with c6:
            if st.button("Re-run selected stricter"):
                new_set = set(st.session_state.get("sug_selected", set()))
                for i in sorted(list(new_set)):
                    s = sugs[i]
                    review_txt = s.get("review", "")
                    stars = s.get("stars")
                    dels, dets, novel_dels, novel_dets, extras = _llm_pick(
                        review_txt,
                        stars,
                        ALLOWED_DELIGHTERS,
                        ALLOWED_DETRACTORS,
                        min(0.95, strictness + 0.1),
                        evidence_hits_required=max(2, evidence_hits_required),
                        row_index=int(s.get("row_index")),
                        verify_pass_enabled=True,
                        verify_topk=max(6, verify_topk),
                    )
                    s["delighters"] = dels
                    s["detractors"] = dets
                    s["novel_delighters"] = novel_dels
                    s["novel_detractors"] = novel_dets
                    s["explain"] = extras
                st.success("Re-ran selected with higher strictness.")

    # Render each suggestion
    for i, s in enumerate(sugs):
        label = f"Row {s.get('row_index','?')} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i == 0)):
            # Selection checkbox bound to session key
            default_checked = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
            checked = st.checkbox("Select for apply", value=default_checked, key=f"sel_{i}")
            if checked:
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            # Auto-approve if rules satisfied
            if st.session_state.get(f"auto_checked_{i}") is None and auto_enable and s.get("explain"):
                def _has_enough(scored):
                    return any((it["fused_conf"] >= auto_min_conf and it["evidence"].get("total_hits",0) >= auto_min_hits) for it in scored)
                if _has_enough(s["explain"].get("detractors_scored", [])) or _has_enough(s["explain"].get("delighters_scored", [])):
                    st.session_state["sug_selected"].add(i)
                    st.session_state[f"sel_{i}"] = True
                st.session_state[f"auto_checked_{i}"] = True

            # Review text with highlights (highlight suggested terms for clarity)
            if s["review"]:
                terms_to_highlight = s["delighters"] + s["detractors"]
                highlighted = _highlight_terms(s["review"], terms_to_highlight)
                st.markdown("**Full review:**")
                st.markdown(f"<div class='review-quote'>{highlighted}</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Full review:** (empty)")

            # Suggestions chips
            c1, c2 = st.columns(2)
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

            # Explainability table (why picked)
            if s.get("explain"):
                st.write("**Why these were picked**")
                def _render_table(scored, positive=True):
                    if not scored:
                        st.caption("—")
                        return
                    for item in scored:
                        cols = st.columns([0.4, 0.15, 0.15, 0.15, 0.15])
                        with cols[0]:
                            st.markdown(f"{'🟢' if positive else '🔴'} **{item['name']}**")
                            if item.get("quote"):
                                st.caption(f"“{item['quote']}”")
                        with cols[1]:
                            st.caption(f"Final: **{item['fused_conf']:.2f}**")
                        with cols[2]:
                            st.caption(f"LLM: {item['llm_conf']:.2f}")
                        with cols[3]:
                            st.caption(f"Sem: {item['sem_sim']:.2f}")
                        ev = item["evidence"]
                        with cols[4]:
                            st.caption(f"Hits: {ev.get('total_hits',0)}; Neg: {sum(1 for h in ev.get('hits',[]) if h.get('negated'))}")
                c3, c4 = st.columns(2)
                with c3:
                    st.write("**Detractors – details**")
                    _render_table(s["explain"].get("detractors_scored", []), positive=False)
                with c4:
                    st.write("**Delighters – details**")
                    _render_table(s["explain"].get("delighters_scored", []), positive=True)

            # Novel candidates with approval toggles
            if s["novel_detractors"] or s["novel_delighters"]:
                st.info("Potential NEW symptoms (not in your list). Approve to add & allow.")
                c5, c6 = st.columns(2)
                with c5:
                    if s["novel_detractors"]:
                        st.write("**Novel Detractors (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_detractors"]):
                            if st.checkbox(name, key=f"novdet_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_det"] = picks
                with c6:
                    if s["novel_delighters"]:
                        st.write("**Novel Delighters (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_delighters"]):
                            if st.checkbox(name, key=f"novdel_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_del"] = picks

            # Per-row stricter re-run
            if st.button("Re-run this row stricter", key=f"rerow_{i}"):
                review_txt = s.get("review", "")
                stars = s.get("stars")
                dels, dets, novel_dels, novel_dets, extras = _llm_pick(
                    review_txt,
                    stars,
                    ALLOWED_DELIGHTERS,
                    ALLOWED_DETRACTORS,
                    min(0.95, strictness + 0.1),
                    evidence_hits_required=max(2, evidence_hits_required),
                    row_index=int(s.get("row_index")),
                    verify_pass_enabled=True,
                    verify_topk=max(6, verify_topk),
                )
                s["delighters"] = dels
                s["detractors"] = dets
                s["novel_delighters"] = novel_dels
                s["novel_detractors"] = novel_dets
                s["explain"] = extras
                st.rerun()

    # ---------------- Apply selected ----------------
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
                # write to df + record changes
                for j, name in enumerate(dets_final, start=1):
                    col = f"{APP['SYMPTOM_PREFIX']}{j}"
                    if col in df.columns:
                        old = df.at[ri, col] if col in df.columns else None
                        if str(old) != str(name):
                            st.session_state["pending_changes"].append({"row": int(ri), "column": col, "old": None if pd.isna(old) else str(old), "new": str(name)})
                        df.at[ri, col] = name
                for j, name in enumerate(dels_final, start=11):
                    col = f"{APP['SYMPTOM_PREFIX']}{j}"
                    if col in df.columns:
                        old = df.at[ri, col]
                        if str(old) != str(name):
                            st.session_state["pending_changes"].append({"row": int(ri), "column": col, "old": None if pd.isna(old) else str(old), "new": str(name)})
                        df.at[ri, col] = name
                # accumulate approved-new for workbook append later
                for n in s.get("approve_novel_del", []):
                    if n: st.session_state["approved_new_delighters"].add(n)
                for n in s.get("approve_novel_det", []):
                    if n: st.session_state["approved_new_detractors"].add(n)
            # commit batch changes
            if st.session_state["pending_changes"]:
                st.session_state["history"].append({"changes": st.session_state["pending_changes"]})
                st.session_state["pending_changes"] = []
            # prune applied suggestions
            st.session_state["symptom_suggestions"] = [s for k, s in enumerate(sugs) if k not in picked]
            st.success(f"Applied {len(picked)} row(s) to DataFrame.")

# ---------------- Novel Symptoms Review Center ----------------
pending_novel_del: Dict[str, int] = {}
pending_novel_det: Dict[str, int] = {}
for _s in st.session_state.get("symptom_suggestions", []):
    for name in _s.get("novel_delighters", []):
        if name: pending_novel_del[name] = pending_novel_del.get(name, 0) + 1
    for name in _s.get("novel_detractors", []):
        if name: pending_novel_det[name] = pending_novel_det.get(name, 0) + 1

_total_novel = len(pending_novel_del) + len(pending_novel_det)
if _total_novel:
    with st.expander(f"🧪 Review New Symptoms ({_total_novel} pending)", expanded=True):
        tabs = st.tabs(["Novel Detractors", "Novel Delighters"])

        def _review_table(pending_dict: Dict[str, int], kind: str):
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
                    if kind == "detractors":
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
                    ALLOWED_DETRACTORS.append(n)
                    ALLOWED_DETRACTORS_SET.add(n)
            for n in list(st.session_state["approved_new_delighters"]):
                if n and n not in ALLOWED_DELIGHTERS_SET:
                    ALLOWED_DELIGHTERS.append(n)
                    ALLOWED_DELIGHTERS_SET.add(n)
            st.success("Allowed lists updated for this session.")

# ---------------- Save & Export ----------------
st.markdown("## Step 5 — ⬇️ Save & Export")

with st.expander("Save options", expanded=False):
    dry_run = st.checkbox("Dry run (do not write Excel; preview changes only)", value=False)

# Undo & Change Log
cols_tools = st.columns([1,1,2,2])
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
        log = []
        for k, h in enumerate(st.session_state["history"], start=1):
            for ch in h["changes"]:
                log.append({"batch": k, **ch})
        if log:
            log_df = pd.DataFrame(log)
            csv = log_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download changes.csv", data=csv, file_name="changes.csv", mime="text/csv")
        else:
            st.info("No changes recorded yet.")

# ---------------- Download Updated Workbook ----------------
def offer_downloads(dry_run_flag: bool = False):
    st.markdown("### Export files")
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first.")
        return
    if dry_run_flag:
        st.info("Dry run enabled. Downloads disabled to prevent accidental writes.")
        return

    raw = st.session_state["uploaded_bytes"]
    formatted_ok = False
    formatted_bytes = None

    # Try formatting-preserving write and append approved novel symptoms + cache sheet
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = APP["DATA_SHEET_DEFAULT"]
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]

            # Map header names -> column indices
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
            def col_idx(name):
                return headers.get(name)

            # Write symptoms only (data begins row 2)
            df_reset = df.reset_index(drop=True)
            for df_row_idx, row in df_reset.iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci is None:
                        continue
                    val = row.get(c, None)
                    if pd.isna(val) or (str(val).strip() == ""):
                        ws.cell(row=excel_row, column=ci, value=None)
                    else:
                        ws.cell(row=excel_row, column=ci, value=str(val))

            # Hidden approvals sheet
            if HIDDEN_SHEET not in wb.sheetnames:
                wh = wb.create_sheet(HIDDEN_SHEET)
                wh.sheet_state = "hidden"
                wh.cell(row=1, column=1, value="Approved Delighters")
                wh.cell(row=1, column=2, value="Approved Detractors")
            else:
                wh = wb[HIDDEN_SHEET]
                if not wh.cell(row=1, column=1).value:
                    wh.cell(row=1, column=1, value="Approved Delighters")
                if not wh.cell(row=1, column=2).value:
                    wh.cell(row=1, column=2, value="Approved Detractors")

            # Read existing approved items
            exist_del, exist_det = set(), set()
            try:
                r = 2
                while True:
                    v = wh.cell(row=r, column=1).value
                    if v is None: break
                    v = str(v).strip()
                    if v: exist_del.add(v)
                    r += 1
            except Exception:
                pass
            try:
                r = 2
                while True:
                    v = wh.cell(row=r, column=2).value
                    if v is None: break
                    v = str(v).strip()
                    if v: exist_det.add(v)
                    r += 1
            except Exception:
                pass

            # Merge with newly approved this session
            new_del = set([n for n in st.session_state.get("approved_new_delighters", set()) if n])
            new_det = set([n for n in st.session_state.get("approved_new_detractors", set()) if n])
            final_del = sorted(exist_del.union(new_del))
            final_det = sorted(exist_det.union(new_det))

            # Clear current columns (below header) then write back
            max_len = max(len(final_del), len(final_det), 1)
            for r in range(2, 2 + max_len + 200):  # generous clear range
                wh.cell(row=r, column=1, value=None)
                wh.cell(row=r, column=2, value=None)
            for i, v in enumerate(final_del, start=2):
                wh.cell(row=i, column=1, value=v)
            for i, v in enumerate(final_det, start=2):
                wh.cell(row=i, column=2, value=v)

            # Save to bytes
            out_bio = io.BytesIO()
            wb.save(out_bio)
            formatted_bytes = out_bio.getvalue()
            formatted_ok = True
        except Exception as e:
            st.warning(f"Format-preserving save failed, falling back to basic writer. Reason: {e}")

    # Basic writer (no preserved formatting)
    basic_bytes = None
    try:
        out2 = io.BytesIO()
        with pd.ExcelWriter(out2, engine="xlsxwriter") as xlw:
            df.to_excel(xlw, sheet_name=APP["DATA_SHEET_DEFAULT"], index=False)
            # Also include allowed (session) for transparency
            allowed_df = pd.DataFrame({
                "Delighters": pd.Series(ALLOWED_DELIGHTERS),
                "Detractors": pd.Series(ALLOWED_DETRACTORS),
            })
            allowed_df.to_excel(xlw, sheet_name="Allowed Symptoms (session)", index=False)
            # Approved cache (session)
            appr_df = pd.DataFrame({
                "Approved Delighters": pd.Series(sorted(list(st.session_state.get("approved_new_delighters", set())))),
                "Approved Detractors": pd.Series(sorted(list(st.session_state.get("approved_new_detractors", set())))),
            })
            appr_df.to_excel(xlw, sheet_name=APP["HIDDEN_SHEET"], index=False)
        basic_bytes = out2.getvalue()
    except Exception as e:
        st.error(f"Basic writer failed: {e}")

    cols = st.columns([1, 1, 1])
    with cols[0]:
        if formatted_ok and formatted_bytes:
            st.download_button(
                "⬇️ Download updated (preserve formatting)",
                data=formatted_bytes,
                file_name="starwalk_symptomized_formatted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.caption("Format-preserving version unavailable on this run.")
    with cols[1]:
        if basic_bytes:
            st.download_button(
                "⬇️ Download updated (basic)",
                data=basic_bytes,
                file_name="starwalk_symptomized.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    with cols[2]:
        if st.button("🔄 Reset session state"):
            for k in ["symptom_suggestions","sug_selected","approved_new_delighters","approved_new_detractors","PRIMER","history","pending_changes"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.success("Session state cleared. You can re-run symptomize.")
            st.rerun()

# Call once UI is ready
offer_downloads(dry_run_flag=dry_run)
