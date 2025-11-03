# Retry creating the Streamlit app, requirements, and README files.
from pathlib import Path

app_code = r'''
import io
import json
from datetime import datetime
from typing import List, Optional, Dict

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Bazaarvoice Merger", layout="wide")

# ---------- Helpers ----------

TRUE_SET  = {"true","t","1","yes","y"}
FALSE_SET = {"false","f","0","no","n"}

def read_any(uploaded_file) -> pd.DataFrame:
    """Read CSV/TSV/XLSX/XLS into a DataFrame with dtype=object to preserve values."""
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, dtype=object)
    elif name.endswith((".csv", ".tsv", ".txt")):
        # autodetect separator
        return pd.read_csv(uploaded_file, sep=None, engine="python", dtype=object)
    else:
        raise ValueError(f"Unsupported file type for {uploaded_file.name}")

def canonicalize(name: str) -> str:
    return str(name).strip().lower()

def is_true_false_col(series: pd.Series) -> bool:
    vals = set()
    for v in series.dropna().unique().tolist():
        if isinstance(v, bool):
            vals.add("true" if v else "false")
        else:
            vals.add(str(v).strip().lower())
    if not vals:
        return False
    allowed = TRUE_SET | FALSE_SET
    return vals.issubset(allowed)

def normalized_bool_yes_no(series: pd.Series) -> pd.Series:
    def conv(v):
        if pd.isna(v):
            return v
        if isinstance(v, bool):
            return "Yes" if v else "No"
        s = str(v).strip().lower()
        if s in TRUE_SET:
            return "Yes"
        if s in FALSE_SET:
            return "No"
        return v
    return series.apply(conv)

def get_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return the actual column name in df that matches any candidate (case-insensitive)."""
    lookup = {canonicalize(c): c for c in df.columns}
    for cand in candidates:
        key = canonicalize(cand)
        if key in lookup:
            return lookup[key]
    return None

def parse_short_date(s) -> Optional[str]:
    """Parse arbitrary date to mm/dd/yyyy string; return None if not parseable."""
    if pd.isna(s):
        return None
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.strftime("%m/%d/%Y")
    except Exception:
        return None

def infer_country(product_id: Optional[str], source_name: str) -> Optional[str]:
    """Country: If Product ID has UK or EU put this, else if source file 'USA' by default -> USA."""
    if product_id:
        up = str(product_id).upper()
        if "UK" in up:
            return "UK"
        if "EU" in up:
            return "EU"
    upname = source_name.upper()
    if "US" in upname or "USA" in upname:
        return "USA"
    if "UK" in upname:
        return "UK"
    if "EU" in upname:
        return "EU"
    return None

# Candidate header names by field
CANDIDATES = {
    "product_id": ["ProductId", "Product ID", "ProductExternalId", "SKU", "ProductSKU", "Product_External_Id"],
    "review_id": ["ReviewId", "Review ID", "Id", "id"],
    "submission_time": ["SubmissionTime", "Submission Time", "SubmittedDate", "Submission Date", "ReviewSubmissionDate", "Date"],
    "review_text": ["ReviewText", "Review Text", "ReviewBody", "Text", "content", "Review"],
    "rating": ["Rating", "StarRating", "Stars", "RatingValue"],
    "incentivized": ["Incentivized", "IsIncentivized", "IncentivizedReview", "Seeded"],
}

# ---------- UI ----------
st.title("Bazaarvoice Review Merger")
st.markdown("Upload **1 or more** Bazaarvoice export files (CSV/TSV/XLSX). We'll merge them, convert booleans to **Yes/No**, and give you two downloads: **Raw Merged** and **Formatted Export**.")

uploaded_files = st.file_uploader("Upload US / UK / EU Bazaarvoice files", type=["csv", "tsv", "txt", "xlsx", "xls"], accept_multiple_files=True)

with st.expander("Options"):
    force_yes_new_review = st.checkbox("Set 'New Review' to Yes for all rows", value=True)
    constant_source = st.text_input("Source value", value="DTC")
    bool_to_yesno = st.checkbox("Convert boolean-like columns to Yes/No", value=True)

if not uploaded_files:
    st.info("⬆️ Add one or more files to begin.")
    st.stop()

frames = []
for f in uploaded_files:
    try:
        df = read_any(f)
        df["_source_file"] = f.name
        frames.append(df)
    except Exception as e:
        st.error(f"Failed to read {f.name}: {e}")

if not frames:
    st.error("No readable files were uploaded.")
    st.stop()

# Create union-of-columns in order of first appearance
union_cols = []
for df in frames:
    for c in map(str, df.columns):
        if c not in union_cols:
            union_cols.append(c)

# Align and concat
aligned = []
for df in frames:
    aligned.append(df.reindex(columns=union_cols))
merged = pd.concat(aligned, ignore_index=True)

# Optionally convert boolean-like columns to Yes/No
if bool_to_yesno:
    for c in merged.columns:
        try:
            if is_true_false_col(merged[c]):
                merged[c] = normalized_bool_yes_no(merged[c])
        except Exception:
            pass

# Build "Raw Data (JSON)" column for convenience
raw_json = []
for _, row in merged.iterrows():
    as_dict = {str(k): (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
    raw_json.append(json.dumps(as_dict, ensure_ascii=False))
merged_with_raw = merged.copy()
merged_with_raw["Raw Data (JSON)"] = raw_json

# ----- Build Formatted Export -----
actual_cols = {k: get_col(merged, v) for k, v in CANDIDATES.items()}
fmt_rows = []
for idx, row in merged.iterrows():
    product_id_val = row[actual_cols["product_id"]] if actual_cols["product_id"] else None
    review_id_val = row[actual_cols["review_id"]] if actual_cols["review_id"] else None
    submission_val = row[actual_cols["submission_time"]] if actual_cols["submission_time"] else None
    review_text_val = row[actual_cols["review_text"]] if actual_cols["review_text"] else None
    rating_val = row[actual_cols["rating"]] if actual_cols["rating"] else None
    incentivized_val = row[actual_cols["incentivized"]] if actual_cols["incentivized"] else None

    # Seeded = Yes/No from incentivized-like source
    if bool_to_yesno:
        seeded = normalized_bool_yes_no(pd.Series([incentivized_val])).iloc[0] if incentivized_val is not None else "No"
    else:
        seeded = "Yes" if str(incentivized_val).strip().lower() in TRUE_SET else "No"

    # Country rule
    country = infer_country(product_id_val, str(row.get("_source_file", "")))

    # New Review default Yes
    new_review = "Yes" if force_yes_new_review else ""

    # Date formatting
    review_date = parse_short_date(submission_val)

    fmt_rows.append({
        "Source": constant_source,                                       # = DTC (default)
        "Model (SKU)": product_id_val,                                   # = Product ID
        "Seeded": seeded,                                                # = Incentivized -> Yes/No
        "Country": country,                                              # rule
        "New Review": new_review,                                        # Yes by default
        "Review Date": review_date,                                      # short date
        "Verbatim Id": review_id_val,                                    # = Review ID
        "Verbatim (Review)": review_text_val,                            # = ReviewText
        "Star Rating": rating_val,                                       # = Rating
    })

formatted = pd.DataFrame(fmt_rows, columns=[
    "Source","Model (SKU)","Seeded","Country","New Review","Review Date","Verbatim Id","Verbatim (Review)","Star Rating"
])

st.subheader("Preview")
t1, t2 = st.tabs(["Raw Merged", "Formatted Export"])
with t1:
    st.dataframe(merged_with_raw.head(100), use_container_width=True)
with t2:
    st.dataframe(formatted.head(100), use_container_width=True)

# ----- Downloads -----
def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

def df_to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])
    bio.seek(0)
    return bio.read()

st.markdown("---")
colA, colB = st.columns(2)

with colA:
    st.markdown("### Download Raw Merged")
    st.download_button(
        label="Download CSV (Raw)",
        data=df_to_csv_bytes(merged_with_raw),
        file_name="bv_merged_raw.csv",
        mime="text/csv"
    )
    st.download_button(
        label="Download Excel (Raw + Formatted)",
        data=df_to_excel_bytes({"Raw": merged_with_raw, "Formatted": formatted}),
        file_name="bv_merged_outputs.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

with colB:
    st.markdown("### Download Formatted Export")
    st.download_button(
        label="Download CSV (Formatted)",
        data=df_to_csv_bytes(formatted),
        file_name="bv_formatted_export.csv",
        mime="text/csv"
    )
'''

reqs = """\
streamlit>=1.38.0
pandas>=2.2.0
openpyxl>=3.1.2
pyarrow>=15.0.0
"""

readme = """\
# Bazaarvoice Merger - Streamlit App

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements_streamlit.txt
streamlit run bv_streamlit_app.py
