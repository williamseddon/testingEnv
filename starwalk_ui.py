# starwalk_ui.py – Dynamic Symptoms + Auto-Symptomize + Excel Export
# Streamlit 1.38+

import streamlit as st
import pandas as pd
import numpy as np
from openai import OpenAI
from datetime import datetime
import io, os, json, re
from typing import List, Dict, Tuple
from openpyxl import load_workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.worksheet import Worksheet

st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ------------------------------
# Utility functions
# ------------------------------

def clean_text(x):
    if pd.isna(x): return ""
    return str(x).strip()

def _read_excel_sheet(file_like, sheet_name: str):
    try:
        if hasattr(file_like, "seek"):
            file_like.seek(0)
        return pd.read_excel(file_like, sheet_name=sheet_name)
    except Exception:
        return None

def load_symptom_whitelists_from_sheet(file_like) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """Auto-detect symptom schema and load whitelists."""
    df_sym = _read_excel_sheet(file_like, "Symptoms")
    if df_sym is None or df_sym.empty:
        return [], [], {}

    df_sym.columns = [str(c).strip() for c in df_sym.columns]
    lowcols = {c.lower(): c for c in df_sym.columns}
    alias_col = next((lowcols.get(c) for c in ["aliases","alias"] if c in lowcols), None)
    label_col = next((lowcols.get(c) for c in ["symptom","label","name","item"] if c in lowcols), None)
    type_col  = next((lowcols.get(c) for c in ["type","polarity","category"] if c in lowcols), None)

    delighters, detractors = [], []
    alias_map: Dict[str, List[str]] = {}
    POS_TAGS = {"delighter","delighters","positive","pos","pros"}
    NEG_TAGS = {"detractor","detractors","negative","neg","cons"}

    def _clean(s):
        vals = s.dropna().astype(str).map(str.strip)
        seen = set(); out = []
        for v in vals:
            if v and v not in seen:
                seen.add(v); out.append(v)
        return out

    if label_col and type_col:
        df_sym[type_col] = df_sym[type_col].astype(str).str.lower().str.strip()
        delighters = _clean(df_sym.loc[df_sym[type_col].isin(POS_TAGS), label_col])
        detractors = _clean(df_sym.loc[df_sym[type_col].isin(NEG_TAGS), label_col])
        if alias_col:
            for _, r in df_sym.iterrows():
                lbl = str(r.get(label_col, "")).strip()
                als = str(r.get(alias_col, "")).strip()
                if lbl and als:
                    alias_map[lbl] = re.split(r"[|,]", als)
    else:
        for lc, orig in lowcols.items():
            if "delight" in lc or "positive" in lc or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if "detract" in lc or "negative" in lc or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))

    delighters = list(dict.fromkeys(delighters))
    detractors = list(dict.fromkeys(detractors))
    return delighters, detractors, alias_map


def _openai_labeler(verbatim: str, client, model: str, delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    if not verbatim.strip(): return [], []
    sys = (
        "Classify the review below into delighters and detractors for Shark Glossi. "
        "Use ONLY the provided whitelists. Do NOT invent new labels. "
        f"DELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}\n"
        f"DETRACTORS = {json.dumps(detractors, ensure_ascii=False)}\n"
        f"ALIASES = {json.dumps(alias_map, ensure_ascii=False)}\n"
        "Return JSON with keys 'delighters' and 'detractors'."
    )
    user = f"Review:\n\"{verbatim.strip()}\""
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            response_format={"type":"json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        dels = [x for x in data.get("delighters", []) if x in delighters]
        dets = [x for x in data.get("detractors", []) if x in detractors]
        return dels[:6], dets[:6]
    except Exception:
        return [], []


def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="Updated_Reviews.xlsx"):
    """Writes updated data back to Excel, preserving formatting."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Remove AI columns if exist (avoid duplicates)
    for col in list(ws.iter_cols(min_row=1, max_row=1)):
        for cell in col:
            if cell.value and str(cell.value).startswith("AI Symptom"):
                ws.delete_cols(cell.column)

    # Append AI columns
    ai_cols = [c for c in updated_df.columns if c.startswith("AI Symptom")]
    existing_headers = [c.value for c in ws[1]]

    for col_idx, col_name in enumerate(ai_cols, start=len(existing_headers)+1):
        ws.cell(row=1, column=col_idx, value=col_name)
        for i, val in enumerate(updated_df[col_name].values, start=2):
            ws.cell(row=i, column=col_idx, value=val)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (preserves formatting)",
        out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ------------------------------
# Streamlit UI
# ------------------------------

st.title("🌟 Star Walk Review Analyzer — Dynamic Symptoms")

uploaded_file = st.file_uploader("Upload Excel (must include 'Star Walk scrubbed verbatims' and 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

# Load reviews sheet
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    df = pd.read_excel(uploaded_file)

if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column in the reviews sheet.")
    st.stop()

df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms dynamically
DELIGHTERS_WHITELIST, DETRACTORS_WHITELIST, SYMPTOM_ALIASES = load_symptom_whitelists_from_sheet(uploaded_file)

if not DELIGHTERS_WHITELIST and not DETRACTORS_WHITELIST:
    st.warning("⚠️ No valid Symptoms loaded from the 'Symptoms' sheet.")
else:
    st.success(f"Loaded {len(DELIGHTERS_WHITELIST)} Delighters and {len(DETRACTORS_WHITELIST)} Detractors from Symptoms tab.")

st.divider()

# LLM setup
api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not api_key:
    st.warning("Set your OPENAI_API_KEY to use this feature.")
    st.stop()
client = OpenAI(api_key=api_key)
model = "gpt-4o-mini"

# ------------------------------
# Auto-Symptomize section
# ------------------------------
st.subheader("🤖 Auto-Symptomize Missing Reviews")

# Detect missing
mask_missing = ~df.filter(like="Symptom").apply(lambda row: row.astype(str).str.strip().any(), axis=1)
missing_df = df[mask_missing]
st.caption(f"Reviews missing any symptoms: {len(missing_df)}")

limit = st.slider("Max reviews per run", 5, 200, 20)
dry_run = st.checkbox("Preview only (don’t write)", value=True)
go = st.button("🚀 Run Auto-Symptomize")

if go:
    processed = []
    prog = st.progress(0.0)
    for i, (idx, row) in enumerate(missing_df.head(limit).iterrows()):
        vb = row["Verbatim"]
        dels, dets = _openai_labeler(vb, client, model, DELIGHTERS_WHITELIST, DETRACTORS_WHITELIST, SYMPTOM_ALIASES)
        if not dry_run:
            for j, d in enumerate(detrs):
                df.loc[idx, f"AI Symptom Detractor {j+1}"] = d
            for j, d in enumerate(dels):
                df.loc[idx, f"AI Symptom Delighter {j+1}"] = d
        processed.append({
            "Index": idx,
            "Delighters": ", ".join(dels) or "-",
            "Detractors": ", ".join(dets) or "-",
            "Preview": "Yes" if dry_run else "Written"
        })
        prog.progress((i+1)/limit)
    st.success(f"Processed {len(processed)} reviews.")
    st.dataframe(pd.DataFrame(processed), use_container_width=True)

    if not dry_run:
        write_updated_excel(uploaded_file, df, output_name="AI_Symptomized_Reviews.xlsx")
