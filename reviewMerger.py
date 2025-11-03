import io
import json
from typing import List, Optional, Dict
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

# Optional acceleration libraries (used if installed)
try:
    import polars as pl  # fastest CSV reader
    HAS_POLARS = True
except Exception:
    HAS_POLARS = False

try:
    import pyarrow as pa
    HAS_ARROW = True
except Exception:
    HAS_ARROW = False

st.set_page_config(page_title="Bazaarvoice File Merger", layout="wide")

# ------------------ Tunables ------------------
HEAVY_MB  = 80     # warn threshold
GIANT_MB  = 200    # strong warning threshold
PREVIEW_N = 200    # preview rows
# ----------------------------------------------

# ---------- Helpers ----------

TRUE_SET  = {"true","t","1","yes","y"}
FALSE_SET = {"false","f","0","no","n"}

def canonicalize(name: str) -> str:
    s = str(name).lower().strip()
    return "".join(ch for ch in s if ch.isalnum())

def yes_no_from_any(series: pd.Series) -> pd.Series:
    s = series.astype("string", copy=False)
    norm = s.str.strip().str.lower()
    yes_mask = norm.isin(TRUE_SET)
    no_mask  = norm.isin(FALSE_SET)
    out = s.astype("object")
    out[yes_mask] = "Yes"
    out[no_mask]  = "No"
    return out

def is_boolean_like(series: pd.Series) -> bool:
    s = series.dropna()
    if s.empty:
        return False
    valset = set(s.astype("string").str.strip().str.lower().unique())
    allowed = TRUE_SET | FALSE_SET
    return valset.issubset(allowed)

def parse_short_date_col(series: pd.Series) -> pd.Series:
    """
    Robust parse to mm/dd/yyyy; invalids -> None.
    Avoids AttributeError by only using .dt when truly datetimelike;
    otherwise falls back to safe per-value parsing.
    """
    s = pd.Series(series).astype("object")
    dt = pd.to_datetime(s, errors="coerce", utc=False)
    try:
        out = dt.dt.strftime("%m/%d/%Y")
        return out.where(~dt.isna(), None)
    except AttributeError:
        def fmt_one(x):
            if x is None:
                return None
            try:
                if pd.isna(x) or (isinstance(x, str) and not x.strip()):
                    return None
            except Exception:
                pass
            try:
                d = pd.to_datetime([x], errors="coerce", utc=False)[0]
                if pd.isna(d):
                    return None
                try:
                    return d.tz_localize(None).strftime("%m/%d/%Y")
                except Exception:
                    return d.strftime("%m/%d/%Y")
            except Exception:
                return None
        return s.map(fmt_one)

def try_read_csv_fast(raw: bytes, compat_mode: bool) -> Optional[pd.DataFrame]:
    """
    Prefer Polars; else pandas with pyarrow engine; else robust pandas fallback.
    If compat_mode=True, force pandas python engine + dtype=object (most robust).
    """
    if compat_mode:
        for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
            try:
                return pd.read_csv(io.BytesIO(raw), sep=None, engine="python", dtype=object, encoding=enc)
            except Exception:
                continue
        return None

    if HAS_POLARS:
        for sep in [None, ",", "\t", ";"]:
            try:
                df_pl = pl.read_csv(io.BytesIO(raw), separator=sep, infer_schema_length=1000, ignore_errors=True)
                return df_pl.to_pandas(use_pyarrow_extension_array=True)
            except Exception:
                continue

    for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
        try:
            df = pd.read_csv(
                io.BytesIO(raw),
                sep=None,
                engine=("pyarrow" if HAS_ARROW else "python"),
                dtype_backend=("pyarrow" if HAS_ARROW else None)
            )
            return df
        except Exception:
            try:
                df = pd.read_csv(
                    io.BytesIO(raw),
                    sep=None,
                    engine="python",
                    dtype=object,
                    encoding=enc
                )
                return df
            except Exception:
                continue
    return None

def read_any(uploaded_file, compat_mode: bool) -> pd.DataFrame:
    """Read CSV/TSV/XLSX into a DataFrame (Arrow/Polars-backed when possible unless compat_mode is on)."""
    name = uploaded_file.name.lower()
    raw = uploaded_file.getvalue()

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw), dtype=object)
    elif name.endswith((".csv", ".tsv", ".txt")):
        out = try_read_csv_fast(raw, compat_mode=compat_mode)
        if out is None:
            raise ValueError(f"Could not read delimited file {uploaded_file.name}")
        return out
    else:
        raise ValueError(f"Unsupported file type for {uploaded_file.name}")

def get_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lookup = {canonicalize(c): c for c in df.columns}
    for cand in candidates:
        key = canonicalize(cand)
        if key in lookup:
            return lookup[key]
    return None

def infer_country_series(product_id: pd.Series, source_name_series: pd.Series) -> pd.Series:
    pid = product_id.fillna("").astype("string")
    src = source_name_series.fillna("").astype("string")
    up_pid = pid.str.upper()
    up_src = src.str.upper()

    country = pd.Series([None]*len(pid), dtype="object")
    country = country.mask(up_pid.str.contains("UK", na=False), "UK")
    country = country.mask(up_pid.str.contains("EU", na=False), "EU")
    country = country.mask(up_src.str.contains("US|USA", na=False), "USA")
    country = country.mask(up_src.str.contains("UK", na=False) & country.isna(), "UK")
    country = country.mask(up_src.str.contains("EU", na=False) & country.isna(), "EU")
    return country

# Expanded synonyms to cover BV + RR_Export variants
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
    "title": ["Review title","Review Title","Title","Headline"]
}

# --- JSON safety ---
def to_serializable(x):
    """Make any scalar JSON-safe: handle pd.NA, NaN, Timestamp, numpy scalars, bytes, etc."""
    try:
        if x is None or pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, str):
        return x
    if isinstance(x, (bool, int, float)):
        return x
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return x.strftime("%Y-%m-%d %H:%M:%S") if isinstance(x, datetime) else x.strftime("%Y-%m-%d")
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode("utf-8", "replace")
        except Exception:
            return str(x)
    if isinstance(x, (list, tuple)):
        return [to_serializable(v) for v in x]
    if isinstance(x, dict):
        return {str(k): to_serializable(v) for k, v in x.items()}
    return str(x)

# ---------- UI ----------

st.title("Bazaarvoice Review Merger — High-Performance")
st.caption("Optimized for large files: Polars/PyArrow acceleration, vectorized transforms, optional JSON, and a heads-up for big merges.")

uploaded_files = st.file_uploader(
    "Upload Bazaarvoice files (US / UK / EU, including RR_Export)",
    type=["csv","tsv","txt","xlsx","xls"],
    accept_multiple_files=True
)

with st.expander("Options"):
    force_yes_new_review = st.checkbox("Set 'New Review' to Yes for all rows", value=True)
    constant_source = st.text_input("Source value", value="DTC")
    bool_to_yesno = st.checkbox("Convert boolean-like columns to Yes/No", value=True)
    include_raw_json_download = st.checkbox(
        "Include 'Raw Data (JSON)' in downloaded Raw CSV/Excel (memory heavy)", value=False
    )
    show_match_debug = st.checkbox("Show detected column matches", value=False)
    compat_mode = st.checkbox("Compatibility mode (force classic pandas object dtypes)", value=False)

if not uploaded_files:
    st.info("⬆️ Add one or more files to begin.")
    st.stop()

# ---------- Big-file heads-up & Start button ----------
def file_size_mb(f) -> float:
    try:
        return getattr(f, "size", None) / (1024 * 1024)
    except Exception:
        # fallback to bytes length of buffer if size missing
        try:
            return len(f.getvalue()) / (1024 * 1024)
        except Exception:
            return 0.0

sizes = [(f.name, max(file_size_mb(f), 0.0)) for f in uploaded_files]
total_mb = sum(mb for _, mb in sizes)

st.write("**Selected files:**")
for name, mb in sizes:
    st.write(f"• {name} — ~{mb:,.1f} MB")

if total_mb >= GIANT_MB:
    st.warning(f"These look **HUGE** (~{total_mb:,.1f} MB total). Combining large files may take time. "
               f"Tip: prefer CSV over Excel; turn off the JSON option for speed.")
elif total_mb >= HEAVY_MB:
    st.info(f"These look **large** (~{total_mb:,.1f} MB total). Merging may take a bit. "
            f"Tip: prefer CSV; consider disabling the JSON option.")

start = st.button("🚀 Merge files")
if not start:
    st.stop()

# ---------- Merge with live status ----------
with st.status("Preparing to merge…", expanded=True) as status:
    status.update(label="Reading files (1/5)…")
    frames = []
    errors = []
    for f in uploaded_files:
        try:
            df = read_any(f, compat_mode=compat_mode)
            df["_source_file"] = f.name
            frames.append(df)
            st.write(f"✅ Read {f.name} — {df.shape[0]:,} rows, {df.shape[1]:,} cols")
        except Exception as e:
            errors.append(f"❌ {f.name}: {e}")
    if errors:
        st.error("\n".join(errors))
        status.update(label="Aborted due to read errors.", state="error")
        st.stop()

    status.update(label="Building union schema (2/5)…")
    union_cols: List[str] = []
    for df in frames:
        for col in map(str, df.columns):
            if col not in union_cols:
                union_cols.append(col)

    status.update(label="Aligning & concatenating (3/5)…")
    aligned = [df.reindex(columns=union_cols) for df in frames]
    merged = pd.concat(aligned, ignore_index=True)

    status.update(label="Normalizing + formatting (4/5)…")
    # Convert boolean-like columns to Yes/No (vectorized)
    if bool_to_yesno:
        for c in merged.columns:
            try:
                if is_boolean_like(merged[c]):
                    merged[c] = yes_no_from_any(merged[c])
            except Exception:
                pass

    # Mapping UI (now that we know schema)
    st.sidebar.header("Manual Mapping (optional)")
    all_cols_sorted = ["— auto —"] + sorted(union_cols, key=lambda x: canonicalize(x))
    def mapping_control(label: str, key_name: str, default_actual: Optional[str]):
        idx = all_cols_sorted.index(default_actual) if default_actual in all_cols_sorted else 0
        return st.sidebar.selectbox(label, options=all_cols_sorted, index=idx, key=key_name)

    # Auto-map against union schema
    fake_df = pd.DataFrame(columns=union_cols)
    def get_col_from_candidates(cands: List[str]) -> Optional[str]:
        return get_col(fake_df, cands)

    auto_map = {
        "product_id":      get_col_from_candidates(CANDIDATES["product_id"]),
        "review_id":       get_col_from_candidates(CANDIDATES["review_id"]),
        "submission_time": get_col_from_candidates(CANDIDATES["submission_time"]),
        "review_text":     get_col_from_candidates(CANDIDATES["review_text"]),
        "rating":          get_col_from_candidates(CANDIDATES["rating"]),
        "incentivized":    get_col_from_candidates(CANDIDATES["incentivized"]),
    }
    user_map = {}
    user_map["product_id"]      = mapping_control("Product ID → Model (SKU)", "map_product_id",      auto_map["product_id"])
    user_map["review_id"]       = mapping_control("Review ID → Verbatim Id", "map_review_id",        auto_map["review_id"])
    user_map["submission_time"] = mapping_control("Submission Date → Review Date", "map_submission", auto_map["submission_time"])
    user_map["review_text"]     = mapping_control("Review Text → Verbatim (Review)", "map_reviewtext", auto_map["review_text"])
    user_map["rating"]          = mapping_control("Rating → Star Rating", "map_rating",              auto_map["rating"])
    user_map["incentivized"]    = mapping_control("Incentivized → Seeded", "map_incent",             auto_map["incentivized"])

    final_map: Dict[str, Optional[str]] = {}
    for k, v in user_map.items():
        final_map[k] = None if v == "— auto —" else v
        if final_map[k] is None:
            final_map[k] = auto_map.get(k)

    def safe_col(name: Optional[str]) -> pd.Series:
        if name is None or name not in merged.columns:
            return pd.Series([None]*len(merged), dtype="object")
        return merged[name]

    product_id_s  = safe_col(final_map["product_id"])
    review_id_s   = safe_col(final_map["review_id"])
    submission_s  = safe_col(final_map["submission_time"])
    review_text_s = safe_col(final_map["review_text"])
    rating_s      = safe_col(final_map["rating"])
    incent_s      = safe_col(final_map["incentivized"])
    source_file_s = merged.get("_source_file", pd.Series([None]*len(merged), dtype="object"))

    seeded_s     = yes_no_from_any(incent_s) if bool_to_yesno else incent_s
    country_s    = infer_country_series(product_id_s, source_file_s)
    new_review_s = pd.Series(["Yes" if force_yes_new_review else ""]*len(merged), dtype="object")
    review_date_s= parse_short_date_col(submission_s)
    source_s     = pd.Series([constant_source or "DTC"]*len(merged), dtype="object")

    formatted = pd.DataFrame({
        "Source": source_s,
        "Model (SKU)": product_id_s,
        "Seeded": seeded_s,
        "Country": country_s,
        "New Review": new_review_s,
        "Review Date": review_date_s,
        "Verbatim Id": review_id_s,
        "Verbatim (Review)": review_text_s,
        "Star Rating": rating_s,
    })

    # Optional full JSON column (memory heavy)
    if include_raw_json_download:
        merged_with_raw = merged.copy()
        merged_with_raw["Raw Data (JSON)"] = merged.apply(
            lambda r: json.dumps({str(k): to_serializable(r[k]) for k in merged.columns}, ensure_ascii=False),
            axis=1
        )
    else:
        merged_with_raw = merged

    status.update(label="Finalizing outputs (5/5)…", state="running")

    status.update(label="Merge complete ✅", state="complete")

# ---------- Preview ----------
st.subheader(f"Preview (first {PREVIEW_N} rows)")
t1, t2 = st.tabs(["Raw Merged", "Formatted Export"])
with t1:
    sample = merged.head(PREVIEW_N).copy()
    # lightweight JSON preview (always safe)
    sample["Raw Data (JSON)"] = sample.apply(
        lambda r: json.dumps({str(k): to_serializable(r[k]) for k in sample.columns}, ensure_ascii=False),
        axis=1
    )
    st.dataframe(sample, use_container_width=True)
with t2:
    st.dataframe(formatted.head(PREVIEW_N), use_container_width=True)

# ---------- Downloads ----------
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

def to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
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
        data=to_csv_bytes(merged_with_raw),
        file_name="bv_merged_raw.csv",
        mime="text/csv"
    )
    st.download_button(
        label="Download Excel (Raw + Formatted)",
        data=to_excel_bytes({"Raw": merged_with_raw, "Formatted": formatted}),
        file_name="bv_merged_outputs.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

with colB:
    st.markdown("### Download Formatted Export")
    st.download_button(
        label="Download CSV (Formatted)",
        data=to_csv_bytes(formatted),
        file_name="bv_formatted_export.csv",
        mime="text/csv"
    )

with st.expander("Performance tips"):
    st.markdown(
        f"- **Heads-up shown for files ≥ {HEAVY_MB} MB**; extra strong warning at {GIANT_MB} MB.\n"
        "- Prefer **CSV** over Excel for very large files.\n"
        "- Upload **unzipped CSVs** directly from Bazaarvoice when possible.\n"
        "- If installed, the app uses **Polars** or **PyArrow-backed pandas** for speed and lower memory.\n"
        "- Turn **off** the 'Raw Data (JSON)' option for big merges.\n"
        "- Use **Compatibility mode** if you see dtype/date parsing quirks."
    )







