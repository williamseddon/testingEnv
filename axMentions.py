# ax_product_pillar_view.py
# Streamlit: Product Name [AX] → Pillar label counts + mention rate + avg stars (mentions)
#
# What you get (best-in-class visuals for this ask):
# 1) Heatmap: % Mention Rate by Product × Pillar (hover shows label counts + avg stars)
# 2) Faceted stacked bars: sentiment mix counts per Pillar, split by Product
# 3) Exportable table: counts (pos/neu/neg/not mentioned) + mention_rate + avg_stars_mentions
#
# Run:
#   pip install streamlit pandas plotly openpyxl numpy
#   streamlit run ax_product_pillar_view.py

from __future__ import annotations

import io
import re
from typing import Optional, List, Dict

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# ---------------------------
# Config
# ---------------------------
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

LABEL_MAP = {
    "pos": "positive", "+": "positive", "positive": "positive", "good": "positive", "great": "positive",
    "neg": "negative", "-": "negative", "negative": "negative", "bad": "negative", "poor": "negative",
    "neutral": "neutral", "mixed": "neutral", "unclear": "neutral",
    "not mentioned": "not mentioned", "not_mentioned": "not mentioned", "notmentioned": "not mentioned",
    "na": "not mentioned", "n/a": "not mentioned", "none": "not mentioned", "": "not mentioned",
}

STAR_COL_CANDIDATES = ["stars", "star", "rating", "star rating", "star_rating", "score", "overall rating"]
PRODUCT_DEFAULT = "Product Name [AX]"


# ---------------------------
# Helpers
# ---------------------------
def clean_col(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())


def normalize_label(x) -> str:
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


def parse_stars(x) -> float:
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


def guess_stars_col(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in STAR_COL_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    # numeric heuristic
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            s = df[c].dropna()
            if len(s) >= 10 and s.between(1, 5).mean() > 0.85:
                return c
    return None


def load_file(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        sample = data[:2048].decode("utf-8", errors="ignore")
        sep = "\t" if sample.count("\t") > sample.count(",") else ","
        return pd.read_csv(io.BytesIO(data), sep=sep)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(data))
    raise ValueError("Unsupported file type. Upload CSV/TSV/TXT or Excel.")


def compute_product_pillar_summary(
    df: pd.DataFrame,
    product_col: str,
    pillar_cols: List[str],
    stars_col: Optional[str],
) -> pd.DataFrame:
    # Wide → long
    id_vars = [product_col]
    if stars_col:
        id_vars.append(stars_col)

    long_df = df.melt(
        id_vars=id_vars,
        value_vars=pillar_cols,
        var_name="pillar",
        value_name="label_raw",
    ).copy()

    long_df["product"] = long_df[product_col].astype(str)
    long_df["pillar"] = long_df["pillar"].astype(str).map(clean_col)
    long_df["label"] = long_df["label_raw"].map(normalize_label)

    if stars_col:
        long_df["stars"] = long_df[stars_col].map(parse_stars)
    else:
        long_df["stars"] = np.nan

    # keep only valid labels
    long_df = long_df[long_df["label"].isin(VALID_LABELS)].copy()

    # counts
    ct = (
        long_df.groupby(["product", "pillar", "label"])
        .size()
        .rename("count")
        .reset_index()
    )
    pivot = ct.pivot_table(index=["product", "pillar"], columns="label", values="count", fill_value=0).reset_index()
    for lbl in VALID_LABELS:
        if lbl not in pivot.columns:
            pivot[lbl] = 0

    pivot["total_reviews"] = pivot[VALID_LABELS].sum(axis=1)
    pivot["mentions"] = pivot[["positive", "negative", "neutral"]].sum(axis=1)
    pivot["mention_rate"] = np.where(pivot["total_reviews"] > 0, pivot["mentions"] / pivot["total_reviews"], 0.0)

    # avg stars among mentions (label != not mentioned)
    if stars_col:
        mention_rows = long_df[(long_df["label"] != "not mentioned") & long_df["stars"].notna()].copy()
        avg_mentions = (
            mention_rows.groupby(["product", "pillar"])["stars"]
            .mean()
            .rename("avg_stars_mentions")
            .reset_index()
        )
        out = pivot.merge(avg_mentions, on=["product", "pillar"], how="left")
    else:
        out = pivot.copy()
        out["avg_stars_mentions"] = np.nan

    # Nice ordering of pillars if possible
    order_map = {p: i for i, p in enumerate(DEFAULT_PILLARS)}
    out["pillar_order"] = out["pillar"].map(lambda p: order_map.get(p, 10_000))
    out = out.sort_values(["product", "pillar_order", "pillar"]).drop(columns=["pillar_order"])

    # Standardize count col names (explicit)
    out = out.rename(columns={
        "positive": "positive_count",
        "neutral": "neutral_count",
        "negative": "negative_count",
        "not mentioned": "not_mentioned_count",
    })

    return out


# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="AX Pillar Mentions + Stars", layout="wide")

st.title("📊 Product Name [AX] — Pillar Mentions, Sentiment Mix, and Avg Stars")
st.caption(
    "This view quantifies **how much each pillar is mentioned** per product (mention rate + counts), "
    "and shows **avg star rating among reviews that mention the pillar**."
)

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])
if not uploaded:
    st.info("Upload your processed output to generate the Product × Pillar dashboard.")
    st.stop()

df = load_file(uploaded)
df.columns = [clean_col(c) for c in df.columns]

# Column pickers
st.sidebar.subheader("Column mapping")

product_col_default_idx = df.columns.tolist().index(PRODUCT_DEFAULT) if PRODUCT_DEFAULT in df.columns else 0
product_col = st.sidebar.selectbox("Product column", df.columns.tolist(), index=product_col_default_idx)

stars_guess = guess_stars_col(df)
stars_col = st.sidebar.selectbox(
    "Stars column (optional)",
    ["(none)"] + df.columns.tolist(),
    index=(["(none)"] + df.columns.tolist()).index(stars_guess) if stars_guess in df.columns else 0,
)
stars_col = None if stars_col == "(none)" else stars_col

excluded = {product_col}
if stars_col:
    excluded.add(stars_col)

# pillar columns: default to your known pillars if present
default_pillars_present = [p for p in DEFAULT_PILLARS if p in df.columns]
pillar_cols = st.sidebar.multiselect(
    "Pillar columns",
    [c for c in df.columns if c not in excluded],
    default=default_pillars_present if default_pillars_present else [c for c in df.columns if c not in excluded],
)

if not pillar_cols:
    st.error("Select at least one pillar column.")
    st.stop()

# Compute summary
summary = compute_product_pillar_summary(df, product_col, pillar_cols, stars_col)

products = sorted(summary["product"].unique().tolist())
pillars = summary["pillar"].unique().tolist()

st.sidebar.markdown("---")
st.sidebar.subheader("Filters")
selected_products = st.sidebar.multiselect("Products", products, default=products)
selected_pillars = st.sidebar.multiselect("Pillars", pillars, default=pillars)

view = summary[summary["product"].isin(selected_products) & summary["pillar"].isin(selected_pillars)].copy()

# ---------------------------
# KPI row
# ---------------------------
k1, k2, k3, k4 = st.columns(4)
total_mentions = int(view["mentions"].sum())
total_possible = int(view["total_reviews"].sum())
mention_rate_overall = (total_mentions / total_possible) if total_possible else 0.0

k1.metric("Products", f"{len(selected_products)}")
k2.metric("Pillars", f"{len(selected_pillars)}")
k3.metric("Total mentions (pos+neu+neg)", f"{total_mentions:,}")
k4.metric("Overall mention rate", f"{mention_rate_overall*100:.1f}%")

st.markdown("---")

# ---------------------------
# Visual 1: Mention Rate Heatmap (best to quantify “how much it’s mentioned”)
# ---------------------------
st.subheader("1) Mention Rate Heatmap (Product × Pillar)")
heat = view.pivot_table(index="product", columns="pillar", values="mention_rate", aggfunc="mean").reindex(index=selected_products)
heat = heat.reindex(columns=selected_pillars)

fig_heat = px.imshow(
    heat * 100,
    aspect="auto",
    color_continuous_scale="Blues",
    labels=dict(x="Pillar", y="Product", color="% mentioned"),
    title="% of reviews that mention the pillar (positive/neutral/negative)",
)

fig_heat.update_layout(height=min(600, 130 + 35 * len(selected_products)), margin=dict(l=10, r=10, t=60, b=10))
st.plotly_chart(fig_heat, use_container_width=True)

# ---------------------------
# Visual 2: “Sentiment Mix + Volume” stacked bars, faceted by Product
# ---------------------------
st.subheader("2) Sentiment Mix by Pillar (Counts), faceted by Product")

plot_df = view.copy()
plot_long = plot_df.melt(
    id_vars=["product", "pillar", "mention_rate", "avg_stars_mentions"],
    value_vars=["positive_count", "neutral_count", "negative_count", "not_mentioned_count"],
    var_name="label",
    value_name="count",
)

label_pretty = {
    "positive_count": "positive",
    "neutral_count": "neutral",
    "negative_count": "negative",
    "not_mentioned_count": "not mentioned",
}
plot_long["label"] = plot_long["label"].map(label_pretty)
plot_long["label"] = pd.Categorical(plot_long["label"], categories=LABEL_ORDER, ordered=True)

# Keep pillars ordered
pillar_order = [p for p in DEFAULT_PILLARS if p in selected_pillars] + [p for p in selected_pillars if p not in DEFAULT_PILLARS]
plot_long["pillar"] = pd.Categorical(plot_long["pillar"], categories=pillar_order, ordered=True)

# Facet row by product (best for many pillars; user can filter products)
fig_stack = px.bar(
    plot_long.sort_values(["product", "pillar", "label"]),
    x="count",
    y="pillar",
    color="label",
    facet_row="product",
    orientation="h",
    barmode="stack",
    category_orders={"label": LABEL_ORDER, "pillar": pillar_order, "product": selected_products},
    color_discrete_map={
        "positive": "#2ecc71",
        "neutral": "#95a5a6",
        "negative": "#e74c3c",
        "not mentioned": "#bdc3c7",
    },
    hover_data={
        "count": True,
        "mention_rate": ":.2%",
        "avg_stars_mentions": True,
        "label": True,
        "product": True,
        "pillar": True,
    },
    title="Counts of labels per pillar (hover includes mention rate + avg stars among mentions)",
)

# Make it readable
fig_stack.update_layout(
    height=min(1600, 250 + 260 * len(selected_products)),
    margin=dict(l=10, r=10, t=60, b=10),
    legend_title_text="Label",
)
fig_stack.for_each_annotation(lambda a: a.update(text=a.text.replace("product=", "Product: ")))
st.plotly_chart(fig_stack, use_container_width=True)

# ---------------------------
# Table: exact counts + mention rate + stars
# ---------------------------
st.subheader("3) Product × Pillar Table (Counts + Mention Rate + Avg Stars)")

table = view[[
    "product",
    "pillar",
    "positive_count",
    "neutral_count",
    "negative_count",
    "not_mentioned_count",
    "mentions",
    "total_reviews",
    "mention_rate",
    "avg_stars_mentions",
]].copy()

# Format for display (keep raw for download)
display = table.copy()
display["mention_rate"] = (display["mention_rate"] * 100).round(1).astype(str) + "%"
display["avg_stars_mentions"] = pd.to_numeric(display["avg_stars_mentions"], errors="coerce").round(2)

st.dataframe(display.sort_values(["product", "pillar"]), use_container_width=True, height=520)

# Downloads
st.markdown("---")
st.subheader("⬇️ Download")
st.download_button(
    "Download Product×Pillar summary (CSV)",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name="product_pillar_counts_mentions_stars.csv",
    mime="text/csv",
    use_container_width=True,
)







