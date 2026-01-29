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

st.set_page_config(page_title="Bazaarvoice Merger (Stable + Fast)", layout="wide")
st.title("🧱⚡ Bazaarvoice Merger (Stable + Fast)")
st.caption(
    "Optimized to avoid crashes: loads/merges only on click, avoids rendering huge tables, "
    "and writes downloads to disk (not big in-memory bytes)."
)

REQUIRED_HEADERS = ("Review ID", "Review Submission Date")
EXCEL_CELL_LIMIT = 32767  # Excel hard limit per cell (characters)

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

@st.cache_data(show_spinner=False)
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

# Disk-backed writers (avoid huge in-memory bytes)
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
        help="For your EU/UK export this is typically 5 (Excel row 6)."
    )
with c2:
    f2 = st.file_uploader("Upload USA Excel export", type=["xlsx"], key="us")
    region2 = st.text_input("Label for file 2", value="USA", key="r2")
    header_hint_2 = st.number_input(
        "Header hint (0-based) file 2",
        min_value=0, max_value=200, value=6, step=1,
        help="For your USA export this is typically 6 (Excel row 7)."
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
        value="Review ID, Review Submission Date, Product ID, Rating, Review Title, Review Text",
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

load_btn = st.button("🚀 Load & Merge", type="primary", disabled=not (f1 and f2))

if load_btn:
    b1 = f1.getvalue()
    b2 = f2.getvalue()

    with st.spinner("Loading EU/UK…"):
        df1, hdr1, err1 = load_excel_pandas(b1, region1, int(header_hint_1), usecols=usecols)
    with st.spinner("Loading USA…"):
        df2, hdr2, err2 = load_excel_pandas(b2, region2, int(header_hint_2), usecols=usecols)

    use_polars = (engine == "Auto (recommended)") and HAS_POLARS

    if use_polars:
        with st.spinner("Converting to Polars + merging…"):
            p1 = pandas_to_polars(df1)
            p2 = pandas_to_polars(df2)
            merged = pl.concat([p1, p2], how="diagonal")  # safe for mismatched columns
        merged_kind = "polars"
        total_rows, total_cols = merged.height, merged.width
        cols1, cols2 = df1.columns.tolist(), df2.columns.tolist()
    else:
        with st.spinner("Merging with Pandas…"):
            merged = pd.concat([df1, df2], ignore_index=True, sort=False)
        merged_kind = "pandas"
        total_rows, total_cols = len(merged), merged.shape[1]
        cols1, cols2 = df1.columns.tolist(), df2.columns.tolist()

    st.session_state.merged = merged
    st.session_state.merged_kind = merged_kind
    st.session_state.meta = {
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
    }

# ----------------------------
# Results + download
# ----------------------------
if st.session_state.merged is None:
    st.info("Upload both files and click **Load & Merge**.")
    st.stop()

merged = st.session_state.merged
kind = st.session_state.merged_kind
meta = st.session_state.meta

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"{meta['region1']} rows", f"{meta['rows_1']:,}")
m2.metric(f"{meta['region2']} rows", f"{meta['rows_2']:,}")
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
csv_path  = os.path.join(temp_dir, "merged_reviews.csv")
gz_path   = os.path.join(temp_dir, "merged_reviews.csv.gz")
pq_path   = os.path.join(temp_dir, "merged_reviews.parquet")
xlsx_path = os.path.join(temp_dir, "merged_reviews.xlsx")

# Build step separated from download to avoid rebuild on reruns
build_btn_label = {
    "CSV (recommended)": "Build CSV",
    "CSV (.gz) (smaller)": "Build compressed CSV (.gz)",
    "Parquet (fast + compact)": "Build Parquet",
    "Excel (.xlsx) (slow/risky)": "Build XLSX",
}[out_format]

if out_format.startswith("Excel"):
    st.warning(
        "XLSX export is the most likely to crash for ~400k-row text-heavy exports. "
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

st.caption("Tip: If downloads still crash, use Parquet or CSV (.gz). XLSX is the most memory-intensive.")




