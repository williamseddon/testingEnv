import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TITLE = "Bazaarvoice Conversations Review Puller"
DEFAULT_API_VERSION = "5.4"
DEFAULT_ENV = "production"
DEFAULT_LIMIT = 100
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


# -----------------------------
# Helpers
# -----------------------------
def get_api_domain(environment: str) -> str:
    return "api.bazaarvoice.com" if environment == "production" else "stg.api.bazaarvoice.com"


def normalize_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url:
        return raw_url
    if not raw_url.startswith(("http://", "https://")):
        raw_url = f"https://{raw_url}"
    return raw_url


def safe_get(url: str) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    return requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)


def extract_json_candidates_from_html(html: str) -> List[str]:
    candidates: List[str] = []

    # JSON-LD scripts
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        candidates.append(match.group(1))

    # Generic scripts that might contain product metadata or Bazaarvoice config
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL):
        body = match.group(1)
        if any(token in body.lower() for token in ["bazaarvoice", "productid", "product_id", "sku", "model"]):
            candidates.append(body)

    return candidates


def collect_meta_candidates(soup: BeautifulSoup) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name") or tag.get("itemprop")
        content = tag.get("content")
        if key and content:
            found[key.strip()] = content.strip()
    return found


def find_product_id_in_text(text: str) -> List[Tuple[str, str]]:
    patterns = [
        ("bazaarvoice_productid", r'(?i)["\']productid["\']\s*[:=]\s*["\']([^"\']{1,200})["\']'),
        ("product_id", r'(?i)["\']product[_-]?id["\']\s*[:=]\s*["\']([^"\']{1,200})["\']'),
        ("sku", r'(?i)["\']sku["\']\s*[:=]\s*["\']([^"\']{1,200})["\']'),
        ("model", r'(?i)["\']model(?:number)?["\']\s*[:=]\s*["\']([^"\']{1,200})["\']'),
        ("data_bv_product_id", r'(?i)data-bv-product-id\s*=\s*["\']([^"\']{1,200})["\']'),
        ("data_product_id", r'(?i)data-product-id\s*=\s*["\']([^"\']{1,200})["\']'),
    ]

    hits: List[Tuple[str, str]] = []
    for label, pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1).strip()
            if value:
                hits.append((label, value))
    return hits


def score_candidate(value: str) -> int:
    score = 0
    if not value:
        return score
    if len(value) > 2:
        score += 1
    if re.search(r"[A-Za-z]", value):
        score += 2
    if re.search(r"\d", value):
        score += 2
    if any(ch in value for ch in ["-", "_", "."]):
        score += 1
    if value.lower() not in {"product", "sku", "id", "null", "none", "undefined"}:
        score += 2
    if len(value) > 80:
        score -= 2
    return score


def dedupe_ranked_candidates(candidates: List[Tuple[str, str]]) -> List[Tuple[str, str, int]]:
    seen = set()
    ranked: List[Tuple[str, str, int]] = []
    for source, value in candidates:
        value = value.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ranked.append((source, value, score_candidate(value)))
    ranked.sort(key=lambda x: x[2], reverse=True)
    return ranked


def detect_possible_product_id(page_url: str) -> Dict[str, Any]:
    page_url = normalize_url(page_url)
    response = safe_get(page_url)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    candidates: List[Tuple[str, str]] = []

    # HTML attributes
    for attr in ["data-bv-product-id", "data-product-id", "data-sku", "data-model-number"]:
        for tag in soup.find_all(attrs={attr: True}):
            value = tag.get(attr)
            if value:
                candidates.append((attr, value))

    # Meta tags
    meta_map = collect_meta_candidates(soup)
    for key, value in meta_map.items():
        key_lower = key.lower()
        if any(token in key_lower for token in ["sku", "product", "model"]):
            candidates.append((f"meta:{key}", value))

    # JSON-LD and scripts
    for blob in extract_json_candidates_from_html(html):
        candidates.extend(find_product_id_in_text(blob))
        try:
            parsed = json.loads(blob)
            parsed_items = parsed if isinstance(parsed, list) else [parsed]
            for item in parsed_items:
                if isinstance(item, dict):
                    for key in ["sku", "productID", "productId", "mpn"]:
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            candidates.append((f"jsonld:{key}", value.strip()))
                if isinstance(item, dict) and "offers" in item and isinstance(item["offers"], dict):
                    maybe_sku = item["offers"].get("sku")
                    if isinstance(maybe_sku, str) and maybe_sku.strip():
                        candidates.append(("jsonld:offers.sku", maybe_sku.strip()))
        except Exception:
            pass

    # Raw page text fallback
    candidates.extend(find_product_id_in_text(html))

    # URL fallback
    parsed_url = urlparse(page_url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if path_parts:
        last = path_parts[-1]
        slug_parts = [part for part in re.split(r"[-_]", last) if part]
        if slug_parts:
            candidates.append(("url:last-segment", last))

    ranked = dedupe_ranked_candidates(candidates)

    return {
        "url": page_url,
        "status_code": response.status_code,
        "title": soup.title.text.strip() if soup.title and soup.title.text else "",
        "is_bazaarvoice_present": "bazaarvoice" in html.lower(),
        "top_candidate": ranked[0][1] if ranked else None,
        "candidates": ranked,
    }


def fetch_reviews(
    passkey: str,
    product_id: str,
    environment: str,
    limit: int,
    offset: int = 0,
    include_products: bool = True,
    sort: str = "SubmissionTime:desc",
) -> Dict[str, Any]:
    domain = get_api_domain(environment)
    endpoint = f"https://{domain}/data/reviews.json"
    params = {
        "apiversion": DEFAULT_API_VERSION,
        "passkey": passkey,
        "Filter": f"ProductId:{product_id}",
        "Limit": limit,
        "Offset": offset,
        "Sort": sort,
        "Include": "Products" if include_products else None,
        "Stats": "Reviews" if include_products else None,
    }
    params = {k: v for k, v in params.items() if v is not None}

    response = requests.get(
        endpoint,
        params=params,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        raise RuntimeError(f"Unexpected response type: {content_type or 'unknown'}")

    payload = response.json()
    if response.status_code >= 400:
        raise RuntimeError(json.dumps(payload, indent=2))
    return payload


def fetch_all_reviews(
    passkey: str,
    product_id: str,
    environment: str,
    page_size: int,
    max_reviews: int,
) -> Dict[str, Any]:
    all_reviews: List[Dict[str, Any]] = []
    offset = 0
    first_payload: Optional[Dict[str, Any]] = None

    while len(all_reviews) < max_reviews:
        limit = min(page_size, max_reviews - len(all_reviews))
        payload = fetch_reviews(
            passkey=passkey,
            product_id=product_id,
            environment=environment,
            limit=limit,
            offset=offset,
        )
        if first_payload is None:
            first_payload = payload

        chunk = payload.get("Results", []) or []
        if not chunk:
            break

        all_reviews.extend(chunk)
        offset += len(chunk)

        total_results = payload.get("TotalResults", 0)
        if len(all_reviews) >= total_results:
            break

    if first_payload is None:
        first_payload = {}

    first_payload["Results"] = all_reviews[:max_reviews]
    return first_payload


def flatten_review(review: Dict[str, Any]) -> Dict[str, Any]:
    badges = review.get("Badges", {}) or {}
    contextual = review.get("ContextDataValues", {}) or {}
    photos = review.get("Photos", []) or []
    videos = review.get("Videos", []) or []

    flattened = {
        "ReviewId": review.get("Id"),
        "ProductId": review.get("ProductId"),
        "Title": review.get("Title"),
        "ReviewText": review.get("ReviewText"),
        "Rating": review.get("Rating"),
        "IsRecommended": review.get("IsRecommended"),
        "UserNickname": review.get("UserNickname"),
        "SubmissionTime": review.get("SubmissionTime"),
        "LastModificationTime": review.get("LastModificationTime"),
        "TotalPositiveFeedbackCount": review.get("TotalPositiveFeedbackCount"),
        "TotalNegativeFeedbackCount": review.get("TotalNegativeFeedbackCount"),
        "Badges": json.dumps(badges, ensure_ascii=False),
        "ContextDataValues": json.dumps(contextual, ensure_ascii=False),
        "PhotoCount": len(photos),
        "VideoCount": len(videos),
        "SyndicationSource": review.get("SyndicationSource"),
        "Locale": review.get("ContentLocale"),
    }
    return flattened


def reviews_to_dataframe(payload: Dict[str, Any]) -> pd.DataFrame:
    rows = [flatten_review(review) for review in payload.get("Results", []) or []]
    return pd.DataFrame(rows)


def summarize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    includes = payload.get("Includes", {}) or {}
    products = includes.get("Products", {}) or {}
    product_name = None
    average_rating = None
    review_count = payload.get("TotalResults")

    if products:
        first_key = next(iter(products))
        first_product = products.get(first_key, {}) or {}
        product_name = first_product.get("Name")
        stats = first_product.get("ReviewStatistics", {}) or {}
        average_rating = stats.get("AverageOverallRating")
        if stats.get("TotalReviewCount") is not None:
            review_count = stats.get("TotalReviewCount")

    return {
        "product_name": product_name,
        "average_rating": average_rating,
        "review_count": review_count,
        "has_errors": bool(payload.get("Errors")),
        "has_form_errors": bool(payload.get("FormErrors")),
    }


def make_download_filename(product_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", product_id).strip("_") or "reviews"
    return f"bazaarvoice_reviews_{cleaned}.csv"


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(
    "Paste a product page URL, let the app try to detect the product ID, then pull reviews from the Bazaarvoice Conversations API."
)

with st.sidebar:
    st.header("API settings")
    # Load passkey from Streamlit secrets
    passkey = st.secrets.get("BAZAARVOICE_PASSKEY", "")

    if not passkey:
        st.warning("No Bazaarvoice passkey found in secrets.toml. Add BAZAARVOICE_PASSKEY to your Streamlit secrets."),
        help="Use a Conversations API passkey that has access to the target client instance.",
    )
    environment = st.selectbox("Environment", options=["production", "staging"], index=0)
    page_size = st.number_input("API page size", min_value=1, max_value=100, value=100)
    max_reviews = st.number_input("Max reviews to fetch", min_value=1, max_value=5000, value=500)
    st.markdown("---")
    st.markdown(
        "**Notes**\n\n"
        "- The app uses the Bazaarvoice Reviews display endpoint.\n"
        "- URL-to-product-ID detection is heuristic because every site exposes product data differently.\n"
        "- If auto-detection misses, paste the Product ID manually."
    )

col1, col2 = st.columns([2, 1])
with col1:
    website_url = st.text_input("Product page URL", placeholder="https://www.example.com/products/widget-123")
with col2:
    manual_product_id = st.text_input("Manual Product ID override", placeholder="Optional")

run_detection = st.button("Detect product ID", use_container_width=True)
pull_reviews = st.button("Pull reviews", type="primary", use_container_width=True)

if run_detection:
    if not website_url.strip():
        st.error("Enter a product page URL first.")
    else:
        try:
            detection = detect_possible_product_id(website_url)
            st.session_state["detected_product_id"] = detection.get("top_candidate")
            st.session_state["detection_debug"] = detection

            left, right = st.columns(2)
            with left:
                st.success("Page scanned.")
                st.write("**Page title:**", detection.get("title") or "Not found")
                st.write("**Bazaarvoice markers detected:**", "Yes" if detection.get("is_bazaarvoice_present") else "No")
                st.write("**Best candidate Product ID:**", detection.get("top_candidate") or "Not found")
            with right:
                st.write("**Candidate values found on page**")
                candidates = detection.get("candidates", [])
                if candidates:
                    candidate_df = pd.DataFrame(candidates, columns=["Source", "Value", "Score"])
                    st.dataframe(candidate_df, use_container_width=True)
                else:
                    st.info("No product ID candidates were detected.")
        except Exception as exc:
            st.exception(exc)

current_product_id = manual_product_id.strip() or st.session_state.get("detected_product_id", "")
if current_product_id:
    st.info(f"Using Product ID: `{current_product_id}`")

if pull_reviews:
    if not passkey.strip():
        st.error("Enter a Bazaarvoice passkey in the sidebar.")
    elif not current_product_id:
        st.error("Detect a Product ID or paste one manually before pulling reviews.")
    else:
        try:
            with st.spinner("Fetching reviews from Bazaarvoice..."):
                payload = fetch_all_reviews(
                    passkey=passkey.strip(),
                    product_id=current_product_id,
                    environment=environment,
                    page_size=int(page_size),
                    max_reviews=int(max_reviews),
                )

            summary = summarize_payload(payload)
            df = reviews_to_dataframe(payload)

            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("Reviews returned", int(len(df)))
            metric2.metric("Average rating", summary.get("average_rating") or "N/A")
            metric3.metric("Product", summary.get("product_name") or current_product_id)

            tab1, tab2, tab3 = st.tabs(["Reviews", "Raw JSON", "Summary"])
            with tab1:
                if df.empty:
                    st.warning("No reviews were returned for this Product ID.")
                else:
                    st.dataframe(df, use_container_width=True)
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="Download reviews as CSV",
                        data=csv_bytes,
                        file_name=make_download_filename(current_product_id),
                        mime="text/csv",
                    )

            with tab2:
                st.json(payload)

            with tab3:
                st.write(
                    {
                        "product_id": current_product_id,
                        "product_name": summary.get("product_name"),
                        "average_rating": summary.get("average_rating"),
                        "review_count_reported": summary.get("review_count"),
                        "reviews_loaded_in_app": len(df),
                    }
                )

        except Exception as exc:
            st.exception(exc)

st.markdown("---")
st.subheader("Implementation notes")
st.markdown(
    "This app works best when the page exposes a stable product identifier in HTML, JSON-LD, or Bazaarvoice-related scripts. "
    "If the retailer uses client-side rendering or hides the product ID, the manual override field is the fallback."
)
