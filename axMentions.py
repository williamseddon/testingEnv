







# axMentions.py
# ------------------------------------------------------------
# AX Product Pillar Benchmark — BEST‑IN‑CLASS (FAST + CLEAN)
#
# ✅ Robust + fast compute (no stale chart issues)
# ✅ Seeded review filter (Include / Exclude / Only)
# ✅ Product name shortening (remove Dyson/Shark)
# ✅ BEST chart (your screenshot):
#    Conversation Map by Pillar (bubble grid)
#      - X = Pillar
#      - Y = Product
#      - Size = Mention rate OR Mentions OR % of product mentions
#      - Color = Net sentiment OR Avg stars (mentions)
#
# Also includes:
#  - Heatmaps (focus + sentiment)
#  - Stacked bars (counts or % of product mentions)
#  - Pillar faceoff
#  - Hotspots / Differentiators
#  - Health checks + export
#
# Run:
#   streamlit run axMentions.py
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
import plotly.graph_objects as go
from plotly.colors import qualitative
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
    "stars",
    "star",
    "rating",
    "star rating",
    "star_rating",
    "overall rating",
    "overall_rating",
    "score",
]

REMOVE_BRANDS_DEFAULT = ["Dyson", "Shark"]


# -----------------------------
# Helpers
# -----------------------------
def clean_col(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def load_file_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".csv", ".tsv", ".txt")):
        sample = file_bytes[:2048].decode("utf-8", errors="ignore")
        sep = "\t" if sample.count("\t") > sample.count(",") else ","
        return pd.read_csv(io.BytesIO(file_bytes), sep=sep)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(file_bytes))
    raise ValueError("Unsupported file type. Upload CSV/TSV/TXT or Excel.")


def guess_stars_col(df: pd.DataFrame) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in STAR_COL_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]

    # heuristic: numeric mostly 1-5
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            s = df[c].dropna()
            if len(s) >= 10 and (s.between(1, 5).mean() > 0.85):
                return c
    return None


def parse_stars_series(s: pd.Series) -> pd.Series:
    """Vectorized parse stars -> float."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    extracted = s.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


def normalize_label_series(s: pd.Series) -> pd.Series:
    """Vectorized normalization; unknown values kept for QA then coerced by caller."""
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
    """Normalize seeded flag into boolean (True = seeded)."""
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


def scale_bubble_sizes(values: pd.Series, min_size: int = 10, max_size: int = 55) -> np.ndarray:
    """Sqrt scale for stable bubble sizing across big/small products."""
    v = pd.to_numeric(values, errors="coerce").fillna(0).clip(lower=0).to_numpy(dtype=float)
    v = np.sqrt(v)
    vmax = float(np.nanmax(v)) if len(v) else 0.0
    if vmax <= 0:
        return np.full(len(v), min_size, dtype=float)
    return min_size + (v / vmax) * (max_size - min_size)


# -----------------------------
# Compute pack
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

    # Product
    work[product_col] = work[product_col].astype("string").fillna("Unknown").str.strip()
    product = work[product_col].astype(str)

    # Stars parsed once
    stars = parse_stars_series(work[stars_col]) if stars_col else pd.Series(np.nan, index=work.index)

    # Display name map
    full_products = sorted(product.dropna().unique().tolist())
    mapping = make_unique_display_map(full_products, remove_words=remove_words) if shorten_names else {p: p for p in full_products}
    name_map_df = pd.DataFrame({"product_full": list(mapping.keys()), "product_display": list(mapping.values())}).sort_values("product_display")

    # Product summary: review count + overall stars
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

        lbl_clean = lbl_norm.where(lbl_norm.isin(VALID_LABELS), "not mentioned")

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

        # Avg stars among mentions for this pillar
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

    # totals per product for share-of-product calculations
    prod_mentions_total = summary.groupby("product_full", dropna=False)["mentions"].sum().rename("product_mentions_total").reset_index()
    summary = summary.merge(prod_mentions_total, on="product_full", how="left")

    summary["mention_share_of_product_mentions"] = np.where(
        summary["product_mentions_total"] > 0,
        summary["mentions"] / summary["product_mentions_total"],
        0.0,
    )
    summary["opportunity_score"] = summary["mention_share_of_product_mentions"] * pd.Series(summary["neg_share_mentions"]).fillna(0)

    # product rollups
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

    # top mentioned pillars
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
# BEST CHART: Conversation bubble map (your screenshot)
# -----------------------------
def conversation_bubble_map(
    summary: pd.DataFrame,
    product_order: List[str],
    pillar_order: List[str],
    template: str,
    size_mode: str,
    color_mode: str,
    stars_available: bool,
) -> go.Figure:
    """
    Bubble grid:
      X = pillar
      Y = product
      size = mention_rate OR mentions OR share_of_product_mentions
      color = net_sentiment OR avg_stars_mentions
    """

    df = summary.copy()
    df["pillar"] = df["pillar"].astype(str)
    df["product_display"] = df["product_display"].astype(str)

    # Only show points where there are actual mentions (pos/neu/neg)
    df = df[df["mentions"].fillna(0) > 0].copy()
    if df.empty:
        fig = go.Figure()
        fig.update_layout(template=template, height=420, title="Conversation map by pillar")
        return fig

    # ----- SIZE metric -----
    if size_mode.startswith("Mention rate"):
        size_base = pd.to_numeric(df["mention_rate"], errors="coerce").fillna(0) * 100.0
        size_title = "Mention rate (%)"
    elif size_mode.startswith("% of product mentions"):
        size_base = pd.to_numeric(df["mention_share_of_product_mentions"], errors="coerce").fillna(0) * 100.0
        size_title = "Share of product mentions (%)"
    else:
        # Count mode: sqrt for stability
        size_base = pd.to_numeric(df["mentions"], errors="coerce").fillna(0).clip(lower=0)
        size_title = "Mentions (count)"

    df["_bubble_size"] = scale_bubble_sizes(size_base, min_size=10, max_size=55)

    # ----- COLOR metric -----
    if color_mode == "Net sentiment":
        c = pd.to_numeric(df["net_sentiment"], errors="coerce")
        cmin, cmax, colorscale, colorbar_title = -1, 1, "RdYlGn", "net_sentiment"
        df["_color"] = c
        missing_mask = c.isna()
    else:
        # Avg stars among mentions
        if not stars_available:
            # fallback to net sentiment if stars not available
            c = pd.to_numeric(df["net_sentiment"], errors="coerce")
            cmin, cmax, colorscale, colorbar_title = -1, 1, "RdYlGn", "net_sentiment"
            df["_color"] = c
            missing_mask = c.isna()
        else:
            c = pd.to_numeric(df["avg_stars_mentions"], errors="coerce")
            cmin, cmax, colorscale, colorbar_title = 1, 5, "RdYlGn", "avg stars (mentions)"
            df["_color"] = c
            missing_mask = c.isna()

    # ----- Hover fields (preformatted strings so no 'nan') -----
    df["_mention_rate_str"] = (pd.to_numeric(df["mention_rate"], errors="coerce").fillna(0) * 100).round(1).astype(str) + "%"
    df["_mention_share_str"] = (pd.to_numeric(df["mention_share_of_product_mentions"], errors="coerce").fillna(0) * 100).round(1).astype(str) + "%"
    df["_net_str"] = pd.to_numeric(df["net_sentiment"], errors="coerce").map(lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
    df["_stars_str"] = pd.to_numeric(df["avg_stars_mentions"], errors="coerce").map(lambda x: f"{x:.2f}" if pd.notna(x) else "—")

    # customdata: product, pillar, mentions, pos, neu, neg, mention_rate, mention_share, net, stars
    cd = np.stack(
        [
            df["product_display"].to_numpy(),
            df["pillar"].to_numpy(),
            df["mentions"].fillna(0).astype(int).to_numpy(),
            df["positive_count"].fillna(0).astype(int).to_numpy(),
            df["neutral_count"].fillna(0).astype(int).to_numpy(),
            df["negative_count"].fillna(0).astype(int).to_numpy(),
            df["_mention_rate_str"].to_numpy(),
            df["_mention_share_str"].to_numpy(),
            df["_net_str"].to_numpy(),
            df["_stars_str"].to_numpy(),
        ],
        axis=-1,
    )

    hovertemplate = (
        "<b>%{customdata[0]}</b><br>"
        "Pillar: %{customdata[1]}<br>"
        f"{size_title}: "  # value shown below (we include both anyway)
        "<br>"
        "Mentions: %{customdata[2]:,}<br>"
        "Pos/Neu/Neg: %{customdata[3]:,} / %{customdata[4]:,} / %{customdata[5]:,}<br>"
        "Mention rate: %{customdata[6]}<br>"
        "Share of product mentions: %{customdata[7]}<br>"
        "Net sentiment: %{customdata[8]}<br>"
        "Avg stars (mentions): %{customdata[9]}<br>"
        "<extra></extra>"
    )

    # ----- Build figure with 1–2 traces (to handle missing colors gracefully) -----
    fig = go.Figure()

    # trace with valid colors
    valid = ~missing_mask
    fig.add_trace(
        go.Scatter(
            x=df.loc[valid, "pillar"],
            y=df.loc[valid, "product_display"],
            mode="markers",
            customdata=cd[valid.to_numpy()],
            hovertemplate=hovertemplate,
            marker=dict(
                size=df.loc[valid, "_bubble_size"],
                color=df.loc[valid, "_color"],
                colorscale=colorscale,
                cmin=cmin,
                cmax=cmax,
                showscale=True,
                colorbar=dict(title=colorbar_title),
                opacity=0.90,
                line=dict(width=1, color="rgba(255,255,255,0.9)"),
            ),
            showlegend=False,
        )
    )

    # trace for missing color values (grey)
    if missing_mask.any():
        fig.add_trace(
            go.Scatter(
                x=df.loc[missing_mask, "pillar"],
                y=df.loc[missing_mask, "product_display"],
                mode="markers",
                customdata=cd[missing_mask.to_numpy()],
                hovertemplate=hovertemplate,
                marker=dict(
                    size=df.loc[missing_mask, "_bubble_size"],
                    color="rgba(170,170,170,0.75)",
                    opacity=0.85,
                    line=dict(width=1, color="rgba(255,255,255,0.9)"),
                ),
                showlegend=False,
            )
        )

    # Axes ordering to match your screenshot
    fig.update_xaxes(
        type="category",
        categoryorder="array",
        categoryarray=[str(p) for p in pillar_order],
        tickangle=35,
        title="Pillar",
        showgrid=False,
    )
    fig.update_yaxes(
        type="category",
        categoryorder="array",
        categoryarray=product_order,
        title="Product",
        showgrid=True,
        gridcolor="rgba(0,0,0,0.08)",
    )

    fig.update_layout(
        template=template,
        height=max(420, 120 + 70 * len(product_order)),
        margin=dict(l=40, r=40, t=80, b=60),
        title="Conversation map by pillar (size = volume, color = sentiment)",
    )

    return fig


# -----------------------------
# OPTIONAL: Bubble-line storyline (kept for your earlier request)
# -----------------------------
def bubble_line_story(
    summary: pd.DataFrame,
    product_order: List[str],
    pillars_ordered: List[str],
    template: str,
    stars_available: bool,
) -> go.Figure:
    """
    One line chart per product with bubbles on pillars.
    Y defaults to share of product mentions (%).
    Bubble size = mentions
    Bubble color = net sentiment
    """
    df_plot = summary.copy()
    df_plot["pillar_str"] = df_plot["pillar"].astype(str)

    pillar_x = [str(p) for p in pillars_ordered]

    df_plot["_y"] = pd.to_numeric(df_plot["mention_share_of_product_mentions"], errors="coerce") * 100.0
    df_plot["_size_base"] = pd.to_numeric(df_plot["mentions"], errors="coerce").fillna(0)
    df_plot["_color"] = pd.to_numeric(df_plot["net_sentiment"], errors="coerce")

    palette = qualitative.Set2 + qualitative.Safe + qualitative.Dark2 + qualitative.Plotly
    product_colors = {p: palette[i % len(palette)] for i, p in enumerate(product_order)}

    fig = go.Figure()
    show_scale = True

    for p_name in product_order:
        g = df_plot[df_plot["product_display"] == p_name].copy()
        if g.empty:
            continue
        g = g.set_index("pillar_str").reindex(pillar_x).reset_index()
        g["product_display"] = p_name

        # line
        fig.add_trace(
            go.Scatter(
                x=g["pillar_str"],
                y=g["_y"],
                mode="lines",
                name=p_name,
                line=dict(color=product_colors.get(p_name, "#333"), width=2),
                legendgroup=p_name,
                showlegend=True,
                hoverinfo="skip",
            )
        )

        gm = g[g["mentions"].fillna(0) > 0].copy()
        if gm.empty:
            continue

        sizes = scale_bubble_sizes(gm["_size_base"], min_size=7, max_size=28)

        gm["_mr_str"] = (pd.to_numeric(gm["mention_rate"], errors="coerce").fillna(0) * 100).round(1).astype(str) + "%"
        gm["_ns_str"] = pd.to_numeric(gm["net_sentiment"], errors="coerce").map(lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
        gm["_stars_str"] = pd.to_numeric(gm["avg_stars_mentions"], errors="coerce").map(lambda x: f"{x:.2f}" if pd.notna(x) else "—")

        cd_cols = [
            gm["product_display"].astype(str).to_numpy(),
            gm["mentions"].fillna(0).astype(int).to_numpy(),
            gm["positive_count"].fillna(0).astype(int).to_numpy(),
            gm["neutral_count"].fillna(0).astype(int).to_numpy(),
            gm["negative_count"].fillna(0).astype(int).to_numpy(),
            gm["_mr_str"].to_numpy(),
            gm["_ns_str"].to_numpy(),
            gm["_stars_str"].to_numpy(),
        ]
        cd = np.stack(cd_cols, axis=-1)

        hovertemplate = (
            "<b>%{customdata[0]}</b><br>"
            "Pillar: %{x}<br>"
            "Mention share: %{y:.2f}%<br>"
            "Mentions: %{customdata[1]:,}<br>"
            "Pos/Neu/Neg: %{customdata[2]:,} / %{customdata[3]:,} / %{customdata[4]:,}<br>"
            "Mention rate: %{customdata[5]}<br>"
            "Net sentiment: %{customdata[6]}<br>"
            "Avg stars (mentions): %{customdata[7]}<br>"
            "<extra></extra>"
        )

        fig.add_trace(
            go.Scatter(
                x=gm["pillar_str"],
                y=gm["_y"],
                mode="markers",
                name=p_name,
                legendgroup=p_name,
                showlegend=False,
                customdata=cd,
                hovertemplate=hovertemplate,
                marker=dict(
                    size=sizes,
                    color=gm["_color"],
                    colorscale="RdYlGn",
                    cmin=-1,
                    cmax=1,
                    showscale=show_scale,
                    colorbar=dict(title="net_sentiment") if show_scale else None,
                    line=dict(color="rgba(255,255,255,0.95)", width=1),
                    opacity=0.95,
                ),
            )
        )
        show_scale = False

    fig.update_layout(
        template=template,
        height=580,
        margin=dict(l=20, r=20, t=60, b=40),
        legend_title_text="Product",
        hovermode="closest",
        title="Storyline view (line = focus %, bubbles = volume + sentiment)",
    )
    fig.update_xaxes(
        title="",
        type="category",
        categoryorder="array",
        categoryarray=pillar_x,
        tickangle=-25,
    )
    fig.update_yaxes(title="Mention share (%)", rangemode="tozero")

    return fig


# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="AX Pillar Benchmark — Best in Class", layout="wide")

st.title("🏁 AX Product Pillar Benchmark — Best‑in‑Class")
st.caption("Designed for fast, clean comparisons across products and pillars.")

uploaded = st.sidebar.file_uploader("Upload processed output (CSV/Excel)", type=["csv", "tsv", "txt", "xlsx", "xls"])
if not uploaded:
    st.info("Upload your processed output to begin.")
    st.stop()

file_bytes = uploaded.getvalue()
file_id = md5_bytes(file_bytes)

# Load base DF once per file upload
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

# Sidebar: column mapping
st.sidebar.subheader("Column mapping")
product_col = st.sidebar.selectbox(
    "Product column",
    cols,
    index=cols.index(DEFAULT_PRODUCT_COL) if DEFAULT_PRODUCT_COL in cols else 0,
)

stars_guess = guess_stars_col(df_base)
stars_col_choice = st.sidebar.selectbox(
    "Stars column (optional)",
    ["(none)"] + cols,
    index=(["(none)"] + cols).index(stars_guess) if stars_guess in cols else 0,
)
stars_col = None if stars_col_choice == "(none)" else stars_col_choice

seeded_col_choice = st.sidebar.selectbox(
    "Seeded flag column (optional)",
    ["(none)"] + cols,
    index=(["(none)"] + cols).index(DEFAULT_SEEDED_COL) if DEFAULT_SEEDED_COL in cols else 0,
)
seeded_col = None if seeded_col_choice == "(none)" else seeded_col_choice

excluded = {product_col}
if stars_col:
    excluded.add(stars_col)
if seeded_col:
    excluded.add(seeded_col)

default_pillars_present = [p for p in DEFAULT_PILLARS if p in cols]
pillar_cols = st.sidebar.multiselect(
    "Pillar columns",
    [c for c in cols if c not in excluded],
    default=default_pillars_present if default_pillars_present else [c for c in cols if c not in excluded],
)
if not pillar_cols:
    st.error("Select at least one pillar column.")
    st.stop()

# Sidebar: filters
st.sidebar.markdown("---")
st.sidebar.subheader("Data filters")

seeded_mode = "Include all"
seeded_bool = pd.Series(False, index=df_base.index)
seeded_count = 0

if seeded_col and seeded_col in df_base.columns:
    seeded_mode = st.sidebar.selectbox(
        "Seeded reviews",
        ["Include all", "Exclude seeded", "Only seeded"],
        index=1,
        help="Applied BEFORE computing charts.",
    )
    seeded_bool = to_seeded_bool(df_base[seeded_col])
    seeded_count = int(seeded_bool.sum())
else:
    st.sidebar.caption("No seeded flag column selected.")

if seeded_col and seeded_mode == "Exclude seeded":
    df_work = df_base.loc[~seeded_bool].copy()
elif seeded_col and seeded_mode == "Only seeded":
    df_work = df_base.loc[seeded_bool].copy()
else:
    df_work = df_base.copy()

# Sidebar: presentation
st.sidebar.markdown("---")
st.sidebar.subheader("Presentation")

shorten_names = st.sidebar.checkbox("Shorten product names (remove Dyson/Shark)", value=True)
remove_words = REMOVE_BRANDS_DEFAULT

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

# Compute key includes seeded settings + rowcount so charts always update correctly
compute_key = (
    file_id,
    product_col,
    tuple(pillar_cols),
    stars_col,
    seeded_col,
    seeded_mode,
    df_work.shape[0],
    df_work[product_col].nunique(),
    shorten_names,
    tuple(remove_words),
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

# Sidebar: view filters
st.sidebar.markdown("---")
st.sidebar.subheader("View filters")

display_by_full = dict(zip(prod["product_full"], prod["product_display"]))
selected_products_full = st.sidebar.multiselect(
    "Products",
    options=prod["product_full"].tolist(),
    default=prod["product_full"].tolist(),
    format_func=lambda x: display_by_full.get(x, x),
)

all_pillars = sorted(
    summary["pillar"].astype(str).unique().tolist(),
    key=lambda p: DEFAULT_PILLARS.index(p) if p in DEFAULT_PILLARS else 10_000
)
selected_pillars = st.sidebar.multiselect("Pillars", options=all_pillars, default=all_pillars)

summary = summary[summary["product_full"].isin(selected_products_full) & summary["pillar"].astype(str).isin(selected_pillars)].copy()
prod = prod[prod["product_full"].isin(selected_products_full)].copy().sort_values("product_display")
product_order = prod["product_display"].tolist()

def get_pillar_order(df: pd.DataFrame) -> List[str]:
    pillars = df["pillar"].astype(str).unique().tolist()
    if pillar_order_mode == "Default":
        base = [p for p in DEFAULT_PILLARS if p in pillars]
        rest = [p for p in pillars if p not in base]
        return base + sorted(rest)
    if pillar_order_mode == "Most mentioned":
        return df.groupby(df["pillar"].astype(str))["mentions"].sum().sort_values(ascending=False).index.tolist()
    if pillar_order_mode == "Most negative":
        tmp = df.copy()
        tmp["_wneg"] = pd.to_numeric(tmp["neg_share_mentions"], errors="coerce").fillna(0) * tmp["mentions"].fillna(0)
        agg = (
            tmp.groupby(tmp["pillar"].astype(str))["_wneg"].sum()
            / tmp.groupby(tmp["pillar"].astype(str))["mentions"].sum().replace(0, np.nan)
        ).sort_values(ascending=False)
        return agg.index.tolist()
    return df.groupby(df["pillar"].astype(str))["opportunity_score"].mean().sort_values(ascending=False).index.tolist()

pillars_ordered = [p for p in get_pillar_order(summary) if p in selected_pillars]

summary["product_display"] = pd.Categorical(summary["product_display"], categories=product_order, ordered=True)
summary["pillar"] = pd.Categorical(summary["pillar"].astype(str), categories=pillars_ordered, ordered=True)

# Top status row
st.markdown("---")
c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1, 1, 1.2])
c1.metric("Reviews (base)", f"{len(df_base):,}")
c2.metric("Seeded (base)", f"{seeded_count:,}" if seeded_col else "N/A")
c3.metric("Reviews (included)", f"{len(df_work):,}")
c4.metric("Products", f"{prod['product_full'].nunique():,}")
c5.metric("Pillars", f"{len(selected_pillars):,}")
if seeded_col:
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
    ["Story", "Stacked Bars", "Pillar Faceoff", "Table + Export", "Health Check"]
)

# -----------------------------
# TAB: Story (includes your requested chart)
# -----------------------------
with tab_story:
    st.subheader("Conversation map by pillar (size = volume, color = sentiment)")

    size_mode = st.radio(
        "",
        [
            "Mention rate (% of reviews mentioning pillar)",
            "Mentions (count)",
            "% of product mentions (pillar share)",
        ],
        index=0,
        horizontal=True,
        key="bubble_map_size_mode",
    )

    color_options = ["Net sentiment"]
    if stars_col:
        color_options.append("Avg stars (mentions)")

    color_mode = st.selectbox(
        "Circle color",
        color_options,
        index=0,
        key="bubble_map_color_mode",
    )

    fig = conversation_bubble_map(
        summary=summary,
        product_order=product_order,
        pillar_order=pillars_ordered,
        template=template,
        size_mode=size_mode,
        color_mode=color_mode,
        stars_available=bool(stars_col),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    with st.expander("Details: Heatmaps + Differentiators + Hotspots", expanded=True):
        st.subheader("1) Conversation focus (heatmap)")
        st.caption("Share of each product’s total mentions by pillar (each product sums to 100% across pillars).")

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

        st.subheader("2) How it feels (net sentiment heatmap)")
        st.caption("Net sentiment = (positive − negative) / mentions, from -1 to +1.")

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

        st.subheader("3) Biggest differentiators")
        st.caption("Ranks pillars by volume × variation (meaningful differences, not noise).")

        d = summary.copy()
        grp = d.groupby(d["pillar"].astype(str)).agg(
            total_mentions=("mentions", "sum"),
            share_min=("mention_share_of_product_mentions", "min"),
            share_max=("mention_share_of_product_mentions", "max"),
            net_min=("net_sentiment", "min"),
            net_max=("net_sentiment", "max"),
        ).reset_index().rename(columns={"pillar": "Pillar"})

        grp["share_range"] = grp["share_max"] - grp["share_min"]
        grp["net_range"] = (grp["net_max"] - grp["net_min"]).abs()
        grp["impact_share"] = grp["total_mentions"] * grp["share_range"]
        grp["impact_sentiment"] = grp["total_mentions"] * grp["net_range"]

        rank_mode = st.radio("Rank by", ["Conversation focus differences", "Sentiment differences"], horizontal=True)
        topN = st.slider("Show top N", 5, min(30, len(grp)), 12)

        show = grp.sort_values("impact_share" if rank_mode == "Conversation focus differences" else "impact_sentiment", ascending=False).head(topN).copy()
        show["Mention share range (pp)"] = (show["share_range"] * 100).round(1)
        show["Net sentiment range"] = show["net_range"].round(2)

        st.dataframe(
            show[["Pillar", "total_mentions", "Mention share range (pp)", "Net sentiment range"]].rename(columns={"total_mentions": "Total mentions"}),
            use_container_width=True,
            height=360,
        )

        st.subheader("4) Hotspots (high-volume pain)")
        st.caption("Opportunity = mention share × negative share.")

        min_mentions = st.slider("Min mentions to include", 0, int(summary["mentions"].max() if len(summary) else 0), 10)
        opp = summary[summary["mentions"] >= min_mentions].copy().sort_values("opportunity_score", ascending=False).head(25)

        opp_show = opp.copy()
        opp_show["Mention share (%)"] = (opp_show["mention_share_of_product_mentions"] * 100).round(1)
        opp_show["Negative share (%)"] = (pd.to_numeric(opp_show["neg_share_mentions"], errors="coerce") * 100).round(1)
        opp_show["Net sentiment"] = pd.to_numeric(opp_show["net_sentiment"], errors="coerce").round(2)
        opp_show["Opportunity (0-100)"] = (pd.to_numeric(opp_show["opportunity_score"], errors="coerce") * 100).round(2)
        if stars_col:
            opp_show["Avg stars (mentions)"] = pd.to_numeric(opp_show["avg_stars_mentions"], errors="coerce").round(2)

        cols_to_show = ["product_display", "pillar", "mentions", "Mention share (%)", "Negative share (%)", "Net sentiment", "Opportunity (0-100)"]
        if stars_col:
            cols_to_show.insert(6, "Avg stars (mentions)")

        st.dataframe(
            opp_show[cols_to_show].rename(columns={"product_display": "Product", "pillar": "Pillar", "mentions": "Mentions"}),
            use_container_width=True,
            height=420,
        )

    with st.expander("Alternative view: Bubble-line storyline chart (optional)", expanded=False):
        fig_story = bubble_line_story(
            summary=summary,
            product_order=product_order,
            pillars_ordered=pillars_ordered,
            template=template,
            stars_available=bool(stars_col),
        )
        st.plotly_chart(fig_story, use_container_width=True)

# -----------------------------
# TAB: Stacked Bars
# -----------------------------
with tab_stack:
    st.subheader("Stacked bars by pillar")
    st.caption("Switch between raw counts and % of product total mentions.")

    mode = st.radio(
        "X-axis mode",
        ["Counts (pos/neu/neg/not mentioned)", "% of product total mentions (pos/neu/neg only)"],
        index=0,
        horizontal=True,
    )

    cA, cB = st.columns([1, 1])
    with cA:
        limit_pillars = st.checkbox("Show only top pillars by mentions (overall)", value=False)
    with cB:
        top_k = st.slider("Top K pillars", 3, max(3, len(pillars_ordered)), min(8, len(pillars_ordered)))

    plot_base = summary.copy()
    if limit_pillars:
        top_p = plot_base.groupby(plot_base["pillar"].astype(str))["mentions"].sum().sort_values(ascending=False).head(top_k).index.tolist()
        plot_base = plot_base[plot_base["pillar"].astype(str).isin(top_p)].copy()
        plot_base["pillar"] = pd.Categorical(plot_base["pillar"].astype(str), categories=top_p, ordered=True)
        local_pillar_order = top_p
    else:
        local_pillar_order = pillars_ordered

    if mode.startswith("Counts"):
        plot_long = plot_base.melt(
            id_vars=["product_display", "pillar"],
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
        plot_long["label"] = pd.Categorical(plot_long["label"].map(label_map), categories=LABEL_ORDER, ordered=True)

        fig = px.bar(
            plot_long.sort_values(["product_display", "pillar", "label"]),
            x="value",
            y="pillar",
            color="label",
            facet_row="product_display",
            orientation="h",
            barmode="stack",
            color_discrete_map=SENTIMENT_COLORS,
            category_orders={"product_display": product_order, "pillar": local_pillar_order, "label": LABEL_ORDER},
            template=template,
            title="Counts of labels per pillar (faceted by product)",
        )
        fig.update_xaxes(title="Count")
    else:
        plot_long = plot_base.melt(
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
            category_orders={"product_display": product_order, "pillar": local_pillar_order, "label": ["positive", "neutral", "negative"]},
            template=template,
            title="% of product total mentions (pos/neu/neg) — sums to 100% per product",
        )
        fig.update_xaxes(title="% of product total mentions")

    fig.update_layout(
        height=min(1800, 260 + 260 * len(product_order)),
        margin=dict(l=10, r=10, t=70, b=10),
        legend_title_text="Label",
    )
    fig.for_each_annotation(lambda a: a.update(text=a.text.replace("product_display=", "Product: ")))
    fig.update_yaxes(title="")
    st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# TAB: Pillar Faceoff
# -----------------------------
with tab_faceoff:
    st.subheader("Pillar faceoff")
    pillar_pick = st.selectbox("Choose pillar", pillars_ordered, index=0)
    p = summary[summary["pillar"].astype(str) == pillar_pick].copy().sort_values("product_display")

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
            st.info("Select a stars column to enable avg stars comparison.")

# -----------------------------
# TAB: Table + Export
# -----------------------------
with tab_table:
    st.subheader("Full Product × Pillar table")

    out = summary[[
        "product_display", "product_full", "pillar",
        "positive_count", "neutral_count", "negative_count", "not_mentioned_count",
        "mentions", "total_reviews",
        "mention_rate", "mention_share_of_product_mentions",
        "net_sentiment", "neg_share_mentions",
        "avg_stars_mentions", "n_star_mentions",
        "opportunity_score",
    ]].copy()

    st.dataframe(out.sort_values(["product_display", "pillar"]), use_container_width=True, height=560)

    st.download_button(
        "Download summary CSV",
        data=out.to_csv(index=False).encode("utf-8"),
        file_name="ax_product_pillar_summary.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Download product name mapping CSV",
        data=pack.name_map.to_csv(index=False).encode("utf-8"),
        file_name="ax_product_name_mapping.csv",
        mime="text/csv",
        use_container_width=True,
    )

# -----------------------------
# TAB: Health Check
# -----------------------------
with tab_health:
    st.subheader("✅ Health Check")

    st.markdown("### Label validity")
    if len(pack.invalid_labels):
        st.warning("Invalid/unknown label values found. They are treated as 'not mentioned' in metrics.")
        st.dataframe(pack.invalid_labels, use_container_width=True, height=320)
    else:
        st.success("All pillar labels are valid: positive / neutral / negative / not mentioned.")

    st.markdown("---")
    st.markdown("### Stars QA")
    if stars_col:
        stars_parsed = parse_stars_series(df_work[stars_col])
        n_total = len(stars_parsed)
        n_ok = int(stars_parsed.notna().sum())
        out_of_range = int(((stars_parsed < 1) | (stars_parsed > 5)).sum(skipna=True))

        c1, c2, c3 = st.columns(3)
        c1.metric("Stars present", f"{n_ok:,} / {n_total:,}")
        c2.metric("Avg stars", f"{stars_parsed.mean():.2f}" if n_ok else "—")
        c3.metric("Out of 1–5 range", f"{out_of_range:,}")

        fig_hist = px.histogram(
            pd.DataFrame({"stars": stars_parsed.dropna()}),
            x="stars",
            nbins=10,
            template=template,
            title="Stars distribution",
        )
        fig_hist.update_layout(height=320, margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("No stars column selected.")

    st.markdown("---")
    st.markdown("### Seeded QA")
    if seeded_col:
        seeded_rate = seeded_count / len(df_base) if len(df_base) else 0
        st.write(f"Seeded in base: **{seeded_count:,}** (**{seeded_rate*100:.1f}%**). Current mode: **{seeded_mode}**.")
    else:
        st.info("No seeded flag column selected.")

    st.markdown("---")
    st.markdown("### Product name mapping")
    st.dataframe(pack.name_map, use_container_width=True, height=320)
