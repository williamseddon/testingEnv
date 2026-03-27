import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TITLE = "SharkNinja Bazaarvoice Review Puller"
REQUEST_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_API_VERSION = "5.5"
DEFAULT_DISPLAY_CODE = "15973_3_0-en_us"
DEFAULT_LIMIT = 100
DEFAULT_MAX_REVIEWS = 500
DEFAULT_LIMIT_COMMENTS = 3
DEFAULT_SORT = "relevancy:a1"
DEFAULT_LOCALE_FILTER = (
    "en_US,ar*,zh*,hr*,cs*,da*,nl*,en*,et*,fi*,fr*,de*,el*,he*,hu*,"
    "id*,it*,ja*,ko*,lv*,lt*,ms*,no*,pl*,pt*,ro*,sk*,sl*,es*,sv*,th*,"
    "tr*,vi*,en_AU,en_CA,en_GB"
)
SUPPORTED_SHARKNINJA_DOMAINS = {
    "www.sharkninja.com",
    "sharkninja.com",
    "www.sharkclean.com",
    "sharkclean.com",
    "www.ninjakitchen.com",
    "ninjakitchen.com",
    "www.sharkbeauty.com",
    "sharkbeauty.com",
}
BV_ENDPOINT = "https://api.bazaarvoice.com/data/reviews.json"


def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def normalize_text(value: str) -> str:
    return value.strip()


def safe_get(url: str) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)


def is_bazaarvoice_api_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return "bazaarvoice.com" in (parsed.netloc or "") and parsed.path.endswith("/data/reviews.json")
    except Exception:
        return False


def is_supported_sharkninja_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (parsed.scheme in {"http", "https"}) and (parsed.netloc.lower() in SUPPORTED_SHARKNINJA_DOMAINS)
    except Exception:
        return False


def clean_product_id(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"\.html$", "", value, flags=re.IGNORECASE)
    value = value.strip("/")
    return value


def looks_like_product_id(value: str) -> bool:
    value = clean_product_id(value)
    if not value:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{3,60}", value) and re.search(r"\d", value))


def extract_product_id_from_sharkninja_url(page_url: str) -> Optional[str]:
    parsed = urlparse(page_url)
    path = parsed.path or ""
    query = parse_qs(parsed.query or "")

    match = re.search(r"/([A-Za-z0-9_-]+)\.html$", path, flags=re.IGNORECASE)
    if match:
        candidate = clean_product_id(match.group(1))
        if looks_like_product_id(candidate):
            return candidate

    for key in query.keys():
        match = re.match(r"dwvar_([A-Za-z0-9_-]+)_", key, flags=re.IGNORECASE)
        if match:
            candidate = clean_product_id(match.group(1))
            if looks_like_product_id(candidate):
                return candidate

    for segment in reversed([part for part in path.split("/") if part]):
        candidate = clean_product_id(segment)
        if looks_like_product_id(candidate):
            return candidate

    return None


def parse_bv_style_url(bv_url: str) -> Dict[str, Optional[str]]:
    parsed = urlparse(bv_url)
    query = parse_qs(parsed.query or "")

    product_id = None
    for raw_filter in query.get("filter", []):
        match = re.search(r"productid:.*?:([A-Za-z0-9_-]{3,60})", raw_filter, flags=re.IGNORECASE)
        if match:
            product_id = match.group(1)
            break

    return {
        "product_id": clean_product_id(product_id or "") or None,
        "displaycode": (query.get("displaycode", [None])[0] or None),
        "apiversion": (query.get("apiversion", [None])[0] or None),
        "sort": (query.get("sort", [None])[0] or None),
        "limit": (query.get("limit", [None])[0] or None),
        "offset": (query.get("offset", [None])[0] or None),
    }


def extract_json_candidates_from_html(html: str) -> List[str]:
    candidates: List[str] = []

    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        candidates.append(match.group(1))

    for match in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL):
        body = match.group(1)
        lowered = body.lower()
        if any(token in lowered for token in ["productid", "sku", "item no", "itemno", "model"]):
            candidates.append(body)

    return candidates


def find_product_id_in_text(text: str) -> List[str]:
    patterns = [
        r'(?i)["\']productid["\']\s*[:=]\s*["\']([^"\']{1,120})["\']',
        r'(?i)["\']product[_-]?id["\']\s*[:=]\s*["\']([^"\']{1,120})["\']',
        r'(?i)["\']sku["\']\s*[:=]\s*["\']([^"\']{1,120})["\']',
        r'(?i)item\s*no\.?\s*[:#]?\s*([A-Z0-9_-]{3,60})',
        r'(?i)data-bv-product-id\s*=\s*["\']([^"\']{1,120})["\']',
        r'(?i)data-product-id\s*=\s*["\']([^"\']{1,120})["\']',
    ]

    results: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = clean_product_id(match.group(1))
            if looks_like_product_id(candidate):
                results.append(candidate)
    return results


def detect_product_id_from_page(page_url: str) -> Dict[str, Any]:
    response = safe_get(page_url)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    candidates: List[str] = []

    url_candidate = extract_product_id_from_sharkninja_url(page_url)
    if url_candidate:
        candidates.append(url_candidate)

    for attr in ["data-bv-product-id", "data-product-id", "data-sku", "data-model-number"]:
        for tag in soup.find_all(attrs={attr: True}):
            value = clean_product_id(str(tag.get(attr)))
            if looks_like_product_id(value):
                candidates.append(value)

    for blob in extract_json_candidates_from_html(html):
        candidates.extend(find_product_id_in_text(blob))
        try:
            parsed = json.loads(blob)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if isinstance(item, dict):
                    for key in ["sku", "productID", "productId", "mpn"]:
                        value = item.get(key)
                        if isinstance(value, str):
                            cleaned = clean_product_id(value)
                            if looks_like_product_id(cleaned):
                                candidates.append(cleaned)
        except Exception:
            pass

    candidates.extend(find_product_id_in_text(html))

    deduped: List[str] = []
    seen = set()
    for item in candidates:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return {
        "title": soup.title.text.strip() if soup.title and soup.title.text else "",
        "status_code": response.status_code,
        "top_candidate": deduped[0] if deduped else None,
        "candidates": deduped,
    }


def resolve_input_to_product_id(user_input: str, manual_override: str = "") -> Dict[str, Any]:
    manual_override = clean_product_id(manual_override)
    if manual_override:
        return {
            "mode": "manual_product_id",
            "product_id": manual_override,
            "displaycode_from_input": None,
            "details": {"source": "manual override"},
        }

    value = normalize_text(user_input)
    if not value:
        return {
            "mode": "empty",
            "product_id": None,
            "displaycode_from_input": None,
            "details": {},
        }

    if is_bazaarvoice_api_url(value):
        parsed = parse_bv_style_url(value)
        return {
            "mode": "bazaarvoice_url",
            "product_id": parsed.get("product_id"),
            "displaycode_from_input": parsed.get("displaycode"),
            "details": parsed,
        }

    if is_supported_sharkninja_url(value):
        direct_candidate = extract_product_id_from_sharkninja_url(value)
        if direct_candidate:
            return {
                "mode": "sharkninja_url",
                "product_id": direct_candidate,
                "displaycode_from_input": None,
                "details": {"source": "url path / dwvar", "url": value},
            }

        detected = detect_product_id_from_page(value)
        return {
            "mode": "sharkninja_url_scraped",
            "product_id": detected.get("top_candidate"),
            "displaycode_from_input": None,
            "details": detected,
        }

    cleaned = clean_product_id(value)
    if looks_like_product_id(cleaned):
        return {
            "mode": "product_id",
            "product_id": cleaned,
            "displaycode_from_input": None,
            "details": {"source": "direct product id"},
        }

    return {
        "mode": "unknown",
        "product_id": None,
        "displaycode_from_input": None,
        "details": {"source": "unrecognized input", "value": value},
    }


def build_bv_params(
    product_id: str,
    passkey: str,
    displaycode: str,
    apiversion: str,
    limit: int,
    offset: int,
    sort: str,
    locale_filter: str,
    limit_comments: int,
) -> List[Tuple[str, str]]:
    return [
        ("resource", "reviews"),
        ("action", "REVIEWS_N_STATS"),
        ("filter", f"productid:eq:{product_id}"),
        ("filter", f"contentlocale:eq:{locale_filter}"),
        ("filter", "isratingsonly:eq:false"),
        ("filter_reviews", f"contentlocale:eq:{locale_filter}"),
        ("include", "authors,products,comments"),
        ("filteredstats", "reviews"),
        ("Stats", "Reviews"),
        ("limit", str(limit)),
        ("offset", str(offset)),
        ("limit_comments", str(limit_comments)),
        ("sort", sort),
        ("passkey", passkey),
        ("apiversion", apiversion),
        ("displaycode", displaycode),
    ]


def build_preview_url(params: List[Tuple[str, str]]) -> str:
    safe_params = [(k, v) for k, v in params if k.lower() != "passkey"]
    return f"{BV_ENDPOINT}?{urlencode(safe_params, doseq=True)}"


def fetch_reviews_page_style(
    passkey: str,
    product_id: str,
    displaycode: str,
    apiversion: str,
    limit: int,
    offset: int,
    sort: str,
    locale_filter: str,
    limit_comments: int,
) -> Dict[str, Any]:
    params = build_bv_params(
        product_id=product_id,
        passkey=passkey,
        displaycode=displaycode,
        apiversion=apiversion,
        limit=limit,
        offset=offset,
        sort=sort,
        locale_filter=locale_filter,
        limit_comments=limit_comments,
    )

    response = requests.get(
        BV_ENDPOINT,
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

    payload["_request_preview_url"] = build_preview_url(params)
    return payload


def fetch_all_reviews_page_style(
    passkey: str,
    product_id: str,
    displaycode: str,
    apiversion: str,
    page_size: int,
    max_reviews: int,
    sort: str,
    locale_filter: str,
    limit_comments: int,
) -> Dict[str, Any]:
    all_reviews: List[Dict[str, Any]] = []
    offset = 0
    first_payload: Optional[Dict[str, Any]] = None

    while len(all_reviews) < max_reviews:
        limit = min(page_size, max_reviews - len(all_reviews))
        payload = fetch_reviews_page_style(
            passkey=passkey,
            product_id=product_id,
            displaycode=displaycode,
            apiversion=apiversion,
            limit=limit,
            offset=offset,
            sort=sort,
            locale_filter=locale_filter,
            limit_comments=limit_comments,
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
    }


def flatten_review(review: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    includes = payload.get("Includes", {}) or {}
    comments_lookup = includes.get("Comments", {}) or {}
    authors_lookup = includes.get("Authors", {}) or {}

    comment_ids = review.get("CommentIds", []) or []
    comment_texts: List[str] = []
    for comment_id in comment_ids:
        comment = comments_lookup.get(str(comment_id)) or comments_lookup.get(comment_id) or {}
        comment_text = comment.get("CommentText") or comment.get("Comment") or ""
        if comment_text:
            comment_texts.append(comment_text)

    author = authors_lookup.get(str(review.get("AuthorId"))) or authors_lookup.get(review.get("AuthorId")) or {}
    badges = review.get("Badges", {}) or {}
    contextual = review.get("ContextDataValues", {}) or {}

    return {
        "ReviewId": review.get("Id"),
        "ProductId": review.get("ProductId"),
        "Title": review.get("Title"),
        "ReviewText": review.get("ReviewText"),
        "Rating": review.get("Rating"),
        "IsRecommended": review.get("IsRecommended"),
        "UserNickname": review.get("UserNickname"),
        "AuthorId": review.get("AuthorId"),
        "AuthorLocation": author.get("Location"),
        "SubmissionTime": review.get("SubmissionTime"),
        "LastModificationTime": review.get("LastModificationTime"),
        "Locale": review.get("ContentLocale"),
        "SyndicationSource": review.get("SyndicationSource"),
        "HelpfulYes": review.get("TotalPositiveFeedbackCount"),
        "HelpfulNo": review.get("TotalNegativeFeedbackCount"),
        "CommentCount": len(comment_texts),
        "Comments": " | ".join(comment_texts),
        "Badges": json.dumps(badges, ensure_ascii=False),
        "ContextDataValues": json.dumps(contextual, ensure_ascii=False),
    }


def reviews_to_dataframe(payload: Dict[str, Any]) -> pd.DataFrame:
    rows = [flatten_review(review, payload) for review in payload.get("Results", []) or []]
    return pd.DataFrame(rows)


def make_download_filename(product_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", product_id).strip("_") or "reviews"
    return f"sharkninja_bazaarvoice_reviews_{cleaned}.csv"


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(
    "Paste a SharkNinja product URL, a SharkNinja product ID, or a Bazaarvoice reviews URL. "
    "The app resolves the Product ID, then calls Bazaarvoice using the same request style used on SharkNinja pages."
)

with st.sidebar:
    st.header("SharkNinja Bazaarvoice settings")

    passkey = get_secret("BAZAARVOICE_PASSKEY") or get_secret("SHARKNINJA_BV_PASSKEY")
    displaycode_default = get_secret("SHARKNINJA_BV_DISPLAYCODE", DEFAULT_DISPLAY_CODE)

    if not passkey:
        st.error("Missing Bazaarvoice passkey. Add BAZAARVOICE_PASSKEY or SHARKNINJA_BV_PASSKEY to Streamlit secrets.")

    displaycode = st.text_input("Display code", value=displaycode_default)
    apiversion = st.text_input("API version", value=DEFAULT_API_VERSION)
    page_size = st.number_input("API page size", min_value=1, max_value=100, value=DEFAULT_LIMIT)
    max_reviews = st.number_input("Max reviews to fetch", min_value=1, max_value=5000, value=DEFAULT_MAX_REVIEWS)
    limit_comments = st.number_input("Max comments per review", min_value=0, max_value=20, value=DEFAULT_LIMIT_COMMENTS)
    sort = st.selectbox(
        "Sort",
        options=["relevancy:a1", "SubmissionTime:desc", "Rating:desc", "Rating:asc"],
        index=0,
    )
    locale_filter = st.text_area("Content locale filter", value=DEFAULT_LOCALE_FILTER, height=120)

    st.markdown("---")
    st.markdown(
        "**Supported inputs**\n\n"
        "- SharkNinja product page URL\n"
        "- Direct product ID such as `HT400PU` or `RV2820YE`\n"
        "- Existing Bazaarvoice reviews URL\n\n"
        "For SharkNinja product pages, the app first tries to extract the model code from the URL path like `HT400PU.html`, "
        "then falls back to page inspection if needed."
    )

input_value = st.text_input(
    "Paste SharkNinja product URL, Product ID, or Bazaarvoice reviews URL",
    placeholder="https://www.sharkninja.com/.../HT400PU.html or RV2820YE or https://api.bazaarvoice.com/data/reviews.json?...",
)
manual_product_id = st.text_input("Optional manual Product ID override", placeholder="Leave blank unless you want to force a Product ID")

col_a, col_b = st.columns(2)
with col_a:
    inspect_input = st.button("Inspect input", use_container_width=True)
with col_b:
    pull_reviews = st.button("Pull reviews", type="primary", use_container_width=True)

if inspect_input:
    try:
        resolution = resolve_input_to_product_id(input_value, manual_product_id)
        st.session_state["resolution"] = resolution
        st.session_state["resolved_product_id"] = resolution.get("product_id")
        st.session_state["input_displaycode"] = resolution.get("displaycode_from_input")

        st.subheader("Resolved input")
        st.write(
            {
                "mode": resolution.get("mode"),
                "product_id": resolution.get("product_id"),
                "displaycode_from_input": resolution.get("displaycode_from_input"),
                "details": resolution.get("details"),
            }
        )
    except Exception as exc:
        st.exception(exc)

resolved_product_id = clean_product_id(manual_product_id) or st.session_state.get("resolved_product_id", "")
if resolved_product_id:
    st.info(f"Using Product ID: `{resolved_product_id}`")

if pull_reviews:
    try:
        resolution = resolve_input_to_product_id(input_value, manual_product_id)
        product_id = resolution.get("product_id")
        displaycode_to_use = resolution.get("displaycode_from_input") or displaycode

        if not passkey:
            st.error("Missing Bazaarvoice passkey in Streamlit secrets.")
        elif not product_id:
            st.error("Could not resolve a Product ID from the input. Paste a SharkNinja product URL, product ID, or Bazaarvoice reviews URL.")
        else:
            with st.spinner("Fetching SharkNinja Bazaarvoice reviews..."):
                payload = fetch_all_reviews_page_style(
                    passkey=passkey,
                    product_id=product_id,
                    displaycode=displaycode_to_use,
                    apiversion=apiversion,
                    page_size=int(page_size),
                    max_reviews=int(max_reviews),
                    sort=sort,
                    locale_filter=locale_filter,
                    limit_comments=int(limit_comments),
                )

            df = reviews_to_dataframe(payload)
            summary = summarize_payload(payload)

            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("Reviews returned", int(len(df)))
            metric2.metric("Average rating", summary.get("average_rating") or "N/A")
            metric3.metric("Product", summary.get("product_name") or product_id)

            with st.expander("Request preview", expanded=False):
                st.code(payload.get("_request_preview_url", ""), language="text")

            tab1, tab2, tab3 = st.tabs(["Reviews", "Raw JSON", "Summary"])

            with tab1:
                if df.empty:
                    st.warning("No reviews were returned for this Product ID.")
                else:
                    st.dataframe(df, use_container_width=True)
                    st.download_button(
                        label="Download reviews as CSV",
                        data=df.to_csv(index=False).encode("utf-8"),
                        file_name=make_download_filename(product_id),
                        mime="text/csv",
                    )

            with tab2:
                st.json(payload)

            with tab3:
                st.write(
                    {
                        "input_mode": resolution.get("mode"),
                        "product_id": product_id,
                        "displaycode_used": displaycode_to_use,
                        "api_version": apiversion,
                        "sort": sort,
                        "average_rating": summary.get("average_rating"),
                        "review_count_reported": summary.get("review_count"),
                        "reviews_loaded_in_app": len(df),
                    }
                )
    except Exception as exc:
        st.exception(exc)

st.markdown("---")
st.subheader("Streamlit secrets")
st.code(
    'BAZAARVOICE_PASSKEY = "your_actual_passkey_here"\n'
    'SHARKNINJA_BV_DISPLAYCODE = "15973_3_0-en_us"',
    language="toml",
)

st.subheader("Design notes")
st.markdown(
    "This app is SharkNinja-specific by design. It accepts a SharkNinja product page URL, a direct Product ID, or a Bazaarvoice review URL, "
    "then requests reviews using the Bazaarvoice query style you shared."
)
