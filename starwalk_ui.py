# starwalk_ui_v4.py
# Streamlit App — Dynamic Symptoms + Model Selector + Smart Auto‑Symptomization + Approval Queue + Color‑Coded Excel Export
# Requirements: streamlit>=1.28, pandas, openpyxl, openai

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json
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

NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", ""}

def is_filled(val: str) -> bool:
    s = str(val).strip()
    return s.upper() not in NON_VALUES

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
    """Detect manual and AI symptom columns by naming patterns."""
    cols = list(df.columns)
    # Manual: Symptom 1..10 (detractors), 11..20 (delighters)
    man_det = [c for c in cols if re.match(r"(?i)^symptom\s*(?:[1-9]|10)$", str(c))]
    man_del = [c for c in cols if re.match(r"(?i)^symptom\s*(1[1-9]|20)$", str(c))]
    # If names are non-standard but contain numbers, fallback by index
    if not man_det or not man_del:
        num_map = []
        for c in cols:
            m = re.findall(r"\d+", str(c))
            if m and str(c).lower().startswith("symptom"):
                num_map.append((int(m[0]), c))
        nums_sorted = sorted(num_map)
        man_det = [c for n, c in nums_sorted if 1 <= n <= 10] or man_det
        man_del = [c for n, c in nums_sorted if 11 <= n <= 20] or man_del

    # AI columns
    ai_det = [c for c in cols if re.match(r"(?i)^ai\s*symptom.*detractor", str(c))]
    ai_del = [c for c in cols if re.match(r"(?i)^ai\s*symptom.*delighter", str(c))]

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
    return out


def _openai_labeler(verbatim: str, client, model: str, temperature: float,
                    delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]
                   ) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Classify a review strictly using whitelist labels.
    Returns (dels, dets, unlisted_dels, unlisted_dets).
    """
    if not verbatim or not verbatim.strip():
        return [], [], [], []

    sys = (
        "Classify this Shark Glossi review into delighters and detractors. "
        "Use ONLY the provided whitelists; do NOT invent labels. "
        "If a synonym is close but not listed, output it under the correct 'unlisted' bucket.\n" \
        f"DELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}\n" \
        f"DETRACTORS = {json.dumps(detractors, ensure_ascii=False)}\n" \
        f"ALIASES = {json.dumps(alias_map, ensure_ascii=False)}\n" \
        "Return strict JSON: {\"delighters\":[],\"detractors\":[],\"unlisted_delighters\":[],\"unlisted_detractors\":[]}"
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
        dels = [x for x in data.get("delighters", []) if x in delighters][:6]
        dets = [x for x in data.get("detractors", []) if x in detractors][:6]
        unl_dels = [x for x in data.get("unlisted_delighters", [])][:6]
        unl_dets = [x for x in data.get("unlisted_detractors", [])][:6]
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
            cell = ws.cell(row=i, column=col_idx, value=None if (pd.isna(val) or val == "") else val)
            if is_filled(val):
                cell.fill = fill

    out = io.BytesIO(); wb.save(out); out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (Color‑Coded)", out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

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

df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms from sheet (cached)
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

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

# ------------------- Detection & Preview -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

# Summary chips
total = len(work)
need_del = int(work["Needs_Delighters"].sum())
need_det = int(work["Needs_Detractors"].sum())
need_both = int(((work["Needs_Delighters"]) & (work["Needs_Detractors"])) .sum())

st.markdown(
    f"""
**Dataset:** {total:,} reviews • **Need Delighters:** {need_del:,} • **Need Detractors:** {need_det:,} • **Missing Both:** {need_both:,}
"""
)

# Scope filter
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

st.write(f"🔎 **{len(target):,} reviews** match the selected scope.")
with st.expander("Preview rows that need symptomization", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# ------------------- Auto‑Symptomize Controls -------------------
st.divider()
st.subheader("🧠 Auto‑Symptomize Reviews")

limit = st.slider("Max reviews this run", 5, 500, min(50, max(5, len(target))))
dry_run = st.checkbox("Preview only (don’t write AI columns)", value=True)
clear_ai_for_processed = st.checkbox("Clear existing AI Symptom columns for processed rows (fresh fill)", value=False)
run_it = st.button("🚀 Run Auto‑Symptomize", type="primary", disabled=(client is None or len(target) == 0))

if run_it:
    # Ensure AI columns exist (we'll create on write)
    # Determine starting indices for AI columns
    # We'll support up to 6 per side
    max_per_side = 6

    # Prepare results
    rows = []
    failed_calls = 0
    filled_deli = 0
    filled_detr = 0

    processed = 0
    total_to_process = min(limit, len(target))

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
                dels, dets, unl_dels, unl_dets = _openai_labeler(
                    vb, client, selected_model, temperature, DELIGHTERS, DETRACTORS, ALIASES
                ) if client else ([], [], [], [])
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
            status.write(f"Row {idx} — needs: [Delighters={needs_deli} Detractors={needs_detr}] → "
                         f"AI[Dels={len(dels)} Dets={len(dets)}] • Unlisted[{len(unl_dels)}/{len(unl_dets)}]")
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
    # Aggregate unlisted suggestions across processed rows
    unlisted_del_all = []
    unlisted_det_all = []
    for r in rows:
        if r["Unlisted Delighters"] != "-":
            unlisted_del_all.extend([s.strip() for s in r["Unlisted Delighters"].split(",") if s.strip()])
        if r["Unlisted Detractors"] != "-":
            unlisted_det_all.extend([s.strip() for s in r["Unlisted Detractors"].split(",") if s.strip()])

    if unlisted_del_all or unlisted_det_all:
        st.subheader("🟡 Pending New Symptoms (Approval)")
        def count_unique(items: List[str]) -> pd.DataFrame:
            if not items:
                return pd.DataFrame({"Symptom": [], "Count": []})
            vc = pd.Series(items).value_counts().reset_index()
            vc.columns = ["Symptom", "Count"]
            return vc

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Unlisted Delighters**")
            tbl_del = count_unique(unlisted_del_all)
            st.dataframe(tbl_del, use_container_width=True, hide_index=True)
            add_del = st.multiselect("Approve delighters to add", tbl_del["Symptom"].tolist(), key="approve_dels")
        with c2:
            st.markdown("**Unlisted Detractors**")
            tbl_det = count_unique(unlisted_det_all)
            st.dataframe(tbl_det, use_container_width=True, hide_index=True)
            add_det = st.multiselect("Approve detractors to add", tbl_det["Symptom"].tolist(), key="approve_dets")

        if st.button("✅ Add Approved to Symptoms sheet"):
            # Write a new workbook with Symptoms tab updated
            uploaded_file.seek(0)
            wb = load_workbook(uploaded_file)
            if "Symptoms" not in wb.sheetnames:
                st.error("No 'Symptoms' sheet found; cannot add approvals.")
            else:
                ws = wb["Symptoms"]
                # Build existing set to avoid duplicates (check column A for label)
                existing = set()
                for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1):
                    cell = row[0]
                    if cell.value:
                        existing.add(str(cell.value).strip())
                last = ws.max_row + 1
                added = 0
                def _add_items(items: List[str], type_label: str):
                    nonlocal last, added
                    for s in items:
                        if not s: continue
                        if s in existing: continue
                        ws.cell(row=last, column=1, value=s)
                        ws.cell(row=last, column=2, value=type_label)
                        existing.add(s)
                        last += 1
                        added += 1
                _add_items(add_del, "Delighter")
                _add_items(add_det, "Detractor")

                out = io.BytesIO(); wb.save(out); out.seek(0)
                st.download_button(
                    "⬇️ Download Workbook (Symptoms Updated)", out,
                    file_name="Symptoms_Updated.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.success(f"Added {added} new symptom(s) to Symptoms sheet.")

# Footer
st.divider()
st.caption("Tip: Use ‘Preview only’ first to audit the AI tags, then uncheck to write and export.")




