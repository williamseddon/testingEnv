# ax_pillar_storyboard_best.py
# Best-in-class Streamlit dashboard for:
#   Product Name [AX] × Pillar → label COUNTS (pos/neu/neg/not mentioned)
#   + Mention rate + Avg stars (among mentions)
#
# Key upgrades for DIGESTIBILITY:
# ✅ Shortens product names by removing "Dyson" and "Shark" (toggle)
# ✅ Bubble Matrix (size = mention rate, color = net sentiment) = fastest “story” view
# ✅ Opportunity Hotspots (high mention × high negative share)
# ✅ Theme rollups (Performance / Hair & Scalp / Usability / Ownership) for easy exec comparison
# ✅ Clean tables + exports + drilldown to review text (optional)
#
# Run:
#   pip install streamlit pandas plotly openpyxl numpy
#   streamlit run ax_pillar_storyboard_best.py

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

# Theme grouping to make comparisons more digestible
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
    # heuristic: long text column
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
    """
    Remove brand tokens 'Dyson' and 'Shark' to make charts more digestible.
    Keeps the rest of the string intact.
    """
    s = str(name or "").strip()
    # remove whole-word occurrences (case-insensitive)
    s = re.sub(r"\b(Dyson|Shark)\b", "", s, flags=re.IGNORECASE)
    # clean extra spaces / punctuation leftovers
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^\W+|\W+$", "", s).strip()
    return s if s else str(name)


def make_unique_display_names(original_names: List[str], shorten: bool = True) -> Dict[str, str]:
    """
    If shortening causes collisions, append a suffix to keep display names unique.
    """
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


@dataclass
class Computed:
    long_df: pd.DataFrame
    summary: pd.DataFrame
    product_rollup: pd.DataFrame
    name_map: pd.DataFrame  # original → display


@st.cache_data(show_spinner=False)
def compute_all(
    df_wide: pd.DataFrame,
    product_col: str,
    pillar_cols: List[str],
    stars_col: Optional[str],
    review_text_col: Optional[str],
    shorten_names: bool,
) -> Computed:
    df = df_wide.copy()
    df["__review_id"] = np.arange(len(df))

    # Name mapping
    originals = df[product_col].astype(str).fillna("").tolist()
    uniq_originals = sorted(pd.Series(originals).unique().tolist())
    mapping = make_unique_display_names(uniq_originals, shorten=shorten_names)

    map_df = pd.DataFrame(
        {"product_original": list(mapping.keys()), "product_display": list(mapping.values())}
    ).sort_values("product_display")

    # Melt to long
    id_vars = ["__review_id", product_col]
    if stars_col:
        id_vars.append(stars_col)
    if review_text_col:
        id_vars.append(review_text_col)

    long_df = df.melt(
        id_vars=id_vars,
        value_vars=pillar_cols,
        var_name="pillar",
        value_name="label_raw",
    ).copy()

    long_df["product_original"] = long_df[product_col].astype(str)
    long_df["product"] = long_df["product_original"].map(mapping).fillna(long_df["product_original"])
    long_df["pillar"] = long_df["pillar"].astype(str).map(clean_col)
    long_df["label"] = long_df["label_raw"].map(normalize_label)

    if stars_col:
        long_df["stars"] = long_df[stars_col].map(parse_stars)
    else:
        long_df["stars"] = np.nan

    if review_text_col:
        long_df["review_text"] = long_df[review_text_col].astype(str)
    else:
        long_df["review_text"] = None

    long_df = long_df[long_df["label"].isin(VALID_LABELS)].copy()

    # Counts per product×pillar×label
    ct = (
        long_df.groupby(["product", "pillar", "label"])
        .size()
        .rename("count")
        .reset_index()
    )
    piv = ct.pivot_table(index=["product", "pillar"], columns="label", values="count", fill_value=0).reset_index()
    for lbl in VALID_LABELS:
        if lbl not in piv.columns:
            piv[lbl] = 0

    # Standardize count columns
    piv = piv.rename(columns={
        "positive": "positive_count",
        "neutral": "neutral_count",
        "negative": "negative_count",
        "not mentioned": "not_mentioned_count",
    })

    piv["total_reviews"] = piv[["positive_count", "neutral_count", "negative_count", "not_mentioned_count"]].sum(axis=1)
    piv["mentions"] = piv[["positive_count", "neutral_count", "negative_count"]].sum(axis=1)

    piv["mention_rate"] = safe_div(piv["mentions"].to_numpy(), piv["total_reviews"].to_numpy())
    piv["net_sentiment"] = safe_div(
        (piv["positive_count"] - piv["negative_count"]).to_numpy(),
        piv["mentions"].to_numpy(),
    )
    piv["neg_share_mentions"] = safe_div(piv["negative_count"].to_numpy(), piv["mentions"].to_numpy())
    piv["pos_share_mentions"] = safe_div(piv["positive_count"].to_numpy(), piv["mentions"].to_numpy())
    piv["neu_share_mentions"] = safe_div(piv["neutral_count"].to_numpy(), piv["mentions"].to_numpy())

    # Simple “pain hotspot” score (high-volume negative conversation)
    piv["opportunity_score"] = piv["mention_rate"] * piv["neg_share_mentions"]

    # Avg stars among mentions (label != not mentioned)
    if stars_col:
        mention_rows = long_df[(long_df["label"] != "not mentioned") & long_df["stars"].notna()].copy()
        avg_mentions = (
            mention_rows.groupby(["product", "pillar"])["stars"]
            .mean()
            .rename("avg_stars_mentions")
            .reset_index()
        )
        star_n = (
            mention_rows.groupby(["product", "pillar"])["stars"]
            .size()
            .rename("n_stars_mentions")
            .reset_index()
        )
        avg_mentions = avg_mentions.merge(star_n, on=["product", "pillar"], how="left")
        piv = piv.merge(avg_mentions, on=["product", "pillar"], how="left")
    else:
        piv["avg_stars_mentions"] = np.nan
        piv["n_stars_mentions"] = 0

    # Product rollup: review counts + overall stars (from wide, not duplicated per pillar)
    roll = df.groupby(product_col).agg(
        n_reviews=("__review_id", "count"),
    ).reset_index()
    roll["product_original"] = roll[product_col].astype(str)
    roll["product"] = roll["product_original"].map(mapping).fillna(roll["product_original"])

    if stars_col:
        roll["avg_stars_overall"] = df.groupby(product_col)[stars_col].apply(lambda s: pd.to_numeric(s, errors="coerce")).mean().values
    else:
        roll["avg_stars_overall"] = np.nan

    # Add “portfolio-style” rollups across pillars (weighted by mentions)
    tmp = piv.groupby("product").agg(
        mentions=("mentions", "sum"),
        total=("total_reviews", "sum"),
        opportunity=("opportunity_score", "mean"),
    ).reset_index()
    tmp["mention_rate_avg"] = safe_div(tmp["mentions"].to_numpy(), tmp["total"].to_numpy())

    # Weighted net sentiment & neg share across pillars
    w = piv.copy()
    w["w_mentions"] = w["mentions"].clip(lower=0)
    ws = w.groupby("product").apply(
        lambda g: pd.Series({
            "net_sentiment_w": np.average(g["net_sentiment"], weights=g["w_mentions"]) if g["w_mentions"].sum() > 0 else 0.0,
            "neg_share_w": np.average(g["neg_share_mentions"], weights=g["w_mentions"]) if g["w_mentions"].sum() > 0 else 0.0,
        })
    ).reset_index()

    product_rollup = roll[["product", "product_original", "n_reviews", "avg_stars_overall"]].merge(
        tmp[["product", "mention_rate_avg", "opportunity"]],
        on="product", how="left"
    ).merge(ws, on="product", how="left")

    # make sure pillar order is sane
    order_map = {p: i for i, p in enumerate(DEFAULT_PILLARS)}
    piv["pillar_order"] = piv["pillar"].map(lambda p: order_map.get(p, 10_000))
    piv = piv.sort_values(["product", "pillar_order", "pillar"]).drop(columns=["pillar_order"])

    return Computed(long_df=long_df, summary=piv, product_rollup=product_rollup, name_map=map_df)


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
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    return out


def fmt_pct(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x*100:.1f}%"


def fmt_pm(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x:+.2f}"


# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="AX Pillar Storyboard", layout="wide")

st.title("📌 Product Pillar Storyboard (Mentions × Sentiment × Stars)")
st.caption(
    "Focused on **Product Name [AX]**: counts of positive/neutral/negative/not mentioned per pillar, "
    "**how often it’s mentioned**, and **avg star rating when it’s mentioned**. "
    "Designed to be extremely easy to scan and compare."
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
)

summary = computed.summary.copy()
long_df = computed.long_df.copy()
product_roll = computed.product_rollup.copy()

# Filters
st.sidebar.markdown("---")
st.sidebar.subheader("Filters")

products = sorted(summary["product"].unique().tolist())
pillars = summary["pillar"].unique().tolist()

# Pillar ordering (digestibility)
pillar_order_mode = st.sidebar.selectbox(
    "Order pillars by",
    ["Default (recommended)", "Most mentioned", "Most negative", "Biggest opportunity"],
    index=0,
)

# Choose products/pillars
selected_products = st.sidebar.multiselect("Products", products, default=products)

# Pillar selection helper: top N
topn = st.sidebar.slider("Optional: limit to top N pillars (by selected ordering)", 5, max(5, len(pillars)), min(12, len(pillars)))
use_topn = st.sidebar.checkbox("Use top N pillars only", value=False)

# Apply filters
view = summary[summary["product"].isin(selected_products)].copy()

# Compute global pillar ordering metric
pillar_stats = view.groupby("pillar").agg(
    mention_rate=("mention_rate", "mean"),
    neg_share=("neg_share_mentions", "mean"),
    opportunity=("opportunity_score", "mean"),
).reset_index()

default_order = [p for p in DEFAULT_PILLARS if p in pillars] + [p for p in pillars if p not in DEFAULT_PILLARS]

if pillar_order_mode == "Default (recommended)":
    ordered_pillars = default_order
elif pillar_order_mode == "Most mentioned":
    ordered_pillars = pillar_stats.sort_values("mention_rate", ascending=False)["pillar"].tolist()
elif pillar_order_mode == "Most negative":
    ordered_pillars = pillar_stats.sort_values("neg_share", ascending=False)["pillar"].tolist()
else:
    ordered_pillars = pillar_stats.sort_values("opportunity", ascending=False)["pillar"].tolist()

if use_topn:
    ordered_pillars = ordered_pillars[:topn]

selected_pillars = st.sidebar.multiselect("Pillars", ordered_pillars, default=ordered_pillars)

view = view[view["pillar"].isin(selected_pillars)].copy()

# Name map expander (for audit)
with st.sidebar.expander("Name mapping (original → display)", expanded=False):
    st.dataframe(computed.name_map, use_container_width=True, height=220)

# KPIs
k1, k2, k3, k4, k5 = st.columns([1, 1, 1.2, 1.2, 1.2])
k1.metric("Products", f"{len(selected_products)}")
k2.metric("Pillars", f"{len(selected_pillars)}")
k3.metric("Total mentions", f"{int(view['mentions'].sum()):,}")
k4.metric("Avg mention rate", fmt_pct(float(view["mention_rate"].mean() if len(view) else 0.0)))
k5.metric("Avg net sentiment", fmt_pm(float(view["net_sentiment"].mean() if len(view) else 0.0)))

tab_story, tab_pillar, tab_product, tab_table = st.tabs(["Story", "Pillar Compare", "Product Deep Dive", "Table & Export"])


# -----------------------------
# STORY TAB (best-in-class scan)
# -----------------------------
with tab_story:
    st.subheader("1) Conversation Map: What’s mentioned + how it feels (Bubble Matrix)")

    size_mode = st.radio(
        "Bubble size represents",
        ["Mention rate", "Mentions (count)"],
        horizontal=True,
        index=0,
    )

    bubble = view.copy()
    bubble["product"] = pd.Categorical(bubble["product"], categories=selected_products, ordered=True)
    bubble["pillar"] = pd.Categorical(bubble["pillar"], categories=selected_pillars, ordered=True)

    # size scaling
    if size_mode == "Mention rate":
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
        title="Bigger bubble = more discussed. Greener = more positive. Redder = more negative.",
    )
    fig_bubble.update_traces(marker=dict(line=dict(width=0.5, color="rgba(0,0,0,0.25)")))
    fig_bubble.update_layout(
        height=min(700, 220 + 38 * len(selected_products)),
        margin=dict(l=10, r=10, t=60, b=10),
        xaxis_title="Pillar",
        yaxis_title="Product",
    )
    st.plotly_chart(fig_bubble, use_container_width=True)

    st.markdown("---")
    st.subheader("2) Hotspots: High-mention × High-negative (Where to act)")

    min_mentions = st.slider("Minimum mentions (count) to show hotspots", 0, int(view["mentions"].max() if len(view) else 0), 10)
    hotspots = view[view["mentions"] >= min_mentions].copy()
    hotspots = hotspots.sort_values("opportunity_score", ascending=False).head(15)

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

        fig_hot = px.bar(
            hotspots,
            x="opportunity_score",
            y="pillar",
            color="product",
            orientation="h",
            hover_data={
                "mentions": True,
                "mention_rate": ":.1%",
                "neg_share_mentions": ":.1%",
                "net_sentiment": ":+.2f",
                "avg_stars_mentions": True,
            },
            title="Top hotspots (opportunity score = mention rate × negative share)",
        )
        fig_hot.update_layout(height=420, xaxis_tickformat=".0%", margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_hot, use_container_width=True)

    st.markdown("---")
    st.subheader("3) Theme Rollup (Exec-friendly comparison)")

    theme_df = compute_theme_rollup(view, THEMES, selected_pillars)

    if theme_df.empty:
        st.info("Theme rollup not available (none of the theme pillars found in your selected pillars).")
    else:
        theme_df["theme"] = pd.Categorical(theme_df["theme"], categories=list(THEMES.keys()), ordered=True)
        theme_df["product"] = pd.Categorical(theme_df["product"], categories=selected_products, ordered=True)

        fig_theme = px.bar(
            theme_df.sort_values(["theme", "product"]),
            x="theme",
            y="net_sentiment",
            color="product",
            barmode="group",
            hover_data={
                "mention_rate": ":.1%",
                "neg_share_mentions": ":.1%",
                "mentions": True,
                "total": True,
            },
            title="Theme Net Sentiment (weighted by mentions across included pillars)",
        )
        fig_theme.update_layout(height=420, yaxis_range=[-1, 1], margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_theme, use_container_width=True)


# -----------------------------
# PILLAR COMPARE TAB
# -----------------------------
with tab_pillar:
    st.subheader("Compare products for one pillar (super digestible)")
    pillar = st.selectbox("Pick a pillar", selected_pillars, index=0)

    p = view[view["pillar"] == pillar].copy().sort_values("mention_rate", ascending=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        fig_mr = px.bar(p, x="product", y="mention_rate", title="Mention rate", text=p["mention_rate"].map(fmt_pct))
        fig_mr.update_layout(height=350, yaxis_tickformat=".0%")
        st.plotly_chart(fig_mr, use_container_width=True)

    with c2:
        fig_ns = px.bar(p, x="product", y="net_sentiment", title="Net sentiment (pos − neg among mentions)", text=p["net_sentiment"].map(fmt_pm))
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

    st.markdown("#### Pillar table (exact numbers)")
    tbl = p[[
        "product", "positive_count", "neutral_count", "negative_count", "not_mentioned_count",
        "mentions", "total_reviews", "mention_rate", "neg_share_mentions", "net_sentiment",
        "avg_stars_mentions",
    ]].copy()
    tbl["mention_rate"] = tbl["mention_rate"].map(fmt_pct)
    tbl["neg_share_mentions"] = tbl["neg_share_mentions"].map(fmt_pct)
    tbl["net_sentiment"] = tbl["net_sentiment"].map(fmt_pm)
    if stars_col:
        tbl["avg_stars_mentions"] = pd.to_numeric(tbl["avg_stars_mentions"], errors="coerce").round(2)
    st.dataframe(tbl, use_container_width=True, height=320)


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
        # Pillar fingerprint: sort by opportunity score
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

        st.markdown("#### Product fingerprint (mention rate + sentiment)")
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
            title="Each pillar: x = mention rate, color = net sentiment, size = mentions",
        )
        fig_fp.update_layout(height=min(700, 250 + 28 * len(pp)), xaxis_tickformat=".0%")
        st.plotly_chart(fig_fp, use_container_width=True)

        # Top pain + top delight
        st.markdown("---")
        left, right = st.columns(2)

        with left:
            st.markdown("##### 🔥 Top pain pillars (highest opportunity)")
            pain = pp.sort_values("opportunity_score", ascending=False).head(6).copy()
            pain["mention_rate"] = pain["mention_rate"].map(fmt_pct)
            pain["neg_share_mentions"] = pain["neg_share_mentions"].map(fmt_pct)
            pain["net_sentiment"] = pain["net_sentiment"].map(fmt_pm)
            if stars_col:
                pain["avg_stars_mentions"] = pd.to_numeric(pain["avg_stars_mentions"], errors="coerce").round(2)
            st.dataframe(
                pain[["pillar", "mentions", "mention_rate", "negative_count", "neg_share_mentions", "net_sentiment", "avg_stars_mentions"]],
                use_container_width=True,
                height=260,
            )

        with right:
            st.markdown("##### ✅ Top delight pillars (high net sentiment + meaningful mentions)")
            delight = pp[pp["mentions"] > 0].copy()
            delight = delight.sort_values(["net_sentiment", "mention_rate"], ascending=[False, False]).head(6)
            delight["mention_rate"] = delight["mention_rate"].map(fmt_pct)
            delight["net_sentiment"] = delight["net_sentiment"].map(fmt_pm)
            if stars_col:
                delight["avg_stars_mentions"] = pd.to_numeric(delight["avg_stars_mentions"], errors="coerce").round(2)
            st.dataframe(
                delight[["pillar", "mentions", "mention_rate", "net_sentiment", "avg_stars_mentions"]],
                use_container_width=True,
                height=260,
            )

        # Optional drilldown: show example review text
        if review_text_col:
            st.markdown("---")
            st.subheader("Example reviews (optional drilldown)")
            d1, d2, d3 = st.columns([1, 1, 1.2])
            with d1:
                drill_pillar = st.selectbox("Pillar", selected_pillars, index=0, key="drill_pillar")
            with d2:
                drill_label = st.selectbox("Label", VALID_LABELS, index=0, key="drill_label")
            with d3:
                n_show = st.slider("Rows", 5, 50, 10, step=5)

            drill = long_df[
                (long_df["product"] == product) &
                (long_df["pillar"] == drill_pillar) &
                (long_df["label"] == drill_label)
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

            if drill.empty:
                st.info("No rows for this selection.")
            else:
                st.dataframe(drill[show_cols], use_container_width=True, height=360)


# -----------------------------
# TABLE & EXPORT TAB
# -----------------------------
with tab_table:
    st.subheader("Product × Pillar table (counts + mention rate + stars)")

    out = view[[
        "product", "pillar",
        "positive_count", "neutral_count", "negative_count", "not_mentioned_count",
        "mentions", "total_reviews",
        "mention_rate", "neg_share_mentions", "net_sentiment",
        "avg_stars_mentions",
        "opportunity_score",
    ]].copy()

    disp = out.copy()
    disp["mention_rate"] = disp["mention_rate"].map(fmt_pct)
    disp["neg_share_mentions"] = disp["neg_share_mentions"].map(fmt_pct)
    disp["net_sentiment"] = disp["net_sentiment"].map(fmt_pm)
    disp["opportunity_score"] = (disp["opportunity_score"] * 100).round(2)
    if stars_col:
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



