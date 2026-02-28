# starwalk_master_app_v7.py
# Streamlit master app:
# - Accepts either:
#   1) Star Walk scrubbed verbatims Excel/CSV (same behavior as starwalk_ui_test.py), OR
#   2) Axion JSON export -> auto-convert to Star Walk scrubbed verbatims -> dashboard
#
# Improvements in v4:
# - Saved Views / Shareable Filter Presets (export/import + URL param)
# - Smoother/faster filtering with "Apply filters" mode (reduces reruns)
# - Major performance fix: symptom analysis is vectorized (no per-symptom row scans)
# - Removed redundant monthly chart (kept cumulative weighted chart)
# - Country × Source breakdown now unifies Avg ★ (color) + Count (labels)
# - Light mode default is safer (prevents white-on-white + avoids infinite MutationObserver loops)

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
from typing import Any, Dict, List, Optional, Set, Tuple
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

APP_VERSION = "2026-02-28-master-v9"

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

# ---------- Force Light Mode (safer: no infinite attribute mutation) ----------
st_html(
    """
<script>
(function () {
  function setLight() {
    try {
      const html = document.documentElement;
      const cur = html.getAttribute('data-theme') || '';
      if (cur.toLowerCase() === 'light') return; // prevent loops
      html.setAttribute('data-theme','light');
      if (document.body) {
        const bcur = document.body.getAttribute('data-theme') || '';
        if (bcur.toLowerCase() !== 'light') document.body.setAttribute('data-theme','light');
      }
      try { window.localStorage.setItem('theme','light'); } catch(e) {}
      try { html.style.colorScheme = 'light'; } catch(e) {}
    } catch (e) {}
  }
  setLight();
  new MutationObserver(function(muts){
    for (const m of muts) {
      if (m.type === 'attributes' && m.attributeName === 'data-theme') {
        setLight();
        break;
      }
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
</script>
""",
    height=0,
)

# ---------- Global CSS (always readable in light) ----------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; color-scheme: light; }
  *, ::before, ::after { box-sizing: border-box; }

  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }

  /* Even if Streamlit flips to dark, keep our light palette to prevent white-on-white. */
  html[data-theme="dark"], body[data-theme="dark"]{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
  }

  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }

  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  section[data-testid="stSidebar"] .block-container { padding-top:.6rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

  .soft-panel{
    background:var(--bg-card);
    border-radius:14px;
    padding:14px 16px;
    box-shadow:0 0 0 1.2px var(--border-strong), 0 6px 12px rgba(15,23,42,0.05);
    margin:10px 0 14px;
  }

  .small-muted{ color:var(--muted); font-size:.9rem; }

  /* --- Metrics cards (responsive; prevents small-screen overflow) --- */
  .metrics-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));
    gap:17px;
  }
  .metric-card{
    background:var(--bg-card);
    border-radius:14px;
    padding:16px;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    color:var(--text);
    min-width:0;
  }
  .metric-card h4{ margin:.2rem 0 .7rem 0; font-size:1.05rem; color:var(--text); }

  .metric-row{
    display:grid;
    /* auto-fit prevents small-screen clipping even when sidebar reduces content width */
    grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
    gap:12px;
    align-items:stretch;
  }

  .metric-box{
    background:var(--bg-tile);
    border:1.6px solid var(--border);
    border-radius:12px;
    padding:12px 10px;
    text-align:center;
    color:var(--text);
    min-width:0;           /* critical: allow CSS-grid children to shrink */
    overflow:hidden;       /* prevent text bleed from pushing rounded corners out */
  }

  .metric-label{
    color:var(--muted);
    font-size:clamp(0.78rem, 1.0vw, 0.85rem);
    line-height:1.15;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }

  .metric-kpi{
    font-weight:800;
    /* Slightly smaller minimum so numbers never clip on smaller laptop widths */
    font-size:clamp(1.10rem, 2.35vw, 1.75rem);
    letter-spacing:-0.01em;
    margin-top:2px;
    color:var(--text);
    line-height:1.05;
    white-space:nowrap;
    /* Never truncate numbers; instead rely on responsive sizing + grid wrapping */
    overflow:visible;
    font-variant-numeric: tabular-nums;
  }

  /* Backstop for very tight layouts */
  @media (max-width: 520px){
    .metric-row{ grid-template-columns:1fr; }
  }

  /* Sticky top navigation (ONLY the main View radio) */
  .sticky-topnav-host{
    position: sticky;
    top: 0;
    z-index: 999;
    margin: 6px 0 12px;
    padding: 6px 0;
    background: linear-gradient(to bottom, var(--bg-app) 60%, rgba(0,0,0,0) 100%);
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
    box-shadow:0 0 0 1.2px var(--border-strong), 0 8px 16px rgba(15,23,42,0.08);
    align-items:center;
  }
  .sticky-topnav-host label[data-baseweb="radio"]{
    margin:0 !important;
    border-radius:999px !important;
    border:1.2px solid var(--border) !important;
    background:var(--bg-tile) !important;
    padding:8px 14px !important;
    font-weight:850 !important;
  }
  .sticky-topnav-host label[data-baseweb="radio"]:hover{ border-color: var(--border-strong) !important; }
  .sticky-topnav-host label[data-baseweb="radio"]:has(input:checked){
    background: rgba(59,130,246,0.12) !important;
    border-color: rgba(59,130,246,0.55) !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.10);
  }

  @media (max-width: 520px){
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

  .review-card{ background:var(--bg-card); border-radius:12px; padding:16px; margin:16px 0 24px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .review-card p{ margin:.25rem 0; line-height:1.5; }
  .badges{ display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
  .badge{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:600; font-size:.85rem; border:1.5px solid transparent; }
  .badge.pos{ background:#ecfdf5; border-color:#86efac; color:#065f46; }
  .badge.neg{ background:#fef2f2; border-color:#fca5a5; color:#7f1d1d; }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }

  .kpi-pill{
    display:inline-block; padding:4px 10px; border-radius:999px;
    border:1.3px solid var(--border);
    background:var(--bg-tile);
    font-weight:650; margin-right:8px;
  }

  .pill-row{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .pill{
    display:inline-flex; align-items:center; gap:8px;
    padding:5px 10px; border-radius:999px;
    border:1.2px solid var(--border);
    background:var(--bg-card);
    font-size:.88rem; font-weight:650;
    color:var(--text);
  }
  .pill .muted{ color:var(--muted); font-weight:600; }

  .section-title{
    margin-top: 10px;
    font-weight: 850;
    font-size: 1.1rem;
  }
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
# JSON -> Reviews DF (Axion) helpers (from aXreviewsConverter.py)
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

REVIEWS_EXTRA_COLS: List[Tuple[str, Any]] = [
    ("Key Review Sentiment_Reviews", lambda r: join_list(safe_get(r, ["customAttributes", "Key Review Sentiment_Reviews"]))),
    ("Key Review Sentiment Type_Reviews", lambda r: join_list(safe_get(r, ["customAttributes", "Key Review Sentiment Type_Reviews"]))),
    ("Dominant Customer Journey Step", lambda r: join_list(safe_get(r, ["customAttributes", "Dominant Customer Journey Step"]))),
    ("Trigger Point_Product", lambda r: join_list(safe_get(r, ["customAttributes", "Trigger Point_Product"]))),
    ("L2 Delighter Component", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Component"]))),
    ("L2 Delighter Condition", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Condition"]))),
    ("L2 Delighter Mode", lambda r: join_list(safe_get(r, ["customAttributes", "L2 Delighter Mode"]))),
    ("L3 Non Product Detractors", lambda r: join_list(safe_get(r, ["customAttributes", "L3 Non Product Detractors"]))),
    ("Product_Symptom Component", lambda r: join_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Component"]))),
    ("Product_Symptom Conditions", lambda r: join_list(safe_get(r, ["customAttributes", "taxonomies", "Product_Symptom Conditions"]))),
]


def build_reviews_df(records: List[Dict[str, Any]], include_extra: bool = True) -> pd.DataFrame:
    cols = REVIEWS_BASE_COLS + (REVIEWS_EXTRA_COLS if include_extra else [])
    rows = [{name: fn(r) for name, fn in cols} for r in records]
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
# Reviews DF -> Star Walk DF (from axionReviews.py)
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
    - Uses Axion export column names if present
    - Maps into default Star Walk scrubbed verbatims schema
    """
    src_df = reviews_df.reset_index(drop=True)
    out_cols = list(DEFAULT_STARWALK_COLUMNS)
    if include_extra_cols_after_symptom20:
        out_cols = insert_after_symptom20(out_cols, DEFAULT_EXTRA_AFTER_SYMPTOM20)

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
        **{c: c for c in DEFAULT_EXTRA_AFTER_SYMPTOM20 if c in src_df.columns},
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
    reviews_df = build_reviews_df(records, include_extra=True)

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

    # additional columns (dynamic)
    for col in additional_columns:
        k = f"f_{col}"
        if k in st.session_state:
            state["filters"][k] = st.session_state.get(k)

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
# Main UI
# ============================================================
st.title("Star Walk — Master Dashboard")
st.caption(f"Version: {APP_VERSION} • Default light mode • Faster filters • Saved Views")

# ----------------------------
# Input mode
# ----------------------------
st.markdown("### 📁 Data input")

mode = st.radio(
    "Choose input type",
    options=[
        "Star Walk scrubbed verbatims (Excel/CSV)",
        "Axion JSON export (auto-convert → dashboard)",
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
        json_file = st.file_uploader("Upload Axion JSON", type=["json"], key="upl_json")
    with right:
        pasted = st.text_area("…or paste JSON text", height=180, key="json_paste", placeholder="Paste JSON or JSON Lines here.")

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
        df_base, meta_info = _json_text_to_starwalk(raw_text, source_label, include_extra_cols=include_extra_cols)
        meta_info["convert_s"] = round(time.perf_counter() - t0, 3)

    # Conversion diagnostics
    with st.expander("✅ JSON conversion details", expanded=False):
        for w in meta_info.get("warnings", []):
            st.info(w)
        st.write("Records:", meta_info.get("records"))
        st.write("Truncation stats:", meta_info.get("stats"))
        st.caption("You can download the converted Star Walk file below if you want.")
        out_bytes = df_base.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Download converted Star Walk CSV", out_bytes, file_name=f"{Path(source_label).stem}_starwalk.csv", mime="text/csv")

# ----------------------------
# Dataset basics
# ----------------------------
assert df_base is not None
product_label = _infer_product_label(df_base, source_label)

# Reset heavyweight caches when a new dataset is loaded (prevents stale AI insights across uploads)
try:
    _dataset_sig = hashlib.sha1(
        (str(source_label) + "|" + str(df_base.shape) + "|" + "|".join([str(c) for c in df_base.columns.tolist()[:40]])).encode("utf-8")
    ).hexdigest()
except Exception:
    _dataset_sig = str(getattr(df_base, "shape", ""))

if st.session_state.get("_dataset_sig") != _dataset_sig:
    for _k in ["_ai_csat_cache", "_ai_enriched_texts", "_ai_last_search_results", "_vec_idx", "_local_text_idx"]:
        st.session_state.pop(_k, None)
    st.session_state["_dataset_sig"] = _dataset_sig
    # Also clear last error so users don't see stale failures
    st.session_state.pop("ai_last_error", None)

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
# Sidebar: Saved Views + Filters
# ============================================================

# ---- Load preset from URL param once (no UI; applies before widgets render) ----
qp = _get_query_params()
if (not st.session_state.get("_sv_loaded_once")) and qp.get("sv"):
    try:
        sv = qp.get("sv")
        if isinstance(sv, list):
            sv = sv[0] if sv else ""
        preset_obj = decode_preset_from_url_param(str(sv))
        # only apply if it looks like our schema
        if isinstance(preset_obj, dict) and preset_obj.get("schema_version") == PRESET_SCHEMA_VERSION:
            st.session_state["_sv_loaded_once"] = True
            st.session_state["_last_loaded_preset_name"] = preset_obj.get("name") or "Shared View"
            # We'll apply after we know additional columns
            st.session_state["_pending_preset_from_url"] = preset_obj
    except Exception:
        st.session_state["_sv_loaded_once"] = True
        st.sidebar.warning("Could not load Saved View from URL (invalid or truncated).")

# Determine columns for dynamic filters
core_cols = {"Country", "Source", "Model (SKU)", "Seeded", "New Review", "Star Rating", "Review Date", "Verbatim"}
symptom_cols = {f"Symptom {i}" for i in range(1, 21)}
additional_columns = [c for c in df_base.columns if c not in (core_cols | symptom_cols)]

# Apply pending URL preset now that we know columns
pending = st.session_state.pop("_pending_preset_from_url", None)
if pending:
    _apply_filter_state_to_session(pending, available_columns=list(df_base.columns), additional_columns=additional_columns)

# Ensure containers + defaults
st.session_state.setdefault("saved_views", {})  # name -> preset object
st.session_state.setdefault("live_filters", False)
st.session_state.setdefault("show_perf", False)

live_update = bool(st.session_state.get("live_filters", False))
show_perf = bool(st.session_state.get("show_perf", False))

# ----------------------------
# Sidebar filters (Apply mode)
# ----------------------------
st.sidebar.header("🔍 Filters")

# Create stable default values
tz_today = datetime.now(_NY_TZ).date() if _NY_TZ else datetime.today().date()
today = tz_today

# Ensure default keys
st.session_state.setdefault("tf", "All Time")
st.session_state.setdefault("sr", ["All"])
st.session_state.setdefault("kw", "")
st.session_state.setdefault("delight", ["All"])
st.session_state.setdefault("detract", ["All"])
st.session_state.setdefault("rpp", 10)
st.session_state.setdefault("tf_range", (today - timedelta(days=30), today))

# Utility to build options
def _col_options(df_in: pd.DataFrame, col: str) -> list:
    if col not in df_in.columns:
        return ["ALL"]
    s = df_in[col].astype("string").replace({"": pd.NA}).dropna()
    vals = sorted(pd.unique(s.to_numpy()).tolist())
    return ["ALL"] + [v for v in vals if str(v).strip() != ""]


# Precompute symptom columns lists from base dataset
detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
delighter_columns = [f"Symptom {i}" for i in range(11, 21)]
existing_detractor_columns = [c for c in detractor_columns if c in df_base.columns]
existing_delighter_columns = [c for c in delighter_columns if c in df_base.columns]
all_sym_cols_present = [c for c in [f"Symptom {i}" for i in range(1, 21)] if c in df_base.columns]

# Build symptom options once per dataset (store in session_state to avoid expensive DataFrame hashing)
_sym_key = (
    f"{source_label}|{len(df_base)}|" + hashlib.md5("|".join(df_base.columns).encode("utf-8")).hexdigest()
)
if st.session_state.get("_symptom_opts_key") != _sym_key:
    st.session_state["_symptom_opts_key"] = _sym_key
    st.session_state["_symptom_opts_det"] = collect_unique_symptoms(df_base, existing_detractor_columns)
    st.session_state["_symptom_opts_del"] = collect_unique_symptoms(df_base, existing_delighter_columns)

detractor_symptoms_all = st.session_state.get("_symptom_opts_det", []) or []
delighter_symptoms_all = st.session_state.get("_symptom_opts_del", []) or []

# Apply-form
def _render_filter_widgets():
    """Render sidebar filter widgets and sanitize any preset-loaded values."""
    def _as_list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, tuple):
            return list(v)
        return [v]

    def _ensure_multiselect(key: str, options: list, fallback: list):
        cur = _as_list(st.session_state.get(key, fallback))
        cur = [v for v in cur if v in options]
        if not cur:
            cur = list(fallback)
        st.session_state[key] = cur
        return cur

    # timeframe
    tf_opts = ["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"]
    if st.session_state.get("tf") not in tf_opts:
        st.session_state["tf"] = "All Time"

    with st.sidebar.expander("🗓️ Timeframe", expanded=False):
        st.selectbox("Select Timeframe", options=tf_opts, key="tf")
        if st.session_state["tf"] == "Custom Range":
            rng = st.session_state.get("tf_range", (today - timedelta(days=30), today))
            if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
                rng = (today - timedelta(days=30), today)
            st.session_state["tf_range"] = tuple(rng)
            st.date_input(
                "Date Range",
                value=st.session_state["tf_range"],
                min_value=date(2000, 1, 1),
                max_value=today,
                key="tf_range",
            )

    # star ratings
    with st.sidebar.expander("🌟 Star Rating", expanded=False):
        sr_opts = ["All", 1, 2, 3, 4, 5]
        _ensure_multiselect("sr", sr_opts, ["All"])
        st.multiselect("Select Star Ratings", options=sr_opts, key="sr")

    # standard filters
    with st.sidebar.expander("🌍 Standard Filters", expanded=False):
        for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
            opts = _col_options(df_base, col)
            _ensure_multiselect(f"f_{col}", opts, ["ALL"])
            st.multiselect(f"Select {col}", options=opts, key=f"f_{col}")

    # symptoms
    with st.sidebar.expander("🩺 Review Symptoms", expanded=False):
        del_opts = ["All"] + sorted(delighter_symptoms_all)
        det_opts = ["All"] + sorted(detractor_symptoms_all)
        _ensure_multiselect("delight", del_opts, ["All"])
        _ensure_multiselect("detract", det_opts, ["All"])

        st.multiselect("Select Delighter Symptoms", options=del_opts, key="delight")
        st.multiselect("Select Detractor Symptoms", options=det_opts, key="detract")

    with st.sidebar.expander("🔎 Keyword", expanded=False):
        st.text_input(
            "Keyword to search (in review text)",
            value=str(st.session_state.get("kw", "")),
            key="kw",
            help="Case-insensitive match in review text. Emails/phone numbers are masked in AI mode.",
        )

    with st.sidebar.expander("📋 Additional Filters", expanded=False):
        if additional_columns:
            for column in additional_columns:
                opts = _col_options(df_base, column)
                _ensure_multiselect(f"f_{column}", opts, ["ALL"])
                st.multiselect(f"Select {column}", options=opts, key=f"f_{column}")
        else:
            st.caption("No additional filters available.")

    with st.sidebar.expander("📄 Review List", expanded=False):
        rpp_opts = [10, 20, 50, 100]
        if int(st.session_state.get("rpp", 10)) not in rpp_opts:
            st.session_state["rpp"] = 10
        st.selectbox("Reviews per page", options=rpp_opts, key="rpp")


def _collect_current_filters_dict() -> dict:
    f = {}
    f["tf"] = st.session_state.get("tf", "All Time")
    f["tf_range"] = st.session_state.get("tf_range", None)
    f["sr"] = st.session_state.get("sr", ["All"])
    f["kw"] = st.session_state.get("kw", "")
    f["delight"] = st.session_state.get("delight", ["All"])
    f["detract"] = st.session_state.get("detract", ["All"])
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
        f[f"f_{col}"] = st.session_state.get(f"f_{col}", ["ALL"])
    for col in additional_columns:
        f[f"f_{col}"] = st.session_state.get(f"f_{col}", ["ALL"])
    return f


if not live_update:
    with st.sidebar.form("filters_form", clear_on_submit=False):
        _render_filter_widgets()
        apply_btn = st.form_submit_button("✅ Apply filters")
    # Initialize active filters once, or update on apply
    if ("_active_filters" not in st.session_state) or apply_btn:
        st.session_state["_active_filters"] = _collect_current_filters_dict()
        st.session_state["review_page"] = 0
        if apply_btn:
            st.rerun()
    active_filters = st.session_state.get("_active_filters", {})
else:
    _render_filter_widgets()
    active_filters = _collect_current_filters_dict()
    st.session_state["_active_filters"] = active_filters

# Clear filters button
if st.sidebar.button("🧹 Clear all filters", use_container_width=True):
    for k in [
        "tf",
        "tf_range",
        "sr",
        "kw",
        "delight",
        "detract",
        "rpp",
        "review_page",
        "_active_filters",
        "product_summary_text",
        "ask_q",
    ] + [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()

# ----------------------------
# Controls (under Clear button)
# ----------------------------
st.sidebar.header("🧰 Controls")

# ---- Saved Views UI ----
with st.sidebar.expander("💾 Saved Views / Presets", expanded=False):
    st.caption("Save your current *applied* filters, reload later, or share via URL.")

    if not live_update:
        st.info("Tip: Live-update is OFF, so Saved Views capture the last **applied** filters (click ✅ Apply filters first).")

    def _collect_applied_preset_state() -> dict:
        state = {
            "schema_version": PRESET_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "filters": {},
            "ui": {},
        }
        f = st.session_state.get("_active_filters") or _collect_current_filters_dict()
        if isinstance(f, dict):
            state["filters"] = dict(f)
        state["ui"]["rpp"] = st.session_state.get("rpp", 10)
        return state

    # Save current
    name = st.text_input("Preset name", value="", placeholder="e.g., US • Amazon • Last Month", key="sv_name")
    c1, c2 = st.columns([1, 1])
    if c1.button("Save current", use_container_width=True):
        if not name.strip():
            st.warning("Please enter a preset name.")
        else:
            preset = _collect_applied_preset_state()
            preset["name"] = name.strip()
            st.session_state["saved_views"][name.strip()] = preset
            st.success(f"Saved: {name.strip()}")

    # Load preset
    preset_names = sorted(st.session_state["saved_views"].keys())
    chosen = st.selectbox("Load preset", options=["(none)"] + preset_names, index=0, key="sv_load_select")
    if c2.button("Load selected", use_container_width=True):
        if chosen and chosen != "(none)":
            preset = st.session_state["saved_views"].get(chosen)
            if preset:
                _apply_filter_state_to_session(preset, available_columns=list(df_base.columns), additional_columns=additional_columns)
                st.rerun()

    # Export / Import
    if preset_names:
        export_choice = st.selectbox("Export preset", options=preset_names, key="sv_export_select")
        export_obj = st.session_state["saved_views"].get(export_choice)
        if export_obj:
            export_bytes = json.dumps(export_obj, ensure_ascii=False, indent=2).encode("utf-8")
            st.download_button(
                "Download preset JSON",
                data=export_bytes,
                file_name=f"saved_view_{_norm(export_choice) or 'preset'}.json",
                mime="application/json",
                use_container_width=True,
            )

            # Share link
            share_obj = dict(export_obj)
            share_obj["name"] = export_choice
            sv_param = encode_preset_to_url_param(share_obj)
            if st.button("Create shareable URL param", use_container_width=True):
                _set_query_params(sv=sv_param)
                st.success("URL param set. Copy the query string below.")
            st.code(f"?sv={sv_param}", language="text")
            st.caption("Append this to your app URL, or click 'Create shareable URL param' to set it in the browser.")

    import_file = st.file_uploader("Import preset JSON", type=["json"], key="sv_import_upl")
    if import_file is not None:
        try:
            obj = json.loads(import_file.getvalue().decode("utf-8", errors="replace"))
            if isinstance(obj, dict) and obj.get("schema_version") == PRESET_SCHEMA_VERSION:
                pname = obj.get("name") or f"Imported {len(st.session_state['saved_views'])+1}"
                st.session_state["saved_views"][pname] = obj
                st.success(f"Imported preset: {pname}")
            else:
                st.error("That JSON doesn't look like a Star Walk Saved View preset.")
        except Exception as e:
            st.error(f"Could not import preset: {e}")

    if st.button("Reset filters to default", type="secondary", use_container_width=True):
        # Clear known keys
        for k in ["tf", "tf_range", "sr", "kw", "delight", "detract", "review_page", "_active_filters"]:
            if k in st.session_state:
                del st.session_state[k]
        for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + additional_columns:
            k = f"f_{col}"
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

# ---- Filter UX mode ----
with st.sidebar.expander("⚡ Performance", expanded=False):
    st.toggle(
        "Live-update filters (slower on big files)",
        value=bool(st.session_state.get("live_filters", False)),
        key="live_filters",
        help="Off = change multiple filters, then click Apply once (recommended for speed).",
    )
    st.toggle("Show perf timings (debug)", value=bool(st.session_state.get("show_perf", False)), key="show_perf")

# ============================================================
# Apply filters to dataset
# ============================================================
t_filter0 = time.perf_counter()

filtered = df_base

# timeframe
start_date = end_date = None
tf = active_filters.get("tf", "All Time")
if tf == "Custom Range":
    rng = active_filters.get("tf_range")
    if isinstance(rng, tuple) and len(rng) == 2:
        start_date, end_date = rng
    else:
        start_date = end_date = rng
elif tf == "Last Week":
    start_date, end_date = today - timedelta(days=7), today
elif tf == "Last Month":
    start_date, end_date = today - timedelta(days=30), today
elif tf == "Last Year":
    start_date, end_date = today - timedelta(days=365), today

if start_date and end_date and "Review Date" in filtered.columns:
    dt = pd.to_datetime(filtered["Review Date"], errors="coerce")
    end_inclusive = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    filtered = filtered[(dt >= pd.Timestamp(start_date)) & (dt <= end_inclusive)]

# star rating
selected_ratings = active_filters.get("sr", ["All"])
if "All" not in selected_ratings and "Star Rating" in filtered.columns:
    filtered = filtered[filtered["Star Rating"].isin(selected_ratings)]

# standard column filters
for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
    sel = active_filters.get(f"f_{col}", ["ALL"])
    if col in filtered.columns and sel and "ALL" not in sel:
        filtered = filtered[filtered[col].astype("string").isin(sel)]

# additional filters
for col in additional_columns:
    sel = active_filters.get(f"f_{col}", ["ALL"])
    if col in filtered.columns and sel and "ALL" not in sel:
        filtered = filtered[filtered[col].astype("string").isin(sel)]

# symptom filters
selected_delighter = active_filters.get("delight", ["All"])
selected_detractor = active_filters.get("detract", ["All"])
if "All" not in selected_delighter and existing_delighter_columns:
    filtered = filtered[filtered[existing_delighter_columns].isin(selected_delighter).any(axis=1)]
if "All" not in selected_detractor and existing_detractor_columns:
    filtered = filtered[filtered[existing_detractor_columns].isin(selected_detractor).any(axis=1)]

# keyword
keyword = (active_filters.get("kw") or "").strip()
if keyword and "Verbatim" in filtered.columns:
    mask_kw = filtered["Verbatim"].astype("string").fillna("").str.contains(keyword, case=False, na=False)
    filtered = filtered[mask_kw]

filter_s = time.perf_counter() - t_filter0

# ============================================================
# Active filter summary panel (high-impact UI)
# ============================================================
def _summarize_active_filters(df_in: pd.DataFrame) -> list[tuple[str, str]]:
    items = []
    if tf != "All Time":
        if tf == "Custom Range" and start_date and end_date:
            items.append(("Timeframe", f"{start_date} → {end_date}"))
        else:
            items.append(("Timeframe", tf))
    if "All" not in selected_ratings:
        items.append(("Stars", ", ".join([str(x) for x in selected_ratings])))
    for col in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"] + additional_columns:
        sel = active_filters.get(f"f_{col}", ["ALL"])
        if sel and "ALL" not in sel:
            items.append((col, ", ".join([str(x) for x in sel[:4]]) + ("" if len(sel) <= 4 else f" +{len(sel)-4}")))
    if "All" not in selected_delighter:
        items.append(("Delighters", ", ".join([str(x) for x in selected_delighter[:3]]) + ("" if len(selected_delighter) <= 3 else f" +{len(selected_delighter)-3}")))
    if "All" not in selected_detractor:
        items.append(("Detractors", ", ".join([str(x) for x in selected_detractor[:3]]) + ("" if len(selected_detractor) <= 3 else f" +{len(selected_detractor)-3}")))
    if keyword:
        items.append(("Keyword", keyword))
    return items


active_items = _summarize_active_filters(filtered)
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
# Navigation / Views (sticky top bar, NO page refresh)
# ============================================================

# Marker lets us find-and-sticky this exact widget via a tiny DOM hook (no session reset).
st.markdown("<div id='view-nav-marker'></div>", unsafe_allow_html=True)

view = st.radio(
    "View",
    options=["📊 Dashboard", "📝 All Reviews", "🤖 AI"],
    horizontal=True,
    index=0,
    key="main_view",
    label_visibility="collapsed",
)

# Make ONLY the view switcher sticky + centered after the file loads
st_html(
    """
<script>
(function(){
  try {
    const doc = window.parent.document;
    const marker = doc.getElementById('view-nav-marker');
    if (!marker) return;
    // Streamlit wraps each element in an element-container; the widget is usually the next sibling
    const host = marker.closest('div.element-container') || marker.parentElement;
    if (!host) return;
    // Find the first stRadio after the marker
    let node = host.nextElementSibling;
    let radio = null;
    for (let i=0; i<6 && node; i++){
      if (node.querySelector && node.querySelector('[data-testid="stRadio"]')) { radio = node; break; }
      node = node.nextElementSibling;
    }
    if (!radio) return;
    radio.classList.add('sticky-topnav-host');
  } catch (e) {}
})();
</script>
""",
    height=0,
)

st.caption("Tip: **📝 All Reviews** contains the individual review cards (with green/red symptom tiles).")

# ============================================================
# Precompute analysis tables (fast) — only when needed
# ============================================================
need_agg = view.startswith("📊") or view.startswith("🤖")

if need_agg:
    t_agg0 = time.perf_counter()
    with st.spinner("Analyzing symptoms…"):
        detractors_results_full = analyze_symptoms_fast(filtered, existing_detractor_columns)
        delighters_results_full = analyze_symptoms_fast(filtered, existing_delighter_columns)
        trend_watchouts = _detect_trends(filtered, symptom_cols=all_sym_cols_present, min_mentions=3)
    agg_s = time.perf_counter() - t_agg0
else:
    detractors_results_full = pd.DataFrame(columns=["Item", "Mentions", "Avg Star", "% Total"])
    delighters_results_full = pd.DataFrame(columns=["Item", "Mentions", "Avg Star", "% Total"])
    trend_watchouts = []
    agg_s = 0.0

if show_perf:
    st.sidebar.caption(f"Filter time: {filter_s:.3f}s • Agg time: {agg_s:.3f}s")

# ============================================================
# Dashboard view
# ============================================================
if view.startswith("📊"):
    # ---------- Metrics ----------
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

    def card_html(title, count, avg, pct):
        return textwrap.dedent(f"""
        <div class="metric-card">
          <h4>{_html.escape(title)}</h4>
          <div class="metric-row">
            <div class="metric-box">
              <div class="metric-label">Count</div>
              <div class="metric-kpi">{count:,}</div>
            </div>
            <div class="metric-box">
              <div class="metric-label">Avg ★</div>
              <div class="metric-kpi">{avg:.1f}</div>
            </div>
            <div class="metric-box">
              <div class="metric-label">% 1–2★</div>
              <div class="metric-kpi">{pct:.1f}%</div>
            </div>
          </div>
        </div>
        """).strip()

    st.markdown(
        (
            '<div class="metrics-grid">'
            f'{card_html("All Reviews", all_cnt, all_avg, all_low)}'
            f'{card_html("Organic (non-Seeded)", org_cnt, org_avg, org_low)}'
            f'{card_html("Seeded", seed_cnt, seed_avg, seed_low)}'
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    # ---------- Symptom Tables ----------
    st.markdown("## 🩺 Symptom Tables")
    # Default display limit; show a "View full" affordance if truncated.
    table_limit = int(st.session_state.get("symptom_table_limit", 50))
    table_limit = st.selectbox("Rows to preview", options=[25, 50, 100], index=[25, 50, 100].index(50), key="symptom_table_limit")

    detractors_results = detractors_results_full.head(table_limit)
    delighters_results = delighters_results_full.head(table_limit)

    view_mode = st.radio("View mode", ["Split", "Tabs"], horizontal=True, index=0, key="symptom_table_view_mode")

    def _styled_table(df_in: pd.DataFrame):
        if df_in.empty:
            return df_in
        def colstyle(v):
            if pd.isna(v):
                return ""
            try:
                vv = float(v)
                if vv >= 4.5:
                    return "color:#065F46;font-weight:600;"
                if vv < 4.5:
                    return "color:#7F1D1D;font-weight:600;"
            except Exception:
                pass
            return ""
        return df_in.style.applymap(colstyle, subset=["Avg Star"]).format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"})

    def _table_downloads(df_full: pd.DataFrame, label: str, key_prefix: str):
        if df_full is None or df_full.empty:
            return
        csv = df_full.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            f"⬇️ Download full {label} (CSV)",
            data=csv,
            file_name=f"{key_prefix}_full.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"dl_{key_prefix}",
        )

    def _render_symptom_table(df_preview: pd.DataFrame, df_full: pd.DataFrame, title: str, key_prefix: str):
        left, right = st.columns([1.25, 0.9])
        with left:
            st.subheader(title)
        with right:
            _table_downloads(df_full, title, key_prefix)

        # Main preview
        st.dataframe(
            _styled_table(df_preview) if not df_preview.empty else df_preview,
            use_container_width=True,
            hide_index=True,
        )

        # If truncated, show "View full" affordance
        if len(df_full) > len(df_preview) and len(df_preview) > 0:
            if st.button(f"View full table ({len(df_full):,} rows)", key=f"view_full_{key_prefix}"):
                st.session_state[f"show_full_{key_prefix}"] = True
            if st.session_state.get(f"show_full_{key_prefix}"):
                with st.expander(f"Full {title} table", expanded=True):
                    st.dataframe(
                        _styled_table(df_full) if not df_full.empty else df_full,
                        use_container_width=True,
                        hide_index=True,
                        height=680,
                    )

    if view_mode == "Split":
        c1, c2 = st.columns([1, 1])
        with c1:
            _render_symptom_table(detractors_results, detractors_results_full, "All Detractors", "detractors")
        with c2:
            _render_symptom_table(delighters_results, delighters_results_full, "All Delighters", "delighters")
    else:
        tab1, tab2 = st.tabs(["All Detractors", "All Delighters"])
        with tab1:
            _render_symptom_table(detractors_results, detractors_results_full, "All Detractors", "detractors")
        with tab2:
            _render_symptom_table(delighters_results, delighters_results_full, "All Delighters", "delighters")




    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    if trend_watchouts:
        with st.expander("⚠️ Watchouts & Recent Movement (local)", expanded=False):
            st.markdown("\n".join([f"- {t}" for t in trend_watchouts]))

    # ---------- Top symptoms bars ----------
    st.markdown("## 🧩 Top Delighters & Detractors")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Top Detractors (by mentions)**")
        det = detractors_results_full.head(12)
        if det.empty:
            st.info("No detractor symptoms available.")
        else:
            fig_det = go.Figure(
                go.Bar(
                    x=det["Mentions"][::-1],
                    y=det["Item"][::-1],
                    orientation="h",
                    opacity=0.85,
                    hovertemplate="%{y}<br>Mentions: %{x}<extra></extra>",
                )
            )
            fig_det.update_layout(template="plotly_white", margin=dict(l=160, r=20, t=20, b=20), height=460)
            st.plotly_chart(fig_det, use_container_width=True)
    with c2:
        st.markdown("**Top Delighters (by mentions)**")
        deli = delighters_results_full.head(12)
        if deli.empty:
            st.info("No delighter symptoms available.")
        else:
            fig_del = go.Figure(
                go.Bar(
                    x=deli["Mentions"][::-1],
                    y=deli["Item"][::-1],
                    orientation="h",
                    opacity=0.85,
                    hovertemplate="%{y}<br>Mentions: %{x}<extra></extra>",
                )
            )
            fig_del.update_layout(template="plotly_white", margin=dict(l=160, r=20, t=20, b=20), height=460)
            st.plotly_chart(fig_del, use_container_width=True)


    # ---------- Opportunity Matrix (high impact) ----------
    st.markdown("## 🎯 Opportunity Matrix")
    st.caption("Mentions vs Avg ★. Fix high-mention low-star detractors first; amplify high-mention high-star delighters.")

    # Baseline = overall average star rating (CURRENT filtered dataset)
    try:
        baseline_overall = float(all_avg)
    except Exception:
        baseline_overall = float(pd.to_numeric(filtered.get("Star Rating"), errors="coerce").mean())
    if not np.isfinite(baseline_overall):
        baseline_overall = 0.0

    tab_det, tab_del = st.tabs(["Detractors", "Delighters"])

    def _opportunity_scatter(tbl: pd.DataFrame, title: str, kind: str, baseline: float):
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

        # Priority score (used for labels + table)
        if kind == "detractors":
            score = x * np.clip(baseline - y, 0, None)
            table_label = "Fix first (high mentions × below-baseline ★)"
        else:
            score = x * np.clip(y - baseline, 0, None)
            table_label = "Amplify (high mentions × above-baseline ★)"

        # Controls (labels are OFF by default to prevent overlap)
        c1, c2, c3 = st.columns([1.1, 1.5, 1.6])
        show_labels = c1.toggle("Show labels", value=False, key=f"opp_show_labels_{kind}")
        max_labels = int(min(25, len(d)))
        label_default = int(min(10, max_labels))
        label_n = c2.slider("Label top N", 0, max_labels, value=label_default, key=f"opp_label_n_{kind}", disabled=not show_labels)
        size_by_mentions = c3.toggle("Bubble size = Mentions", value=True, key=f"opp_size_mentions_{kind}")

        labels = np.array([""] * len(d), dtype=object)
        if show_labels and label_n > 0:
            top_idx = np.argsort(-score)[:label_n]
            labels[top_idx] = d["Item"].astype(str).to_numpy()[top_idx]

        # Marker sizing (sqrt scale for stability)
        if size_by_mentions and np.nanmax(x) > 0:
            size = (np.sqrt(x) / (np.sqrt(np.nanmax(x)) + 1e-9)) * 28 + 10
        else:
            size = np.full_like(x, 14.0, dtype=float)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode=("markers+text" if show_labels else "markers"),
                text=(labels if show_labels else None),
                textposition="top center",
                textfont=dict(size=10),
                customdata=np.stack([d["Item"].astype(str).to_numpy(), score], axis=-1),
                marker=dict(
                    size=size,
                    color=y,
                    colorscale="RdYlGn",
                    cmin=1.0,
                    cmax=5.0,
                    showscale=True,
                    colorbar=dict(title="Avg ★"),
                    opacity=0.86,
                    line=dict(width=1, color="rgba(0,0,0,0.25)"),
                ),
                hovertemplate="%{customdata[0]}<br>Mentions=%{x}<br>Avg ★=%{y:.2f}<br>Priority=%{customdata[1]:.2f}<extra></extra>",
            )
        )

        # Reference lines (median mentions + overall avg ★)
        try:
            fig.add_vline(x=float(np.nanmedian(x)), line_dash="dot", opacity=0.35)
        except Exception:
            pass
        try:
            fig.add_hline(y=float(baseline), line_dash="dot", opacity=0.35)
        except Exception:
            pass

        fig.update_layout(
            template="plotly_white",
            xaxis_title="Mentions",
            yaxis_title="Avg ★",
            title=title,
            margin=dict(l=55, r=20, t=65, b=55),
            height=560,
        )
        fig.update_yaxes(range=[1.0, 5.2])
        st.plotly_chart(fig, use_container_width=True)

        # Best-in-class usability: show a ranked table (click-to-scan)
        d_tbl = d.copy()
        d_tbl["Priority"] = score
        d_tbl = d_tbl.sort_values("Priority", ascending=False).head(15)

        cols = ["Item", "Mentions", "Avg Star"]
        if "% Total" in d_tbl.columns:
            cols.append("% Total")
        cols.append("Priority")

        with st.expander(f"📋 {table_label} — top 15", expanded=False):
            out = d_tbl[cols].copy()
            for c in ["Mentions", "Avg Star", "% Total", "Priority"]:
                if c in out.columns:
                    out[c] = pd.to_numeric(out[c], errors="coerce")
            if "% Total" in out.columns:
                out["% Total"] = out["% Total"].round(2)
            out["Avg Star"] = out["Avg Star"].round(2)
            out["Priority"] = out["Priority"].round(2)
            st.dataframe(out, use_container_width=True, hide_index=True)

    with tab_det:
        st.caption("Hover points for symptom names. Turn on labels only if needed (top N only).")
        _opportunity_scatter(
            detractors_results_full.head(60),
            "Detractors — prioritize high mentions + low Avg ★",
            kind="detractors",
            baseline=float(baseline_overall),
        )

    with tab_del:
        st.caption("Hover points for symptom names. Turn on labels only if needed (top N only).")
        _opportunity_scatter(
            delighters_results_full.head(60),
            "Delighters — amplify high mentions + high Avg ★",
            kind="delighters",
            baseline=float(baseline_overall),
        )

    # ---------- Cumulative Avg ★ Over Time by Region (Weighted) ----------
    st.markdown("## 📈 Cumulative Avg ★ Over Time by Region (Weighted)")

    if "Review Date" not in filtered.columns or "Star Rating" not in filtered.columns:
        st.info("Need 'Review Date' and 'Star Rating' columns to compute this chart.")
    else:
        c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 1.1, 0.9, 0.9])
        with c1:
            bucket_label = st.selectbox("Bucket size", ["Day", "Week", "Month"], index=2, key="region_bucket")
            _freq_map = {"Day": "D", "Week": "W", "Month": "M"}
            freq = _freq_map[bucket_label]
        with c2:
            _candidates = [c for c in ["Country", "Source", "Model (SKU)"] if c in filtered.columns]
            region_col = st.selectbox("Region field", options=_candidates or ["(none)"], key="region_col")
        with c3:
            top_n = st.number_input("Top regions by volume", 1, 15, value=5, step=1, key="region_topn")
        with c4:
            organic_only = st.toggle("Organic Only", value=False, help="Exclude reviews where Seeded == YES")
        with c5:
            show_volume = st.toggle("Show Volume", value=True, help="Adds subtle bars + right axis showing review count per bucket.")

        if region_col not in filtered.columns or region_col == "(none)":
            st.info("No region column found for this chart.")
        else:
            d = filtered
            if organic_only and "Seeded" in d.columns:
                d = d[d["Seeded"].astype("string").str.upper().ne("YES")]

            d = d.copy()
            d["Star Rating"] = pd.to_numeric(d["Star Rating"], errors="coerce")
            d["Review Date"] = pd.to_datetime(d["Review Date"], errors="coerce")
            d = d.dropna(subset=["Review Date", "Star Rating"])
            if d.empty:
                st.warning("No data available for the current selections.")
            else:
                freq_eff = "W-MON" if freq == "W" else freq
                counts = d[region_col].astype("string").str.strip().replace({"": pd.NA}).dropna()
                top_regions = counts.value_counts().head(int(top_n)).index.tolist()
                regions_available = sorted(r for r in d[region_col].astype("string").dropna().unique().tolist() if str(r).strip() != "")
                chosen_regions = st.multiselect(
                    "Regions to plot",
                    options=regions_available,
                    default=[r for r in top_regions if r in regions_available],
                    key="region_pick",
                )
                if chosen_regions:
                    d = d[d[region_col].astype("string").isin(chosen_regions)]
                if d.empty:
                    st.warning("No data after region selection.")
                else:
                    tmp = (
                        d.assign(_region=d[region_col].astype("string"))
                        .groupby([pd.Grouper(key="Review Date", freq=freq_eff), "_region"])["Star Rating"]
                        .agg(bucket_sum="sum", bucket_count="count")
                        .reset_index()
                        .sort_values(["_region", "Review Date"])
                    )
                    tmp["cum_sum"] = tmp.groupby("_region")["bucket_sum"].cumsum()
                    tmp["cum_cnt"] = tmp.groupby("_region")["bucket_count"].cumsum()
                    tmp["Cumulative Avg ★"] = tmp["cum_sum"] / tmp["cum_cnt"]

                    overall = (
                        d.groupby(pd.Grouper(key="Review Date", freq=freq_eff))["Star Rating"]
                        .agg(bucket_sum="sum", bucket_count="count")
                        .reset_index()
                        .sort_values("Review Date")
                    )
                    overall["cum_sum"] = overall["bucket_sum"].cumsum()
                    overall["cum_cnt"] = overall["bucket_count"].cumsum()
                    overall["Cumulative Avg ★"] = overall["cum_sum"] / overall["cum_cnt"]

                    fig = go.Figure()
                    if show_volume and not overall.empty:
                        fig.add_trace(
                            go.Bar(
                                x=overall["Review Date"],
                                y=overall["bucket_count"],
                                name="Review volume",
                                yaxis="y2",
                                opacity=0.30,
                                marker=dict(color="rgba(15, 23, 42, 0.35)", line=dict(width=0)),
                                hovertemplate="Review volume<br>Bucket end: %{x|%Y-%m-%d}<br>Reviews: %{y}<extra></extra>",
                                showlegend=False,
                            )
                        )

                    plot_regions = chosen_regions or tmp["_region"].unique().tolist()
                    for reg in plot_regions:
                        sub = tmp[tmp["_region"] == reg]
                        if sub.empty:
                            continue
                        fig.add_trace(
                            go.Scatter(
                                x=sub["Review Date"],
                                y=sub["Cumulative Avg ★"],
                                mode="lines+markers",
                                name=str(reg),
                                line=dict(width=2),
                                marker=dict(size=5),
                                hovertemplate=(
                                    f"{region_col}: {reg}<br>"
                                    "Bucket end: %{x|%Y-%m-%d}<br>"
                                    "Cumulative Avg ★: %{y:.3f}<br>"
                                    "Cum. Reviews: %{customdata}<extra></extra>"
                                ),
                                customdata=sub["cum_cnt"],
                            )
                        )

                    if not overall.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=overall["Review Date"],
                                y=overall["Cumulative Avg ★"],
                                mode="lines",
                                name="Overall",
                                line=dict(width=3, dash="dash"),
                                hovertemplate=(
                                    "Overall<br>"
                                    "Bucket end: %{x|%Y-%m-%d}<br>"
                                    "Cumulative Avg ★: %{y:.3f}<br>"
                                    "Cum. Reviews: %{customdata}<extra></extra>"
                                ),
                                customdata=overall["cum_cnt"],
                            )
                        )

                    _tickformat = {"D": "%b %d, %Y", "W": "%b %d, %Y", "M": "%b %Y"}[freq]
                    fig.update_xaxes(tickformat=_tickformat, automargin=True)
                    fig.update_layout(
                        template="plotly_white",
                        hovermode="x unified",
                        barmode="overlay",
                        margin=dict(l=60, r=(60 if show_volume else 40), t=50, b=100),
                        legend=dict(orientation="h", yanchor="top", y=-0.28, xanchor="center", x=0.5),
                        yaxis=dict(title="Cumulative Avg ★", range=[1.0, 5.2], showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
                        yaxis2=dict(
                            title="Review volume",
                            overlaying="y",
                            side="right",
                            showgrid=False,
                            rangemode="tozero",
                            autorange=True,
                            visible=bool(show_volume),
                        ),
                    )
                    st.plotly_chart(fig, use_container_width=True)

    # ---------- Country × Source breakdown (connected Avg + Count) ----------
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

            # Apply min count threshold: hide avg if low N
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

            # overlay counts as text (contrast-aware)
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
                        xs_dark.append(source)
                        ys_dark.append(country)
                        texts_dark.append(str(c))
                    else:
                        xs_light.append(source)
                        ys_light.append(country)
                        texts_light.append(str(c))

            if xs_light:
                fig.add_trace(
                    go.Scatter(
                        x=xs_light,
                        y=ys_light,
                        mode="text",
                        text=texts_light,
                        hoverinfo="skip",
                        showlegend=False,
                        textfont=dict(size=12, color="black"),
                    )
                )

            if xs_dark:
                fig.add_trace(
                    go.Scatter(
                        x=xs_dark,
                        y=ys_dark,
                        mode="text",
                        text=texts_dark,
                        hoverinfo="skip",
                        showlegend=False,
                        textfont=dict(size=12, color="white"),
                    )
                )

            fig.update_layout(
                template="plotly_white",
                margin=dict(l=90, r=20, t=40, b=60),
                height=560,
            )

            st.plotly_chart(fig, use_container_width=True)

            if show_table:
                with st.expander("📋 Table view (Count + Avg ★)", expanded=True):
                    st.markdown("**Counts**")
                    st.dataframe(count_m, use_container_width=True)
                    st.markdown("**Avg ★**")
                    st.dataframe(mean_m.round(2), use_container_width=True)
    else:
        st.info("Need Country, Source, and Star Rating columns to compute this breakdown.")


# ============================================================
# Reviews view
# ============================================================
if view.startswith("📝"):
    st.markdown("## 📝 All Reviews")

    if not filtered.empty:
        csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes, file_name="filtered_reviews.csv", mime="text/csv")

    if "review_page" not in st.session_state:
        st.session_state["review_page"] = 0
    reviews_per_page = int(st.session_state.get("rpp", 10))
    total_reviews_count = len(filtered)
    total_pages = max((total_reviews_count + reviews_per_page - 1) // reviews_per_page, 1)

    st.session_state["review_page"] = min(st.session_state.get("review_page", 0), max(total_pages - 1, 0))
    current_page = st.session_state["review_page"]
    start_index = current_page * reviews_per_page
    end_index = start_index + reviews_per_page
    paginated = filtered.iloc[start_index:end_index]

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

    # Pager
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
# AI view (CSAT Copilot)
# ============================================================
if view.startswith("🤖"):
    st.markdown("## 🤖 CSAT Copilot")
    st.caption("Built for **Quality Engineering + CSAT**. No remote calls happen until you press **Send**.")

    # ----------------------------
    # Compute baseline CSAT metrics (local)
    # ----------------------------
    stars_all = pd.to_numeric(filtered.get("Star Rating"), errors="coerce") if "Star Rating" in filtered.columns else pd.Series(dtype=float)
    stars_all = stars_all.dropna()
    total_reviews = int(len(filtered))
    baseline_avg = float(stars_all.mean()) if not stars_all.empty else 0.0
    baseline_median = float(stars_all.median()) if not stars_all.empty else 0.0
    baseline_pct_low = float((stars_all <= 2).mean() * 100) if not stars_all.empty else 0.0
    baseline_pct_high = float((stars_all >= 4).mean() * 100) if not stars_all.empty else 0.0
    star_counts = (
        stars_all.value_counts().reindex([1, 2, 3, 4, 5]).fillna(0).astype(int).to_dict()
        if not stars_all.empty
        else {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    )

    # Cache heavyweight CSAT/driver tables per applied filter state
    try:
        _filters_hash = hashlib.sha1(json.dumps(active_filters, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    except Exception:
        _filters_hash = str(len(filtered))

    _ai_cache = st.session_state.setdefault("_ai_csat_cache", {})
    _REQUIRED_CSAT_KEYS = {"driver_det", "driver_del", "hotspots", "product_profile", "text_themes"}
    _cached = _ai_cache.get(_filters_hash)
    _needs_build = (not isinstance(_cached, dict)) or (not _REQUIRED_CSAT_KEYS.issubset(set(_cached.keys())))
    if _needs_build:
        def _symptom_driver_stats(df_in: pd.DataFrame, symptom_cols: list[str]) -> pd.DataFrame:
            cols = [c for c in symptom_cols if c in df_in.columns]
            if not cols or df_in.empty or "Star Rating" not in df_in.columns:
                return pd.DataFrame(columns=["Symptom", "Reviews", "Mention %", "Avg ★", "% 1–2★"])

            stars = pd.to_numeric(df_in["Star Rating"], errors="coerce")
            # long stack
            long = df_in[cols].stack(dropna=False).reset_index()
            long.columns = ["__idx", "__col", "symptom"]
            s = long["symptom"].astype("string").str.strip()
            long = long.loc[s.map(is_valid_symptom_value), ["__idx", "symptom"]]
            if long.empty:
                return pd.DataFrame(columns=["Symptom", "Reviews", "Mention %", "Avg ★", "% 1–2★"])

            tmp = long.drop_duplicates(subset=["__idx", "symptom"]).copy()
            star_map = stars.to_dict()
            tmp["star"] = tmp["__idx"].map(star_map)
            tmp = tmp.dropna(subset=["star"])
            tmp["low"] = tmp["star"] <= 2

            g = tmp.groupby("symptom").agg(Reviews=("__idx", "nunique"), AvgStar=("star", "mean"), PctLow=("low", "mean"))
            g = g.sort_values("Reviews", ascending=False)
            g.reset_index(inplace=True)
            g.rename(columns={"symptom": "Symptom"}, inplace=True)

            total = max(1, len(df_in))
            g["Mention %"] = (g["Reviews"] / total * 100).round(1)
            g["Avg ★"] = g["AvgStar"].astype(float).round(2)
            g["% 1–2★"] = (g["PctLow"].astype(float) * 100).round(1)
            out = g[["Symptom", "Reviews", "Mention %", "Avg ★", "% 1–2★"]].copy()
            out["Symptom"] = out["Symptom"].astype(str).str.title()
            return out

        def _driver_table(stats_df: pd.DataFrame, kind: str) -> pd.DataFrame:
            if stats_df.empty:
                return stats_df
            df = stats_df.copy()
            # Deltas vs baseline
            df["Δ Avg ★"] = (df["Avg ★"] - baseline_avg).round(2)
            df["Δ % 1–2★"] = (df["% 1–2★"] - baseline_pct_low).round(1)

            # Impact score: "rating points recovered" if symptom removed (heuristic)
            if kind == "detractors":
                df["Impact"] = (df["Reviews"] * (baseline_avg - df["Avg ★"])).round(2)
                df = df.sort_values(["Impact", "Reviews"], ascending=[False, False])
            else:
                df["Impact"] = (df["Reviews"] * (df["Avg ★"] - baseline_avg)).round(2)
                df = df.sort_values(["Impact", "Reviews"], ascending=[False, False])

            # Keep readable columns
            return df[["Symptom", "Reviews", "Mention %", "Avg ★", "% 1–2★", "Δ Avg ★", "Δ % 1–2★", "Impact"]].reset_index(drop=True)

        # Hotspot tables (Country/Source/SKU) for DSAT (%1–2★)
        def _segment_hotspots(df_in: pd.DataFrame, field: str, k: int = 10, min_n: int = 15) -> pd.DataFrame:
            if field not in df_in.columns or df_in.empty or "Star Rating" not in df_in.columns:
                return pd.DataFrame(columns=[field, "Reviews", "Avg ★", "% 1–2★", "% 4–5★"])
            d = df_in[[field, "Star Rating"]].copy()
            d[field] = d[field].astype("string").fillna("(blank)").str.strip().replace("", "(blank)")
            d["star"] = pd.to_numeric(d["Star Rating"], errors="coerce")
            d = d.dropna(subset=["star"])
            if d.empty:
                return pd.DataFrame(columns=[field, "Reviews", "Avg ★", "% 1–2★", "% 4–5★"])
            d["low"] = d["star"] <= 2
            d["high"] = d["star"] >= 4
            g = d.groupby(field).agg(Reviews=("star", "size"), Avg=("star", "mean"), PctLow=("low", "mean"), PctHigh=("high", "mean")).reset_index()
            g["Avg ★"] = g["Avg"].round(2)
            g["% 1–2★"] = (g["PctLow"] * 100).round(1)
            g["% 4–5★"] = (g["PctHigh"] * 100).round(1)
            g = g[g["Reviews"] >= int(min_n)]
            g = g.sort_values(["% 1–2★", "Reviews"], ascending=[False, False]).head(int(k))
            return g[[field, "Reviews", "Avg ★", "% 1–2★", "% 4–5★"]]

        with st.spinner("Building CSAT driver tables…"):
            det_stats = _symptom_driver_stats(filtered, existing_detractor_columns)
            del_stats = _symptom_driver_stats(filtered, existing_delighter_columns)
            driver_det = _driver_table(det_stats, "detractors")
            driver_del = _driver_table(del_stats, "delighters")

            hotspots = {}
            for f in ["Country", "Source", "Model (SKU)", "Seeded"]:
                hotspots[f] = _segment_hotspots(filtered, f, k=10, min_n=15)

            product_profile = infer_product_profile(filtered, source_label)
            text_themes = compute_text_theme_diffs(filtered, max_reviews=5000, top_n=18)

            _ai_cache[_filters_hash] = {
                "driver_det": driver_det,
                "driver_del": driver_del,
                "hotspots": hotspots,
                "product_profile": product_profile,
                "text_themes": text_themes,
            }

    _cached = _ai_cache.get(_filters_hash, {}) if isinstance(_ai_cache, dict) else {}
    driver_det = _cached.get("driver_det", pd.DataFrame())
    driver_del = _cached.get("driver_del", pd.DataFrame())
    hotspots = _cached.get("hotspots", {})
    product_profile = _cached.get("product_profile") or infer_product_profile(filtered, source_label)
    text_themes = _cached.get("text_themes") or compute_text_theme_diffs(filtered, max_reviews=5000, top_n=18)
    # Backfill cache for backward compatibility (prevents KeyError after upgrades)
    _cached["product_profile"] = product_profile
    _cached["text_themes"] = text_themes
    _ai_cache[_filters_hash] = _cached

    # ----------------------------
    # CSAT snapshot panel
    # ----------------------------
    cA, cB, cC, cD = st.columns([1, 1, 1, 1])
    cA.metric("Reviews (filtered)", f"{total_reviews:,}")
    cB.metric("Avg ★", f"{baseline_avg:.2f}")
    cC.metric("% 1–2★ (DSAT proxy)", f"{baseline_pct_low:.1f}%")
    cD.metric("% 4–5★ (CSAT proxy)", f"{baseline_pct_high:.1f}%")

    with st.expander("📌 Quick CSAT context (drivers + hotspots)", expanded=True):
        left, right = st.columns([1, 1])
        with left:
            st.markdown("### 🚨 Top CSAT risks (Detractors)")
            st.caption("Sorted by **Impact** (heuristic: reviews × rating deficit vs baseline).")
            if driver_det.empty:
                st.info("No detractor symptoms found in this filtered dataset.")
            else:
                st.dataframe(driver_det.head(12), use_container_width=True, hide_index=True)

        with right:
            st.markdown("### ✅ Top CSAT strengths (Delighters)")
            st.caption("Protect these. Also useful for 'what to highlight in marketing / onboarding'.")
            if driver_del.empty:
                st.info("No delighter symptoms found in this filtered dataset.")
            else:
                st.dataframe(driver_del.head(12), use_container_width=True, hide_index=True)

        st.markdown("### 🗺️ DSAT hotspots (where the pain concentrates)")
        h1, h2 = st.columns([1, 1])
        with h1:
            st.markdown("**By Country**")
            st.dataframe(hotspots.get("Country", pd.DataFrame()).head(10), use_container_width=True, hide_index=True)
        with h2:
            st.markdown("**By Source**")
            st.dataframe(hotspots.get("Source", pd.DataFrame()).head(10), use_container_width=True, hide_index=True)

    st.divider()

    # ----------------------------
    # Sidebar: AI settings (kept local until Send)
    # ----------------------------
    with st.sidebar.expander("🤖 AI Assistant", expanded=False):
        _model_choices = [
            ("Fast & economical – 4o-mini", "gpt-4o-mini"),
            ("Balanced – 4o", "gpt-4o"),
            ("Advanced – 4.1", "gpt-4.1"),
            ("Most advanced – GPT-5", "gpt-5"),
            ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
        ]
        _default_model = st.session_state.get("llm_model", "gpt-4o-mini")
        _default_idx = next((i for i, (_, mid) in enumerate(_model_choices) if mid == _default_model), 0)
        _label = st.selectbox("Model", options=[l for (l, _) in _model_choices], index=_default_idx, key="llm_model_label")
        st.session_state["llm_model"] = dict(_model_choices)[_label]

        temp_supported = model_supports_temperature(st.session_state["llm_model"])
        st.session_state["llm_temp"] = st.slider(
            "Creativity (temperature)",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.get("llm_temp", 0.2)),
            step=0.1,
            disabled=not temp_supported,
        )
        if not temp_supported:
            st.caption("ℹ️ This model uses a fixed temperature; the slider is disabled.")

        st.session_state["ai_style"] = st.radio(
            "Answer style",
            options=["Engineering deep dive", "Executive brief"],
            index=0 if st.session_state.get("ai_style", "Engineering deep dive") == "Engineering deep dive" else 1,
            help="Engineering = root cause hypotheses + next tests. Executive = crisp summary + priorities.",
        )

    with st.sidebar.expander("🔒 Privacy & Retrieval", expanded=True):
        ai_enabled = st.toggle(
            "Enable remote AI (send masked snippets to OpenAI)",
            value=bool(st.session_state.get("ai_enabled", True)),
            key="ai_enabled",
        )
        send_quotes = st.toggle(
            "Include evidence quotes in AI context",
            value=bool(st.session_state.get("ai_send_quotes", True)),
            key="ai_send_quotes",
            help="Turn off if you want *only* aggregated stats sent to the model.",
        )
        ai_cap = st.number_input(
            "Max reviews to embed per Q (semantic search)",
            min_value=200,
            max_value=5000,
            value=int(st.session_state.get("ai_cap", 1500)),
            step=100,
            key="ai_cap",
        )
        st.caption("Emails/phone numbers are masked before embedding.")

        st.toggle("Use BM25 blend (local, fast)", value=st.session_state.get("use_bm25", False), key="use_bm25")
        st.toggle("Use cross-encoder reranker (local, slower)", value=st.session_state.get("use_reranker", False), key="use_reranker")

    with st.sidebar.expander("🔑 OpenAI Key (optional override)", expanded=False):
        st.text_input("OPENAI_API_KEY override", value="", type="password", key="api_key_override")
        if _HAS_OPENAI:
            if st.button("🔌 Test API key", use_container_width=True, key="test_openai_key"):
                k = (st.session_state.get("api_key_override") or "").strip() or st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
                if not k:
                    st.warning("No API key found (override, secrets, or env).")
                else:
                    try:
                        _client = OpenAI(api_key=k, timeout=20, max_retries=0)
                        # Lightweight call
                        models = _client.models.list()
                        mnames = []
                        try:
                            mnames = [getattr(m, "id", "") for m in getattr(models, "data", [])][:6]
                        except Exception:
                            mnames = []
                        st.success("API key looks valid. ✅")
                        if mnames:
                            st.caption("Sample models: " + ", ".join([x for x in mnames if x]))
                    except Exception as e:
                        st.session_state["ai_last_error"] = repr(e)
                        st.error(f"API key test failed: {e}")

    api_key_override = (st.session_state.get("api_key_override") or "").strip()
    api_key = api_key_override or st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

    if not _HAS_OPENAI:
        st.info("To enable remote LLM, add `openai` to requirements and set `OPENAI_API_KEY`. Local CSAT Copilot still works.")
    elif not api_key:
        st.info("Set `OPENAI_API_KEY` (env or .streamlit/secrets.toml) for remote LLM. Local CSAT Copilot still works.")

    # Main-page AI status (so model selection + connectivity is obvious)
    sel_model = st.session_state.get("llm_model", "gpt-4o-mini")
    remote_ready = bool(ai_enabled and _HAS_OPENAI and api_key)
    status_txt = "🟢 Remote AI ready" if remote_ready else "🟡 Local-only mode"
    st.markdown(
        f"""
<div class="soft-panel" style="margin-top: 8px;">
  <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;">
    <div style="font-weight:850;">{_html.escape(status_txt)}</div>
    <div class="small-muted">Model: <b>{_html.escape(str(sel_model))}</b></div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    if st.session_state.get("ai_last_error"):
        with st.expander("⚠️ Last AI error (debug)", expanded=False):
            st.code(st.session_state.get("ai_last_error"))

    # ----------------------------
    # Preset questions tuned for CSAT / quality engineering
    # ----------------------------
    if "ask_q" not in st.session_state:
        st.session_state["ask_q"] = ""

    st.markdown(
        """
<div class="soft-panel">
  <div style="font-weight:850; font-size:1.05rem;">🪄 CSAT Playbooks</div>
  <div class="small-muted" style="margin-top:4px;">
    Click to prefill the question box, then press <b>Send</b>.
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    b1, b2, b3, b4, b5, b6 = st.columns([1, 1, 1, 1, 1, 1])
    if b1.button("CSAT Brief", use_container_width=True):
        st.session_state["ask_q"] = (
            "Write a CSAT brief for a quality engineer using ONLY the current filtered reviews: "
            "1) CSAT headline, 2) top 5 DSAT drivers with metrics + likely root causes, "
            "3) hotspots (Country/Source/SKU), 4) next 7-day actions + validation plan, "
            "5) top 3 delighters to protect. Cite evidence quotes by ID."
        )
    if b2.button("Top DSAT Drivers", use_container_width=True):
        st.session_state["ask_q"] = "What are the top drivers of 1–2★ reviews? Quantify (mention %, avg ★, DSAT lift) and propose fixes + validation."
    if b3.button("Hotspot RCA", use_container_width=True):
        st.session_state["ask_q"] = "Pick the biggest DSAT hotspot (Country/Source/SKU) and do an RCA: what themes drive low stars there, and what should we test next?"
    if b4.button("Seeded vs Organic", use_container_width=True):
        st.session_state["ask_q"] = "Compare Seeded vs Organic: rating gap, DSAT gap, and the top theme differences. Provide actions."
    if b5.button("Trend Watchouts", use_container_width=True):
        st.session_state["ask_q"] = "Are there emerging watchouts recently? Quantify movement and list the most likely drivers and what to monitor."
    if b6.button("Defect Backlog", use_container_width=True):
        st.session_state["ask_q"] = "Create a prioritized defect backlog from detractor symptoms: severity × frequency × trend. Recommend owners/next tests."

    # ----------------------------
    # Ask form
    # ----------------------------
    with st.form("ask_ai_form", clear_on_submit=False):
        q = st.text_area("Ask a question", key="ask_q", height=110, placeholder="Example: What should we fix first to reduce % 1–2★?")
        send = st.form_submit_button("Send")

    if send and q.strip():
        q = q.strip()
        # Reset any previous on-demand search evidence (keeps the Evidence panel relevant to this question)
        st.session_state["_ai_last_search_results"] = []

        # --------------------------------------
        # Local retrieval corpus (enriched)
        # --------------------------------------
        def _row_symptoms_list(row: pd.Series, cols: list[str]) -> list[str]:
            out = []
            for c in cols:
                if c not in row.index:
                    continue
                v = row.get(c, pd.NA)
                if pd.isna(v):
                    continue
                s = str(v).strip()
                if not s or s.upper() in {"<NA>", "NA", "N/A", "-"}:
                    continue
                out.append(s)
            # de-dup preserving order
            seen = set()
            out2 = []
            for x in out:
                xl = x.lower()
                if xl in seen:
                    continue
                seen.add(xl)
                out2.append(x)
            return out2

        # Build enriched text lines ONCE per filter hash
        _enriched_cache = st.session_state.setdefault("_ai_enriched_texts", {})
        if _filters_hash not in _enriched_cache:
            rows = []
            # Keep this fast: avoid expensive apply() with python objects too much; iterrows is OK up to a few thousand.
            for _, r in filtered.iterrows():
                verb = clean_text(r.get("Verbatim", ""))
                verb = _mask_pii(str(verb))
                meta = []
                for f in ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]:
                    if f in filtered.columns:
                        vv = r.get(f, pd.NA)
                        if pd.notna(vv) and str(vv).strip():
                            meta.append(f"{f}={str(vv).strip()}")
                sr = r.get("Star Rating", pd.NA)
                if pd.notna(sr):
                    meta.append(f"Star={sr}")
                dets = _row_symptoms_list(r, existing_detractor_columns)
                dels = _row_symptoms_list(r, existing_delighter_columns)
                if dets:
                    meta.append("Detractors=" + ", ".join(dets[:6]) + ("…" if len(dets) > 6 else ""))
                if dels:
                    meta.append("Delighters=" + ", ".join(dels[:6]) + ("…" if len(dels) > 6 else ""))
                prefix = " | ".join(meta)
                txt = (prefix + " || " if prefix else "") + verb
                rows.append(txt[:2500])
            _enriched_cache[_filters_hash] = rows

        local_texts = _enriched_cache[_filters_hash]
        content_hash_local = _hash_texts(local_texts)
        local_index = _get_or_build_local_text_index(local_texts, content_hash_local)

        hits = _local_search(q, local_index, top_k=10)
        local_quotes = []
        for txt, _score in hits[:6]:
            # Extract only the verbatim portion for display
            s = (txt or "").split("||", 1)[-1].strip()
            s = _mask_pii(s)
            if len(s) > 320:
                s = s[:317] + "…"
            if s:
                local_quotes.append(s)

        # --------------------------------------
        # Evidence pack: add symptom-focused quotes for top drivers
        # --------------------------------------
        evidence_pack = {"local_quotes": local_quotes[:6], "retrieved_quotes": [], "symptom_quotes": []}

        # pick top 2 detractor + top 2 delighter symptoms as extra evidence anchors
        top_det_syms = driver_det["Symptom"].head(2).tolist() if not driver_det.empty else []
        top_del_syms = driver_del["Symptom"].head(2).tolist() if not driver_del.empty else []

        for sym in top_det_syms:
            evidence_pack["symptom_quotes"].extend(_pick_quotes_for_symptom(filtered, sym, existing_detractor_columns, k=2, prefer="low"))
        for sym in top_del_syms:
            evidence_pack["symptom_quotes"].extend(_pick_quotes_for_symptom(filtered, sym, existing_delighter_columns, k=2, prefer="high"))

        # --------------------------------------
        # Local structured answer (always available)
        # --------------------------------------
        def _local_product_identity_answer() -> str:
            prof = product_profile or {}
            lines = []
            lines.append(f"**Product guess:** {prof.get('product_guess','(unknown)')}")
            if prof.get("product_family_guess"):
                conf = prof.get("product_family_confidence")
                sig = prof.get("product_family_signals") or []
                conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else ""
                sig_s = (", ".join(sig[:6])) if sig else ""
                extra = f" (confidence {conf_s})" if conf_s else ""
                lines.append(f"**Likely family:** {prof.get('product_family_guess')}{extra}" + (f" — signals: {sig_s}" if sig_s else ""))
            # Prefer explicit metadata if present
            for k in ["top_product_name", "top_brand", "top_product_category", "top_company", "top_model_(sku)"]:
                if k in prof:
                    pretty = k.replace("top_", "").replace("_", " ").title()
                    vals = prof.get(k) or []
                    if vals:
                        lines.append(f"- **{pretty}:** " + ", ".join(vals[:5]))
            kws = prof.get("top_keywords") or []
            if kws:
                lines.append("- **Common review keywords:** " + ", ".join(kws[:12]))
            bigs = prof.get("top_bigrams") or []
            if bigs:
                lines.append("- **Common phrases:** " + ", ".join(bigs[:10]))
            lines.append("")
            lines.append("If you want a cleaner label, add a column like **Product Name** or **Product Category** to the file (or map SKU → product internally).")
            return "\n".join(lines)

        def _local_csat_answer() -> str:
            lines = []
            lines.append(f"**CSAT snapshot (filtered):** {total_reviews:,} reviews • Avg ★ **{baseline_avg:.2f}** • % 1–2★ **{baseline_pct_low:.1f}%** • % 4–5★ **{baseline_pct_high:.1f}%**.")
            if not driver_det.empty:
                lines.append("")
                lines.append("**Top CSAT risks (detractors) — by Impact:**")
                for _, r in driver_det.head(5).iterrows():
                    lines.append(
                        f"- **{r['Symptom']}** — reviews {int(r['Reviews']):,} ({float(r['Mention %']):.1f}%), "
                        f"Avg ★ {float(r['Avg ★']):.2f}, %1–2★ {float(r['% 1–2★']):.1f}% "
                        f"(Δ Avg {float(r['Δ Avg ★']):+.2f}, Δ DSAT {float(r['Δ % 1–2★']):+.1f}pp)"
                    )
            if not driver_del.empty:
                lines.append("")
                lines.append("**Top strengths (delighters) — protect these:**")
                for _, r in driver_del.head(3).iterrows():
                    lines.append(
                        f"- **{r['Symptom']}** — reviews {int(r['Reviews']):,} ({float(r['Mention %']):.1f}%), Avg ★ {float(r['Avg ★']):.2f}"
                    )
            if evidence_pack["local_quotes"]:
                lines.append("")
                lines.append("**Representative quotes (local retrieval):**")
                for i, s in enumerate(evidence_pack["local_quotes"][:4], start=1):
                    lines.append(f"- [L{i}] “{_mask_pii(s)}”")
            return "\n".join(lines)

        ql = q.lower()
        if "what is this product" in ql or "what product is this" in ql or re.search(r"\bwhat\s+product\b", ql):
            local_answer = _local_product_identity_answer()
        else:
            local_answer = _local_csat_answer()
        final_text = None

        # --------------------------------------
        # Remote LLM (optional)
        # --------------------------------------
        if (not ai_enabled) or (not _HAS_OPENAI) or (not api_key):
            final_text = local_answer
        else:
            try:
                # Vector retrieval (semantic) over a stable subset (optional but helpful for nuanced Qs)
                raw_texts_all = local_texts  # already masked, enriched, truncated
                def _stable_top_k(texts: list[str], k: int) -> list[str]:
                    if k >= len(texts):
                        return texts
                    keyed = [(hashlib.md5((t or "").encode("utf-8")).hexdigest(), t) for t in texts]
                    keyed.sort(key=lambda x: x[0])
                    return [t for _, t in keyed[:k]]

                cap_n = int(ai_cap)
                raw_texts = _stable_top_k(raw_texts_all, cap_n)
                emb_model = "text-embedding-3-small"
                content_hash = _hash_texts(raw_texts)
                index = None
                try:
                    index = _get_or_build_index(content_hash, raw_texts, api_key, emb_model)
                except Exception as e_emb:
                    # Embeddings can fail (quota/rate/network). Don't block the main LLM call.
                    st.session_state["ai_last_error"] = st.session_state.get("ai_last_error") or repr(e_emb)
                    index = None

                retrieved = []
                try:
                    retrieved = vector_search(q, index, api_key, top_k=10) if index else []
                except Exception:
                    retrieved = []
                retrieved_quotes = []
                for txt, _sim in retrieved[:6]:
                    s = (txt or "").split("||", 1)[-1].strip()
                    s = _mask_pii(s)
                    if len(s) > 320:
                        s = s[:317] + "…"
                    if s:
                        retrieved_quotes.append(s)
                evidence_pack["retrieved_quotes"] = retrieved_quotes[:6]

                # Context pack (compact, CSAT-focused)
                style = st.session_state.get("ai_style", "Engineering deep dive")
                ctx = {
                    "product_profile": product_profile,
                     "text_themes": text_themes,
                    "filters": active_filters,
                    "counts": {"total_reviews": total_reviews},
                    "csat": {
                        "avg_star": round(baseline_avg, 3),
                        "median_star": round(baseline_median, 3),
                        "pct_1_2": round(baseline_pct_low, 2),
                        "pct_4_5": round(baseline_pct_high, 2),
                        "star_counts": star_counts,
                    },
                    "top_detractor_drivers": driver_det.head(15).to_dict(orient="records") if not driver_det.empty else [],
                    "top_delighter_drivers": driver_del.head(15).to_dict(orient="records") if not driver_del.empty else [],
                    "hotspots_country": hotspots.get("Country", pd.DataFrame()).head(10).to_dict(orient="records"),
                    "hotspots_source": hotspots.get("Source", pd.DataFrame()).head(10).to_dict(orient="records"),
                    "watchouts": trend_watchouts[:8],
                    "answer_style": style,
                }

                evidence_lines = []
                if send_quotes:
                    for i, s in enumerate(local_quotes[:5], start=1):
                        evidence_lines.append(f"[L{i}] “{_mask_pii(s)}”")
                    for i, s in enumerate(retrieved_quotes[:5], start=1):
                        evidence_lines.append(f"[R{i}] “{_mask_pii(s)}”")
                    for i, qq in enumerate(evidence_pack["symptom_quotes"][:6], start=1):
                        evidence_lines.append(f"[S{i}] “{qq.get('text','')}” — {qq.get('meta','')}")

                # ---------------- Tooling (LLM can ask for exact stats) ----------------
                def _safe_query(qs: str) -> bool:
                    if not qs or len(qs) > 220:
                        return False
                    bad = ["__", "@", "import", "exec", "eval", "os.", "pd.", "open(", "read", "write", "globals", "locals", "`", ";", "\n"]
                    if any(t in qs.lower() for t in bad):
                        return False
                    return bool(re.fullmatch(r"[A-Za-z0-9_ .<>=!&|()'\"-]+", qs))


                def search_reviews(query: str, k: int = 8) -> dict:
                    """Search the *current filtered* consumer-insight corpus (local index).
                    Returns short masked quotes + lightweight metadata for evidence-based answers.
                    """
                    try:
                        qq = (query or "").strip()
                        if not qq:
                            return {"results": []}
                        if len(qq) > 160:
                            qq = qq[:160]

                        kk = max(1, min(int(k or 8), 15))
                        hits2 = _local_search(qq, local_index, top_k=kk)

                        qid = hashlib.md5(qq.encode("utf-8")).hexdigest()[:4]
                        rows = []
                        for i, (txt2, score2) in enumerate(hits2[:kk], start=1):
                            raw = txt2 or ""
                            meta = ""
                            verb = raw
                            if "||" in raw:
                                prefix, verb = raw.split("||", 1)
                                meta = prefix.strip().replace("|", " • ").strip()

                            verb = _mask_pii(str(verb).strip())
                            if len(verb) > 320:
                                verb = verb[:317] + "…"

                            rows.append(
                                {
                                    "id": f"SR{qid}-{i}",
                                    "quote": verb,
                                    "meta": meta,
                                    "score": float(round(float(score2 or 0.0), 4)),
                                }
                            )

                        # Store for the Evidence panel (optional)
                        st.session_state.setdefault("_ai_last_search_results", [])
                        st.session_state["_ai_last_search_results"] = (st.session_state["_ai_last_search_results"] + rows)[-40:]

                        return {"results": rows}
                    except Exception as e:
                        return {"error": str(e)}
                def pandas_count(query: str) -> dict:
                    try:
                        if not _safe_query(query):
                            return {"error": "unsafe query"}
                        res = filtered.query(query)
                        return {"count": int(len(res))}
                    except Exception as e:
                        return {"error": str(e)}

                def pandas_mean(column: str, query: str | None = None) -> dict:
                    try:
                        if column not in filtered.columns:
                            return {"error": f"Unknown column {column}"}
                        d = filtered if not query else (filtered.query(query) if _safe_query(query) else None)
                        if d is None:
                            return {"error": "unsafe query"}
                        return {"mean": float(pd.to_numeric(d[column], errors="coerce").mean())}
                    except Exception as e:
                        return {"error": str(e)}

                def get_csat_snapshot() -> dict:
                    return ctx["csat"] | {"total_reviews": total_reviews}

                def top_drivers(which: str, k: int = 10) -> dict:
                    try:
                        kk = max(1, min(int(k or 10), 50))
                        if (which or "").lower().startswith("del"):
                            d = driver_del.head(kk)
                        else:
                            d = driver_det.head(kk)
                        return {"rows": d.to_dict(orient="records")}
                    except Exception as e:
                        return {"error": str(e)}
                def segment_hotspots(field: str, k: int = 10, min_n: int = 15) -> dict:
                    """DSAT hotspots by segment (uses the current filtered dataframe)."""
                    try:
                        allowed = {"Country", "Source", "Model (SKU)", "Seeded", "New Review"}
                        if field not in allowed:
                            return {"error": f"field must be one of {sorted(list(allowed))}"}

                        kk = max(1, min(int(k or 10), 25))
                        mn = max(1, min(int(min_n or 15), 200))

                        if field not in filtered.columns or "Star Rating" not in filtered.columns or filtered.empty:
                            return {"rows": []}

                        d = filtered[[field, "Star Rating"]].copy()
                        d[field] = d[field].astype("string").fillna("(blank)").str.strip().replace("", "(blank)")
                        d["star"] = pd.to_numeric(d["Star Rating"], errors="coerce")
                        d = d.dropna(subset=["star"])
                        if d.empty:
                            return {"rows": []}
                        d["low"] = d["star"] <= 2
                        d["high"] = d["star"] >= 4
                        g = (
                            d.groupby(field)
                            .agg(Reviews=("star", "size"), Avg=("star", "mean"), PctLow=("low", "mean"), PctHigh=("high", "mean"))
                            .reset_index()
                        )
                        g["Avg ★"] = g["Avg"].round(2)
                        g["% 1–2★"] = (g["PctLow"] * 100).round(1)
                        g["% 4–5★"] = (g["PctHigh"] * 100).round(1)
                        g = g[g["Reviews"] >= mn].sort_values(["% 1–2★", "Reviews"], ascending=[False, False]).head(kk)
                        return {"rows": g[[field, "Reviews", "Avg ★", "% 1–2★", "% 4–5★"]].to_dict(orient="records"), "min_n": mn}
                    except Exception as e:
                        return {"error": str(e)}

                def symptom_deep_dive(symptom: str, k_quotes: int = 4) -> dict:
                    try:
                        sym = (symptom or "").strip()
                        if not sym:
                            return {"error": "missing symptom"}
                        # Try to normalize to known symptom names
                        import difflib
                        all_syms = []
                        if not driver_det.empty:
                            all_syms.extend(driver_det["Symptom"].tolist())
                        if not driver_del.empty:
                            all_syms.extend(driver_del["Symptom"].tolist())
                        all_syms = sorted(set(all_syms))
                        sym_norm = sym.title()
                        if all_syms and sym_norm not in all_syms:
                            m = difflib.get_close_matches(sym_norm, all_syms, n=1, cutoff=0.6)
                            if m:
                                sym_norm = m[0]

                        # Compute stats quickly
                        cols = [c for c in all_sym_cols_present if c in filtered.columns]
                        if not cols:
                            return {"error": "no symptom columns in dataset"}
                        sym_l = sym_norm.lower()
                        mask = pd.Series(False, index=filtered.index)
                        for c in cols:
                            s = filtered[c].astype("string").fillna("").str.strip().str.lower()
                            mask |= s.eq(sym_l)
                        d = filtered[mask]
                        s = pd.to_numeric(d.get("Star Rating"), errors="coerce")
                        snap = {
                            "symptom": sym_norm,
                            "reviews": int(len(d)),
                            "mention_pct": round((len(d) / max(1, total_reviews)) * 100, 2),
                            "avg_star": float(s.mean()) if not s.empty else None,
                            "pct_1_2": float((s <= 2).mean() * 100) if not s.empty else None,
                        }

                        # Top segments
                        seg = {}
                        for f in ["Country", "Source", "Model (SKU)"]:
                            if f in d.columns and len(d):
                                seg[f] = d[f].astype("string").fillna("(blank)").value_counts().head(8).to_dict()

                        # Evidence quotes
                        qq = []
                        qq.extend(_pick_quotes_for_symptom(filtered, sym_norm, existing_detractor_columns, k=int(k_quotes or 4), prefer="low"))
                        if not qq:
                            qq.extend(_pick_quotes_for_symptom(filtered, sym_norm, existing_delighter_columns, k=int(k_quotes or 4), prefer="high"))
                        return {"snapshot": snap, "top_segments": seg, "quotes": qq[: int(k_quotes or 4)]}
                    except Exception as e:
                        return {"error": str(e)}

                def monthly_csat(last_n: int = 6) -> dict:
                    try:
                        if "Review Date" not in filtered.columns or "Star Rating" not in filtered.columns:
                            return {"rows": []}
                        n = max(3, min(int(last_n or 6), 24))
                        d = filtered[["Review Date", "Star Rating"]].copy()
                        d["dt"] = pd.to_datetime(d["Review Date"], errors="coerce")
                        d = d.dropna(subset=["dt"])
                        d["star"] = pd.to_numeric(d["Star Rating"], errors="coerce")
                        d = d.dropna(subset=["star"])
                        if d.empty:
                            return {"rows": []}
                        d["month"] = d["dt"].dt.to_period("M").astype(str)
                        d["low"] = d["star"] <= 2
                        d["high"] = d["star"] >= 4
                        g = d.groupby("month").agg(
                            Reviews=("star", "size"),
                            Avg=("star", "mean"),
                            PctLow=("low", "mean"),
                            PctHigh=("high", "mean"),
                        ).reset_index()
                        g["Avg ★"] = g["Avg"].round(2)
                        g["% 1–2★"] = (g["PctLow"] * 100).round(1)
                        g["% 4–5★"] = (g["PctHigh"] * 100).round(1)
                        g = g.sort_values("month").tail(n)
                        return {"rows": g[["month", "Reviews", "Avg ★", "% 1–2★", "% 4–5★"]].to_dict(orient="records")}
                    except Exception as e:
                        return {"error": str(e)}

                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_csat_snapshot",
                            "description": "Return CSAT/DSAT metrics for the currently filtered dataset.",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "top_drivers",
                            "description": "Return top driver symptoms for CSAT. which='detractors' or 'delighters'.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "which": {"type": "string"},
                                    "k": {"type": "integer"},
                                },
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "segment_hotspots",
                            "description": "Return DSAT hotspots for a segment field (Country/Source/Model (SKU)/Seeded/New Review).",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "k": {"type": "integer"},
                                    "min_n": {"type": "integer"},
                                },
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "symptom_deep_dive",
                            "description": "Deep dive a symptom: volume, DSAT share, affected segments, and evidence quotes.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "symptom": {"type": "string"},
                                    "k_quotes": {"type": "integer"},
                                },
                                "required": ["symptom"],
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "monthly_csat",
                            "description": "Monthly CSAT metrics (reviews, avg star, DSAT/CSAT proxy) for the last N months.",
                            "parameters": {"type": "object", "properties": {"last_n": {"type": "integer"}}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_reviews",
                            "description": "Search the filtered review corpus and return evidence quotes + metadata. Use this when you need more evidence on a topic (e.g., a specific issue, region, SKU).",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                    "k": {"type": "integer"},
                                },
                                "required": ["query"],
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "pandas_count",
                            "description": "Count rows in the filtered dataset matching a safe pandas.query expression.",
                            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "pandas_mean",
                            "description": "Mean of a numeric column for all rows (or those matching a safe pandas.query).",
                            "parameters": {
                                "type": "object",
                                "properties": {"column": {"type": "string"}, "query": {"type": "string"}},
                                "required": ["column"],
                            },
                        },
                    },
                ]

                # System prompt — Quality Engineer / CSAT framing
                style_note = (
                    "Engineering deep dive: include root-cause hypotheses (design vs manufacturing vs packaging/shipping vs usability), and a validation plan."
                    if style == "Engineering deep dive"
                    else "Executive brief: short, crisp summary + 3–6 priorities + risks + next actions."
                )

                sys_ctx = (
                    "You are **CSAT Copilot**, an assistant for a **Quality Engineer**.\n"
                    "Primary goal: improve CSAT by reducing DSAT (1–2★ share) and removing the highest-impact detractors.\n\n"
                    "Hard rules:\n"
                    "- Use ONLY numbers that appear in CONTEXT_JSON or tool outputs. If you need a number, call a tool.\n"
                    "- Do NOT fabricate quotes. Cite only from EVIDENCE_QUOTES by ID (e.g., [L1], [R2], [S1]) **or** from tool outputs (e.g., search_reviews returns ids like [SRabcd-1]).\n- If you need more evidence on a specific issue/segment, call search_reviews(query=..., k=...).\n"
                    "- When you propose actions, include how to validate (what metric moves, what test, what data).\n\n"
                    f"STYLE_GUIDE: {style_note}\n"
                    "RESPONSE_TEMPLATE (use headings, be concise, but insight-dense):\n"
                    "- TL;DR (1–3 bullets)\n"
                    "- What to fix first (top detractors by Impact; quantify volume + DSAT lift)\n"
                    "- Where it happens (Country/Source/SKU hotspots)\n"
                    "- Why it happens (root-cause hypotheses: design vs manufacturing vs packaging/shipping vs usability)\n"
                    "- What to do next (actions + validation plan + what metric should move)\n"
                    "- Evidence (cite quote IDs)\n\n"
                    f"PRODUCT_GUESS={product_label}\n"
                    f"ROW_COUNT={total_reviews}\n\n"
                    f"CONTEXT_JSON:\n{json.dumps(ctx, ensure_ascii=False)}\n\n"
                    "EVIDENCE_QUOTES:\n" + ("\n".join(evidence_lines) if evidence_lines else "(quotes not provided)")
                )

                selected_model = st.session_state.get("llm_model", "gpt-4o-mini")
                llm_temp = float(st.session_state.get("llm_temp", 0.2))

                client = OpenAI(api_key=api_key, timeout=60, max_retries=0)

                # NOTE: GPT‑5 family works best with the Responses API (and can be finicky with tool-calling on Chat Completions).
                # We keep the tool-driven loop for non‑GPT‑5 models, and use Responses for GPT‑5 for maximum reliability.
                if str(selected_model).startswith("gpt-5"):
                    with st.spinner("Thinking…"):
                        resp = client.responses.create(
                            model=selected_model,
                            input=[
                                {"role": "system", "content": sys_ctx},
                                {"role": "user", "content": q},
                            ],
                            store=False,
                        )
                    final_text = getattr(resp, "output_text", None) or ""
                    if not final_text:
                        # Fallback for SDK variants
                        try:
                            final_text = "".join([
                                it.get("text", "") for it in getattr(resp, "output", []) if isinstance(it, dict)
                            ])
                        except Exception:
                            final_text = ""
                else:
                    req = {
                        "model": selected_model,
                        "messages": [{"role": "system", "content": sys_ctx}, {"role": "user", "content": q}],
                        "tools": tools,
                    }
                    if model_supports_temperature(selected_model):
                        req["temperature"] = llm_temp

                    with st.spinner("Thinking…"):
                        first = client.chat.completions.create(**req)

                    msg = first.choices[0].message
                    tool_calls = getattr(msg, "tool_calls", []) or []
                    tool_msgs = []
                    if tool_calls:
                        for call in tool_calls:
                            try:
                                args = json.loads(call.function.arguments or "{}")
                                if not isinstance(args, dict):
                                    args = {}
                            except Exception:
                                args = {}
                            name = call.function.name
                            out = {"error": "unknown tool"}
                            if name == "get_csat_snapshot":
                                out = get_csat_snapshot()
                            if name == "top_drivers":
                                out = top_drivers(args.get("which", "detractors"), int(args.get("k", 10) or 10))
                            if name == "segment_hotspots":
                                out = segment_hotspots(args.get("field", "Country"), int(args.get("k", 10) or 10), int(args.get("min_n", 15) or 15))
                            if name == "symptom_deep_dive":
                                out = symptom_deep_dive(args.get("symptom", ""), int(args.get("k_quotes", 4) or 4))
                            if name == "monthly_csat":
                                out = monthly_csat(int(args.get("last_n", 6) or 6))
                            if name == "search_reviews":
                                out = search_reviews(args.get("query", ""), int(args.get("k", 8) or 8))
                            if name == "pandas_count":
                                out = pandas_count(args.get("query", ""))
                            if name == "pandas_mean":
                                out = pandas_mean(args.get("column", ""), args.get("query"))

                            tool_msgs.append({"tool_call_id": call.id, "role": "tool", "name": name, "content": json.dumps(out)})

                    if tool_msgs:
                        follow = {
                            "model": selected_model,
                            "messages": [
                                {"role": "system", "content": sys_ctx},
                                {"role": "assistant", "tool_calls": tool_calls, "content": None},
                                *tool_msgs,
                            ],
                        }
                        if model_supports_temperature(selected_model):
                            follow["temperature"] = llm_temp
                        res2 = client.chat.completions.create(**follow)
                        final_text = res2.choices[0].message.content
                    else:
                        final_text = msg.content

            except Exception as e:
                st.session_state["ai_last_error"] = repr(e)
                st.info("AI temporarily unavailable. Showing local CSAT brief instead.")
                with st.expander("⚠️ AI error details (why it fell back)", expanded=False):
                    st.write("This is the exact exception raised by the OpenAI client:")
                    st.code(repr(e))
                    st.caption(
                        "Common fixes: confirm the API key is valid, verify the selected model is available on your account, "
                        "and try a non‑GPT‑5 model if your network blocks /responses."
                    )
                final_text = local_answer

        # Render answer
        st.markdown(f"<div class='soft-panel'><b>User:</b> {esc(q)}<br><br><b>Assistant:</b><br>{_html.escape(final_text or '').replace(chr(10), '<br>')}</div>", unsafe_allow_html=True)

        with st.expander("🔎 Evidence used (snippets)", expanded=False):
            if evidence_pack.get("local_quotes"):
                st.markdown("**Local retrieval (covers full dataset):**")
                for i, s in enumerate(evidence_pack["local_quotes"], start=1):
                    st.write(f"[L{i}] “{_mask_pii(s)}”")
            if evidence_pack.get("retrieved_quotes"):
                st.markdown("**Vector retrieval (semantic):**")
                for i, s in enumerate(evidence_pack["retrieved_quotes"], start=1):
                    st.write(f"[R{i}] “{_mask_pii(s)}”")
            if evidence_pack.get("symptom_quotes"):
                st.markdown("**Symptom-anchored quotes (top drivers):**")
                for i, qq in enumerate(evidence_pack["symptom_quotes"][:8], start=1):
                    st.write(f"[S{i}] “{qq.get('text','')}” — {qq.get('meta','')}")

            # On-demand evidence retrieved via tool-calling (if the model asked for it)
            sr = st.session_state.get("_ai_last_search_results") or []
            if sr:
                st.markdown("**On-demand search evidence (tool):**")
                for r in sr[:12]:
                    st.write(f"[{r.get('id','SR')}] “{r.get('quote','')}” — {r.get('meta','')}")
