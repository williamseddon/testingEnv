import streamlit as st
from io import BytesIO
import pandas as pd

# Optional acceleration + lower memory
try:
    import polars as pl
    HAS_POLARS = True
except Exception:
    HAS_POLARS = False

st.set_page_config(page_title="Bazaarvoice Merger (Stable)", layout="wide")
st.title("🧱 Bazaarvoice Merger (Stable / Crash-Resistant)")

REQUIRED_HEADERS = ("Review ID", "Review Submission Date")
EXCEL_CELL_LIMIT = 32767

# ----------------------------
# Header detection (fast-ish)
# ----------------------------
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
    # Open once
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

    if header_row is not None:
        try:
            df = _parse(header_row)
            cols_lower = {str(c).strip().lower() for c in df.columns}
            if not all(h.lower() in cols_lower for h in REQUIRED_HEADERS):
                raise ValueError("Header hint parsed but required headers not found.")
        except Exception as e:
            last_err = str(e)
            df = None

    if df is None:
        preview = xls.parse(header=None, nrows=25)
        header_row = find_header_row_from_preview(preview)
        df = _parse(header_row)

    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    df.insert(0, "Region", region)

    return df, header_row, last_err

def pandas_to_polars(df: pd.DataFrame) -> "pl.DataFrame":
    # Avoid huge copies when possible
    return pl.from_pandas(df, include_index=False)

# ----------------------------
# Output writers (low memory)
# ----------------------------
def merged_to_csv_bytes_pandas(df: pd.DataFrame) -> bytes:
    # Still builds bytes in memory; OK for many cases but can be heavy.
    return df.to_csv(index=False).encode("utf-8")

def merged_to_csv_bytes_polars(df: "pl.DataFrame") -> bytes:
    # Polars write_csv to bytes
    buf = BytesIO()
    df.write_csv(buf)
    return buf.getvalue()

def merged_to_parquet_bytes_polars(df: "pl.DataFrame") -> bytes:
    buf = BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()

def merged_to_parquet_bytes_pandas(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()

# ----------------------------
# UI
# ----------------------------
c1, c2 = st.columns(2)
with c1:
    f1 = st.file_uploader("Upload EU/UK Excel export", type=["xlsx"], key="eu")
    region1 = st.text_input("Label for file 1", value="EU/UK", key="r1")
    header_hint_1 = st.number_input("Header hint (0-based) file 1", min_value=0, max_value=200, value=5, step=1)
with c2:
    f2 = st.file_uploader("Upload USA Excel export", type=["xlsx"], key="us")
    region2 = st.text_input("Label for file 2", value="USA", key="r2")
    header_hint_2 = st.number_input("Header hint (0-based) file 2", min_value=0, max_value=200, value=6, step=1)

st.divider()

# Avoid rerun-related churn: do heavy work only on click
load_btn = st.button("🚀 Load & Merge", type="primary", disabled=not (f1 and f2))

# Persist minimal state
if "merged_kind" not in st.session_state:
    st.session_state.merged_kind = None  # "polars" or "pandas"
    st.session_state.merged = None
    st.session_state.meta = None

# Choose engine
engine = st.radio(
    "Engine",
    ["Auto (recommended)", "Pandas only"],
    horizontal=True,
    help="Auto uses Polars if installed for better stability/performance."
)

if load_btn:
    b1 = f1.getvalue()
    b2 = f2.getvalue()

    with st.spinner("Loading Excel files…"):
        df1, hdr1, err1 = load_excel_pandas(b1, region1, int(header_hint_1))
        df2, hdr2, err2 = load_excel_pandas(b2, region2, int(header_hint_2))

    # Merge with lower memory if possible
    use_polars = (engine == "Auto (recommended)") and HAS_POLARS
    if use_polars:
        with st.spinner("Converting to Polars + merging…"):
            p1 = pandas_to_polars(df1)
            p2 = pandas_to_polars(df2)
            merged = pl.concat([p1, p2], how="diagonal")  # diagonal allows mismatched columns safely
        st.session_state.merged_kind = "polars"
    else:
        with st.spinner("Merging…"):
            merged = pd.concat([df1, df2], ignore_index=True, sort=False)
        st.session_state.merged_kind = "pandas"

    # Store only merged + lightweight meta
    st.session_state.merged = merged
    st.session_state.meta = {
        "rows_1": len(df1),
        "rows_2": len(df2),
        "hdr1": hdr1,
        "hdr2": hdr2,
        "err1": err1,
        "err2": err2,
        "cols_1": list(df1.columns),
        "cols_2": list(df2.columns),
    }

# Show results
if st.session_state.merged is not None:
    meta = st.session_state.meta
    merged = st.session_state.merged
    kind = st.session_state.merged_kind

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"{region1} rows", f"{meta['rows_1']:,}")
    m2.metric(f"{region2} rows", f"{meta['rows_2']:,}")
    m3.metric("Header row file 1", f"{meta['hdr1']} (Excel row {meta['hdr1']+1})")
    m4.metric("Header row file 2", f"{meta['hdr2']} (Excel row {meta['hdr2']+1})")

    if meta["err1"]:
        st.info(f"File 1 used fallback header detection: {meta['err1']}")
    if meta["err2"]:
        st.info(f"File 2 used fallback header detection: {meta['err2']}")

    # Column diffs (cheap)
    only_1 = sorted(set(meta["cols_1"]) - set(meta["cols_2"]))
    only_2 = sorted(set(meta["cols_2"]) - set(meta["cols_1"]))
    if only_1 or only_2:
        st.warning("Column differences detected. Merge is still safe; missing values become blank.")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.write(f"Only in **{region1}** ({len(only_1)}):")
            st.code("\n".join(only_1) if only_1 else "(none)")
        with cc2:
            st.write(f"Only in **{region2}** ({len(only_2)}):")
            st.code("\n".join(only_2) if only_2 else "(none)")
    else:
        st.success("Columns match exactly.")

    # Avoid heavy rendering: show only a small sample
    st.subheader("Sample rows (first 25)")
    if kind == "polars":
        st.dataframe(merged.head(25).to_pandas(), use_container_width=True)
        total_rows = merged.height
        total_cols = merged.width
    else:
        st.dataframe(merged.head(25), use_container_width=True)
        total_rows = len(merged)
        total_cols = merged.shape[1]

    st.caption(f"Merged size: {total_rows:,} rows × {total_cols:,} columns")

    st.divider()

    st.subheader("Download")
    out_format = st.selectbox(
        "Format",
        ["CSV (recommended)", "Parquet (fast + compact)", "Excel (.xlsx) (slow, may fail)"]
    )

    if out_format.startswith("CSV"):
        with st.spinner("Building CSV…"):
            if kind == "polars":
                data = merged_to_csv_bytes_polars(merged)
            else:
                data = merged_to_csv_bytes_pandas(merged)
        st.download_button("⬇️ Download merged CSV", data=data, file_name="merged_reviews.csv", mime="text/csv")

    elif out_format.startswith("Parquet"):
        with st.spinner("Building Parquet…"):
            if kind == "polars":
                data = merged_to_parquet_bytes_polars(merged)
            else:
                data = merged_to_parquet_bytes_pandas(merged)
        st.download_button(
            "⬇️ Download merged Parquet",
            data=data,
            file_name="merged_reviews.parquet",
            mime="application/octet-stream"
        )

    else:
        st.warning(
            "XLSX export is memory-heavy for ~400k rows, and can crash depending on machine/RAM. "
            "Use CSV/Parquet if possible."
        )
        proceed = st.checkbox("I understand; try XLSX anyway")
        if proceed:
            with st.spinner("Building XLSX…"):
                # Convert to pandas only at the last moment
                if kind == "polars":
                    tmp = merged.to_pandas()
                else:
                    tmp = merged

                # Trim long text cells only if needed
                for col in ["Review Text", "Review Title"]:
                    if col in tmp.columns:
                        s = tmp[col].astype("string")
                        mask = s.str.len() > EXCEL_CELL_LIMIT
                        if mask.any():
                            tmp.loc[mask, col] = s[mask].str.slice(0, EXCEL_CELL_LIMIT)

                out = BytesIO()
                # openpyxl tends to be slower but stable; xlsxwriter faster but memory-heavy sometimes
                with pd.ExcelWriter(out, engine="openpyxl") as writer:
                    tmp.to_excel(writer, index=False, sheet_name="Merged")

            st.download_button(
                "⬇️ Download merged XLSX",
                data=out.getvalue(),
                file_name="merged_reviews.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
else:
    st.info("Upload both files and click **Load & Merge**.")

st.caption("Tip: CSV or Parquet is strongly recommended for large Bazaarvoice exports.")



