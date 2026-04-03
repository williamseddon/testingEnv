"""
ECN Risk Assessment Tool — Streamlit App
=========================================
Run with:
    streamlit run ecn_app.py

Install:
    pip install streamlit requests openai msal

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ONE-TIME AZURE AD SETUP  (do this once)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.  Go to https://portal.azure.com → Azure Active Directory → App registrations
2.  Click "New registration"
        Name:            ECN Risk Tool
        Supported types: Accounts in this organizational directory only
        Redirect URI:    leave blank
3.  Click Register. Copy the Application (client) ID and Directory (tenant) ID.
4.  Go to API permissions → Add a permission → SharePoint → Delegated
        Add:  AllSites.Read
5.  Click "Grant admin consent for SharkNinja".
6.  Go to Authentication → Advanced settings →
        Enable "Allow public client flows" = YES
7.  Add the two IDs to .streamlit/secrets.toml:

        [auth]
        AZURE_TENANT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        AZURE_CLIENT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

    Or set environment variables AZURE_TENANT_ID and AZURE_CLIENT_ID.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
# Page config
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
HIVE_ROOT   = "https://hive.sharkninja.com"
SP_SCOPE    = [f"{HIVE_ROOT}/.default"]

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
class AuthSession:
    username: str
    access_token: str
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60


@dataclass
class DownloadResult:
    filename: str
    success: bool
    url: str
    data: Optional[bytes] = None
    error: Optional[str] = None
    method: str = "bearer"
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    redirect_chain: list[str] = field(default_factory=list)
    size_bytes: int = 0


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
# Azure AD config
# ─────────────────────────────────────────────────────────────────────────────

def get_azure_config() -> tuple[Optional[str], Optional[str]]:
    tenant_id = None
    client_id = None
    try:
        tenant_id = st.secrets.get("auth", {}).get("AZURE_TENANT_ID") or st.secrets.get("AZURE_TENANT_ID")
        client_id = st.secrets.get("auth", {}).get("AZURE_CLIENT_ID") or st.secrets.get("AZURE_CLIENT_ID")
    except Exception:
        pass
    tenant_id = tenant_id or os.getenv("AZURE_TENANT_ID")
    client_id = client_id or os.getenv("AZURE_CLIENT_ID")
    return tenant_id, client_id


# ─────────────────────────────────────────────────────────────────────────────
# MSAL sign-in  (username + password / ROPC flow)
# ─────────────────────────────────────────────────────────────────────────────

def msal_sign_in(
    username: str,
    password: str,
    tenant_id: str,
    client_id: str,
) -> tuple[Optional[AuthSession], Optional[str]]:
    try:
        import msal
    except ImportError:
        return None, "The `msal` package is not installed. Run: pip install msal"

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_by_username_password(
        username=username,
        password=password,
        scopes=SP_SCOPE,
    )

    if "access_token" in result:
        expires_in = result.get("expires_in", 3600)
        return AuthSession(
            username=username,
            access_token=result["access_token"],
            expires_at=time.time() + expires_in,
        ), None

    desc = result.get("error_description", "")
    if "AADSTS50076" in desc:
        msg = "Multi-factor authentication (MFA) is required. Ask your Azure AD admin to exempt this app from MFA, or enable the 'Allow public client flows' setting."
    elif "AADSTS50126" in desc or "AADSTS50020" in desc:
        msg = "Incorrect email or password — please try again."
    elif "AADSTS70011" in desc:
        msg = "The SharePoint scope is not authorised. Ask your Azure AD admin to grant AllSites.Read permission to this app."
    elif "AADSTS65001" in desc:
        msg = "Admin consent is required. Ask your Azure AD admin to grant consent for the ECN Risk Tool app."
    elif "AADSTS7000218" in desc:
        msg = "Public client flows are not enabled. In Azure Portal → App registration → Authentication → enable 'Allow public client flows'."
    else:
        msg = desc or result.get("error", "Authentication failed.")
    return None, msg


def refresh_if_needed(
    auth: AuthSession,
    tenant_id: str,
    client_id: str,
) -> tuple[AuthSession, Optional[str]]:
    if not auth.is_expired:
        return auth, None
    try:
        import msal
        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        accounts = app.get_accounts(username=auth.username)
        if accounts:
            result = app.acquire_token_silent(scopes=SP_SCOPE, account=accounts[0])
            if result and "access_token" in result:
                return AuthSession(
                    username=auth.username,
                    access_token=result["access_token"],
                    expires_at=time.time() + result.get("expires_in", 3600),
                ), None
    except Exception:
        pass
    return auth, "Session expired — please sign in again."


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session  —  Bearer token auth
# ─────────────────────────────────────────────────────────────────────────────

def make_session(access_token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {access_token}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json;odata=verbose, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HIVE_ROOT,
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00A0", " ")


def extract_sharepoint_id(text: str) -> Optional[str]:
    m = re.search(r"SharePoint\s+ID\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    return m.group(1) if m else None


def extract_attachments(text: str) -> list[str]:
    m = re.search(
        r"\bAttachments\b\s*(.*?)\s*(?:" + SECTION_BOUNDARY.pattern + r"|\Z)",
        text, flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return []
    seen: set[str] = set()
    results: list[str] = []
    for line in m.group(1).splitlines():
        clean = line.strip()
        if clean and FILE_EXT_PATTERN.search(clean) and clean not in seen:
            seen.add(clean)
            results.append(clean)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# URL helpers  —  four encoding variants
# ─────────────────────────────────────────────────────────────────────────────

def _encode(filename: str, v: int) -> str:
    if v == 0: return urllib.parse.quote(filename, safe="()-._")
    if v == 1: return urllib.parse.quote(filename.replace(" ", "\u00A0"), safe="()-._\u00A0")
    if v == 2: return urllib.parse.quote(filename, safe="")
    return filename


def candidate_urls(filename: str, sharepoint_id: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for v in range(4):
        url = f"{HIVE_BASE}/{sharepoint_id}/{_encode(filename, v)}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# ─────────────────────────────────────────────────────────────────────────────
# Auth-page / error detection
# ─────────────────────────────────────────────────────────────────────────────

def _classify_response(r: requests.Response) -> tuple[bool, str]:
    ct = r.headers.get("content-type", "").lower()
    url = r.url.lower()
    if any(d in url for d in ["login.microsoftonline", "login.windows.net", "sts.windows.net"]):
        return True, "Redirected to Microsoft login — token may be expired. Sign out and back in."
    if "text/html" in ct:
        try:
            snippet = r.text[:6000].lower()
        except Exception:
            snippet = ""
        if any(m in snippet for m in _AUTH_MARKERS):
            return True, "Received an authentication page — token may be expired."
        return True, f"Received HTML instead of a file."
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_attachment(
    session: requests.Session,
    filename: str,
    sharepoint_id: str,
    max_retries: int = 2,
) -> DownloadResult:
    urls = candidate_urls(filename, sharepoint_id)
    last: Optional[DownloadResult] = None

    for url in urls:
        for attempt in range(max_retries):
            try:
                r = session.get(url, timeout=30, allow_redirects=True)
            except requests.RequestException as exc:
                last = DownloadResult(filename=filename, success=False, url=url, error=str(exc))
                break

            redirects = [resp.url for resp in r.history]
            ct = r.headers.get("content-type", "")
            auth, reason = _classify_response(r)

            if r.status_code == 200 and not auth:
                return DownloadResult(
                    filename=filename, success=True, url=r.url, data=r.content,
                    status_code=r.status_code, content_type=ct,
                    redirect_chain=redirects, size_bytes=len(r.content),
                )

            last = DownloadResult(
                filename=filename, success=False, url=url,
                error=reason or f"HTTP {r.status_code}",
                status_code=r.status_code, content_type=ct, redirect_chain=redirects,
            )
            if r.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    return last or DownloadResult(filename=filename, success=False, url=urls[0], error="All variants exhausted.")


# ─────────────────────────────────────────────────────────────────────────────
# Risk & due-diligence
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
# ZIP
# ─────────────────────────────────────────────────────────────────────────────

def build_zip(files: dict[str, bytes]) -> BytesIO:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# AI
# ─────────────────────────────────────────────────────────────────────────────

def build_ai_prompt(ecn_text: str, attachments: list[str], results: list[DownloadResult]) -> str:
    downloaded = [r.filename for r in results if r.success]
    failed     = [r.filename for r in results if not r.success]
    return f"""You are a senior SharkNinja quality engineer reviewing an Engineering Change Notice (ECN).

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
Respond in clear sections with concise bullet points."""


def run_ai_analysis(ecn_text: str, attachments: list[str], results: list[DownloadResult], api_key: str, model: str) -> str:
    from openai import OpenAI
    r = OpenAI(api_key=api_key).chat.completions.create(
        model=model, temperature=0.2,
        messages=[{"role": "user", "content": build_ai_prompt(ecn_text, attachments, results)}],
    )
    return r.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# Review pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_review(
    ecn_text: str,
    access_token: str,
    openai_api_key: Optional[str] = None,
    ai_model: str = "gpt-4o",
    manual_files: Optional[dict[str, bytes]] = None,
) -> ECNReport:
    text          = normalize(ecn_text)
    sharepoint_id = extract_sharepoint_id(text)
    attachments   = extract_attachments(text)
    session       = make_session(access_token)

    download_results: list[DownloadResult] = []
    if sharepoint_id and attachments:
        for filename in attachments:
            result = download_attachment(session, filename, sharepoint_id)
            if not result.success and manual_files and filename in manual_files:
                result = DownloadResult(
                    filename=filename, success=True, url="manual-upload",
                    data=manual_files[filename], method="manual",
                    size_bytes=len(manual_files[filename]),
                )
            download_results.append(result)

    if manual_files:
        parsed = {r.filename for r in download_results}
        for name, data in manual_files.items():
            if name not in parsed:
                download_results.append(DownloadResult(
                    filename=name, success=True, url="manual-upload",
                    data=data, method="manual", size_bytes=len(data),
                ))

    risk     = rule_based_risk(text)
    dd_items = dd_completeness(text)
    ai_out: Optional[str] = None

    if openai_api_key:
        try:
            ai_out = run_ai_analysis(text, attachments, download_results, openai_api_key, ai_model)
        except Exception as exc:
            ai_out = f"⚠️ AI analysis failed: {exc}"

    return ECNReport(
        sharepoint_id=sharepoint_id, attachments=attachments,
        download_results=download_results, risk=risk,
        dd_items=dd_items, ai_analysis=ai_out,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #1e2130; border: 1px solid #2d3250;
    border-radius: 10px; padding: 1rem 1.2rem;
}
.risk-badge { display:inline-block; padding:.35em .9em; border-radius:20px; font-weight:700; font-size:1.1rem; letter-spacing:.05em; }
.risk-HIGH   { background:#ff4b4b22; color:#ff4b4b; border:1px solid #ff4b4b55; }
.risk-MEDIUM { background:#ffa72222; color:#ffa722; border:1px solid #ffa72255; }
.risk-LOW    { background:#21c35422; color:#21c354; border:1px solid #21c35455; }
.section-header {
    font-size:1rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
    color:#a0aec0; margin:1.5rem 0 .5rem 0; padding-bottom:.3rem; border-bottom:1px solid #2d3250;
}
.attach-row { display:flex; align-items:center; gap:.6rem; padding:.3rem 0; font-size:.9rem; color:#e2e8f0; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────
for k, v in {"auth_session": None, "login_error": ""}.items():
    if k not in st.session_state:
        st.session_state[k] = v


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
# LOGIN SCREEN
# ─────────────────────────────────────────────────────────────────────────────

def show_login(tenant_id: Optional[str], client_id: Optional[str]) -> None:
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("---")
        st.image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/SharkNinja_logo.svg/320px-SharkNinja_logo.svg.png",
            width=160,
        )
        st.markdown("## 🔍 ECN Risk Assessment")
        st.markdown("Sign in with your SharkNinja Microsoft account to continue.")
        st.markdown("---")

        if not tenant_id or not client_id:
            st.error(
                "**Azure AD is not configured.**  \n"
                "Add `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` to `.streamlit/secrets.toml` "
                "or as environment variables.  \nSee the setup instructions at the top of `ecn_app.py`.",
                icon="⚙️",
            )
            return

        with st.form("login_form"):
            email    = st.text_input("Email", placeholder="you@sharkninja.com")
            password = st.text_input("Password", type="password")
            submit   = st.form_submit_button("Sign in", use_container_width=True, type="primary")

        if st.session_state["login_error"]:
            st.error(st.session_state["login_error"], icon="❌")

        if submit:
            if not email.strip() or not password.strip():
                st.session_state["login_error"] = "Please enter your email and password."
                st.rerun()
            with st.spinner("Signing in…"):
                auth, error = msal_sign_in(email.strip(), password, tenant_id, client_id)
            if auth:
                st.session_state["auth_session"] = auth
                st.session_state["login_error"]  = ""
                st.rerun()
            else:
                st.session_state["login_error"] = error or "Sign-in failed."
                st.rerun()

        st.caption("Your credentials go directly to Microsoft. This app never stores your password.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────

def show_main_app(auth: AuthSession, tenant_id: str, client_id: str) -> None:
    # Refresh token if close to expiry
    auth, refresh_err = refresh_if_needed(auth, tenant_id, client_id)
    if refresh_err:
        st.session_state["auth_session"] = None
        st.session_state["login_error"]  = "Your session expired. Please sign in again."
        st.rerun()
    st.session_state["auth_session"] = auth

    # ── Sidebar ───────────────────────────────────────────────────────────
    with st.sidebar:
        st.image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/SharkNinja_logo.svg/320px-SharkNinja_logo.svg.png",
            width=160,
        )
        st.title("ECN Risk Tool")
        st.caption("Engineering Change Notice Reviewer")
        st.divider()

        st.markdown("### 👤 Signed in as")
        st.markdown(f"`{auth.username}`")
        mins_left = max(0, int((auth.expires_at - time.time()) / 60))
        st.caption(f"Session expires in ~{mins_left} min")
        if st.button("Sign out", use_container_width=True):
            st.session_state["auth_session"] = None
            st.rerun()

        st.divider()

        st.markdown("### 🤖 AI Analysis")
        stored_key, key_source = resolve_api_key()
        if key_source == "secrets":
            st.success("API key loaded from Streamlit secrets.", icon="✅")
        elif key_source == "env":
            st.info("API key loaded from environment.", icon="ℹ️")
        else:
            st.warning("No API key found.", icon="⚠️")

        manual_key = "" if stored_key else st.text_input("OpenAI API key", type="password", placeholder="sk-...")
        api_key    = stored_key or manual_key or None
        use_ai     = st.toggle("Enable AI analysis", value=bool(api_key))
        ai_model   = st.selectbox("Model", ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"], index=0)

        st.divider()

        st.markdown("### ⚙️ Options")
        show_diagnostics = st.toggle("Show download diagnostics", value=False)

    # ── Page ──────────────────────────────────────────────────────────────
    st.markdown("## 🔍 ECN Risk Assessment")
    st.caption("Paste an Engineering Change Notice below to analyse risk, check due diligence, and download attachments.")
    st.divider()

    ecn_text = st.text_area("ECN Content", height=280, placeholder="Paste full ECN text here…", label_visibility="collapsed")

    col_upload, col_run = st.columns([3, 1])
    with col_upload:
        manual_uploads = st.file_uploader("Upload attachments manually (fallback if Hive download fails)", accept_multiple_files=True)
    with col_run:
        st.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
        run = st.button("▶  Run Review", type="primary", use_container_width=True)

    if not run:
        return

    if not ecn_text.strip():
        st.error("Please paste ECN content before running the review.")
        return

    manual_files: dict[str, bytes] = {}
    if manual_uploads:
        for f in manual_uploads:
            manual_files[f.name] = f.read()

    with st.spinner("Running ECN review…"):
        report = run_review(
            ecn_text=ecn_text,
            access_token=auth.access_token,
            openai_api_key=api_key if use_ai else None,
            ai_model=ai_model,
            manual_files=manual_files or None,
        )

    st.divider()

    # Metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("SharePoint ID",  report.sharepoint_id or "—")
    m2.metric("Attachments",    len(report.attachments))
    m3.metric("Downloaded",     len(report.downloaded))
    m4.metric("Failed",         len(report.failed))
    m5.metric("Manual uploads", len(manual_files))
    st.divider()

    left, right = st.columns([1, 1], gap="large")

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
                    st.code(candidate_urls(name, report.sharepoint_id)[0], language=None)

        st.markdown('<div class="section-header">⬇️ Download Status</div>', unsafe_allow_html=True)

        if report.download_results:
            for r in report.download_results:
                if r.success:
                    size_str = f"{r.size_bytes/1024:.1f} KB" if r.size_bytes else ""
                    st.success(f"**{r.filename}** &nbsp;·&nbsp; {r.method} {size_str}", icon="✅")
                    if show_diagnostics:
                        st.caption(f"URL: {r.url}  ·  Type: {r.content_type}  ·  Status: {r.status_code}")
                else:
                    st.error(f"**{r.filename}**", icon="❌")
                    st.caption(r.error or "Unknown error")
                    if show_diagnostics:
                        with st.expander(f"Diagnostics — {r.filename}", expanded=False):
                            st.write(f"**Status:** {r.status_code or 'N/A'}")
                            st.write(f"**Content-Type:** {r.content_type or 'N/A'}")
                            st.write(f"**Last URL:** `{r.url}`")
                            if r.redirect_chain:
                                st.write("**Redirect chain:**")
                                for u in r.redirect_chain: st.caption(u)
                            if report.sharepoint_id:
                                st.write("**All URL variants tried:**")
                                for u in candidate_urls(r.filename, report.sharepoint_id): st.caption(u)

            if any(r for r in report.failed if r.error and "expired" in r.error.lower()):
                st.warning("Your session may have expired. Try signing out and back in.", icon="🔐")
        else:
            st.info("No downloads attempted — SharePoint ID or attachments not found.")

        all_files = {r.filename: r.data for r in report.downloaded if r.data}
        all_files.update(manual_files)
        if all_files:
            st.markdown('<div class="section-header">📦 Package</div>', unsafe_allow_html=True)
            st.download_button(
                label=f"⬇️  Download {len(all_files)} file(s) as ZIP",
                data=build_zip(all_files),
                file_name=f"ECN_{report.sharepoint_id or 'unknown'}_attachments.zip",
                mime="application/zip",
                use_container_width=True,
            )

    with right:
        st.markdown('<div class="section-header">⚠️ Rule-Based Risk</div>', unsafe_allow_html=True)
        st.markdown(
            f'<span class="risk-badge risk-{report.risk.rating}">'
            f'{report.risk.emoji} {report.risk.rating} &nbsp;·&nbsp; score {report.risk.score}</span>',
            unsafe_allow_html=True,
        )
        st.markdown("")
        if report.risk.findings:
            for f in report.risk.findings: st.warning(f, icon="⚠️")
        else:
            st.success("No rule-based risk triggers detected.", icon="✅")

        st.markdown('<div class="section-header">✅ Due Diligence Checklist</div>', unsafe_allow_html=True)
        dd_cols = st.columns(2)
        for i, item in enumerate(report.dd_items):
            col = dd_cols[i % 2]
            if item.present: col.success(item.label, icon="✅")
            else:            col.error(item.label, icon="❌")

    st.divider()

    st.markdown('<div class="section-header">🤖 AI Risk Assessment</div>', unsafe_allow_html=True)
    if report.ai_analysis:
        st.markdown(report.ai_analysis)
    elif not use_ai:
        st.info("AI analysis is disabled. Enable it in the sidebar.", icon="ℹ️")
    elif not api_key:
        st.warning("No OpenAI API key available. Add one in the sidebar.", icon="⚠️")

    st.divider()

    with st.expander("📋 Raw summary (JSON)", expanded=False):
        st.json({
            "sharepoint_id":      report.sharepoint_id,
            "parsed_attachments": len(report.attachments),
            "downloaded":         len(report.downloaded),
            "failed":             len(report.failed),
            "manual_uploads":     len(manual_files),
            "risk_score":         report.risk.score,
            "risk_rating":        report.risk.rating,
            "risk_findings":      report.risk.findings,
            "due_diligence":      [{"item": d.label, "present": d.present} for d in report.dd_items],
            "ai_analysis_run":    report.ai_analysis is not None,
            "download_details": [
                {"filename": r.filename, "success": r.success, "method": r.method,
                 "status_code": r.status_code, "content_type": r.content_type,
                 "size_bytes": r.size_bytes, "error": r.error, "url": r.url}
                for r in report.download_results
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
tenant_id, client_id = get_azure_config()
auth_session: Optional[AuthSession] = st.session_state["auth_session"]

if auth_session is None:
    show_login(tenant_id, client_id)
else:
    show_main_app(auth_session, tenant_id or "", client_id or "")
