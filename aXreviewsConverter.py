import io
import json
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


# -----------------------------
# Helpers
# -----------------------------
def safe_get(d: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def join_list(x: Any, sep: str = " | ") -> Any:
    if x is None:
        return None
    if isinstance(x, list):
        # remove Nones/empties
        vals = [str(v).strip() for v in x if v is not None and str(v).strip() != ""]
        return sep.join(vals) if vals else None
    return x


def parse_iso_date(x: Any) -> Optional[date]:
    if not x:
        return None
    try:
        # Handles "...Z" as UTC.
        ts = pd.to_datetime(x, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)  # camelCase -> words
    stem = re.sub(r"\s+", " ", stem).strip()

    # Make first token uppercase if it contains digits (e.g., hd6000 -> HD6000)
    tokens = stem.split(" ")
    if tokens:
        if any(ch.isdigit() for ch in tokens[0]):
            tokens[0] = tokens[0].upper()
    return " ".join(tokens)


# -----------------------------
# JSON -> DataFrames
# -----------------------------
REVIEWS_BASE_COLS: List[Tuple[str, Any]] = [
    ("Record ID", lambda r: r.get("_id")),
    ("Opened Timestamp", lambda r: parse_iso_date(r.get("openedTimestamp"))),
    ("Rating (num)", lambda r: safe_get(r, ["clientAttributes", "Rating (num)"])),
    ("Retailer", lambda r: safe_get(r, ["clientAttributes", "Retailer"])),
    ("Retailer Rating", lambda r: safe_get(r, ["clientAttributes", "Retailer Rating"])),
    ("Model", lambda r: safe_get(r, ["clientAttributes", "Model"])),
    ("Seeded Reviews", lambda r: safe_get(r, ["clientAttributes", "Seeded Reviews"])),
    ("Syndicated/Seeded Reviews", lambda r: safe_get(r, ["clientAttributes", "Syndicated/Seeded Reviews"])),
    ("Location", lambda r: safe_get(r, ["clientAttributes", "Location"])),
    ("Post Link", lambda r: safe_get(r, ["clientAttributes", "Post Link"])),
    ("Title", lambda r: safe_get(r, ["freeText", "Title"])),
    ("Review", lambda r: safe_get(r, ["freeText", "Review"])),
]

REVIEWS_EXTRA_COLS: List[Tuple[str, Any]] = [
    ("Satisfaction Score", lambda r: safe_get(r, ["customAttributes", "Satisfaction Score"])),
    ("Key Review Sentiment_Reviews", lambda r: join_list(safe_get(r, ["customAttributes", "Key Review Sentiment_Reviews"]))),
    ("Key Review Sentiment Type_Reviews", lambda r: join_list(safe_get(r, ["customAttributes", "Key Review Sentiment Type_Reviews"]))),
    ("Dominant Customer Journey Step", lambda r: join_list(safe_get(r, ["customAttributes", "Dominant Customer Journey Step"]))),
    ("Trigger Point_Product", lambda r: join_list(safe_get(r, ["customAttributes", "Trigger Point_Product"]))),
    ("L2 Delighter Component", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Component"]))),
    ("L2 Delighter Condition", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Condition"]))),
    ("L2 Delighter Mode", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Mode"]))),
    ("L3 Non Product Detractors", lambda r: join_list(safe_get(r, ["customAttributes", "L3 Non Product Detractors"]))),
    ("Product_Symptom Component", lambda r: join_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Component"]))),
    ("Product_Symptom Conditions", lambda r: join_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Conditions"]))),
    ("Product_Symptom Mode", lambda r: join_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Mode"]))),
    ("Product Name", lambda r: safe_get(r, ["clientAttributes", "Product Name"])),
    ("Product Category", lambda r: safe_get(r, ["clientAttributes", "Product Category"])),
    ("Base SKU", lambda r: safe_get(r, ["clientAttributes", "Base SKU"])),
    ("Brand", lambda r: safe_get(r, ["clientAttributes", "Brand"])),
    ("Company", lambda r: safe_get(r, ["clientAttributes", "Company"])),
    ("Factory Name", lambda r: safe_get(r, ["clientAttributes", "Factory Name"])),
    ("Translation", lambda r: safe_get(r, ["clientAttributes", "Translation"])),
    ("Event ID", lambda r: r.get("eventId")),
    ("Event Type", lambda r: r.get("eventType")),
    ("Is Linked", lambda r: r.get("isLinked")),
    ("Workspace ID", lambda r: safe_get(r, ["clientAttributes", "Workspace ID"])),
]


def build_reviews_df(records: List[Dict[str, Any]], include_extra: bool = True) -> pd.DataFrame:
    cols = REVIEWS_BASE_COLS + (REVIEWS_EXTRA_COLS if include_extra else [])
    rows = []
    for r in records:
        row = {name: fn(r) for name, fn in cols}
        rows.append(row)
    df = pd.DataFrame(rows)

    # Normalize numeric rating
    if "Rating (num)" in df.columns:
        df["Rating (num)"] = pd.to_numeric(df["Rating (num)"], errors="coerce")

    return df


def build_symptoms_df(records: List[Dict[str, Any]], include_blank_row_when_missing: bool = True) -> pd.DataFrame:
    out_rows: List[Dict[str, Any]] = []

    for r in records:
        rid = r.get("_id")
        opened = parse_iso_date(r.get("openedTimestamp"))
        rating = safe_get(r, ["clientAttributes", "Rating (num)"])
        retailer = safe_get(r, ["clientAttributes", "Retailer"])
        model = safe_get(r, ["clientAttributes", "Model"])

        comps = as_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Component"]))
        conds = as_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Conditions"]))
        modes = as_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Mode"]))

        max_len = max(len(comps), len(conds), len(modes), 0)

        if max_len == 0 and include_blank_row_when_missing:
            out_rows.append({
                "Record ID": rid,
                "Opened Timestamp": opened,
                "Rating": rating,
                "Retailer": retailer,
                "Model": model,
                "Symptom Index": None,
                "Symptom Component": None,
                "Symptom Condition": None,
                "Symptom Mode": None,
            })
            continue

        for i in range(max_len):
            comp = comps[i] if i < len(comps) else None
            cond = conds[i] if i < len(conds) else None
            mode = modes[i] if i < len(modes) else None

            # Fill mode if missing
            if mode in (None, "", "-", "—"):
                if comp and cond:
                    mode = f"{comp} - {cond}"
                elif cond:
                    mode = f"- {cond}"
                elif comp:
                    mode = f"{comp} -"
                else:
                    mode = "-"

            out_rows.append({
                "Record ID": rid,
                "Opened Timestamp": opened,
                "Rating": rating,
                "Retailer": retailer,
                "Model": model,
                "Symptom Index": i + 1,
                "Symptom Component": comp,
                "Symptom Condition": cond,
                "Symptom Mode": mode,
            })

    df = pd.DataFrame(out_rows)
    df["Rating"] = pd.to_numeric(df["Rating"], errors="coerce")
    return df


# -----------------------------
# Summary Data
# -----------------------------
def build_summary_tables(reviews_df: pd.DataFrame, symptoms_df: pd.DataFrame, top_n: int = 10):
    total_reviews = int(len(reviews_df))

    # Date range
    date_min = reviews_df["Opened Timestamp"].min() if "Opened Timestamp" in reviews_df.columns else None
    date_max = reviews_df["Opened Timestamp"].max() if "Opened Timestamp" in reviews_df.columns else None
    date_range_str = ""
    if pd.notna(date_min) and pd.notna(date_max) and date_min and date_max:
        date_range_str = f"{date_min} to {date_max}"

    # Avg rating
    avg_rating = None
    if "Rating (num)" in reviews_df.columns:
        avg_rating = float(pd.to_numeric(reviews_df["Rating (num)"], errors="coerce").mean())

    # Rating distribution (1-5)
    rating_counts = (
        reviews_df["Rating (num)"]
        .dropna()
        .astype(int)
        .value_counts()
        .reindex([5, 4, 3, 2, 1], fill_value=0)
    )
    rating_dist = pd.DataFrame({
        "Rating": rating_counts.index.astype(int),
        "Count": rating_counts.values.astype(int),
        "Share": (rating_counts.values / total_reviews) if total_reviews else 0,
    })

    # Top retailers
    retailer_counts = (
        reviews_df.get("Retailer", pd.Series(dtype=str))
        .fillna("(blank)")
        .value_counts()
        .head(top_n)
    )
    top_retailers = pd.DataFrame({
        "Retailer": retailer_counts.index,
        "Count": retailer_counts.values.astype(int),
        "Share": (retailer_counts.values / total_reviews) if total_reviews else 0,
    })

    # Top symptom conditions (exclude blanks)
    cond_series = symptoms_df.get("Symptom Condition", pd.Series(dtype=str)).fillna("")
    cond_series = cond_series[cond_series.astype(str).str.strip() != ""]
    symptom_rows = int(len(cond_series))
    cond_counts = cond_series.value_counts().head(top_n)
    top_conditions = pd.DataFrame({
        "Condition": cond_counts.index,
        "Count": cond_counts.values.astype(int),
        "Share (of symptom rows)": (cond_counts.values / symptom_rows) if symptom_rows else 0,
    })

    return {
        "total_reviews": total_reviews,
        "date_range_str": date_range_str,
        "avg_rating": avg_rating,
        "rating_dist": rating_dist,
        "top_retailers": top_retailers,
        "top_conditions": top_conditions,
        "symptom_rows": symptom_rows,
    }


# -----------------------------
# Excel Writer (openpyxl)
# -----------------------------
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")  # dark blue
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(color="1F4E79", bold=True, size=14)
SECTION_FONT = Font(color="1F4E79", bold=False)

def write_df_to_sheet(ws, df: pd.DataFrame):
    ws.append(list(df.columns))
    for row in df.itertuples(index=False, name=None):
        ws.append(list(row))


def add_excel_table(ws, df: pd.DataFrame, table_name: str, style_name: str = "TableStyleMedium9"):
    nrows = len(df) + 1
    ncols = len(df.columns)
    if nrows <= 1 or ncols == 0:
        return

    ref = f"A1:{get_column_letter(ncols)}{nrows}"
    tab = Table(displayName=table_name, ref=ref)

    style = TableStyleInfo(
        name=style_name,
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    tab.tableStyleInfo = style
    ws.add_table(tab)

    ws.freeze_panes = "A2"


def set_col_widths(ws, df: pd.DataFrame, widths: Dict[str, float]):
    for i, col in enumerate(df.columns, start=1):
        if col in widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[col]


def set_date_format(ws, df: pd.DataFrame, date_cols: List[str]):
    for col_name in date_cols:
        if col_name not in df.columns:
            continue
        col_idx = list(df.columns).index(col_name) + 1
        for r in range(2, len(df) + 2):
            cell = ws.cell(row=r, column=col_idx)
            if isinstance(cell.value, (datetime, date)):
                cell.number_format = "yyyy-mm-dd"


def apply_hyperlinks(ws, df: pd.DataFrame, url_cols: List[str]):
    for col_name in url_cols:
        if col_name not in df.columns:
            continue
        col_idx = list(df.columns).index(col_name) + 1
        for r in range(2, len(df) + 2):
            cell = ws.cell(row=r, column=col_idx)
            val = cell.value
            if isinstance(val, str) and val.startswith("http"):
                cell.hyperlink = val
                cell.style = "Hyperlink"


def wrap_cells(ws, df: pd.DataFrame, wrap_cols: List[str]):
    align = Alignment(wrap_text=True, vertical="top")
    for col_name in wrap_cols:
        if col_name not in df.columns:
            continue
        col_idx = list(df.columns).index(col_name) + 1
        for r in range(1, len(df) + 2):
            ws.cell(row=r, column=col_idx).alignment = align


def build_workbook(
    dataset_title: str,
    reviews_df: pd.DataFrame,
    symptoms_df: pd.DataFrame,
    summary: Dict[str, Any],
    wrap_long_text: bool = False,
) -> bytes:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # --- Summary sheet ---
    ws = wb.create_sheet("Summary")

    ws["A1"] = f"{dataset_title} — Summary"
    ws["A1"].font = TITLE_FONT

    ws["A3"] = "Dataset"
    ws["A3"].font = Font(bold=True)

    ws["A4"] = "Total Reviews"
    ws["B4"] = summary["total_reviews"]

    ws["A5"] = "Date Range (Opened)"
    ws["B5"] = summary["date_range_str"]

    ws["A6"] = "Average Rating"
    if summary["avg_rating"] is not None:
        ws["B6"] = round(summary["avg_rating"], 3)

    # Section headers
    ws["A8"] = "Rating Distribution"
    ws["A8"].font = SECTION_FONT
    ws["D8"] = "Top Retailers"
    ws["D8"].font = SECTION_FONT

    # Rating dist table A9:C?
    rd = summary["rating_dist"].copy()
    # Ensure 3 columns
    rd_cols = ["Rating", "Count", "Share"]
    rd = rd[rd_cols]
    start_row = 9
    start_col = 1  # A
    # header
    for j, col in enumerate(rd_cols):
        cell = ws.cell(row=start_row, column=start_col + j, value=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    # body
    for i, row in enumerate(rd.itertuples(index=False, name=None), start=1):
        for j, val in enumerate(row):
            c = ws.cell(row=start_row + i, column=start_col + j, value=val)
            if j == 2:  # Share
                c.number_format = "0.0%"

    # Top retailers table D9:F?
    tr = summary["top_retailers"].copy()
    tr_cols = ["Retailer", "Count", "Share"]
    tr = tr[tr_cols]
    start_row = 9
    start_col = 4  # D
    for j, col in enumerate(tr_cols):
        cell = ws.cell(row=start_row, column=start_col + j, value=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for i, row in enumerate(tr.itertuples(index=False, name=None), start=1):
        for j, val in enumerate(row):
            c = ws.cell(row=start_row + i, column=start_col + j, value=val)
            if j == 2:
                c.number_format = "0.0%"

    # Top conditions section
    ws["A22"] = "Top Symptom Conditions"
    ws["A22"].font = SECTION_FONT
    ws["E22"] = "Notes"
    ws["E22"].font = SECTION_FONT

    ws["E23"] = (
        "Symptoms sheet is exploded from\n"
        "customAttributes.taxonomies lists\n"
        "(Component / Condition / Mode)."
    )
    ws["E23"].alignment = Alignment(wrap_text=True, vertical="top")

    tc = summary["top_conditions"].copy()
    tc_cols = ["Condition", "Count", "Share (of symptom rows)"]
    tc = tc[tc_cols]
    start_row = 23
    start_col = 1  # A
    for j, col in enumerate(tc_cols):
        cell = ws.cell(row=start_row, column=start_col + j, value=col if j < 2 else "Share")
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        if j == 2:
            # Make share column narrow like the screenshot
            ws.column_dimensions[get_column_letter(start_col + j)].width = 6
            cell.alignment = Alignment(horizontal="center", vertical="center", textRotation=90)
    for i, row in enumerate(tc.itertuples(index=False, name=None), start=1):
        for j, val in enumerate(row):
            c = ws.cell(row=start_row + i, column=start_col + j, value=val)
            if j == 2:
                c.number_format = "0.0%"

    # Column widths (Summary)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 35
    ws.column_dimensions["F"].width = 10

    # --- Reviews sheet ---
    ws_r = wb.create_sheet("Reviews")
    write_df_to_sheet(ws_r, reviews_df)

    add_excel_table(ws_r, reviews_df, table_name="ReviewsTable")
    apply_hyperlinks(ws_r, reviews_df, url_cols=["Post Link"])
    set_date_format(ws_r, reviews_df, date_cols=["Opened Timestamp"])

    review_widths = {
        "Record ID": 34,
        "Opened Timestamp": 16,
        "Rating (num)": 11,
        "Retailer": 14,
        "Retailer Rating": 14,
        "Model": 12,
        "Seeded Reviews": 15,
        "Syndicated/Seeded Reviews": 22,
        "Location": 10,
        "Post Link": 55,
        "Title": 45,
        "Review": 85,
    }
    set_col_widths(ws_r, reviews_df, review_widths)

    if wrap_long_text:
        wrap_cells(ws_r, reviews_df, wrap_cols=["Title", "Review", "Translation"])

    # --- Symptoms sheet ---
    ws_s = wb.create_sheet("Symptoms")
    write_df_to_sheet(ws_s, symptoms_df)

    add_excel_table(ws_s, symptoms_df, table_name="SymptomsTable")
    set_date_format(ws_s, symptoms_df, date_cols=["Opened Timestamp"])

    symptom_widths = {
        "Record ID": 34,
        "Opened Timestamp": 16,
        "Rating": 8,
        "Retailer": 14,
        "Model": 12,
        "Symptom Index": 12,
        "Symptom Component": 22,
        "Symptom Condition": 38,
        "Symptom Mode": 38,
    }
    set_col_widths(ws_s, symptoms_df, symptom_widths)

    # Save to bytes
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="JSON → Clean Excel (Reviews)", layout="wide")

st.title("JSON → Clean Excel Converter (Reviews format)")
st.caption("Upload a JSON that contains a top-level `results` list of review records, then download a formatted Excel workbook (Summary / Reviews / Symptoms).")

uploaded = st.file_uploader("Upload JSON", type=["json"])

col1, col2, col3 = st.columns(3)
with col1:
    include_extra = st.checkbox("Include extra tag/taxonomy columns", value=True)
with col2:
    wrap_long_text = st.checkbox("Wrap Title/Review text", value=False)
with col3:
    include_blank_symptom_rows = st.checkbox("Keep 1 blank symptom row when no symptoms exist", value=True)

top_n = st.slider("Top N (Retailers & Symptom Conditions) in Summary", min_value=5, max_value=25, value=10, step=1)

if uploaded is not None:
    try:
        raw = json.loads(uploaded.getvalue().decode("utf-8"))
        if isinstance(raw, dict) and "results" in raw and isinstance(raw["results"], list):
            records = raw["results"]
        elif isinstance(raw, list):
            records = raw
        else:
            st.error("Unrecognized JSON shape. Expected a dict with `results: []` or a list of records.")
            st.stop()

        dataset_title = title_from_filename(uploaded.name)
        dataset_title = st.text_input("Dataset title (used in Summary sheet title)", value=dataset_title)

        reviews_df = build_reviews_df(records, include_extra=include_extra)
        symptoms_df = build_symptoms_df(records, include_blank_row_when_missing=include_blank_symptom_rows)
        summary = build_summary_tables(reviews_df, symptoms_df, top_n=top_n)

        st.success(f"Loaded {len(records):,} records.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Reviews", summary["total_reviews"])
        if summary["avg_rating"] is not None:
            c2.metric("Average Rating", f"{summary['avg_rating']:.3f}")
        c3.metric("Symptom rows (non-blank conditions)", summary["symptom_rows"])

        st.subheader("Preview — Reviews")
        st.dataframe(reviews_df.head(50), use_container_width=True)

        st.subheader("Preview — Symptoms")
        st.dataframe(symptoms_df.head(50), use_container_width=True)

        excel_bytes = build_workbook(
            dataset_title=f"{dataset_title} Reviews" if "review" not in dataset_title.lower() else dataset_title,
            reviews_df=reviews_df,
            symptoms_df=symptoms_df,
            summary=summary,
            wrap_long_text=wrap_long_text,
        )

        out_name = f"{Path(uploaded.name).stem}_clean.xlsx"
        st.download_button(
            "Download Excel",
            data=excel_bytes,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        st.exception(e)
