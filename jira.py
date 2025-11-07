# Build an improved Streamlit app per the user's requests:
# - Works with this (and varied) file formats via a schema-mapping step
# - File upload moved to the main page (not the sidebar)
# - Robust sheet autodetection + override
# - Better UX: quick chips, weekly heatmap, top-N bars, save/load views, performance tweaks
# Saves to /mnt/data/streamlit_app_jira_v2.py

from pathlib import Path

code = r'''
# streamlit_app_jira_v2.py
# Best-in-class Jira Issues explorer with flexible schema mapping and main-page upload
import io
import json
import difflib
from datetime import timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# -------------------- Page & Styles --------------------
st.set_page_config(page_title="Jira Issues — Flexible Explorer",
                   page_icon="🧭",
                   layout="wide",
                   initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      .kpi {
        border-radius: 14px;
        padding: 14px;
        border: 1px solid rgba(0,0,0,0.06);
        box-shadow: 0 6px 18px rgba(0,0,0,0.06);
        background: white;
      }
      .kpi h3 { margin: 0; font-size: 13px; color: #666; }
      .kpi p { margin: 2px 0 0 0; font-size: 24px; font-weight: 700; }

      .scrollable-table {
        overflow-y: auto;
        max-height: 420px;
        border: 1px solid #eee;
        padding: 10px;
        border-radius: 12px;
        background-color: #fafafa;
      }
      .desc-card {
        background: #fff;
        padding: 18px;
        margin: 12px 0;
        border-left: 5px solid #4CAF50;
        border-radius: 10px;
        box-shadow: 0 4px 10px rgba(0,0,0,.06);
      }
      .delta-pos { color: #0a0; font-weight: 700; }
      .delta-neg { color: #d00; font-weight: 700; }
      .chip {
        display: inline-block; padding: 6px 10px; margin: 0 6px 6px 0;
        border-radius: 999px; background: #f0f0f0; cursor: pointer; font-size: 12px;
      }
      .chip-active { background: #4CAF50; color: white; }
      .stPlotlyChart {
        background: white; border-radius: 12px; padding: 8px;
      }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🧭 Jira Issues — Flexible Explorer")
st.caption("Main-page upload • Flexible schema mapping • Fast filters • Trend charts • Deltas • Heatmaps")

# -------------------- Canonical schema --------------------
REQUIRED_FIELDS = [
    "Date Identified", "SKU(s)", "Base SKU", "Region",
    "Symptom", "Disposition", "Description", "Serial Number",
]

# Synonym dictionary to guess mappings (lowercased matching)
SYNONYMS: Dict[str, List[str]] = {
    "Date Identified": ["date identified","identified","date","created","created date","created_on","created at","opened","report date"],
    "SKU(s)": ["sku(s)","sku","skus","product sku","product","model sku","model"],
    "Base SKU": ["base sku","base","base_sku","base sku(s)","base product","family sku","platform"],
    "Region": ["region","market","country","geo","territory","locale"],
    "Symptom": ["symptom","issue","category","failure mode","problem","defect","tag"],
    "Disposition": ["disposition","status","resolution","outcome","result","action"],
    "Description": ["description","details","summary","comments","issue description","text","notes"],
    "Serial Number": ["serial number","sn","s/n","serial","unit sn","serial_no","serial no","serialnumber"],
}

# -------------------- Cache helpers --------------------
@st.cache_data(show_spinner=False)
def read_excel_sheet_bytes(content: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    buf = io.BytesIO(content)
    return pd.read_excel(buf, sheet_name=sheet)  # openpyxl engine under the hood

@st.cache_data(show_spinner=False)
def read_csv_bytes(content: bytes) -> pd.DataFrame:
    buf = io.BytesIO(content)
    return pd.read_csv(buf)

# -------------------- Utility functions --------------------
def score_column_for_field(col: str, field: str) -> float:
    """Score how well a column name matches the target field using synonyms and fuzzy ratio."""
    col_l = col.strip().lower()
    cands = [field.lower()] + SYNONYMS.get(field, [])
    scores = [difflib.SequenceMatcher(None, col_l, cand).ratio() for cand in cands]
    # extra credit if column contains the candidate as substring
    for cand in cands:
        if cand in col_l:
            scores.append(1.0 - 0.02 * abs(len(col_l) - len(cand)))  # prefer close length
    return max(scores) if scores else 0.0

def guess_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    """Guess a mapping from canonical fields to actual columns."""
    mapping: Dict[str, Optional[str]] = {}
    for field in REQUIRED_FIELDS:
        best_col = None
        best_score = 0.0
        for col in columns:
            s = score_column_for_field(col, field)
            if s > best_score:
                best_col = col
                best_score = s
        mapping[field] = best_col if (best_col and best_score >= 0.55) else None
    return mapping

def best_sheet_for_schema(xls_bytes: bytes, sheet_names: List[str]) -> Optional[str]:
    """Pick the sheet with the highest number of good mapping guesses."""
    best_sheet = None
    best_hits = -1
    for name in sheet_names:
        try:
            df = read_excel_sheet_bytes(xls_bytes, sheet=name)
        except Exception:
            continue
        mapping = guess_mapping(list(map(str, df.columns)))
        hits = sum(1 for k, v in mapping.items() if v is not None)
        if hits > best_hits:
            best_hits = hits
            best_sheet = name
    return best_sheet

def apply_mapping(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """Rename columns to canonical names based on chosen mapping and coerce types."""
    reverse = {v: k for k, v in mapping.items() if v}
    out = df.rename(columns=reverse).copy()
    # Ensure all required columns exist, even if empty
    for col in REQUIRED_FIELDS:
        if col not in out.columns:
            out[col] = pd.NA
    # Type coercions
    out["Date Identified"] = pd.to_datetime(out["Date Identified"], errors="coerce")
    for c in ("SKU(s)", "Base SKU", "Region", "Symptom", "Disposition", "Description", "Serial Number"):
        out[c] = out[c].astype("string").str.strip().str.upper() if c in ("SKU(s)", "Base SKU") else out[c].astype("string")
    return out

def build_filter_mask(
    df: pd.DataFrame,
    sku=None,
    base=None,
    region=None,
    symptom=None,
    disp=None,
    tsf_only=False,
    search_text: str = "",
) -> pd.Series:
    mask = pd.Series(True, index=df.index, dtype=bool)
    def _apply_in(col, values):
        nonlocal mask
        if col in df.columns and values and "ALL" not in values:
            mask &= df[col].isin(values)
    _apply_in("SKU(s)", sku)
    _apply_in("Base SKU", base)
    _apply_in("Region", region)
    _apply_in("Symptom", symptom)
    _apply_in("Disposition", disp)
    if tsf_only and "Disposition" in df.columns:
        mask &= df["Disposition"].fillna("").str.contains(r"_ts_failed|_replaced|tsf", case=False, regex=True)
    if search_text:
        s = str(search_text).lower()
        mask &= df["Description"].fillna("").str.lower().str.contains(s, na=False)
    return mask

def combine_top_n(series: pd.Series, n: int = 10, label: str = "Other") -> pd.Series:
    s = series.fillna("(NA)")
    counts = s.value_counts(dropna=False)
    if len(counts) <= n:
        return s
    top = set(counts.nlargest(n).index.tolist())
    return s.apply(lambda x: x if x in top else label)

def kpi(label: str, value):
    st.markdown(f"<div class='kpi'><h3>{label}</h3><p>{value}</p></div>", unsafe_allow_html=True)

def safe_bar(df: pd.DataFrame, x: str, y: str, color: str, title: str):
    if df.empty:
        st.info("No data to plot for current filters/time window.")
        return
    fig = px.bar(df, x=x, y=y, color=color, title=title, labels={y: "Count", x: "Date"}, template="plotly_white")
    fig.update_layout(barmode="stack", margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)

def weekly_heatmap(df: pd.DataFrame, value_col: str, label: str):
    if df.empty:
        st.info("No data for heatmap.")
        return
    d = df.copy()
    d = d.dropna(subset=["Date Identified"])
    if d.empty:
        st.info("No dates to plot.")
        return
    d["Week"] = d["Date Identified"].dt.to_period("W").apply(lambda r: r.start_time)
    mat = d.groupby(["Week", label]).size().reset_index(name=value_col)
    # Keep top N labels for clarity
    top_labels = mat.groupby(label)[value_col].sum().nlargest(12).index
    mat[label] = mat[label].apply(lambda x: x if x in top_labels else "Other")
    mat = mat.groupby(["Week", label])[value_col].sum().reset_index()
    pv = mat.pivot(index=label, columns="Week", values=value_col).fillna(0)
    fig = px.imshow(pv, aspect="auto", color_continuous_scale="Blues", origin="lower", title=f"Weekly Heatmap — {label}")
    st.plotly_chart(fig, use_container_width=True)

def topn_bars(df: pd.DataFrame, col: str, n: int = 15, title: Optional[str] = None):
    if df.empty:
        st.info("No data for Top-N.")
        return
    vc = df[col].value_counts().nlargest(n).reset_index()
    vc.columns = [col, "Count"]
    fig = px.bar(vc, x="Count", y=col, orientation="h", title=title or f"Top {n}: {col}", template="plotly_white")
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)

def serialize_view(state: dict) -> bytes:
    return json.dumps(state, default=str, indent=2).encode("utf-8")

def parse_view(b: bytes) -> dict:
    return json.loads(b.decode("utf-8"))

# -------------------- Main-page file upload --------------------
st.markdown("### 1) Upload file")
col_u1, col_u2 = st.columns([3,1])

with col_u1:
    uploaded = st.file_uploader("Upload an Excel (.xlsx) or CSV (.csv) export", type=["xlsx","csv"], key="main_uploader")

with col_u2:
    DEFAULT_SAMPLE = "/mnt/data/data (31).xlsx"
    use_sample = st.checkbox("Use included sample", value=False if uploaded else Path(DEFAULT_SAMPLE).exists())
    sample_info = st.caption("Uses a local sample if present.")

file_content = None
file_kind = None
if uploaded is not None:
    file_content = uploaded.getvalue()
    file_kind = "csv" if uploaded.name.lower().endswith(".csv") else "xlsx"
elif use_sample and Path(DEFAULT_SAMPLE).exists():
    file_content = Path(DEFAULT_SAMPLE).read_bytes()
    file_kind = "xlsx"

if file_content is None:
    st.info("Upload a file (or choose the sample) to continue.")
    st.stop()

# -------------------- Sheet detection & selection (Excel only) --------------------
df_raw = None
selected_sheet = None
if file_kind == "xlsx":
    # List sheets
    try:
        xls = pd.ExcelFile(io.BytesIO(file_content))
        sheets = xls.sheet_names
    except Exception as e:
        st.error(f"Could not read Excel file: {e}")
        st.stop()
    # Guess best sheet
    candidate = best_sheet_for_schema(file_content, sheets) or (sheets[0] if sheets else None)
    selected_sheet = st.selectbox("Select sheet", options=sheets, index=sheets.index(candidate) if candidate in sheets else 0)
    try:
        df_raw = read_excel_sheet_bytes(file_content, sheet=selected_sheet)
    except Exception as e:
        st.error(f"Failed to read sheet '{selected_sheet}': {e}")
        st.stop()
else:
    try:
        df_raw = read_csv_bytes(file_content)
    except Exception as e:
        st.error(f"Failed to read CSV: {e}")
        st.stop()

# -------------------- Schema mapping UI --------------------
st.markdown("### 2) Map columns (auto-guessed, editable)")
columns = list(map(str, df_raw.columns))
guessed = guess_mapping(columns)

mapping: Dict[str, Optional[str]] = {}
mcols = st.columns(2)
for i, field in enumerate(REQUIRED_FIELDS):
    default = guessed.get(field)
    with (mcols[i % 2] if len(REQUIRED_FIELDS) > 1 else st):
        mapping[field] = st.selectbox(f"{field}", options=[None] + columns, index=(columns.index(default)+1) if default in columns else 0, key=f"map_{field}")

if not all(mapping.get(f) for f in REQUIRED_FIELDS):
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    st.warning(f"Select a column for all required fields to proceed. Missing: {', '.join(missing)}")
    st.stop()

# -------------------- Apply mapping & standardize --------------------
df = apply_mapping(df_raw, mapping)

# Sidebar controls AFTER data ready
with st.sidebar:
    st.header("Filters")
    sku_filter = st.multiselect("SKU(s)", options=["ALL"] + sorted(df["SKU(s)"].dropna().unique().tolist()), default=["ALL"])
    base_filter = st.multiselect("Base SKU", options=["ALL"] + sorted(df["Base SKU"].dropna().unique().tolist()), default=["ALL"])
    region_filter = st.multiselect("Region", options=["ALL"] + sorted(df["Region"].dropna().unique().tolist()), default=["ALL"])
    symptom_filter = st.multiselect("Symptom", options=["ALL"] + sorted(df["Symptom"].dropna().unique().tolist()), default=["ALL"])
    disposition_filter = st.multiselect("Disposition", options=["ALL"] + sorted(df["Disposition"].dropna().unique().tolist()), default=["ALL"])

    st.markdown("**Quick chips**")
    # simple chips: top 10 symptoms/dispositions for fast include
    top_sym = df["Symptom"].value_counts().nlargest(6).index.tolist()
    top_disp = df["Disposition"].value_counts().nlargest(6).index.tolist()
    chip_sym = st.multiselect("Top symptom chips", options=top_sym, default=[])
    chip_disp = st.multiselect("Top disposition chips", options=top_disp, default=[])

    tsf_only = st.checkbox("TSF only (disposition contains _ts_failed / _replaced / tsf)", value=False)
    combine_other = st.checkbox("Combine lesser categories into 'Other' (charts only)", value=False)
    search_text = st.text_input("Search 'Description' contains…", value="")

    st.markdown("---")
    st.header("Date Windows")
    date_range_graph = st.selectbox("Chart range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3)
    table_days = st.number_input("Delta window (table): days", min_value=7, value=30, step=1)

    st.markdown("---")
    st.header("Views")
    view_to_load = st.file_uploader("Load saved view (.json)", type=["json"], key="view_loader")
    if view_to_load is not None:
        try:
            view_cfg = parse_view(view_to_load.getvalue())
            # Best-effort populate simple fields
            sku_filter = view_cfg.get("sku_filter", sku_filter)
            base_filter = view_cfg.get("base_filter", base_filter)
            region_filter = view_cfg.get("region_filter", region_filter)
            symptom_filter = view_cfg.get("symptom_filter", symptom_filter)
            disposition_filter = view_cfg.get("disposition_filter", disposition_filter)
            tsf_only = view_cfg.get("tsf_only", tsf_only)
            combine_other = view_cfg.get("combine_other", combine_other)
            search_text = view_cfg.get("search_text", search_text)
            date_range_graph = view_cfg.get("date_range_graph", date_range_graph)
            table_days = int(view_cfg.get("table_days", table_days))
            st.success("View loaded. (Filters updated above)")
        except Exception as e:
            st.error(f"Failed to load view: {e}")

# Apply quick chips (union with existing selections)
if chip_sym:
    symptom_filter = list(set((symptom_filter or []) + chip_sym))
if chip_disp:
    disposition_filter = list(set((disposition_filter or []) + chip_disp))

# -------------------- Time windows --------------------
now = pd.Timestamp.now()
if date_range_graph == "Last Week":
    start_graph = now - timedelta(days=7)
    period_label_graph = "Last 7 Days"
elif date_range_graph == "Last Month":
    start_graph = now - timedelta(days=30)
    period_label_graph = "Last 30 Days"
elif date_range_graph == "Last Year":
    start_graph = now - timedelta(days=365)
    period_label_graph = "Last 365 Days"
else:
    start_graph = df["Date Identified"].min()
    period_label_graph = "All Time"

table_days = int(table_days)
start_table = now - timedelta(days=table_days)
prev_start_table = start_table - timedelta(days=table_days)

# -------------------- Filtering --------------------
base_mask = build_filter_mask(
    df,
    sku=sku_filter, base=base_filter, region=region_filter,
    symptom=symptom_filter, disp=disposition_filter,
    tsf_only=tsf_only, search_text=search_text
)
df_filtered = df.loc[base_mask].copy()

# -------------------- KPIs --------------------
st.markdown("### 3) Overview")
c1, c2, c3, c4, c5 = st.columns(5)
with c1: kpi("Rows (filtered)", f"{len(df_filtered):,}")
with c2: kpi("Unique SKUs", df_filtered["SKU(s)"].nunique())
with c3: kpi("Base SKUs", df_filtered["Base SKU"].nunique())
with c4: kpi("Regions", df_filtered["Region"].nunique())
with c5: kpi("Symptoms", df_filtered["Symptom"].nunique())

# Preview
with st.expander("🔍 Preview filtered data"):
    st.dataframe(df_filtered.head(1000), use_container_width=True, height=320)

# -------------------- Trends --------------------
st.markdown("### 4) Trends")
agg_choice = st.selectbox("Aggregate by", ["Day", "Week", "Month"], index=1)
freq_map = {"Day": "D", "Week": "W", "Month": "M"}
freq = freq_map[agg_choice]

# Symptom trends
sym_df = df_filtered.copy()
if pd.notna(start_graph):
    sym_df = sym_df[sym_df["Date Identified"] >= start_graph]
if combine_other:
    sym_df = sym_df.copy()
    sym_df["Symptom"] = combine_top_n(sym_df["Symptom"], n=10, label="Other")
sym_trend = (sym_df
             .groupby([pd.Grouper(key="Date Identified", freq=freq), "Symptom"], dropna=False)
             .size().reset_index(name="Count"))
safe_bar(sym_trend, "Date Identified", "Count", "Symptom", f"Symptom Trends Over Time ({period_label_graph})")

# Disposition trends
disp_df = df_filtered.copy()
if pd.notna(start_graph):
    disp_df = disp_df[disp_df["Date Identified"] >= start_graph]
if combine_other:
    disp_df = disp_df.copy()
    disp_df["Disposition"] = combine_top_n(disp_df["Disposition"], n=10, label="Other")
disp_trend = (disp_df
              .groupby([pd.Grouper(key="Date Identified", freq=freq), "Disposition"], dropna=False)
              .size().reset_index(name="Count"))
safe_bar(disp_trend, "Date Identified", "Count", "Disposition", f"Disposition Trends Over Time ({period_label_graph})")

# Weekly heatmaps
st.markdown("#### Heatmaps")
col_h1, col_h2 = st.columns(2)
with col_h1:
    weekly_heatmap(df_filtered, "Count", "Symptom")
with col_h2:
    weekly_heatmap(df_filtered, "Count", "Disposition")

# Top-N bars
st.markdown("#### Top-N")
col_t1, col_t2 = st.columns(2)
with col_t1:
    topn_bars(df_filtered, "Symptom", n=15, title="Top 15 Symptoms")
with col_t2:
    topn_bars(df_filtered, "Disposition", n=15, title="Top 15 Dispositions")

# -------------------- Ranked Symptoms with Δ --------------------
st.markdown("### 5) Ranked Symptoms (Δ over equal windows)")
cur = df_filtered[df_filtered["Date Identified"] >= start_table]
prev = df_filtered[(df_filtered["Date Identified"] < start_table) & (df_filtered["Date Identified"] >= prev_start_table)]

sym_all = df_filtered["Symptom"].value_counts().reset_index()
sym_all.columns = ["Symptom", "Total"]

cur_counts = cur["Symptom"].value_counts()
prev_counts = prev["Symptom"].value_counts()

rank = sym_all.copy()
rank[f"Last {table_days}d"] = rank["Symptom"].map(cur_counts).fillna(0).astype(int)
rank[f"Prev {table_days}d"] = rank["Symptom"].map(prev_counts).fillna(0).astype(int)
rank["Delta"] = rank[f"Last {table_days}d"] - rank[f"Prev {table_days}d"]
rank["Delta %"] = np.where(rank[f"Prev {table_days}d"] > 0,
                           (rank["Delta"] / rank[f"Prev {table_days}d"] * 100.0).round(2),
                           np.nan)
rank = rank.sort_values(["Total", f"Last {table_days}d"], ascending=False).head(10)

def _fmt_delta(v):
    try:
        v = int(v)
    except Exception:
        return "—"
    if v > 0: return f"<span class='delta-pos'>+{v}</span>"
    if v < 0: return f"<span class='delta-neg'>{v}</span>"
    return "0"

def _fmt_pct(v):
    if pd.isna(v): return "—"
    try: v = float(v)
    except Exception: return "—"
    if v > 0: return f"<span class='delta-pos'>+{v:.2f}%</span>"
    if v < 0: return f"<span class='delta-neg'>{v:.2f}%</span>"
    return "—"

rank_disp = rank.copy()
rank_disp["Delta"] = rank_disp["Delta"].apply(_fmt_delta)
rank_disp["Delta %"] = rank_disp["Delta %"].apply(_fmt_pct)

st.markdown(f"<div class='scrollable-table'>{rank_disp.to_html(escape=False, index=False)}</div>", unsafe_allow_html=True)

# -------------------- Descriptions (Paginated) --------------------
st.markdown("### 6) Descriptions")
descs = (
    df_filtered[["Description","SKU(s)","Base SKU","Region","Disposition","Symptom","Date Identified","Serial Number"]]
    .dropna(subset=["Description"])
    .sort_values("Date Identified", ascending=False)
    .reset_index(drop=True)
)
total = len(descs)
items_per = st.selectbox("Items per page", [10,25,50,100], index=0)
pages = max(1, (total + items_per - 1)//items_per)
page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
start = (page-1)*items_per; end = min(start+items_per, total)

if total == 0:
    st.warning("No descriptions match your filters.")
else:
    for _, row in descs.iloc[start:end].iterrows():
        d = row["Date Identified"]
        dstr = d.strftime("%Y-%m-%d") if pd.notnull(d) else "N/A"
        st.markdown(
            f"""
            <div class='desc-card'>
              <h4 style="margin:0 0 8px 0;">Issue Details</h4>
              <div><strong>SKU:</strong> {row['SKU(s)']} &nbsp;&nbsp;
                   <strong>Base:</strong> {row['Base SKU']} &nbsp;&nbsp;
                   <strong>Region:</strong> {row['Region']}</div>
              <div><strong>Disposition:</strong> {row['Disposition']} &nbsp;&nbsp;
                   <strong>Symptom:</strong> {row['Symptom']} &nbsp;&nbsp;
                   <strong>Date:</strong> {dstr} &nbsp;&nbsp;
                   <strong>Serial:</strong> {row['Serial Number']}</div>
              <div style="margin-top:8px;"><strong>Description:</strong> {row['Description']}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    st.caption(f"Showing {start+1}–{end} of {total}")

# -------------------- Save/Download --------------------
st.sidebar.download_button(
    label="⬇️ Download filtered CSV",
    data=df_filtered.to_csv(index=False).encode("utf-8"),
    file_name="jira_filtered.csv",
    mime="text/csv",
)
# Save current view (filters)
view_state = {
    "sku_filter": sku_filter,
    "base_filter": base_filter,
    "region_filter": region_filter,
    "symptom_filter": symptom_filter,
    "disposition_filter": disposition_filter,
    "tsf_only": tsf_only,
    "combine_other": combine_other,
    "search_text": search_text,
    "date_range_graph": date_range_graph,
    "table_days": table_days,
}
st.sidebar.download_button(
    label="💾 Save current view (.json)",
    data=serialize_view(view_state),
    file_name="jira_view.json",
    mime="application/json",
)

st.success("Ready. Upload, map columns if needed, then slice & explore.")
'''

out_path = Path('/mnt/data/streamlit_app_jira_v2.py')
out_path.write_text(code, encoding='utf-8')
str(out_path)

