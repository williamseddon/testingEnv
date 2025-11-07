# Create a production-ready Streamlit app with robust filtering and charting for large Excel files.
# We'll save it as /mnt/data/streamlit_app.py and also include a minimal README with run instructions.
import os, json, hashlib, io, textwrap, sys, traceback
from pathlib import Path

app_code = r'''
import streamlit as st
import pandas as pd
import numpy as np
import io, hashlib, os, textwrap
from datetime import datetime, date
from typing import Dict, List, Tuple, Optional

# Optional: Plotly for interactive charts
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(
    page_title="Best-in-Class Data Explorer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---- Minimal theming tweaks ----
CUSTOM_CSS = """
<style>
/* Softer look */
.reportview-container .markdown-text-container {
  font-size: 0.95rem;
}
.block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
.sidebar .sidebar-content {padding-top: 1rem;}
.css-1kyxreq {padding-top: 1rem;}
hr { margin: 0.5rem 0 1rem 0; }
.kpi-card {
  border-radius: 16px; padding: 16px; border: 1px solid rgba(0,0,0,0.08);
  box-shadow: 0 3px 16px rgba(0,0,0,0.06); background: white;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ---- Utilities ----
def file_bytes_and_hash(file) -> Tuple[bytes, str]:
    data = file.read() if hasattr(file, "read") else file
    if isinstance(data, bytes):
        b = data
    else:
        b = data.getvalue()
    h = hashlib.md5(b).hexdigest()
    return b, h

@st.cache_data(show_spinner=False)
def read_excel_cached(content: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    buffer = io.BytesIO(content)
    try:
        df = pd.read_excel(buffer, sheet_name=sheet)  # openpyxl engine
    except Exception as e:
        raise RuntimeError(f"Failed to read Excel: {e}")
    return df

def optimize_memory(df: pd.DataFrame, cat_unique_threshold: int = 1000, cat_ratio_threshold: float = 0.5) -> pd.DataFrame:
    """Downcast numerics; convert low-cardinality object columns to category."""
    out = df.copy()
    for col in out.select_dtypes(include=["float64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="float")
    for col in out.select_dtypes(include=["int64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="integer")
    # Try to parse datetimes if they look like dates
    for col in out.columns:
        if out[col].dtype == "object":
            # Heuristic: if >80% parseable to datetime and at least 10 unique
            sample = out[col].dropna().astype(str).head(500)
            parse_hits = 0
            for s in sample:
                try:
                    _ = pd.to_datetime(s)
                    parse_hits += 1
                except Exception:
                    pass
            if len(sample) >= 10 and parse_hits / max(len(sample),1) > 0.8:
                try:
                    out[col] = pd.to_datetime(out[col], errors="coerce")
                    continue
                except Exception:
                    pass
    # Categorical conversion
    obj_cols = out.select_dtypes(include=["object"]).columns
    n = len(out)
    for col in obj_cols:
        uniq = out[col].nunique(dropna=True)
        if n == 0:
            continue
        ratio = uniq / max(n,1)
        if uniq <= cat_unique_threshold and ratio <= cat_ratio_threshold:
            out[col] = out[col].astype("category")
    return out

def infer_types(df: pd.DataFrame) -> Dict[str, List[str]]:
    types = {"numeric": [], "categorical": [], "datetime": [], "boolean": []}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            types["boolean"].append(c)
        elif pd.api.types.is_numeric_dtype(s):
            types["numeric"].append(c)
        elif pd.api.types.is_datetime64_any_dtype(s):
            types["datetime"].append(c)
        else:
            types["categorical"].append(c)
    return types

def build_filters(types: Dict[str, List[str]], df: pd.DataFrame) -> Dict[str, dict]:
    st.sidebar.header("🔎 Filters")
    st.sidebar.caption("Choose filters to slice the dataset. Use **Reset filters** to clear.")
    applied = {}

    if st.sidebar.button("Reset filters", type="secondary"):
        st.session_state.pop("filters", None)

    # Free text search across all categorical columns
    with st.sidebar.expander("🔤 Global text search", expanded=False):
        q = st.text_input("Contains text (applies to all categorical columns)", key="global_text_query")
        if q:
            applied["__global_text__"] = {"query": q}

    # Categorical filters (limit to manageable cardinality)
    with st.sidebar.expander("🏷️ Categorical filters", expanded=True):
        for col in types["categorical"]:
            nunique = df[col].nunique(dropna=True)
            if nunique <= 200:
                options = list(df[col].dropna().value_counts().head(500).index)
                sel = st.multiselect(f"{col} (choose values)", options=options, key=f"cat_{col}")
                if sel:
                    applied[col] = {"type": "categorical", "values": sel}
            else:
                # Provide a text contains control for high-cardinality columns
                txt = st.text_input(f"{col} contains…", key=f"cat_text_{col}")
                if txt:
                    applied[col] = {"type": "text_contains", "value": txt}

    # Numeric filters
    with st.sidebar.expander("🔢 Numeric filters", expanded=True):
        for col in types["numeric"]:
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if s.empty:
                continue
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            lower = float(q1 - 1.5 * iqr)
            upper = float(q3 + 1.5 * iqr)
            vmin, vmax = float(s.min()), float(s.max())
            use_iqr = st.checkbox(f"{col}: clip to IQR range [{lower:.3g}, {upper:.3g}]", value=False, key=f"num_clip_{col}")
            rmin, rmax = (lower, upper) if use_iqr else (vmin, vmax)
            sel = st.slider(f"{col} range", min_value=float(vmin), max_value=float(vmax), value=(float(rmin), float(rmax)))
            applied[col] = {"type": "numeric", "min": sel[0], "max": sel[1]}

    # Datetime filters
    with st.sidebar.expander("📅 Datetime filters", expanded=True):
        for col in types["datetime"]:
            s = pd.to_datetime(df[col], errors="coerce").dropna()
            if s.empty:
                continue
            dmin, dmax = s.min(), s.max()
            # Use dates if range > 2 days, else datetimes
            if (dmax - dmin).days >= 2:
                start = st.date_input(f"{col} start", value=dmin.date())
                end = st.date_input(f"{col} end", value=dmax.date())
                applied[col] = {"type": "date", "start": pd.to_datetime(start), "end": pd.to_datetime(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)}
            else:
                start = st.datetime_input(f"{col} start", value=dmin.to_pydatetime())
                end = st.datetime_input(f"{col} end", value=dmax.to_pydatetime())
                applied[col] = {"type": "datetime", "start": pd.to_datetime(start), "end": pd.to_datetime(end)}
    return applied

def apply_filters(df: pd.DataFrame, filters: Dict[str, dict]) -> pd.DataFrame:
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for col, cfg in filters.items():
        if col == "__global_text__":
            q = str(cfg["query"]).lower()
            cat_cols = df.select_dtypes(include=["object", "string", "category"]).columns
            if len(cat_cols) == 0:
                continue
            cat_mask = pd.Series(False, index=df.index)
            for c in cat_cols:
                cat_mask = cat_mask | df[c].astype(str).str.lower().str.contains(q, na=False)
            mask = mask & cat_mask
            continue

        if cfg.get("type") == "categorical":
            mask = mask & df[col].isin(cfg["values"])
        elif cfg.get("type") == "text_contains":
            val = str(cfg["value"]).lower()
            mask = mask & df[col].astype(str).str.lower().str.contains(val, na=False)
        elif cfg.get("type") == "numeric":
            s = pd.to_numeric(df[col], errors="coerce")
            mask = mask & s.ge(cfg["min"]) & s.le(cfg["max"])
        elif cfg.get("type") in ("date", "datetime"):
            s = pd.to_datetime(df[col], errors="coerce")
            mask = mask & s.ge(cfg["start"]) & s.le(cfg["end"])
    return df[mask]

def kpi(label: str, value, help_text: Optional[str] = None, cols=None):
    col = cols if cols else st
    with col.container():
        st.markdown(f"<div class='kpi-card'><div style='font-size:14px;color:#666;'>{label}</div><div style='font-size:28px;font-weight:700'>{value}</div></div>", unsafe_allow_html=True)
        if help_text:
            st.caption(help_text)

def chart_builder(df: pd.DataFrame, types: Dict[str, List[str]]):
    st.subheader("📊 Chart Builder")
    if df.empty:
        st.info("No data after filters.")
        return

    chart_type = st.selectbox("Chart type", ["Line", "Bar", "Area", "Scatter", "Histogram", "Box", "Heatmap"], index=0, help="Choose how to visualize your data.")
    x_col = st.selectbox("X-axis", options=list(df.columns))
    y_col = None
    color = st.selectbox("Color (optional)", options=["(none)"] + list(df.columns), index=0)
    color = None if color == "(none)" else color

    if chart_type in ["Line", "Bar", "Area", "Scatter", "Box"]:
        y_col = st.selectbox("Y-axis / numeric", options=types["numeric"] or list(df.columns))
        if y_col is None:
            st.warning("Pick a numeric column for Y.")
            return

    # Resample if datetime x
    if pd.api.types.is_datetime64_any_dtype(df[x_col]):
        freq = st.selectbox("Resample frequency (if datetime X)", ["(none)", "D", "W", "M", "Q", "Y"], index=0,
                            help="Aggregate to daily/weekly/monthly/etc. when the X-axis is datetime.")
        agg = st.selectbox("Aggregate function", ["sum", "mean", "median", "min", "max", "count"], index=1)
        if freq != "(none)":
            if chart_type in ["Histogram", "Box", "Heatmap"]:
                st.info("Resampling applies to numeric summaries; choose line/bar/area/scatter for resampled series.")
            if y_col:
                grp = df[[x_col, y_col] + ([color] if color else [])].dropna()
                if grp.empty:
                    st.info("No rows to plot after dropping NaNs.")
                    return
                if color:
                    grp = grp.groupby([pd.Grouper(key=x_col, freq=freq), color]).agg({y_col: agg}).reset_index()
                else:
                    grp = grp.groupby(pd.Grouper(key=x_col, freq=freq)).agg({y_col: agg}).reset_index()
                df_plot = grp
            else:
                df_plot = df.copy()
        else:
            df_plot = df.copy()
    else:
        df_plot = df.copy()

    # Build chart
    if chart_type == "Line":
        fig = px.line(df_plot, x=x_col, y=y_col, color=color)
    elif chart_type == "Bar":
        fig = px.bar(df_plot, x=x_col, y=y_col, color=color, barmode="group")
    elif chart_type == "Area":
        fig = px.area(df_plot, x=x_col, y=y_col, color=color, groupnorm=None)
    elif chart_type == "Scatter":
        trend = st.checkbox("Add trendline (OLS)", value=False)
        tl = "ols" if trend else None
        fig = px.scatter(df_plot, x=x_col, y=y_col, color=color, trendline=tl)
    elif chart_type == "Histogram":
        nbins = st.slider("Number of bins", 5, 100, 40)
        fig = px.histogram(df_plot, x=x_col, color=color, nbins=nbins, barmode="overlay")
    elif chart_type == "Box":
        fig = px.box(df_plot, x=x_col, y=y_col, color=color, points="outliers")
    else:  # Heatmap
        st.info("Select two numeric columns to visualize a correlation heatmap.")
        cols = st.multiselect("Pick numeric columns", options=types["numeric"], default=types["numeric"][:5] if types["numeric"] else [])
        if len(cols) >= 2:
            corr = df_plot[cols].corr(numeric_only=True)
            fig = px.imshow(corr, text_auto=True, aspect="auto", color_continuous_scale="RdBu", origin="lower")
        else:
            st.warning("Choose at least two numeric columns.")
            return

    st.plotly_chart(fig, use_container_width=True)

def aggregation_builder(df: pd.DataFrame, types: Dict[str, List[str]]):
    st.subheader("📐 Group & Aggregate")
    if df.empty:
        st.info("No data after filters.")
        return

    dims = st.multiselect("Group by (dimensions)", options=types["categorical"] + types["datetime"])
    metrics = st.multiselect("Metrics (numeric)", options=types["numeric"])
    aggs = st.multiselect("Aggregations", options=["sum", "mean", "median", "min", "max", "count", "nunique"], default=["sum","mean","count"])

    if not dims or not metrics or not aggs:
        st.caption("Pick dimensions, metrics and aggregations.")
        return

    gb = df.groupby(dims, dropna=False)
    agg_dict = {m: aggs for m in metrics}
    out = gb.agg(agg_dict)
    out.columns = ["_".join([c for c in col if c]) if isinstance(col, tuple) else str(col) for col in out.columns.values]
    out = out.reset_index()
    st.dataframe(out.head(500), use_container_width=True)
    st.download_button("⬇️ Download aggregated CSV", out.to_csv(index=False).encode("utf-8"), file_name="aggregated.csv", mime="text/csv")

def pivot_builder(df: pd.DataFrame, types: Dict[str, List[str]]):
    st.subheader("🧮 Pivot Table")
    if df.empty:
        st.info("No data after filters.")
        return

    rows = st.multiselect("Rows", options=list(df.columns))
    cols = st.multiselect("Columns", options=list(df.columns))
    vals = st.multiselect("Values (numeric preferred)", options=list(df.columns))
    aggfunc = st.selectbox("Aggregation", ["sum","mean","median","min","max","count","nunique"], index=0)

    if not rows or not vals:
        st.caption("Pick at least rows and values.")
        return
    try:
        pv = pd.pivot_table(df, index=rows, columns=cols if cols else None, values=vals, aggfunc=aggfunc)
        st.dataframe(pv.head(500), use_container_width=True)
        st.download_button("⬇️ Download pivot CSV", pv.reset_index().to_csv(index=False).encode("utf-8"), file_name="pivot.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Pivot failed: {e}")

def profile_view(df: pd.DataFrame):
    st.subheader("🧭 Data Profile")
    n_rows, n_cols = df.shape
    c1, c2, c3, c4 = st.columns(4)
    kpi("Rows", f"{n_rows:,}", cols=c1)
    kpi("Columns", f"{n_cols:,}", cols=c2)
    kpi("Missing cells %", f"{(df.isna().sum().sum() / max(n_rows*n_cols,1) * 100):.2f}%", cols=c3)
    kpi("Duplicate rows", f"{df.duplicated().sum():,}", cols=c4)

    st.markdown("#### Column Overview")
    ov = pd.DataFrame({
        "column": df.columns,
        "dtype": df.dtypes.astype(str).values,
        "non_null": df.notna().sum().values,
        "missing": df.isna().sum().values,
        "unique": [df[c].nunique(dropna=True) for c in df.columns],
    })
    st.dataframe(ov, use_container_width=True, height=300)

    with st.expander("📈 Quick histograms (top numeric)", expanded=False):
        num_cols = df.select_dtypes(include=np.number).columns.tolist()[:6]
        for c in num_cols:
            fig = px.histogram(df, x=c, nbins=40, title=c)
            st.plotly_chart(fig, use_container_width=True)

# ---- App body ----
st.title("📈 Best-in-Class Data Explorer")
st.caption("Fast filtering • Powerful grouping • Interactive charts • Exportable results")

# Inlined small helper to load default sample if present
DEFAULT_SAMPLE_PATH = "/mnt/data/data (31).xlsx"

with st.sidebar:
    st.markdown("### 1) Load data")
    upl = st.file_uploader("Upload an Excel file (.xlsx)", type=["xlsx"])
    use_sample = st.checkbox("Use included sample (if present)", value=os.path.exists(DEFAULT_SAMPLE_PATH))
    sheet_choice = st.text_input("Sheet name (optional)", value="")
    st.markdown("---")
    st.markdown("### 2) Options")
    do_opt = st.checkbox("Optimize memory (downcast + categorical)", value=True)
    st.markdown("---")
    st.markdown("### 3) Export")
    st.caption("Use the Download buttons in each section to export filtered or aggregated data.")

# Load data
df = None
load_err = None
if upl is not None:
    try:
        b, h = file_bytes_and_hash(upl)
        df = read_excel_cached(b, sheet=sheet_choice or None)
    except Exception as e:
        load_err = str(e)
elif use_sample and os.path.exists(DEFAULT_SAMPLE_PATH):
    try:
        with open(DEFAULT_SAMPLE_PATH, "rb") as f:
            b = f.read()
        df = read_excel_cached(b, sheet=sheet_choice or None)
    except Exception as e:
        load_err = str(e)

if load_err:
    st.error(f"Failed to load Excel: {load_err}")

if df is None:
    st.info("Upload an Excel file (or tick 'Use included sample') to begin.")
    st.stop()

# Optimize memory & infer types
if do_opt:
    df = optimize_memory(df)
types = infer_types(df)

# Sidebar: show quick info
st.sidebar.markdown("### Dataset snapshot")
st.sidebar.write(f"**Rows:** {len(df):,}  \n**Columns:** {df.shape[1]:,}")
st.sidebar.write(f"**Numeric:** {len(types['numeric'])}  \n**Categorical:** {len(types['categorical'])}  \n**Datetime:** {len(types['datetime'])}  \n**Boolean:** {len(types['boolean'])}")

# Build & apply filters
filters = build_filters(types, df)
filtered = apply_filters(df, filters)

# Top summary
st.markdown("### Overview")
c1, c2, c3, c4 = st.columns(4)
kpi("Rows (filtered)", f"{len(filtered):,}", cols=c1)
kpi("Columns", f"{filtered.shape[1]:,}", cols=c2)
kpi("Numeric columns", f"{len(types['numeric'])}", cols=c3)
kpi("Categorical columns", f"{len(types['categorical'])}", cols=c4)

with st.expander("🔍 Preview filtered data"):
    st.dataframe(filtered.head(1000), use_container_width=True, height=320)
    st.download_button("⬇️ Download filtered CSV", filtered.to_csv(index=False).encode("utf-8"), file_name="filtered.csv", mime="text/csv")

# Tabs for deeper work
tab1, tab2, tab3, tab4 = st.tabs(["📐 Group & Aggregate", "🧮 Pivot", "📊 Charts", "🧭 Profile"])

with tab1:
    aggregation_builder(filtered, types)

with tab2:
    pivot_builder(filtered, types)

with tab3:
    chart_builder(filtered, types)

with tab4:
    profile_view(filtered)

st.success("Ready. Use the controls to mine trends and find upticks across your dataset.")
'''

readme = r'''# Best-in-Class Data Explorer (Streamlit)

A fast, flexible Streamlit app for exploring large Excel datasets with powerful filters, grouping, pivots, and interactive charts.

## Run locally
```bash
pip install streamlit pandas plotly openpyxl
streamlit run streamlit_app.py
