from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openpyxl.utils import get_column_letter


APP_TITLE = "SharkNinja Review Downloader"
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
BAZAARVOICE_ENDPOINT = "https://api.bazaarvoice.com/data/reviews.json"


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


def normalize_product_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ReviewDownloaderError("Please paste a product URL.")
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
    # Prefer product-specific identifiers over generic "Model" matches because
    # SharkNinja PDPs often contain add-ons/accessories above the main specs.
    primary_patterns = [
        r"Item\s*No\.?\s*([A-Z0-9_-]{3,})",
        r'"productId"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"sku"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"mpn"\s*:\s*"([A-Z0-9_-]{3,})"',
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
    # For SharkNinja PDPs, the final path segment (e.g. /AF181.html) is usually
    # the canonical product ID and is more reliable than the first "Model:" found
    # anywhere in the raw HTML.
    product_id = _extract_product_id_from_url(product_url) or _extract_product_id_from_html(html)
    if not product_id:
        raise ReviewDownloaderError(
            "Could not find a product ID on the page. Try a SharkNinja product detail URL like /AF181.html."
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
        "filter": [f"productid:eq:{product_id}", f"contentlocale:eq:{content_locales}", "isratingsonly:eq:false"],
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


def flatten_review(review: Dict[str, Any]) -> Dict[str, Any]:
    syndication_source = review.get("SyndicationSource") or {}
    photos = review.get("Photos") or []
    badges_order = review.get("BadgesOrder") or []

    context_data = review.get("ContextDataValues") or {}
    if not isinstance(context_data, dict):
        context_data = {}

    return {
        "review_id": review.get("Id"),
        "cid": review.get("CID"),
        "product_id": review.get("ProductId"),
        "original_product_name": review.get("OriginalProductName"),
        "title": review.get("Title"),
        "review_text": review.get("ReviewText"),
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
        "badges": ", ".join(badges_order),
        "badges_json": json.dumps(review.get("Badges") or {}, ensure_ascii=False),
        "context_data_json": json.dumps(context_data, ensure_ascii=False),
        "secondary_ratings_json": json.dumps(review.get("SecondaryRatings") or [], ensure_ascii=False),
        "tag_dimensions_json": json.dumps(review.get("TagDimensions") or {}, ensure_ascii=False),
        "photos_count": len(photos),
        "photo_urls": " | ".join(extract_photo_urls(photos)),
        "incentivized_review": "incentivizedReview" in badges_order or "IncentivizedReview" in context_data,
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


def build_excel_file(summary: ReviewBatchSummary, reviews_df: pd.DataFrame) -> bytes:
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

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        reviews_df.to_excel(writer, sheet_name="Reviews", index=False)

        for sheet_name, df in {"Summary": summary_df, "Reviews": reviews_df}.items():
            worksheet = writer.sheets[sheet_name]
            for idx, column in enumerate(df.columns, start=1):
                max_len = max(len(str(column)), *(len(str(v)) for v in df[column].head(200).fillna("")))
                worksheet.column_dimensions[get_column_letter(idx)].width = min(max_len + 2, 50)

    output.seek(0)
    return output.getvalue()


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(
    "Paste a SharkNinja product page, pull the product ID, calculate how many Bazaarvoice requests are needed, "
    "and download all reviews to Excel."
)

with st.sidebar:
    st.header("Bazaarvoice settings")
    passkey = st.text_input("Passkey", value=DEFAULT_PASSKEY, type="password")
    displaycode = st.text_input("Display code", value=DEFAULT_DISPLAYCODE)
    api_version = st.text_input("API version", value=DEFAULT_API_VERSION)
    page_size = st.number_input("Reviews per request", min_value=1, max_value=100, value=DEFAULT_PAGE_SIZE, step=1)
    sort = st.text_input("Sort", value=DEFAULT_SORT)
    content_locales = st.text_area("Content locale filter", value=DEFAULT_CONTENT_LOCALES, height=120)
    st.caption("The defaults are based on the SharkNinja AF181 Bazaarvoice calls you shared.")

product_url = st.text_input(
    "Product URL",
    value="https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
    help="Example: https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html",
)

run_button = st.button("Pull all reviews and build Excel", type="primary")

if run_button:
    try:
        product_url = normalize_product_url(product_url)
        session = get_session()

        with st.spinner("Loading product page..."):
            html = fetch_product_html(session, product_url)
            product_id = extract_product_id(product_url, html)

        with st.spinner("Checking total review count..."):
            total_reviews = get_total_reviews(
                session,
                product_id=product_id,
                passkey=passkey,
                displaycode=displaycode,
                api_version=api_version,
                sort=sort,
                content_locales=content_locales,
            )

        requests_needed = math.ceil(total_reviews / page_size) if total_reviews else 0

        metric_cols = st.columns(3)
        metric_cols[0].metric("Product ID", product_id)
        metric_cols[1].metric("Total reviews", total_reviews)
        metric_cols[2].metric("Requests needed", requests_needed)

        raw_reviews = fetch_all_reviews(
            session,
            product_id=product_id,
            passkey=passkey,
            displaycode=displaycode,
            api_version=api_version,
            page_size=int(page_size),
            sort=sort,
            content_locales=content_locales,
            total_reviews=total_reviews,
        )

        reviews_df = pd.DataFrame(flatten_review(review) for review in raw_reviews)
        summary = ReviewBatchSummary(
            product_url=product_url,
            product_id=product_id,
            total_reviews=total_reviews,
            page_size=int(page_size),
            requests_needed=requests_needed,
            reviews_downloaded=len(reviews_df),
        )

        excel_bytes = build_excel_file(summary, reviews_df)
        file_name = f"{product_id}_reviews_{pd.Timestamp.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"

        st.success("Done. Your Excel file is ready.")
        st.download_button(
            label="Download Excel",
            data=excel_bytes,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        with st.expander("Preview first 20 reviews"):
            st.dataframe(reviews_df.head(20), use_container_width=True)

    except requests.HTTPError as exc:
        st.error(f"HTTP error: {exc}")
    except ReviewDownloaderError as exc:
        st.error(str(exc))
    except Exception as exc:  # pragma: no cover - useful for Streamlit UI debugging
        st.exception(exc)
