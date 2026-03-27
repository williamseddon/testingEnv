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
import requests
import streamlit as st
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
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_REASONING_EFFORT = "low"
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



def build_reviews_dataframe(raw_reviews: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = [flatten_review(review) for review in raw_reviews]
    df = pd.DataFrame(rows)

    required_columns = [
        "review_id",
        "product_id",
        "original_product_name",
        "title",
        "review_text",
        "rating",
        "is_recommended",
        "content_locale",
        "submission_time",
        "incentivized_review",
        "is_syndicated",
        "photos_count",
        "photo_urls",
        "title_and_text",
    ]
    df = ensure_columns(df, required_columns)

    if df.empty:
        return df

    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["is_recommended"] = df["is_recommended"].map(lambda value: bool(value) if pd.notna(value) else pd.NA)
    df["incentivized_review"] = df["incentivized_review"].fillna(False).astype(bool)
    df["is_syndicated"] = df["is_syndicated"].fillna(False).astype(bool)
    df["photos_count"] = pd.to_numeric(df["photos_count"], errors="coerce").fillna(0).astype(int)
    df["submission_time"] = pd.to_datetime(df["submission_time"], errors="coerce", utc=True).dt.tz_convert(None)
    df["submission_date"] = df["submission_time"].dt.date
    df["submission_month"] = df["submission_time"].dt.to_period("M").astype(str)
    df["has_photos"] = df["photos_count"] > 0
    df["has_media"] = df["has_photos"]
    df["title"] = df["title"].fillna("").astype(str)
    df["review_text"] = df["review_text"].fillna("").astype(str)
    df["title_and_text"] = (df["title"].str.strip() + " " + df["review_text"].str.strip()).str.strip()
    df["review_length_chars"] = df["review_text"].str.len()
    df["review_length_words"] = df["review_text"].str.split().str.len().fillna(0).astype(int)
    df["rating_label"] = df["rating"].map(lambda x: f"{int(x)} star" if pd.notna(x) else "Unknown")
    df["year_month_sort"] = pd.to_datetime(df["submission_month"], format="%Y-%m", errors="coerce")

    sort_cols = [col for col in ["submission_time", "review_id"] if col in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=[False, False], na_position="last").reset_index(drop=True)

    return df



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
    text = str(text or "").lower()
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
    min_date = None
    max_date = None
    valid_dates = df["submission_date"].dropna() if "submission_date" in df.columns else pd.Series(dtype="object")
    if not valid_dates.empty:
        min_date = valid_dates.min()
        max_date = valid_dates.max()
    return {
        "ratings": [1, 2, 3, 4, 5],
        "locales": sorted(str(locale) for locale in df["content_locale"].dropna().unique()) if not df.empty else [],
        "min_date": min_date,
        "max_date": max_date,
    }



def apply_filters(
    df: pd.DataFrame,
    *,
    selected_ratings: Sequence[int],
    incentivized_mode: str,
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

    text_query = text_query.strip()
    if text_query:
        pattern = re.escape(text_query)
        text_cols = filtered["title_and_text"].fillna("")
        filtered = filtered[text_cols.str.contains(pattern, case=False, na=False, regex=True)]

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
        parts.append(f"incentivized={incentivized_mode}")
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
# AI context + chatbot
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



def truncate_text(text: str, max_chars: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"



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
        if row.get("incentivized_review") is False:
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
                "review_id": row.get("review_id"),
                "rating": row.get("rating"),
                "incentivized_review": bool(row.get("incentivized_review", False)),
                "content_locale": row.get("content_locale"),
                "submission_date": str(row.get("submission_date") or ""),
                "title": truncate_text(row.get("title", ""), 120),
                "snippet": truncate_text(row.get("review_text", ""), 360),
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
    theme_df = compute_theme_signals(filtered_df).head(10)
    rating_df = rating_distribution(filtered_df)
    monthly_df = monthly_trend(filtered_df).tail(12)
    locale_df = locale_distribution(filtered_df).head(10)
    negative_terms_df = top_terms(filtered_df[filtered_df["rating"].isin([1, 2])]["title_and_text"], top_n=12)
    positive_terms_df = top_terms(filtered_df[filtered_df["rating"].isin([4, 5])]["title_and_text"], top_n=12)
    relevant_reviews = select_relevant_reviews(filtered_df, question, max_reviews=18)

    context_payload = {
        "product": {
            "product_id": summary.product_id,
            "product_url": summary.product_url,
            "total_reviews_downloaded": summary.reviews_downloaded,
            "requests_needed": summary.requests_needed,
        },
        "analysis_scope": {
            "current_filter_description": filter_description,
            "overall_review_count": int(len(overall_df)),
            "filtered_review_count": int(len(filtered_df)),
        },
        "overall_metrics": overall_metrics,
        "filtered_metrics": filtered_metrics,
        "rating_distribution_filtered": rating_df.to_dict(orient="records"),
        "monthly_trend_filtered": monthly_df.to_dict(orient="records"),
        "locale_distribution_filtered": locale_df.to_dict(orient="records"),
        "theme_signals_filtered": theme_df.to_dict(orient="records"),
        "top_negative_terms_filtered": negative_terms_df.to_dict(orient="records"),
        "top_positive_terms_filtered": positive_terms_df.to_dict(orient="records"),
        "review_evidence_pack": review_snippet_rows(relevant_reviews),
    }
    return json.dumps(context_payload, ensure_ascii=False, indent=2, default=str)



def build_persona_instructions(persona_name: str) -> str:
    persona = PERSONAS[persona_name]
    return textwrap.dedent(
        f"""
        {persona['instructions']}

        Ground every important finding in the supplied review dataset.
        Do not invent facts, counts, or quotes that are not supported by the evidence pack.
        If evidence is mixed or weak, say so explicitly.
        Use markdown.
        Cite supporting review IDs in parentheses, for example: (review_ids: 12345, 67890).
        Where useful, separate facts from inference.
        """
    ).strip()



def call_openai_analyst(
    *,
    api_key: str,
    model: str,
    reasoning_effort: str,
    persona_name: str,
    question: str,
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
    chat_history: Sequence[Dict[str, str]],
) -> str:
    if OpenAI is None:
        raise ReviewDownloaderError("The OpenAI Python package is not installed. Add openai to your environment.")
    if not api_key:
        raise ReviewDownloaderError("No OpenAI API key was found in Streamlit secrets or the OPENAI_API_KEY environment variable.")

    client = OpenAI(api_key=api_key)
    instructions = build_persona_instructions(persona_name)
    ai_context = build_ai_context(
        overall_df=overall_df,
        filtered_df=filtered_df,
        summary=summary,
        filter_description=filter_description,
        question=question,
    )

    input_messages: List[Dict[str, Any]] = []
    for message in chat_history[-6:]:
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

    response = client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=instructions,
        input=input_messages,
        max_output_tokens=1400,
        truncation="auto",
    )
    return (response.output_text or "").strip()


# -----------------------------------------------------------------------------
# UI rendering
# -----------------------------------------------------------------------------


def initialize_session_state() -> None:
    st.session_state.setdefault("analysis_dataset", None)
    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("download_artifacts", None)



def render_sidebar_settings() -> Dict[str, Any]:
    with st.sidebar:
        st.header("Bazaarvoice settings")
        passkey = st.text_input("Passkey", value=DEFAULT_PASSKEY, type="password")
        displaycode = st.text_input("Display code", value=DEFAULT_DISPLAYCODE)
        api_version = st.text_input("API version", value=DEFAULT_API_VERSION)
        page_size = st.number_input("Reviews per request", min_value=1, max_value=100, value=DEFAULT_PAGE_SIZE, step=1)
        sort = st.text_input("Sort", value=DEFAULT_SORT)
        content_locales = st.text_area("Content locale filter", value=DEFAULT_CONTENT_LOCALES, height=120)
        st.caption("Defaults are based on the Bazaarvoice API call pattern you shared.")

        st.divider()
        st.header("OpenAI analyst")
        openai_api_key = get_openai_api_key()
        key_loaded = bool(openai_api_key)
        st.write("API key status:")
        if key_loaded:
            st.success("Loaded from Streamlit secrets or environment")
        else:
            st.warning("No OPENAI_API_KEY found yet")
        model = st.selectbox("Model", options=["gpt-5-mini", "gpt-5.4"], index=0)
        reasoning_effort = st.selectbox("Reasoning effort", options=["minimal", "low", "medium", "high"], index=1)

        return {
            "passkey": passkey,
            "displaycode": displaycode,
            "api_version": api_version,
            "page_size": int(page_size),
            "sort": sort,
            "content_locales": content_locales,
            "openai_api_key": openai_api_key,
            "openai_model": model,
            "reasoning_effort": reasoning_effort,
        }



def render_filter_sidebar(df: pd.DataFrame) -> Dict[str, Any]:
    options = build_filter_options(df)
    with st.sidebar:
        st.divider()
        st.header("Analysis filters")
        selected_ratings = st.multiselect("Ratings", options=options["ratings"], default=options["ratings"])
        incentivized_mode = st.selectbox(
            "Incentivized reviews",
            options=["All reviews", "Non-incentivized only", "Incentivized only"],
            index=0,
        )
        selected_locales = st.multiselect("Locales", options=options["locales"], default=[])
        recommendation_mode = st.selectbox(
            "Recommendation status",
            options=["All", "Recommended only", "Not recommended only"],
            index=0,
        )
        syndicated_mode = st.selectbox(
            "Syndication",
            options=["All", "Non-syndicated only", "Syndicated only"],
            index=0,
        )
        media_mode = st.selectbox(
            "Photos",
            options=["All", "With photos only", "No photos only"],
            index=0,
        )

        date_range: Optional[Tuple[date, date]] = None
        if options["min_date"] and options["max_date"]:
            picked = st.date_input(
                "Submission date range",
                value=(options["min_date"], options["max_date"]),
                min_value=options["min_date"],
                max_value=options["max_date"],
            )
            if isinstance(picked, tuple) and len(picked) == 2:
                date_range = (picked[0], picked[1])
        text_query = st.text_input("Text contains", value="")

        return {
            "selected_ratings": selected_ratings,
            "incentivized_mode": incentivized_mode,
            "selected_locales": selected_locales,
            "recommendation_mode": recommendation_mode,
            "syndicated_mode": syndicated_mode,
            "media_mode": media_mode,
            "date_range": date_range,
            "text_query": text_query,
        }



def render_top_metrics(overall_df: pd.DataFrame, filtered_df: pd.DataFrame) -> None:
    overall_metrics = compute_metrics(overall_df)
    filtered_metrics = compute_metrics(filtered_df)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric(
        "Reviews in view",
        f"{filtered_metrics['review_count']:,}",
        delta=f"of {overall_metrics['review_count']:,} total",
    )
    col2.metric(
        "Avg rating",
        format_metric_number(filtered_metrics["avg_rating"]),
        delta=compare_metric_delta(filtered_metrics["avg_rating"], overall_metrics["avg_rating"]),
    )
    col3.metric(
        "Avg rating (non-incent.)",
        format_metric_number(filtered_metrics["avg_rating_non_incentivized"]),
        delta=compare_metric_delta(
            filtered_metrics["avg_rating_non_incentivized"],
            overall_metrics["avg_rating_non_incentivized"],
        ),
    )
    col4.metric(
        "% 1-2 star",
        format_pct(filtered_metrics["pct_low_star"]),
        delta=compare_metric_delta(filtered_metrics["pct_low_star"], overall_metrics["pct_low_star"], is_pct=True),
    )
    col5.metric(
        "% incentivized",
        format_pct(filtered_metrics["pct_incentivized"]),
        delta=compare_metric_delta(
            filtered_metrics["pct_incentivized"], overall_metrics["pct_incentivized"], is_pct=True
        ),
    )
    col6.metric(
        "% with photos",
        format_pct(filtered_metrics["pct_with_photos"]),
        delta=compare_metric_delta(filtered_metrics["pct_with_photos"], overall_metrics["pct_with_photos"], is_pct=True),
    )



def render_dashboard(overall_df: pd.DataFrame, filtered_df: pd.DataFrame) -> None:
    st.subheader("Dashboard")
    st.markdown('<div class="section-subtitle">High-signal KPIs and review patterns for the current filter set.</div>', unsafe_allow_html=True)

    filtered_metrics = compute_metrics(filtered_df)
    rating_df = rating_distribution(filtered_df)
    monthly_df = monthly_trend(filtered_df)
    locale_df = locale_distribution(filtered_df).head(10)
    theme_df = compute_theme_signals(filtered_df).head(8)

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        if not rating_df.empty:
            fig = px.bar(rating_df, x="rating", y="review_count", text="review_count", title="Rating distribution")
            fig.update_layout(xaxis_title="Star rating", yaxis_title="Reviews", margin=dict(l=20, r=20, t=50, b=20))
            st.plotly_chart(fig, use_container_width=True)
    with chart_col2:
        if not monthly_df.empty:
            fig = px.line(monthly_df, x="month_start", y="review_count", markers=True, title="Review volume over time")
            fig.update_layout(xaxis_title="Month", yaxis_title="Reviews", margin=dict(l=20, r=20, t=50, b=20))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No dated reviews available for a trend chart.")

    chart_col3, chart_col4 = st.columns(2)
    with chart_col3:
        if not locale_df.empty:
            fig = px.bar(locale_df, x="review_count", y="content_locale", orientation="h", title="Top locales")
            fig.update_layout(xaxis_title="Reviews", yaxis_title="Locale", margin=dict(l=20, r=20, t=50, b=20))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No locale data available.")
    with chart_col4:
        spotlight = st.container(border=True)
        with spotlight:
            st.markdown("**Current review mix**")
            st.write(f"Recommendation rate: {format_pct(filtered_metrics['recommend_rate'])}")
            st.write(f"1-star share: {format_pct(filtered_metrics['pct_one_star'])}")
            st.write(f"2-star share: {format_pct(filtered_metrics['pct_two_star'])}")
            st.write(f"5-star share: {format_pct(filtered_metrics['pct_five_star'])}")
            st.write(f"Median review length: {format_metric_number(filtered_metrics['median_review_words'], 0)} words")
            st.write(f"Non-incentivized reviews in scope: {filtered_metrics['non_incentivized_count']:,}")

    bottom_col1, bottom_col2 = st.columns(2)
    with bottom_col1:
        st.markdown("**Theme signals**")
        if not theme_df.empty:
            display_df = theme_df.copy()
            display_df["mention_rate"] = display_df["mention_rate"].map(format_pct)
            display_df["avg_rating_when_mentioned"] = display_df["avg_rating_when_mentioned"].map(format_metric_number)
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No theme signals available yet.")
    with bottom_col2:
        pos_terms = top_terms(filtered_df[filtered_df["rating"].isin([4, 5])]["title_and_text"], top_n=10)
        neg_terms = top_terms(filtered_df[filtered_df["rating"].isin([1, 2])]["title_and_text"], top_n=10)
        st.markdown("**Top words**")
        term_col1, term_col2 = st.columns(2)
        with term_col1:
            st.caption("Positive / 4-5 star")
            st.dataframe(pos_terms, use_container_width=True, hide_index=True)
        with term_col2:
            st.caption("Negative / 1-2 star")
            st.dataframe(neg_terms, use_container_width=True, hide_index=True)



def render_review_explorer(filtered_df: pd.DataFrame, overall_count: int) -> None:
    st.subheader("Review explorer")
    st.markdown(
        f'<div class="section-subtitle">Showing {len(filtered_df):,} reviews from the current filter set out of {overall_count:,} downloaded reviews.</div>',
        unsafe_allow_html=True,
    )

    preview_cols = [
        "review_id",
        "submission_time",
        "rating",
        "incentivized_review",
        "is_syndicated",
        "content_locale",
        "title",
        "review_text",
        "user_nickname",
        "user_location",
        "has_photos",
        "photos_count",
        "is_recommended",
        "syndication_source_name",
    ]
    preview_cols = [column for column in preview_cols if column in filtered_df.columns]
    st.dataframe(filtered_df[preview_cols], use_container_width=True, hide_index=True, height=520)

    with st.expander("Preview first 25 raw records"):
        st.json(json.loads(filtered_df.head(25).to_json(orient="records", date_format="iso")))



def render_ai_tab(
    *,
    settings: Dict[str, Any],
    overall_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    summary: ReviewBatchSummary,
    filter_description: str,
) -> None:
    st.subheader("OpenAI analyst")
    st.markdown(
        '<div class="section-subtitle">Ask questions against the current filter set. The assistant receives KPIs, theme signals, distributions, and a curated evidence pack of review snippets.</div>',
        unsafe_allow_html=True,
    )

    if len(filtered_df) == 0:
        st.info("The current filters return no reviews. Adjust the filter set before asking the AI analyst.")
        return

    api_key = settings.get("openai_api_key")
    if not api_key:
        st.warning("Add OPENAI_API_KEY to Streamlit secrets to enable the AI analyst.")
        st.code('OPENAI_API_KEY = "sk-..."', language="toml")
        return

    persona_name = st.radio("Persona", options=list(PERSONAS.keys()), horizontal=True)
    persona = PERSONAS[persona_name]
    st.info(persona["blurb"])

    action_cols = st.columns(len(persona["sample_questions"]) + 1)
    prompt_to_send: Optional[str] = None
    for idx, sample_prompt in enumerate(persona["sample_questions"]):
        if action_cols[idx].button(f"Prompt {idx + 1}", use_container_width=True, key=f"sample_{persona_name}_{idx}"):
            prompt_to_send = sample_prompt
    if action_cols[-1].button("Generate preset report", use_container_width=True, key=f"preset_{persona_name}"):
        prompt_to_send = persona["prompt"]

    chat_container = st.container()
    with chat_container:
        for message in st.session_state["chat_messages"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    user_prompt = st.chat_input(
        "Ask about rating drivers, quality risks, feature opportunities, or segment-level differences..."
    )
    prompt_to_send = user_prompt or prompt_to_send

    clear_col1, clear_col2 = st.columns([1, 5])
    if clear_col1.button("Clear chat"):
        st.session_state["chat_messages"] = []
        st.rerun()
    clear_col2.caption(
        f"Current AI scope: {len(filtered_df):,} filtered reviews from {summary.product_id}. Filters: {filter_description}."
    )

    if prompt_to_send:
        prior_chat_history = list(st.session_state["chat_messages"])
        with st.chat_message("user"):
            st.markdown(prompt_to_send)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing reviews with OpenAI..."):
                try:
                    answer = call_openai_analyst(
                        api_key=api_key,
                        model=settings["openai_model"],
                        reasoning_effort=settings["reasoning_effort"],
                        persona_name=persona_name,
                        question=prompt_to_send,
                        overall_df=overall_df,
                        filtered_df=filtered_df,
                        summary=summary,
                        filter_description=filter_description,
                        chat_history=prior_chat_history,
                    )
                except Exception as exc:  # pragma: no cover - network/runtime safety in UI
                    answer = f"OpenAI request failed: {exc}"
                st.markdown(answer)
        st.session_state["chat_messages"].append({"role": "user", "content": prompt_to_send})
        st.session_state["chat_messages"].append({"role": "assistant", "content": answer})



def render_downloads_tab(
    *,
    summary: ReviewBatchSummary,
    overall_df: pd.DataFrame,
) -> None:
    st.subheader("Downloads")
    st.markdown('<div class="section-subtitle">Export the full review database, workbook, and analysis tables.</div>', unsafe_allow_html=True)

    artifact_key = f"{summary.product_id}:{summary.reviews_downloaded}:{summary.total_reviews}"
    bundle = st.session_state.get("download_artifacts")
    if not bundle or bundle.get("key") != artifact_key:
        with st.spinner("Preparing export files..."):
            overall_metrics = compute_metrics(overall_df)
            rating_df = rating_distribution(overall_df)
            monthly_df = monthly_trend(overall_df)
            locale_df = locale_distribution(overall_df)
            theme_df = compute_theme_signals(overall_df)
            positive_terms_df = top_terms(overall_df[overall_df["rating"].isin([4, 5])]["title_and_text"], top_n=20)
            negative_terms_df = top_terms(overall_df[overall_df["rating"].isin([1, 2])]["title_and_text"], top_n=20)
            excel_bytes = build_excel_file(
                summary,
                overall_df,
                overall_metrics,
                theme_df,
                rating_df,
                monthly_df,
                locale_df,
                positive_terms_df,
                negative_terms_df,
            )
            db_bytes = build_sqlite_database(
                summary,
                overall_df,
                overall_metrics,
                theme_df,
                rating_df,
                monthly_df,
                locale_df,
            )
            timestamp = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")
            bundle = {
                "key": artifact_key,
                "excel_bytes": excel_bytes,
                "db_bytes": db_bytes,
                "excel_name": f"{summary.product_id}_review_analysis_{timestamp}.xlsx",
                "db_name": f"{summary.product_id}_reviews_{timestamp}.db",
            }
            st.session_state["download_artifacts"] = bundle

    dcol1, dcol2 = st.columns(2)
    dcol1.download_button(
        label="Download Excel workbook",
        data=bundle["excel_bytes"],
        file_name=bundle["excel_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    dcol2.download_button(
        label="Download SQLite database",
        data=bundle["db_bytes"],
        file_name=bundle["db_name"],
        mime="application/octet-stream",
        use_container_width=True,
    )

    with st.expander("SQLite tables"):
        st.code(
            """
reviews
metadata
metrics
theme_signals
rating_distribution
monthly_trend
locale_distribution
            """.strip(),
            language="sql",
        )



def load_product_reviews(product_url: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    product_url = normalize_product_url(product_url)
    session = get_session()

    with st.spinner("Loading product page and resolving the product ID..."):
        html = fetch_product_html(session, product_url)
        product_id = extract_product_id(product_url, html)

    with st.spinner("Checking total review count..."):
        total_reviews = get_total_reviews(
            session,
            product_id=product_id,
            passkey=settings["passkey"],
            displaycode=settings["displaycode"],
            api_version=settings["api_version"],
            sort=settings["sort"],
            content_locales=settings["content_locales"],
        )

    requests_needed = math.ceil(total_reviews / settings["page_size"]) if total_reviews else 0

    metric_cols = st.columns(3)
    metric_cols[0].metric("Product ID", product_id)
    metric_cols[1].metric("Total reviews", total_reviews)
    metric_cols[2].metric("Requests needed", requests_needed)

    raw_reviews = fetch_all_reviews(
        session,
        product_id=product_id,
        passkey=settings["passkey"],
        displaycode=settings["displaycode"],
        api_version=settings["api_version"],
        page_size=settings["page_size"],
        sort=settings["sort"],
        content_locales=settings["content_locales"],
        total_reviews=total_reviews,
    )
    reviews_df = build_reviews_dataframe(raw_reviews)
    summary = ReviewBatchSummary(
        product_url=product_url,
        product_id=product_id,
        total_reviews=total_reviews,
        page_size=settings["page_size"],
        requests_needed=requests_needed,
        reviews_downloaded=len(reviews_df),
    )
    return {"summary": summary, "reviews_df": reviews_df}


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    inject_css()
    initialize_session_state()

    st.title(APP_TITLE)
    st.caption(
        "Pull SharkNinja Bazaarvoice reviews, build a review database, explore filtered analytics, and ask an OpenAI analyst for persona-based reports."
    )

    settings = render_sidebar_settings()

    product_url = st.text_input(
        "Product URL",
        value="https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
        help="Example: https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
    )

    load_clicked = st.button("Pull reviews and build analysis", type="primary")

    if load_clicked:
        try:
            dataset = load_product_reviews(product_url, settings)
            st.session_state["analysis_dataset"] = dataset
            st.session_state["chat_messages"] = []
            st.session_state["download_artifacts"] = None
            st.success(
                f"Loaded {dataset['summary'].reviews_downloaded:,} reviews for {dataset['summary'].product_id}. Review database and analysis are ready."
            )
        except requests.HTTPError as exc:
            st.error(f"HTTP error: {exc}")
        except ReviewDownloaderError as exc:
            st.error(str(exc))
        except Exception as exc:  # pragma: no cover - helpful for UI debugging
            st.exception(exc)

    dataset = st.session_state.get("analysis_dataset")
    if not dataset:
        st.info("Load a SharkNinja product page to unlock the dashboard, review explorer, SQLite database export, and OpenAI analyst.")
        return

    summary: ReviewBatchSummary = dataset["summary"]
    overall_df: pd.DataFrame = dataset["reviews_df"]

    filters = render_filter_sidebar(overall_df)
    filtered_df = apply_filters(overall_df, **filters)
    filter_description = describe_active_filters(**filters)

    render_top_metrics(overall_df, filtered_df)
    st.caption(f"Filter status: {filter_description}. Showing {len(filtered_df):,} of {len(overall_df):,} reviews.")

    tabs = st.tabs(["Dashboard", "Review explorer", "AI analyst", "Downloads"])
    with tabs[0]:
        render_dashboard(overall_df, filtered_df)
    with tabs[1]:
        render_review_explorer(filtered_df, len(overall_df))
    with tabs[2]:
        render_ai_tab(
            settings=settings,
            overall_df=overall_df,
            filtered_df=filtered_df,
            summary=summary,
            filter_description=filter_description,
        )
    with tabs[3]:
        render_downloads_tab(summary=summary, overall_df=overall_df)


if __name__ == "__main__":
    main()

