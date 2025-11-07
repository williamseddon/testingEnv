# app.py — Jira Issues Explorer (automap + robust charts + diagnostics)
# ✔ Auto-maps your Zendesk-style export to the minimal required fields
# ✔ Clear diagnostics if charts can't render (mapping / date / filter issues)
# ✔ Timezone-safe dates, Arrow-safe preview, reset filters, disposition-by-symptom timeline
# ✔ No runtime file writes

import io
import json
import re
import difflib
from datetime import timedelta
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# ============== Page & Styles ==============
st.set_page_config(page_title="Jira Issues — Explorer", page_icon="🧭", layout="wide")
st.markdown("""
<style>
.kpi {border-radius:14px;padding:14px;border:1px solid rgba(0,0,0,0.06);
      box-shadow:0 6px 18px rgba(0,0,0,0.06);background:white;}
.kpi h3 {margin:0;font-size:13px;color:#666;}
.kpi p {margin:2px 0 0 0;font-size:24px;font-weight:700;}
.scrollable-table {overflow-y:auto;max-height:420px;border:1px solid #eee;
      padding:10px;border-radius:12px;background-color:#fafafa;}
.desc-card {background:#fff;padding:18px;margin:12px 0;border-left:5px solid #4CAF50;
      border-radius:10px;box-shadow:0 4px 10px rgba(0,0,0,.06);}
.delta-pos {color:#0a0;font-weight:700;}
.delta-neg {color:#d00;font-weight:700;}
.stPlotlyChart {background:white;border-radius:12px;padding:8px;}
</style>
""", unsafe_allow_html=True)
st.title("🧭 Jira Issues — Flexible Explorer")
st.caption("Auto-maps your Zendesk-style export • Robust charts • Diagnostics • No runtime file writes")

# ============== Canonical schema & mapping helpers ==============
REQUIRED_FIELDS = [
    "Date Identified", "SKU(s)", "Base SKU", "Region",
    "Symptom", "Disposition", "Description", "Serial Number",
]
MIN_REQUIRED = ["Date Identified", "Symptom", "Disposition", "Description"]

KNOWN_PROFILE: Dict[str, Optional[str]] = {
    "Date Identified": "Start Time (Date/Time)",
    "Symptom": "Symptom",
    "Disposition": "Disposition Tag",
    "Description": "Zoom Summary",
    # optional enrichers:
    "SKU(s)": "Zendesk SKU",
    "Base SKU": "Product Brand",
    "Region": "Queue Country",
    "Serial Number": None,
}

SYNONYMS: Dict[str, List[str]] = {
    "Date Identified": ["date identified","start time (date/time)","start time","created","created date","opened","report date"],
    "SKU(s)": ["zendesk sku","sku(s)","skus","sku","product sku","model"],
    "Base SKU": ["product brand","brand","base sku","base product","family sku","platform"],
    "Region": ["queue country","region","market","country","geo","territory","locale"],
    "Symptom": ["symptom","issue","category","failure mode","problem","defect","tag"],
    "Disposition": ["disposition tag","disposition","status","resolution","outcome","result","action"],
    "Description": ["zoom summary","description","details","summary","comments","issue description","text","notes"],
    "Serial Number": ["serial number","sn","s/n","serial","serial no","serial_no"],
}

@st.cache_data(show_spinner=False)
def read_excel_sheet_bytes(content: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(content), sheet_name=sheet)

@st.cache_data(show_spinner=False)
def read_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), low_memory=False)

def _norm(s: str) -> str:
    return re.sub(r"\W+", "", str(s).strip().lower())

def _build_norm_index(columns: List[str]) -> Dict[str, str]:
    return {_norm(c): c for c in columns}

def auto_map_known_profile(columns: List[str]) -> Dict[str, Optional[str]]:
    idx = _build_norm_index(columns)
    mapping: Dict[str, Optional[str]] = {}
    for canon, src in KNOWN_PROFILE.items():
        mapping[canon] = None if src is None else idx.get(_norm(src), None)
    return mapping

def guess_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    mapping: Dict[str, Optional[str]] = {}
    cols_norm = [(c, _norm(c)) for c in columns]
    for canon, syns in SYNONYMS.items():
        best, best_score = None, 0.0
        wanted = [_norm(canon)] + [_norm(s) for s in syns]
        for c, cn in cols_norm:
            if cn in wanted:
                best, best_score = c, 1.0
                break
            for s in wanted:
                sc = difflib.SequenceMatcher(None, cn, s).ratio()
                if sc > best_score:
                    best, best_score = c, sc
        mapping[canon] = best if (best and best_score >= 0.6) else None
    return mapping

def apply_mapping(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    reverse = {v: k for k, v in mapping.items() if v}
    out = df.rename(columns=reverse).copy()
    for col in REQUIRED_FIELDS:
        if col not in out.columns:
            out[col] = pd.NA

    # robust datetime: parse, force UTC, then drop tz (naive)
    dt = pd.to_datetime(out["Date Identified"], errors="coerce", utc=True)
    out["Date Identified"] = dt.dt.tz_convert(None)

    # string normalization
    for c in ("SKU(s)", "Base SKU"):
        if c in out.columns:
            out[c] = pd.Series(out[c], dtype="string").str.upper().str.strip()
    for c in ("Region","Symptom","Disposition","Description","Serial Number"):
        if c in out.columns:
            out[c] = pd.Series(out[c], dtype="string")
    return out

def optimize_memory(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.select_dtypes(include=["float64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="float")
    for col in out.select_dtypes(include=["int64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="integer")
    for c in ["SKU(s)","Base SKU","Region","Symptom","Disposition"]:
        if c in out.columns and out[c].nunique(dropna=True) <= max(200, len(out)//2):
            out[c] = out[c].astype("category")
    return out

def ensure_arrow_safe(df: pd.DataFrame, max_str_len: int = 10000) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_period_dtype(s):
            out[c] = s.astype("datetime64[ns]"); continue
        if pd.api.types.is_datetime64tz_dtype(s):
            out[c] = pd.to_datetime(s, utc=True).dt.tz_convert(None); continue
        if pd.api.types.is_object_dtype(s):
            out[c] = s.astype(str).map(lambda z: z if len(z)<=max_str_len else z[:max_str_len]+"…")
    return out

def kpi(label: str, value):
    st.markdown(f"<div class='kpi'><h3>{label}</h3><p>{value}</p></div>", unsafe_allow_html=True)

def combine_top_n(series: pd.Series, n: int = 10, label: str = "Other") -> pd.Series:
    s = series.fillna("(NA)")
    counts = s.value_counts(dropna=False)
    if len(counts) <= n:
        return s
    top = set(counts.nlargest(n).index.tolist())
    return s.apply(lambda x: x if x in top else label)

def build_filter_mask(df: pd.DataFrame, *, sku=None, base=None, region=None, symptom=None, disp=None,
                      tsf_only=False, search_text: str = "", regex_search: bool=False) -> pd.Series:
    mask = pd.Series(True, index=df.index, dtype=bool)
    def _apply_in(col, values):
        nonlocal mask
        if col in df.columns and values and "ALL" not in values:
            mask &= df[col].isin(values)
    _apply_in("SKU(s)", sku); _apply_in("Base SKU", base); _apply_in("Region", region)
    _apply_in("Symptom", symptom); _apply_in("Disposition", disp)
    if tsf_only and "Disposition" in df.columns:
        mask &= df["Disposition"].fillna("").str.contains(r"_ts_failed|_replaced|tsf", case=False, regex=True)
    if search_text:
        if regex_search:
            try:
                mask &= df["Description"].fillna("").str.contains(search_text, case=False, regex=True)
            except Exception:
                st.warning("Invalid regex; falling back to plain search.")
                mask &= df["Description"].fillna("").str.lower().str.contains(search_text.lower(), na=False)
        else:
            mask &= df["Description"].fillna("").str.lower().str.contains(search_text.lower(), na=False)
    return mask

def nonnull_count(df: pd.DataFrame, col: str) -> int:
    return int(df[col].notna().sum()) if col in df.columns else 0

# ============== 1) Upload ==============
st.subheader("1) Upload file")
c1, c2 = st.columns([3,1])
with c1:
    uploaded = st.file_uploader("Upload Excel (.xlsx) or CSV (.csv)", type=["xlsx","csv"])
with c2:
    st.caption("Tip: CSV loads fastest for very large exports.")

if uploaded is None:
    st.info("Upload a file to continue.")
    st.stop()

# ============== 2) Read ==============
try:
    content = uploaded.getvalue()
    if uploaded.name.lower().endswith(".csv"):
        df_raw = read_csv_bytes(content)
    else:
        xls = pd.ExcelFile(io.BytesIO(content))
        sheet = st.selectbox("Select sheet", xls.sheet_names, index=0)
        df_raw = read_excel_sheet_bytes(content, sheet=sheet)
except Exception as e:
    st.error(f"Failed to read file: {e}")
    st.stop()

# ============== 3) Auto-map (no prompts) ==============
columns = [str(c) for c in df_raw.columns]
auto_map = auto_map_known_profile(columns)
needs_guess = [f for f in MIN_REQUIRED if not auto_map.get(f)]
if needs_guess:
    guessed = guess_mapping(columns)
    for k in MIN_REQUIRED:
        if not auto_map.get(k) and guessed.get(k):
            auto_map[k] = guessed[k]

missing_final = [f for f in MIN_REQUIRED if not auto_map.get(f)]
with st.expander("Mapping health (read-only unless using Advanced remap)"):
    st.write("**Detected mapping:**")
    st.json(auto_map)
    for fld in MIN_REQUIRED:
        ok = "✅" if auto_map.get(fld) else "❌"
        st.write(f"{ok} {fld} → {auto_map.get(fld)}")

# Optional: Advanced remap
with st.expander("⚙️ Advanced remap (optional)"):
    cols2 = st.columns(2)
    override: Dict[str, Optional[str]] = {}
    for i, field in enumerate(REQUIRED_FIELDS):
        with cols2[i % 2]:
            options = [None] + columns
            idx = (options.index(auto_map.get(field)) if auto_map.get(field) in options else 0)
            override[field] = st.selectbox(field, options, index=idx, key=f"map_{field}")
    for k in REQUIRED_FIELDS:
        auto_map[k] = override.get(k, auto_map.get(k))

missing_final = [f for f in MIN_REQUIRED if not auto_map.get(f)]
if missing_final:
    st.error("❗ Could not auto-map required fields. Fix in **Advanced remap**. Missing: " + ", ".join(missing_final))
    st.stop()

# ============== 4) Standardize & Options ==============
df = apply_mapping(df_raw, auto_map)

with st.expander("Options"):
    do_opt = st.checkbox("Optimize memory (downcast + categorize)", value=True)
    regex_search = st.checkbox("Use regex for Description search (advanced)", value=False)
if do_opt:
    df = optimize_memory(df)

# ============== Sidebar filters + Reset ==============
FILTER_KEYS = [
    "sku_filter","base_filter","region_filter","symptom_filter","disposition_filter",
    "tsf_only","combine_other","search_text","date_range_graph","table_days"
]

def reset_filters():
    for k in FILTER_KEYS:
        if k in st.session_state: del st.session_state[k]
    st.experimental_rerun()

with st.sidebar:
    st.header("Filters")
    st.button("Reset filters", on_click=reset_filters)
    sku_filter = st.multiselect("SKU(s)", options=["ALL"] + sorted(pd.Series(df["SKU(s)"], dtype="string").dropna().unique().tolist()), default=["ALL"], key="sku_filter")
    base_filter = st.multiselect("Base SKU", options=["ALL"] + sorted(pd.Series(df["Base SKU"], dtype="string").dropna().unique().tolist()), default=["ALL"], key="base_filter")
    region_filter = st.multiselect("Region", options=["ALL"] + sorted(pd.Series(df["Region"], dtype="string").dropna().unique().tolist()), default=["ALL"], key="region_filter")
    symptom_filter = st.multiselect("Symptom", options=["ALL"] + sorted(pd.Series(df["Symptom"], dtype="string").dropna().unique().tolist()), default=["ALL"], key="symptom_filter")
    disposition_filter = st.multiselect("Disposition", options=["ALL"] + sorted(pd.Series(df["Disposition"], dtype="string").dropna().unique().tolist()), default=["ALL"], key="disposition_filter")
    tsf_only = st.checkbox("TSF only (_ts_failed / _replaced / tsf)", value=False, key="tsf_only")
    combine_other = st.checkbox("Combine lesser categories into 'Other' (charts only)", value=False, key="combine_other")
    search_text = st.text_input("Search 'Description'…", value="", key="search_text")
    st.markdown("---")
    st.header("Date Windows")
    date_range_graph = st.selectbox("Chart range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3, key="date_range_graph")
    table_days = st.number_input("Delta window (table): days", min_value=7, value=30, step=1, key="table_days")

# ============== Time windows ==============
now = pd.Timestamp.now()
if date_range_graph == "Last Week":
    start_graph, period_label_graph = now - timedelta(days=7), "Last 7 Days"
elif date_range_graph == "Last Month":
    start_graph, period_label_graph = now - timedelta(days=30), "Last 30 Days"
elif date_range_graph == "Last Year":
    start_graph, period_label_graph = now - timedelta(days=365), "Last 365 Days"
else:
    # Use min non-null date; if none -> use now - 365d as a safe fallback
    dmin = pd.to_datetime(df["Date Identified"], errors="coerce").dropna()
    start_graph, period_label_graph = (dmin.min() if not dmin.empty else now - timedelta(days=365), "All Time")

table_days = int(table_days)
start_table = now - timedelta(days=table_days)
prev_start_table = start_table - timedelta(days=table_days)

# ============== Filtering ==============
mask = build_filter_mask(
    df, sku=sku_filter, base=base_filter, region=region_filter,
    symptom=symptom_filter, disp=disposition_filter,
    tsf_only=tsf_only, search_text=search_text, regex_search=regex_search
)
df_filtered = df.loc[mask].copy()
df_time = df_filtered.dropna(subset=["Date Identified"]).copy()

# ============== Diagnostics (why charts might not show) ==============
with st.expander("🔎 Chart diagnostics"):
    st.write("- **Rows after filters**:", len(df_filtered))
    st.write("- **Rows with valid Date Identified**:", len(df_time))
    st.write("- **Non-null counts** — Date:", nonnull_count(df_filtered, "Date Identified"),
             "| Symptom:", nonnull_count(df_filtered, "Symptom"),
             "| Disposition:", nonnull_count(df_filtered, "Disposition"))
    if len(df_time) == 0:
        st.warning("No rows have a valid **Date Identified** after mapping. Check mapping and date format.")
    if df_filtered.empty:
        st.info("Filters/time window may be excluding all rows. Try **Reset filters** or set Chart range to **All Time**.")

# ============== KPIs & Preview ==============
st.subheader("2) Overview")
c1, c2, c3, c4, c5 = st.columns(5)
with c1: kpi("Rows (filtered)", f"{len(df_filtered):,}")
with c2: kpi("Unique SKUs", df_filtered["SKU(s)"].nunique())
with c3: kpi("Base SKUs", df_filtered["Base SKU"].nunique())
with c4: kpi("Regions", df_filtered["Region"].nunique())
with c5: kpi("Symptoms", df_filtered["Symptom"].nunique())

with st.expander("🔍 Preview filtered data"):
    st.dataframe(ensure_arrow_safe(df_filtered.head(1000)), use_container_width=True, height=320)

# ============== 3) Trends (guarded) ==============
st.subheader("3) Trends")
agg_choice = st.selectbox("Aggregate by", ["Day", "Week", "Month"], index=1)
freq = {"Day":"D","Week":"W","Month":"M"}[agg_choice]

def safe_trend_chart(df_in: pd.DataFrame, key_col: str, title: str):
    d = df_in[df_in["Date Identified"] >= start_graph] if not df_in.empty else df_in
    if d.empty or d[key_col].dropna().empty:
        st.info(f"No data to plot for **{key_col}** in current scope.")
        return
    g = d.groupby([pd.Grouper(key="Date Identified", freq=freq), key_col], dropna=False).size().reset_index(name="Count")
    if g.empty:
        st.info(f"No data after grouping for **{key_col}**.")
        return
    fig = px.bar(g, x="Date Identified", y="Count", color=key_col, title=title, template="plotly_white")
    fig.update_layout(barmode="stack", margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)

# Optionally combine minor categories to "Other"
combine_other = st.sidebar.checkbox("Combine lesser categories into 'Other' (charts only)", value=st.session_state.get("combine_other", False), key="combine_other")
df_sym = df_time.copy()
df_disp = df_time.copy()
if combine_other:
    if "Symptom" in df_sym.columns:
        df_sym["Symptom"] = combine_top_n(df_sym["Symptom"], 10, "Other")
    if "Disposition" in df_disp.columns:
        df_disp["Disposition"] = combine_top_n(df_disp["Disposition"], 10, "Other")

safe_trend_chart(df_sym, "Symptom", f"Symptom Trends Over Time ({period_label_graph})")
safe_trend_chart(df_disp, "Disposition", f"Disposition Trends Over Time ({period_label_graph})")

# ============== 4) Heatmaps & Top-N (guarded) ==============
st.subheader("4) Heatmaps & Top-N")
def weekly_heatmap(df_in: pd.DataFrame, label: str):
    d = df_in.dropna(subset=["Date Identified"]).copy()
    if d.empty:
        st.info(f"No dates to plot heatmap for **{label}**."); return
    d["Week"] = d["Date Identified"].dt.to_period("W").apply(lambda r: r.start_time)
    mat = d.groupby(["Week", label]).size().reset_index(name="Count")
    if mat.empty:
        st.info(f"No data for heatmap — **{label}**."); return
    top_labels = mat.groupby(label)["Count"].sum().nlargest(12).index
    mat[label] = mat[label].apply(lambda x: x if x in top_labels else "Other")
    mat = mat.groupby(["Week", label])["Count"].sum().reset_index()
    pv = mat.pivot(index=label, columns="Week", values="Count").fillna(0)
    if pv.empty:
        st.info(f"No matrix for heatmap — **{label}**."); return
    fig = px.imshow(pv, aspect="auto", color_continuous_scale="Blues", origin="lower", title=f"Weekly Heatmap — {label}")
    st.plotly_chart(fig, use_container_width=True)

cH1, cH2 = st.columns(2)
with cH1: weekly_heatmap(df_time, "Symptom")
with cH2: weekly_heatmap(df_time, "Disposition")

def topn_bars(df_in: pd.DataFrame, col: str, n: int = 15, title: Optional[str] = None):
    if df_in.empty or col not in df_in.columns:
        st.info(f"No data for Top-N **{col}**."); return
    vc = df_in[col].value_counts().nlargest(n).reset_index()
    if vc.empty:
        st.info(f"No counts for **{col}**."); return
    vc.columns = [col, "Count"]
    fig = px.bar(vc, x="Count", y=col, orientation="h", title=title or f"Top {n}: {col}", template="plotly_white")
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)

cT1, cT2 = st.columns(2)
with cT1: topn_bars(df_filtered, "Symptom", 15, "Top 15 Symptoms")
with cT2: topn_bars(df_filtered, "Disposition", 15, "Top 15 Dispositions")

# ============== 5) Ranked Symptoms with Δ (guarded) ==============
st.subheader("5) Ranked Symptoms (Δ over equal windows)")
cur = df_time[df_time["Date Identified"] >= start_table]
prev = df_time[(df_time["Date Identified"] < start_table) & (df_time["Date Identified"] >= prev_start_table)]
if df_time.empty:
    st.info("No dated rows to compute Δ table.")
else:
    sym_all = df_time["Symptom"].value_counts().reset_index()
    sym_all.columns = ["Symptom","Total"]
    cur_counts = cur["Symptom"].value_counts()
    prev_counts = prev["Symptom"].value_counts()
    rank = sym_all.copy()
    rank[f"Last {table_days}d"] = rank["Symptom"].map(cur_counts).fillna(0).astype(int)
    rank[f"Prev {table_days}d"] = rank["Symptom"].map(prev_counts).fillna(0).astype(int)
    rank["Delta"] = rank[f"Last {table_days}d"] - rank[f"Prev {table_days}d"]
    rank["Delta %"] = np.where(rank[f"Prev {table_days}d"]>0, (rank["Delta"]/rank[f"Prev {table_days}d"]*100.0).round(2), np.nan)

    def _fmt_delta(v):
        try: v = int(v)
        except Exception: return "—"
        if v>0: return f"<span class='delta-pos'>+{v}</span>"
        if v<0: return f"<span class='delta-neg'>{v}</span>"
        return "0"
    def _fmt_pct(v):
        if pd.isna(v): return "—"
        try: v = float(v)
        except Exception: return "—"
        if v>0: return f"<span class='delta-pos'>+{v:.2f}%</span>"
        if v<0: return f"<span class='delta-neg'>{v:.2f}%</span>"
        return "—"

    rank = rank.sort_values(["Total", f"Last {table_days}d"], ascending=False).head(10)
    rank_disp = rank.copy()
    rank_disp["Delta"] = rank_disp["Delta"].apply(_fmt_delta)
    rank_disp["Delta %"] = rank_disp["Delta %"].apply(_fmt_pct)
    st.markdown(f"<div class='scrollable-table'>{rank_disp.to_html(escape=False, index=False)}</div>", unsafe_allow_html=True)

# ============== 6) Disposition mix over time — by Symptom (guarded) ==============
st.subheader("6) Disposition mix over time — by Symptom")

cA, cB, cC, cD, cE = st.columns([2,1,1,1,1])
with cA:
    symptom_focus = st.selectbox(
        "Focus on a single symptom (optional)",
        options=["(All symptoms)"] + sorted(pd.Series(df_filtered["Symptom"], dtype="string").dropna().unique().tolist()),
        index=0
    )
with cB:
    mix_agg = st.selectbox("Aggregate by", ["Week","Month"], index=0)
with cC:
    mix_chart = st.selectbox("Chart type", ["Stacked area","Stacked bar"], index=0)
with cD:
    normalize_share = st.checkbox("Normalize to share (%)", value=True)
with cE:
    smooth = st.selectbox("Smoothing", ["None","7d roll","4w roll"], index=0)

def disposition_mix(df_in: pd.DataFrame, symptom: Optional[str], freq_code: str, normalize: bool, smooth_opt: str):
    d = df_in.dropna(subset=["Date Identified"]).copy()
    if symptom and symptom != "(All symptoms)":
        d = d[d["Symptom"] == symptom]
    if d.empty:
        return pd.DataFrame()

    freq_map = {"Week":"W", "Month":"M"}
    code = freq_map.get(freq_code, "W")
    d["Bucket"] = d["Date Identified"].dt.to_period(code).apply(lambda r: r.start_time)

    g = d.groupby(["Bucket", "Disposition"]).size().reset_index(name="Count")
    if g.empty:
        return g

    if smooth_opt != "None":
        win = 4 if smooth_opt == "4w roll" else 7
        pv = g.pivot(index="Bucket", columns="Disposition", values="Count").sort_index().fillna(0)
        pv = pv.rolling(window=win, min_periods=1).mean()
        g = pv.reset_index().melt(id_vars="Bucket", var_name="Disposition", value_name="Count")

    if normalize:
        g["Total"] = g.groupby("Bucket")["Count"].transform("sum")
        g["Share %"] = np.where(g["Total"]>0, (g["Count"]/g["Total"]*100), 0.0).round(2)
    return g

scope = df_time[df_time["Date Identified"] >= start_graph] if not df_time.empty else df_time
data_mix = disposition_mix(scope, symptom_focus, mix_agg, normalize_share, smooth)

if data_mix is None or data_mix.empty:
    st.info("No data to draw disposition mix. Check mapping, dates, or filters.")
else:
    y_col = "Share %" if normalize_share else "Count"
    title_suffix = f"{'share %' if normalize_share else 'count'} by disposition over time"
    title_prefix = "Disposition mix" if symptom_focus == "(All symptoms)" else f"Disposition mix — {symptom_focus}"
    if mix_chart == "Stacked area":
        fig_mix = px.area(data_mix, x="Bucket", y=y_col, color="Disposition",
                          title=f"{title_prefix} ({title_suffix})", template="plotly_white")
    else:
        fig_mix = px.bar(data_mix, x="Bucket", y=y_col, color="Disposition",
                         title=f"{title_prefix} ({title_suffix})", template="plotly_white")
        fig_mix.update_layout(barmode="stack")
    fig_mix.update_layout(margin=dict(t=50))
    st.plotly_chart(fig_mix, use_container_width=True)

with st.expander("Small multiples: Top-N symptoms — disposition mix over time"):
    cN1, cN2 = st.columns([1,1])
    with cN1:
        topN = st.slider("Top-N symptoms by volume", min_value=3, max_value=12, value=6, step=1)
    with cN2:
        normalize_sm = st.checkbox("Normalize to share (%) (small multiples)", value=True)

    if df_time.empty:
        st.info("No dated rows to build small multiples.")
    else:
        top_syms = df_time["Symptom"].value_counts().nlargest(topN).index.tolist()
        d2 = df_time[df_time["Symptom"].isin(top_syms)].copy()
        if not d2.empty:
            code = {"Week":"W","Month":"M"}[mix_agg]
            d2["Bucket"] = d2["Date Identified"].dt.to_period(code).apply(lambda r: r.start_time)
            gm = d2.groupby(["Symptom","Bucket","Disposition"]).size().reset_index(name="Count")
            if normalize_sm:
                gm["Total"] = gm.groupby(["Symptom","Bucket"])["Count"].transform("sum")
                gm["Share %"] = np.where(gm["Total"]>0, (gm["Count"]/gm["Total"]*100), 0.0).round(2)
            y2 = "Share %" if normalize_sm else "Count"
            fig_sm = px.area(gm, x="Bucket", y=y2, color="Disposition",
                             facet_col="Symptom", facet_col_wrap=3,
                             title=f"Disposition mix over time — top {topN} symptoms",
                             template="plotly_white")
            fig_sm.update_layout(margin=dict(t=60))
            st.plotly_chart(fig_sm, use_container_width=True)
            st.download_button("Download small-multiples dataset (CSV)",
                               data=gm.to_csv(index=False).encode("utf-8"),
                               file_name="disposition_mix_small_multiples.csv",
                               mime="text/csv")
        else:
            st.info("No data available for small multiples in this scope.")

# ============== 7) Descriptions (guarded) ==============
st.subheader("7) Descriptions")
descs = (df_filtered[["Description","SKU(s)","Base SKU","Region","Disposition","Symptom","Date Identified","Serial Number"]]
         .dropna(subset=["Description"])
         .sort_values("Date Identified", ascending=False)
         .reset_index(drop=True))
total = len(descs)
items_per = st.selectbox("Items per page", [10,25,50,100], index=0)
pages = max(1, (total + items_per - 1)//items_per)
page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
start_idx, end_idx = (page-1)*items_per, min((page-1)*items_per + items_per, total)

if total == 0:
    st.info("No descriptions match your filters.")
else:
    for _, row in descs.iloc[start_idx:end_idx].iterrows():
        d = row["Date Identified"]
        dstr = d.strftime("%Y-%m-%d") if pd.notnull(d) else "N/A"
        st.markdown(f"""
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
        """, unsafe_allow_html=True)
    st.caption(f"Showing {start_idx+1}–{end_idx} of {total}")

# ============== Downloads (in-memory) ==============
st.sidebar.download_button("⬇️ Download filtered CSV",
    data=df_filtered.to_csv(index=False).encode("utf-8"),
    file_name="jira_filtered.csv",
    mime="text/csv")
st.sidebar.download_button("💾 Save view (.json)",
    data=json.dumps({"date_range_graph": date_range_graph, "table_days": int(table_days)}, indent=2).encode("utf-8"),
    file_name="jira_view.json",
    mime="application/json")

