from __future__ import annotations

import ast
import base64
import hashlib
import html as _html
import io
import json
import os
import random
import re
import textwrap
import time
import zlib
from collections import Counter
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

try:
    from ftfy import fix_text as _ftfy_fix  # type: ignore
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None  # type: ignore

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import linear_kernel
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False
    TfidfVectorizer = None  # type: ignore
    linear_kernel = None  # type: ignore

APP_VERSION = "2026-03-18-summit-v22"
STARWALK_SHEET_NAME = "Star Walk scrubbed verbatims"
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}

DEFAULT_STARWALK_COLUMNS: List[str] = [
    "Source",
    "Model (SKU)",
    "Seeded",
    "Country",
    "New Review",
    "Review Date",
    "Verbatim Id",
    "Verbatim",
    "Star Rating",
    "Review count per detractor",
] + [f"Symptom {i}" for i in range(1, 21)]

DEFAULT_EXTRA_AFTER_SYMPTOM20: List[str] = [
    "Key Review Sentiment_Reviews",
    "Key Review Sentiment Type_Reviews",
    "Trigger Point_Product",
    "Dominant Customer Journey Step",
    "L2 Delighter Component",
    "L2 Delighter Mode",
    "L3 Non Product Detractors",
    "Product_Symptom Component",
    "Signal Type",
    "Signal File",
]

SUPPLEMENTAL_TEXT_CANDIDATES = [
    "Verbatim", "Review", "Text", "Transcript", "Message", "Body", "Comment",
    "Content", "Description", "Case Notes", "Conversation", "Post", "Feedback",
]
SUPPLEMENTAL_DATE_CANDIDATES = [
    "Review Date", "Date", "Created Date", "Timestamp", "Opened Timestamp",
    "Created", "Opened", "Published", "Message Date",
]
SUPPLEMENTAL_RATING_CANDIDATES = [
    "Star Rating", "Rating", "CSAT", "Score", "NPS", "Sentiment Score",
]
SUPPLEMENTAL_COUNTRY_CANDIDATES = ["Country", "Market", "Geo", "Region", "Location"]
SUPPLEMENTAL_SOURCE_CANDIDATES = ["Source", "Channel", "Retailer", "Platform", "Site"]
SUPPLEMENTAL_MODEL_CANDIDATES = ["Model (SKU)", "Model", "Product", "SKU", "Product Name"]
SUPPLEMENTAL_ID_CANDIDATES = ["Verbatim Id", "ID", "Record ID", "Ticket ID", "Case ID", "Message ID"]
DEFAULT_TAG_SPLIT_RE = re.compile(r"[;\|\n,]+")
_BAD_CHARS_REGEX = re.compile(r"[ÃÂâï€™]")
PII_PAT = re.compile(r"[\w\.-]+@[\w\.-]+|\+?\d[\d\-\s]{6,}\d")
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.DOTALL | re.IGNORECASE)

_BASIC_STOP = {
    "the", "and", "for", "with", "this", "that", "have", "has", "had", "was", "were", "are",
    "but", "not", "you", "your", "i", "me", "my", "we", "our", "they", "them", "their", "it",
    "its", "a", "an", "to", "of", "in", "on", "at", "as", "is", "be", "been", "so", "if",
    "very", "really", "just", "from", "or", "by", "about", "after", "before", "when", "while",
    "can", "could", "would", "should", "will", "did", "does", "do", "than", "then", "there", "here",
    "also", "too", "more", "most", "less", "much", "many", "one", "two", "three", "into", "over",
    "under", "still", "even", "because", "only", "such", "like", "get", "got", "getting", "gotten",
}

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = None


# -----------------------------------------------------------------------------
# App config + CSS
# -----------------------------------------------------------------------------
st.set_page_config(layout="wide", page_title="Star Walk — Summit Dashboard")

GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; }
  .stApp {
    --bg-card: color-mix(in srgb, var(--background-color, #ffffff) 86%, var(--secondary-background-color, #f5f7fb) 14%);
    --bg-soft: color-mix(in srgb, var(--secondary-background-color, #f5f7fb) 75%, var(--background-color, #ffffff) 25%);
    --border-soft: color-mix(in srgb, var(--text-color, #111827) 12%, transparent);
    --border-strong: color-mix(in srgb, var(--text-color, #111827) 18%, transparent);
    --muted: color-mix(in srgb, var(--text-color, #111827) 72%, transparent);
  }
  .soft-panel {
    background: var(--bg-card);
    border: 1px solid var(--border-soft);
    border-radius: 16px;
    padding: 14px 16px;
    box-shadow: 0 8px 20px rgba(2,6,23,.05);
    margin: 10px 0 16px;
  }
  .metric-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
    margin: 10px 0 16px;
  }
  .metric-card {
    background: var(--bg-card);
    border: 1px solid var(--border-soft);
    border-radius: 16px;
    padding: 16px;
    box-shadow: 0 8px 20px rgba(2,6,23,.05);
  }
  .metric-title { font-size: .9rem; color: var(--muted); font-weight: 700; }
  .metric-value { font-size: 1.95rem; font-weight: 900; line-height: 1.08; margin-top: 4px; }
  .metric-sub { font-size: .86rem; color: var(--muted); margin-top: 6px; }
  .pill-row { display:flex; flex-wrap:wrap; gap:8px; }
  .pill {
    display:inline-flex; align-items:center; gap:8px; padding:6px 10px;
    border-radius:999px; background: var(--bg-soft); border:1px solid var(--border-soft);
    font-size:.86rem; font-weight:700;
  }
  .small-muted { color: var(--muted); font-size: .9rem; }
  .quote-card {
    background: var(--bg-card);
    border-left: 4px solid rgba(59,130,246,.65);
    border-radius: 10px;
    padding: 12px 14px;
    margin: 8px 0;
    border-top: 1px solid var(--border-soft);
    border-right: 1px solid var(--border-soft);
    border-bottom: 1px solid var(--border-soft);
  }
  .action-box {
    background: var(--bg-card);
    border: 1px solid var(--border-soft);
    border-radius: 14px;
    padding: 12px 14px;
    margin-top: 10px;
  }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

PLOTLY_TEMPLATE = "plotly"
PLOTLY_GRIDCOLOR = "rgba(148,163,184,0.24)"


def style_plotly(fig: go.Figure) -> go.Figure:
    try:
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="currentColor"),
            title=dict(font=dict(color="currentColor")),
        )
        fig.update_xaxes(
            gridcolor=PLOTLY_GRIDCOLOR,
            zerolinecolor=PLOTLY_GRIDCOLOR,
            tickfont=dict(color="currentColor"),
            titlefont=dict(color="currentColor"),
        )
        fig.update_yaxes(
            gridcolor=PLOTLY_GRIDCOLOR,
            zerolinecolor=PLOTLY_GRIDCOLOR,
            tickfont=dict(color="currentColor"),
            titlefont=dict(color="currentColor"),
        )
    except Exception:
        pass
    return fig


def model_supports_temperature(model_id: str) -> bool:
    if not model_id:
        return True
    if model_id in NO_TEMP_MODELS:
        return False
    return not model_id.startswith("gpt-5")


# -----------------------------------------------------------------------------
# Small utilities
# -----------------------------------------------------------------------------
def esc(x: Any) -> str:
    return _html.escape("" if pd.isna(x) else str(x))


def clean_text(x: Any, keep_na: bool = False) -> Any:
    if pd.isna(x):
        return pd.NA if keep_na else ""
    s = str(x)
    if s.isascii():
        s = s.strip()
        if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
            return pd.NA if keep_na else ""
        return s
    if _HAS_FTFY:
        try:
            s = _ftfy_fix(s)
        except Exception:
            pass
    if _BAD_CHARS_REGEX.search(s):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                s = repaired
        except Exception:
            pass
    for bad, good in {
        "â€™": "'",
        "â€˜": "‘",
        "â€œ": "“",
        "â€\x9d": "”",
        "â€“": "–",
        "â€”": "—",
        "Â": "",
    }.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return pd.NA if keep_na else ""
    return s


def mask_pii(s: str) -> str:
    try:
        return PII_PAT.sub("[redacted]", s or "")
    except Exception:
        return s or ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def dedupe_preserve(items: Iterable[Any]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def parse_tags(value: Any, split_re: re.Pattern) -> List[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, (list, tuple, set)):
        return dedupe_preserve([str(v).strip() for v in value if str(v).strip()])
    s = str(value).strip()
    if not s:
        return []
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("(") and s.endswith(")")):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple, set)):
                return dedupe_preserve([str(v).strip().strip("'\"") for v in parsed if str(v).strip()])
        except Exception:
            pass
    parts = [p.strip() for p in split_re.split(s) if p and p.strip()]
    return dedupe_preserve(parts)


def clean_seeded_value(v: Any) -> Any:
    if v is None:
        return "NO"
    try:
        if pd.isna(v):
            return "NO"
    except Exception:
        pass
    if isinstance(v, (bool, np.bool_)):
        return "YES" if bool(v) else "NO"
    s = str(v).strip().lower()
    if s in {"1", "1.0", "true", "yes", "y", "seeded"}:
        return "YES"
    if s in {"0", "0.0", "false", "no", "n", "unseeded", "not seeded", "notseeded"}:
        return "NO"
    toks = parse_tags(v, DEFAULT_TAG_SPLIT_RE)
    for tok in toks:
        nt = _norm(tok)
        if nt in {"seeded", "yes", "true", "1"}:
            return "YES"
    return "NO"


def clean_star_rating_value(v: Any) -> Any:
    if v is None:
        return pd.NA
    try:
        if pd.isna(v):
            return pd.NA
    except Exception:
        pass
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        try:
            if np.isnan(v):
                return pd.NA
        except Exception:
            pass
        fv = float(v)
        return int(fv) if fv.is_integer() else fv
    s = str(v).strip()
    if not s:
        return pd.NA
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    if not m:
        return pd.NA
    fv = float(m.group(1))
    return int(fv) if fv.is_integer() else fv


def pct_12(stars: pd.Series) -> float:
    s = pd.to_numeric(stars, errors="coerce").dropna()
    if s.empty:
        return 0.0
    return float((s <= 2).mean() * 100.0)


def section_stats(df_in: pd.DataFrame) -> Tuple[int, float, float]:
    s = pd.to_numeric(df_in.get("Star Rating"), errors="coerce")
    cnt = int(s.notna().sum())
    avg = float(s.mean()) if cnt else float("nan")
    low = pct_12(s) if cnt else float("nan")
    return cnt, avg, low


def series_matches_any(series: pd.Series, selected: List[str], *, delim: str = " | ") -> pd.Series:
    if not selected or ("ALL" in selected) or ("All" in selected):
        return pd.Series(True, index=series.index)
    sel = [str(x) for x in selected if str(x).strip() and str(x) not in {"ALL", "All"}]
    if not sel:
        return pd.Series(True, index=series.index)
    s = series.astype("string").fillna("")
    try:
        has_delim = s.str.contains(re.escape(delim), regex=True, na=False).any()
    except Exception:
        has_delim = False
    if has_delim:
        pat = r"(?:^|%s)(?:%s)(?:%s|$)" % (
            re.escape(delim),
            "|".join(re.escape(x) for x in sel),
            re.escape(delim),
        )
        return s.str.contains(pat, regex=True, case=False, na=False)
    return s.isin(sel)


def is_valid_symptom_value(x: Any) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return False
    return not bool(re.fullmatch(r"[\W_]+", s))


def collect_unique_symptoms(df: pd.DataFrame, cols: List[str]) -> List[str]:
    vals: List[str] = []
    seen: Set[str] = set()
    for c in cols:
        if c not in df.columns:
            continue
        s = df[c].astype("string").str.strip()
        s = s[s.map(is_valid_symptom_value)]
        for v in pd.unique(s.to_numpy()):
            vv = str(v).strip().title()
            if vv and vv not in seen:
                seen.add(vv)
                vals.append(vv)
    return vals


def highlight_html(text: str, keyword: Optional[str]) -> str:
    safe = _html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error:
            pass
    return safe


def metric_card_html(title: str, value: str, sub: str = "") -> str:
    return f"""
    <div class='metric-card'>
      <div class='metric-title'>{esc(title)}</div>
      <div class='metric-value'>{esc(value)}</div>
      <div class='metric-sub'>{esc(sub)}</div>
    </div>
    """


def _infer_product_label(df_in: pd.DataFrame, fallback_filename: str) -> str:
    base = os.path.splitext(fallback_filename or "Uploaded File")[0]
    if "Model (SKU)" in df_in.columns:
        s = df_in["Model (SKU)"].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if not s.empty:
            top = s.value_counts().head(3).index.tolist()
            if len(top) == 1:
                return str(top[0])
            return " / ".join([str(x) for x in top])
    if "Product Name" in df_in.columns:
        s = df_in["Product Name"].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if not s.empty:
            return str(s.value_counts().idxmax())
    return base


def infer_product_profile(df_in: pd.DataFrame, fallback_filename: str) -> Dict[str, Any]:
    prof: Dict[str, Any] = {"product_guess": _infer_product_label(df_in, fallback_filename)}

    def _top_vals(col: str, k: int = 5) -> List[str]:
        if col not in df_in.columns:
            return []
        s = df_in[col].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if s.empty:
            return []
        return [str(x) for x in s.value_counts().head(k).index.tolist()]

    for c in ["Product Name", "Product Category", "Brand", "Company", "Model (SKU)"]:
        vals = _top_vals(c)
        if vals:
            prof[f"top_{c.lower().replace(' ', '_').replace('(', '').replace(')', '')}"] = vals

    if "Verbatim" in df_in.columns:
        uni = Counter()
        bi = Counter()
        for text in df_in["Verbatim"].astype("string").fillna("").head(min(len(df_in), 5000)).tolist():
            t = clean_text(text)
            words = re.findall(r"[a-zA-Z]{3,}", str(t).lower())
            words = [w for w in words if w not in _BASIC_STOP]
            if not words:
                continue
            uni.update(set(words))
            if len(words) >= 2:
                bi.update(set(" ".join(p) for p in zip(words, words[1:])))
        prof["top_keywords"] = [w for w, _ in uni.most_common(15)]
        prof["top_bigrams"] = [w for w, _ in bi.most_common(15)]

    return prof


def symptom_long_df(df_in: pd.DataFrame, symptom_columns: List[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in df_in.columns]
    if not cols:
        return pd.DataFrame(columns=["__idx", "symptom"])
    block = df_in[cols]
    long = block.stack(dropna=False).reset_index()
    long.columns = ["__idx", "__col", "symptom"]
    s = long["symptom"].astype("string").str.strip()
    mask = s.map(is_valid_symptom_value)
    out = long.loc[mask, ["__idx"]].copy()
    out["symptom"] = s[mask].astype("string").str.title()
    return out


def analyze_symptoms_fast(df_in: pd.DataFrame, symptom_columns: List[str]) -> pd.DataFrame:
    long = symptom_long_df(df_in, symptom_columns)
    if long.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total", "Review Mentions", "Review Rate %"])
    counts = long["symptom"].value_counts()
    review_mentions = long.drop_duplicates(subset=["__idx", "symptom"])["symptom"].value_counts()

    avg_map: Dict[str, float] = {}
    if "Star Rating" in df_in.columns:
        tmp = long.drop_duplicates(subset=["__idx", "symptom"]).copy()
        tmp = tmp.join(pd.to_numeric(df_in["Star Rating"], errors="coerce").rename("star"), on="__idx")
        avg_map = tmp.groupby("symptom")["star"].mean().to_dict()

    total_rows = max(1, len(df_in))
    rows = []
    for sym, cnt in counts.items():
        review_cnt = int(review_mentions.get(sym, 0))
        rows.append(
            {
                "Item": str(sym).title(),
                "Avg Star": round(float(avg_map.get(sym)), 2) if sym in avg_map and pd.notna(avg_map.get(sym)) else np.nan,
                "Mentions": int(cnt),
                "% Total": round(cnt / total_rows * 100.0, 1),
                "Review Mentions": review_cnt,
                "Review Rate %": round(review_cnt / total_rows * 100.0, 1),
            }
        )
    return pd.DataFrame(rows).sort_values(["Mentions", "Review Mentions"], ascending=False, ignore_index=True)


def token_presence_counts(texts: Iterable[str], max_reviews: int = 4000) -> Tuple[Counter, int]:
    counter = Counter()
    n = 0
    for t in list(texts)[: int(max_reviews)]:
        n += 1
        t2 = clean_text(t)
        words = re.findall(r"[a-zA-Z]{3,}", str(t2).lower())
        words = [w for w in words if w not in _BASIC_STOP]
        if not words:
            continue
        counter.update(set(words))
        if len(words) >= 2:
            counter.update(set(" ".join(p) for p in zip(words, words[1:])))
    return counter, n


def compute_group_text_diff(df_a: pd.DataFrame, df_b: pd.DataFrame, top_n: int = 15, max_reviews: int = 3000) -> Dict[str, Any]:
    texts_a = df_a.get("Verbatim", pd.Series(dtype="string")).astype("string").fillna("").tolist()
    texts_b = df_b.get("Verbatim", pd.Series(dtype="string")).astype("string").fillna("").tolist()
    c_a, n_a = token_presence_counts(texts_a, max_reviews=max_reviews)
    c_b, n_b = token_presence_counts(texts_b, max_reviews=max_reviews)
    out = {"a_count": n_a, "b_count": n_b, "a_vs_b": [], "b_vs_a": []}
    if n_a == 0 or n_b == 0:
        return out
    terms = set(list(c_a.keys())[:3000]) | set(list(c_b.keys())[:3000])
    diffs = []
    for term in terms:
        ra = c_a.get(term, 0) / max(1, n_a)
        rb = c_b.get(term, 0) / max(1, n_b)
        diffs.append((term, ra - rb, ra, rb))
    diffs.sort(key=lambda x: x[1], reverse=True)
    out["a_vs_b"] = [
        {"term": t, "delta_pp": round(delta * 100.0, 1), "a_rate_pct": round(ra * 100.0, 1), "b_rate_pct": round(rb * 100.0, 1)}
        for t, delta, ra, rb in diffs[: int(top_n)] if delta > 0
    ]
    diffs.sort(key=lambda x: x[1])
    out["b_vs_a"] = [
        {"term": t, "delta_pp": round((-delta) * 100.0, 1), "b_rate_pct": round(rb * 100.0, 1), "a_rate_pct": round(ra * 100.0, 1)}
        for t, delta, ra, rb in diffs[: int(top_n)] if delta < 0
    ]
    return out


def compute_text_theme_diffs(df_in: pd.DataFrame, max_reviews: int = 4000, top_n: int = 15) -> Dict[str, Any]:
    if df_in is None or df_in.empty or "Verbatim" not in df_in.columns or "Star Rating" not in df_in.columns:
        return {"n_low_reviews": 0, "n_high_reviews": 0, "low_vs_high": [], "high_vs_low": [], "low_terms": [], "high_terms": []}
    d = df_in[["Verbatim", "Star Rating"]].copy()
    d["star"] = pd.to_numeric(d["Star Rating"], errors="coerce")
    d = d.dropna(subset=["star"])
    low = d.loc[d["star"] <= 2]
    high = d.loc[d["star"] >= 4]
    diff = compute_group_text_diff(low, high, top_n=top_n, max_reviews=max_reviews)
    low_c, low_n = token_presence_counts(low["Verbatim"].astype("string").fillna("").tolist(), max_reviews=max_reviews)
    high_c, high_n = token_presence_counts(high["Verbatim"].astype("string").fillna("").tolist(), max_reviews=max_reviews)
    return {
        "n_low_reviews": low_n,
        "n_high_reviews": high_n,
        "low_terms": [{"term": t, "reviews": int(c), "rate_pct": round(c / max(1, low_n) * 100.0, 1)} for t, c in low_c.most_common(top_n)],
        "high_terms": [{"term": t, "reviews": int(c), "rate_pct": round(c / max(1, high_n) * 100.0, 1)} for t, c in high_c.most_common(top_n)],
        "low_vs_high": diff.get("a_vs_b", []),
        "high_vs_low": diff.get("b_vs_a", []),
    }


def _extract_sentence(text: str, keyword: Optional[str] = None, prefer_tail: bool = False) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    parts = re.split(r"(?<=[.!?\n])\s+", t)
    if keyword:
        k = keyword.lower()
        for p in parts:
            if k in p.lower():
                return p.strip()
    if parts:
        return (parts[-1] if prefer_tail else parts[0]).strip()
    return t[:260]


def pick_quotes_for_symptom(df_in: pd.DataFrame, symptom: str, cols: List[str], k: int = 3, prefer: str = "low") -> List[Dict[str, str]]:
    if not symptom or not cols or "Star Rating" not in df_in.columns:
        return []
    sym = str(symptom).strip().lower()
    mask = pd.Series(False, index=df_in.index)
    for c in cols:
        if c not in df_in.columns:
            continue
        s = df_in[c].astype("string").fillna("").str.strip().str.lower()
        mask |= s.eq(sym)
    sub = df_in.loc[mask].copy()
    if sub.empty:
        return []
    sub["Star Rating"] = pd.to_numeric(sub["Star Rating"], errors="coerce")
    sub = sub.dropna(subset=["Star Rating"])
    if sub.empty:
        return []
    sub = sub.sort_values("Star Rating", ascending=(prefer == "low"))
    out = []
    for _, row in sub.head(k).iterrows():
        txt = mask_pii(clean_text(row.get("Verbatim", "")))
        sent = _extract_sentence(txt, keyword=symptom, prefer_tail=(prefer == "low"))
        if len(sent) > 280:
            sent = sent[:277] + "…"
        meta = []
        try:
            meta.append(f"{int(float(row.get('Star Rating')))}★")
        except Exception:
            pass
        for col in ["Source", "Country", "Model (SKU)", "Signal Type"]:
            if col in row.index:
                v = row.get(col, pd.NA)
                if pd.notna(v) and str(v).strip():
                    meta.append(str(v).strip())
        if "Review Date" in row.index:
            v = row.get("Review Date", pd.NaT)
            if pd.notna(v):
                try:
                    meta.append(pd.to_datetime(v).strftime("%Y-%m-%d"))
                except Exception:
                    pass
        out.append({"text": sent, "meta": " • ".join(meta) if meta else ""})
    return out

# -----------------------------------------------------------------------------
# JSON parsing + conversion helpers
# -----------------------------------------------------------------------------
def safe_get(d: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = d
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def join_list(x: Any, sep: str = " | ") -> Any:
    if x is None:
        return None
    if isinstance(x, dict):
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    if isinstance(x, list):
        vals = [str(v).strip() for v in x if v is not None and str(v).strip()]
        return sep.join(vals) if vals else None
    return x


def parse_iso_date(x: Any) -> Optional[date]:
    if not x:
        return None
    ts = pd.to_datetime(x, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _collect_attribute_keys(records: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str], List[str]]:
    def _clean_keys(keys: Iterable[Any]) -> List[str]:
        out = []
        for k in keys:
            s = str(k).strip()
            if not s or s.lower() in {"unnamed: 0"}:
                continue
            out.append(s)
        return sorted(list(dict.fromkeys(out)))

    client: Set[str] = set()
    custom: Set[str] = set()
    tax: Set[str] = set()
    ax: Set[str] = set()

    for r in records:
        if not isinstance(r, dict):
            continue
        ca = r.get("clientAttributes")
        if isinstance(ca, dict):
            client.update([str(k) for k in ca.keys()])
        cu = r.get("customAttributes")
        if isinstance(cu, dict):
            for k, v in cu.items():
                if str(k) == "taxonomies" and isinstance(v, dict):
                    tax.update([str(tk) for tk in v.keys()])
                else:
                    custom.add(str(k))
        aa = r.get("axionAttributes")
        if isinstance(aa, dict):
            ax.update([str(k) for k in aa.keys()])

    return _clean_keys(client), _clean_keys(custom), _clean_keys(tax), _clean_keys(ax)


def build_reviews_df(records: List[Dict[str, Any]], include_extra: bool = True) -> pd.DataFrame:
    client_keys: List[str] = []
    custom_keys: List[str] = []
    tax_keys: List[str] = []
    ax_keys: List[str] = []
    if include_extra:
        client_keys, custom_keys, tax_keys, ax_keys = _collect_attribute_keys(records)

    rows: List[Dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        row: Dict[str, Any] = {
            "Record ID": r.get("_id"),
            "Opened Timestamp": parse_iso_date(r.get("openedTimestamp")),
            "Rating (num)": safe_get(r, ["clientAttributes", "Rating (num)"]),
            "Retailer": safe_get(r, ["clientAttributes", "Retailer"]),
            "Model": safe_get(r, ["clientAttributes", "Model"]),
            "Seeded Reviews": safe_get(r, ["clientAttributes", "Seeded Reviews"]),
            "Syndicated/Seeded Reviews": safe_get(r, ["clientAttributes", "Syndicated/Seeded Reviews"]),
            "Location": safe_get(r, ["clientAttributes", "Location"]),
            "Title": safe_get(r, ["freeText", "Title"]),
            "Review": safe_get(r, ["freeText", "Review"]),
        }
        if include_extra:
            ca = r.get("clientAttributes") if isinstance(r.get("clientAttributes"), dict) else {}
            cu = r.get("customAttributes") if isinstance(r.get("customAttributes"), dict) else {}
            tax = cu.get("taxonomies") if isinstance(cu.get("taxonomies"), dict) else {}
            aa = r.get("axionAttributes") if isinstance(r.get("axionAttributes"), dict) else {}
            for k in client_keys:
                row[k] = join_list(ca.get(k))
            for k in custom_keys:
                row[k] = join_list(cu.get(k))
            for k in tax_keys:
                row[k] = join_list(tax.get(k))
            for k in ax_keys:
                row[k] = join_list(aa.get(k))
            if "eventType" in r:
                row["eventType"] = r.get("eventType")
            if "eventId" in r:
                row["eventId"] = r.get("eventId")
        rows.append(row)
    df = pd.DataFrame(rows)
    if "Rating (num)" in df.columns:
        df["Rating (num)"] = pd.to_numeric(df["Rating (num)"], errors="coerce")
    return df


def _strip_code_fences(s: str) -> str:
    s0 = "" if s is None else str(s)
    m = _CODE_FENCE_RE.match(s0)
    if m:
        return (m.group(1) or "").strip()
    return s0.strip()


def _extract_json_substring(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        return s
    start = min(starts)
    sub = s[start:]
    stack: List[str] = []
    in_str = False
    quote = ""
    escape = False
    for i, ch in enumerate(sub):
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch in "[{":
            stack.append(ch)
            continue
        if ch in "]}":
            if not stack:
                continue
            opener = stack.pop()
            if (opener == "{" and ch != "}") or (opener == "[" and ch != "]"):
                stack = []
                continue
            if not stack:
                return sub[: i + 1].strip()
    return sub.strip()


def _try_parse_json_lines(s: str) -> Optional[List[Dict[str, Any]]]:
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    objs: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                objs.append(obj)
            else:
                return None
        except Exception:
            return None
    return objs


def _convert_curly_delimited_strings_to_json(s: str) -> str:
    out: List[str] = []
    in_str = False
    delim = "standard"
    escape = False
    for ch in s:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if delim == "standard":
                if ch == '"':
                    out.append('"')
                    in_str = False
                    delim = "standard"
                    continue
                out.append(ch)
                continue
            if ch == "”":
                out.append('"')
                in_str = False
                delim = "standard"
                continue
            if ch == '"':
                out.append('\\"')
                continue
            out.append(ch)
            continue
        if ch == '"':
            out.append('"')
            in_str = True
            delim = "standard"
            continue
        if ch in ("“", "”"):
            out.append('"')
            in_str = True
            delim = "curly"
            continue
        out.append(ch)
    return "".join(out)


def loads_flexible_json(text_in: str) -> Tuple[Any, List[str]]:
    warnings: List[str] = []
    if text_in is None:
        raise ValueError("No JSON provided.")
    s = str(text_in)
    s = s.replace("\ufeff", "").replace("\u200b", "").replace("\u2060", "").replace("\xa0", " ")
    s = _strip_code_fences(s)
    s = _extract_json_substring(s).strip()
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
    try:
        return json.loads(s), warnings
    except Exception as e1:
        last_err: Exception = e1

    s2 = re.sub(r",\s*([}\]])", r"\1", s)
    if s2 != s:
        try:
            warnings.append("Removed trailing commas to make JSON valid.")
            return json.loads(s2), warnings
        except Exception as e2:
            last_err = e2
            warnings = [w for w in warnings if "trailing commas" not in w.lower()]

    jl = _try_parse_json_lines(s)
    if jl is not None:
        warnings.append("Detected JSON Lines and parsed each line as a record list.")
        return jl, warnings

    dec = json.JSONDecoder()
    for start in [s.find("{"), s.find("[")]:
        if start == -1:
            continue
        try:
            obj, end = dec.raw_decode(s[start:])
            tail = s[start + end :].strip()
            if tail:
                warnings.append("Ignored trailing text after JSON payload.")
            return obj, warnings
        except Exception as e3:
            last_err = e3

    if ("“" in s) or ("”" in s):
        s3 = _convert_curly_delimited_strings_to_json(s)
        if s3 != s:
            try:
                obj = json.loads(s3)
                warnings.append("Repaired curly-quote delimited strings.")
                return obj, warnings
            except Exception as e4:
                last_err = e4

    ss = s.strip()
    if ss.startswith("{") and re.search(r"\}\s*,\s*\{", ss):
        candidate = re.sub(r",\s*$", "", ss)
        try:
            arr = json.loads("[" + candidate + "]")
            if isinstance(arr, list):
                warnings.append("Wrapped comma-separated JSON objects into an array.")
                return arr, warnings
        except Exception as e5:
            last_err = e5

    try:
        obj = ast.literal_eval(ss)
        if isinstance(obj, (dict, list)):
            warnings.append("Parsed as a Python literal (best-effort).")
            return obj, warnings
    except Exception as e6:
        last_err = e6

    if (ss.startswith('"') and ss.endswith('"')) or (ss.startswith("'") and ss.endswith("'")):
        try:
            inner = ss[1:-1]
            return json.loads(inner), warnings + ["Removed outer quotes around pasted JSON text."]
        except Exception as e7:
            last_err = e7

    hint = ""
    if isinstance(last_err, json.JSONDecodeError):
        hint = f" Details: line {last_err.lineno}, col {last_err.colno}: {last_err.msg}"
    raise ValueError(
        "Could not parse input as JSON. Paste valid JSON (object/array) or JSON Lines."
        + hint
    ) from last_err


def extract_records(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        return [r for r in raw.get("results") if isinstance(r, dict)]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        if ("freeText" in raw) and ("clientAttributes" in raw or "customAttributes" in raw):
            return [raw]

    def _search(obj: Any, depth: int = 0) -> Optional[List[Dict[str, Any]]]:
        if depth > 6:
            return None
        if isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj[: min(len(obj), 5)]):
                return [x for x in obj if isinstance(x, dict)]
            for x in obj:
                found = _search(x, depth + 1)
                if found is not None:
                    return found
        elif isinstance(obj, dict):
            for _, v in obj.items():
                found = _search(v, depth + 1)
                if found is not None:
                    return found
        return None

    found = _search(raw)
    if found is not None:
        return found
    raise ValueError(
        "Unrecognized JSON shape. Expected a dict with `results: []`, a list of records, or a wrapper containing a list of records."
    )


def insert_after_symptom20(out_cols: List[str], extra_cols: List[str]) -> List[str]:
    base = list(out_cols)
    extras = [c for c in extra_cols if c and str(c).strip()]
    if not extras:
        return base
    base_no_extras = [c for c in base if c not in extras]
    try:
        idx = base_no_extras.index("Symptom 20")
        return base_no_extras[: idx + 1] + extras + base_no_extras[idx + 1 :]
    except ValueError:
        return base_no_extras + extras


def collect_from_row_tuple(row_tup: tuple, col_idx: Dict[str, int], cols: List[str], split_re: re.Pattern) -> List[str]:
    tags: List[str] = []
    for c in cols:
        i = col_idx.get(c)
        if i is None:
            continue
        tags.extend(parse_tags(row_tup[i], split_re))
    return dedupe_preserve(tags)


def convert_to_starwalk_from_reviews_df(
    reviews_df: pd.DataFrame,
    include_extra_cols_after_symptom20: bool = True,
    weight_mode: str = "Leave blank",
    split_regex: str = r"[;\|\n,]+",
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    src_df = reviews_df.reset_index(drop=True)
    out_cols = list(DEFAULT_STARWALK_COLUMNS)
    extra_cols_dynamic: List[str] = []
    extra_cols_final: List[str] = []

    if include_extra_cols_after_symptom20:
        base_extras = [c for c in DEFAULT_EXTRA_AFTER_SYMPTOM20 if c in src_df.columns]
        _used_inputs = {
            "Retailer", "Model", "Seeded Reviews", "Syndicated/Seeded Reviews", "Location",
            "Opened Timestamp", "Record ID", "Review", "Rating (num)",
        }
        for c in list(src_df.columns):
            sc = str(c).strip()
            if not sc or sc.lower() in {"unnamed: 0"}:
                continue
            if sc in _used_inputs or sc in base_extras or sc in out_cols or sc.startswith("Symptom "):
                continue
            try:
                if src_df[sc].isna().all():
                    continue
            except Exception:
                pass
            extra_cols_dynamic.append(sc)
        extra_cols_dynamic = sorted(list(dict.fromkeys(extra_cols_dynamic)))
        extra_cols_final = base_extras + [c for c in extra_cols_dynamic if c not in base_extras]
        out_cols = insert_after_symptom20(out_cols, extra_cols_final)

    field_map = {
        "Source": "Retailer",
        "Model (SKU)": "Model",
        "Seeded": "Seeded Reviews",
        "Country": "Location",
        "New Review": "Syndicated/Seeded Reviews",
        "Review Date": "Opened Timestamp",
        "Verbatim Id": "Record ID",
        "Verbatim": "Review",
        "Star Rating": "Rating (num)",
        **{c: c for c in extra_cols_final if c in src_df.columns},
    }

    l2_det_cols = [c for c in ["Product_Symptom Conditions", "Product Symptom Conditions"] if c in src_df.columns]
    l2_del_cols = [c for c in ["L2 Delighter Condition", "L2 Delighter Conditions"] if c in src_df.columns]
    if not l2_det_cols:
        l2_det_cols = [c for c in src_df.columns if "productsymptom" in _norm(c) and "condition" in _norm(c)][:1]
    if not l2_del_cols:
        l2_del_cols = [c for c in src_df.columns if "delighter" in _norm(c) and "condition" in _norm(c)][:1]

    split_re = re.compile(split_regex)
    src_cols = list(src_df.columns)
    col_idx = {c: i for i, c in enumerate(src_cols)}
    n = len(src_df)
    base_cols = list(out_cols)
    out = pd.DataFrame(index=range(n), columns=base_cols, dtype="object")

    for out_field, in_field in field_map.items():
        if out_field not in out.columns:
            continue
        if in_field and in_field in src_df.columns:
            series = src_df[in_field]
            if out_field == "Seeded":
                out[out_field] = series.apply(clean_seeded_value).astype("object")
            elif out_field == "Star Rating":
                out[out_field] = series.apply(clean_star_rating_value).astype("object")
            else:
                out[out_field] = series.values
        else:
            out[out_field] = pd.NA

    detr_matrix = [[pd.NA] * 10 for _ in range(n)]
    deli_matrix = [[pd.NA] * 10 for _ in range(n)]
    detr_count = np.zeros(n, dtype=int)
    deli_count = np.zeros(n, dtype=int)
    rows_trunc_det = 0
    rows_trunc_del = 0

    for r_i, row in enumerate(src_df.itertuples(index=False, name=None)):
        d_tags = collect_from_row_tuple(row, col_idx, l2_det_cols, split_re)
        l_tags = collect_from_row_tuple(row, col_idx, l2_del_cols, split_re)
        if len(d_tags) > 10:
            rows_trunc_det += 1
        if len(l_tags) > 10:
            rows_trunc_del += 1
        d_tags = d_tags[:10]
        l_tags = l_tags[:10]
        detr_count[r_i] = len(d_tags)
        deli_count[r_i] = len(l_tags)
        for j, t in enumerate(d_tags):
            detr_matrix[r_i][j] = t
        for j, t in enumerate(l_tags):
            deli_matrix[r_i][j] = t

    out[[f"Symptom {i}" for i in range(1, 11)]] = pd.DataFrame(detr_matrix, columns=[f"Symptom {i}" for i in range(1, 11)]).values
    out[[f"Symptom {i}" for i in range(11, 21)]] = pd.DataFrame(deli_matrix, columns=[f"Symptom {i}" for i in range(11, 21)]).values

    if "Review count per detractor" in out.columns:
        if weight_mode == "Leave blank":
            out["Review count per detractor"] = pd.NA
        elif weight_mode == "Always 1":
            out["Review count per detractor"] = 1.0
        elif weight_mode == "1 / # detractor symptoms (if any)":
            out["Review count per detractor"] = np.where(detr_count > 0, 1.0 / detr_count, pd.NA)
        elif weight_mode == "1 / # delighter symptoms (if any)":
            out["Review count per detractor"] = np.where(deli_count > 0, 1.0 / deli_count, pd.NA)
        else:
            total = detr_count + deli_count
            out["Review count per detractor"] = np.where(total > 0, 1.0 / total, pd.NA)

    stats = {
        "rows": n,
        "rows_truncated_detractors_gt10": rows_trunc_det,
        "rows_truncated_delighters_gt10": rows_trunc_del,
    }
    return out, stats


@st.cache_data(show_spinner=False)
def _load_starwalk_table(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    try:
        if file_name.lower().endswith(".csv"):
            df_local = pd.read_csv(BytesIO(file_bytes))
        else:
            bio = BytesIO(file_bytes)
            try:
                df_local = pd.read_excel(bio, sheet_name=STARWALK_SHEET_NAME)
            except ValueError:
                bio2 = BytesIO(file_bytes)
                xls = pd.ExcelFile(bio2)
                candidate = None
                for sh in xls.sheet_names:
                    try:
                        sample = pd.read_excel(xls, sheet_name=sh, nrows=1)
                        cols = [str(c).strip().lower() for c in sample.columns]
                        if any(c == "verbatim" or c.startswith("verbatim") for c in cols):
                            candidate = sh
                            break
                    except Exception:
                        continue
                if candidate:
                    df_local = pd.read_excel(xls, sheet_name=candidate)
                else:
                    df_local = pd.read_excel(BytesIO(file_bytes))
    except Exception as e:
        raise RuntimeError(f"Could not read file: {e}")

    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review", "Signal Type"]:
        if col in df_local.columns:
            df_local[col] = df_local[col].astype("string").str.upper()
    if "Star Rating" in df_local.columns:
        df_local["Star Rating"] = pd.to_numeric(df_local["Star Rating"], errors="coerce")
    if "Review Date" in df_local.columns:
        df_local["Review Date"] = pd.to_datetime(df_local["Review Date"], errors="coerce")
    for c in [col for col in df_local.columns if str(col).startswith("Symptom")]:
        df_local[c] = df_local[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")
    if "Verbatim" in df_local.columns:
        df_local["Verbatim"] = df_local["Verbatim"].astype("string").map(clean_text)
    return df_local


@st.cache_data(show_spinner=False)
def _json_text_to_starwalk(json_text: str, source_name: str, include_extra_cols: bool = True) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    raw_obj, warnings = loads_flexible_json(json_text)
    records = extract_records(raw_obj)
    if not records:
        raise ValueError("Parsed input, but found 0 record objects to convert.")
    reviews_df = build_reviews_df(records, include_extra=include_extra_cols)
    out_df, stats = convert_to_starwalk_from_reviews_df(
        reviews_df,
        include_extra_cols_after_symptom20=include_extra_cols,
        weight_mode="Leave blank",
    )
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review", "Signal Type"]:
        if col in out_df.columns:
            out_df[col] = out_df[col].astype("string").str.upper()
    if "Star Rating" in out_df.columns:
        out_df["Star Rating"] = pd.to_numeric(out_df["Star Rating"], errors="coerce")
    if "Review Date" in out_df.columns:
        out_df["Review Date"] = pd.to_datetime(out_df["Review Date"], errors="coerce")
    for c in [col for col in out_df.columns if str(col).startswith("Symptom")]:
        out_df[c] = out_df[c].astype("string").map(lambda v: clean_text(v, keep_na=True)).astype("string")
    if "Verbatim" in out_df.columns:
        out_df["Verbatim"] = out_df["Verbatim"].astype("string").map(clean_text)
    meta = {"source": source_name, "warnings": warnings, "stats": stats, "records": len(records)}
    return out_df, meta


@st.cache_data(show_spinner=False)
def _load_supplemental_signal_file(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    try:
        if file_name.lower().endswith(".csv"):
            raw = pd.read_csv(BytesIO(file_bytes))
        else:
            raw = pd.read_excel(BytesIO(file_bytes))
    except Exception as e:
        raise RuntimeError(f"Could not read supplemental file {file_name}: {e}")
    if raw.empty:
        raise ValueError(f"Supplemental file {file_name} is empty.")

    norm_map = {_norm(c): c for c in raw.columns}

    def _pick(candidates: List[str]) -> Optional[str]:
        for cand in candidates:
            if cand in raw.columns:
                return cand
        for cand in candidates:
            nc = _norm(cand)
            if nc in norm_map:
                return norm_map[nc]
        return None

    text_col = _pick(SUPPLEMENTAL_TEXT_CANDIDATES)
    if text_col is None:
        for c in raw.columns:
            s = raw[c].astype("string")
            if s.dropna().astype(str).str.len().median() > 25:
                text_col = c
                break
    if text_col is None:
        raise ValueError(
            f"Could not find a text column in {file_name}. Expected one of: {', '.join(SUPPLEMENTAL_TEXT_CANDIDATES)}"
        )

    date_col = _pick(SUPPLEMENTAL_DATE_CANDIDATES)
    rating_col = _pick(SUPPLEMENTAL_RATING_CANDIDATES)
    country_col = _pick(SUPPLEMENTAL_COUNTRY_CANDIDATES)
    source_col = _pick(SUPPLEMENTAL_SOURCE_CANDIDATES)
    model_col = _pick(SUPPLEMENTAL_MODEL_CANDIDATES)
    id_col = _pick(SUPPLEMENTAL_ID_CANDIDATES)

    out = pd.DataFrame(index=raw.index)
    out["Verbatim"] = raw[text_col].astype("string").map(clean_text)
    out["Verbatim Id"] = raw[id_col].astype("string") if id_col else [f"{Path(file_name).stem}-{i+1}" for i in range(len(raw))]
    out["Review Date"] = pd.to_datetime(raw[date_col], errors="coerce") if date_col else pd.NaT
    out["Star Rating"] = pd.to_numeric(raw[rating_col], errors="coerce") if rating_col else pd.NA
    out["Country"] = raw[country_col].astype("string") if country_col else pd.NA
    out["Source"] = raw[source_col].astype("string") if source_col else Path(file_name).stem.upper()
    out["Model (SKU)"] = raw[model_col].astype("string") if model_col else pd.NA
    out["Seeded"] = "NO"
    out["New Review"] = "NO"
    out["Review count per detractor"] = pd.NA
    out["Signal Type"] = Path(file_name).stem.upper()
    out["Signal File"] = file_name
    for i in range(1, 21):
        out[f"Symptom {i}"] = pd.NA

    mapped = {text_col, date_col, rating_col, country_col, source_col, model_col, id_col}
    mapped = {m for m in mapped if m is not None}
    for c in raw.columns:
        if c in mapped or c in out.columns:
            continue
        out[c] = raw[c]

    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review", "Signal Type"]:
        if col in out.columns:
            out[col] = out[col].astype("string").str.upper()
    return out


def append_supplemental_signals(base_df: pd.DataFrame, supp_dfs: List[pd.DataFrame]) -> pd.DataFrame:
    if not supp_dfs:
        return base_df
    frames = [base_df] + supp_dfs
    all_cols = []
    seen: Set[str] = set()
    for df in frames:
        for c in df.columns:
            if c not in seen:
                seen.add(c)
                all_cols.append(c)
    aligned = []
    for df in frames:
        temp = df.copy()
        for c in all_cols:
            if c not in temp.columns:
                temp[c] = pd.NA
        aligned.append(temp[all_cols])
    out = pd.concat(aligned, ignore_index=True)
    if "Review Date" in out.columns:
        out["Review Date"] = pd.to_datetime(out["Review Date"], errors="coerce")
    if "Star Rating" in out.columns:
        out["Star Rating"] = pd.to_numeric(out["Star Rating"], errors="coerce")
    if "Verbatim" in out.columns:
        out["Verbatim"] = out["Verbatim"].astype("string").map(clean_text)
    return out

# -----------------------------------------------------------------------------
# Preset helpers
# -----------------------------------------------------------------------------
PRESET_SCHEMA_VERSION = 2


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def collect_filter_state(additional_columns: List[str]) -> Dict[str, Any]:
    state = {"schema_version": PRESET_SCHEMA_VERSION, "created_at": _now_iso(), "filters": {}, "ui": {}}
    state["filters"]["tf"] = st.session_state.get("tf", "All Time")
    state["filters"]["tf_range"] = st.session_state.get("tf_range", None)
    state["filters"]["sr"] = st.session_state.get("sr", ["All"])
    state["filters"]["kw"] = st.session_state.get("kw", "")
    state["filters"]["delight"] = st.session_state.get("delight", ["All"])
    state["filters"]["detract"] = st.session_state.get("detract", ["All"])
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + list(additional_columns):
        for suffix in ["", "_range", "_contains"]:
            key = f"f_{col}{suffix}"
            if key in st.session_state:
                state["filters"][key] = st.session_state.get(key)
    state["ui"]["extra_filter_cols"] = st.session_state.get("extra_filter_cols", [])
    state["ui"]["rpp"] = st.session_state.get("rpp", 10)
    return state


def apply_filter_state(state: Dict[str, Any], additional_columns: List[str]) -> None:
    filters = state.get("filters", {})
    for key in ["tf", "tf_range", "sr", "kw", "delight", "detract"]:
        if key in filters:
            st.session_state[key] = filters.get(key)
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + list(additional_columns):
        for suffix in ["", "_range", "_contains"]:
            key = f"f_{col}{suffix}"
            if key in filters:
                st.session_state[key] = filters.get(key)
    ui = state.get("ui", {})
    st.session_state["extra_filter_cols"] = ui.get("extra_filter_cols", [])
    st.session_state["rpp"] = int(ui.get("rpp", 10) or 10)
    st.session_state["review_page"] = 0


# -----------------------------------------------------------------------------
# Radar, compare, root cause, simulation, actions
# -----------------------------------------------------------------------------
def detect_issue_radar(
    df_in: pd.DataFrame,
    detractor_cols: List[str],
    delighter_cols: List[str],
    lookback_days: int = 30,
    min_mentions: int = 3,
) -> pd.DataFrame:
    if df_in.empty or "Review Date" not in df_in.columns:
        return pd.DataFrame(columns=["Type", "Issue", "Narrative", "Severity", "Current", "Previous", "Delta pp", "Window"])
    d = df_in.copy()
    d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
    d = d.dropna(subset=["Review Date"])
    if d.empty:
        return pd.DataFrame(columns=["Type", "Issue", "Narrative", "Severity", "Current", "Previous", "Delta pp", "Window"])

    last_day = d["Review Date"].max().normalize()
    current_start = last_day - pd.Timedelta(days=int(lookback_days) - 1)
    prev_end = current_start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=int(lookback_days) - 1)
    cur = d[(d["Review Date"] >= current_start) & (d["Review Date"] <= last_day)]
    prev = d[(d["Review Date"] >= prev_start) & (d["Review Date"] <= prev_end)]
    rows: List[Dict[str, Any]] = []
    if cur.empty:
        return pd.DataFrame(columns=["Type", "Issue", "Narrative", "Severity", "Current", "Previous", "Delta pp", "Window"])

    if "Star Rating" in cur.columns:
        cur_avg = pd.to_numeric(cur["Star Rating"], errors="coerce").mean()
        prev_avg = pd.to_numeric(prev["Star Rating"], errors="coerce").mean() if not prev.empty else np.nan
        if pd.notna(cur_avg) and pd.notna(prev_avg):
            drop = float(cur_avg - prev_avg)
            if drop <= -0.20:
                rows.append(
                    {
                        "Type": "Rating drop",
                        "Issue": "Overall Avg ★",
                        "Narrative": f"Avg ★ moved from {prev_avg:.2f} to {cur_avg:.2f} in the last {lookback_days} days.",
                        "Severity": round(abs(drop) * 100, 1),
                        "Current": round(cur_avg, 2),
                        "Previous": round(prev_avg, 2),
                        "Delta pp": round(drop, 2),
                        "Window": f"{current_start.date()} → {last_day.date()}",
                    }
                )
        cur_low = pct_12(cur["Star Rating"]) if "Star Rating" in cur.columns else np.nan
        prev_low = pct_12(prev["Star Rating"]) if (not prev.empty and "Star Rating" in prev.columns) else np.nan
        if pd.notna(cur_low) and pd.notna(prev_low):
            delta_low = float(cur_low - prev_low)
            if delta_low >= 4.0:
                rows.append(
                    {
                        "Type": "Low-star spike",
                        "Issue": "1–2★ reviews",
                        "Narrative": f"Low-star share increased from {prev_low:.1f}% to {cur_low:.1f}% in the last {lookback_days} days.",
                        "Severity": round(delta_low * 3.0, 1),
                        "Current": round(cur_low, 1),
                        "Previous": round(prev_low, 1),
                        "Delta pp": round(delta_low, 1),
                        "Window": f"{current_start.date()} → {last_day.date()}",
                    }
                )

    all_sym_cols = detractor_cols + delighter_cols
    long_cur = symptom_long_df(cur, all_sym_cols)
    long_prev = symptom_long_df(prev, all_sym_cols)
    cur_counts = long_cur["symptom"].value_counts()
    prev_counts = long_prev["symptom"].value_counts()
    detractor_set = {s.title() for s in collect_unique_symptoms(df_in, detractor_cols)}
    denom_cur = max(1, len(cur))
    denom_prev = max(1, len(prev))
    candidate_terms = set(cur_counts.head(40).index.tolist()) | set(prev_counts.head(20).index.tolist())
    for term in candidate_terms:
        c = int(cur_counts.get(term, 0))
        p = int(prev_counts.get(term, 0))
        if c < min_mentions:
            continue
        rate_c = c / denom_cur * 100.0
        rate_p = p / denom_prev * 100.0 if denom_prev else 0.0
        delta_pp = rate_c - rate_p
        ratio = (c + 1) / (p + 1)
        if p == 0 and c >= min_mentions:
            severity = c * 6.0
            kind = "New theme"
            narrative = f"{term} appeared {c} times in the latest window after 0 mentions in the previous window."
        elif delta_pp >= 2.0 or ratio >= 1.8:
            severity = c * max(delta_pp, 1.0)
            kind = "Detractor spike" if term in detractor_set else "Theme spike"
            narrative = f"{term} moved from {rate_p:.1f}% to {rate_c:.1f}% of reviews ({p} → {c} mentions)."
        else:
            continue
        rows.append(
            {
                "Type": kind,
                "Issue": term,
                "Narrative": narrative,
                "Severity": round(severity, 1),
                "Current": round(rate_c, 1),
                "Previous": round(rate_p, 1),
                "Delta pp": round(delta_pp, 1),
                "Window": f"{current_start.date()} → {last_day.date()}",
            }
        )

    if {"Country", "Source", "Star Rating"}.issubset(d.columns):
        cur2 = cur.copy()
        cur2["Star Rating"] = pd.to_numeric(cur2["Star Rating"], errors="coerce")
        grp = cur2.groupby(["Country", "Source"])["Star Rating"].agg(["count", "mean"]).reset_index()
        grp = grp[(grp["count"] >= 5) & (grp["mean"] <= max(2.9, pd.to_numeric(cur2["Star Rating"], errors="coerce").mean() - 0.4))]
        for _, row in grp.sort_values(["mean", "count"]).head(8).iterrows():
            rows.append(
                {
                    "Type": "Pocket underperformance",
                    "Issue": f"{row['Country']} × {row['Source']}",
                    "Narrative": f"Recent Avg ★ is {row['mean']:.2f} across {int(row['count'])} reviews.",
                    "Severity": round((5.0 - float(row["mean"])) * float(row["count"]), 1),
                    "Current": round(float(row["mean"]), 2),
                    "Previous": np.nan,
                    "Delta pp": np.nan,
                    "Window": f"{current_start.date()} → {last_day.date()}",
                }
            )

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["Type", "Issue", "Narrative", "Severity", "Current", "Previous", "Delta pp", "Window"])
    out = out.sort_values("Severity", ascending=False, ignore_index=True)
    return out.head(20)


def build_symptom_delta_table(df_a: pd.DataFrame, df_b: pd.DataFrame, symptom_cols: List[str], label_a: str, label_b: str) -> pd.DataFrame:
    ta = analyze_symptoms_fast(df_a, symptom_cols)
    tb = analyze_symptoms_fast(df_b, symptom_cols)
    if ta.empty and tb.empty:
        return pd.DataFrame(columns=["Item", f"{label_a} Rate %", f"{label_b} Rate %", "Delta pp", "Direction"])
    ma = ta[["Item", "Review Mentions", "Review Rate %", "Avg Star"]].rename(
        columns={"Review Mentions": f"{label_a} Reviews", "Review Rate %": f"{label_a} Rate %", "Avg Star": f"{label_a} Avg ★"}
    )
    mb = tb[["Item", "Review Mentions", "Review Rate %", "Avg Star"]].rename(
        columns={"Review Mentions": f"{label_b} Reviews", "Review Rate %": f"{label_b} Rate %", "Avg Star": f"{label_b} Avg ★"}
    )
    merged = ma.merge(mb, on="Item", how="outer")
    for col in merged.columns:
        if col != "Item":
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    merged["Delta pp"] = merged[f"{label_a} Rate %"] - merged[f"{label_b} Rate %"]
    merged["Direction"] = np.where(merged["Delta pp"] >= 0, label_a, label_b)
    return merged.sort_values("Delta pp", key=lambda s: s.abs(), ascending=False, ignore_index=True)


def issue_mask_from_mode(df_in: pd.DataFrame, mode: str, issue_name: Optional[str], detractor_cols: List[str], delighter_cols: List[str]) -> pd.Series:
    if df_in.empty:
        return pd.Series(dtype=bool)
    if mode == "1–2★ reviews":
        return pd.to_numeric(df_in.get("Star Rating"), errors="coerce") <= 2
    target_cols = detractor_cols if mode == "Detractor symptom" else delighter_cols
    if not issue_name:
        return pd.Series(False, index=df_in.index)
    sym = str(issue_name).strip().lower()
    mask = pd.Series(False, index=df_in.index)
    for c in target_cols:
        if c not in df_in.columns:
            continue
        s = df_in[c].astype("string").fillna("").str.strip().str.lower()
        mask |= s.eq(sym)
    return mask


def rank_drivers_for_issue(
    df_in: pd.DataFrame,
    issue_mask: pd.Series,
    driver_cols: List[str],
    mode: str,
    min_group_n: int = 5,
) -> pd.DataFrame:
    if df_in.empty or issue_mask.empty or issue_mask.sum() == 0:
        return pd.DataFrame(columns=["Driver", "Bucket", "Reviews", "Issue Reviews", "Issue Rate %", "Lift vs Baseline", "Avg Star", "Contribution %", "Impact Score"])
    d = df_in.copy()
    d["__issue"] = issue_mask.values
    d["__star"] = pd.to_numeric(d.get("Star Rating"), errors="coerce")
    baseline_rate = float(issue_mask.mean())
    overall_avg = float(d["__star"].mean()) if d["__star"].notna().any() else np.nan
    rows: List[Dict[str, Any]] = []

    for col in driver_cols:
        if col not in d.columns:
            continue
        s = d[col].astype("string").fillna("UNKNOWN").replace({"": "UNKNOWN"})
        tokenize = bool(s.head(250).astype(str).str.contains(r"\s\|\s", regex=True).any())
        if tokenize:
            temp = pd.DataFrame({"bucket": s.str.split(r"\s*\|\s*", regex=True), "issue": d["__issue"], "star": d["__star"]})
            temp = temp.explode("bucket")
            temp["bucket"] = temp["bucket"].astype("string").fillna("UNKNOWN").replace({"": "UNKNOWN"})
        else:
            temp = pd.DataFrame({"bucket": s, "issue": d["__issue"], "star": d["__star"]})
        grp = temp.groupby("bucket").agg(reviews=("issue", "size"), issue_reviews=("issue", "sum"), avg_star=("star", "mean")).reset_index()
        grp = grp[grp["reviews"] >= int(min_group_n)]
        if grp.empty:
            continue
        grp["issue_rate"] = grp["issue_reviews"] / grp["reviews"]
        grp["lift"] = grp["issue_rate"] / max(baseline_rate, 1e-9)
        grp["contribution"] = grp["issue_reviews"] / max(int(issue_mask.sum()), 1)
        if mode in {"Detractor symptom", "1–2★ reviews"}:
            grp["impact_score"] = grp["issue_reviews"] * np.maximum(grp["lift"] - 1.0, 0) * np.maximum(overall_avg - grp["avg_star"] + 0.15, 0.15)
        else:
            grp["impact_score"] = grp["issue_reviews"] * np.maximum(grp["lift"] - 1.0, 0) * np.maximum(grp["avg_star"] - overall_avg + 0.15, 0.15)
        for _, row in grp.sort_values("impact_score", ascending=False).head(15).iterrows():
            rows.append(
                {
                    "Driver": col,
                    "Bucket": str(row["bucket"]),
                    "Reviews": int(row["reviews"]),
                    "Issue Reviews": int(row["issue_reviews"]),
                    "Issue Rate %": round(float(row["issue_rate"]) * 100.0, 1),
                    "Lift vs Baseline": round(float(row["lift"]), 2),
                    "Avg Star": round(float(row["avg_star"]), 2) if pd.notna(row["avg_star"]) else np.nan,
                    "Contribution %": round(float(row["contribution"]) * 100.0, 1),
                    "Impact Score": round(float(row["impact_score"]), 2),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["Driver", "Bucket", "Reviews", "Issue Reviews", "Issue Rate %", "Lift vs Baseline", "Avg Star", "Contribution %", "Impact Score"])
    out = pd.DataFrame(rows)
    return out.sort_values("Impact Score", ascending=False, ignore_index=True).head(30)


def estimate_issue_lift(df_in: pd.DataFrame, issue_mask: pd.Series, mode: str, reduction_pct: float) -> Dict[str, float]:
    total = len(df_in)
    issue_reviews = int(issue_mask.sum())
    if total == 0 or issue_reviews == 0 or "Star Rating" not in df_in.columns:
        return {"estimated_lift": 0.0, "issue_share_pct": 0.0, "issue_avg": np.nan, "overall_avg": np.nan}
    stars = pd.to_numeric(df_in["Star Rating"], errors="coerce")
    overall_avg = float(stars.mean()) if stars.notna().any() else np.nan
    issue_avg = float(stars[issue_mask].mean()) if stars[issue_mask].notna().any() else np.nan
    share = issue_reviews / total
    if mode in {"Detractor symptom", "1–2★ reviews"}:
        local_gap = max(0.0, overall_avg - issue_avg)
    else:
        local_gap = max(0.0, issue_avg - overall_avg)
    estimated_lift = share * local_gap * (float(reduction_pct) / 100.0)
    return {
        "estimated_lift": round(float(estimated_lift), 3),
        "issue_share_pct": round(float(share) * 100.0, 1),
        "issue_avg": round(float(issue_avg), 2) if pd.notna(issue_avg) else np.nan,
        "overall_avg": round(float(overall_avg), 2) if pd.notna(overall_avg) else np.nan,
    }


def build_action_center(
    df_in: pd.DataFrame,
    issue_label: str,
    mode: str,
    issue_mask: pd.Series,
    driver_df: pd.DataFrame,
    quotes: List[Dict[str, str]],
    product_label: str,
) -> Dict[str, Any]:
    total_reviews = len(df_in)
    issue_reviews = int(issue_mask.sum())
    stars = pd.to_numeric(df_in.get("Star Rating"), errors="coerce")
    overall_avg = float(stars.mean()) if stars.notna().any() else np.nan
    issue_avg = float(stars[issue_mask].mean()) if issue_reviews and stars[issue_mask].notna().any() else np.nan
    top_drivers = driver_df.head(3).to_dict("records") if driver_df is not None and not driver_df.empty else []
    top_driver_text = "; ".join(
        [f"{r['Driver']}={r['Bucket']} ({r['Issue Rate %']}% issue rate, {r['Contribution %']}% contribution)" for r in top_drivers]
    ) or "No strong concentration detected in the selected driver set."
    quote_text = quotes[0]["text"] if quotes else "No direct quote available for this filtered slice."
    quote_meta = quotes[0]["meta"] if quotes else ""

    problem_line = f"{issue_label} shows up in {issue_reviews:,} of {total_reviews:,} filtered reviews ({issue_reviews / max(total_reviews, 1) * 100.0:.1f}%)."
    rating_line = f"Overall Avg ★ is {overall_avg:.2f}; impacted reviews average {issue_avg:.2f}★." if pd.notna(overall_avg) and pd.notna(issue_avg) else "Star-rating context is limited in this slice."

    exec_summary = textwrap.dedent(
        f"""
        • **Signal:** {problem_line}
        • **Impact:** {rating_line}
        • **Where it concentrates:** {top_driver_text}
        • **Evidence:** “{quote_text}” ({quote_meta})
        • **Recommendation:** Triage the top concentration buckets first, validate the specific failure mode in recent reviews, and track whether issue prevalence drops in the next 2–4 weeks.
        """
    ).strip()

    eng_brief = textwrap.dedent(
        f"""
        # Engineering Brief — {issue_label}

        ## Problem statement
        {product_label}: {problem_line}
        {rating_line}

        ## Root-cause hypothesis
        {top_driver_text}

        ## Consumer evidence
        > {quote_text}
        > {quote_meta}

        ## Recommended next actions
        1. Pull recent defect or support cases for the top-ranked driver buckets.
        2. Reproduce the issue on the most-concentrated model / channel combinations.
        3. Check whether the failure is tied to a specific component, journey step, or onboarding moment.
        4. Add a post-fix watchlist for issue prevalence, Avg ★, and low-star rate.

        ## Success metric
        Reduce {issue_label} mention rate by 20%+ in the affected slice without lowering delighter mentions.
        """
    ).strip()

    jira_payload = {
        "summary": f"Investigate consumer issue: {issue_label}",
        "priority": "High" if mode in {"Detractor symptom", "1–2★ reviews"} else "Medium",
        "labels": ["consumer-insights", "voice-of-customer", _norm(product_label)[:24], _norm(issue_label)[:24]],
        "description": {
            "product": product_label,
            "mode": mode,
            "problem": problem_line,
            "impact": rating_line,
            "top_drivers": top_drivers,
            "consumer_evidence": [{"text": q["text"], "meta": q["meta"]} for q in quotes[:3]],
            "next_steps": [
                "Validate issue with QA and support logs",
                "Reproduce with top-ranked cohort",
                "Create fix / mitigation plan",
                "Monitor issue prevalence weekly",
            ],
        },
        "acceptance_criteria": [
            "Issue driver validated or falsified",
            "Action owner assigned",
            "Monitoring slice defined",
            "Success metric agreed",
        ],
    }

    support_macro = textwrap.dedent(
        f"""
        Thanks for the feedback, and I’m sorry you ran into {issue_label.lower()}.
        We’re reviewing this closely with the product team. To help us diagnose it faster, please share your model/SKU, where you purchased it, and when the issue started.
        In the meantime, we can guide you through a few checks or next best steps based on your setup.
        """
    ).strip()

    retailer_note = textwrap.dedent(
        f"""
        Subject: Consumer signal escalation — {issue_label}

        We’re seeing a meaningful consumer signal around **{issue_label}** in the current filtered dataset for **{product_label}**.
        {problem_line}
        {rating_line}
        Highest concentration: {top_driver_text}

        Example verbatim: “{quote_text}” ({quote_meta})

        We recommend flagging this for your category / quality contact and aligning on any recent returns, defect claims, or PDP / onboarding confusion patterns tied to this slice.
        """
    ).strip()

    return {
        "executive_summary": exec_summary,
        "engineering_brief": eng_brief,
        "jira": jira_payload,
        "support_macro": support_macro,
        "retailer_note": retailer_note,
    }


# -----------------------------------------------------------------------------
# AI helpers
# -----------------------------------------------------------------------------
def _build_ai_corpus(df_in: pd.DataFrame, max_rows: int) -> Tuple[List[str], List[Dict[str, Any]]]:
    if df_in.empty:
        return [], []
    df_use = df_in if len(df_in) <= max_rows else df_in.sample(max_rows, random_state=42)
    texts: List[str] = []
    meta: List[Dict[str, Any]] = []
    for _, r in df_use.iterrows():
        verb = mask_pii(str(r.get("Verbatim", "") or ""))
        star = r.get("Star Rating", "")
        country = r.get("Country", "")
        source = r.get("Source", "")
        signal_type = r.get("Signal Type", "")
        dtv = r.get("Review Date", "")
        try:
            dtv = pd.to_datetime(dtv).strftime("%Y-%m-%d") if pd.notna(dtv) else ""
        except Exception:
            dtv = ""
        prefix = f"[★{star}] [{country}] [{source}] [{signal_type}] [{dtv}] "
        txt = (prefix + verb).strip()
        texts.append(txt)
        meta.append({"star": star, "country": country, "source": source, "signal_type": signal_type, "date": dtv, "text": verb})
    return texts, meta


def get_local_index(df_in: pd.DataFrame, corpus_cap: int = 1800) -> Tuple[Any, Any, List[str], List[Dict[str, Any]]]:
    if not _HAS_SKLEARN:
        return None, None, [], []
    cache = st.session_state.setdefault("_ai_local_index", {})
    try:
        sig = hashlib.sha1((str(df_in.shape) + "|" + "|".join(df_in.columns.astype(str))).encode("utf-8")).hexdigest()
    except Exception:
        sig = str(df_in.shape)
    if sig in cache:
        return cache[sig]["vec"], cache[sig]["mat"], cache[sig]["texts"], cache[sig]["meta"]
    texts, meta = _build_ai_corpus(df_in, max_rows=corpus_cap)
    if not texts:
        cache[sig] = {"vec": None, "mat": None, "texts": [], "meta": []}
        return None, None, [], []
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=35000)
    mat = vec.fit_transform(texts)
    cache[sig] = {"vec": vec, "mat": mat, "texts": texts, "meta": meta}
    return vec, mat, texts, meta


def retrieve_quotes(query: str, df_in: pd.DataFrame, k: int = 8) -> List[Dict[str, Any]]:
    if not _HAS_SKLEARN:
        return []
    vec, mat, texts, meta = get_local_index(df_in)
    if vec is None or mat is None or not texts:
        return []
    qv = vec.transform([query])
    sims = linear_kernel(qv, mat).flatten()
    if sims.size == 0:
        return []
    top = np.argsort(-sims)[: max(1, k)]
    out = []
    for rank, idx in enumerate(top, start=1):
        m = meta[int(idx)]
        out.append({"id": f"Q{rank}", "score": float(sims[int(idx)]), **m})
    return out


def build_knowledge_pack(
    df_in: pd.DataFrame,
    source_label: str,
    detractor_cols: List[str],
    delighter_cols: List[str],
) -> Dict[str, Any]:
    cnt, avg, low = section_stats(df_in)
    seeded_mask = df_in["Seeded"].astype("string").str.upper().eq("YES") if "Seeded" in df_in.columns else pd.Series(False, index=df_in.index)
    organic = df_in[~seeded_mask]
    seeded = df_in[seeded_mask]
    org_cnt, org_avg, org_low = section_stats(organic)
    seed_cnt, seed_avg, seed_low = section_stats(seeded)
    radar = detect_issue_radar(df_in, detractor_cols, delighter_cols)
    themes = compute_text_theme_diffs(df_in, top_n=10)
    return {
        "product_profile": infer_product_profile(df_in, source_label),
        "csat": {
            "count": cnt,
            "avg_star": round(avg, 3) if pd.notna(avg) else np.nan,
            "pct_1_2": round(low, 2) if pd.notna(low) else np.nan,
            "organic": {"count": org_cnt, "avg_star": round(org_avg, 3) if pd.notna(org_avg) else np.nan, "pct_1_2": round(org_low, 2) if pd.notna(org_low) else np.nan},
            "seeded": {"count": seed_cnt, "avg_star": round(seed_avg, 3) if pd.notna(seed_avg) else np.nan, "pct_1_2": round(seed_low, 2) if pd.notna(seed_low) else np.nan},
        },
        "top_detractors": analyze_symptoms_fast(df_in, detractor_cols).head(12).to_dict("records") if detractor_cols else [],
        "top_delighters": analyze_symptoms_fast(df_in, delighter_cols).head(12).to_dict("records") if delighter_cols else [],
        "issue_radar": radar.head(8).to_dict("records") if radar is not None and not radar.empty else [],
        "theme_diffs": themes,
    }


def openai_chat_http(api_key: str, model: str, messages: List[Dict[str, str]], temperature: Optional[float] = None, max_tokens: int = 900, timeout_s: int = 60) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages, "max_tokens": int(max_tokens)}
    if temperature is not None and model_supports_temperature(model):
        payload["temperature"] = float(temperature)
    last_err: Optional[Exception] = None
    for attempt in range(5):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(8.0, (2 ** attempt) * 0.6 + 0.1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"OpenAI API error {r.status_code}: {r.text[:600]}")
            data = r.json()
            return str(data["choices"][0]["message"]["content"])
        except Exception as e:
            last_err = e
            time.sleep(min(6.0, (2 ** attempt) * 0.35 + 0.1))
    raise RuntimeError(str(last_err) if last_err else "Unknown OpenAI error")


def transcribe_audio_http(api_key: str, uploaded_audio: Any, model: str = "gpt-4o-mini-transcribe", timeout_s: int = 90) -> str:
    audio_name = getattr(uploaded_audio, "name", "voice_question.wav")
    audio_type = getattr(uploaded_audio, "type", "audio/wav")
    audio_bytes = uploaded_audio.getvalue()
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (audio_name, audio_bytes, audio_type)}
    data = {"model": model, "response_format": "json"}
    r = requests.post(url, headers=headers, files=files, data=data, timeout=timeout_s)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI transcription error {r.status_code}: {r.text[:600]}")
    payload = r.json()
    text = payload.get("text") if isinstance(payload, dict) else None
    return str(text or "").strip()


def local_ai_answer(question: str, knowledge: Dict[str, Any], quotes: List[Dict[str, Any]]) -> str:
    csat = knowledge.get("csat", {})
    prof = knowledge.get("product_profile", {})
    det = knowledge.get("top_detractors", [])[:6]
    deli = knowledge.get("top_delighters", [])[:6]
    radar = knowledge.get("issue_radar", [])[:5]
    themes = knowledge.get("theme_diffs", {})
    lines = []
    lines.append("**Local insight brief**")
    lines.append(f"- Reviews in filter: **{csat.get('count', 0):,}**")
    lines.append(f"- Avg ★: **{csat.get('avg_star', 0)}** • % 1–2★: **{csat.get('pct_1_2', 0)}%**")
    if prof:
        lines.append(f"- Product guess: **{prof.get('product_guess', 'Unknown')}**")
    if radar:
        lines.append("\n**Top watchouts right now:**")
        for r in radar:
            lines.append(f"- {r.get('Type')}: {r.get('Issue')} — {r.get('Narrative')}")
    if det:
        lines.append("\n**Biggest detractors:**")
        for r in det:
            lines.append(f"- {r.get('Item')}: {r.get('Review Mentions')} reviews • Avg ★ {r.get('Avg Star')}")
    if deli:
        lines.append("\n**Strongest delighters:**")
        for r in deli:
            lines.append(f"- {r.get('Item')}: {r.get('Review Mentions')} reviews • Avg ★ {r.get('Avg Star')}")
    low_vs_high = themes.get("low_vs_high", [])[:5]
    if low_vs_high:
        lines.append("\n**Language most associated with low-star reviews:**")
        for r in low_vs_high:
            lines.append(f"- {r.get('term')}: +{r.get('delta_pp')}pp vs high-star reviews")
    if quotes:
        lines.append("\n**Most relevant quotes:**")
        for q in quotes[:5]:
            lines.append(f"- [{q['id']}] ★{q.get('star')} {q.get('country')} {q.get('source')} {q.get('date')}: {q.get('text', '')[:240]}")
    lines.append(f"\n**Question received:** {question}")
    return "\n".join(lines)

# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------
st.title("Star Walk — Consumer Insights Summit Dashboard")
st.caption(f"Version: {APP_VERSION}")

st.markdown("### 📁 Data input")
mode = st.radio(
    "Choose input",
    options=[
        "Star Walk scrubbed verbatims (Excel/CSV)",
        "JSON export (auto-convert → dashboard)",
    ],
    horizontal=True,
)

df_base: Optional[pd.DataFrame] = None
source_label: str = "Uploaded file"
meta_info: Dict[str, Any] = {}

if mode.startswith("Star Walk"):
    uploaded_file = st.file_uploader("Upload Star Walk Excel/CSV", type=["xlsx", "csv"], key="upl_starwalk")
    if not uploaded_file:
        st.info("Upload a Star Walk scrubbed verbatims file to begin.")
        st.stop()
    t0 = time.perf_counter()
    df_base = _load_starwalk_table(uploaded_file.getvalue(), uploaded_file.name)
    meta_info = {"source": uploaded_file.name, "load_s": round(time.perf_counter() - t0, 3)}
    source_label = uploaded_file.name
else:
    left, right = st.columns(2)
    with left:
        json_file = st.file_uploader("Upload JSON export", type=["json"], key="upl_json")
    with right:
        pasted = st.text_area("…or paste JSON / JSON Lines", height=200, key="json_paste", placeholder="Paste JSON or JSON Lines here.")

    raw_text: Optional[str] = None
    source_label = "pasted_json"
    if json_file is not None and getattr(json_file, "size", 0) > 0:
        source_label = json_file.name
        raw_text = json_file.getvalue().decode("utf-8-sig", errors="replace")
    elif pasted and pasted.strip():
        raw_text = pasted.strip()

    include_extra_cols = st.checkbox("Include extra columns after Symptom 20", value=True)
    if not raw_text:
        st.info("Upload JSON or paste JSON text to begin.")
        st.stop()
    with st.spinner("Parsing JSON and converting to Star Walk format…"):
        t0 = time.perf_counter()
        try:
            df_base, meta_info = _json_text_to_starwalk(raw_text, source_label, include_extra_cols=include_extra_cols)
            meta_info["convert_s"] = round(time.perf_counter() - t0, 3)
        except Exception as e:
            st.error(str(e))
            st.stop()
    with st.expander("✅ JSON conversion details", expanded=False):
        for w in meta_info.get("warnings", []):
            st.info(w)
        st.write("Records:", meta_info.get("records"))
        st.write("Truncation stats:", meta_info.get("stats"))
        out_bytes = df_base.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Download converted Star Walk CSV",
            out_bytes,
            file_name=f"{Path(source_label).stem}_starwalk.csv",
            mime="text/csv",
        )

assert df_base is not None

with st.expander("🔗 Optional Signal Hub — add support tickets, chats, call notes, or social exports", expanded=False):
    st.caption("Upload extra CSV/XLSX files with at least one text column. The app will append them into the filtered insight layer and tag them with Signal Type.")
    supplemental_files = st.file_uploader(
        "Supplemental files",
        type=["csv", "xlsx"],
        accept_multiple_files=True,
        key="supp_files",
    )

supp_dfs: List[pd.DataFrame] = []
if supplemental_files:
    load_notes: List[str] = []
    for f in supplemental_files:
        try:
            sdf = _load_supplemental_signal_file(f.getvalue(), f.name)
            supp_dfs.append(sdf)
            load_notes.append(f"Loaded {f.name}: {len(sdf):,} rows")
        except Exception as e:
            load_notes.append(f"Skipped {f.name}: {e}")
    if supp_dfs:
        df_base = append_supplemental_signals(df_base, supp_dfs)
    for note in load_notes:
        st.caption(note)

product_label = _infer_product_label(df_base, source_label)

try:
    dataset_sig = hashlib.sha1(
        (str(source_label) + "|" + str(df_base.shape) + "|" + "|".join([str(c) for c in df_base.columns.tolist()[:100]])).encode("utf-8")
    ).hexdigest()
except Exception:
    dataset_sig = str(getattr(df_base, "shape", ""))

if st.session_state.get("_dataset_sig") != dataset_sig:
    for key in ["_ai_local_index", "review_page", "saved_views", "_review_sort_prev"]:
        st.session_state.pop(key, None)
    st.session_state["_dataset_sig"] = dataset_sig

st.markdown(
    f"""
    <div class='soft-panel'>
      <div><b>Source:</b> {esc(source_label)}</div>
      <div style='margin-top:6px;'><b>Product guess:</b> {esc(product_label)}</div>
      <div class='small-muted' style='margin-top:8px;'>All insights below are based on the currently filtered slice of data.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Sidebar filters
# -----------------------------------------------------------------------------
core_cols = {"Country", "Source", "Model (SKU)", "Seeded", "New Review", "Star Rating", "Review Date", "Verbatim"}
symptom_cols = {f"Symptom {i}" for i in range(1, 21)}
extra_filter_candidates = [c for c in df_base.columns if c not in (core_cols | symptom_cols)]
extra_filter_candidates = [c for c in extra_filter_candidates if str(c).strip() and str(c).strip().lower() not in {"unnamed: 0"}]

st.session_state.setdefault("extra_filter_cols", [])
st.session_state.setdefault("saved_views", {})

tz_today = datetime.now(_NY_TZ).date() if _NY_TZ else datetime.today().date()
st.session_state.setdefault("tf", "All Time")
st.session_state.setdefault("tf_range", (tz_today - timedelta(days=30), tz_today))
st.session_state.setdefault("sr", ["All"])
st.session_state.setdefault("kw", "")
st.session_state.setdefault("delight", ["All"])
st.session_state.setdefault("detract", ["All"])
st.session_state.setdefault("rpp", 10)

all_detractor_columns = [c for c in [f"Symptom {i}" for i in range(1, 11)] if c in df_base.columns]
all_delighter_columns = [c for c in [f"Symptom {i}" for i in range(11, 21)] if c in df_base.columns]
detractor_symptoms_all = collect_unique_symptoms(df_base, all_detractor_columns)
delighter_symptoms_all = collect_unique_symptoms(df_base, all_delighter_columns)


def _col_options(df_in: pd.DataFrame, col: str, max_vals: Optional[int] = 250) -> List[str]:
    if col not in df_in.columns:
        return ["ALL"]
    s0 = df_in[col].astype("string").replace({"": pd.NA}).dropna()
    if s0.empty:
        return ["ALL"]
    tokenize_multi = bool(s0.head(300).astype(str).str.contains(r"\s\|\s", regex=True).any())
    if tokenize_multi:
        tok = (
            s0.astype(str)
            .str.split(r"\s*\|\s*", regex=True)
            .explode()
            .astype("string")
            .str.strip()
            .replace({"": pd.NA})
            .dropna()
        )
        vc = tok.value_counts()
    else:
        vc = s0.value_counts()
    if isinstance(max_vals, int) and max_vals > 0:
        vc = vc.head(max_vals)
    return ["ALL"] + vc.index.astype(str).tolist()


def _sanitize_multiselect(key: str, options: List[Any], default: List[Any]) -> List[Any]:
    cur = st.session_state.get(key, default)
    if cur is None:
        cur = list(default)
    if not isinstance(cur, list):
        cur = [cur]
    cur = [v for v in cur if v in options]
    if not cur:
        cur = list(default)
    if ("ALL" in cur or "All" in cur) and len(cur) > 1:
        cur = [v for v in cur if v not in {"ALL", "All"}]
    st.session_state[key] = cur
    return cur


def _reset_all_filters() -> None:
    st.session_state["tf"] = "All Time"
    st.session_state["tf_range"] = (tz_today - timedelta(days=30), tz_today)
    st.session_state["sr"] = ["All"]
    st.session_state["kw"] = ""
    st.session_state["delight"] = ["All"]
    st.session_state["detract"] = ["All"]
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + list(st.session_state.get("extra_filter_cols", [])):
        st.session_state[f"f_{col}"] = ["ALL"]
        st.session_state.pop(f"f_{col}_range", None)
        st.session_state.pop(f"f_{col}_contains", None)
    st.session_state["extra_filter_cols"] = []
    st.session_state["review_page"] = 0


if st.sidebar.button("🏠 Dashboard", use_container_width=True):
    st.session_state["main_view"] = "📊 Dashboard"
    st.rerun()

st.sidebar.header("🔍 Filters")
if st.sidebar.button("🧹 Clear all filters", use_container_width=True):
    _reset_all_filters()
    st.rerun()

with st.sidebar.expander("🗓️ Timeframe", expanded=False):
    tf_opts = ["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"]
    if st.session_state.get("tf") not in tf_opts:
        st.session_state["tf"] = "All Time"
    st.selectbox("Select timeframe", options=tf_opts, key="tf")
    if st.session_state["tf"] == "Custom Range":
        st.date_input("Start / end", value=st.session_state.get("tf_range", (tz_today - timedelta(days=30), tz_today)), key="tf_range")

with st.sidebar.expander("⭐ Star rating", expanded=False):
    sr_opts = ["All", 5, 4, 3, 2, 1]
    _sanitize_multiselect("sr", sr_opts, ["All"])
    st.multiselect("Select stars", options=sr_opts, default=st.session_state["sr"], key="sr")

with st.sidebar.expander("🌍 Country / Source / Model / Seeded", expanded=True):
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        opts = _col_options(df_base, col, max_vals=250)
        _sanitize_multiselect(f"f_{col}", opts, ["ALL"])
        st.multiselect(col, options=opts, default=st.session_state[f"f_{col}"], key=f"f_{col}")

with st.sidebar.expander("🩺 Symptom filters", expanded=False):
    det_opts = ["All"] + detractor_symptoms_all
    del_opts = ["All"] + delighter_symptoms_all
    _sanitize_multiselect("detract", det_opts, ["All"])
    _sanitize_multiselect("delight", del_opts, ["All"])
    st.multiselect("Detractors", options=det_opts, default=st.session_state["detract"], key="detract")
    st.multiselect("Delighters", options=del_opts, default=st.session_state["delight"], key="delight")

with st.sidebar.expander("🔎 Keyword", expanded=False):
    st.text_input("Search in review text", value=st.session_state.get("kw", ""), key="kw")

with st.sidebar.expander("➕ Add filters", expanded=False):
    st.multiselect(
        "Available extra columns",
        options=extra_filter_candidates,
        default=st.session_state.get("extra_filter_cols", []),
        key="extra_filter_cols",
    )

extra_cols = st.session_state.get("extra_filter_cols", []) or []
if extra_cols:
    with st.sidebar.expander("🧩 Extra filters", expanded=True):
        for col in extra_cols:
            if col not in df_base.columns:
                continue
            s = df_base[col]
            kind = "categorical"
            try:
                if pd.api.types.is_datetime64_any_dtype(s):
                    kind = "date"
                else:
                    num = pd.to_numeric(s, errors="coerce")
                    if num.notna().mean() >= 0.9 and num.nunique(dropna=True) > 6:
                        kind = "numeric"
            except Exception:
                kind = "categorical"
            if kind == "numeric":
                num = pd.to_numeric(s, errors="coerce").dropna()
                if num.empty:
                    continue
                lo, hi = float(num.min()), float(num.max())
                if lo == hi:
                    st.caption(f"{col}: {lo:g} (constant)")
                    continue
                key = f"f_{col}_range"
                default = st.session_state.get(key, (lo, hi))
                if not (isinstance(default, (tuple, list)) and len(default) == 2):
                    default = (lo, hi)
                st.slider(col, min_value=lo, max_value=hi, value=(float(default[0]), float(default[1])), key=key)
            else:
                try:
                    nunique = int(s.astype("string").replace({"": pd.NA}).nunique(dropna=True))
                except Exception:
                    nunique = 0
                if nunique > 600:
                    st.text_input(f"{col} contains", value=str(st.session_state.get(f"f_{col}_contains") or ""), key=f"f_{col}_contains")
                else:
                    opts = _col_options(df_base, col, max_vals=None)
                    _sanitize_multiselect(f"f_{col}", opts, ["ALL"])
                    st.multiselect(col, options=opts, default=st.session_state[f"f_{col}"], key=f"f_{col}")

with st.sidebar.expander("💾 Saved Views", expanded=False):
    name = st.text_input("Preset name", value="", key="sv_name")
    c_a, c_b = st.columns(2)
    with c_a:
        if st.button("💾 Save", use_container_width=True):
            nm = (st.session_state.get("sv_name") or "").strip() or f"Preset {len(st.session_state['saved_views']) + 1}"
            st.session_state["saved_views"][nm] = collect_filter_state(extra_cols)
            st.success(f"Saved {nm}")
    with c_b:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state["saved_views"] = {}
            st.success("Cleared saved presets.")
    preset_names = sorted(st.session_state.get("saved_views", {}).keys())
    if preset_names:
        sel = st.selectbox("Load preset", options=["—"] + preset_names, index=0)
        cc1, cc2 = st.columns(2)
        with cc1:
            if sel != "—" and st.button("Load", use_container_width=True):
                apply_filter_state(st.session_state["saved_views"].get(sel, {}), extra_filter_candidates)
                st.rerun()
        with cc2:
            if sel != "—":
                payload = json.dumps(st.session_state["saved_views"].get(sel, {}), ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button("Export", payload, file_name=f"{sel}.json", mime="application/json", use_container_width=True)
    imported = st.file_uploader("Import preset JSON", type=["json"], key="preset_json")
    if imported is not None:
        try:
            preset_obj = json.loads(imported.getvalue().decode("utf-8"))
            if isinstance(preset_obj, dict):
                apply_filter_state(preset_obj, extra_filter_candidates)
                st.success("Imported preset.")
        except Exception as e:
            st.warning(f"Could not import preset: {e}")

# -----------------------------------------------------------------------------
# Apply filters
# -----------------------------------------------------------------------------
d0 = df_base
mask = pd.Series(True, index=d0.index)

tf = st.session_state.get("tf", "All Time")
start_date = end_date = None
if tf == "Custom Range":
    rng = st.session_state.get("tf_range", (tz_today - timedelta(days=30), tz_today))
    if isinstance(rng, (tuple, list)) and len(rng) == 2:
        start_date, end_date = rng
elif tf == "Last Week":
    start_date, end_date = tz_today - timedelta(days=7), tz_today
elif tf == "Last Month":
    start_date, end_date = tz_today - timedelta(days=30), tz_today
elif tf == "Last Year":
    start_date, end_date = tz_today - timedelta(days=365), tz_today

if start_date and end_date and "Review Date" in d0.columns:
    dt = pd.to_datetime(d0["Review Date"], errors="coerce")
    end_inclusive = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    mask &= (dt >= pd.Timestamp(start_date)) & (dt <= end_inclusive)

sr_sel = [x for x in st.session_state.get("sr", ["All"]) if str(x).strip() and str(x).lower() != "all"]
if sr_sel and "Star Rating" in d0.columns:
    sr_nums = [int(x) for x in sr_sel if str(x).isdigit()]
    if sr_nums:
        mask &= pd.to_numeric(d0["Star Rating"], errors="coerce").isin(sr_nums)

for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
    sel_clean = [x for x in st.session_state.get(f"f_{col}", ["ALL"]) if str(x).strip() and str(x).upper() != "ALL"]
    if col in d0.columns and sel_clean:
        mask &= series_matches_any(d0[col], [str(x) for x in sel_clean])

for col in extra_cols:
    if col not in d0.columns:
        continue
    range_key = f"f_{col}_range"
    if range_key in st.session_state and isinstance(st.session_state.get(range_key), (tuple, list)):
        lo, hi = st.session_state.get(range_key)
        num = pd.to_numeric(d0[col], errors="coerce")
        mask &= num.between(float(lo), float(hi))
    else:
        contains_key = f"f_{col}_contains"
        contains_val = (st.session_state.get(contains_key) or "").strip()
        if contains_val:
            mask &= d0[col].astype("string").fillna("").str.contains(contains_val, case=False, na=False)
        else:
            sel_clean = [x for x in st.session_state.get(f"f_{col}", ["ALL"]) if str(x).strip() and str(x).upper() != "ALL"]
            if sel_clean:
                mask &= series_matches_any(d0[col], [str(x) for x in sel_clean])

sel_del = [x for x in st.session_state.get("delight", ["All"]) if str(x).strip() and str(x).lower() != "all"]
sel_det = [x for x in st.session_state.get("detract", ["All"]) if str(x).strip() and str(x).lower() != "all"]
if sel_del and all_delighter_columns:
    mask &= d0[all_delighter_columns].isin(sel_del).any(axis=1)
if sel_det and all_detractor_columns:
    mask &= d0[all_detractor_columns].isin(sel_det).any(axis=1)

keyword = (st.session_state.get("kw") or "").strip()
if keyword and "Verbatim" in d0.columns:
    mask &= d0["Verbatim"].astype("string").fillna("").str.contains(keyword, case=False, na=False)

filtered = d0[mask].copy()

active_pills: List[str] = []
if tf != "All Time":
    active_pills.append(f"<div class='pill'>Timeframe: {esc(tf if tf != 'Custom Range' else f'{start_date} → {end_date}')}</div>")
if sr_sel:
    active_pills.append(f"<div class='pill'>Stars: {esc(', '.join(map(str, sr_sel)))}</div>")
for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + extra_cols:
    if f"f_{col}_range" in st.session_state and isinstance(st.session_state.get(f"f_{col}_range"), (tuple, list)):
        lo, hi = st.session_state.get(f"f_{col}_range")
        active_pills.append(f"<div class='pill'>{esc(col)}: {float(lo):g} → {float(hi):g}</div>")
        continue
    cv = (st.session_state.get(f"f_{col}_contains") or "").strip()
    if cv:
        active_pills.append(f"<div class='pill'>{esc(col)} contains: {esc(cv)}</div>")
        continue
    sel = st.session_state.get(f"f_{col}", ["ALL"])
    if isinstance(sel, list) and sel and "ALL" not in sel:
        pretty = ", ".join([str(x) for x in sel[:4]]) + ("" if len(sel) <= 4 else f" +{len(sel) - 4}")
        active_pills.append(f"<div class='pill'>{esc(col)}: {esc(pretty)}</div>")
if sel_del:
    active_pills.append(f"<div class='pill'>Delighters: {esc(', '.join(map(str, sel_del[:3])) + ('' if len(sel_del) <= 3 else f' +{len(sel_del)-3}'))}</div>")
if sel_det:
    active_pills.append(f"<div class='pill'>Detractors: {esc(', '.join(map(str, sel_det[:3])) + ('' if len(sel_det) <= 3 else f' +{len(sel_det)-3}'))}</div>")
if keyword:
    active_pills.append(f"<div class='pill'>Keyword: {esc(keyword)}</div>")

st.markdown(
    f"""
    <div class='soft-panel'>
      <div><b>Active filters</b> • Showing <b>{len(filtered):,}</b> of <b>{len(df_base):,}</b> rows</div>
      <div class='pill-row' style='margin-top:8px;'>{''.join(active_pills) if active_pills else '<span class="small-muted">None (All data)</span>'}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

view = st.radio(
    "View",
    options=["📊 Dashboard", "⚖️ Compare", "🧠 Root Cause", "📝 All Reviews", "🤖 AI"],
    horizontal=True,
    index=["📊 Dashboard", "⚖️ Compare", "🧠 Root Cause", "📝 All Reviews", "🤖 AI"].index(st.session_state.get("main_view", "📊 Dashboard")) if st.session_state.get("main_view") else 0,
    key="main_view",
)

# -----------------------------------------------------------------------------
# Dashboard view
# -----------------------------------------------------------------------------
if view.startswith("📊"):
    st.markdown("## ⭐ Star Rating Metrics")
    seeded_mask = filtered["Seeded"].astype("string").str.upper().eq("YES") if "Seeded" in filtered.columns else pd.Series(False, index=filtered.index)
    all_cnt, all_avg, all_low = section_stats(filtered)
    org_cnt, org_avg, org_low = section_stats(filtered[~seeded_mask])
    seed_cnt, seed_avg, seed_low = section_stats(filtered[seeded_mask])

    st.markdown(
        "<div class='metric-grid'>"
        + metric_card_html("All reviews", f"{all_cnt:,}", f"Avg ★ {all_avg:.2f} • 1–2★ {all_low:.1f}%" if all_cnt else "No ratings")
        + metric_card_html("Organic", f"{org_cnt:,}", f"Avg ★ {org_avg:.2f} • 1–2★ {org_low:.1f}%" if org_cnt else "No ratings")
        + metric_card_html("Seeded", f"{seed_cnt:,}", f"Avg ★ {seed_avg:.2f} • 1–2★ {seed_low:.1f}%" if seed_cnt else "No ratings")
        + metric_card_html("Signals in slice", f"{filtered.get('Signal Type', pd.Series(dtype='string')).astype('string').replace({'': pd.NA}).nunique(dropna=True) if 'Signal Type' in filtered.columns else 1}", "Distinct signal types in current slice")
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("## 🚨 Emerging Issue Radar")
    radar = detect_issue_radar(filtered, all_detractor_columns, all_delighter_columns)
    if radar.empty:
        st.info("No material issues or spikes detected in the current filtered timeframe.")
    else:
        top_alerts = radar.head(4)
        st.markdown(
            "<div class='metric-grid'>"
            + "".join(
                [metric_card_html(str(r["Type"]), str(r["Issue"]), str(r["Narrative"])) for _, r in top_alerts.iterrows()]
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        with st.expander("See full radar table", expanded=False):
            st.dataframe(radar, use_container_width=True, hide_index=True)

    st.markdown("## 🧠 Language that separates low-star vs high-star reviews")
    theme_diff = compute_text_theme_diffs(filtered, top_n=12)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**More associated with 1–2★ reviews**")
        if theme_diff.get("low_vs_high"):
            st.dataframe(pd.DataFrame(theme_diff["low_vs_high"]), use_container_width=True, hide_index=True)
        else:
            st.info("Not enough low-star language to compare.")
    with c2:
        st.markdown("**More associated with 4–5★ reviews**")
        if theme_diff.get("high_vs_low"):
            st.dataframe(pd.DataFrame(theme_diff["high_vs_low"]), use_container_width=True, hide_index=True)
        else:
            st.info("Not enough high-star language to compare.")

    st.markdown("## 🩺 Symptom Explorer")
    detr_tbl = analyze_symptoms_fast(filtered, all_detractor_columns)
    del_tbl = analyze_symptoms_fast(filtered, all_delighter_columns)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Top detractors**")
        if detr_tbl.empty:
            st.info("No detractor symptoms in this slice.")
        else:
            st.dataframe(detr_tbl[["Item", "Review Mentions", "Review Rate %", "Avg Star"]].head(25), use_container_width=True, hide_index=True)
    with c2:
        st.markdown("**Top delighters**")
        if del_tbl.empty:
            st.info("No delighter symptoms in this slice.")
        else:
            st.dataframe(del_tbl[["Item", "Review Mentions", "Review Rate %", "Avg Star"]].head(25), use_container_width=True, hide_index=True)

    union_symptoms = detr_tbl["Item"].head(20).tolist() + [x for x in del_tbl["Item"].head(20).tolist() if x not in detr_tbl["Item"].head(20).tolist()]
    st.markdown("### 🔍 Evidence drawer")
    if union_symptoms:
        inspect_symptom = st.selectbox("Choose a symptom to inspect", options=union_symptoms, key="inspect_symptom")
        is_det = inspect_symptom in set(detr_tbl["Item"].tolist())
        quote_cols = all_detractor_columns if is_det else all_delighter_columns
        prefer = "low" if is_det else "high"
        quotes = pick_quotes_for_symptom(filtered, inspect_symptom, quote_cols, k=4, prefer=prefer)
        if quotes:
            for q in quotes:
                st.markdown(
                    f"<div class='quote-card'><div>{esc(q['text'])}</div><div class='small-muted' style='margin-top:6px;'>{esc(q['meta'])}</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No direct quotes found for that symptom in the current slice.")
    else:
        st.info("No symptoms available to inspect in the current slice.")

    st.markdown("## 🎯 Opportunity Matrix")
    baseline_avg = float(all_avg) if pd.notna(all_avg) else np.nan
    tab_det, tab_del = st.tabs(["Detractors", "Delighters"])

    def _opportunity_scatter(tbl: pd.DataFrame, kind: str, baseline: float):
        if tbl.empty or pd.isna(baseline):
            st.info("Not enough data to render this matrix.")
            return
        d = tbl.copy()
        d["Mentions"] = pd.to_numeric(d["Review Mentions"], errors="coerce").fillna(0)
        d["Avg Star"] = pd.to_numeric(d["Avg Star"], errors="coerce")
        d = d.dropna(subset=["Avg Star"])
        if d.empty:
            st.info("Not enough data to render this matrix.")
            return
        x = d["Mentions"].astype(float).to_numpy()
        y = d["Avg Star"].astype(float).to_numpy()
        if kind == "detractors":
            score = x * np.clip(baseline - y, 0, None)
        else:
            score = x * np.clip(y - baseline, 0, None)
        labels = d["Item"].astype(str).to_numpy()
        size = (np.sqrt(x) / (np.sqrt(np.nanmax(x)) + 1e-9)) * 30 + 10
        fig = go.Figure(
            go.Scatter(
                x=x,
                y=y,
                mode="markers+text",
                text=labels,
                textposition="top center",
                customdata=np.stack([labels], axis=1),
                hovertemplate="%{customdata[0]}<br>Mentions=%{x:.0f}<br>Avg ★=%{y:.2f}<extra></extra>",
                marker=dict(size=size, opacity=0.75, line=dict(width=1, color="rgba(148,163,184,.4)")),
            )
        )
        fig.add_hline(y=baseline, line_dash="dash", opacity=0.5)
        fig.update_layout(height=500, margin=dict(l=40, r=20, t=20, b=40), xaxis_title="Review mentions", yaxis_title="Avg ★")
        style_plotly(fig)
        st.plotly_chart(fig, use_container_width=True)
        d2 = d.copy()
        d2["Score"] = score
        st.dataframe(d2[["Item", "Review Mentions", "Review Rate %", "Avg Star", "Score"]].sort_values("Score", ascending=False).head(15), use_container_width=True, hide_index=True)

    with tab_det:
        _opportunity_scatter(detr_tbl.head(30), "detractors", baseline_avg)
    with tab_del:
        _opportunity_scatter(del_tbl.head(30), "delighters", baseline_avg)

    st.markdown("## 📈 Cumulative Avg ★ Over Time by Region")
    if {"Review Date", "Star Rating", "Country"}.issubset(filtered.columns):
        data = filtered.copy()
        data["Review Date"] = pd.to_datetime(data["Review Date"], errors="coerce")
        data["Star Rating"] = pd.to_numeric(data["Star Rating"], errors="coerce")
        data = data.dropna(subset=["Review Date", "Star Rating"])
        if data.empty:
            st.info("No time-series data in the current slice.")
        else:
            top_regions = data["Country"].astype("string").value_counts().head(6).index.tolist()
            data = data[data["Country"].astype("string").isin(top_regions)].copy()
            data["date"] = data["Review Date"].dt.date
            grp = data.groupby(["date", "Country"])["Star Rating"].agg(cnt="count", total="sum").reset_index()
            fig = go.Figure()
            y_vals: List[float] = []
            for region in top_regions:
                sub = grp[grp["Country"].astype("string") == str(region)].sort_values("date")
                sub["cum_cnt"] = sub["cnt"].cumsum()
                sub["cum_sum"] = sub["total"].cumsum()
                sub["cum_avg"] = sub["cum_sum"] / sub["cum_cnt"]
                y_vals.extend(sub["cum_avg"].tolist())
                fig.add_trace(
                    go.Scatter(
                        x=sub["date"],
                        y=sub["cum_avg"],
                        mode="lines",
                        name=str(region),
                        hovertemplate=f"Country: {region}<br>Date: %{{x}}<br>Cumulative Avg ★: %{{y:.3f}}<extra></extra>",
                    )
                )
            overall = grp.groupby("date").agg(cnt=("cnt", "sum"), total=("total", "sum")).reset_index().sort_values("date")
            overall["cum_cnt"] = overall["cnt"].cumsum()
            overall["cum_sum"] = overall["total"].cumsum()
            overall["cum_avg"] = overall["cum_sum"] / overall["cum_cnt"]
            y_vals.extend(overall["cum_avg"].tolist())
            fig.add_trace(go.Scatter(x=overall["date"], y=overall["cum_avg"], mode="lines", name="Overall", line=dict(width=4)))
            if y_vals:
                lo = max(1.0, float(np.nanpercentile(y_vals, 3)) - 0.12)
                hi = min(5.2, float(np.nanpercentile(y_vals, 97)) + 0.12)
                if (hi - lo) < 0.2:
                    hi = min(5.2, lo + 0.2)
            else:
                lo, hi = 1.0, 5.2
            fig.update_layout(height=520, margin=dict(l=40, r=20, t=20, b=40), xaxis_title="Date", yaxis_title="Cumulative Avg ★")
            fig.update_yaxes(range=[lo, hi], dtick=0.1 if (hi - lo) <= 1.2 else None)
            style_plotly(fig)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Need Review Date, Star Rating, and Country to render this chart.")

# -----------------------------------------------------------------------------
# Compare view
# -----------------------------------------------------------------------------
if view.startswith("⚖️"):
    st.markdown("## ⚖️ Compare any two cohorts")
    st.caption("Compare countries, retailers, models, journey stages, channels, signal types, or any extra column in the current filtered slice.")

    compare_fields = []
    for c in ["Country", "Source", "Model (SKU)", "Seeded", "New Review", "Signal Type"] + extra_filter_candidates:
        if c in filtered.columns:
            try:
                nuniq = int(filtered[c].astype("string").replace({"": pd.NA}).nunique(dropna=True))
            except Exception:
                nuniq = 0
            if nuniq >= 2:
                compare_fields.append(c)
    compare_fields = list(dict.fromkeys(compare_fields))
    if not compare_fields:
        st.info("No columns in the current slice have enough variation to compare cohorts.")
    else:
        c1, c2, c3 = st.columns([1.2, 1, 1])
        with c1:
            compare_field = st.selectbox("Compare field", options=compare_fields, key="cmp_field")
        options = _col_options(filtered, compare_field, max_vals=None)[1:]
        if len(options) < 2:
            st.info("Need at least two values in the selected field.")
        else:
            with c2:
                cohort_a = st.selectbox("Cohort A", options=options, index=0, key="cmp_a")
            with c3:
                default_b_idx = 1 if len(options) > 1 else 0
                cohort_b = st.selectbox("Cohort B", options=options, index=default_b_idx, key="cmp_b")
            a_mask = series_matches_any(filtered[compare_field], [cohort_a])
            b_mask = series_matches_any(filtered[compare_field], [cohort_b])
            df_a = filtered[a_mask].copy()
            df_b = filtered[b_mask].copy()
            if df_a.empty or df_b.empty:
                st.warning("One of the selected cohorts has no rows after filtering.")
            else:
                a_cnt, a_avg, a_low = section_stats(df_a)
                b_cnt, b_avg, b_low = section_stats(df_b)
                delta_avg = (a_avg - b_avg) if pd.notna(a_avg) and pd.notna(b_avg) else np.nan
                delta_low = (a_low - b_low) if pd.notna(a_low) and pd.notna(b_low) else np.nan
                st.markdown(
                    "<div class='metric-grid'>"
                    + metric_card_html(cohort_a, f"{a_cnt:,}", f"Avg ★ {a_avg:.2f} • 1–2★ {a_low:.1f}%")
                    + metric_card_html(cohort_b, f"{b_cnt:,}", f"Avg ★ {b_avg:.2f} • 1–2★ {b_low:.1f}%")
                    + metric_card_html("Delta Avg ★", f"{delta_avg:+.2f}" if pd.notna(delta_avg) else "n/a", f"{cohort_a} minus {cohort_b}")
                    + metric_card_html("Delta low-star share", f"{delta_low:+.1f} pp" if pd.notna(delta_low) else "n/a", f"{cohort_a} minus {cohort_b}")
                    + "</div>",
                    unsafe_allow_html=True,
                )

                st.markdown("### 🩺 Symptom deltas")
                tab1, tab2 = st.tabs(["Detractors", "Delighters"])
                with tab1:
                    det_delta = build_symptom_delta_table(df_a, df_b, all_detractor_columns, cohort_a, cohort_b)
                    if det_delta.empty:
                        st.info("No detractor differences available.")
                    else:
                        st.dataframe(det_delta.head(20), use_container_width=True, hide_index=True)
                with tab2:
                    del_delta = build_symptom_delta_table(df_a, df_b, all_delighter_columns, cohort_a, cohort_b)
                    if del_delta.empty:
                        st.info("No delighter differences available.")
                    else:
                        st.dataframe(del_delta.head(20), use_container_width=True, hide_index=True)

                st.markdown("### 🧠 Language unique to each cohort")
                text_diff = compute_group_text_diff(df_a, df_b, top_n=12)
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown(f"**More common in {cohort_a}**")
                    if text_diff.get("a_vs_b"):
                        st.dataframe(pd.DataFrame(text_diff["a_vs_b"]), use_container_width=True, hide_index=True)
                    else:
                        st.info("No clear differentiating language.")
                with cc2:
                    st.markdown(f"**More common in {cohort_b}**")
                    if text_diff.get("b_vs_a"):
                        st.dataframe(pd.DataFrame(text_diff["b_vs_a"]), use_container_width=True, hide_index=True)
                    else:
                        st.info("No clear differentiating language.")

                st.markdown("### 🔍 Evidence by differentiator")
                det_candidates = det_delta["Item"].head(15).tolist() if 'det_delta' in locals() and not det_delta.empty else []
                del_candidates = del_delta["Item"].head(15).tolist() if 'del_delta' in locals() and not del_delta.empty else []
                inspect_options = det_candidates + [x for x in del_candidates if x not in det_candidates]
                if inspect_options:
                    inspect_issue = st.selectbox("Inspect a differentiating symptom", options=inspect_options, key="cmp_issue")
                    is_det_issue = inspect_issue in set(det_candidates)
                    quote_cols = all_detractor_columns if is_det_issue else all_delighter_columns
                    qa = pick_quotes_for_symptom(df_a, inspect_issue, quote_cols, k=2, prefer="low" if is_det_issue else "high")
                    qb = pick_quotes_for_symptom(df_b, inspect_issue, quote_cols, k=2, prefer="low" if is_det_issue else "high")
                    q1, q2 = st.columns(2)
                    with q1:
                        st.markdown(f"**{cohort_a}**")
                        if qa:
                            for q in qa:
                                st.markdown(
                                    f"<div class='quote-card'><div>{esc(q['text'])}</div><div class='small-muted' style='margin-top:6px;'>{esc(q['meta'])}</div></div>",
                                    unsafe_allow_html=True,
                                )
                        else:
                            st.info("No direct quotes found.")
                    with q2:
                        st.markdown(f"**{cohort_b}**")
                        if qb:
                            for q in qb:
                                st.markdown(
                                    f"<div class='quote-card'><div>{esc(q['text'])}</div><div class='small-muted' style='margin-top:6px;'>{esc(q['meta'])}</div></div>",
                                    unsafe_allow_html=True,
                                )
                        else:
                            st.info("No direct quotes found.")
                else:
                    st.info("No differentiating symptoms available to inspect.")

                st.markdown("### 📦 Download compare pack")
                out_xlsx = BytesIO()
                with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
                    pd.DataFrame([{"Cohort A": cohort_a, "Count": a_cnt, "Avg ★": a_avg, "% 1–2★": a_low}, {"Cohort B": cohort_b, "Count": b_cnt, "Avg ★": b_avg, "% 1–2★": b_low}]).to_excel(writer, sheet_name="KPI Delta", index=False)
                    if 'det_delta' in locals() and not det_delta.empty:
                        det_delta.to_excel(writer, sheet_name="Detractor Delta", index=False)
                    if 'del_delta' in locals() and not del_delta.empty:
                        del_delta.to_excel(writer, sheet_name="Delighter Delta", index=False)
                    if text_diff.get("a_vs_b"):
                        pd.DataFrame(text_diff["a_vs_b"]).to_excel(writer, sheet_name="Language A vs B", index=False)
                    if text_diff.get("b_vs_a"):
                        pd.DataFrame(text_diff["b_vs_a"]).to_excel(writer, sheet_name="Language B vs A", index=False)
                st.download_button(
                    "⬇️ Download compare workbook",
                    out_xlsx.getvalue(),
                    file_name=f"compare_{_norm(compare_field)}_{_norm(str(cohort_a))}_vs_{_norm(str(cohort_b))}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

# -----------------------------------------------------------------------------
# Root cause + action center view
# -----------------------------------------------------------------------------
if view.startswith("🧠"):
    st.markdown("## 🧠 Root Cause Copilot + Action Center")
    st.caption("Pick a symptom or low-star cohort, rank the most likely drivers, simulate impact, and export ready-to-use action artifacts.")

    issue_mode = st.radio(
        "Issue target",
        options=["Detractor symptom", "1–2★ reviews", "Delighter symptom"],
        horizontal=True,
        key="rc_mode",
    )

    issue_name: Optional[str] = None
    if issue_mode == "Detractor symptom":
        if detractor_symptoms_all:
            issue_name = st.selectbox("Detractor symptom", options=detractor_symptoms_all, key="rc_issue_det")
        else:
            st.info("No detractor symptoms are available in the current slice.")
    elif issue_mode == "Delighter symptom":
        if delighter_symptoms_all:
            issue_name = st.selectbox("Delighter symptom", options=delighter_symptoms_all, key="rc_issue_del")
        else:
            st.info("No delighter symptoms are available in the current slice.")

    suggested_driver_cols = [c for c in ["Country", "Source", "Model (SKU)", "Signal Type", "Product_Symptom Component", "Trigger Point_Product", "Dominant Customer Journey Step", "L2 Delighter Component", "L2 Delighter Mode", "Brand", "Company"] if c in filtered.columns]
    driver_cols = st.multiselect(
        "Driver dimensions to inspect",
        options=[c for c in ["Country", "Source", "Model (SKU)", "Seeded", "New Review", "Signal Type"] + extra_filter_candidates if c in filtered.columns],
        default=suggested_driver_cols[:6] if suggested_driver_cols else [c for c in ["Country", "Source", "Model (SKU)"] if c in filtered.columns],
        key="rc_drivers",
    )
    min_group_n = st.slider("Minimum cohort size", 2, 30, 5, 1, key="rc_min_group")

    issue_mask = issue_mask_from_mode(filtered, issue_mode, issue_name, all_detractor_columns, all_delighter_columns)
    issue_label = issue_name if issue_name else issue_mode

    if filtered.empty:
        st.info("No rows in the current filtered slice.")
    elif int(issue_mask.sum()) == 0:
        st.info("The selected issue is not present in the current filtered slice.")
    else:
        driver_df = rank_drivers_for_issue(filtered, issue_mask, driver_cols, issue_mode, min_group_n=min_group_n)
        sim = estimate_issue_lift(filtered, issue_mask, issue_mode, reduction_pct=25)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(metric_card_html("Issue reviews", f"{int(issue_mask.sum()):,}", issue_label), unsafe_allow_html=True)
        with c2:
            st.markdown(metric_card_html("Issue share", f"{sim['issue_share_pct']:.1f}%", "Of current slice"), unsafe_allow_html=True)
        with c3:
            st.markdown(metric_card_html("Issue Avg ★", f"{sim['issue_avg']:.2f}" if pd.notna(sim['issue_avg']) else "n/a", "Among impacted reviews"), unsafe_allow_html=True)
        with c4:
            st.markdown(metric_card_html("Overall Avg ★", f"{sim['overall_avg']:.2f}" if pd.notna(sim['overall_avg']) else "n/a", "Current slice baseline"), unsafe_allow_html=True)

        st.markdown("### Ranked drivers")
        if driver_df.empty:
            st.info("No concentrated drivers detected with the selected settings.")
        else:
            fig = go.Figure(
                go.Bar(
                    x=driver_df.head(15)["Impact Score"][::-1],
                    y=(driver_df.head(15)["Driver"] + " = " + driver_df.head(15)["Bucket"])[::-1],
                    orientation="h",
                    customdata=np.stack([driver_df.head(15)["Issue Rate %"][::-1], driver_df.head(15)["Contribution %"][::-1]], axis=1),
                    hovertemplate="Impact Score=%{x:.2f}<br>Issue Rate=%{customdata[0]:.1f}%<br>Contribution=%{customdata[1]:.1f}%<extra></extra>",
                )
            )
            fig.update_layout(height=520, margin=dict(l=190, r=20, t=20, b=40), xaxis_title="Impact score", yaxis_title="Driver bucket")
            style_plotly(fig)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(driver_df, use_container_width=True, hide_index=True)

        st.markdown("### What-if simulator")
        reduction_pct = st.slider(
            "Assume you reduce this issue's prevalence by…",
            0,
            80,
            25,
            5,
            key="rc_reduction",
        )
        sim = estimate_issue_lift(filtered, issue_mask, issue_mode, reduction_pct=reduction_pct)
        direction = "rating lift" if issue_mode in {"Detractor symptom", "1–2★ reviews"} else "delighter amplification lift"
        st.markdown(
            f"<div class='soft-panel'><b>Directional estimate:</b> reducing <b>{esc(issue_label)}</b> by <b>{reduction_pct}%</b> is worth about <b>{sim['estimated_lift']:+.3f}★</b> of {direction} in this slice. <span class='small-muted'>Heuristic = issue share × rating gap × reduction rate.</span></div>",
            unsafe_allow_html=True,
        )

        st.markdown("### Evidence")
        quote_cols = all_detractor_columns if issue_mode == "Detractor symptom" else all_delighter_columns
        prefer = "low" if issue_mode in {"Detractor symptom", "1–2★ reviews"} else "high"
        quotes = []
        if issue_mode == "1–2★ reviews":
            sub = filtered.loc[issue_mask].copy()
            sub["Star Rating"] = pd.to_numeric(sub["Star Rating"], errors="coerce")
            sub = sub.sort_values("Star Rating", ascending=True)
            for _, row in sub.head(4).iterrows():
                text = mask_pii(clean_text(row.get("Verbatim", "")))
                sent = _extract_sentence(text, prefer_tail=True)
                meta = []
                for col in ["Source", "Country", "Model (SKU)", "Signal Type"]:
                    v = row.get(col, pd.NA)
                    if pd.notna(v) and str(v).strip():
                        meta.append(str(v).strip())
                try:
                    meta.insert(0, f"{int(float(row.get('Star Rating')))}★")
                except Exception:
                    pass
                quotes.append({"text": sent[:280] + ("…" if len(sent) > 280 else ""), "meta": " • ".join(meta)})
        else:
            quotes = pick_quotes_for_symptom(filtered, issue_label, quote_cols, k=4, prefer=prefer)
        if quotes:
            for q in quotes:
                st.markdown(
                    f"<div class='quote-card'><div>{esc(q['text'])}</div><div class='small-muted' style='margin-top:6px;'>{esc(q['meta'])}</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No direct quotes found for the selected issue in the current slice.")

        st.markdown("### Action Center")
        action_pack = build_action_center(filtered, str(issue_label), issue_mode, issue_mask, driver_df, quotes, product_label)
        at1, at2, at3, at4, at5 = st.tabs(["Executive summary", "Engineering brief", "Jira JSON", "Support macro", "Retailer note"])
        with at1:
            st.markdown(action_pack["executive_summary"])
            st.download_button("Download executive summary", action_pack["executive_summary"].encode("utf-8"), file_name=f"{_norm(str(issue_label))}_exec_summary.md")
        with at2:
            st.markdown(action_pack["engineering_brief"])
            st.download_button("Download engineering brief", action_pack["engineering_brief"].encode("utf-8"), file_name=f"{_norm(str(issue_label))}_engineering_brief.md")
        with at3:
            jira_text = json.dumps(action_pack["jira"], ensure_ascii=False, indent=2)
            st.code(jira_text, language="json")
            st.download_button("Download Jira payload", jira_text.encode("utf-8"), file_name=f"{_norm(str(issue_label))}_jira.json")
        with at4:
            st.text_area("Support macro", value=action_pack["support_macro"], height=180, key="rc_support_macro", label_visibility="collapsed")
            st.download_button("Download support macro", action_pack["support_macro"].encode("utf-8"), file_name=f"{_norm(str(issue_label))}_support_macro.txt")
        with at5:
            st.text_area("Retailer note", value=action_pack["retailer_note"], height=220, key="rc_retailer_note", label_visibility="collapsed")
            st.download_button("Download retailer note", action_pack["retailer_note"].encode("utf-8"), file_name=f"{_norm(str(issue_label))}_retailer_note.txt")

# -----------------------------------------------------------------------------
# Reviews view
# -----------------------------------------------------------------------------
if view.startswith("📝"):
    st.markdown("## 📝 Reviews in current filter criteria")
    st.caption("Change filters in the sidebar and this list updates automatically.")

    sort_options: List[str] = []
    has_date = "Review Date" in filtered.columns
    has_star = "Star Rating" in filtered.columns
    if has_date:
        sort_options += ["Newest → Oldest", "Oldest → Newest"]
    if has_star:
        sort_options += ["Rating: High → Low", "Rating: Low → High"]
    if not sort_options:
        sort_options = ["Default order"]

    c1, c2 = st.columns([1.2, 1])
    with c1:
        sort_choice = st.selectbox("Sort", options=sort_options, index=0, key="review_sort_choice")
    with c2:
        if not filtered.empty:
            st.download_button(
                "⬇️ Download current slice",
                filtered.to_csv(index=False).encode("utf-8-sig"),
                file_name="filtered_reviews.csv",
                mime="text/csv",
                use_container_width=True,
            )

    st.session_state.setdefault("review_page", 0)
    if st.session_state.get("_review_sort_prev") != sort_choice:
        st.session_state["_review_sort_prev"] = sort_choice
        st.session_state["review_page"] = 0

    reviews_df = filtered.copy()
    try:
        if sort_choice == "Newest → Oldest" and has_date:
            reviews_df = reviews_df.sort_values("Review Date", ascending=False, na_position="last")
        elif sort_choice == "Oldest → Newest" and has_date:
            reviews_df = reviews_df.sort_values("Review Date", ascending=True, na_position="last")
        elif sort_choice == "Rating: High → Low" and has_star:
            reviews_df = reviews_df.sort_values(["Star Rating", "Review Date"] if has_date else ["Star Rating"], ascending=[False, False] if has_date else [False], na_position="last")
        elif sort_choice == "Rating: Low → High" and has_star:
            reviews_df = reviews_df.sort_values(["Star Rating", "Review Date"] if has_date else ["Star Rating"], ascending=[True, False] if has_date else [True], na_position="last")
    except Exception:
        pass

    reviews_per_page = int(st.session_state.get("rpp", 10))
    total_reviews_count = len(reviews_df)
    total_pages = max(1, int(np.ceil(total_reviews_count / reviews_per_page))) if reviews_per_page > 0 else 1
    current_page = max(0, min(int(st.session_state.get("review_page", 0)), total_pages - 1))
    st.session_state["review_page"] = current_page
    start_index = current_page * reviews_per_page
    end_index = start_index + reviews_per_page
    paginated = reviews_df.iloc[start_index:end_index]

    if paginated.empty:
        st.warning("No reviews match the selected criteria.")
    else:
        for _, row in paginated.iterrows():
            review_text = clean_text(row.get("Verbatim", ""))
            display_review_html = highlight_html(review_text, keyword)
            try:
                star_int = int(float(row.get("Star Rating", 0))) if pd.notna(row.get("Star Rating")) else 0
            except Exception:
                star_int = 0
            try:
                date_str = pd.to_datetime(row.get("Review Date")).strftime("%Y-%m-%d") if pd.notna(row.get("Review Date")) else "-"
            except Exception:
                date_str = "-"

            def chips(row_i: pd.Series, columns: List[str], css_class: str) -> str:
                items = []
                for c in columns:
                    if c not in row_i.index:
                        continue
                    val = row_i.get(c, pd.NA)
                    if pd.isna(val):
                        continue
                    s = str(val).strip()
                    if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}:
                        continue
                    items.append(f'<span class="pill" style="border-color:transparent;">{_html.escape(s)}</span>')
                return " ".join(items) if items else "<span class='small-muted'>None</span>"

            st.markdown(
                f"""
                <div class='soft-panel'>
                  <div><b>Source:</b> {esc(row.get('Source'))} &nbsp;|&nbsp; <b>Model:</b> {esc(row.get('Model (SKU)'))} &nbsp;|&nbsp; <b>Country:</b> {esc(row.get('Country'))}</div>
                  <div style='margin-top:6px;'><b>Date:</b> {esc(date_str)} &nbsp;|&nbsp; <b>Rating:</b> {'⭐' * star_int} ({esc(row.get('Star Rating'))}/5) &nbsp;|&nbsp; <b>Signal:</b> {esc(row.get('Signal Type'))}</div>
                  <div style='margin-top:10px;'><b>Review:</b> {display_review_html}</div>
                  <div style='margin-top:10px;'><b>Delighters:</b> {chips(row, all_delighter_columns, 'pos')}</div>
                  <div style='margin-top:8px;'><b>Detractors:</b> {chips(row, all_detractor_columns, 'neg')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    p1, p2, p3, p4, p5 = st.columns([1, 1, 2, 1, 1])
    with p1:
        if st.button("⏮ First", disabled=current_page == 0):
            st.session_state["review_page"] = 0
            st.rerun()
    with p2:
        if st.button("⬅ Prev", disabled=current_page == 0):
            st.session_state["review_page"] = max(current_page - 1, 0)
            st.rerun()
    with p3:
        showing_from = 0 if total_reviews_count == 0 else start_index + 1
        showing_to = min(end_index, total_reviews_count)
        st.markdown(f"<div style='text-align:center;font-weight:700;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>", unsafe_allow_html=True)
    with p4:
        if st.button("Next ➡", disabled=current_page >= total_pages - 1):
            st.session_state["review_page"] = min(current_page + 1, total_pages - 1)
            st.rerun()
    with p5:
        if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
            st.session_state["review_page"] = total_pages - 1
            st.rerun()


# -----------------------------------------------------------------------------
# AI view
# -----------------------------------------------------------------------------
if view.startswith("🤖"):
    st.markdown("## 🤖 AI Analyst")
    st.caption("Ask by text or voice. Answers are grounded in the currently filtered slice, including issue radar, top symptoms, and retrieved evidence.")

    with st.sidebar.expander("🤖 AI Settings", expanded=False):
        st.session_state.setdefault("ai_model", "gpt-4o-mini")
        st.session_state.setdefault("ai_temp", 0.2)
        st.session_state.setdefault("ai_quote_k", 8)
        st.session_state.setdefault("ai_cap", 1800)
        st.selectbox("Model", options=["gpt-4o-mini", "gpt-4o", "gpt-4.1"], key="ai_model")
        st.slider("Creativity", 0.0, 1.0, float(st.session_state.get("ai_temp", 0.2)), 0.1, key="ai_temp")
        st.slider("Retrieved quotes", 4, 18, int(st.session_state.get("ai_quote_k", 8)), 1, key="ai_quote_k")
        st.number_input("Max reviews in retrieval corpus", 200, 8000, int(st.session_state.get("ai_cap", 1800)), 100, key="ai_cap")

    with st.sidebar.expander("🔑 OpenAI API Key", expanded=False):
        st.text_input("OPENAI_API_KEY override", value="", type="password", key="api_key_override")
        st.caption("Uses override → secrets → env. Key is never displayed.")

    api_key_override = (st.session_state.get("api_key_override") or "").strip()
    api_key = api_key_override
    if not api_key:
        try:
            api_key = st.secrets["OPENAI_API_KEY"]
        except Exception:
            api_key = os.getenv("OPENAI_API_KEY")
    remote_ready = bool(api_key)
    model = str(st.session_state.get("ai_model") or "gpt-4o-mini")
    temp = float(st.session_state.get("ai_temp") or 0.2)
    quote_k = int(st.session_state.get("ai_quote_k", 8))

    st.markdown(
        f"<div class='soft-panel'><b>{'🟢 Remote AI ready' if remote_ready else '🟡 Remote AI disabled — local grounded insights still available'}</b><div class='small-muted' style='margin-top:6px;'>Model: {esc(model)}</div></div>",
        unsafe_allow_html=True,
    )

    st.session_state.setdefault("_ai_last_q", "")
    st.session_state.setdefault("_ai_last_answer", "")
    st.session_state.setdefault("ai_user_q", "")

    q1, q2, q3, q4 = st.columns(4)
    if q1.button("🧾 Executive summary", use_container_width=True):
        st.session_state["ai_user_q"] = "Give me an executive summary of what consumers love, what is breaking, and what engineering should do next in this filtered slice."
    if q2.button("🚨 What changed recently?", use_container_width=True):
        st.session_state["ai_user_q"] = "What changed recently? Use the issue radar and recent review language to explain emerging issues in this filtered slice."
    if q3.button("⚖️ Where are cohort differences?", use_container_width=True):
        st.session_state["ai_user_q"] = "What are the biggest cohort differences I should investigate in this filtered slice?"
    if q4.button("✉️ Draft an escalation", use_container_width=True):
        st.session_state["ai_user_q"] = "Draft a retailer escalation note and an engineering brief based on the most important issue in this filtered slice."

    voice_audio = st.audio_input("Optional voice question")
    if voice_audio is not None:
        cva, cvb = st.columns([0.22, 0.78])
        with cva:
            transcribe_clicked = st.button("🎙️ Transcribe")
        with cvb:
            st.caption("Use your mic to speak a question and transcribe it into the prompt box.")
        if transcribe_clicked:
            if not remote_ready:
                st.warning("Add an OpenAI API key to transcribe voice questions.")
            else:
                with st.spinner("Transcribing voice question…"):
                    try:
                        transcript = transcribe_audio_http(api_key, voice_audio)
                        if transcript:
                            st.session_state["ai_user_q"] = transcript
                            st.success("Voice question transcribed into the prompt box.")
                        else:
                            st.warning("The transcript came back empty.")
                    except Exception as e:
                        st.warning(f"Transcription failed: {e}")

    user_q = st.text_area(
        "Your question",
        value=st.session_state.get("ai_user_q", ""),
        height=140,
        key="ai_user_q",
        placeholder="E.g., Why is suction driving low ratings in Germany? What changed in the last month? Draft an engineering brief.",
    )

    if st.session_state.get("_ai_last_answer"):
        st.markdown("### Latest answer")
        if st.session_state.get("_ai_last_q"):
            st.markdown(f"**Q:** {esc(st.session_state.get('_ai_last_q'))}")
        st.markdown(st.session_state.get("_ai_last_answer"))

    send = st.button("➡️ Send", type="primary")
    if send:
        q = (user_q or "").strip()
        if not q:
            st.warning("Type a question first.")
        else:
            knowledge = build_knowledge_pack(filtered, source_label, all_detractor_columns, all_delighter_columns)
            quotes = retrieve_quotes(q, filtered, k=quote_k)
            sys_prompt = (
                "You are a SharkNinja consumer-insights copilot. Ground every answer in the dataset context and retrieved evidence. "
                "Quantify with counts, Avg ★, low-star rates, and mention rates where possible. "
                "When evidence supports a claim, cite quote IDs like [Q3]. Never invent data."
            )
            user_payload = {
                "question": q,
                "dataset_context": knowledge,
                "retrieved_evidence": [
                    {"id": it["id"], "star": it.get("star"), "country": it.get("country"), "source": it.get("source"), "signal_type": it.get("signal_type"), "date": it.get("date"), "text": it.get("text")}
                    for it in quotes[:quote_k]
                ],
            }
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            with st.spinner("Thinking…"):
                if remote_ready:
                    try:
                        ans = openai_chat_http(api_key, model, messages, temperature=temp)
                    except Exception as e:
                        st.warning("Remote AI failed; falling back to local grounded summary.")
                        st.caption(f"Last AI error: {str(e)[:220]}")
                        ans = local_ai_answer(q, knowledge, quotes)
                else:
                    ans = local_ai_answer(q, knowledge, quotes)
            st.session_state["_ai_last_q"] = q
            st.session_state["_ai_last_answer"] = ans
            st.rerun()



