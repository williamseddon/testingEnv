# starwalk_ui_v4.py — BEST-IN-CLASS UI (Oct 2025)
# Streamlit App — Dynamic Symptoms • Clean UI • Model Selector • Smart Auto‑Symptomization
# Approval Inbox with examples • Exact Excel export (K–T dets, U–AD dels) • Color‑Coded
# Notes:
# - Does NOT rename headers in your workbook. It writes values only into template slots K–T and U–AD.
# - Caps at 10 per side by design and flags rows where >10 were detected.
# - Nothing runs automatically; you choose N or ALL, or one‑click "Process Missing Both".
# Requirements: streamlit>=1.28, pandas, openpyxl, openai (optional)

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime

# Optional: OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI = False

# Excel handling
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v4")

# Minimal, consistent styling
st.markdown(
    """
    <style>
      .metric-row {display:flex; gap:16px; flex-wrap:wrap;}
      .chip-wrap {display:flex; flex-wrap:wrap; gap:8px;}
      .chip {padding:4px 10px; border-radius:999px; border:1px solid #e5e7eb; background:#f8fafc; font-size:12.5px;}
      .chip.red {background:#fdeaea; border-color:#f5b3b3;}
      .chip.green {background:#e7f7ed; border-color:#a8e6bf;}
      .muted {color:#667085;}
      .section {padding:12px 16px; border:1px solid #e5e7eb; border-radius:12px; background:white;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Dynamic Symptoms • Smart Auto‑Symptomize • Approval Inbox • Exact Excel Export (K–T / U–AD)")

# Compatibility shim (prevents NameError if referenced in legacy branches)
build_clicked = False

# ------------------- Utilities -------------------
NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}

def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

def is_filled(val) -> bool:
    if pd.isna(val):
        return False
    s = str(val).strip()
    return (s != "") and (s.upper() not in NON_VALUES)

@st.cache_data(show_spinner=False)
def get_symptom_whitelists(file_bytes: bytes) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    bio = io.BytesIO(file_bytes)
    try:
        df_sym = pd.read_excel(bio, sheet_name="Symptoms")
    except Exception:
        return [], [], {}
    if df_sym is None or df_sym.empty:
        return [], [], {}

    df_sym.columns = [str(c).strip() for c in df_sym.columns]
    lowcols = {c.lower(): c for c in df_sym.columns}

    alias_col = next((lowcols.get(c) for c in ["aliases", "alias"] if c in lowcols), None)
    label_col = next((lowcols.get(c) for c in ["symptom", "label", "name", "item"] if c in lowcols), None)
    type_col  = next((lowcols.get(c) for c in ["type", "polarity", "category", "side"] if c in lowcols), None)

    POS_TAGS = {"delighter", "delighters", "positive", "pos", "pros"}
    NEG_TAGS = {"detractor", "detractors", "negative", "neg", "cons"}

    def _clean(series: pd.Series) -> List[str]:
        vals = series.dropna().astype(str).map(str.strip)
        out, seen = [], set()
        for v in vals:
            if v and v not in seen:
                seen.add(v); out.append(v)
        return out

    delighters, detractors, alias_map = [], [], {}

    if label_col and type_col:
        df_sym[type_col] = df_sym[type_col].astype(str).str.lower().str.strip()
        delighters = _clean(df_sym.loc[df_sym[type_col].isin(POS_TAGS), label_col])
        detractors = _clean(df_sym.loc[df_sym[type_col].isin(NEG_TAGS), label_col])
        if alias_col:
            for _, r in df_sym.iterrows():
                lbl = str(r.get(label_col, "")).strip()
                als = str(r.get(alias_col, "")).strip()
                if lbl and als:
                    alias_map[lbl] = [p.strip() for p in re.split(r"[|,]", als) if p.strip()]
    else:
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map

@st.cache_data(show_spinner=False)
def read_symptoms_sheet(file_bytes: bytes) -> pd.DataFrame:
    bio = io.BytesIO(file_bytes)
    try:
        df_sym = pd.read_excel(bio, sheet_name="Symptoms")
        if df_sym is None:
            return pd.DataFrame()
        df_sym.columns = [str(c).strip() for c in df_sym.columns]
        return df_sym
    except Exception:
        return pd.DataFrame()

# ------------------- Column detection & missing flags -------------------
def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    cols = [str(c).strip() for c in df.columns]
    man_det = [f"Symptom {i}" for i in range(1, 11) if f"Symptom {i}" in cols]
    man_del = [f"Symptom {i}" for i in range(11, 21) if f"Symptom {i}" in cols]
    ai_det  = [c for c in cols if re.fullmatch(r"AI Symptom Detractor \d+", c)]
    ai_del  = [c for c in cols if re.fullmatch(r"AI Symptom Delighter \d+", c)]
    return {
        "manual_detractors": man_det,
        "manual_delighters": man_del,
        "ai_detractors": ai_det,
        "ai_delighters": ai_del,
    }

def row_has_any(row: pd.Series, columns: List[str]) -> bool:
    if not columns:
        return False
    for c in columns:
        if c in row and is_filled(row[c]):
            return True
    return False

def detect_missing(df: pd.DataFrame, colmap: Dict[str, List[str]]) -> pd.DataFrame:
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]
    out = df.copy()
    out["Has_Detractors"] = out.apply(lambda r: row_has_any(r, det_cols), axis=1)
    out["Has_Delighters"] = out.apply(lambda r: row_has_any(r, del_cols), axis=1)
    out["Needs_Detractors"] = ~out["Has_Detractors"]
    out["Needs_Delighters"] = ~out["Has_Delighters"]
    out["Needs_Symptomization"] = out["Needs_Detractors"] & out["Needs_Delighters"]
    return out

# ------------------- Fixed template column mapping -------------------
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]
AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]

def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    for h in AI_DET_HEADERS + AI_DEL_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in

# ------------------- Canonicalization & alias mapping -------------------

def _canon(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()

def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label

# ---------- LLM labeler ----------

def _openai_labeler(
    verbatim: str,
    client,
    model: str,
    temperature: float,
    delighters: List[str],
    detractors: List[str],
    alias_map: Dict[str, List[str]],
    del_map: Dict[str, str],
    det_map: Dict[str, str],
    alias_to_label: Dict[str, str],
) -> Tuple[List[str], List[str], List[str], List[str], int, int]:
    """Classify a review using only whitelist labels.
    Returns (dels10, dets10, unlisted_dels10, unlisted_dets10, raw_del_count, raw_det_count)
    where raw_*_count is the number of unique mapped labels before the 10‑cap.
    """
    if not verbatim or not verbatim.strip():
        return [], [], [], [], 0, 0

    sys = (
        "Classify this Shark Glossi review into delighters and detractors. "
        "Use ONLY the provided whitelists; do NOT invent labels. "
        "If a synonym is close but not listed, output it under the correct 'unlisted' bucket.\n"
        f"DELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}\n"
        f"DETRACTORS = {json.dumps(detractors, ensure_ascii=False)}\n"
        f"ALIASES = {json.dumps(alias_map, ensure_ascii=False)}\n"
        'Return strict JSON: {"delighters":[],"detractors":[],"unlisted_delighters":[],"unlisted_detractors":[]}'
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": f"Review:\n\"\"\"{verbatim.strip()}\"\"\""},
            ],
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)

        def _map_side(items: List[str], side: str) -> List[str]:
            mapped: List[str] = []
            for x in (items or []):
                key = _canon(x)
                if side == "del":
                    label = del_map.get(key)
                    if not label:
                        alias_label = alias_to_label.get(key)
                        if alias_label and alias_label in delighters:
                            label = alias_label
                else:
                    label = det_map.get(key)
                    if not label:
                        alias_label = alias_to_label.get(key)
                        if alias_label and alias_label in detractors:
                            label = alias_label
                if label and label not in mapped:
                    mapped.append(label)
            return mapped

        mapped_dels = _map_side(data.get("delighters", []), side="del")
        mapped_dets = _map_side(data.get("detractors", []), side="det")
        raw_del_count = len(mapped_dels)
        raw_det_count = len(mapped_dets)

        dels10 = mapped_dels[:10]
        dets10 = mapped_dets[:10]
        unl_dels10 = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
        unl_dets10 = [x for x in (data.get("unlisted_detractors", []) or [])][:10]
        return dels10, dets10, unl_dels10, unl_dets10, raw_del_count, raw_det_count
    except Exception:
        return [], [], [], [], 0, 0

# ------------------- Export helpers -------------------

def _clear_template_slots(ws: Worksheet, row_index: int):
    for col_idx in DET_INDEXES + DEL_INDEXES:
        ws.cell(row=row_index, column=col_idx, value=None)


def generate_template_workbook_bytes(
    original_file,
    updated_df: pd.DataFrame,
    processed_idx: Optional[Set[int]] = None,
    overwrite_processed_slots: bool = False,
) -> bytes:
    """Return workbook bytes with K–T (dets) and U–AD (dels) written.
    - Does NOT change headers. Only writes cell values and color for non‑empty.
    - If overwrite_processed_slots is True, clears K–T + U–AD only for processed rows before writing.
    """
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    df2 = ensure_ai_columns(updated_df.copy())

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    pset = set(processed_idx or [])

    for i, (rid, r) in enumerate(df2.iterrows(), start=2):
        if overwrite_processed_slots and (rid in pset):
            _clear_template_slots(ws, i)
        # Detractors K–T
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            if cell_value is not None:
                cell = ws.cell(row=i, column=col_idx, value=cell_value)
                cell.fill = fill_red
        # Delighters U–AD
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            if cell_value is not None:
                cell = ws.cell(row=i, column=col_idx, value=cell_value)
                cell.fill = fill_green

    # column widths for readability
    for c in DET_INDEXES + DEL_INDEXES:
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO(); wb.save(out)
    return out.getvalue()

# ------------------- Helpers: add new symptoms to Symptoms sheet -------------------

def add_new_symptoms_to_workbook(original_file, selections: List[Tuple[str, str]]) -> bytes:
    original_file.seek(0)
    wb = load_workbook(original_file)
    if "Symptoms" not in wb.sheetnames:
        ws = wb.create_sheet("Symptoms")
        ws.cell(row=1, column=1, value="Symptom")
        ws.cell(row=1, column=2, value="Type")
        ws.cell(row=1, column=3, value="Aliases")
    else:
        ws = wb["Symptoms"]

    headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
    hlow = [h.lower() for h in headers]

    def _col_idx(names: List[str]) -> int:
        for nm in names:
            if nm.lower() in hlow:
                return hlow.index(nm.lower()) + 1
        return -1

    col_label = _col_idx(["symptom", "label", "name", "item"]) or 1
    col_type  = _col_idx(["type", "polarity", "category", "side"]) or 2
    col_alias = _col_idx(["aliases", "alias"])  # may be -1

    if len(headers) < col_label or not headers[col_label-1]:
        ws.cell(row=1, column=col_label, value="Symptom")
    if len(headers) < col_type or not headers[col_type-1]:
        ws.cell(row=1, column=col_type, value="Type")
    if col_alias == -1:
        col_alias = len(headers) + 1
        ws.cell(row=1, column=col_alias, value="Aliases")

    existing = set()
    for r_i in range(2, ws.max_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if v:
            existing.add(str(v).strip())

    for label, side in selections:
        lab = str(label).strip()
        if not lab:
            continue
        if lab in existing:
            continue
        row_new = ws.max_row + 1
        ws.cell(row=row_new, column=col_label, value=lab)
        ws.cell(row=row_new, column=col_type, value=str(side).strip() or "")
        # leave aliases blank by default
        existing.add(lab)

    out = io.BytesIO(); wb.save(out); out.seek(0)
    return out.getvalue()

# ------------------- File Upload -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

# Read once into bytes for caching + multiple passes
uploaded_bytes = uploaded_file.read()
uploaded_file.seek(0)

# Reviews sheet
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)

if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column.")
    st.stop()

# Normalize column names (trim whitespace)
df.columns = [str(c).strip() for c in df.columns]
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms from sheet (cached)
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# Build canonical maps for robust matching
DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

# ------------------- Detection & KPIs -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

# KPI bar
total = len(work)
need_del = int(work["Needs_Delighters"].sum())
need_det = int(work["Needs_Detractors"].sum())
need_both = int(work["Needs_Symptomization"].sum())

k1, k2, k3 = st.columns(3)
with k1:
    st.metric("Total reviews", f"{total:,}")
with k2:
    st.metric("Need Delighters", f"{need_del:,}")
with k3:
    st.metric("Need Detractors", f"{need_det:,}")
st.info(f"Number of reviews missing **both** sides: **{need_both:,}**")

# ------------------- Symptomize Center -------------------
st.subheader("🧪 Symptomize")

# Scope & run controls
left, mid, right = st.columns([2,1.1,1.1])
with left:
    scope = st.selectbox(
        "Choose scope",
        ["Missing both", "Any missing", "Missing delighters only", "Missing detractors only"],
        index=0,
    )
with mid:
    n_to_process = st.number_input("N (from top of scope)", min_value=1, max_value=max(1, total), value=min(50, max(1, need_both)), step=1)
with right:
    overwrite_slots = st.toggle("Overwrite template slots for processed rows", value=False, help="If on, clears K–T and U–AD for processed rows before writing new values.")

# Build target per scope
if scope == "Missing both":
    target = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
elif scope == "Missing delighters only":
    target = work[(work["Needs_Delighters"]) & (~work["Needs_Detractors"]) ]
elif scope == "Missing detractors only":
    target = work[(~work["Needs_Delighters"]) & (work["Needs_Detractors"]) ]
else:
    target = work[(work["Needs_Delighters"]) | (work["Needs_Detractors"]) ]

st.write(f"🔎 **{len(target):,} reviews** match the selected scope.")

c_run1, c_run2, c_run3 = st.columns([1.3,1,1.8])
with c_run1:
    run_n_btn = st.button("▶️ Run N in scope")
with c_run2:
    run_all_btn = st.button("⏩ Run ALL in scope")
with c_run3:
    run_missing_both_btn = st.button("✨ One‑click: Process ALL missing BOTH")

# Detailed preview (optional)
with st.expander("Preview in-scope rows", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

processed_rows: List[Dict] = []
processed_idx_set: Set[int] = set()
over10_deli_count = 0
over10_detr_count = 0

client = OpenAI(api_key=(st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))) if (_HAS_OPENAI and (st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))) else None

st.sidebar.header("🤖 LLM Settings")
MODEL_CHOICES = {
    "Fast – GPT‑4o‑mini": "gpt-4o-mini",
    "Balanced – GPT‑4o": "gpt-4o",
    "Advanced – GPT‑4.1": "gpt-4.1",
    "Most Advanced – GPT‑5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()), index=1)
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

# Helper: html escape for chips
_def_esc_repl = [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")]

def _esc_html(s: str) -> str:
    t = str(s)
    for a, b in _def_esc_repl:
        t = t.replace(a, b)
    return t

# --- Run handlers ---
def _run_symptomize(rows_df: pd.DataFrame):
    global over10_deli_count, over10_detr_count
    max_per_side = 10
    prog = st.progress(0.0)
    total_n = max(1, len(rows_df))
    did = 0
    for k, (idx, row) in enumerate(rows_df.iterrows(), start=1):
        vb = row.get("Verbatim", "")
        needs_deli = bool(row.get("Needs_Delighters", False))
        needs_detr = bool(row.get("Needs_Detractors", False))
        try:
            dels, dets, unl_dels, unl_dets, raw_deli_count, raw_detr_count = _openai_labeler(
                vb, client, selected_model, temperature,
                DELIGHTERS, DETRACTORS, ALIASES,
                DEL_MAP, DET_MAP, ALIAS_TO_LABEL
            ) if client else ([], [], [], [], 0, 0)
        except Exception:
            dels, dets, unl_dels, unl_dets, raw_deli_count, raw_detr_count = [], [], [], [], 0, 0

        wrote_dets, wrote_dels = [], []
        if needs_detr and dets:
            for j, lab in enumerate(dets[:max_per_side]):
                col = f"AI Symptom Detractor {j+1}"
                if col not in df.columns: df[col] = None
                df.loc[idx, col] = lab
            wrote_dets = dets[:max_per_side]
        if needs_deli and dels:
            for j, lab in enumerate(dels[:max_per_side]):
                col = f"AI Symptom Delighter {j+1}"
                if col not in df.columns: df[col] = None
                df.loc[idx, col] = lab
            wrote_dels = dels[:max_per_side]

        processed_rows.append({
            "Index": int(idx),
            "Verbatim": str(vb),  # full text — no truncation
            "Added_Detractors": wrote_dets,
            "Added_Delighters": wrote_dels,
            "Unlisted_Detractors": unl_dets,
            "Unlisted_Delighters": unl_dels,
            "Over10_Detractors": bool(raw_detr_count > 10),
            "Over10_Delighters": bool(raw_deli_count > 10),
        })
        if raw_deli_count > 10: over10_deli_count += 1
        if raw_detr_count > 10: over10_detr_count += 1

        processed_idx_set.add(int(idx))
        did += 1
        prog.progress(k/total_n)
    return did

if client is not None and (run_n_btn or run_all_btn or run_missing_both_btn):
    if run_missing_both_btn:
        rows_iter = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
    else:
        rows_iter = target.head(int(n_to_process)) if run_n_btn else target

    did = _run_symptomize(rows_iter)
    st.success(f"Symptomized {did} review(s).")
    if over10_deli_count or over10_detr_count:
        st.warning(
            f"Note: {over10_deli_count} review(s) had >10 delighters and {over10_detr_count} had >10 detractors. "
            "Only the first 10 per side are written to the template columns (U–AD / K–T)."
        )

# ------------------- Review results (beautiful chips) -------------------
if processed_rows:
    st.subheader("🧾 Processed Reviews (this run)")
    for rec in processed_rows:
        flags = []
        if rec.get('Over10_Detractors'): flags.append('⚠️ >10 dets')
        if rec.get('Over10_Delighters'): flags.append('⚠️ >10 dels')
        head = f"Row {rec['Index']} — Dets: {len(rec['Added_Detractors'])} • Dels: {len(rec['Added_Delighters'])}" + (" • " + " | ".join(flags) if flags else "")
        with st.expander(head):
            st.markdown("**Verbatim**")
            st.write(rec["Verbatim"])  # full
            st.markdown("**Detractors added**")
            st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip red'>{_esc_html(x)}</span>" for x in rec["Added_Detractors"]]) + "</div>", unsafe_allow_html=True)
            st.markdown("**Delighters added**")
            st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip green'>{_esc_html(x)}</span>" for x in rec["Added_Delighters"]]) + "</div>", unsafe_allow_html=True)
            if rec["Unlisted_Detractors"]:
                st.markdown("**Unlisted detractors (candidates)**")
                st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip red'>{_esc_html(x)}</span>" for x in rec["Unlisted_Detractors"]]) + "</div>", unsafe_allow_html=True)
            if rec["Unlisted_Delighters"]:
                st.markdown("**Unlisted delighters (candidates)**")
                st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip green'>{_esc_html(x)}</span>" for x in rec["Unlisted_Delighters"]]) + "</div>", unsafe_allow_html=True)

# ------------------- New Symptom Candidates (Approval Inbox) -------------------
# Aggregate from processed_rows
cand_del: Dict[str, List[int]] = {}
cand_det: Dict[str, List[int]] = {}
for rec in processed_rows:
    for u in rec.get("Unlisted_Delighters", []) or []:
        cand_del.setdefault(u, []).append(rec["Index"])
    for u in rec.get("Unlisted_Detractors", []) or []:
        cand_det.setdefault(u, []).append(rec["Index"])

cand_total = len(cand_del) + len(cand_det)
if cand_total:
    st.subheader("🟡 New Symptom Inbox — Review & Approve")

    def _mk_table(cmap: Dict[str, List[int]], side_label: str) -> pd.DataFrame:
        rows_tbl = []
        for sym, refs in sorted(cmap.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            examples = []
            for ridx in refs[:3]:
                try:
                    examples.append(str(df.loc[ridx, "Verbatim"]))
                except Exception:
                    pass
            rows_tbl.append({
                "Add": False,
                "Label": sym,
                "Side": side_label,
                "Count": len(refs),
                "Examples": " | ".join(["— "+e for e in examples])
            })
        return pd.DataFrame(rows_tbl) if rows_tbl else pd.DataFrame({"Add": [], "Label": [], "Side": [], "Count": [], "Examples": []})

    tbl_del = _mk_table(cand_del, "Delighter")
    tbl_det = _mk_table(cand_det, "Detractor")

    with st.form("new_symptom_candidates_form"):
        cA, cB = st.columns(2)
        with cA:
            st.markdown("**Delighter candidates**")
            editor_del = st.data_editor(
                tbl_del,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Check to add to the Symptoms sheet"),
                    "Label": st.column_config.TextColumn(),
                    "Side": st.column_config.TextColumn(disabled=True),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="cand_del_editor",
            )
        with cB:
            st.markdown("**Detractor candidates**")
            editor_det = st.data_editor(
                tbl_det,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Check to add to the Symptoms sheet"),
                    "Label": st.column_config.TextColumn(),
                    "Side": st.column_config.TextColumn(disabled=True),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="cand_det_editor",
            )
        add_btn = st.form_submit_button("✅ Add selected to Symptoms & Download updated workbook")

    # References per candidate
    st.markdown("**References per candidate**")
    for sym, refs in sorted(cand_det.items(), key=lambda kv: -len(kv[1])):
        with st.expander(f"Detractor — {sym} • {len(refs)} reference(s)"):
            ref_rows = []
            for ridx in refs:
                row_dict = {"Index": int(ridx)}
                if "Star Rating" in df.columns:
                    row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])  # full
                ref_rows.append(row_dict)
            st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)
    for sym, refs in sorted(cand_del.items(), key=lambda kv: -len(kv[1])):
        with st.expander(f"Delighter — {sym} • {len(refs)} reference(s)"):
            ref_rows = []
            for ridx in refs:
                row_dict = {"Index": int(ridx)}
                if "Star Rating" in df.columns:
                    row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])  # full
                ref_rows.append(row_dict)
            st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)

    if add_btn:
        selections: List[Tuple[str, str]] = []
        try:
            if isinstance(editor_del, pd.DataFrame) and not editor_del.empty:
                for _, r_ in editor_del.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        selections.append((str(r_["Label"]).strip(), "Delighter"))
        except Exception:
            pass
        try:
            if isinstance(editor_det, pd.DataFrame) and not editor_det.empty:
                for _, r_ in editor_det.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        selections.append((str(r_["Label"]).strip(), "Detractor"))
        except Exception:
            pass

        if selections:
            updated_bytes = add_new_symptoms_to_workbook(uploaded_file, selections)
            st.download_button(
                "⬇️ Download 'Symptoms' (updated)",
                data=updated_bytes,
                file_name="Symptoms_Updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success(f"Queued {len(selections)} new label(s) into the Symptoms tab.")
        else:
            st.info("No candidates selected.")

# ------------------- Download Symptomized Workbook -------------------
st.subheader("📦 Download Symptomized Workbook")
try:
    file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
except Exception:
    file_base = 'Reviews'

export_bytes = generate_template_workbook_bytes(
    uploaded_file,
    df,
    processed_idx=processed_idx_set if processed_idx_set else None,
    overwrite_processed_slots=bool(overwrite_slots),
)

st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------- Browse Symptoms (clean chips) -------------------
st.subheader("🔎 Browse Symptoms")
view_side = st.selectbox("View", ["Detractors", "Delighters"], index=0)

# Build counts from existing columns in the sheet (manual + AI)
col_det_all = colmap.get("manual_detractors", []) + colmap.get("ai_detractors", [])
col_del_all = colmap.get("manual_delighters", []) + colmap.get("ai_delighters", [])

def _label_counts(df_in: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    vals: List[str] = []
    for c in cols:
        if c in df_in.columns:
            series = df_in[c].dropna().astype(str).map(str.strip)
            vals.extend([v for v in series if is_filled(v)])
    vc = pd.Series(vals).value_counts().reset_index() if vals else pd.DataFrame(columns=["Label","Count"]).assign(Label=[], Count=[])
    if not vc.empty:
        vc.columns = ["Label", "Count"]
    return vc

counts_df = _label_counts(df, col_det_all if view_side=="Detractors" else col_del_all)

st.markdown("**Top labels**")
if counts_df.empty:
    st.write("(none found)")
else:
    chips_html = "<div class='chip-wrap'>" + "".join([f"<span class='chip {'red' if view_side=='Detractors' else 'green'}'>{_esc_html(l)} · {c}</span>" for l, c in counts_df.head(60).itertuples(index=False)]) + "</div>"
    st.markdown(chips_html, unsafe_allow_html=True)

# ------------------- Download current Symptoms sheet (optional) -------------------
st.subheader("🗂️ Export Symptoms sheet (as-is)")
df_sym_raw = read_symptoms_sheet(uploaded_bytes)
if df_sym_raw.empty:
    st.write("No 'Symptoms' sheet found.")
else:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df_sym_raw.to_excel(writer, index=False, sheet_name="Symptoms")
    bio.seek(0)
    st.download_button(
        "⬇️ Download current 'Symptoms' sheet",
        data=bio.getvalue(),
        file_name="Symptoms_Snapshot.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# Footer
st.divider()
st.caption("Use ‘Run N’ for a quick audit, or the one‑click button to fill everything missing both sides. Export writes EXACTLY to K–T (dets) and U–AD (dels), max 10 each.")



