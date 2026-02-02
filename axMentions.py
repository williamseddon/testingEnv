# app.py
# Streamlit Review Benchmark Dashboard
#
# Works with your "processed output" format:
# - One row = one review
# - One column = Product Name (you mentioned column AX; in files it will be a header like "Product Name")
# - Pillar columns (Noise level, Ease of use, Dry time, etc.) contain one of:
#     positive / negative / neutral / not mentioned
#
# Also supports a "long" format if your file already has columns like:
#   Product Name | Pillar | Label  (or Theme/Attribute instead of Pillar)
#
# Run:
#   pip install streamlit pandas plotly openpyxl numpy
#   streamlit run app.py

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# -----------------------------
# Config / constants
# -----------------------------
VALID_LABELS = ["positive", "negative", "neutral", "not mentioned"]
LABEL_ORDER = ["positive", "neutral", "negative", "not mentioned"]

DEFAULT_PILLARS = [
    "Filter cleaning",
    "Reliability",
    "Powerfulness",
    "Hair health",
    "Scalp health",
    "Hair regrowth",
    "Frizz reduction",
    "Ergonomics",
    "Dry time",
    "Ease of use",
    "Noise level",
    "Price",
]

# Common alternate header spellings
ALT_PILLAR_HEADERS = {
    "frizz": "Frizz reduction",
    "frizz_reduction": "Frizz reduction",
    "hair_regrowth": "Hair regrowth",
    "scalp_health": "Scalp health",
    "hair_health": "Hair health",
    "ease_of_use": "Ease of use",
    "dry_time": "Dry time",
    "noise": "Noise level",
    "noise_level": "Noise level",
    "filter_cleaning": "Filter cleaning",
    "power": "Powerfulness",
    "powerfulness": "Powerfulness",
    "reliability": "Reliability",
    "price": "Price",
    "value": "Price",
}

# Label normalization map
LABEL_MAP = {
    "pos": "positive",
    "+": "positive",
    "good": "positive",
    "great": "positive",
    "positive": "positive",
    "neg": "negative",
    "-": "negative",
    "bad": "negative",
    "poor": "negative",
    "negative": "negative",
    "neutral": "neutral",
    "mixed": "neutral",
    "unclear": "neutral",
    "not mentioned": "not mentioned",
    "not_mentioned": "not mentioned",
    "notmentioned": "not mentioned",
    "n/a": "not mentioned",
    "na": "not mentioned",
    "none": "not mentioned",
    "": "not mentioned",
}


@dataclass
class SummaryTables:
    counts: pd.DataFrame          # product x pillar x label counts
    percents_total: pd.DataFrame  # % of total (includes not mentioned)
    percents_mention: pd.DataFrame  # % among mentions only (excludes not mentioned)
    metrics: pd.DataFrame         # mention rate, net sentiment, neg share, polarization


# -----------------------------
# Helpers
# -----------------------------
def _clean_colname(c: str) -> str:
    c2 = (c or "").strip()
    c2 = re.sub(r"\s+", " ", c2)
    # Normalize common variants for matching, but keep original display elsewhere
    return c2


def _canonicalize_header(header: str) -> str:
    h = _clean_colname(header)
    key = re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_")
    return ALT_PILLAR_HEADERS.get(key, h)


def _normalize_label(x) -> str:
    if pd.isna(x):
        return "not mentioned"
    s = str(x).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("notmentioned", "not mentioned")
    s = s.replace("not_mentioned", "not mentioned")
    # Sometimes people include punctuation
    s = re.sub(r"[^\w\s/+:-]", "", s).strip()
    s = LABEL_MAP.get(s, s)
    # If still not valid, attempt fuzzy containment
    if "not" in s and "mention" in s:
        return "not mentioned"
    if "posit" in s:
        return "positive"
    if "negat" in s:
        return "negative"
    if "neutral" in s:
        return "neutral"
    # Unknown -> treat as not mentioned but surface in QA
    return s


def _is_long_format(df: pd.DataFrame) -> bool:
    cols = {c.lower() for c in df.columns}
    pillar_like = any(x in cols for x in ["pillar", "theme", "attribute", "benchmark", "category"])
    label_like = any(x in cols for x in ["label", "sentiment", "classification", "value"])
    product_like = any(x in cols for x in ["product", "product name", "product_name", "sku", "model"])
    return pillar_like and label_like and product_like


def _guess_review_text_column(df: pd.DataFrame) -> Optional[str]:
    candidates = [
        "review", "review_text", "text", "customer review", "customer_review",
        "review body", "body", "comment", "comments"
    ]
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    # heuristic: long-ish text column
    for c in df.columns:
        if df[c].dtype == object:
            sample = df[c].dropna().astype(str).head(50)
            if len(sample) and sample.map(len).mean() > 120:
                return c
    return None


def _validate_labels(long_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (bad_rows_sample, label_frequencies)"""
    freq = (
        long_df.groupby(["pillar", "label"])["label"]
        .count()
        .rename("count")
        .reset_index()
        .sort_values(["pillar", "count"], ascending=[True, False])
    )
    bad = long_df[~long_df["label"].isin(VALID_LABELS)].copy()
    return bad.head(25), freq


@st.cache_data(show_spinner=False)
def load_file(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    data = uploaded.getvalue()

    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        # try to infer delimiter
        sample = data[:2048].decode("utf-8", errors="ignore")
        sep = "\t" if sample.count("\t") > sample.count(",") else ","
        return pd.read_csv(io.BytesIO(data), sep=sep)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(data))
    raise ValueError("Unsupported file type. Upload CSV/TSV/TXT or Excel (.xlsx/.xls).")


def to_long(
    df: pd.DataFrame,
    product_col: str,
    pillar_cols: List[str],
    review_text_col: Optional[str] = None,
    review_id_col: Optional[str] = None,
) -> pd.DataFrame:
    id_vars = [product_col]
    if review_id_col and review_id_col in df.columns:
        id_vars.append(review_id_col)
    if review_text_col and review_text_col in df.columns:
        id_vars.append(review_text_col)

    # Melt wide -> long
    long_df = df.melt(
        id_vars=id_vars,
        value_vars=pillar_cols,
        var_name="pillar",
        value_name="label_raw",
    ).copy()

    long_df["pillar"] = long_df["pillar"].map(_canonicalize_header)
    long_df["label"] = long_df["label_raw"].map(_normalize_label)

    # Standardize product column name for downstream
    long_df = long_df.rename(columns={product_col: "product"})
    if review_text_col and review_text_col in long_df.columns:
        long_df = long_df.rename(columns={review_text_col: "review_text"})
    if review_id_col and review_id_col in long_df.columns:
        long_df = long_df.rename(columns={review_id_col: "review_id"})

    return long_df.drop(columns=["label_raw"])


def from_long(df: pd.DataFrame, product_col: str, pillar_col: str, label_col: str,
              review_text_col: Optional[str] = None, review_id_col: Optional[str] = None) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns={
        product_col: "product",
        pillar_col: "pillar",
        label_col: "label_raw",
    })
    if review_text_col and review_text_col in out.columns:
        out = out.rename(columns={review_text_col: "review_text"})
    if review_id_col and review_id_col in out.columns:
        out = out.rename(columns={review_id_col: "review_id"})

    out["pillar"] = out["pillar"].map(_canonicalize_header)
    out["label"] = out["label_raw"].map(_normalize_label)
    return out.drop(columns=["label_raw"])


def compute_summary(long_df: pd.DataFrame) -> SummaryTables:
    # Counts
    counts = (
        long_df.groupby(["product", "pillar", "label"])["label"]
        .count()
        .rename("count")
        .reset_index()
    )

    # Full grid to ensure missing labels appear as zeros
    products = sorted(long_df["product"].dropna().unique().tolist())
    pillars = sorted(long_df["pillar"].dropna().unique().tolist())
    grid = pd.MultiIndex.from_product([products, pillars, VALID_LABELS], names=["product", "pillar", "label"])
    counts_full = (
        counts.set_index(["product", "pillar", "label"])
        .reindex(grid, fill_value=0)
        .reset_index()
    )

    # Totals
    totals = counts_full.groupby(["product", "pillar"])["count"].sum().rename("total").reset_index()
    merged = counts_full.merge(totals, on=["product", "pillar"], how="left")
    merged["pct_total"] = np.where(merged["total"] > 0, merged["count"] / merged["total"], 0.0)

    # Mention-only denominator (exclude not mentioned)
    mention_totals = (
        merged[merged["label"] != "not mentioned"]
        .groupby(["product", "pillar"])["count"]
        .sum()
        .rename("mentions")
        .reset_index()
    )
    merged = merged.merge(mention_totals, on=["product", "pillar"], how="left")
    merged["mentions"] = merged["mentions"].fillna(0).astype(int)
    merged["pct_mentions"] = np.where(
        (merged["label"] != "not mentioned") & (merged["mentions"] > 0),
        merged["count"] / merged["mentions"],
        np.where(merged["label"] == "not mentioned", np.nan, 0.0)
    )

    # Pivot tables
    pivot_counts = merged.pivot_table(index=["product", "pillar"], columns="label", values="count", fill_value=0)
    pivot_pct_total = merged.pivot_table(index=["product", "pillar"], columns="label", values="pct_total", fill_value=0.0)

    # pct among mentions only (exclude not mentioned column)
    mention_view = merged[merged["label"] != "not mentioned"].copy()
    pivot_pct_mentions = mention_view.pivot_table(index=["product", "pillar"], columns="label", values="pct_mentions", fill_value=0.0)

    # Metrics
    def safe_div(a, b):
        return np.where(b > 0, a / b, 0.0)

    pos = pivot_counts.get("positive", 0)
    neg = pivot_counts.get("negative", 0)
    neu = pivot_counts.get("neutral", 0)
    nm = pivot_counts.get("not mentioned", 0)

    total = pos + neg + neu + nm
    mentions = pos + neg + neu

    mention_rate = safe_div(mentions, total)
    net_sentiment = safe_div((pos - neg), mentions)  # -1..+1 among mentions
    neg_share_mentions = safe_div(neg, mentions)
    polarization = safe_div((pos + neg), mentions)  # 0..1 among mentions

    metrics = pd.DataFrame({
        "mentions": mentions.astype(int),
        "total": total.astype(int),
        "mention_rate": mention_rate.astype(float),
        "net_sentiment": net_sentiment.astype(float),
        "neg_share_mentions": neg_share_mentions.astype(float),
        "polarization": polarization.astype(float),
    }).reset_index()

    return SummaryTables(
        counts=pivot_counts.reset_index(),
        percents_total=pivot_pct_total.reset_index(),
        percents_mention=pivot_pct_mentions.reset_index(),
        metrics=metrics,
    )


def top_differences(metrics: pd.DataFrame, products: List[str], top_n: int = 8) -> pd.DataFrame:
    df = metrics.copy()
    df = df[df["product"].isin(products)]
    # pivot net sentiment per pillar
    pivot = df.pivot_table(index="pillar", columns="product", values="net_sentiment", aggfunc="mean")
    # compute range across products per pillar
    diff = (pivot.max(axis=1) - pivot.min(axis=1)).rename("net_gap").to_frame()
    # identify best/worst
    diff["winner"] = pivot.idxmax(axis=1)
    diff["loser"] = pivot.idxmin(axis=1)
    diff["winner_net"] = pivot.max(axis=1)
    diff["loser_net"] = pivot.min(axis=1)
    out = diff.sort_values("net_gap", ascending=False).head(top_n).reset_index()
    return out


def fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def fmt_score(x: float) -> str:
    return f"{x:+.2f}"


# -----------------------------
# UI
# -----------------------------
st.set_page_config(
    page_title="Review Benchmark Explorer",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🧪 Review Benchmark Explorer (Processed Output)")
st.caption(
    "Upload your processed review labeling file and benchmark products across themes (Noise, Dry Time, Reliability, etc.). "
    "Built for wide tables (one pillar per column) and also supports long format."
)

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])

if not uploaded:
    st.info("Upload a file to begin. Your pillar columns should contain: positive / negative / neutral / not mentioned.")
    st.stop()

df_raw = load_file(uploaded)
df_raw.columns = [_clean_colname(c) for c in df_raw.columns]

st.sidebar.markdown("---")
st.sidebar.subheader("Format + columns")

# Detect long vs wide
long_detected = _is_long_format(df_raw)

review_text_guess = _guess_review_text_column(df_raw)

if long_detected:
    st.sidebar.success("Detected LONG format (Product + Pillar + Label).")
    # pick product/pillar/label cols
    cols = df_raw.columns.tolist()
    product_col = st.sidebar.selectbox("Product column", cols, index=cols.index("Product Name") if "Product Name" in cols else 0)
    pillar_candidates = [c for c in cols if c.lower() in ["pillar", "theme", "attribute", "benchmark", "category"]]
    label_candidates = [c for c in cols if c.lower() in ["label", "sentiment", "classification", "value"]]
    pillar_col = st.sidebar.selectbox("Pillar column", cols, index=cols.index(pillar_candidates[0]) if pillar_candidates else 0)
    label_col = st.sidebar.selectbox("Label column", cols, index=cols.index(label_candidates[0]) if label_candidates else 0)

    review_text_col = st.sidebar.selectbox(
        "Optional: review text column",
        ["(none)"] + cols,
        index=(["(none)"] + cols).index(review_text_guess) if review_text_guess in cols else 0,
    )
    review_text_col = None if review_text_col == "(none)" else review_text_col

    review_id_col = st.sidebar.selectbox("Optional: review id column", ["(none)"] + cols, index=0)
    review_id_col = None if review_id_col == "(none)" else review_id_col

    long_df = from_long(df_raw, product_col, pillar_col, label_col, review_text_col, review_id_col)

else:
    st.sidebar.success("Detected WIDE format (Product column + pillar columns).")
    cols = df_raw.columns.tolist()

    # Guess product col
    product_guess = None
    for cand in ["Product Name", "Product", "product", "Model", "SKU"]:
        if cand in cols:
            product_guess = cand
            break
    product_col = st.sidebar.selectbox("Product column", cols, index=cols.index(product_guess) if product_guess in cols else 0)

    # Guess pillar columns from DEFAULT_PILLARS + known columns
    canonical_cols = {c: _canonicalize_header(c) for c in cols}
    rev_map = {}
    for orig, canon in canonical_cols.items():
        rev_map.setdefault(canon, []).append(orig)

    default_selected = []
    for p in DEFAULT_PILLARS:
        if p in rev_map:
            # pick the first matching original column
            default_selected.append(rev_map[p][0])

    pillar_cols = st.sidebar.multiselect(
        "Pillar columns",
        options=[c for c in cols if c != product_col],
        default=default_selected if default_selected else [c for c in cols if c != product_col],
    )

    review_text_col = st.sidebar.selectbox(
        "Optional: review text column",
        ["(none)"] + cols,
        index=(["(none)"] + cols).index(review_text_guess) if review_text_guess in cols else 0,
    )
    review_text_col = None if review_text_col == "(none)" else review_text_col

    review_id_col = st.sidebar.selectbox("Optional: review id column", ["(none)"] + cols, index=0)
    review_id_col = None if review_id_col == "(none)" else review_id_col

    long_df = to_long(df_raw, product_col, pillar_cols, review_text_col, review_id_col)

# Normalize labels into 4 options; keep a QA view for unknowns
bad_rows, label_freq = _validate_labels(long_df)

st.sidebar.markdown("---")
st.sidebar.subheader("Data QA")

with st.sidebar.expander("Label frequency (by pillar)", expanded=False):
    st.dataframe(label_freq, use_container_width=True, height=300)

if len(bad_rows):
    st.sidebar.warning(f"Found {len(long_df[~long_df['label'].isin(VALID_LABELS)])} rows with non-standard labels.")
    with st.sidebar.expander("Show sample of non-standard labels", expanded=False):
        st.dataframe(bad_rows, use_container_width=True)
    st.sidebar.info("Those labels are kept as-is for QA. You can fix upstream or map them in LABEL_MAP in the app.")

# Filter to valid labels for benchmark metrics (keeps the 4-class contract)
bench_df = long_df[long_df["label"].isin(VALID_LABELS)].copy()

# Compute summary
summary = compute_summary(bench_df)

# Sidebar filters
st.sidebar.markdown("---")
st.sidebar.subheader("Benchmark controls")

all_products = sorted(summary.metrics["product"].unique().tolist())
selected_products = st.sidebar.multiselect("Products to include", all_products, default=all_products[:3] if len(all_products) >= 3 else all_products)

all_pillars = sorted(summary.metrics["pillar"].unique().tolist())
# Keep your preferred ordering when possible
preferred_order = [p for p in DEFAULT_PILLARS if p in all_pillars]
remaining = [p for p in all_pillars if p not in preferred_order]
pillar_order = preferred_order + remaining

selected_pillars = st.sidebar.multiselect("Pillars to include", pillar_order, default=pillar_order)

metric_choice = st.sidebar.selectbox(
    "Primary score (heatmap)",
    [
        "net_sentiment (pos-neg among mentions)",
        "mention_rate (talked about vs not mentioned)",
        "neg_share_mentions (complaints concentration)",
        "polarization (love/hate intensity)",
    ],
    index=0,
)

# Filter metric table
m = summary.metrics.copy()
m = m[m["product"].isin(selected_products) & m["pillar"].isin(selected_pillars)].copy()

# -----------------------------
# Top-level KPI cards
# -----------------------------
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

overall = m.copy()
overall_mentions = int(overall["mentions"].sum())
overall_total = int(overall["total"].sum())
overall_mention_rate = overall_mentions / overall_total if overall_total else 0.0
overall_net = np.average(overall["net_sentiment"], weights=overall["mentions"].clip(lower=0)) if overall_mentions else 0.0
overall_neg = np.average(overall["neg_share_mentions"], weights=overall["mentions"].clip(lower=0)) if overall_mentions else 0.0
overall_pol = np.average(overall["polarization"], weights=overall["mentions"].clip(lower=0)) if overall_mentions else 0.0

kpi1.metric("Total reviews x pillars", f"{overall_total:,}")
kpi2.metric("Mention rate (overall)", fmt_pct(overall_mention_rate))
kpi3.metric("Net sentiment (overall)", fmt_score(overall_net))
kpi4.metric("Neg share among mentions", fmt_pct(overall_neg))

st.markdown("---")

# -----------------------------
# Heatmaps
# -----------------------------
def metric_to_matrix(df_metrics: pd.DataFrame, value_col: str) -> pd.DataFrame:
    mat = df_metrics.pivot_table(index="product", columns="pillar", values=value_col, aggfunc="mean")
    # order
    mat = mat.reindex(index=selected_products)
    mat = mat.reindex(columns=selected_pillars)
    return mat


metric_map = {
    "net_sentiment (pos-neg among mentions)": "net_sentiment",
    "mention_rate (talked about vs not mentioned)": "mention_rate",
    "neg_share_mentions (complaints concentration)": "neg_share_mentions",
    "polarization (love/hate intensity)": "polarization",
}
value_col = metric_map[metric_choice]
mat = metric_to_matrix(m, value_col)

left, right = st.columns([1.4, 1])

with left:
    st.subheader("📌 Benchmark heatmap")
    if value_col == "net_sentiment":
        color_scale = "RdYlGn"
        zmin, zmax = -1, 1
        title = "Net sentiment (positive − negative) among mentions"
    elif value_col in ["mention_rate", "neg_share_mentions", "polarization"]:
        color_scale = "Blues" if value_col == "mention_rate" else "Reds"
        zmin, zmax = 0, 1
        title = metric_choice
    else:
        color_scale = "Viridis"
        zmin, zmax = None, None
        title = metric_choice

    fig = px.imshow(
        mat,
        aspect="auto",
        color_continuous_scale=color_scale,
        zmin=zmin,
        zmax=zmax,
        labels=dict(x="Pillar", y="Product", color=value_col),
    )
    fig.update_layout(
        height=520,
        margin=dict(l=10, r=10, t=50, b=10),
        title=title,
    )
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("🏁 Biggest differentiators")
    if len(selected_products) >= 2:
        diffs = top_differences(m, selected_products, top_n=10)
        if len(diffs):
            # make it more readable
            diffs_display = diffs.copy()
            diffs_display["winner_net"] = diffs_display["winner_net"].map(fmt_score)
            diffs_display["loser_net"] = diffs_display["loser_net"].map(fmt_score)
            diffs_display["net_gap"] = diffs_display["net_gap"].map(lambda x: f"{x:.2f}")
            st.dataframe(diffs_display, use_container_width=True, height=320)
        else:
            st.info("Not enough data to compute differentiators.")
    else:
        st.info("Select at least 2 products to compute differentiators.")

    st.markdown("### Quick interpretation")
    st.write(
        "- **Net sentiment**: +1 means all-positive mentions; -1 means all-negative mentions.\n"
        "- **Mention rate**: how often people talk about this theme at all.\n"
        "- **Neg share**: within mentions, how complaint-heavy the theme is.\n"
        "- **Polarization**: higher means stronger love/hate vs mostly neutral."
    )

st.markdown("---")

# -----------------------------
# Pillar deep dive
# -----------------------------
st.subheader("🔎 Pillar deep dive")
col1, col2, col3 = st.columns([1, 1, 1.2])

with col1:
    pillar = st.selectbox("Choose a pillar", selected_pillars, index=selected_pillars.index("Noise level") if "Noise level" in selected_pillars else 0)
with col2:
    view = st.selectbox("View", ["% of total (includes not mentioned)", "% among mentions only", "Counts"], index=0)
with col3:
    normalize = st.selectbox("Chart style", ["Stacked bar", "Grouped bar"], index=0)

# Build distribution table for the pillar
counts = summary.counts.copy()
counts = counts[counts["product"].isin(selected_products) & (counts["pillar"] == pillar)].copy()

pct_total = summary.percents_total.copy()
pct_total = pct_total[pct_total["product"].isin(selected_products) & (pct_total["pillar"] == pillar)].copy()

pct_mentions = summary.percents_mention.copy()
pct_mentions = pct_mentions[pct_mentions["product"].isin(selected_products) & (pct_mentions["pillar"] == pillar)].copy()

def wide_to_long_for_plot(df_wide: pd.DataFrame, value_name: str) -> pd.DataFrame:
    cols = [c for c in VALID_LABELS if c in df_wide.columns]
    out = df_wide.melt(id_vars=["product", "pillar"], value_vars=cols, var_name="label", value_name=value_name)
    out["label"] = pd.Categorical(out["label"], categories=LABEL_ORDER, ordered=True)
    return out.sort_values(["product", "label"])

if view.startswith("% of total"):
    plot_df = wide_to_long_for_plot(pct_total, "value")
    ytitle = "Percent of total reviews"
    plot_df["value"] = plot_df["value"] * 100
elif view.startswith("% among mentions"):
    plot_df = wide_to_long_for_plot(pct_mentions, "value")
    ytitle = "Percent among mentions"
    plot_df["value"] = plot_df["value"] * 100
else:
    plot_df = wide_to_long_for_plot(counts, "value")
    ytitle = "Count"

# Ensure product order
plot_df["product"] = pd.Categorical(plot_df["product"], categories=selected_products, ordered=True)
plot_df = plot_df.sort_values(["product", "label"])

if normalize == "Stacked bar":
    fig2 = px.bar(
        plot_df,
        x="product",
        y="value",
        color="label",
        barmode="stack",
        category_orders={"label": LABEL_ORDER, "product": selected_products},
        color_discrete_map={
            "positive": "#2ecc71",
            "neutral": "#95a5a6",
            "negative": "#e74c3c",
            "not mentioned": "#bdc3c7",
        },
        title=f"{pillar} distribution by product",
    )
else:
    fig2 = px.bar(
        plot_df,
        x="product",
        y="value",
        color="label",
        barmode="group",
        category_orders={"label": LABEL_ORDER, "product": selected_products},
        color_discrete_map={
            "positive": "#2ecc71",
            "neutral": "#95a5a6",
            "negative": "#e74c3c",
            "not mentioned": "#bdc3c7",
        },
        title=f"{pillar} distribution by product",
    )

fig2.update_layout(height=420, yaxis_title=ytitle, xaxis_title="Product", margin=dict(l=10, r=10, t=50, b=10))
st.plotly_chart(fig2, use_container_width=True)

# -----------------------------
# Product profile radar
# -----------------------------
st.subheader("🧭 Product profiles")
c1, c2 = st.columns([1, 2])

with c1:
    product_focus = st.selectbox("Choose a product", selected_products, index=0)
    radar_metric = st.selectbox(
        "Radar metric",
        ["net_sentiment", "mention_rate", "neg_share_mentions", "polarization"],
        index=0,
    )

prof = m[m["product"] == product_focus].copy()
prof = prof.set_index("pillar").reindex(selected_pillars).reset_index()

# Radar values
r = prof[radar_metric].fillna(0).astype(float).tolist()
theta = prof["pillar"].tolist()

radar = go.Figure()
radar.add_trace(go.Scatterpolar(
    r=r + [r[0]] if len(r) else r,
    theta=theta + [theta[0]] if len(theta) else theta,
    fill="toself",
    name=product_focus,
))
radar.update_layout(
    polar=dict(
        radialaxis=dict(visible=True, range=[-1, 1] if radar_metric == "net_sentiment" else [0, 1])
    ),
    showlegend=False,
    height=520,
    margin=dict(l=10, r=10, t=30, b=10),
)
with c2:
    st.plotly_chart(radar, use_container_width=True)

# -----------------------------
# Drilldown: show underlying rows
# -----------------------------
st.subheader("🧷 Drilldown to rows (optional review text)")
d1, d2, d3, d4 = st.columns([1, 1, 1, 1.2])

with d1:
    drill_product = st.selectbox("Product", selected_products, index=0, key="drill_product")
with d2:
    drill_pillar = st.selectbox("Pillar", selected_pillars, index=0, key="drill_pillar")
with d3:
    drill_label = st.selectbox("Label", VALID_LABELS, index=0, key="drill_label")
with d4:
    max_rows = st.slider("Max rows to show", 10, 500, 100, step=10)

drill = bench_df[(bench_df["product"] == drill_product) & (bench_df["pillar"] == drill_pillar) & (bench_df["label"] == drill_label)].copy()
drill = drill.head(max_rows)

cols_to_show = ["product", "pillar", "label"]
if "review_id" in drill.columns:
    cols_to_show.insert(0, "review_id")
if "review_text" in drill.columns:
    cols_to_show.append("review_text")

if len(drill) == 0:
    st.info("No rows match this drilldown selection.")
else:
    st.dataframe(drill[cols_to_show], use_container_width=True, height=320)

# -----------------------------
# Downloads
# -----------------------------
st.markdown("---")
st.subheader("⬇️ Download benchmark tables")

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

dl1, dl2, dl3 = st.columns(3)
with dl1:
    st.download_button(
        "Download metrics (CSV)",
        data=df_to_csv_bytes(summary.metrics),
        file_name="benchmark_metrics.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl2:
    # Long-form distribution (counts and pct_total)
    dist = summary.percents_total.merge(summary.counts, on=["product", "pillar"], suffixes=("_pct_total", "_count"))
    st.download_button(
        "Download distributions (CSV)",
        data=df_to_csv_bytes(dist),
        file_name="benchmark_distributions.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl3:
    st.download_button(
        "Download cleaned long format (CSV)",
        data=df_to_csv_bytes(bench_df),
        file_name="bench_long_clean.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.caption(
    "Tip: If your Product Name column is truly AX in Excel, that’s fine—Excel letters don’t carry into CSV/XLSX parsing. "
    "Just select the correct header in the sidebar."
)
