from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field


APP_TITLE = "SharkNinja Review Intelligence Studio"
APP_TAGLINE = "Local SharkNinja review scraping, evidence-grounded AI analysis, and local snapshot storage."
MAX_REVIEWS_CAP = 100
LOCAL_STORE_ROOT = Path("local_store") / "sharkninja"
ALLOWED_HOSTS = ("sharkninja.com", "sharkclean.com")

SORT_OPTIONS = {
    "Most recent": "recent",
    "Most helpful": "helpful",
    "Highest rated": "highest",
    "Lowest rated": "lowest",
}

SORT_TO_BAZAARVOICE = {
    "recent": "SubmissionTime:desc",
    "helpful": "TotalFeedbackCount:desc",
    "highest": "Rating:desc",
    "lowest": "Rating:asc",
}

AI_REPORT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro"]
CHAT_MODELS = ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4-pro"]
LENS_OPTIONS = ["Product Development", "Quality Engineer", "Consumer Insights"]

REF_RE = re.compile(r"\bR\d{3}\b")
MODEL_CODE_RE = re.compile(r"\b[A-Z]{1,6}\d{2,}[A-Z0-9-]*\b")
JSONP_RE = re.compile(r"^[\w$.]+\((.*)\)\s*;?\s*$", re.DOTALL)


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
    actions_for_product_development: List[str] = Field(default_factory=list)
    actions_for_quality_engineering: List[str] = Field(default_factory=list)
    actions_for_consumer_insights: List[str] = Field(default_factory=list)
    confidence_note: str


@dataclass
class ProgressEvent:
    ts: float
    stage: str
    detail: str
    progress: float
    collected: int = 0


@dataclass
class ScrapeResult:
    reviews_df: pd.DataFrame
    overview_df: pd.DataFrame
    meta: Dict[str, Any]
    raw_payloads: List[Dict[str, Any]]
    snapshot_dir: Path


# ----------------------------
# Session state / styling
# ----------------------------
def init_state() -> None:
    defaults: Dict[str, Any] = {
        "reviews_df": None,
        "overview_df": None,
        "report": None,
        "product_meta": None,
        "raw_payloads": None,
        "chat_messages": [],
        "last_product_url": "",
        "snapshot_dir": "",
        "nav": "Studio",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def inject_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                --sn-blue: #0f4c81;
                --sn-indigo: #4338ca;
                --sn-teal: #0f766e;
                --sn-slate: #0f172a;
                --sn-surface: rgba(255,255,255,0.88);
                --sn-border: rgba(15, 23, 42, 0.08);
                --sn-shadow: 0 24px 80px rgba(15, 23, 42, 0.10);
            }
            .block-container {
                padding-top: 1.2rem;
                padding-bottom: 2rem;
                max-width: 1400px;
            }
            .sn-hero {
                padding: 1.35rem 1.5rem;
                border-radius: 28px;
                border: 1px solid rgba(255,255,255,0.5);
                background:
                    radial-gradient(circle at top left, rgba(59,130,246,0.20), transparent 34%),
                    radial-gradient(circle at top right, rgba(16,185,129,0.18), transparent 30%),
                    linear-gradient(135deg, rgba(248,250,252,0.96), rgba(240,249,255,0.96));
                box-shadow: var(--sn-shadow);
                margin-bottom: 1rem;
            }
            .sn-hero h2 {
                margin: 0 0 0.3rem 0;
                letter-spacing: -0.02em;
            }
            .sn-muted {
                color: #64748b;
                font-size: 0.96rem;
            }
            .sn-card {
                background: var(--sn-surface);
                border: 1px solid var(--sn-border);
                border-radius: 22px;
                padding: 1rem 1rem 0.95rem 1rem;
                box-shadow: 0 16px 40px rgba(15, 23, 42, 0.05);
                margin-bottom: 0.95rem;
                backdrop-filter: blur(8px);
            }
            .sn-card h4, .sn-card h5 {
                margin: 0 0 0.35rem 0;
            }
            .sn-chip-row {
                display: flex;
                gap: 0.35rem;
                flex-wrap: wrap;
                margin-top: 0.6rem;
            }
            .sn-chip {
                display: inline-flex;
                align-items: center;
                gap: 0.25rem;
                padding: 0.18rem 0.58rem;
                border-radius: 999px;
                background: rgba(15,23,42,0.05);
                border: 1px solid rgba(15,23,42,0.09);
                font-size: 0.81rem;
            }
            .sn-ref {
                position: relative;
                display: inline-flex;
                align-items: center;
                gap: 0.25rem;
                padding: 0.08rem 0.46rem;
                margin: 0 0.12rem;
                border-radius: 999px;
                border: 1px solid rgba(15,23,42,0.10);
                background: rgba(255,255,255,0.72);
                color: var(--sn-indigo);
                font-weight: 600;
                cursor: help;
                text-decoration: none;
            }
            .sn-ref:hover {
                background: rgba(224,231,255,0.88);
                border-color: rgba(67,56,202,0.22);
            }
            .sn-ref .sn-tooltip {
                display: none;
                position: absolute;
                left: 0;
                top: calc(100% + 10px);
                min-width: 320px;
                max-width: 420px;
                padding: 0.72rem 0.78rem;
                border-radius: 16px;
                background: rgba(15,23,42,0.96);
                color: #f8fafc;
                font-weight: 400;
                line-height: 1.45;
                z-index: 9999;
                box-shadow: 0 18px 48px rgba(15,23,42,0.35);
            }
            .sn-ref:hover .sn-tooltip {
                display: block;
            }
            .sn-tooltip-title {
                font-weight: 700;
                margin-bottom: 0.28rem;
                color: white;
            }
            .sn-tooltip-meta {
                color: rgba(226,232,240,0.88);
                font-size: 0.81rem;
                margin-bottom: 0.38rem;
            }
            .sn-list li {
                margin-bottom: 0.36rem;
            }
            .sn-overlay {
                position: fixed;
                right: 22px;
                bottom: 22px;
                width: min(440px, calc(100vw - 38px));
                background: rgba(15,23,42,0.96);
                color: #f8fafc;
                border: 1px solid rgba(148,163,184,0.20);
                border-radius: 22px;
                box-shadow: 0 30px 80px rgba(15,23,42,0.42);
                padding: 1rem 1rem 0.85rem 1rem;
                z-index: 9998;
                backdrop-filter: blur(14px);
            }
            .sn-overlay h4 {
                margin: 0 0 0.25rem 0;
                font-size: 1rem;
                color: #fff;
            }
            .sn-overlay-meta {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 0.6rem;
                margin: 0.7rem 0 0.65rem 0;
            }
            .sn-overlay-metric {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
                padding: 0.55rem 0.6rem;
            }
            .sn-overlay-metric-label {
                color: rgba(226,232,240,0.75);
                font-size: 0.72rem;
                margin-bottom: 0.1rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }
            .sn-overlay-metric-value {
                font-size: 1rem;
                font-weight: 700;
                color: white;
            }
            .sn-progress-track {
                width: 100%;
                height: 10px;
                border-radius: 999px;
                background: rgba(255,255,255,0.10);
                overflow: hidden;
                margin: 0.5rem 0 0.75rem 0;
            }
            .sn-progress-fill {
                height: 100%;
                border-radius: 999px;
                background: linear-gradient(90deg, #38bdf8, #34d399);
            }
            .sn-activity {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 16px;
                padding: 0.55rem 0.65rem;
                max-height: 210px;
                overflow-y: auto;
            }
            .sn-activity-item {
                padding: 0.42rem 0;
                border-bottom: 1px solid rgba(255,255,255,0.06);
            }
            .sn-activity-item:last-child {
                border-bottom: none;
            }
            .sn-kpi-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 0.8rem;
            }
            .sn-kpi {
                background: rgba(255,255,255,0.76);
                border: 1px solid var(--sn-border);
                border-radius: 20px;
                padding: 0.9rem 1rem;
                box-shadow: 0 16px 40px rgba(15, 23, 42, 0.04);
            }
            .sn-kpi-label {
                color: #64748b;
                font-size: 0.78rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                margin-bottom: 0.25rem;
            }
            .sn-kpi-value {
                font-size: 1.45rem;
                font-weight: 700;
                color: #0f172a;
            }
            @media (max-width: 900px) {
                .sn-kpi-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
                .sn-overlay-meta {
                    grid-template-columns: 1fr 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------
# Generic helpers
# ----------------------------
def get_secret(name: str) -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
        if "openai" in st.secrets and name in st.secrets["openai"]:
            return str(st.secrets["openai"][name]).strip()
    except Exception:
        return ""
    return ""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value


def host_candidates(host: str) -> List[str]:
    host = host.lower().split(":")[0]
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts)) if ".".join(parts[i:])]


def is_sharkninja_url(url: str) -> bool:
    try:
        host = urlparse(normalize_url(url)).netloc.lower()
    except Exception:
        return False
    return any(candidate.endswith(ALLOWED_HOSTS) for candidate in host_candidates(host))


def pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) not in (None, "", [], {}):
            return item.get(key)
    return None


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    return value[:80] or "snapshot"


def safe_sheet_name(name: str) -> str:
    return re.sub(r"[:\\/?*\[\]]", "_", name)[:31]


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        match = re.search(r"(\d+(?:\.\d+)?)", str(value))
        return float(match.group(1)) if match else None


def format_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def compact_text(value: Any, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def infer_model_code(url: str, html_text: str = "") -> str:
    path = urlparse(url).path or ""
    basename = Path(path).name.replace(".html", "")
    if basename and MODEL_CODE_RE.fullmatch(basename):
        return basename.upper()
    search_space = " ".join([path, html_text])
    match = MODEL_CODE_RE.search(search_space)
    return match.group(0).upper() if match else ""


def parse_date(value: Any) -> Any:
    try:
        return pd.to_datetime(value, errors="coerce")
    except Exception:
        return pd.NaT


def classify_sentiment(rating: Any) -> str:
    score = safe_float(rating)
    if score is None:
        return "Unknown"
    if score >= 4:
        return "Positive"
    if score <= 2:
        return "Negative"
    return "Mixed"


def review_dedupe_key(item: Dict[str, Any]) -> str:
    for key in ("ReviewId", "review_id", "Id", "id", "ExternalId"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"id::{value}"
    title = str(item.get("Title") or "").strip()
    text = str(item.get("ReviewText") or "").strip()
    author = str(item.get("Author") or "").strip()
    date = str(item.get("ReviewDate") or "").strip()
    rating = str(item.get("RatingValue") or "").strip()
    raw = " | ".join([title, text, author, date, rating])
    return "fp::" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def dedupe_reviews(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        key = review_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(dict(item))
    return output


def compatible_model_dump_json(model: BaseModel) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json(indent=2)
    return model.json(indent=2)


def compatible_model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return json.loads(model.json())


# ----------------------------
# JSON / API parsing helpers
# ----------------------------
def extract_balanced_json(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
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
                    return text[start : idx + 1]
    return None


def parse_json_like(raw: str) -> Optional[Any]:
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]
    match = JSONP_RE.match(text)
    if match:
        candidates.insert(0, match.group(1).strip())

    balanced = extract_balanced_json(text)
    if balanced and balanced not in candidates:
        candidates.append(balanced)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def looks_like_review_dict(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {k.lower() for k in item.keys()}
    signal_keys = {
        "reviewtext",
        "title",
        "rating",
        "submissiontime",
        "reviewid",
        "usernickname",
        "author",
        "reviewdate",
    }
    return bool(keys & signal_keys)


def walk_review_payloads(data: Any, trail: str = "root") -> List[Tuple[str, Dict[str, Any]]]:
    results: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(data, dict):
        payload_results = data.get("Results")
        if isinstance(payload_results, list):
            if not payload_results or any(looks_like_review_dict(x) for x in payload_results if isinstance(x, dict)):
                results.append((trail, data))
        batched = data.get("BatchedResults")
        if isinstance(batched, dict):
            for key, value in batched.items():
                results.extend(walk_review_payloads(value, f"{trail}.BatchedResults.{key}"))
        for key, value in data.items():
            if key in {"Results", "BatchedResults"}:
                continue
            results.extend(walk_review_payloads(value, f"{trail}.{key}"))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            results.extend(walk_review_payloads(value, f"{trail}[{idx}]"))
    return results


def extract_ld_json_objects(html_text: str) -> List[Any]:
    soup = BeautifulSoup(html_text or "", "lxml")
    objects: List[Any] = []
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text(" ", strip=False)
        data = parse_json_like(raw)
        if data is not None:
            objects.append(data)
    return objects


def extract_product_ld_meta(ld_objects: Sequence[Any]) -> Dict[str, Any]:
    for obj in ld_objects:
        candidates: List[Dict[str, Any]] = []
        if isinstance(obj, dict):
            if obj.get("@type") == "Product":
                candidates.append(obj)
            graph = obj.get("@graph")
            if isinstance(graph, list):
                candidates.extend([x for x in graph if isinstance(x, dict) and x.get("@type") == "Product"])
        elif isinstance(obj, list):
            candidates.extend([x for x in obj if isinstance(x, dict) and x.get("@type") == "Product"])

        for product in candidates:
            aggregate = product.get("aggregateRating") or {}
            return {
                "product_title": product.get("name") or "",
                "sku": product.get("sku") or product.get("mpn") or "",
                "brand": (product.get("brand") or {}).get("name") if isinstance(product.get("brand"), dict) else product.get("brand") or "",
                "site_average_rating": safe_float(aggregate.get("ratingValue")),
                "site_review_count": int(float(aggregate.get("reviewCount"))) if aggregate.get("reviewCount") not in (None, "") else None,
            }
    return {}


def standardize_bazaarvoice_item(item: Dict[str, Any], source_url: str, source_method: str, product_url: str) -> Optional[Dict[str, Any]]:
    title = pick(item, "Title", "title") or ""
    text = pick(item, "ReviewText", "reviewText", "Text", "text") or ""
    if not title and not text:
        return None

    badges = pick(item, "Badges", "badges")
    badge_labels: List[str] = []
    if isinstance(badges, dict):
        badge_labels = [str(k) for k in badges.keys()]

    photo_count = 0
    video_count = 0
    for key in ("Photos", "photos", "PhotoUrls", "photoUrls"):
        value = item.get(key)
        if isinstance(value, list):
            photo_count = max(photo_count, len(value))
    for key in ("Videos", "videos", "VideoUrls", "videoUrls"):
        value = item.get(key)
        if isinstance(value, list):
            video_count = max(video_count, len(value))

    recommended = pick(item, "IsRecommended", "isRecommended")
    if isinstance(recommended, str):
        recommended = recommended.strip().lower() in {"true", "yes", "1"}

    review = {
        "ReviewId": pick(item, "Id", "ReviewId", "id") or "",
        "Title": title,
        "ReviewText": text,
        "Author": pick(item, "UserNickname", "Author", "author", "Nickname") or "",
        "AuthorLocation": pick(item, "UserLocation", "userLocation") or "",
        "ReviewDate": pick(item, "SubmissionTime", "ReviewDate", "LastModificationTime") or "",
        "RatingValue": safe_float(pick(item, "Rating", "rating", "OverallRating")),
        "RatingText": pick(item, "Rating", "rating") or "",
        "HelpfulYes": pick(item, "TotalPositiveFeedbackCount", "HelpfulVoteCount") or 0,
        "HelpfulNo": pick(item, "TotalNegativeFeedbackCount") or 0,
        "Recommended": recommended,
        "Variant": pick(item, "OriginalProductName", "ProductName", "productName", "Variation") or "",
        "ProductId": pick(item, "ProductId", "productId") or "",
        "SourceClient": pick(item, "SourceClient", "sourceClient") or "",
        "SyndicationSource": pick(item, "SyndicationSource", "SyndicationSourceName") or "",
        "Badges": ", ".join(badge_labels),
        "PhotoCount": photo_count,
        "VideoCount": video_count,
        "SourceMethod": source_method,
        "SourceUrl": source_url,
        "ProductUrl": product_url,
        "VerifiedPurchase": any("verified" in label.lower() for label in badge_labels),
    }
    return review


def standardize_ld_review(item: Dict[str, Any], product_url: str) -> Optional[Dict[str, Any]]:
    rating_value = None
    if isinstance(item.get("reviewRating"), dict):
        rating_value = safe_float(item["reviewRating"].get("ratingValue"))
    if rating_value is None:
        rating_value = safe_float(item.get("ratingValue"))

    author = ""
    if isinstance(item.get("author"), dict):
        author = str(item["author"].get("name") or "").strip()
    else:
        author = str(item.get("author") or "").strip()

    title = str(item.get("name") or item.get("headline") or "").strip()
    body = str(item.get("reviewBody") or item.get("description") or "").strip()
    if not title and not body:
        return None

    return {
        "ReviewId": str(item.get("@id") or "").strip(),
        "Title": title,
        "ReviewText": body,
        "Author": author,
        "AuthorLocation": "",
        "ReviewDate": str(item.get("datePublished") or "").strip(),
        "RatingValue": rating_value,
        "RatingText": rating_value if rating_value is not None else "",
        "HelpfulYes": "",
        "HelpfulNo": "",
        "Recommended": None,
        "Variant": "",
        "ProductId": "",
        "SourceClient": "schema_org",
        "SyndicationSource": "",
        "Badges": "",
        "PhotoCount": 0,
        "VideoCount": 0,
        "SourceMethod": "ld_json",
        "SourceUrl": product_url,
        "ProductUrl": product_url,
        "VerifiedPurchase": None,
    }


def extract_ld_reviews(ld_objects: Sequence[Any], product_url: str) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("@type") == "Review":
                parsed = standardize_ld_review(obj, product_url)
                if parsed:
                    collected.append(parsed)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    for obj in ld_objects:
        walk(obj)
    return collected


# ----------------------------
# HTTP scraping helpers
# ----------------------------
def build_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    return session


def fetch_page_html(session: requests.Session, url: str, timeout: int = 45) -> Tuple[str, str]:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response.text, response.url


def extract_candidate_review_api_urls(html_text: str, page_url: str) -> List[str]:
    candidates: List[str] = []

    patterns = [
        r"https?://[^\"'\s<>]+(?:bazaarvoice|reviews\.json)[^\"'\s<>]*",
        r"//[^\"'\s<>]+(?:bazaarvoice|reviews\.json)[^\"'\s<>]*",
        r"https?:\\/\\/[^\"'\s<>]+(?:bazaarvoice|reviews\.json)[^\"'\s<>]*",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html_text or '', flags=re.I):
            url = html.unescape(match).replace('\\/', '/').strip('"\' ')
            if url.startswith('//'):
                url = 'https:' + url
            if 'reviews.json' in url.lower() or 'bazaarvoice' in url.lower():
                candidates.append(url)

    soup = BeautifulSoup(html_text or '', 'lxml')
    attrs = ['data-bv-show', 'data-bv-product-id', 'data-bv-seo', 'data-bv-url', 'src', 'href']
    for node in soup.find_all(True):
        for attr in attrs:
            value = node.get(attr)
            if not value or not isinstance(value, str):
                continue
            lowered = value.lower()
            if 'bazaarvoice' in lowered or 'reviews.json' in lowered:
                url = html.unescape(value).replace('\\/', '/')
                if url.startswith('//'):
                    url = 'https:' + url
                elif url.startswith('/'):
                    parsed = urlparse(page_url)
                    url = f'{parsed.scheme}://{parsed.netloc}{url}'
                candidates.append(url)

    deduped: List[str] = []
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def fetch_review_pages_via_http(
    session: requests.Session,
    base_url: str,
    target_reviews: int,
    progress_cb: Callable[[str, str, float, int], None],
    merged_seed: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    fallback_rows: List[Dict[str, Any]] = []
    max_pages = max(2, math.ceil(target_reviews / 8) + 2)
    for page_num in range(1, max_pages + 1):
        page_url = append_bvstate(base_url, page_num)
        try:
            html_page, _ = fetch_page_html(session, page_url, timeout=45)
            batch = parse_reviews_from_dom_html(html_page, page_url)
            batch += extract_ld_reviews(extract_ld_json_objects(html_page), page_url)
            batch = dedupe_reviews(batch)
            if not batch:
                if page_num > 1:
                    break
                continue
            before = len(dedupe_reviews(fallback_rows))
            fallback_rows.extend(batch)
            after = len(dedupe_reviews(fallback_rows))
            progress = min(0.92, 0.72 + 0.20 * (page_num / max_pages))
            progress_cb(
                'HTML fallback',
                f'Processed SharkNinja review page {page_num}. Collected {after} direct-page review(s).',
                progress,
                len(dedupe_reviews(list(merged_seed) + fallback_rows)),
            )
            if after == before and page_num > 1:
                break
            if len(dedupe_reviews(list(merged_seed) + fallback_rows)) >= target_reviews:
                break
        except Exception:
            if page_num == 1:
                continue
            break
    return dedupe_reviews(fallback_rows)


# ----------------------------
# DOM / HTML parsing helpers
# ----------------------------
def accept_cookie_banner(page: Any) -> None:
    patterns = [
        re.compile(r"accept", re.I),
        re.compile(r"allow", re.I),
        re.compile(r"got it", re.I),
    ]
    for pattern in patterns:
        for role in ("button", "link"):
            try:
                locator = page.get_by_role(role, name=pattern)
                if locator.count() > 0:
                    locator.first.click(timeout=2000)
                    page.wait_for_timeout(600)
                    return
            except Exception:
                continue


def open_review_panel(page: Any) -> None:
    patterns = [
        re.compile(r"read\s+\d*\s*reviews", re.I),
        re.compile(r"customer reviews", re.I),
        re.compile(r"reviews", re.I),
        re.compile(r"rating snapshot", re.I),
    ]

    for _ in range(2):
        try:
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(600)
        except Exception:
            pass

    for pattern in patterns:
        for role in ("link", "button"):
            try:
                locator = page.get_by_role(role, name=pattern)
                if locator.count() > 0:
                    locator.first.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    return
            except Exception:
                continue
    try:
        locator = page.get_by_text(re.compile(r"reviews", re.I))
        if locator.count() > 0:
            locator.first.click(timeout=2000)
            page.wait_for_timeout(1000)
    except Exception:
        pass


def get_first_text(node: Any, selectors: Sequence[str]) -> str:
    for selector in selectors:
        try:
            found = node.select_one(selector)
        except Exception:
            found = None
        if found:
            text = found.get_text(" ", strip=True)
            if text:
                return text
    return ""


def get_attr_match(node: Any, attrs: Iterable[str]) -> str:
    for candidate in node.find_all(True):
        for attr in attrs:
            value = candidate.get(attr)
            if value and isinstance(value, str):
                match = re.search(r"(\d+(?:\.\d+)?)\s*out of\s*5", value, flags=re.I)
                if match:
                    return match.group(1)
    return ""


def parse_reviews_from_dom_html(html_text: str, product_url: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_text or "", "lxml")
    selectors = [
        "[class*='bv-content-review']",
        "[data-bv-v='review']",
        "[itemprop='review']",
        "article[class*='review']",
        "li[class*='review']",
        "div[class*='review']",
    ]
    candidates = []
    seen_signatures = set()
    for selector in selectors:
        try:
            found = soup.select(selector)
        except Exception:
            found = []
        for node in found:
            signature = hashlib.sha1(str(node).encode("utf-8", errors="ignore")).hexdigest()
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            candidates.append(node)

    parsed_rows: List[Dict[str, Any]] = []
    for node in candidates:
        title = get_first_text(
            node,
            [
                ".bv-content-title",
                ".bv-content-review-title",
                "[class*='review-title']",
                "[itemprop='name']",
                "[data-bv-v='review-title']",
            ],
        )
        body = get_first_text(
            node,
            [
                ".bv-content-review-body-text",
                ".bv-content-review-body",
                "[class*='review-body']",
                "[class*='review-text']",
                "[itemprop='reviewBody']",
                "[data-bv-v='review-text']",
            ],
        )
        author = get_first_text(
            node,
            [
                ".bv-content-author-name",
                "[class*='author']",
                "[itemprop='author']",
                "[data-bv-v='author']",
            ],
        )
        date = get_first_text(
            node,
            [
                ".bv-content-datetime-stamp",
                "time",
                "[itemprop='datePublished']",
                "[data-bv-v='submission-time']",
                "[class*='date']",
            ],
        )
        rating_text = get_first_text(
            node,
            [
                ".bv-content-rating-rating",
                "[itemprop='ratingValue']",
                "[data-bv-v='rating']",
                "[class*='rating']",
            ],
        ) or get_attr_match(node, ["aria-label", "title"])

        full_text = node.get_text(" ", strip=True)
        if not body and len(full_text) < 40:
            continue
        if not title and not body:
            continue

        parsed_rows.append(
            {
                "ReviewId": "",
                "Title": title,
                "ReviewText": body or compact_text(full_text, limit=600),
                "Author": author,
                "AuthorLocation": "",
                "ReviewDate": date,
                "RatingValue": safe_float(rating_text),
                "RatingText": rating_text,
                "HelpfulYes": "",
                "HelpfulNo": "",
                "Recommended": None,
                "Variant": "",
                "ProductId": "",
                "SourceClient": "dom",
                "SyndicationSource": "",
                "Badges": "",
                "PhotoCount": 0,
                "VideoCount": 0,
                "SourceMethod": "dom",
                "SourceUrl": product_url,
                "ProductUrl": product_url,
                "VerifiedPurchase": None,
            }
        )
    return parsed_rows


def append_bvstate(url: str, page_num: int) -> str:
    parsed = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "bvstate"]
    pairs.append(("bvstate", f"pg:{page_num}/ct:r"))
    return urlunparse(parsed._replace(query=urlencode(pairs, doseq=True)))


def replace_query_params(url: str, updates: Dict[str, Optional[str]]) -> str:
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered: List[Tuple[str, str]] = []
    update_keys = {k.lower() for k in updates.keys()}
    for key, value in pairs:
        if key.lower() in update_keys:
            continue
        if key.lower() == "callback":
            continue
        filtered.append((key, value))
    for key, value in updates.items():
        if value is None:
            continue
        filtered.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))


def choose_best_api_url(request_urls: Sequence[str]) -> Optional[str]:
    ranked = []
    for url in request_urls:
        lowered = url.lower()
        score = 0
        if "reviews.json" in lowered:
            score += 100
        if "bazaarvoice" in lowered:
            score += 50
        if "filter=productid" in lowered:
            score += 25
        if "sort=" in lowered:
            score += 10
        if "statistics" in lowered:
            score -= 40
        if score > 0:
            ranked.append((score, url))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


def fetch_reviews_from_bazaarvoice_api(
    api_url: str,
    product_url: str,
    target_reviews: int,
    sort_key: str,
    progress_cb: Callable[[str, str, float, int], None],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        }
    )

    limit = min(100, target_reviews)
    preferred_sort = SORT_TO_BAZAARVOICE.get(sort_key)
    urls_to_try = [replace_query_params(api_url, {"Limit": str(limit), "Offset": "0"})]
    if preferred_sort:
        urls_to_try.insert(0, replace_query_params(api_url, {"Limit": str(limit), "Offset": "0", "Sort": preferred_sort}))

    raw_payloads: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    total_results: Optional[int] = None
    used_url = None

    for initial_url in urls_to_try:
        try:
            response = session.get(initial_url, timeout=30)
            parsed = parse_json_like(response.text)
            if parsed is None:
                continue
            payload_candidates = walk_review_payloads(parsed)
            if not payload_candidates:
                continue
            raw_payloads.append({"source": "api", "url": initial_url, "status_code": response.status_code, "data": parsed})
            trail, payload = payload_candidates[0]
            total_results = int(float(payload.get("TotalResults"))) if payload.get("TotalResults") not in (None, "") else None
            for item in payload.get("Results", []):
                normalized = standardize_bazaarvoice_item(item, initial_url, "bazaarvoice_api", product_url)
                if normalized:
                    rows.append(normalized)
            used_url = initial_url
            break
        except Exception:
            continue

    if not rows:
        return [], raw_payloads, {"api_url": None, "total_results": None, "pages_fetched": 0}

    pages_fetched = 1
    progress_cb(
        "Fast review feed",
        f"Fetched page 1 from the SharkNinja review feed. {len(rows)} review(s) collected.",
        0.58,
        len(dedupe_reviews(rows)),
    )

    if total_results is None:
        total_results = len(rows)

    while len(dedupe_reviews(rows)) < min(target_reviews, total_results):
        offset = pages_fetched * limit
        next_url = replace_query_params(used_url or api_url, {"Limit": str(limit), "Offset": str(offset)})
        try:
            response = session.get(next_url, timeout=30)
            parsed = parse_json_like(response.text)
            if parsed is None:
                break
            raw_payloads.append({"source": "api", "url": next_url, "status_code": response.status_code, "data": parsed})
            payload_candidates = walk_review_payloads(parsed)
            if not payload_candidates:
                break
            _, payload = payload_candidates[0]
            batch_rows = []
            for item in payload.get("Results", []):
                normalized = standardize_bazaarvoice_item(item, next_url, "bazaarvoice_api", product_url)
                if normalized:
                    batch_rows.append(normalized)
            if not batch_rows:
                break
            rows.extend(batch_rows)
            pages_fetched += 1
            page_total = max(1, math.ceil(min(target_reviews, total_results) / limit))
            progress = min(0.83, 0.58 + 0.22 * (pages_fetched / page_total))
            progress_cb(
                "Fast review feed",
                f"Fetched page {pages_fetched} from the SharkNinja review feed. {len(dedupe_reviews(rows))} review(s) collected.",
                progress,
                len(dedupe_reviews(rows)),
            )
        except Exception:
            break

    return dedupe_reviews(rows)[:target_reviews], raw_payloads, {
        "api_url": used_url or api_url,
        "total_results": total_results,
        "pages_fetched": pages_fetched,
    }


def scrape_sharkninja_reviews(
    product_url: str,
    target_reviews: int,
    sort_key: str,
    progress_cb: Callable[[str, str, float, int], None],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    normalized_url = normalize_url(product_url)
    session = build_http_session()
    raw_payloads: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {
        "product_url": normalized_url,
        "source_method": "unknown",
        "site_review_count": None,
        "site_average_rating": None,
    }

    progress_cb("Open product page", "Fetching the SharkNinja product page...", 0.08, 0)
    html_text, final_url = fetch_page_html(session, normalized_url, timeout=60)
    normalized_url = normalize_url(final_url)
    meta["product_url"] = normalized_url

    progress_cb("Parse page", "Parsing structured data and rendered reviews from the product page...", 0.20, 0)
    ld_objects = extract_ld_json_objects(html_text)
    ld_meta = extract_product_ld_meta(ld_objects)
    ld_rows = extract_ld_reviews(ld_objects, normalized_url)
    dom_rows = parse_reviews_from_dom_html(html_text, normalized_url)

    page_title_match = re.search(r"<title>(.*?)</title>", html_text or "", flags=re.I | re.S)
    page_title = html.unescape(page_title_match.group(1)).strip() if page_title_match else "SharkNinja product"
    meta["product_title"] = ld_meta.get("product_title") or page_title.split("|")[0].strip() or "SharkNinja product"
    meta["model_code"] = ld_meta.get("sku") or infer_model_code(normalized_url, html_text)
    meta["brand"] = ld_meta.get("brand") or "SharkNinja"
    meta["site_average_rating"] = ld_meta.get("site_average_rating") or meta.get("site_average_rating")
    if ld_meta.get("site_review_count"):
        meta["site_review_count"] = ld_meta.get("site_review_count")

    merged_seed = dedupe_reviews(ld_rows + dom_rows)
    progress_cb(
        "Initial capture",
        f"Initial page capture done. Found {len(merged_seed)} candidate review(s).",
        0.32,
        len(merged_seed),
    )

    candidate_api_urls = extract_candidate_review_api_urls(html_text, normalized_url)
    api_rows: List[Dict[str, Any]] = []
    api_meta: Dict[str, Any] = {}
    api_url = choose_best_api_url(candidate_api_urls)
    if api_url:
        progress_cb(
            "Fast review feed",
            "Review feed detected in page markup. Switching to the faster direct feed path...",
            0.42,
            len(merged_seed),
        )
        api_rows, api_raw, api_meta = fetch_reviews_from_bazaarvoice_api(
            api_url=api_url,
            product_url=normalized_url,
            target_reviews=target_reviews,
            sort_key=sort_key,
            progress_cb=progress_cb,
        )
        raw_payloads.extend(api_raw)
        if api_meta.get("total_results"):
            meta["site_review_count"] = api_meta.get("total_results")

    merged = dedupe_reviews(api_rows + merged_seed)

    if len(merged) < target_reviews:
        progress_cb(
            "HTML fallback",
            "Direct feed was not enough. Walking review pages over HTTP for more coverage...",
            0.72,
            len(merged),
        )
        fallback_rows = fetch_review_pages_via_http(
            session=session,
            base_url=normalized_url,
            target_reviews=target_reviews,
            progress_cb=progress_cb,
            merged_seed=merged,
        )
        merged = dedupe_reviews(api_rows + merged_seed + fallback_rows)

    merged = dedupe_reviews(merged)[:target_reviews]
    if not merged:
        raise RuntimeError(
            "No reviews could be extracted from this SharkNinja page. Try a product page that visibly shows reviews, or run again on a public product URL."
        )

    if api_rows:
        meta["source_method"] = "bazaarvoice_api + html fallback"
    elif dom_rows or ld_rows:
        meta["source_method"] = "html / ld_json"
    else:
        meta["source_method"] = "html fallback"

    meta["captured_api_url"] = api_url
    meta["captured_request_count"] = len(candidate_api_urls)
    return merged, raw_payloads, meta


# ----------------------------
# Snapshot / dataframe / exports
# ----------------------------
def reviews_to_dataframe(rows: Sequence[Dict[str, Any]], meta: Dict[str, Any]) -> pd.DataFrame:
    cleaned = dedupe_reviews(rows)
    for idx, row in enumerate(cleaned, start=1):
        row["ReviewRef"] = f"R{idx:03d}"
        row["ReviewDateParsed"] = parse_date(row.get("ReviewDate"))
        row["SentimentBucket"] = classify_sentiment(row.get("RatingValue"))
        row["ProductTitle"] = meta.get("product_title") or ""
        row["ModelCode"] = meta.get("model_code") or ""
        row["Brand"] = meta.get("brand") or "SharkNinja"
    frame = pd.DataFrame(cleaned)
    preferred_cols = [
        "ReviewRef",
        "RatingValue",
        "SentimentBucket",
        "ReviewDate",
        "Title",
        "ReviewText",
        "Author",
        "AuthorLocation",
        "HelpfulYes",
        "Recommended",
        "VerifiedPurchase",
        "Variant",
        "ProductId",
        "ModelCode",
        "Brand",
        "SourceMethod",
        "SourceUrl",
        "ProductUrl",
    ]
    extra_cols = [col for col in frame.columns if col not in preferred_cols]
    return frame[[col for col in preferred_cols if col in frame.columns] + extra_cols]


def summarize_overview(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    avg_rating_sample = float(df["RatingValue"].dropna().mean()) if "RatingValue" in df.columns and not df["RatingValue"].dropna().empty else None
    positive_share = float((df["RatingValue"].fillna(0) >= 4).mean()) if "RatingValue" in df.columns and not df.empty else None
    negative_share = float((df["RatingValue"].fillna(0) <= 2).mean()) if "RatingValue" in df.columns and not df.empty else None

    date_min = None
    date_max = None
    if "ReviewDateParsed" in df.columns:
        valid_dates = df["ReviewDateParsed"].dropna()
        if not valid_dates.empty:
            date_min = str(valid_dates.min().date())
            date_max = str(valid_dates.max().date())

    star_distribution: Dict[str, int] = {}
    if "RatingValue" in df.columns and not df["RatingValue"].dropna().empty:
        counts = df["RatingValue"].dropna().round(0).astype(int).value_counts().sort_index()
        star_distribution = {f"{int(star)} star": int(count) for star, count in counts.items()}

    note = ""
    site_total = meta.get("site_review_count")
    if site_total and int(site_total) < len(df):
        site_total = len(df)
    if site_total and int(site_total) < meta.get("target_reviews", len(df)):
        note = f"The page/feed exposed {site_total} review(s) during scraping, so the requested cap could not be fully reached."
    elif len(df) < meta.get("target_reviews", len(df)):
        note = "The requested cap was not reached because the page exposed fewer distinct reviews than requested or additional pages were not available."

    overview = {
        "GeneratedAt": now_str(),
        "ProductTitle": meta.get("product_title") or "",
        "Brand": meta.get("brand") or "SharkNinja",
        "ModelCode": meta.get("model_code") or "",
        "SourceUrl": meta.get("product_url") or "",
        "SnapshotDir": meta.get("snapshot_dir") or "",
        "ReviewsCollected": int(len(df)),
        "RequestedReviews": int(meta.get("target_reviews") or len(df)),
        "SiteReviewCount": int(site_total) if site_total not in (None, "") else None,
        "AverageRatingOnSite": round(float(meta.get("site_average_rating")), 2) if meta.get("site_average_rating") not in (None, "") else None,
        "AverageRatingInSample": round(avg_rating_sample, 2) if avg_rating_sample is not None else None,
        "PositiveShare": round(positive_share * 100, 1) if positive_share is not None else None,
        "NegativeShare": round(negative_share * 100, 1) if negative_share is not None else None,
        "ReviewDateMin": date_min,
        "ReviewDateMax": date_max,
        "SourceMethod": meta.get("source_method") or "",
        "CapturedApiUrl": meta.get("captured_api_url") or "",
        "StarDistributionJSON": json.dumps(star_distribution, ensure_ascii=False),
        "RetrievalNote": note,
    }
    return pd.DataFrame([overview])


def report_to_frames(report: ProductIntelReport) -> Dict[str, pd.DataFrame]:
    executive = pd.DataFrame(
        [
            {
                "ExecutiveSummary": report.executive_summary,
                "ConfidenceNote": report.confidence_note,
                "ExecutiveTakeaways": " | ".join(report.executive_takeaways),
                "JobsToBeDone": " | ".join(report.jobs_to_be_done),
            }
        ]
    )

    theme = lambda items: pd.DataFrame(
        [
            {
                "Theme": item.theme,
                "Summary": item.summary,
                "SupportingReviews": ", ".join(item.supporting_reviews),
            }
            for item in items
        ]
    )

    risks = pd.DataFrame(
        [
            {
                "Issue": item.issue,
                "Severity": item.severity,
                "WhyItMatters": item.why_it_matters,
                "SupportingReviews": ", ".join(item.supporting_reviews),
                "SuggestedOwner": item.suggested_owner,
            }
            for item in report.quality_risks
        ]
    )

    requests_df = pd.DataFrame(
        [
            {
                "Request": item.request,
                "Rationale": item.rationale,
                "SupportingReviews": ", ".join(item.supporting_reviews),
                "SuggestedOwner": item.suggested_owner,
            }
            for item in report.feature_requests
        ]
    )

    actions = pd.DataFrame(
        [{"Audience": "Product Development", "Action": x} for x in report.actions_for_product_development]
        + [{"Audience": "Quality Engineer", "Action": x} for x in report.actions_for_quality_engineering]
        + [{"Audience": "Consumer Insights", "Action": x} for x in report.actions_for_consumer_insights]
    )

    return {
        "AI_Executive": executive,
        "AI_Themes": theme(report.top_themes),
        "AI_Delighters": theme(report.delighters),
        "AI_Detractors": theme(report.detractors),
        "AI_Quality_Risks": risks,
        "AI_Feature_Requests": requests_df,
        "AI_Actions": actions,
    }


def build_excel_bytes(reviews_df: pd.DataFrame, overview_df: pd.DataFrame, report: Optional[ProductIntelReport]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        overview_df.to_excel(writer, sheet_name="Overview", index=False)
        reviews_df.to_excel(writer, sheet_name="Reviews", index=False)
        if report:
            for sheet_name, frame in report_to_frames(report).items():
                frame.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
    buffer.seek(0)
    return buffer.read()


def snapshot_metadata(meta: Dict[str, Any], overview_df: pd.DataFrame) -> Dict[str, Any]:
    overview = overview_df.iloc[0].to_dict() if overview_df is not None and not overview_df.empty else {}
    output = dict(meta)
    output["overview"] = overview
    return output


def allocate_snapshot_dir(meta: Dict[str, Any]) -> Path:
    LOCAL_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    folder_name = slugify(f"{meta.get('product_title') or 'product'}-{meta.get('model_code') or 'snapshot'}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = LOCAL_STORE_ROOT / folder_name / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir


def persist_snapshot(
    reviews_df: pd.DataFrame,
    overview_df: pd.DataFrame,
    raw_payloads: List[Dict[str, Any]],
    meta: Dict[str, Any],
    report: Optional[ProductIntelReport] = None,
    snapshot_dir: Optional[str | Path] = None,
) -> Path:
    if snapshot_dir:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    else:
        snapshot_dir = allocate_snapshot_dir(meta)

    reviews_df.to_csv(snapshot_dir / "reviews.csv", index=False, encoding="utf-8-sig")
    reviews_df.to_json(snapshot_dir / "reviews.json", orient="records", force_ascii=False, indent=2)
    overview_df.to_json(snapshot_dir / "overview.json", orient="records", force_ascii=False, indent=2)
    with open(snapshot_dir / "raw_payloads.json", "w", encoding="utf-8") as f:
        json.dump(raw_payloads, f, ensure_ascii=False, indent=2)
    with open(snapshot_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(snapshot_metadata(meta, overview_df), f, ensure_ascii=False, indent=2)
    if report:
        with open(snapshot_dir / "ai_report.json", "w", encoding="utf-8") as f:
            json.dump(compatible_model_dump(report), f, ensure_ascii=False, indent=2)
    elif (snapshot_dir / "ai_report.json").exists():
        # Keep previous AI work unless a new report is explicitly written.
        pass

    workbook_bytes = build_excel_bytes(reviews_df, overview_df, report)
    with open(snapshot_dir / "review_intelligence.xlsx", "wb") as f:
        f.write(workbook_bytes)
    return snapshot_dir


def list_local_snapshots() -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    if not LOCAL_STORE_ROOT.exists():
        return pd.DataFrame()
    for metadata_file in sorted(LOCAL_STORE_ROOT.glob("**/metadata.json"), reverse=True):
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        overview = payload.get("overview") or {}
        records.append(
            {
                "SnapshotDir": str(metadata_file.parent),
                "ProductTitle": payload.get("product_title") or overview.get("ProductTitle") or "",
                "ModelCode": payload.get("model_code") or overview.get("ModelCode") or "",
                "ReviewsCollected": overview.get("ReviewsCollected"),
                "SiteReviewCount": overview.get("SiteReviewCount"),
                "GeneratedAt": overview.get("GeneratedAt") or metadata_file.parent.name,
                "SourceUrl": payload.get("product_url") or overview.get("SourceUrl") or "",
            }
        )
    return pd.DataFrame(records)


def load_snapshot(snapshot_dir: str) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[ProductIntelReport], Dict[str, Any], List[Dict[str, Any]]]:
    base = Path(snapshot_dir)
    reviews_df = pd.read_csv(base / "reviews.csv")
    overview_payload = json.loads((base / "overview.json").read_text(encoding="utf-8"))
    overview_df = pd.DataFrame(overview_payload)
    metadata = json.loads((base / "metadata.json").read_text(encoding="utf-8"))
    raw_payloads = json.loads((base / "raw_payloads.json").read_text(encoding="utf-8"))

    report = None
    report_path = base / "ai_report.json"
    if report_path.exists():
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = ProductIntelReport(**report_payload)
    return reviews_df, overview_df, report, metadata, raw_payloads


# ----------------------------
# AI helpers
# ----------------------------
def build_review_context(df: pd.DataFrame, overview_df: pd.DataFrame, max_chars_per_review: int = 650) -> str:
    overview = overview_df.iloc[0].to_dict()
    header_lines = [
        f"Product title: {overview.get('ProductTitle')}",
        f"Brand: {overview.get('Brand')}",
        f"Model code: {overview.get('ModelCode')}",
        f"Source URL: {overview.get('SourceUrl')}",
        f"Reviews analyzed: {overview.get('ReviewsCollected')}",
        f"Site review count (if exposed): {overview.get('SiteReviewCount')}",
        f"Average rating on site: {overview.get('AverageRatingOnSite')}",
        f"Average rating in sample: {overview.get('AverageRatingInSample')}",
        f"Positive share %: {overview.get('PositiveShare')}",
        f"Negative share %: {overview.get('NegativeShare')}",
        f"Review date window: {overview.get('ReviewDateMin')} to {overview.get('ReviewDateMax')}",
        f"Retrieval note: {overview.get('RetrievalNote')}",
        f"Star distribution JSON: {overview.get('StarDistributionJSON')}",
    ]

    review_lines = []
    for _, row in df.iterrows():
        body = compact_text(row.get("ReviewText"), limit=max_chars_per_review)
        review_lines.append(
            "\n".join(
                [
                    f"[{row.get('ReviewRef')}] {row.get('RatingValue')} stars | date={row.get('ReviewDate')} | author={row.get('Author')}",
                    f"Title: {row.get('Title')}",
                    f"Review: {body}",
                ]
            )
        )
    return "\n\n".join(header_lines + ["", "Reviews:"] + review_lines)


def reasoning_effort(model_name: str, task: str) -> str:
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
    context = build_review_context(reviews_df, overview_df)
    system_prompt = (
        "You are a senior product intelligence analyst helping Product Development, Quality Engineers, "
        "and Consumer Insights teams understand SharkNinja direct-to-consumer product reviews. "
        "Use only the supplied review evidence. Never invent facts, counts, review IDs, or consumer claims. "
        "Cite evidence only using the provided ReviewRef values like R001. Distinguish true delighters from detractors. "
        "Treat durability, reliability, breakage, defect patterns, packaging issues, cleaning difficulty, performance inconsistency, "
        "and safety-adjacent complaints as quality risks. Keep the executive summary sharp and decision-ready. "
        "If evidence is thin, mixed, or possibly biased by sample size, say so in confidence_note."
    )
    user_prompt = (
        "Analyze this SharkNinja review dataset and produce a structured product intelligence report. "
        "Make it useful for Product Development, Quality Engineer, and Consumer Insights stakeholders.\n\n"
        f"{context}"
    )

    response = client.responses.parse(
        model=model_name,
        reasoning={"effort": reasoning_effort(model_name, "report")},
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text_format=ProductIntelReport,
    )
    if not response.output_parsed:
        raise RuntimeError("The AI report came back empty.")
    return response.output_parsed


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
    context = build_review_context(reviews_df, overview_df, max_chars_per_review=500)
    report_json = compatible_model_dump_json(report) if report else "{}"

    system_prompt = (
        "You are Product Intelligence Copilot for SharkNinja review analysis. "
        "Answer only from the supplied review dataset and the structured report. "
        "Cite review refs in square brackets like [R014] whenever you make a factual claim. "
        "If the review set does not support a conclusion, say that directly. Tailor the answer to the stakeholder lens."
    )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": f"Stakeholder lens: {stakeholder_lens}\n\nStructured report JSON:\n{report_json}\n\nReview dataset:\n{context}",
        },
    ]
    messages.extend(chat_history[-10:])
    messages.append({"role": "user", "content": user_message})

    response = client.responses.create(
        model=model_name,
        reasoning={"effort": reasoning_effort(model_name, "chat")},
        text={"verbosity": "medium"},
        input=messages,
    )
    return (response.output_text or "").strip()


# ----------------------------
# Evidence rendering helpers
# ----------------------------
def build_evidence_map(df: pd.DataFrame) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        ref = str(row.get("ReviewRef") or "").strip()
        if not ref:
            continue
        title = html.escape(str(row.get("Title") or "No title"))
        meta = " · ".join(
            [
                f"{row.get('RatingValue')}★" if row.get("RatingValue") not in (None, "") else "Rating n/a",
                str(row.get("ReviewDate") or "Date n/a"),
                compact_text(row.get("Author") or "Anonymous", limit=36),
            ]
        )
        body = html.escape(compact_text(row.get("ReviewText") or "", limit=360))
        mapping[ref] = (
            f"<div class='sn-tooltip-title'>{title}</div>"
            f"<div class='sn-tooltip-meta'>{html.escape(meta)}</div>"
            f"<div>{body}</div>"
        )
    return mapping


def ref_chip_html(ref: str, evidence_map: Dict[str, str]) -> str:
    escaped_ref = html.escape(ref)
    tooltip = evidence_map.get(ref)
    if not tooltip:
        return f"<span class='sn-chip'>{escaped_ref}</span>"
    return f"<span class='sn-ref'>{escaped_ref}<span class='sn-tooltip'>{tooltip}</span></span>"


def refs_to_html(refs: Sequence[str], evidence_map: Dict[str, str]) -> str:
    return "".join(ref_chip_html(ref, evidence_map) for ref in refs)


def annotate_text_with_evidence(text: str, evidence_map: Dict[str, str]) -> str:
    safe = html.escape(text or "")
    safe = safe.replace("\n", "<br>")
    return REF_RE.sub(lambda match: ref_chip_html(match.group(0), evidence_map), safe)


def render_rich_text(text: str, evidence_map: Dict[str, str]) -> None:
    st.markdown(f"<div>{annotate_text_with_evidence(text, evidence_map)}</div>", unsafe_allow_html=True)


def render_theme_cards(items: List[ThemeEvidence], evidence_map: Dict[str, str], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        st.markdown(
            f"""
            <div class="sn-card">
                <h5>{html.escape(item.theme)}</h5>
                <div>{annotate_text_with_evidence(item.summary, evidence_map)}</div>
                <div class="sn-chip-row">{refs_to_html(item.supporting_reviews, evidence_map)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_quality_cards(items: List[QualityRisk], evidence_map: Dict[str, str], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        st.markdown(
            f"""
            <div class="sn-card">
                <h5>{html.escape(item.issue)}</h5>
                <div class="sn-muted">Severity: {html.escape(item.severity)} · Owner: {html.escape(item.suggested_owner)}</div>
                <div style="margin-top:0.35rem;">{annotate_text_with_evidence(item.why_it_matters, evidence_map)}</div>
                <div class="sn-chip-row">{refs_to_html(item.supporting_reviews, evidence_map)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_request_cards(items: List[FeatureRequest], evidence_map: Dict[str, str], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        st.markdown(
            f"""
            <div class="sn-card">
                <h5>{html.escape(item.request)}</h5>
                <div class="sn-muted">Owner: {html.escape(item.suggested_owner)}</div>
                <div style="margin-top:0.35rem;">{annotate_text_with_evidence(item.rationale, evidence_map)}</div>
                <div class="sn-chip-row">{refs_to_html(item.supporting_reviews, evidence_map)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_action_list(title: str, actions: Sequence[str], evidence_map: Dict[str, str], empty_message: str) -> None:
    st.markdown(f"### {title}")
    if not actions:
        st.info(empty_message)
        return
    html_items = "".join(f"<li>{annotate_text_with_evidence(action, evidence_map)}</li>" for action in actions)
    st.markdown(f"<div class='sn-card'><ul class='sn-list'>{html_items}</ul></div>", unsafe_allow_html=True)


# ----------------------------
# Progress overlay
# ----------------------------
def render_progress_overlay(event_log: List[ProgressEvent], current_stage: str, current_detail: str) -> None:
    if not event_log:
        return
    latest = event_log[-1]
    start_ts = event_log[0].ts
    elapsed = latest.ts - start_ts
    progress = max(0.01, min(1.0, latest.progress))
    eta = elapsed * (1 - progress) / progress if progress > 0.04 else None

    activity_html = ""
    for event in reversed(event_log[-6:]):
        activity_html += (
            f"<div class='sn-activity-item'>"
            f"<div style='font-weight:600'>{html.escape(event.stage)}</div>"
            f"<div style='color:rgba(226,232,240,0.82); font-size:0.88rem'>{html.escape(event.detail)}</div>"
            f"</div>"
        )

    st.markdown(
        f"""
        <div class="sn-overlay">
            <h4>Scraping SharkNinja reviews</h4>
            <div style="color:rgba(226,232,240,0.82); font-size:0.93rem">{html.escape(current_stage)} · {html.escape(current_detail)}</div>
            <div class="sn-overlay-meta">
                <div class="sn-overlay-metric">
                    <div class="sn-overlay-metric-label">Collected</div>
                    <div class="sn-overlay-metric-value">{latest.collected}</div>
                </div>
                <div class="sn-overlay-metric">
                    <div class="sn-overlay-metric-label">Elapsed</div>
                    <div class="sn-overlay-metric-value">{html.escape(format_seconds(elapsed))}</div>
                </div>
                <div class="sn-overlay-metric">
                    <div class="sn-overlay-metric-label">ETA</div>
                    <div class="sn-overlay-metric-value">{html.escape(format_seconds(eta))}</div>
                </div>
            </div>
            <div class="sn-progress-track"><div class="sn-progress-fill" style="width:{progress * 100:.1f}%"></div></div>
            <div class="sn-activity">{activity_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------
# Main app
# ----------------------------
def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_state()
    inject_css()

    st.markdown(
        f"""
        <div class="sn-hero">
            <h2>{APP_TITLE}</h2>
            <div class="sn-muted">{APP_TAGLINE}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("OpenAI")
        openai_secret = get_secret("OPENAI_API_KEY")
        use_secrets = st.checkbox("Use Streamlit secrets when available", value=True)
        openai_api_key = st.text_input(
            "OpenAI API key",
            type="password",
            value="" if not (use_secrets and openai_secret) else openai_secret,
        ).strip()

        st.divider()
        st.subheader("Scrape settings")
        target_reviews = st.slider("Reviews to collect", min_value=10, max_value=MAX_REVIEWS_CAP, value=100, step=10)
        sort_label = st.selectbox("Review sort", options=list(SORT_OPTIONS.keys()), index=0)
        accept_sharkclean = st.toggle("Allow SharkClean URLs that redirect to SharkNinja", value=True)

        st.divider()
        st.subheader("AI settings")
        report_model = st.selectbox("AI report model", options=AI_REPORT_MODELS, index=0)
        chat_model = st.selectbox("Chatbot model", options=CHAT_MODELS, index=0)
        default_lens = st.selectbox("Default stakeholder lens", options=LENS_OPTIONS, index=0)

        with st.expander("Install note", expanded=False):
            st.code(
                "pip install -r requirements.txt",
                language="bash",
            )

    product_url = st.text_input(
        "SharkNinja product URL",
        value=st.session_state.get("last_product_url", ""),
        placeholder="https://www.sharkninja.com/shark-navigator-lift-away-adv-with-self-cleaning-brushroll/LA362.html",
    )

    normalized_url = normalize_url(product_url)
    domain_hint = urlparse(normalized_url).netloc if normalized_url else ""
    status_cols = st.columns([1.2, 1, 1], vertical_alignment="center")
    status_cols[0].caption(domain_hint or "Paste a SharkNinja product page URL to begin")
    status_cols[1].caption(f"Target reviews: {target_reviews}")
    status_cols[2].caption(f"Sort: {sort_label}")

    action_cols = st.columns([1.25, 1.05, 1.05], vertical_alignment="center")
    scrape_clicked = action_cols[0].button("Scrape SharkNinja reviews", type="primary", use_container_width=True)
    report_clicked = action_cols[1].button(
        "Generate AI report",
        use_container_width=True,
        disabled=st.session_state.get("reviews_df") is None,
    )
    clear_clicked = action_cols[2].button("Clear session", use_container_width=True)

    if clear_clicked:
        for key in ["reviews_df", "overview_df", "report", "product_meta", "raw_payloads", "chat_messages", "last_product_url", "snapshot_dir"]:
            st.session_state[key] = None if key not in {"chat_messages", "last_product_url", "snapshot_dir"} else ([] if key == "chat_messages" else "")
        st.rerun()

    if scrape_clicked:
        if not normalized_url:
            st.error("Paste a SharkNinja product URL first.")
        elif not is_sharkninja_url(normalized_url):
            st.error("Use a SharkNinja/Shark product page URL. Amazon has been removed from this build.")
        elif not accept_sharkclean and "sharkclean.com" in domain_hint:
            st.error("This build is focused on SharkNinja-hosted pages. Re-enable SharkClean redirects or use the final SharkNinja URL.")
        else:
            overlay_placeholder = st.empty()
            log: List[ProgressEvent] = []

            def progress_cb(stage: str, detail: str, progress: float, collected: int) -> None:
                event = ProgressEvent(ts=time.time(), stage=stage, detail=detail, progress=progress, collected=collected)
                log.append(event)
                with overlay_placeholder.container():
                    render_progress_overlay(log, stage, detail)

            try:
                rows, raw_payloads, meta = scrape_sharkninja_reviews(
                    product_url=normalized_url,
                    target_reviews=target_reviews,
                    sort_key=SORT_OPTIONS[sort_label],
                    progress_cb=progress_cb,
                )
                meta["target_reviews"] = target_reviews
                reviews_df = reviews_to_dataframe(rows, meta)
                snapshot_dir = allocate_snapshot_dir(meta)
                meta["snapshot_dir"] = str(snapshot_dir)
                overview_df = summarize_overview(reviews_df, meta)
                persist_snapshot(reviews_df, overview_df, raw_payloads, meta, snapshot_dir=snapshot_dir)

                st.session_state["reviews_df"] = reviews_df
                st.session_state["overview_df"] = overview_df
                st.session_state["report"] = None
                st.session_state["product_meta"] = meta
                st.session_state["raw_payloads"] = raw_payloads
                st.session_state["chat_messages"] = []
                st.session_state["last_product_url"] = normalized_url
                st.session_state["snapshot_dir"] = str(snapshot_dir)
                overlay_placeholder.empty()
                st.success(f"Collected {len(reviews_df)} SharkNinja review(s) and saved a local snapshot.")
            except Exception as exc:
                overlay_placeholder.empty()
                st.error(str(exc))

    if report_clicked:
        if st.session_state.get("reviews_df") is None or st.session_state.get("overview_df") is None:
            st.error("Scrape reviews first.")
        elif not openai_api_key:
            st.error("Add your OpenAI API key in the sidebar.")
        else:
            with st.spinner("Generating SharkNinja product intelligence report..."):
                try:
                    report = generate_product_intel_report(
                        openai_api_key=openai_api_key,
                        model_name=report_model,
                        reviews_df=st.session_state["reviews_df"],
                        overview_df=st.session_state["overview_df"],
                    )
                    st.session_state["report"] = report
                    if st.session_state.get("snapshot_dir"):
                        persist_snapshot(
                            st.session_state["reviews_df"],
                            st.session_state["overview_df"],
                            st.session_state.get("raw_payloads") or [],
                            st.session_state.get("product_meta") or {},
                            report,
                            snapshot_dir=st.session_state.get("snapshot_dir") or None,
                        )
                    st.success("AI report ready.")
                except Exception as exc:
                    st.error(str(exc))

    reviews_df: Optional[pd.DataFrame] = st.session_state.get("reviews_df")
    overview_df: Optional[pd.DataFrame] = st.session_state.get("overview_df")
    report: Optional[ProductIntelReport] = st.session_state.get("report")
    product_meta: Dict[str, Any] = st.session_state.get("product_meta") or {}
    evidence_map = build_evidence_map(reviews_df) if reviews_df is not None else {}

    nav = st.radio(
        "",
        options=["Studio", "AI Report", "Chatbot", "Downloads", "Local Library", "Help"],
        horizontal=True,
        key="nav",
        label_visibility="collapsed",
    )

    if reviews_df is not None and overview_df is not None:
        overview = overview_df.iloc[0].to_dict()
        st.markdown(
            f"""
            <div class="sn-kpi-grid">
                <div class="sn-kpi"><div class="sn-kpi-label">Reviews collected</div><div class="sn-kpi-value">{int(overview.get('ReviewsCollected') or 0)}</div></div>
                <div class="sn-kpi"><div class="sn-kpi-label">Avg rating in sample</div><div class="sn-kpi-value">{overview.get('AverageRatingInSample') or '—'}</div></div>
                <div class="sn-kpi"><div class="sn-kpi-label">Site review count</div><div class="sn-kpi-value">{overview.get('SiteReviewCount') or '—'}</div></div>
                <div class="sn-kpi"><div class="sn-kpi-label">Snapshot</div><div class="sn-kpi-value">{html.escape(Path(st.session_state.get('snapshot_dir') or '').name or '—')}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.write("")

    if nav == "Studio":
        if reviews_df is None or overview_df is None:
            st.info("Scrape a SharkNinja product page to populate the studio.")
        else:
            overview = overview_df.iloc[0].to_dict()
            c1, c2 = st.columns([1.1, 1], vertical_alignment="top")
            with c1:
                st.markdown(
                    f"""
                    <div class="sn-card">
                        <h4>{html.escape(product_meta.get('product_title') or 'SharkNinja product')}</h4>
                        <div class="sn-muted">{html.escape(product_meta.get('product_url') or '')}</div>
                        <div style="margin-top:0.55rem; line-height:1.6;">
                            <strong>Model:</strong> {html.escape(product_meta.get('model_code') or '—')}<br>
                            <strong>Brand:</strong> {html.escape(product_meta.get('brand') or 'SharkNinja')}<br>
                            <strong>Source method:</strong> {html.escape(product_meta.get('source_method') or '—')}<br>
                            <strong>Retrieval note:</strong> {html.escape(overview.get('RetrievalNote') or '—')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with c2:
                star_json = overview.get("StarDistributionJSON") or "{}"
                try:
                    star_data = json.loads(star_json)
                except Exception:
                    star_data = {}
                if star_data:
                    star_df = pd.DataFrame({"Star": list(star_data.keys()), "Reviews": list(star_data.values())})
                    st.bar_chart(star_df.set_index("Star"))
                else:
                    st.info("No star distribution available.")

            preview_cols = [
                "ReviewRef",
                "RatingValue",
                "SentimentBucket",
                "ReviewDate",
                "Title",
                "ReviewText",
                "Author",
                "SourceMethod",
            ]
            st.markdown("### Review table")
            st.dataframe(reviews_df[[col for col in preview_cols if col in reviews_df.columns]], use_container_width=True, hide_index=True)

    elif nav == "AI Report":
        if reviews_df is None:
            st.info("Scrape SharkNinja reviews first.")
        elif not openai_api_key:
            st.info("Add your OpenAI API key in the sidebar to generate the AI report.")
        elif report is None:
            st.info("Generate the AI report to unlock the intelligence view.")
        else:
            st.markdown("### Executive summary")
            st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
            render_rich_text(report.executive_summary, evidence_map)
            if report.executive_takeaways:
                takeaways_html = "".join(f"<li>{annotate_text_with_evidence(item, evidence_map)}</li>" for item in report.executive_takeaways)
                st.markdown(f"<ul class='sn-list'>{takeaways_html}</ul>", unsafe_allow_html=True)
            st.markdown(f"<div class='sn-muted'>{html.escape(report.confidence_note)}</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

            col1, col2 = st.columns(2, vertical_alignment="top")
            with col1:
                st.markdown("### Delighters")
                render_theme_cards(report.delighters, evidence_map, "No strong delighters identified.")
                st.markdown("### Top themes")
                render_theme_cards(report.top_themes, evidence_map, "No top themes identified.")
            with col2:
                st.markdown("### Detractors")
                render_theme_cards(report.detractors, evidence_map, "No clear detractors identified.")
                st.markdown("### Quality risks")
                render_quality_cards(report.quality_risks, evidence_map, "No clear quality risks identified.")

            st.markdown("### Feature requests")
            render_request_cards(report.feature_requests, evidence_map, "No clear feature requests identified.")

            action_cols = st.columns(3, vertical_alignment="top")
            with action_cols[0]:
                render_action_list("Actions for Product Development", report.actions_for_product_development, evidence_map, "No product-development actions generated.")
            with action_cols[1]:
                render_action_list("Actions for Quality Engineer", report.actions_for_quality_engineering, evidence_map, "No quality actions generated.")
            with action_cols[2]:
                render_action_list("Actions for Consumer Insights", report.actions_for_consumer_insights, evidence_map, "No consumer-insights actions generated.")

    elif nav == "Chatbot":
        if reviews_df is None or overview_df is None:
            st.info("Scrape SharkNinja reviews first.")
        elif not openai_api_key:
            st.info("Add your OpenAI API key in the sidebar to use the chatbot.")
        else:
            st.caption(f"Grounded chatbot lens: {default_lens}")
            for message in st.session_state.get("chat_messages", []):
                with st.chat_message(message["role"]):
                    if message["role"] == "assistant":
                        st.markdown(annotate_text_with_evidence(message["content"], evidence_map), unsafe_allow_html=True)
                    else:
                        st.markdown(html.escape(message["content"]), unsafe_allow_html=True)

            prompt = st.chat_input("Ask about delighters, breakage patterns, quality risks, consumer language, or feature gaps...")
            if prompt:
                st.session_state["chat_messages"].append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(html.escape(prompt), unsafe_allow_html=True)
                with st.chat_message("assistant"):
                    with st.spinner("Analyzing the review evidence..."):
                        try:
                            answer = ask_product_chatbot(
                                openai_api_key=openai_api_key,
                                model_name=chat_model,
                                reviews_df=reviews_df,
                                overview_df=overview_df,
                                report=report,
                                chat_history=st.session_state["chat_messages"][:-1],
                                user_message=prompt,
                                stakeholder_lens=default_lens,
                            )
                            st.markdown(annotate_text_with_evidence(answer, evidence_map), unsafe_allow_html=True)
                            st.session_state["chat_messages"].append({"role": "assistant", "content": answer})
                        except Exception as exc:
                            st.error(str(exc))

    elif nav == "Downloads":
        if reviews_df is None or overview_df is None:
            st.info("Nothing to download yet.")
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_bytes = build_excel_bytes(reviews_df, overview_df, report)
            csv_bytes = reviews_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            json_bytes = reviews_df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")

            d1, d2, d3 = st.columns(3)
            with d1:
                st.download_button(
                    "Download Excel workbook",
                    data=excel_bytes,
                    file_name=f"sharkninja_review_intelligence_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with d2:
                st.download_button(
                    "Download review CSV",
                    data=csv_bytes,
                    file_name=f"sharkninja_reviews_{timestamp}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with d3:
                st.download_button(
                    "Download review JSON",
                    data=json_bytes,
                    file_name=f"sharkninja_reviews_{timestamp}.json",
                    mime="application/json",
                    use_container_width=True,
                )

            st.markdown(
                f"""
                <div class="sn-card">
                    <h4>Local snapshot</h4>
                    <div class="sn-muted">Every scrape is saved locally so the reviews remain available even after you close the app.</div>
                    <div style="margin-top:0.5rem;"><strong>Snapshot path:</strong> {html.escape(st.session_state.get('snapshot_dir') or '—')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    elif nav == "Local Library":
        snapshots_df = list_local_snapshots()
        if snapshots_df.empty:
            st.info("No local SharkNinja snapshots have been saved yet.")
        else:
            st.dataframe(snapshots_df, use_container_width=True, hide_index=True)
            selected_dir = st.selectbox("Load a saved snapshot", options=snapshots_df["SnapshotDir"].tolist())
            if st.button("Load selected snapshot", use_container_width=False):
                try:
                    reviews_df, overview_df, report, metadata, raw_payloads = load_snapshot(selected_dir)
                    st.session_state["reviews_df"] = reviews_df
                    st.session_state["overview_df"] = overview_df
                    st.session_state["report"] = report
                    st.session_state["product_meta"] = metadata
                    st.session_state["raw_payloads"] = raw_payloads
                    st.session_state["snapshot_dir"] = selected_dir
                    st.session_state["last_product_url"] = metadata.get("product_url") or ""
                    st.success("Snapshot loaded into the studio.")
                except Exception as exc:
                    st.error(f"Failed to load snapshot: {exc}")

    elif nav == "Help":
        st.markdown("### What changed")
        st.markdown(
            """
            - Amazon and Apify are fully removed.
            - The scraper now targets SharkNinja product pages only.
            - Reviews are collected locally through a feed-first SharkNinja scraper with HTML fallback.
            - Each scrape is stored locally as a snapshot with CSV, JSON, and Excel artifacts.
            - The AI layer is tuned for Product Development, Quality Engineer, and Consumer Insights teams.
            - Review references like R001 show hover evidence in the AI report and chatbot.
            """
        )
        st.markdown("### Scrape strategy")
        st.markdown(
            """
            1. Open the public SharkNinja product page.
            2. Capture review traffic and page content.
            3. If a faster review feed is exposed, pull reviews from that feed directly.
            4. If needed, fall back to walking SharkNinja review pages directly.
            5. Save everything locally.
            """
        )
        st.markdown("### Notes")
        st.markdown(
            """
            - If a product page only exposes 8 or 12 reviews publicly, the app will tell you that the visible feed was smaller than your requested cap.
            - No browser install is required in the default setup.
            - Use public product pages only and make sure you comply with the site's terms and internal data-governance rules.
            """
        )


if __name__ == "__main__":
    main()
