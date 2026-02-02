# axMentions.py
# ------------------------------------------------------------
# FAST + BEST‑IN‑CLASS Product × Pillar Storytelling Dashboard
# Focus: Product Name [AX]
#
# FIXES:
# ✅ Robust renaming after reset_index() (pandas versions differ) -> prevents KeyError
# ✅ Seeded filter applied BEFORE compute
# ✅ compute_key includes seeded_mode + included row count -> graphs update
# ✅ Reset button clears computed cache
# ------------------------------------------------------------

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

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

DEFAULT_PRODUCT_COL = "Product Name [AX]"
DEFAULT_SEEDED_COL = "Seeded Flag"

SENTIMENT_COLORS = {
    "positive": "#2ecc71",
    "neutral": "#95a5a6",
    "negative": "#e74c3c",
    "not mentioned": "#d9dde3",
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
    "na": "not mentioned",
    "n/a": "not mentioned",
    "none": "not mentioned",
    "": "not mentioned",
}

STAR_COL_CANDIDATES = [
    "stars", "star", "rating", "star rating", "star_rating",
    "overall rating", "overall_rating", "score",
]


# -----------------------------
# Utilities
# -----------------------------
def clean_col(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def load_file_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        sample = file_bytes[:2048].decode("utf-8", errors="ignore")
        sep = "\t" if sample.count("\t") > sample.count(",") else ","
        return pd.read_csv(io.BytesIO(file_bytes), sep=sep)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(file_bytes))
    raise ValueError("Unsupported file type. Upload CSV/TSV/TXT or Excel.")


def guess_stars_col(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in STAR_COL_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            s = df[c].dropna()
            if len(s) >= 10 and (s.between(1, 5).mean() > 0.85):
                return c
    return None


def parse_stars_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    extracted = s.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


def normalize_label_series(s: pd.Series) -> pd.Series:
    out = s.astype("string").fillna("not mentioned")
    out = out.str.strip().str.lower()
    out = out.str.replace(r"\s+", " ", regex=True)
    out = out.str.replace("notmentioned", "not mentioned", regex=False)
    out = out.str.replace("not_mentioned", "not mentioned", regex=False)
    out = out.str.replace(r"[^\w\s/+:-]", "", regex=True).str.strip()

    out = out.replace(LABEL_MAP)

    nm = out.str.contains("not", na=False) & out.str.contains("mention", na=False)
    out = out.mask(nm, "not mentioned")

    pos = out.str.contains("posit", na=False)
    out = out.mask(pos, "positive")

    neg = out.str.contains("negat", na=False)
    out = out.mask(neg, "negative")

    neu = out.str.contains("neutral", na=False)
    out = out.mask(neu, "neutral")

    return out


def shorten_product_name(name: str, remove_words: List[str]) -> str:
    s = str(name or "").strip()
    for w in remove_words:
        s = re.sub(rf"\b{re.escape(w)}\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("-–—_ ")
    return s if s else str(name)


def make_unique_display_map(full_names: List[str], remove_words: List[str]) -> Dict[str, str]:
    base = [shorten_product_name(n, remove_words) for n in full_names]
    vc = pd.Series(base).value_counts()
    dupes = set(vc[vc > 1].index.tolist())

    if not dupes:
        return dict(zip(full_names, base))

    seen: Dict[str, int] = {d: 0 for d in dupes}
    out: Dict[str, str] = {}
    for full, b in zip(full_names, base):
        if b in dupes:
            seen[b] += 1
            out[full] = f"{b} #{seen[b]}"
        else:
            out[full] = b
    return out


def to_seeded_bool(seed: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(seed):
        return seed.fillna(False)

    num = pd.to_numeric(seed, errors="coerce")
    seeded = pd.Series(False, index=seed.index)

    mask_num = num.notna()
    seeded.loc[mask_num] = num.loc[mask_num].astype(float).eq(1.0)

    s = seed.astype("string").str.strip().str.lower()
    true_set = {"1", "true", "t", "yes", "y", "seeded", "incentivized", "gifted", "paid"}
    seeded.loc[~mask_num] = s.loc[~mask_num].isin(true_set)

    return seeded.fillna(False)


def safe_div(a: pd.Series, b: pd.Series) -> np.ndarray:
    a_np = a.to_numpy(dtype=float)
    b_np = b.to_numpy(dtype=float)
    return np.where(b_np > 0, a_np / b_np, np.nan)


def fmt_pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x*100:.1f}%"


def fmt_pm(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:+.2f}"


def rename_first_col(df: pd.DataFrame, new_name: str) -> pd.DataFrame:
    """Robust across pandas versions: after reset_index(), first column may not be 'index'."""
    if df.shape[1] == 0:
        return df
    return df.rename(columns={df.columns[0]: new_name})


# -----------------------------
# Compute (FAST; no huge melt)
# -----------------------------
@dataclass
class DataPack:
    summary: pd.DataFrame
    product_summary: pd.DataFrame
    invalid_labels: pd.DataFrame
    name_map: pd.DataFrame


def compute_fast_summary(
    df: pd.DataFrame,
    product_col: str,
    pillar_cols: List[str],
    stars_col: Optional[str],
    shorten_names: bool,
    remove_words: List[str],
) -> DataPack:
    cols = [product_col] + pillar_cols + ([stars_col] if stars_col else [])
    work = df[cols].copy()

    # product series
    work[product_col] = work[product_col].astype("string").fillna("Unknown").str.strip()
    product = work[product_col].astype(str)

    # stars parsed once
    if stars_col:
        stars = parse_stars_series(work[stars_col])
    else:
        stars = pd.Series(np.nan, index=work.index)

    # name mapping
    full_products = sorted(product.dropna().unique().tolist())
    mapping = make_unique_display_map(full_products, remove_words=remove_words) if shorten_names else {p: p for p in full_products}
    name_map_df = pd.DataFrame({"product_full": list(mapping.keys()), "product_display": list(mapping.values())}).sort_values("product_display")

    # product summary (ROBUST: rename first col not just "index")
    prod = product.value_counts(dropna=False).rename("review_count").reset_index()
    prod = rename_first_col(prod, "product_full")

    if stars_col:
        prod_stars = (
            pd.DataFrame({"product_full": product, "stars": stars})
            .groupby("product_full", dropna=False)["stars"]
            .mean()
            .reset_index()
        )
        prod = prod.merge(prod_stars.rename(columns={"stars": "avg_stars_overall"}), on="product_full", how="left")
    else:
        prod["avg_stars_overall"] = np.nan

    prod["product_display"] = prod["product_full"].map(mapping).fillna(prod["product_full"])

    out_rows = []
    invalid_rows = []

    for pillar in pillar_cols:
        lbl_norm = normalize_label_series(work[pillar])

        invalid_mask = ~lbl_norm.isin(VALID_LABELS)
        if invalid_mask.any():
            vc = lbl_norm[invalid_mask].value_counts().head(25)
            for val, cnt in vc.items():
                invalid_rows.append({"pillar": pillar, "invalid_value": str(val), "count": int(cnt)})

        # coerce invalid -> not mentioned for stable compute
        lbl_clean = lbl_norm.where(lbl_norm.isin(VALID_LABELS), "not mentioned")

        # counts per product (ROBUST: rename first column after reset_index)
        ct = pd.crosstab(product, lbl_clean.astype(str), dropna=False)
        ct = ct.reindex(columns=VALID_LABELS, fill_value=0).reset_index()
        ct = rename_first_col(ct, "product_full")

        ct = ct.rename(columns={
            "positive": "positive_count",
            "neutral": "neutral_count",
            "negative": "negative_count",
            "not mentioned": "not_mentioned_count",
        })

        ct["pillar"] = pillar
        ct["total_reviews"] = ct[["positive_count", "neutral_count", "negative_count", "not_mentioned_count"]].sum(axis=1)
        ct["mentions"] = ct[["positive_count", "neutral_count", "negative_count"]].sum(axis=1)

        ct["mention_rate"] = np.where(ct["total_reviews"] > 0, ct["mentions"] / ct["total_reviews"], 0.0)
        ct["net_sentiment"] = safe_div((ct["positive_count"] - ct["negative_count"]), ct["mentions"])
        ct["neg_share_mentions"] = safe_div(ct["negative_count"], ct["mentions"])
        ct["pos_share_mentions"] = safe_div(ct["positive_count"], ct["mentions"])
        ct["neu_share_mentions"] = safe_div(ct["neutral_count"], ct["mentions"])

        # avg stars among mentions (label != not mentioned)
        if stars_col:
            mask = (lbl_clean != "not mentioned") & stars.notna()
            sum_stars = stars.where(mask).groupby(product).sum(min_count=1)
            cnt_stars = stars.where(mask).groupby(product).count()
            avg = (sum_stars / cnt_stars).rename("avg_stars_mentions").reset_index()
            avg = rename_first_col(avg, "product_full")

            ct = ct.merge(avg, on="product_full", how="left")
            ct["n_star_mentions"] = ct["product_full"].map(cnt_stars).fillna(0).astype(int)
        else:
            ct["avg_stars_mentions"] = np.nan
            ct["n_star_mentions"] = 0

        out_rows.append(ct)

    summary = pd.concat(out_rows, ignore_index=True)
    summary["product_display"] = summary["product_full"].map(mapping).fillna(summary["product_full"])

    # mentions total per product -> share
    prod_mentions_total = summary.groupby("product_full", dropna=False)["mentions"].sum().rename("product_mentions_total").reset_index()
    summary = summary.merge(prod_mentions_total, on="product_full", how="left")
    summary["mention_share_of_product_mentions"] = np.where(
        summary["product_mentions_total"] > 0,
        summary["mentions"] / summary["product_mentions_total"],
        0.0,
    )
    summary["opportunity_score"] = summary["mention_share_of_product_mentions"] * pd.Series(summary["neg_share_mentions"]).fillna(0)

    # product-level mention sentiment
    prod_agg = summary.groupby("product_full", dropna=False).agg(
        pos_mentions=("positive_count", "sum"),
        neg_mentions=("negative_count", "sum"),
        total_mentions=("mentions", "sum"),
    ).reset_index()

    prod_agg["overall_net_sentiment_mentions"] = np.where(
        prod_agg["total_mentions"] > 0,
        (prod_agg["pos_mentions"] - prod_agg["neg_mentions"]) / prod_agg["total_mentions"],
        np.nan,
    )
    prod_agg["overall_neg_share_mentions"] = np.where(
        prod_agg["total_mentions"] > 0,
        prod_agg["neg_mentions"] / prod_agg["total_mentions"],
        np.nan,
    )

    prod = prod.merge(prod_mentions_total, on="product_full", how="left")
    prod = prod.merge(
        prod_agg[["product_full", "overall_net_sentiment_mentions", "overall_neg_share_mentions"]],
        on="product_full", how="left"
    )
    prod["product_mentions_total"] = prod["product_mentions_total"].fillna(0).astype(int)

    # top pillars strings
    def top_pillars_str(g: pd.DataFrame, k: int = 3) -> str:
        gg = g.sort_values("mention_share_of_product_mentions", ascending=False).head(k)
        return ", ".join([f"{r.pillar} ({r.mention_share_of_product_mentions*100:.0f}%)" for r in gg.itertuples()])

    top3 = summary.groupby("product_full").apply(lambda g: top_pillars_str(g, 3)).rename("top_mentioned_pillars").reset_index()
    top3 = rename_first_col(top3, "product_full")
    prod = prod.merge(top3, on="product_full", how="left")

    # top opportunity pillar per product
    tops = []
    for pfull, g in summary.groupby("product_full"):
        g2 = g.copy()
        min_m = max(5, int(g2["mentions"].sum() * 0.02)) if g2["mentions"].sum() > 0 else 5
        g2 = g2[g2["mentions"] >= min_m]
        best = None if g2.empty else g2.sort_values("opportunity_score", ascending=False).iloc[0]["pillar"]
        tops.append((pfull, best))
    prod = prod.merge(pd.DataFrame(tops, columns=["product_full", "top_opportunity_pillar"]), on="product_full", how="left")

    invalid_df = (
        pd.DataFrame(invalid_rows).sort_values(["pillar", "count"], ascending=[True, False])
        if invalid_rows else pd.DataFrame(columns=["pillar", "invalid_value", "count"])
    )

    prod = prod.sort_values("product_display")
    return DataPack(summary=summary, product_summary=prod, invalid_labels=invalid_df, name_map=name_map_df)


# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="AX Pillar Bench — Best in Class", layout="wide")

st.title("🏁 AX Product Pillar Benchmark — Best‑in‑Class")
st.caption("Fast updates + impactful visuals for comparing products across key pillars.")

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])
if not uploaded:
    st.info("Upload your processed output to begin.")
    st.stop()

file_bytes = uploaded.getvalue()
file_id = md5_bytes(file_bytes)

# Load base df once per file
if st.session_state.get("file_id") != file_id:
    df_base = load_file_bytes(file_bytes, uploaded.name)
    df_base.columns = [clean_col(c) for c in df_base.columns]
    st.session_state["file_id"] = file_id
    st.session_state["df_base"] = df_base
    st.session_state.pop("compute_key", None)
    st.session_state.pop("pack", None)
else:
    df_base = st.session_state["df_base"]

cols = df_base.columns.tolist()

# Column mapping
st.sidebar.subheader("Column mapping")
product_col = st.sidebar.selectbox(
    "Product column",
    cols,
    index=cols.index(DEFAULT_PRODUCT_COL) if DEFAULT_PRODUCT_COL in cols else 0,
)

stars_guess = guess_stars_col(df_base)
stars_col = st.sidebar.selectbox(
    "Stars column (optional)",
    ["(none)"] + cols,
    index=(["(none)"] + cols).index(stars_guess) if stars_guess in cols else 0,
)
stars_col = None if stars_col == "(none)" else stars_col

excluded = {product_col}
if stars_col:
    excluded.add(stars_col)
excluded.add(DEFAULT_SEEDED_COL)

default_pillars_present = [p for p in DEFAULT_PILLARS if p in cols]
pillar_cols = st.sidebar.multiselect(
    "Pillar columns",
    [c for c in cols if c not in excluded],
    default=default_pillars_present if default_pillars_present else [c for c in cols if c not in excluded],
)
if not pillar_cols:
    st.error("Select at least one pillar column.")
    st.stop()

# Seeded filter (apply BEFORE compute)
st.sidebar.markdown("---")
st.sidebar.subheader("Data filters")
seeded_col_present = DEFAULT_SEEDED_COL in df_base.columns
seeded_mode = "Include all"
seeded_bool = pd.Series(False, index=df_base.index)
seeded_count = 0

if seeded_col_present:
    seeded_mode = st.sidebar.selectbox(
        "Seeded reviews",
        ["Include all", "Exclude seeded", "Only seeded"],
        index=1,
        help="Filters using 'Seeded Flag' BEFORE computing charts.",
    )
    seeded_bool = to_seeded_bool(df_base[DEFAULT_SEEDED_COL])
    seeded_count = int(seeded_bool.sum())
else:
    st.sidebar.caption("Seeded Flag column not found.")

if seeded_col_present and seeded_mode == "Exclude seeded":
    df_work = df_base.loc[~seeded_bool].copy()
elif seeded_col_present and seeded_mode == "Only seeded":
    df_work = df_base.loc[seeded_bool].copy()
else:
    df_work = df_base.copy()

# Presentation controls
st.sidebar.markdown("---")
st.sidebar.subheader("Presentation")
shorten_names = st.sidebar.checkbox("Shorten product names (remove Dyson/Shark)", value=True)
remove_words = ["Dyson", "Shark"]

theme = st.sidebar.radio("Theme", ["Light", "Dark"], index=0)
template = "plotly_white" if theme == "Light" else "plotly_dark"

pillar_order_mode = st.sidebar.selectbox(
    "Pillar order",
    ["Default", "Most mentioned", "Most negative", "Biggest opportunity"],
    index=0,
)

if st.sidebar.button("🔄 Reset / Clear computed cache"):
    st.session_state.pop("compute_key", None)
    st.session_state.pop("pack", None)
    st.rerun()

# Compute key must include seeded_mode + included row count
compute_key = (
    file_id,
    product_col,
    tuple(pillar_cols),
    stars_col,
    shorten_names,
    tuple(remove_words),
    seeded_col_present,
    seeded_mode,
    len(df_work),
)

if st.session_state.get("compute_key") != compute_key:
    with st.spinner("Computing metrics..."):
        pack = compute_fast_summary(
            df=df_work,
            product_col=product_col,
            pillar_cols=pillar_cols,
            stars_col=stars_col,
            shorten_names=shorten_names,
            remove_words=remove_words,
        )
    st.session_state["compute_key"] = compute_key
    st.session_state["pack"] = pack
else:
    pack = st.session_state["pack"]

summary = pack.summary.copy()
prod = pack.product_summary.copy()

# View filters
st.sidebar.markdown("---")
st.sidebar.subheader("View filters")
display_by_full = dict(zip(prod["product_full"], prod["product_display"]))

selected_products_full = st.sidebar.multiselect(
    "Products",
    options=prod["product_full"].tolist(),
    default=prod["product_full"].tolist(),
    format_func=lambda x: display_by_full.get(x, x),
)

all_pillars = sorted(summary["pillar"].unique().tolist(), key=lambda p: DEFAULT_PILLARS.index(p) if p in DEFAULT_PILLARS else 10_000)
selected_pillars = st.sidebar.multiselect("Pillars", options=all_pillars, default=all_pillars)

summary = summary[summary["product_full"].isin(selected_products_full) & summary["pillar"].isin(selected_pillars)].copy()
prod = prod[prod["product_full"].isin(selected_products_full)].copy().sort_values("product_display")
product_order = prod["product_display"].tolist()

def pillar_order(df: pd.DataFrame) -> List[str]:
    pillars = df["pillar"].astype(str).unique().tolist()
    if pillar_order_mode == "Default":
        base = [p for p in DEFAULT_PILLARS if p in pillars]
        rest = [p for p in pillars if p not in base]
        return base + sorted(rest)
    if pillar_order_mode == "Most mentioned":
        return df.groupby("pillar")["mentions"].sum().sort_values(ascending=False).index.tolist()
    if pillar_order_mode == "Most negative":
        tmp = df.copy()
        tmp["wneg"] = pd.to_numeric(tmp["neg_share_mentions"], errors="coerce").fillna(0) * tmp["mentions"].fillna(0)
        agg = (tmp.groupby("pillar")["wneg"].sum() / tmp.groupby("pillar")["mentions"].sum().replace(0, np.nan)).sort_values(ascending=False)
        return agg.index.tolist()
    return df.groupby("pillar")["opportunity_score"].mean().sort_values(ascending=False).index.tolist()

pillars_ordered = [p for p in pillar_order(summary) if p in selected_pillars]

summary["product_display"] = pd.Categorical(summary["product_display"], categories=product_order, ordered=True)
summary["pillar"] = pd.Categorical(summary["pillar"], categories=pillars_ordered, ordered=True)

# Top status
st.markdown("---")
c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1, 1, 1.2])
c1.metric("Reviews (base)", f"{len(df_base):,}")
c2.metric("Seeded (base)", f"{seeded_count:,}" if seeded_col_present else "N/A")
c3.metric("Reviews (included)", f"{len(df_work):,}")
c4.metric("Products", f"{prod['product_full'].nunique():,}")
c5.metric("Pillars", f"{len(selected_pillars):,}")
if seeded_col_present:
    st.caption(f"Seeded mode: **{seeded_mode}** · Included reviews: **{len(df_work):,}**")

# Product scorecards
st.subheader("🔎 Product scorecards")
cols_cards = st.columns(min(4, max(1, len(prod))))
for i, r in enumerate(prod.itertuples()):
    with cols_cards[i % len(cols_cards)]:
        st.markdown(f"### {r.product_display}")
        st.metric("Reviews", f"{int(r.review_count):,}")
        st.metric("Total mentions", f"{int(r.product_mentions_total):,}")
        st.metric("Net sentiment (mentions)", fmt_pm(float(r.overall_net_sentiment_mentions)) if pd.notna(r.overall_net_sentiment_mentions) else "—")
        st.metric("Neg share (mentions)", fmt_pct(float(r.overall_neg_share_mentions)) if pd.notna(r.overall_neg_share_mentions) else "—")
        if stars_col and pd.notna(r.avg_stars_overall):
            st.metric("Avg stars (overall)", f"{r.avg_stars_overall:.2f}")
        st.caption(f"**Top mentioned:** {r.top_mentioned_pillars or '—'}")
        st.caption(f"**Top opportunity:** {r.top_opportunity_pillar or '—'}")

st.markdown("---")

tab_story, tab_stack, tab_faceoff, tab_table, tab_health = st.tabs(
    ["Story Heatmaps", "Stacked Bars", "Pillar Faceoff", "Table + Export", "Health Check"]
)

with tab_story:
    st.subheader("Conversation focus")
    st.caption("Heatmap = share of each product’s total mentions by pillar (each product sums to 100% across pillars).")

    fig_focus = px.density_heatmap(
        summary,
        x="pillar",
        y="product_display",
        z="mention_share_of_product_mentions",
        color_continuous_scale="Blues",
        template=template,
        hover_data={"mentions": True, "mention_rate": ":.1%", "avg_stars_mentions": True},
    )
    fig_focus.update_layout(height=min(650, 180 + 38 * len(product_order)), margin=dict(l=10, r=10, t=10, b=10))
    fig_focus.update_xaxes(title="")
    fig_focus.update_yaxes(title="")
    st.plotly_chart(fig_focus, use_container_width=True)

    st.subheader("How it feels")
    fig_sent = px.density_heatmap(
        summary,
        x="pillar",
        y="product_display",
        z="net_sentiment",
        color_continuous_scale="RdYlGn",
        range_color=(-1, 1),
        template=template,
        hover_data={"mentions": True, "neg_share_mentions": ":.1%", "avg_stars_mentions": True},
    )
    fig_sent.update_layout(height=min(650, 180 + 38 * len(product_order)), margin=dict(l=10, r=10, t=10, b=10))
    fig_sent.update_xaxes(title="")
    fig_sent.update_yaxes(title="")
    st.plotly_chart(fig_sent, use_container_width=True)

    st.subheader("Hotspots (high‑volume pain)")
    min_mentions = st.slider("Min mentions to include", 0, int(summary["mentions"].max() if len(summary) else 0), 10)
    opp = summary[summary["mentions"] >= min_mentions].copy().sort_values("opportunity_score", ascending=False).head(20)

    opp_show = opp.copy()
    opp_show["mention_share_%"] = (opp_show["mention_share_of_product_mentions"] * 100).round(1)
    opp_show["neg_share_%"] = (pd.to_numeric(opp_show["neg_share_mentions"], errors="coerce") * 100).round(1)
    opp_show["net_sentiment"] = pd.to_numeric(opp_show["net_sentiment"], errors="coerce").round(2)
    opp_show["opportunity_(0-100)"] = (pd.to_numeric(opp_show["opportunity_score"], errors="coerce") * 100).round(2)

    if stars_col:
        opp_show["avg_stars_mentions"] = pd.to_numeric(opp_show["avg_stars_mentions"], errors="coerce").round(2)

    st.dataframe(
        opp_show[["product_display", "pillar", "mentions", "mention_share_%", "neg_share_%", "net_sentiment", "avg_stars_mentions", "opportunity_(0-100)"]]
        .rename(columns={"product_display": "Product", "avg_stars_mentions": "Avg stars (mentions)"}),
        use_container_width=True,
        height=420,
    )

with tab_stack:
    st.subheader("Stacked bars by pillar")
    mode = st.radio(
        "X-axis mode",
        ["Counts (positive/neutral/negative/not mentioned)", "% of product total mentions (pos/neu/neg only)"],
        index=0,
        horizontal=True,
    )

    if mode.startswith("Counts"):
        plot_long = summary.melt(
            id_vars=["product_display", "pillar", "mentions", "mention_rate", "avg_stars_mentions"],
            value_vars=["positive_count", "neutral_count", "negative_count", "not_mentioned_count"],
            var_name="label",
            value_name="value",
        )
        label_map = {
            "positive_count": "positive",
            "neutral_count": "neutral",
            "negative_count": "negative",
            "not_mentioned_count": "not mentioned",
        }
        plot_long["label"] = plot_long["label"].map(label_map)
        plot_long["label"] = pd.Categorical(plot_long["label"], categories=LABEL_ORDER, ordered=True)

        fig = px.bar(
            plot_long.sort_values(["product_display", "pillar", "label"]),
            x="value",
            y="pillar",
            color="label",
            facet_row="product_display",
            orientation="h",
            barmode="stack",
            color_discrete_map=SENTIMENT_COLORS,
            template=template,
            title="Counts of labels per pillar (faceted by product)",
        )
        fig.update_xaxes(title="Count")
    else:
        plot_long = summary.melt(
            id_vars=["product_display", "pillar", "product_mentions_total"],
            value_vars=["positive_count", "neutral_count", "negative_count"],
            var_name="label",
            value_name="count",
        )
        label_map = {"positive_count": "positive", "neutral_count": "neutral", "negative_count": "negative"}
        plot_long["label"] = plot_long["label"].map(label_map)
        plot_long["value_pct"] = np.where(
            plot_long["product_mentions_total"] > 0,
            (plot_long["count"] / plot_long["product_mentions_total"]) * 100,
            0.0,
        )

        fig = px.bar(
            plot_long.sort_values(["product_display", "pillar", "label"]),
            x="value_pct",
            y="pillar",
            color="label",
            facet_row="product_display",
            orientation="h",
            barmode="stack",
            color_discrete_map=SENTIMENT_COLORS,
            template=template,
            title="% of product total mentions (pos/neu/neg only) — sums to 100% per product",
        )
        fig.update_xaxes(title="% of product total mentions")

    fig.update_layout(height=min(1800, 260 + 260 * len(product_order)), margin=dict(l=10, r=10, t=60, b=10))
    fig.for_each_annotation(lambda a: a.update(text=a.text.replace("product_display=", "Product: ")))
    fig.update_yaxes(title="")
    st.plotly_chart(fig, use_container_width=True)

with tab_faceoff:
    st.subheader("Pillar faceoff")
    pillar_pick = st.selectbox("Choose pillar", pillars_ordered, index=0)
    p = summary[summary["pillar"] == pillar_pick].copy().sort_values("product_display")

    c1, c2, c3 = st.columns(3)
    with c1:
        fig_m = px.bar(p, x="product_display", y="mention_rate", template=template, title="Mention rate")
        fig_m.update_layout(height=360)
        fig_m.update_yaxes(tickformat=".0%")
        fig_m.update_xaxes(title="")
        st.plotly_chart(fig_m, use_container_width=True)

    with c2:
        fig_n = px.bar(p, x="product_display", y="net_sentiment", template=template, title="Net sentiment")
        fig_n.update_layout(height=360, yaxis_range=[-1, 1])
        fig_n.update_xaxes(title="")
        st.plotly_chart(fig_n, use_container_width=True)

    with c3:
        if stars_col:
            fig_s = px.bar(p, x="product_display", y="avg_stars_mentions", template=template, title="Avg stars (mentions)")
            fig_s.update_layout(height=360, yaxis_range=[1, 5])
            fig_s.update_xaxes(title="")
            st.plotly_chart(fig_s, use_container_width=True)
        else:
            st.info("Provide a stars column to enable avg stars comparison.")

with tab_table:
    st.subheader("Full Product × Pillar table")
    out = summary.copy()
    st.dataframe(out.sort_values(["product_display", "pillar"]), use_container_width=True, height=560)
    st.download_button(
        "Download summary CSV",
        data=out.to_csv(index=False).encode("utf-8"),
        file_name="ax_product_pillar_summary.csv",
        mime="text/csv",
        use_container_width=True,
    )

with tab_health:
    st.subheader("Health Check")
    if len(pack.invalid_labels):
        st.warning("Invalid/unknown label values found (treated as 'not mentioned' for metrics).")
        st.dataframe(pack.invalid_labels, use_container_width=True, height=320)
    else:
        st.success("All pillar labels are valid.")
    st.markdown("### Product name mapping")
    st.dataframe(pack.name_map, use_container_width=True, height=320)


