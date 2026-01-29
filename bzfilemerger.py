import streamlit as st
import pandas as pd
import openpyxl
from io import BytesIO

st.set_page_config(page_title="Bazaarvoice Review Merger (Fast)", layout="wide")
st.title("⚡ Bazaarvoice Excel Merger (Fast)")

REQUIRED_HEADERS = ("Review ID", "Review Submission Date")
EXCEL_CELL_LIMIT = 32767  # Excel hard limit per cell (characters)

# ---------- Helpers ----------

def find_header_row_from_preview(preview: pd.DataFrame) -> int:
    """
    preview is a dataframe read with header=None.
    We scan rows to find one containing REQUIRED_HEADERS (case-insensitive).
    Returns 0-based row index suitable for pd.read_excel(header=...).
    """
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

def detect_header_row_fast(file_bytes: bytes, max_preview_rows: int = 25) -> int:
    """
    Fast header detection:
    - Open workbook ONCE via pd.ExcelFile
    - Parse small preview with header=None
    - Find header row
    """
    xls = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")
    preview = xls.parse(header=None, nrows=max_preview_rows)
    return find_header_row_from_preview(preview)

def make_excel_safe(df: pd.DataFrame):
    """
    Excel can fail if any cell text exceeds 32,767 chars.
    Trim known long-text columns if needed.
    Returns (df, trimmed_info dict).
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
    return df, trimmed

def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """
    Write df to XLSX in memory. Prefer xlsxwriter if available, fallback to openpyxl.
    """
    output = BytesIO()
    try:
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Merged")
    except Exception:
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Merged")
    return output.getvalue()

@st.cache_data(show_spinner=False)
def load_bv_excel_once(file_bytes: bytes, region_label: str, header_hint: int | None, usecols: list[str] | None):
    """
    Optimized loader:
    - Open the workbook ONCE (pd.ExcelFile)
    - Try header_hint first (fast path)
    - If hint fails, detect header from a small preview, then parse
    """
    xls = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")

    # Choose header row (fast path)
    header_row = header_hint
    df = None
    last_err = None

    def _parse(header_idx: int):
        return xls.parse(
            header=header_idx,
            dtype={
                "Review ID": "string",
                "Product ID": "string",
                "Reviewer ID": "string",
                "EAN": "string",
                "UPC": "string",
            },
            usecols=usecols
        )

    if header_row is not None:
        try:
            df = _parse(header_row)
            # Quick validation: must have required headers
            cols_lower = {str(c).strip().lower() for c in df.columns}
            if not all(h.lower() in cols_lower for h in REQUIRED_HEADERS):
                raise ValueError("Header hint parsed, but required headers not found.")
        except Exception as e:
            last_err = e
            df = None

    # Fallback: detect header row from small preview
    if df is None:
        preview = xls.parse(header=None, nrows=25)
        header_row = find_header_row_from_preview(preview)
        df = _parse(header_row)

    # Normalize columns + clean
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    # Add region label up front
    df.insert(0, "Region", region_label)

    # Avoid expensive conversions unless needed elsewhere; keep raw.
    return df, header_row, (str(last_err) if last_err else None)

def compute_col_diffs(df1: pd.DataFrame, df2: pd.DataFrame):
    cols1 = set(df1.columns)
    cols2 = set(df2.columns)
    only_1 = sorted(list(cols1 - cols2))
    only_2 = sorted(list(cols2 - cols1))
    return only_1, only_2

# ---------- UI ----------

st.write(
    "This is optimized to avoid re-reading large Excel files on every UI change: "
    "loads happen only when you click **Load & Merge**."
)

c1, c2 = st.columns(2)

with c1:
    f1 = st.file_uploader("Upload EU/UK Excel export", type=["xlsx"], key="eu")
    region1 = st.text_input("Label for file 1", value="EU/UK", key="r1")
    header_hint_1 = st.number_input(
        "Header row hint (0-based) for file 1",
        min_value=0, max_value=200, value=5, step=1,
        help="For your EU/UK export this is typically 5 (Excel row 6).",
        key="h1"
    )

with c2:
    f2 = st.file_uploader("Upload USA Excel export", type=["xlsx"], key="us")
    region2 = st.text_input("Label for file 2", value="USA", key="r2")
    header_hint_2 = st.number_input(
        "Header row hint (0-based) for file 2",
        min_value=0, max_value=200, value=6, step=1,
        help="For your USA export this is typically 6 (Excel row 7).",
        key="h2"
    )

st.divider()

# Optional: choose columns (fast if you restrict)
usecols_mode = st.radio(
    "Columns to load",
    ["All columns (slower)", "Select columns (faster)"],
    horizontal=True
)

usecols = None
selected_cols = None

# We can only offer a column picker after loading at least one file.
# So for now, we collect selection after load; alternatively user can type a comma list.
cols_text = None
if usecols_mode == "Select columns (faster)":
    cols_text = st.text_input(
        "Enter columns to load (comma-separated). Leave blank to load all.",
        value="Review ID, Review Submission Date, Product ID, Rating, Review Title, Review Text",
        help="Exact column names. If a column doesn't exist in one file, it will be skipped for that file."
    )

def parse_usecols(cols_text_value: str | None):
    if not cols_text_value:
        return None
    cols = [c.strip() for c in cols_text_value.split(",") if c.strip()]
    return cols if cols else None

usecols = parse_usecols(cols_text)

# Session-state to avoid re-running expensive work
if "merged_df" not in st.session_state:
    st.session_state.merged_df = None
    st.session_state.df1 = None
    st.session_state.df2 = None
    st.session_state.hdrs = None

load_btn = st.button(
    "🚀 Load & Merge",
    type="primary",
    disabled=not (f1 and f2),
    help="Loads the Excel files, validates headers, and merges (append/union)."
)

if load_btn:
    b1 = f1.getvalue()
    b2 = f2.getvalue()

    with st.spinner("Loading EU/UK file…"):
        df1, hdr1, hint_err1 = load_bv_excel_once(b1, region1, int(header_hint_1), usecols)

    with st.spinner("Loading USA file…"):
        df2, hdr2, hint_err2 = load_bv_excel_once(b2, region2, int(header_hint_2), usecols)

    st.session_state.df1 = df1
    st.session_state.df2 = df2
    st.session_state.hdrs = (hdr1, hdr2, hint_err1, hint_err2)

    # Merge is cheap
    merged = pd.concat([df1, df2], ignore_index=True, sort=False)
    st.session_state.merged_df = merged

# ---------- Results ----------

if st.session_state.merged_df is not None:
    df1 = st.session_state.df1
    df2 = st.session_state.df2
    merged = st.session_state.merged_df
    hdr1, hdr2, hint_err1, hint_err2 = st.session_state.hdrs

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"{region1} rows", f"{len(df1):,}")
    m2.metric(f"{region2} rows", f"{len(df2):,}")
    m3.metric("Header row (file 1)", f"0-based {hdr1} (Excel row {hdr1+1})")
    m4.metric("Header row (file 2)", f"0-based {hdr2} (Excel row {hdr2+1})")

    if hint_err1:
        st.info(f"File 1: header hint fallback was used. Reason: {hint_err1}")
    if hint_err2:
        st.info(f"File 2: header hint fallback was used. Reason: {hint_err2}")

    only_1, only_2 = compute_col_diffs(df1, df2)
    if only_1 or only_2:
        st.warning("Column differences detected (merge is still safe; missing values become blank).")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.write(f"Only in **{region1}** ({len(only_1)}):")
            st.code("\n".join(only_1) if only_1 else "(none)")
        with cc2:
            st.write(f"Only in **{region2}** ({len(only_2)}):")
            st.code("\n".join(only_2) if only_2 else "(none)")
    else:
        st.success("Columns match exactly across both files.")

    # Optional dedupe (can be expensive but still OK)
    dedupe = st.checkbox("Drop duplicate Review ID rows (keep first)", value=False)
    if dedupe:
        if "Review ID" in merged.columns:
            before = len(merged)
            merged = merged.drop_duplicates(subset=["Review ID"], keep="first")
            st.info(f"Deduped by Review ID: removed {before - len(merged):,} rows.")
        else:
            st.warning("No 'Review ID' column found; cannot dedupe by Review ID.")

    st.subheader("Merged preview")
    st.dataframe(merged.head(50), use_container_width=True)
    st.caption(f"Total merged rows: {len(merged):,} | Total columns: {merged.shape[1]}")

    st.divider()

    # Downloads: CSV is fastest
    out_format = st.selectbox("Download format", ["CSV (fastest)", "Excel (.xlsx)"])
    if out_format.startswith("CSV"):
        csv_bytes = merged.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download merged CSV",
            data=csv_bytes,
            file_name="merged_reviews.csv",
            mime="text/csv",
        )
    else:
        excel_safe = st.checkbox("Make Excel-safe (trim very long text cells if needed)", value=True)
        tmp = merged.copy()
        trimmed_info = {}
        if excel_safe:
            tmp, trimmed_info = make_excel_safe(tmp)
            if trimmed_info:
                st.warning(
                    "Some cells exceeded Excel’s 32,767 character limit and were trimmed:\n"
                    + "\n".join([f"- {k}: {v:,} rows" for k, v in trimmed_info.items()])
                )

        with st.spinner("Building Excel file…"):
            xlsx_bytes = df_to_excel_bytes(tmp)

        st.download_button(
            "⬇️ Download merged Excel",
            data=xlsx_bytes,
            file_name="merged_reviews.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

else:
    st.info("Upload both files, then click **Load & Merge**.")


