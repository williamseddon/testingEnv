"""
ECN Risk Assessment Tool — Streamlit App
=========================================
Run with:
    streamlit run ecn_app.py

Requires:
    pip install streamlit requests openai
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Page config  (must be the very first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ECN Risk Assessment",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
HIVE_BASE = os.getenv(
    "HIVE_BASE_URL",
    "https://hive.sharkninja.com/quality/root/Lists/ECN%20Live/Attachments",
)
HIVE_ROOT = "https://hive.sharkninja.com"

FILE_EXT_PATTERN = re.compile(
    r"\.(pdf|xlsx|xls|doc|docx|ppt|pptx|msg|zip|csv|txt)$",
    flags=re.IGNORECASE,
)
SECTION_BOUNDARY = re.compile(
    r"\b(?:Priority|Disposition of Stock|Checklist|Reviews/Approvals)\b",
    flags=re.IGNORECASE,
)
_AUTH_MARKERS = frozenset([
    "sign in", "signin", "microsoft", "office 365",
    "sharepoint", "login", "authentication", "aadcdn",
    "microsoftonline", "login.windows",
])
_RISK_RULES: list[tuple[list[str], str, int]] = [
    (["cost reduction", "vave"],
     "Cost-reduction / VAVE ECN — verify no hidden performance or reliability degradation.", 2),
    (["supplier name", "leshow"],
     "Supplier change detected — confirm source qualification, consistency, and traceability.", 2),
    (["no design nor ebom change", "no design impact"],
     "'No design impact' claim — verify against test evidence, EBOM linkage, and approval comments.", 2),
    (["need dqtp", "proposed dqtp"],
     "DQTP required or referenced — ensure the test plan is complete and results support the change.", 2),
    (["electrical changes required", "need ee"],
     "Electrical review required — do not assume no PCBA impact until EE review is closed with evidence.", 3),
    (["no impact confirmed"],
     "'No impact confirmed' — objective evidence must exist in the attachments.", 2),
    (["change not compatible with earlier date codes"],
     "Date-code compatibility issue — review stock disposition, field interchangeability, and rework plan.", 3),
    (["17 affected skus"],
     "Many affected SKUs — confirm SKU-level EBOM / PLM linkage for all impacted configurations.", 2),
]
_DD_REQUIRED = [
    "Explanation of Change",
    "Redlined EBOM",
    "Proposed DQTP",
    "Compliance Plan",
    "Electrical Changes Required",
]

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    filename: str
    success: bool
    url: str
    data: Optional[bytes] = None
    error: Optional[str] = None
    method: str = "requests"
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    redirect_chain: list[str] = field(default_factory=list)
    size_bytes: int = 0


@dataclass
class ConnectionTestResult:
    success: bool
    url: str
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    redirect_chain: list[str] = field(default_factory=list)
    error: Optional[str] = None
    auth_detected: bool = False
    elapsed_ms: int = 0


@dataclass
class CookieHealth:
    raw: str
    has_fedauth: bool = False
    has_rtfa: bool = False
    has_spoidcrl: bool = False
    cookie_count: int = 0

    @property
    def likely_valid(self) -> bool:
        return self.has_fedauth or self.has_rtfa or self.has_spoidcrl

    @property
    def status_label(self) -> str:
        if not self.raw.strip():
            return "empty"
        if self.likely_valid:
            return "good"
        return "unknown"


@dataclass
class RiskResult:
    findings: list[str] = field(default_factory=list)
    score: int = 0

    @property
    def rating(self) -> str:
        if self.score >= 8:
            return "HIGH"
        if self.score >= 4:
            return "MEDIUM"
        return "LOW"

    @property
    def emoji(self) -> str:
        return {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(self.rating, "⚪")


@dataclass
class DDItem:
    label: str
    present: bool


@dataclass
class ECNReport:
    sharepoint_id: Optional[str]
    attachments: list[str]
    download_results: list[DownloadResult]
    risk: RiskResult
    dd_items: list[DDItem]
    ai_analysis: Optional[str] = None

    @property
    def downloaded(self) -> list[DownloadResult]:
        return [r for r in self.download_results if r.success]

    @property
    def failed(self) -> list[DownloadResult]:
        return [r for r in self.download_results if not r.success]


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00A0", " ")


def extract_sharepoint_id(text: str) -> Optional[str]:
    match = re.search(r"SharePoint\s+ID\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_attachments(text: str) -> list[str]:
    match = re.search(
        r"\bAttachments\b\s*(.*?)\s*(?:" + SECTION_BOUNDARY.pattern + r"|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    seen: set[str] = set()
    results: list[str] = []
    for line in match.group(1).splitlines():
        clean = line.strip()
        if clean and FILE_EXT_PATTERN.search(clean) and clean not in seen:
            seen.add(clean)
            results.append(clean)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# URL helpers  —  four encoding variants to maximise hit rate
# ─────────────────────────────────────────────────────────────────────────────

def _encode_filename(filename: str, variant: int) -> str:
    """
    Variant 0: standard %20 spaces, safe punctuation kept
    Variant 1: NBSP (\\u00A0) instead of spaces
    Variant 2: fully percent-encoded (no safe chars)
    Variant 3: spaces kept literally
    """
    if variant == 0:
        return urllib.parse.quote(filename, safe="()-._")
    if variant == 1:
        return urllib.parse.quote(filename.replace(" ", "\u00A0"), safe="()-._\u00A0")
    if variant == 2:
        return urllib.parse.quote(filename, safe="")
    if variant == 3:
        return filename
    return urllib.parse.quote(filename, safe="()-._")


def candidate_urls(filename: str, sharepoint_id: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for v in range(4):
        encoded = _encode_filename(filename, v)
        url = f"{HIVE_BASE}/{sharepoint_id}/{encoded}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session  —  SharePoint-friendly headers
# ─────────────────────────────────────────────────────────────────────────────

def make_session(auth_cookie: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": HIVE_ROOT,
    })
    if auth_cookie.strip():
        session.headers["Cookie"] = auth_cookie.strip()
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Cookie analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyse_cookie(raw: str) -> CookieHealth:
    lower = raw.lower()
    pairs = [p.strip() for p in raw.split(";") if p.strip()]
    return CookieHealth(
        raw=raw,
        has_fedauth="fedauth" in lower,
        has_rtfa="rtfa" in lower,
        has_spoidcrl="spoidcrl" in lower,
        cookie_count=len(pairs),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auth-page detection
# ─────────────────────────────────────────────────────────────────────────────

def _classify_response(response: requests.Response) -> tuple[bool, str]:
    """Returns (is_auth_wall, reason_string)."""
    ct = response.headers.get("content-type", "").lower()
    final_url = response.url.lower()

    if any(d in final_url for d in ["login.microsoftonline", "login.windows.net", "sts.windows.net"]):
        return True, f"Redirected to Microsoft login: {response.url}"

    if "text/html" in ct:
        try:
            snippet = response.text[:6000].lower()
        except Exception:
            snippet = ""
        if any(m in snippet for m in _AUTH_MARKERS):
            return True, "Received an authentication/sign-in HTML page instead of the file."
        return True, f"Received HTML (content-type: {ct}) — expected a file."

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Connection test
# ─────────────────────────────────────────────────────────────────────────────

def test_hive_connection(auth_cookie: str = "") -> ConnectionTestResult:
    session = make_session(auth_cookie)
    t0 = time.monotonic()
    try:
        r = session.get(HIVE_ROOT, timeout=15, allow_redirects=True)
        elapsed = int((time.monotonic() - t0) * 1000)
        redirects = [resp.url for resp in r.history]
        ct = r.headers.get("content-type", "")
        auth, reason = _classify_response(r)
        return ConnectionTestResult(
            success=not auth,
            url=r.url,
            status_code=r.status_code,
            content_type=ct,
            redirect_chain=redirects,
            error=reason if auth else None,
            auth_detected=auth,
            elapsed_ms=elapsed,
        )
    except requests.RequestException as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return ConnectionTestResult(
            success=False, url=HIVE_ROOT,
            error=f"Request failed: {exc}", elapsed_ms=elapsed,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Single-file download with retry across URL variants
# ─────────────────────────────────────────────────────────────────────────────

def download_attachment(
    session: requests.Session,
    filename: str,
    sharepoint_id: str,
    max_retries: int = 2,
) -> DownloadResult:
    urls = candidate_urls(filename, sharepoint_id)
    last_result: Optional[DownloadResult] = None

    for url in urls:
        for attempt in range(max_retries):
            try:
                r = session.get(url, timeout=30, allow_redirects=True)
            except requests.RequestException as exc:
                last_result = DownloadResult(
                    filename=filename, success=False, url=url,
                    error=f"Request error: {exc}",
                )
                break  # network error — skip remaining retries for this URL

            redirects = [resp.url for resp in r.history]
            ct = r.headers.get("content-type", "")
            auth, reason = _classify_response(r)

            if r.status_code == 200 and not auth:
                return DownloadResult(
                    filename=filename,
                    success=True,
                    url=r.url,
                    data=r.content,
                    status_code=r.status_code,
                    content_type=ct,
                    redirect_chain=redirects,
                    size_bytes=len(r.content),
                )

            last_result = DownloadResult(
                filename=filename,
                success=False,
                url=url,
                error=reason or f"HTTP {r.status_code}",
                status_code=r.status_code,
                content_type=ct,
                redirect_chain=redirects,
            )

            # Retry only on transient server errors
            if r.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            break  # non-retryable — try next URL variant

    return last_result or DownloadResult(
        filename=filename, success=False, url=urls[0], error="All URL variants exhausted."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Risk & due-diligence checks
# ─────────────────────────────────────────────────────────────────────────────

def rule_based_risk(text: str) -> RiskResult:
    lower = text.lower()
    result = RiskResult()
    for keywords, message, weight in _RISK_RULES:
        if any(kw in lower for kw in keywords):
            result.findings.append(message)
            result.score += weight
    return result


def dd_completeness(text: str) -> list[DDItem]:
    lower = text.lower()
    return [DDItem(label=item, present=item.lower() in lower) for item in _DD_REQUIRED]


# ─────────────────────────────────────────────────────────────────────────────
# ZIP helper
# ─────────────────────────────────────────────────────────────────────────────

def build_zip(files: dict[str, bytes]) -> BytesIO:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# AI prompt & analysis
# ─────────────────────────────────────────────────────────────────────────────

def build_ai_prompt(
    ecn_text: str,
    attachments: list[str],
    download_results: list[DownloadResult],
) -> str:
    downloaded = [r.filename for r in download_results if r.success]
    failed = [r.filename for r in download_results if not r.success]
    return f"""
You are a senior SharkNinja quality engineer reviewing an Engineering Change Notice (ECN).

Tasks:
1. Summarize the ECN concisely.
2. Identify key technical, quality, compliance, supply-chain, and implementation risks.
3. Identify missing due-diligence items.
4. Identify contradictions or weak logic in the ECN.
5. Comment on whether approvals and comments are sufficient.
6. Highlight what evidence still needs to be reviewed from the attachments.
7. Give a final recommendation: GO / CONDITIONAL GO / NO GO.
8. Provide a concise rationale for your recommendation.

ECN TEXT:
{ecn_text}

PARSED ATTACHMENT NAMES:
{json.dumps(attachments, indent=2)}

SUCCESSFULLY DOWNLOADED:
{json.dumps(downloaded, indent=2)}

FAILED DOWNLOADS:
{json.dumps(failed, indent=2)}

Be practical and sceptical. Focus on hidden risk, incomplete evidence, and implementation gaps.
Respond in clear sections with concise bullet points.
""".strip()


def run_ai_analysis(
    ecn_text: str,
    attachments: list[str],
    download_results: list[DownloadResult],
    api_key: str,
    model: str,
) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    prompt = build_ai_prompt(ecn_text, attachments, download_results)
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# Main review pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_review(
    ecn_text: str,
    auth_cookie: str = "",
    openai_api_key: Optional[str] = None,
    ai_model: str = "gpt-4o",
    manual_files: Optional[dict[str, bytes]] = None,
) -> ECNReport:
    text = normalize(ecn_text)
    sharepoint_id = extract_sharepoint_id(text)
    attachments = extract_attachments(text)
    session = make_session(auth_cookie)

    download_results: list[DownloadResult] = []
    if sharepoint_id and attachments:
        for filename in attachments:
            result = download_attachment(session, filename, sharepoint_id)
            # Supplement with manual upload if download failed
            if not result.success and manual_files and filename in manual_files:
                result = DownloadResult(
                    filename=filename, success=True, url="manual-upload",
                    data=manual_files[filename], method="manual",
                    size_bytes=len(manual_files[filename]),
                )
            download_results.append(result)

    # Include any manually uploaded files not found in parsed attachment list
    if manual_files:
        parsed_names = {r.filename for r in download_results}
        for name, data in manual_files.items():
            if name not in parsed_names:
                download_results.append(DownloadResult(
                    filename=name, success=True, url="manual-upload",
                    data=data, method="manual", size_bytes=len(data),
                ))

    risk = rule_based_risk(text)
    dd_items = dd_completeness(text)
    ai_output: Optional[str] = None

    if openai_api_key:
        try:
            ai_output = run_ai_analysis(text, attachments, download_results, openai_api_key, ai_model)
        except Exception as exc:
            ai_output = f"⚠️ AI analysis failed: {exc}"

    return ECNReport(
        sharepoint_id=sharepoint_id,
        attachments=attachments,
        download_results=download_results,
        risk=risk,
        dd_items=dd_items,
        ai_analysis=ai_output,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #1e2130;
    border: 1px solid #2d3250;
    border-radius: 10px;
    padding: 1rem 1.2rem;
}
.risk-badge {
    display: inline-block;
    padding: 0.35em 0.9em;
    border-radius: 20px;
    font-weight: 700;
    font-size: 1.1rem;
    letter-spacing: 0.05em;
}
.risk-HIGH   { background: #ff4b4b22; color: #ff4b4b; border: 1px solid #ff4b4b55; }
.risk-MEDIUM { background: #ffa72222; color: #ffa722; border: 1px solid #ffa72255; }
.risk-LOW    { background: #21c35422; color: #21c354; border: 1px solid #21c35455; }
.section-header {
    font-size: 1rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #a0aec0;
    margin: 1.5rem 0 0.5rem 0;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid #2d3250;
}
.attach-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.3rem 0;
    font-size: 0.9rem;
    color: #e2e8f0;
}
.cookie-good    { color: #21c354; font-weight: 600; }
.cookie-empty   { color: #718096; }
.cookie-unknown { color: #ffa722; font-weight: 600; }
.step-box {
    background: #1a2035;
    border: 1px solid #2d3250;
    border-radius: 8px;
    padding: 0.8rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.85rem;
    line-height: 1.6;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Session-state initialisation
# ─────────────────────────────────────────────────────────────────────────────
if "auth_cookie" not in st.session_state:
    st.session_state["auth_cookie"] = ""
if "conn_test_result" not in st.session_state:
    st.session_state["conn_test_result"] = None


# ─────────────────────────────────────────────────────────────────────────────
# API key resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_api_key() -> tuple[Optional[str], str]:
    try:
        k = st.secrets.get("OPENAI_API_KEY")
        if k:
            return k, "secrets"
    except Exception:
        pass
    k = os.getenv("OPENAI_API_KEY")
    if k:
        return k, "env"
    return None, "missing"


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/SharkNinja_logo.svg/320px-SharkNinja_logo.svg.png",
        width=160,
    )
    st.title("ECN Risk Tool")
    st.caption("Engineering Change Notice Reviewer")
    st.divider()

    # ── AI settings ───────────────────────────────────────────────────────
    st.markdown("### 🤖 AI Analysis")
    stored_key, key_source = resolve_api_key()
    if key_source == "secrets":
        st.success("API key loaded from Streamlit secrets.", icon="✅")
    elif key_source == "env":
        st.info("API key loaded from environment.", icon="ℹ️")
    else:
        st.warning("No API key found.", icon="⚠️")

    manual_key = "" if stored_key else st.text_input(
        "OpenAI API key", type="password", placeholder="sk-..."
    )
    api_key = stored_key or manual_key or None
    use_ai = st.toggle("Enable AI analysis", value=bool(api_key))
    ai_model = st.selectbox("Model", ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"], index=0)

    st.divider()

    # ── Hive authentication ───────────────────────────────────────────────
    st.markdown("### 🔐 Hive Authentication")

    with st.expander("📖 How to get your Hive cookie", expanded=False):
        st.markdown("**Follow these steps while logged into Hive in your browser:**")
        st.markdown(
            '<div class="step-box">1️⃣ &nbsp;Open <strong>hive.sharkninja.com</strong> '
            'and confirm you are signed in.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="step-box">2️⃣ &nbsp;Press <strong>F12</strong> to open DevTools '
            'and click the <strong>Console</strong> tab.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="step-box">3️⃣ &nbsp;Paste the line below and press <strong>Enter</strong>. '
            'Your cookies will be copied to your clipboard automatically.</div>',
            unsafe_allow_html=True,
        )
        st.code("copy(document.cookie)", language="javascript")
        st.markdown(
            '<div class="step-box">4️⃣ &nbsp;Return here and paste the result into the '
            'box below, then click <strong>Save cookie</strong>.</div>',
            unsafe_allow_html=True,
        )
        st.info(
            "🔒 These cookies grant Hive access as you. Do not share them. "
            "They expire when you sign out of Hive.",
            icon="ℹ️",
        )

    # Cookie textarea — seeded from session state
    new_cookie = st.text_area(
        "Paste cookie string",
        value=st.session_state["auth_cookie"],
        height=90,
        placeholder="FedAuth=eyJ0...; rtFa=abc123...",
        label_visibility="collapsed",
    )

    col_save, col_clear = st.columns([2, 1])
    with col_save:
        if st.button("💾  Save cookie", use_container_width=True):
            st.session_state["auth_cookie"] = new_cookie.strip()
            st.session_state["conn_test_result"] = None
            st.success("Cookie saved.", icon="✅")
    with col_clear:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state["auth_cookie"] = ""
            st.session_state["conn_test_result"] = None
            st.info("Cookie cleared.")

    # Cookie health badge
    auth_cookie = st.session_state["auth_cookie"]
    if auth_cookie:
        health = analyse_cookie(auth_cookie)
        if health.likely_valid:
            st.markdown(
                f'<span class="cookie-good">✅ Cookie looks valid</span> '
                f'<span style="color:#718096;font-size:0.8rem">'
                f'({health.cookie_count} values · '
                f'FedAuth={health.has_fedauth} · rtFa={health.has_rtfa})'
                f'</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span class="cookie-unknown">⚠️ No FedAuth/rtFa tokens detected</span>',
                unsafe_allow_html=True,
            )
            st.caption(
                "The cookie is present but the key SharePoint tokens weren't found. "
                "Try the copy(document.cookie) method above."
            )
    else:
        st.markdown('<span class="cookie-empty">○ No cookie set — downloads will fail</span>', unsafe_allow_html=True)

    st.markdown("")
    if st.button("🔌  Test Hive connection", use_container_width=True):
        with st.spinner("Probing Hive…"):
            st.session_state["conn_test_result"] = test_hive_connection(auth_cookie)

    test: Optional[ConnectionTestResult] = st.session_state["conn_test_result"]
    if test is not None:
        if test.success:
            st.success(f"Connected — HTTP {test.status_code} in {test.elapsed_ms} ms", icon="✅")
        else:
            st.error(f"{test.error}", icon="❌")
            if test.status_code:
                st.caption(f"Status {test.status_code} · Final URL: {test.url}")
            if test.redirect_chain:
                with st.expander("Redirect chain"):
                    for u in test.redirect_chain:
                        st.caption(u)
            if test.auth_detected and not auth_cookie:
                st.warning(
                    "Hive is redirecting to Microsoft login. "
                    "Follow the cookie guide above to authenticate.",
                    icon="🔐",
                )

    st.divider()

    # ── Options ───────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    show_diagnostics = st.toggle(
        "Show download diagnostics",
        value=False,
        help="Show status codes, redirect chains, and content-type for each download.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Page header
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 🔍 ECN Risk Assessment")
st.caption(
    "Paste an Engineering Change Notice below to analyse risk, "
    "check due diligence, and download attachments."
)
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Input
# ─────────────────────────────────────────────────────────────────────────────
ecn_text = st.text_area(
    "ECN Content",
    height=280,
    placeholder="Paste full ECN text here…",
    label_visibility="collapsed",
)

col_upload, col_run = st.columns([3, 1])
with col_upload:
    manual_uploads = st.file_uploader(
        "Upload attachments manually (fallback if Hive download fails)",
        accept_multiple_files=True,
    )
with col_run:
    st.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
    run = st.button("▶  Run Review", type="primary", use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────
if run:
    if not ecn_text.strip():
        st.error("Please paste ECN content before running the review.")
        st.stop()

    auth_cookie = st.session_state["auth_cookie"]

    if not auth_cookie and not manual_uploads:
        st.warning(
            "**No Hive cookie is set.** Attachment downloads will fail. "
            "Use the 📖 cookie guide in the sidebar, or upload files manually.",
            icon="🔐",
        )

    manual_files: dict[str, bytes] = {}
    if manual_uploads:
        for f in manual_uploads:
            manual_files[f.name] = f.read()

    effective_key = api_key if use_ai else None

    with st.spinner("Running ECN review…"):
        report = run_review(
            ecn_text=ecn_text,
            auth_cookie=auth_cookie,
            openai_api_key=effective_key,
            ai_model=ai_model,
            manual_files=manual_files or None,
        )

    st.divider()

    # ── Metrics bar ───────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("SharePoint ID", report.sharepoint_id or "—")
    m2.metric("Attachments", len(report.attachments))
    m3.metric("Downloaded", len(report.downloaded))
    m4.metric("Failed", len(report.failed))
    m5.metric("Manual uploads", len(manual_files))

    st.divider()

    left, right = st.columns([1, 1], gap="large")

    # ── Left: attachments + downloads ─────────────────────────────────────
    with left:
        st.markdown('<div class="section-header">📎 Parsed Attachments</div>', unsafe_allow_html=True)
        if report.attachments:
            for name in report.attachments:
                st.markdown(f'<div class="attach-row">📄 {name}</div>', unsafe_allow_html=True)
        else:
            st.warning("No attachment names found in the ECN text.")

        if report.sharepoint_id and report.attachments:
            with st.expander("Hive attachment URLs", expanded=False):
                for name in report.attachments:
                    urls = candidate_urls(name, report.sharepoint_id)
                    st.code(urls[0], language=None)

        st.markdown('<div class="section-header">⬇️ Download Status</div>', unsafe_allow_html=True)

        if report.download_results:
            for r in report.download_results:
                if r.success:
                    size_str = f"{r.size_bytes / 1024:.1f} KB" if r.size_bytes else ""
                    st.success(
                        f"**{r.filename}** &nbsp;·&nbsp; {r.method} {size_str}",
                        icon="✅",
                    )
                    if show_diagnostics:
                        st.caption(f"URL: {r.url}")
                        st.caption(f"Type: {r.content_type} · Status: {r.status_code}")
                else:
                    st.error(f"**{r.filename}**", icon="❌")
                    st.caption(r.error or "Unknown error")
                    if show_diagnostics:
                        with st.expander(f"Diagnostics — {r.filename}", expanded=False):
                            st.write(f"**Status:** {r.status_code or 'N/A'}")
                            st.write(f"**Content-Type:** {r.content_type or 'N/A'}")
                            st.write(f"**Last URL tried:** `{r.url}`")
                            if r.redirect_chain:
                                st.write("**Redirects:**")
                                for u in r.redirect_chain:
                                    st.caption(u)
                            if report.sharepoint_id:
                                st.write("**All URL variants:**")
                                for u in candidate_urls(r.filename, report.sharepoint_id):
                                    st.caption(u)

            auth_failed = [
                r for r in report.failed
                if r.error and any(
                    k in r.error.lower()
                    for k in ("authentication", "sign-in", "html", "login", "redirected")
                )
            ]
            if auth_failed:
                st.warning(
                    "**Authentication required.**\n\n"
                    "Open **🔐 Hive Authentication** in the sidebar → follow the "
                    "**📖 How to get your Hive cookie** guide → paste and save your cookie → "
                    "run the review again.",
                    icon="🔐",
                )
        else:
            st.info("No downloads attempted — SharePoint ID or attachment names not found.")

        # ZIP package
        all_files = {r.filename: r.data for r in report.downloaded if r.data}
        all_files.update(manual_files)
        if all_files:
            st.markdown('<div class="section-header">📦 Package</div>', unsafe_allow_html=True)
            zip_buf = build_zip(all_files)
            st.download_button(
                label=f"⬇️  Download {len(all_files)} file(s) as ZIP",
                data=zip_buf,
                file_name=f"ECN_{report.sharepoint_id or 'unknown'}_attachments.zip",
                mime="application/zip",
                use_container_width=True,
            )
        elif report.failed:
            st.info(
                "No files available yet. Add your Hive cookie or upload files manually.",
                icon="📂",
            )

    # ── Right: risk + due diligence ───────────────────────────────────────
    with right:
        st.markdown('<div class="section-header">⚠️ Rule-Based Risk</div>', unsafe_allow_html=True)
        st.markdown(
            f'<span class="risk-badge risk-{report.risk.rating}">'
            f'{report.risk.emoji} {report.risk.rating}'
            f' &nbsp;·&nbsp; score {report.risk.score}'
            f'</span>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        if report.risk.findings:
            for finding in report.risk.findings:
                st.warning(finding, icon="⚠️")
        else:
            st.success("No rule-based risk triggers detected.", icon="✅")

        st.markdown('<div class="section-header">✅ Due Diligence Checklist</div>', unsafe_allow_html=True)
        dd_cols = st.columns(2)
        for i, item in enumerate(report.dd_items):
            col = dd_cols[i % 2]
            if item.present:
                col.success(item.label, icon="✅")
            else:
                col.error(item.label, icon="❌")

    st.divider()

    # ── AI analysis ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">🤖 AI Risk Assessment</div>', unsafe_allow_html=True)
    if report.ai_analysis:
        st.markdown(report.ai_analysis)
    elif not use_ai:
        st.info("AI analysis is disabled. Enable it in the sidebar.", icon="ℹ️")
    elif not api_key:
        st.warning("No OpenAI API key available. Add one in the sidebar.", icon="⚠️")

    st.divider()

    # ── Summary JSON ──────────────────────────────────────────────────────
    with st.expander("📋 Raw summary (JSON)", expanded=False):
        st.json({
            "sharepoint_id": report.sharepoint_id,
            "parsed_attachments": len(report.attachments),
            "downloaded": len(report.downloaded),
            "failed": len(report.failed),
            "manual_uploads": len(manual_files),
            "risk_score": report.risk.score,
            "risk_rating": report.risk.rating,
            "risk_findings": report.risk.findings,
            "due_diligence": [
                {"item": d.label, "present": d.present} for d in report.dd_items
            ],
            "ai_analysis_run": report.ai_analysis is not None,
            "download_details": [
                {
                    "filename": r.filename,
                    "success": r.success,
                    "method": r.method,
                    "status_code": r.status_code,
                    "content_type": r.content_type,
                    "size_bytes": r.size_bytes,
                    "error": r.error,
                    "url": r.url,
                }
                for r in report.download_results
            ],
        })
