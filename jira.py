# app.py — Jira Issues Flexible Explorer (v4)
# Main-page upload • Flexible schema mapping • Arrow-safe preview
# Disposition-by-Symptom timeline (single & small multiples) • Heatmaps • Top-N • Deltas

import io
import json
import difflib
from datetime import timedelta
from typing import Dict, List, Optional, Any

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
    """,
    unsafe_allow_html=True
)

st.title("🧭 Jira Issues — Flexible Explorer")
st.caption("Main-page upload • Flexible schema mapping • Arrow-safe preview • Trend charts • Deltas • Heatmaps")


# -------------------- Canonical schema --------------------
REQUIRED_FIELDS = [
    "Date Identified", "SKU(s)", "Base SKU", "Region",
    "Symptom", "Disposition", "Description", "Serial Number",
]

# Minimal fields to run analysis
MIN_REQUIRED = ["Date Identified", "Symptom", "Disposition", "Description"]

# Synonyms to help auto-map (lowercased matching)
SYNONYMS: Dict[str, List[str]] = {
    "Date Identified": ["date identified","identified","date","created","created date","created_on","opened",
                        "report date","start time (date/time)","start time","start_time"],
    "SKU(s)": ["sku(s)","sku","skus","product sku","product","model sku","model","zendesk sku","zendesk_sku"],
    "Base SKU": ["base sku","base","base_sku","base sku(s)","base product","family sku","platform","product brand","brand"],
    "Region": ["region","market","country","geo","territory","locale","queue country"],
    "Symptom": ["symptom","issue","category","failure mode","problem","defect","tag"],
    "Disposition": ["disposition","status","resolution","outcome","result","action","disposition tag"],
    "Description": ["description","details","summary","comments","issue description","text","notes","zoom summary"],
    "Serial Number": ["serial number","sn","s/n","serial","unit sn","serial_no","serial no","serialnumber"],
}


# -------------------- Cache helpers --------------------
@st.cache_data(show_spinner=False)
def read_excel_sheet_bytes(content: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(content), sheet_name=sheet)  # openpyxl under the hood

@st.cache_data(show_spinner=False)
def read_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content))


# -------------------- Utility functions --------------------
def score_column_for_field(col: str, field: str) -> float:
    col_l = col.strip().lower()
    cands = [field.lower()] + SYNONYMS.get(field, [])
    scores = [difflib.SequenceMatcher(None, col_l, cand).ratio() for cand in cands]
    for cand in cands:
        if cand in col_l:
            scores.append(1.0 - 0.02 * abs(len(col_l) - len(cand)))
    return max(scores) if scores else 0.0

def guess_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    mapping: Dict[str, Optional[str]] = {}
    for field in REQUIRED_FIELDS:
        best_col, best_score = None, 0.0
        for col in columns:
            s = score_column_for_field(col, field)
            if s > best_score:
                best_col, best_score = col, s
        mapping[field] = best_col if (best_col and best_score >= 0.55) else None
    return mapping

def best_sheet_for_schema(xls_bytes: bytes, sheet_names: List[str]) -> Optional[str]:
    best_sheet, best_hits = None, -1
    for name in sheet_names:
        try:
            df = read_excel_sheet_bytes(xls_bytes, sheet=name)
        except Exception:
            continue
        hits = sum(1 for v in guess_mapping([str(c) for c in df.columns]).values() if v)
        if hits > best_hits:
            best_hits, best_sheet = hits, name
    return best_sheet

def apply_mapping(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    reverse = {v: k for k, v in mapping.items() if v}
    out = df.rename(columns=reverse).copy()
    for col in REQUIRED_FIELDS:
        if col not in out.columns:
            out[col] = pd.NA
    out["Date Identified"] = pd.to_datetime(out["Date Identified"], errors="coerce")
    for c in ("SKU(s)", "Base SKU"):
        if c in out.columns:
            out[c] = out[c].astype("string").str.upper().str.strip()
    for c in ("Region","Symptom","Disposition","Description","Serial Number"):
        if c in out.columns:
            out[c] = out[c].astype("string")
    return out

def optimize_memory(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.select_dtypes(include=["float64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="float")
    for col in out.select_dtypes(include=["int64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="integer")
    dim_cols = ["SKU(s)","Base SKU","Region","Symptom","Disposition"]
    for c in dim_cols:
        if c in out.columns and out[c].nunique(dropna=True) <= max(200, len(out)//2):
            out[c] = out[c].astype("category")
    return out

def build_filter_mask(df: pd.DataFrame, *, sku=None, base=None, region=None, symptom=None, disp=None,
                      tsf_only=False, search_text: str = "", regex_search: bool = False) -> pd.Series:
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
        s = str(search_text)
        if regex_search:
            try:
                mask &= df["Description"].fillna("").str.contains(s, case=False, regex=True)
            except Exception:
                st.warning("Invalid regex; falling back to plain text search.")
                mask &= df["Description"].fillna("").str.lower().str.contains(s.lower(), na=False)
        else:
            mask &= df["Description"].fillna("").str.lower().str.contains(s.lower(), na=False)
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

def _is_scalar_for_arrow(x: Any) -> bool:
    return not isinstance(x, (list, dict, set, tuple, bytes, bytearray))

def ensure_arrow_safe(df: pd.DataFrame, max_str_len: int = 10000) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_period_dtype(s):
            out[c] = s.astype("datetime64[ns]")
            continue
        if pd.api.types.is_datetime64tz_dtype(s):
            out[c] = pd.to_datetime(s, utc=True).dt.tz_convert(None)
            continue
        if pd.api.types.is_object_dtype(s):
            if not s.dropna().map(_is_scalar_for_arrow).all():
                out[c] = s.map(lambda v: json.dumps(v) if isinstance(v, (dict, list)) else str(v))
            else:
                out[c] = s.astype(str)
            out[c] = out[c].map(
                lambda z: z if isinstance(z, str) and len(z) <= max_str_len
                else (z[:max_str_len] + "…") if isinstance(z, str) else z
            )
    return out


# -------------------- 1) Upload (MAIN PAGE) --------------------
st.markdown("### 1) Upload file")
col_u1, col_u2 = st.columns([3,1])
with col_u1:
    uploaded = st.file_uploader("Upload Excel (.xlsx) or CSV (.csv)", type=["xlsx","csv"], key="main_uploader")
with col_u2:
    st.caption("Tip: CSV loads fastest for very large exports.")

if uploaded is None:
    st.info("Upload a file to continue.")
    st.stop()


# -------------------- Read file --------------------
kind = "csv" if uploaded.name.lower().endswith(".csv") else "xlsx"
try:
    content = uploaded.getvalue()
    if kind == "xlsx":
        xls = pd.ExcelFile(io.BytesIO(content))
        sheets = xls.sheet_names
        candidate = best_sheet_for_schema(content, sheets) or (sheets[0] if sheets else None)
        sel = st.selectbox("Select sheet", sheets, index=(sheets.index(candidate) if candidate in sheets else 0))
        df_raw = read_excel_sheet_bytes(content, sheet=sel)
    else:
        df_raw = read_csv_bytes(content)
except Exception as e:
    st.error(f"Failed to read file: {e}")
    st.stop()


# -------------------- 2) Map columns --------------------
st.markdown("### 2) Map columns (auto-guessed, editable)")
columns = [str(c) for c in df_raw.columns]
defaults = guess_mapping(columns)

st.session_state.setdefault("last_mapping_key", None)
st.session_state.setdefault("last_mapping", {})
key_now = "|".join(columns)
if st.session_state["last_mapping_key"] == key_now:
    defaults = {k: st.session_state["last_mapping"].get(k, v) for k, v in defaults.items()}

mapping: Dict[str, Optional[str]] = {}
cols = st.columns(2)
for i, field in enumerate(REQUIRED_FIELDS):
    default = defaults.get(field)
    with cols[i % 2]:
        mapping[field] = st.selectbox(field, [None] + columns,
                                      index=(columns.index(default)+1) if default in columns else 0,
                                      key=f"map_{field}")

# Warn on duplicate mappings (but don’t block)
selected_vals = [v for v in mapping.values() if v]
dupes = sorted({v for v in selected_vals if selected_vals.count(v) > 1})
if dupes:
    st.warning("Duplicate source columns selected: " + ", ".join(dupes) +
               ". Allowed, but consider mapping each canonical field to its best column.")

# Ensure minimal required fields are mapped
missing_min = [f for f in MIN_REQUIRED if not mapping.get(f)]
if missing_min:
    st.error(f"Please map the minimal required fields: {', '.join(MIN_REQUIRED)}.")
    st.stop()

# Encourage at least one SKU identifier
if not mapping.get("SKU(s)") and not mapping.get("Base SKU"):
    st.info("Tip: Map at least one of 'SKU(s)' or 'Base SKU' for better product-level analysis. Proceeding without it.")

st.session_state["last_mapping_key"] = key_now
st.session_state["last_mapping"] = mapping.copy()


# -------------------- 3) Standardize & Options --------------------
df = apply_mapping(df_raw, mapping)

with st.expander("⚙️ Options"):
    do_opt = st.checkbox("Optimize memory (downcast + categorize likely dimensions)", value=True)
    regex_search = st.checkbox("Use regex for Description search (advanced)", value=False)
if do_opt:
    df = optimize_memory(df)


# -------------------- Sidebar filters --------------------
with st.sidebar:
    st.header("Filters")
    sku_filter = st.multiselect("SKU(s)", options=["ALL"] + sorted(df["SKU(s)"].dropna().unique().tolist()), default=["ALL"])
    base_filter = st.multiselect("Base SKU", options=["ALL"] + sorted(df["Base SKU"].dropna().unique().tolist()), default=["ALL"])
    region_filter = st.multiselect("Region", options=["ALL"] + sorted(df["Region"].dropna().unique().tolist()), default=["ALL"])
    symptom_filter = st.multiselect("Symptom", options=["ALL"] + sorted(pd.Series(df["Symptom"], dtype="string").dropna().unique().tolist()), default=["ALL"])
    disposition_filter = st.multiselect("Disposition", options=["ALL"] + sorted(pd.Series(df["Disposition"], dtype="string").dropna().unique().tolist()), default=["ALL"])

    tsf_only = st.checkbox("TSF only (disposition contains _ts_failed / _replaced / tsf)", value=False)
    combine_other = st.checkbox("Combine lesser categories into 'Other' (charts only)", value=False)
    search_text = st.text_input("Search 'Description'…", value="")
    st.markdown("---")
    st.header("Date Windows")
    date_range_graph = st.selectbox("Chart range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3)
    table_days = st.number_input("Delta window (table): days", min_value=7, value=30, step=1)
    st.markdown("---")
    st.header("Views")
    view_to_load = st.file_uploader("Load saved view (.json)", type=["json"], key="view_loader")
    if view_to_load is not None:
        try:
            view_cfg = json.loads(view_to_load.getvalue().decode("utf-8"))
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
            st.success("View loaded. Filters updated above.")
        except Exception as e:
            st.error(f"Failed to load view: {e}")


# -------------------- Field Inspector --------------------
with st.expander("🔎 Field Inspector (validate mapping quickly)"):
    for col in REQUIRED_FIELDS:
        if col not in df.columns:
            continue
        s = df[col]
        st.write(f"**{col}** — non-null: {int(s.notna().sum()):,}, unique: {s.nunique(dropna=True):,}")
        if pd.api.types.is_datetime64_any_dtype(s):
            st.write(f"• min: {pd.to_datetime(s, errors='coerce').min()}  |  max: {pd.to_datetime(s, errors='coerce').max()}")
        else:
            top = s.value_counts(dropna=True).head(10)
            st.dataframe(top.rename("count"))


# -------------------- Time windows --------------------
now = pd.Timestamp.now()
if date_range_graph == "Last Week":
    start_graph, period_label_graph = now - timedelta(days=7), "Last 7 Days"
elif date_range_graph == "Last Month":
    start_graph, period_label_graph = now - timedelta(days=30), "Last 30 Days"
elif date_range_graph == "Last Year":
    start_graph, period_label_graph = now - timedelta(days=365), "Last 365 Days"
else:
    start_graph, period_label_graph = df["Date Identified"].min(), "All Time"

table_days = int(table_days)
start_table = now - timedelta(days=table_days)
prev_start_table = start_table - timedelta(days=table_days)


# -------------------- Filtering --------------------
mask = build_filter_mask(
    df, sku=sku_filter, base=base_filter, region=region_filter,
    symptom=symptom_filter, disp=disposition_filter,
    tsf_only=tsf_only, search_text=search_text, regex_search=regex_search
)
df_filtered = df.loc[mask].copy()


# -------------------- KPIs & Preview --------------------
st.markdown("### 3) Overview")
c1, c2, c3, c4, c5 = st.columns(5)
with c1: kpi("Rows (filtered)", f"{len(df_filtered):,}")
with c2: kpi("Unique SKUs", df_filtered["SKU(s)"].nunique())
with c3: kpi("Base SKUs", df_filtered["Base SKU"].nunique())
with c4: kpi("Regions", df_filtered["Region"].nunique())
with c5: kpi("Symptoms", df_filtered["Symptom"].nunique())

with st.expander("🔍 Preview filtered data"):
    preview = ensure_arrow_safe(df_filtered.head(1000))
    st.dataframe(preview, use_container_width=True, height=320)


# -------------------- 4) Trends (Symptom & Disposition stacks) --------------------
st.markdown("### 4) Trends")
agg_choice = st.selectbox("Aggregate by", ["Day", "Week", "Month"], index=1)
freq = {"Day":"D","Week":"W","Month":"M"}[agg_choice]

sym_df = df_filtered[df_filtered["Date Identified"] >= start_graph] if pd.notna(start_graph) else df_filtered.copy()
if combine_other: sym_df = sym_df.assign(Symptom=combine_top_n(sym_df["Symptom"], 10, "Other"))
sym_trend = sym_df.groupby([pd.Grouper(key="Date Identified", freq=freq), "Symptom"], dropna=False).size().reset_index(name="Count")
fig_sym = px.bar(sym_trend, x="Date Identified", y="Count", color="Symptom",
                 title=f"Symptom Trends Over Time ({period_label_graph})", template="plotly_white")
fig_sym.update_layout(barmode="stack", margin=dict(t=40))
st.plotly_chart(fig_sym, use_container_width=True)

disp_df = df_filtered[df_filtered["Date Identified"] >= start_graph] if pd.notna(start_graph) else df_filtered.copy()
if combine_other: disp_df = disp_df.assign(Disposition=combine_top_n(disp_df["Disposition"], 10, "Other"))
disp_trend = disp_df.groupby([pd.Grouper(key="Date Identified", freq=freq), "Disposition"], dropna=False).size().reset_index(name="Count")
fig_disp = px.bar(disp_trend, x="Date Identified", y="Count", color="Disposition",
                  title=f"Disposition Trends Over Time ({period_label_graph})", template="plotly_white")
fig_disp.update_layout(barmode="stack", margin=dict(t=40))
st.plotly_chart(fig_disp, use_container_width=True)


# -------------------- 5) Heatmaps & Top-N --------------------
st.markdown("#### Heatmaps")
def weekly_heatmap(df_in: pd.DataFrame, label: str):
    d = df_in.dropna(subset=["Date Identified"]).copy()
    if d.empty:
        st.info("No dates to plot.")
        return
    d["Week"] = d["Date Identified"].dt.to_period("W").apply(lambda r: r.start_time)
    mat = d.groupby(["Week", label]).size().reset_index(name="Count")
    top_labels = mat.groupby(label)["Count"].sum().nlargest(12).index
    mat[label] = mat[label].apply(lambda x: x if x in top_labels else "Other")
    mat = mat.groupby(["Week", label])["Count"].sum().reset_index()
    pv = mat.pivot(index=label, columns="Week", values="Count").fillna(0)
    fig = px.imshow(pv, aspect="auto", color_continuous_scale="Blues", origin="lower",
                    title=f"Weekly Heatmap — {label}")
    st.plotly_chart(fig, use_container_width=True)

cH1, cH2 = st.columns(2)
with cH1: weekly_heatmap(df_filtered, "Symptom")
with cH2: weekly_heatmap(df_filtered, "Disposition")

st.markdown("#### Top-N")
def topn_bars(df_in: pd.DataFrame, col: str, n: int = 15, title: Optional[str] = None):
    if df_in.empty:
        st.info("No data for Top-N.")
        return
    vc = df_in[col].value_counts().nlargest(n).reset_index()
    vc.columns = [col, "Count"]
    fig = px.bar(vc, x="Count", y=col, orientation="h", title=title or f"Top {n}: {col}", template="plotly_white")
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True)

cT1, cT2 = st.columns(2)
with cT1: topn_bars(df_filtered, "Symptom", 15, "Top 15 Symptoms")
with cT2: topn_bars(df_filtered, "Disposition", 15, "Top 15 Dispositions")


# -------------------- 6) Ranked Symptoms with Δ --------------------
st.markdown("### 6) Ranked Symptoms (Δ over equal windows)")
cur = df_filtered[df_filtered["Date Identified"] >= start_table]
prev = df_filtered[(df_filtered["Date Identified"] < start_table) & (df_filtered["Date Identified"] >= prev_start_table)]
sym_all = df_filtered["Symptom"].value_counts().reset_index()
sym_all.columns = ["Symptom","Total"]
cur_counts = cur["Symptom"].value_counts()
prev_counts = prev["Symptom"].value_counts()
rank = sym_all.copy()
rank[f"Last {table_days}d"] = rank["Symptom"].map(cur_counts).fillna(0).astype(int)
rank[f"Prev {table_days}d"] = rank["Symptom"].map(prev_counts).fillna(0).astype(int)
rank["Delta"] = rank[f"Last {table_days}d"] - rank[f"Prev {table_days}d"]
rank["Delta %"] = np.where(rank[f"Prev {table_days}d"]>0,
                           (rank["Delta"]/rank[f"Prev {table_days}d"]*100.0).round(2),
                           np.nan)
rank = rank.sort_values(["Total", f"Last {table_days}d"], ascending=False).head(10)

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

rank_disp = rank.copy()
rank_disp["Delta"] = rank_disp["Delta"].apply(_fmt_delta)
rank_disp["Delta %"] = rank_disp["Delta %"].apply(_fmt_pct)
st.markdown(f"<div class='scrollable-table'>{rank_disp.to_html(escape=False, index=False)}</div>", unsafe_allow_html=True)


# -------------------- 7) NEW: Disposition mix over time by Symptom --------------------
st.markdown("### 7) Disposition mix over time — by Symptom")

with st.container():
    colA, colB, colC, colD = st.columns([2,1,1,1])
    with colA:
        symptom_focus = st.selectbox(
            "Focus on a single symptom (for detailed view)",
            options=["(Choose)"] + sorted(pd.Series(df_filtered["Symptom"], dtype="string").dropna().unique().tolist()),
            index=0
        )
    with colB:
        mix_agg = st.selectbox("Aggregate by", ["Week","Month"], index=0)
    with colC:
        mix_chart = st.selectbox("Chart type", ["Stacked area","Stacked bar"], index=0)
    with colD:
        normalize_share = st.checkbox("Normalize to share (%)", value=True)

    # Prepare function
    def disposition_mix(df_in: pd.DataFrame, symptom: Optional[str], freq_code: str, normalize: bool):
        d = df_in.dropna(subset=["Date Identified"]).copy()
        if symptom and symptom != "(Choose)":
            d = d[d["Symptom"] == symptom]
        if d.empty:
            return pd.DataFrame()

        # Time bucketing
        freq_map = {"Week": "W", "Month": "M"}
        code = freq_map.get(freq_code, "W")
        d["Bucket"] = d["Date Identified"].dt.to_period(code).apply(lambda r: r.start_time)

        g = d.groupby(["Bucket", "Disposition"]).size().reset_index(name="Count")
        if normalize:
            g["Total"] = g.groupby("Bucket")["Count"].transform("sum")
            g["Share %"] = (g["Count"] / g["Total"] * 100).round(2)
        return g

    # Single-symptom detail
    data_mix = disposition_mix(df_filtered[df_filtered["Date Identified"] >= start_graph] if pd.notna(start_graph) else df_filtered,
                               symptom_focus, mix_agg, normalize_share)

    if data_mix.empty and symptom_focus != "(Choose)":
        st.info("No data for the chosen symptom with current filters/time window.")
    else:
        y_col = "Share %" if normalize_share else "Count"
        title_suffix = f"{'share %' if normalize_share else 'count'} by disposition over time"
        if mix_chart == "Stacked area":
            fig_mix = px.area(data_mix, x="Bucket", y=y_col, color="Disposition",
                              title=f"{'Disposition mix' if symptom_focus=='(Choose)' else f'Disposition mix — {symptom_focus}'} ({title_suffix})",
                              template="plotly_white")
        else:
            fig_mix = px.bar(data_mix, x="Bucket", y=y_col, color="Disposition",
                             title=f"{'Disposition mix' if symptom_focus=='(Choose)' else f'Disposition mix — {symptom_focus}'} ({title_suffix})",
                             template="plotly_white")
            fig_mix.update_layout(barmode="stack")
        fig_mix.update_layout(margin=dict(t=50))
        st.plotly_chart(fig_mix, use_container_width=True)

    # Small multiples: top-N symptoms by volume
    with st.expander("Small multiples: Top-N symptoms — disposition mix over time"):
        colN1, colN2 = st.columns([1,1])
        with colN1:
            topN = st.slider("Top-N symptoms by volume", min_value=3, max_value=12, value=6, step=1)
        with colN2:
            normalize_sm = st.checkbox("Normalize to share (%) (small multiples)", value=True)

        # Prep top symptoms in filtered scope
        top_symptoms = (df_filtered["Symptom"].value_counts().nlargest(topN).index.tolist()
                        if "Symptom" in df_filtered.columns else [])
        d2 = df_filtered[df_filtered["Date Identified"] >= start_graph] if pd.notna(start_graph) else df_filtered
        d2 = d2[d2["Symptom"].isin(top_symptoms)].dropna(subset=["Date Identified"]).copy()
        if not d2.empty:
            code = {"Week":"W", "Month":"M"}[mix_agg]
            d2["Bucket"] = d2["Date Identified"].dt.to_period(code).apply(lambda r: r.start_time)
            gm = d2.groupby(["Symptom","Bucket","Disposition"]).size().reset_index(name="Count")
            if normalize_sm:
                gm["Total"] = gm.groupby(["Symptom","Bucket"])["Count"].transform("sum")
                gm["Share %"] = (gm["Count"]/gm["Total"]*100).round(2)
            y2 = "Share %" if normalize_sm else "Count"
            fig_sm = px.area(gm, x="Bucket", y=y2, color="Disposition",
                             facet_col="Symptom", facet_col_wrap=3,
                             title=f"Disposition mix over time — top {topN} symptoms",
                             template="plotly_white")
            fig_sm.update_layout(margin=dict(t=60))
            st.plotly_chart(fig_sm, use_container_width=True)
        else:
            st.info("No data available for small multiples with current filters/time window.")

        st.download_button(
            "Download small-multiples dataset (CSV)",
            data=(gm.to_csv(index=False).encode("utf-8") if 'gm' in locals() and not gm.empty else b""),
            file_name="disposition_mix_small_multiples.csv",
            mime="text/csv",
            disabled=('gm' not in locals() or gm.empty)
        )


# -------------------- 8) Descriptions (Paginated) --------------------
st.markdown("### 8) Descriptions")
descs = (df_filtered[["Description","SKU(s)","Base SKU","Region","Disposition","Symptom","Date Identified","Serial Number"]]
         .dropna(subset=["Description"])
         .sort_values("Date Identified", ascending=False)
         .reset_index(drop=True))
total = len(descs)
items_per = st.selectbox("Items per page", [10,25,50,100], index=0)
pages = max(1, (total + items_per - 1)//items_per)
page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
start, end = (page-1)*items_per, min((page-1)*items_per + items_per, total)

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


# -------------------- Downloads (in-memory) --------------------
st.sidebar.download_button(
    label="⬇️ Download filtered CSV",
    data=df_filtered.to_csv(index=False).encode("utf-8"),
    file_name="jira_filtered.csv",
    mime="text/csv",
)
view_state = {
    "sku_filter": sku_filter, "base_filter": base_filter, "region_filter": region_filter,
    "symptom_filter": symptom_filter, "disposition_filter": disposition_filter,
    "tsf_only": tsf_only, "combine_other": combine_other, "search_text": search_text,
    "date_range_graph": date_range_graph, "table_days": int(table_days),
}
st.sidebar.download_button(
    label="💾 Save current view (.json)",
    data=json.dumps(view_state, indent=2).encode("utf-8"),
    file_name="jira_view.json",
    mime="application/json",
)

