# starwalk_ui_v3.py
# Streamlit App — Dynamic Symptoms + Model Selector + Smart Auto-Symptomization + Approval Queue
# Requirements: streamlit, pandas, openai, openpyxl

import streamlit as st
import pandas as pd
import numpy as np
from openai import OpenAI
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
import io, os, json, re
from typing import List, Dict, Tuple

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v3")
st.title("🌟 Star Walk Review Analyzer v3")
st.caption("Dynamic Symptoms • Model Selector • Smart Auto-Symptomize • Approval Queue")

# ------------------- Utility Functions -------------------
def clean_text(x):
    if pd.isna(x): return ""
    return str(x).strip()

def _read_excel_sheet(file_like, sheet_name: str):
    try:
        if hasattr(file_like, "seek"): file_like.seek(0)
        return pd.read_excel(file_like, sheet_name=sheet_name)
    except Exception:
        return None

def load_symptom_whitelists_from_sheet(file_like) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """Load delighters/detractors/aliases dynamically from 'Symptoms' sheet."""
    df_sym = _read_excel_sheet(file_like, "Symptoms")
    if df_sym is None or df_sym.empty:
        return [], [], {}
    df_sym.columns = [str(c).strip() for c in df_sym.columns]
    lowcols = {c.lower(): c for c in df_sym.columns}
    alias_col = next((lowcols.get(c) for c in ["aliases","alias"] if c in lowcols), None)
    label_col = next((lowcols.get(c) for c in ["symptom","label","name","item"] if c in lowcols), None)
    type_col  = next((lowcols.get(c) for c in ["type","polarity","category"] if c in lowcols), None)

    POS_TAGS = {"delighter","delighters","positive","pos","pros"}
    NEG_TAGS = {"detractor","detractors","negative","neg","cons"}

    def _clean(s):
        vals = s.dropna().astype(str).map(str.strip)
        seen, out = set(), []
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
            if "delight" in lc or "positive" in lc or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if "detract" in lc or "negative" in lc or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))

    return list(dict.fromkeys(delighters)), list(dict.fromkeys(detractors)), alias_map

def _openai_labeler(verbatim: str, client, model: str, temperature: float,
                    delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    """Ask model to classify a review strictly using whitelists."""
    if not verbatim.strip(): return [], [], []
    sys = (
        "Classify this Shark Glossi review into delighters and detractors. "
        "Use ONLY the provided whitelists; do NOT invent labels. "
        "If something similar but not listed, include under 'unlisted'.\n"
        f"DELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}\n"
        f"DETRACTORS = {json.dumps(detractors, ensure_ascii=False)}\n"
        f"ALIASES = {json.dumps(alias_map, ensure_ascii=False)}\n"
        "Return JSON: {\"delighters\":[],\"detractors\":[],\"unlisted\":[]}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role":"system","content":sys},{"role":"user","content":verbatim}],
            response_format={"type":"json_object"}
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        dels = [x for x in data.get("delighters", []) if x in delighters]
        dets = [x for x in data.get("detractors", []) if x in detractors]
        unlisted = data.get("unlisted", [])
        return dels[:6], dets[:6], unlisted[:6]
    except Exception:
        return [], [], []

def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="Updated_Reviews.xlsx"):
    """Write AI columns to Excel preserving formatting."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    # Remove existing AI columns to avoid duplicates
    headers = [c.value for c in ws[1] if c.value]
    for idx, h in enumerate(headers, start=1):
        if str(h).startswith("AI Symptom"):
            ws.delete_cols(idx)

    ai_cols = [c for c in updated_df.columns if c.startswith("AI Symptom")]
    base_col = len(headers) + 1
    for j, col in enumerate(ai_cols):
        ws.cell(row=1, column=base_col + j, value=col)
        for i, val in enumerate(updated_df[col].values, start=2):
            ws.cell(row=i, column=base_col + j, value=val)

    out = io.BytesIO()
    wb.save(out); out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (Preserves Formatting)",
        out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ------------------- Load Excel -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    df = pd.read_excel(uploaded_file)
if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column.")
    st.stop()
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms
DELIGHTERS, DETRACTORS, ALIASES = load_symptom_whitelists_from_sheet(uploaded_file)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors.")

# ------------------- Model Selector -------------------
st.sidebar.header("🤖 LLM Settings")
model_choices = {
    "Fast – GPT-4o-mini": "gpt-4o-mini",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-4.1": "gpt-4.1",
    "Most Advanced – GPT-5": "gpt-5"
}
model_label = st.sidebar.selectbox("Model", list(model_choices.keys()))
selected_model = model_choices[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not api_key:
    st.warning("Please set your OPENAI_API_KEY.")
    st.stop()
client = OpenAI(api_key=api_key)

# ------------------- Detection Logic -------------------
st.divider()
st.subheader("🧩 Smart Detection of Missing Symptoms")

# Identify manual delighter/detractor columns
detractor_cols = [c for c in df.columns if re.match(r"(?i)^symptom\s*(?:[1-9]|10)$", c)]
delighter_cols = [c for c in df.columns if re.match(r"(?i)^symptom\s*(1[1-9]|20)$", c)]

def has_any(row, cols):
    if not cols: return False
    for c in cols:
        v = str(row.get(c, "")).strip().upper()
        if v and v not in {"<NA>","NA","N/A","NONE","-"}:
            return True
    return False

df["Has_Detractors"] = df.apply(lambda r: has_any(r, detractor_cols), axis=1)
df["Has_Delighters"] = df.apply(lambda r: has_any(r, delighter_cols), axis=1)
df["Needs_Detractors"] = ~df["Has_Detractors"]
df["Needs_Delighters"] = ~df["Has_Delighters"]

missing_df = df[(df["Needs_Detractors"]) | (df["Needs_Delighters"])]

st.caption(f"🔍 {len(missing_df)} reviews need symptomization "
           f"({df['Needs_Delighters'].sum()} missing delighters, {df['Needs_Detractors'].sum()} missing detractors)")

# ------------------- Auto-Symptomize -------------------
st.divider()
st.subheader("🧠 Auto-Symptomize Missing Reviews")

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
        # Write selectively
        if not dry_run:
            if row["Needs_Detractors"] and dets:
                for j, d in enumerate(dets):
                    df.loc[idx, f"AI Symptom Detractor {j+1}"] = d
            if row["Needs_Delighters"] and dels:
                for j, d in enumerate(dels):
                    df.loc[idx, f"AI Symptom Delighter {j+1}"] = d
        if unlisted:
            pending_approvals.append({
                "Index": idx,
                "Review": vb[:120] + ("…" if len(vb) > 120 else ""),
                "Unlisted": ", ".join(unlisted)
            })
        prog.progress((i+1)/limit)

    st.success(f"✅ Processed {min(limit, len(missing_df))} reviews.")
    if not dry_run:
        write_updated_excel(uploaded_file, df, "AI_Symptomized_Reviews.xlsx")

    if pending_approvals:
        st.subheader("🟡 Pending New Symptoms (Unlisted)")
        df_pending = pd.DataFrame(pending_approvals)
        st.dataframe(df_pending, use_container_width=True)
        approve = st.checkbox("Approve and add to Symptoms tab")
        if approve:
            rows_to_add = st.multiselect("Select rows to approve", df_pending.index)
            if st.button("✅ Add Approved Symptoms"):
                wb = load_workbook(uploaded_file)
                ws = wb["Symptoms"]
                last_row = ws.max_row + 1
                for i in rows_to_add:
                    for u in [x.strip() for x in df_pending.loc[i,"Unlisted"].split(",") if x.strip()]:
                        ws.cell(row=last_row, column=1, value=u)
                        ws.cell(row=last_row, column=2, value="Needs Review")
                        last_row += 1
                out = io.BytesIO()
                wb.save(out); out.seek(0)
                st.download_button(
                    "⬇️ Download Workbook (Updated Symptoms)",
                    out,
                    file_name="Symptoms_Updated.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.success("Approved items added to Symptoms sheet!")

# Clean up helper cols
for c in ["Has_Detractors","Has_Delighters","Needs_Detractors","Needs_Delighters"]:
    if c in df.columns: df.drop(columns=c, inplace=True)

st.divider()
st.caption("✅ Analysis complete. You can rerun with different model settings or files.")


