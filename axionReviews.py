# app.py
import re
import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Star Walk Formatter", layout="wide")

STARWALK_SHEET_NAME = "Star Walk scrubbed verbatims"

DEFAULT_STARWALK_COLUMNS = [
    "Source", "Model (SKU)", "Seeded", "Country", "New Review", "Review Date",
    "Verbatim Id", "Verbatim", "Star Rating", "Review count per detractor",
    "Symptom 1", "Symptom 2", "Symptom 3", "Symptom 4", "Symptom 5",
    "Symptom 6", "Symptom 7", "Symptom 8", "Symptom 9", "Symptom 10",
    "Symptom 11", "Symptom 12", "Symptom 13", "Symptom 14", "Symptom 15",
    "Symptom 16", "Symptom 17", "Symptom 18", "Symptom 19", "Symptom 20",
    "Hair Type", "Unnamed: 31", "Unnamed: 32"
]

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())

def best_match(target: str, candidates: list[str]) -> str | None:
    """Very lightweight fuzzy-ish matcher: exact normalized match > substring match."""
    t = _norm(target)
    if not candidates:
        return None
    norm_map = {c: _norm(c) for c in candidates}
    # exact
    for c, nc in norm_map.items():
        if nc == t:
            return c
    # substring
    for c, nc in norm_map.items():
        if t and (t in nc or nc in t):
            return c
    return None

@st.cache_data(show_spinner=False)
def read_table(file_bytes: bytes, filename: str, sheet: str | None = None) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(file_bytes))
    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
    raise ValueError("Unsupported file type. Please upload a CSV or Excel file.")

def parse_tags(value, split_regex: str) -> list[str]:
    """
    Parse a cell that may contain:
      - NaN / empty
      - a single tag string
      - delimited tags (e.g., "A; B | C")
      - a python-like list string: "['A','B']"
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []

    s = str(value).strip()
    if not s:
        return []

    # Try to handle list-like strings
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("(") and s.endswith(")")):
        inner = s[1:-1].strip()
        if inner:
            parts = [p.strip().strip("'\"") for p in inner.split(",")]
            parts = [p for p in parts if p]
            return dedupe_preserve(parts)

    # Split by configured regex
    parts = [p.strip() for p in re.split(split_regex, s) if p and p.strip()]
    return dedupe_preserve(parts)

def dedupe_preserve(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        x2 = str(x).strip()
        if not x2:
            continue
        if x2 not in seen:
            seen.add(x2)
            out.append(x2)
    return out

def collect_from_columns(row: pd.Series, cols: list[str], split_regex: str) -> list[str]:
    all_tags: list[str] = []
    for c in cols:
        if c in row.index:
            all_tags.extend(parse_tags(row[c], split_regex))
    return dedupe_preserve(all_tags)

def build_output(
    src: pd.DataFrame,
    out_cols: list[str],
    field_map: dict[str, str | None],
    l2_detractor_cols: list[str],
    l2_delighter_cols: list[str],
    split_regex: str,
) -> pd.DataFrame:
    out = pd.DataFrame(columns=out_cols)

    # Copy mapped fields
    for out_field, in_field in field_map.items():
        if out_field in out_cols:
            if in_field and in_field in src.columns:
                out[out_field] = src[in_field]
            else:
                out[out_field] = pd.NA

    # Ensure symptom columns exist
    for i in range(1, 21):
        col = f"Symptom {i}"
        if col not in out.columns:
            out[col] = pd.NA

    detr_cols_out = [f"Symptom {i}" for i in range(1, 11)]
    deli_cols_out = [f"Symptom {i}" for i in range(11, 21)]

    detr_counts = []
    for idx, row in src.iterrows():
        detr_tags = collect_from_columns(row, l2_detractor_cols, split_regex)[:10]
        deli_tags = collect_from_columns(row, l2_delighter_cols, split_regex)[:10]

        # Fill detractors into Symptom 1-10
        for j, c in enumerate(detr_cols_out):
            out.at[idx, c] = detr_tags[j] if j < len(detr_tags) else pd.NA

        # Fill delighters into Symptom 11-20
        for j, c in enumerate(deli_cols_out):
            out.at[idx, c] = deli_tags[j] if j < len(deli_tags) else pd.NA

        detr_counts.append(len(detr_tags))

    # Review count per detractor (common weighting): 1 / (# detractors), else 0 if none
    if "Review count per detractor" in out.columns:
        out["Review count per detractor"] = [
            (1.0 / c) if c > 0 else 0.0 for c in detr_counts
        ]

    return out

def to_excel_bytes(df_out: pd.DataFrame, sheet_name: str = STARWALK_SHEET_NAME) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_out.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()

st.title("Star Walk Scrubbed Verbatims Converter")

st.markdown(
    """
Upload your **raw website reviews** file and (optionally) a **Star Walk template** workbook.
This app will output an Excel sheet formatted like **Star Walk scrubbed verbatims** and will place:

- **Level 2 Detractors → Symptom 1–10**
- **Level 2 Delighters → Symptom 11–20**
"""
)

colA, colB = st.columns(2)

with colA:
    src_file = st.file_uploader("Upload raw website reviews (CSV/XLSX)", type=["csv", "xlsx", "xlsm", "xls"])
with colB:
    template_file = st.file_uploader("Optional: Upload Star Walk template workbook (XLSX)", type=["xlsx", "xlsm", "xls"])

if not src_file:
    st.stop()

src_bytes = src_file.getvalue()

# If Excel, let user choose sheet
src_sheet = None
if src_file.name.lower().endswith((".xlsx", ".xlsm", ".xls")):
    try:
        xl = pd.ExcelFile(io.BytesIO(src_bytes))
        sheets = xl.sheet_names
        src_sheet = st.selectbox("Select source sheet", sheets, index=0)
    except Exception:
        src_sheet = None

src_df = read_table(src_bytes, src_file.name, sheet=src_sheet)
st.subheader("Source Preview")
st.dataframe(src_df.head(25), use_container_width=True)

# Determine output columns from template or default
out_cols = DEFAULT_STARWALK_COLUMNS
template_sheet = None
if template_file:
    tbytes = template_file.getvalue()
    try:
        txl = pd.ExcelFile(io.BytesIO(tbytes))
        tsheets = txl.sheet_names
        default_idx = tsheets.index(STARWALK_SHEET_NAME) if STARWALK_SHEET_NAME in tsheets else 0
        template_sheet = st.selectbox("Select template sheet to mirror columns", tsheets, index=default_idx)
        tmp_df = pd.read_excel(io.BytesIO(tbytes), sheet_name=template_sheet, nrows=1)
        out_cols = list(tmp_df.columns)
    except Exception:
        st.warning("Could not read template columns; using default Star Walk columns.")
        out_cols = DEFAULT_STARWALK_COLUMNS

st.subheader("Mapping")

all_src_cols = list(src_df.columns)

# Auto-suggest common fields
suggest = {
    "Source": best_match("Source", all_src_cols),
    "Model (SKU)": best_match("Model (SKU)", all_src_cols) or best_match("SKU", all_src_cols) or best_match("Model", all_src_cols),
    "Seeded": best_match("Seeded", all_src_cols),
    "Country": best_match("Country", all_src_cols),
    "New Review": best_match("New Review", all_src_cols),
    "Review Date": best_match("Review Date", all_src_cols) or best_match("Date", all_src_cols),
    "Verbatim Id": best_match("Verbatim Id", all_src_cols) or best_match("Review ID", all_src_cols) or best_match("Id", all_src_cols),
    "Verbatim": best_match("Verbatim", all_src_cols) or best_match("Review", all_src_cols) or best_match("Review Text", all_src_cols),
    "Star Rating": best_match("Star Rating", all_src_cols) or best_match("Rating", all_src_cols) or best_match("Stars", all_src_cols),
    "Hair Type": best_match("Hair Type", all_src_cols),
}

# Let the user map fields (optional)
field_map = {}
with st.expander("Map core fields (optional but recommended)", expanded=True):
    left, right = st.columns(2)
    core_fields = ["Source", "Model (SKU)", "Seeded", "Country", "New Review", "Review Date", "Verbatim Id", "Verbatim", "Star Rating", "Hair Type"]
    for i, f in enumerate(core_fields):
        with (left if i < (len(core_fields)+1)//2 else right):
            field_map[f] = st.selectbox(
                f"Output '{f}' comes from source column:",
                options=[None] + all_src_cols,
                index=([None] + all_src_cols).index(suggest.get(f)) if suggest.get(f) in all_src_cols else 0,
                key=f"map_{f}",
            )

with st.expander("Select Level 2 detractor/delighter columns (these populate Symptom 1–20)", expanded=True):
    # Heuristics for L2 columns
    l2_det_guess = [c for c in all_src_cols if "l2" in _norm(c) and "detr" in _norm(c)]
    l2_del_guess = [c for c in all_src_cols if "l2" in _norm(c) and ("delight" in _norm(c) or "promot" in _norm(c))]

    l2_detractor_cols = st.multiselect(
        "Source column(s) that contain **Level 2 Detractors**",
        options=all_src_cols,
        default=l2_det_guess,
    )
    l2_delighter_cols = st.multiselect(
        "Source column(s) that contain **Level 2 Delighters**",
        options=all_src_cols,
        default=l2_del_guess,
    )

    split_choice = st.selectbox(
        "How are multiple tags separated inside a cell?",
        options=[
            "Semicolon / Comma / Pipe / Newline (recommended)",
            "Semicolon only ;",
            "Comma only ,",
            "Pipe only |",
            "Newline only",
        ],
        index=0,
    )

    # Regex used to split tags
    if split_choice == "Semicolon / Comma / Pipe / Newline (recommended)":
        split_regex = r"[;\|\n,]+"
    elif split_choice == "Semicolon only ;":
        split_regex = r"[;]+"
    elif split_choice == "Comma only ,":
        split_regex = r"[,]+"
    elif split_choice == "Pipe only |":
        split_regex = r"[\|]+"
    else:
        split_regex = r"[\n]+"

st.subheader("Build & Download")
build = st.button("Convert to Star Walk scrubbed verbatims")

if build:
    if not l2_detractor_cols and not l2_delighter_cols:
        st.warning("You did not select any L2 detractor/delighter columns. Symptoms will be blank unless you select them.")
    out_df = build_output(
        src=src_df.reset_index(drop=True),
        out_cols=out_cols,
        field_map=field_map,
        l2_detractor_cols=l2_detractor_cols,
        l2_delighter_cols=l2_delighter_cols,
        split_regex=split_regex,
    )

    st.success("Conversion complete.")
    c1, c2, c3 = st.columns(3)
    detr_counts = out_df[[f"Symptom {i}" for i in range(1, 11)]].notna().sum(axis=1)
    deli_counts = out_df[[f"Symptom {i}" for i in range(11, 21)]].notna().sum(axis=1)
    with c1:
        st.metric("Rows", len(out_df))
    with c2:
        st.metric("Avg detractors (L2) / row", round(float(detr_counts.mean()), 2))
    with c3:
        st.metric("Avg delighters (L2) / row", round(float(deli_counts.mean()), 2))

    st.dataframe(out_df.head(50), use_container_width=True)

    xbytes = to_excel_bytes(out_df, sheet_name=STARWALK_SHEET_NAME)
    st.download_button(
        "Download Excel (Star Walk scrubbed verbatims format)",
        data=xbytes,
        file_name="starwalk_scrubbed_verbatims_converted.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
