# streamlit_app_jira.py
import io
from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


# -------------------- Page & Styles --------------------
st.set_page_config(page_title="Jira Issues — Best-in-Class Explorer",
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

      .stPlotlyChart {
        background: white;
        border-radius: 12px;
        padding: 8px;
      }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🧭 Jira Issues — Best-in-Class Explorer")
st.caption("Fast filters • Clear deltas • Trend charts • Pivots")


# -------------------- Constants --------------------
REQUIRED_COLS = [
    "Date Identified", "SKU(s)", "Base SKU", "Region",
    "Symptom", "Disposition", "Description", "Serial Number",
]


# -------------------- Helpers --------------------
@st.cache_data(show_spinner=False)
def read_excel_bytes(b: bytes, sheet_name: str) -> pd.DataFrame:
    """Read an Excel sheet from raw bytes with caching."""
    buf = io.BytesIO(b)
    # Let pandas choose engine (openpyxl installed via requirements)
    return pd.read_excel(buf, sheet_name=sheet_name)


def standardize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize types and normalize key text columns."""
    out = df.copy()
    if "Date Identified" in out.columns:
        out["Date Identified"] = pd.to_datetime(out["Date Identified"], errors="coerce")
    for c in ("SKU(s)", "Base SKU"):
        if c in out.columns:
            out[c] = (
                out[c]
                .astype(str, errors="ignore")
                .str.upper()
                .str.strip()
            )
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
    """Build a boolean mask with all selected filters."""
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
        mask &= df["Disposition"].astype(str).str.contains(
            r"_ts_failed|_replaced", case=False, na=False
        )

    if search_text:
        s = str(search_text).lower()
        col = "Description"
        if col in df.columns:
            mask &= (
                df[col].fillna("").astype(str).str.lower().str.contains(s, na=False)
            )

    return mask


def combine_top_n(series: pd.Series, n: int = 10, label: str = "Other") -> pd.Series:
    """Collapse low-frequency categories to 'label' while keeping top N."""
    # Treat NaN as explicit category
    s = series.fillna("(NA)")
    counts = s.value_counts(dropna=False)
    if len(counts) <= n:
        return s
    top = set(counts.nlargest(n).index.tolist())
    return s.apply(lambda x: x if x in top else label)


def kpi(label: str, value: str | int | float):
    st.markdown(
        f"<div class='kpi'><h3>{label}</h3><p>{value}</p></div>",
        unsafe_allow_html=True,
    )


def safe_bar_chart(df: pd.DataFrame, x: str, y: str, color: str, title: str):
    if df.empty:
        st.info("No data to plot for current filters/time window.")
        return
    fig = px.bar(
        df,
        x=x,
        y=y,
        color=color,
        title=title,
        labels={y: "Count", x: "Date"},
        template="plotly_white",
    )
    fig.update_layout(barmode="stack", margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)


# -------------------- Sidebar: Load & Configure --------------------
with st.sidebar:
    st.header("1) Load Data")
    upl = st.file_uploader("Upload Excel with 'Your Jira Issues' sheet", type=["xlsx"])
    sheet_name = st.text_input(
        "Sheet name", value="Your Jira Issues", help="Exact name of the Jira export tab."
    )

    st.header("2) Filters")
    st.caption("Tip: set date windows separately for charts & the delta table.")
    # Placeholders (populated after load)
    sku_filter = st.multiselect("SKU(s)", options=["ALL"], default=["ALL"])
    base_filter = st.multiselect("Base SKU", options=["ALL"], default=["ALL"])
    region_filter = st.multiselect("Region", options=["ALL"], default=["ALL"])
    symptom_filter = st.multiselect("Symptom", options=["ALL"], default=["ALL"])
    disposition_filter = st.multiselect("Disposition", options=["ALL"], default=["ALL"])
    tsf_only = st.checkbox(
        "TSF only (disposition contains _ts_failed or _replaced)", value=False
    )
    combine_other = st.checkbox(
        "Combine lesser categories into 'Other' (charts only)", value=False
    )
    search_text = st.text_input("Search 'Description' contains…", value="")

    st.header("3) Date Windows")
    date_range_graph = st.selectbox(
        "Chart range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3
    )
    table_days = st.number_input(
        "Delta window (table): days", min_value=7, value=30, step=1
    )


# -------------------- Load & Validate --------------------
if not upl:
    st.info("Upload your Excel to begin.")
    st.stop()

try:
    df_raw = read_excel_bytes(upl.getvalue(), sheet_name)
except Exception as e:
    st.error(f"Failed to read sheet '{sheet_name}': {e}")
    st.stop()

missing = [c for c in REQUIRED_COLS if c not in df_raw.columns]
if missing:
    st.error(f"Missing required columns: {', '.join(missing)}")
    st.stop()

df = standardize_df(df_raw)

# Populate dynamic filter options
with st.sidebar:
    sku_filter = st.multiselect(
        "SKU(s)",
        options=["ALL"] + sorted(df["SKU(s)"].dropna().unique().tolist()),
        default=["ALL"],
    )
    base_filter = st.multiselect(
        "Base SKU",
        options=["ALL"] + sorted(df["Base SKU"].dropna().unique().tolist()),
        default=["ALL"],
    )
    region_filter = st.multiselect(
        "Region",
        options=["ALL"] + sorted(df["Region"].dropna().unique().tolist()),
        default=["ALL"],
    )
    symptom_filter = st.multiselect(
        "Symptom",
        options=["ALL"] + sorted(df["Symptom"].dropna().unique().tolist()),
        default=["ALL"],
    )
    disposition_filter = st.multiselect(
        "Disposition",
        options=["ALL"] + sorted(df["Disposition"].dropna().unique().tolist()),
        default=["ALL"],
    )


# -------------------- Time Windows --------------------
now = pd.Timestamp.now()

# Chart window
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
    # If the column is all NaT, start_graph becomes NaT; guard below when filtering.
    start_graph = df["Date Identified"].min()
    period_label_graph = "All Time"

# Delta windows for the ranked table
table_days = int(table_days)
start_table = now - timedelta(days=table_days)
prev_start_table = start_table - timedelta(days=table_days)


# -------------------- Filtering --------------------
base_mask = build_filter_mask(
    df,
    sku=sku_filter,
    base=base_filter,
    region=region_filter,
    symptom=symptom_filter,
    disp=disposition_filter,
    tsf_only=tsf_only,
    search_text=search_text,
)
df_filtered = df.loc[base_mask].copy()

# -------------------- KPIs --------------------
st.markdown("### Overview")
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    kpi("Rows (filtered)", f"{len(df_filtered):,}")
with c2:
    kpi("Unique SKUs", df_filtered["SKU(s)"].nunique())
with c3:
    kpi("Base SKUs", df_filtered["Base SKU"].nunique())
with c4:
    kpi("Regions", df_filtered["Region"].nunique())
with c5:
    kpi("Symptoms", df_filtered["Symptom"].nunique())


# -------------------- Charts --------------------
st.markdown("### Trends")

agg_choice = st.selectbox("Aggregate by", ["Day", "Week", "Month"], index=1)
freq_map = {"Day": "D", "Week": "W", "Month": "M"}
freq = freq_map[agg_choice]

# Symptom trends
sym_df = df_filtered.copy()
if pd.notna(start_graph):
    sym_df = sym_df[sym_df["Date Identified"] >= start_graph]
if combine_other:
    sym_df = sym_df.copy()  # avoid changing backing df
    sym_df["Symptom"] = combine_top_n(sym_df["Symptom"], n=10, label="Other")
sym_trend = (
    sym_df
    .groupby([pd.Grouper(key="Date Identified", freq=freq), "Symptom"], dropna=False)
    .size()
    .reset_index(name="Count")
)
safe_bar_chart(
    sym_trend,
    x="Date Identified",
    y="Count",
    color="Symptom",
    title=f"Symptom Trends Over Time ({period_label_graph})",
)

# Disposition trends
disp_df = df_filtered.copy()
if pd.notna(start_graph):
    disp_df = disp_df[disp_df["Date Identified"] >= start_graph]
if combine_other:
    disp_df = disp_df.copy()
    disp_df["Disposition"] = combine_top_n(disp_df["Disposition"], n=10, label="Other")
disp_trend = (
    disp_df
    .groupby([pd.Grouper(key="Date Identified", freq=freq), "Disposition"], dropna=False)
    .size()
    .reset_index(name="Count")
)
safe_bar_chart(
    disp_trend,
    x="Date Identified",
    y="Count",
    color="Disposition",
    title=f"Disposition Trends Over Time ({period_label_graph})",
)


# -------------------- Ranked Symptoms with Δ --------------------
st.markdown("### Ranked Symptoms (Δ over equal windows)")

cur = df_filtered[df_filtered["Date Identified"] >= start_table]
prev = df_filtered[
    (df_filtered["Date Identified"] < start_table)
    & (df_filtered["Date Identified"] >= prev_start_table)
]

sym_all = df_filtered["Symptom"].value_counts().reset_index()
sym_all.columns = ["Symptom", "Total"]

cur_counts = cur["Symptom"].value_counts()
prev_counts = prev["Symptom"].value_counts()

rank = sym_all.copy()
rank[f"Last {table_days}d"] = rank["Symptom"].map(cur_counts).fillna(0).astype(int)
rank[f"Prev {table_days}d"] = rank["Symptom"].map(prev_counts).fillna(0).astype(int)
rank["Delta"] = rank[f"Last {table_days}d"] - rank[f"Prev {table_days}d"]
rank["Delta %"] = np.where(
    rank[f"Prev {table_days}d"] > 0,
    (rank["Delta"] / rank[f"Prev {table_days}d"] * 100.0).round(2),
    np.nan,
)
rank = rank.sort_values(["Total", f"Last {table_days}d"], ascending=False).head(10)

def _fmt_delta(v):
    try:
        v = int(v)
    except Exception:
        return "—"
    if v > 0:
        return f"<span class='delta-pos'>+{v}</span>"
    if v < 0:
        return f"<span class='delta-neg'>{v}</span>"
    return "0"

def _fmt_pct(v):
    if pd.isna(v):
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    if v > 0:
        return f"<span class='delta-pos'>+{v:.2f}%</span>"
    if v < 0:
        return f"<span class='delta-neg'>{v:.2f}%</span>"
    return "—"

rank_disp = rank.copy()
rank_disp["Delta"] = rank_disp["Delta"].apply(_fmt_delta)
rank_disp["Delta %"] = rank_disp["Delta %"].apply(_fmt_pct)

st.markdown(
    f"<div class='scrollable-table'>{rank_disp.to_html(escape=False, index=False)}</div>",
    unsafe_allow_html=True,
)


# -------------------- Descriptions (Paginated) --------------------
st.markdown("### Descriptions")

descs = (
    df_filtered[
        [
            "Description",
            "SKU(s)",
            "Base SKU",
            "Region",
            "Disposition",
            "Symptom",
            "Date Identified",
            "Serial Number",
        ]
    ]
    .dropna(subset=["Description"])
    .sort_values("Date Identified", ascending=False)
    .reset_index(drop=True)
)

total = len(descs)
items_per = st.selectbox("Items per page", [10, 25, 50, 100], index=0)
pages = max(1, (total + items_per - 1) // items_per)
page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
start = (page - 1) * items_per
end = min(start + items_per, total)

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
            unsafe_allow_html=True,
        )
    st.caption(f"Showing {start + 1}–{end} of {total}")


# -------------------- Download --------------------
st.sidebar.download_button(
    label="⬇️ Download filtered CSV",
    data=df_filtered.to_csv(index=False).encode("utf-8"),
    file_name="jira_filtered.csv",
    mime="text/csv",
)
