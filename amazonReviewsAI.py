from __future__ import annotations

import hashlib
import io
import json
import math
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from apify_client import ApifyClient
from apify_client.errors import ApifyApiError
from openai import OpenAI
from pydantic import BaseModel, Field


APP_TITLE = "Amazon Review Intelligence Studio"
DEFAULT_ACTOR_ID = "8vhDnIX6dStLlGVr7"
MAX_REVIEWS_CAP = 100

SORT_OPTIONS = {
    "Most recent": "recent",
    "Most helpful": "helpful",
}
SORT_KEY_TO_LABEL = {value: key for key, value in SORT_OPTIONS.items()}

HOST_TO_MARKET = {
    "amazon.com": {"country": "United States", "label": "US", "domain": ".com"},
    "amazon.co.uk": {"country": "United Kingdom", "label": "UK", "domain": ".co.uk"},
    "amazon.de": {"country": "Germany", "label": "DE", "domain": ".de"},
    "amazon.fr": {"country": "France", "label": "FR", "domain": ".fr"},
    "amazon.it": {"country": "Italy", "label": "IT", "domain": ".it"},
    "amazon.es": {"country": "Spain", "label": "ES", "domain": ".es"},
    "amazon.ca": {"country": "Canada", "label": "CA", "domain": ".ca"},
    "amazon.co.jp": {"country": "Japan", "label": "JP", "domain": ".co.jp"},
}
SUPPORTED_COUNTRIES = sorted({value["country"] for value in HOST_TO_MARKET.values()})

VIDEO_MARKER = "This is a modal window."
ASIN_RE = re.compile(r"\b([A-Z0-9]{10})\b", re.IGNORECASE)

ALL_STAR_FILTER = ("all_stars",)
ALL_STAR_FALLBACK = ("one_star", "two_star", "three_star", "four_star", "five_star")
STAR_BUCKETS: List[Tuple[str, Tuple[str, ...]]] = [
    ("1-star", ("one_star",)),
    ("2-star", ("two_star",)),
    ("3-star", ("three_star",)),
    ("4-star", ("four_star",)),
    ("5-star", ("five_star",)),
]

SCRAPE_MODES = {
    "Fast": "Exact URL first, then one pooled ASIN fallback if needed. Best when speed matters most.",
    "Balanced": "Adaptive recovery across exact URL, ASIN fallback, and selective star fan-out. Best default when you want to get close to 100.",
    "Max coverage": "Most aggressive fan-out across sort orders and star buckets. Slowest, but strongest when a listing keeps stalling at a low count.",
}

STAKEHOLDER_OPTIONS = [
    "Product Development",
    "Quality Engineer",
    "Consumer Insights",
]

AI_REPORT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro"]
CHAT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro"]
WORKSPACE_SECTIONS = ["Overview", "Reviews", "AI report", "Chatbot", "Export", "Help"]


@dataclass(frozen=True)
class PathSpec:
    label: str
    stage: str
    input_value: str
    input_scope: str
    sort_key: str
    rating_label: str
    rating_filters: Tuple[str, ...]
    request_n: int


class ThemeEvidence(BaseModel):
    theme: str
    summary: str
    supporting_reviews: List[str] = Field(default_factory=list)


class QualityRisk(BaseModel):
    issue: str
    severity: str
    why_it_matters: str
    supporting_reviews: List[str] = Field(default_factory=list)
    suggested_owner: str


class FeatureRequest(BaseModel):
    request: str
    rationale: str
    supporting_reviews: List[str] = Field(default_factory=list)
    suggested_owner: str


class ProductIntelReport(BaseModel):
    executive_summary: str
    executive_takeaways: List[str] = Field(default_factory=list)
    jobs_to_be_done: List[str] = Field(default_factory=list)
    delighters: List[ThemeEvidence] = Field(default_factory=list)
    detractors: List[ThemeEvidence] = Field(default_factory=list)
    top_themes: List[ThemeEvidence] = Field(default_factory=list)
    quality_risks: List[QualityRisk] = Field(default_factory=list)
    feature_requests: List[FeatureRequest] = Field(default_factory=list)
    actions_for_product: List[str] = Field(default_factory=list)
    actions_for_quality: List[str] = Field(default_factory=list)
    confidence_note: str


st.set_page_config(page_title=APP_TITLE, layout="wide")


def init_state() -> None:
    defaults: Dict[str, Any] = {
        "workspace_section": "Overview",
        "reviews_df": None,
        "raw_reviews": None,
        "overview": None,
        "report": None,
        "product_meta": None,
        "chat_messages": [],
        "last_scraped_url": "",
        "last_scrape_warning": "",
        "last_run_paths": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def inject_css() -> None:
    st.markdown(
        """
        <style>
            [data-testid="stAppViewContainer"] {
                background:
                    radial-gradient(circle at top right, rgba(16, 185, 129, 0.11), transparent 26%),
                    radial-gradient(circle at top left, rgba(59, 130, 246, 0.12), transparent 30%),
                    linear-gradient(180deg, #f8fafc 0%, #f3f6fb 100%);
            }
            .block-container {
                padding-top: 1.05rem;
                padding-bottom: 2rem;
                max-width: 1400px;
            }
            div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button {
                border-radius: 14px;
                border: 1px solid rgba(15, 23, 42, 0.12);
                font-weight: 600;
                min-height: 2.8rem;
                box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
            }
            .hero-card {
                padding: 1.35rem 1.45rem;
                border-radius: 24px;
                border: 1px solid rgba(15, 23, 42, 0.08);
                background: linear-gradient(135deg, rgba(15, 23, 42, 0.98), rgba(37, 99, 235, 0.94));
                color: white;
                box-shadow: 0 18px 40px rgba(15, 23, 42, 0.18);
                margin-bottom: 1rem;
            }
            .hero-kicker {
                display: inline-block;
                padding: 0.3rem 0.65rem;
                border-radius: 999px;
                background: rgba(255,255,255,0.12);
                font-size: 0.82rem;
                margin-bottom: 0.7rem;
                letter-spacing: 0.02em;
            }
            .soft-card {
                padding: 1rem 1rem 0.95rem 1rem;
                border-radius: 20px;
                border: 1px solid rgba(15, 23, 42, 0.08);
                background: rgba(255, 255, 255, 0.9);
                box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
                margin-bottom: 0.9rem;
                backdrop-filter: blur(8px);
            }
            .stat-card {
                padding: 1rem 1rem 0.95rem 1rem;
                border-radius: 18px;
                border: 1px solid rgba(15, 23, 42, 0.08);
                background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248,250,252,0.96));
                min-height: 126px;
                box-shadow: 0 10px 22px rgba(15, 23, 42, 0.05);
            }
            .stat-label {
                color: #64748b;
                font-size: 0.85rem;
                margin-bottom: 0.45rem;
            }
            .stat-value {
                color: #0f172a;
                font-size: 1.95rem;
                line-height: 1.05;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }
            .mini-note {
                color: #64748b;
                font-size: 0.92rem;
                line-height: 1.45;
            }
            .badge-row {
                margin-top: 0.25rem;
                margin-bottom: 0.2rem;
            }
            .badge {
                display: inline-block;
                padding: 0.28rem 0.62rem;
                margin: 0 0.34rem 0.34rem 0;
                border-radius: 999px;
                border: 1px solid rgba(15, 23, 42, 0.08);
                background: rgba(15, 23, 42, 0.04);
                font-size: 0.84rem;
                color: #0f172a;
            }
            .quote-card {
                padding: 0.95rem 1rem;
                border-radius: 18px;
                border: 1px solid rgba(15, 23, 42, 0.08);
                background: rgba(255,255,255,0.86);
                box-shadow: 0 8px 18px rgba(15,23,42,0.05);
                margin-bottom: 0.75rem;
            }
            .quote-text {
                font-size: 0.98rem;
                line-height: 1.55;
                color: #0f172a;
            }
            .quote-meta {
                color: #64748b;
                font-size: 0.84rem;
                margin-top: 0.6rem;
            }
            .evidence-chip {
                display: inline-block;
                padding: 0.16rem 0.5rem;
                margin: 0.14rem 0.2rem 0.14rem 0;
                border-radius: 999px;
                font-size: 0.78rem;
                color: #1d4ed8;
                background: rgba(59, 130, 246, 0.10);
                border: 1px solid rgba(59, 130, 246, 0.12);
                font-weight: 600;
            }
            .ref-tooltip {
                position: relative;
                display: inline-block;
                vertical-align: middle;
            }
            .ref-tooltip-content {
                visibility: hidden;
                opacity: 0;
                transition: opacity 0.18s ease;
                position: absolute;
                z-index: 1000000;
                left: 50%;
                transform: translateX(-50%);
                bottom: calc(100% + 10px);
                width: min(380px, 70vw);
                padding: 0.75rem 0.85rem;
                border-radius: 16px;
                background: rgba(15, 23, 42, 0.98);
                color: white;
                box-shadow: 0 16px 40px rgba(15, 23, 42, 0.30);
                font-size: 0.82rem;
                line-height: 1.45;
                text-align: left;
                pointer-events: none;
            }
            .ref-tooltip:hover .ref-tooltip-content {
                visibility: visible;
                opacity: 1;
            }
            .ref-tooltip-title {
                font-weight: 700;
                color: #dbeafe;
                margin-bottom: 0.25rem;
            }
            .ref-tooltip-meta {
                color: #cbd5e1;
                margin-bottom: 0.28rem;
            }
            .scrape-modal-overlay {
                position: fixed;
                inset: 0;
                z-index: 999999;
                background: rgba(15, 23, 42, 0.32);
                backdrop-filter: blur(8px);
                display: flex;
                align-items: flex-start;
                justify-content: center;
                padding: 3.2rem 1rem 1.5rem 1rem;
            }
            .scrape-modal-card {
                width: min(860px, 94vw);
                border-radius: 26px;
                background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,250,252,0.98));
                border: 1px solid rgba(15, 23, 42, 0.08);
                box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
                padding: 1.15rem 1.2rem 1.1rem 1.2rem;
            }
            .scrape-kicker {
                display: inline-block;
                padding: 0.28rem 0.62rem;
                border-radius: 999px;
                background: rgba(37, 99, 235, 0.10);
                border: 1px solid rgba(37, 99, 235, 0.12);
                color: #1d4ed8;
                font-size: 0.78rem;
                margin-bottom: 0.5rem;
            }
            .scrape-title {
                color: #0f172a;
                font-size: 1.28rem;
                font-weight: 700;
                margin-bottom: 0.12rem;
            }
            .scrape-subtitle {
                color: #475569;
                font-size: 0.96rem;
                line-height: 1.45;
                margin-bottom: 0.95rem;
            }
            .scrape-progress-track {
                width: 100%;
                height: 12px;
                border-radius: 999px;
                background: rgba(148, 163, 184, 0.22);
                overflow: hidden;
                margin-bottom: 0.95rem;
            }
            .scrape-progress-fill {
                height: 100%;
                border-radius: 999px;
                background: linear-gradient(90deg, #2563eb 0%, #10b981 100%);
                transition: width 0.2s ease;
            }
            .scrape-metrics {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 0.75rem;
                margin-bottom: 0.95rem;
            }
            .scrape-metric {
                border-radius: 18px;
                background: rgba(255,255,255,0.9);
                border: 1px solid rgba(15, 23, 42, 0.06);
                padding: 0.8rem 0.9rem;
            }
            .scrape-metric-label {
                color: #64748b;
                font-size: 0.78rem;
                margin-bottom: 0.26rem;
            }
            .scrape-metric-value {
                color: #0f172a;
                font-weight: 700;
                font-size: 1.2rem;
                line-height: 1.1;
            }
            .scrape-pill-row {
                margin-bottom: 0.65rem;
            }
            .scrape-pill {
                display: inline-block;
                padding: 0.24rem 0.55rem;
                border-radius: 999px;
                margin: 0 0.35rem 0.35rem 0;
                font-size: 0.78rem;
                color: #0f172a;
                background: rgba(15, 23, 42, 0.05);
                border: 1px solid rgba(15, 23, 42, 0.07);
            }
            .scrape-activity-shell {
                border-radius: 20px;
                border: 1px solid rgba(15, 23, 42, 0.06);
                background: rgba(248,250,252,0.92);
                padding: 0.8rem 0.9rem;
            }
            .scrape-activity-title {
                color: #0f172a;
                font-weight: 700;
                margin-bottom: 0.55rem;
            }
            table.scrape-activity-table {
                width: 100%;
                border-collapse: collapse;
                font-size: 0.87rem;
            }
            table.scrape-activity-table th,
            table.scrape-activity-table td {
                text-align: left;
                padding: 0.46rem 0.32rem;
                border-bottom: 1px solid rgba(148, 163, 184, 0.18);
                vertical-align: top;
            }
            table.scrape-activity-table th {
                color: #64748b;
                font-weight: 600;
            }
            .status-chip {
                display: inline-block;
                padding: 0.18rem 0.46rem;
                border-radius: 999px;
                font-size: 0.74rem;
                font-weight: 600;
            }
            .status-running {
                color: #1d4ed8;
                background: rgba(59, 130, 246, 0.12);
            }
            .status-ok {
                color: #047857;
                background: rgba(16, 185, 129, 0.12);
            }
            .status-error {
                color: #b91c1c;
                background: rgba(239, 68, 68, 0.12);
            }
            @media (max-width: 920px) {
                .scrape-metrics {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
            @media (max-width: 640px) {
                .scrape-metrics {
                    grid-template-columns: 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

# ----------------------------
# General helpers
# ----------------------------
def get_secret(name: str) -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
        if "openai" in st.secrets and name in st.secrets["openai"]:
            return str(st.secrets["openai"][name]).strip()
        if "apify" in st.secrets and name in st.secrets["apify"]:
            return str(st.secrets["apify"][name]).strip()
    except Exception:
        return ""
    return ""


def normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value


def is_probably_amazon_url(url: str) -> bool:
    if not url:
        return False
    try:
        host = urlparse(normalize_url(url)).netloc.lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host.startswith("amazon.") or ".amazon." in host


def detect_marketplace(url: str) -> Tuple[Optional[Dict[str, str]], str]:
    if not url:
        return None, ""
    try:
        host = urlparse(normalize_url(url)).netloc.lower()
    except Exception:
        return None, ""
    if host.startswith("www."):
        host = host[4:]
    if host in HOST_TO_MARKET:
        return HOST_TO_MARKET[host], host
    return None, host


def marketplace_status(url: str) -> Tuple[str, str, str]:
    market, host = detect_marketplace(url)
    asin = extract_asin(url)
    if not url:
        return "", "", ""
    if market:
        market_text = f"Detected marketplace: {host} → {market['country']}"
    else:
        market_text = "Marketplace could not be auto-detected"
    asin_text = f"ASIN: {asin}" if asin else "ASIN: not found"
    return market_text, asin_text, host or ""


def extract_asin(text: str) -> str:
    raw = normalize_url(text)
    if not raw:
        return ""

    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"\b([A-Z0-9]{10})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def pick(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_score_value(score: Any) -> Optional[float]:
    if score is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(score))
    return float(match.group(1)) if match else None


def parse_bool(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "verified", "1", "verified purchase"}:
        return True
    if text in {"false", "no", "n", "0", "unverified"}:
        return False
    return None


def split_leading_json(value: str) -> Tuple[Optional[str], str]:
    if not value.startswith("{"):
        return None, value

    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(value):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[: idx + 1], value[idx + 1 :]
    return None, value


def parse_video_meta(json_str: Optional[str]) -> Dict[str, Any]:
    if not json_str:
        return {}
    try:
        data = json.loads(json_str)
    except Exception:
        return {}
    output: Dict[str, Any] = {}
    if data.get("videoUrl"):
        output["VideoUrl"] = data.get("videoUrl")
    if data.get("imageUrl"):
        output["VideoPosterImageUrl"] = data.get("imageUrl")
    if data.get("initialClosedCaptions"):
        output["VideoCaptionsUrl"] = data.get("initialClosedCaptions")
    if output:
        output["HasVideoWidget"] = True
    return output


def clean_review_content(raw: Any) -> Tuple[str, Dict[str, Any], bool]:
    value = "" if raw is None else str(raw)
    if value.startswith("{") and '"videoUrl"' in value and VIDEO_MARKER in value:
        json_str, remainder = split_leading_json(value)
        video_meta = parse_video_meta(json_str)
        remainder = remainder.split(VIDEO_MARKER)[-1].strip()
        remainder = re.sub(r"\s+", " ", remainder).strip()
        return remainder, video_meta, True
    return value.strip(), {}, False


def classify_sentiment(rating_value: Any) -> str:
    try:
        score = float(rating_value)
    except Exception:
        return "Unknown"
    if score >= 4:
        return "Positive"
    if score <= 2:
        return "Negative"
    return "Mixed"


def review_key_from_row(row: Dict[str, Any]) -> str:
    review_id = str(row.get("ReviewId") or "").strip()
    if review_id:
        return f"id::{review_id}"
    review_url = str(row.get("ReviewUrl") or "").strip()
    if review_url:
        return f"url::{review_url}"
    raw = " | ".join(
        [
            str(row.get("Author") or "").strip(),
            str(row.get("Title") or "").strip(),
            str(row.get("ReviewDate") or "").strip(),
            str(row.get("RatingValue") or "").strip(),
            str(row.get("ReviewText") or "").strip(),
        ]
    )
    return "fp::" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


# ----------------------------
# Review shaping / overview
# ----------------------------
def standardize_reviews(
    items: List[Dict[str, Any]],
    product_url: str,
    asin: str,
    country: str,
    marketplace_host: str,
    max_reviews: int,
    retrieval_path: str,
    retrieval_scope: str,
    retrieval_sort: str,
    retrieval_rating: str,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    seen = set()

    for idx, item in enumerate(items, start=1):
        raw_text = pick(item, "ReviewContent", "reviewContent", "content")
        cleaned_text, video_meta, had_video = clean_review_content(raw_text)
        score_text = pick(item, "ReviewScore", "reviewScore", "rating")
        score_value = parse_score_value(score_text)
        verified = parse_bool(
            pick(item, "isVerifiedPurchase", "IsVerifiedPurchase", "verifiedPurchase", "VerifiedPurchase")
        )

        row = {
            "ReviewRef": f"R{idx:03d}",
            "ReviewId": pick(item, "reviewId", "ReviewId", "id", "Id") or "",
            "Title": pick(item, "ReviewTitle", "reviewTitle", "title") or "",
            "ReviewText": cleaned_text,
            "ReviewTextRaw": raw_text or "",
            "Author": pick(item, "AuthorName", "authorName", "author") or "",
            "ReviewDate": pick(item, "ReviewDate", "reviewDate", "date") or "",
            "RatingText": score_text or "",
            "RatingValue": score_value,
            "VerifiedPurchase": verified,
            "HelpfulVotes": pick(item, "HelpfulVoteCount", "helpfulVoteCount", "helpfulVotes") or "",
            "ReviewUrl": pick(item, "ReviewUrl", "reviewUrl", "url") or "",
            "PageUrl": pick(item, "PageUrl", "pageUrl") or "",
            "Variant": pick(item, "ProductVariant", "productVariant", "variation") or "",
            "HasVideoWidget": had_video,
            "ProductUrl": product_url,
            "ASIN": asin,
            "Country": country,
            "MarketplaceHost": marketplace_host,
            "SentimentBucket": classify_sentiment(score_value),
            "RetrievalPath": retrieval_path,
            "RetrievalScope": retrieval_scope,
            "RetrievalSort": SORT_KEY_TO_LABEL.get(retrieval_sort, retrieval_sort),
            "RetrievalRatingBucket": retrieval_rating,
        }
        row.update(video_meta)

        key = review_key_from_row(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= max_reviews:
            break

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame["ReviewRef"] = [f"R{i:03d}" for i in range(1, len(frame) + 1)]
    frame["ReviewDateParsed"] = pd.to_datetime(frame["ReviewDate"], errors="coerce", utc=False)
    return frame


def merge_review_frames(frames: Sequence[pd.DataFrame], limit: Optional[int] = None) -> pd.DataFrame:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return pd.DataFrame()

    merged = pd.concat(non_empty, ignore_index=True)
    deduped_rows: List[Dict[str, Any]] = []
    seen = set()

    for _, row in merged.iterrows():
        payload = row.to_dict()
        key = review_key_from_row(payload)
        if key in seen:
            continue
        seen.add(key)
        deduped_rows.append(payload)
        if limit is not None and len(deduped_rows) >= limit:
            break

    if not deduped_rows:
        return pd.DataFrame()

    frame = pd.DataFrame(deduped_rows)
    frame["ReviewRef"] = [f"R{i:03d}" for i in range(1, len(frame) + 1)]
    if "ReviewDateParsed" not in frame.columns:
        frame["ReviewDateParsed"] = pd.to_datetime(frame["ReviewDate"], errors="coerce", utc=False)
    return frame


def infer_product_title(raw_items: List[Dict[str, Any]], asin: str) -> str:
    keys = [
        "ProductTitle",
        "productTitle",
        "ProductName",
        "productName",
        "Title",
        "title",
    ]
    for item in raw_items:
        for key in keys:
            if item.get(key):
                return str(item[key]).strip()
    return f"Amazon product {asin}" if asin else "Amazon product"


def summarize_overview(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    review_count = int(len(df))
    avg_rating = None
    if "RatingValue" in df.columns and not df["RatingValue"].dropna().empty:
        avg_rating = float(df["RatingValue"].dropna().mean())

    verified_share = None
    if "VerifiedPurchase" in df.columns:
        valid = df["VerifiedPurchase"].dropna()
        if not valid.empty:
            verified_share = float(valid.mean())

    positive_share = None
    negative_share = None
    if "RatingValue" in df.columns:
        valid_ratings = df["RatingValue"].dropna()
        if not valid_ratings.empty:
            positive_share = float((valid_ratings >= 4).mean())
            negative_share = float((valid_ratings <= 2).mean())

    date_min = None
    date_max = None
    if "ReviewDateParsed" in df.columns:
        valid_dates = df["ReviewDateParsed"].dropna()
        if not valid_dates.empty:
            date_min = valid_dates.min().date().isoformat()
            date_max = valid_dates.max().date().isoformat()

    star_distribution: Dict[str, int] = {}
    if "RatingValue" in df.columns and not df["RatingValue"].dropna().empty:
        counts = df["RatingValue"].fillna(0).astype(float).round(0).value_counts().sort_index()
        for star, count in counts.items():
            if star <= 0:
                continue
            star_distribution[f"{int(star)} star"] = int(count)

    overview = {
        "GeneratedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ProductTitle": meta.get("product_title", ""),
        "SourceUrl": meta.get("product_url", ""),
        "ASIN": meta.get("asin", ""),
        "Country": meta.get("country", ""),
        "Marketplace": meta.get("marketplace_host", ""),
        "SortOrder": meta.get("sort_label", ""),
        "VerifiedOnly": meta.get("verified_only", False),
        "ScrapeMode": meta.get("scrape_mode", "Balanced"),
        "RequestedReviews": meta.get("requested_reviews"),
        "ReviewsCollected": review_count,
        "ShortfallToTarget": max(int(meta.get("requested_reviews") or review_count) - review_count, 0),
        "CompletedPaths": meta.get("completed_paths"),
        "AverageRating": round(avg_rating, 2) if avg_rating is not None else None,
        "PositiveShare": round(positive_share * 100, 1) if positive_share is not None else None,
        "NegativeShare": round(negative_share * 100, 1) if negative_share is not None else None,
        "VerifiedShare": round(verified_share * 100, 1) if verified_share is not None else None,
        "ReviewDateMin": date_min,
        "ReviewDateMax": date_max,
        "StarDistributionJSON": json.dumps(star_distribution, ensure_ascii=False),
        "ScrapeWarning": meta.get("warning", ""),
    }
    return pd.DataFrame([overview])

# ----------------------------
# Scraping engine
# ----------------------------
def build_actor_input(
    input_value: str,
    country: str,
    max_reviews: int,
    sort_key: str,
    verified_only: bool,
    rating_filters: Sequence[str],
) -> Dict[str, Any]:
    return {
        "ASIN_or_URL": [input_value],
        "country": country,
        "max_reviews": int(max_reviews),
        "sort_reviews_by": [sort_key],
        "filter_by_verified_purchase_only": ["avp_only_reviews" if verified_only else "all_reviews"],
        "filter_by_mediaType": ["all_contents"],
        "filter_by_ratings": list(rating_filters),
        "unique_only": True,
        "get_customers_say": False,
    }


def alt_sort_key(sort_key: str) -> str:
    return "helpful" if sort_key == "recent" else "recent"


def sort_label_for_key(sort_key: str) -> str:
    return SORT_KEY_TO_LABEL.get(sort_key, sort_key)


def build_scrape_plan(product_url: str, asin: str, max_reviews: int, sort_key: str, scrape_mode: str) -> List[PathSpec]:
    url = normalize_url(product_url)
    asin_value = asin.strip().upper()
    alternate_sort = alt_sort_key(sort_key)
    per_bucket_reviews = min(30, max(16, int(math.ceil(max_reviews / 4))))

    plan: List[PathSpec] = [
        PathSpec(
            label=f"Exact URL · {sort_label_for_key(sort_key)} · All stars",
            stage="Primary",
            input_value=url,
            input_scope="Exact URL",
            sort_key=sort_key,
            rating_label="All stars",
            rating_filters=ALL_STAR_FILTER,
            request_n=max_reviews,
        )
    ]

    if asin_value:
        plan.append(
            PathSpec(
                label=f"ASIN fallback · {sort_label_for_key(sort_key)} · All stars",
                stage="Recovery",
                input_value=asin_value,
                input_scope="ASIN fallback",
                sort_key=sort_key,
                rating_label="All stars",
                rating_filters=ALL_STAR_FILTER,
                request_n=max_reviews,
            )
        )

    if scrape_mode in {"Balanced", "Max coverage"}:
        plan.append(
            PathSpec(
                label=f"Exact URL · {sort_label_for_key(alternate_sort)} · All stars",
                stage="Recovery",
                input_value=url,
                input_scope="Exact URL",
                sort_key=alternate_sort,
                rating_label="All stars",
                rating_filters=ALL_STAR_FILTER,
                request_n=max_reviews,
            )
        )
        if asin_value:
            plan.append(
                PathSpec(
                    label=f"ASIN fallback · {sort_label_for_key(alternate_sort)} · All stars",
                    stage="Recovery",
                    input_value=asin_value,
                    input_scope="ASIN fallback",
                    sort_key=alternate_sort,
                    rating_label="All stars",
                    rating_filters=ALL_STAR_FILTER,
                    request_n=max_reviews,
                )
            )

    bucket_sorts: List[str] = []
    if scrape_mode == "Balanced":
        bucket_sorts = [sort_key]
        if max_reviews >= 90:
            bucket_sorts.append(alternate_sort)
    elif scrape_mode == "Max coverage":
        bucket_sorts = [sort_key, alternate_sort]

    bucket_input = asin_value or url
    bucket_scope = "ASIN fallback" if asin_value else "Exact URL"
    for bucket_sort in bucket_sorts:
        for rating_label, rating_filters in STAR_BUCKETS:
            plan.append(
                PathSpec(
                    label=f"{bucket_scope} · {sort_label_for_key(bucket_sort)} · {rating_label}",
                    stage="Star fan-out",
                    input_value=bucket_input,
                    input_scope=bucket_scope,
                    sort_key=bucket_sort,
                    rating_label=rating_label,
                    rating_filters=rating_filters,
                    request_n=per_bucket_reviews,
                )
            )

    return plan


def run_actor_path(
    client: ApifyClient,
    actor_id: str,
    path: PathSpec,
    country: str,
    verified_only: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    actor_input = build_actor_input(
        input_value=path.input_value,
        country=country,
        max_reviews=path.request_n,
        sort_key=path.sort_key,
        verified_only=verified_only,
        rating_filters=path.rating_filters,
    )
    used_all_star_fallback = False

    try:
        run = client.actor(actor_id).call(run_input=actor_input)
    except ApifyApiError as exc:
        message = str(exc)
        if path.rating_filters == ALL_STAR_FILTER and (
            "all_stars" in message or "filter_by_ratings" in message or "ratings" in message
        ):
            used_all_star_fallback = True
            actor_input["filter_by_ratings"] = list(ALL_STAR_FALLBACK)
            run = client.actor(actor_id).call(run_input=actor_input)
        else:
            raise RuntimeError(message) from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    dataset_id = run.get("defaultDatasetId")
    items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
    return run, items, used_all_star_fallback


def scrape_reviews_with_recovery(
    apify_token: str,
    actor_id: str,
    product_url: str,
    country: str,
    marketplace_host: str,
    max_reviews: int,
    sort_key: str,
    verified_only: bool,
    scrape_mode: str,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], str]:
    client = ApifyClient(apify_token)
    normalized_url = normalize_url(product_url)
    asin = extract_asin(normalized_url)
    plan = build_scrape_plan(normalized_url, asin, max_reviews, sort_key, scrape_mode)

    if progress_callback:
        progress_callback(
            {
                "type": "start",
                "total_paths": len(plan),
                "target_reviews": max_reviews,
                "asin": asin,
                "scrape_mode": scrape_mode,
            }
        )

    all_frames: List[pd.DataFrame] = []
    all_raw_items: List[Dict[str, Any]] = []
    path_logs: List[Dict[str, Any]] = []
    dataset_ids: List[str] = []
    run_ids: List[str] = []
    combined_df = pd.DataFrame()

    for index, path in enumerate(plan, start=1):
        current_unique = int(len(combined_df)) if not combined_df.empty else 0
        if current_unique >= max_reviews:
            break

        if progress_callback:
            progress_callback(
                {
                    "type": "path_start",
                    "index": index,
                    "total_paths": len(plan),
                    "label": path.label,
                    "stage": path.stage,
                    "scope": path.input_scope,
                    "sort_label": sort_label_for_key(path.sort_key),
                    "rating_label": path.rating_label,
                    "requested": path.request_n,
                    "current_unique": current_unique,
                }
            )

        started_at = time.time()
        path_status = "OK"
        error_text = ""
        path_items: List[Dict[str, Any]] = []
        run_obj: Dict[str, Any] = {}
        used_all_star_fallback = False

        try:
            run_obj, path_items, used_all_star_fallback = run_actor_path(
                client=client,
                actor_id=actor_id,
                path=path,
                country=country,
                verified_only=verified_only,
            )
            dataset_id = run_obj.get("defaultDatasetId")
            run_id = run_obj.get("id")
            if dataset_id:
                dataset_ids.append(str(dataset_id))
            if run_id:
                run_ids.append(str(run_id))
        except Exception as exc:
            path_status = "ERROR"
            error_text = str(exc)

        duration_s = max(time.time() - started_at, 0.01)
        previous_unique = current_unique
        path_returned = len(path_items)
        unique_added = 0

        if path_items:
            path_frame = standardize_reviews(
                items=path_items,
                product_url=normalized_url,
                asin=asin,
                country=country,
                marketplace_host=marketplace_host,
                max_reviews=path.request_n,
                retrieval_path=path.label,
                retrieval_scope=path.input_scope,
                retrieval_sort=path.sort_key,
                retrieval_rating=path.rating_label,
            )
            if not path_frame.empty:
                all_frames.append(path_frame)
                combined_df = merge_review_frames(all_frames, limit=max_reviews)
                unique_added = len(combined_df) - previous_unique
            all_raw_items.extend(path_items)

        unique_total = int(len(combined_df)) if not combined_df.empty else previous_unique

        path_log = {
            "Stage": path.stage,
            "Path": path.label,
            "InputScope": path.input_scope,
            "Sort": sort_label_for_key(path.sort_key),
            "Rating": path.rating_label,
            "Requested": path.request_n,
            "Returned": path_returned,
            "UniqueAdded": max(unique_added, 0),
            "UniqueTotal": unique_total,
            "DurationSec": round(duration_s, 2),
            "FallbackAllStars": used_all_star_fallback,
            "Status": path_status,
            "Error": error_text,
        }
        path_logs.append(path_log)

        if progress_callback:
            progress_callback(
                {
                    "type": "path_done",
                    "index": index,
                    "total_paths": len(plan),
                    "label": path.label,
                    "status": path_status,
                    "returned": path_returned,
                    "unique_added": max(unique_added, 0),
                    "unique_total": unique_total,
                    "duration_s": duration_s,
                    "error": error_text,
                }
            )

        if unique_total >= max_reviews:
            break

    if combined_df.empty:
        raise RuntimeError("No reviews were returned across any retrieval path for this product.")

    final_df = combined_df.head(max_reviews).copy().reset_index(drop=True)
    final_df["ReviewRef"] = [f"R{i:03d}" for i in range(1, len(final_df) + 1)]

    product_title = infer_product_title(all_raw_items, asin)
    completed_paths = len(path_logs)
    warning = ""
    if len(final_df) < max_reviews:
        warning = (
            f"Recovered {len(final_df)} unique reviews after {completed_paths} retrieval path(s), which is below the requested {max_reviews}. "
            f"This usually means the exact variant page is sparse, the actor is only exposing a limited pool for that listing, "
            f"or Amazon is heavily pooling or limiting the reachable review pages."
        )

    meta = {
        "product_url": normalized_url,
        "asin": asin,
        "country": country,
        "marketplace_host": marketplace_host,
        "product_title": product_title,
        "dataset_ids": ", ".join(dataset_ids),
        "run_ids": ", ".join(run_ids),
        "requested_reviews": max_reviews,
        "completed_paths": completed_paths,
        "sort_label": sort_label_for_key(sort_key),
        "verified_only": verified_only,
        "scrape_mode": scrape_mode,
        "warning": warning,
    }

    if progress_callback:
        progress_callback(
            {
                "type": "complete",
                "collected": len(final_df),
                "completed_paths": completed_paths,
                "total_paths": len(plan),
                "warning": warning,
            }
        )

    return final_df, all_raw_items, meta, path_logs, warning


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = max(float(seconds), 0.0)
    if seconds < 60:
        return f"{int(round(seconds))}s"
    minutes = int(seconds // 60)
    sec = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours = int(minutes // 60)
    minutes = minutes % 60
    return f"{hours}h {minutes:02d}m"


def estimate_eta_seconds(progress_state: Dict[str, Any]) -> Optional[float]:
    total_paths = int(progress_state.get("total_paths") or 0)
    completed_paths = int(progress_state.get("completed_paths") or 0)
    durations: List[float] = list(progress_state.get("durations") or [])
    scrape_mode = str(progress_state.get("scrape_mode") or "Balanced")
    current_started_at = progress_state.get("current_path_started_at")

    if total_paths <= 0:
        return None

    default_seconds = {
        "Fast": 12.0,
        "Balanced": 15.0,
        "Max coverage": 18.0,
    }.get(scrape_mode, 15.0)
    avg_path_s = (sum(durations) / len(durations)) if durations else default_seconds

    current_remaining = 0.0
    if current_started_at:
        elapsed_current = max(time.time() - float(current_started_at), 0.0)
        current_remaining = max(avg_path_s - elapsed_current, 2.0)

    remaining_after_current = max(total_paths - completed_paths - (1 if current_started_at else 0), 0)
    return max(current_remaining + remaining_after_current * avg_path_s, 0.0)


def compute_progress_pct(progress_state: Dict[str, Any]) -> float:
    target_reviews = max(int(progress_state.get("target_reviews") or 0), 1)
    unique_total = int(progress_state.get("unique_total") or 0)
    total_paths = max(int(progress_state.get("total_paths") or 0), 1)
    completed_paths = int(progress_state.get("completed_paths") or 0)

    path_share = completed_paths / total_paths
    review_share = min(unique_total / target_reviews, 1.0)
    pct = (path_share * 0.55 + review_share * 0.45) * 100
    return max(2.0, min(pct, 100.0))


def render_scrape_overlay(placeholder: Any, progress_state: Dict[str, Any]) -> None:
    current_label = progress_state.get("current_path") or "Preparing retrieval plan"
    stage = progress_state.get("current_stage") or "Initializing"
    target_reviews = int(progress_state.get("target_reviews") or 0)
    unique_total = int(progress_state.get("unique_total") or 0)
    total_paths = int(progress_state.get("total_paths") or 0)
    completed_paths = int(progress_state.get("completed_paths") or 0)
    elapsed_s = max(time.time() - float(progress_state.get("started_at") or time.time()), 0.0)
    eta_s = estimate_eta_seconds(progress_state)
    progress_pct = compute_progress_pct(progress_state)

    path_logs = list(progress_state.get("path_logs") or [])[-6:]
    row_html = ""
    for row in reversed(path_logs):
        status = str(row.get("Status") or "RUNNING")
        status_class = "status-running"
        if status == "OK":
            status_class = "status-ok"
        elif status == "ERROR":
            status_class = "status-error"
        row_html += (
            "<tr>"
            f"<td>{escape(str(row.get('Path') or ''))}</td>"
            f"<td>{escape(str(row.get('Returned') or 0))}</td>"
            f"<td>{escape(str(row.get('UniqueAdded') or 0))}</td>"
            f"<td>{escape(str(row.get('UniqueTotal') or 0))}</td>"
            f"<td>{escape(str(row.get('DurationSec') or ''))}s</td>"
            f"<td><span class='status-chip {status_class}'>{escape(status)}</span></td>"
            "</tr>"
        )
    if not row_html:
        row_html = (
            "<tr>"
            "<td colspan='6' style='color:#64748b;'>Waiting for the first retrieval path to complete…</td>"
            "</tr>"
        )

    eta_text = format_duration(eta_s) if eta_s is not None else "Calibrating…"
    progress_style = f"width:{progress_pct:.1f}%"

    placeholder.markdown(
        f"""
        <div class="scrape-modal-overlay">
            <div class="scrape-modal-card">
                <div class="scrape-kicker">Live scrape tracker</div>
                <div class="scrape-title">Scraping Amazon reviews</div>
                <div class="scrape-subtitle">
                    Stage: <strong>{escape(str(stage))}</strong><br>
                    Current path: <strong>{escape(str(current_label))}</strong>
                </div>
                <div class="scrape-progress-track">
                    <div class="scrape-progress-fill" style="{progress_style}"></div>
                </div>
                <div class="scrape-metrics">
                    <div class="scrape-metric">
                        <div class="scrape-metric-label">Unique reviews</div>
                        <div class="scrape-metric-value">{unique_total}/{target_reviews}</div>
                    </div>
                    <div class="scrape-metric">
                        <div class="scrape-metric-label">Paths complete</div>
                        <div class="scrape-metric-value">{completed_paths}/{total_paths}</div>
                    </div>
                    <div class="scrape-metric">
                        <div class="scrape-metric-label">Elapsed</div>
                        <div class="scrape-metric-value">{escape(format_duration(elapsed_s))}</div>
                    </div>
                    <div class="scrape-metric">
                        <div class="scrape-metric-label">Estimated time left</div>
                        <div class="scrape-metric-value">{escape(eta_text)}</div>
                    </div>
                </div>
                <div class="scrape-pill-row">
                    <span class="scrape-pill">Mode: {escape(str(progress_state.get('scrape_mode') or 'Balanced'))}</span>
                    <span class="scrape-pill">Marketplace: {escape(str(progress_state.get('marketplace_host') or ''))}</span>
                    <span class="scrape-pill">ASIN: {escape(str(progress_state.get('asin') or ''))}</span>
                </div>
                <div class="scrape-activity-shell">
                    <div class="scrape-activity-title">Activity</div>
                    <table class="scrape-activity-table">
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th>Returned</th>
                                <th>Added</th>
                                <th>Total</th>
                                <th>Time</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {row_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def run_scrape_with_live_overlay(
    apify_token: str,
    actor_id: str,
    product_url: str,
    country: str,
    marketplace_host: str,
    max_reviews: int,
    sort_key: str,
    verified_only: bool,
    scrape_mode: str,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], str]:
    events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    outcome: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)

    def on_progress(event: Dict[str, Any]) -> None:
        events.put(event)

    def worker() -> None:
        try:
            result = scrape_reviews_with_recovery(
                apify_token=apify_token,
                actor_id=actor_id,
                product_url=product_url,
                country=country,
                marketplace_host=marketplace_host,
                max_reviews=max_reviews,
                sort_key=sort_key,
                verified_only=verified_only,
                scrape_mode=scrape_mode,
                progress_callback=on_progress,
            )
            outcome.put({"status": "ok", "result": result})
        except Exception as exc:
            outcome.put({"status": "error", "error": str(exc)})

    progress_state: Dict[str, Any] = {
        "started_at": time.time(),
        "target_reviews": max_reviews,
        "unique_total": 0,
        "total_paths": 0,
        "completed_paths": 0,
        "durations": [],
        "current_path": "Preparing retrieval plan",
        "current_stage": "Initializing",
        "current_path_started_at": None,
        "path_logs": [],
        "scrape_mode": scrape_mode,
        "marketplace_host": marketplace_host,
        "asin": extract_asin(product_url),
    }

    overlay = st.empty()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while thread.is_alive() or not events.empty():
        try:
            while True:
                event = events.get_nowait()
                event_type = event.get("type")
                if event_type == "start":
                    progress_state["total_paths"] = int(event.get("total_paths") or 0)
                    progress_state["target_reviews"] = int(event.get("target_reviews") or max_reviews)
                    progress_state["asin"] = event.get("asin") or progress_state.get("asin")
                    progress_state["scrape_mode"] = event.get("scrape_mode") or scrape_mode
                elif event_type == "path_start":
                    progress_state["current_path"] = event.get("label") or "Working"
                    progress_state["current_stage"] = event.get("stage") or "Working"
                    progress_state["current_path_started_at"] = time.time()
                    try:
                        st.toast(f"Processing {event.get('label')}")
                    except Exception:
                        pass
                elif event_type == "path_done":
                    progress_state["completed_paths"] = int(event.get("index") or progress_state.get("completed_paths") or 0)
                    progress_state["unique_total"] = int(event.get("unique_total") or progress_state.get("unique_total") or 0)
                    progress_state.setdefault("durations", []).append(float(event.get("duration_s") or 0.0))
                    progress_state["current_path_started_at"] = None
                    progress_state.setdefault("path_logs", []).append(
                        {
                            "Path": event.get("label"),
                            "Returned": event.get("returned"),
                            "UniqueAdded": event.get("unique_added"),
                            "UniqueTotal": event.get("unique_total"),
                            "DurationSec": round(float(event.get("duration_s") or 0.0), 2),
                            "Status": event.get("status") or "OK",
                        }
                    )
                elif event_type == "complete":
                    progress_state["unique_total"] = int(event.get("collected") or progress_state.get("unique_total") or 0)
                    progress_state["completed_paths"] = int(event.get("completed_paths") or progress_state.get("completed_paths") or 0)
                    progress_state["current_stage"] = "Complete"
                    progress_state["current_path"] = "Finalizing review dataset"
                    progress_state["current_path_started_at"] = None
        except queue.Empty:
            pass

        render_scrape_overlay(overlay, progress_state)
        time.sleep(0.18)

    render_scrape_overlay(overlay, progress_state)
    time.sleep(0.18)
    overlay.empty()

    if outcome.empty():
        raise RuntimeError("The scrape worker stopped before returning a result.")

    result = outcome.get()
    if result.get("status") != "ok":
        raise RuntimeError(str(result.get("error") or "Scrape failed."))
    return result["result"]

# ----------------------------
# OpenAI helpers
# ----------------------------
def build_review_context(df: pd.DataFrame, overview_df: pd.DataFrame, max_chars_per_review: int = 750) -> str:
    overview = overview_df.iloc[0].to_dict()
    header_lines = [
        f"Product title: {overview.get('ProductTitle')}",
        f"Source URL: {overview.get('SourceUrl')}",
        f"ASIN: {overview.get('ASIN')}",
        f"Marketplace: {overview.get('Marketplace')} ({overview.get('Country')})",
        f"Reviews analyzed: {overview.get('ReviewsCollected')}",
        f"Requested reviews: {overview.get('RequestedReviews')}",
        f"Average rating: {overview.get('AverageRating')}",
        f"Positive share %: {overview.get('PositiveShare')}",
        f"Negative share %: {overview.get('NegativeShare')}",
        f"Verified share %: {overview.get('VerifiedShare')}",
        f"Review date window: {overview.get('ReviewDateMin')} to {overview.get('ReviewDateMax')}",
        f"Star distribution JSON: {overview.get('StarDistributionJSON')}",
        f"Scrape warning: {overview.get('ScrapeWarning')}",
    ]

    review_lines = []
    for _, row in df.iterrows():
        text = str(row.get("ReviewText") or "").strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > max_chars_per_review:
            text = text[: max_chars_per_review - 1].rstrip() + "…"
        review_lines.append(
            "\n".join(
                [
                    f"[{row.get('ReviewRef')}] {row.get('RatingValue')} stars | verified={row.get('VerifiedPurchase')} | date={row.get('ReviewDate')} | path={row.get('RetrievalPath')}",
                    f"Title: {row.get('Title')}",
                    f"Review: {text}",
                ]
            )
        )

    return "\n\n".join(header_lines + ["", "Reviews:"] + review_lines)


def reasoning_effort_for_model(model_name: str, task: str) -> str:
    if model_name.endswith("-pro"):
        return "high"
    if task == "chat":
        return "medium"
    return "medium"


def generate_product_intel_report(
    openai_api_key: str,
    model_name: str,
    reviews_df: pd.DataFrame,
    overview_df: pd.DataFrame,
) -> ProductIntelReport:
    client = OpenAI(api_key=openai_api_key)
    dataset_context = build_review_context(reviews_df, overview_df)

    system_prompt = (
        "You are a senior product intelligence analyst helping Product Development, Quality Engineers, and Consumer Insights teams understand Amazon reviews. "
        "Use only the supplied review evidence. Do not invent facts, counts, or review IDs. Cite evidence only with the provided ReviewRef values like R001. "
        "Separate true delight drivers from detractors. Treat durability, reliability, defects, packaging issues, performance instability, and safety-adjacent concerns as quality risks. "
        "Highlight unmet needs and demand signals that Consumer Insights should watch. Keep the executive summary crisp and useful. "
        "If evidence is thin or mixed, say so explicitly in confidence_note."
    )

    user_prompt = (
        "Analyze this Amazon review dataset and produce a structured product intelligence report. "
        "Focus on what Product Development, Quality Engineers, and Consumer Insights should know next.\n\n"
        f"{dataset_context}"
    )

    try:
        response = client.responses.parse(
            model=model_name,
            reasoning={"effort": reasoning_effort_for_model(model_name, "report")},
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text_format=ProductIntelReport,
        )
    except AttributeError as exc:
        raise RuntimeError(
            "Your installed openai package is too old for responses.parse(). Upgrade to a recent version of the openai SDK."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI report generation failed: {exc}") from exc

    report = response.output_parsed
    if not report:
        raise RuntimeError("The AI report was empty.")
    return report


def ask_product_chatbot(
    openai_api_key: str,
    model_name: str,
    reviews_df: pd.DataFrame,
    overview_df: pd.DataFrame,
    report: Optional[ProductIntelReport],
    chat_history: List[Dict[str, str]],
    user_message: str,
    stakeholder_lens: str,
) -> str:
    client = OpenAI(api_key=openai_api_key)
    dataset_context = build_review_context(reviews_df, overview_df, max_chars_per_review=500)
    report_json = report.model_dump_json(indent=2) if report else "{}"

    system_prompt = (
        "You are Product Intelligence Copilot. Answer only from the supplied Amazon review dataset and the structured report. "
        "Never claim evidence that is not present. Cite review refs in square brackets like [R003] whenever you make a factual claim. "
        "If the reviews do not support a conclusion, say that clearly. Tailor your answer to the stakeholder lens provided. "
        "Prefer concise, decision-ready answers for Product Development, Quality Engineers, or Consumer Insights users."
    )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": (
                f"Stakeholder lens: {stakeholder_lens}\n\n"
                f"Structured report JSON:\n{report_json}\n\n"
                f"Review dataset:\n{dataset_context}"
            ),
        },
    ]
    messages.extend(chat_history[-10:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.responses.create(
            model=model_name,
            reasoning={"effort": reasoning_effort_for_model(model_name, "chat")},
            text={"verbosity": "medium"},
            input=messages,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI chat failed: {exc}") from exc

    return response.output_text.strip()


# ----------------------------
# Export helpers
# ----------------------------
def safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[:\\/?*\[\]]", "_", name)
    return cleaned[:31]


def report_to_frames(report: ProductIntelReport) -> Dict[str, pd.DataFrame]:
    executive_rows = [
        {
            "ExecutiveSummary": report.executive_summary,
            "ConfidenceNote": report.confidence_note,
            "ExecutiveTakeaways": " | ".join(report.executive_takeaways),
            "JobsToBeDone": " | ".join(report.jobs_to_be_done),
            "ActionsForProduct": " | ".join(report.actions_for_product),
            "ActionsForQuality": " | ".join(report.actions_for_quality),
        }
    ]

    def theme_rows(items: List[ThemeEvidence]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Theme": item.theme,
                    "Summary": item.summary,
                    "SupportingReviews": ", ".join(item.supporting_reviews),
                }
                for item in items
            ]
        )

    def quality_rows(items: List[QualityRisk]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Issue": item.issue,
                    "Severity": item.severity,
                    "WhyItMatters": item.why_it_matters,
                    "SupportingReviews": ", ".join(item.supporting_reviews),
                    "SuggestedOwner": item.suggested_owner,
                }
                for item in items
            ]
        )

    def request_rows(items: List[FeatureRequest]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Request": item.request,
                    "Rationale": item.rationale,
                    "SupportingReviews": ", ".join(item.supporting_reviews),
                    "SuggestedOwner": item.suggested_owner,
                }
                for item in items
            ]
        )

    return {
        "AI_Executive": pd.DataFrame(executive_rows),
        "AI_Themes": theme_rows(report.top_themes),
        "AI_Delighters": theme_rows(report.delighters),
        "AI_Detractors": theme_rows(report.detractors),
        "AI_Quality_Risks": quality_rows(report.quality_risks),
        "AI_Feature_Requests": request_rows(report.feature_requests),
        "AI_Actions": pd.DataFrame(
            [{"Audience": "Product", "Action": action} for action in report.actions_for_product]
            + [{"Audience": "Quality", "Action": action} for action in report.actions_for_quality]
        ),
    }


def build_excel_bytes(
    reviews_df: pd.DataFrame,
    overview_df: pd.DataFrame,
    report: Optional[ProductIntelReport],
    path_logs: Optional[List[Dict[str, Any]]],
) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        overview_df.to_excel(writer, sheet_name="Overview", index=False)
        reviews_df.to_excel(writer, sheet_name="Reviews", index=False)
        if path_logs:
            pd.DataFrame(path_logs).to_excel(writer, sheet_name="Scrape_Diagnostics", index=False)
        if report:
            for sheet_name, frame in report_to_frames(report).items():
                frame.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
    buffer.seek(0)
    return buffer.read()


# ----------------------------
# Evidence helpers
# ----------------------------
def build_review_ref_lookup(reviews_df: Optional[pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
    if reviews_df is None or reviews_df.empty:
        return {}
    lookup: Dict[str, Dict[str, Any]] = {}
    for _, row in reviews_df.iterrows():
        ref = str(row.get("ReviewRef") or "").strip()
        if ref:
            lookup[ref] = row.to_dict()
    return lookup


def review_tooltip_html(ref: str, review: Optional[Dict[str, Any]]) -> str:
    chip = f'<span class="evidence-chip">{escape(ref)}</span>'
    if not review:
        return chip

    title = str(review.get("Title") or "Untitled review").strip()
    rating = review.get("RatingValue")
    verified = review.get("VerifiedPurchase")
    review_date = str(review.get("ReviewDate") or "").strip()
    text = str(review.get("ReviewText") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 220:
        text = text[:219].rstrip() + "…"

    tooltip = (
        f'<div class="ref-tooltip-title">{escape(ref)} · {escape(title)}</div>'
        f'<div class="ref-tooltip-meta">{escape(str(rating))} stars · verified={escape(str(verified))} · {escape(review_date)}</div>'
        f'<div>{escape(text)}</div>'
    )
    return f'<span class="ref-tooltip">{chip}<span class="ref-tooltip-content">{tooltip}</span></span>'


def render_reference_badges(refs: List[str], ref_lookup: Dict[str, Dict[str, Any]]) -> str:
    if not refs:
        return ""
    return "".join([review_tooltip_html(ref, ref_lookup.get(ref)) for ref in refs])


def render_text_with_review_tooltips(text: str, ref_lookup: Dict[str, Dict[str, Any]]) -> str:
    escaped_text = escape(text or "")

    def replacer(match: re.Match[str]) -> str:
        raw_ref = match.group(0).strip("[]")
        review = ref_lookup.get(raw_ref)
        return review_tooltip_html(raw_ref, review)

    with_refs = re.sub(r"\[?(R\d{3})\]?", replacer, escaped_text)
    return with_refs.replace("\n", "<br>")


def render_theme_cards(items: List[ThemeEvidence], empty_message: str, ref_lookup: Dict[str, Dict[str, Any]]) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = render_reference_badges(item.supporting_reviews, ref_lookup)
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{escape(item.theme)}</strong><br>
                <span>{escape(item.summary)}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_quality_cards(items: List[QualityRisk], empty_message: str, ref_lookup: Dict[str, Dict[str, Any]]) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = render_reference_badges(item.supporting_reviews, ref_lookup)
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{escape(item.issue)}</strong> · <span class="mini-note">Severity: {escape(item.severity)} · Owner: {escape(item.suggested_owner)}</span><br>
                <span>{escape(item.why_it_matters)}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_request_cards(items: List[FeatureRequest], empty_message: str, ref_lookup: Dict[str, Dict[str, Any]]) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = render_reference_badges(item.supporting_reviews, ref_lookup)
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{escape(item.request)}</strong> · <span class="mini-note">Owner: {escape(item.suggested_owner)}</span><br>
                <span>{escape(item.rationale)}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_stat_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-label">{escape(label)}</div>
            <div class="stat-value">{escape(value)}</div>
            <div class="mini-note">{escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sample_review_quotes(reviews_df: pd.DataFrame, sentiment: str, limit: int = 3) -> pd.DataFrame:
    if reviews_df is None or reviews_df.empty:
        return pd.DataFrame()
    filtered = reviews_df.copy()
    if sentiment == "Positive":
        filtered = filtered[filtered["RatingValue"].fillna(0) >= 4]
    elif sentiment == "Negative":
        filtered = filtered[filtered["RatingValue"].fillna(0) <= 2]
    filtered = filtered.sort_values(by=["HelpfulVotes", "ReviewDateParsed"], ascending=[False, False], na_position="last")
    return filtered.head(limit)


def render_quote_cards(df: pd.DataFrame, title: str) -> None:
    st.markdown(f"#### {title}")
    if df is None or df.empty:
        st.info("No review examples available.")
        return
    for _, row in df.iterrows():
        text = str(row.get("ReviewText") or "").strip()
        if len(text) > 260:
            text = text[:259].rstrip() + "…"
        st.markdown(
            f"""
            <div class="quote-card">
                <div class="quote-text">“{escape(text)}”</div>
                <div class="quote-meta">{escape(str(row.get('ReviewRef')))} · {escape(str(row.get('RatingValue')))} stars · {escape(str(row.get('ReviewDate')))} · {escape(str(row.get('Title') or 'Untitled'))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ----------------------------
# View helpers
# ----------------------------
def reset_analysis_state(clear_reviews: bool = False) -> None:
    st.session_state["report"] = None
    st.session_state["chat_messages"] = []
    if clear_reviews:
        st.session_state["reviews_df"] = None
        st.session_state["raw_reviews"] = None
        st.session_state["overview"] = None
        st.session_state["product_meta"] = None
        st.session_state["last_scrape_warning"] = ""
        st.session_state["last_run_paths"] = []


def render_navigation() -> str:
    st.markdown("### Workspace")
    return st.radio(
        "Sections",
        WORKSPACE_SECTIONS,
        key="workspace_section",
        horizontal=True,
        label_visibility="collapsed",
    )


def render_workspace(reviews_df: Optional[pd.DataFrame], overview_df: Optional[pd.DataFrame], meta: Dict[str, Any]) -> None:
    if reviews_df is None or overview_df is None:
        st.info("Fetch reviews to populate the dashboard.")
        return

    overview = overview_df.iloc[0].to_dict()

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        render_stat_card("Reviews", str(int(overview.get("ReviewsCollected") or 0)), "Unique reviews collected")
    with c2:
        render_stat_card("Average rating", str(overview.get("AverageRating") or "—"), "Mean review score")
    with c3:
        pos = overview.get("PositiveShare")
        render_stat_card("Positive share", f"{pos}%" if pos is not None else "—", "4–5 star review share")
    with c4:
        neg = overview.get("NegativeShare")
        render_stat_card("Negative share", f"{neg}%" if neg is not None else "—", "1–2 star review share")
    with c5:
        verified = overview.get("VerifiedShare")
        render_stat_card("Verified share", f"{verified}%" if verified is not None else "—", "Verified purchase share")

    top_left, top_right = st.columns([1.2, 1], vertical_alignment="top")
    with top_left:
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{escape(str(meta.get('product_title') or 'Amazon product'))}</strong><br>
                <span class="mini-note">{escape(str(meta.get('product_url') or ''))}</span><br><br>
                <div class="badge-row">
                    <span class="badge">ASIN: {escape(str(meta.get('asin') or '—'))}</span>
                    <span class="badge">Marketplace: {escape(str(meta.get('marketplace_host') or '—'))}</span>
                    <span class="badge">Country: {escape(str(meta.get('country') or '—'))}</span>
                    <span class="badge">Sort: {escape(str(meta.get('sort_label') or '—'))}</span>
                    <span class="badge">Mode: {escape(str(meta.get('scrape_mode') or 'Balanced'))}</span>
                    <span class="badge">Verified only: {escape(str(meta.get('verified_only') or False))}</span>
                    <span class="badge">Paths used: {escape(str(meta.get('completed_paths') or '—'))}</span>
                </div>
                <div class="mini-note">
                    Review date window: {escape(str(overview.get('ReviewDateMin') or '—'))} to {escape(str(overview.get('ReviewDateMax') or '—'))}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        warning = str(meta.get("warning") or "").strip()
        if warning:
            st.warning(warning)

    with top_right:
        star_json = overview.get("StarDistributionJSON") or "{}"
        try:
            star_data = json.loads(star_json)
        except Exception:
            star_data = {}
        if star_data:
            dist_df = pd.DataFrame({"Star rating": list(star_data.keys()), "Reviews": list(star_data.values())})
            st.bar_chart(dist_df.set_index("Star rating"))
        else:
            st.info("No star distribution available.")

    quote_left, quote_right = st.columns(2, vertical_alignment="top")
    with quote_left:
        render_quote_cards(sample_review_quotes(reviews_df, "Positive", limit=3), "Sample delight evidence")
    with quote_right:
        render_quote_cards(sample_review_quotes(reviews_df, "Negative", limit=3), "Sample detractor evidence")

    path_logs = st.session_state.get("last_run_paths") or []
    if path_logs:
        with st.expander("Scrape diagnostics", expanded=False):
            st.caption("This shows each retrieval path the scraper attempted. It is especially useful when a listing stalls below the target review count.")
            st.dataframe(pd.DataFrame(path_logs), use_container_width=True, hide_index=True)


def render_reviews_view(reviews_df: Optional[pd.DataFrame]) -> None:
    if reviews_df is None or reviews_df.empty:
        st.info("No reviews yet.")
        return

    filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 1.2], vertical_alignment="center")
    with filter_col1:
        sentiment_filter = st.selectbox("Sentiment", ["All", "Positive", "Mixed", "Negative"], index=0)
    with filter_col2:
        rating_filter = st.selectbox("Rating", ["All", "5", "4", "3", "2", "1"], index=0)
    with filter_col3:
        search_text = st.text_input("Search review text or title", value="").strip().lower()

    filtered = reviews_df.copy()
    if sentiment_filter != "All":
        filtered = filtered[filtered["SentimentBucket"] == sentiment_filter]
    if rating_filter != "All":
        filtered = filtered[filtered["RatingValue"].fillna(0).astype(int) == int(rating_filter)]
    if search_text:
        title_mask = filtered["Title"].fillna("").str.lower().str.contains(search_text)
        text_mask = filtered["ReviewText"].fillna("").str.lower().str.contains(search_text)
        filtered = filtered[title_mask | text_mask]

    preview_cols = [
        "ReviewRef",
        "RatingValue",
        "SentimentBucket",
        "VerifiedPurchase",
        "ReviewDate",
        "Title",
        "ReviewText",
        "Author",
        "HelpfulVotes",
        "RetrievalPath",
    ]
    st.dataframe(filtered[preview_cols], use_container_width=True, hide_index=True)

    if filtered.empty:
        st.info("No reviews match the current filters.")
        return

    with st.expander("Evidence browser", expanded=False):
        ref_choice = st.selectbox("Review reference", options=filtered["ReviewRef"].tolist(), index=0)
        selected = filtered[filtered["ReviewRef"] == ref_choice].iloc[0].to_dict()
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{escape(str(selected.get('Title') or 'Untitled review'))}</strong><br>
                <span class="mini-note">{escape(str(selected.get('ReviewRef')))} · {escape(str(selected.get('RatingValue')))} stars · verified={escape(str(selected.get('VerifiedPurchase')))} · {escape(str(selected.get('ReviewDate')))}</span><br><br>
                <span>{escape(str(selected.get('ReviewText') or ''))}</span><br><br>
                <span class="mini-note">Path: {escape(str(selected.get('RetrievalPath') or ''))} · Author: {escape(str(selected.get('Author') or ''))}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_ai_report_view(report: Optional[ProductIntelReport], reviews_df: Optional[pd.DataFrame]) -> None:
    if reviews_df is None:
        st.info("Fetch reviews first.")
        return
    if report is None:
        st.info("Generate the AI report to unlock product intelligence views.")
        return

    ref_lookup = build_review_ref_lookup(reviews_df)
    st.caption("Hover over any review reference like R001 to preview the underlying evidence.")

    st.markdown(
        f"""
        <div class="soft-card">
            <strong>Executive summary</strong><br>
            <span>{render_text_with_review_tooltips(report.executive_summary, ref_lookup)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    takeaway_c1, takeaway_c2 = st.columns(2, vertical_alignment="top")
    with takeaway_c1:
        st.markdown("#### Executive takeaways")
        for item in report.executive_takeaways:
            st.markdown(
                f"<div class='soft-card'>{render_text_with_review_tooltips(item, ref_lookup)}</div>",
                unsafe_allow_html=True,
            )
        st.markdown("#### Jobs to be done")
        for item in report.jobs_to_be_done:
            st.markdown(
                f"<div class='soft-card'>{render_text_with_review_tooltips(item, ref_lookup)}</div>",
                unsafe_allow_html=True,
            )
    with takeaway_c2:
        st.markdown("#### Confidence note")
        st.markdown(
            f"<div class='soft-card'>{render_text_with_review_tooltips(report.confidence_note, ref_lookup)}</div>",
            unsafe_allow_html=True,
        )
        st.markdown("#### Recommended actions")
        for item in report.actions_for_product[:4]:
            st.markdown(
                f"<div class='soft-card'><strong>Product Development</strong><br>{render_text_with_review_tooltips(item, ref_lookup)}</div>",
                unsafe_allow_html=True,
            )
        for item in report.actions_for_quality[:4]:
            st.markdown(
                f"<div class='soft-card'><strong>Quality Engineer</strong><br>{render_text_with_review_tooltips(item, ref_lookup)}</div>",
                unsafe_allow_html=True,
            )

    d1, d2 = st.columns(2, vertical_alignment="top")
    with d1:
        st.markdown("#### Delighters")
        render_theme_cards(report.delighters, "No strong delight themes detected.", ref_lookup)
    with d2:
        st.markdown("#### Detractors")
        render_theme_cards(report.detractors, "No major detractor themes detected.", ref_lookup)

    q1, q2 = st.columns(2, vertical_alignment="top")
    with q1:
        st.markdown("#### Quality risks")
        render_quality_cards(report.quality_risks, "No notable quality risks surfaced.", ref_lookup)
    with q2:
        st.markdown("#### Feature requests")
        render_request_cards(report.feature_requests, "No clear feature requests surfaced.", ref_lookup)

    st.markdown("#### Top cross-cutting themes")
    render_theme_cards(report.top_themes, "No theme map available.", ref_lookup)


def render_chat_message(role: str, content: str, ref_lookup: Dict[str, Dict[str, Any]]) -> None:
    with st.chat_message(role):
        html = render_text_with_review_tooltips(content, ref_lookup)
        st.markdown(html, unsafe_allow_html=True)


def render_chatbot_view(
    openai_api_key: str,
    chat_model: str,
    stakeholder_lens: str,
    reviews_df: Optional[pd.DataFrame],
    overview_df: Optional[pd.DataFrame],
    report: Optional[ProductIntelReport],
) -> None:
    if reviews_df is None or overview_df is None:
        st.info("Fetch reviews first.")
        return
    if not openai_api_key:
        st.info("Add your OpenAI API key to use the chatbot.")
        return

    ref_lookup = build_review_ref_lookup(reviews_df)

    current_lens = st.selectbox(
        "Stakeholder lens",
        options=STAKEHOLDER_OPTIONS,
        index=STAKEHOLDER_OPTIONS.index(stakeholder_lens),
        key="chat_lens_select",
    )
    st.caption("Hover over citations like R001 in the answers to preview the evidence.")

    suggestion_cols = st.columns(4)
    suggestions = [
        "What should Product Development fix first?",
        "What would a Quality Engineer investigate next?",
        "What is Consumer Insights learning about unmet needs?",
        "Which review themes are blocking satisfaction most?",
    ]
    selected_prompt = None
    for col, suggestion in zip(suggestion_cols, suggestions):
        if col.button(suggestion, use_container_width=True):
            selected_prompt = suggestion

    for message in st.session_state.get("chat_messages", []):
        render_chat_message(message["role"], message["content"], ref_lookup)

    prompt = st.chat_input("Ask the product intelligence chatbot")
    user_prompt = prompt or selected_prompt

    if user_prompt:
        st.session_state["chat_messages"].append({"role": "user", "content": user_prompt})
        render_chat_message("user", user_prompt, ref_lookup)

        with st.chat_message("assistant"):
            with st.spinner("Thinking through the review evidence..."):
                try:
                    answer = ask_product_chatbot(
                        openai_api_key=openai_api_key,
                        model_name=chat_model,
                        reviews_df=reviews_df,
                        overview_df=overview_df,
                        report=report,
                        chat_history=st.session_state["chat_messages"][:-1],
                        user_message=user_prompt,
                        stakeholder_lens=current_lens,
                    )
                except Exception as exc:
                    answer = str(exc)
                st.markdown(render_text_with_review_tooltips(answer, ref_lookup), unsafe_allow_html=True)
        st.session_state["chat_messages"].append({"role": "assistant", "content": answer})


def render_export_view(
    reviews_df: Optional[pd.DataFrame],
    overview_df: Optional[pd.DataFrame],
    report: Optional[ProductIntelReport],
    meta: Dict[str, Any],
) -> None:
    if reviews_df is None or overview_df is None:
        st.info("Fetch reviews first.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_root = f"amazon_product_intelligence_{timestamp}"
    path_logs = st.session_state.get("last_run_paths") or []
    excel_bytes = build_excel_bytes(reviews_df, overview_df, report, path_logs)
    csv_bytes = reviews_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    overview_csv = overview_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    diagnostics_csv = pd.DataFrame(path_logs).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig") if path_logs else None

    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "Download Excel workbook",
        data=excel_bytes,
        file_name=f"{file_root}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    c2.download_button(
        "Download reviews CSV",
        data=csv_bytes,
        file_name=f"{file_root}_reviews.csv",
        mime="text/csv",
        use_container_width=True,
    )
    c3.download_button(
        "Download overview CSV",
        data=overview_csv,
        file_name=f"{file_root}_overview.csv",
        mime="text/csv",
        use_container_width=True,
    )

    if diagnostics_csv is not None:
        st.download_button(
            "Download scrape diagnostics CSV",
            data=diagnostics_csv,
            file_name=f"{file_root}_scrape_diagnostics.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if report is not None:
        st.download_button(
            "Download AI report JSON",
            data=report.model_dump_json(indent=2).encode("utf-8"),
            file_name=f"{file_root}_ai_report.json",
            mime="application/json",
            use_container_width=True,
        )
    else:
        st.caption("Generate the AI report first if you want the workbook to include AI summary tabs.")

    st.markdown(
        f"""
        <div class="soft-card">
            <strong>Latest run metadata</strong><br>
            <span class="mini-note">ASIN: {escape(str(meta.get('asin') or '—'))} · Marketplace: {escape(str(meta.get('marketplace_host') or '—'))} · Completed paths: {escape(str(meta.get('completed_paths') or '—'))}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_help_view() -> None:
    st.markdown("### What changed")
    st.markdown(
        "- The workflow is built around a single Amazon product URL.\n"
        "- The app auto-detects supported marketplaces from the URL.\n"
        "- Scraping now uses an adaptive recovery strategy to reduce the common 'only 8 reviews' failure mode.\n"
        "- A live popup tracker shows current retrieval path, recent activity, and estimated time left while scraping.\n"
        "- AI lenses are tuned for Product Development, Quality Engineer, and Consumer Insights.\n"
        "- Hover over citations like R001 in AI views to preview the exact supporting review."
    )

    st.markdown("### Scrape modes")
    for mode, description in SCRAPE_MODES.items():
        st.markdown(f"- **{mode}**: {description}")

    st.markdown("### Supported marketplace auto-detection")
    st.code("amazon.com, amazon.co.uk, amazon.de, amazon.fr, amazon.it, amazon.es, amazon.ca, amazon.co.jp", language="text")

    st.markdown("### Best use cases")
    st.markdown(
        "- Product Development teams prioritizing fixes and feature opportunities\n"
        "- Quality Engineers surfacing defect patterns and reliability risks\n"
        "- Consumer Insights teams spotting unmet needs and delight drivers"
    )

# ----------------------------
# Main app
# ----------------------------
def main() -> None:
    init_state()
    inject_css()

    st.title(APP_TITLE)
    st.markdown(
        """
        <div class="hero-card">
            <div class="hero-kicker">Amazon review intelligence</div>
            <h3 style="margin:0 0 0.35rem 0;">From one Amazon URL to a product-intelligence workspace</h3>
            <div class="mini-note" style="color:rgba(255,255,255,0.85);">
                Auto-detects the Amazon marketplace, scrapes up to 100 reviews with Apify, exports Excel, and layers on an OpenAI-powered copilot for executive summaries, delighters, detractors, quality risks, feature requests, and stakeholder Q&A.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("API keys")
        apify_secret = get_secret("APIFY_TOKEN")
        openai_secret = get_secret("OPENAI_API_KEY")

        use_secrets = st.checkbox("Use Streamlit secrets when available", value=True)
        apify_token = st.text_input(
            "Apify API token",
            type="password",
            value=apify_secret if (use_secrets and apify_secret) else "",
        ).strip()
        openai_api_key = st.text_input(
            "OpenAI API key",
            type="password",
            value=openai_secret if (use_secrets and openai_secret) else "",
        ).strip()

        st.divider()
        st.subheader("Scrape settings")
        actor_id = st.text_input("Apify actor ID", value=DEFAULT_ACTOR_ID)
        max_reviews = st.slider("Reviews to collect", min_value=10, max_value=MAX_REVIEWS_CAP, value=100, step=10)
        sort_label = st.selectbox("Review sort", options=list(SORT_OPTIONS.keys()), index=0)
        verified_only = st.toggle("Verified purchases only", value=False)
        scrape_mode = st.selectbox("Scrape mode", options=list(SCRAPE_MODES.keys()), index=1)
        st.caption(SCRAPE_MODES[scrape_mode])
        manual_country = st.selectbox("Country override", options=["Auto-detect"] + SUPPORTED_COUNTRIES, index=0)

        st.divider()
        st.subheader("AI settings")
        report_model = st.selectbox("AI report model", options=AI_REPORT_MODELS, index=0)
        chat_model = st.selectbox("Chatbot model", options=CHAT_MODELS, index=1)
        stakeholder_lens = st.selectbox(
            "Default stakeholder lens",
            options=STAKEHOLDER_OPTIONS,
            index=0,
        )

        with st.expander("Secrets file example", expanded=False):
            st.code(
                'APIFY_TOKEN = "your_apify_token"\nOPENAI_API_KEY = "your_openai_api_key"',
                language="toml",
            )

    product_url = st.text_input(
        "Amazon product URL",
        value=st.session_state.get("last_scraped_url", ""),
        placeholder="https://www.amazon.com/dp/B0XXXXXXXX",
    )

    market_text, asin_text, detected_host = marketplace_status(product_url)
    info_c1, info_c2, info_c3, info_c4 = st.columns([1.2, 1, 1, 1.1], vertical_alignment="center")
    with info_c1:
        st.caption(market_text or "Paste an Amazon URL to begin")
    with info_c2:
        st.caption(asin_text)
    with info_c3:
        st.caption(f"Review cap: {max_reviews}")
    with info_c4:
        st.caption(f"Mode: {scrape_mode}")

    scrape_col, report_col, clear_col = st.columns([1.2, 1.1, 1], vertical_alignment="center")
    scrape_clicked = scrape_col.button("Fetch reviews", type="primary", use_container_width=True)
    report_clicked = report_col.button(
        "Generate AI report",
        use_container_width=True,
        disabled=st.session_state.get("reviews_df") is None,
    )
    clear_clicked = clear_col.button("Clear session", use_container_width=True)

    if clear_clicked:
        reset_analysis_state(clear_reviews=True)
        st.session_state["last_scraped_url"] = ""
        st.rerun()

    if scrape_clicked:
        if not apify_token:
            st.error("Add your Apify API token in the sidebar.")
        elif not actor_id.strip():
            st.error("Add a valid Apify actor ID.")
        elif not product_url.strip() or not is_probably_amazon_url(product_url):
            st.error("Paste a valid Amazon product URL.")
        else:
            detected_market, detected_host = detect_marketplace(product_url)
            chosen_country = detected_market["country"] if detected_market else None
            if manual_country != "Auto-detect":
                chosen_country = manual_country
            if not chosen_country:
                st.error(
                    "This app could not auto-detect a supported Amazon marketplace from the URL. Use a supported URL or choose a country override."
                )
            else:
                try:
                    reviews_df, raw_items, meta, path_logs, warning = run_scrape_with_live_overlay(
                        apify_token=apify_token,
                        actor_id=actor_id.strip(),
                        product_url=normalize_url(product_url),
                        country=chosen_country,
                        marketplace_host=detected_host or "manual_override",
                        max_reviews=max_reviews,
                        sort_key=SORT_OPTIONS[sort_label],
                        verified_only=verified_only,
                        scrape_mode=scrape_mode,
                    )
                    meta["sort_label"] = sort_label
                    meta["verified_only"] = verified_only
                    meta["warning"] = warning
                    overview_df = summarize_overview(reviews_df, meta)

                    st.session_state["reviews_df"] = reviews_df
                    st.session_state["raw_reviews"] = raw_items
                    st.session_state["overview"] = overview_df
                    st.session_state["product_meta"] = meta
                    st.session_state["last_scraped_url"] = normalize_url(product_url)
                    st.session_state["last_scrape_warning"] = warning
                    st.session_state["last_run_paths"] = path_logs
                    reset_analysis_state(clear_reviews=False)
                    st.session_state["reviews_df"] = reviews_df
                    st.session_state["raw_reviews"] = raw_items
                    st.session_state["overview"] = overview_df
                    st.session_state["product_meta"] = meta
                    st.session_state["last_run_paths"] = path_logs
                    st.session_state["last_scrape_warning"] = warning
                    if warning:
                        st.warning(warning)
                    st.success(f"Collected {len(reviews_df)} unique reviews for {meta['product_title']}.")
                except Exception as exc:
                    st.error(str(exc))

    if report_clicked:
        if st.session_state.get("reviews_df") is None or st.session_state.get("overview") is None:
            st.error("Fetch reviews first.")
        elif not openai_api_key:
            st.error("Add your OpenAI API key in the sidebar.")
        else:
            with st.status("Generating AI report", expanded=True) as report_status:
                try:
                    report_status.write("Analyzing the review evidence with OpenAI…")
                    report = generate_product_intel_report(
                        openai_api_key=openai_api_key,
                        model_name=report_model,
                        reviews_df=st.session_state["reviews_df"],
                        overview_df=st.session_state["overview"],
                    )
                    st.session_state["report"] = report
                    report_status.update(label="AI report ready", state="complete", expanded=False)
                    st.success("AI report generated.")
                except Exception as exc:
                    report_status.update(label="AI report failed", state="error", expanded=True)
                    st.error(str(exc))

    current_view = render_navigation()
    reviews_df = st.session_state.get("reviews_df")
    overview_df = st.session_state.get("overview")
    report = st.session_state.get("report")
    meta = st.session_state.get("product_meta") or {}

    if current_view == "Overview":
        render_workspace(reviews_df, overview_df, meta)
    elif current_view == "Reviews":
        render_reviews_view(reviews_df)
    elif current_view == "AI report":
        render_ai_report_view(report, reviews_df)
    elif current_view == "Chatbot":
        render_chatbot_view(
            openai_api_key=openai_api_key,
            chat_model=chat_model,
            stakeholder_lens=stakeholder_lens,
            reviews_df=reviews_df,
            overview_df=overview_df,
            report=report,
        )
    elif current_view == "Export":
        render_export_view(reviews_df, overview_df, report, meta)
    else:
        render_help_view()


if __name__ == "__main__":
    main()
