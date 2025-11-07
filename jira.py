# Create a more advanced Streamlit app with robust mapping, diagnostics, anomaly detection,
# matrix view, export options, and quality-of-life improvements. No runtime disk writes.
from pathlib import Path

code = r'''
# app_pro_v7.py — Jira Issues Pro Explorer
# • Auto-maps minimal fields (Date Identified, Symptom, Disposition, Description=Zoom Summary)
# • Mapping profiles (save/load JSON), uniqueness enforcement, health diagnostics
# • Robust date parsing (tz-safe), memory optimization, Arrow-safe preview
# • Fast filters, Reset, counts on options, global text search
# • Trend charts (Symptom/Disposition) with rolling averages and WoW/4W deltas
# • Disposition-by-Symptom timeline (share %, smoothing), small multiples
# • Symptom × Disposition matrix with current vs previous window, deltas, % shares
# • Spike finder (rolling z-score) to flag anomalous upticks
# • Description explorer with keyword highlighting
# • Exports: filtered CSV, chart datasets CSV, mapping/view JSON
# • No runtime file writes

import io
import json
import re
import difflib
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# -------------------- Page & Styles --------------------
st.set_page_config(page_title="Jira Issues — Pro Explorer", page_icon="🧭", layout="wide")
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
      .pill {display:inline-block;padding:2px 8px;border-radius:12px;background:#eef;border:1px solid #dde;margin-right:6px;font-size:12px;}
      .pill-bad {background:#fee;border-color:#fdd;}
      .hl {background: #fffd8a;}
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🧭 Jira Issues — Pro Explorer")
st.caption("Auto-map • Diagnostics • Trends • Mix • Matrix • Anomalies • Exports — No runtime file writes")

# -------------------- Canonical schema --------------------
REQUIRED_FIELDS = [
    "Date Identified", "SKU(s)", "Base SKU", "Region",
    "Symptom", "Disposition", "Description", "Serial Number",
]
MIN_REQUIRED = ["Date Identified", "Symptom", "Disposition", "Description"]

# Hard preference for your Zendesk-style export
KNOWN_PROFILE: Dict[str, Optional[str]] = {
    "Date Identified": "Start Time (Date/Time)",
    "Symptom": "Symptom",
    "Disposition": "Disposition Tag",
    "Description": "Zoom Summary",   # <- enforced when present
    # enrichers (optional)
    "SKU(s)": "Zendesk SKU",
    "Base SKU": "Product Brand",
    "Region": "Queue Country",
    "Serial Number": None,
}

SYNONYMS: Dict[str, List[str]] = {
    "Date Identified": ["date identified","start time (date/time)","start time","created","created date","opened","report date","timestamp"],
    "SKU(s)": ["zendesk sku","sku(s)","skus","sku","product sku","model"],
    "Base SKU": ["product brand","brand","base sku","base product","family sku","platform"],
    "Region": ["queue country","region","market","country","geo","territory","locale"],
    "Symptom": ["symptom","issue","category","failure mode","problem","defect","tag","topic"],
    "Disposition": ["disposition tag","disposition","status","resolution","outcome","result","action"],
    "Description": ["zoom summary","description","details","summary","comments","issue description","text","notes"],
    "Serial Number": ["serial number","sn","s/n","serial","serial no","serial_no"],
}

# -------------------- IO helpers --------------------
@st.cache_data(show_spinner=False)
def read_excel_sheet_bytes(content: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(content), sheet_name=sheet)

@st.cache_data(show_spinner=False)
def read_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), low_memory=False)

# -------------------- Mapping helpers --------------------
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

def enforce_unique_mapping(map_in: Dict[str, Optional[str]]) -> Tuple[bool, List[str]]:
    seen = {}
    duplicates = []
    for canon, src in map_in.items():
        if src is None:
            continue
        if src in seen:
            duplicates.append(src)
        else:
            seen[src] = canon
    return (len(duplicates) == 0, duplicates)

def apply_mapping(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    # force Description -> "Zoom Summary" if present
    if "Zoom Summary" in df.columns:
        mapping["Description"] = "Zoom Summary"
    reverse = {v: k for k, v in mapping.items() if v}
    out = df.rename(columns=reverse).copy()
    # fill missing canonical columns
    for col in REQUIRED_FIELDS:
        if col not in out.columns:
            out[col] = pd.NA

    # robust datetime: parse, force UTC -> naive
    dt = pd.to_datetime(out["Date Identified"], errors="coerce", utc=True)
    out["Date Identified"] = dt.dt.tz_convert(None)

    # strings / categories
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

def highlight(text: str, term: str) -> str:
    if not term:
        return text
    try:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        return pattern.sub(lambda m: f"<span class='hl'>{m.group(0)}</span>", text)
    except re.error:
        return text

# -------------------- 1) Upload --------------------
st.markdown("### 1) Upload")
u1, u2 = st.columns([3,1])
with u1:
    uploaded = st.file_uploader("Upload Excel (.xlsx) or CSV (.csv)", type=["xlsx","csv"], key="u_main")
with u2:
    st.caption("Tip: CSV loads fastest for very large exports.")

if uploaded is None:
    st.info("Upload a file to continue.")
    st.stop()

# -------------------- 2) Read --------------------
try:
    content = uploaded.getvalue()
    if uploaded.name.lower().endswith(".csv"):
        df_raw = read_csv_bytes(content)
        sheets = None
        st.caption("Detected CSV")
    else:
        xls = pd.ExcelFile(io.BytesIO(content))
        sheets = xls.sheet_names
        sel = st.selectbox("Select sheet", sheets, index=0, key="sheet_pick")
        df_raw = read_excel_sheet_bytes(content, sheet=sel)
except Exception as e:
    st.error(f"Failed to read file: {e}")
    st.stop()

columns = [str(c) for c in df_raw.columns]

# -------------------- 3) Mapping (auto + profile save/load) --------------------
st.markdown("### 2) Mapping")
col_map_l, col_map_r = st.columns([2,1])

with col_map_l:
    auto_map = auto_map_known_profile(columns)

    # Backfill missing minimal fields with guesses
    needs_guess = [f for f in MIN_REQUIRED if not auto_map.get(f)]
    if needs_guess:
        guessed = guess_mapping(columns)
        for k in MIN_REQUIRED:
            if k == "Description" and "Zoom Summary" in columns:
                auto_map[k] = "Zoom Summary"
            elif not auto_map.get(k) and guessed.get(k):
                auto_map[k] = guessed[k]

    ok_unique, dupes = enforce_unique_mapping(auto_map)
    missing_final = [f for f in MIN_REQUIRED if not auto_map.get(f)]

    # Mapping Health
    pills = []
    for fld in MIN_REQUIRED:
        pills.append(f"<span class='pill{' pill-bad' if fld in missing_final else ''}'>{fld} → {auto_map.get(fld)}</span>")
    if not ok_unique:
        pills.append(f"<span class='pill pill-bad'>Duplicates: {', '.join(dupes)}</span>")
    st.markdown(" ".join(pills), unsafe_allow_html=True)

    with st.expander("⚙️ Advanced remap (optional)"):
        cols2 = st.columns(2)
        override: Dict[str, Optional[str]] = {}
        for i, field in enumerate(REQUIRED_FIELDS):
            with cols2[i % 2]:
                options = [None] + columns
                idx = (options.index(auto_map.get(field)) if auto_map.get(field) in options else 0)
                override[field] = st.selectbox(field, options, index=idx, key=f"adv_{field}")
        # apply override
        for k in REQUIRED_FIELDS:
            auto_map[k] = override.get(k, auto_map.get(k))
        ok_unique, dupes = enforce_unique_mapping(auto_map)
        missing_final = [f for f in MIN_REQUIRED if not auto_map.get(f)]

        if not ok_unique:
            st.error(f"Each canonical field must map to a different source column. Duplicate(s): {', '.join(dupes)}")
        if missing_final:
            st.error(f"Missing required mapping: {', '.join(missing_final)}")

with col_map_r:
    st.write("**Mapping profiles**")
    prof_dl = st.download_button(
        "💾 Download current mapping (.json)",
        data=json.dumps(auto_map, indent=2).encode("utf-8"),
        file_name="mapping_profile.json",
        mime="application/json",
        key="dl_map"
    )
    prof_up = st.file_uploader("Load mapping (.json)", type=["json"], key="map_upload")
    if prof_up is not None:
        try:
            loaded = json.loads(prof_up.getvalue().decode("utf-8"))
            if isinstance(loaded, dict):
                auto_map.update({k: (v if v in columns else None) for k, v in loaded.items()})
                st.success("Mapping profile loaded. (Only columns present in the file were applied.)")
            else:
                st.warning("Invalid mapping file.")
        except Exception as e:
            st.warning(f"Failed to load mapping: {e}")

# Final gate
if not ok_unique or missing_final:
    st.stop()

# -------------------- 4) Standardize & Options --------------------
df = apply_mapping(df_raw, auto_map)

with st.expander("Options"):
    do_opt = st.checkbox("Optimize memory (downcast + categorize)", value=True, key="opt_mem")
    regex_search = st.checkbox("Use regex for Description search (advanced)", value=False, key="opt_regex")
if do_opt:
    df = optimize_memory(df)

# -------------------- 5) Data Profile & Diagnostics --------------------
st.markdown("### 3) Data Profile & Diagnostics")
cpr1, cpr2, cpr3 = st.columns(3)
with cpr1:
    st.markdown(f"<div class='kpi'><h3>Rows</h3><p>{len(df):,}</p></div>", unsafe_allow_html=True)
with cpr2:
    miss_rate = df["Date Identified"].isna().mean()*100 if "Date Identified" in df.columns else 100.0
    st.markdown(f"<div class='kpi'><h3>Missing Date %</h3><p>{miss_rate:.2f}%</p></div>", unsafe_allow_html=True)
with cpr3:
    dt_min = pd.to_datetime(df["Date Identified"], errors="coerce").min()
    dt_max = pd.to_datetime(df["Date Identified"], errors="coerce").max()
    st.markdown(f"<div class='kpi'><h3>Date Range</h3><p>{str(dt_min)[:10]} → {str(dt_max)[:10]}</p></div>", unsafe_allow_html=True)

with st.expander("🔎 Column quick profile"):
    prof_cols = ["SKU(s)","Base SKU","Region","Symptom","Disposition"]
    rows = []
    for c in prof_cols:
        if c in df.columns:
            rows.append([c, int(df[c].nunique(dropna=True)), int(df[c].isna().sum())])
    if rows:
        prof = pd.DataFrame(rows, columns=["Column","Unique","Missing"])
        st.dataframe(prof, use_container_width=True)
    st.write("Sample rows:")
    st.dataframe(ensure_arrow_safe(df.head(10)), use_container_width=True)

# -------------------- 6) Filters --------------------
st.markdown("### 4) Filters")
with st.sidebar:
    st.header("Filters")
    if "filter_reset" not in st.session_state:
        st.session_state["filter_reset"] = False
    def reset_filters():
        for k in list(st.session_state.keys()):
            if k.startswith("f_"):
                del st.session_state[k]
        st.session_state["filter_reset"] = True
        st.experimental_rerun()
    st.button("Reset filters", on_click=reset_filters, key="btn_reset")

def options_with_counts(series: pd.Series) -> List[str]:
    s = pd.Series(series, dtype="string")
    vc = s.value_counts(dropna=True).sort_values(ascending=False)
    items = [f"{idx} ({cnt})" for idx, cnt in vc.items() if pd.notna(idx)]
    return items

def strip_counts(selected: List[str]) -> List[str]:
    return [re.sub(r"\s+\(\d+\)$","", x) for x in selected]

sku_opt = ["ALL"] + strip_counts(options_with_counts(df["SKU(s)"])) if "SKU(s)" in df.columns else ["ALL"]
base_opt = ["ALL"] + strip_counts(options_with_counts(df["Base SKU"])) if "Base SKU" in df.columns else ["ALL"]
reg_opt = ["ALL"] + strip_counts(options_with_counts(df["Region"])) if "Region" in df.columns else ["ALL"]
sym_opt = ["ALL"] + strip_counts(options_with_counts(df["Symptom"]))
disp_opt = ["ALL"] + strip_counts(options_with_counts(df["Disposition"]))

# Sidebar widgets (unique keys)
with st.sidebar:
    sku_filter = st.multiselect("SKU(s)", options=sku_opt, default=["ALL"], key="f_sku")
    base_filter = st.multiselect("Base SKU", options=base_opt, default=["ALL"], key="f_base")
    region_filter = st.multiselect("Region", options=reg_opt, default=["ALL"], key="f_region")
    symptom_filter = st.multiselect("Symptom", options=sym_opt, default=["ALL"], key="f_symptom")
    disposition_filter = st.multiselect("Disposition", options=disp_opt, default=["ALL"], key="f_disposition")
    tsf_only = st.checkbox("TSF only (_ts_failed / _replaced / tsf)", value=False, key="f_tsf")
    combine_other = st.checkbox("Combine lesser categories into 'Other' (charts only)", value=False, key="f_other")
    search_text = st.text_input("Search 'Description'…", value="", key="f_search")
    st.markdown("---")
    st.header("Date Windows")
    date_range_graph = st.selectbox("Chart range", ["Last Week","Last Month","Last Year","All Time"], index=3, key="f_range")
    table_days = st.number_input("Delta window (table/matrix): days", min_value=7, value=30, step=1, key="f_days")

# -------------------- 7) Time windows --------------------
now = pd.Timestamp.now()
if date_range_graph == "Last Week":
    start_graph, period_label_graph = now - timedelta(days=7), "Last 7 Days"
elif date_range_graph == "Last Month":
    start_graph, period_label_graph = now - timedelta(days=30), "Last 30 Days"
elif date_range_graph == "Last Year":
    start_graph, period_label_graph = now - timedelta(days=365), "Last 365 Days"
else:
    dmin = pd.to_datetime(df["Date Identified"], errors="coerce").dropna()
    start_graph, period_label_graph = (dmin.min() if not dmin.empty else now - timedelta(days=365), "All Time")

table_days = int(table_days)
start_table = now - timedelta(days=table_days)
prev_start_table = start_table - timedelta(days=table_days)

# -------------------- 8) Apply filters --------------------
def build_filter_mask(df: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=df.index, dtype=bool)
    def _apply_in(col, selected):
        nonlocal mask
        if col in df.columns and selected and "ALL" not in selected:
            mask &= df[col].isin(selected)
    _apply_in("SKU(s)", st.session_state.get("f_sku", ["ALL"]))
    _apply_in("Base SKU", st.session_state.get("f_base", ["ALL"]))
    _apply_in("Region", st.session_state.get("f_region", ["ALL"]))
    _apply_in("Symptom", st.session_state.get("f_symptom", ["ALL"]))
    _apply_in("Disposition", st.session_state.get("f_disposition", ["ALL"]))
    if st.session_state.get("f_tsf", False) and "Disposition" in df.columns:
        mask &= df["Disposition"].fillna("").str.contains(r"_ts_failed|_replaced|tsf", case=False, regex=True)
    stext = st.session_state.get("f_search","")
    if stext:
        try:
            mask &= df["Description"].fillna("").str.contains(stext, case=False, regex=False)
        except Exception:
            mask &= df["Description"].fillna("").str.lower().str.contains(str(stext).lower(), na=False)
    return mask

mask = build_filter_mask(df)
df_filtered = df.loc[mask].copy()
df_time = df_filtered.dropna(subset=["Date Identified"]).copy()

# Diagnostics
with st.expander("🔎 Chart diagnostics"):
    st.write("- **Rows after filters**:", len(df_filtered))
    st.write("- **Rows with valid Date Identified**:", len(df_time))
    st.write("- **Non-null counts** — Date:", int(df_filtered["Date Identified"].notna().sum()),
             "| Symptom:", int(df_filtered["Symptom"].notna().sum()),
             "| Disposition:", int(df_filtered["Disposition"].notna().sum()))
    if len(df_time) == 0:
        st.warning("No rows with valid **Date Identified** after mapping/filters. Check mapping, date format, or reset filters.")
    if df_filtered.empty:
        st.info("Filters/time window may be excluding all rows. Try **Reset filters** or set Chart range to **All Time**.")

# -------------------- 9) KPIs & Preview --------------------
st.markdown("### 5) Overview")
k1, k2, k3, k4, k5 = st.columns(5)
with k1: st.markdown(f"<div class='kpi'><h3>Rows (filtered)</h3><p>{len(df_filtered):,}</p></div>", unsafe_allow_html=True)
with k2: st.markdown(f"<div class='kpi'><h3>Unique SKUs</h3><p>{df_filtered['SKU(s)'].nunique()}</p></div>", unsafe_allow_html=True)
with k3: st.markdown(f"<div class='kpi'><h3>Base SKUs</h3><p>{df_filtered['Base SKU'].nunique()}</p></div>", unsafe_allow_html=True)
with k4: st.markdown(f"<div class='kpi'><h3>Regions</h3><p>{df_filtered['Region'].nunique()}</p></div>", unsafe_allow_html=True)
with k5: st.markdown(f"<div class='kpi'><h3>Symptoms</h3><p>{df_filtered['Symptom'].nunique()}</p></div>", unsafe_allow_html=True)

with st.expander("🔍 Preview filtered data"):
    st.dataframe(ensure_arrow_safe(df_filtered.head(1000)), use_container_width=True, height=320)

# -------------------- 10) Trends --------------------
st.markdown("### 6) Trends")
agg_choice = st.selectbox("Aggregate by", ["Day","Week","Month"], index=1, key="agg_choice")
freq = {"Day":"D","Week":"W","Month":"M"}[agg_choice]

def trend_chart(df_in: pd.DataFrame, key_col: str, title: str):
    d = df_in[df_in["Date Identified"] >= start_graph] if not df_in.empty else df_in
    if d.empty or key_col not in d.columns or d[key_col].dropna().empty:
        st.info(f"No data to plot for **{key_col}** in current scope.")
        return
    g = d.groupby([pd.Grouper(key="Date Identified", freq=freq), key_col], dropna=False).size().reset_index(name="Count")
    if g.empty:
        st.info(f"No data after grouping for **{key_col}**.")
        return
    # rolling avg on total counts for context
    tot = g.groupby("Date Identified")["Count"].sum().reset_index(name="Total")
    tot["Roll7"] = tot["Total"].rolling(7 if freq=="D" else 4, min_periods=1).mean()
    fig = px.bar(g, x="Date Identified", y="Count", color=key_col, title=title, template="plotly_white")
    fig.update_layout(barmode="stack", margin=dict(t=40))
    fig.add_trace(go.Scatter(x=tot["Date Identified"], y=tot["Roll7"], name="Rolling avg", mode="lines"))
    st.plotly_chart(fig, use_container_width=True)
    # delta panel
    if freq=="W":
        end = d["Date Identified"].max()
        start_w = end - pd.Timedelta(days=7)
        prev_w = start_w - pd.Timedelta(days=7)
        cur_ct = len(d[d["Date Identified"]>=start_w])
        prev_ct = len(d[(d["Date Identified"]<start_w)&(d["Date Identified"]>=prev_w)])
        delta = cur_ct - prev_ct
        pct = (delta/prev_ct*100.0) if prev_ct>0 else np.nan
        st.caption(f"WoW Δ: {delta:+} ({pct:.2f}% if defined)")
    elif freq=="M":
        # approx 30-day delta
        end = d["Date Identified"].max()
        start_m = end - pd.Timedelta(days=30)
        prev_m = start_m - pd.Timedelta(days=30)
        cur_ct = len(d[d["Date Identified"]>=start_m])
        prev_ct = len(d[(d["Date Identified"]<start_m)&(d["Date Identified"]>=prev_m)])
        delta = cur_ct - prev_ct
        pct = (delta/prev_ct*100.0) if prev_ct>0 else np.nan
        st.caption(f"4W Δ: {delta:+} ({pct:.2f}% if defined)")

df_sym = df_time.copy()
df_disp = df_time.copy()
if st.session_state.get("f_other", False):
    if "Symptom" in df_sym.columns:
        df_sym["Symptom"] = df_sym["Symptom"].astype(str)
        vc = df_sym["Symptom"].value_counts()
        top_syms = set(vc.nlargest(10).index.tolist())
        df_sym["Symptom"] = df_sym["Symptom"].apply(lambda x: x if x in top_syms else "Other")
    if "Disposition" in df_disp.columns:
        df_disp["Disposition"] = df_disp["Disposition"].astype(str)
        vc = df_disp["Disposition"].value_counts()
        top_disp = set(vc.nlargest(10).index.tolist())
        df_disp["Disposition"] = df_disp["Disposition"].apply(lambda x: x if x in top_disp else "Other")

trend_chart(df_sym, "Symptom", f"Symptom Trends Over Time ({period_label_graph})")
trend_chart(df_disp, "Disposition", f"Disposition Trends Over Time ({period_label_graph})")

# -------------------- 11) Disposition mix over time — by Symptom --------------------
st.markdown("### 7) Disposition mix over time — by Symptom")

cA, cB, cC, cD, cE = st.columns([2,1,1,1,1])
with cA:
    symptom_focus = st.selectbox("Focus on a single symptom (optional)",
                                 options=["(All symptoms)"] + sorted(pd.Series(df_filtered["Symptom"], dtype="string").dropna().unique().tolist()),
                                 index=0, key="mix_sym")
with cB:
    mix_agg = st.selectbox("Aggregate by", ["Week","Month"], index=0, key="mix_agg")
with cC:
    mix_chart = st.selectbox("Chart type", ["Stacked area","Stacked bar"], index=0, key="mix_type")
with cD:
    normalize_share = st.checkbox("Normalize to share (%)", value=True, key="mix_norm")
with cE:
    smooth = st.selectbox("Smoothing", ["None","7d roll","4w roll"], index=0, key="mix_smooth")

def disposition_mix(df_in: pd.DataFrame, symptom: Optional[str], freq_code: str, normalize: bool, smooth_opt: str) -> pd.DataFrame:
    d = df_in.dropna(subset=["Date Identified"]).copy()
    if symptom and symptom != "(All symptoms)":
        d = d[d["Symptom"] == symptom]
    if d.empty:
        return pd.DataFrame()
    code = {"Week":"W", "Month":"M"}[freq_code]
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
        topN = st.slider("Top-N symptoms by volume", min_value=3, max_value=12, value=6, step=1, key="sm_topn")
    with cN2:
        normalize_sm = st.checkbox("Normalize to share (%) (small multiples)", value=True, key="sm_norm")
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
                               mime="text/csv",
                               key="dl_sm")
        else:
            st.info("No data available for small multiples in this scope.")

# -------------------- 12) Symptom × Disposition Matrix with deltas --------------------
st.markdown("### 8) Symptom × Disposition Matrix")
if df_time.empty:
    st.info("No dated rows to compute matrix.")
else:
    cur = df_time[df_time["Date Identified"] >= start_table].copy()
    prev = df_time[(df_time["Date Identified"] < start_table) & (df_time["Date Identified"] >= prev_start_table)].copy()

    def build_matrix(dfin: pd.DataFrame, top_sym: int = 20, top_disp: int = 20) -> pd.DataFrame:
        # limit to top categories for readability
        top_s = dfin["Symptom"].value_counts().nlargest(top_sym).index
        top_d = dfin["Disposition"].value_counts().nlargest(top_disp).index
        d = dfin[dfin["Symptom"].isin(top_s) & dfin["Disposition"].isin(top_d)]
        mat = d.groupby(["Symptom","Disposition"]).size().reset_index(name="Count")
        pv = mat.pivot(index="Symptom", columns="Disposition", values="Count").fillna(0).astype(int)
        pv["Row Total"] = pv.sum(axis=1)
        return pv.sort_values("Row Total", ascending=False)

    cur_mat = build_matrix(cur)
    prev_mat = build_matrix(prev)
    # Align
    cur_mat, prev_mat = cur_mat.align(prev_mat, join="outer", fill_value=0)

    delta_mat = cur_mat.iloc[:, :-1] - prev_mat.iloc[:, :-1]
    share_cur = (cur_mat.iloc[:, :-1].div(cur_mat["Row Total"], axis=0).replace([np.inf, -np.inf], 0).fillna(0) * 100).round(2)

    st.write("**Current window (counts)**")
    st.dataframe(cur_mat, use_container_width=True)
    st.write("**Δ vs previous window (counts)**")
    st.dataframe(delta_mat, use_container_width=True)
    st.write("**Current window (row share %)**")
    st.dataframe(share_cur, use_container_width=True)

    st.download_button("⬇️ Download matrix (current counts CSV)",
        data=cur_mat.to_csv().encode("utf-8"), file_name="matrix_current_counts.csv", mime="text/csv", key="dl_mat1")
    st.download_button("⬇️ Download matrix (delta CSV)",
        data=delta_mat.to_csv().encode("utf-8"), file_name="matrix_delta.csv", mime="text/csv", key="dl_mat2")
    st.download_button("⬇️ Download matrix (share % CSV)",
        data=share_cur.to_csv().encode("utf-8"), file_name="matrix_share.csv", mime="text/csv", key="dl_mat3")

# -------------------- 13) Spike Finder (anomalies) --------------------
st.markdown("### 9) Spike Finder (rolling z-score)")
if df_time.empty:
    st.info("No dated rows to compute anomalies.")
else:
    s_sym = st.selectbox("Symptom for anomaly scan", options=sorted(pd.Series(df_filtered["Symptom"], dtype="string").dropna().unique().tolist()), key="anom_sym")
    bucket = st.selectbox("Bucket", ["Week","Month"], index=0, key="anom_bucket")
    code = {"Week":"W","Month":"M"}[bucket]
    d = df_time[df_time["Date Identified"] >= start_graph].copy()
    d = d[d["Symptom"] == s_sym]
    if d.empty:
        st.info("No rows for selected Symptom.")
    else:
        d["Bucket"] = d["Date Identified"].dt.to_period(code).apply(lambda r: r.start_time)
        s = d.groupby("Bucket").size().reset_index(name="Count").sort_values("Bucket")
        s["Mean"] = s["Count"].rolling(6, min_periods=1).mean()
        s["Std"]  = s["Count"].rolling(6, min_periods=1).std(ddof=0).fillna(0)
        s["Z"] = np.where(s["Std"]>0, (s["Count"]-s["Mean"])/s["Std"], 0.0)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=s["Bucket"], y=s["Count"], name="Count"))
        fig.add_trace(go.Scatter(x=s["Bucket"], y=s["Mean"], name="Rolling mean", mode="lines"))
        # highlight spikes
        spikes = s[s["Z"]>=2.0]
        if not spikes.empty:
            fig.add_trace(go.Scatter(x=spikes["Bucket"], y=spikes["Count"], mode="markers",
                                     name="Spike (Z≥2)", marker=dict(size=10, symbol="diamond")))
        fig.update_layout(title=f"Anomaly scan — {s_sym} ({bucket})", template="plotly_white", margin=dict(t=50))
        st.plotly_chart(fig, use_container_width=True)
        st.download_button("⬇️ Download anomaly series (CSV)",
                           data=s.to_csv(index=False).encode("utf-8"),
                           file_name="anomaly_series.csv", mime="text/csv", key="dl_anom")

# -------------------- 14) Descriptions --------------------
st.markdown("### 10) Descriptions")
descs = (df_filtered[["Description","SKU(s)","Base SKU","Region","Disposition","Symptom","Date Identified","Serial Number"]]
         .dropna(subset=["Description"]).sort_values("Date Identified", ascending=False).reset_index(drop=True))
total = len(descs)
items_per = st.selectbox("Items per page", [10,25,50,100], index=0, key="desc_pp")
pages = max(1, (total + items_per - 1)//items_per)
page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1, key="desc_page")
start_idx, end_idx = (page-1)*items_per, min((page-1)*items_per + items_per, total)
term = st.session_state.get("f_search","")

if total == 0:
    st.info("No descriptions match your filters.")
else:
    for _, row in descs.iloc[start_idx:end_idx].iterrows():
        d = row["Date Identified"]
        dstr = pd.to_datetime(d).strftime("%Y-%m-%d") if pd.notnull(d) else "N/A"
        desc_html = highlight(str(row["Description"]), term)
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
          <div style="margin-top:8px;"><strong>Description:</strong> {desc_html}</div>
        </div>
        """, unsafe_allow_html=True)
    st.caption(f"Showing {start_idx+1}–{end_idx} of {total}")

# -------------------- 15) Exports --------------------
st.markdown("### 11) Exports")
exp1, exp2, exp3 = st.columns(3)
with exp1:
    st.download_button("⬇️ Download filtered CSV",
        data=df_filtered.to_csv(index=False).encode("utf-8"),
        file_name="jira_filtered.csv", mime="text/csv", key="dl_filtered")
with exp2:
    view_state = {
        "date_range_graph": date_range_graph,
        "table_days": int(table_days),
        "filters": {
            "sku": st.session_state.get("f_sku",["ALL"]),
            "base": st.session_state.get("f_base",["ALL"]),
            "region": st.session_state.get("f_region",["ALL"]),
            "symptom": st.session_state.get("f_symptom",["ALL"]),
            "disposition": st.session_state.get("f_disposition",["ALL"]),
            "tsf_only": st.session_state.get("f_tsf", False),
            "combine_other": st.session_state.get("f_other", False),
            "search": st.session_state.get("f_search","")
        }
    }
    st.download_button("💾 Save current view (.json)",
        data=json.dumps(view_state, indent=2).encode("utf-8"),
        file_name="jira_view.json", mime="application/json", key="dl_view")
with exp3:
    st.download_button("💾 Save current mapping (.json)",
        data=json.dumps(auto_map, indent=2).encode("utf-8"),
        file_name="mapping_profile.json", mime="application/json", key="dl_map2")
'''
out = Path('/mnt/data/app_pro_v7.py')
out.write_text(code, encoding='utf-8')
print(str(out))

