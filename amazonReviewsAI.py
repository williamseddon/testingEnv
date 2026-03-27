from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
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
SUPPORTED_COUNTRIES = sorted({v["country"] for v in HOST_TO_MARKET.values()})

VIDEO_MARKER = "This is a modal window."
ASIN_RE = re.compile(r"\b([A-Z0-9]{10})\b", re.IGNORECASE)

AI_REPORT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro"]
CHAT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro"]


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


def init_state() -> None:
    defaults: Dict[str, Any] = {
        "reviews_df": None,
        "raw_reviews": None,
        "overview": None,
        "report": None,
        "product_meta": None,
        "chat_messages": [],
        "last_scraped_url": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def inject_css() -> None:
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 1.4rem;
                padding-bottom: 2rem;
            }
            .hero-card {
                padding: 1.25rem 1.4rem;
                border-radius: 22px;
                border: 1px solid rgba(49, 51, 63, 0.12);
                background: linear-gradient(135deg, rgba(59,130,246,0.08), rgba(16,185,129,0.08));
                margin-bottom: 1rem;
            }
            .soft-card {
                padding: 1rem 1rem 0.9rem 1rem;
                border-radius: 18px;
                border: 1px solid rgba(49, 51, 63, 0.10);
                background: rgba(255,255,255,0.80);
                margin-bottom: 0.8rem;
            }
            .mini-note {
                color: #6b7280;
                font-size: 0.93rem;
            }
            .evidence-chip {
                display: inline-block;
                padding: 0.15rem 0.5rem;
                margin: 0.12rem 0.18rem 0.12rem 0;
                border-radius: 999px;
                background: rgba(15,23,42,0.06);
                border: 1px solid rgba(15,23,42,0.08);
                font-size: 0.82rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    try:
        host = urlparse(normalize_url(url)).netloc.lower()
    except Exception:
        return False
    host = host.split(":")[0]
    return "amazon." in host


def host_candidates(host: str) -> List[str]:
    host = host.lower().split(":")[0]
    parts = host.split(".")
    candidates = []
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        if candidate:
            candidates.append(candidate)
    return candidates


def detect_marketplace(url: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    try:
        host = urlparse(normalize_url(url)).netloc.lower()
    except Exception:
        return None, None
    for candidate in host_candidates(host):
        if candidate in HOST_TO_MARKET:
            return HOST_TO_MARKET[candidate], candidate
    return None, None


def extract_asin(text: str) -> str:
    raw = normalize_url(text)
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    match = ASIN_RE.search(raw)
    return match.group(1).upper() if match else ""


def parse_score_value(score: Any) -> Optional[float]:
    if score is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(score))
    return float(match.group(1)) if match else None


def parse_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    truthy = {"true", "yes", "y", "1", "verified", "verified purchase"}
    falsy = {"false", "no", "n", "0", "unverified"}
    if text in truthy:
        return True
    if text in falsy:
        return False
    return None


def pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
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


def dedupe_key(item: Dict[str, Any]) -> str:
    review_id = str(item.get("ReviewId") or "").strip()
    if review_id:
        return f"id::{review_id}"
    review_url = str(item.get("ReviewUrl") or "").strip()
    if review_url:
        return f"url::{review_url}"
    body = str(item.get("ReviewText") or "").strip()
    title = str(item.get("Title") or "").strip()
    author = str(item.get("Author") or "").strip()
    date = str(item.get("ReviewDate") or "").strip()
    rating = str(item.get("RatingValue") or "").strip()
    raw = " | ".join([author, title, date, rating, body])
    return "fp::" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def standardize_reviews(
    items: List[Dict[str, Any]],
    product_url: str,
    asin: str,
    country: str,
    marketplace_host: str,
    max_reviews: int,
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
        }
        row.update(video_meta)

        key = dedupe_key(row)
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
    avg_rating = float(df["RatingValue"].dropna().mean()) if "RatingValue" in df.columns and not df["RatingValue"].dropna().empty else None
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

    star_distribution = {}
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
        "ReviewsCollected": review_count,
        "AverageRating": round(avg_rating, 2) if avg_rating is not None else None,
        "PositiveShare": round(positive_share * 100, 1) if positive_share is not None else None,
        "NegativeShare": round(negative_share * 100, 1) if negative_share is not None else None,
        "VerifiedShare": round(verified_share * 100, 1) if verified_share is not None else None,
        "ReviewDateMin": date_min,
        "ReviewDateMax": date_max,
        "StarDistributionJSON": json.dumps(star_distribution, ensure_ascii=False),
    }
    return pd.DataFrame([overview])


def build_actor_input(product_url: str, country: str, max_reviews: int, sort_key: str, verified_only: bool) -> Dict[str, Any]:
    return {
        "ASIN_or_URL": [product_url],
        "country": country,
        "max_reviews": int(max_reviews),
        "sort_reviews_by": [sort_key],
        "filter_by_verified_purchase_only": ["avp_only_reviews" if verified_only else "all_reviews"],
        "filter_by_mediaType": ["all_contents"],
        "filter_by_ratings": ["all_stars"],
        "unique_only": True,
        "get_customers_say": False,
    }


def scrape_reviews(
    apify_token: str,
    actor_id: str,
    product_url: str,
    country: str,
    marketplace_host: str,
    max_reviews: int,
    sort_key: str,
    verified_only: bool,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any]]:
    client = ApifyClient(apify_token)
    actor_input = build_actor_input(
        product_url=product_url,
        country=country,
        max_reviews=max_reviews,
        sort_key=sort_key,
        verified_only=verified_only,
    )

    try:
        run = client.actor(actor_id).call(run_input=actor_input)
    except ApifyApiError as exc:
        raise RuntimeError(str(exc)) from exc

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError("The Apify actor did not return a dataset ID.")

    raw_items = list(client.dataset(dataset_id).iterate_items())
    asin = extract_asin(product_url)
    reviews_df = standardize_reviews(
        items=raw_items,
        product_url=product_url,
        asin=asin,
        country=country,
        marketplace_host=marketplace_host,
        max_reviews=max_reviews,
    )
    if reviews_df.empty:
        raise RuntimeError("No reviews were returned for this product URL.")

    product_title = infer_product_title(raw_items, asin)
    meta = {
        "product_url": product_url,
        "asin": asin,
        "country": country,
        "marketplace_host": marketplace_host,
        "product_title": product_title,
        "dataset_id": dataset_id,
        "run_id": run.get("id"),
    }
    return reviews_df, raw_items, meta


def build_review_context(df: pd.DataFrame, overview_df: pd.DataFrame, max_chars_per_review: int = 750) -> str:
    overview = overview_df.iloc[0].to_dict()
    header_lines = [
        f"Product title: {overview.get('ProductTitle')}",
        f"Source URL: {overview.get('SourceUrl')}",
        f"ASIN: {overview.get('ASIN')}",
        f"Marketplace: {overview.get('Marketplace')} ({overview.get('Country')})",
        f"Reviews analyzed: {overview.get('ReviewsCollected')}",
        f"Average rating: {overview.get('AverageRating')}",
        f"Positive share %: {overview.get('PositiveShare')}",
        f"Negative share %: {overview.get('NegativeShare')}",
        f"Verified share %: {overview.get('VerifiedShare')}",
        f"Review date window: {overview.get('ReviewDateMin')} to {overview.get('ReviewDateMax')}",
        f"Star distribution JSON: {overview.get('StarDistributionJSON')}",
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
                    f"[{row.get('ReviewRef')}] {row.get('RatingValue')} stars | verified={row.get('VerifiedPurchase')} | date={row.get('ReviewDate')}",
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
        "You are a senior product intelligence analyst helping product developers, quality engineers, "
        "UX researchers, and product leaders understand Amazon reviews. Use only the supplied review evidence. "
        "Do not invent facts, counts, or review IDs. Cite evidence only with the provided ReviewRef values like R001. "
        "Separate true delight drivers from detractors. Treat durability, reliability, defects, packaging issues, "
        "performance instability, and safety-adjacent concerns as quality risks. Keep the executive summary crisp and useful. "
        "If evidence is thin or mixed, say so explicitly in confidence_note."
    )

    user_prompt = (
        "Analyze this Amazon review dataset and produce a structured product intelligence report. "
        "Focus on what a product developer or quality engineer should know next.\n\n"
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
        "Prefer concise, decision-ready answers."
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


def safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[:\\/?*\[\]]", "_", name)
    return cleaned[:31]


def report_to_frames(report: ProductIntelReport) -> Dict[str, pd.DataFrame]:
    executive_rows = [{
        "ExecutiveSummary": report.executive_summary,
        "ConfidenceNote": report.confidence_note,
        "ExecutiveTakeaways": " | ".join(report.executive_takeaways),
        "JobsToBeDone": " | ".join(report.jobs_to_be_done),
        "ActionsForProduct": " | ".join(report.actions_for_product),
        "ActionsForQuality": " | ".join(report.actions_for_quality),
    }]

    def theme_rows(items: List[ThemeEvidence]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "Theme": item.theme,
                "Summary": item.summary,
                "SupportingReviews": ", ".join(item.supporting_reviews),
            }
            for item in items
        ])

    def quality_rows(items: List[QualityRisk]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "Issue": item.issue,
                "Severity": item.severity,
                "WhyItMatters": item.why_it_matters,
                "SupportingReviews": ", ".join(item.supporting_reviews),
                "SuggestedOwner": item.suggested_owner,
            }
            for item in items
        ])

    def request_rows(items: List[FeatureRequest]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "Request": item.request,
                "Rationale": item.rationale,
                "SupportingReviews": ", ".join(item.supporting_reviews),
                "SuggestedOwner": item.suggested_owner,
            }
            for item in items
        ])

    return {
        "AI_Executive": pd.DataFrame(executive_rows),
        "AI_Themes": theme_rows(report.top_themes),
        "AI_Delighters": theme_rows(report.delighters),
        "AI_Detractors": theme_rows(report.detractors),
        "AI_Quality_Risks": quality_rows(report.quality_risks),
        "AI_Feature_Requests": request_rows(report.feature_requests),
        "AI_Actions": pd.DataFrame(
            [
                {"Audience": "Product", "Action": action}
                for action in report.actions_for_product
            ]
            + [
                {"Audience": "Quality", "Action": action}
                for action in report.actions_for_quality
            ]
        ),
    }


def build_excel_bytes(
    reviews_df: pd.DataFrame,
    overview_df: pd.DataFrame,
    report: Optional[ProductIntelReport],
) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        overview_df.to_excel(writer, sheet_name="Overview", index=False)
        reviews_df.to_excel(writer, sheet_name="Reviews", index=False)
        if report:
            for sheet_name, frame in report_to_frames(report).items():
                frame.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
    buffer.seek(0)
    return buffer.read()


def render_theme_cards(items: List[ThemeEvidence], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = "".join([f'<span class="evidence-chip">{ref}</span>' for ref in item.supporting_reviews])
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{item.theme}</strong><br>
                <span>{item.summary}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_quality_cards(items: List[QualityRisk], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = "".join([f'<span class="evidence-chip">{ref}</span>' for ref in item.supporting_reviews])
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{item.issue}</strong> · <span class="mini-note">Severity: {item.severity} · Owner: {item.suggested_owner}</span><br>
                <span>{item.why_it_matters}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_request_cards(items: List[FeatureRequest], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    for item in items:
        chips = "".join([f'<span class="evidence-chip">{ref}</span>' for ref in item.supporting_reviews])
        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{item.request}</strong> · <span class="mini-note">Owner: {item.suggested_owner}</span><br>
                <span>{item.rationale}</span>
                <div style="margin-top:0.55rem;">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def reset_analysis_state() -> None:
    st.session_state["report"] = None
    st.session_state["chat_messages"] = []


def marketplace_status(url: str) -> Tuple[str, str, str]:
    market, host = detect_marketplace(url)
    asin = extract_asin(url)
    if not url:
        return "", "", ""
    if market:
        market_text = f"Detected marketplace: {host} -> {market['country']}"
    else:
        market_text = "Marketplace could not be auto-detected"
    asin_text = f"ASIN: {asin}" if asin else "ASIN: not found"
    return market_text, asin_text, host or ""


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_state()
    inject_css()

    st.title(APP_TITLE)
    st.markdown(
        """
        <div class="hero-card">
            <h3 style="margin:0 0 0.35rem 0;">From one Amazon URL to a product-intelligence workbook</h3>
            <div class="mini-note">
                Auto-detects the Amazon marketplace, scrapes up to 100 reviews with Apify, exports Excel, and layers on an OpenAI-powered product intelligence copilot for summaries, delighters, detractors, quality risks, and feature requests.
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
            value="" if not (use_secrets and apify_secret) else apify_secret,
        ).strip()
        openai_api_key = st.text_input(
            "OpenAI API key",
            type="password",
            value="" if not (use_secrets and openai_secret) else openai_secret,
        ).strip()

        st.divider()
        st.subheader("Scrape settings")
        actor_id = st.text_input("Apify actor ID", value=DEFAULT_ACTOR_ID)
        max_reviews = st.slider("Reviews to collect", min_value=10, max_value=MAX_REVIEWS_CAP, value=100, step=10)
        sort_label = st.selectbox("Review sort", options=list(SORT_OPTIONS.keys()), index=0)
        verified_only = st.toggle("Verified purchases only", value=False)
        manual_country = st.selectbox("Country override", options=["Auto-detect"] + SUPPORTED_COUNTRIES, index=0)

        st.divider()
        st.subheader("AI settings")
        report_model = st.selectbox("AI report model", options=AI_REPORT_MODELS, index=0)
        chat_model = st.selectbox("Chatbot model", options=CHAT_MODELS, index=1)
        stakeholder_lens = st.selectbox(
            "Default stakeholder lens",
            options=["Product Management", "Product Development", "Quality Engineering", "UX Research", "Customer Support"],
            index=2,
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
    info_c1, info_c2, info_c3 = st.columns([1.2, 1, 1], vertical_alignment="center")
    with info_c1:
        st.caption(market_text or "Paste an Amazon URL to begin")
    with info_c2:
        st.caption(asin_text)
    with info_c3:
        st.caption(f"Review cap: {max_reviews}")

    scrape_col, report_col, reset_col = st.columns([1.1, 1.1, 1], vertical_alignment="center")
    scrape_clicked = scrape_col.button("Fetch reviews", type="primary", use_container_width=True)
    report_clicked = report_col.button(
        "Generate AI report",
        use_container_width=True,
        disabled=st.session_state.get("reviews_df") is None,
    )
    reset_clicked = reset_col.button("Clear session", use_container_width=True)

    if reset_clicked:
        for key in ["reviews_df", "raw_reviews", "overview", "report", "product_meta", "chat_messages", "last_scraped_url"]:
            st.session_state[key] = None if key not in {"chat_messages", "last_scraped_url"} else ([] if key == "chat_messages" else "")
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
                st.error("This app could not auto-detect a supported Amazon marketplace from the URL. Use a supported URL or choose a country override.")
            else:
                with st.spinner("Scraping reviews from Amazon via Apify..."):
                    try:
                        reviews_df, raw_items, meta = scrape_reviews(
                            apify_token=apify_token,
                            actor_id=actor_id.strip(),
                            product_url=normalize_url(product_url),
                            country=chosen_country,
                            marketplace_host=detected_host or "manual_override",
                            max_reviews=max_reviews,
                            sort_key=SORT_OPTIONS[sort_label],
                            verified_only=verified_only,
                        )
                        meta["sort_label"] = sort_label
                        meta["verified_only"] = verified_only
                        overview_df = summarize_overview(reviews_df, meta)

                        st.session_state["reviews_df"] = reviews_df
                        st.session_state["raw_reviews"] = raw_items
                        st.session_state["overview"] = overview_df
                        st.session_state["product_meta"] = meta
                        st.session_state["last_scraped_url"] = normalize_url(product_url)
                        reset_analysis_state()
                        st.success(f"Collected {len(reviews_df)} reviews for {meta['product_title']}.")
                    except Exception as exc:
                        st.error(str(exc))

    if report_clicked:
        if st.session_state.get("reviews_df") is None:
            st.error("Fetch reviews first.")
        elif not openai_api_key:
            st.error("Add your OpenAI API key in the sidebar.")
        else:
            with st.spinner("Generating product intelligence report with OpenAI..."):
                try:
                    report = generate_product_intel_report(
                        openai_api_key=openai_api_key,
                        model_name=report_model,
                        reviews_df=st.session_state["reviews_df"],
                        overview_df=st.session_state["overview"],
                    )
                    st.session_state["report"] = report
                    st.success("AI report ready.")
                except Exception as exc:
                    st.error(str(exc))

    reviews_df = st.session_state.get("reviews_df")
    overview_df = st.session_state.get("overview")
    report = st.session_state.get("report")
    product_meta = st.session_state.get("product_meta") or {}

    tabs = st.tabs(["Overview", "Reviews", "AI report", "Chatbot", "Export", "Help"])

    with tabs[0]:
        if reviews_df is None or overview_df is None:
            st.info("Fetch reviews to populate the dashboard.")
        else:
            overview = overview_df.iloc[0].to_dict()
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Reviews", int(overview.get("ReviewsCollected") or 0))
            m2.metric("Average rating", overview.get("AverageRating") or "—")
            m3.metric("Positive share", f"{overview.get('PositiveShare')}%" if overview.get("PositiveShare") is not None else "—")
            m4.metric("Negative share", f"{overview.get('NegativeShare')}%" if overview.get("NegativeShare") is not None else "—")
            m5.metric("Verified share", f"{overview.get('VerifiedShare')}%" if overview.get("VerifiedShare") is not None else "—")

            c1, c2 = st.columns([1.1, 1], vertical_alignment="top")
            with c1:
                st.markdown(
                    f"""
                    <div class="soft-card">
                        <strong>{product_meta.get('product_title', 'Amazon product')}</strong><br>
                        <span class="mini-note">{product_meta.get('product_url', '')}</span><br><br>
                        <span><strong>ASIN:</strong> {product_meta.get('asin', '—')}</span><br>
                        <span><strong>Marketplace:</strong> {product_meta.get('marketplace_host', '—')}</span><br>
                        <span><strong>Country:</strong> {product_meta.get('country', '—')}</span><br>
                        <span><strong>Sort:</strong> {product_meta.get('sort_label', '—')}</span><br>
                        <span><strong>Verified only:</strong> {product_meta.get('verified_only', False)}</span><br>
                        <span><strong>Date window:</strong> {overview.get('ReviewDateMin') or '—'} to {overview.get('ReviewDateMax') or '—'}</span>
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
                    dist_df = pd.DataFrame(
                        {"Star rating": list(star_data.keys()), "Reviews": list(star_data.values())}
                    )
                    st.bar_chart(dist_df.set_index("Star rating"))
                else:
                    st.info("No star distribution available.")

    with tabs[1]:
        if reviews_df is None:
            st.info("No reviews yet.")
        else:
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
            ]
            st.dataframe(reviews_df[preview_cols], use_container_width=True, hide_index=True)

    with tabs[2]:
        if reviews_df is None:
            st.info("Fetch reviews first.")
        elif not openai_api_key:
            st.info("Add your OpenAI API key to generate the AI report.")
        elif report is None:
            st.info("Generate the AI report to unlock product intelligence views.")
        else:
            st.markdown(
                f"""
                <div class="soft-card">
                    <strong>Executive summary</strong><br>
                    <span>{report.executive_summary}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            takeaway_c1, takeaway_c2 = st.columns(2, vertical_alignment="top")
            with takeaway_c1:
                st.markdown("#### Executive takeaways")
                for item in report.executive_takeaways:
                    st.write(f"- {item}")
                st.markdown("#### Jobs to be done")
                for item in report.jobs_to_be_done:
                    st.write(f"- {item}")
            with takeaway_c2:
                st.markdown("#### Confidence note")
                st.write(report.confidence_note)
                st.markdown("#### Recommended actions")
                for item in report.actions_for_product[:4]:
                    st.write(f"- Product: {item}")
                for item in report.actions_for_quality[:4]:
                    st.write(f"- Quality: {item}")

            d1, d2 = st.columns(2, vertical_alignment="top")
            with d1:
                st.markdown("#### Delighters")
                render_theme_cards(report.delighters, "No strong delight themes detected.")
            with d2:
                st.markdown("#### Detractors")
                render_theme_cards(report.detractors, "No major detractor themes detected.")

            q1, q2 = st.columns(2, vertical_alignment="top")
            with q1:
                st.markdown("#### Quality risks")
                render_quality_cards(report.quality_risks, "No notable quality risks surfaced.")
            with q2:
                st.markdown("#### Feature requests")
                render_request_cards(report.feature_requests, "No clear feature requests surfaced.")

            st.markdown("#### Top cross-cutting themes")
            render_theme_cards(report.top_themes, "No theme map available.")

    with tabs[3]:
        if reviews_df is None:
            st.info("Fetch reviews first.")
        elif not openai_api_key:
            st.info("Add your OpenAI API key to use the chatbot.")
        else:
            current_lens = st.selectbox(
                "Stakeholder lens",
                options=["Product Management", "Product Development", "Quality Engineering", "UX Research", "Customer Support"],
                index=["Product Management", "Product Development", "Quality Engineering", "UX Research", "Customer Support"].index(stakeholder_lens),
                key="chat_lens_select",
            )

            suggestion_cols = st.columns(4)
            suggestions = [
                "What should product fix first?",
                "What would a quality engineer investigate next?",
                "What feature requests appear most often?",
                "What do 1-star reviews complain about most?",
            ]
            selected_prompt = None
            for col, suggestion in zip(suggestion_cols, suggestions):
                if col.button(suggestion, use_container_width=True):
                    selected_prompt = suggestion

            for message in st.session_state.get("chat_messages", []):
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

            prompt = st.chat_input("Ask the product intelligence chatbot")
            user_prompt = prompt or selected_prompt

            if user_prompt:
                st.session_state["chat_messages"].append({"role": "user", "content": user_prompt})
                with st.chat_message("user"):
                    st.markdown(user_prompt)

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
                        st.markdown(answer)
                st.session_state["chat_messages"].append({"role": "assistant", "content": answer})

    with tabs[4]:
        if reviews_df is None or overview_df is None:
            st.info("Fetch reviews first.")
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_root = f"amazon_product_intelligence_{timestamp}"
            excel_bytes = build_excel_bytes(reviews_df, overview_df, report)
            csv_bytes = reviews_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            overview_csv = overview_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

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

    with tabs[5]:
        st.markdown("### What changed from your original app")
        st.markdown(
            "- The workflow is now built around a single Amazon product URL.\n"
            "- The app auto-detects supported marketplaces from the URL.\n"
            "- Review collection is intentionally capped at 100 to keep the UI fast and the AI analysis grounded.\n"
            "- Excel exports now support both raw reviews and AI insight tabs.\n"
            "- A built-in OpenAI chatbot answers product and quality questions grounded in the review evidence."
        )
        st.markdown("### Best use cases")
        st.markdown(
            "- Product developers triaging feature gaps and usability issues\n"
            "- Quality engineers surfacing reliability and defect patterns\n"
            "- PMs writing executive snapshots before reviews or design critiques\n"
            "- Support leads identifying repeat pain points"
        )
        st.markdown("### Supported marketplace auto-detection")
        st.code("amazon.com, amazon.co.uk, amazon.de, amazon.fr, amazon.it, amazon.es, amazon.ca, amazon.co.jp", language="text")


if __name__ == "__main__":
    main()
