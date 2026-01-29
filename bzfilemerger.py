import streamlit as st
import pandas as pd
import openpyxl
from io import BytesIO

st.set_page_config(page_title="Bazaarvoice Review Merger", layout="wide")
st.title("🧩 Bazaarvoice Excel Merger (EU/UK + USA)")

REQUIRED_HEADERS = ("Review ID", "Review Submission Date")
EXCEL_CELL_LIMIT = 32767  # Excel hard limit per cell

def detect_header_row(file_bytes: bytes, max_scan_rows: int = 60) -> int:
    """Return 0-based header row index for pandas, by scanning the first rows."""
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    for excel_row_idx, row in enumerate(
        ws.iter_rows(min_row=1, max_row=max_scan_rows, values_only=True),
        start=1
    ):
        cells = [str(c).strip() for c in row if c is not None]
        if not cells:
            continue
        lower = {c.lower() for c in cells}
        if all(h.lower() in lower for h in REQUIRED_HEADERS):
            return excel_row_idx - 1  # convert Excel (1-based) to pandas header row (0-based)

    raise ValueError(
        f"Could not find a header row containing: {REQUIRED_HEADERS}. "
        "This doesn't look like a standard Bazaarvoice export (or the format changed)."
    )

@st.cache_data(show_spinner=False)
def load_bv_excel(file_bytes: bytes, region_label: str):
    header_row = detect_header_row(file_bytes)

    # Read using the detected header row
    dtype_map = {
        "Review ID": "string",
        "Product ID": "string",
        "Reviewer ID": "string",
        "EAN": "string",
        "UPC": "string",
    }

    df = pd.read_excel(
        BytesIO(file_bytes),
        header=header_row,
        engine="openpyxl",
        dtype=dtype_map
    )

    # Clean column names
    df.columns = [str(c).strip() for c in df.columns]

    # Drop totally empty rows
    df = df.dropna(how="all")

    # Add region label
    df.insert(0, "Region", region_label)

    # Parse dates if present
    if "Review Submission Date" in df.columns:
        df["Review Submission Date"] = pd.to_datetime(df["Review Submission Date"], errors="coerce")

    return df, header_row

def make_excel_safe(df: pd.DataFrame):
    """Trim known long-text columns to avoid Excel cell length failures."""
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
    output = BytesIO()
    try:
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Merged")
    except Exception:
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Merged")
    return output.getvalue()

# Uploaders
col1, col2 = st.columns(2)
with col1:
    f1 = st.file_uploader("Upload EU/UK Excel export", type=["xlsx"], key="eu")
    region1 = st.text_input("Label for file 1", value="EU/UK")
with col2:
    f2 = st.file_uploader("Upload USA Excel export", type=["xlsx"], key="us")
    region2 = st.text_input("Label for file 2", value="USA")

if f1 and f2:
    b1 = f1.getvalue()
    b2 = f2.getvalue()

    with st.spinner("Loading files (auto-detecting headers)…"):
        df1, hdr1 = load_bv_excel(b1, region1)
        df2, hdr2 = load_bv_excel(b2, region2)

    st.success("Loaded both files successfully.")

    # Basic diagnostics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("EU/UK rows", f"{len(df1):,}")
    c2.metric("USA rows", f"{len(df2):,}")
    c3.metric("EU/UK header row", f"Excel row {hdr1+1}")
    c4.metric("USA header row", f"Excel row {hdr2+1}")

    # Column diffs
    cols1 = set(df1.columns)
    cols2 = set(df2.columns)
    only_1 = sorted(list(cols1 - cols2))
    only_2 = sorted(list(cols2 - cols1))

    if only_1 or only_2:
        st.warning("Column differences detected (merge will still work; missing values become blank).")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            st.write(f"Columns only in **{region1}** ({len(only_1)}):")
            st.code("\n".join(only_1) if only_1 else "(none)")
        with dcol2:
            st.write(f"Columns only in **{region2}** ({len(only_2)}):")
            st.code("\n".join(only_2) if only_2 else "(none)")
    else:
        st.info("Columns match exactly across both files.")

    # Merge
    merged = pd.concat([df1, df2], ignore_index=True, sort=False)

    # Dedupe option
    dedupe = st.checkbox("Drop duplicate Review ID rows (keep first)", value=False)
    if dedupe and "Review ID" in merged.columns:
        before = len(merged)
        merged = merged.drop_duplicates(subset=["Review ID"], keep="first")
        st.info(f"Deduped by Review ID: removed {before - len(merged):,} rows.")

    st.subheader("Merged preview")
    st.dataframe(merged.head(50), use_container_width=True)
    st.caption(f"Total merged rows: {len(merged):,} | Total columns: {merged.shape[1]}")

    # Output format
    out_format = st.selectbox("Download format", ["CSV (recommended for big files)", "Excel (.xlsx)"])

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
    st.info("Upload both files to load, validate, merge, and download.")

