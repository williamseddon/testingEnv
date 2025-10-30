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

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v4")
st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Dynamic Symptoms • Model Selector • Smart Auto‑Symptomize • Approval Queue • Color‑Coded Excel Export")

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
            return mapped[:6]

        dels = _map_side(data.get("delighters", []), side="del")
        dets = _map_side(data.get("detractors", []), side="det")
        unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:6]
        unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:6]
        return dels, dets, unl_dels, unl_dets
    except Exception:
        return [], [], [], []


def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="AI_Symptomized_Reviews.xlsx"):
    """Write AI columns into Excel while preserving formatting and color-coding."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    # Reviews sheet name
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Collect header labels and safely delete any previous AI columns
    headers = [cell.value for cell in ws[1]]
    del_idxs = [i+1 for i, h in enumerate(headers) if h and str(h).startswith("AI Symptom")]
    for col_idx in sorted(del_idxs, reverse=True):
        ws.delete_cols(col_idx)

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
    """Return bytes for a workbook matching the original, with AI columns appended & color-coded."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Remove any existing AI columns in sheet
    headers = [cell.value for cell in ws[1]]
    del_idxs = [i+1 for i, h in enumerate(headers) if h and str(h).startswith("AI Symptom")]
    for col_idx in sorted(del_idxs, reverse=True):
        ws.delete_cols(col_idx)

    # Find AI columns present in DF; if none, return original bytes
    ai_cols = [c for c in updated_df.columns if c.startswith("AI Symptom ")]
    if not ai_cols:
        out0 = io.BytesIO(); wb.save(out0); return out0.getvalue()

    base_col = ws.max_column + 1
    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    for j, col in enumerate(ai_cols):
        col_idx = base_col + j
        ws.cell(row=1, column=col_idx, value=col)
        is_delighter = "Delighter" in col
        fill = fill_green if is_delighter else fill_red
        try:
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = 28
        except Exception:
            pass
        for i, val in enumerate(updated_df[col].values, start=2):
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill

    out = io.BytesIO(); wb.save(out); return out.getvalue()

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

# ------------------- Quick Symptoms Download -------------------
sym_df = read_symptoms_sheet(uploaded_bytes)
st.sidebar.header("📥 Download Symptoms")
if sym_df is None or sym_df.empty:
    st.sidebar.caption("No 'Symptoms' sheet found in the uploaded workbook.")
else:
    # Raw XLSX
    bio_xlsx = io.BytesIO()
    with pd.ExcelWriter(bio_xlsx, engine="openpyxl") as writer:
        sym_df.to_excel(writer, index=False, sheet_name="Symptoms")
    bio_xlsx.seek(0)
    st.sidebar.download_button(
        "⬇️ Symptoms tab (XLSX)", data=bio_xlsx.getvalue(), file_name="Symptoms_Tab.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Raw CSV
    csv_bytes = sym_df.to_csv(index=False).encode("utf-8")
    st.sidebar.download_button("⬇️ Symptoms tab (CSV)", data=csv_bytes, file_name="Symptoms_Tab.csv", mime="text/csv")

    # Alias expansion CSV
    alias_df = build_alias_expansion_df(sym_df, DELIGHTERS, DETRACTORS, ALIASES)
    alias_csv = alias_df.to_csv(index=False).encode("utf-8")
    st.sidebar.download_button("⬇️ Alias expansion (CSV)", data=alias_csv, file_name="Symptoms_Aliases_Expanded.csv", mime="text/csv")

    # Whitelist snapshot JSON
    snapshot = {
        "generated_at": datetime.utcnow().isoformat(),
        "delighters": DELIGHTERS,
        "detractors": DETRACTORS,
        "aliases": ALIASES,
        "counts": {
            "delighters": len(DELIGHTERS),
            "detractors": len(DETRACTORS),
            "aliases": sum(len(v) for v in ALIASES.values()) if ALIASES else 0,
        },
    }
    json_bytes = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
    st.sidebar.download_button("⬇️ Whitelist snapshot (JSON)", data=json_bytes, file_name="Whitelist_Snapshot.json", mime="application/json")


# ------------------- Model Selector -------------------
st.sidebar.header("🤖 LLM Settings")
MODEL_CHOICES = {
    "Fast – GPT‑4o‑mini": "gpt-4o-mini",
    "Balanced – GPT‑4o": "gpt-4o",
    "Advanced – GPT‑4.1": "gpt-4.1",
    "Most Advanced – GPT‑5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()))
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI or not api_key:
    st.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'. Auto‑symptomize will be disabled.")
client = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None

# ------------------- Approvals & Roles -------------------
st.sidebar.header("🔒 Approvals")
approver_name = st.sidebar.text_input("Approver name")
pin_required = st.secrets.get("APPROVER_PIN")
pin_input = st.sidebar.text_input("Approval PIN", type="password") if pin_required else ""
pin_ok = (not pin_required) or (pin_input == pin_required)
if pin_required and not pin_ok:
    st.sidebar.info("Enter the Approval PIN to enable applying changes.")

# Optional bulk rules for defaults
st.sidebar.subheader("⚙️ Bulk Rules (defaults)")
rule_alias_on = st.sidebar.checkbox("Default to 'Alias of' when count ≥", value=True)
rule_alias_threshold = st.sidebar.slider("Alias threshold", 1, 50, 5)
rule_new_on = st.sidebar.checkbox("Default to 'Add as new' when no suggestion and count ≥", value=False)
rule_new_threshold = st.sidebar.slider("New‑label threshold", 1, 50, 10)


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

# ------------------- Export (sidebar) -------------------
with st.sidebar:
    st.header("📦 Download Symptomized Workbook")
    run_before_export = st.checkbox("Compute symptomization across entire dataset now", value=True)
    overwrite_ai_export = st.checkbox("Overwrite existing AI columns during export", value=True)

    # Prepare export dataframe (optionally compute fresh symptomization for ALL rows)
    df_export = df.copy()
    if run_before_export and (client is not None):
        max_per_side = 6
        work_all = detect_missing(df_export, colmap)
        prog = st.progress(0.0)
        total_n = max(1, len(work_all))
        for k, (idx, row) in enumerate(work_all.iterrows(), start=1):
            vb = row.get("Verbatim", "")
            needs_deli = bool(row.get("Needs_Delighters", False))
            needs_detr = bool(row.get("Needs_Detractors", False))
            if overwrite_ai_export:
                for c in [c for c in df_export.columns if c.startswith("AI Symptom ")]:
                    df_export.at[idx, c] = None
            try:
                dels, dets, _, _ = _openai_labeler(
                    vb, client, selected_model, temperature,
                    DELIGHTERS, DETRACTORS, ALIASES,
                    DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                ) if client else ([], [], [], [])
            except Exception:
                dels, dets = [], []
            if needs_detr and dets:
                for j, lab in enumerate(dets[:max_per_side]):
                    df_export.at[idx, f"AI Symptom Detractor {j+1}"] = lab
            if needs_deli and dels:
                for j, lab in enumerate(dels[:max_per_side]):
                    df_export.at[idx, f"AI Symptom Delighter {j+1}"] = lab
            prog.progress(k/total_n)
    elif run_before_export and (client is None):
        st.info("OpenAI not configured — exporting current content without recomputing.")

    try:
        file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
    except Exception:
        file_base = 'Reviews'

    export_bytes = generate_updated_workbook_bytes(uploaded_file, df_export)
    st.download_button(
        "⬇️ Download symptomized workbook (XLSX)",
        data=export_bytes,
        file_name=f"{file_base}_Symptomized.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    if build_clicked:
        max_per_side = 6
        pb = st.progress(0.0)
        total_n = max(1, len(target))
        for k, (idx, row) in enumerate(target.iterrows(), start=1):
            vb = row.get("Verbatim", "")
            needs_deli = bool(row.get("Needs_Delighters", False))
            needs_detr = bool(row.get("Needs_Detractors", False))
            try:
                dels, dets, _, _ = (
                    _openai_labeler(
                        vb, client, selected_model, temperature,
                        DELIGHTERS, DETRACTORS, ALIASES,
                        DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                    ) if client else ([], [], [], [])
                )
            except Exception:
                dels, dets = [], []
            if needs_detr and dets:
                for j, lab in enumerate(dets[:max_per_side]):
                    df.loc[idx, f"AI Symptom Detractor {j+1}"] = lab
            if needs_deli and dels:
                for j, lab in enumerate(dels[:max_per_side]):
                    df.loc[idx, f"AI Symptom Delighter {j+1}"] = lab
            pb.progress(k/total_n)

        # Build bytes and expose a single download button
        try:
            file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
        except Exception:
            file_base = 'Reviews'
        st.session_state["export_bytes"] = generate_updated_workbook_bytes(uploaded_file, df)
        st.session_state["export_name"] = f"{file_base}_Symptomized.xlsx"

    if st.session_state.get("export_bytes"):
        st.download_button(
            "⬇️ Download symptomized workbook (XLSX)",
            data=st.session_state["export_bytes"],
            file_name=st.session_state.get("export_name", "AI_Symptomized_Reviews.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

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
    max_per_side = 6

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
                cnt = int(rec["Count"])
                has_suggestion = bool(rec["Suggested Mapping"])
                if rule_alias_on and has_suggestion and cnt >= rule_alias_threshold:
                    return "Alias of"
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
            elif not pin_ok:
                st.error("Approval PIN required or incorrect.")
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
                    approver = approver_name or "(unknown)"

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










