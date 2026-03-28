from __future__ import annotations

import io
import json
import math
import os
import re
import sqlite3
import tempfile
import textwrap
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots
from bs4 import BeautifulSoup
from openpyxl.utils import get_column_letter

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - keeps the app usable without the AI package installed
    OpenAI = None


APP_TITLE = "SharkNinja Review Analyst"
DEFAULT_PASSKEY = "caC6wVBHos09eVeBkLIniLUTzrNMMH2XMADEhpHe1ewUw"
DEFAULT_DISPLAYCODE = "15973_3_0-en_us"
DEFAULT_API_VERSION = "5.5"
DEFAULT_PAGE_SIZE = 100
DEFAULT_SORT = "SubmissionTime:desc"
DEFAULT_CONTENT_LOCALES = (
    "en_US,ar*,zh*,hr*,cs*,da*,nl*,en*,et*,fi*,fr*,de*,el*,he*,hu*,"
    "id*,it*,ja*,ko*,lv*,lt*,ms*,no*,pl*,pt*,ro*,sk*,sl*,es*,sv*,th*,"
    "tr*,vi*,en_AU,en_CA,en_GB"
)
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "low"
MODEL_OPTIONS = ["gpt-5.4-mini", "gpt-5.4", "gpt-5-mini"]
REASONING_OPTIONS = ["none", "minimal", "low", "medium", "high", "xhigh"]
MODEL_REASONING_SUPPORT = {
    "gpt-5.4-mini": ["none", "low", "medium", "high", "xhigh"],
    "gpt-5.4": ["none", "minimal", "low", "medium", "high", "xhigh"],
    "gpt-5-mini": ["none", "low", "medium", "high"],
}
BAZAARVOICE_ENDPOINT = "https://api.bazaarvoice.com/data/reviews.json"

THEME_KEYWORDS: Dict[str, List[str]] = {
    "Cooking performance": [
        "crispy",
        "cook",
        "cooking",
        "air fry",
        "bake",
        "broil",
        "reheat",
        "dehydrate",
        "temperature",
        "preheat",
        "evenly",
        "juicy",
        "frozen",
    ],
    "Ease of use": [
        "easy",
        "simple",
        "intuitive",
        "buttons",
        "controls",
        "instructions",
        "setup",
        "user friendly",
        "learning curve",
    ],
    "Capacity and footprint": [
        "size",
        "capacity",
        "counter",
        "countertop",
        "space",
        "basket",
        "tray",
        "fits",
        "large",
        "small",
        "compact",
    ],
    "Cleaning and maintenance": [
        "clean",
        "cleanup",
        "dishwasher",
        "wash",
        "mess",
        "grease",
        "sticky",
        "scrub",
    ],
    "Build quality and durability": [
        "broke",
        "broken",
        "durable",
        "quality",
        "plastic",
        "flimsy",
        "stopped working",
        "defect",
        "replacement",
        "warranty",
        "repair",
    ],
    "Noise, odor, and heat": [
        "noise",
        "noisy",
        "loud",
        "odor",
        "smell",
        "hot",
        "heat",
        "steam",
        "fan",
    ],
    "Design and aesthetics": [
        "design",
        "looks",
        "sleek",
        "beautiful",
        "style",
        "appearance",
        "color",
    ],
    "Value and price": [
        "price",
        "worth",
        "value",
        "expensive",
        "cost",
        "money",
        "deal",
    ],
    "Service and shipping": [
        "shipping",
        "delivery",
        "customer service",
        "support",
        "return",
        "replacement",
        "arrived",
        "damaged",
        "missing",
    ],
}

STOPWORDS = {
    "a",
    "about",
    "after",
    "again",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "best",
    "better",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "don",
    "down",
    "even",
    "every",
    "for",
    "from",
    "get",
    "got",
    "great",
    "had",
    "has",
    "have",
    "he",
    "her",
    "here",
    "hers",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "love",
    "made",
    "make",
    "many",
    "me",
    "more",
    "most",
    "much",
    "my",
    "new",
    "no",
    "not",
    "now",
    "of",
    "on",
    "one",
    "only",
    "or",
    "other",
    "our",
    "out",
    "over",
    "product",
    "really",
    "so",
    "some",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "too",
    "use",
    "used",
    "using",
    "very",
    "was",
    "we",
    "well",
    "were",
    "what",
    "when",
    "which",
    "while",
    "with",
    "would",
    "you",
    "your",
}

PERSONAS: Dict[str, Dict[str, Any]] = {
    "Product Development": {
        "blurb": "Translates reviews into product and feature decisions.",
        "prompt": (
            "Create a report for the product development team. Highlight what customers love, unmet needs, "
            "feature gaps, usability friction, size/capacity comments, and concrete roadmap opportunities. "
            "End with the top 5 product actions ranked by impact."
        ),
        "sample_questions": [
            "What are the top product improvements suggested by non-incentivized reviewers?",
            "What features should we preserve in the next generation of this product?",
            "Summarize the biggest usability and design opportunities for the product team.",
        ],
        "instructions": (
            "You are a senior product strategy analyst. Focus on feature prioritization, user experience, "
            "jobs-to-be-done, product-market fit, and roadmap implications. Give clear recommendations and tie "
            "important claims to review IDs from the evidence pack."
        ),
    },
    "Quality Engineer": {
        "blurb": "Focuses on failure modes, defects, durability, and root-cause signals.",
        "prompt": (
            "Create a report for a quality engineer. Identify defect patterns, reliability risks, cleaning issues, "
            "performance inconsistencies, noise/odor/heat complaints, and probable root-cause hypotheses. "
            "Separate confirmed evidence from inference."
        ),
        "sample_questions": [
            "What are the highest-risk 1-star and 2-star failure themes?",
            "List possible quality issues that deserve engineering investigation.",
            "Compare low-star non-incentivized feedback versus the overall review base.",
        ],
        "instructions": (
            "You are a senior quality and reliability analyst. Be evidence-led, precise, and cautious. Prioritize "
            "failure modes, defect language, repeat complaints, severity, probable root causes, and follow-up tests. "
            "Clearly label any inference. Cite review IDs for material claims."
        ),
    },
    "Consumer Insights": {
        "blurb": "Extracts sentiment drivers, purchase motivations, and voice-of-customer insights.",
        "prompt": (
            "Create a report for the consumer insights team. Summarize key sentiment drivers, barriers to adoption, "
            "purchase motivations, key use cases, emotional language, and message opportunities. Include how the "
            "tone changes across star ratings and across incentivized vs non-incentivized reviews."
        ),
        "sample_questions": [
            "What are the strongest drivers of delight and disappointment?",
            "What jobs-to-be-done and usage occasions show up most often?",
            "How should marketing talk about this product based on the reviews?",
        ],
        "instructions": (
            "You are a consumer insights lead. Synthesize sentiment drivers, motivations, barriers, and language that "
            "helps teams understand the customer voice. Use concise, executive-ready writing and cite review IDs for "
            "important findings."
        ),
    },
}


class ReviewDownloaderError(Exception):
    """Raised when the product page or Bazaarvoice API cannot be processed."""


@dataclass
class ReviewBatchSummary:
    product_url: str
    product_id: str
    total_reviews: int
    page_size: int
    requests_needed: int
    reviews_downloaded: int


# -----------------------------------------------------------------------------
# Page + API helpers
# -----------------------------------------------------------------------------


def inject_css() -> None:
    st.markdown(
        """
        <style>
            .block-container {padding-top: 1.4rem; padding-bottom: 2rem;}
            div[data-testid="stMetric"] {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 14px;
                padding: 0.8rem 1rem;
                background: rgba(250, 250, 252, 0.85);
            }
            .section-subtitle {
                color: #6b7280;
                font-size: 0.95rem;
                margin-bottom: 0.75rem;
            }
            .insight-card {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 12px;
                padding: 0.9rem 1rem;
                background: rgba(250, 250, 252, 0.85);
            }
        </style>
        """,
        unsafe_allow_html=True,
    )



def normalize_product_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ReviewDownloaderError("Please paste a SharkNinja product URL.")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"
    return url



def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0 Safari/537.36"
            )
        }
    )
    return session



def fetch_product_html(session: requests.Session, product_url: str) -> str:
    response = session.get(product_url, timeout=30)
    response.raise_for_status()
    return response.text



def _extract_product_id_from_url(product_url: str) -> Optional[str]:
    path = urlparse(product_url).path
    match = re.search(r"/([A-Za-z0-9_-]+)\.html(?:$|[?#])", path)
    if match:
        candidate = match.group(1).strip().upper()
        if re.fullmatch(r"[A-Z0-9_-]{3,}", candidate):
            return candidate
    return None



def _extract_product_id_from_html(html: str) -> Optional[str]:
    primary_patterns = [
        r"Item\s*No\.?\s*([A-Z0-9_-]{3,})",
        r'"productId"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"sku"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"mpn"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"model"\s*:\s*"([A-Z0-9_-]{3,})"',
    ]
    for pattern in primary_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    for pattern in [r"Item\s*No\.?\s*([A-Z0-9_-]{3,})", r"Model\s*:?\s*([A-Z0-9_-]{3,})"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()

    return None



def extract_product_id(product_url: str, html: str) -> str:
    product_id = _extract_product_id_from_url(product_url) or _extract_product_id_from_html(html)
    if not product_id:
        raise ReviewDownloaderError(
            "Could not find a product ID on the page. Try a SharkNinja PDP URL like /AF181.html."
        )
    return product_id



def build_bv_params(
    *,
    product_id: str,
    passkey: str,
    displaycode: str,
    api_version: str,
    page_size: int,
    offset: int,
    sort: str,
    content_locales: str,
) -> Dict[str, Any]:
    return {
        "resource": "reviews",
        "action": "REVIEWS_N_STATS",
        "filter": [
            f"productid:eq:{product_id}",
            f"contentlocale:eq:{content_locales}",
            "isratingsonly:eq:false",
        ],
        "filter_reviews": f"contentlocale:eq:{content_locales}",
        "include": "authors,products,comments",
        "filteredstats": "reviews",
        "Stats": "Reviews",
        "limit": int(page_size),
        "offset": int(offset),
        "limit_comments": 3,
        "sort": sort,
        "passkey": passkey,
        "apiversion": api_version,
        "displaycode": displaycode,
    }



def fetch_reviews_page(
    session: requests.Session,
    *,
    product_id: str,
    passkey: str,
    displaycode: str,
    api_version: str,
    page_size: int,
    offset: int,
    sort: str,
    content_locales: str,
) -> Dict[str, Any]:
    params = build_bv_params(
        product_id=product_id,
        passkey=passkey,
        displaycode=displaycode,
        api_version=api_version,
        page_size=page_size,
        offset=offset,
        sort=sort,
        content_locales=content_locales,
    )
    response = session.get(BAZAARVOICE_ENDPOINT, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()

    if payload.get("HasErrors"):
        errors = payload.get("Errors") or []
        raise ReviewDownloaderError(f"Bazaarvoice returned an error: {json.dumps(errors, ensure_ascii=False)}")

    return payload



def get_total_reviews(
    session: requests.Session,
    *,
    product_id: str,
    passkey: str,
    displaycode: str,
    api_version: str,
    sort: str,
    content_locales: str,
) -> int:
    payload = fetch_reviews_page(
        session,
        product_id=product_id,
        passkey=passkey,
        displaycode=displaycode,
        api_version=api_version,
        page_size=1,
        offset=0,
        sort=sort,
        content_locales=content_locales,
    )
    return int(payload.get("TotalResults", 0))



def extract_photo_urls(photos: Iterable[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    for photo in photos or []:
        sizes = photo.get("Sizes") or {}
        for size_name in ["large", "normal", "thumbnail"]:
            candidate = ((sizes.get(size_name) or {}).get("Url"))
            if candidate:
                urls.append(candidate)
                break
    return urls



def is_incentivized_review(review: Dict[str, Any]) -> bool:
    badges_order = [str(item).lower() for item in (review.get("BadgesOrder") or [])]
    if any("incentivized" in badge for badge in badges_order):
        return True

    context_data = review.get("ContextDataValues") or {}
    if isinstance(context_data, dict):
        for key, value in context_data.items():
            if "incentivized" in str(key).lower():
                if isinstance(value, dict):
                    flag = str(value.get("Value", "")).strip().lower()
                    if flag in {"", "true", "1", "yes"}:
                        return True
                else:
                    return True
    return False



def flatten_review(review: Dict[str, Any]) -> Dict[str, Any]:
    syndication_source = review.get("SyndicationSource") or {}
    photos = review.get("Photos") or []
    badges_order = review.get("BadgesOrder") or []
    context_data = review.get("ContextDataValues") or {}
    if not isinstance(context_data, dict):
        context_data = {}

    review_text = (review.get("ReviewText") or "").strip()
    title = (review.get("Title") or "").strip()

    return {
        "review_id": review.get("Id"),
        "cid": review.get("CID"),
        "product_id": review.get("ProductId"),
        "original_product_name": review.get("OriginalProductName"),
        "title": title,
        "review_text": review_text,
        "rating": review.get("Rating"),
        "is_recommended": review.get("IsRecommended"),
        "user_nickname": review.get("UserNickname"),
        "author_id": review.get("AuthorId"),
        "user_location": review.get("UserLocation"),
        "content_locale": review.get("ContentLocale"),
        "submission_time": review.get("SubmissionTime"),
        "last_modification_time": review.get("LastModificationTime"),
        "last_moderated_time": review.get("LastModeratedTime"),
        "moderation_status": review.get("ModerationStatus"),
        "campaign_id": review.get("CampaignId"),
        "source_client": review.get("SourceClient"),
        "is_featured": review.get("IsFeatured"),
        "is_syndicated": review.get("IsSyndicated"),
        "syndication_source_name": syndication_source.get("Name"),
        "syndication_source_link": syndication_source.get("ContentLink"),
        "is_ratings_only": review.get("IsRatingsOnly"),
        "total_feedback_count": review.get("TotalFeedbackCount"),
        "total_positive_feedback_count": review.get("TotalPositiveFeedbackCount"),
        "total_negative_feedback_count": review.get("TotalNegativeFeedbackCount"),
        "total_inappropriate_feedback_count": review.get("TotalInappropriateFeedbackCount"),
        "total_comment_count": review.get("TotalCommentCount"),
        "total_client_response_count": review.get("TotalClientResponseCount"),
        "badges": ", ".join(str(x) for x in badges_order),
        "badges_json": json.dumps(review.get("Badges") or {}, ensure_ascii=False),
        "context_data_json": json.dumps(context_data, ensure_ascii=False),
        "secondary_ratings_json": json.dumps(review.get("SecondaryRatings") or {}, ensure_ascii=False),
        "tag_dimensions_json": json.dumps(review.get("TagDimensions") or {}, ensure_ascii=False),
        "photos_count": len(photos),
        "photo_urls": " | ".join(extract_photo_urls(photos)),
        "incentivized_review": is_incentivized_review(review),
        "raw_json": json.dumps(review, ensure_ascii=False),
    }



def fetch_all_reviews(
    session: requests.Session,
    *,
    product_id: str,
    passkey: str,
    displaycode: str,
    api_version: str,
    page_size: int,
    sort: str,
    content_locales: str,
    total_reviews: int,
) -> List[Dict[str, Any]]:
    reviews: List[Dict[str, Any]] = []
    if total_reviews <= 0:
        return reviews

    progress_bar = st.progress(0.0, text="Starting review download...")
    status = st.empty()

    offsets = list(range(0, total_reviews, page_size))
    for index, offset in enumerate(offsets, start=1):
        status.info(f"Pulling request {index} of {len(offsets)} (offset {offset})")
        payload = fetch_reviews_page(
            session,
            product_id=product_id,
            passkey=passkey,
            displaycode=displaycode,
            api_version=api_version,
            page_size=page_size,
            offset=offset,
            sort=sort,
            content_locales=content_locales,
        )
        page_results = payload.get("Results") or []
        reviews.extend(page_results)
        progress_bar.progress(index / len(offsets), text=f"Downloaded {len(reviews)} of {total_reviews} reviews")

    status.success(f"Finished downloading {len(reviews)} reviews.")
    return reviews


# -----------------------------------------------------------------------------
# Data shaping + analytics
# -----------------------------------------------------------------------------


def ensure_columns(df: pd.DataFrame, required_columns: Sequence[str]) -> pd.DataFrame:
    for column in required_columns:
        if column not in df.columns:
            df[column] = pd.NA
    return df



def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict, pd.Series, pd.DataFrame, pd.Index)):
        return False
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    return bool(missing) if isinstance(missing, (bool, int)) else False



def safe_text(value: Any, default: str = "") -> str:
    if is_missing_value(value):
        return default
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "<na>"}:
        return default
    return text



def safe_bool(value: Any, default: bool = False) -> bool:
    if is_missing_value(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if pd.isna(value):
                return default
        except Exception:
            pass
        return bool(value)
    text = safe_text(value).lower()
    if text in {"true", "1", "yes", "y", "t"}:
        return True
    if text in {"false", "0", "no", "n", "f", ""}:
        return False
    return default



def safe_int(value: Any, default: int = 0) -> int:
    if is_missing_value(value):
        return default
    try:
        return int(float(value))
    except Exception:
        try:
            return int(value)
        except Exception:
            return default





def parse_flag_text(value: Any, *, positive_tokens: Sequence[str], negative_tokens: Sequence[str]) -> Any:
    text = safe_text(value).lower()
    if text in {"", "nan", "none", "null", "n/a"}:
        return pd.NA
    if any(text == token.lower() for token in negative_tokens):
        return False
    if any(text == token.lower() for token in positive_tokens):
        return True
    if text.startswith("not ") or text.startswith("non "):
        return False
    return True



def extract_age_group_from_context_json(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    payload = value
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return None
        try:
            payload = json.loads(stripped)
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None

    for key, raw in payload.items():
        key_norm = str(key).lower().replace("_", " ").replace("-", " ")
        if "age" not in key_norm:
            continue
        candidate = raw
        if isinstance(raw, dict):
            candidate = raw.get("Value") or raw.get("value") or raw.get("Label") or raw.get("label")
        candidate = safe_text(candidate)
        if candidate and candidate.lower() not in {"nan", "none", "null", "unknown", "prefer not to say"}:
            return candidate
    return None



def finalize_reviews_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    required_columns = [
        "review_id",
        "product_id",
        "base_sku",
        "sku_item",
        "product_or_sku",
        "original_product_name",
        "title",
        "review_text",
        "rating",
        "is_recommended",
        "content_locale",
        "submission_time",
        "submission_date",
        "submission_month",
        "incentivized_review",
        "is_syndicated",
        "photos_count",
        "photo_urls",
        "title_and_text",
        "retailer",
        "post_link",
        "age_group",
        "user_nickname",
        "user_location",
        "total_positive_feedback_count",
        "source_system",
        "source_file",
    ]
    df = ensure_columns(df.copy(), required_columns)

    if df.empty:
        for extra in ["has_photos", "has_media", "review_length_chars", "review_length_words", "rating_label", "year_month_sort"]:
            if extra not in df.columns:
                df[extra] = pd.Series(dtype="object")
        return df

    df["review_id"] = df["review_id"].fillna("").astype(str).str.strip()
    missing_ids = df["review_id"].eq("") | df["review_id"].str.lower().isin({"nan", "none", "null"})
    if missing_ids.any():
        generated = [f"review_{idx + 1}" for idx in range(int(missing_ids.sum()))]
        df.loc[missing_ids, "review_id"] = generated

    if "context_data_json" in df.columns:
        df["age_group"] = df["age_group"].fillna(df["context_data_json"].map(extract_age_group_from_context_json))

    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["is_recommended"] = df["is_recommended"].map(lambda value: bool(value) if pd.notna(value) else pd.NA)
    df["incentivized_review"] = df["incentivized_review"].fillna(False).astype(bool)
    df["is_syndicated"] = df["is_syndicated"].fillna(False).astype(bool)
    df["photos_count"] = pd.to_numeric(df["photos_count"], errors="coerce").fillna(0).astype(int)
    df["title"] = df["title"].fillna("").astype(str)
    df["review_text"] = df["review_text"].fillna("").astype(str)
    df["submission_time"] = pd.to_datetime(df["submission_time"], errors="coerce", utc=True).dt.tz_convert(None)
    df["submission_date"] = df["submission_time"].dt.date
    df["submission_month"] = df["submission_time"].dt.to_period("M").astype(str)
    df["content_locale"] = df["content_locale"].fillna("").astype(str).replace({"": pd.NA})
    df["base_sku"] = df["base_sku"].fillna("").astype(str).str.strip()
    df["sku_item"] = df["sku_item"].fillna("").astype(str).str.strip()
    df["product_id"] = df["product_id"].fillna("").astype(str).str.strip()
    fallback = df["base_sku"].where(df["base_sku"].ne(""), df["product_id"])
    df["product_or_sku"] = df["sku_item"].where(df["sku_item"].ne(""), fallback)
    df["product_or_sku"] = df["product_or_sku"].fillna("").astype(str).str.strip().replace({"": pd.NA})
    df["title_and_text"] = (df["title"].str.strip() + " " + df["review_text"].str.strip()).str.strip()
    df["has_photos"] = df["photos_count"] > 0
    df["has_media"] = df["has_photos"]
    df["review_length_chars"] = df["review_text"].str.len()
    df["review_length_words"] = df["review_text"].str.split().str.len().fillna(0).astype(int)
    df["rating_label"] = df["rating"].map(lambda x: f"{int(x)} star" if pd.notna(x) else "Unknown")
    df["year_month_sort"] = pd.to_datetime(df["submission_month"], format="%Y-%m", errors="coerce")

    sort_cols = [col for col in ["submission_time", "review_id"] if col in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=[False, False], na_position="last").reset_index(drop=True)
    return df



def pick_first_column(df: pd.DataFrame, aliases: Sequence[str]) -> Optional[str]:
    lookup = {str(column).strip().lower(): column for column in df.columns}
    for alias in aliases:
        column = lookup.get(str(alias).strip().lower())
        if column is not None:
            return column
    return None



def series_from_aliases(df: pd.DataFrame, aliases: Sequence[str]) -> pd.Series:
    column = pick_first_column(df, aliases)
    if column is None:
        return pd.Series([pd.NA] * len(df), index=df.index)
    return df[column]



def normalize_uploaded_reviews_dataframe(raw_df: pd.DataFrame, *, source_name: str = "") -> pd.DataFrame:
    working = raw_df.copy()
    working.columns = [str(column).strip() for column in working.columns]
    normalized = pd.DataFrame(index=working.index)

    normalized["review_id"] = series_from_aliases(working, ["Event Id", "Event ID", "Review ID", "Review Id", "Id"])
    normalized["product_id"] = series_from_aliases(working, ["Base SKU", "Product ID", "Product Id", "ProductId", "BaseSKU"])
    normalized["base_sku"] = series_from_aliases(working, ["Base SKU", "BaseSKU"])
    normalized["sku_item"] = series_from_aliases(working, ["SKU Item", "SKU", "Child SKU", "Variant SKU", "Item Number", "Item No"])
    normalized["original_product_name"] = series_from_aliases(working, ["Product Name", "Product", "Name"])
    normalized["review_text"] = series_from_aliases(working, ["Review Text", "Review", "Body", "Content"])
    normalized["title"] = series_from_aliases(working, ["Title", "Review Title", "Headline"])
    normalized["post_link"] = series_from_aliases(working, ["Post Link", "URL", "Review URL", "Product URL"])
    normalized["rating"] = series_from_aliases(working, ["Rating (num)", "Rating", "Stars", "Star Rating"])
    normalized["submission_time"] = series_from_aliases(working, ["Opened date", "Opened Date", "Submission Time", "Review Date", "Date"])
    normalized["content_locale"] = series_from_aliases(working, ["Content Locale", "Locale", "Location", "Country"])
    normalized["retailer"] = series_from_aliases(working, ["Retailer", "Merchant", "Channel"])
    normalized["age_group"] = series_from_aliases(working, ["Age Group", "Age", "Age Range", "Age Bracket"])
    normalized["user_location"] = series_from_aliases(working, ["Location", "Country"])
    normalized["translated_flag"] = series_from_aliases(working, ["Translated Flag", "Translated"])
    normalized["seeded_flag"] = series_from_aliases(working, ["Seeded Flag", "Seeded", "Incentivized"])
    normalized["syndicated_flag"] = series_from_aliases(working, ["Syndicated Flag", "Syndicated"])
    normalized["consumer_facing_rating"] = series_from_aliases(working, ["Consumer Facing Rating", "Average Rating"])
    normalized["factory_name"] = series_from_aliases(working, ["Factory Name"])
    normalized["product_category"] = series_from_aliases(working, ["Product Category", "Category"])
    normalized["product_sub_category"] = series_from_aliases(working, ["Product Sub Category", "Sub Category", "Subcategory"])
    normalized["brand"] = series_from_aliases(working, ["Brand"])
    normalized["user_nickname"] = pd.NA
    normalized["total_positive_feedback_count"] = pd.NA
    normalized["is_recommended"] = pd.NA
    normalized["photos_count"] = 0
    normalized["photo_urls"] = pd.NA
    normalized["source_file"] = source_name or pd.NA
    normalized["source_system"] = "Uploaded file"
    normalized["incentivized_review"] = normalized["seeded_flag"].map(
        lambda value: parse_flag_text(
            value,
            positive_tokens=["seeded", "incentivized", "yes", "true", "1"],
            negative_tokens=["not seeded", "not incentivized", "no", "false", "0"],
        )
    )
    normalized["is_syndicated"] = normalized["syndicated_flag"].map(
        lambda value: parse_flag_text(
            value,
            positive_tokens=["syndicated", "yes", "true", "1"],
            negative_tokens=["not syndicated", "no", "false", "0"],
        )
    )
    return finalize_reviews_dataframe(normalized)



def read_uploaded_review_file(uploaded_file: Any) -> pd.DataFrame:
    file_name = getattr(uploaded_file, "name", "uploaded_file")
    raw_bytes = uploaded_file.getvalue()
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else "csv"

    if suffix == "csv":
        try:
            raw_df = pd.read_csv(io.BytesIO(raw_bytes))
        except UnicodeDecodeError:
            raw_df = pd.read_csv(io.BytesIO(raw_bytes), encoding="latin-1")
    elif suffix in {"xlsx", "xls", "xlsm"}:
        raw_df = pd.read_excel(io.BytesIO(raw_bytes))
    else:
        raise ReviewDownloaderError(f"Unsupported upload type for {file_name}. Use CSV or Excel.")

    if raw_df.empty:
        raise ReviewDownloaderError(f"{file_name} is empty.")
    return normalize_uploaded_reviews_dataframe(raw_df, source_name=file_name)



def load_uploaded_review_files(uploaded_files: Sequence[Any]) -> Dict[str, Any]:
    if not uploaded_files:
        raise ReviewDownloaderError("Upload at least one CSV or Excel review export to build the workspace.")

    with st.spinner("Reading and mapping the uploaded review files..."):
        frames = [read_uploaded_review_file(file) for file in uploaded_files]

    combined_df = pd.concat(frames, ignore_index=True)
    combined_df["review_id"] = combined_df["review_id"].astype(str)
    combined_df = combined_df.drop_duplicates(subset=["review_id"], keep="first").reset_index(drop=True)
    combined_df = finalize_reviews_dataframe(combined_df)

    inferred_product_id = first_non_empty(combined_df["base_sku"].fillna("")) or first_non_empty(combined_df["product_id"].fillna("")) or "UPLOADED_REVIEWS"
    file_names = [getattr(file, "name", "uploaded_file") for file in uploaded_files]
    source_label = file_names[0] if len(file_names) == 1 else f"{len(file_names)} uploaded files"
    summary = ReviewBatchSummary(
        product_url="",
        product_id=inferred_product_id,
        total_reviews=int(len(combined_df)),
        page_size=max(int(len(combined_df)), 1),
        requests_needed=0,
        reviews_downloaded=int(len(combined_df)),
    )
    return {
        "summary": summary,
        "reviews_df": combined_df,
        "source_type": "uploaded",
        "source_label": source_label,
    }



def build_reviews_dataframe(raw_reviews: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = [flatten_review(review) for review in raw_reviews]
    df = pd.DataFrame(rows)
    return finalize_reviews_dataframe(df)


def safe_mean(series: pd.Series) -> Optional[float]:
    if series.empty:
        return None
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.mean())



def safe_pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)



def compute_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    total_reviews = int(len(df))
    if total_reviews == 0:
        return {
            "review_count": 0,
            "avg_rating": None,
            "avg_rating_non_incentivized": None,
            "pct_low_star": 0.0,
            "pct_one_star": 0.0,
            "pct_two_star": 0.0,
            "pct_five_star": 0.0,
            "pct_incentivized": 0.0,
            "pct_with_photos": 0.0,
            "pct_syndicated": 0.0,
            "recommend_rate": None,
            "median_review_words": None,
            "non_incentivized_count": 0,
            "low_star_count": 0,
        }

    non_incentivized = df[~df["incentivized_review"].fillna(False)]
    low_star_mask = df["rating"].isin([1, 2])
    one_star_mask = df["rating"] == 1
    two_star_mask = df["rating"] == 2
    five_star_mask = df["rating"] == 5
    recommend_base = df[df["is_recommended"].notna()]

    recommend_rate: Optional[float] = None
    if not recommend_base.empty:
        recommend_rate = safe_pct(int(recommend_base["is_recommended"].astype(bool).sum()), len(recommend_base))

    median_review_words: Optional[float] = None
    if "review_length_words" in df.columns and not df["review_length_words"].dropna().empty:
        median_review_words = float(df["review_length_words"].median())

    return {
        "review_count": total_reviews,
        "avg_rating": safe_mean(df["rating"]),
        "avg_rating_non_incentivized": safe_mean(non_incentivized["rating"]),
        "pct_low_star": safe_pct(int(low_star_mask.sum()), total_reviews),
        "pct_one_star": safe_pct(int(one_star_mask.sum()), total_reviews),
        "pct_two_star": safe_pct(int(two_star_mask.sum()), total_reviews),
        "pct_five_star": safe_pct(int(five_star_mask.sum()), total_reviews),
        "pct_incentivized": safe_pct(int(df["incentivized_review"].fillna(False).sum()), total_reviews),
        "pct_with_photos": safe_pct(int(df["has_photos"].fillna(False).sum()), total_reviews),
        "pct_syndicated": safe_pct(int(df["is_syndicated"].fillna(False).sum()), total_reviews),
        "recommend_rate": recommend_rate,
        "median_review_words": median_review_words,
        "non_incentivized_count": int(len(non_incentivized)),
        "low_star_count": int(low_star_mask.sum()),
    }



def rating_distribution(df: pd.DataFrame) -> pd.DataFrame:
    base = pd.DataFrame({"rating": [1, 2, 3, 4, 5]})
    if df.empty:
        base["review_count"] = 0
        base["share"] = 0.0
        return base

    grouped = (
        df.dropna(subset=["rating"])
        .assign(rating=lambda x: x["rating"].astype(int))
        .groupby("rating", as_index=False)
        .size()
        .rename(columns={"size": "review_count"})
    )
    merged = base.merge(grouped, how="left", on="rating").fillna({"review_count": 0})
    merged["review_count"] = merged["review_count"].astype(int)
    merged["share"] = merged["review_count"] / max(len(df), 1)
    return merged



def monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["submission_month", "review_count", "avg_rating", "month_start"])

    monthly = (
        df.dropna(subset=["submission_time"])
        .assign(month_start=lambda x: x["submission_time"].dt.to_period("M").dt.to_timestamp())
        .groupby("month_start", as_index=False)
        .agg(review_count=("review_id", "count"), avg_rating=("rating", "mean"))
    )
    monthly["submission_month"] = monthly["month_start"].dt.strftime("%Y-%m")
    return monthly.sort_values("month_start")



def locale_distribution(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["content_locale", "review_count", "share"])

    locale_df = (
        df.assign(content_locale=df["content_locale"].fillna("Unknown"))
        .groupby("content_locale", as_index=False)
        .size()
        .rename(columns={"size": "review_count"})
        .sort_values("review_count", ascending=False)
    )
    locale_df["share"] = locale_df["review_count"] / max(len(df), 1)
    return locale_df



def normalize_text_for_search(text: str) -> str:
    text = safe_text(text).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()



def tokenize(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9']+", normalize_text_for_search(text))
        if len(token) > 2 and token not in STOPWORDS
    ]



def top_terms(texts: Iterable[str], *, top_n: int = 12) -> pd.DataFrame:
    counter: Counter[str] = Counter()
    for text in texts:
        counter.update(tokenize(text))
    rows = [{"term": term, "count": count} for term, count in counter.most_common(top_n)]
    return pd.DataFrame(rows)



def compute_theme_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "theme",
                "mention_count",
                "mention_rate",
                "avg_rating_when_mentioned",
                "low_star_mentions",
                "high_star_mentions",
            ]
        )

    text_series = df["title_and_text"].fillna("").astype(str).map(normalize_text_for_search)
    rows: List[Dict[str, Any]] = []

    for theme, keywords in THEME_KEYWORDS.items():
        mask = text_series.map(lambda text: any(keyword in text for keyword in keywords))
        subset = df[mask]
        rows.append(
            {
                "theme": theme,
                "mention_count": int(mask.sum()),
                "mention_rate": safe_pct(int(mask.sum()), len(df)),
                "avg_rating_when_mentioned": safe_mean(subset["rating"]),
                "low_star_mentions": int(subset["rating"].isin([1, 2]).sum()),
                "high_star_mentions": int(subset["rating"].isin([4, 5]).sum()),
            }
        )

    return pd.DataFrame(rows).sort_values(["mention_count", "low_star_mentions"], ascending=[False, False])



def format_metric_number(value: Optional[float], digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.{digits}f}"



def format_pct(value: Optional[float], digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{100 * float(value):.{digits}f}%"



def compare_metric_delta(filtered_value: Optional[float], overall_value: Optional[float], *, is_pct: bool = False) -> str:
    if filtered_value is None or overall_value is None or pd.isna(filtered_value) or pd.isna(overall_value):
        return "vs overall n/a"
    delta = float(filtered_value) - float(overall_value)
    if is_pct:
        return f"vs overall {delta * 100:+.1f} pts"
    return f"vs overall {delta:+.2f}"





def build_filter_options(df: pd.DataFrame) -> Dict[str, Any]:
    valid_dates = df["submission_date"].dropna() if "submission_date" in df.columns else pd.Series(dtype="object")
    min_date = valid_dates.min() if not valid_dates.empty else None
    max_date = valid_dates.max() if not valid_dates.empty else None
    product_groups = []
    if "product_or_sku" in df.columns and not df.empty:
        product_groups = sorted(
            {
                str(value).strip()
                for value in df["product_or_sku"].dropna().astype(str)
                if str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
            }
        )
    return {
        "ratings": [1, 2, 3, 4, 5],
        "product_groups": product_groups,
        "locales": sorted(str(locale) for locale in df["content_locale"].dropna().unique()) if not df.empty else [],
        "min_date": min_date,
        "max_date": max_date,
    }



def apply_filters(
    df: pd.DataFrame,
    *,
    selected_ratings: Sequence[int],
    incentivized_mode: str,
    selected_products: Sequence[str] = (),
    selected_locales: Sequence[str],
    recommendation_mode: str,
    syndicated_mode: str,
    media_mode: str,
    date_range: Optional[Tuple[date, date]],
    text_query: str,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    filtered = df.copy()
    if selected_ratings:
        filtered = filtered[filtered["rating"].isin(selected_ratings)]
    if selected_products and "product_or_sku" in filtered.columns:
        filtered = filtered[filtered["product_or_sku"].fillna("").isin(selected_products)]
    if incentivized_mode == "Non-incentivized only":
        filtered = filtered[~filtered["incentivized_review"].fillna(False)]
    elif incentivized_mode == "Incentivized only":
        filtered = filtered[filtered["incentivized_review"].fillna(False)]
    if selected_locales:
        filtered = filtered[filtered["content_locale"].fillna("Unknown").isin(selected_locales)]
    if recommendation_mode == "Recommended only":
        filtered = filtered[filtered["is_recommended"].fillna(False)]
    elif recommendation_mode == "Not recommended only":
        filtered = filtered[filtered["is_recommended"].notna() & ~filtered["is_recommended"].fillna(False)]
    if syndicated_mode == "Syndicated only":
        filtered = filtered[filtered["is_syndicated"].fillna(False)]
    elif syndicated_mode == "Non-syndicated only":
        filtered = filtered[~filtered["is_syndicated"].fillna(False)]
    if media_mode == "With photos only":
        filtered = filtered[filtered["has_photos"].fillna(False)]
    elif media_mode == "No photos only":
        filtered = filtered[~filtered["has_photos"].fillna(False)]
    if date_range and date_range[0] and date_range[1] and "submission_date" in filtered.columns:
        start_date, end_date = date_range
        filtered = filtered[
            filtered["submission_date"].notna()
            & (filtered["submission_date"] >= start_date)
            & (filtered["submission_date"] <= end_date)
        ]

    query = text_query.strip()
    if query:
        pattern = re.escape(query)
        filtered = filtered[filtered["title_and_text"].fillna("").str.contains(pattern, case=False, na=False, regex=True)]

    return filtered.reset_index(drop=True)



def describe_active_filters(
    *,
    selected_ratings: Sequence[int],
    incentivized_mode: str,
    selected_locales: Sequence[str],
    recommendation_mode: str,
    syndicated_mode: str,
    media_mode: str,
    date_range: Optional[Tuple[date, date]],
    text_query: str,
) -> str:
    parts: List[str] = []
    if selected_ratings and set(selected_ratings) != {1, 2, 3, 4, 5}:
        parts.append("ratings=" + ",".join(str(r) for r in selected_ratings))
    if incentivized_mode != "All reviews":
        parts.append(f"source={incentivized_mode}")
    if selected_locales:
        parts.append("locales=" + ", ".join(selected_locales))
    if recommendation_mode != "All":
        parts.append(f"recommendation={recommendation_mode}")
    if syndicated_mode != "All":
        parts.append(f"syndication={syndicated_mode}")
    if media_mode != "All":
        parts.append(f"media={media_mode}")
    if date_range and date_range[0] and date_range[1]:
        parts.append(f"dates={date_range[0]} to {date_range[1]}")
    if text_query.strip():
        parts.append(f'text contains="{text_query.strip()}"')
    return "; ".join(parts) if parts else "No active filters"


# -----------------------------------------------------------------------------
# Exports# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------


def autosize_worksheet(worksheet, df: pd.DataFrame, sample_rows: int = 250) -> None:
    worksheet.freeze_panes = "A2"
    for idx, column in enumerate(df.columns, start=1):
        series = df[column].head(sample_rows).fillna("").astype(str) if column in df.columns else pd.Series(dtype="str")
        max_len = max([len(str(column)), *[len(value) for value in series.tolist()]] or [10])
        worksheet.column_dimensions[get_column_letter(idx)].width = min(max_len + 2, 48)



def metrics_table(metrics: Dict[str, Any]) -> pd.DataFrame:
    ordered_rows = [
        ("review_count", metrics.get("review_count")),
        ("avg_rating", metrics.get("avg_rating")),
        ("avg_rating_non_incentivized", metrics.get("avg_rating_non_incentivized")),
        ("pct_low_star", metrics.get("pct_low_star")),
        ("pct_one_star", metrics.get("pct_one_star")),
        ("pct_two_star", metrics.get("pct_two_star")),
        ("pct_five_star", metrics.get("pct_five_star")),
        ("pct_incentivized", metrics.get("pct_incentivized")),
        ("pct_with_photos", metrics.get("pct_with_photos")),
        ("pct_syndicated", metrics.get("pct_syndicated")),
        ("recommend_rate", metrics.get("recommend_rate")),
        ("median_review_words", metrics.get("median_review_words")),
        ("non_incentivized_count", metrics.get("non_incentivized_count")),
        ("low_star_count", metrics.get("low_star_count")),
    ]
    return pd.DataFrame(ordered_rows, columns=["metric", "value"])



def build_excel_file(
    summary: ReviewBatchSummary,
    reviews_df: pd.DataFrame,
    overall_metrics: Dict[str, Any],
    theme_df: pd.DataFrame,
    rating_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    locale_df: pd.DataFrame,
    positive_terms_df: pd.DataFrame,
    negative_terms_df: pd.DataFrame,
) -> bytes:
    summary_df = pd.DataFrame(
        [
            {
                "product_url": summary.product_url,
                "product_id": summary.product_id,
                "total_reviews": summary.total_reviews,
                "page_size": summary.page_size,
                "requests_needed": summary.requests_needed,
                "reviews_downloaded": summary.reviews_downloaded,
                "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        ]
    )
    metrics_df = metrics_table(overall_metrics)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheets = {
            "Summary": summary_df,
            "Metrics": metrics_df,
            "Reviews": reviews_df,
            "RatingDistribution": rating_df,
            "MonthlyTrend": monthly_df,
            "Locales": locale_df,
            "Themes": theme_df,
            "TopPositiveTerms": positive_terms_df,
            "TopNegativeTerms": negative_terms_df,
        }
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            autosize_worksheet(writer.sheets[sheet_name], df)

    output.seek(0)
    return output.getvalue()



def dataframe_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    sql_df = df.copy()
    for column in sql_df.columns:
        if pd.api.types.is_datetime64_any_dtype(sql_df[column]):
            sql_df[column] = sql_df[column].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif pd.api.types.is_bool_dtype(sql_df[column]):
            sql_df[column] = sql_df[column].astype(int)
    return sql_df



def build_sqlite_database(
    summary: ReviewBatchSummary,
    reviews_df: pd.DataFrame,
    overall_metrics: Dict[str, Any],
    theme_df: pd.DataFrame,
    rating_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    locale_df: pd.DataFrame,
) -> bytes:
    summary_df = pd.DataFrame(
        [
            {
                "product_url": summary.product_url,
                "product_id": summary.product_id,
                "total_reviews": summary.total_reviews,
                "page_size": summary.page_size,
                "requests_needed": summary.requests_needed,
                "reviews_downloaded": summary.reviews_downloaded,
                "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        ]
    )
    metrics_df = metrics_table(overall_metrics)

    temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp.close()
    try:
        conn = sqlite3.connect(temp.name)
        dataframe_for_sql(reviews_df).to_sql("reviews", conn, index=False, if_exists="replace")
        dataframe_for_sql(summary_df).to_sql("metadata", conn, index=False, if_exists="replace")
        dataframe_for_sql(metrics_df).to_sql("metrics", conn, index=False, if_exists="replace")
        dataframe_for_sql(theme_df).to_sql("theme_signals", conn, index=False, if_exists="replace")
        dataframe_for_sql(rating_df).to_sql("rating_distribution", conn, index=False, if_exists="replace")
        dataframe_for_sql(monthly_df).to_sql("monthly_trend", conn, index=False, if_exists="replace")
        dataframe_for_sql(locale_df).to_sql("locale_distribution", conn, index=False, if_exists="replace")
        conn.close()
        with open(temp.name, "rb") as file:
            return file.read()
    finally:
        try:
            os.remove(temp.name)
        except OSError:
            pass



# -----------------------------------------------------------------------------
# AI context, prompt tagging, UI, and app shell
# -----------------------------------------------------------------------------


GENERAL_ANALYST_INSTRUCTIONS = textwrap.dedent(
    """
    You are SharkNinja Review Analyst, an internal voice-of-customer assistant.
    Help product development, quality engineering, and consumer insights teams understand the review base.
    Prioritize the supplied review text over generic product assumptions.

    Ground every material claim in the supplied review dataset.
    Base most of the narrative on the supplied review text evidence, using the metrics only as supporting context.
    Do not invent counts, quotes, or trends that are not supported by the evidence pack.
    When evidence is mixed or weak, say so clearly.
    Use markdown.
    Cite supporting review IDs in parentheses, for example: (review_ids: 12345, 67890).
    Turn insights into practical actions whenever possible.
    """
).strip()

DEFAULT_PROMPT_BATCH_SIZE = 15


# -----------------------------------------------------------------------------
# OpenAI helpers
# -----------------------------------------------------------------------------


def get_openai_api_key() -> Optional[str]:
    try:
        if "OPENAI_API_KEY" in st.secrets:
            return str(st.secrets["OPENAI_API_KEY"])
        if "openai" in st.secrets and st.secrets["openai"].get("api_key"):
            return str(st.secrets["openai"]["api_key"])
    except Exception:
        pass
    return os.getenv("OPENAI_API_KEY")



def get_openai_client(api_key: str) -> Any:
    if OpenAI is None:
        raise ReviewDownloaderError("The OpenAI Python package is not installed. Add openai to your environment.")
    if not api_key:
        raise ReviewDownloaderError(
            "No OpenAI API key was found in Streamlit secrets or the OPENAI_API_KEY environment variable."
        )
    return OpenAI(api_key=api_key)



def reasoning_options_for_model(model: str) -> List[str]:
    return list(MODEL_REASONING_SUPPORT.get(model, REASONING_OPTIONS))


def default_reasoning_effort_for_model(model: str) -> str:
    supported = reasoning_options_for_model(model)
    for candidate in [DEFAULT_REASONING_EFFORT, "low", "medium", "none"]:
        if candidate in supported:
            return candidate
    return supported[0] if supported else DEFAULT_REASONING_EFFORT


def sanitize_reasoning_effort(model: str, reasoning_effort: Optional[str]) -> str:
    supported = reasoning_options_for_model(model)
    effort = str(reasoning_effort or "").strip().lower()
    if effort in supported:
        return effort
    if effort == "minimal" and "low" in supported:
        return "low"
    if effort == "none" and "low" in supported:
        return "low"
    if effort == "xhigh" and "high" in supported:
        return "high"
    return default_reasoning_effort_for_model(model)


def build_reasoning_kwargs(model: str, reasoning_effort: Optional[str]) -> Dict[str, Any]:
    effort = sanitize_reasoning_effort(model, reasoning_effort)
    if not effort:
        return {}
    return {"reasoning": {"effort": effort}}


def create_openai_response(client: Any, *, model: str, reasoning_effort: Optional[str], **kwargs: Any) -> Any:
    request_kwargs = {"model": model, **build_reasoning_kwargs(model, reasoning_effort), **kwargs}
    try:
        return client.responses.create(**request_kwargs)
    except Exception as exc:
        message = str(exc)
        if "reasoning.effort" not in message and "unsupported_value" not in message:
            raise

        fallback_effort = default_reasoning_effort_for_model(model)
        current_effort = (((request_kwargs.get("reasoning") or {}).get("effort")) if isinstance(request_kwargs.get("reasoning"), dict) else None)

        if fallback_effort and fallback_effort != current_effort:
            retry_kwargs = dict(request_kwargs)
            retry_kwargs["reasoning"] = {"effort": fallback_effort}
            return client.responses.create(**retry_kwargs)

        retry_kwargs = dict(request_kwargs)
        retry_kwargs.pop("reasoning", None)
        return client.responses.create(**retry_kwargs)


def parse_openai_json_response(response: Any) -> Dict[str, Any]:
    output_text = (getattr(response, "output_text", None) or "").strip()
    if not output_text:
        raise ReviewDownloaderError("OpenAI returned an empty structured response.")
    try:
        return json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ReviewDownloaderError(f"OpenAI returned invalid JSON: {exc}") from exc



def call_openai_json(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    instructions: str,
    input_payload: Any,
    schema_name: str,
    schema: Dict[str, Any],
    max_output_tokens: int = 3500,
) -> Dict[str, Any]:
    client = get_openai_client(api_key)
    response = create_openai_response(
        client,
        model=model,
        reasoning_effort=reasoning_effort,
        instructions=instructions,
        input=input_payload,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        },
        max_output_tokens=max_output_tokens,
        truncation="auto",
    )
    return parse_openai_json_response(response)



def truncate_text(text: str, max_chars: int = 420) -> str:
    text = re.sub(r"\s+", " ", safe_text(text)).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."



def humanize_column_name(name: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", safe_text(name)).strip()
    return cleaned.title() if cleaned else "Custom prompt"



def slugify_column_name(text: str, *, fallback: str = "custom_prompt") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", safe_text(text).lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"prompt_{cleaned}"
    return cleaned[:64]



def first_non_empty(series: pd.Series) -> str:
    if series.empty:
        return ""
    for value in series.astype(str):
        value = safe_text(value)
        if value and value.lower() != "nan":
            return value
    return ""



def product_display_name(summary: ReviewBatchSummary, reviews_df: pd.DataFrame) -> str:
    if not reviews_df.empty and "original_product_name" in reviews_df.columns:
        name = first_non_empty(reviews_df["original_product_name"].fillna(""))
        if name:
            return name
    return summary.product_id



def select_relevant_reviews(df: pd.DataFrame, question: str, max_reviews: int = 18) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    working = df.copy()
    working["search_blob"] = working["title_and_text"].fillna("").astype(str).map(normalize_text_for_search)
    query_tokens = tokenize(question)

    def score_row(row: pd.Series) -> float:
        score = 0.0
        text = row["search_blob"]
        for token in query_tokens:
            if token in text:
                score += 3 + text.count(token)
        rating = row.get("rating")
        if any(token in {"defect", "broken", "issue", "problem", "negative", "bad", "return", "quality", "warranty"} for token in query_tokens):
            if pd.notna(rating):
                score += max(0, 6 - float(rating))
        if any(token in {"love", "best", "favorite", "positive", "strength", "delight"} for token in query_tokens):
            if pd.notna(rating):
                score += max(0, float(rating) - 2)
        incentivized_value = row.get("incentivized_review")
        if not is_missing_value(incentivized_value) and not safe_bool(incentivized_value, False):
            score += 0.5
        if pd.notna(row.get("review_length_words")):
            score += min(float(row.get("review_length_words", 0)) / 60, 2)
        return score

    working["relevance_score"] = working.apply(score_row, axis=1)
    ranked = working.sort_values(["relevance_score", "submission_time"], ascending=[False, False], na_position="last")

    buckets = []
    if query_tokens:
        buckets.append(ranked.head(max_reviews))
    else:
        buckets.append(ranked[ranked["rating"].isin([1, 2])].head(max_reviews // 3 or 1))
        buckets.append(ranked[ranked["rating"].isin([4, 5])].head(max_reviews // 3 or 1))
        buckets.append(ranked.head(max_reviews))

    combined = pd.concat(buckets, ignore_index=True).drop_duplicates(subset=["review_id"])
    combined = combined.sort_values(["relevance_score", "submission_time"], ascending=[False, False], na_position="last")
    return combined.head(max_reviews).drop(columns=["search_blob", "relevance_score"], errors="ignore")



def review_snippet_rows(df: pd.DataFrame, *, max_reviews: int = 18) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, row in df.head(max_reviews).iterrows():
        rows.append(
            {
                "review_id": safe_text(row.get("review_id")),
                "rating": safe_int(row.get("rating"), 0) if pd.notna(row.get("rating")) else None,
                "incentivized_review": safe_bool(row.get("incentivized_review"), False),
                "content_locale": safe_text(row.get("content_locale")),
                "retailer": safe_text(row.get("retailer")),
                "age_group": safe_text(row.get("age_group")),
                "product_or_sku": safe_text(row.get("product_or_sku")),
                "submission_date": safe_text(row.get("submission_date")),
                "title": truncate_text(row.get("title", ""), 120),
                "snippet": truncate_text(row.get("review_text", ""), 520),
            }
        )
    return rows





def build_ai_context(
    *,
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
    question: str,
) -> str:
    overall_metrics = compute_metrics(overall_df)
    filtered_metrics = compute_metrics(filtered_df)
    rating_df = rating_distribution(filtered_df)
    monthly_df = monthly_trend(filtered_df).tail(12)

    relevant_reviews = select_relevant_reviews(filtered_df, question, max_reviews=18)
    recent_reviews = filtered_df.sort_values(["submission_time", "review_id"], ascending=[False, False], na_position="last").head(10)
    low_star_reviews = filtered_df[filtered_df["rating"].isin([1, 2])].head(8)
    high_star_reviews = filtered_df[filtered_df["rating"].isin([4, 5])].head(8)
    evidence_pack = pd.concat([relevant_reviews, recent_reviews, low_star_reviews, high_star_reviews], ignore_index=True).drop_duplicates(subset=["review_id"]).head(28)

    context_payload = {
        "product": {
            "product_id": summary.product_id,
            "product_url": summary.product_url,
            "product_name": product_display_name(summary, overall_df),
        },
        "analysis_scope": {
            "current_filter_description": filter_description,
            "overall_review_count": int(len(overall_df)),
            "filtered_review_count": int(len(filtered_df)),
        },
        "metric_snapshot": {
            "overall": overall_metrics,
            "filtered": filtered_metrics,
            "rating_distribution_filtered": rating_df.to_dict(orient="records"),
            "monthly_trend_filtered": monthly_df.to_dict(orient="records"),
        },
        "review_text_evidence": review_snippet_rows(evidence_pack, max_reviews=28),
    }
    return json.dumps(context_payload, ensure_ascii=False, indent=2, default=str)


def build_report_instructions(persona_name: Optional[str] = None) -> str:
    if not persona_name:
        return GENERAL_ANALYST_INSTRUCTIONS
    persona = PERSONAS[persona_name]
    return textwrap.dedent(
        f"""
        {persona['instructions']}

        Ground every important finding in the supplied review dataset.
        Prioritize the supplied review text evidence and use the metrics only as supporting context.
        Do not invent facts, counts, or quotes that are not supported by the evidence pack.
        If evidence is mixed or weak, say so explicitly.
        Use markdown.
        Cite supporting review IDs in parentheses, for example: (review_ids: 12345, 67890).
        Where useful, separate facts from inference.
        End with a short action list tailored to the audience.
        """
    ).strip()



def call_openai_analyst(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    question: str,
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
    chat_history: Sequence[Dict[str, str]],
    persona_name: Optional[str] = None,
) -> str:
    client = get_openai_client(api_key)
    instructions = build_report_instructions(persona_name)
    ai_context = build_ai_context(
        overall_df=overall_df,
        filtered_df=filtered_df,
        summary=summary,
        filter_description=filter_description,
        question=question,
    )

    input_messages: List[Dict[str, Any]] = []
    for message in chat_history[-8:]:
        input_messages.append({"role": message["role"], "content": message["content"]})

    user_payload = textwrap.dedent(
        f"""
        User request:
        {question}

        Review dataset context (JSON):
        {ai_context}
        """
    ).strip()
    input_messages.append({"role": "user", "content": user_payload})

    response = create_openai_response(
        client,
        model=model,
        reasoning_effort=reasoning_effort,
        instructions=instructions,
        input=input_messages,
        max_output_tokens=1600,
        truncation="auto",
    )
    output_text = (getattr(response, "output_text", None) or "").strip()
    if not output_text:
        raise ReviewDownloaderError("OpenAI returned an empty answer.")
    return output_text


# -----------------------------------------------------------------------------
# Review Prompt builder and row-by-row classification
# -----------------------------------------------------------------------------




REVIEW_PROMPT_STARTER_ROWS: List[Dict[str, str]] = [
    {
        "column_name": "perceived_loudness",
        "prompt": "How is product loudness described? Use Positive, Negative, Neutral, or Not Mentioned.",
        "labels": "Positive, Negative, Neutral, Not Mentioned",
    },
    {
        "column_name": "usage_session_bucket",
        "prompt": "What level of hands-on use is explicitly described? Use 1 Session, 2-5 Sessions, 6+ Sessions, Long-Term Use, or Not Mentioned.",
        "labels": "1 Session, 2-5 Sessions, 6+ Sessions, Long-Term Use, Not Mentioned",
    },
    {
        "column_name": "safety_risk_level",
        "prompt": "Does the review mention a safety risk? Use High Risk, Medium Risk, Low Risk, or No Risk Mentioned.",
        "labels": "High Risk, Medium Risk, Low Risk, No Risk Mentioned",
    },
    {
        "column_name": "reliability_risk_signal",
        "prompt": "Does the review mention a product reliability or durability risk? Use Risk Mentioned, Positive Reliability, or Not Mentioned.",
        "labels": "Risk Mentioned, Positive Reliability, Not Mentioned",
    },
]



def default_prompt_definitions() -> pd.DataFrame:
    return pd.DataFrame([REVIEW_PROMPT_STARTER_ROWS[0]])



def add_prompt_rows(prompt_df: pd.DataFrame, rows: Sequence[Dict[str, str]]) -> pd.DataFrame:
    base = prompt_df.copy() if prompt_df is not None else pd.DataFrame(columns=["column_name", "prompt", "labels"])
    existing = {str(value).strip().lower() for value in base.get("column_name", pd.Series(dtype="object")).fillna("").astype(str)}
    new_rows = []
    for row in rows:
        name = safe_text(row.get("column_name")).lower()
        if name and name in existing:
            continue
        new_rows.append({
            "column_name": safe_text(row.get("column_name")),
            "prompt": safe_text(row.get("prompt")),
            "labels": safe_text(row.get("labels")),
        })
        if name:
            existing.add(name)
    if not new_rows:
        return base.reset_index(drop=True)
    return pd.concat([base, pd.DataFrame(new_rows)], ignore_index=True)



def normalize_prompt_definitions(prompt_df: pd.DataFrame, existing_columns: Sequence[str]) -> List[Dict[str, Any]]:
    if prompt_df is None or prompt_df.empty:
        return []

    normalized: List[Dict[str, Any]] = []
    seen_columns: set[str] = set()
    existing_set = {str(col) for col in existing_columns}

    for _, row in prompt_df.fillna("").iterrows():
        raw_prompt = safe_text(row.get("prompt"))
        raw_labels = safe_text(row.get("labels"))
        raw_column = safe_text(row.get("column_name"))

        if not raw_prompt and not raw_labels and not raw_column:
            continue
        if not raw_prompt:
            raise ReviewDownloaderError("Each Review Prompt row needs a prompt.")
        if not raw_labels:
            raise ReviewDownloaderError("Each Review Prompt row needs labels separated by commas.")

        labels = [label.strip() for label in raw_labels.split(",") if label.strip()]
        deduped_labels: List[str] = []
        for label in labels:
            if label not in deduped_labels:
                deduped_labels.append(label)
        if len(deduped_labels) < 2:
            raise ReviewDownloaderError("Each Review Prompt row needs at least two labels.")
        if "Not Mentioned" not in deduped_labels and len(deduped_labels) <= 7:
            deduped_labels.append("Not Mentioned")

        column_name = slugify_column_name(raw_column or raw_prompt)
        if column_name in existing_set and column_name not in {"review_id"}:
            if column_name not in seen_columns:
                column_name = f"{column_name}_ai"
        base_name = column_name
        suffix = 2
        while column_name in seen_columns:
            column_name = f"{base_name}_{suffix}"
            suffix += 1
        seen_columns.add(column_name)

        normalized.append(
            {
                "column_name": column_name,
                "display_name": humanize_column_name(column_name),
                "prompt": raw_prompt,
                "labels": deduped_labels,
                "labels_csv": ", ".join(deduped_labels),
            }
        )
    return normalized



def prompt_definition_signature(prompt_definitions: Sequence[Dict[str, Any]]) -> str:
    serializable = [
        {"column_name": item["column_name"], "prompt": item["prompt"], "labels": list(item["labels"])}
        for item in prompt_definitions
    ]
    return json.dumps(serializable, sort_keys=True)



def build_prompt_builder_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "column_name": {"type": "string"},
            "prompt": {"type": "string"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 6,
            },
            "why_it_matters": {"type": "string"},
        },
        "required": ["column_name", "prompt", "labels", "why_it_matters"],
    }



def build_prompt_builder_context(goal: str, filtered_df: pd.DataFrame, summary: ReviewBatchSummary) -> str:
    sample_reviews = review_snippet_rows(select_relevant_reviews(filtered_df, goal, max_reviews=8), max_reviews=8)
    payload = {
        "product_id": summary.product_id,
        "product_name": product_display_name(summary, filtered_df),
        "goal": goal,
        "sample_reviews": sample_reviews,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)





def call_openai_prompt_builder(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    goal: str,
    preferred_labels: str,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
) -> Dict[str, Any]:
    context = build_prompt_builder_context(goal, filtered_df, summary)
    instructions = textwrap.dedent(
        """
        You design row-level review-tagging prompts for SharkNinja internal analysts.
        Draft one short prompt.
        Keep the prompt to one sentence and under 16 words.
        Avoid examples, long explanations, extra caveats, and multi-step rules.
        Make the column name snake_case.
        Prefer 3 to 5 labels in business-friendly title case.
        Include Not Mentioned when the signal may be absent.
        Keep why_it_matters to one short phrase.
        """
    ).strip()
    input_payload = textwrap.dedent(
        f"""
        Analyst goal:
        {goal}

        Preferred labels:
        {preferred_labels or 'Positive, Negative, Neutral, Not Mentioned'}

        Product context:
        {context}
        """
    ).strip()
    result = call_openai_json(
        api_key=api_key,
        model=model,
        reasoning_effort=reasoning_effort,
        instructions=instructions,
        input_payload=input_payload,
        schema_name="review_prompt_builder",
        schema=build_prompt_builder_schema(),
        max_output_tokens=360,
    )
    result["column_name"] = slugify_column_name(result.get("column_name", "") or goal)
    labels = result.get("labels") or []
    cleaned_labels: List[str] = []
    for label in labels:
        label = str(label).strip()
        if label and label not in cleaned_labels:
            cleaned_labels.append(label)
    if "Not Mentioned" not in cleaned_labels and len(cleaned_labels) <= 7:
        cleaned_labels.append("Not Mentioned")
    result["labels"] = cleaned_labels or ["Positive", "Negative", "Neutral", "Not Mentioned"]
    result["prompt"] = truncate_text(str(result.get("prompt", "")).replace("\n", " "), 120)
    result["why_it_matters"] = truncate_text(str(result.get("why_it_matters", "")).replace("\n", " "), 60)
    return result


def build_review_tagging_schema(prompt_definitions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    item_properties: Dict[str, Any] = {"review_id": {"type": "string"}}
    required = ["review_id"]
    for prompt in prompt_definitions:
        item_properties[prompt["column_name"]] = {
            "type": "string",
            "enum": list(prompt["labels"]),
        }
        required.append(prompt["column_name"])

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": item_properties,
                    "required": required,
                },
            }
        },
        "required": ["results"],
    }



def build_review_tagging_input(
    chunk_df: pd.DataFrame,
    prompt_definitions: Sequence[Dict[str, Any]],
) -> str:
    reviews_payload: List[Dict[str, Any]] = []
    for _, row in chunk_df.iterrows():
        reviews_payload.append(
            {
                "review_id": safe_text(row.get("review_id")),
                "rating": safe_int(row.get("rating"), 0) if pd.notna(row.get("rating")) else None,
                "title": truncate_text(row.get("title", ""), 200),
                "review_text": truncate_text(row.get("review_text", ""), 1000),
                "incentivized_review": safe_bool(row.get("incentivized_review"), False),
                "submission_date": safe_text(row.get("submission_date")),
                "content_locale": safe_text(row.get("content_locale")),
            }
        )

    prompt_payload = [
        {
            "column_name": prompt["column_name"],
            "prompt": prompt["prompt"],
            "labels": prompt["labels"],
        }
        for prompt in prompt_definitions
    ]
    return json.dumps({"prompt_definitions": prompt_payload, "reviews": reviews_payload}, ensure_ascii=False, indent=2)



def classify_review_chunk(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    chunk_df: pd.DataFrame,
    prompt_definitions: Sequence[Dict[str, Any]],
) -> pd.DataFrame:
    instructions = textwrap.dedent(
        """
        You are a deterministic review-tagging engine.
        For each review and each prompt definition, return exactly one allowed label.
        Base each label only on the supplied review content.
        Do not use product priors or guess beyond the evidence in the review.
        If the review does not mention the topic, use Not Mentioned when that label is available.
        """
    ).strip()
    result = call_openai_json(
        api_key=api_key,
        model=model,
        reasoning_effort=reasoning_effort,
        instructions=instructions,
        input_payload=build_review_tagging_input(chunk_df, prompt_definitions),
        schema_name="review_prompt_tagging",
        schema=build_review_tagging_schema(prompt_definitions),
        max_output_tokens=4200,
    )
    output_rows = result.get("results") or []
    output_df = pd.DataFrame(output_rows)
    if output_df.empty:
        raise ReviewDownloaderError("OpenAI returned no row-level prompt results.")
    output_df["review_id"] = output_df["review_id"].astype(str)

    expected_review_ids = set(chunk_df["review_id"].astype(str))
    returned_review_ids = set(output_df["review_id"].astype(str))
    if expected_review_ids != returned_review_ids:
        missing = sorted(expected_review_ids - returned_review_ids)
        extra = sorted(returned_review_ids - expected_review_ids)
        raise ReviewDownloaderError(
            "OpenAI returned an incomplete review-tagging batch. "
            f"Missing review_ids: {missing[:5]} | Extra review_ids: {extra[:5]}"
        )

    for prompt in prompt_definitions:
        column_name = prompt["column_name"]
        if column_name not in output_df.columns:
            raise ReviewDownloaderError(f"OpenAI omitted the expected Review Prompt column: {column_name}")

    return output_df



def merge_prompt_results_into_reviews(
    overall_df: pd.DataFrame,
    prompt_results_df: pd.DataFrame,
    prompt_definitions: Sequence[Dict[str, Any]],
) -> pd.DataFrame:
    updated = overall_df.copy()
    review_id_series = updated["review_id"].astype(str)
    result_lookup = prompt_results_df.set_index("review_id")

    for prompt in prompt_definitions:
        column_name = prompt["column_name"]
        if column_name not in updated.columns:
            updated[column_name] = pd.NA
        mapping = result_lookup[column_name].to_dict()
        new_values = review_id_series.map(mapping)
        updated[column_name] = new_values.where(new_values.notna(), updated[column_name])

    return updated





def summarize_prompt_results(
    prompt_results_df: pd.DataFrame,
    prompt_definitions: Sequence[Dict[str, Any]],
    source_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    merged = prompt_results_df.copy()
    merged["review_id"] = merged["review_id"].astype(str)
    if source_df is not None and not source_df.empty and "review_id" in source_df.columns:
        lookup = source_df[[col for col in ["review_id", "rating"] if col in source_df.columns]].copy()
        lookup["review_id"] = lookup["review_id"].astype(str)
        merged = merged.merge(lookup, on="review_id", how="left")

    rows: List[Dict[str, Any]] = []
    total = max(len(prompt_results_df), 1)
    for prompt in prompt_definitions:
        column_name = prompt["column_name"]
        for label in prompt["labels"]:
            subset = merged[merged[column_name] == label]
            rows.append(
                {
                    "column_name": column_name,
                    "display_name": prompt["display_name"],
                    "label": str(label),
                    "review_count": int(len(subset)),
                    "share": safe_pct(int(len(subset)), total),
                    "avg_rating": safe_mean(subset["rating"]) if "rating" in subset.columns else None,
                }
            )
    return pd.DataFrame(rows)



def summarize_single_prompt_view(view_df: pd.DataFrame, prompt: Dict[str, Any]) -> pd.DataFrame:
    total = max(len(view_df), 1)
    rows: List[Dict[str, Any]] = []
    column_name = prompt["column_name"]
    for label in prompt["labels"]:
        subset = view_df[view_df[column_name] == label] if column_name in view_df.columns else view_df.iloc[0:0]
        rows.append(
            {
                "label": label,
                "review_count": int(len(subset)),
                "share": safe_pct(int(len(subset)), total) if len(view_df) else 0.0,
                "avg_rating": safe_mean(subset["rating"]) if "rating" in subset.columns else None,
            }
        )
    summary_df = pd.DataFrame(rows)
    return summary_df[summary_df["review_count"] > 0].reset_index(drop=True)


def run_review_prompt_tagging(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    source_df: pd.DataFrame,
    prompt_definitions: Sequence[Dict[str, Any]],
    chunk_size: int,
) -> pd.DataFrame:
    if source_df.empty:
        raise ReviewDownloaderError("There are no reviews in the selected scope to classify.")

    chunks = list(range(0, len(source_df), chunk_size))
    progress = st.progress(0.0, text="Preparing AI review prompt run...")
    status = st.empty()
    outputs: List[pd.DataFrame] = []

    for index, start in enumerate(chunks, start=1):
        stop = start + chunk_size
        chunk_df = source_df.iloc[start:stop].copy()
        status.info(f"Classifying reviews {start + 1}-{min(stop, len(source_df))} of {len(source_df)}")
        chunk_result = classify_review_chunk(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            chunk_df=chunk_df,
            prompt_definitions=prompt_definitions,
        )
        outputs.append(chunk_result)
        progress.progress(index / len(chunks), text=f"Completed {index} of {len(chunks)} OpenAI requests")

    status.success(f"Finished tagging {len(source_df):,} reviews.")
    combined = pd.concat(outputs, ignore_index=True).drop_duplicates(subset=["review_id"], keep="last")
    return combined


# -----------------------------------------------------------------------------
# Export helpers
# -----------------------------------------------------------------------------


def prompt_definitions_to_df(prompt_definitions: Sequence[Dict[str, Any]], scope_label: str = "") -> pd.DataFrame:
    rows = []
    for prompt in prompt_definitions:
        rows.append(
            {
                "column_name": prompt["column_name"],
                "display_name": prompt["display_name"],
                "prompt": prompt["prompt"],
                "labels": ", ".join(prompt["labels"]),
                "scope": scope_label,
            }
        )
    return pd.DataFrame(rows)



def build_master_excel_file(
    summary: ReviewBatchSummary,
    reviews_df: pd.DataFrame,
    *,
    prompt_definitions: Optional[Sequence[Dict[str, Any]]] = None,
    prompt_summary_df: Optional[pd.DataFrame] = None,
    prompt_scope_label: str = "",
) -> bytes:
    metrics = compute_metrics(reviews_df)
    rating_df = rating_distribution(reviews_df)
    monthly_df = monthly_trend(reviews_df)

    summary_df = pd.DataFrame(
        [
            {
                "product_name": product_display_name(summary, reviews_df),
                "product_id": summary.product_id,
                "product_url": summary.product_url,
                "reviews_downloaded": summary.reviews_downloaded,
                "bazaarvoice_total_reviews": summary.total_reviews,
                "requests_needed": summary.requests_needed,
                "avg_rating": metrics.get("avg_rating"),
                "avg_rating_non_incentivized": metrics.get("avg_rating_non_incentivized"),
                "pct_low_star": metrics.get("pct_low_star"),
                "pct_incentivized": metrics.get("pct_incentivized"),
                "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        ]
    )

    prompt_defs_df = prompt_definitions_to_df(prompt_definitions or [], scope_label=prompt_scope_label)
    export_reviews_df = reviews_df.copy()

    priority_columns = [
        "review_id",
        "product_id",
        "rating",
        "incentivized_review",
        "is_recommended",
        "submission_time",
        "content_locale",
        "title",
        "review_text",
    ]
    prompt_columns = [prompt["column_name"] for prompt in (prompt_definitions or []) if prompt["column_name"] in export_reviews_df.columns]
    ordered_columns = [col for col in priority_columns + prompt_columns if col in export_reviews_df.columns]
    remaining_columns = [col for col in export_reviews_df.columns if col not in ordered_columns]
    export_reviews_df = export_reviews_df[ordered_columns + remaining_columns]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheets: Dict[str, pd.DataFrame] = {
            "Summary": summary_df,
            "Reviews": export_reviews_df,
            "RatingDistribution": rating_df,
            "ReviewVolume": monthly_df,
        }
        if prompt_definitions:
            sheets["ReviewPromptDefinitions"] = prompt_defs_df
        if prompt_summary_df is not None and not prompt_summary_df.empty:
            sheets["ReviewPromptSummary"] = prompt_summary_df

        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            autosize_worksheet(writer.sheets[sheet_name], df)

    output.seek(0)
    return output.getvalue()



def get_master_export_bundle(
    summary: ReviewBatchSummary,
    reviews_df: pd.DataFrame,
    prompt_artifacts: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt_defs = (prompt_artifacts or {}).get("definitions") or []
    prompt_summary_df = (prompt_artifacts or {}).get("summary_df")
    prompt_scope_label = (prompt_artifacts or {}).get("scope_label", "")

    artifact_key = json.dumps(
        {
            "product_id": summary.product_id,
            "review_count": int(len(reviews_df)),
            "columns": sorted(str(col) for col in reviews_df.columns),
            "prompt_signature": (prompt_artifacts or {}).get("definition_signature"),
            "prompt_scope": prompt_scope_label,
        },
        sort_keys=True,
    )
    bundle = st.session_state.get("master_export_bundle")
    if bundle and bundle.get("key") == artifact_key:
        return bundle

    excel_bytes = build_master_excel_file(
        summary,
        reviews_df,
        prompt_definitions=prompt_defs,
        prompt_summary_df=prompt_summary_df,
        prompt_scope_label=prompt_scope_label,
    )
    timestamp = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")
    bundle = {
        "key": artifact_key,
        "excel_bytes": excel_bytes,
        "excel_name": f"{summary.product_id}_review_workspace_{timestamp}.xlsx",
    }
    st.session_state["master_export_bundle"] = bundle
    return bundle



# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------


def inject_css() -> None:
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 1.1rem;
                padding-bottom: 2rem;
                max-width: 1480px;
            }
            .hero-card {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 18px;
                padding: 1.1rem 1.2rem;
                background: linear-gradient(180deg, rgba(250,250,252,0.96), rgba(245,247,250,0.96));
                margin-bottom: 1rem;
            }
            .hero-kicker {
                font-size: 0.78rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #6b7280;
                margin-bottom: 0.35rem;
            }
            .hero-title {
                font-size: 1.5rem;
                font-weight: 700;
                color: #16213e;
                margin-bottom: 0.3rem;
            }
            .hero-subtitle {
                color: #4b5563;
                font-size: 0.98rem;
                line-height: 1.4;
            }
            .metric-card {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 16px;
                padding: 0.95rem 1rem;
                background: rgba(250, 250, 252, 0.92);
                min-height: 146px;
                height: 146px;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
            }
            .metric-label {
                color: #6b7280;
                font-size: 0.82rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                margin-bottom: 0.45rem;
            }
            .metric-value {
                color: #16213e;
                font-size: 2rem;
                font-weight: 700;
                line-height: 1.05;
                margin-bottom: 0.3rem;
            }
            .metric-sub {
                color: #4b5563;
                font-size: 0.84rem;
                line-height: 1.3;
                min-height: 2.5em;
                overflow: hidden;
            }
            .section-subtitle {
                color: #6b7280;
                font-size: 0.96rem;
                margin-bottom: 0.85rem;
            }
            .review-shell {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 18px;
                padding: 1rem 1rem 0.9rem 1rem;
                background: rgba(255,255,255,0.98);
                margin-bottom: 0.9rem;
            }
            .report-card {
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 16px;
                padding: 1rem 1rem 0.9rem 1rem;
                background: rgba(250, 250, 252, 0.92);
                min-height: 180px;
            }
            .tiny-note {
                color: #6b7280;
                font-size: 0.85rem;
            }
            .thinking-overlay {
                position: fixed;
                inset: 0;
                background: rgba(15, 23, 42, 0.30);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 99999;
            }
            .thinking-card {
                width: min(430px, 92vw);
                background: rgba(255,255,255,0.98);
                border: 1px solid rgba(49, 51, 63, 0.12);
                border-radius: 20px;
                box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
                padding: 1.2rem 1.3rem;
                text-align: center;
            }
            .thinking-spinner {
                width: 40px;
                height: 40px;
                border: 4px solid rgba(17, 24, 39, 0.14);
                border-top-color: #111827;
                border-radius: 50%;
                margin: 0 auto 0.8rem auto;
                animation: thinking-spin 0.9s linear infinite;
            }
            .thinking-title {
                color: #16213e;
                font-weight: 700;
                font-size: 1.08rem;
                margin-bottom: 0.3rem;
            }
            .thinking-sub {
                color: #4b5563;
                font-size: 0.95rem;
                line-height: 1.35;
            }
            @keyframes thinking-spin {
                to { transform: rotate(360deg); }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )



def initialize_session_state() -> None:
    st.session_state.setdefault("analysis_dataset", None)
    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("master_export_bundle", None)
    st.session_state.setdefault("prompt_definitions_df", default_prompt_definitions())
    st.session_state.setdefault("prompt_builder_suggestion", None)
    st.session_state.setdefault("prompt_run_artifacts", None)
    st.session_state.setdefault("prompt_run_notice", None)
    st.session_state.setdefault("chat_scope_signature", None)
    st.session_state.setdefault("chat_scope_notice", None)
    st.session_state.setdefault("openai_model", DEFAULT_OPENAI_MODEL)
    st.session_state.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
    st.session_state.setdefault("prompt_batch_size", DEFAULT_PROMPT_BATCH_SIZE)
    st.session_state.setdefault("active_main_view", "Dashboard")
    st.session_state.setdefault("workspace_view_selector", st.session_state["active_main_view"])
    st.session_state.setdefault("review_explorer_sort", "Newest")
    st.session_state.setdefault("review_explorer_per_page", 20)
    st.session_state.setdefault("review_explorer_page", 1)
    st.session_state.setdefault("review_explorer_page_input", 1)
    st.session_state.setdefault("prompt_result_view", "")
    for prefix in ["ai_tab", "prompt_tab"]:
        st.session_state.setdefault(f"{prefix}_model", st.session_state["openai_model"])
        st.session_state.setdefault(f"{prefix}_reasoning_effort", st.session_state["reasoning_effort"])
        st.session_state.setdefault(f"{prefix}_prompt_batch_size", st.session_state["prompt_batch_size"])
        normalize_ai_settings_prefix(prefix)



RATING_FILTER_OPTIONS = [
    "All ratings",
    "1 star",
    "2 stars",
    "3 stars",
    "4 stars",
    "5 stars",
    "1-2 stars",
    "4-5 stars",
    "Custom",
]
RATING_FILTER_OPTIONS_SIMPLE = [
    "All ratings",
    "1 star",
    "2 stars",
    "3 stars",
    "4 stars",
    "5 stars",
    "1-2 stars",
    "4-5 stars",
]



def rating_values_for_mode(mode: str, custom_values: Optional[Sequence[int]] = None) -> List[int]:
    mapping = {
        "All ratings": [1, 2, 3, 4, 5],
        "1 star": [1],
        "2 stars": [2],
        "3 stars": [3],
        "4 stars": [4],
        "5 stars": [5],
        "1-2 stars": [1, 2],
        "4-5 stars": [4, 5],
    }
    if mode == "Custom":
        chosen = sorted({int(value) for value in (custom_values or [1, 2, 3, 4, 5])})
        return chosen or [1, 2, 3, 4, 5]
    return mapping.get(mode, [1, 2, 3, 4, 5])



def normalize_ai_settings_prefix(prefix: str) -> None:
    model = st.session_state.get(f"{prefix}_model", st.session_state.get("openai_model", DEFAULT_OPENAI_MODEL))
    if model not in MODEL_OPTIONS:
        fallback_model = st.session_state.get("openai_model", DEFAULT_OPENAI_MODEL)
        model = fallback_model if fallback_model in MODEL_OPTIONS else DEFAULT_OPENAI_MODEL
    effort = sanitize_reasoning_effort(
        model,
        st.session_state.get(f"{prefix}_reasoning_effort", st.session_state.get("reasoning_effort", DEFAULT_REASONING_EFFORT)),
    )
    batch_size = safe_int(
        st.session_state.get(f"{prefix}_prompt_batch_size", st.session_state.get("prompt_batch_size", DEFAULT_PROMPT_BATCH_SIZE)),
        DEFAULT_PROMPT_BATCH_SIZE,
    )
    batch_size = max(5, min(batch_size, 30))
    st.session_state[f"{prefix}_model"] = model
    st.session_state[f"{prefix}_reasoning_effort"] = effort
    st.session_state[f"{prefix}_prompt_batch_size"] = batch_size



def save_ai_settings_from_prefix(prefix: str) -> Dict[str, Any]:
    model = st.session_state.get(f"{prefix}_model", st.session_state.get("openai_model", DEFAULT_OPENAI_MODEL))
    if model not in MODEL_OPTIONS:
        model = DEFAULT_OPENAI_MODEL
    effort = sanitize_reasoning_effort(model, st.session_state.get(f"{prefix}_reasoning_effort", DEFAULT_REASONING_EFFORT))
    batch_size = safe_int(st.session_state.get(f"{prefix}_prompt_batch_size", DEFAULT_PROMPT_BATCH_SIZE), DEFAULT_PROMPT_BATCH_SIZE)
    batch_size = max(5, min(batch_size, 30))
    st.session_state["openai_model"] = model
    st.session_state["reasoning_effort"] = effort
    st.session_state["prompt_batch_size"] = batch_size
    return {
        "api_key": get_openai_api_key(),
        "model": model,
        "reasoning_effort": effort,
        "prompt_batch_size": batch_size,
    }



def render_ai_settings_controls(prefix: str, *, include_batch_size: bool = False, expander_label: str = "Advanced AI settings") -> Dict[str, Any]:
    api_key = get_openai_api_key()
    normalize_ai_settings_prefix(prefix)

    with st.expander(expander_label, expanded=False):
        if api_key:
            st.success("OpenAI API key loaded")
        else:
            st.warning("Add OPENAI_API_KEY to Streamlit secrets to enable AI features")
        st.selectbox(
            "Model",
            options=MODEL_OPTIONS,
            key=f"{prefix}_model",
            help="Use a GPT-5 reasoning model for grounded review analysis and row-level tagging.",
        )

        current_model = st.session_state.get(f"{prefix}_model", DEFAULT_OPENAI_MODEL)
        supported_efforts = reasoning_options_for_model(current_model)
        effort_key = f"{prefix}_reasoning_effort"
        if st.session_state.get(effort_key) not in supported_efforts:
            st.session_state[effort_key] = sanitize_reasoning_effort(current_model, st.session_state.get(effort_key))
        st.selectbox(
            "Reasoning effort",
            options=supported_efforts,
            key=effort_key,
            help="Higher effort can improve depth, while lower effort is faster and cheaper.",
        )
        if include_batch_size:
            st.slider(
                "Review Prompt batch size",
                min_value=5,
                max_value=30,
                step=1,
                key=f"{prefix}_prompt_batch_size",
                help="Larger batches reduce API calls but make each request heavier.",
            )

    return save_ai_settings_from_prefix(prefix)



def show_thinking_overlay(message: str):
    placeholder = st.empty()
    placeholder.markdown(
        f"""
        <div class="thinking-overlay">
            <div class="thinking-card">
                <div class="thinking-spinner"></div>
                <div class="thinking-title">OpenAI is working</div>
                <div class="thinking-sub">{message}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return placeholder



def get_scroll_container(height: int = 520, border: bool = True):
    try:
        return st.container(height=height, border=border)
    except TypeError:
        return st.container(border=border)



def render_sidebar_controls(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    api_key = get_openai_api_key()
    selected_ratings = [1, 2, 3, 4, 5]
    selected_products: List[str] = []
    review_source_mode = "All reviews"
    selected_locales: List[str] = []
    recommendation_mode = "All"
    date_range: Optional[Tuple[date, date]] = None
    text_query = ""

    with st.sidebar:
        st.header("Review filters")
        st.caption("These filters drive the dashboard, review explorer, AI analyst, and Review Prompt.")
        if df is None:
            st.info("Build a workspace from a product URL or uploaded review file to unlock the filters.")
        else:
            options = build_filter_options(df)
            rating_mode = st.selectbox("Ratings", options=RATING_FILTER_OPTIONS, index=0, key="sidebar_rating_mode")
            custom_ratings = None
            if rating_mode == "Custom":
                custom_ratings = st.multiselect(
                    "Custom ratings",
                    options=options["ratings"],
                    default=options["ratings"],
                    key="sidebar_custom_ratings",
                )
            selected_ratings = rating_values_for_mode(rating_mode, custom_ratings)
            review_source_mode = st.selectbox(
                "Review source",
                options=["All reviews", "Organic only", "Incentivized only"],
                index=0,
                key="sidebar_review_source",
            )
            if options["product_groups"] and len(options["product_groups"]) > 1:
                selected_products = st.multiselect(
                    "SKU / product ID",
                    options=options["product_groups"],
                    default=[],
                    key="sidebar_product_groups",
                )
            if options["locales"]:
                selected_locales = st.multiselect(
                    "Market / locale",
                    options=options["locales"],
                    default=[],
                    key="sidebar_locales",
                )
            recommendation_mode = st.selectbox(
                "Recommendation status",
                options=["All", "Recommended only", "Not recommended only"],
                index=0,
                key="sidebar_recommendation",
            )
            if options["min_date"] and options["max_date"]:
                picked = st.date_input(
                    "Submission date range",
                    value=(options["min_date"], options["max_date"]),
                    min_value=options["min_date"],
                    max_value=options["max_date"],
                    key="sidebar_date_range",
                )
                if isinstance(picked, tuple) and len(picked) == 2:
                    date_range = (picked[0], picked[1])
            text_query = st.text_input(
                "Text contains",
                value="",
                key="sidebar_text_query",
                placeholder="noise, basket, capacity, smell...",
            )
        st.divider()
        if api_key:
            st.caption("OpenAI analyst is connected through Streamlit secrets.")
        else:
            st.caption("Add OPENAI_API_KEY to Streamlit secrets to unlock AI features.")

    return {
        "selected_ratings": selected_ratings,
        "selected_products": selected_products,
        "review_source_mode": review_source_mode,
        "selected_locales": selected_locales,
        "recommendation_mode": recommendation_mode,
        "date_range": date_range,
        "text_query": text_query,
        "openai_api_key": api_key,
        "openai_model": st.session_state.get("openai_model", DEFAULT_OPENAI_MODEL),
        "reasoning_effort": st.session_state.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
        "prompt_batch_size": int(st.session_state.get("prompt_batch_size", DEFAULT_PROMPT_BATCH_SIZE)),
    }



def map_review_source_mode(source_mode: str) -> str:
    mapping = {
        "All reviews": "All reviews",
        "Organic only": "Non-incentivized only",
        "Incentivized only": "Incentivized only",
    }
    return mapping.get(source_mode, "All reviews")



def describe_current_filters(
    *,
    selected_ratings: Sequence[int],
    selected_products: Sequence[str],
    review_source_mode: str,
    selected_locales: Sequence[str],
    recommendation_mode: str,
    date_range: Optional[Tuple[date, date]],
    text_query: str,
) -> str:
    parts: List[str] = []
    if selected_ratings and set(selected_ratings) != {1, 2, 3, 4, 5}:
        parts.append("ratings=" + ", ".join(str(item) for item in selected_ratings))
    if selected_products:
        preview = ", ".join(selected_products[:4]) + ("..." if len(selected_products) > 4 else "")
        parts.append("sku/product=" + preview)
    if review_source_mode != "All reviews":
        parts.append(f"source={review_source_mode.lower()}")
    if selected_locales:
        parts.append("locales=" + ", ".join(selected_locales))
    if recommendation_mode != "All":
        parts.append(f"recommendation={recommendation_mode.lower()}")
    if date_range and date_range[0] and date_range[1]:
        parts.append(f"dates={date_range[0]} to {date_range[1]}")
    if text_query.strip():
        parts.append(f'text contains="{text_query.strip()}"')
    return "; ".join(parts) if parts else "No active filters"



def render_metric_card(label: str, value: str, subtext: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-sub">{subtext}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def render_workspace_header(
    summary: ReviewBatchSummary,
    overall_df: pd.DataFrame,
    prompt_artifacts: Optional[Dict[str, Any]],
    *,
    source_type: str,
    source_label: str,
) -> None:
    bundle = get_master_export_bundle(summary, overall_df, prompt_artifacts)
    product_name = product_display_name(summary, overall_df)
    organic_count = int((~overall_df["incentivized_review"].fillna(False)).sum()) if not overall_df.empty else 0
    unique_products = int(overall_df["product_or_sku"].dropna().astype(str).nunique()) if "product_or_sku" in overall_df.columns else 0

    if source_type == "uploaded":
        subtitle = f"Source: {source_label} | {summary.reviews_downloaded:,} reviews mapped | {organic_count:,} organic reviews"
        if unique_products > 1:
            subtitle += f" | {unique_products:,} SKUs / product IDs"
    else:
        subtitle = f"Product ID {summary.product_id} | {summary.reviews_downloaded:,} reviews downloaded | {organic_count:,} organic reviews | {summary.requests_needed} Bazaarvoice requests"

    st.markdown(
        f"""
        <div class="hero-card">
            <div class="hero-kicker">Review workspace ready</div>
            <div class="hero-title">{product_name}</div>
            <div class="hero-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    action_cols = st.columns([1.2, 1.2, 4])
    action_cols[0].download_button(
        label="Download all reviews",
        data=bundle["excel_bytes"],
        file_name=bundle["excel_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    if action_cols[1].button("Reset workspace", use_container_width=True):
        st.session_state["analysis_dataset"] = None
        st.session_state["chat_messages"] = []
        st.session_state["chat_scope_signature"] = None
        st.session_state["chat_scope_notice"] = None
        st.session_state["master_export_bundle"] = None
        st.session_state["prompt_run_artifacts"] = None
        st.session_state["prompt_run_notice"] = None
        st.rerun()
    action_cols[2].caption(
        "The workbook includes the full review table, rating distribution, review volume over time, average rating over time, and any Review Prompt columns generated so far."
    )



def render_top_metrics(overall_df: pd.DataFrame, filtered_df: pd.DataFrame) -> None:
    metrics = compute_metrics(filtered_df)
    cards = [
        ("Reviews in view", f"{metrics['review_count']:,}", f"Loaded base · {len(overall_df):,} reviews"),
        ("Avg rating", format_metric_number(metrics["avg_rating"]), "Filtered review set"),
        (
            "Avg rating (organic)",
            format_metric_number(metrics["avg_rating_non_incentivized"]),
            f"Organic base · {metrics['non_incentivized_count']:,} reviews",
        ),
        ("% 1-2 star", format_pct(metrics["pct_low_star"]), f"Low-star base · {metrics['low_star_count']:,} reviews"),
        ("% incentivized", format_pct(metrics["pct_incentivized"]), "Share of current view"),
    ]
    cols = st.columns(len(cards))
    for col, (label, value, subtext) in zip(cols, cards):
        with col:
            render_metric_card(label, value, subtext)



def summarize_group_avg_rating(df: pd.DataFrame, group_column: str, top_n: int = 12) -> pd.DataFrame:
    if df.empty or group_column not in df.columns:
        return pd.DataFrame(columns=[group_column, "review_count", "avg_rating"])
    working = df.copy()
    working[group_column] = working[group_column].fillna("").astype(str).str.strip()
    working = working[working[group_column].ne("")]
    if working.empty:
        return pd.DataFrame(columns=[group_column, "review_count", "avg_rating"])
    grouped = (
        working.groupby(group_column, as_index=False)
        .agg(review_count=("review_id", "count"), avg_rating=("rating", "mean"))
        .sort_values(["review_count", "avg_rating"], ascending=[False, False])
        .head(top_n)
    )
    return grouped



def available_time_series_dimensions(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    options: Dict[str, Optional[str]] = {"Overall only": None}
    for label, column in [
        ("Market / locale", "content_locale"),
        ("Retailer / POS", "retailer"),
        ("Age group", "age_group"),
        ("SKU / product ID", "product_or_sku"),
    ]:
        if column not in df.columns:
            continue
        values = {
            str(value).strip()
            for value in df[column].dropna().astype(str)
            if str(value).strip() and str(value).strip().lower() not in {"nan", "none", "unknown"}
        }
        if len(values) > 1:
            options[label] = column
    return options



def normalize_breakout_value(value: Any) -> str:
    cleaned = safe_text(value)
    if not cleaned or cleaned.lower() in {"nan", "none"}:
        return "Unknown"
    return cleaned



def prepare_avg_rating_over_time(
    df: pd.DataFrame,
    *,
    group_column: Optional[str],
    trend_mode: str,
    smoothing_days: int,
    top_groups: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df.empty:
        empty = pd.DataFrame(columns=["day", "group_value", "review_count", "rating_sum", "avg_rating", "display_rating"])
        return empty, empty, pd.DataFrame(columns=["day", "review_count"])

    working = df.dropna(subset=["submission_time", "rating"]).copy()
    if working.empty:
        empty = pd.DataFrame(columns=["day", "group_value", "review_count", "rating_sum", "avg_rating", "display_rating"])
        return empty, empty, pd.DataFrame(columns=["day", "review_count"])

    working["day"] = working["submission_time"].dt.floor("D")
    if group_column:
        working["group_value"] = working[group_column].map(normalize_breakout_value)
        ranking = (
            working.groupby("group_value")
            .size()
            .sort_values(ascending=False)
        )
        selected_groups = ranking.head(max(int(top_groups), 1)).index.tolist()
        working = working[working["group_value"].isin(selected_groups)].copy()
    else:
        working["group_value"] = "Selected reviews"
        selected_groups = ["Selected reviews"]

    if working.empty:
        empty = pd.DataFrame(columns=["day", "group_value", "review_count", "rating_sum", "avg_rating", "display_rating"])
        return empty, empty, pd.DataFrame(columns=["day", "review_count"])

    daily = (
        working.groupby(["day", "group_value"], as_index=False)
        .agg(review_count=("review_id", "count"), rating_sum=("rating", "sum"), avg_rating=("rating", "mean"))
    )
    daily_volume = working.groupby("day", as_index=False).agg(review_count=("review_id", "count"))
    full_days = pd.date_range(daily["day"].min(), daily["day"].max(), freq="D")

    def _series_for_group(source_df: pd.DataFrame, group_value: str) -> pd.DataFrame:
        group_df = source_df[source_df["group_value"] == group_value].set_index("day").reindex(full_days)
        group_df.index.name = "day"
        group_df["group_value"] = group_value
        group_df["review_count"] = pd.to_numeric(group_df["review_count"], errors="coerce").fillna(0).astype(int)
        group_df["rating_sum"] = pd.to_numeric(group_df["rating_sum"], errors="coerce").fillna(0.0)
        denom = group_df["review_count"].replace(0, pd.NA)
        group_df["avg_rating"] = group_df["rating_sum"] / denom
        if trend_mode == "Rolling average":
            window = max(int(smoothing_days), 1)
            rolling_count = group_df["review_count"].rolling(window=window, min_periods=1).sum().replace(0, pd.NA)
            rolling_sum = group_df["rating_sum"].rolling(window=window, min_periods=1).sum()
            group_df["display_rating"] = rolling_sum / rolling_count
        else:
            cumulative_count = group_df["review_count"].cumsum().replace(0, pd.NA)
            cumulative_sum = group_df["rating_sum"].cumsum()
            group_df["display_rating"] = cumulative_sum / cumulative_count
        return group_df.reset_index()

    breakout_frames = [_series_for_group(daily, group_value) for group_value in selected_groups]
    breakout_df = pd.concat(breakout_frames, ignore_index=True) if breakout_frames else pd.DataFrame()

    overall_daily = (
        working.groupby("day", as_index=False)
        .agg(review_count=("review_id", "count"), rating_sum=("rating", "sum"), avg_rating=("rating", "mean"))
    )
    overall_daily["group_value"] = "Overall"
    overall_df = _series_for_group(overall_daily, "Overall")
    return breakout_df, overall_df, daily_volume



def build_avg_rating_over_time_figure(
    breakout_df: pd.DataFrame,
    overall_df: pd.DataFrame,
    volume_df: pd.DataFrame,
    *,
    title: str,
    show_overall: bool,
    show_volume_bars: bool,
    zoom_mode: str,
) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if show_volume_bars and not volume_df.empty:
        fig.add_trace(
            go.Bar(
                x=volume_df["day"],
                y=volume_df["review_count"],
                name="Daily volume",
                opacity=0.15,
                hovertemplate="%{x|%Y-%m-%d}<br>Reviews: %{y}<extra></extra>",
            ),
            secondary_y=True,
        )

    for group_value in breakout_df["group_value"].dropna().astype(str).unique().tolist():
        group_df = breakout_df[breakout_df["group_value"] == group_value].copy()
        fig.add_trace(
            go.Scatter(
                x=group_df["day"],
                y=group_df["display_rating"],
                mode="lines",
                name=group_value,
                hovertemplate="%{x|%Y-%m-%d}<br>Avg rating: %{y:.2f}<extra></extra>",
            ),
            secondary_y=False,
        )

    if show_overall and not overall_df.empty and (breakout_df["group_value"].nunique() > 1 or (breakout_df["group_value"].iloc[0] if not breakout_df.empty else "") != "Selected reviews"):
        fig.add_trace(
            go.Scatter(
                x=overall_df["day"],
                y=overall_df["display_rating"],
                mode="lines",
                name="Overall",
                line={"width": 4},
                hovertemplate="%{x|%Y-%m-%d}<br>Overall avg: %{y:.2f}<extra></extra>",
            ),
            secondary_y=False,
        )

    all_y = pd.concat([breakout_df.get("display_rating", pd.Series(dtype="float64")), overall_df.get("display_rating", pd.Series(dtype="float64"))], ignore_index=True).dropna()
    y_range = None
    if zoom_mode == "Zoomed-in" and not all_y.empty:
        y_min = max(1.0, math.floor((float(all_y.min()) - 0.05) * 20) / 20)
        y_max = min(5.0, math.ceil((float(all_y.max()) + 0.05) * 20) / 20)
        if y_max - y_min < 0.15:
            y_min = max(1.0, y_min - 0.1)
            y_max = min(5.0, y_max + 0.1)
        y_range = [y_min, y_max]
    elif zoom_mode == "Full scale":
        y_range = [1, 5]

    fig.update_layout(
        title=title,
        margin=dict(l=24, r=24, t=65, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
        bargap=0.05,
    )
    fig.update_yaxes(title_text="Average rating", range=y_range, secondary_y=False)
    fig.update_yaxes(title_text="Reviews/day", showgrid=False, secondary_y=True)
    fig.update_xaxes(title_text="Date")
    return fig



def render_dashboard(filtered_df: pd.DataFrame) -> None:
    st.subheader("Dashboard")
    st.markdown(
        '<div class="section-subtitle">A sharper decision view for internal SharkNinja teams: lead with average rating over time, then scan rating mix, review volume, and the strongest performance splits.</div>',
        unsafe_allow_html=True,
    )

    chart_scope = st.radio(
        "Dashboard scope",
        options=["All matching reviews", "Organic only"],
        horizontal=True,
        key="dashboard_chart_scope",
    )
    chart_df = filtered_df.copy()
    if chart_scope == "Organic only":
        chart_df = chart_df[~chart_df["incentivized_review"].fillna(False)].reset_index(drop=True)

    if chart_df.empty:
        st.info("No reviews match the current dashboard scope.")
        return

    dim_options = available_time_series_dimensions(chart_df)
    with st.container(border=True):
        control_cols = st.columns([1.15, 1.2, 0.9, 0.8, 0.9, 0.9, 0.95])
        trend_mode = control_cols[0].selectbox("Trend", options=["Cumulative average", "Rolling average"], index=0, key="dash_trend_mode")
        breakout_label = control_cols[1].selectbox("Breakout", options=list(dim_options.keys()), index=1 if len(dim_options) > 1 else 0, key="dash_breakout")
        smoothing_label = control_cols[2].selectbox("Smoothing", options=["7-day", "14-day", "30-day"], index=0, key="dash_smoothing")
        top_groups = control_cols[3].selectbox("Top lines", options=[4, 5, 6, 8], index=2, key="dash_top_groups")
        show_overall = control_cols[4].checkbox("Show overall", value=True, key="dash_show_overall")
        show_volume_bars = control_cols[5].checkbox("Show volume bars", value=True, key="dash_show_volume")
        zoom_mode = control_cols[6].radio("Y-axis view", options=["Zoomed-in", "Full scale"], index=0, horizontal=True, key="dash_zoom_mode")
        st.caption("The primary chart uses weighted averages from the selected reviews. Volume bars show how many reviews landed on each day.")

        smoothing_days = int(smoothing_label.split("-")[0])
        breakout_df, overall_line_df, daily_volume_df = prepare_avg_rating_over_time(
            chart_df,
            group_column=dim_options.get(breakout_label),
            trend_mode=trend_mode,
            smoothing_days=smoothing_days,
            top_groups=int(top_groups),
        )

        if breakout_df.empty:
            st.info("No dated ratings are available for the average-rating trend.")
        else:
            title = f"{trend_mode} ★ over time"
            if dim_options.get(breakout_label):
                title += f" by {breakout_label}"
            fig = build_avg_rating_over_time_figure(
                breakout_df,
                overall_line_df,
                daily_volume_df,
                title=title,
                show_overall=show_overall,
                show_volume_bars=show_volume_bars,
                zoom_mode=zoom_mode,
            )
            st.plotly_chart(fig, use_container_width=True)

    rating_df = rating_distribution(chart_df)
    rating_df["rating_label"] = rating_df["rating"].map(lambda value: f"{int(value)}★")
    rating_df["count_pct_label"] = rating_df.apply(lambda row: f"{int(row['review_count']):,} · {format_pct(row['share'])}", axis=1)
    monthly_df = monthly_trend(chart_df)

    chart_cols = st.columns([1.05, 1.15])
    with chart_cols[0]:
        with st.container(border=True):
            fig = px.bar(
                rating_df,
                x="rating_label",
                y="review_count",
                text="count_pct_label",
                title="Rating distribution",
                category_orders={"rating_label": ["1★", "2★", "3★", "4★", "5★"]},
                hover_data={"share": ':.1%', "review_count": True},
            )
            fig.update_traces(textposition="outside", cliponaxis=False)
            fig.update_layout(margin=dict(l=24, r=24, t=60, b=20), xaxis_title="Star rating", yaxis_title="Review count")
            st.plotly_chart(fig, use_container_width=True)
    with chart_cols[1]:
        with st.container(border=True):
            if monthly_df.empty:
                st.info("No dated reviews are available for the review-volume chart.")
            else:
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(
                    go.Bar(x=monthly_df["month_start"], y=monthly_df["review_count"], name="Review count", opacity=0.65),
                    secondary_y=False,
                )
                fig.add_trace(
                    go.Scatter(x=monthly_df["month_start"], y=monthly_df["avg_rating"], name="Avg rating", mode="lines+markers"),
                    secondary_y=True,
                )
                fig.update_layout(title="Review volume over time", margin=dict(l=24, r=24, t=60, b=20), hovermode="x unified")
                fig.update_xaxes(title_text="Month")
                fig.update_yaxes(title_text="Review count", secondary_y=False)
                fig.update_yaxes(title_text="Avg rating", range=[1, 5], secondary_y=True)
                st.plotly_chart(fig, use_container_width=True)

    lower_cols = st.columns(2)
    sku_df = summarize_group_avg_rating(chart_df, "product_or_sku", top_n=12)
    with lower_cols[0]:
        with st.container(border=True):
            if len(sku_df) <= 1:
                st.info("Average rating by SKU / product ID will appear when multiple products are in scope.")
            else:
                sorted_sku = sku_df.sort_values(["avg_rating", "review_count"], ascending=[True, True])
                fig = px.bar(
                    sorted_sku,
                    x="avg_rating",
                    y="product_or_sku",
                    orientation="h",
                    text=sorted_sku.apply(lambda row: f"{row['avg_rating']:.2f} · {int(row['review_count'])}", axis=1),
                    title="Average rating by SKU / product ID",
                    hover_data={"review_count": True, "avg_rating": ':.2f'},
                )
                fig.update_layout(margin=dict(l=24, r=24, t=60, b=20), xaxis_title="Average rating", yaxis_title="")
                fig.update_xaxes(range=[0, 5])
                st.plotly_chart(fig, use_container_width=True)
    age_df = summarize_group_avg_rating(chart_df, "age_group", top_n=12)
    with lower_cols[1]:
        with st.container(border=True):
            if len(age_df) <= 1:
                st.info("Average rating by age group will appear when age-group data is available in the review source.")
            else:
                sorted_age = age_df.sort_values(["avg_rating", "review_count"], ascending=[True, True])
                fig = px.bar(
                    sorted_age,
                    x="avg_rating",
                    y="age_group",
                    orientation="h",
                    text=sorted_age.apply(lambda row: f"{row['avg_rating']:.2f} · {int(row['review_count'])}", axis=1),
                    title="Average rating by age group",
                    hover_data={"review_count": True, "avg_rating": ':.2f'},
                )
                fig.update_layout(margin=dict(l=24, r=24, t=60, b=20), xaxis_title="Average rating", yaxis_title="")
                fig.update_xaxes(range=[0, 5])
                st.plotly_chart(fig, use_container_width=True)



def sort_reviews_for_explorer(df: pd.DataFrame, sort_mode: str) -> pd.DataFrame:
    working = df.copy()
    if sort_mode == "Newest":
        return working.sort_values(["submission_time", "review_id"], ascending=[False, False], na_position="last")
    if sort_mode == "Oldest":
        return working.sort_values(["submission_time", "review_id"], ascending=[True, True], na_position="last")
    if sort_mode == "Highest rating":
        return working.sort_values(["rating", "submission_time"], ascending=[False, False], na_position="last")
    if sort_mode == "Lowest rating":
        return working.sort_values(["rating", "submission_time"], ascending=[True, False], na_position="last")
    if sort_mode == "Most helpful":
        return working.sort_values(["total_positive_feedback_count", "submission_time"], ascending=[False, False], na_position="last")
    if sort_mode == "Longest":
        return working.sort_values(["review_length_words", "submission_time"], ascending=[False, False], na_position="last")
    return working



def render_review_card(row: pd.Series) -> None:
    rating_value = safe_int(row.get("rating"), 0) if pd.notna(row.get("rating")) else 0
    filled_stars = "&#9733;" * max(0, min(rating_value, 5))
    empty_stars = "&#9734;" * max(0, 5 - rating_value)
    star_label = f"{rating_value}/5" if rating_value else "No rating"
    title = safe_text(row.get("title"), "No title") or "No title"
    review_text = safe_text(row.get("review_text"), "No written review text.") or "No written review text."

    meta_bits = []
    submission_date = safe_text(row.get("submission_date"))
    content_locale = safe_text(row.get("content_locale"))
    retailer = safe_text(row.get("retailer"))
    product_or_sku = safe_text(row.get("product_or_sku"))
    if submission_date:
        meta_bits.append(submission_date)
    if content_locale:
        meta_bits.append(content_locale)
    if retailer:
        meta_bits.append(retailer)
    if product_or_sku:
        meta_bits.append(product_or_sku)

    chips = ["Organic" if not safe_bool(row.get("incentivized_review"), False) else "Incentivized"]
    recommended_value = row.get("is_recommended")
    if not is_missing_value(recommended_value):
        if safe_bool(recommended_value, False):
            chips.append("Recommended")
        else:
            chips.append("Not recommended")
    if safe_bool(row.get("has_photos"), False):
        chips.append(f"Photos: {safe_int(row.get('photos_count'), 0)}")

    with st.container(border=True):
        top_cols = st.columns([4.6, 1.6])
        with top_cols[0]:
            st.markdown(f"<div class='tiny-note'>{filled_stars}{empty_stars} {star_label}</div>", unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            if meta_bits:
                st.caption(" | ".join(meta_bits))
        with top_cols[1]:
            st.caption(" | ".join(chips))
        st.write(review_text)
        footer_bits = []
        review_id = safe_text(row.get("review_id"))
        user_location = safe_text(row.get("user_location"))
        if review_id:
            footer_bits.append(f"Review ID: {review_id}")
        if user_location:
            footer_bits.append(user_location)
        if footer_bits:
            st.caption(" | ".join(footer_bits))



def render_review_explorer(
    *,
    summary: ReviewBatchSummary,
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    prompt_artifacts: Optional[Dict[str, Any]],
) -> None:
    st.subheader("Review explorer")
    st.markdown(
        f'<div class="section-subtitle">A cleaner website-style stream for the current filter set. Showing {len(filtered_df):,} reviews out of {len(overall_df):,} loaded.</div>',
        unsafe_allow_html=True,
    )

    bundle = get_master_export_bundle(summary, overall_df, prompt_artifacts)
    top_controls = st.columns([1.3, 1.4, 1.0, 2.0])
    top_controls[0].download_button(
        label="Download all reviews",
        data=bundle["excel_bytes"],
        file_name=bundle["excel_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="review_explorer_download_all",
    )
    sort_mode = top_controls[1].selectbox(
        "Sort reviews",
        options=["Newest", "Oldest", "Highest rating", "Lowest rating", "Most helpful", "Longest"],
        key="review_explorer_sort",
    )
    per_page = int(
        top_controls[2].selectbox(
            "Reviews per page",
            options=[10, 20, 30, 50],
            key="review_explorer_per_page",
        )
    )
    top_controls[3].caption("Use the sidebar filters to narrow the review stream, then page through the results without getting bumped back to Dashboard.")

    ordered_df = sort_reviews_for_explorer(filtered_df, sort_mode).reset_index(drop=True)
    if ordered_df.empty:
        st.info("No reviews match the current filters.")
        return

    page_count = max(1, math.ceil(len(ordered_df) / max(per_page, 1)))
    current_page = int(st.session_state.get("review_explorer_page", 1))
    current_page = max(1, min(current_page, page_count))

    pager_cols = st.columns([0.9, 0.9, 2.15, 1.05, 0.9, 0.9])
    if pager_cols[0].button("⏮ First", use_container_width=True, disabled=current_page <= 1, key="reviews_first_page"):
        current_page = 1
    if pager_cols[1].button("← Prev", use_container_width=True, disabled=current_page <= 1, key="reviews_prev_page"):
        current_page = max(1, current_page - 1)
    pager_cols[2].markdown(
        f"<div style='text-align:center; font-weight:700; padding-top:0.6rem;'>Page {current_page} of {page_count:,} • Showing {(current_page - 1) * per_page + 1:,}-{min(current_page * per_page, len(ordered_df)):,} of {len(ordered_df):,}</div>",
        unsafe_allow_html=True,
    )
    if st.session_state.get("review_explorer_page_input") != current_page:
        st.session_state["review_explorer_page_input"] = current_page
    current_page = int(
        pager_cols[3].number_input(
            "Page",
            min_value=1,
            max_value=page_count,
            value=current_page,
            step=1,
            key="review_explorer_page_input",
            label_visibility="collapsed",
        )
    )
    if pager_cols[4].button("Next →", use_container_width=True, disabled=current_page >= page_count, key="reviews_next_page"):
        current_page = min(page_count, current_page + 1)
    if pager_cols[5].button("Last ⏭", use_container_width=True, disabled=current_page >= page_count, key="reviews_last_page"):
        current_page = page_count

    st.session_state["review_explorer_page"] = max(1, min(current_page, page_count))
    start = (st.session_state["review_explorer_page"] - 1) * per_page
    end = start + per_page
    page_df = ordered_df.iloc[start:end]

    for _, row in page_df.iterrows():
        render_review_card(row)



def render_ai_tab(
    *,
    settings: Dict[str, Any],
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
) -> None:
    st.subheader("AI — Product & Consumer Insights")
    st.markdown(
        '<div class="section-subtitle">Ask anything. The assistant is grounded in the currently filtered review text and keeps one continuous chat so context does not drift.</div>',
        unsafe_allow_html=True,
    )

    if filtered_df.empty:
        st.info("The current filters return no reviews. Adjust the filters before using AI analyst.")
        return

    scope_signature = json.dumps(
        {
            "product_id": summary.product_id,
            "filter_description": filter_description,
            "review_count": int(len(filtered_df)),
            "source_type": st.session_state.get("analysis_dataset", {}).get("source_type", "bazaarvoice"),
        },
        sort_keys=True,
    )
    if st.session_state.get("chat_scope_signature") != scope_signature:
        if st.session_state.get("chat_messages"):
            st.session_state["chat_messages"] = []
            st.session_state["chat_scope_notice"] = "AI chat was cleared so it stays aligned with the latest filtered review scope."
        st.session_state["chat_scope_signature"] = scope_signature

    notice = st.session_state.pop("chat_scope_notice", None)
    if notice:
        st.info(notice)

    with st.container(border=True):
        status_cols = st.columns([1.4, 1.1, 1.5])
        with status_cols[0]:
            st.markdown("**🟢 Remote AI ready**" if get_openai_api_key() else "**AI setup needed**")
            st.caption("The analyst prioritizes the review text, then uses the filtered metrics for context.")
        with status_cols[1]:
            st.metric("Reviews in scope", f"{len(filtered_df):,}")
            organic_reviews = int((~filtered_df["incentivized_review"].fillna(False)).sum())
            st.caption(f"Organic reviews · {organic_reviews:,}")
        with status_cols[2]:
            ai_runtime = render_ai_settings_controls("ai_tab", include_batch_size=False, expander_label="Advanced AI settings")
            st.caption(f"Model: {ai_runtime['model']} · Reasoning: {ai_runtime['reasoning_effort']}")

    api_key = ai_runtime.get("api_key")
    if not api_key:
        st.warning("Add OPENAI_API_KEY to Streamlit secrets to enable preset reports and chat.")
        st.code('OPENAI_API_KEY = "sk-..."', language="toml")
        return

    quick_actions = {
        "Executive summary": {
            "prompt": "Create a concise executive summary of the filtered reviews. Lead with the biggest strengths, biggest risks, key consumer insight, and the top 3 actions.",
            "help": "Leadership-ready readout with strengths, risks, and top actions.",
            "persona": None,
        },
        "Product Development": {
            "prompt": PERSONAS["Product Development"]["prompt"],
            "help": PERSONAS["Product Development"]["blurb"],
            "persona": "Product Development",
        },
        "Quality Engineer": {
            "prompt": PERSONAS["Quality Engineer"]["prompt"],
            "help": PERSONAS["Quality Engineer"]["blurb"],
            "persona": "Quality Engineer",
        },
        "Consumer Insights": {
            "prompt": PERSONAS["Consumer Insights"]["prompt"],
            "help": PERSONAS["Consumer Insights"]["blurb"],
            "persona": "Consumer Insights",
        },
    }

    quick_trigger: Optional[Tuple[Optional[str], str, str]] = None
    with st.container(border=True):
        st.markdown("**Quick reports**")
        action_cols = st.columns(4)
        for col, (label, config) in zip(action_cols, quick_actions.items()):
            if col.button(label, use_container_width=True, help=config["help"], key=f"ai_quick_{slugify_column_name(label, fallback='quick')}"):
                quick_trigger = (config["persona"], label, config["prompt"])
        st.caption("Each report is grounded in the filtered review text and should cite review IDs for important claims.")

    chat_container = get_scroll_container(height=560, border=True)
    with chat_container:
        if not st.session_state["chat_messages"]:
            st.info(
                "Start with a quick report above, or ask a direct question such as: What are the biggest improvement opportunities? What is driving 1-star reviews? What language should marketing avoid or lean into?"
            )
        for message in st.session_state["chat_messages"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    helper_cols = st.columns([1.8, 1.2, 1])
    helper_cols[0].caption(f"Current scope: {filter_description}")
    if helper_cols[1].button("Clear chat", use_container_width=True, key="ai_clear_chat"):
        st.session_state["chat_messages"] = []
        st.rerun()
    helper_cols[2].caption("Chat input stays pinned at the bottom.")

    user_message = st.chat_input(
        "Ask about complaint drivers, product opportunities, quality risks, sentiment drivers, unmet needs, or voice-of-customer themes...",
        key="ai_chat_input",
    )

    prompt_to_send: Optional[str] = None
    visible_user_message: Optional[str] = None
    persona_name: Optional[str] = None
    if quick_trigger:
        persona_name, visible_user_message, prompt_to_send = quick_trigger
    elif user_message:
        prompt_to_send = user_message
        visible_user_message = user_message

    if prompt_to_send and visible_user_message:
        prior_chat_history = list(st.session_state["chat_messages"])
        st.session_state["chat_messages"].append({"role": "user", "content": visible_user_message})
        overlay = show_thinking_overlay("Reviewing the filtered review text and building a grounded answer...")
        try:
            answer = call_openai_analyst(
                api_key=api_key,
                model=ai_runtime["model"],
                reasoning_effort=ai_runtime["reasoning_effort"],
                question=prompt_to_send,
                overall_df=overall_df,
                filtered_df=filtered_df,
                summary=summary,
                filter_description=filter_description,
                chat_history=prior_chat_history,
                persona_name=persona_name,
            )
            if persona_name:
                answer = f"## {persona_name} report\n\n{answer}"
        except Exception as exc:
            answer = f"OpenAI request failed: {exc}"
        finally:
            overlay.empty()
        st.session_state["chat_messages"].append({"role": "assistant", "content": answer})
        st.rerun()



def render_review_prompt_tab(
    *,
    settings: Dict[str, Any],
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
) -> None:
    st.subheader("Review Prompt")
    st.markdown(
        '<div class="section-subtitle">Create row-level AI tags that become new review columns. Keep prompts short, specific, and label-driven so the output stays stable and decision-ready.</div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        top_cols = st.columns([2.2, 1.3])
        with top_cols[0]:
            st.markdown("**Prompt library**")
            starter_cols = st.columns([1.2, 1.2, 1])
            if starter_cols[0].button("Add starter pack", use_container_width=True, key="prompt_add_starter_pack"):
                st.session_state["prompt_definitions_df"] = add_prompt_rows(st.session_state["prompt_definitions_df"], REVIEW_PROMPT_STARTER_ROWS)
                st.rerun()
            if starter_cols[1].button("Reset to starter pack", use_container_width=True, key="prompt_reset_starter_pack"):
                st.session_state["prompt_definitions_df"] = pd.DataFrame(REVIEW_PROMPT_STARTER_ROWS)
                st.rerun()
            if starter_cols[2].button("Clear prompts", use_container_width=True, key="prompt_clear_all"):
                st.session_state["prompt_definitions_df"] = pd.DataFrame(columns=["column_name", "prompt", "labels"])
                st.session_state["prompt_builder_suggestion"] = None
                st.rerun()
            st.caption("Starter pack includes loudness, usage sessions, safety risk level, and reliability risk signals.")
        with top_cols[1]:
            ai_runtime = render_ai_settings_controls("prompt_tab", include_batch_size=True, expander_label="Advanced AI settings")
            st.caption(
                f"Model: {ai_runtime['model']} · Reasoning: {ai_runtime['reasoning_effort']} · Batch size: {ai_runtime['prompt_batch_size']}"
            )

    api_key = ai_runtime.get("api_key")

    st.markdown("#### Prompt definitions")
    st.caption("Each row creates a new output column in the review file. Keep prompts short and specific to reduce drift.")
    edited_df = st.data_editor(
        st.session_state["prompt_definitions_df"],
        use_container_width=True,
        num_rows="dynamic",
        hide_index=True,
        key="prompt_definition_editor",
        height=520,
        column_config={
            "column_name": st.column_config.TextColumn("Column name", width="medium", help="Snake case is best, for example perceived_loudness"),
            "prompt": st.column_config.TextColumn("Review prompt", width="large"),
            "labels": st.column_config.TextColumn("Labels", width="large", help="Comma-separated, for example Positive, Negative, Neutral, Not Mentioned"),
        },
    )
    st.session_state["prompt_definitions_df"] = edited_df

    builder_error: Optional[str] = None
    with st.expander("AI prompt builder", expanded=False):
        st.caption("Use AI to tighten a prompt. The builder intentionally drafts short prompts to keep tagging stable.")
        builder_cols = st.columns([2.0, 1.2, 0.9])
        builder_goal = builder_cols[0].text_area(
            "What do you want to detect?",
            value="",
            placeholder="Example: detect whether the product is perceived as loud and classify the mention.",
            key="prompt_builder_goal",
            height=120,
        )
        preferred_labels = builder_cols[1].text_input(
            "Preferred labels",
            value="Positive, Negative, Neutral, Not Mentioned",
            key="prompt_builder_labels",
        )
        with builder_cols[2]:
            st.markdown("&nbsp;", unsafe_allow_html=True)
            if st.button("Draft with AI", use_container_width=True, disabled=not bool(api_key), key="prompt_builder_run"):
                if not builder_goal.strip():
                    builder_error = "Describe the signal you want to detect before using the AI prompt builder."
                else:
                    overlay = show_thinking_overlay("Drafting a shorter prompt and label set...")
                    try:
                        suggestion = call_openai_prompt_builder(
                            api_key=api_key,
                            model=ai_runtime["model"],
                            reasoning_effort=ai_runtime["reasoning_effort"],
                            goal=builder_goal,
                            preferred_labels=preferred_labels,
                            filtered_df=filtered_df if not filtered_df.empty else overall_df,
                            summary=summary,
                        )
                        st.session_state["prompt_builder_suggestion"] = suggestion
                        st.rerun()
                    except Exception as exc:
                        builder_error = f"OpenAI prompt builder failed: {exc}"
                    finally:
                        overlay.empty()

        suggestion = st.session_state.get("prompt_builder_suggestion")
        if builder_error:
            st.error(builder_error)
        if suggestion:
            suggestion_cols = st.columns([3.0, 1.0, 1.0])
            with suggestion_cols[0]:
                st.markdown(f"**Suggested column** `{suggestion['column_name']}`")
                st.write(suggestion.get("prompt", ""))
                st.caption("Labels: " + ", ".join(suggestion.get("labels", [])))
                if suggestion.get("why_it_matters"):
                    st.caption(suggestion["why_it_matters"])
            if suggestion_cols[1].button("Add to list", use_container_width=True, key="prompt_builder_add"):
                append_df = pd.DataFrame([
                    {
                        "column_name": suggestion["column_name"],
                        "prompt": suggestion["prompt"],
                        "labels": ", ".join(suggestion.get("labels", [])),
                    }
                ])
                st.session_state["prompt_definitions_df"] = pd.concat([st.session_state["prompt_definitions_df"], append_df], ignore_index=True)
                st.session_state["prompt_builder_suggestion"] = None
                st.rerun()
            if suggestion_cols[2].button("Dismiss", use_container_width=True, key="prompt_builder_dismiss"):
                st.session_state["prompt_builder_suggestion"] = None
                st.rerun()

    try:
        prompt_definitions = normalize_prompt_definitions(st.session_state["prompt_definitions_df"], overall_df.columns)
    except ReviewDownloaderError as exc:
        st.error(str(exc))
        prompt_definitions = []

    with st.container(border=True):
        scope_cols = st.columns([1.35, 1, 1, 2.25])
        tagging_scope = scope_cols[0].selectbox("Tagging scope", options=["Current filtered reviews", "All loaded reviews"], index=0, key="prompt_tagging_scope")
        scope_df = filtered_df if tagging_scope == "Current filtered reviews" else overall_df
        review_count_in_scope = int(len(scope_df))
        estimated_calls = math.ceil(review_count_in_scope / max(1, ai_runtime["prompt_batch_size"])) if review_count_in_scope else 0
        scope_cols[1].metric("Reviews in scope", f"{review_count_in_scope:,}")
        scope_cols[2].metric("OpenAI requests", f"{estimated_calls:,}")
        scope_cols[3].caption(
            f"Scope: {tagging_scope.lower()}. Filters: {filter_description}. Batch size: {ai_runtime['prompt_batch_size']}."
        )
        run_disabled = (not api_key) or (not prompt_definitions) or review_count_in_scope == 0
        if st.button("Run Review Prompt", type="primary", use_container_width=True, disabled=run_disabled, key="prompt_run_button"):
            overlay = show_thinking_overlay("Classifying each review with your Review Prompt definitions...")
            try:
                prompt_results_df = run_review_prompt_tagging(
                    api_key=api_key,
                    model=ai_runtime["model"],
                    reasoning_effort=ai_runtime["reasoning_effort"],
                    source_df=scope_df.reset_index(drop=True),
                    prompt_definitions=prompt_definitions,
                    chunk_size=ai_runtime["prompt_batch_size"],
                )
                updated_overall_df = merge_prompt_results_into_reviews(overall_df, prompt_results_df, prompt_definitions)
                updated_dataset = dict(st.session_state["analysis_dataset"])
                updated_dataset["reviews_df"] = updated_overall_df
                st.session_state["analysis_dataset"] = updated_dataset
                summary_df = summarize_prompt_results(prompt_results_df, prompt_definitions, source_df=scope_df)
                st.session_state["prompt_run_artifacts"] = {
                    "definitions": prompt_definitions,
                    "summary_df": summary_df,
                    "scope_label": tagging_scope,
                    "scope_filter_description": filter_description,
                    "scope_review_ids": list(prompt_results_df["review_id"].astype(str)),
                    "definition_signature": prompt_definition_signature(prompt_definitions),
                    "review_count": int(len(prompt_results_df)),
                    "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
                st.session_state["master_export_bundle"] = None
                st.session_state["prompt_run_notice"] = f"Finished Review Prompt tagging for {len(prompt_results_df):,} reviews across {len(prompt_definitions)} prompts."
            except Exception as exc:
                st.error(f"Review Prompt run failed: {exc}")
            finally:
                overlay.empty()
            st.rerun()

    notice = st.session_state.pop("prompt_run_notice", None)
    if notice:
        st.success(notice)

    prompt_artifacts = st.session_state.get("prompt_run_artifacts")
    if not prompt_artifacts:
        st.info("Run Review Prompt to generate new AI columns, export the tagged review file, and inspect the label mix for each prompt.")
        return

    current_signature = prompt_definition_signature(prompt_definitions) if prompt_definitions else ""
    if current_signature != prompt_artifacts.get("definition_signature"):
        st.info("The prompt definitions changed since the last run. Re-run Review Prompt to refresh the results below.")
    if prompt_artifacts.get("scope_filter_description") != filter_description and prompt_artifacts.get("scope_label") == "Current filtered reviews":
        st.info("The current filters changed after the last Review Prompt run. Re-run to refresh the current-filter scope.")

    updated_overall_df = st.session_state["analysis_dataset"]["reviews_df"]
    review_ids = set(str(item) for item in prompt_artifacts.get("scope_review_ids", []))
    result_scope_df = updated_overall_df[updated_overall_df["review_id"].astype(str).isin(review_ids)].copy()
    bundle = get_master_export_bundle(summary, updated_overall_df, prompt_artifacts)

    header_cols = st.columns([1.3, 4])
    header_cols[0].download_button(
        label="Download tagged review file",
        data=bundle["excel_bytes"],
        file_name=bundle["excel_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="review_prompt_download",
    )
    header_cols[1].caption(
        f"Latest Review Prompt run: {prompt_artifacts.get('generated_utc')} | Scope: {prompt_artifacts.get('scope_label')} | Reviews tagged: {prompt_artifacts.get('review_count'):,}"
    )

    prompt_lookup = {prompt["display_name"]: prompt for prompt in prompt_artifacts["definitions"]}
    prompt_names = list(prompt_lookup.keys())
    if not prompt_names:
        st.info("No prompt results are available yet.")
        return
    if st.session_state.get("prompt_result_view") not in prompt_names:
        st.session_state["prompt_result_view"] = prompt_names[0]

    st.markdown("#### Tagged result view")
    selected_prompt_name = st.radio(
        "Prompt result view",
        options=prompt_names,
        horizontal=True,
        key="prompt_result_view",
        label_visibility="collapsed",
    )
    prompt = prompt_lookup[selected_prompt_name]
    prompt_col = prompt["column_name"]
    base_view_df = result_scope_df[result_scope_df[prompt_col].notna()].copy() if prompt_col in result_scope_df.columns else result_scope_df.iloc[0:0]

    control_cols = st.columns([2.1, 1.1, 1.1, 1.0])
    label_options = [str(label) for label in prompt_artifacts["summary_df"][prompt_artifacts["summary_df"]["column_name"] == prompt_col]["label"].tolist()]
    label_key = f"prompt_labels_{prompt_col}"
    if label_key not in st.session_state or not st.session_state.get(label_key):
        st.session_state[label_key] = label_options
    selected_labels = control_cols[0].multiselect("Labels", options=label_options, default=st.session_state[label_key], key=label_key)
    source_mode = control_cols[1].selectbox("Review source", options=["All tagged reviews", "Organic only", "Incentivized only"], key=f"prompt_source_{prompt_col}")
    rating_mode = control_cols[2].selectbox("Ratings", options=RATING_FILTER_OPTIONS_SIMPLE, key=f"prompt_rating_mode_{prompt_col}")
    preview_rows = int(control_cols[3].selectbox("Preview rows", options=[25, 50, 100], index=1, key=f"prompt_preview_rows_{prompt_col}"))

    preview_df = base_view_df.copy()
    if selected_labels:
        preview_df = preview_df[preview_df[prompt_col].isin(selected_labels)]
    else:
        preview_df = preview_df.iloc[0:0]
    if source_mode == "Organic only":
        preview_df = preview_df[~preview_df["incentivized_review"].fillna(False)]
    elif source_mode == "Incentivized only":
        preview_df = preview_df[preview_df["incentivized_review"].fillna(False)]
    selected_ratings = rating_values_for_mode(rating_mode)
    if selected_ratings:
        preview_df = preview_df[preview_df["rating"].isin(selected_ratings)]

    prompt_summary = summarize_single_prompt_view(preview_df, prompt)

    def _extract_plotly_selected_labels(selection_event: Any, summary_df: pd.DataFrame) -> Optional[List[str]]:
        if selection_event is None:
            return None
        selection = getattr(selection_event, "selection", None)
        if selection is None and isinstance(selection_event, dict):
            selection = selection_event.get("selection")
        if selection is None:
            return None
        points = getattr(selection, "points", None)
        if points is None and isinstance(selection, dict):
            points = selection.get("points")
        if points is None:
            return None
        labels: List[str] = []
        for point in points:
            point_data = point if isinstance(point, dict) else {}
            label = point_data.get("label")
            if label is None:
                point_number = point_data.get("point_number")
                if point_number is not None and 0 <= int(point_number) < len(summary_df):
                    label = str(summary_df.iloc[int(point_number)]["label"])
            if label is not None and str(label) not in labels:
                labels.append(str(label))
        return labels

    chart_col, table_col = st.columns([1.45, 1.05])
    with chart_col:
        with st.container(border=True):
            st.markdown(f"**{prompt['display_name']} distribution**")
            st.caption("Click a pie slice to filter the summary table and preview below.")
            if prompt_summary.empty:
                st.info("No tagged reviews match the current prompt filters.")
            else:
                fig = px.pie(
                    prompt_summary,
                    names="label",
                    values="review_count",
                    hole=0.42,
                    title=None,
                    custom_data=["review_count", "avg_rating", "share"],
                )
                fig.update_traces(hovertemplate="%{label}<br>Reviews: %{customdata[0]}<br>Avg rating: %{customdata[1]:.2f}<br>Share: %{customdata[2]:.1%}<extra></extra>")
                fig.update_layout(margin=dict(l=20, r=20, t=20, b=20))
                selection_event = None
                try:
                    selection_event = st.plotly_chart(fig, use_container_width=True, key=f"prompt_pie_{prompt_col}", on_select="rerun")
                except TypeError:
                    st.plotly_chart(fig, use_container_width=True, key=f"prompt_pie_{prompt_col}")
                selected_from_chart = _extract_plotly_selected_labels(selection_event, prompt_summary)
                chart_flag_key = f"prompt_chart_active_{prompt_col}"
                current_labels = list(st.session_state.get(label_key, label_options))
                if selected_from_chart is not None:
                    if selected_from_chart and sorted(current_labels) != sorted(selected_from_chart):
                        st.session_state[label_key] = selected_from_chart
                        st.session_state[chart_flag_key] = True
                        st.rerun()
                    if selected_from_chart == [] and st.session_state.get(chart_flag_key):
                        st.session_state[label_key] = label_options
                        st.session_state[chart_flag_key] = False
                        st.rerun()
    with table_col:
        with st.container(border=True):
            st.markdown(f"**Column name** `{prompt_col}`")
            st.write(prompt["prompt"])
            if prompt_summary.empty:
                st.info("No label counts for the current prompt filters.")
            else:
                display_summary = prompt_summary.copy()
                display_summary["avg_rating"] = display_summary["avg_rating"].map(lambda x: f"{x:.2f}★" if pd.notna(x) else "—")
                display_summary["share"] = display_summary["share"].map(format_pct)
                st.dataframe(
                    display_summary[["label", "review_count", "avg_rating", "share"]],
                    use_container_width=True,
                    hide_index=True,
                    height=280,
                )

    preview_columns = [col for col in ["review_id", "rating", "incentivized_review", "submission_time", "content_locale", "retailer", "product_or_sku", "title", "review_text", prompt_col] if col in preview_df.columns]
    st.markdown("**Tagged review preview**")
    st.dataframe(preview_df[preview_columns].head(preview_rows), use_container_width=True, hide_index=True, height=360)


# -----------------------------------------------------------------------------
# Data loading and app shell
# -----------------------------------------------------------------------------


def load_product_reviews(product_url: str) -> Dict[str, Any]:
    product_url = normalize_product_url(product_url)
    session = get_session()

    with st.spinner("Loading the product page and resolving the product ID..."):
        html = fetch_product_html(session, product_url)
        product_id = extract_product_id(product_url, html)

    with st.spinner("Checking Bazaarvoice review volume..."):
        total_reviews = get_total_reviews(
            session,
            product_id=product_id,
            passkey=DEFAULT_PASSKEY,
            displaycode=DEFAULT_DISPLAYCODE,
            api_version=DEFAULT_API_VERSION,
            sort=DEFAULT_SORT,
            content_locales=DEFAULT_CONTENT_LOCALES,
        )

    requests_needed = math.ceil(total_reviews / DEFAULT_PAGE_SIZE) if total_reviews else 0
    raw_reviews = fetch_all_reviews(
        session,
        product_id=product_id,
        passkey=DEFAULT_PASSKEY,
        displaycode=DEFAULT_DISPLAYCODE,
        api_version=DEFAULT_API_VERSION,
        page_size=DEFAULT_PAGE_SIZE,
        sort=DEFAULT_SORT,
        content_locales=DEFAULT_CONTENT_LOCALES,
        total_reviews=total_reviews,
    )
    reviews_df = build_reviews_dataframe(raw_reviews)
    if not reviews_df.empty:
        reviews_df["review_id"] = reviews_df["review_id"].astype(str)
        reviews_df["product_or_sku"] = reviews_df.get("product_or_sku", pd.Series(index=reviews_df.index, dtype="object")).fillna(product_id)
        reviews_df["base_sku"] = reviews_df.get("base_sku", pd.Series(index=reviews_df.index, dtype="object")).fillna(product_id)
        reviews_df["product_id"] = reviews_df["product_id"].fillna(product_id)
    summary = ReviewBatchSummary(
        product_url=product_url,
        product_id=product_id,
        total_reviews=total_reviews,
        page_size=DEFAULT_PAGE_SIZE,
        requests_needed=requests_needed,
        reviews_downloaded=len(reviews_df),
    )
    return {"summary": summary, "reviews_df": reviews_df, "source_type": "bazaarvoice", "source_label": product_url}



def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    inject_css()
    initialize_session_state()

    st.title(APP_TITLE)
    st.caption(
        "Build a review workspace from a SharkNinja product URL or an uploaded review export, then filter the voice of customer, explore review cards, chat with an AI analyst, and create row-level AI tags."
    )

    source_mode = st.radio(
        "Workspace source",
        options=["SharkNinja product URL", "Uploaded review file"],
        horizontal=True,
        key="workspace_source_mode",
    )

    if source_mode == "SharkNinja product URL":
        product_url = st.text_input(
            "Product URL",
            value="https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
            help="Example: https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
        )
        build_clicked = st.button("Build review workspace", type="primary")
        if build_clicked:
            try:
                dataset = load_product_reviews(product_url)
                st.session_state["analysis_dataset"] = dataset
                st.session_state["chat_messages"] = []
                st.session_state["chat_scope_signature"] = None
                st.session_state["chat_scope_notice"] = None
                st.session_state["master_export_bundle"] = None
                st.session_state["prompt_run_artifacts"] = None
                st.session_state["prompt_run_notice"] = None
                st.session_state["active_main_view"] = "Dashboard"
                st.session_state["workspace_view_selector"] = "Dashboard"
                st.success(f"Loaded {dataset['summary'].reviews_downloaded:,} reviews for {dataset['summary'].product_id}.")
            except requests.HTTPError as exc:
                st.error(f"HTTP error: {exc}")
            except ReviewDownloaderError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.exception(exc)
    else:
        uploaded_files = st.file_uploader(
            "Upload review export files",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            help="Supports Axion-style exports and similar CSV/XLSX review files.",
        )
        st.caption("Mapped columns include Opened date, Base SKU, SKU Item, Product Name, Review Text, Title, Rating (num), Seeded Flag, Syndicated Flag, Retailer, Location, and Event Id.")
        build_clicked = st.button("Build review workspace from file", type="primary")
        if build_clicked:
            try:
                dataset = load_uploaded_review_files(uploaded_files or [])
                st.session_state["analysis_dataset"] = dataset
                st.session_state["chat_messages"] = []
                st.session_state["chat_scope_signature"] = None
                st.session_state["chat_scope_notice"] = None
                st.session_state["master_export_bundle"] = None
                st.session_state["prompt_run_artifacts"] = None
                st.session_state["prompt_run_notice"] = None
                st.session_state["active_main_view"] = "Dashboard"
                st.session_state["workspace_view_selector"] = "Dashboard"
                st.success(f"Loaded {dataset['summary'].reviews_downloaded:,} uploaded reviews into the workspace.")
            except ReviewDownloaderError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.exception(exc)

    dataset = st.session_state.get("analysis_dataset")
    settings = render_sidebar_controls(dataset["reviews_df"] if dataset else None)
    if not dataset:
        st.info("Build a review workspace to unlock the dashboard, review explorer, AI analyst, and Review Prompt tagging.")
        return

    summary: ReviewBatchSummary = dataset["summary"]
    overall_df: pd.DataFrame = dataset["reviews_df"]
    source_type = dataset.get("source_type", "bazaarvoice")
    source_label = dataset.get("source_label", "")

    filtered_df = apply_filters(
        overall_df,
        selected_ratings=settings["selected_ratings"],
        incentivized_mode=map_review_source_mode(settings["review_source_mode"]),
        selected_products=settings["selected_products"],
        selected_locales=settings["selected_locales"],
        recommendation_mode=settings["recommendation_mode"],
        syndicated_mode="All",
        media_mode="All",
        date_range=settings["date_range"],
        text_query=settings["text_query"],
    )
    filter_description = describe_current_filters(
        selected_ratings=settings["selected_ratings"],
        selected_products=settings["selected_products"],
        review_source_mode=settings["review_source_mode"],
        selected_locales=settings["selected_locales"],
        recommendation_mode=settings["recommendation_mode"],
        date_range=settings["date_range"],
        text_query=settings["text_query"],
    )

    render_workspace_header(
        summary,
        overall_df,
        st.session_state.get("prompt_run_artifacts"),
        source_type=source_type,
        source_label=source_label,
    )
    render_top_metrics(overall_df, filtered_df)
    st.caption(f"Filter status: {filter_description}. Showing {len(filtered_df):,} of {len(overall_df):,} reviews.")

    if st.session_state.get("workspace_view_selector") not in ["Dashboard", "Review Explorer", "AI Analyst", "Review Prompt"]:
        st.session_state["workspace_view_selector"] = st.session_state.get("active_main_view", "Dashboard")
    st.radio(
        "Workspace view",
        options=["Dashboard", "Review Explorer", "AI Analyst", "Review Prompt"],
        horizontal=True,
        key="workspace_view_selector",
    )
    st.session_state["active_main_view"] = st.session_state.get("workspace_view_selector", "Dashboard")

    active_view = st.session_state.get("active_main_view", "Dashboard")
    if active_view == "Dashboard":
        render_dashboard(filtered_df)
    elif active_view == "Review Explorer":
        render_review_explorer(
            summary=summary,
            overall_df=overall_df,
            filtered_df=filtered_df,
            prompt_artifacts=st.session_state.get("prompt_run_artifacts"),
        )
    elif active_view == "AI Analyst":
        render_ai_tab(
            settings=settings,
            overall_df=overall_df,
            filtered_df=filtered_df,
            summary=summary,
            filter_description=filter_description,
        )
    else:
        render_review_prompt_tab(
            settings=settings,
            overall_df=overall_df,
            filtered_df=filtered_df,
            summary=summary,
            filter_description=filter_description,
        )


if __name__ == "__main__":
    main()
