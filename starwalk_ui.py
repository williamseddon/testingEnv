# starwalk_ui_v2.py
# Streamlit App — Dynamic Symptoms + Model Selector + AI Auto-Symptomization + Approval Queue
# Requires: streamlit, pandas, openpyxl, openai

import streamlit as st
import pandas as pd
import numpy as np
from openai import OpenAI
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from datetime import datetime
import io, os, json, re
from typing import List, Dict, Tuple

st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v2")

# -------------------------------------------------
# Utility functions
# -------------------------------------------------
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
    """Auto-detect symptom schema and load delighters/detractors/aliases dynamically."""
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
                    alias_map[lbl] = [p.strip() for p in re.split(r"[|,]", als) if p.strip()]
    else:
        for lc, orig in lowcols.items():
            if "delight" in lc or "positive" in lc or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if "detract" in lc or "negative" in lc or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))

    delighters = list(dict.fromkeys(delighters))
    detractors = list(dict.fromkeys(detractors))
    return delighters, detractors, alias_map

def _openai_labeler(verbatim: str, client, model: str, temperature: float,
                    delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    """Ask model to classify review strictly using whitelists."""
    if not verbatim.strip(): return [], [], []
    sys = (
        "Classify the following Shark Glossi review into delighters and detractors. "
        "Use ONLY the provided whitelists; do NOT invent new labels. "
        "If something does not match, include it under 'unlisted'. "
        f"\nDELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}"
        f"\nDETRACTORS = {json.dumps(detractors, ensure_ascii=False)}"
        f"\nALIASES = {json.dumps(alias_map, ensure_ascii=False)}"
        "\nReturn JSON: {\"delighters\":[],\"detractors\":[],\"unlisted\":[]}"
    )
    user = f"Review:\n\"{verbatim.strip()}\""
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            response_format={"type":"json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        dels = [x for x in data.get("delighters", []) if x in delighters]
        dets = [x for x in data.get("detractors", []) if x in detractors]
        unlisted = data.get("unlisted", [])
        return dels[:6], dets[:6], unlisted[:6]
    except Exception:
        return [], [], []

def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="Updated_Reviews.xlsx"):
    """Write AI columns back into Excel preserving formatting."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Remove existing AI Symptom columns if present
    headers = [c.value for c in ws[1]]
    for idx, h in enumerate(headers, start=1):
        if h and str(h).startswith("AI Symptom"):
            ws.delete_cols(idx)

    ai_cols = [c for c in updated_df.columns if c.startswith("AI Symptom")]
    existing_headers = [c.value for c in ws[1]]
    base_col = len(existing_headers) + 1
    for j, col in enumerate(ai_cols):
        ws.cell(row=1, column=base_col + j, value=col)
        for i, val in enumerate(updated_df[col].values, start=2):
            ws.cell(row=i, column=base_col + j, value=val)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (preserves formatting)",
        out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# -------------------------------------------------
# Streamlit App Layout
# -------------------------------------------------
st.title("🌟 Star Walk Review Analyzer v2")
st.caption("Dynamic Symptoms • Model Selector • Auto-Symptomize • Approval Queue")

uploaded_file = st.file_uploader("📂 Upload Excel file (with 'Star Walk scrubbed verbatims' and 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

# Load data
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    df = pd.read_excel(uploaded_file)
if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column in the review sheet.")
    st.stop()
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms tab
DELIGHTERS, DETRACTORS, ALIASES = load_symptom_whitelists_from_sheet(uploaded_file)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors.")

# Sidebar Model selector
st.sidebar.header("🤖 LLM Settings")
model_choices = {
    "Fast / Economical – GPT-4o-mini": "gpt-4o-mini",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-4.1": "gpt-4.1",
    "Most Advanced – GPT-5": "gpt-5"
}
model_label = st.sidebar.selectbox("Model", list(model_choices.keys()))
selected_model = model_choices[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

# API key
api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not api_key:
    st.warning("Set OPENAI_API_KEY in environment or Streamlit secrets.")
    st.stop()
client = OpenAI(api_key=api_key)

st.divider()
st.subheader("🧠 Auto-Symptomize Missing Reviews")

# Detect missing
mask_missing = ~df.filter(like="Symptom").apply(lambda r: r.astype(str).str.strip().any(), axis=1)
missing_df = df[mask_missing]
st.caption(f"Reviews missing any manual symptoms: {len(missing_df)}")

limit = st.slider("Max reviews per run", 5, 200, 20)
dry_run = st.checkbox("Preview only (don’t write changes)", value=True)
go = st.button("🚀 Run Auto-Symptomize")

if go:
    pending_approvals = []
    prog = st.progress(0.0)
    for i, (idx, row) in enumerate(missing_df.head(limit).iterrows()):
        vb = row["Verbatim"]
        dels, dets, unlisted = _openai_labeler(vb, client, selected_model, temperature,
                                               DELIGHTERS, DETRACTORS, ALIASES)
        if not dry_run:
            for j, d in enumerate(dets):
                df.loc[idx, f"AI Symptom Detractor {j+1}"] = d
            for j, d in enumerate(dels):
                df.loc[idx, f"AI Symptom Delighter {j+1}"] = d
        if unlisted:
            pending_approvals.append({
                "Index": idx,
                "Review (truncated)": vb[:120] + ("…" if len(vb) > 120 else ""),
                "Unlisted": ", ".join(unlisted)
            })
        prog.progress((i+1)/limit)
    st.success(f"Processed {min(limit, len(missing_df))} reviews.")
    if not dry_run:
        write_updated_excel(uploaded_file, df, output_name="AI_Symptomized_Reviews.xlsx")

    if pending_approvals:
        st.subheader("🟡 Pending New Symptoms for Approval")
        df_pending = pd.DataFrame(pending_approvals)
        st.dataframe(df_pending, use_container_width=True)
        approve = st.checkbox("I want to approve and add new symptoms to the Symptoms tab")
        if approve:
            st.info("Select which rows to add as new Symptoms.")
            rows_to_add = st.multiselect("Select rows to approve", df_pending.index)
            if st.button("✅ Add Approved to Symptoms Sheet"):
                wb = load_workbook(uploaded_file)
                ws = wb["Symptoms"]
                last_row = ws.max_row + 1
                for i in rows_to_add:
                    unlisted_items = [x.strip() for x in df_pending.loc[i, "Unlisted"].split(",") if x.strip()]
                    for u in unlisted_items:
                        ws.cell(row=last_row, column=1, value=u)
                        ws.cell(row=last_row, column=2, value="Needs Review")
                        last_row += 1
                out = io.BytesIO()
                wb.save(out)
                out.seek(0)
                st.download_button(
                    "⬇️ Download Updated Workbook (Symptoms Sheet Modified)",
                    out,
                    file_name="Symptoms_Updated.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.success("Approved items added to Symptoms sheet!")

st.divider()
st.caption("✅ End of analysis. Upload a new file or rerun with different filters.")

