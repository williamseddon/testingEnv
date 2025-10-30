# starwalk_ui_v4.py (fixed)
# Streamlit App — Dynamic Symptoms + Model Selector + Smart Auto‑Symptomization + Approval Queue + Color‑Coded Excel Export
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
st.caption("Dynamic Symptoms • Model Selector • Smart Auto‑Symptomize • Approval Queue • Color‑Coded Excel Export")

# Compatibility shim: some legacy blocks referenced this flag.
# We set a default so NameError cannot occur even if an old branch remains.
build_clicked = False

# ------------------- Utilities -------------------
def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

# Include common placeholder variants
NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}

def is_filled(val) -> bool:
    """Return True only if a cell has a real, non-placeholder value.
    Prevent np.nan/None from being counted as filled.
    """
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


@st.cache_data(show_spinner=False)
def read_symptoms_sheet(file_bytes: bytes) -> pd.DataFrame:
    """Return the raw 'Symptoms' sheet as a DataFrame for easy export; empty DF if missing."""
    bio = io.BytesIO(file_bytes)
    try:
        df_sym = pd.read_excel(bio, sheet_name="Symptoms")
        if df_sym is None:
            return pd.DataFrame()
        df_sym.columns = [str(c).strip() for c in df_sym.columns]
        return df_sym
    except Exception:
        return pd.DataFrame()


def build_alias_expansion_df(df_sym: pd.DataFrame, delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]) -> pd.DataFrame:
    """Create a long table with one row per (Label, Side, Alias). Side derived from sheet or whitelist membership."""
    side_by_sheet: Dict[str, str] = {}
    if not df_sym.empty:
        lowcols = {c.lower(): c for c in df_sym.columns}
        label_col = next((lowcols.get(c) for c in ["symptom", "label", "name", "item"] if c in lowcols), None)
        type_col  = next((lowcols.get(c) for c in ["type", "polarity", "category", "side"] if c in lowcols), None)
        if label_col and type_col:
            for _, r in df_sym.iterrows():
                lbl = str(r.get(label_col, "")).strip()
                typ = str(r.get(type_col, "")).strip()
                if lbl:
                    side_by_sheet[lbl] = typ

    rows = []
    for lbl in sorted(set(list(delighters) + list(detractors) + list(alias_map.keys()))):
        side = side_by_sheet.get(lbl, "Delighter" if lbl in delighters else ("Detractor" if lbl in detractors else ""))
        aliases = alias_map.get(lbl, [])
        if aliases:
            for a in aliases:
                rows.append({"Label": lbl, "Side": side, "Alias": a})
        else:
            rows.append({"Label": lbl, "Side": side, "Alias": ""})
    return pd.DataFrame(rows)


def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    """Detect symptom columns using exact Star Walk schema with robust AI column detection.
    Manual detractors: Symptom 1..10
    Manual delighters: Symptom 11..20
    AI columns: AI Symptom Detractor 1..6, AI Symptom Delighter 1..6
    """
    cols = [str(c).strip() for c in df.columns]

    # Manual ranges (keep convention)
    man_det = [f"Symptom {i}" for i in range(1, 11) if f"Symptom {i}" in cols]
    man_del = [f"Symptom {i}" for i in range(11, 21) if f"Symptom {i}" in cols]

    # Flexible regex for AI columns
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
    """Return a copy with helper flags showing what's missing per row.
    Counts both manual and AI columns when determining if a side is already present.
    """
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]

    out = df.copy()
    out["Has_Detractors"] = out.apply(lambda r: row_has_any(r, det_cols), axis=1)
    out["Has_Delighters"] = out.apply(lambda r: row_has_any(r, del_cols), axis=1)
    out["Needs_Detractors"] = ~out["Has_Detractors"]
    out["Needs_Delighters"] = ~out["Has_Delighters"]
    # Final gating logic no longer forced; we respect user scope selection downstream
    out["Needs_Symptomization"] = out["Needs_Detractors"] & out["Needs_Delighters"]
    return out

# ------------------- Fixed template column mapping -------------------
# Detractors must live in K–T (10 cols) and Delighters in U–AD (10 cols)
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]

AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]


def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    """Make sure the 10+10 AI columns exist in the DataFrame (filled with None if missing)."""
    for h in AI_DET_HEADERS + AI_DEL_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in


# ---------- Canonicalization helpers for robust matching ----------
def _canon(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    """Build maps for case/space-insensitive matching and alias resolution."""
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
    """Classify a review strictly using whitelist labels.
    Returns (dels, dets, unlisted_dels, unlisted_dets).
    - Robustly maps case/spacing variants and known aliases back to canonical labels.
    """
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
            return mapped[:10]

        dels = _map_side(data.get("delighters", []), side="del")
        dets = _map_side(data.get("detractors", []), side="det")
        unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
        unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:10]
        return dels, dets, unl_dels, unl_dets
    except Exception:
        return [], [], [], []


def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="AI_Symptomized_Reviews.xlsx"):
    """Write AI columns into the exact template positions:
    - Detractors → K..T (10 cols)
    - Delighters → U..AD (10 cols)
    Preserves workbook formatting and color-codes only non-empty cells.
    """
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Ensure DF has the 20 AI columns
    updated_df = ensure_ai_columns(updated_df)

    # (Preserve template headers; do not overwrite)
fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # Delighters
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # Detractors

    # Write rows
    max_row = max(len(updated_df) + 1, ws.max_row)
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
        "⬇️ Download Updated Excel (Color‑Coded)", out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Append all AI columns from df
    ai_cols = [c for c in updated_df.columns if c.startswith("AI Symptom ")]
    if not ai_cols:
        # Nothing to write; still offer download of unchanged workbook
        out = io.BytesIO(); wb.save(out); out.seek(0)
        st.download_button(
            "⬇️ Download Excel (no AI columns to add)", out,
            file_name=output_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return

    base_col = ws.max_column + 1
    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # Delighters
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # Detractors

    for j, col in enumerate(ai_cols):
        col_idx = base_col + j
        ws.cell(row=1, column=col_idx, value=col)
        is_delighter = "Delighter" in col
        fill = fill_green if is_delighter else fill_red
        # Optional: set width a bit wider
        try:
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = 28
        except Exception:
            pass
        for i, val in enumerate(updated_df[col].values, start=2):
            # Only color truly filled cells
            if pd.isna(val) or str(val).strip() == "":
                cell_value = None
            else:
                cell_value = val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill

    out = io.BytesIO(); wb.save(out); out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (Color‑Coded)", out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def generate_updated_workbook_bytes(original_file, updated_df: pd.DataFrame) -> bytes:
    """Return bytes for a workbook matching the original, with AI columns placed in template slots:
    Detractors K–T, Delighters U–AD (10 each)."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    updated_df = ensure_ai_columns(updated_df.copy())

    # (Preserve template headers; do not overwrite)
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

# ------------------- Helpers: add new symptoms to Symptoms sheet -------------------
def add_new_symptoms_to_workbook(original_file, selections: List[Tuple[str, str]]) -> bytes:
    """Add (label, side) pairs to the 'Symptoms' sheet and return workbook bytes.
    selections: list of (label, side) where side is 'Delighter' or 'Detractor'.
    Creates the sheet/headers if missing. Skips duplicates (by label).
    """
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

    if len(headers) < col_label or not headers[col_label-1]:
        ws.cell(row=1, column=col_label, value="Symptom")
    if len(headers) < col_type or not headers[col_type-1]:
        ws.cell(row=1, column=col_type, value="Type")

    existing = set()
    for r_i in range(2, ws.max_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if v:
            existing.add(str(v).strip())

    added = 0
    for label, side in selections:
        lab = str(label).strip()
        if not lab:
            continue
        if lab in existing:
            continue
        row_new = ws.max_row + 1
        ws.cell(row=row_new, column=col_label, value=lab)
        ws.cell(row=row_new, column=col_type, value=str(side).strip() or "")
        existing.add(lab)
        added += 1

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

# Scope filter (now respected)
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

# ------------------- Symptomize Center (main) -------------------
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

            processed_rows = []  # collect detailed log for UX
            cand_del: Dict[str, List[int]] = {}
            cand_det: Dict[str, List[int]] = {}

            for k, (idx, row) in enumerate(rows_iter.iterrows(), start=1):
                vb = row.get("Verbatim", "")
                needs_deli = bool(row.get("Needs_Delighters", False))
                needs_detr = bool(row.get("Needs_Detractors", False))
                try:
                    dels, dets, unl_dels, unl_dets = _openai_labeler(
                        vb, client, selected_model, temperature,
                        DELIGHTERS, DETRACTORS, ALIASES,
                        DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                    )
                except Exception:
                    dels, dets, unl_dels, unl_dets = [], [], [], []

                wrote_dets = []
                wrote_dels = []
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

                # collect unlisted candidates with references
                for u in unl_dels:
                    cand_del.setdefault(u, []).append(idx)
                for u in unl_dets:
                    cand_det.setdefault(u, []).append(idx)

                processed_rows.append({
                    "Index": int(idx),
                    "Verbatim": str(vb)[:300],
                    "Added_Delighters": wrote_dels,
                    "Added_Detractors": wrote_dets,
                    "Unlisted_Delighters": unl_dels,
                    "Unlisted_Detractors": unl_dets,
                })

                did += 1
                prog.progress(k/total_n)

            # Persist to session for later reference
            st.session_state["processed_rows"] = processed_rows
            st.success(f"Symptomized {did} review(s) in the selected scope.")

            # ---- New Symptom Candidates UX ----
            cand_total = len(cand_del) + len(cand_det)
            if cand_total:
                st.info(f"🔔 Detected {cand_total} new symptom candidate(s). Review below and select any to add to the Symptoms tab.")

                def _mk_table(cmap: Dict[str, List[int]], side_label: str) -> pd.DataFrame:
                    rows_tbl = []
                    for sym, refs in sorted(cmap.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                        # build up to two short examples
                        examples = []
                        for ridx in refs[:2]:
                            try:
                                ex = df.loc[ridx, "Verbatim"]
                                examples.append((str(ex) or "")[:160])
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

                # References per candidate (full list)
                st.markdown("**References per candidate**")
                for sym, refs in sorted(cand_det.items(), key=lambda kv: -len(kv[1])):
                    with st.expander(f"Detractor — {sym} • {len(refs)} reference(s)"):
                        ref_rows = []
                        for ridx in refs:
                            row_dict = {"Index": int(ridx)}
                            if "Star Rating" in df.columns:
                                row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                            row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])[:400]
                            ref_rows.append(row_dict)
                        st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)
                for sym, refs in sorted(cand_del.items(), key=lambda kv: -len(kv[1])):
                    with st.expander(f"Delighter — {sym} • {len(refs)} reference(s)"):
                        ref_rows = []
                        for ridx in refs:
                            row_dict = {"Index": int(ridx)}
                            if "Star Rating" in df.columns:
                                row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                            row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])[:400]
                            ref_rows.append(row_dict)
                        st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)

                # If user clicked add, build selections and offer workbook download
                if 'add_btn' in locals() and add_btn:
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

            # ---- Processed reviews log ----
            if processed_rows:
                st.subheader("🧾 Processed reviews (this run)")
                for rec in processed_rows:
                    head = f"Row {rec['Index']} — Dets added: {len(rec['Added_Detractors'])}, Dels added: {len(rec['Added_Delighters'])}"
                    with st.expander(head):
                        st.markdown(f"**Verbatim**: {rec['Verbatim']}")
                        st.markdown("**Detractors added:** " + (", ".join(rec["Added_Detractors"]) if rec["Added_Detractors"] else "—"))
                        st.markdown("**Delighters added:** " + (", ".join(rec["Added_Delighters"]) if rec["Added_Delighters"] else "—"))
                        st.markdown("**Unlisted detractors (candidates):** " + (", ".join(rec["Unlisted_Detractors"]) if rec["Unlisted_Detractors"] else "—"))
                        st.markdown("**Unlisted delighters (candidates):** " + (", ".join(rec["Unlisted_Delighters"]) if rec["Unlisted_Delighters"] else "—")) in the selected scope.")

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

# ------------------- Symptomize & Export (sidebar; manual) -------------------

# ------------------- Detection diagnostics (advanced) -------------------
with st.expander("Detection diagnostics (advanced)", expanded=False):
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]

    def _filled_counts(df_in: pd.DataFrame, cols: List[str], label: str):
        if not cols:
            st.info(f"No {label} columns detected.")
            return
        sample = df_in[[*cols]].head(100).copy()
        counts = sample.applymap(is_filled).sum(axis=1)
        st.write(f"{label}: first 100 rows — filled cell count per row (higher means already symptomized)")
        st.dataframe(pd.DataFrame({"filled_count": counts}).join(sample), use_container_width=True)

    _filled_counts(work, det_cols, "Detractors")
    _filled_counts(work, del_cols, "Delighters")

st.divider()
st.subheader("🧠 Auto‑Symptomize Reviews")

limit = st.slider("Max reviews this run", 5, 500, min(50, max(5, len(target))))
dry_run = st.checkbox("Preview only (don’t write AI columns)", value=True)
clear_ai_for_processed = st.checkbox("Clear existing AI Symptom columns for processed rows (fresh fill)", value=False)
run_it = st.button("🚀 Run Auto‑Symptomize", type="primary", disabled=(client is None or len(target) == 0))

if run_it:
    # Ensure AI columns exist (we'll create on write)
    max_per_side = 10

    # Prepare results
    rows = []
    failed_calls = 0
    filled_deli = 0
    filled_detr = 0

    processed = 0
    total_to_process = min(limit, len(target))

    progress = st.progress(0.0)

    with st.status("Classifying reviews…", expanded=True) as status:
        for idx, row in target.head(limit).iterrows():
            vb = row.get("Verbatim", "")

            # Determine what this row needs (based on helper flags)
            needs_deli = bool(row.get("Needs_Delighters", False))
            needs_detr = bool(row.get("Needs_Detractors", False))

            # Optional clear AI columns for this row to avoid duplicates on re-run
            if clear_ai_for_processed and not dry_run:
                for c in [c for c in df.columns if c.startswith("AI Symptom ")]:
                    df.loc[idx, c] = None

            # Call model
            try:
                dels, dets, unl_dels, unl_dets = (
                    _openai_labeler(
                        vb, client, selected_model, temperature,
                        DELIGHTERS, DETRACTORS, ALIASES,
                        DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                    ) if client else ([], [], [], [])
                )
            except Exception:
                dels, dets, unl_dels, unl_dets = [], [], [], []
                failed_calls += 1

            wrote_deli = []
            wrote_detr = []

            if not dry_run:
                # Write only what is missing
                if needs_detr and dets:
                    for j, lab in enumerate(dets[:max_per_side]):
                        df.loc[idx, f"AI Symptom Detractor {j+1}"] = lab
                    wrote_detr = dets[:max_per_side]
                    filled_detr += len(wrote_detr)
                if needs_deli and dels:
                    for j, lab in enumerate(dels[:max_per_side]):
                        df.loc[idx, f"AI Symptom Delighter {j+1}"] = lab
                    wrote_deli = dels[:max_per_side]
                    filled_deli += len(wrote_deli)

            rows.append({
                "Index": idx,
                "Needs Delighters": needs_deli,
                "Needs Detractors": needs_detr,
                "AI Delighters": ", ".join(dels) or "-",
                "AI Detractors": ", ".join(dets) or "-",
                "Unlisted Delighters": ", ".join(unl_dels) or "-",
                "Unlisted Detractors": ", ".join(unl_dets) or "-",
                "Action": "Preview" if dry_run else ("Written" if (wrote_deli or wrote_detr) else "Skipped"),
            })

            processed += 1
            progress.progress(processed/total_to_process)
            status.write(
                f"Row {idx} — needs: [Delighters={needs_deli} Detractors={needs_detr}] → "
                f"AI[Dels={len(dels)} Dets={len(dets)}] • Unlisted[{len(unl_dels)}/{len(unl_dets)}]"
            )
            status.update(label=f"Classifying reviews… {processed}/{total_to_process}")

        status.update(state="complete", label="Classification finished")

    st.success(f"Processed {processed} / {total_to_process} rows • Filled Delighters: {filled_deli} • Filled Detractors: {filled_detr}")
    if failed_calls:
        st.warning(f"{failed_calls} review(s) failed AI classification.")

    st.subheader("Results")
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    if not dry_run:
        # Offer Excel download with AI columns appended + color coding
        write_updated_excel(uploaded_file, df, output_name="AI_Symptomized_Reviews.xlsx")

    # ------------------- Approval Queue -------------------
    # Aggregate unlisted suggestions across processed rows + keep examples
    unlisted_del_all = []
    unlisted_det_all = []
    # candidate -> list of row indices (for examples)
    cand_examples_del: Dict[str, List[int]] = {}
    cand_examples_det: Dict[str, List[int]] = {}
    for r in rows:
        idx = r["Index"]
        if r["Unlisted Delighters"] != "-":
            items = [s.strip() for s in r["Unlisted Delighters"].split(",") if s.strip()]
            unlisted_del_all.extend(items)
            for it in items:
                cand_examples_del.setdefault(it, []).append(idx)
        if r["Unlisted Detractors"] != "-":
            items = [s.strip() for s in r["Unlisted Detractors"].split(",") if s.strip()]
            unlisted_det_all.extend(items)
            for it in items:
                cand_examples_det.setdefault(it, []).append(idx)

    if unlisted_del_all or unlisted_det_all:
        st.subheader("🟡 New Symptom Inbox — Review & Approve")

        def count_unique_with_examples(items: List[str], example_map: Dict[str, List[int]], side: str) -> pd.DataFrame:
            if not items:
                return pd.DataFrame({"Symptom": [], "Side": [], "Count": [], "Examples": [], "Suggested Mapping": [], "Impact (now)": []})
            vc = pd.Series(items).value_counts()
            rows_local = []
            # Build suggestions using fuzzy match vs canonical lists
            search_space = (DELIGHTERS if side == "Delighter" else DETRACTORS)
            for sym, cnt in vc.items():
                # collect up to 2 example verbatims
                ex_idxs = (example_map.get(sym, []) or [])[:2]
                examples = []
                months = []
                for exi in ex_idxs:
                    try:
                        examples.append(df.loc[exi, "Verbatim"][:180])
                        if "Review Date" in df.columns:
                            d = pd.to_datetime(df.loc[exi, "Review Date"], errors="coerce")
                            if pd.notna(d):
                                months.append(d.strftime("%Y-%m"))
                    except Exception:
                        pass
                examples_text = " | ".join(["— "+e for e in examples]) if examples else ""
                # fuzzy suggestion
                suggestion = ""
                try:
                    matches = difflib.get_close_matches(sym, search_space, n=1, cutoff=0.82)
                    if matches:
                        suggestion = matches[0]
                except Exception:
                    suggestion = ""
                rows_local.append({
                    "Symptom": sym,
                    "Side": side,
                    "Count": int(cnt),
                    "Examples": examples_text,
                    "Suggested Mapping": suggestion,
                    "Impact (now)": int(cnt),  # at least these many rows become fillable
                })
            df_out = pd.DataFrame(rows_local).sort_values(["Count", "Symptom"], ascending=[False, True]).reset_index(drop=True)
            return df_out

        tbl_del = count_unique_with_examples(unlisted_del_all, cand_examples_del, side="Delighter")
        tbl_det = count_unique_with_examples(unlisted_det_all, cand_examples_det, side="Detractor")

        min_count = st.slider("Only show candidates with count ≥", 1, 20, 1)
        show_df = pd.concat([
            tbl_del[tbl_del["Count"] >= min_count],
            tbl_det[tbl_det["Count"] >= min_count]
        ], ignore_index=True)

        # Optional dataset-wide impact estimate (slower)
        estimate_dataset = st.checkbox("Estimate dataset‑wide impact for shown candidates (slower)", value=False)
        if estimate_dataset and not show_df.empty:
            def _estimate_impact_row(row):
                try:
                    patt = re.escape(str(row["Symptom"]))
                    mask = df["Verbatim"].str.contains(patt, case=False, na=False)
                    return int(mask.sum())
                except Exception:
                    return int(row["Count"])  # fallback
            show_df["Dataset Impact (est.)"] = show_df.apply(_estimate_impact_row, axis=1)

        if show_df.empty:
            st.info("No candidates meet the current threshold.")
        else:
            st.markdown("**Decide for each candidate:** set *Action* to `Add as new` or `Alias of`, and choose a *Target Label* if aliasing.")

            # Build default actions using sidebar bulk rules
            def _default_action(rec):
                # Simple default: alias when a suggestion exists; otherwise add as new
                return "Alias of" if bool(rec["Suggested Mapping"]) else "Add as new"
                if (not has_suggestion) and rule_new_on and cnt >= rule_new_threshold:
                    return "Add as new"
                return "Alias of" if has_suggestion else "Add as new"

            show_df["Action"] = show_df.apply(_default_action, axis=1)
            show_df["Target Label"] = show_df["Suggested Mapping"].fillna("")

            edited = st.data_editor(
                show_df,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Action": st.column_config.SelectboxColumn(options=["Add as new", "Alias of"], required=True),
                    "Target Label": st.column_config.SelectboxColumn(options=[], required=False),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="new_symptom_inbox"
            )

        # Optional trend explorer
        if (unlisted_del_all or unlisted_det_all) and "Review Date" in df.columns:
            st.markdown("### 📈 Candidate Trend Explorer")
            all_syms = sorted(set(unlisted_del_all + unlisted_det_all))
            sym_pick = st.selectbox("Pick a candidate to view monthly trend", all_syms)
            if sym_pick:
                # Use entire dataset for trend (substring contains)
                try:
                    patt = re.escape(sym_pick)
                    df_tr = df.copy()
                    if "Review Date" in df_tr.columns:
                        df_tr["_month"] = pd.to_datetime(df_tr["Review Date"], errors="coerce").dt.to_period("M").astype(str)
                        mask = df_tr["Verbatim"].str.contains(patt, case=False, na=False)
                        trend = df_tr.loc[mask].groupby("_month").size().reindex(sorted(df_tr["_month"].dropna().unique())).fillna(0)
                        st.line_chart(trend)
                except Exception:
                    pass

        st.caption("Tip: *Impact (now)* equals how many processed reviews already contained this candidate.")

        # Safety: confirmation & PIN gate
        confirm_changes = st.checkbox("I confirm the actions above are correct.")

        if st.button("✅ Apply actions & Download updated 'Symptoms' workbook"):
            if not confirm_changes:
                st.error("Please confirm the actions before applying.")
            else:
                uploaded_file.seek(0)
                wb = load_workbook(uploaded_file)
                if "Symptoms" not in wb.sheetnames:
                    st.error("No 'Symptoms' sheet found; cannot apply approvals.")
                else:
                    ws = wb["Symptoms"]
                    # Build header map (case-insensitive)
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

                    if col_alias == -1:
                        # Create Aliases column at end
                        col_alias = len(headers) + 1
                        ws.cell(row=1, column=col_alias, value="Aliases")

                    # Build existing label rows and alias text
                    label_to_row: Dict[str, int] = {}
                    existing_aliases: Dict[str, str] = {}
                    for r_i in range(2, ws.max_row + 1):
                        lbl = ws.cell(row=r_i, column=col_label).value
                        if lbl:
                            label_to_row[str(lbl).strip()] = r_i
                            existing_aliases[str(lbl).strip()] = str(ws.cell(row=r_i, column=col_alias).value or "").strip()

                    # Prepare audit sheet
                    audit_name = "Symptoms_Audit"
                    if audit_name not in wb.sheetnames:
                        ws_a = wb.create_sheet(audit_name)
                        ws_a.append(["Timestamp", "Approver", "Action", "Side", "Label", "Alias", "Target", "Count", "Source"])
                    else:
                        ws_a = wb[audit_name]

                    added_new = 0
                    added_aliases = 0

                    src_tag = "starwalk_ui_v4"
                    now_iso = datetime.utcnow().isoformat()
                    approver = ""  # Approver field optional; UI removed per request

                    for _, rec in edited.iterrows():
                        sym = str(rec["Symptom"]).strip()
                        side = str(rec["Side"]).strip()
                        action = str(rec["Action"]).strip()
                        target = str(rec.get("Target Label", "")).strip()
                        cnt = int(rec.get("Count", 0))
                        if not sym:
                            continue
                        if action == "Add as new":
                            # Add brand new label if it doesn't already exist
                            if sym not in label_to_row:
                                new_row = ws.max_row + 1
                                ws.cell(row=new_row, column=col_label, value=sym)
                                ws.cell(row=new_row, column=col_type, value=side)
                                if col_alias > 0:
                                    ws.cell(row=new_row, column=col_alias, value="")
                                label_to_row[sym] = new_row
                                added_new += 1
                                ws_a.append([now_iso, approver, "Add Label", side, sym, "", "", cnt, src_tag])
                        else:
                            # Alias mapping requires a valid target label
                            if not target:
                                continue
                            # Ensure target exists; if not, create it with the same side
                            if target not in label_to_row:
                                new_row = ws.max_row + 1
                                ws.cell(row=new_row, column=col_label, value=target)
                                ws.cell(row=new_row, column=col_type, value=side)
                                if col_alias > 0:
                                    ws.cell(row=new_row, column=col_alias, value="")
                                label_to_row[target] = new_row
                                ws_a.append([now_iso, approver, "Add Label (for alias)", side, target, "", "", 0, src_tag])
                            row_i = label_to_row[target]
                            current = existing_aliases.get(target, "")
                            alias_list = [a.strip() for a in re.split(r"[|,]", current) if a.strip()]
                            if sym not in alias_list and sym != target:
                                alias_list.append(sym)
                                ws.cell(row=row_i, column=col_alias, value=" | ".join(alias_list))
                                existing_aliases[target] = " | ".join(alias_list)
                                added_aliases += 1
                                ws_a.append([now_iso, approver, "Add Alias", side, target, sym, target, cnt, src_tag])

                    out = io.BytesIO(); wb.save(out); out.seek(0)
                    st.download_button(
                        "⬇️ Download Workbook (Symptoms + Aliases + Audit Updated)", out,
                        file_name="Symptoms_Updated.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.success(f"Added {added_new} new label(s) and {added_aliases} alias(es). Audit trail written to 'Symptoms_Audit'.")

# Footer
st.divider()
st.caption("Tip: Use ‘Preview only’ first to audit the AI tags, then uncheck to write and export.")
st.divider()
st.caption("Tip: Use ‘Preview only’ first to audit the AI tags, then uncheck to write and export.")
st.divider()
st.caption("Tip: Use ‘Preview only’ first to audit the AI tags, then uncheck to write and export.")



