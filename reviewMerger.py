import io
import json
from typing import List, Optional, Dict

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Bazaarvoice Merger", layout="wide")

# ---------- Helpers ----------

TRUE_SET  = {"true", "t", "1", "yes", "y"}
FALSE_SET = {"false", "f", "0", "no", "n"}

def canonicalize(name: str) -> str:
    """Lowercase and strip non-alnum for robust header matching."""
    s = str(name).lower().strip()
    return "".join(ch for ch in s if ch.isalnum())

def is_true_false_col(series: pd.Series) -> bool:
    """Heuristically detect columns that contain only boolean-like values."""
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
    """Map True/False-like values to Yes/No; leave other values alone."""
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

def try_read_csv_bytes(raw: bytes) -> Optional[pd.DataFrame]:
    """Try multiple encodings for CSV/TSV; auto-detect delimiter (engine='python')."""
    for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
        try:
            bio = io.BytesIO(raw)
            df = pd.read_csv(bio, sep=None, engine="python", dtype=object, encoding=enc)
            return df
        except Exception:
            continue
    return None

def read_any(uploaded_file) -> pd.DataFrame:
    """
    Read CSV/TSV/XLSX/XLS into a DataFrame with dtype=object to preserve values.
    We read file bytes once to allow multiple parsing attempts.
    """
    name = uploaded_file.name.lower()
    raw = uploaded_file.getvalue()  # bytes

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw), dtype=object)
    elif name.endswith((".csv", ".tsv", ".txt")):
        df = try_read_csv_bytes(raw)
        if df is None:
            raise ValueError(f"Unsupported or unreadable delimited file for {uploaded_file.name}")
        return df
    else:
        raise ValueError(f"Unsupported file type for {uploaded_file.name}")

def get_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return the actual column name in df that matches any candidate (case & punctuation-insensitive)."""
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
    ts = pd.to_datetime(s, errors="coerce", utc=False)
    if pd.isna(ts):
        return None
    return ts.strftime("%m/%d/%Y")

def infer_country(product_id: Optional[str], source_name: str) -> Optional[str]:
    """
    Country rule:
    - If Product ID contains 'UK' or 'EU', use that.
    - Else if the filename suggests 'US'/'USA', default to 'USA'.
    """
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

# Expanded header synonyms to cover BV exports & RR_Export variants
CANDIDATES = {
    "product_id": [
        "Product ID","ProductId","ProductID","ProductExternalId","SKU","ProductSKU","Model (SKU)","Model",
        "PRODUCTID","PRODUCT_ID","Product_External_Id"
    ],
    "review_id": [
        "Review ID","ReviewId","ReviewID","Id","id","VERBATIM ID","Verbatim Id"
    ],
    "submission_time": [
        "Submission date","Submission Date","SubmissionTime","Submission Time","SubmittedDate",
        "Review Submission Date","ReviewSubmissionDate","Date","Created At","CreatedAt","Initial publish date"
    ],
    "review_text": [
        "Review text","Review Text","ReviewText","ReviewBody","Text","content","Review","Verbatim (Review)"
    ],
    "rating": [
        "Rating","Star Rating","StarRating","Stars","RatingValue","Overall Rating","OverallRating"
    ],
    "incentivized": [
        "IncentivizedReview","Incentivized review","Incentivized","IsIncentivized","Seeded",
        "Incentivised review","IncentivisedReview","IsSeeded"
    ],
    # (title not required for your formatted export, but supported for completeness)
    "title": ["Review title","Review Title","Title","Headline"]
}

# ---------- UI ----------

st.title("Bazaarvoice Review Merger")
st.markdown(
    "Upload **1 or more** Bazaarvoice export files (CSV/TSV/XLSX). "
    "We'll merge them, convert boolean-like fields to **Yes/No**, and provide two outputs: "
    "**Raw Merged** (with a **Raw Data (JSON)** column) and your **Formatted Export**."
)

uploaded_files = st.file_uploader(
    "Upload Bazaarvoice files (US / UK / EU, including RR_Export)",
    type=["csv", "tsv", "txt", "xlsx", "xls"],
    accept_multiple_files=True
)

with st.expander("Options"):
    force_yes_new_review = st.checkbox("Set 'New Review' to Yes for all rows", value=True)
    constant_source = st.text_input("Source value", value="DTC")
    bool_to_yesno = st.checkbox("Convert boolean-like columns to Yes/No", value=True)
    show_match_debug = st.checkbox("Show detected column matches", value=False)

if not uploaded_files:
    st.info("⬆️ Add one or more files to begin.")
    st.stop()

# Read files
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
union_cols: List[str] = []
for df in frames:
    for c in map(str, df.columns):
        if c not in union_cols:
            union_cols.append(c)

# Manual mapping override (applies to all rows)
st.sidebar.header("Manual Mapping (optional)")
all_cols_sorted = ["— auto —"] + sorted(union_cols, key=lambda x: canonicalize(x))

def mapping_control(label: str, key_name: str, default_actual: Optional[str]):
    default_idx = 0
    if default_actual and default_actual in all_cols_sorted:
        default_idx = all_cols_sorted.index(default_actual)
    return st.sidebar.selectbox(label, options=all_cols_sorted, index=default_idx, key=key_name)

# Auto-detect candidates on the merged union schema
fake_df_for_match = pd.DataFrame(columns=union_cols)
auto_map = {k: get_col(fake_df_for_match, v) for k, v in CANDIDATES.items()}

user_map = {}
user_map["product_id"]      = mapping_control("Product ID → Model (SKU)", "map_product_id",      auto_map["product_id"])
user_map["review_id"]       = mapping_control("Review ID → Verbatim Id", "map_review_id",        auto_map["review_id"])
user_map["submission_time"] = mapping_control("Submission Date → Review Date", "map_submission", auto_map["submission_time"])
user_map["review_text"]     = mapping_control("Review Text → Verbatim (Review)", "map_reviewtext", auto_map["review_text"])
user_map["rating"]          = mapping_control("Rating → Star Rating", "map_rating",              auto_map["rating"])
user_map["incentivized"]    = mapping_control("Incentivized → Seeded", "map_incentivized",       auto_map["incentivized"])

# Resolve final mapping (choose user override if not '— auto —')
final_map: Dict[str, Optional[str]] = {}
for k, v in user_map.items():
    final_map[k] = None if v == "— auto —" else v
    if final_map[k] is None:
        final_map[k] = auto_map.get(k)

# Align and concat
aligned = [df.reindex(columns=union_cols) for df in frames]
merged = pd.concat(aligned, ignore_index=True)

# Optionally convert boolean-like columns across the merged dataset
if bool_to_yesno:
    for c in merged.columns:
        try:
            if is_true_false_col(merged[c]):
                merged[c] = normalized_bool_yes_no(merged[c])
        except Exception:
            pass

# Debug table of matches
if show_match_debug:
    debug_rows = []
    for std_key, candidates in CANDIDATES.items():
        debug_rows.append({
            "Target field": std_key,
            "Auto-detected column": auto_map.get(std_key),
            "Manual override": user_map.get(std_key),
            "Using column": final_map.get(std_key),
            "Candidates tried": ", ".join(candidates[:8]) + ("…" if len(candidates) > 8 else "")
        })
    st.expander("Detected column mapping").dataframe(pd.DataFrame(debug_rows))

# Build "Raw Data (JSON)" column
raw_json = []
for _, row in merged.iterrows():
    as_dict = {str(k): (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
    raw_json.append(json.dumps(as_dict, ensure_ascii=False))
merged_with_raw = merged.copy()
merged_with_raw["Raw Data (JSON)"] = raw_json

# ----- Build Formatted Export -----

def colget(row, key: str):
    col = final_map.get(key)
    return row[col] if (col is not None and col in row.index) else None

fmt_rows = []
for idx, row in merged.iterrows():
    product_id_val  = colget(row, "product_id")
    review_id_val   = colget(row, "review_id")
    submission_val  = colget(row, "submission_time")
    review_text_val = colget(row, "review_text")
    rating_val      = colget(row, "rating")
    incentivized_val= colget(row, "incentivized")

    # Seeded
    seeded = "No"
    if incentivized_val is not None:
        seeded = normalized_bool_yes_no(pd.Series([incentivized_val])).iloc[0]

    # Country rule
    country = infer_country(product_id_val, str(row.get("_source_file", "")))

    # New Review default Yes
    new_review = "Yes" if force_yes_new_review else ""

    # Date formatting
    review_date = parse_short_date(submission_val)

    fmt_rows.append({
        "Source": constant_source or "DTC",        # = DTC by default
        "Model (SKU)": product_id_val,             # = Product ID
        "Seeded": seeded,                          # = Incentivized -> Yes/No
        "Country": country,                        # rule
        "New Review": new_review,                  # Yes by default (toggle)
        "Review Date": review_date,                # short date
        "Verbatim Id": review_id_val,              # = Review ID
        "Verbatim (Review)": review_text_val,      # = Review Text
        "Star Rating": rating_val,                 # = Rating
    })

formatted = pd.DataFrame(fmt_rows, columns=[
    "Source","Model (SKU)","Seeded","Country","New Review","Review Date","Verbatim Id","Verbatim (Review)","Star Rating"
])

# ---------- Preview ----------

st.subheader("Preview")
t1, t2 = st.tabs(["Raw Merged", "Formatted Export"])
with t1:
    st.dataframe(merged_with_raw.head(100), use_container_width=True)
with t2:
    st.dataframe(formatted.head(100), use_container_width=True)

# ---------- Downloads ----------

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

def df_to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])  # Excel sheet name limit
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

