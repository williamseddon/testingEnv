# =========================
# Star Walk Analysis Dashboard — Symptomization Beta
# Streamlit 1.38+
# =========================

# ---------- Imports ----------
import io
import os
import re
import math
import time
import json
import textwrap
from typing import List, Dict, Tuple

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from streamlit.components.v1 import html as st_html

# Optional: text fixer
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# Optional: OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Excel formatting-preserving writer
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# ---------- Page Config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Force Light Mode ----------
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

# ---------- Global CSS ----------
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
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.3vw,42px); font-weight:800; margin:0; font-family:inherit; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); font-family:inherit; }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:48px; width:auto; display:block; }
  .callout{border-left:4px solid;border-radius:10px;padding:10px 12px;margin:10px 0}
  .callout.warn{background:#FFF7ED;border-color:#F97316;color:#7C2D12}
  .review-card{
    background:var(--bg-card);
    border-radius:12px; padding:16px; margin:16px 0 24px;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
  }
  .badge{
    display:inline-flex; align-items:center; gap:.4ch;
    padding:6px 12px; border-radius:10px; font-weight:600; font-size:.94rem;
    border:1.6px solid var(--border); background:var(--bg-tile);
  }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------- Helpers ----------
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}

def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not str(model_id).startswith("gpt-5")

def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    if _HAS_FTFY:
        try: s = _ftfy_fix(s)
        except Exception: pass
    if any(ch in s for ch in ("Ã","Â","â","ï","€","™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip(): s = repaired
        except Exception: pass
    rep = {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}
    for bad, good in rep.items(): s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def find_symptom_columns(df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    """Return lists of detractor (1..10) and delighter (11..20) column names."""
    names = df.columns.tolist()
    # Prefer explicit "Symptom 1..20"
    sym_cols = [c for c in names if re.fullmatch(r"Symptom\s+([1-9]|1[0-9]|20)", str(c).strip(), re.I)]
    if len(sym_cols) >= 20:
        # keep natural numeric order
        sym_cols_sorted = sorted(sym_cols, key=lambda c: int(re.findall(r"\d+", c)[0]))
        return sym_cols_sorted[:10], sym_cols_sorted[10:20]
    # Fallback: indices K..AD (0-based 10..29)
    idx = list(range(10, 30))
    cols = [names[i] for i in idx if i < len(names)]
    return cols[:10], cols[10:20]

def read_symptom_library(xls_bytes: bytes) -> Tuple[List[str], List[str]]:
    """Read 'Symptoms' sheet: look for columns that contain Delighters / Detractors."""
    try:
        xls = pd.ExcelFile(io.BytesIO(xls_bytes))
        if "Symptoms" not in xls.sheet_names:
            return [], []
        sheet = pd.read_excel(xls, sheet_name="Symptoms")
        # Heuristics: use first two non-empty columns if unlabeled
        cols = [c for c in sheet.columns if str(c).strip()]
        dl, dt = [], []
        if any("delight" in str(c).lower() for c in cols):
            # labeled columns
            for c in cols:
                if "delight" in str(c).lower():
                    dl = [clean_text(v) for v in sheet[c].dropna().astype(str).tolist() if clean_text(v)]
                if "detract" in str(c).lower():
                    dt = [clean_text(v) for v in sheet[c].dropna().astype(str).tolist() if clean_text(v)]
        else:
            # take first two columns
            if len(cols) >= 2:
                dl = [clean_text(v) for v in sheet[cols[0]].dropna().astype(str).tolist() if clean_text(v)]
                dt = [clean_text(v) for v in sheet[cols[1]].dropna().astype(str).tolist() if clean_text(v)]
        # de-dup & keep order
        def _uniq(seq): 
            seen=set(); out=[]
            for s in seq:
                t=s.strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower()); out.append(t)
            return out
        return _uniq(dl), _uniq(dt)
    except Exception:
        return [], []

# Near-duplicate conflict groups
CONFLICT_GROUPS = [
    {"canonical": "Learning curve",
     "aliases": ["Initial difficulty", "Setup confusion", "First-use confusion"]},
    {"canonical": "Price/value",
     "aliases": ["Price mismatch", "Too expensive", "Cost"]},
    {"canonical": "Effectiveness - Frizz Free",
     "aliases": ["Not effective - frizz fighting", "Anti-frizz performance"]},
]

def _apply_conflict_dedupe(items: List[Tuple[str, float]], sim_cut: float = 0.90) -> List[Tuple[str, float]]:
    canon = {}
    for g in CONFLICT_GROUPS:
        base = g["canonical"].lower()
        for a in [g["canonical"], *g.get("aliases", [])]:
            canon[a.lower()] = base

    grouped = {}
    for label, score in items:
        key = canon.get(label.lower(), label.lower())
        if key not in grouped or score > grouped[key][1]:
            grouped[key] = (label, score)

    labels = [v[0] for v in grouped.values()]
    scores = [v[1] for v in grouped.values()]

    def jacc(a: str, b: str) -> float:
        sa, sb = set(a.lower().split()), set(b.lower().split())
        return 0.0 if not sa or not sb else len(sa & sb) / len(sa | sb)

    kept: List[Tuple[str, float]] = []
    for li, si in zip(labels, scores):
        if any(jacc(li, lj) >= 0.80 for lj, _ in kept):
            continue
        kept.append((li, si))
    return kept

def _norm(s): return re.sub(r"\s+", " ", str(s or "")).strip()

# ---------- LLM Extraction ----------
def llm_extract_symptoms(cli, model, review_text: str,
                         allowed_delighters: List[str], allowed_detractors: List[str],
                         max_each: int, require_evidence: bool, conserv_thresh: float):
    """Ask the LLM for JSON with symptoms + quotes + confidence. Abstain if weak."""
    if not cli:
        return {"delighters": [], "detractors": [], "new_candidates": {"delighters": [], "detractors": []}}

    sys = (
        "You are SharkNinja's review tagger. Select symptoms ONLY from the provided lists.\n"
        "If evidence is weak or ambiguous, ABSTAIN. Never guess.\n"
        "Return at most the requested number per section. Provide a short exact quote for each selection.\n"
        "If something clear is NOT in the lists, place it in new_candidates (do not include it in main lists).\n"
        "Avoid near-duplicates; prefer canonical phrasing (e.g., 'Learning curve' over 'Initial difficulty').\n"
        "Include numeric confidence 0.0–1.0 for each item."
    )
    user = {
        "review": review_text,
        "allowed": {"delighters": allowed_delighters, "detractors": allowed_detractors},
        "max_each": max_each,
        "require_quote": require_evidence,
        "confidence_floor": conserv_thresh,
        "format": {
            "delighters": [{"symptom": "string", "quote": "string", "confidence": 0.0}],
            "detractors": [{"symptom": "string", "quote": "string", "confidence": 0.0}],
            "new_candidates": {"delighters": ["string"], "detractors": ["string"]}
        }
    }

    req = {
        "model": model,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        "response_format": {"type": "json_object"},
    }
    if model_supports_temperature(model):
        req["temperature"] = 0.2  # safe & deterministic-ish for non-GPT-5 models

    try:
        out = cli.chat.completions.create(**req)
        return json.loads(out.choices[0].message.content or "{}")
    except Exception:
        return {"delighters": [], "detractors": [], "new_candidates": {"delighters": [], "detractors": []}}

def _filter_side(items_json: List[Dict], side_key: str, review_text: str,
                 stars: float, conservative: float, require_evidence: bool,
                 block_duplicates: bool, max_each: int) -> List[Tuple[str, float, str]]:
    """Post-filter for quotes, confidence, sentiment sanity, dedupe."""
    out: List[Tuple[str, float, str]] = []
    txt_lower = review_text.lower()
    for it in items_json or []:
        label = _norm(it.get("symptom"))
        quote = _norm(it.get("quote"))
        conf = float(it.get("confidence") or 0.0)
        if not label:
            continue
        if require_evidence and (not quote or quote.lower() not in txt_lower):
            continue
        # Sentiment sanity
        if not math.isnan(stars):
            if side_key == "delighters" and stars <= 2:
                conf -= 0.08
            if side_key == "detractors" and stars >= 4:
                conf -= 0.08
        if conf >= conservative:
            out.append((label, conf, quote))
    if block_duplicates:
        pared = _apply_conflict_dedupe([(l, c) for (l, c, _) in out])
        keep = {l for (l, _) in pared}
        out = [t for t in out if t[0] in keep]
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:max_each]

# ---------- Sidebar ----------
st.sidebar.header("📁 Upload Star Walk File")
uploaded_file = st.sidebar.file_uploader("Choose Excel (.xlsx)", type=["xlsx"])

st.sidebar.subheader("⚙️ Processing")
batch_size = st.sidebar.slider("Reviews to process per request", 1, 20, 10, help="Process a subset each time.")
st.sidebar.subheader("🎯 Accuracy Controls")
conservative = st.sidebar.slider("Conservativeness (higher = fewer, surer picks)", 0.50, 0.95, 0.80, 0.01)
require_evidence = st.sidebar.toggle("Require explicit quote in text", value=True)
block_duplicates = st.sidebar.toggle("Block near-duplicate symptoms", value=True)
max_per_side = st.sidebar.slider("Max per side (Delighters / Detractors)", 1, 10, 6)

st.sidebar.subheader("🤖 Model")
_model_choices = [
    ("Fast & economical – 4o-mini", "gpt-4o-mini"),
    ("Balanced – 4o", "gpt-4o"),
    ("Advanced – 4.1", "gpt-4.1"),
    ("Most advanced – GPT-5", "gpt-5"),
    ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
]
_default_model = st.session_state.get("llm_model", "gpt-4o-mini")
_default_idx = next((i for i, (_, mid) in enumerate(_model_choices) if mid == _default_model), 0)
label_choice = st.sidebar.selectbox("Model", options=[l for (l, _) in _model_choices], index=_default_idx)
selected_model = dict(_model_choices)[label_choice]
st.session_state["llm_model"] = selected_model

if model_supports_temperature(selected_model):
    st.sidebar.caption("Temperature set internally to 0.2 for consistency.")
else:
    st.sidebar.caption("This model ignores temperature (fixes unsupported 'temperature' error).")

# ---------- Body: Guard on upload ----------
if not uploaded_file:
    st.info("Upload your Excel file to begin.")
    st.stop()

# Read workbook bytes (we keep it to preserve formatting later)
xls_bytes = uploaded_file.getvalue()
df = pd.read_excel(io.BytesIO(xls_bytes), sheet_name=None)

# Choose primary sheet
sheet_name = "Star Walk scrubbed verbatims" if "Star Walk scrubbed verbatims" in df else list(df.keys())[0]
data = df[sheet_name].copy()

# Clean basic fields
for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
    if col in data.columns: data[col] = data[col].astype("string").str.upper()
if "Star Rating" in data.columns:
    data["Star Rating"] = pd.to_numeric(data["Star Rating"], errors="coerce")
if "Verbatim" in data.columns:
    data["Verbatim"] = data["Verbatim"].astype("string").map(clean_text)

# Symptom columns
det_cols, del_cols = find_symptom_columns(data)
all_symptom_cols = det_cols + del_cols

def row_is_missing_symptoms(row) -> bool:
    subset = row[all_symptom_cols] if set(all_symptom_cols).issubset(row.index) else pd.Series(dtype=object)
    if subset.empty: return False
    return subset.replace("", np.nan).isna().all()

missing_mask = data.apply(row_is_missing_symptoms, axis=1) if all_symptom_cols else pd.Series(False, index=data.index)
missing_indices = data.index[missing_mask].tolist()
missing_count = int(missing_mask.sum())

# Read library from "Symptoms" sheet
lib_delighters, lib_detractors = read_symptom_library(xls_bytes)

# ---------- Hero + Summary ----------
logo_html = (
    '<img class="sn-logo" '
    'src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" '
    'alt="SharkNinja logo" />'
)
st_html(f"""
<div class="hero-wrap">
  <div class="hero-inner">
    <div>
      <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
      <div class="hero-sub">Insights, trends, and ratings — fast.</div>
    </div>
    <div class="hero-right">{logo_html}</div>
  </div>
</div>
""", height=160)

# Snapshot cards
def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float((s <= 2).mean() * 100) if not s.empty else 0.0

def section_stats(sub: pd.DataFrame) -> Tuple[int, float, float]:
    cnt = len(sub)
    if cnt == 0 or "Star Rating" not in sub.columns: return 0, 0.0, 0.0
    avg = float(pd.to_numeric(sub["Star Rating"], errors="coerce").mean())
    pct = pct_12(sub["Star Rating"])
    return cnt, avg, pct

all_cnt, all_avg, all_low = section_stats(data)

st.markdown("### ⭐ Star Rating Metrics")
def card_html(title, count, avg, pct):
    return textwrap.dedent(f"""
    <div class="review-card" style="display:flex;justify-content:space-between;align-items:center">
      <div><b>{title}</b><br/><span class="badge">Count: {count:,}</span></div>
      <div><span class="badge">Avg ★ {avg:.1f}</span> <span class="badge">% 1–2★ {pct:.1f}%</span></div>
    </div>
    """)
st.markdown(card_html("All Reviews", all_cnt, all_avg, all_low), unsafe_allow_html=True)

# IQR of max chars per review
st.markdown("#### 🧪 Review Length IQR")
if "Verbatim" in data.columns and not data["Verbatim"].dropna().empty:
    lens = data["Verbatim"].fillna("").astype(str).map(len)
    q1, q3 = np.percentile(lens, [25, 75])
    iqr = q3 - q1
    st.write(f"IQR (characters): **{int(iqr)}** — Q1: {int(q1)}, Q3: {int(q3)}. Max: {int(lens.max())}")
else:
    st.write("No review text found to compute IQR.")

# ---------- “Missing Symptoms” banner ----------
st.markdown("---")
st.markdown(f"### 🧩 {missing_count} reviews missing symptoms")
st.caption("Only **Symptom 1–10 = Detractors** and **Symptom 11–20 = Delighters** will be filled (<=10 each side).")

# ---------- Session state for suggestions & timing ----------
if "symp_suggestions" not in st.session_state:
    # row_index -> {"del": [(label,conf,quote)], "det":[...], "new_del":[...], "new_det":[...]}
    st.session_state.symp_suggestions = {}

if "ema_secs_per_review" not in st.session_state:
    st.session_state.ema_secs_per_review = 1.2  # initial guess

if "processed_rows" not in st.session_state:
    st.session_state.processed_rows = set()

# ---------- Controls row ----------
c1, c2, c3, c4 = st.columns([1,1,1,2])
with c1:
    do_process = st.button(f"✨ Symptomize next {min(batch_size, max(0, missing_count - len(st.session_state.processed_rows)))} review(s)")
with c2:
    clear_suggestions = st.button("🧹 Clear pending suggestions")
with c3:
    apply_now = st.button("✅ Apply approved to sheet")
with c4:
    st.write("")  # spacer

# ---------- Processing batch ----------
api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
cli = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None

if clear_suggestions:
    st.session_state.symp_suggestions = {}
    st.session_state.processed_rows = set()
    st.success("Cleared pending suggestions.")

if do_process:
    if not cli:
        st.warning("OpenAI not configured. Set `OPENAI_API_KEY` to enable symptomization.")
    else:
        todo = [i for i in missing_indices if i not in st.session_state.processed_rows][:batch_size]
        if not todo:
            st.info("No more missing-symptom reviews to process in this batch.")
        else:
            prog = st.progress(0, text="Starting…")
            t0 = time.time()
            for k, ridx in enumerate(todo, start=1):
                row = data.loc[ridx]
                txt = clean_text(row.get("Verbatim", ""))
                stars = float(row.get("Star Rating")) if pd.notna(row.get("Star Rating")) else float("nan")
                t_start = time.time()

                result = llm_extract_symptoms(
                    cli, selected_model, txt,
                    lib_delighters, lib_detractors,
                    max_each=max_per_side,
                    require_evidence=require_evidence,
                    conserv_thresh=conservative
                )
                dels = _filter_side(result.get("delighters"), "delighters", txt, stars,
                                    conservative, require_evidence, block_duplicates, max_per_side)
                dets = _filter_side(result.get("detractors"), "detractors", txt, stars,
                                    conservative, require_evidence, block_duplicates, max_per_side)

                st.session_state.symp_suggestions[ridx] = {
                    "del": dels, "det": dets,
                    "new_del": list(dict.fromkeys([_norm(x) for x in (result.get("new_candidates", {}).get("delighters") or []) if _norm(x)])),
                    "new_det": list(dict.fromkeys([_norm(x) for x in (result.get("new_candidates", {}).get("detractors") or []) if _norm(x)])),
                    "text": txt,
                    "stars": stars,
                }
                st.session_state.processed_rows.add(ridx)

                # timing + ETA
                dt = max(0.05, time.time() - t_start)
                alpha = 0.35
                st.session_state.ema_secs_per_review = (1 - alpha) * st.session_state.ema_secs_per_review + alpha * dt
                remaining = len(todo) - k
                eta = remaining * st.session_state.ema_secs_per_review
                prog.progress(k / len(todo), text=f"Processed {k}/{len(todo)} • ~{eta:.1f}s remaining")

            prog.progress(1.0, text="Batch complete")
            st.success(f"Processed {len(todo)} review(s).")

# ---------- Approval UI ----------
if st.session_state.symp_suggestions:
    st.markdown("## 🔎 Review & Approve Suggestions")
    for ridx, pack in st.session_state.symp_suggestions.items():
        txt = pack.get("text", "")
        stars = pack.get("stars", float("nan"))
        dels = pack.get("del", [])
        dets = pack.get("det", [])
        new_del = pack.get("new_del", [])
        new_det = pack.get("new_det", [])

        with st.expander(f"Review #{ridx} • Stars: {stars if not math.isnan(stars) else '–'} • {len(dels)} delighters / {len(dets)} detractors", expanded=False):
            st.markdown(f"**Full review:**\n\n> {st._utils.escape_markdown(txt) if txt else '_(empty)_'}")

            a1, a2 = st.columns(2)
            with a1:
                st.markdown("**Delighters (checked = approve)**")
                keep_del = []
                for lab, conf, quote in dels:
                    if st.checkbox(f"{lab} — {conf:.2f}", key=f"del_{ridx}_{lab}", value=True,
                                   help=f'Evidence: "{quote}"'):
                        keep_del.append(lab)
            with a2:
                st.markdown("**Detractors (checked = approve)**")
                keep_det = []
                for lab, conf, quote in dets:
                    if st.checkbox(f"{lab} — {conf:.2f}", key=f"det_{ridx}_{lab}", value=True,
                                   help=f'Evidence: "{quote}"'):
                        keep_det.append(lab)

            # New candidates (approval into library)
            if new_del or new_det:
                st.markdown("---")
                st.markdown("**New candidate symptoms detected (not in library):**")
                c1, c2 = st.columns(2)
                with c1:
                    if new_del:
                        st.caption("Proposed Delighters")
                        for s in new_del:
                            st.checkbox(f"Approve: {s}", key=f"newdel_{ridx}_{s}", value=False)
                with c2:
                    if new_det:
                        st.caption("Proposed Detractors")
                        for s in new_det:
                            st.checkbox(f"Approve: {s}", key=f"newdet_{ridx}_{s}", value=False)

# ---------- Apply selections to DataFrame & Excel ----------
def write_symptoms_to_df(df_in: pd.DataFrame, ridx: int, detractors: List[str], delighters: List[str]):
    """Write approved items into Symptom 1..10 (detractors), 11..20 (delighters)."""
    for i in range(10):
        col = det_cols[i] if i < len(det_cols) else None
        if col: df_in.at[ridx, col] = detractors[i] if i < len(detractors) else ""
    for j in range(10):
        col = del_cols[j] if j < len(del_cols) else None
        if col: df_in.at[ridx, col] = delighters[j] if j < len(delighters) else ""

def apply_to_library(xls_path: str, approve_map: Dict[str, List[str]]):
    """Append approved new candidates into Symptoms sheet without duplicating."""
    try:
        wb = load_workbook(xls_path)
        if "Symptoms" not in wb.sheetnames:
            return
        ws = wb["Symptoms"]
        # Try to find delighters / detractors columns by header row 1
        headers = [str(c.value).strip().lower() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
        try:
            del_idx = headers.index(next(h for h in headers if "delight" in h))
        except StopIteration:
            del_idx = 0
        try:
            det_idx = headers.index(next(h for h in headers if "detract" in h))
        except StopIteration:
            det_idx = 1

        # Build existing sets
        max_row = ws.max_row
        existing_del = set()
        existing_det = set()
        for r in range(2, max_row + 1):
            v1 = ws.cell(row=r, column=del_idx+1).value
            v2 = ws.cell(row=r, column=det_idx+1).value
            if v1: existing_del.add(str(v1).strip().lower())
            if v2: existing_det.add(str(v2).strip().lower())

        # Append new approved
        add_del = [s for s in approve_map.get("delighters", []) if s.lower() not in existing_del]
        add_det = [s for s in approve_map.get("detractors", []) if s.lower() not in existing_det]
        # Append at end
        row_ptr = max_row + 1
        for s in add_del:
            ws.cell(row=row_ptr, column=del_idx+1, value=s); row_ptr += 1
        row_ptr = max_row + 1
        for s in add_det:
            ws.cell(row=row_ptr, column=det_idx+1, value=s); row_ptr += 1
        wb.save(xls_path)
    except Exception:
        pass

def save_preserving_format(xls_bytes: bytes, out_path: str, df_updated: pd.DataFrame,
                           sheetname: str, rows_to_update: List[int],
                           col_map: Dict[str, int]):
    """
    Load original workbook and set only Symptom cells for selected rows to preserve formatting.
    col_map is column-name -> 1-based index in sheet (openpyxl).
    """
    wb = load_workbook(io.BytesIO(xls_bytes))
    if sheetname not in wb.sheetnames:
        # fallback: first sheet
        sheetname = wb.sheetnames[0]
    ws = wb[sheetname]

    # Build a lookup from df index to worksheet row number:
    # We assume the first data row is 2 (headers at row 1) and order preserved post-read.
    # If the sheet has filters / hidden rows, this still works for values.
    # Map DF position (0..n-1) -> ws row = 2 + pos
    df_pos_to_ws_row = {pos: 2 + pos for pos in range(len(df_updated))}

    for ridx in rows_to_update:
        ws_row = df_pos_to_ws_row.get(df_updated.index.get_loc(ridx), None)
        if ws_row is None: 
            continue
        # Write detractors 1..10
        for i in range(10):
            colname = det_cols[i] if i < len(det_cols) else None
            if colname and colname in col_map:
                val = df_updated.at[ridx, colname]
                ws.cell(row=ws_row, column=col_map[colname], value=val if val != "" else None)
        # Write delighters 11..20
        for j in range(10):
            colname = del_cols[j] if j < len(del_cols) else None
            if colname and colname in col_map:
                val = df_updated.at[ridx, colname]
                ws.cell(row=ws_row, column=col_map[colname], value=val if val != "" else None)

    wb.save(out_path)

def build_colmap_from_sheet(xls_bytes: bytes, sheetname: str) -> Dict[str, int]:
    wb = load_workbook(io.BytesIO(xls_bytes), read_only=False)
    if sheetname not in wb.sheetnames: sheetname = wb.sheetnames[0]
    ws = wb[sheetname]
    header_row = next(ws.iter_rows(min_row=1, max_row=1))
    mapping = {}
    for idx, cell in enumerate(header_row, start=1):
        label = str(cell.value).strip() if cell.value else ""
        if label: mapping[label] = idx
    return mapping

if apply_now and st.session_state.symp_suggestions:
    # Collect approvals & write into DF
    updated_rows = []
    approved_new_del = []
    approved_new_det = []
    for ridx, pack in st.session_state.symp_suggestions.items():
        keep_del, keep_det = [], []
        # read checkbox states
        for lab, conf, quote in pack.get("del", []):
            if st.session_state.get(f"del_{ridx}_{lab}", False):
                keep_del.append(lab)
        for lab, conf, quote in pack.get("det", []):
            if st.session_state.get(f"det_{ridx}_{lab}", False):
                keep_det.append(lab)
        # apply to df
        write_symptoms_to_df(data, ridx, detractors=keep_det[:10], delighters=keep_del[:10])
        if keep_del or keep_det:
            updated_rows.append(ridx)
        # new candidate approvals
        for s in pack.get("new_del", []):
            if st.session_state.get(f"newdel_{ridx}_{s}", False):
                approved_new_del.append(s)
        for s in pack.get("new_det", []):
            if st.session_state.get(f"newdet_{ridx}_{s}", False):
                approved_new_det.append(s)

    if not updated_rows and not (approved_new_del or approved_new_det):
        st.info("No changes selected.")
    else:
        # Save a copy with preserved formatting by writing only changed symptom cells
        out_path = "/mnt/data/StarWalk_symptomized.xlsx"
        colmap = build_colmap_from_sheet(xls_bytes, sheet_name)
        try:
            save_preserving_format(
                xls_bytes, out_path, data, sheet_name, updated_rows, colmap
            )
            # Also update library if needed
            if approved_new_del or approved_new_det:
                apply_to_library(
                    out_path,
                    {"delighters": list(dict.fromkeys(approved_new_del)),
                     "detractors": list(dict.fromkeys(approved_new_det))}
                )
            st.success("Updates applied. You can download the updated workbook below.")
            with open(out_path, "rb") as f:
                st.download_button("⬇️ Download updated Excel", f, file_name="StarWalk_symptomized.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error(f"Failed to write Excel with preserved formatting: {e}")
            # Fallback: CSV download of the full DataFrame
            csv_bytes = data.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Download updated data (CSV fallback)", csv_bytes, file_name="StarWalk_symptomized.csv", mime="text/csv")

# ---------- Simple distribution chart ----------
st.markdown("---")
st.markdown("### 📊 Star Rating Distribution")
if "Star Rating" in data.columns:
    star_counts = pd.to_numeric(data["Star Rating"], errors="coerce").dropna().value_counts().sort_index()
else:
    star_counts = pd.Series([], dtype="int")
total_reviews = len(data)
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

# ---------- Footer callout ----------
st.markdown(
    '<div class="callout warn">⚠️ AI can make mistakes. This tool abstains when evidence is weak and requires quotes (if enabled). Please review suggestions before applying.</div>',
    unsafe_allow_html=True
)

