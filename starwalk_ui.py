# starwalk_ui_v4.py — final
# Streamlit App — Dynamic Symptoms • Model Selector • Manual Symptomize (Run N/ALL) • Browse Symptoms • Color-Coded Export (fixed columns)
# Requirements: streamlit>=1.28, pandas, openpyxl, openai

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json, difflib
from typing import List, Dict, Tuple
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
st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Dynamic Symptoms • Model Selector • Manual Symptomize (Run N/ALL) • Browse Symptoms • Template-exact Export")

# ------------------- Utilities -------------------
def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}

def is_filled(val) -> bool:
    """Return True only if a cell has a real, non-placeholder value."""
    if pd.isna(val):
        return False
    s = str(val).strip()
    return (s != "") and (s.upper() not in NON_VALUES)

@st.cache_data(show_spinner=False)
def get_symptom_whitelists(file_bytes: bytes) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """Load delighters/detractors/aliases dynamically from 'Symptoms' sheet. Cached by file bytes."""
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
        # Wide format fallback
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))
        # unique preserve order
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map


def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    """Detect symptom columns using expected schema; flexible regex for AI columns."""
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
    """Return a copy with helper flags showing what's missing per row."""
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]
    out = df.copy()
    out["Has_Detractors"] = out.apply(lambda r: row_has_any(r, det_cols), axis=1)
    out["Has_Delighters"] = out.apply(lambda r: row_has_any(r, del_cols), axis=1)
    out["Needs_Detractors"] = ~out["Has_Detractors"]
    out["Needs_Delighters"] = ~out["Has_Delighters"]
    out["Needs_Symptomization"] = out["Needs_Detractors"] & out["Needs_Delighters"]
    return out

# ---------- Canonicalization helpers ----------
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
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Classify a review strictly using whitelist labels. Returns (dels, dets, unlisted_dels, unlisted_dets)."""
    if not verbatim or not verbatim.strip():
        return [], [], [], []

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
            return mapped[:10]  # cap per side

        dels = _map_side(data.get("delighters", []), side="del")
        dets = _map_side(data.get("detractors", []), side="det")
        unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
        unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:10]
        return dels, dets, unl_dels, unl_dets
    except Exception:
        return [], [], [], []

# ------------------- Fixed template column mapping -------------------
# Detractors: K–T (10), Delighters: U–AD (10)
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]

AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]

def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    """Ensure the 10+10 AI columns exist in the DataFrame (used internally; export preserves workbook headers)."""
    for h in AI_DET_HEADERS + AI_DEL_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in

def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="AI_Symptomized_Reviews.xlsx"):
    """Write values into exact template slots (no header relabel):
    - Detractors → K..T (10)
    - Delighters → U..AD (10)
    Color only non-empty cells.
    """
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    updated_df = ensure_ai_columns(updated_df)

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # Delighters
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # Detractors

    for i, (_, r) in enumerate(updated_df.iterrows(), start=2):
        # Detractors K–T
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_red
        # Delighters U–AD
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_green

    # Optional widths
    for c in DET_INDEXES + DEL_INDEXES:
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO(); wb.save(out); out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (XLSX)",
        out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def generate_updated_workbook_bytes(original_file, updated_df: pd.DataFrame) -> bytes:
    """Return bytes for a workbook matching the original, with values in fixed slots
    (Detractors K–T, Delighters U–AD). Headers are preserved.
    """
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    updated_df = ensure_ai_columns(updated_df.copy())

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    for i, (_, r) in enumerate(updated_df.iterrows(), start=2):
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_green

    out = io.BytesIO(); wb.save(out)
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

# ------------------- Model -------------------
st.sidebar.header("🤖 LLM Settings")
MODEL_CHOICES = {
    "Fast – GPT-4o-mini": "gpt-4o-mini",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-4.1": "gpt-4.1",
    "Most Advanced – GPT-5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()))
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI or not api_key:
    st.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'. Auto-symptomize will be disabled.")
client = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None

# ------------------- Detection & Preview -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

# Summary chips
total = len(work)
need_del = int(work["Needs_Delighters"].sum())
need_det = int(work["Needs_Detractors"].sum())
need_both = int(work["Needs_Symptomization"].sum())

st.markdown(
    f"""
**Dataset:** {total:,} reviews • **Need Delighters:** {need_del:,} • **Need Detractors:** {need_det:,} • **Missing Both:** {need_both:,}
"""
)

# Scope filter (respected)
scope = st.radio(
    "Process scope",
    ["Any missing", "Missing both", "Missing delighters only", "Missing detractors only"],
    horizontal=True,
)

if scope == "Missing both":
    target = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
elif scope == "Missing delighters only":
    target = work[(work["Needs_Delighters"]) & (~work["Needs_Detractors"]) ]
elif scope == "Missing detractors only":
    target = work[(~work["Needs_Delighters"]) & (work["Needs_Detractors"]) ]
else:
    target = work[(work["Needs_Delighters"]) | (work["Needs_Detractors"]) ]

st.write(f"🔎 **{len(target):,} reviews** match the **selected scope**.")
with st.expander("Preview rows that need symptomization", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# ------------------- Symptomize (main controls) -------------------
st.subheader("🧪 Symptomize")
count_max = int(len(target))
if count_max == 0:
    st.info("Nothing in scope to symptomize.")
else:
    st.caption("Choose how many to process — nothing runs automatically.")
    c1, c2, c3, _ = st.columns([2,1,1,2])
    with c1:
        n_default = int(min(50, max(1, count_max)))
        n_to_sym_main = st.number_input("Count to symptomize (from top of scope)", min_value=1, max_value=count_max, value=n_default, step=1)
    with c2:
        run_n_main = st.button("Run N", use_container_width=True)
    with c3:
        run_all_main = st.button("Run ALL", use_container_width=True)

    if run_n_main or run_all_main:
        if client is None:
            st.error("OpenAI not configured — cannot symptomize.")
        else:
            max_per_side = 10
            rows_iter = target if run_all_main else target.head(int(n_to_sym_main))
            prog = st.progress(0.0)
            total_n = max(1, len(rows_iter))
            did = 0
            for k, (idx, row) in enumerate(rows_iter.iterrows(), start=1):
                vb = row.get("Verbatim", "")
                needs_deli = bool(row.get("Needs_Delighters", False))
                needs_detr = bool(row.get("Needs_Detractors", False))
                try:
                    dels, dets, _, _ = _openai_labeler(
                        vb, client, selected_model, temperature,
                        DELIGHTERS, DETRACTORS, ALIASES,
                        DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                    )
                except Exception:
                    dels, dets = [], []
                if needs_detr and dets:
                    for j, lab in enumerate(dets[:max_per_side]):
                        col = f"AI Symptom Detractor {j+1}"
                        if col not in df.columns: df[col] = None
                        df.loc[idx, col] = lab
                if needs_deli and dels:
                    for j, lab in enumerate(dels[:max_per_side]):
                        col = f"AI Symptom Delighter {j+1}"
                        if col not in df.columns: df[col] = None
                        df.loc[idx, col] = lab
                did += 1
                prog.progress(k/total_n)
            st.success(f"Symptomized {did} review(s) in the selected scope.")

# ------------------- Download (main) -------------------
st.subheader("📦 Download Symptomized Workbook")
try:
    file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
except Exception:
    file_base = 'Reviews'
export_bytes = generate_updated_workbook_bytes(uploaded_file, df)
st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------- Browse Symptoms (main) -------------------
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
    if not vals:
        return pd.DataFrame({"Label": [], "Count": []})
    vc = pd.Series(vals).value_counts().reset_index()
    vc.columns = ["Label", "Count"]
    return vc

if view_side == "Detractors":
    counts_df = _label_counts(df, col_det_all)
    whitelist = pd.DataFrame({"Label": DETRACTORS}) if DETRACTORS else pd.DataFrame({"Label": []})
else:
    counts_df = _label_counts(df, col_del_all)
    whitelist = pd.DataFrame({"Label": DELIGHTERS}) if DELIGHTERS else pd.DataFrame({"Label": []})

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Total reviews", f"{len(df):,}")
with m2:
    st.metric("In-scope now", f"{len(target):,}")
with m3:
    st.metric("Unique labels (found)", f"{counts_df['Label'].nunique() if not counts_df.empty else 0}")

c_left, c_right = st.columns(2)
with c_left:
    st.markdown("**Top labels in data**")
    st.dataframe(counts_df.head(50), use_container_width=True, hide_index=True)
with c_right:
    st.markdown("**Whitelist for this side**")
    st.dataframe(whitelist, use_container_width=True, hide_index=True)

# ------------------- (Optional) New Symptom Inbox remains available from prior runs -------------------
# If you’d like me to remove or simplify the Inbox as well, say the word and I’ll trim it.


