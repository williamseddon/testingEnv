import streamlit as st
import pandas as pd
from io import BytesIO
import tempfile
import os
import gzip
import shutil

# Optional acceleration + lower memory
try:
    import polars as pl
    HAS_POLARS = True
except Exception:
    HAS_POLARS = False

st.set_page_config(page_title="Bazaarvoice Merger (Stable + Fast + Base SKU)", layout="wide")
st.title("🧱⚡ Bazaarvoice Merger (Stable + Fast + Base SKU + Locale/Boolean Tools)")
st.caption(
    "Crash-resistant: loads/merges only on click, avoids rendering huge tables, "
    "and writes downloads to disk (no giant in-memory blobs)."
)

REQUIRED_HEADERS = ("Review ID", "Review Submission Date")
EXCEL_CELL_LIMIT = 32767  # Excel hard limit per cell (characters)

DEFAULT_LOCALES = [
    "en_GB",
    "de_DE",
    "fr_FR",
    "pl_PL",
    "nl_NL",
    "es_ES",
    "nl_BE",
    "it_IT",
    "da_DK",
    "sv_SE",
    "fr_BE",
    "no_NO",
    "en_US",
    "en_CA",
]

# ----------------------------
# Utilities
# ----------------------------
def ensure_temp_dir():
    d = os.path.join(tempfile.gettempdir(), "bv_merge_streamlit")
    os.makedirs(d, exist_ok=True)
    return d

def gzip_file(src_path: str, gz_path: str):
    with open(src_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

def find_header_row_from_preview(preview: pd.DataFrame) -> int:
    req = [h.strip().lower() for h in REQUIRED_HEADERS]
    for i in range(len(preview)):
        row_vals = (
            preview.iloc[i]
            .dropna()
            .astype(str)
            .str.strip()
            .str.lower()
            .tolist()
        )
        if all(r in row_vals for r in req):
            return i
    raise ValueError(f"Could not find header row containing: {REQUIRED_HEADERS}")

def load_excel_pandas(file_bytes: bytes, region: str, header_hint: int | None = None, usecols=None):
    """
    Optimized Excel reader:
    - Open workbook once via pd.ExcelFile
    - Try header_hint first; if that fails, detect header from a small preview
    """
    xls = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")

    header_row = header_hint
    df = None
    last_err = None

    def _parse(h):
        return xls.parse(
            header=h,
            usecols=usecols,
            dtype={
                "Review ID": "string",
                "Product ID": "string",
                "Reviewer ID": "string",
                "EAN": "string",
                "UPC": "string",
            },
        )

    # Fast path: header hint
    if header_row is not None:
        try:
            df = _parse(header_row)
            cols_lower = {str(c).strip().lower() for c in df.columns}
            if not all(h.lower() in cols_lower for h in REQUIRED_HEADERS):
                raise ValueError("Header hint parsed but required headers not found.")
        except Exception as e:
            last_err = str(e)
            df = None

    # Fallback: detect header from small preview
    if df is None:
        preview = xls.parse(header=None, nrows=25)
        header_row = find_header_row_from_preview(preview)
        df = _parse(header_row)

    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    df.insert(0, "Region", region)

    return df, header_row, last_err

def pandas_to_polars(df: pd.DataFrame) -> "pl.DataFrame":
    return pl.from_pandas(df, include_index=False)

def compute_col_diffs(cols1, cols2):
    s1, s2 = set(cols1), set(cols2)
    return sorted(s1 - s2), sorted(s2 - s1)

def make_excel_safe_inplace(df: pd.DataFrame):
    """
    Trim known long-text columns in-place to avoid Excel 32,767 char cell limit.
    Returns dict of {col: trimmed_count}.
    """
    trimmed = {}
    for col in ["Review Text", "Review Title"]:
        if col in df.columns:
            s = df[col].astype("string")
            mask = s.str.len() > EXCEL_CELL_LIMIT
            n = int(mask.sum())
            if n > 0:
                df.loc[mask, col] = s[mask].str.slice(0, EXCEL_CELL_LIMIT)
                trimmed[col] = n
    return trimmed

# ----------------------------
# Base SKU mapping
# ----------------------------
def load_mapping_excel(mapping_bytes: bytes):
    """
    Load Base SKU mapping file. Expects columns:
      - SKU
      - Master Item
    Returns mapping_df (pandas) and a lookup dict {SKU: Master Item}.
    """
    m = pd.read_excel(BytesIO(mapping_bytes), engine="openpyxl")
    m.columns = [str(c).strip() for c in m.columns]

    if "SKU" not in m.columns or "Master Item" not in m.columns:
        raise ValueError("Mapping file must contain columns named exactly: 'SKU' and 'Master Item'.")

    sku = m["SKU"].astype("string").str.strip()
    base = m["Master Item"].astype("string").str.strip()

    lookup = dict(zip(sku, base))
    return m, lookup

def apply_base_sku_lookup_pandas(merged_df: pd.DataFrame, lookup: dict) -> tuple[pd.DataFrame, int]:
    """
    Adds 'Base SKU' column by mapping merged_df['Product ID'] -> lookup[SKU] = Master Item.
    Places 'Base SKU' next to 'Product ID' when present.
    Returns (updated_df, matched_count).
    """
    if "Product ID" not in merged_df.columns:
        merged_df["Base SKU"] = pd.NA
        return merged_df, 0

    pid = merged_df["Product ID"].astype("string").str.strip()
    merged_df["Base SKU"] = pid.map(lookup)

    matched = int(merged_df["Base SKU"].notna().sum())

    # Move Base SKU next to Product ID
    cols = list(merged_df.columns)
    cols.remove("Base SKU")
    pid_idx = cols.index("Product ID")
    cols.insert(pid_idx + 1, "Base SKU")
    merged_df = merged_df[cols]

    return merged_df, matched

def apply_base_sku_lookup_polars(merged_pl: "pl.DataFrame", mapping_df: pd.DataFrame) -> tuple["pl.DataFrame", int]:
    """
    Polars join approach:
    left join on Product ID == SKU, output Base SKU = Master Item.
    Returns (updated_pl, matched_count).
    """
    if "Product ID" not in merged_pl.columns:
        merged_pl = merged_pl.with_columns(pl.lit(None).alias("Base SKU"))
        return merged_pl, 0

    map_pl = pl.from_pandas(
        mapping_df[["SKU", "Master Item"]].copy(),
        include_index=False
    ).with_columns([
        pl.col("SKU").cast(pl.Utf8).str.strip_chars(),
        pl.col("Master Item").cast(pl.Utf8).str.strip_chars()
    ])

    merged_pl = merged_pl.with_columns(
        pl.col("Product ID").cast(pl.Utf8).str.strip_chars()
    )

    joined = merged_pl.join(map_pl, left_on="Product ID", right_on="SKU", how="left")
    joined = joined.rename({"Master Item": "Base SKU"}).drop("SKU")

    matched = int(joined.select(pl.col("Base SKU").is_not_null().sum()).item())

    # Reorder Base SKU next to Product ID
    cols = joined.columns
    if "Base SKU" in cols and "Product ID" in cols:
        cols2 = [c for c in cols if c != "Base SKU"]
        pid_idx = cols2.index("Product ID")
        cols2.insert(pid_idx + 1, "Base SKU")
        joined = joined.select(cols2)

    return joined, matched

# ----------------------------
# Incentivized boolean normalization
# ----------------------------
TRUE_STRINGS = {"yes", "true", "1", "y", "t"}

def find_incentivized_col(columns: list[str]) -> str | None:
    # Prefer exact-ish common name, else first contains "incentiv"
    preferred = [
        "IncentivizedReview (CDV)",
        "Incentivized Review",
        "Incentivized",
    ]
    cols_set = {c: c for c in columns}
    for p in preferred:
        if p in cols_set:
            return p
    for c in columns:
        if "incentiv" in c.lower():
            return c
    return None

def normalize_incentivized_pandas(df: pd.DataFrame, col: str) -> tuple[pd.DataFrame, int, int]:
    """
    Convert Incentivized column to boolean:
    - Yes/True -> True
    - blanks/No/False/anything else -> False
    Returns (df, true_count, total_count)
    """
    if col not in df.columns:
        return df, 0, 0

    # Robust: cast to string, lower, strip; bools become "True"/"False"
    s = df[col].astype("string").str.strip().str.lower()
    out = s.isin(TRUE_STRINGS)  # <NA> becomes False
    df[col] = out

    true_count = int(out.sum())
    total_count = int(len(df))
    return df, true_count, total_count

def normalize_incentivized_polars(df: "pl.DataFrame", col: str) -> tuple["pl.DataFrame", int, int]:
    if col not in df.columns:
        return df, 0, 0

    expr = (
        pl.col(col)
        .cast(pl.Utf8)
        .str.strip_chars()
        .str.to_lowercase()
        .is_in(list(TRUE_STRINGS))
        .fill_null(False)
        .alias(col)
    )
    df = df.with_columns(expr)
    true_count = int(df.select(pl.col(col).sum()).item())  # True treated as 1
    total_count = int(df.height)
    return df, true_count, total_count

# ----------------------------
# Locale filter + Country column
# ----------------------------
def add_country_from_locale_pandas(df: pd.DataFrame, locale_col: str = "Review Display Locale") -> pd.DataFrame:
    if locale_col not in df.columns:
        df["Country"] = pd.NA
        return df

    loc = df[locale_col].astype("string").str.strip()
    country = loc.str.split("_").str[-1].str.upper()
    country = country.replace({"GB": "UK"})  # friendlier grouping
    df["Country"] = country

    # Place Country next to locale col
    cols = list(df.columns)
    cols.remove("Country")
    idx = cols.index(locale_col)
    cols.insert(idx + 1, "Country")
    return df[cols]

def filter_locales_pandas(df: pd.DataFrame, selected_locales: list[str], locale_col: str = "Review Display Locale") -> tuple[pd.DataFrame, int, int]:
    if locale_col not in df.columns:
        return df, len(df), len(df)

    before = len(df)
    loc = df[locale_col].astype("string").str.strip()
    df = df[loc.isin(selected_locales)]
    after = len(df)
    return df, before, after

def add_country_from_locale_polars(df: "pl.DataFrame", locale_col: str = "Review Display Locale") -> "pl.DataFrame":
    if locale_col not in df.columns:
        return df.with_columns(pl.lit(None).alias("Country"))

    country_expr = (
        pl.col(locale_col)
        .cast(pl.Utf8)
        .str.strip_chars()
        .str.split("_")
        .list.get(-1)
        .cast(pl.Utf8)
        .str.to_uppercase()
    )

    country_expr = (
        pl.when(country_expr == "GB").then(pl.lit("UK")).otherwise(country_expr).alias("Country")
    )

    df = df.with_columns(country_expr)

    # Reorder Country next to locale_col
    cols = df.columns
    if "Country" in cols:
        cols2 = [c for c in cols if c != "Country"]
        idx = cols2.index(locale_col)
        cols2.insert(idx + 1, "Country")
        df = df.select(cols2)
    return df

def filter_locales_polars(df: "pl.DataFrame", selected_locales: list[str], locale_col: str = "Review Display Locale") -> tuple["pl.DataFrame", int, int]:
    if locale_col not in df.columns:
        return df, int(df.height), int(df.height)

    before = int(df.height)
    keep_expr = (
        pl.col(locale_col)
        .cast(pl.Utf8)
        .str.strip_chars()
        .is_in(selected_locales)
        .fill_null(False)
    )
    df = df.filter(keep_expr)
    after = int(df.height)
    return df, before, after

# ----------------------------
# Disk-backed writers (avoid huge in-memory bytes)
# ----------------------------
def write_csv_disk(merged, kind: str, out_path: str):
    if kind == "polars":
        merged.write_csv(out_path)
    else:
        merged.to_csv(out_path, index=False)

def write_parquet_disk(merged, kind: str, out_path: str):
    if kind == "polars":
        merged.write_parquet(out_path)
    else:
        merged.to_parquet(out_path, index=False)

def write_xlsx_disk(merged, kind: str, out_path: str):
    # XLSX needs pandas. Convert only at the last second.
    if kind == "polars":
        tmp = merged.to_pandas()
    else:
        tmp = merged

    trimmed_info = make_excel_safe_inplace(tmp)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        tmp.to_excel(writer, index=False, sheet_name="Merged")

    return trimmed_info

# ----------------------------
# UI inputs
# ----------------------------
c1, c2 = st.columns(2)
with c1:
    f1 = st.file_uploader("Upload EU/UK Excel export", type=["xlsx"], key="eu")
    region1 = st.text_input("Label for file 1", value="EU/UK", key="r1")
    header_hint_1 = st.number_input(
        "Header hint (0-based) file 1",
        min_value=0, max_value=200, value=5, step=1,
        help="For typical EU/UK BV export this is often 5 (Excel row 6)."
    )
with c2:
    f2 = st.file_uploader("Upload USA Excel export", type=["xlsx"], key="us")
    region2 = st.text_input("Label for file 2", value="USA", key="r2")
    header_hint_2 = st.number_input(
        "Header hint (0-based) file 2",
        min_value=0, max_value=200, value=6, step=1,
        help="For typical USA BV export this is often 6 (Excel row 7)."
    )

st.divider()

# Optional mapping upload
mapping_file = st.file_uploader(
    "Optional: Upload Base SKU mapping file (must include columns 'SKU' and 'Master Item')",
    type=["xlsx"],
    key="mapping"
)

# NEW: Incentivized normalization (prechecked)
normalize_incentivized = st.checkbox(
    "Normalize Incentivized column to TRUE/FALSE (Yes/True → True; blanks/No → False)",
    value=True
)

# NEW: Locale filter + Country (both prechecked)
apply_locale_filter = st.checkbox(
    "Filter to selected Review Display Locale values",
    value=True
)
selected_locales = st.multiselect(
    "Review Display Locale values to include",
    options=DEFAULT_LOCALES,
    default=DEFAULT_LOCALES,
    disabled=not apply_locale_filter
)
add_country_col = st.checkbox(
    "Add a 'Country' column derived from Review Display Locale (e.g., en_GB → UK, de_DE → DE)",
    value=True
)

st.divider()

engine = st.radio(
    "Engine",
    ["Auto (recommended)", "Pandas only"],
    horizontal=True,
    help="Auto uses Polars if installed for better memory stability."
)

usecols_mode = st.radio(
    "Columns to load",
    ["All columns (slowest / biggest)", "Select columns (faster / smaller)"],
    horizontal=True
)

cols_text = None
if usecols_mode == "Select columns (faster / smaller)":
    cols_text = st.text_input(
        "Columns to load (comma-separated, exact names). Leave blank for all.",
        value=(
            "Review ID, Review Submission Date, Product ID, Overall Rating, "
            "IncentivizedReview (CDV), Review Display Locale, Review Title, Review Text"
        ),
        help="Tip: Keep Product ID for Base SKU and Review Display Locale for locale/country tools."
    )

def parse_usecols(text):
    if not text:
        return None
    cols = [c.strip() for c in text.split(",") if c.strip()]
    return cols if cols else None

usecols = parse_usecols(cols_text)

st.divider()

# Persist minimal state (avoid repeated expensive work)
if "merged" not in st.session_state:
    st.session_state.merged = None
    st.session_state.merged_kind = None
    st.session_state.meta = None
    st.session_state.run_id = 0

load_btn = st.button("🚀 Load & Merge", type="primary", disabled=not (f1 and f2))

if load_btn:
    st.session_state.run_id += 1
    run_id = st.session_state.run_id

    b1 = f1.getvalue()
    b2 = f2.getvalue()

    with st.spinner("Loading EU/UK…"):
        df1, hdr1, err1 = load_excel_pandas(b1, region1, int(header_hint_1), usecols=usecols)
    with st.spinner("Loading USA…"):
        df2, hdr2, err2 = load_excel_pandas(b2, region2, int(header_hint_2), usecols=usecols)

    use_polars = (engine == "Auto (recommended)") and HAS_POLARS

    # Merge
    if use_polars:
        with st.spinner("Converting to Polars + merging…"):
            p1 = pandas_to_polars(df1)
            p2 = pandas_to_polars(df2)
            merged = pl.concat([p1, p2], how="diagonal")  # safe for mismatched columns
        merged_kind = "polars"
        cols1, cols2 = df1.columns.tolist(), df2.columns.tolist()
    else:
        with st.spinner("Merging with Pandas…"):
            merged = pd.concat([df1, df2], ignore_index=True, sort=False)
        merged_kind = "pandas"
        cols1, cols2 = df1.columns.tolist(), df2.columns.tolist()

    # Apply locale filter (prechecked)
    locale_filter_info = None
    if apply_locale_filter and selected_locales:
        if merged_kind == "polars":
            merged, before_n, after_n = filter_locales_polars(merged, selected_locales)
        else:
            merged, before_n, after_n = filter_locales_pandas(merged, selected_locales)
        locale_filter_info = (before_n, after_n)

    # Add Country column (prechecked)
    if add_country_col:
        if merged_kind == "polars":
            merged = add_country_from_locale_polars(merged)
        else:
            merged = add_country_from_locale_pandas(merged)

    # Incentivized normalization (prechecked)
    incent_info = None
    if normalize_incentivized:
        if merged_kind == "polars":
            incent_col = find_incentivized_col(merged.columns)
            if incent_col:
                merged, tcnt, tot = normalize_incentivized_polars(merged, incent_col)
                incent_info = (incent_col, tcnt, tot)
        else:
            incent_col = find_incentivized_col(list(merged.columns))
            if incent_col:
                merged, tcnt, tot = normalize_incentivized_pandas(merged, incent_col)
                incent_info = (incent_col, tcnt, tot)

    # Optional Base SKU lookup
    base_sku_matched = None
    base_sku_enabled = False
    mapping_cols_ok = None
    mapping_err = None

    if mapping_file is not None:
        try:
            with st.spinner("Loading mapping + applying Base SKU lookup…"):
                mbytes = mapping_file.getvalue()
                mapping_df, lookup = load_mapping_excel(mbytes)
                base_sku_enabled = True
                mapping_cols_ok = True

                if merged_kind == "polars":
                    merged, base_sku_matched = apply_base_sku_lookup_polars(merged, mapping_df)
                else:
                    merged, base_sku_matched = apply_base_sku_lookup_pandas(merged, lookup)

        except Exception as e:
            mapping_err = str(e)
            mapping_cols_ok = False

    # Sizes for display
    if merged_kind == "polars":
        total_rows, total_cols = int(merged.height), int(merged.width)
    else:
        total_rows, total_cols = int(len(merged)), int(merged.shape[1])

    st.session_state.merged = merged
    st.session_state.merged_kind = merged_kind
    st.session_state.meta = {
        "run_id": run_id,
        "rows_1": len(df1),
        "rows_2": len(df2),
        "hdr1": hdr1,
        "hdr2": hdr2,
        "err1": err1,
        "err2": err2,
        "cols_1": cols1,
        "cols_2": cols2,
        "total_rows": total_rows,
        "total_cols": total_cols,
        "region1": region1,
        "region2": region2,
        "base_sku_enabled": base_sku_enabled,
        "base_sku_matched": base_sku_matched,
        "mapping_ok": mapping_cols_ok,
        "mapping_err": mapping_err,
        "locale_filter_info": locale_filter_info,
        "incent_info": incent_info,
    }

# ----------------------------
# Results + download
# ----------------------------
if st.session_state.merged is None:
    st.info("Upload both files (and optional mapping), then click **Load & Merge**.")
    st.stop()

merged = st.session_state.merged
kind = st.session_state.merged_kind
meta = st.session_state.meta

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"{meta['region1']} rows (input)", f"{meta['rows_1']:,}")
m2.metric(f"{meta['region2']} rows (input)", f"{meta['rows_2']:,}")
m3.metric("Header row file 1", f"{meta['hdr1']} (Excel row {meta['hdr1']+1})")
m4.metric("Header row file 2", f"{meta['hdr2']} (Excel row {meta['hdr2']+1})")

if meta["err1"]:
    st.info(f"File 1 header hint fallback used: {meta['err1']}")
if meta["err2"]:
    st.info(f"File 2 header hint fallback used: {meta['err2']}")

only_1, only_2 = compute_col_diffs(meta["cols_1"], meta["cols_2"])
if only_1 or only_2:
    st.warning("Column differences detected. Merge is safe; missing values become blank.")
    cc1, cc2 = st.columns(2)
    with cc1:
        st.write(f"Only in **{meta['region1']}** ({len(only_1)}):")
        st.code("\n".join(only_1) if only_1 else "(none)")
    with cc2:
        st.write(f"Only in **{meta['region2']}** ({len(only_2)}):")
        st.code("\n".join(only_2) if only_2 else "(none)")
else:
    st.success("Columns match exactly across both files.")

# Locale filter info
if meta.get("locale_filter_info"):
    before_n, after_n = meta["locale_filter_info"]
    st.success(f"Locale filter applied: {before_n:,} → {after_n:,} rows kept.")

# Incentivized info
if meta.get("incent_info"):
    col, tcnt, tot = meta["incent_info"]
    st.success(f"Incentivized normalized in '{col}': True = {tcnt:,} / {tot:,} rows.")
elif normalize_incentivized:
    st.warning("Incentivized normalization enabled, but no Incentivized column was found in the loaded columns.")

# Mapping status
if meta.get("mapping_ok") is False:
    st.error(f"Base SKU mapping was uploaded but could not be applied: {meta.get('mapping_err')}")
elif meta.get("base_sku_enabled"):
    st.success(f"Base SKU lookup applied. Matches found: {meta.get('base_sku_matched', 0):,}")

st.subheader("Sample rows (first 25)")
if kind == "polars":
    st.dataframe(merged.head(25).to_pandas(), use_container_width=True)
else:
    st.dataframe(merged.head(25), use_container_width=True)

st.caption(f"Merged size: {meta['total_rows']:,} rows × {meta['total_cols']:,} columns | Engine: {kind}")

st.divider()
st.subheader("Download (disk-backed, crash-resistant)")

out_format = st.selectbox(
    "Format",
    ["CSV (recommended)", "CSV (.gz) (smaller)", "Parquet (fast + compact)", "Excel (.xlsx) (slow/risky)"]
)

temp_dir = ensure_temp_dir()
run_id = meta["run_id"]

csv_path  = os.path.join(temp_dir, f"merged_reviews_{run_id}.csv")
gz_path   = os.path.join(temp_dir, f"merged_reviews_{run_id}.csv.gz")
pq_path   = os.path.join(temp_dir, f"merged_reviews_{run_id}.parquet")
xlsx_path = os.path.join(temp_dir, f"merged_reviews_{run_id}.xlsx")

build_btn_label = {
    "CSV (recommended)": "Build CSV",
    "CSV (.gz) (smaller)": "Build compressed CSV (.gz)",
    "Parquet (fast + compact)": "Build Parquet",
    "Excel (.xlsx) (slow/risky)": "Build XLSX",
}[out_format]

if out_format.startswith("Excel"):
    st.warning(
        "XLSX export is the most likely to crash for large, text-heavy exports. "
        "Prefer CSV/Parquet when possible."
    )
    proceed = st.checkbox("I understand; try XLSX anyway", value=False)
else:
    proceed = True

if proceed and st.button(build_btn_label, type="secondary"):
    if out_format.startswith("CSV (.gz)"):
        with st.spinner("Writing CSV to disk…"):
            write_csv_disk(merged, kind, csv_path)
        with st.spinner("Compressing…"):
            gzip_file(csv_path, gz_path)
        st.success("Compressed CSV ready.")

    elif out_format.startswith("CSV"):
        with st.spinner("Writing CSV to disk…"):
            write_csv_disk(merged, kind, csv_path)
        st.success("CSV ready.")

    elif out_format.startswith("Parquet"):
        with st.spinner("Writing Parquet to disk…"):
            write_parquet_disk(merged, kind, pq_path)
        st.success("Parquet ready.")

    else:
        with st.spinner("Writing XLSX to disk…"):
            trimmed = write_xlsx_disk(merged, kind, xlsx_path)
        if trimmed:
            st.warning(
                "Some cells exceeded Excel’s 32,767 character limit and were trimmed:\n"
                + "\n".join([f"- {k}: {v:,} rows" for k, v in trimmed.items()])
            )
        st.success("XLSX ready.")

# Download buttons appear if file exists
if out_format.startswith("CSV (.gz)") and os.path.exists(gz_path):
    with open(gz_path, "rb") as f:
        st.download_button(
            "⬇️ Download merged CSV (.gz)",
            data=f,
            file_name="merged_reviews.csv.gz",
            mime="application/gzip",
        )

elif out_format.startswith("CSV") and os.path.exists(csv_path):
    with open(csv_path, "rb") as f:
        st.download_button(
            "⬇️ Download merged CSV",
            data=f,
            file_name="merged_reviews.csv",
            mime="text/csv",
        )

elif out_format.startswith("Parquet") and os.path.exists(pq_path):
    with open(pq_path, "rb") as f:
        st.download_button(
            "⬇️ Download merged Parquet",
            data=f,
            file_name="merged_reviews.parquet",
            mime="application/octet-stream",
        )

elif out_format.startswith("Excel") and os.path.exists(xlsx_path):
    with open(xlsx_path, "rb") as f:
        st.download_button(
            "⬇️ Download merged XLSX",
            data=f,
            file_name="merged_reviews.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.caption(
    "Base SKU lookup: Product ID (reviews) → SKU (mapping) → Master Item (output as Base SKU). "
    "Locale tools: filter by Review Display Locale + add Country derived from locale. "
    "Incentivized tool: Yes/True → True; blanks/No → False."
)
