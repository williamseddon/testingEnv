# app.py
# Streamlit Review Benchmark Dashboard (Enhanced + FIXED column collisions)
#
# ✅ Includes:
# - % mentions per pillar (by product)
# - Average star rating per pillar (by product), incl.:
#     - avg stars among mentions (label != "not mentioned")
#     - avg stars by sentiment label (positive/negative/neutral/not mentioned)
# - Strong product-vs-product comparison views
#
# ✅ FIX:
# - Renames avg-stars-by-label columns to avoid collisions with % distribution columns.
#   (e.g., avg_stars_positive instead of "positive")
#
# Input formats supported:
# 1) WIDE: one row per review, with Product column + pillar columns (labels)
# 2) LONG: Product | Pillar | Label (and optional Stars, Review Text, Review ID)
#
# Labels must be one of:
#   positive / negative / neutral / not mentioned
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
import streamlit as st

# -----------------------------
# Constants
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

STAR_GUESS_CANDIDATES = [
    "stars", "star", "rating", "star rating", "star_rating",
    "overall rating", "overall_rating", "review rating", "review_rating",
    "score",
]

# -----------------------------
# Data containers
# -----------------------------
@dataclass
class SummaryTables:
    counts: pd.DataFrame
    percents_total: pd.DataFrame
    percents_mention: pd.DataFrame
    metrics: pd.DataFrame
    stars_metrics: Optional[pd.DataFrame]
    product_stars: Optional[pd.DataFrame]


# -----------------------------
# Helpers
# -----------------------------
def _clean_colname(c: str) -> str:
    c2 = (c or "").strip()
    c2 = re.sub(r"\s+", " ", c2)
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
    s = s.replace("notmentioned", "not mentioned").replace("not_mentioned", "not mentioned")
    s = re.sub(r"[^\w\s/+:-]", "", s).strip()
    s = LABEL_MAP.get(s, s)
    if "not" in s and "mention" in s:
        return "not mentioned"
    if "posit" in s:
        return "positive"
    if "negat" in s:
        return "negative"
    if "neutral" in s:
        return "neutral"
    return s


def _parse_stars(x) -> float:
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        v = float(x)
        return v if np.isfinite(v) else np.nan
    s = str(x).strip().lower()
    m = re.findall(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return np.nan
    try:
        return float(m[0])
    except Exception:
        return np.nan


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
    for c in df.columns:
        if df[c].dtype == object:
            sample = df[c].dropna().astype(str).head(50)
            if len(sample) and sample.map(len).mean() > 120:
                return c
    return None


def _guess_star_column(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in STAR_GUESS_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            s = df[c].dropna()
            if len(s) >= 10 and s.between(1, 5).mean() > 0.85:
                return c
    return None


def _validate_labels(long_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
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
    stars_col: Optional[str] = None,
) -> pd.DataFrame:
    id_vars = [product_col]
    if review_id_col and review_id_col in df.columns:
        id_vars.append(review_id_col)
    if review_text_col and review_text_col in df.columns:
        id_vars.append(review_text_col)
    if stars_col and stars_col in df.columns:
        id_vars.append(stars_col)

    long_df = df.melt(
        id_vars=id_vars,
        value_vars=pillar_cols,
        var_name="pillar",
        value_name="label_raw",
    ).copy()

    long_df["pillar"] = long_df["pillar"].map(_canonicalize_header)
    long_df["label"] = long_df["label_raw"].map(_normalize_label)

    rename_map = {product_col: "product"}
    if review_text_col and review_text_col in long_df.columns:
        rename_map[review_text_col] = "review_text"
    if review_id_col and review_id_col in long_df.columns:
        rename_map[review_id_col] = "review_id"
    if stars_col and stars_col in long_df.columns:
        rename_map[stars_col] = "stars"

    long_df = long_df.rename(columns=rename_map).drop(columns=["label_raw"])

    if "stars" in long_df.columns:
        long_df["stars"] = long_df["stars"].map(_parse_stars)

    return long_df


def from_long(
    df: pd.DataFrame,
    product_col: str,
    pillar_col: str,
    label_col: str,
    review_text_col: Optional[str] = None,
    review_id_col: Optional[str] = None,
    stars_col: Optional[str] = None,
) -> pd.DataFrame:
    out = df.copy()
    rename_map = {
        product_col: "product",
        pillar_col: "pillar",
        label_col: "label_raw",
    }
    if review_text_col and review_text_col in out.columns:
        rename_map[review_text_col] = "review_text"
    if review_id_col and review_id_col in out.columns:
        rename_map[review_id_col] = "review_id"
    if stars_col and stars_col in out.columns:
        rename_map[stars_col] = "stars"

    out = out.rename(columns=rename_map)
    out["pillar"] = out["pillar"].map(_canonicalize_header)
    out["label"] = out["label_raw"].map(_normalize_label)
    out = out.drop(columns=["label_raw"])

    if "stars" in out.columns:
        out["stars"] = out["stars"].map(_parse_stars)

    return out


def compute_summary(long_df: pd.DataFrame) -> SummaryTables:
    counts = (
        long_df.groupby(["product", "pillar", "label"])["label"]
        .count()
        .rename("count")
        .reset_index()
    )

    products = sorted(long_df["product"].dropna().unique().tolist())
    pillars = sorted(long_df["pillar"].dropna().unique().tolist())
    grid = pd.MultiIndex.from_product([products, pillars, VALID_LABELS], names=["product", "pillar", "label"])

    counts_full = (
        counts.set_index(["product", "pillar", "label"])
        .reindex(grid, fill_value=0)
        .reset_index()
    )

    totals = counts_full.groupby(["product", "pillar"])["count"].sum().rename("total").reset_index()
    merged = counts_full.merge(totals, on=["product", "pillar"], how="left")
    merged["pct_total"] = np.where(merged["total"] > 0, merged["count"] / merged["total"], 0.0)

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
        np.where(merged["label"] == "not mentioned", np.nan, 0.0),
    )

    pivot_counts = merged.pivot_table(index=["product", "pillar"], columns="label", values="count", fill_value=0).reset_index()
    pivot_pct_total = merged.pivot_table(index=["product", "pillar"], columns="label", values="pct_total", fill_value=0.0).reset_index()

    mention_view = merged[merged["label"] != "not mentioned"].copy()
    pivot_pct_mentions = mention_view.pivot_table(index=["product", "pillar"], columns="label", values="pct_mentions", fill_value=0.0).reset_index()

    pc = pivot_counts.set_index(["product", "pillar"])
    pos = pc.get("positive", 0)
    neg = pc.get("negative", 0)
    neu = pc.get("neutral", 0)
    nm = pc.get("not mentioned", 0)

    total = pos + neg + neu + nm
    mentions = pos + neg + neu

    def safe_div(a, b):
        return np.where(b > 0, a / b, 0.0)

    mention_rate = safe_div(mentions, total)
    net_sentiment = safe_div((pos - neg), mentions)
    neg_share_mentions = safe_div(neg, mentions)
    polarization = safe_div((pos + neg), mentions)

    metrics = pd.DataFrame({
        "mentions": mentions.astype(int),
        "total_reviews": total.astype(int),
        "mention_rate": mention_rate.astype(float),
        "net_sentiment": net_sentiment.astype(float),
        "neg_share_mentions": neg_share_mentions.astype(float),
        "polarization": polarization.astype(float),
        "pos_count": pos.astype(int),
        "neu_count": neu.astype(int),
        "neg_count": neg.astype(int),
        "not_mentioned_count": nm.astype(int),
    }).reset_index()

    # ⭐ Stars metrics (optional) — FIXED naming to avoid collisions
    stars_metrics = None
    product_stars = None
    if "stars" in long_df.columns and long_df["stars"].notna().any():
        if "review_id" in long_df.columns:
            tmp = long_df[["product", "review_id", "stars"]].dropna().drop_duplicates(subset=["product", "review_id"])
            product_stars = tmp.groupby("product")["stars"].mean().rename("avg_stars_overall").reset_index()
            product_stars["n_reviews_with_stars"] = tmp.groupby("product")["stars"].size().values
        else:
            if "review_text" in long_df.columns:
                tmp = long_df[["product", "review_text", "stars"]].dropna().drop_duplicates(subset=["product", "review_text"])
                product_stars = tmp.groupby("product")["stars"].mean().rename("avg_stars_overall").reset_index()
                product_stars["n_reviews_with_stars"] = tmp.groupby("product")["stars"].size().values
            else:
                product_stars = long_df.groupby("product")["stars"].mean().rename("avg_stars_overall").reset_index()
                product_stars["n_reviews_with_stars"] = long_df.groupby("product")["stars"].apply(lambda s: s.notna().sum()).values

        mention_rows = long_df[(long_df["label"].isin(VALID_LABELS)) & (long_df["label"] != "not mentioned") & long_df["stars"].notna()].copy()
        avg_mentions = (
            mention_rows.groupby(["product", "pillar"])["stars"]
            .mean()
            .rename("avg_stars_mentions")
            .reset_index()
        )

        label_rows = long_df[(long_df["label"].isin(VALID_LABELS)) & long_df["stars"].notna()].copy()
        avg_by_label = (
            label_rows.groupby(["product", "pillar", "label"])["stars"]
            .mean()
            .rename("avg_stars")
            .reset_index()
        )

        avg_by_label_pivot = (
            avg_by_label.pivot_table(index=["product", "pillar"], columns="label", values="avg_stars")
            .reset_index()
        )

        # ✅ Rename label columns to avoid collisions with pct tables (positive/negative/etc.)
        rename_map = {}
        for lbl in VALID_LABELS:
            if lbl in avg_by_label_pivot.columns:
                rename_map[lbl] = f"avg_stars_{lbl.replace(' ', '_')}"  # avg_stars_not_mentioned
        avg_by_label_pivot = avg_by_label_pivot.rename(columns=rename_map)

        stars_metrics = avg_by_label_pivot.merge(avg_mentions, on=["product", "pillar"], how="left")

    return SummaryTables(
        counts=pivot_counts,
        percents_total=pivot_pct_total,
        percents_mention=pivot_pct_mentions,
        metrics=metrics,
        stars_metrics=stars_metrics,
        product_stars=product_stars,
    )


def top_differences(metrics: pd.DataFrame, products: List[str], top_n: int = 10) -> pd.DataFrame:
    df = metrics[metrics["product"].isin(products)].copy()
    pivot = df.pivot_table(index="pillar", columns="product", values="net_sentiment", aggfunc="mean")
    diff = (pivot.max(axis=1) - pivot.min(axis=1)).rename("net_gap").to_frame()
    diff["winner"] = pivot.idxmax(axis=1)
    diff["loser"] = pivot.idxmin(axis=1)
    diff["winner_net"] = pivot.max(axis=1)
    diff["loser_net"] = pivot.min(axis=1)
    out = diff.sort_values("net_gap", ascending=False).head(top_n).reset_index()
    return out


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Review Benchmark Explorer", layout="wide", initial_sidebar_state="expanded")

st.title("🧪 Review Benchmark Explorer (with % Mentions + Avg Stars)")
st.caption(
    "Upload your processed output and benchmark products across themes (Noise, Dry Time, Reliability, Price, etc.). "
    "Includes % mentions and average star rating per theme (if a stars column exists)."
)

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])

if not uploaded:
    st.info("Upload a file to begin. Pillar cells should be: positive / negative / neutral / not mentioned.")
    st.stop()

df_raw = load_file(uploaded)
df_raw.columns = [_clean_colname(c) for c in df_raw.columns]

long_detected = _is_long_format(df_raw)
review_text_guess = _guess_review_text_column(df_raw)
stars_guess = _guess_star_column(df_raw)

st.sidebar.markdown("---")
st.sidebar.subheader("Columns")

if long_detected:
    st.sidebar.success("Detected LONG format (Product + Pillar + Label).")
    cols = df_raw.columns.tolist()

    product_col = st.sidebar.selectbox(
        "Product column",
        cols,
        index=cols.index("Product Name") if "Product Name" in cols else 0,
    )

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

    stars_col = st.sidebar.selectbox(
        "Optional: stars/rating column",
        ["(none)"] + cols,
        index=(["(none)"] + cols).index(stars_guess) if stars_guess in cols else 0,
    )
    stars_col = None if stars_col == "(none)" else stars_col

    long_df = from_long(df_raw, product_col, pillar_col, label_col, review_text_col, review_id_col, stars_col)

else:
    st.sidebar.success("Detected WIDE format (Product column + pillar columns).")
    cols = df_raw.columns.tolist()

    product_guess = None
    for cand in ["Product Name", "Product", "product", "Model", "SKU"]:
        if cand in cols:
            product_guess = cand
            break
    product_col = st.sidebar.selectbox("Product column", cols, index=cols.index(product_guess) if product_guess in cols else 0)

    stars_col = st.sidebar.selectbox(
        "Optional: stars/rating column",
        ["(none)"] + cols,
        index=(["(none)"] + cols).index(stars_guess) if stars_guess in cols else 0,
    )
    stars_col = None if stars_col == "(none)" else stars_col

    excluded = {product_col}
    if stars_col:
        excluded.add(stars_col)

    canonical_cols = {c: _canonicalize_header(c) for c in cols}
    rev_map = {}
    for orig, canon in canonical_cols.items():
        rev_map.setdefault(canon, []).append(orig)

    default_selected = []
    for p in DEFAULT_PILLARS:
        if p in rev_map:
            default_selected.append(rev_map[p][0])

    pillar_cols = st.sidebar.multiselect(
        "Pillar columns",
        options=[c for c in cols if c not in excluded],
        default=default_selected if default_selected else [c for c in cols if c not in excluded],
    )

    review_text_col = st.sidebar.selectbox(
        "Optional: review text column",
        ["(none)"] + cols,
        index=(["(none)"] + cols).index(review_text_guess) if review_text_guess in cols else 0,
    )
    review_text_col = None if review_text_col == "(none)" else review_text_col

    review_id_col = st.sidebar.selectbox("Optional: review id column", ["(none)"] + cols, index=0)
    review_id_col = None if review_id_col == "(none)" else review_id_col

    long_df = to_long(df_raw, product_col, pillar_cols, review_text_col, review_id_col, stars_col)

bad_rows, label_freq = _validate_labels(long_df)

st.sidebar.markdown("---")
st.sidebar.subheader("Data QA")
with st.sidebar.expander("Label frequency (by pillar)", expanded=False):
    st.dataframe(label_freq, use_container_width=True, height=260)

if len(bad_rows):
    st.sidebar.warning(f"Found {len(long_df[~long_df['label'].isin(VALID_LABELS)])} rows with non-standard labels.")
    with st.sidebar.expander("Sample of non-standard labels", expanded=False):
        st.dataframe(bad_rows, use_container_width=True)
    st.sidebar.info("Fix upstream or map variants in LABEL_MAP in this app.")

bench_df = long_df[long_df["label"].isin(VALID_LABELS)].copy()
summary = compute_summary(bench_df)

# Filters
st.sidebar.markdown("---")
st.sidebar.subheader("Benchmark controls")

all_products = sorted(summary.metrics["product"].unique().tolist())
default_products = all_products[:3] if len(all_products) >= 3 else all_products
selected_products = st.sidebar.multiselect("Products to include", all_products, default=default_products)

all_pillars = sorted(summary.metrics["pillar"].unique().tolist())
preferred_order = [p for p in DEFAULT_PILLARS if p in all_pillars]
remaining = [p for p in all_pillars if p not in preferred_order]
pillar_order = preferred_order + remaining
selected_pillars = st.sidebar.multiselect("Pillars to include", pillar_order, default=pillar_order)

m = summary.metrics.copy()
m = m[m["product"].isin(selected_products) & m["pillar"].isin(selected_pillars)].copy()

has_stars = summary.stars_metrics is not None and len(summary.stars_metrics) > 0
if has_stars:
    sm = summary.stars_metrics.copy()
    sm = sm[sm["product"].isin(selected_products) & sm["pillar"].isin(selected_pillars)].copy()
    m = m.merge(sm, on=["product", "pillar"], how="left")

tab_overview, tab_compare, tab_pillar, tab_drill, tab_download = st.tabs(
    ["Overview", "Compare Products", "Pillar Deep Dive", "Drilldown", "Downloads"]
)

# -----------------------------
# Overview
# -----------------------------
with tab_overview:
    st.subheader("📌 Portfolio overview")

    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])

    overall_total = int(m["total_reviews"].sum())
    overall_mentions = int(m["mentions"].sum())
    overall_mention_rate = (overall_mentions / overall_total) if overall_total else 0.0

    overall_net = np.average(m["net_sentiment"], weights=m["mentions"].clip(lower=0)) if overall_mentions else 0.0
    overall_neg_share = np.average(m["neg_share_mentions"], weights=m["mentions"].clip(lower=0)) if overall_mentions else 0.0
    overall_pol = np.average(m["polarization"], weights=m["mentions"].clip(lower=0)) if overall_mentions else 0.0

    c1.metric("Total (reviews×pillars)", f"{overall_total:,}")
    c2.metric("% Mentions (overall)", f"{overall_mention_rate*100:.1f}%")
    c3.metric("Net sentiment (overall)", f"{overall_net:+.2f}")
    c4.metric("Neg share (among mentions)", f"{overall_neg_share*100:.1f}%")
    c5.metric("Polarization", f"{overall_pol*100:.1f}%")

    if has_stars and summary.product_stars is not None:
        ps = summary.product_stars.copy()
        ps = ps[ps["product"].isin(selected_products)]
        st.markdown("#### ⭐ Average star rating by product (overall)")
        st.dataframe(ps.sort_values("avg_stars_overall", ascending=False), use_container_width=True, height=220)

    st.markdown("---")

    metric_options = ["net_sentiment", "mention_rate", "neg_share_mentions", "polarization"]
    if has_stars:
        metric_options += [
            "avg_stars_mentions",
            "avg_stars_positive",
            "avg_stars_negative",
            "avg_stars_neutral",
            "avg_stars_not_mentioned",
        ]

    heat_metric = st.selectbox(
        "Heatmap metric",
        metric_options,
        index=metric_options.index("net_sentiment"),
        help="avg_stars_mentions = average stars for reviews that mention the pillar (label != not mentioned).",
    )

    mat = m.pivot_table(index="product", columns="pillar", values=heat_metric, aggfunc="mean")
    mat = mat.reindex(index=selected_products).reindex(columns=selected_pillars)

    if heat_metric == "net_sentiment":
        colors, zmin, zmax = "RdYlGn", -1, 1
        title = "Net sentiment (positive − negative) among mentions"
    elif heat_metric in ["mention_rate", "polarization"]:
        colors, zmin, zmax = "Blues", 0, 1
        title = heat_metric
    elif heat_metric == "neg_share_mentions":
        colors, zmin, zmax = "Reds", 0, 1
        title = "Neg share among mentions"
    elif heat_metric == "avg_stars_mentions":
        colors, zmin, zmax = "YlGnBu", 1, 5
        title = "Average star rating among mentions"
    elif heat_metric.startswith("avg_stars_"):
        colors, zmin, zmax = "Cividis", 1, 5
        title = f"Average stars by label: {heat_metric.replace('avg_stars_', '').replace('_', ' ')}"
    else:
        colors, zmin, zmax = "Viridis", None, None
        title = heat_metric

    fig = px.imshow(
        mat,
        aspect="auto",
        color_continuous_scale=colors,
        zmin=zmin,
        zmax=zmax,
        labels=dict(x="Pillar", y="Product", color=heat_metric),
        title=title,
    )
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=60, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 🏁 Biggest differentiators (Net sentiment gap)")
    if len(selected_products) >= 2:
        diffs = top_differences(summary.metrics[summary.metrics["pillar"].isin(selected_pillars)], selected_products, top_n=12)
        diffs["winner_net"] = diffs["winner_net"].map(lambda x: f"{x:+.2f}")
        diffs["loser_net"] = diffs["loser_net"].map(lambda x: f"{x:+.2f}")
        diffs["net_gap"] = diffs["net_gap"].map(lambda x: f"{x:.2f}")
        st.dataframe(diffs, use_container_width=True, height=320)
    else:
        st.info("Select at least 2 products to compute differentiators.")

# -----------------------------
# Compare Products
# -----------------------------
with tab_compare:
    st.subheader("⚔️ Compare products (side-by-side)")

    compare_pillar = st.selectbox(
        "Choose a pillar to compare",
        selected_pillars,
        index=selected_pillars.index("Noise level") if "Noise level" in selected_pillars else 0,
    )

    cmp = m[m["pillar"] == compare_pillar].copy()
    cmp = cmp.sort_values(["mention_rate", "net_sentiment"], ascending=[False, False])

    cmp_view = cmp[[
        "product",
        "mentions",
        "total_reviews",
        "mention_rate",
        "net_sentiment",
        "neg_share_mentions",
        "polarization",
        "pos_count",
        "neu_count",
        "neg_count",
        "not_mentioned_count",
    ]].copy()

    if has_stars:
        for col in [
            "avg_stars_mentions",
            "avg_stars_positive",
            "avg_stars_negative",
            "avg_stars_neutral",
            "avg_stars_not_mentioned",
        ]:
            if col in cmp.columns:
                cmp_view[col] = cmp[col]

    for c in ["mention_rate", "neg_share_mentions", "polarization"]:
        cmp_view[c] = (cmp_view[c] * 100).round(1)
    cmp_view["net_sentiment"] = cmp_view["net_sentiment"].round(2)
    if has_stars:
        for c in [
            "avg_stars_mentions",
            "avg_stars_positive",
            "avg_stars_negative",
            "avg_stars_neutral",
            "avg_stars_not_mentioned",
        ]:
            if c in cmp_view.columns:
                cmp_view[c] = pd.to_numeric(cmp_view[c], errors="coerce").round(2)

    st.dataframe(cmp_view, use_container_width=True, height=360)

    st.markdown("#### Visual compare")
    v1, v2 = st.columns(2)

    with v1:
        fig_a = px.scatter(
            cmp,
            x="mention_rate",
            y="net_sentiment",
            size="mentions",
            color="product",
            hover_data={"mentions": True, "total_reviews": True, "neg_share_mentions": True, "polarization": True},
            title=f"{compare_pillar}: Mention rate vs Net sentiment",
        )
        fig_a.update_layout(height=420, xaxis_tickformat=".0%", yaxis_range=[-1, 1])
        st.plotly_chart(fig_a, use_container_width=True)

    with v2:
        if has_stars and "avg_stars_mentions" in cmp.columns:
            fig_b = px.scatter(
                cmp,
                x="mention_rate",
                y="avg_stars_mentions",
                size="mentions",
                color="product",
                hover_data={"mentions": True, "net_sentiment": True, "neg_share_mentions": True},
                title=f"{compare_pillar}: Mention rate vs Avg stars (mentions)",
            )
            fig_b.update_layout(height=420, xaxis_tickformat=".0%", yaxis_range=[1, 5])
            st.plotly_chart(fig_b, use_container_width=True)
        else:
            st.info("Upload/select a stars column to enable Avg Stars comparisons.")

    st.markdown("---")
    st.markdown("### 🧾 Full benchmark table (all pillars × products)")

    metric_pick = st.multiselect(
        "Metrics to include in the comparison table",
        ["mention_rate", "net_sentiment", "neg_share_mentions", "polarization"] + (["avg_stars_mentions"] if has_stars else []),
        default=["mention_rate", "net_sentiment"] + (["avg_stars_mentions"] if has_stars else []),
    )

    wide_blocks = []
    for met in metric_pick:
        piv = m.pivot_table(index="pillar", columns="product", values=met, aggfunc="mean").reindex(index=selected_pillars)
        piv.columns = [f"{met} | {c}" for c in piv.columns]
        wide_blocks.append(piv)
    wide = pd.concat(wide_blocks, axis=1)

    pretty = wide.copy()
    for c in pretty.columns:
        if c.startswith("mention_rate") or c.startswith("neg_share") or c.startswith("polarization"):
            pretty[c] = (pretty[c] * 100).round(1).astype(str) + "%"
        elif c.startswith("net_sentiment"):
            pretty[c] = pretty[c].round(2).map(lambda x: f"{x:+.2f}")
        elif c.startswith("avg_stars"):
            pretty[c] = pd.to_numeric(pretty[c], errors="coerce").round(2)

    st.dataframe(pretty, use_container_width=True, height=520)

# -----------------------------
# Pillar Deep Dive
# -----------------------------
with tab_pillar:
    st.subheader("🔎 Pillar distribution (labels + % mentions + stars)")

    pillar = st.selectbox(
        "Choose a pillar",
        selected_pillars,
        index=selected_pillars.index("Noise level") if "Noise level" in selected_pillars else 0,
        key="deep_pillar",
    )

    pct_total = summary.percents_total.copy()
    pct_total = pct_total[pct_total["product"].isin(selected_products) & (pct_total["pillar"] == pillar)].copy()

    # Ensure label columns exist for safe selection
    for lbl in VALID_LABELS:
        if lbl not in pct_total.columns:
            pct_total[lbl] = 0.0

    base = m[m["pillar"] == pillar][[
        "product", "mention_rate", "net_sentiment", "neg_share_mentions", "polarization", "mentions", "total_reviews"
    ]].copy()

    out = pct_total.merge(base, on="product", how="left")

    if has_stars:
        out = out.merge(summary.stars_metrics[summary.stars_metrics["pillar"] == pillar], on=["product", "pillar"], how="left")

    display = out[["product"] + VALID_LABELS + ["mention_rate", "net_sentiment", "neg_share_mentions", "polarization"]].copy()

    if has_stars:
        if "avg_stars_mentions" in out.columns:
            display["avg_stars_mentions"] = out["avg_stars_mentions"]

        star_label_cols = [
            ("avg_stars_positive", "positive"),
            ("avg_stars_negative", "negative"),
            ("avg_stars_neutral", "neutral"),
            ("avg_stars_not_mentioned", "not mentioned"),
        ]
        for col, pretty_lbl in star_label_cols:
            if col in out.columns:
                display[f"avg_stars | {pretty_lbl}"] = out[col]

    # format %
    for lbl in VALID_LABELS:
        display[lbl] = (display[lbl] * 100).round(1).astype(str) + "%"
    display["mention_rate"] = (out["mention_rate"] * 100).round(1).astype(str) + "%"
    display["neg_share_mentions"] = (out["neg_share_mentions"] * 100).round(1).astype(str) + "%"
    display["polarization"] = (out["polarization"] * 100).round(1).astype(str) + "%"
    display["net_sentiment"] = out["net_sentiment"].round(2).map(lambda x: f"{x:+.2f}")

    if has_stars:
        if "avg_stars_mentions" in display.columns:
            display["avg_stars_mentions"] = pd.to_numeric(display["avg_stars_mentions"], errors="coerce").round(2)
        for c in [c for c in display.columns if c.startswith("avg_stars |")]:
            display[c] = pd.to_numeric(display[c], errors="coerce").round(2)

    st.dataframe(display.sort_values("product"), use_container_width=True, height=320)

    plot_df = out.melt(id_vars=["product"], value_vars=VALID_LABELS, var_name="label", value_name="pct_total")
    plot_df["label"] = pd.Categorical(plot_df["label"], categories=LABEL_ORDER, ordered=True)
    plot_df["pct_total"] = plot_df["pct_total"] * 100
    plot_df["product"] = pd.Categorical(plot_df["product"], categories=selected_products, ordered=True)
    plot_df = plot_df.sort_values(["product", "label"])

    fig = px.bar(
        plot_df,
        x="product",
        y="pct_total",
        color="label",
        barmode="stack",
        category_orders={"label": LABEL_ORDER, "product": selected_products},
        color_discrete_map={
            "positive": "#2ecc71",
            "neutral": "#95a5a6",
            "negative": "#e74c3c",
            "not mentioned": "#bdc3c7",
        },
        title=f"{pillar}: label distribution (% of total reviews)",
    )
    fig.update_layout(height=420, yaxis_title="% of total reviews", xaxis_title="Product", margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# Drilldown
# -----------------------------
with tab_drill:
    st.subheader("🧷 Drilldown to underlying rows")

    d1, d2, d3, d4 = st.columns([1, 1, 1, 1.2])
    with d1:
        drill_product = st.selectbox("Product", selected_products, index=0, key="drill_product")
    with d2:
        drill_pillar = st.selectbox("Pillar", selected_pillars, index=0, key="drill_pillar")
    with d3:
        drill_label = st.selectbox("Label", VALID_LABELS, index=0, key="drill_label")
    with d4:
        max_rows = st.slider("Max rows to show", 10, 500, 100, step=10)

    drill = bench_df[
        (bench_df["product"] == drill_product)
        & (bench_df["pillar"] == drill_pillar)
        & (bench_df["label"] == drill_label)
    ].copy()

    if "stars" in drill.columns and drill["stars"].notna().any():
        if drill_label == "negative":
            drill = drill.sort_values("stars", ascending=True)
        elif drill_label == "positive":
            drill = drill.sort_values("stars", ascending=False)

    drill = drill.head(max_rows)

    cols_to_show = ["product", "pillar", "label"]
    if "stars" in drill.columns:
        cols_to_show.append("stars")
    if "review_id" in drill.columns:
        cols_to_show.insert(0, "review_id")
    if "review_text" in drill.columns:
        cols_to_show.append("review_text")

    if len(drill) == 0:
        st.info("No rows match this drilldown selection.")
    else:
        st.dataframe(drill[cols_to_show], use_container_width=True, height=360)

# -----------------------------
# Downloads
# -----------------------------
with tab_download:
    st.subheader("⬇️ Downloads")

    def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False).encode("utf-8")

    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button(
            "Download metrics (CSV)",
            data=df_to_csv_bytes(summary.metrics),
            file_name="benchmark_metrics.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with d2:
        dist = summary.percents_total.merge(summary.counts, on=["product", "pillar"], suffixes=("_pct_total", "_count"))
        if has_stars:
            dist = dist.merge(summary.stars_metrics, on=["product", "pillar"], how="left")
        st.download_button(
            "Download distributions + stars (CSV)",
            data=df_to_csv_bytes(dist),
            file_name="benchmark_distributions_with_stars.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with d3:
        st.download_button(
            "Download cleaned long format (CSV)",
            data=df_to_csv_bytes(bench_df),
            file_name="bench_long_clean.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.caption(
        "Excel column letters (e.g., AX) don’t carry into CSV/XLSX parsing. "
        "Just select the correct header in the sidebar."
    )





