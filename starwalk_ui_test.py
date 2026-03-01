# starwalk_master_app_v16.py
# Streamlit master app:
# - Accepts either:
#   1) Star Walk scrubbed verbatims Excel/CSV (same behavior as starwalk_ui_test.py), OR
#   2) JSON export -> auto-convert to Star Walk scrubbed verbatims -> dashboard
#
# Improvements in v4:
# - Saved Views / Shareable Filter Presets (export/import + URL param)
# - Major performance fix: symptom analysis is vectorized (no per-symptom row scans)
# - Removed redundant monthly chart (kept cumulative weighted chart)
# - Country × Source breakdown now unifies Avg ★ (color) + Count (labels)
# - Dual-theme CSS (light + dark) to prevent white-on-white / mixed-theme first load

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
import threading
import time
import zlib
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit.components.v1 import html as st_html
from io import BytesIO

# ---------- Optional text fixer ----------
try:
    from ftfy import fix_text as _ftfy_fix  # type: ignore
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None  # type: ignore

# ---------- OpenAI SDK ----------
try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Optional FAISS for fast vector similarity
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

# ---------- Local semantic search (fast, offline) ----------
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

try:
    from rank_bm25 import BM25Okapi  # type: ignore
    _HAS_BM25 = True
except Exception:
    _HAS_BM25 = False

try:
    from sentence_transformers import CrossEncoder  # type: ignore
    _HAS_RERANKER = True
except Exception:
    _HAS_RERANKER = False

APP_VERSION = "2026-03-01-master-v19"

STARWALK_SHEET_NAME = "Star Walk scrubbed verbatims"

NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}


def model_supports_temperature(model_id: str) -> bool:
    if not model_id:
        return True
    if model_id in NO_TEMP_MODELS:
        return False
    return not model_id.startswith("gpt-5")


# Timezone
try:
    from zoneinfo import ZoneInfo  # py3.9+
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = None


# ---------- Page config ----------
st.set_page_config(layout="wide", page_title="Star Walk — Master Dashboard")

# ---------- Plotly helpers (theme-agnostic + no fragile detection) ----------
# Streamlit theme can be toggled client-side (Light/Dark/System) and Python-side
# st.get_option("theme.base") does NOT reliably reflect the current UI theme.
#
# To avoid white-on-white (or black-on-black) charts, we:
# - Use transparent plot/paper backgrounds.
# - Use CSS keyword `currentColor` for text, so the chart inherits the surrounding
#   text color (works in light + dark automatically).
PLOTLY_TEMPLATE = "plotly"
PLOTLY_GRIDCOLOR = "rgba(148,163,184,0.25)"


def style_plotly(fig: go.Figure) -> go.Figure:
    """Apply a readable, theme-agnostic Plotly style."""
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

# ---------- Global CSS (always readable in light) ----------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
  *, ::before, ::after { box-sizing: border-box; }

  /*
    Theme system (v19):
    - DO NOT guess light/dark.
    - DO NOT override Streamlit's theme.
    - Instead, derive our tokens from Streamlit's live CSS variables.

    This prevents the failure mode you saw: "Light mode" UI with a dark background.
  */

  :root{
    color-scheme: light dark;

    /* Streamlit tokens (with safe fallbacks) */
    --st-bg: var(--background-color, #ffffff);
    --st-card: var(--secondary-background-color, #f6f8fc);
    --st-text: var(--text-color, #0f172a);
    --st-primary: var(--primary-color, #3b82f6);

    /* App tokens derived from Streamlit (with fallbacks for older browsers) */
    --text: var(--st-text);
    --bg-app: var(--st-bg);
    --bg-card: var(--st-card);

    /* Safe fallbacks (overridden below when color-mix is supported) */
    --bg-tile: var(--st-card);
    --border-soft: rgba(148,163,184,0.22);
    --border: rgba(148,163,184,0.32);
    --border-strong: rgba(148,163,184,0.45);
    --muted: rgba(71,85,105,0.95);
    --muted-2: rgba(100,116,139,0.95);

    --ring: var(--st-primary);
    --ok:#16a34a; --bad:#dc2626;

    --shadow: rgba(0,0,0,0.10);
    --shadow-lg: rgba(0,0,0,0.18);

    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }

  /* Modern browsers: derive subtle tints from the live Streamlit theme */
  @supports (color: color-mix(in srgb, white 50%, black)) {
    :root{
      --bg-tile: color-mix(in srgb, var(--st-card) 70%, var(--st-bg) 30%);
      --border-soft: color-mix(in srgb, var(--st-text) 10%, transparent);
      --border: color-mix(in srgb, var(--st-text) 16%, transparent);
      --border-strong: color-mix(in srgb, var(--st-text) 22%, transparent);
      --muted: color-mix(in srgb, var(--st-text) 78%, transparent);
      --muted-2: color-mix(in srgb, var(--st-text) 66%, transparent);
    }
  }

  /* Ensure the overall page uses Streamlit's current theme colors */
  html, body, .stApp {
    background: var(--bg-app) !important;
    color: var(--text) !important;
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
  }

  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  section[data-testid="stSidebar"] .block-container { padding-top:.6rem; }

  mark{ background:rgba(253,224,71,0.55); padding:0 .2em; border-radius:3px; }

  .soft-panel{
    background:var(--bg-card);
    border-radius:14px;
    padding:14px 16px;
    box-shadow:0 0 0 1.2px var(--border-strong), 0 10px 18px var(--shadow);
    margin:10px 0 14px;
  }

  .small-muted{ color:var(--muted); font-size:.9rem; }

  /* File uploader */
  [data-testid="stFileUploadDropzone"]{
    border-radius:14px !important;
    border:1.8px dashed var(--border-strong) !important;
    background:var(--bg-card) !important;
    box-shadow:0 0 0 1px var(--border-soft) inset;
  }
  [data-testid="stFileUploadDropzone"] *{ color:var(--text) !important; }
  [data-testid="stFileUploadDropzone"] button{
    background:var(--bg-tile) !important;
    border:1.2px solid var(--border) !important;
    color:var(--text) !important;
    border-radius:10px !important;
  }

  /* --- Metrics cards (cleaner + responsive; prevents small-screen overflow) --- */
  .metrics-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));
    gap:17px;
  }
  .metric-card{
    background:var(--bg-card);
    border-radius:16px;
    padding:16px;
    box-shadow:0 0 0 1.6px var(--border-strong), 0 14px 22px var(--shadow);
    color:var(--text);
    min-width:0;
    position:relative;
    overflow:hidden;
  }
  .metric-card::before{
    content:"";
    position:absolute;
    inset:-2px;
    /* Subtle accent that works in both themes */
    background: radial-gradient(1200px 220px at 20% -20%, rgba(59,130,246,0.18), rgba(0,0,0,0)),
                radial-gradient(1200px 220px at 80% 120%, rgba(34,197,94,0.12), rgba(0,0,0,0));
    pointer-events:none;
  }
  .metric-head{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:10px; }
  .metric-title{ font-weight:900; font-size:1.05rem; color:var(--text); }
  .metric-sub{ color:var(--muted); font-size:0.9rem; font-weight:650; }

  .metric-row{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
    gap:12px;
    align-items:stretch;
    position:relative;
    z-index:1;
  }

  .metric-box{
    background:var(--bg-tile);
    border:1.5px solid var(--border);
    border-radius:14px;
    padding:12px 10px;
    text-align:center;
    color:var(--text);
    min-width:0;
    overflow:hidden;
  }

  .metric-label{
    color:var(--muted);
    font-size:clamp(0.78rem, 1.0vw, 0.86rem);
    line-height:1.15;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }

  .metric-kpi{
    font-weight:900;
    font-size:clamp(1.12rem, 2.35vw, 1.85rem);
    letter-spacing:-0.01em;
    margin-top:2px;
    color:var(--text);
    line-height:1.05;
    white-space:nowrap;
    font-variant-numeric: tabular-nums;
  }

  .mini-bar{
    height:8px;
    border-radius:999px;
    background:rgba(148,163,184,0.22);
    overflow:hidden;
    margin-top:8px;
  }
  .mini-bar > div{
    height:100%;
    border-radius:999px;
    background:rgba(248,113,113,0.9);
    width:0%;
  }

  @media (max-width: 520px){
    .metric-row{ grid-template-columns:1fr; }
    .metric-box{
      text-align:left;
      display:flex;
      align-items:baseline;
      justify-content:space-between;
      gap:10px;
    }
    .metric-kpi{ margin-top:0; }
    .metric-label{ white-space:normal; }
  }

  /* Sticky top navigation (ONLY the main View radio) */
  .sticky-topnav-host{
    position: sticky;
    top: calc(var(--stHeaderH, 0px) + 6px);
    z-index: 999;
    margin: 6px 0 12px;
    padding: 6px 0;
    background: linear-gradient(to bottom, var(--bg-app) 65%, rgba(0,0,0,0) 100%);
    backdrop-filter: blur(6px);
  }
  .sticky-topnav-host [data-testid="stRadio"]{ display:flex; justify-content:center; }
  .sticky-topnav-host [role="radiogroup"]{
    display:flex;
    flex-direction: row;
    gap:10px;
    padding:10px;
    border-radius:999px;
    background:var(--bg-card);
    box-shadow:0 0 0 1.2px var(--border-strong), 0 14px 22px var(--shadow-lg);
    align-items:center;
  }
  .sticky-topnav-host label[data-baseweb="radio"]{
    margin:0 !important;
    border-radius:999px !important;
    border:1.2px solid var(--border) !important;
    background:var(--bg-tile) !important;
    padding:8px 14px !important;
    font-weight:900 !important;
    color:var(--text) !important;
  }
  .sticky-topnav-host label[data-baseweb="radio"]:hover{ border-color: var(--border-strong) !important; }
  .sticky-topnav-host label[data-baseweb="radio"]:has(input:checked){
    background: rgba(96,165,250,0.16) !important;
    border-color: rgba(96,165,250,0.65) !important;
    box-shadow: 0 0 0 3px rgba(96,165,250,0.14);
  }

  .review-card{
    background:var(--bg-card);
    border-radius:14px;
    padding:16px;
    margin:16px 0 24px;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 14px 22px var(--shadow);
    color:var(--text);
  }
  .review-card p{ margin:.25rem 0; line-height:1.55; }
  .badges{ display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
  .badge{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:700; font-size:.85rem; border:1.4px solid transparent; }
  .badge.pos{ background:rgba(34,197,94,0.14); border-color:rgba(34,197,94,0.35); color:rgba(34,197,94,0.95); }
  .badge.neg{ background:rgba(248,113,113,0.14); border-color:rgba(248,113,113,0.35); color:rgba(248,113,113,0.95); }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }

  .kpi-pill{
    display:inline-block; padding:4px 10px; border-radius:999px;
    border:1.3px solid var(--border);
    background:var(--bg-tile);
    font-weight:750; margin-right:8px;
    color:var(--text);
  }

  .pill-row{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .pill{
    display:inline-flex; align-items:center; gap:8px;
    padding:5px 10px; border-radius:999px;
    border:1.2px solid var(--border);
    background:var(--bg-card);
    font-size:.88rem; font-weight:750;
    color:var(--text);
  }
  .pill .muted{ color:var(--muted); font-weight:650; }

  .section-title{ margin-top: 10px; font-weight: 900; font-size: 1.1rem; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


# ============================================================
# Utilities
# ============================================================
_BAD_CHARS_REGEX = re.compile(r"[ÃÂâï€™]")
PII_PAT = re.compile(r"[\w\.-]+@[\w\.-]+|\+?\d[\d\-\s]{6,}\d")


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


def _mask_pii(s: str) -> str:
    try:
        return PII_PAT.sub("[redacted]", s or "")
    except Exception:
        return s or ""


def esc(x) -> str:
    return _html.escape("" if pd.isna(x) else str(x))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def is_valid_symptom_value(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>", "NA", "N/A", "NULL", "NONE"}:
        return False
    return not bool(re.fullmatch(r"[\W_]+", s))


def collect_unique_symptoms(df: pd.DataFrame, cols: list[str]) -> list[str]:
    vals, seen = [], set()
    for c in cols:
        if c in df.columns:
            s = df[c].astype("string").str.strip()
            s = s[s.map(is_valid_symptom_value)]
            for v in pd.unique(s.to_numpy()):
                v = str(v).strip()
                if v and v not in seen:
                    seen.add(v)
                    vals.append(v)
    return vals


def highlight_html(text: str, keyword: str | None) -> str:
    safe = _html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error:
            pass
    return safe


def _infer_product_label(df_in: pd.DataFrame, fallback_filename: str) -> str:
    base = os.path.splitext(fallback_filename or "Uploaded File")[0]
    if "Model (SKU)" in df_in.columns:
        s = df_in["Model (SKU)"].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if not s.empty:
            top = s.value_counts().head(3).index.tolist()
            if len(top) == 1:
                return str(top[0])
            return " / ".join([str(x) for x in top])
    return base


_BASIC_STOP = {
    "the","and","for","with","this","that","have","has","had","was","were","are","but","not","you","your","i","me","my",
    "we","our","they","them","their","it","its","a","an","to","of","in","on","at","as","is","be","been","so","if",
    "very","really","just","from","or","by","about","after","before","when","while","can","could","would","should","will",
    "did","does","do","than","then","there","here","also","too","more","most","less","much","many","one","two","three",
}


def infer_product_profile(df_in: pd.DataFrame, fallback_filename: str) -> dict:
    """Best-effort product identity from the dataset itself (no web calls).

    This powers the AI's "product knowledge" without relying on external lookups.
    """
    prof: dict = {"product_guess": _infer_product_label(df_in, fallback_filename)}

    def _top_vals(col: str, k: int = 5) -> list[str]:
        if col not in df_in.columns:
            return []
        s = df_in[col].astype("string").str.strip().replace({"": pd.NA}).dropna()
        if s.empty:
            return []
        return [str(x) for x in s.value_counts().head(k).index.tolist()]

    for c in ["Product Name", "Product Category", "Brand", "Company", "Model (SKU)"]:
        top = _top_vals(c, 5)
        if top:
            prof[f"top_{c.lower().replace(' ', '_')}"] = top

    # Keywords + bigrams from verbatims (presence per review, so long reviews don't dominate)
    if "Verbatim" in df_in.columns:
        txt = df_in["Verbatim"].astype("string").fillna("")
        uni = Counter()
        bi = Counter()
        max_n = min(len(txt), 7000)

        for t in txt.head(max_n).tolist():
            t = clean_text(t)
            words = re.findall(r"[a-zA-Z]{3,}", t.lower())
            words = [w for w in words if w not in _BASIC_STOP]
            if not words:
                continue
            uni.update(set(words))
            if len(words) >= 2:
                bi.update(set(" ".join(p) for p in zip(words, words[1:])))

        if uni:
            prof["top_keywords"] = [w for w, _ in uni.most_common(18)]
        if bi:
            prof["top_bigrams"] = [w for w, _ in bi.most_common(18)]

    # Heuristic SharkNinja-friendly "product family" guess (best-effort)
    fam_rules = {
        "Floorcare / Vacuum": [
            "vacuum", "suction", "cordless", "carpet", "floor", "dust", "brush", "roller", "mop", "shark", "robot",
        ],
        "Kitchen / Air Fryer / Oven": [
            "air fryer", "airfryer", "crisp", "basket", "oven", "roast", "bake", "preheat",
        ],
        "Kitchen / Blender": [
            "blender", "smoothie", "pitcher", "blend", "ice", "nutri", "nutribullet",
        ],
        "Kitchen / Coffee": [
            "coffee", "espresso", "brew", "frother", "pod", "carafe",
        ],
        "Kitchen / Ice Cream (Creami)": [
            "creami", "gelato", "sorbet", "ice cream",
        ],
        "Beauty / Hair": [
            "hair", "dryer", "blow dry", "styler", "curl", "frizz", "brush",
        ],
    }

    blob = " ".join(
        [str(prof.get("product_guess", ""))]
        + (prof.get("top_keywords") or [])
        + (prof.get("top_bigrams") or [])
        + (prof.get("top_model_(sku)") or [])
    ).lower()

    best_fam = None
    best_score = 0
    best_hits: list[str] = []
    for fam, terms in fam_rules.items():
        hits = [t for t in terms if t in blob]
        score = len(hits)
        if score > best_score:
            best_score = score
            best_fam = fam
            best_hits = hits

    if best_fam and best_score > 0:
        # Rough confidence: increases with number of matched signals, capped
        prof["product_family_guess"] = best_fam
        prof["product_family_confidence"] = round(min(0.95, 0.30 + 0.15 * best_score), 2)
        prof["product_family_signals"] = best_hits[:10]

    return prof


def compute_text_theme_diffs(df_in: pd.DataFrame, max_reviews: int = 5000, top_n: int = 18) -> dict:
    """Text themes that differentiate low-star (<=2★) vs high-star (>=4★) reviews.

    Uses "presence per review" (not raw word counts) so long reviews don't dominate.
    Returns small, JSON-serializable dict for the AI context pack.
    """
    out: dict = {
        "n_low_reviews": 0,
        "n_high_reviews": 0,
        "low_terms": [],
        "high_terms": [],
        "low_vs_high": [],
        "high_vs_low": [],
    }
    if df_in is None or df_in.empty:
        return out
    if "Verbatim" not in df_in.columns or "Star Rating" not in df_in.columns:
        return out

    d = df_in[["Verbatim", "Star Rating"]].copy()
    d["star"] = pd.to_numeric(d["Star Rating"], errors="coerce")
    d = d.dropna(subset=["star"])
    if d.empty:
        return out

    low = d.loc[d["star"] <= 2].head(int(max_reviews))
    high = d.loc[d["star"] >= 4].head(int(max_reviews))

    def _presence_counts(df_part: pd.DataFrame) -> tuple[Counter, int]:
        c = Counter()
        texts = df_part["Verbatim"].astype("string").fillna("").tolist()
        for t in texts:
            t = clean_text(t)
            words = re.findall(r"[a-zA-Z]{3,}", t.lower())
            words = [w for w in words if w not in _BASIC_STOP]
            if not words:
                continue

            # Unigrams
            c.update(set(words))

            # Bigrams (only keep if there is enough signal)
            if len(words) >= 2:
                bigs = set(" ".join(p) for p in zip(words, words[1:]))
                # Avoid noisy bigrams like "very good" by filtering stopwords already; keep the rest.
                c.update(bigs)

        return c, len(texts)

    low_c, low_n = _presence_counts(low)
    high_c, high_n = _presence_counts(high)
    out["n_low_reviews"] = int(low_n)
    out["n_high_reviews"] = int(high_n)

    def _top_list(counter: Counter, n_reviews: int) -> list[dict]:
        if not counter or n_reviews <= 0:
            return []
        rows = []
        for term, cnt in counter.most_common(int(top_n)):
            rows.append(
                {
                    "term": term,
                    "reviews": int(cnt),
                    "rate_pct": round((cnt / max(1, n_reviews)) * 100, 1),
                }
            )
        return rows

    out["low_terms"] = _top_list(low_c, low_n)
    out["high_terms"] = _top_list(high_c, high_n)

    # Differential (delta in review-mention rate)
    terms = set(list(low_c.keys())[:2500]) | set(list(high_c.keys())[:2500])
    diffs = []
    for term in terms:
        lr = low_c.get(term, 0) / max(1, low_n)
        hr = high_c.get(term, 0) / max(1, high_n)
        diffs.append((term, lr - hr, lr, hr))

    diffs.sort(key=lambda x: x[1], reverse=True)
    out["low_vs_high"] = [
        {
            "term": t,
            "delta_pp": round(delta * 100, 1),
            "low_rate_pct": round(lr * 100, 1),
            "high_rate_pct": round(hr * 100, 1),
        }
        for t, delta, lr, hr in diffs[: int(top_n)]
        if abs(delta) > 0
    ]

    diffs.sort(key=lambda x: x[1])  # most negative => high > low
    out["high_vs_low"] = [
        {
            "term": t,
            "delta_pp": round((-delta) * 100, 1),
            "high_rate_pct": round(hr * 100, 1),
            "low_rate_pct": round(lr * 100, 1),
        }
        for t, delta, lr, hr in diffs[: int(top_n)]
        if abs(delta) > 0
    ]
    return out



def _extract_sentence(text: str, keyword: str | None = None, prefer_tail: bool = False) -> str:
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


def _pick_quotes_for_symptom(df_in: pd.DataFrame, symptom: str, cols: list[str], k: int = 2, prefer: str = "low"):
    """Pick up to k short evidence snippets for a symptom.

    Robustness improvements:
    - case-insensitive symptom matching (prevents missed matches due to casing)
    - tolerates blank/NA cells
    """
    if not cols or not symptom:
        return []
    if "Star Rating" not in df_in.columns:
        return []

    sym = str(symptom).strip()
    if not sym:
        return []
    sym_l = sym.lower()

    # Case-insensitive match across symptom columns
    mask = pd.Series(False, index=df_in.index)
    for c in cols:
        if c not in df_in.columns:
            continue
        s = df_in[c].astype("string").fillna("").str.strip().str.lower()
        mask |= s.eq(sym_l)

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
        txt = clean_text(row.get("Verbatim", ""))
        txt = _mask_pii(txt)
        sent = _extract_sentence(txt, keyword=sym, prefer_tail=(prefer == "low"))
        if len(sent) > 280:
            sent = sent[:277] + "…"
        meta = []
        try:
            meta.append(f"{int(row.get('Star Rating'))}★")
        except Exception:
            pass
        for c in ["Source", "Country", "Model (SKU)"]:
            if c in df_in.columns:
                v = row.get(c, pd.NA)
                if pd.notna(v) and str(v).strip():
                    meta.append(str(v).strip())
        if "Review Date" in df_in.columns:
            dv = row.get("Review Date", pd.NaT)
            if pd.notna(dv):
                try:
                    meta.append(pd.to_datetime(dv).strftime("%Y-%m-%d"))
                except Exception:
                    pass
        out.append({"text": sent, "meta": " • ".join(meta) if meta else ""})
    return out



def _detect_trends(df_in: pd.DataFrame, symptom_cols: list[str], min_mentions: int = 3):
    if "Review Date" not in df_in.columns or "Star Rating" not in df_in.columns:
        return []
    d = df_in.copy()
    d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
    d = d.dropna(subset=["Review Date"])
    if d.empty:
        return []

    last_date = d["Review Date"].max()
    last_m = pd.Period(last_date, freq="M")
    prev_m = last_m - 1

    last = d[d["Review Date"].dt.to_period("M") == last_m]
    prev = d[d["Review Date"].dt.to_period("M") == prev_m]

    out = []
    if len(prev) > 0:
        pct = ((len(last) - len(prev)) / max(1, len(prev))) * 100
        if (len(last) - len(prev)) >= 5 and pct >= 50:
            out.append(f"Review volume increased from {len(prev)} → {len(last)} in {last_m.strftime('%b %Y')} (↑{pct:.0f}%).")
    elif len(last) >= 5:
        out.append(f"Review volume jumped to {len(last)} in {last_m.strftime('%b %Y')} (from 0 in prior month).")

    cols = [c for c in symptom_cols if c in d.columns]
    if cols and (not last.empty) and (not prev.empty or not last.empty):
        def _sym_counts(frame: pd.DataFrame):
            vals = []
            for c in cols:
                s = frame[c].astype("string").str.strip()
                s = s[s.map(is_valid_symptom_value)]
                vals.extend([str(x) for x in s.tolist()])
            return Counter(vals)

        c_last = _sym_counts(last)
        c_prev = _sym_counts(prev) if not prev.empty else Counter()

        for sym, cnt in c_last.most_common(12):
            prev_cnt = c_prev.get(sym, 0)
            if prev_cnt == 0 and cnt >= min_mentions:
                out.append(f"New/returning theme: **{str(sym).title()}** with {cnt} mentions in {last_m.strftime('%b %Y')} (0 prior month).")
            elif prev_cnt > 0:
                inc = cnt - prev_cnt
                inc_pct = (inc / prev_cnt) * 100.0
                if inc >= min_mentions and inc_pct >= 50:
                    out.append(f"Spike: **{str(sym).title()}** mentions {prev_cnt} → {cnt} in {last_m.strftime('%b %Y')} (↑{inc_pct:.0f}%).")

    return out[:8]


# ============================================================
# JSON -> Reviews DF helpers (from aXreviewsConverter.py)
# ============================================================
EXCEL_MAX_CHARS = 32767


def safe_get(d: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def join_list(x: Any, sep: str = " | ") -> Any:
    if x is None:
        return None
    if isinstance(x, dict):
        # Preserve nested dicts as a compact JSON string so they can still be filtered/searchable.
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    if isinstance(x, list):
        vals = [str(v).strip() for v in x if v is not None and str(v).strip() != ""]
        return sep.join(vals) if vals else None
    return x


def parse_iso_date(x: Any) -> Optional[date]:
    if not x:
        return None
    ts = pd.to_datetime(x, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


REVIEWS_BASE_COLS: List[Tuple[str, Any]] = [
    ("Record ID", lambda r: r.get("_id")),
    ("Opened Timestamp", lambda r: parse_iso_date(r.get("openedTimestamp"))),
    ("Rating (num)", lambda r: safe_get(r, ["clientAttributes", "Rating (num)"])),
    ("Retailer", lambda r: safe_get(r, ["clientAttributes", "Retailer"])),
    ("Model", lambda r: safe_get(r, ["clientAttributes", "Model"])),
    ("Seeded Reviews", lambda r: safe_get(r, ["clientAttributes", "Seeded Reviews"])),
    ("Syndicated/Seeded Reviews", lambda r: safe_get(r, ["clientAttributes", "Syndicated/Seeded Reviews"])),
    ("Location", lambda r: safe_get(r, ["clientAttributes", "Location"])),
    ("Title", lambda r: safe_get(r, ["freeText", "Title"])),
    ("Review", lambda r: safe_get(r, ["freeText", "Review"])),
]

def _collect_attribute_keys(records: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Collect *all* attribute keys so they can become filterable columns.

    This is intentionally broad: anything under clientAttributes/customAttributes/
    customAttributes.taxonomies/axionAttributes becomes a column.
    """

    def _clean_keys(keys: Iterable[Any]) -> List[str]:
        out = []
        for k in keys:
            s = str(k).strip()
            if not s or s.lower() in {"unnamed: 0"}:
                continue
            out.append(s)
        return sorted(list(dict.fromkeys(out)))

    client: set[str] = set()
    custom: set[str] = set()
    tax: set[str] = set()
    ax: set[str] = set()

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

    return (
        _clean_keys(client),
        _clean_keys(custom),
        _clean_keys(tax),
        _clean_keys(ax),
    )


def build_reviews_df(records: List[Dict[str, Any]], include_extra: bool = True) -> pd.DataFrame:
    """Build a "reviews" DataFrame from the JSON export.

    v19 upgrade:
    - Keeps the original base columns used by the app.
    - Also flattens **all** JSON attributes (client/custom/taxonomies/axion)
      into additional columns so they can be used in the "➕ Add Filters" system.
    """

    client_keys: List[str] = []
    custom_keys: List[str] = []
    tax_keys: List[str] = []
    ax_keys: List[str] = []
    if include_extra:
        client_keys, custom_keys, tax_keys, ax_keys = _collect_attribute_keys(records)

    base_cols = list(REVIEWS_BASE_COLS)

    rows: List[Dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict):
            continue

        row: Dict[str, Any] = {name: fn(r) for name, fn in base_cols}

        if include_extra:
            ca = r.get("clientAttributes") if isinstance(r.get("clientAttributes"), dict) else {}
            cu = r.get("customAttributes") if isinstance(r.get("customAttributes"), dict) else {}
            tax = cu.get("taxonomies") if isinstance(cu.get("taxonomies"), dict) else {}
            aa = r.get("axionAttributes") if isinstance(r.get("axionAttributes"), dict) else {}

            for k in client_keys:
                row[k] = join_list(ca.get(k))
            for k in custom_keys:
                # (taxonomies handled separately)
                row[k] = join_list(cu.get(k))
            for k in tax_keys:
                row[k] = join_list(tax.get(k))
            for k in ax_keys:
                row[k] = join_list(aa.get(k))

            # A couple useful top-level scalars (kept minimal)
            if "eventType" in r:
                row["eventType"] = r.get("eventType")
            if "eventId" in r:
                row["eventId"] = r.get("eventId")

        rows.append(row)

    df = pd.DataFrame(rows)
    if "Rating (num)" in df.columns:
        df["Rating (num)"] = pd.to_numeric(df["Rating (num)"], errors="coerce")
    return df


# Flexible JSON parsing (from aXreviewsConverter.py)
def _strip_code_fences(s: str) -> str:
    s = s.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return s


def _extract_json_substring(s: str) -> str:
    s = s.strip()
    if s.startswith("{") or s.startswith("["):
        return s
    start_candidates = [i for i in [s.find("{"), s.find("[")] if i != -1]
    if not start_candidates:
        return s
    start = min(start_candidates)
    end = max(s.rfind("}"), s.rfind("]"))
    if end != -1 and end > start:
        return s[start : end + 1].strip()
    return s


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


def loads_flexible_json(text_in: str) -> Tuple[Any, List[str]]:
    warnings: List[str] = []
    s = _strip_code_fences(text_in)
    s = _extract_json_substring(s)

    try:
        return json.loads(s), warnings
    except Exception as e1:
        # Attempt: remove trailing commas
        s2 = re.sub(r",\s*([}\]])", r"\1", s)
        if s2 != s:
            try:
                warnings.append("Removed trailing commas to make JSON valid.")
                return json.loads(s2), warnings
            except Exception:
                warnings.pop()

        # Attempt: JSON Lines
        jl = _try_parse_json_lines(s)
        if jl is not None:
            warnings.append("Detected JSON Lines and parsed each line as a record.")
            return jl, warnings

        raise ValueError("Could not parse input as JSON. Paste valid JSON (or JSON Lines).") from e1


def extract_records(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict) and "results" in raw and isinstance(raw["results"], list):
        return raw["results"]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    raise ValueError("Unrecognized JSON shape. Expected a dict with `results: []` or a list of record objects.")


# ============================================================
# Reviews DF -> Star Walk DF (from reviews_transform.py)
# ============================================================
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
]

DEFAULT_TAG_SPLIT_RE = re.compile(r"[;\|\n,]+")


def dedupe_preserve(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_tags(value, split_re: re.Pattern) -> List[str]:
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
            inner = s[1:-1].strip()
            if inner:
                parts = [p.strip().strip("'\"") for p in inner.split(",")]
                return dedupe_preserve([p for p in parts if p])
            return []
    parts = [p.strip() for p in split_re.split(s) if p and p.strip()]
    return dedupe_preserve(parts)


_NUMERIC_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")


def clean_seeded_value(v):
    if v is None:
        return "no"
    try:
        if pd.isna(v):
            return "no"
    except Exception:
        pass
    if isinstance(v, (bool, np.bool_)):
        return "Yes" if bool(v) else "no"
    if isinstance(v, (int, np.integer, float, np.floating)):
        try:
            return "Yes" if float(v) == 1.0 else "no"
        except Exception:
            return "no"

    s0 = str(v).strip()
    if s0 and _NUMERIC_RE.match(s0):
        try:
            fv = float(s0)
            if fv == 1.0:
                return "Yes"
            if fv == 0.0:
                return "no"
        except Exception:
            pass

    tokens = parse_tags(v, DEFAULT_TAG_SPLIT_RE)
    for t in tokens:
        raw = str(t).strip().lower()
        nt = _norm(t)
        if nt in {"notseeded", "unseeded", "nonseeded"} or raw in {"not seeded", "unseeded", "non seeded"}:
            return "no"
        if nt in {"false", "no", "n", "0"}:
            return "no"
        if nt in {"seeded", "yes", "true", "y", "1"}:
            return "Yes"
        if raw in {"1.0", "1"}:
            return "Yes"
        if "seeded" in nt:
            return "Yes"
    return "no"


def clean_seeded_series(series: pd.Series) -> pd.Series:
    return series.apply(clean_seeded_value).astype("object")


def clean_star_rating_value(v):
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
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return pd.NA
    fv = float(m.group(1))
    return int(fv) if fv.is_integer() else fv


def clean_star_rating_series(series: pd.Series) -> pd.Series:
    return series.apply(clean_star_rating_value).astype("object")


def collect_from_row_tuple(row_tup, col_idx: Dict[str, int], cols: List[str], split_re: re.Pattern) -> List[str]:
    tags: List[str] = []
    for c in cols:
        i = col_idx.get(c)
        if i is None:
            continue
        tags.extend(parse_tags(row_tup[i], split_re))
    return dedupe_preserve(tags)


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


def convert_to_starwalk_from_reviews_df(
    reviews_df: pd.DataFrame,
    include_extra_cols_after_symptom20: bool = True,
    weight_mode: str = "Leave blank",
    split_regex: str = r"[;\|\n,]+",
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Opinionated conversion for THIS master app:
    - Uses export column names if present
    - Maps into default Star Walk scrubbed verbatims schema
    """
    src_df = reviews_df.reset_index(drop=True)
    out_cols = list(DEFAULT_STARWALK_COLUMNS)

    # v19: optionally keep **all** extra JSON-derived columns (e.g., Base SKU,
    # Product Category, Company, Factory Name, etc.) so they can be used in
    # the "➕ Add Filters" power-user system.
    extra_cols_dynamic: List[str] = []
    extra_cols_final: List[str] = []
    if include_extra_cols_after_symptom20:
        base_extras = [c for c in DEFAULT_EXTRA_AFTER_SYMPTOM20 if c in src_df.columns]

        # Columns already represented in the Star Walk schema via field_map below.
        # (We avoid duplicating them as extra columns.)
        _used_inputs = {
            "Retailer",
            "Model",
            "Seeded Reviews",
            "Syndicated/Seeded Reviews",
            "Location",
            "Opened Timestamp",
            "Record ID",
            "Review",
            "Rating (num)",
        }

        for c in list(src_df.columns):
            sc = str(c).strip()
            if not sc or sc.lower() in {"unnamed: 0"}:
                continue
            if sc in _used_inputs:
                continue
            if sc in base_extras:
                continue
            # Avoid duplicating symptoms / core outputs
            if sc in out_cols or sc.startswith("Symptom "):
                continue
            try:
                if src_df[sc].isna().all():
                    continue
            except Exception:
                pass
            extra_cols_dynamic.append(sc)

        # Stable order: known extras first, then the remaining columns sorted.
        extra_cols_dynamic = sorted(list(dict.fromkeys(extra_cols_dynamic)))
        extra_cols_final = base_extras + [c for c in extra_cols_dynamic if c not in base_extras]

        out_cols = insert_after_symptom20(out_cols, extra_cols_final)

    # Field mapping (source col -> output col)
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
        # map extras where possible (same names)
        **{c: c for c in extra_cols_final if c in src_df.columns},
    }

    # L2 columns
    l2_det_cols = [c for c in ["Product_Symptom Conditions", "Product Symptom Conditions"] if c in src_df.columns]
    l2_del_cols = [c for c in ["L2 Delighter Condition", "L2 Delighter Conditions"] if c in src_df.columns]

    if not l2_det_cols:
        # try heuristic
        l2_det_cols = [c for c in src_df.columns if "productsymptom" in _norm(c) and "condition" in _norm(c)][:1]
    if not l2_del_cols:
        l2_del_cols = [c for c in src_df.columns if "delighter" in _norm(c) and "condition" in _norm(c)][:1]

    split_re = re.compile(split_regex)
    src_cols = list(src_df.columns)
    col_idx = {c: i for i, c in enumerate(src_cols)}
    n = len(src_df)

    needed_symptoms = [f"Symptom {i}" for i in range(1, 21)]
    base_cols = list(out_cols)
    for c in needed_symptoms:
        if c not in base_cols:
            base_cols.append(c)
    if "Review count per detractor" not in base_cols:
        base_cols.append("Review count per detractor")

    out = pd.DataFrame(index=range(n), columns=base_cols, dtype="object")

    # Copy fields
    for out_field, in_field in field_map.items():
        if out_field not in out.columns:
            continue
        if in_field and in_field in src_df.columns:
            series = src_df[in_field]
            if _norm(out_field) == "seeded":
                out[out_field] = clean_seeded_series(series)
            elif _norm(out_field) == "starrating":
                out[out_field] = clean_star_rating_series(series)
            else:
                out[out_field] = series.values
        else:
            out[out_field] = pd.NA

    # Ensure Seeded / Star are normalized
    if "Seeded" in out.columns:
        out["Seeded"] = clean_seeded_series(out["Seeded"])
    if "Star Rating" in out.columns:
        out["Star Rating"] = clean_star_rating_series(out["Star Rating"])

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

    detr_cols_out = [f"Symptom {i}" for i in range(1, 11)]
    deli_cols_out = [f"Symptom {i}" for i in range(11, 21)]

    out[detr_cols_out] = pd.DataFrame(detr_matrix, columns=detr_cols_out).values
    out[deli_cols_out] = pd.DataFrame(deli_matrix, columns=deli_cols_out).values

    # Weight column behavior
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


# ============================================================
# Star Walk file loader (same behavior as starwalk_ui_test.py)
# ============================================================
@st.cache_data(show_spinner=False)
def _load_starwalk_table(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load uploaded Excel/CSV and return cleaned Star Walk scrubbed verbatims table.
    Behaviors:
    - If Excel: try sheet "Star Walk scrubbed verbatims"
      else detect a sheet containing a 'Verbatim' column
      else fallback first sheet.
    - Standardizes key columns to uppercase and coerces types.
    """
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
                    bio3 = BytesIO(file_bytes)
                    df_local = pd.read_excel(bio3)
    except Exception as e:
        raise RuntimeError(f"Could not read file: {e}")

    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        if col in df_local.columns:
            df_local[col] = df_local[col].astype("string").str.upper()

    if "Star Rating" in df_local.columns:
        df_local["Star Rating"] = pd.to_numeric(df_local["Star Rating"], errors="coerce")

    all_symptom_cols = [c for c in df_local.columns if str(c).startswith("Symptom")]
    for c in all_symptom_cols:
        df_local[c] = df_local[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

    if "Verbatim" in df_local.columns:
        df_local["Verbatim"] = df_local["Verbatim"].astype("string").map(clean_text)

    if "Review Date" in df_local.columns:
        df_local["Review Date"] = pd.to_datetime(df_local["Review Date"], errors="coerce")

    return df_local


# ============================================================
# JSON -> Star Walk conversion (cached)
# ============================================================
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

    # clean types like loader does
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        if col in out_df.columns:
            out_df[col] = out_df[col].astype("string").str.upper()
    if "Star Rating" in out_df.columns:
        out_df["Star Rating"] = pd.to_numeric(out_df["Star Rating"], errors="coerce")
    if "Review Date" in out_df.columns:
        out_df["Review Date"] = pd.to_datetime(out_df["Review Date"], errors="coerce")
    all_symptom_cols = [c for c in out_df.columns if str(c).startswith("Symptom")]
    for c in all_symptom_cols:
        out_df[c] = out_df[c].astype("string").map(lambda v: clean_text(v, keep_na=True)).astype("string")

    out_df["Verbatim"] = out_df.get("Verbatim", pd.Series(dtype="string")).astype("string").map(clean_text)

    meta = {
        "source": source_name,
        "warnings": warnings,
        "stats": stats,
        "records": len(records),
    }
    return out_df, meta


# ============================================================
# Preset / Saved Views helpers
# ============================================================
PRESET_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def encode_preset_to_url_param(obj: dict) -> str:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    comp = zlib.compress(raw, level=9)
    return _b64e(comp)


def decode_preset_from_url_param(s: str) -> dict:
    raw = zlib.decompress(_b64d(s))
    return json.loads(raw.decode("utf-8"))


def _get_query_params() -> dict:
    # Supports Streamlit old/new APIs
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        # qp behaves like a mapping; values can be str or list[str]
        out = {}
        for k in qp.keys():
            out[k] = qp.get(k)
        return out
    except Exception:
        return st.experimental_get_query_params()  # type: ignore


def _set_query_params(**kwargs):
    # Set/replace query params (supports Streamlit old/new)
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        qp.clear()
        for k, v in kwargs.items():
            qp[k] = v
    except Exception:
        st.experimental_set_query_params(**kwargs)  # type: ignore


def _safe_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _collect_filter_state(additional_columns: list[str]) -> dict:
    """
    Collect CURRENT widget values (not necessarily applied).
    We save filters by the keys used in session_state.
    """
    state = {
        "schema_version": PRESET_SCHEMA_VERSION,
        "created_at": _now_iso(),
        "filters": {},
        "ui": {},
    }

    # timeframe
    state["filters"]["tf"] = st.session_state.get("tf", "All Time")
    # custom range stored separately; may be date or tuple
    state["filters"]["tf_range"] = st.session_state.get("tf_range", None)

    # star ratings + keyword + symptoms
    state["filters"]["sr"] = st.session_state.get("sr", ["All"])
    state["filters"]["kw"] = st.session_state.get("kw", "")
    state["filters"]["delight"] = st.session_state.get("delight", ["All"])
    state["filters"]["detract"] = st.session_state.get("detract", ["All"])

    # standard column filters
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        k = f"f_{col}"
        if k in st.session_state:
            state["filters"][k] = st.session_state.get(k)
        rk = f"f_{col}_range"
        if rk in st.session_state:
            state["filters"][rk] = st.session_state.get(rk)
        ck = f"f_{col}_contains"
        if ck in st.session_state:
            state["filters"][ck] = st.session_state.get(ck)

    # additional columns (dynamic)
    for col in additional_columns:
        k = f"f_{col}"
        if k in st.session_state:
            state["filters"][k] = st.session_state.get(k)
        rk = f"f_{col}_range"
        if rk in st.session_state:
            state["filters"][rk] = st.session_state.get(rk)
        ck = f"f_{col}_contains"
        if ck in st.session_state:
            state["filters"][ck] = st.session_state.get(ck)

    # reviews per page
    state["ui"]["rpp"] = st.session_state.get("rpp", 10)

    return state


def _apply_filter_state_to_session(state: dict, available_columns: list[str], additional_columns: list[str]):
    """
    Apply a preset into session_state keys, gracefully handling missing columns/values.
    """
    filters = (state or {}).get("filters", {})

    # simple scalar keys
    if "tf" in filters:
        st.session_state["tf"] = filters.get("tf")
    if "tf_range" in filters:
        st.session_state["tf_range"] = filters.get("tf_range")

    if "sr" in filters:
        st.session_state["sr"] = _safe_list(filters.get("sr"))
    if "kw" in filters:
        st.session_state["kw"] = str(filters.get("kw") or "")
    if "delight" in filters:
        st.session_state["delight"] = _safe_list(filters.get("delight"))
    if "detract" in filters:
        st.session_state["detract"] = _safe_list(filters.get("detract"))

    # column filters
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + list(additional_columns):
        k = f"f_{col}"
        if k in filters:
            st.session_state[k] = _safe_list(filters.get(k))

        rk = f"f_{col}_range"
        if rk in filters:
            st.session_state[rk] = filters.get(rk)

        ck = f"f_{col}_contains"
        if ck in filters:
            st.session_state[ck] = str(filters.get(ck) or "")

    # UI
    ui = (state or {}).get("ui", {})
    if "rpp" in ui:
        st.session_state["rpp"] = int(ui.get("rpp") or 10)

    # mark applied snapshot
    st.session_state["_active_filters"] = state.get("filters", {})
    st.session_state["review_page"] = 0


# ============================================================
# Fast symptom analysis (major perf upgrade)
# ============================================================
def analyze_symptoms_fast(df_in: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    """
    Vectorized version of analyze_delighters_detractors:
    - avoids per-symptom scanning across rows (huge speed-up on large datasets)
    - counts mentions (cell-level) and computes avg star (review-level, de-duplicated)
    """
    cols = [c for c in symptom_columns if c in df_in.columns]
    if not cols:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])

    # Stack to long
    block = df_in[cols]
    # Keep index mapping for joining stars
    idx = block.index.to_numpy()
    long = block.stack(dropna=False).reset_index()
    # columns: level_0 (index), level_1 (col), 0 (value)
    long.columns = ["__idx", "__col", "symptom"]
    # Clean / filter (normalize casing so the same symptom doesn't appear twice)
    s = long["symptom"].astype("string").str.strip()
    mask = s.map(is_valid_symptom_value)
    long = long.loc[mask, ["__idx"]].copy()
    long["symptom"] = s[mask].astype("string").str.strip().str.title()
    if long.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])

    # Mentions (cell-level)
    counts = long["symptom"].value_counts()

    # Avg star (review-level, de-dup symptom within a review)
    avg_map = {}
    if "Star Rating" in df_in.columns:
        stars = pd.to_numeric(df_in["Star Rating"], errors="coerce")
        tmp = long.drop_duplicates(subset=["__idx", "symptom"]).copy()
        tmp["star"] = tmp["__idx"].map(stars.to_dict())
        avg = tmp.groupby("symptom")["star"].mean()
        avg_map = avg.to_dict()

    total_rows = len(df_in) or 1
    out = pd.DataFrame(
        {
            "Item": [str(x).title() for x in counts.index.tolist()],
            "Avg Star": [None if pd.isna(avg_map.get(x, pd.NA)) else round(float(avg_map.get(x)), 1) for x in counts.index.tolist()],
            "Mentions": counts.values.astype(int),
            "% Total": (counts.values / total_rows * 100).round(1).astype(str) + "%",
        }
    )
    return out.sort_values("Mentions", ascending=False, ignore_index=True)


# ============================================================
# Local retrieval (TF-IDF + optional BM25 + optional reranker)
# ============================================================
def _hash_texts(texts: list[str]) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(((t or "") + "\x00").encode("utf-8"))
    return h.hexdigest()


def _get_or_build_local_text_index(texts: list[str], content_hash: str):
    memo = st.session_state.setdefault("_local_text_idx", {})
    if content_hash in memo:
        return memo[content_hash]
    corpus = [(t or "").strip() for t in texts]
    tfidf = TfidfVectorizer(lowercase=True, strip_accents="unicode", ngram_range=(1, 2), min_df=2, max_df=0.95)
    tfidf_mat = tfidf.fit_transform(corpus)
    bm25 = None
    tokenized = None
    if _HAS_BM25 and st.session_state.get("use_bm25", False):
        tokenized = [t.lower().split() for t in corpus]
        bm25 = BM25Okapi(tokenized)
    memo[content_hash] = {"tfidf": tfidf, "tfidf_mat": tfidf_mat, "bm25": bm25, "tokenized": tokenized, "texts": corpus}
    return memo[content_hash]


def _local_search(query: str, index: dict, top_k: int = 8):
    if not index:
        return []
    q = (query or "").strip()
    if not q:
        return []
    qvec = index["tfidf"].transform([q])
    scores = linear_kernel(qvec, index["tfidf_mat"]).ravel()
    tfidf_top = np.argsort(-scores)[: max(top_k * 3, 20)]
    top = tfidf_top
    if index.get("bm25") is not None:
        bm_scores = index["bm25"].get_scores(q.lower().split())
        bm = (bm_scores - np.min(bm_scores)) / (np.ptp(bm_scores) + 1e-9)
        tf = (scores - np.min(scores)) / (np.ptp(scores) + 1e-9)
        hybrid = 0.6 * tf + 0.4 * bm
        top = np.argsort(-hybrid)[: top_k * 3]
    top_idx = list(top[:top_k])

    if _HAS_RERANKER and st.session_state.get("use_reranker", False):
        try:
            ce = st.session_state.get("_cross_encoder")
            if ce is None:
                with st.spinner("Loading local reranker…"):
                    ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
                st.session_state["_cross_encoder"] = ce
            cand = [index["texts"][i] for i in top]
            pairs = [[q, c] for c in cand]
            rr = ce.predict(pairs)
            reranked = np.argsort(-rr)[:top_k]
            top_idx = [top[i] for i in reranked]
        except Exception:
            top_idx = list(top[:top_k])

    return [(index["texts"][i], float(scores[i])) for i in top_idx]


# ---------- Embedding helpers with backoff, hashing & caching ----------
_EMBED_LOCK = threading.Lock()


def _embed_with_backoff(client, model: str, inputs: List[str], max_attempts: int = 6):
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            return client.embeddings.create(model=model, input=inputs)
        except Exception as e:
            status = getattr(e, "status_code", None)
            msg = str(e).lower()
            is_rate = status == 429 or "rate" in msg or "quota" in msg
            if not is_rate or attempt == max_attempts:
                raise
            sleep_s = delay * (1.5 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(sleep_s)


def _build_vector_index(texts: list[str], api_key: str, model: str = "text-embedding-3-small"):
    if not _HAS_OPENAI or not texts:
        return None
    client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
    embs = []
    batch = 128
    with _EMBED_LOCK:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            safe_chunk = [(t or "")[:2000] for t in chunk]
            resp = _embed_with_backoff(client, model, safe_chunk)
            embs.extend([np.array(d.embedding, dtype=np.float32) for d in resp.data])
            time.sleep(0.05 + random.uniform(0, 0.05))
    if not embs:
        return None
    mat = np.vstack(embs).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    mat_norm = mat / norms
    if _HAS_FAISS:
        index = faiss.IndexFlatIP(mat_norm.shape[1])
        index.add(mat_norm)
        return {"backend": "faiss", "index": index, "texts": texts}
    return (mat, norms, texts)


def _get_or_build_index(content_hash: str, raw_texts: list[str], api_key: str, model: str):
    memo = st.session_state.setdefault("_vec_idx", {})
    if content_hash in memo:
        return memo[content_hash]
    idx = _build_vector_index(raw_texts, api_key=api_key, model=model)
    memo[content_hash] = idx
    return idx


def vector_search(query: str, index, api_key: str, top_k: int = 8):
    if not _HAS_OPENAI or index is None:
        return []
    client = OpenAI(api_key=api_key)
    qemb = client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding
    q = np.array(qemb, dtype=np.float32)
    qn = np.linalg.norm(q) + 1e-8
    qn_vec = q / qn
    if isinstance(index, dict) and index.get("backend") == "faiss":
        D, I = index["index"].search(qn_vec.reshape(1, -1), top_k)
        sims = D[0].tolist()
        idxs = I[0].tolist()
        texts = index["texts"]
        return [(texts[i], float(sims[j])) for j, i in enumerate(idxs) if i != -1]
    mat, norms, texts = index
    sims = (mat @ q) / (norms.flatten() * qn)
    idx = np.argsort(-sims)[:top_k]
    return [(texts[i], float(sims[i])) for i in idx]



# ============================================================
# Theme bootstrap + extra CSS patch (fix mixed light/dark on fresh sessions)
# ============================================================
# - Defaults NEW users to light (only if they have no stored preference)
# - Ensures our surfaces follow Streamlit theme vars with safe fallbacks
THEME_PATCH_CSS = ""
# ============================================================
# Improved flexible JSON parsing (more forgiving for copy/paste)
# ============================================================
def loads_flexible_json(text_in: str) -> Tuple[Any, List[str]]:
    """
    Parses:
    - Normal JSON object/array
    - JSON Lines (one object per line)
    - Common copy/paste variants: code fences, BOM, smart quotes, trailing commas
    - Python-literal dict/list (best-effort via ast.literal_eval)
    """
    warnings: List[str] = []

    if text_in is None:
        raise ValueError("No JSON provided.")

    s = str(text_in)

    # Remove BOM / zero-width / non-breaking spaces
    s = s.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    s = _strip_code_fences(s)
    s = _extract_json_substring(s).strip()

    # Normalize smart quotes
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    # Fast path: standard JSON
    try:
        return json.loads(s), warnings
    except Exception as e1:
        last_err = e1

    # Attempt: remove trailing commas
    s2 = re.sub(r",\s*([}\]])", r"\1", s)
    if s2 != s:
        try:
            warnings.append("Removed trailing commas to make JSON valid.")
            return json.loads(s2), warnings
        except Exception as e2:
            last_err = e2
            warnings = [w for w in warnings if "trailing commas" not in w.lower()]

    # Attempt: JSON Lines
    jl = _try_parse_json_lines(s)
    if jl is not None:
        warnings.append("Detected JSON Lines and parsed each line as a record.")
        return jl, warnings

    # Attempt: Python-literal (dict/list with single quotes, None/True/False)
    try:
        obj = ast.literal_eval(s)
        if isinstance(obj, (dict, list)):
            warnings.append("Parsed as a Python literal (best-effort). Consider exporting valid JSON for reliability.")
            return obj, warnings
    except Exception as e3:
        last_err = e3

    # Attempt: if content is wrapped in quotes (rare copy/paste)
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            inner = s[1:-1]
            return json.loads(inner), warnings + ["Removed outer quotes around pasted JSON text."]
        except Exception as e4:
            last_err = e4

    raise ValueError(
        "Could not parse input as JSON. Paste valid JSON (object/array) or JSON Lines.\n"
        "Tips: remove any leading/trailing commentary, ensure quotes are standard \" characters, and avoid trailing commas."
    ) from last_err


# ============================================================
# Main UI
# ============================================================
st.title("Star Walk — Consumer Insights Dashboard")
st.caption(f"Version: {APP_VERSION}")

# ----------------------------
# Input mode
# ----------------------------
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
meta_info: dict = {}

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
        raw_text = json_file.getvalue().decode("utf-8", errors="replace")
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
        st.caption("You can download the converted Star Walk file below if you want.")
        out_bytes = df_base.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Download converted Star Walk CSV",
            out_bytes,
            file_name=f"{Path(source_label).stem}_starwalk.csv",
            mime="text/csv",
        )

# ----------------------------
# Dataset basics
# ----------------------------
assert df_base is not None
product_label = _infer_product_label(df_base, source_label)

# Reset heavyweight caches when a new dataset is loaded (prevents stale AI insights across uploads)
try:
    _dataset_sig = hashlib.sha1(
        (str(source_label) + "|" + str(df_base.shape) + "|" + "|".join([str(c) for c in df_base.columns.tolist()[:60]])).encode("utf-8")
    ).hexdigest()
except Exception:
    _dataset_sig = str(getattr(df_base, "shape", ""))

if st.session_state.get("_dataset_sig") != _dataset_sig:
    for _k in [
        "_review_sort_cache",
        "_col_opts_cache",
        "_ai_local_index",
        "_ai_last_answer",
        "_ai_last_q",
        "saved_views",
        "_sv_loaded_once",
        "_pending_preset_from_url",
    ]:
        st.session_state.pop(_k, None)
    st.session_state["_dataset_sig"] = _dataset_sig

st.markdown(
    f"""
<div class="soft-panel">
  <div><span class="kpi-pill">Source</span> <b>{esc(source_label)}</b></div>
  <div style="margin-top:6px;"><span class="kpi-pill">Product guess</span> <b>{esc(product_label)}</b></div>
  <div class="small-muted" style="margin-top:8px;">
    All analytics reflect the <b>currently filtered</b> dataset.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# Sidebar: Filters + Saved Views
# ============================================================

# ---- Load preset from URL param once ----
qp = _get_query_params()
if (not st.session_state.get("_sv_loaded_once")) and qp.get("sv"):
    try:
        sv = qp.get("sv")
        if isinstance(sv, list):
            sv = sv[0] if sv else ""
        preset_obj = decode_preset_from_url_param(str(sv))
        if isinstance(preset_obj, dict) and preset_obj.get("schema_version") == PRESET_SCHEMA_VERSION:
            st.session_state["_sv_loaded_once"] = True
            st.session_state["_pending_preset_from_url"] = preset_obj
    except Exception:
        st.session_state["_sv_loaded_once"] = True
        st.sidebar.warning("Could not load Saved View from URL (invalid or truncated).")

core_cols = {"Country", "Source", "Model (SKU)", "Seeded", "New Review", "Star Rating", "Review Date", "Verbatim"}
symptom_cols = {f"Symptom {i}" for i in range(1, 21)}

extra_filter_candidates = [c for c in df_base.columns if c not in (core_cols | symptom_cols)]
extra_filter_candidates = [c for c in extra_filter_candidates if str(c).strip() and str(c).strip().lower() not in {"unnamed: 0"}]

st.session_state.setdefault("extra_filter_cols", [])  # user-selected extra filters to show
st.session_state.setdefault("saved_views", {})        # name -> preset object

# Apply pending URL preset now that we know columns
pending = st.session_state.pop("_pending_preset_from_url", None)
if pending:
    # Apply will also populate f_{col} keys; we then set extra_filter_cols from the preset keys
    _apply_filter_state_to_session(pending, available_columns=list(df_base.columns), additional_columns=extra_filter_candidates)
    # infer which extra filters were used
    used = []
    for k in (pending.get("filters") or {}).keys():
        if k.startswith("f_"):
            col = k[2:]
            if col in extra_filter_candidates:
                used.append(col)
    st.session_state["extra_filter_cols"] = sorted(list(set(used)))

# defaults
tz_today = datetime.now(_NY_TZ).date() if _NY_TZ else datetime.today().date()
today = tz_today
st.session_state.setdefault("tf", "All Time")
st.session_state.setdefault("tf_range", (today - timedelta(days=30), today))
st.session_state.setdefault("sr", ["All"])
st.session_state.setdefault("kw", "")
st.session_state.setdefault("delight", ["All"])
st.session_state.setdefault("detract", ["All"])
st.session_state.setdefault("rpp", 10)

# Precompute symptom columns lists
detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
existing_detractor_columns = [c for c in detractor_columns if c in df_base.columns]
existing_delighter_columns = [c for c in delighter_columns if c in df_base.columns]

# Build symptom options once per dataset
_sym_key = f"{source_label}|{len(df_base)}|" + hashlib.md5("|".join(df_base.columns).encode("utf-8")).hexdigest()
if st.session_state.get("_symptom_opts_key") != _sym_key:
    st.session_state["_symptom_opts_key"] = _sym_key
    st.session_state["_symptom_opts_det"] = collect_unique_symptoms(df_base, existing_detractor_columns)
    st.session_state["_symptom_opts_del"] = collect_unique_symptoms(df_base, existing_delighter_columns)
detractor_symptoms_all = st.session_state.get("_symptom_opts_det", []) or []
delighter_symptoms_all = st.session_state.get("_symptom_opts_del", []) or []

def _col_options(df_in: pd.DataFrame, col: str, max_vals: Optional[int] = 250) -> list:
    """Return filter options for a column.

    - Uses frequency order (e-commerce style: most common values first).
    - If max_vals is None, returns **all** values found.
    - Cached per dataset+column so it stays fast even with many reruns.
    """
    if col not in df_in.columns:
        return ["ALL"]

    cache = st.session_state.setdefault("_col_opts_cache", {})

    s0 = df_in[col].astype("string").replace({"": pd.NA}).dropna()
    # Detect multi-valued fields we created from lists (we join lists with " | ").
    # When detected, we explode tokens so the filter shows *actual* values
    # (e.g., "Factory Name" -> "YDC CN") instead of combo strings.
    sample = s0.head(300)
    tokenize_multi = bool(sample.astype(str).str.contains(r"\s\|\s", regex=True).any())

    cache_key = (
        st.session_state.get("_dataset_sig"),
        str(col),
        int(max_vals) if isinstance(max_vals, int) else None,
        bool(tokenize_multi),
    )
    if cache_key in cache:
        return cache[cache_key]

    if s0.empty:
        cache[cache_key] = ["ALL"]
        return ["ALL"]

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

    vals = vc.index.astype(str).tolist()
    out = ["ALL"] + vals
    cache[cache_key] = out
    return out

def _sanitize_multiselect(key: str, options: list, default: list):
    cur = st.session_state.get(key, default)
    if cur is None:
        cur = list(default)
    if not isinstance(cur, list):
        cur = [cur]
    cur = [v for v in cur if v in options]
    if not cur:
        cur = list(default)
    # If user selected values besides ALL, drop ALL
    if "ALL" in cur and len(cur) > 1:
        cur = [v for v in cur if v != "ALL"]
    st.session_state[key] = cur
    return cur

def _sanitize_multiselect_sym(key: str, options: list, default: list):
    cur = st.session_state.get(key, default)
    if cur is None:
        cur = list(default)
    if not isinstance(cur, list):
        cur = [cur]
    cur = [v for v in cur if v in options]
    if not cur:
        cur = list(default)
    if "All" in cur and len(cur) > 1:
        cur = [v for v in cur if v != "All"]
    st.session_state[key] = cur
    return cur

def _reset_all_filters():
    # Core
    st.session_state["tf"] = "All Time"
    st.session_state["tf_range"] = (today - timedelta(days=30), today)
    st.session_state["sr"] = ["All"]
    st.session_state["kw"] = ""
    st.session_state["delight"] = ["All"]
    st.session_state["detract"] = ["All"]
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        st.session_state[f"f_{col}"] = ["ALL"]
    # Extra
    for col in st.session_state.get("extra_filter_cols", []):
        st.session_state.pop(f"f_{col}", None)
        st.session_state.pop(f"f_{col}_range", None)
        st.session_state.pop(f"f_{col}_contains", None)
    st.session_state["extra_filter_cols"] = []
    # Paging
    st.session_state["review_page"] = 0


# Home (requested): quick way back to main dashboard from anywhere
if st.sidebar.button("🏠 Home", use_container_width=True):
    st.session_state["main_view"] = "📊 Dashboard"
    st.rerun()

st.sidebar.header("🔍 Filters")

# Clear button FIRST (requested)
if st.sidebar.button("🧹 Clear all filters", use_container_width=True):
    _reset_all_filters()
    st.rerun()

# --- Timeframe ---
with st.sidebar.expander("🗓️ Timeframe", expanded=False):
    tf_opts = ["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"]
    if st.session_state.get("tf") not in tf_opts:
        st.session_state["tf"] = "All Time"
    st.selectbox("Select timeframe", options=tf_opts, key="tf")
    if st.session_state["tf"] == "Custom Range":
        rng = st.session_state.get("tf_range", (today - timedelta(days=30), today))
        if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
            rng = (today - timedelta(days=30), today)
        st.session_state["tf_range"] = tuple(rng)
        st.date_input("Start / end", value=st.session_state["tf_range"], key="tf_range")

# --- Star rating ---
with st.sidebar.expander("⭐ Star rating", expanded=False):
    sr_opts = ["All", 5, 4, 3, 2, 1]
    cur = st.session_state.get("sr", ["All"])
    if not isinstance(cur, list):
        cur = [cur]
    # sanitize
    cur = [v for v in cur if v in sr_opts]
    if not cur:
        cur = ["All"]
    if "All" in cur and len(cur) > 1:
        cur = [v for v in cur if v != "All"]
    st.session_state["sr"] = cur
    st.multiselect("Select stars", options=sr_opts, default=st.session_state["sr"], key="sr")

# --- Standard categorical filters ---
with st.sidebar.expander("🌍 Country / Source / Model / Seeded", expanded=True):
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        opts = _col_options(df_base, col, max_vals=250)
        _sanitize_multiselect(f"f_{col}", opts, ["ALL"])
        st.multiselect(col, options=opts, default=st.session_state[f"f_{col}"], key=f"f_{col}")

# --- Symptom filters ---
with st.sidebar.expander("🩺 Symptom filters", expanded=False):
    det_opts = ["All"] + detractor_symptoms_all
    del_opts = ["All"] + delighter_symptoms_all
    _sanitize_multiselect_sym("detract", det_opts, ["All"])
    _sanitize_multiselect_sym("delight", del_opts, ["All"])
    st.multiselect("Detractors", options=det_opts, default=st.session_state["detract"], key="detract")
    st.multiselect("Delighters", options=del_opts, default=st.session_state["delight"], key="delight")

# --- Keyword ---
with st.sidebar.expander("🔎 Keyword", expanded=False):
    st.text_input("Search in review text", value=st.session_state.get("kw", ""), key="kw")

# --- Extra filters builder ---
with st.sidebar.expander("➕ Add Filters (power user)", expanded=False):
    st.caption("Choose additional columns to show as filters in the sidebar.")
    # Use a multiselect so users can quickly add/remove extra filters
    extra_cols = st.multiselect(
        "Available columns",
        options=extra_filter_candidates,
        default=st.session_state.get("extra_filter_cols", []),
        key="extra_filter_cols",
    )

# Render selected extra filter widgets
extra_cols = st.session_state.get("extra_filter_cols", []) or []
if extra_cols:
    with st.sidebar.expander("🧩 Extra filters", expanded=True):
        for col in extra_cols:
            if col not in df_base.columns:
                continue
            s = df_base[col]
            # Detect numeric/date vs categorical
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
                st.session_state[key] = (float(default[0]), float(default[1]))
                st.slider(col, min_value=lo, max_value=hi, value=st.session_state[key], key=key)
            else:
                # Power-user extra filters:
                # - If cardinality is reasonable, show a searchable multiselect with **all** values.
                # - If cardinality is huge, show a fast "contains" search box instead.
                try:
                    nunique = int(s.astype("string").replace({"": pd.NA}).nunique(dropna=True))
                except Exception:
                    nunique = 0

                if nunique > 600:
                    st.text_input(
                        f"{col} contains",
                        value=str(st.session_state.get(f"f_{col}_contains") or ""),
                        key=f"f_{col}_contains",
                        help="High-cardinality column — using a contains filter for speed.",
                    )
                else:
                    opts = _col_options(df_base, col, max_vals=None)
                    _sanitize_multiselect(f"f_{col}", opts, ["ALL"])
                    st.multiselect(col, options=opts, default=st.session_state[f"f_{col}"], key=f"f_{col}")

# --- Saved Views (still supported) ---
with st.sidebar.expander("💾 Saved Views / Presets", expanded=False):
    st.caption("Save your current filters and share them as a link.")
    name = st.text_input("Preset name", value="", key="sv_name")
    cA, cB = st.columns([1, 1])
    with cA:
        if st.button("💾 Save", use_container_width=True):
            nm = (st.session_state.get("sv_name") or "").strip() or f"Preset {len(st.session_state['saved_views'])+1}"
            state = _collect_filter_state(additional_columns=extra_cols)
            state["name"] = nm
            st.session_state["saved_views"][nm] = state
            st.success(f"Saved: {nm}")
    with cB:
        if st.button("🗑️ Clear presets", use_container_width=True):
            st.session_state["saved_views"] = {}
            st.success("Cleared saved presets.")

    names = sorted(st.session_state.get("saved_views", {}).keys())
    if names:
        sel = st.selectbox("Load preset", options=["—"] + names, index=0, key="sv_load_sel")
        if sel != "—" and st.button("Load selected preset", use_container_width=True):
            preset = st.session_state["saved_views"].get(sel)
            if preset:
                _apply_filter_state_to_session(preset, available_columns=list(df_base.columns), additional_columns=extra_filter_candidates)
                # restore extra cols used
                used = []
                for k in (preset.get("filters") or {}).keys():
                    if k.startswith("f_"):
                        col = k[2:]
                        # Strip suffixes used by range/contains filters
                        for suf in ("_range", "_contains"):
                            if col.endswith(suf):
                                col = col[: -len(suf)]
                        if col in extra_filter_candidates:
                            used.append(col)
                st.session_state["extra_filter_cols"] = sorted(list(set(used)))
                st.rerun()

        # Share link
        if sel != "—" and sel in st.session_state["saved_views"]:
            preset = st.session_state["saved_views"][sel]
            sv_param = encode_preset_to_url_param(preset)
            # Keep existing params but set sv
            try:
                base = st.get_option("server.baseUrlPath")  # may be ""
            except Exception:
                base = ""
            # Use query param (Streamlit will show it in browser)
            if st.button("🔗 Make this preset shareable (URL)", use_container_width=True):
                _set_query_params(sv=sv_param)
                st.success("URL updated with ?sv=... (copy from browser address bar)")

# ============================================================
# Apply filters (LIVE — no Apply button)
# ============================================================
t_filter0 = time.perf_counter()
d0 = df_base

# Start with mask = all True
mask = pd.Series(True, index=d0.index)

# timeframe
tf = st.session_state.get("tf", "All Time")
start_date = end_date = None
if tf == "Custom Range":
    rng = st.session_state.get("tf_range", (today - timedelta(days=30), today))
    if isinstance(rng, (tuple, list)) and len(rng) == 2:
        start_date, end_date = rng
elif tf == "Last Week":
    start_date, end_date = today - timedelta(days=7), today
elif tf == "Last Month":
    start_date, end_date = today - timedelta(days=30), today
elif tf == "Last Year":
    start_date, end_date = today - timedelta(days=365), today

if start_date and end_date and "Review Date" in d0.columns:
    dt = pd.to_datetime(d0["Review Date"], errors="coerce")
    end_inclusive = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    mask &= (dt >= pd.Timestamp(start_date)) & (dt <= end_inclusive)

# star rating
sr_sel = st.session_state.get("sr", ["All"])
if isinstance(sr_sel, list) and "All" not in sr_sel and "Star Rating" in d0.columns:
    mask &= pd.to_numeric(d0["Star Rating"], errors="coerce").isin(sr_sel)

# standard categorical
for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
    sel = st.session_state.get(f"f_{col}", ["ALL"])
    if col in d0.columns and isinstance(sel, list) and sel and "ALL" not in sel:
        mask &= d0[col].astype("string").isin([str(x) for x in sel])

# extra filters
for col in extra_cols:
    if col not in d0.columns:
        continue
    # numeric range?
    range_key = f"f_{col}_range"
    if range_key in st.session_state and isinstance(st.session_state.get(range_key), (tuple, list)):
        lo, hi = st.session_state.get(range_key)
        num = pd.to_numeric(d0[col], errors="coerce")
        mask &= num.between(float(lo), float(hi))
    else:
        # high-cardinality text search?
        contains_key = f"f_{col}_contains"
        contains_val = (st.session_state.get(contains_key) or "").strip()
        if contains_val:
            mask &= d0[col].astype("string").fillna("").str.contains(contains_val, case=False, na=False)
        else:
            sel = st.session_state.get(f"f_{col}", ["ALL"])
            if isinstance(sel, list) and sel and "ALL" not in sel:
                s = d0[col].astype("string").fillna("")
                # If the field contains multi-values joined by " | ", treat selections as tokens.
                sample = s.dropna().head(200).astype(str)
                if bool(sample.str.contains(r"\s\|\s", regex=True).any()):
                    # Match whole tokens delimited by pipes (case-insensitive)
                    toks = [str(x).strip() for x in sel if str(x).strip()]
                    if toks:
                        pattern = r"(^|\s*\|\s*)(" + "|".join([re.escape(t) for t in toks]) + r")(\s*\|\s*|$)"
                        mask &= s.str.contains(pattern, case=False, regex=True, na=False)
                else:
                    mask &= s.isin([str(x) for x in sel])

# symptom filters
sel_del = st.session_state.get("delight", ["All"])
sel_det = st.session_state.get("detract", ["All"])
if isinstance(sel_del, list) and "All" not in sel_del and existing_delighter_columns:
    mask &= d0[existing_delighter_columns].isin(sel_del).any(axis=1)
if isinstance(sel_det, list) and "All" not in sel_det and existing_detractor_columns:
    mask &= d0[existing_detractor_columns].isin(sel_det).any(axis=1)

# keyword
keyword = (st.session_state.get("kw") or "").strip()
if keyword and "Verbatim" in d0.columns:
    mask &= d0["Verbatim"].astype("string").fillna("").str.contains(keyword, case=False, na=False)

filtered = d0[mask].copy()
filter_s = time.perf_counter() - t_filter0

# ============================================================
# Active filter summary panel
# ============================================================
def _summarize_active_filters() -> list[tuple[str, str]]:
    items = []
    if tf != "All Time":
        if tf == "Custom Range" and start_date and end_date:
            items.append(("Timeframe", f"{start_date} → {end_date}"))
        else:
            items.append(("Timeframe", tf))
    if isinstance(sr_sel, list) and "All" not in sr_sel:
        items.append(("Stars", ", ".join([str(x) for x in sr_sel])))
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + extra_cols:
        # range filters
        rk = f"f_{col}_range"
        if rk in st.session_state and isinstance(st.session_state.get(rk), (tuple, list)) and len(st.session_state.get(rk)) == 2:
            lo, hi = st.session_state.get(rk)
            items.append((col, f"{float(lo):g} → {float(hi):g}"))
            continue

        # contains filters
        ck = f"f_{col}_contains"
        cv = (st.session_state.get(ck) or "").strip()
        if cv:
            items.append((col, f"contains: {cv}"))
            continue

        # multiselect filters
        sel = st.session_state.get(f"f_{col}", ["ALL"])
        if isinstance(sel, list) and sel and "ALL" not in sel:
            items.append((col, ", ".join([str(x) for x in sel[:4]]) + ("" if len(sel) <= 4 else f" +{len(sel)-4}")))
    if isinstance(sel_del, list) and "All" not in sel_del:
        items.append(("Delighters", ", ".join([str(x) for x in sel_del[:3]]) + ("" if len(sel_del) <= 3 else f" +{len(sel_del)-3}")))
    if isinstance(sel_det, list) and "All" not in sel_det:
        items.append(("Detractors", ", ".join([str(x) for x in sel_det[:3]]) + ("" if len(sel_det) <= 3 else f" +{len(sel_det)-3}")))
    if keyword:
        items.append(("Keyword", keyword))
    return items

active_items = _summarize_active_filters()
pills = []
for k, v in active_items[:12]:
    pills.append(f"<div class='pill'><span class='muted'>{esc(k)}:</span> {esc(v)}</div>")

st.markdown(
    f"""
<div class="soft-panel">
  <div><b>Active filters</b> • Showing <b>{len(filtered):,}</b> of <b>{len(df_base):,}</b> reviews
  <span class="small-muted"> (filter time: {filter_s:.3f}s)</span>
  </div>
  <div class="pill-row">{''.join(pills) if pills else '<span class="small-muted">None (All data)</span>'}</div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# Navigation / Views (sticky top bar)
# ============================================================
st.markdown("<div id='view-nav-marker'></div>", unsafe_allow_html=True)

view = st.radio(
    "View",
    options=["📊 Dashboard", "📝 All Reviews", "🤖 AI"],
    horizontal=True,
    index=["📊 Dashboard", "📝 All Reviews", "🤖 AI"].index(st.session_state.get("main_view", "📊 Dashboard")) if st.session_state.get("main_view") else 0,
    key="main_view",
    label_visibility="collapsed",
)

# Sticky: reapply after reruns so it never disappears
st_html(
    """
<script>
(function(){
  try{
    const W = window.parent;
    const doc = W.document;

    function setHeaderH(){
      const header = doc.querySelector('header[data-testid="stHeader"]');
      const h = header ? header.getBoundingClientRect().height : 0;
      doc.documentElement.style.setProperty('--stHeaderH', (h || 0) + 'px');
    }

    function applySticky(){
      const marker = doc.getElementById('view-nav-marker');
      if (!marker) return false;

      const radios = Array.from(doc.querySelectorAll('[data-testid="stRadio"]'));
      let target = null;
      for (const r of radios){
        const rel = marker.compareDocumentPosition(r);
        if (rel & Node.DOCUMENT_POSITION_FOLLOWING){
          target = r;
          break;
        }
      }
      if (!target) return false;

      const host = target.closest('div.element-container') || target.parentElement || target;
      if (host) host.classList.add('sticky-topnav-host');
      return true;
    }

    function schedule(){
      setHeaderH();
      applySticky();
      setTimeout(applySticky, 60);
      setTimeout(applySticky, 200);
      setTimeout(applySticky, 650);
    }

    schedule();

    if (!W.__swStickyNavObserver){
      let t = null;
      const obs = new W.MutationObserver(() => {
        if (t) return;
        t = setTimeout(() => { t=null; schedule(); }, 120);
      });
      obs.observe(doc.body, {childList:true, subtree:true});
      W.__swStickyNavObserver = obs;
    }

    W.addEventListener('resize', setHeaderH);
  }catch(e){}
})();
</script>
""",
    height=0,
)


st.caption("Tip: **📝 All Reviews** shows individual review cards (with green/red symptom tiles).")

# ============================================================
# Precompute aggregates (only when needed)
# ============================================================
need_dashboard = view.startswith("📊")

if need_dashboard:
    with st.spinner("Analyzing symptoms…"):
        detractors_results_full = analyze_symptoms_fast(filtered, existing_detractor_columns)
        delighters_results_full = analyze_symptoms_fast(filtered, existing_delighter_columns)
        trend_watchouts = _detect_trends(filtered, symptom_cols=[c for c in df_base.columns if str(c).startswith("Symptom")], min_mentions=3)
else:
    detractors_results_full = pd.DataFrame(columns=["Item", "Mentions", "Avg Star", "% Total"])
    delighters_results_full = pd.DataFrame(columns=["Item", "Mentions", "Avg Star", "% Total"])
    trend_watchouts = []

# ============================================================
# Dashboard view
# ============================================================
if view.startswith("📊"):
    st.markdown("## ⭐ Star Rating Metrics")
    st.caption("All metrics below reflect the **currently filtered** dataset.")

    def pct_12(series: pd.Series) -> float:
        s = pd.to_numeric(series, errors="coerce").dropna()
        return float((s <= 2).mean() * 100) if not s.empty else 0.0

    def section_stats(sub: pd.DataFrame) -> tuple[int, float, float]:
        cnt = len(sub)
        if cnt == 0 or "Star Rating" not in sub.columns:
            return 0, 0.0, 0.0
        avg = float(pd.to_numeric(sub["Star Rating"], errors="coerce").mean())
        pct = pct_12(sub["Star Rating"])
        return cnt, avg, pct

    if "Seeded" in filtered.columns:
        seed_mask = filtered["Seeded"].astype("string").str.upper().eq("YES")
    else:
        seed_mask = pd.Series(False, index=filtered.index)

    all_cnt, all_avg, all_low = section_stats(filtered)
    org = filtered[~seed_mask]
    seed = filtered[seed_mask]
    org_cnt, org_avg, org_low = section_stats(org)
    seed_cnt, seed_avg, seed_low = section_stats(seed)

    baseline_avg = float(all_avg) if isinstance(all_avg, (int, float)) and np.isfinite(all_avg) else 0.0

    def _mini_bar_html(pct: float) -> str:
        try:
            w = max(0.0, min(100.0, float(pct)))
        except Exception:
            w = 0.0
        return f"<div class='mini-bar'><div style='width:{w:.1f}%;'></div></div>"

    def card_html(title: str, count: int, avg: float, pct_low: float, subtitle: str = "") -> str:
        return textwrap.dedent(f"""
        <div class="metric-card">
          <div class="metric-head">
            <div class="metric-title">{_html.escape(title)}</div>
            <div class="metric-sub">{_html.escape(subtitle)}</div>
          </div>
          <div class="metric-row">
            <div class="metric-box">
              <div class="metric-label">Count</div>
              <div class="metric-kpi">{count:,}</div>
            </div>
            <div class="metric-box">
              <div class="metric-label">Avg ★</div>
              <div class="metric-kpi">{avg:.2f}</div>
            </div>
            <div class="metric-box">
              <div class="metric-label">% 1–2★</div>
              <div class="metric-kpi">{pct_low:.1f}%</div>
              {_mini_bar_html(pct_low)}
            </div>
          </div>
        </div>
        """).strip()

    st.markdown(
        (
            '<div class="metrics-grid">'
            + card_html("All Reviews", all_cnt, all_avg, all_low, subtitle="Current filters")
            + card_html("Organic (non-Seeded)", org_cnt, org_avg, org_low, subtitle="Seeded ≠ YES")
            + card_html("Seeded", seed_cnt, seed_avg, seed_low, subtitle="Seeded = YES")
            + "</div>"
        ),
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ---------- Country × Source breakdown ----------
    st.markdown("## 🧭 Country × Source Breakdown")
    st.caption("Color = Avg ★ (red→green), label = Count. This ties average and volume together in one view.")

    if "Country" in filtered.columns and "Source" in filtered.columns and "Star Rating" in filtered.columns:
        cA, cB, cC, cD = st.columns([1, 1, 1, 1])
        with cA:
            top_countries = st.number_input("Top Countries", 3, 30, value=10, step=1, key="cx_top_countries")
        with cB:
            top_sources = st.number_input("Top Sources", 3, 30, value=10, step=1, key="cx_top_sources")
        with cC:
            min_cell_n = st.number_input("Min count per cell", 0, 50, value=0, step=1, key="cx_min_n")
        with cD:
            show_table = st.toggle("Show table under chart", value=False, key="cx_show_table")

        d = filtered.copy()
        d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
        d = d.dropna(subset=["Star Rating"])
        if d.empty:
            st.info("No rating data available after filtering.")
        else:
            topC = d["Country"].astype("string").value_counts().head(int(top_countries)).index.tolist()
            topS = d["Source"].astype("string").value_counts().head(int(top_sources)).index.tolist()
            d = d[d["Country"].astype("string").isin(topC) & d["Source"].astype("string").isin(topS)]

            grp = d.groupby(["Country", "Source"])["Star Rating"].agg(count="count", mean="mean").reset_index()
            count_m = grp.pivot(index="Country", columns="Source", values="count").fillna(0).astype(int)
            mean_m = grp.pivot(index="Country", columns="Source", values="mean").astype(float)

            if int(min_cell_n) > 0:
                mean_m = mean_m.where(count_m >= int(min_cell_n))

            hm = mean_m.copy()
            hm = hm.reindex(index=sorted(hm.index), columns=sorted(hm.columns))
            count_m = count_m.reindex(index=hm.index, columns=hm.columns).fillna(0).astype(int)

            z = hm.values
            x = list(hm.columns)
            y = list(hm.index)
            counts = count_m.values

            fig = go.Figure()
            fig.add_trace(
                go.Heatmap(
                    z=z,
                    x=x,
                    y=y,
                    customdata=counts,
                    colorscale="RdYlGn",
                    colorbar=dict(title="Avg ★"),
                    zmin=1.0,
                    zmax=5.0,
                    hovertemplate="Country=%{y}<br>Source=%{x}<br>Avg ★=%{z:.2f}<br>Count=%{customdata}<extra></extra>",
                )
            )

            # overlay counts as text
            xs_light, ys_light, texts_light = [], [], []
            xs_dark, ys_dark, texts_dark = [], [], []
            for yi, country in enumerate(y):
                for xi, source in enumerate(x):
                    c = int(counts[yi][xi])
                    if c == 0:
                        continue
                    v = z[yi][xi]
                    is_dark = False
                    try:
                        if v is not None and not (isinstance(v, float) and np.isnan(v)):
                            vv = float(v)
                            if vv <= 2.3 or vv >= 4.5:
                                is_dark = True
                    except Exception:
                        is_dark = False
                    if is_dark:
                        xs_dark.append(source); ys_dark.append(country); texts_dark.append(str(c))
                    else:
                        xs_light.append(source); ys_light.append(country); texts_light.append(str(c))

            if xs_light:
                fig.add_trace(go.Scatter(x=xs_light, y=ys_light, mode="text", text=texts_light, textfont=dict(size=12, color="black"), hoverinfo="skip"))
            if xs_dark:
                fig.add_trace(go.Scatter(x=xs_dark, y=ys_dark, mode="text", text=texts_dark, textfont=dict(size=12, color="white"), hoverinfo="skip"))

            fig.update_layout(
                template=PLOTLY_TEMPLATE,
                margin=dict(l=90, r=20, t=40, b=60),
                height=560,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            style_plotly(fig)
            st.plotly_chart(fig, use_container_width=True)

            if show_table:
                with st.expander("📋 Table view (Count + Avg ★)", expanded=True):
                    st.markdown("**Counts**")
                    st.dataframe(count_m, use_container_width=True)
                    st.markdown("**Avg ★**")
                    st.dataframe(mean_m.round(2), use_container_width=True)
    else:
        st.info("Need Country, Source, and Star Rating columns to compute this breakdown.")

    # ---------- Symptom Tables ----------
    headL, headR = st.columns([1, 0.22])
    with headL:
        st.markdown("## 🩺 Symptom Tables")
    with headR:
        with st.popover("ℹ️ Net Hit"):
            st.markdown(
                """**Net Hit (WIP)** estimates how much each theme contributes to your current **gap from 5★**.

**Steps**
1) Compute current average ★ from the filtered dataset  
2) Compute the total gap: `Gap = 5 − Avg★`  
3) For each symptom, compute its share of mentions within that table  
4) `Net Hit = Gap × (Symptom Mentions / Total Mentions)`

**Interpretation**
- Higher Net Hit = larger rating-impact lever (directionally)
- Net Hits sum to the total gap (within the table)
- For **Detractors**, we show Net Hit as a **positive** "rating drag" share (bigger = worse). If you prefer signed values, treat detractor Net Hit as negative.
- This is a proportional allocation model, not causal regression."""
            )

    gap_to_5 = max(0.0, 5.0 - baseline_avg)

    table_limit = st.selectbox("Rows to preview", options=[25, 50, 100], index=1, key="symptom_table_limit")

    def _add_net_hit(tbl: pd.DataFrame) -> pd.DataFrame:
        if tbl is None or tbl.empty:
            return tbl
        d = tbl.copy()
        d["Mentions"] = pd.to_numeric(d.get("Mentions"), errors="coerce").fillna(0).astype(int)
        d["Avg Star"] = pd.to_numeric(d.get("Avg Star"), errors="coerce")
        total_mentions = float(d["Mentions"].sum()) if d["Mentions"].sum() else 0.0
        if total_mentions > 0:
            d["Net Hit"] = (gap_to_5 * (d["Mentions"] / total_mentions)).round(3)
        else:
            d["Net Hit"] = 0.0
        cols = [c for c in ["Item", "Mentions", "% Total", "Avg Star", "Net Hit"] if c in d.columns]
        return d[cols]

    detractors_full = _add_net_hit(detractors_results_full)
    delighters_full = _add_net_hit(delighters_results_full)

    detractors_preview = detractors_full.head(int(table_limit)) if detractors_full is not None else detractors_full
    delighters_preview = delighters_full.head(int(table_limit)) if delighters_full is not None else delighters_full

    view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0, key="symptom_table_view_mode")

    def _styled_table(df_in: pd.DataFrame):
        if df_in is None or df_in.empty:
            return df_in

        def style_avg(v):
            if pd.isna(v):
                return ""
            try:
                vv = float(v)
                # Requested: only Avg Star colored; threshold 4.5
                if vv >= 4.5:
                    return "color:var(--ok);font-weight:800;"
                return "color:var(--bad);font-weight:800;"
            except Exception:
                return ""

        sty = df_in.style
        if "Avg Star" in df_in.columns:
            sty = sty.applymap(style_avg, subset=["Avg Star"])
        # Do NOT color other columns (keeps text readable in both themes)
        sty = sty.format({"Avg Star": "{:.2f}", "Net Hit": "{:.3f}"})
        return sty

    def _full_table_actions(label: str, full_df: pd.DataFrame):
        if full_df is None or full_df.empty:
            st.info("No data.")
            return
        if len(full_df) > int(table_limit):
            if st.button(f"View full {label} table", key=f"view_full_{label}"):
                st.dataframe(_styled_table(full_df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(_styled_table(full_df), use_container_width=True, hide_index=True)

    if view_mode == "Split":
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### All Detractors")
            if detractors_preview is None or detractors_preview.empty:
                st.info("No detractor symptoms available.")
            else:
                st.dataframe(_styled_table(detractors_preview), use_container_width=True, hide_index=True)
                _full_table_actions("detractors", detractors_full)
        with c2:
            st.markdown("### All Delighters")
            if delighters_preview is None or delighters_preview.empty:
                st.info("No delighter symptoms available.")
            else:
                st.dataframe(_styled_table(delighters_preview), use_container_width=True, hide_index=True)
                _full_table_actions("delighters", delighters_full)
    else:
        tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
        with tab1:
            if detractors_preview is None or detractors_preview.empty:
                st.info("No detractor symptoms available.")
            else:
                st.dataframe(_styled_table(detractors_preview), use_container_width=True, hide_index=True)
                _full_table_actions("detractors", detractors_full)
        with tab2:
            if delighters_preview is None or delighters_preview.empty:
                st.info("No delighter symptoms available.")
            else:
                st.dataframe(_styled_table(delighters_preview), use_container_width=True, hide_index=True)
                _full_table_actions("delighters", delighters_full)

    # Combined download (requested: one button, two tabs) — placed BELOW view full buttons
    if detractors_full is not None and delighters_full is not None and (not detractors_full.empty or not delighters_full.empty):
        out_xlsx = BytesIO()
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
            detractors_full.to_excel(writer, sheet_name="Detractors", index=False)
            delighters_full.to_excel(writer, sheet_name="Delighters", index=False)
        st.download_button(
            "⬇️ Download full Delighter + Detractor tables (Excel)",
            data=out_xlsx.getvalue(),
            file_name="delighters_detractors_full.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ---------- Top Delighters & Detractors ----------
    st.markdown("## 🧩 Top Delighters & Detractors")
    st.caption("Optional: show Mentions as % of total reviews in the current filter range, and/or restrict to Organic only.")

    ctrl1, ctrl2, ctrl3 = st.columns([1.1, 1.2, 1.2])
    with ctrl1:
        top_n = st.slider("Top N", 5, 30, 12, 1, key="top_dd_n")
    with ctrl2:
        show_pct = st.toggle("Show % of reviews", value=False, key="top_dd_pct")
    with ctrl3:
        organic_only = st.toggle("Organic only", value=False, key="top_dd_org")

    dd_df = org if organic_only else filtered
    denom = max(1, len(dd_df))

    det_tbl = analyze_symptoms_fast(dd_df, existing_detractor_columns)
    del_tbl = analyze_symptoms_fast(dd_df, existing_delighter_columns)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Top Detractors**")
        det = det_tbl.head(int(top_n)) if det_tbl is not None else pd.DataFrame()
        if det.empty:
            st.info("No detractor symptoms available.")
        else:
            det = det.copy()
            det["Mentions"] = pd.to_numeric(det.get("Mentions"), errors="coerce").fillna(0)
            det["Mentions %"] = (det["Mentions"] / denom * 100.0)
            x = det["Mentions %"][::-1] if show_pct else det["Mentions"][::-1]
            x_label = "Mentions (% of reviews)" if show_pct else "Mentions (count)"
            hover = "%{customdata[2]}<br>Mentions: %{customdata[0]:,.0f}<br>Mentions %: %{customdata[1]:.1f}%<extra></extra>"
            fig_det = go.Figure(
                go.Bar(
                    x=x,
                    y=det["Item"][::-1],
                    orientation="h",
                    opacity=0.88,
                    customdata=np.stack([det["Mentions"].to_numpy()[::-1], det["Mentions %"].to_numpy()[::-1], det["Item"].astype(str).to_numpy()[::-1]], axis=1),
                    hovertemplate=hover,
                )
            )
            fig_det.update_layout(template=PLOTLY_TEMPLATE, margin=dict(l=170, r=20, t=20, b=40), height=460, xaxis=dict(title=x_label),
                                  paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            style_plotly(fig_det)
            st.plotly_chart(fig_det, use_container_width=True)

    with c2:
        st.markdown("**Top Delighters**")
        deli = del_tbl.head(int(top_n)) if del_tbl is not None else pd.DataFrame()
        if deli.empty:
            st.info("No delighter symptoms available.")
        else:
            deli = deli.copy()
            deli["Mentions"] = pd.to_numeric(deli.get("Mentions"), errors="coerce").fillna(0)
            deli["Mentions %"] = (deli["Mentions"] / denom * 100.0)
            x = deli["Mentions %"][::-1] if show_pct else deli["Mentions"][::-1]
            x_label = "Mentions (% of reviews)" if show_pct else "Mentions (count)"
            hover = "%{customdata[2]}<br>Mentions: %{customdata[0]:,.0f}<br>Mentions %: %{customdata[1]:.1f}%<extra></extra>"
            fig_del = go.Figure(
                go.Bar(
                    x=x,
                    y=deli["Item"][::-1],
                    orientation="h",
                    opacity=0.88,
                    customdata=np.stack([deli["Mentions"].to_numpy()[::-1], deli["Mentions %"].to_numpy()[::-1], deli["Item"].astype(str).to_numpy()[::-1]], axis=1),
                    hovertemplate=hover,
                )
            )
            fig_del.update_layout(template=PLOTLY_TEMPLATE, margin=dict(l=170, r=20, t=20, b=40), height=460, xaxis=dict(title=x_label),
                                  paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            style_plotly(fig_del)
            st.plotly_chart(fig_del, use_container_width=True)

    # ---------- Cumulative Avg ★ Over Time by Region (Weighted) ----------
    st.markdown("## 📈 Cumulative Avg ★ Over Time by Region (Weighted)")
    st.caption("Includes subtle volume bars (review count per day) on a secondary axis.")

    if "Review Date" not in filtered.columns or "Star Rating" not in filtered.columns:
        st.info("Need Review Date and Star Rating columns for the cumulative chart.")
    else:
        data = filtered.copy()
        data["Star Rating"] = pd.to_numeric(data["Star Rating"], errors="coerce")
        data = data.dropna(subset=["Star Rating"])
        data["Review Date"] = pd.to_datetime(data["Review Date"], errors="coerce")
        data = data.dropna(subset=["Review Date"])

        if data.empty:
            st.info("No time-series data after filtering.")
        else:
            region_col = "Country" if "Country" in data.columns else None
            if region_col is None:
                st.info("Need a Country column to break down regions.")
            else:
                top_regions = data[region_col].astype("string").value_counts().head(8).index.tolist()
                data = data[data[region_col].astype("string").isin(top_regions)]

                c1, c2, c3 = st.columns([1.1, 1.1, 1.2])
                with c1:
                    smooth = st.selectbox("Smoothing", options=["None", "7-day", "14-day"], index=1, key="cum_smooth")
                with c2:
                    show_overall = st.toggle("Show overall", value=True, key="cum_show_overall")
                with c3:
                    show_volume = st.toggle("Show volume bars", value=True, key="cum_show_volume")

                data["date"] = data["Review Date"].dt.date

                grp = data.groupby(["date", region_col])["Star Rating"].agg(cnt="count", sum="sum").reset_index()

                # overall daily volume
                vol = grp.groupby("date")["cnt"].sum().reset_index().sort_values("date")
                vol["cum_cnt"] = vol["cnt"].cumsum()

                fig = make_subplots(specs=[[{"secondary_y": True}]])
                # volume bars (subtle)
                if show_volume:
                    fig.add_trace(
                        go.Bar(
                            x=vol["date"],
                            y=vol["cnt"],
                            name="Daily volume",
                            opacity=0.22,
                            marker=dict(color="rgba(148,163,184,0.55)"),
                            hovertemplate="Date: %{x}<br>Reviews: %{y}<extra></extra>",
                        ),
                        secondary_y=True,
                    )

                for r in top_regions:
                    sub = grp[grp[region_col].astype("string") == str(r)].sort_values("date")
                    if sub.empty:
                        continue
                    sub["cum_cnt"] = sub["cnt"].cumsum()
                    sub["cum_sum"] = sub["sum"].cumsum()
                    sub["Cumulative Avg ★"] = sub["cum_sum"] / sub["cum_cnt"]

                    y = sub["Cumulative Avg ★"].to_numpy()
                    if smooth != "None" and len(y) > 2:
                        win = 7 if smooth == "7-day" else 14
                        y = pd.Series(y).rolling(window=win, min_periods=1).mean().to_numpy()

                    fig.add_trace(
                        go.Scatter(
                            x=sub["date"],
                            y=y,
                            mode="lines",
                            name=str(r),
                            hovertemplate=(
                                f"{region_col}: {r}<br>"
                                "Date: %{x}<br>"
                                "Cumulative Avg ★: %{y:.3f}<br>"
                                "Cum N: %{customdata}<extra></extra>"
                            ),
                            customdata=sub["cum_cnt"],
                        ),
                        secondary_y=False,
                    )

                if show_overall:
                    overall = grp.groupby("date").agg(cnt=("cnt", "sum"), sum=("sum", "sum")).reset_index().sort_values("date")
                    overall["cum_cnt"] = overall["cnt"].cumsum()
                    overall["cum_sum"] = overall["sum"].cumsum()
                    overall["Cumulative Avg ★"] = overall["cum_sum"] / overall["cum_cnt"]

                    y = overall["Cumulative Avg ★"].to_numpy()
                    if smooth != "None" and len(y) > 2:
                        win = 7 if smooth == "7-day" else 14
                        y = pd.Series(y).rolling(window=win, min_periods=1).mean().to_numpy()

                    fig.add_trace(
                        go.Scatter(
                            x=overall["date"],
                            y=y,
                            mode="lines",
                            name="Overall",
                            line=dict(width=4),
                            hovertemplate=(
                                "Overall<br>Date: %{x}<br>"
                                "Cumulative Avg ★: %{y:.3f}<br>"
                                "Cum N: %{customdata}<extra></extra>"
                            ),
                            customdata=overall["cum_cnt"],
                        ),
                        secondary_y=False,
                    )

                fig.update_layout(
                    template=PLOTLY_TEMPLATE,
                    margin=dict(l=40, r=20, t=30, b=30),
                    height=520,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                    barmode="overlay",
                )
                fig.update_yaxes(title_text="Cumulative Avg ★", range=[1.0, 5.2], showgrid=True, gridcolor=PLOTLY_GRIDCOLOR, secondary_y=False)
                fig.update_yaxes(title_text="Reviews/day", showgrid=False, secondary_y=True, rangemode="tozero", showticklabels=False)

                style_plotly(fig)
                st.plotly_chart(fig, use_container_width=True)

    # ---------- Watchouts ----------
    if trend_watchouts:
        st.markdown("## ⚠️ Watchouts & Recent Movement (local)")
        st.write("")  # spacing from above section (requested)
        for ln in trend_watchouts:
            st.warning(ln)

    # ---------- Opportunity Matrix (bottom) ----------
    st.markdown("## 🎯 Opportunity Matrix")
    st.caption("Mentions vs Avg ★. Fix high-mention low-star detractors first; amplify high-mention high-star delighters.")

    baseline_overall = baseline_avg

    tab_det, tab_del = st.tabs(["Detractors", "Delighters"])

    def _opportunity_scatter(tbl: pd.DataFrame, kind: str, baseline: float):
        if tbl is None or tbl.empty:
            st.info("No data available.")
            return

        d = tbl.copy()
        d["Mentions"] = pd.to_numeric(d.get("Mentions"), errors="coerce").fillna(0)
        d["Avg Star"] = pd.to_numeric(d.get("Avg Star"), errors="coerce")
        d = d.dropna(subset=["Avg Star"])
        if d.empty:
            st.info("No data available.")
            return

        x = d["Mentions"].astype(float).to_numpy()
        y = d["Avg Star"].astype(float).to_numpy()
        symptom_names = d["Item"].astype(str).to_numpy()

        if kind == "detractors":
            score = x * np.clip(baseline - y, 0, None)
            table_label = "Fix first (high mentions × below-baseline ★)"
        else:
            score = x * np.clip(y - baseline, 0, None)
            table_label = "Amplify (high mentions × above-baseline ★)"

        c1, c2, c3 = st.columns([1.1, 1.5, 1.6])
        show_labels = c1.toggle("Show labels", value=False, key=f"opp_show_labels_{kind}")
        max_labels = int(min(25, len(d)))
        label_default = int(min(10, max_labels))
        label_n = c2.slider("Label top N", 0, max_labels, value=label_default, key=f"opp_label_n_{kind}", disabled=not show_labels)
        size_by_mentions = c3.toggle("Bubble size = Mentions", value=True, key=f"opp_size_mentions_{kind}")

        labels = np.array([""] * len(d), dtype=object)
        if show_labels and label_n > 0:
            top_idx = np.argsort(-score)[:label_n]
            labels[top_idx] = symptom_names[top_idx]

        if size_by_mentions and np.nanmax(x) > 0:
            size = (np.sqrt(x) / (np.sqrt(np.nanmax(x)) + 1e-9)) * 28 + 10
        else:
            size = np.full_like(x, 14.0, dtype=float)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="markers+text" if show_labels else "markers",
                text=labels,
                textposition="top center",
                textfont=dict(size=11),
                customdata=np.stack([symptom_names], axis=1),
                # ALWAYS show symptom name on hover (requested)
                hovertemplate="%{customdata[0]}<br>Mentions=%{x:.0f}<br>Avg ★=%{y:.2f}<extra></extra>",
                marker=dict(size=size, opacity=0.78, line=dict(width=1, color="rgba(148,163,184,0.45)")),
            )
        )

        fig.add_hline(y=baseline, line_dash="dash", opacity=0.6)
        fig.add_vline(x=float(np.median(x)), line_dash="dot", opacity=0.35)

        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            margin=dict(l=40, r=20, t=20, b=40),
            height=520,
            xaxis_title="Mentions",
            yaxis_title="Avg ★",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            uniformtext_minsize=9,
            uniformtext_mode="hide",
        )

        style_plotly(fig)
        st.plotly_chart(fig, use_container_width=True)

        d2 = d.copy()
        d2["Score"] = score
        d2 = d2.sort_values("Score", ascending=False).head(15)
        with st.expander(f"📋 {table_label}", expanded=False):
            st.dataframe(d2[["Item", "Mentions", "Avg Star", "Score"]], use_container_width=True, hide_index=True)

    with tab_det:
        _opportunity_scatter(detractors_results_full, "detractors", baseline_overall)
    with tab_del:
        _opportunity_scatter(delighters_results_full, "delighters", baseline_overall)

# ============================================================
# Reviews view
# ============================================================
if view.startswith("📝"):
    st.markdown("## 📝 Reviews (in filter criteria)")
    st.caption(
        "Only reviews that match the **CURRENT** filter criteria are shown below. "
        "Change filters in the sidebar and this list updates automatically."
    )

    # ---- Sort (fast, e-commerce style) ----
    sort_options = []
    has_date = "Review Date" in filtered.columns
    has_star = "Star Rating" in filtered.columns
    if has_date:
        sort_options += ["Newest → Oldest", "Oldest → Newest"]
    if has_star:
        sort_options += ["Rating: High → Low", "Rating: Low → High"]
    if not sort_options:
        sort_options = ["Default order"]

    topL, topR = st.columns([1.2, 1])
    with topL:
        sort_choice = st.selectbox("Sort", options=sort_options, index=0, key="review_sort_choice")
    with topR:
        if not filtered.empty:
            csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "⬇️ Download reviews (filtered)",
                csv_bytes,
                file_name="filtered_reviews.csv",
                mime="text/csv",
                use_container_width=True,
            )

    # Reset paging when sort changes
    st.session_state.setdefault("review_page", 0)
    if st.session_state.get("_review_sort_prev") != sort_choice:
        st.session_state["_review_sort_prev"] = sort_choice
        st.session_state["review_page"] = 0

    # Cache sorted index per filter-state
    sort_cache = st.session_state.setdefault("_review_sort_cache", {})

    # Filter hash
    try:
        _filters_hash_r = hashlib.sha1(
            json.dumps(
                {
                    "tf": tf,
                    "tf_range": st.session_state.get("tf_range"),
                    "sr": sr_sel,
                    "kw": keyword,
                    "delight": sel_del,
                    "detract": sel_det,
                    "std": {c: st.session_state.get(f"f_{c}") for c in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]},
                    "extra_cols": extra_cols,
                    "extra": {c: st.session_state.get(f"f_{c}") or st.session_state.get(f"f_{c}_range") for c in extra_cols},
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
    except Exception:
        _filters_hash_r = str(len(filtered))

    cache_key = f"{st.session_state.get('_dataset_sig','')}|{_filters_hash_r}|{sort_choice}|n={len(filtered)}"
    sorted_idx = sort_cache.get(cache_key)

    if sorted_idx is None:
        df_sorted = filtered
        try:
            if sort_choice == "Newest → Oldest" and has_date:
                df_sorted = filtered.sort_values("Review Date", ascending=False, na_position="last", kind="mergesort")
            elif sort_choice == "Oldest → Newest" and has_date:
                df_sorted = filtered.sort_values("Review Date", ascending=True, na_position="last", kind="mergesort")
            elif sort_choice == "Rating: High → Low" and has_star:
                if has_date:
                    df_sorted = filtered.sort_values(["Star Rating", "Review Date"], ascending=[False, False], na_position="last", kind="mergesort")
                else:
                    df_sorted = filtered.sort_values(["Star Rating"], ascending=[False], na_position="last", kind="mergesort")
            elif sort_choice == "Rating: Low → High" and has_star:
                if has_date:
                    df_sorted = filtered.sort_values(["Star Rating", "Review Date"], ascending=[True, False], na_position="last", kind="mergesort")
                else:
                    df_sorted = filtered.sort_values(["Star Rating"], ascending=[True], na_position="last", kind="mergesort")
        except Exception:
            df_sorted = filtered
        try:
            sorted_idx = df_sorted.index.to_numpy()
        except Exception:
            sorted_idx = None
        sort_cache[cache_key] = sorted_idx

    try:
        reviews_df = filtered.loc[sorted_idx] if sorted_idx is not None else filtered
    except Exception:
        reviews_df = filtered

    # ---- Pagination ----
    reviews_per_page = int(st.session_state.get("rpp", 10))
    total_reviews_count = len(reviews_df)
    total_pages = max(1, int(np.ceil(total_reviews_count / reviews_per_page))) if reviews_per_page > 0 else 1

    current_page = int(st.session_state.get("review_page", 0))
    current_page = max(0, min(current_page, total_pages - 1))
    st.session_state["review_page"] = current_page

    start_index = current_page * reviews_per_page
    end_index = start_index + reviews_per_page
    paginated = reviews_df.iloc[start_index:end_index]

    if paginated.empty:
        st.warning("No reviews match the selected criteria.")
    else:
        for _, row in paginated.iterrows():
            review_text = row.get("Verbatim", pd.NA)
            review_text = "" if pd.isna(review_text) else clean_text(review_text)
            display_review_html = highlight_html(review_text, keyword)

            date_val = row.get("Review Date", pd.NaT)
            if pd.isna(date_val):
                date_str = "-"
            else:
                try:
                    date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
                except Exception:
                    date_str = "-"

            def chips(row_i, columns, css_class):
                items = []
                for c in columns:
                    val = row_i.get(c, pd.NA)
                    if pd.isna(val):
                        continue
                    s = str(val).strip()
                    if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}:
                        continue
                    items.append(f'<span class="badge {css_class}">{_html.escape(s)}</span>')
                return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

            delighter_message = chips(row, existing_delighter_columns, "pos")
            detractor_message = chips(row, existing_detractor_columns, "neg")

            star_val = row.get("Star Rating", 0)
            try:
                star_int = int(star_val) if pd.notna(star_val) else 0
            except Exception:
                star_int = 0

            st.markdown(
                f"""
                <div class="review-card">
                    <p><strong>Source:</strong> {esc(row.get('Source'))} | <strong>Model:</strong> {esc(row.get('Model (SKU)'))}</p>
                    <p><strong>Country:</strong> {esc(row.get('Country'))} | <strong>Date:</strong> {esc(date_str)}</p>
                    <p><strong>Rating:</strong> {'⭐' * star_int} ({esc(row.get('Star Rating'))}/5)</p>
                    <p><strong>Review:</strong> {display_review_html}</p>
                    <div><strong>Delighter Symptoms:</strong> {delighter_message}</div>
                    <div><strong>Detractor Symptoms:</strong> {detractor_message}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ---- Pager ----
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
        st.markdown(
            f"<div style='text-align:center;font-weight:700;'>Page {current_page + 1} of {total_pages} • Showing {showing_from}–{showing_to} of {total_reviews_count}</div>",
            unsafe_allow_html=True,
        )
    with p4:
        if st.button("Next ➡", disabled=current_page >= total_pages - 1):
            st.session_state["review_page"] = min(current_page + 1, total_pages - 1)
            st.rerun()
    with p5:
        if st.button("Last ⏭", disabled=current_page >= total_pages - 1):
            st.session_state["review_page"] = total_pages - 1
            st.rerun()

# ============================================================
# AI view — simplified, reliable chat + 1 executive summary button
# ============================================================
if view.startswith("🤖"):
    st.markdown("## 🤖 AI — Product & Consumer Insights")
    st.caption("Ask anything. The assistant is grounded in the **currently filtered** dataset.")

    # Sidebar: minimal settings (kept out of the main page)
    with st.sidebar.expander("🤖 AI Settings", expanded=False):
        st.session_state.setdefault("ai_model", "gpt-4o-mini")
        st.session_state.setdefault("ai_temp", 0.2)
        st.session_state.setdefault("ai_send_quotes", True)
        st.session_state.setdefault("ai_quote_k", 10)
        st.session_state.setdefault("ai_cap", 1500)

        st.selectbox("Model", options=["gpt-4o-mini", "gpt-4o", "gpt-4.1"], key="ai_model")
        st.slider("Creativity (temperature)", 0.0, 1.0, float(st.session_state.get("ai_temp", 0.2)), 0.1, key="ai_temp")
        st.toggle("Include evidence quotes (masked)", value=bool(st.session_state.get("ai_send_quotes", True)), key="ai_send_quotes")
        st.slider("Quotes to retrieve", 4, 18, int(st.session_state.get("ai_quote_k", 10)), 1, key="ai_quote_k")
        st.number_input("Max reviews in retrieval corpus", 200, 8000, int(st.session_state.get("ai_cap", 1500)), 100, key="ai_cap")

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
    send_quotes = bool(st.session_state.get("ai_send_quotes", True))
    quote_k = int(st.session_state.get("ai_quote_k", 10))
    cap = int(st.session_state.get("ai_cap", 1500))

    st.markdown(
        f"""
<div class="soft-panel" style="margin-top: 8px;">
  <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;">
    <div style="font-weight:850;">{"🟢 Remote AI ready" if remote_ready else "🟡 Add API key to enable remote AI (local insights still work)"}</div>
    <div class="small-muted">Model: <b>{_html.escape(model)}</b></div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # ---- Local retrieval index (TF-IDF) cached per filter-hash ----
    def _filter_hash_for_ai() -> str:
        try:
            payload = {
                "dataset": st.session_state.get("_dataset_sig"),
                "filters": {
                    "tf": tf,
                    "tf_range": st.session_state.get("tf_range"),
                    "sr": sr_sel,
                    "kw": keyword,
                    "delight": sel_del,
                    "detract": sel_det,
                    "std": {c: st.session_state.get(f"f_{c}") for c in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]},
                    "extra_cols": extra_cols,
                    "extra": {c: st.session_state.get(f"f_{c}") or st.session_state.get(f"f_{c}_range") for c in extra_cols},
                },
                "n": int(len(filtered)),
            }
            return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        except Exception:
            return str(len(filtered))

    def _mask_pii(s: str) -> str:
        if not s:
            return s
        return PII_PAT.sub("[REDACTED]", s)

    def _build_ai_corpus(df_in: pd.DataFrame, max_rows: int) -> tuple[list[str], list[dict]]:
        if df_in.empty:
            return [], []
        df_use = df_in
        if len(df_use) > max_rows:
            df_use = df_use.sample(max_rows, random_state=42)
        texts = []
        meta = []
        for _, r in df_use.iterrows():
            verb = r.get("Verbatim", "")
            verb = "" if pd.isna(verb) else str(verb)
            verb = _mask_pii(verb)
            # Light metadata prefix helps retrieval
            star = r.get("Star Rating", "")
            country = r.get("Country", "")
            source = r.get("Source", "")
            dtv = r.get("Review Date", "")
            try:
                dtv = pd.to_datetime(dtv).strftime("%Y-%m-%d") if pd.notna(dtv) else ""
            except Exception:
                dtv = ""
            prefix = f"[★{star}] [{country}] [{source}] [{dtv}] "
            txt = (prefix + verb).strip()
            texts.append(txt)
            meta.append({"star": star, "country": country, "source": source, "date": dtv, "text": verb})
        return texts, meta

    def _get_local_index(df_in: pd.DataFrame) -> tuple[Optional[TfidfVectorizer], Any, list[str], list[dict]]:
        cache = st.session_state.setdefault("_ai_local_index", {})
        h = _filter_hash_for_ai()
        if h in cache:
            return cache[h]["vec"], cache[h]["mat"], cache[h]["texts"], cache[h]["meta"]

        texts, meta = _build_ai_corpus(df_in, cap)
        if not texts:
            cache[h] = {"vec": None, "mat": None, "texts": [], "meta": []}
            return None, None, [], []

        vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=35000)
        mat = vec.fit_transform(texts)
        cache[h] = {"vec": vec, "mat": mat, "texts": texts, "meta": meta}
        return vec, mat, texts, meta

    def _retrieve_quotes(query: str, df_in: pd.DataFrame, k: int) -> list[dict]:
        vec, mat, texts, meta = _get_local_index(df_in)
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

    def _build_knowledge_pack(df_in: pd.DataFrame) -> dict:
        # Product profile
        prof = infer_product_profile(df_in, source_label)
        # Key CSAT metrics
        cnt, avg, low = section_stats(df_in)
        seeded_mask_local = df_in["Seeded"].astype("string").str.upper().eq("YES") if "Seeded" in df_in.columns else pd.Series(False, index=df_in.index)
        org_local = df_in[~seeded_mask_local]
        seed_local = df_in[seeded_mask_local]
        org_cnt2, org_avg2, org_low2 = section_stats(org_local)
        seed_cnt2, seed_avg2, seed_low2 = section_stats(seed_local)

        det_tbl2 = analyze_symptoms_fast(df_in, existing_detractor_columns).head(12).to_dict("records") if existing_detractor_columns else []
        del_tbl2 = analyze_symptoms_fast(df_in, existing_delighter_columns).head(12).to_dict("records") if existing_delighter_columns else []

        return {
            "product_profile": prof,
            "csat": {
                "count": cnt,
                "avg_star": round(avg, 3),
                "pct_1_2": round(low, 2),
                "organic": {"count": org_cnt2, "avg_star": round(org_avg2, 3), "pct_1_2": round(org_low2, 2)},
                "seeded": {"count": seed_cnt2, "avg_star": round(seed_avg2, 3), "pct_1_2": round(seed_low2, 2)},
            },
            "top_detractors": det_tbl2,
            "top_delighters": del_tbl2,
        }

    def _openai_chat_http(api_key: str, model: str, messages: list[dict], temperature: float | None = None, max_tokens: int = 900, timeout_s: int = 60) -> str:
        import requests
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "max_tokens": int(max_tokens)}
        if temperature is not None and model_supports_temperature(model):
            payload["temperature"] = float(temperature)

        last_err = None
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

    def _local_answer(question: str, knowledge: dict, quotes: list[dict]) -> str:
        # A simple deterministic fallback using aggregates
        csat = knowledge.get("csat", {})
        prof = knowledge.get("product_profile", {})
        det = knowledge.get("top_detractors", [])[:6]
        deli = knowledge.get("top_delighters", [])[:6]

        lines = []
        lines.append(f"**Local CSAT Brief (remote AI unavailable)**")
        lines.append(f"- Reviews in filter: **{csat.get('count', 0):,}**")
        lines.append(f"- Avg ★: **{csat.get('avg_star', 0)}** • % 1–2★: **{csat.get('pct_1_2', 0)}%**")
        if prof:
            guess = prof.get("product_guess") or prof.get("name_guess") or prof.get("model_guess") or "Unknown"
            lines.append(f"- Product guess: **{guess}**")
        if det:
            lines.append("\n**Biggest improvement opportunities (top detractors by mentions):**")
            for r in det:
                lines.append(f"- {r.get('Item')}: {r.get('Mentions')} mentions • Avg ★ {r.get('Avg Star')}")
        if deli:
            lines.append("\n**What consumers love (top delighters by mentions):**")
            for r in deli:
                lines.append(f"- {r.get('Item')}: {r.get('Mentions')} mentions • Avg ★ {r.get('Avg Star')}")
        if quotes:
            lines.append("\n**Most relevant quotes (masked):**")
            for q in quotes[:5]:
                lines.append(f"- [{q['id']}] ★{q.get('star')} {q.get('country')} {q.get('source')} {q.get('date')}: {q.get('text','')[:240]}")
        return "\n".join(lines)

    # ---- Main AI interaction ----
    st.session_state.setdefault("_ai_last_q", "")
    st.session_state.setdefault("_ai_last_answer", "")

    colA, colB = st.columns([1, 0.28])
    with colA:
        st.markdown("### Ask a question")
    with colB:
        if st.button("🧾 Executive summary", use_container_width=True):
            st.session_state["ai_user_q"] = "Provide an executive summary of what consumers love, biggest improvement opportunities, and concrete next steps for engineering. Use the filtered dataset."

    # Show latest answer (ONLY)
    last_q = (st.session_state.get("_ai_last_q") or "").strip()
    last_a = (st.session_state.get("_ai_last_answer") or "").strip()
    if last_a:
        st.markdown("### Latest answer")
        if last_q:
            st.markdown(f"**Q:** {esc(last_q)}")
        st.markdown(last_a)

    # Chat box
    user_q = st.text_area("Your question", value=st.session_state.get("ai_user_q", ""), height=120, key="ai_user_q", placeholder="E.g., What are the biggest improvement opportunities? What are consumers loving about the product?")
    send = st.button("➡️ Send", type="primary")
    if send:
        q = (user_q or "").strip()
        if not q:
            st.warning("Type a question first.")
        else:
            knowledge = _build_knowledge_pack(filtered)
            quotes = _retrieve_quotes(q, filtered, quote_k) if send_quotes else []
            # Build prompt
            sys_prompt = (
                "You are a SharkNinja Consumer Insights + Quality Engineering copilot.\n"
                "You MUST ground your answers in the provided dataset context and retrieved evidence.\n"
                "Be practical, concise, and insight-driven. Quantify (counts, avg★, %1–2★) when possible.\n"
                "When you make a claim supported by evidence quotes, cite the quote IDs like [Q3].\n"
                "If the question is about 'what is this product', infer from model/retailer/source strings and the review text.\n"
                "Never invent data that isn't in the context.\n"
            )
            user_payload = {
                "question": q,
                "dataset_context": knowledge,
                "retrieved_evidence": [
                    {
                        "id": it["id"],
                        "star": it.get("star"),
                        "country": it.get("country"),
                        "source": it.get("source"),
                        "date": it.get("date"),
                        "text": it.get("text"),
                    }
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
                        ans = _openai_chat_http(api_key, model, messages, temperature=temp)
                    except Exception as e:
                        st.warning("Remote AI failed; falling back to local insights.")
                        st.sidebar.caption(f"Last AI error: {str(e)[:200]}")
                        ans = _local_answer(q, knowledge, quotes)
                else:
                    ans = _local_answer(q, knowledge, quotes)

            # Requested: replace previous response; newest stays on top
            st.session_state["_ai_last_q"] = q
            st.session_state["_ai_last_answer"] = ans
            # Clear input (optional)
            # st.session_state["ai_user_q"] = ""
            st.rerun()
