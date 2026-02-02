# axMentions.py
# Product Name [AX] Storyboard Dashboard
# FIXED: avg stars overall groupby (no .values on scalar)
# ADDED: Health Check tab + better QA + "pillar share of product mentions" view
#
# Run:
#   pip install streamlit pandas plotly openpyxl numpy
#   streamlit run axMentions.py

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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

THEMES: Dict[str, List[str]] = {
    "Performance": ["Powerfulness", "Dry time"],
    "Hair & Scalp": ["Frizz reduction", "Hair health", "Scalp health", "Hair regrowth"],
    "Usability": ["Ease of use", "Ergonomics", "Noise level"],
    "Ownership": ["Reliability", "Filter cleaning", "Price"],
}

PRODUCT_DEFAULT = "Product Name [AX]"

LABEL_MAP = {
    "pos": "positive", "+": "positive", "positive": "positive", "good": "positive", "great": "positive",
    "neg": "negative", "-": "negative", "negative": "negative", "bad": "negative", "poor": "negative",
    "neutral": "neutral", "mixed": "neutral",
    "not mentioned": "not mentioned", "not_mentioned": "not mentioned", "notmentioned": "not mentioned",
    "na": "not mentioned", "n/a": "not mentioned", "none": "not mentioned", "": "not mentioned",
}

STAR_COL_CANDIDATES = [
    "stars", "star", "rating", "star rating", "star_rating",
    "overall rating", "overall_rating", "score",
]

REVIEW_TEXT_CANDIDATES = [
    "review", "review_text", "text", "customer review", "customer_review",
    "body", "comment", "comments",
]


# -----------------------------
# Helpers
# -----------------------------
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
    return s  # may be invalid; health check will flag


def guess_stars_col(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in STAR_COL_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            s = df[c].dropna()
            if len(s) >= 10 and s.between(1, 5).mean() > 0.85:
                return c
    return None


def guess_review_text_col(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in REVIEW_TEXT_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    for c in df.columns:
        if df[c].dtype == object:
            sample = df[c].dropna().astype(str).head(30)
            if len(sample) and sample.map(len).mean() > 120:
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


def shorten_product_name(name: str) -> str:
    s = str(name or "").strip()
    s = re.sub(r"\b(Dyson|Shark)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^\W+|\W+$", "", s).strip()
    return s if s else str(name)


def make_unique_display_names(original_names: List[str], shorten: bool = True) -> Dict[str, str]:
    seen: Dict[str, int] = {}
    mapping: Dict[str, str] = {}
    for orig in original_names:
        base = shorten_product_name(orig) if shorten else str(orig)
        base = re.sub(r"\s+", " ", base).strip()
        if base not in seen:
            seen[base] = 1
            mapping[orig] = base
        else:
            seen[base] += 1
            mapping[orig] = f"{base} ({seen[base]})"
    return mapping


def safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.where(b > 0, a / b, 0.0)


def fmt_pct(x: float) -> str:
    return "" if pd.isna(x) else f"{x*100:.1f}%"


def fmt_pm(x: float) -> str:
    return "" if pd.isna(x) else f"{x:+.2f}"


# -----------------------------
# Data containers
# -----------------------------
@dataclass
class Computed:
    wide_df: pd.DataFrame
    long_df_valid: pd.DataFrame
    long_df_all: pd.DataFrame
    summary: pd.DataFrame
    product_rollup: pd.DataFrame
    name_map: pd.DataFrame
    invalid_label_examples: pd.DataFrame
    invalid_label_count: int
    coerced_unknown_count: int


# -----------------------------
# Core compute
# -----------------------------
@st.cache_data(show_spinner=False)
def compute_all(
    df_wide: pd.DataFrame,
    product_col: str,
    pillar_cols: List[str],
    stars_col: Optional[str],
    review_text_col: Optional[str],
    shorten_names: bool,
    coerce_unknown_to_not_mentioned: bool,
) -> Computed:
    df = df_wide.copy()
    df["__review_id"] = np.arange(len(df))

    # Parse stars to numeric ONCE (fix + robustness)
    if stars_col:
        df["__stars_num"] = pd.to_numeric(df[stars_col], errors="coerce")
    else:
        df["__stars_num"] = np.nan

    # Prepare product name mapping
    df["product_original"] = df[product_col].astype(str)
    uniq_originals = sorted(df["product_original"].dropna().unique().tolist())
    mapping = make_unique_display_names(uniq_originals, shorten=shorten_names)
    df["product"] = df["product_original"].map(mapping).fillna(df["product_original"])

    map_df = (
        pd.DataFrame({"product_original": list(mapping.keys()), "product_display": list(mapping.values())})
        .sort_values("product_display")
        .reset_index(drop=True)
    )

    # Review text (optional)
    if review_text_col and review_text_col in df.columns:
        df["review_text"] = df[review_text_col].astype(str)
    else:
        df["review_text"] = None

    # Melt to long (all labels)
    id_vars = ["__review_id", "product", "product_original", "__stars_num", "review_text"]
    long_all = df.melt(
        id_vars=id_vars,
        value_vars=pillar_cols,
        var_name="pillar",
        value_name="label_raw",
    ).copy()

    long_all["pillar"] = long_all["pillar"].astype(str).map(clean_col)
    long_all["label"] = long_all["label_raw"].map(normalize_label)
    long_all["stars"] = long_all["__stars_num"]

    # Invalid labels (before coercion)
    invalid_mask = ~long_all["label"].isin(VALID_LABELS)
    invalid_examples = (
        long_all.loc[invalid_mask, ["product", "pillar", "label_raw", "label"]]
        .head(50)
        .reset_index(drop=True)
    )
    invalid_count = int(invalid_mask.sum())

    coerced_unknown_count = 0
    if coerce_unknown_to_not_mentioned and invalid_count > 0:
        coerced_unknown_count = invalid_count
        long_all.loc[invalid_mask, "label"] = "not mentioned"

    # Keep only valid labels for computation
    long_valid = long_all[long_all["label"].isin(VALID_LABELS)].copy()

    # Counts per product×pillar×label
    ct = (
        long_valid.groupby(["product", "pillar", "label"])
        .size()
        .rename("count")
        .reset_index()
    )
    piv = ct.pivot_table(index=["product", "pillar"], columns="label", values="count", fill_value=0).reset_index()
    for lbl in VALID_LABELS:
        if lbl not in piv.columns:
            piv[lbl] = 0

    piv = piv.rename(columns={
        "positive": "positive_count",
        "neutral": "neutral_count",
        "negative": "negative_count",
        "not mentioned": "not_mentioned_count",
    })

    piv["total_reviews"] = piv[["positive_count", "neutral_count", "negative_count", "not_mentioned_count"]].sum(axis=1)
    piv["mentions"] = piv[["positive_count", "neutral_count", "negative_count"]].sum(axis=1)

    piv["mention_rate"] = safe_div(piv["mentions"].to_numpy(), piv["total_reviews"].to_numpy())
    piv["net_sentiment"] = safe_div((piv["positive_count"] - piv["negative_count"]).to_numpy(), piv["mentions"].to_numpy())
    piv["neg_share_mentions"] = safe_div(piv["negative_count"].to_numpy(), piv["mentions"].to_numpy())
    piv["pos_share_mentions"] = safe_div(piv["positive_count"].to_numpy(), piv["mentions"].to_numpy())
    piv["neu_share_mentions"] = safe_div(piv["neutral_count"].to_numpy(), piv["mentions"].to_numpy())
    piv["opportunity_score"] = piv["mention_rate"] * piv["neg_share_mentions"]

    # Avg stars among mentions (label != not mentioned)
    mention_rows = long_valid[(long_valid["label"] != "not mentioned") & long_valid["stars"].notna()].copy()
    avg_mentions = (
        mention_rows.groupby(["product", "pillar"])["stars"]
        .mean()
        .rename("avg_stars_mentions")
        .reset_index()
    )
    n_mentions_stars = (
        mention_rows.groupby(["product", "pillar"])["stars"]
        .size()
        .rename("n_stars_mentions")
        .reset_index()
    )
    stars_block = avg_mentions.merge(n_mentions_stars, on=["product", "pillar"], how="left")
    piv = piv.merge(stars_block, on=["product", "pillar"], how="left")

    # Product rollup (WIDE rows = reviews)
    # ✅ FIXED: groupby mean without `.values` on a scalar
    roll = df.groupby("product", dropna=False).agg(
        n_reviews=("__review_id", "count"),
        n_reviews_with_stars=("__stars_num", lambda s: int(pd.Series(s).notna().sum())),
        avg_stars_overall=("__stars_num", "mean"),
    ).reset_index()

    # Portfolio-style rollup across pillars (weighted)
    tmp = piv.groupby("product").agg(
        mentions=("mentions", "sum"),
        total=("total_reviews", "sum"),
        opportunity=("opportunity_score", "mean"),
    ).reset_index()
    tmp["mention_rate_avg"] = safe_div(tmp["mentions"].to_numpy(), tmp["total"].to_numpy())

    w = piv.copy()
    w["w_mentions"] = w["mentions"].clip(lower=0)
    ws = w.groupby("product").apply(
        lambda g: pd.Series({
            "net_sentiment_w": np.average(g["net_sentiment"], weights=g["w_mentions"]) if g["w_mentions"].sum() > 0 else 0.0,
            "neg_share_w": np.average(g["neg_share_mentions"], weights=g["w_mentions"]) if g["w_mentions"].sum() > 0 else 0.0,
        })
    ).reset_index()

    product_rollup = roll.merge(
        tmp[["product", "mention_rate_avg", "opportunity"]],
        on="product", how="left"
    ).merge(ws, on="product", how="left")

    # Pillar ordering
    order_map = {p: i for i, p in enumerate(DEFAULT_PILLARS)}
    piv["pillar_order"] = piv["pillar"].map(lambda p: order_map.get(p, 10_000))
    piv = piv.sort_values(["product", "pillar_order", "pillar"]).drop(columns=["pillar_order"])

    return Computed(
        wide_df=df,
        long_df_valid=long_valid,
        long_df_all=long_all,
        summary=piv,
        product_rollup=product_rollup,
        name_map=map_df,
        invalid_label_examples=invalid_examples,
        invalid_label_count=invalid_count,
        coerced_unknown_count=coerced_unknown_count,
    )


def compute_theme_rollup(summary: pd.DataFrame, theme_map: Dict[str, List[str]], selected_pillars: List[str]) -> pd.DataFrame:
    rows = []
    for theme, pillars in theme_map.items():
        pillars_in = [p for p in pillars if p in selected_pillars]
        if not pillars_in:
            continue
        sub = summary[summary["pillar"].isin(pillars_in)].copy()
        if sub.empty:
            continue
        agg = sub.groupby("product").agg(
            positive=("positive_count", "sum"),
            neutral=("neutral_count", "sum"),
            negative=("negative_count", "sum"),
            not_mentioned=("not_mentioned_count", "sum"),
            mentions=("mentions", "sum"),
            total=("total_reviews", "sum"),
        ).reset_index()
        agg["theme"] = theme
        agg["mention_rate"] = safe_div(agg["mentions"].to_numpy(), agg["total"].to_numpy())
        agg["net_sentiment"] = safe_div((agg["positive"] - agg["negative"]).to_numpy(), agg["mentions"].to_numpy())
        agg["neg_share_mentions"] = safe_div(agg["negative"].to_numpy(), agg["mentions"].to_numpy())
        agg["opportunity_score"] = agg["mention_rate"] * agg["neg_share_mentions"]
        rows.append(agg)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="AX Pillar Storyboard", layout="wide")

st.title("📌 Product Pillar Storyboard (Mentions × Sentiment × Stars)")
st.caption(
    "Focused on **Product Name [AX]**: label counts (pos/neu/neg/not mentioned) per pillar, "
    "how often each pillar is mentioned, and avg star rating when it’s mentioned."
)

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])
if not uploaded:
    st.info("Upload a file to begin.")
    st.stop()

df_raw = load_file(uploaded)
df_raw.columns = [clean_col(c) for c in df_raw.columns]

# Sidebar mapping
st.sidebar.subheader("Column mapping")

product_default_idx = df_raw.columns.tolist().index(PRODUCT_DEFAULT) if PRODUCT_DEFAULT in df_raw.columns else 0
product_col = st.sidebar.selectbox("Product column", df_raw.columns.tolist(), index=product_default_idx)

stars_guess = guess_stars_col(df_raw)
stars_col = st.sidebar.selectbox(
    "Stars column (optional)",
    ["(none)"] + df_raw.columns.tolist(),
    index=(["(none)"] + df_raw.columns.tolist()).index(stars_guess) if stars_guess in df_raw.columns else 0,
)
stars_col = None if stars_col == "(none)" else stars_col

review_text_guess = guess_review_text_col(df_raw)
review_text_col = st.sidebar.selectbox(
    "Review text column (optional, for drilldown)",
    ["(none)"] + df_raw.columns.tolist(),
    index=(["(none)"] + df_raw.columns.tolist()).index(review_text_guess) if review_text_guess in df_raw.columns else 0,
)
review_text_col = None if review_text_col == "(none)" else review_text_col

shorten_names = st.sidebar.checkbox("Shorten product names (remove 'Dyson' / 'Shark')", value=True)
coerce_unknown = st.sidebar.checkbox("Coerce unknown labels → 'not mentioned' (prevents dropped rows)", value=True)

excluded = {product_col}
if stars_col:
    excluded.add(stars_col)
if review_text_col:
    excluded.add(review_text_col)

default_pillars_present = [p for p in DEFAULT_PILLARS if p in df_raw.columns]
pillar_cols = st.sidebar.multiselect(
    "Pillar columns",
    [c for c in df_raw.columns if c not in excluded],
    default=default_pillars_present if default_pillars_present else [c for c in df_raw.columns if c not in excluded],
)

if not pillar_cols:
    st.error("Select at least one pillar column.")
    st.stop()

computed = compute_all(
    df_wide=df_raw,
    product_col=product_col,
    pillar_cols=pillar_cols,
    stars_col=stars_col,
    review_text_col=review_text_col,
    shorten_names=shorten_names,
    coerce_unknown_to_not_mentioned=coerce_unknown,
)

summary = computed.summary.copy()
long_valid = computed.long_df_valid.copy()
wide_df = computed.wide_df.copy()
product_roll = computed.product_rollup.copy()

# Filters
st.sidebar.markdown("---")
st.sidebar.subheader("Filters")

products = sorted(summary["product"].unique().tolist())
pillars = sorted(summary["pillar"].unique().tolist(), key=lambda p: DEFAULT_PILLARS.index(p) if p in DEFAULT_PILLARS else 10_000)

selected_products = st.sidebar.multiselect("Products", products, default=products)

pillar_order_mode = st.sidebar.selectbox(
    "Order pillars by",
    ["Default (recommended)", "Most mentioned", "Most negative", "Biggest opportunity"],
    index=0,
)
topn = st.sidebar.slider("Optional: limit to top N pillars", 5, max(5, len(pillars)), min(12, len(pillars)))
use_topn = st.sidebar.checkbox("Use top N pillars only", value=False)

view0 = summary[summary["product"].isin(selected_products)].copy()

pillar_stats = view0.groupby("pillar").agg(
    mention_rate=("mention_rate", "mean"),
    neg_share=("neg_share_mentions", "mean"),
    opportunity=("opportunity_score", "mean"),
).reset_index()

if pillar_order_mode == "Default (recommended)":
    ordered_pillars = [p for p in DEFAULT_PILLARS if p in pillars] + [p for p in pillars if p not in DEFAULT_PILLARS]
elif pillar_order_mode == "Most mentioned":
    ordered_pillars = pillar_stats.sort_values("mention_rate", ascending=False)["pillar"].tolist()
elif pillar_order_mode == "Most negative":
    ordered_pillars = pillar_stats.sort_values("neg_share", ascending=False)["pillar"].tolist()
else:
    ordered_pillars = pillar_stats.sort_values("opportunity", ascending=False)["pillar"].tolist()

if use_topn:
    ordered_pillars = ordered_pillars[:topn]

selected_pillars = st.sidebar.multiselect("Pillars", ordered_pillars, default=ordered_pillars)

view = summary[summary["product"].isin(selected_products) & summary["pillar"].isin(selected_pillars)].copy()

# KPIs
k1, k2, k3, k4, k5 = st.columns([1, 1, 1.2, 1.2, 1.2])
k1.metric("Products", f"{len(selected_products)}")
k2.metric("Pillars", f"{len(selected_pillars)}")
k3.metric("Total mentions", f"{int(view['mentions'].sum()):,}")
k4.metric("Avg mention rate", fmt_pct(float(view["mention_rate"].mean() if len(view) else 0.0)))
k5.metric("Avg net sentiment", fmt_pm(float(view["net_sentiment"].mean() if len(view) else 0.0)))

tab_story, tab_pillar, tab_product, tab_health, tab_table = st.tabs(
    ["Story", "Pillar Compare", "Product Deep Dive", "Health Check", "Table & Export"]
)

# -----------------------------
# STORY TAB
# -----------------------------
with tab_story:
    st.subheader("1) Conversation Map (Bubble Matrix)")
    st.caption("Bigger = more discussed. Greener = more positive. Redder = more negative.")

    size_mode = st.radio(
        "Bubble size represents",
        ["Mention rate (% of reviews mentioning pillar)", "Mentions (count)"],
        horizontal=True,
        index=0,
    )

    bubble = view.copy()
    bubble["product"] = pd.Categorical(bubble["product"], categories=selected_products, ordered=True)
    bubble["pillar"] = pd.Categorical(bubble["pillar"], categories=selected_pillars, ordered=True)

    if size_mode.startswith("Mention rate"):
        bubble["size_value"] = (bubble["mention_rate"] * 100).clip(0, 100)
        size_max = 40
    else:
        bubble["size_value"] = bubble["mentions"].clip(lower=0)
        size_max = 48

    fig_bubble = px.scatter(
        bubble,
        x="pillar",
        y="product",
        size="size_value",
        color="net_sentiment",
        color_continuous_scale="RdYlGn",
        range_color=[-1, 1],
        size_max=size_max,
        hover_data={
            "mentions": True,
            "total_reviews": True,
            "mention_rate": ":.1%",
            "net_sentiment": ":+.2f",
            "neg_share_mentions": ":.1%",
            "avg_stars_mentions": True,
            "positive_count": True,
            "neutral_count": True,
            "negative_count": True,
            "not_mentioned_count": True,
        },
        title="Conversation map by pillar (size = volume, color = sentiment)",
    )
    fig_bubble.update_traces(marker=dict(line=dict(width=0.5, color="rgba(0,0,0,0.25)")))
    fig_bubble.update_layout(
        height=min(740, 220 + 38 * len(selected_products)),
        margin=dict(l=10, r=10, t=60, b=10),
        xaxis_title="Pillar",
        yaxis_title="Product",
    )
    st.plotly_chart(fig_bubble, use_container_width=True)

    st.markdown("---")
    st.subheader("2) Faceted Pillar Bars (your screenshot style) — now with % of product mentions option")

    bar_mode = st.radio(
        "Bar values show",
        [
            "Counts of labels (includes not mentioned)",
            "% of product total mentions (pos/neu/neg only)",
        ],
        horizontal=True,
        index=0,
    )

    # Build long-form for plotting
    if bar_mode.startswith("Counts"):
        plot_long = view.melt(
            id_vars=["product", "pillar", "mention_rate", "avg_stars_mentions", "net_sentiment", "neg_share_mentions", "mentions"],
            value_vars=["positive_count", "neutral_count", "negative_count", "not_mentioned_count"],
            var_name="label",
            value_name="value",
        )
        label_pretty = {
            "positive_count": "positive",
            "neutral_count": "neutral",
            "negative_count": "negative",
            "not_mentioned_count": "not mentioned",
        }
        plot_long["label"] = plot_long["label"].map(label_pretty)
        x_title = "Count of reviews"
        x_tickformat = None
    else:
        # % of product total mentions: value = label_count / product_total_mentions
        totals = view.groupby("product")["mentions"].sum().rename("product_total_mentions").reset_index()
        tmp = view.merge(totals, on="product", how="left")
        plot_long = tmp.melt(
            id_vars=["product", "pillar", "mention_rate", "avg_stars_mentions", "net_sentiment", "neg_share_mentions", "mentions", "product_total_mentions"],
            value_vars=["positive_count", "neutral_count", "negative_count"],
            var_name="label",
            value_name="count",
        )
        label_pretty = {
            "positive_count": "positive",
            "neutral_count": "neutral",
            "negative_count": "negative",
        }
        plot_long["label"] = plot_long["label"].map(label_pretty)
        plot_long["value"] = safe_div(plot_long["count"].to_numpy(), plot_long["product_total_mentions"].to_numpy())
        x_title = "% of product total mentions"
        x_tickformat = ".0%"

    plot_long["label"] = pd.Categorical(plot_long["label"], categories=LABEL_ORDER, ordered=True)
    plot_long["pillar"] = pd.Categorical(plot_long["pillar"], categories=selected_pillars, ordered=True)
    plot_long["product"] = pd.Categorical(plot_long["product"], categories=selected_products, ordered=True)

    fig_stack = px.bar(
        plot_long.sort_values(["product", "pillar", "label"]),
        x="value",
        y="pillar",
        color="label",
        facet_row="product",
        orientation="h",
        barmode="stack",
        category_orders={"label": LABEL_ORDER, "pillar": selected_pillars, "product": selected_products},
        color_discrete_map={
            "positive": "#2ecc71",
            "neutral": "#95a5a6",
            "negative": "#e74c3c",
            "not mentioned": "#bdc3c7",
        },
        hover_data={
            "mention_rate": ":.1%",
            "mentions": True,
            "neg_share_mentions": ":.1%",
            "net_sentiment": ":+.2f",
            "avg_stars_mentions": True,
        },
        title="Counts or Share-of-Mentions by pillar (faceted by product)",
    )
    fig_stack.update_layout(
        height=min(1700, 250 + 260 * len(selected_products)),
        margin=dict(l=10, r=10, t=60, b=10),
        legend_title_text="Label",
        xaxis_title=x_title,
    )
    fig_stack.update_xaxes(tickformat=x_tickformat)
    fig_stack.for_each_annotation(lambda a: a.update(text=a.text.replace("product=", "Product: ")))
    st.plotly_chart(fig_stack, use_container_width=True)

    st.markdown("---")
    st.subheader("3) Hotspots: High-mention × High-negative (Where to act)")

    min_mentions = st.slider("Minimum mentions (count) for hotspots", 0, int(view["mentions"].max() if len(view) else 0), 10)
    hotspots = view[view["mentions"] >= min_mentions].copy().sort_values("opportunity_score", ascending=False).head(15)

    if hotspots.empty:
        st.info("No hotspots meet the threshold. Lower the minimum mentions filter.")
    else:
        hotspots_display = hotspots.copy()
        hotspots_display["mention_rate"] = hotspots_display["mention_rate"].map(fmt_pct)
        hotspots_display["neg_share_mentions"] = hotspots_display["neg_share_mentions"].map(fmt_pct)
        hotspots_display["net_sentiment"] = hotspots_display["net_sentiment"].map(fmt_pm)
        hotspots_display["opportunity_score"] = (hotspots_display["opportunity_score"] * 100).round(2)

        st.dataframe(
            hotspots_display[[
                "product", "pillar", "mentions", "mention_rate",
                "negative_count", "neg_share_mentions", "net_sentiment",
                "avg_stars_mentions", "opportunity_score"
            ]].rename(columns={"opportunity_score": "opportunity_score (0-100)"}),
            use_container_width=True,
            height=320,
        )

    st.markdown("---")
    st.subheader("4) Theme Rollup (Exec-friendly)")
    theme_df = compute_theme_rollup(view, THEMES, selected_pillars)
    if theme_df.empty:
        st.info("Theme rollup not available (selected pillars don’t match theme definitions).")
    else:
        theme_df["theme"] = pd.Categorical(theme_df["theme"], categories=list(THEMES.keys()), ordered=True)
        theme_df["product"] = pd.Categorical(theme_df["product"], categories=selected_products, ordered=True)

        fig_theme = px.bar(
            theme_df.sort_values(["theme", "product"]),
            x="theme",
            y="net_sentiment",
            color="product",
            barmode="group",
            hover_data={"mention_rate": ":.1%", "neg_share_mentions": ":.1%", "mentions": True},
            title="Theme Net Sentiment (weighted across included pillars)",
        )
        fig_theme.update_layout(height=420, yaxis_range=[-1, 1], margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_theme, use_container_width=True)


# -----------------------------
# PILLAR COMPARE TAB
# -----------------------------
with tab_pillar:
    st.subheader("Compare products for one pillar")
    pillar = st.selectbox("Pick a pillar", selected_pillars, index=0)

    p = view[view["pillar"] == pillar].copy().sort_values("mention_rate", ascending=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        fig_mr = px.bar(p, x="product", y="mention_rate", title="Mention rate", text=p["mention_rate"].map(fmt_pct))
        fig_mr.update_layout(height=350, yaxis_tickformat=".0%")
        st.plotly_chart(fig_mr, use_container_width=True)

    with c2:
        fig_ns = px.bar(p, x="product", y="net_sentiment", title="Net sentiment (pos − neg)", text=p["net_sentiment"].map(fmt_pm))
        fig_ns.update_layout(height=350, yaxis_range=[-1, 1])
        st.plotly_chart(fig_ns, use_container_width=True)

    with c3:
        if stars_col:
            fig_st = px.bar(
                p, x="product", y="avg_stars_mentions",
                title="Avg stars (mentions only)",
                text=pd.to_numeric(p["avg_stars_mentions"], errors="coerce").round(2),
            )
            fig_st.update_layout(height=350, yaxis_range=[1, 5])
            st.plotly_chart(fig_st, use_container_width=True)
        else:
            st.info("Add a stars column to enable Avg Stars visuals.")

    st.markdown("#### Sentiment mix (counts) for this pillar")
    mix = p.melt(
        id_vars=["product"],
        value_vars=["positive_count", "neutral_count", "negative_count", "not_mentioned_count"],
        var_name="label",
        value_name="count",
    )
    mix["label"] = mix["label"].map({
        "positive_count": "positive",
        "neutral_count": "neutral",
        "negative_count": "negative",
        "not_mentioned_count": "not mentioned",
    })
    mix["label"] = pd.Categorical(mix["label"], categories=LABEL_ORDER, ordered=True)

    fig_mix = px.bar(
        mix.sort_values(["product", "label"]),
        x="product",
        y="count",
        color="label",
        barmode="stack",
        color_discrete_map={
            "positive": "#2ecc71",
            "neutral": "#95a5a6",
            "negative": "#e74c3c",
            "not mentioned": "#bdc3c7",
        },
        title="Label counts (stacked)",
    )
    fig_mix.update_layout(height=420, margin=dict(l=10, r=10, t=60, b=10))
    st.plotly_chart(fig_mix, use_container_width=True)


# -----------------------------
# PRODUCT DEEP DIVE TAB
# -----------------------------
with tab_product:
    st.subheader("Deep dive one product across pillars")
    product = st.selectbox("Pick a product", selected_products, index=0)

    pp = view[view["product"] == product].copy()
    if pp.empty:
        st.info("No data for this product with the current filters.")
    else:
        sort_mode = st.radio(
            "Sort pillars by",
            ["Biggest opportunity", "Most mentioned", "Most negative", "Most positive"],
            horizontal=True,
            index=0,
        )
        if sort_mode == "Biggest opportunity":
            pp = pp.sort_values("opportunity_score", ascending=False)
        elif sort_mode == "Most mentioned":
            pp = pp.sort_values("mention_rate", ascending=False)
        elif sort_mode == "Most negative":
            pp = pp.sort_values("neg_share_mentions", ascending=False)
        else:
            pp = pp.sort_values("net_sentiment", ascending=False)

        fig_fp = px.scatter(
            pp,
            x="mention_rate",
            y="pillar",
            size="mentions",
            color="net_sentiment",
            color_continuous_scale="RdYlGn",
            range_color=[-1, 1],
            hover_data={
                "mentions": True,
                "neg_share_mentions": ":.1%",
                "avg_stars_mentions": True,
                "positive_count": True,
                "neutral_count": True,
                "negative_count": True,
            },
            title="Product fingerprint: x=mention rate, color=sentiment, size=mentions",
        )
        fig_fp.update_layout(height=min(720, 250 + 28 * len(pp)), xaxis_tickformat=".0%")
        st.plotly_chart(fig_fp, use_container_width=True)

        # Drilldown to reviews (optional)
        if review_text_col:
            st.markdown("---")
            st.subheader("Example reviews (drilldown)")
            d1, d2, d3 = st.columns([1, 1, 1.2])
            with d1:
                drill_pillar = st.selectbox("Pillar", selected_pillars, index=0, key="drill_pillar")
            with d2:
                drill_label = st.selectbox("Label", VALID_LABELS, index=0, key="drill_label")
            with d3:
                n_show = st.slider("Rows", 5, 50, 10, step=5)

            drill = long_valid[
                (long_valid["product"] == product) &
                (long_valid["pillar"] == drill_pillar) &
                (long_valid["label"] == drill_label)
            ].copy()

            if stars_col and drill["stars"].notna().any():
                if drill_label == "negative":
                    drill = drill.sort_values("stars", ascending=True)
                elif drill_label == "positive":
                    drill = drill.sort_values("stars", ascending=False)

            drill = drill.head(n_show)
            show_cols = ["product", "pillar", "label"]
            if stars_col:
                show_cols.append("stars")
            show_cols.append("review_text")
            st.dataframe(drill[show_cols], use_container_width=True, height=360)


# -----------------------------
# HEALTH CHECK TAB (FULL QA)
# -----------------------------
with tab_health:
    st.subheader("🧪 Health Check (QA)")

    # 1) Label validity
    c1, c2, c3 = st.columns(3)
    c1.metric("Invalid labels found", f"{computed.invalid_label_count:,}")
    c2.metric("Coerced to 'not mentioned'", f"{computed.coerced_unknown_count:,}" if coerce_unknown else "0 (off)")
    c3.metric("Rows used (valid labels)", f"{len(long_valid):,}")

    if computed.invalid_label_count > 0:
        st.warning("Some labels were not one of: positive / neutral / negative / not mentioned.")
        st.markdown("**Sample of invalid label rows:**")
        st.dataframe(computed.invalid_label_examples, use_container_width=True, height=240)

    # 2) Completeness check: each product×pillar should have total_reviews == n_reviews(product)
    st.markdown("---")
    st.markdown("### Completeness: Does every product have a label for every pillar on every review?")
    expected = wide_df.groupby("product")["__review_id"].count().rename("expected_reviews").reset_index()
    chk = view.merge(expected, on="product", how="left")
    chk["delta"] = chk["total_reviews"] - chk["expected_reviews"]
    mismatches = chk[chk["delta"] != 0].copy()

    if mismatches.empty:
        st.success("✅ Completeness check passed: total_reviews per product×pillar matches n_reviews.")
    else:
        st.error("❌ Completeness issues found (likely missing cells or invalid labels being dropped/coerced).")
        st.dataframe(
            mismatches[["product", "pillar", "total_reviews", "expected_reviews", "delta"]].sort_values(["product", "pillar"]),
            use_container_width=True,
            height=320,
        )

    # 3) Pillar missingness in raw wide
    st.markdown("---")
    st.markdown("### Missing values in raw pillar columns (wide file)")
    miss = wide_df[pillar_cols].isna().mean().sort_values(ascending=False).reset_index()
    miss.columns = ["pillar", "missing_rate"]
    miss["missing_rate"] = miss["missing_rate"].map(fmt_pct)
    st.dataframe(miss, use_container_width=True, height=320)

    # 4) Stars QA
    st.markdown("---")
    st.markdown("### Stars / Rating QA")
    if stars_col:
        s = wide_df["__stars_num"]
        n_total = len(s)
        n_missing = int(s.isna().sum())
        out_of_range = int(((s < 1) | (s > 5)).sum(skipna=True))

        d1, d2, d3 = st.columns(3)
        d1.metric("Reviews with stars", f"{n_total - n_missing:,} / {n_total:,}")
        d2.metric("Missing stars rate", fmt_pct(n_missing / n_total if n_total else 0.0))
        d3.metric("Out-of-range stars", f"{out_of_range:,}")

        if out_of_range > 0:
            st.warning("Some star ratings are outside 1–5 after numeric parsing.")

        # histogram
        hist_df = pd.DataFrame({"stars": s.dropna()})
        fig_hist = px.histogram(hist_df, x="stars", nbins=10, title="Stars distribution (parsed numeric)")
        fig_hist.update_layout(height=320, margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_hist, use_container_width=True)

        st.markdown("**Avg stars by product (overall):**")
        st.dataframe(
            product_roll.sort_values("avg_stars_overall", ascending=False),
            use_container_width=True,
            height=260,
        )
    else:
        st.info("No stars column selected. Stars QA is skipped.")

    # 5) Name mapping check
    st.markdown("---")
    st.markdown("### Product name shortening map")
    st.dataframe(computed.name_map, use_container_width=True, height=260)


# -----------------------------
# TABLE & EXPORT TAB
# -----------------------------
with tab_table:
    st.subheader("Product × Pillar table (counts + mention rate + stars + opportunity)")

    out = view[[
        "product", "pillar",
        "positive_count", "neutral_count", "negative_count", "not_mentioned_count",
        "mentions", "total_reviews",
        "mention_rate", "neg_share_mentions", "net_sentiment",
        "avg_stars_mentions", "n_stars_mentions",
        "opportunity_score",
    ]].copy()

    disp = out.copy()
    disp["mention_rate"] = disp["mention_rate"].map(fmt_pct)
    disp["neg_share_mentions"] = disp["neg_share_mentions"].map(fmt_pct)
    disp["net_sentiment"] = disp["net_sentiment"].map(fmt_pm)
    disp["opportunity_score"] = (disp["opportunity_score"] * 100).round(2)
    disp["avg_stars_mentions"] = pd.to_numeric(disp["avg_stars_mentions"], errors="coerce").round(2)

    st.dataframe(disp.sort_values(["product", "pillar"]), use_container_width=True, height=520)

    st.markdown("---")
    st.subheader("Downloads")
    st.download_button(
        "Download Product×Pillar summary (CSV)",
        data=out.to_csv(index=False).encode("utf-8"),
        file_name="ax_product_pillar_summary.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Download name mapping (CSV)",
        data=computed.name_map.to_csv(index=False).encode("utf-8"),
        file_name="ax_product_name_mapping.csv",
        mime="text/csv",
        use_container_width=True,
    )
