from __future__ import annotations

import html
import io
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field


APP_TITLE = "SharkNinja Review Intelligence Studio"
APP_TAGLINE = "Bazaarvoice API-first SharkNinja review retrieval, diagnostics, local snapshots, and evidence-grounded AI."
MAX_REVIEWS_CAP = 100
LOCAL_STORE_ROOT = Path("local_store") / "sharkninja_bv"
ALLOWED_HOSTS = ("sharkninja.com", "sharkclean.com")
BV_DEFAULT_HOST = "api.bazaarvoice.com"
BV_DEFAULT_API_VERSION = "5.4"
BV_DEFAULT_HEADER_VERSION = "5.4.1"
DEFAULT_LOCALE = "en_US"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SORT_OPTIONS = {
    "Most recent": "SubmissionTime:desc",
    "Most helpful": "TotalFeedbackCount:desc",
    "Highest rated": "Rating:desc",
    "Lowest rated": "Rating:asc",
}
AI_MODELS = ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4-pro"]
LENS_OPTIONS = ["Product Development", "Quality Engineer", "Consumer Insights"]
REF_RE = re.compile(r"\bR\d{3}\b")
MODEL_CODE_RE = re.compile(r"\b[A-Z]{1,8}[0-9]{2,}[A-Z0-9-]*\b")
HTML_CODE_RE = re.compile(r"(?i)(?:Item\s*(?:No\.?|#)|Model|SKU)\s*[:\-]?\s*([A-Z0-9-]{3,40})")
SCRIPT_KV_RE = re.compile(
    r"(?i)(?:productid|product_id|masterpid|master_pid|pid|sku|itemno|item_no|itemnumber|modelnumber|model_number)"
    r"\s*[:=]\s*[\"']([A-Za-z0-9_-]{3,40})[\"']"
)


class ThemeEvidence(BaseModel):
    theme: str
    summary: str
    supporting_reviews: List[str] = Field(default_factory=list)


class ProductIntelReport(BaseModel):
    executive_summary: str
    executive_takeaways: List[str] = Field(default_factory=list)
    delighters: List[ThemeEvidence] = Field(default_factory=list)
    detractors: List[ThemeEvidence] = Field(default_factory=list)
    quality_watchouts: List[ThemeEvidence] = Field(default_factory=list)
    consumer_insights: List[ThemeEvidence] = Field(default_factory=list)
    actions_for_product_development: List[str] = Field(default_factory=list)
    actions_for_quality_engineering: List[str] = Field(default_factory=list)
    actions_for_consumer_insights: List[str] = Field(default_factory=list)
    confidence_note: str


@dataclass
class PageSignals:
    source_url: str
    final_url: str
    title: str
    item_numbers: List[str] = field(default_factory=list)
    family_codes: List[str] = field(default_factory=list)
    visible_rating: Optional[float] = None
    visible_review_count: Optional[int] = None
    raw_hints: Dict[str, Any] = field(default_factory=dict)
    text_excerpt: str = ""


@dataclass
class CandidateProbe:
    candidate_id: str
    reasons: List[str] = field(default_factory=list)
    product_found: bool = False
    product_name: str = ""
    stats_review_count: Optional[int] = None
    stats_average_rating: Optional[float] = None
    review_probe_total: Optional[int] = None
    similarity_score: float = 0.0
    final_score: float = 0.0
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ResolvedProduct:
    product_id: str
    product_name: str
    review_count: Optional[int]
    average_rating: Optional[float]
    source: str


@dataclass
class FetchArtifact:
    reviews_df: pd.DataFrame
    overview_df: pd.DataFrame
    probes_df: pd.DataFrame
    api_log_df: pd.DataFrame
    meta: Dict[str, Any]
    snapshot_dir: Path


def init_state() -> None:
    defaults: Dict[str, Any] = {
        "artifact": None,
        "report": None,
        "chat_messages": [],
        "nav": "Studio",
        "last_url": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def inject_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                --sn-border: rgba(15, 23, 42, 0.10);
                --sn-card: rgba(255,255,255,0.94);
                --sn-shadow: 0 18px 48px rgba(15, 23, 42, 0.10);
                --sn-subtle: #475569;
            }
            .block-container {
                max-width: 1360px;
                padding-top: 1rem;
                padding-bottom: 2rem;
            }
            .sn-hero {
                margin-bottom: 1rem;
                padding: 1.2rem 1.35rem;
                border-radius: 26px;
                border: 1px solid rgba(255,255,255,0.5);
                background:
                    radial-gradient(circle at top left, rgba(59,130,246,0.18), transparent 32%),
                    radial-gradient(circle at top right, rgba(16,185,129,0.15), transparent 28%),
                    linear-gradient(135deg, rgba(248,250,252,0.98), rgba(239,246,255,0.98));
                box-shadow: var(--sn-shadow);
            }
            .sn-card {
                background: var(--sn-card);
                border: 1px solid var(--sn-border);
                border-radius: 22px;
                padding: 1rem 1rem 0.95rem;
                box-shadow: 0 14px 36px rgba(15, 23, 42, 0.06);
                margin-bottom: 1rem;
            }
            .sn-chip-row {
                display: flex;
                gap: 0.35rem;
                flex-wrap: wrap;
                margin-top: 0.5rem;
            }
            .sn-chip {
                display: inline-flex;
                align-items: center;
                padding: 0.16rem 0.56rem;
                border-radius: 999px;
                border: 1px solid rgba(15,23,42,0.08);
                background: rgba(255,255,255,0.92);
                font-size: 0.82rem;
            }
            .sn-overlay {
                position: sticky;
                top: 0.75rem;
                z-index: 10;
                border-radius: 22px;
                padding: 0.9rem 1rem 0.5rem;
                border: 1px solid rgba(29,78,216,0.18);
                background: linear-gradient(135deg, rgba(239,246,255,0.96), rgba(255,255,255,0.96));
                box-shadow: 0 18px 42px rgba(15,23,42,0.10);
                margin-bottom: 1rem;
            }
            .sn-ref {
                position: relative;
                display: inline-flex;
                align-items: center;
                gap: 0.25rem;
                margin: 0 0.12rem;
                padding: 0.10rem 0.46rem;
                border-radius: 999px;
                border: 1px solid rgba(29,78,216,0.18);
                background: rgba(219,234,254,0.70);
                color: #1d4ed8;
                font-weight: 600;
                cursor: help;
                text-decoration: none;
            }
            .sn-ref .sn-tooltip {
                display: none;
                position: absolute;
                left: 0;
                top: calc(100% + 8px);
                z-index: 9999;
                width: 360px;
                max-width: 80vw;
                padding: 0.72rem 0.80rem;
                border-radius: 16px;
                border: 1px solid rgba(15,23,42,0.10);
                background: rgba(255,255,255,0.98);
                color: #0f172a;
                box-shadow: 0 18px 40px rgba(15,23,42,0.18);
                white-space: normal;
                font-weight: 400;
            }
            .sn-ref:hover .sn-tooltip { display: block; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_secret(*keys: str, default: str = "") -> str:
    try:
        cur: Any = st.secrets
        for key in keys:
            cur = cur[key]
        return str(cur).strip()
    except Exception:
        return default


def get_bv_config() -> Dict[str, str]:
    host = get_secret("bazaarvoice", "api_host", default="") or get_secret("BAZAARVOICE_API_HOST", default="") or BV_DEFAULT_HOST
    env = (get_secret("bazaarvoice", "environment", default="") or "").strip().lower()
    if env == "stg":
        host = "stg.api.bazaarvoice.com"
    elif env == "prod":
        host = BV_DEFAULT_HOST
    return {
        "passkey": get_secret("bazaarvoice", "passkey", default="") or get_secret("BAZAARVOICE_PASSKEY", default=""),
        "api_version": get_secret("bazaarvoice", "api_version", default="") or BV_DEFAULT_API_VERSION,
        "api_host": host,
        "locale": get_secret("bazaarvoice", "locale", default="") or DEFAULT_LOCALE,
        "bv_header_version": get_secret("bazaarvoice", "bv_header_version", default="") or BV_DEFAULT_HEADER_VERSION,
    }


def get_openai_api_key() -> str:
    return get_secret("openai", "OPENAI_API_KEY", default="") or get_secret("OPENAI_API_KEY", default="")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def safe_int(value: Any) -> Optional[int]:
    num = safe_float(value)
    return int(round(num)) if num is not None else None


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    return urlunparse(("https", parsed.netloc.lower(), parsed.path, parsed.params, parsed.query, ""))


def is_sharkninja_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)
    except Exception:
        return False


def sanitize_candidate_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = re.sub(r"[^A-Za-z0-9_-]", "", str(value).strip()).upper()
    if len(candidate) < 3 or len(candidate) > 40 or not re.search(r"\d", candidate):
        return None
    return candidate


def unique_keep_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def family_variants(code: str) -> List[str]:
    code = sanitize_candidate_id(code) or ""
    if not code:
        return []
    variants = [code]
    if "-" in code:
        variants.append(code.split("-")[0])
    match = re.match(r"([A-Z]+[0-9]+)", code)
    if match and match.group(1) != code:
        variants.append(match.group(1))
    match2 = re.match(r"([A-Z]+[0-9]+[A-Z]+)\d+$", code)
    if match2 and match2.group(1) != code:
        variants.append(match2.group(1))
    return unique_keep_order(v for v in variants if v)


def similarity(a: str, b: str) -> float:
    a_norm = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
    b_norm = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def redact_passkey(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "passkey" in query:
        query["passkey"] = ["***"]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query, doseq=True), parsed.fragment))


def safe_sheet_name(name: str) -> str:
    name = re.sub(r"[:\\/?*\[\]]", "_", name)
    return name[:31] if len(name) > 31 else name


def format_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.0f}s"
    minutes = int(sec // 60)
    seconds = int(sec % 60)
    return f"{minutes}m {seconds:02d}s"

class BazaarvoiceClient:
    def __init__(self, passkey: str, api_host: str, api_version: str, locale: str, bv_header_version: str) -> None:
        self.passkey = passkey.strip()
        self.api_host = api_host.strip() or BV_DEFAULT_HOST
        self.api_version = api_version.strip() or BV_DEFAULT_API_VERSION
        self.locale = locale.strip() or DEFAULT_LOCALE
        self.bv_header_version = bv_header_version.strip() or BV_DEFAULT_HEADER_VERSION
        self.base_url = f"https://{self.api_host}/data"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "bv-version": self.bv_header_version,
            }
        )

    def _request(self, endpoint: str, params: Any, timeout: int = 30) -> Dict[str, Any]:
        payload: Any
        if isinstance(params, list):
            payload = [("apiversion", self.api_version), ("passkey", self.passkey), *params]
        else:
            payload = {"apiversion": self.api_version, "passkey": self.passkey, **params}
        url = f"{self.base_url}/{endpoint}.json"
        response = self.session.get(url, params=payload, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {"raw_text": compact_text(response.text, 1500)}
        errors: List[str] = []
        if isinstance(data, dict):
            for error in data.get("Errors", []) or []:
                code = str(error.get("Code") or "ERROR")
                message = str(error.get("Message") or "")
                errors.append(f"{code}: {message}".strip())
            if data.get("HasErrors") and not errors:
                errors.append("Bazaarvoice returned HasErrors=true")
        return {
            "endpoint": endpoint,
            "request_url": redact_passkey(response.url),
            "http_status": response.status_code,
            "data": data,
            "errors": errors,
            "ok": response.ok and not errors,
        }

    def products_by_ids(self, product_ids: Sequence[str]) -> Dict[str, Any]:
        ids = unique_keep_order(sanitize_candidate_id(pid) for pid in product_ids if pid)
        if not ids:
            return {"endpoint": "products", "request_url": "", "http_status": 0, "data": {"Results": []}, "errors": [], "ok": True}
        return self._request("products", {"Filter": f"id:{','.join(ids[:100])}", "Stats": "Reviews"})

    def products_search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"endpoint": "products", "request_url": "", "http_status": 0, "data": {"Results": []}, "errors": [], "ok": True}
        return self._request("products", {"Search": query, "Limit": max(1, min(limit, 100)), "Stats": "Reviews"})

    def statistics(self, product_ids: Sequence[str]) -> Dict[str, Any]:
        ids = unique_keep_order(sanitize_candidate_id(pid) for pid in product_ids if pid)
        if not ids:
            return {"endpoint": "statistics", "request_url": "", "http_status": 0, "data": {"Results": []}, "errors": [], "ok": True}
        return self._request("statistics", {"Filter": f"ProductId:eq:{','.join(ids[:100])}", "Stats": "Reviews"})

    def reviews_page(
        self,
        product_id: str,
        limit: int,
        offset: int,
        sort: str,
        exclude_family: Optional[bool] = None,
        content_locale: Optional[str] = None,
        with_stats: bool = False,
    ) -> Dict[str, Any]:
        ordered_params: List[Tuple[str, Any]] = [
            ("Limit", max(1, min(limit, 100))),
            ("Offset", max(0, offset)),
            ("Sort", sort),
            ("Include", "Products,Authors"),
            ("Filter", f"ProductId:{product_id}"),
        ]
        if content_locale:
            ordered_params.append(("Filter", f"ContentLocale:eq:{content_locale}"))
        if exclude_family is not None:
            ordered_params.append(("ExcludeFamily", str(bool(exclude_family)).lower()))
        if with_stats:
            ordered_params.append(("Stats", "Reviews"))
        return self._request("reviews", ordered_params)


def normalize_api_errors(response: Dict[str, Any]) -> List[str]:
    errors = list(response.get("errors") or [])
    data = response.get("data") or {}
    if not errors and isinstance(data, dict) and data.get("HasErrors"):
        errors.append("Bazaarvoice returned HasErrors=true")
    return errors


def build_page_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
    )
    return session


def fetch_page_html(url: str) -> Tuple[str, str, int]:
    session = build_page_session()
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.text, response.url, response.status_code


def extract_page_signals(html_text: str, source_url: str, final_url: str) -> PageSignals:
    soup = BeautifulSoup(html_text, "lxml")
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og:
            title = (og.get("content") or "").strip()

    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    hints: Dict[str, Any] = {}
    raw_codes: List[str] = []

    path_match = re.search(r"/([^/]+)\.html$", urlparse(final_url).path)
    if path_match:
        raw_codes.append(path_match.group(1))
        hints["path_model_code"] = path_match.group(1)

    for key in parse_qs(urlparse(final_url).query).keys():
        match = re.match(r"dwvar_([A-Za-z0-9_-]+)_", key)
        if match:
            raw_codes.append(match.group(1))
            hints.setdefault("query_model_codes", []).append(match.group(1))

    raw_codes.extend(HTML_CODE_RE.findall(text))

    for tag in soup.find_all(True):
        for attr_name, attr_value in tag.attrs.items():
            if not any(token in str(attr_name).lower() for token in ["pid", "product", "sku", "item", "model"]):
                continue
            values = attr_value if isinstance(attr_value, list) else [attr_value]
            for value in values:
                raw_codes.extend(MODEL_CODE_RE.findall(str(value)))

    raw_codes.extend(SCRIPT_KV_RE.findall(html_text))
    raw_codes.extend(MODEL_CODE_RE.findall(text))
    raw_codes.extend(MODEL_CODE_RE.findall(html_text))

    item_numbers = unique_keep_order(sanitize_candidate_id(code) for code in raw_codes if sanitize_candidate_id(code))
    family_codes = unique_keep_order(
        family for code in item_numbers for family in family_variants(code) if family != code
    )

    visible_rating = None
    visible_review_count = None
    for pattern in [
        r"(\d(?:\.\d)?)\s*out\s*of\s*5\s*Customer Rating",
        r"Overall Rating\s*(\d(?:\.\d)?)",
        r"Rated\s*(\d(?:\.\d)?)\s*out\s*of\s*5",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            visible_rating = safe_float(match.group(1))
            break
    for pattern in [
        r"(\d+)\s+Reviews",
        r"(\d+)\s+out\s+of\s+\d+.*?reviewers",
        r"Rating Snapshot.*?5 stars\s+(\d+)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            visible_review_count = safe_int(match.group(1))
            break

    return PageSignals(
        source_url=source_url,
        final_url=final_url,
        title=title,
        item_numbers=item_numbers,
        family_codes=family_codes,
        visible_rating=visible_rating,
        visible_review_count=visible_review_count,
        raw_hints=hints,
        text_excerpt=compact_text(text, 1200),
    )


def build_candidate_map(signals: PageSignals, manual_product_id: str) -> Dict[str, CandidateProbe]:
    probes: Dict[str, CandidateProbe] = {}

    def add(candidate_id: Optional[str], reason: str) -> None:
        cid = sanitize_candidate_id(candidate_id)
        if not cid:
            return
        if cid not in probes:
            probes[cid] = CandidateProbe(candidate_id=cid)
        if reason not in probes[cid].reasons:
            probes[cid].reasons.append(reason)

    manual = sanitize_candidate_id(manual_product_id)
    if manual:
        add(manual, "Manual override")
    for code in signals.item_numbers:
        add(code, "Exact model or item code from page")
        for family in family_variants(code):
            if family != code:
                add(family, f"Family candidate derived from {code}")
    return probes


def build_search_queries(signals: PageSignals, manual_search_query: str) -> List[str]:
    queries: List[str] = []
    if manual_search_query.strip():
        queries.append(manual_search_query.strip())
    queries.extend(signals.item_numbers[:6])
    queries.extend(signals.family_codes[:6])
    if signals.title:
        queries.append(signals.title)
        short_title = re.sub(r"\b(SharkNinja|Shark|Ninja)\b", "", signals.title, flags=re.IGNORECASE).strip()
        if short_title and short_title != signals.title:
            queries.append(short_title)
        if signals.item_numbers:
            queries.append(f"{signals.title} {signals.item_numbers[0]}")
    return unique_keep_order(q for q in queries if q)[:8]


def extract_review_stats(obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[float]]:
    candidates = [
        obj.get("ReviewStatistics"),
        obj.get("NativeReviewStatistics"),
        (obj.get("ProductStatistics") or {}).get("ReviewStatistics"),
        (obj.get("Statistics") or {}).get("ReviewStatistics"),
    ]
    for block in candidates:
        if not isinstance(block, dict):
            continue
        count = None
        for key in ["TotalReviewCount", "ReviewCount", "TotalReviews", "Count"]:
            if key in block:
                count = safe_int(block.get(key))
                break
        avg = None
        for key in ["AverageOverallRating", "AverageRating", "OverallRating", "Rating"]:
            if key in block:
                avg = safe_float(block.get(key))
                break
        if count is not None or avg is not None:
            return count, avg
    return None, None


def extract_product_result_id(result: Dict[str, Any]) -> Optional[str]:
    for key in ["Id", "ProductId", "ExternalId", "SKU", "Sku"]:
        candidate = sanitize_candidate_id(result.get(key))
        if candidate:
            return candidate
    candidate = sanitize_candidate_id((result.get("ProductStatistics") or {}).get("ProductId"))
    return candidate


def extract_product_name(result: Dict[str, Any]) -> str:
    for key in ["Name", "ProductName", "Title"]:
        value = result.get(key)
        if value:
            return str(value)
    return ""


def extract_statistics_map(stats_payload: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
    mapping: Dict[str, Dict[str, Optional[float]]] = {}
    for result in stats_payload.get("Results", []) or []:
        if not isinstance(result, dict):
            continue
        product_stats = result.get("ProductStatistics") if isinstance(result.get("ProductStatistics"), dict) else result
        product_id = sanitize_candidate_id((product_stats or {}).get("ProductId")) or extract_product_result_id(result)
        if not product_id:
            continue
        count, avg = extract_review_stats(product_stats or result)
        mapping[product_id] = {"review_count": count, "average_rating": avg}
    return mapping


def resolve_product(
    client: BazaarvoiceClient,
    signals: PageSignals,
    manual_product_id: str,
    manual_search_query: str,
    progress: Callable[[str, str, float], None],
) -> Tuple[Optional[ResolvedProduct], List[CandidateProbe], List[Dict[str, Any]]]:
    api_log: List[Dict[str, Any]] = []
    probes = build_candidate_map(signals, manual_product_id)
    search_queries = build_search_queries(signals, manual_search_query)

    progress("Resolve product", "Probing exact candidate IDs with Product Display and Statistics Display", 0.25)

    if probes:
        product_lookup = client.products_by_ids(list(probes))
        api_log.append(product_lookup)
        for result in (product_lookup.get("data") or {}).get("Results", []) or []:
            if not isinstance(result, dict):
                continue
            pid = extract_product_result_id(result)
            if not pid or pid not in probes:
                continue
            probe = probes[pid]
            probe.product_found = True
            probe.product_name = extract_product_name(result)
            count, avg = extract_review_stats(result)
            if count is not None:
                probe.stats_review_count = count
            if avg is not None:
                probe.stats_average_rating = avg
            probe.notes.append("Matched by Product Display exact ID lookup")
        for error in normalize_api_errors(product_lookup):
            for probe in probes.values():
                probe.errors.append(error)

        stats_lookup = client.statistics(list(probes))
        api_log.append(stats_lookup)
        for pid, stats in extract_statistics_map(stats_lookup.get("data") or {}).items():
            if pid not in probes:
                probes[pid] = CandidateProbe(candidate_id=pid, reasons=["Returned by Statistics Display"])
            probe = probes[pid]
            if stats.get("review_count") is not None:
                probe.stats_review_count = safe_int(stats.get("review_count"))
            if stats.get("average_rating") is not None:
                probe.stats_average_rating = safe_float(stats.get("average_rating"))
            probe.notes.append("Returned by Statistics Display")
        for error in normalize_api_errors(stats_lookup):
            for probe in probes.values():
                probe.errors.append(error)

    progress("Resolve product", "Running Product Display search queries for title/model fallbacks", 0.38)
    for query in search_queries[:6]:
        response = client.products_search(query, limit=10)
        api_log.append(response)
        errors = normalize_api_errors(response)
        if any("ERROR_PARAM_INVALID_SEARCH_ATTRIBUTE" in error for error in errors):
            continue
        for result in (response.get("data") or {}).get("Results", []) or []:
            if not isinstance(result, dict):
                continue
            pid = extract_product_result_id(result)
            if not pid:
                continue
            if pid not in probes:
                probes[pid] = CandidateProbe(candidate_id=pid)
            probe = probes[pid]
            probe.product_found = True
            if not probe.product_name:
                probe.product_name = extract_product_name(result)
            count, avg = extract_review_stats(result)
            if count is not None and probe.stats_review_count is None:
                probe.stats_review_count = count
            if avg is not None and probe.stats_average_rating is None:
                probe.stats_average_rating = avg
            reason = f"Product Display search: {query}"
            if reason not in probe.reasons:
                probe.reasons.append(reason)
        for error in errors:
            for probe in probes.values():
                probe.notes.append(f"Search query '{query}' returned error: {error}")

    progress("Resolve product", "Verifying top candidates against Review Display", 0.52)
    probe_order = sorted(
        probes.values(),
        key=lambda probe: (
            0 if probe.product_found else 1,
            -(probe.stats_review_count or 0),
            0 if any("Exact model" in reason or "Manual" in reason for reason in probe.reasons) else 1,
            probe.candidate_id,
        ),
    )
    for probe in probe_order[:8]:
        response = client.reviews_page(
            product_id=probe.candidate_id,
            limit=1,
            offset=0,
            sort=SORT_OPTIONS["Most recent"],
            with_stats=True,
        )
        api_log.append(response)
        data = response.get("data") or {}
        probe.review_probe_total = safe_int(data.get("TotalResults"))
        includes_products = (data.get("Includes") or {}).get("Products") or {}
        if not probe.product_name and isinstance(includes_products, dict):
            product = includes_products.get(probe.candidate_id)
            if isinstance(product, dict):
                probe.product_name = extract_product_name(product)
        for error in normalize_api_errors(response):
            probe.errors.append(error)

    title = signals.title
    primary_codes = set(signals.item_numbers)
    primary_families = set(signals.family_codes)
    manual = sanitize_candidate_id(manual_product_id)
    for probe in probes.values():
        score = 0.0
        if manual and probe.candidate_id == manual:
            score += 1000.0
        if probe.candidate_id in primary_codes:
            score += 180.0
        if probe.candidate_id in primary_families:
            score += 120.0
        if probe.product_found:
            score += 140.0
        if probe.stats_review_count is not None:
            score += 250.0 + min(200.0, float(probe.stats_review_count))
        if probe.review_probe_total is not None:
            score += 350.0 + min(250.0, float(probe.review_probe_total))
        if probe.product_name:
            probe.similarity_score = similarity(title, probe.product_name)
            score += probe.similarity_score * 140.0
        if probe.stats_average_rating is not None and signals.visible_rating is not None:
            score += max(0.0, 25.0 - abs(probe.stats_average_rating - signals.visible_rating) * 25.0)
        if probe.errors:
            score -= min(50.0, 5.0 * len(probe.errors))
        probe.final_score = score

    ordered = sorted(probes.values(), key=lambda probe: (-probe.final_score, probe.candidate_id))
    best = ordered[0] if ordered else None
    if not best:
        return None, ordered, api_log

    resolved = ResolvedProduct(
        product_id=best.candidate_id,
        product_name=best.product_name or signals.title or best.candidate_id,
        review_count=best.review_probe_total if best.review_probe_total is not None else best.stats_review_count,
        average_rating=best.stats_average_rating,
        source=", ".join(best.reasons[:3]) or "Resolution score",
    )
    return resolved, ordered, api_log


def review_dedupe_key(review: Dict[str, Any]) -> str:
    for key in ["Id", "ReviewId", "SubmissionId"]:
        if review.get(key):
            return f"{key}:{review.get(key)}"
    return json.dumps(review, sort_keys=True, default=str)


def fetch_reviews_for_product(
    client: BazaarvoiceClient,
    resolved: ResolvedProduct,
    requested_reviews: int,
    sort: str,
    exact_product_only: bool,
    content_locale: str,
    progress: Callable[[str, str, float], None],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    seen: set[str] = set()
    page_size = min(100, requested_reviews)
    offset = 0
    total_expected = resolved.review_count or requested_reviews
    fetch_started = time.time()

    while len(reviews) < requested_reviews:
        progress_fraction = 0.60 + 0.35 * min(1.0, offset / max(requested_reviews, 1))
        progress("Fetch reviews", f"Product {resolved.product_id} · offset {offset} · {len(reviews)}/{requested_reviews} collected", progress_fraction)
        response = client.reviews_page(
            product_id=resolved.product_id,
            limit=page_size,
            offset=offset,
            sort=sort,
            exclude_family=True if exact_product_only else None,
            content_locale=content_locale or None,
            with_stats=(offset == 0),
        )
        pages.append(response)
        data = response.get("data") or {}
        batch = data.get("Results", []) or []
        errors = normalize_api_errors(response)
        if errors and not batch:
            break
        if not batch:
            break
        for review in batch:
            if not isinstance(review, dict):
                continue
            key = review_dedupe_key(review)
            if key in seen:
                continue
            seen.add(key)
            reviews.append(review)
            if len(reviews) >= requested_reviews:
                break
        if len(batch) < page_size:
            break
        offset += len(batch)
        if offset >= min(requested_reviews, total_expected or requested_reviews, 300000):
            break

    return reviews[:requested_reviews], pages, {
        "elapsed_fetch_seconds": time.time() - fetch_started,
        "total_results_reported": pages[0].get("data", {}).get("TotalResults") if pages else None,
    }


def normalize_reviews(raw_reviews: Sequence[Dict[str, Any]], resolved: ResolvedProduct, source_url: str) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for idx, review in enumerate(raw_reviews, start=1):
        rows.append(
            {
                "Reference": f"R{idx:03d}",
                "ReviewId": review.get("Id") or review.get("ReviewId") or "",
                "SubmissionId": review.get("SubmissionId") or "",
                "ProductId": review.get("ProductId") or resolved.product_id,
                "ProductName": resolved.product_name,
                "Rating": safe_float(review.get("Rating")),
                "Title": review.get("Title") or "",
                "ReviewText": review.get("ReviewText") or "",
                "Author": review.get("UserNickname") or "",
                "AuthorId": review.get("AuthorId") or "",
                "UserLocation": review.get("UserLocation") or "",
                "SubmissionTime": review.get("SubmissionTime") or "",
                "LastModificationTime": review.get("LastModificationTime") or "",
                "IsRecommended": review.get("IsRecommended"),
                "IsSyndicated": review.get("IsSyndicated"),
                "Helpfulness": safe_float(review.get("Helpfulness")),
                "TotalFeedbackCount": safe_int(review.get("TotalFeedbackCount")),
                "TotalPositiveFeedbackCount": safe_int(review.get("TotalPositiveFeedbackCount")),
                "TotalNegativeFeedbackCount": safe_int(review.get("TotalNegativeFeedbackCount")),
                "TotalCommentCount": safe_int(review.get("TotalCommentCount")),
                "ContentLocale": review.get("ContentLocale") or "",
                "ModerationStatus": review.get("ModerationStatus") or "",
                "Pros": review.get("Pros") or "",
                "Cons": review.get("Cons") or "",
                "PhotoCount": len(review.get("Photos") or []),
                "VideoCount": len(review.get("Videos") or []),
                "EvidencePreview": compact_text(review.get("ReviewText") or review.get("Title") or "", 240),
                "SourceProductURL": source_url,
            }
        )
    return pd.DataFrame(rows)


def build_overview_df(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"Metric": "Requested reviews", "Value": meta.get("requested_reviews")},
        {"Metric": "Fetched reviews", "Value": len(df)},
        {"Metric": "Resolved product ID", "Value": meta.get("resolved_product_id")},
        {"Metric": "Resolved product name", "Value": meta.get("resolved_product_name")},
        {"Metric": "Bazaarvoice reported total", "Value": meta.get("resolved_review_count")},
        {"Metric": "Average rating", "Value": meta.get("resolved_average_rating")},
        {"Metric": "Resolution source", "Value": meta.get("resolution_source")},
        {"Metric": "Sort", "Value": meta.get("sort_label")},
        {"Metric": "Exact product only", "Value": meta.get("exact_product_only")},
        {"Metric": "Locale filter", "Value": meta.get("content_locale")},
        {"Metric": "Fetched at", "Value": meta.get("fetched_at")},
    ]
    return pd.DataFrame(rows)


def build_excel_bytes(reviews_df: pd.DataFrame, overview_df: pd.DataFrame, probes_df: pd.DataFrame, api_log_df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        overview_df.to_excel(writer, sheet_name="Overview", index=False)
        reviews_df.to_excel(writer, sheet_name=safe_sheet_name("Reviews"), index=False)
        probes_df.to_excel(writer, sheet_name=safe_sheet_name("Candidate Probes"), index=False)
        api_log_df.to_excel(writer, sheet_name=safe_sheet_name("API Log"), index=False)
    buffer.seek(0)
    return buffer.read()


def allocate_snapshot_dir(slug_hint: str) -> Path:
    LOCAL_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", slug_hint.lower()).strip("-")[:40] or "snapshot"
    target = LOCAL_STORE_ROOT / f"{stamp}_{slug}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def persist_snapshot(artifact: FetchArtifact) -> None:
    artifact.snapshot_dir.mkdir(parents=True, exist_ok=True)
    artifact.reviews_df.to_csv(artifact.snapshot_dir / "reviews.csv", index=False)
    artifact.overview_df.to_csv(artifact.snapshot_dir / "overview.csv", index=False)
    artifact.probes_df.to_csv(artifact.snapshot_dir / "candidate_probes.csv", index=False)
    artifact.api_log_df.to_csv(artifact.snapshot_dir / "api_log.csv", index=False)
    (artifact.snapshot_dir / "meta.json").write_text(json.dumps(artifact.meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def list_snapshots() -> List[Path]:
    if not LOCAL_STORE_ROOT.exists():
        return []
    return sorted([path for path in LOCAL_STORE_ROOT.iterdir() if path.is_dir()], reverse=True)


def load_snapshot(snapshot_dir: Path) -> Optional[FetchArtifact]:
    try:
        return FetchArtifact(
            reviews_df=pd.read_csv(snapshot_dir / "reviews.csv"),
            overview_df=pd.read_csv(snapshot_dir / "overview.csv"),
            probes_df=pd.read_csv(snapshot_dir / "candidate_probes.csv"),
            api_log_df=pd.read_csv(snapshot_dir / "api_log.csv"),
            meta=json.loads((snapshot_dir / "meta.json").read_text(encoding="utf-8")),
            snapshot_dir=snapshot_dir,
        )
    except Exception:
        return None


def get_openai_client() -> OpenAI:
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("Missing OpenAI API key in st.secrets.")
    return OpenAI(api_key=api_key)


def build_review_context(df: pd.DataFrame, max_reviews: int = 80, max_chars: int = 40000) -> str:
    if df.empty:
        return ""
    lines: List[str] = []
    used = 0
    for _, row in df.head(max_reviews).iterrows():
        line = (
            f"{row.get('Reference')} | rating={row.get('Rating')} | title={row.get('Title')} | "
            f"text={row.get('ReviewText')} | author={row.get('Author')} | "
            f"recommended={row.get('IsRecommended')} | helpful={row.get('TotalFeedbackCount')}"
        )
        line = compact_text(line, 600)
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def response_text(response: Any) -> str:
    if hasattr(response, "output_text"):
        return str(response.output_text)
    parts: List[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) in {"output_text", "text"}:
                parts.append(getattr(content, "text", ""))
    return "\n".join(part for part in parts if part)


def extract_json_object(text: str) -> Optional[str]:
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
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
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def generate_product_report(df: pd.DataFrame, artifact: FetchArtifact, model: str) -> ProductIntelReport:
    client = get_openai_client()
    context = build_review_context(df)
    if not context:
        raise RuntimeError("No reviews available for AI analysis.")
    schema = ProductIntelReport.model_json_schema()
    prompt = f"""
You are a SharkNinja product intelligence analyst.
Use only the evidence provided. Cite supporting reviews with Reference IDs like R001.
Return strict JSON matching this schema:\n{json.dumps(schema, ensure_ascii=False)}

Product metadata:
- Product name: {artifact.meta.get('resolved_product_name')}
- Product id: {artifact.meta.get('resolved_product_id')}
- Reviews analyzed: {len(df)}

Evidence corpus:
{context}
""".strip()
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": "Return valid JSON only."}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
    )
    text = response_text(response)
    blob = extract_json_object(text)
    if not blob:
        raise RuntimeError("The model did not return valid JSON for the AI report.")
    return ProductIntelReport.model_validate(json.loads(blob))


def ask_chatbot(df: pd.DataFrame, artifact: FetchArtifact, messages: List[Dict[str, str]], model: str, lenses: List[str]) -> str:
    client = get_openai_client()
    context = build_review_context(df, max_reviews=60, max_chars=30000)
    if not context:
        raise RuntimeError("No reviews available for chat.")
    conversation = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    lens_text = ", ".join(lenses) if lenses else "Product Development"
    prompt = f"""
You are a SharkNinja product intelligence chatbot.
Primary lenses: {lens_text}.
Answer only from the evidence below. If you are unsure, say so.
Whenever you make a concrete claim, cite the supporting review references like R001.
Keep answers crisp and useful.

Product: {artifact.meta.get('resolved_product_name')} ({artifact.meta.get('resolved_product_id')})

Evidence corpus:
{context}

Conversation so far:
{conversation}
""".strip()
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    return response_text(response).strip()


def build_evidence_map(df: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    evidence: Dict[str, Dict[str, str]] = {}
    for _, row in df.iterrows():
        ref = str(row.get("Reference") or "").strip()
        if not ref:
            continue
        evidence[ref] = {
            "title": compact_text(row.get("Title") or "", 120),
            "text": compact_text(row.get("ReviewText") or "", 260),
            "author": compact_text(row.get("Author") or "", 60),
            "rating": "" if pd.isna(row.get("Rating")) else str(row.get("Rating")),
        }
    return evidence


def annotate_refs(text: str, evidence_map: Dict[str, Dict[str, str]]) -> str:
    def repl(match: re.Match[str]) -> str:
        ref = match.group(0)
        evidence = evidence_map.get(ref)
        if not evidence:
            return ref
        tooltip = f"<div><strong>{ref}</strong></div>"
        if evidence.get("rating"):
            tooltip += f"<div style='color:#475569'>Rating: {html.escape(evidence['rating'])}</div>"
        if evidence.get("author"):
            tooltip += f"<div style='color:#475569'>Author: {html.escape(evidence['author'])}</div>"
        if evidence.get("title"):
            tooltip += f"<div><strong>{html.escape(evidence['title'])}</strong></div>"
        if evidence.get("text"):
            tooltip += f"<div style='margin-top:0.35rem'>{html.escape(evidence['text'])}</div>"
        return f"<span class='sn-ref'>{ref}<span class='sn-tooltip'>{tooltip}</span></span>"

    escaped = html.escape(text or "").replace("\n", "<br>")
    return REF_RE.sub(repl, escaped)


def render_rich_text(text: str, evidence_map: Dict[str, Dict[str, str]]) -> None:
    st.markdown(annotate_refs(text, evidence_map), unsafe_allow_html=True)


def scrape_via_bazaarvoice(
    url: str,
    requested_reviews: int,
    sort_label: str,
    manual_product_id: str,
    manual_search_query: str,
    exact_product_only: bool,
    content_locale: str,
    progress: Callable[[str, str, float, Optional[float]], None],
) -> FetchArtifact:
    started = time.time()
    config = get_bv_config()
    if not config["passkey"]:
        raise RuntimeError("Missing Bazaarvoice passkey in st.secrets. Add [bazaarvoice].passkey first.")

    normalized_url = normalize_url(url)
    if not is_sharkninja_url(normalized_url):
        raise RuntimeError("Paste a public SharkNinja or SharkClean product URL.")

    progress("Fetch page", "Loading the SharkNinja product page and extracting model signals", 0.08, None)
    html_text, final_url, page_status = fetch_page_html(normalized_url)
    signals = extract_page_signals(html_text, normalized_url, final_url)

    client = BazaarvoiceClient(
        passkey=config["passkey"],
        api_host=config["api_host"],
        api_version=config["api_version"],
        locale=config["locale"],
        bv_header_version=config["bv_header_version"],
    )

    resolved, probes, api_log = resolve_product(
        client=client,
        signals=signals,
        manual_product_id=manual_product_id,
        manual_search_query=manual_search_query,
        progress=lambda stage, detail, frac: progress(stage, detail, frac, time.time() - started),
    )

    if resolved is None:
        meta = {
            "status": "failed",
            "error": "Could not resolve a Bazaarvoice product ID from the page signals.",
            "requested_reviews": requested_reviews,
            "sort_label": sort_label,
            "content_locale": content_locale,
            "exact_product_only": exact_product_only,
            "fetched_at": now_str(),
            "page_http_status": page_status,
            "signals": asdict(signals),
        }
        artifact = FetchArtifact(
            reviews_df=pd.DataFrame(),
            overview_df=build_overview_df(pd.DataFrame(), meta),
            probes_df=pd.DataFrame([asdict(probe) for probe in probes]) if probes else pd.DataFrame(),
            api_log_df=pd.DataFrame(
                [{
                    "Endpoint": entry.get("endpoint"),
                    "HTTP status": entry.get("http_status"),
                    "Request URL": entry.get("request_url"),
                    "Errors": " | ".join(entry.get("errors") or []),
                } for entry in api_log]
            ),
            meta=meta,
            snapshot_dir=allocate_snapshot_dir(signals.item_numbers[0] if signals.item_numbers else "unresolved"),
        )
        persist_snapshot(artifact)
        return artifact

    progress("Fetch reviews", f"Resolved {resolved.product_id}. Paging through Review Display", 0.60, time.time() - started)
    raw_reviews, review_pages, fetch_meta = fetch_reviews_for_product(
        client=client,
        resolved=resolved,
        requested_reviews=requested_reviews,
        sort=SORT_OPTIONS[sort_label],
        exact_product_only=exact_product_only,
        content_locale=content_locale,
        progress=lambda stage, detail, frac: progress(stage, detail, frac, time.time() - started),
    )
    api_log.extend(review_pages)

    reviews_df = normalize_reviews(raw_reviews, resolved, final_url)
    meta = {
        "status": "ok" if not reviews_df.empty else "no_reviews",
        "requested_reviews": requested_reviews,
        "fetched_reviews": len(reviews_df),
        "resolved_product_id": resolved.product_id,
        "resolved_product_name": resolved.product_name,
        "resolved_review_count": resolved.review_count,
        "resolved_average_rating": resolved.average_rating,
        "resolution_source": resolved.source,
        "sort_label": sort_label,
        "content_locale": content_locale,
        "exact_product_only": exact_product_only,
        "source_url": normalized_url,
        "final_url": final_url,
        "page_http_status": page_status,
        "fetched_at": now_str(),
        "elapsed_total_seconds": time.time() - started,
        "signals": asdict(signals),
        **fetch_meta,
    }
    artifact = FetchArtifact(
        reviews_df=reviews_df,
        overview_df=build_overview_df(reviews_df, meta),
        probes_df=pd.DataFrame([asdict(probe) for probe in probes]),
        api_log_df=pd.DataFrame(
            [{
                "Endpoint": entry.get("endpoint"),
                "HTTP status": entry.get("http_status"),
                "Request URL": entry.get("request_url"),
                "Errors": " | ".join(entry.get("errors") or []),
            } for entry in api_log]
        ),
        meta=meta,
        snapshot_dir=allocate_snapshot_dir(resolved.product_id or (signals.item_numbers[0] if signals.item_numbers else "product")),
    )
    persist_snapshot(artifact)
    progress("Complete", f"Done. Retrieved {len(reviews_df)} reviews.", 1.0, time.time() - started)
    return artifact


def render_progress_panel(stage: str, detail: str, fraction: float, elapsed: Optional[float]) -> None:
    st.markdown(
        f"""
        <div class="sn-overlay">
            <div style="display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;">
                <div>
                    <div style="font-weight:700;">{html.escape(stage)}</div>
                    <div style="color:#475569;">{html.escape(detail)}</div>
                </div>
                <div style="font-family:ui-monospace, SFMono-Regular, Menlo, monospace;">Elapsed: {html.escape(format_seconds(elapsed))}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.progress(int(max(0.0, min(1.0, fraction)) * 100))


def render_hero() -> None:
    st.markdown(
        f"""
        <div class="sn-hero">
            <div style="display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;flex-wrap:wrap;">
                <div>
                    <div style="text-transform:uppercase;letter-spacing:0.08em;font-size:0.78rem;color:#64748b;">Ground-up Bazaarvoice rebuild</div>
                    <h2 style="margin:0.15rem 0 0.35rem 0;">{APP_TITLE}</h2>
                    <div style="color:#475569;">{APP_TAGLINE}</div>
                </div>
                <div class="sn-chip-row">
                    <span class="sn-chip">SharkNinja only</span>
                    <span class="sn-chip">Products → stats → reviews</span>
                    <span class="sn-chip">Local snapshots</span>
                    <span class="sn-chip">Evidence-grounded AI</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(artifact: FetchArtifact) -> None:
    meta = artifact.meta
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fetched reviews", len(artifact.reviews_df))
    c2.metric("Resolved product", meta.get("resolved_product_id") or "—")
    c3.metric("BV total", meta.get("resolved_review_count") or "—")
    c4.metric("Avg rating", meta.get("resolved_average_rating") or "—")


def render_summary(artifact: FetchArtifact) -> None:
    meta = artifact.meta
    st.markdown('<div class="sn-card">', unsafe_allow_html=True)
    st.subheader("Summary")
    st.write(meta.get("resolved_product_name") or "Resolved product unavailable")
    chips = [
        f"Product ID: {meta.get('resolved_product_id') or '—'}",
        f"Resolution: {meta.get('resolution_source') or '—'}",
        f"Sort: {meta.get('sort_label')}",
        f"Locale: {meta.get('content_locale') or 'All'}",
        f"Snapshot: {artifact.snapshot_dir.name}",
    ]
    chip_html = "".join(f"<span class='sn-chip'>{html.escape(str(chip))}</span>" for chip in chips)
    st.markdown(f"<div class='sn-chip-row'>{chip_html}</div>", unsafe_allow_html=True)
    if not artifact.reviews_df.empty and "Rating" in artifact.reviews_df.columns:
        dist = artifact.reviews_df["Rating"].value_counts(dropna=False).sort_index()
        st.bar_chart(dist)
    st.markdown("</div>", unsafe_allow_html=True)


def render_report(report: ProductIntelReport, evidence_map: Dict[str, Dict[str, str]]) -> None:
    st.markdown('<div class="sn-card">', unsafe_allow_html=True)
    st.subheader("Executive summary")
    render_rich_text(report.executive_summary, evidence_map)
    if report.executive_takeaways:
        st.markdown("**Executive takeaways**")
        for item in report.executive_takeaways:
            render_rich_text(f"- {item}", evidence_map)
    for title, items in [
        ("Delighters", report.delighters),
        ("Detractors", report.detractors),
        ("Quality watchouts", report.quality_watchouts),
        ("Consumer insights", report.consumer_insights),
    ]:
        if not items:
            continue
        st.markdown(f"**{title}**")
        for item in items:
            refs = ", ".join(item.supporting_reviews)
            render_rich_text(f"**{item.theme}** — {item.summary} {refs}".strip(), evidence_map)
    for title, items in [
        ("Actions for Product Development", report.actions_for_product_development),
        ("Actions for Quality Engineering", report.actions_for_quality_engineering),
        ("Actions for Consumer Insights", report.actions_for_consumer_insights),
    ]:
        if not items:
            continue
        st.markdown(f"**{title}**")
        for item in items:
            render_rich_text(f"- {item}", evidence_map)
    st.caption(report.confidence_note)
    st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_state()
    inject_css()
    render_hero()
    config = get_bv_config()

    with st.sidebar:
        st.subheader("Configuration")
        if config["passkey"]:
            st.success(f"Bazaarvoice passkey loaded · host={config['api_host']} · v{config['api_version']}")
        else:
            st.error("Missing Bazaarvoice passkey in st.secrets")
        if get_openai_api_key():
            st.success("OpenAI API key loaded")
        else:
            st.info("OpenAI API key not configured")
        st.divider()
        nav = st.radio("Workspace", ["Studio", "Snapshots"], index=0 if st.session_state.nav == "Studio" else 1)
        st.session_state.nav = nav
        ai_model = st.selectbox("AI model", AI_MODELS, index=0)
        lenses = st.multiselect("AI lenses", LENS_OPTIONS, default=LENS_OPTIONS)

    if st.session_state.nav == "Snapshots":
        st.subheader("Local snapshots")
        snapshots = list_snapshots()
        if not snapshots:
            st.info("No local snapshots yet.")
            return
        options = {path.name: path for path in snapshots}
        selected = st.selectbox("Choose snapshot", list(options))
        if st.button("Load snapshot", use_container_width=True):
            artifact = load_snapshot(options[selected])
            if artifact is None:
                st.error("Could not load snapshot.")
            else:
                st.session_state.artifact = artifact
                st.session_state.report = None
                st.session_state.chat_messages = []
                st.success(f"Loaded {selected}")
        return

    st.subheader("Fetch reviews")
    with st.form("fetch_form"):
        url = st.text_input(
            "SharkNinja product URL",
            value=st.session_state.last_url,
            placeholder="https://www.sharkninja.com/shark-silkipro-straight-wet-to-dry-straightener-rapid-blow-dryer-plum-satin/HT400PU.html?dwvar_HT400PU_color=A875B3",
        )
        c1, c2, c3 = st.columns(3)
        requested_reviews = c1.number_input("Reviews to pull", min_value=1, max_value=MAX_REVIEWS_CAP, value=100, step=10)
        sort_label = c2.selectbox("Sort", list(SORT_OPTIONS.keys()), index=0)
        content_locale = c3.text_input("Content locale filter", value=config.get("locale") or DEFAULT_LOCALE)
        c4, c5 = st.columns(2)
        manual_product_id = c4.text_input("Manual Bazaarvoice product ID override", value="")
        manual_search_query = c5.text_input("Manual Product Display search query", value="")
        exact_product_only = st.checkbox("Exact product only (ExcludeFamily=true)", value=False)
        submitted = st.form_submit_button("Fetch reviews", type="primary", use_container_width=True)

    if submitted:
        st.session_state.last_url = url
        status_box = st.empty()
        status_log = st.empty()
        events: List[Dict[str, Any]] = []

        def progress(stage: str, detail: str, fraction: float, elapsed: Optional[float]) -> None:
            with status_box.container():
                render_progress_panel(stage, detail, fraction, elapsed)
            events.append({
                "Time": now_str(),
                "Stage": stage,
                "Detail": detail,
                "Progress": f"{int(fraction * 100)}%",
                "Elapsed": format_seconds(elapsed),
            })
            status_log.dataframe(pd.DataFrame(events).tail(12), use_container_width=True, hide_index=True)

        try:
            artifact = scrape_via_bazaarvoice(
                url=url,
                requested_reviews=int(requested_reviews),
                sort_label=sort_label,
                manual_product_id=manual_product_id,
                manual_search_query=manual_search_query,
                exact_product_only=exact_product_only,
                content_locale=content_locale,
                progress=progress,
            )
            st.session_state.artifact = artifact
            st.session_state.report = None
            st.session_state.chat_messages = []
            if artifact.reviews_df.empty:
                st.warning(
                    "Bazaarvoice did not return review rows for the resolved candidate. "
                    "Check Candidate Probes and API Log below, or try a manual product ID override."
                )
            else:
                st.success(f"Retrieved {len(artifact.reviews_df)} reviews for {artifact.meta.get('resolved_product_id')}.")
        except Exception as exc:
            st.session_state.artifact = None
            st.error(str(exc))

    artifact: Optional[FetchArtifact] = st.session_state.artifact
    if artifact is None:
        st.info("Paste a SharkNinja product URL and fetch reviews.")
        return

    render_kpis(artifact)
    render_summary(artifact)

    excel_bytes = build_excel_bytes(artifact.reviews_df, artifact.overview_df, artifact.probes_df, artifact.api_log_df)
    csv_bytes = artifact.reviews_df.to_csv(index=False).encode("utf-8") if not artifact.reviews_df.empty else b""
    d1, d2 = st.columns(2)
    d1.download_button(
        "Download Excel",
        data=excel_bytes,
        file_name=f"sharkninja_reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    d2.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"sharkninja_reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        disabled=artifact.reviews_df.empty,
        use_container_width=True,
    )

    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Reviews", "Diagnostics", "AI"])
    with tab1:
        st.dataframe(artifact.overview_df, use_container_width=True, hide_index=True)
        with st.expander("Page signals", expanded=False):
            st.json(artifact.meta.get("signals") or {})
    with tab2:
        if artifact.reviews_df.empty:
            st.info("No reviews in this snapshot.")
        else:
            st.dataframe(artifact.reviews_df, use_container_width=True, hide_index=True)
    with tab3:
        st.markdown("**Candidate probes**")
        st.dataframe(artifact.probes_df, use_container_width=True, hide_index=True)
        st.markdown("**API log**")
        st.dataframe(artifact.api_log_df, use_container_width=True, hide_index=True)
        if artifact.meta.get("status") != "ok":
            st.error(artifact.meta.get("error") or "No Bazaarvoice reviews returned.")
    with tab4:
        if artifact.reviews_df.empty:
            st.info("Fetch reviews before running AI analysis.")
        else:
            evidence_map = build_evidence_map(artifact.reviews_df)
            if st.button("Generate AI report", use_container_width=True):
                try:
                    st.session_state.report = generate_product_report(artifact.reviews_df, artifact, ai_model)
                    st.success("AI report generated.")
                except Exception as exc:
                    st.error(str(exc))
            if st.session_state.report is not None:
                render_report(st.session_state.report, evidence_map)
            st.markdown("---")
            st.subheader("Chat with the reviews")
            question = st.text_input("Ask a question", placeholder="What are the top quality complaints?")
            if st.button("Send", use_container_width=True):
                if not question.strip():
                    st.warning("Enter a question first.")
                else:
                    st.session_state.chat_messages.append({"role": "user", "content": question.strip()})
                    try:
                        answer = ask_chatbot(artifact.reviews_df, artifact, st.session_state.chat_messages, ai_model, lenses)
                        st.session_state.chat_messages.append({"role": "assistant", "content": answer})
                    except Exception as exc:
                        st.error(str(exc))
            for message in st.session_state.chat_messages:
                with st.chat_message(message["role"]):
                    render_rich_text(message["content"], evidence_map)


if __name__ == "__main__":
    main()

