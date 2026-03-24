import os
import re
import zipfile
import urllib.parse
from io import BytesIO

import requests
import streamlit as st
from openai import OpenAI

# =========================================================
# Page config
# =========================================================
st.set_page_config(page_title="ECN Risk Assessment Tool", layout="wide")
st.title("ECN Risk Assessment Tool")

# =========================================================
# Constants
# =========================================================
HIVE_BASE = "https://hive.sharkninja.com/quality/root/Lists/ECN%20Live/Attachments"
FILE_EXT_PATTERN = r"\.(pdf|xlsx|xls|doc|docx|ppt|pptx|msg|zip|csv|txt)$"

# =========================================================
# Helpers: API key
# =========================================================
def get_openai_api_key():
    secrets_key = None
    env_key = os.getenv("OPENAI_API_KEY")

    try:
        secrets_key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        secrets_key = None

    if secrets_key:
        return secrets_key, "streamlit_secrets"
    if env_key:
        return env_key, "environment"
    return None, "missing"


# =========================================================
# Helpers: parsing
# =========================================================
def normalize_text(text: str) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00A0", " ")


def extract_sharepoint_id(text: str):
    text = normalize_text(text)
    match = re.search(r"SharePoint ID\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_attachments(text: str):
    text = normalize_text(text)

    match = re.search(
        r"\bAttachments\b\s*(.*?)\s*(?:\bPriority\b|\bDisposition of Stock\b|\bChecklist\b|\bReviews/Approvals\b|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return []

    block = match.group(1)
    files = []

    for line in block.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if re.search(FILE_EXT_PATTERN, clean, flags=re.IGNORECASE):
            files.append(clean)

    seen = set()
    deduped = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    return deduped


# =========================================================
# Helpers: URL generation
# =========================================================
def convert_to_hive_url(filename: str, sharepoint_id: str, use_nbsp: bool = False):
    name = filename.replace(" ", "\u00A0") if use_nbsp else filename
    encoded_filename = urllib.parse.quote(name, safe="()-._")
    return f"{HIVE_BASE}/{sharepoint_id}/{encoded_filename}"


def get_candidate_urls(filename: str, sharepoint_id: str):
    return [
        convert_to_hive_url(filename, sharepoint_id, use_nbsp=False),
        convert_to_hive_url(filename, sharepoint_id, use_nbsp=True),
    ]


# =========================================================
# Helpers: auth/session
# =========================================================
def make_session(auth_cookie: str = ""):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
    )
    if auth_cookie.strip():
        session.headers.update({"Cookie": auth_cookie.strip()})
    return session


def detect_signin_or_html(response: requests.Response):
    content_type = response.headers.get("content-type", "").lower()
    text_snippet = response.text[:5000].lower() if "text" in content_type or "html" in content_type else ""

    if "text/html" in content_type:
        return True, "Received HTML instead of a file. Sign-in may be required."

    signin_markers = [
        "sign in",
        "signin",
        "microsoft",
        "office 365",
        "sharepoint",
        "login",
        "authentication",
    ]
    if any(marker in text_snippet for marker in signin_markers):
        return True, "Authentication page detected instead of attachment content."

    return False, ""


# =========================================================
# Helpers: download
# =========================================================
def try_download(session: requests.Session, url: str, timeout: int = 30):
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return False, None, f"Request failed: {exc}"

    if response.status_code != 200:
        return False, None, f"HTTP {response.status_code}"

    signin_detected, signin_reason = detect_signin_or_html(response)
    if signin_detected:
        return False, None, signin_reason

    return True, response.content, None


def download_file_from_candidates(session: requests.Session, filename: str, sharepoint_id: str, timeout: int = 30):
    candidates = get_candidate_urls(filename, sharepoint_id)
    errors = []

    for url in candidates:
        success, data, error = try_download(session, url, timeout=timeout)
        if success:
            return True, data, None, url
        errors.append(f"{url} -> {error}")

    return False, None, " | ".join(errors), candidates[0]


def build_zip(file_dict: dict):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in file_dict.items():
            zf.writestr(filename, data)
    zip_buffer.seek(0)
    return zip_buffer


# =========================================================
# Helpers: rule-based checks
# =========================================================
def rule_based_risk_checks(text: str):
    tl = normalize_text(text).lower()
    risks = []
    score = 0

    if "cost reduction" in tl or "vave" in tl:
        risks.append("Cost reduction / VAVE ECN: verify no hidden performance or reliability degradation.")
        score += 2

    if "supplier name" in tl and "leshow" in tl:
        risks.append("Supplier-related change detected: confirm source qualification, consistency, and traceability.")
        score += 2

    if "no design nor ebom change" in tl or "no design impact" in tl:
        risks.append("Claim of no design impact: verify against test evidence, EBOM linkage, and approval comments.")
        score += 2

    if "need dqtp" in tl or "proposed dqtp" in tl:
        risks.append("DQTP required or referenced: ensure test plan is complete and results support the change.")
        score += 2

    if "electrical changes required" in tl or "need ee" in tl:
        risks.append("Electrical review required: do not assume no PCBA impact until EE review is closed with evidence.")
        score += 3

    if "no impact confirmed" in tl:
        risks.append("Compliance marked 'No impact confirmed': confirm objective evidence exists in attachments.")
        score += 2

    if "change not compatible with earlier date codes" in tl:
        risks.append("Date code compatibility issue: review stock disposition, field interchangeability, and rework plan.")
        score += 3

    if "17 affected skus" in tl:
        risks.append("Many affected SKUs: confirm SKU-level EBOM / PLM linkage for all impacted configurations.")
        score += 2

    return risks, score


def dd_completeness_check(text: str):
    t = normalize_text(text)
    required_items = [
        "Explanation of Change",
        "Redlined EBOM",
        "Proposed DQTP",
        "Compliance Plan",
        "Electrical Changes Required",
    ]
    return [{"item": item, "present": item.lower() in t.lower()} for item in required_items]


def score_to_rating(score: int):
    if score >= 8:
        return "High"
    if score >= 4:
        return "Medium"
    return "Low"


# =========================================================
# Helpers: AI
# =========================================================
def build_ai_prompt(ecn_text: str, attachments: list, download_results: dict):
    downloaded = [name for name, meta in download_results.items() if meta["success"]]
    failed = [name for name, meta in download_results.items() if not meta["success"]]

    return f"""
You are a senior SharkNinja quality engineer reviewing an Engineering Change Notice (ECN).

Tasks:
1. Summarize the ECN.
2. Identify key technical, quality, compliance, supply chain, and implementation risks.
3. Identify missing due diligence.
4. Identify contradictions or weak logic in the ECN.
5. Comment on whether the approvals and comments are sufficient.
6. Highlight what evidence still needs to be reviewed from attachments.
7. Give a final recommendation: GO / CONDITIONAL GO / NO GO.
8. Give a concise rationale.

ECN TEXT:
{ecn_text}

PARSED ATTACHMENTS:
{attachments}

DOWNLOADED ATTACHMENTS:
{downloaded}

FAILED ATTACHMENTS:
{failed}

Be practical and skeptical. Focus on hidden risk, incomplete evidence, and implementation gaps.
Respond in clear sections with concise bullets.
""".strip()


def ai_risk_analysis(client: OpenAI, ecn_text: str, attachments: list, download_results: dict):
    prompt = build_ai_prompt(ecn_text, attachments, download_results)
    response = client.chat.completions.create(
        model="gpt-5",
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# =========================================================
# Sidebar
# =========================================================
stored_api_key, api_source = get_openai_api_key()

with st.sidebar:
    st.header("Settings")

    if api_source == "streamlit_secrets":
        st.success("OpenAI API key loaded from Streamlit secrets.")
    elif api_source == "environment":
        st.info("OpenAI API key loaded from environment variable.")
    else:
        st.warning("No OpenAI API key found in secrets or environment.")

    manual_api_key = ""
    if not stored_api_key:
        manual_api_key = st.text_input("Paste OpenAI API key", type="password")

    use_ai = st.checkbox("Enable AI analysis", value=True)
    try_nbsp = st.checkbox("Try NBSP attachment URL fallback", value=True)

    st.markdown("### Hive authentication")
    st.caption(
        "Direct attachment downloads may fail if Hive requires Microsoft / SharePoint sign-in. "
        "If that happens, either paste an authenticated Cookie header below or upload files manually in the main app."
    )
    auth_cookie = st.text_area(
        "Optional Cookie header for authenticated Hive requests",
        height=140,
        placeholder="FedAuth=...; rtFa=...; other_cookie=...",
    )

api_key = stored_api_key or manual_api_key
client = OpenAI(api_key=api_key) if (api_key and use_ai) else None
session = make_session(auth_cookie=auth_cookie)

# =========================================================
# Main input
# =========================================================
ecn_text = st.text_area("Paste ECN content here", height=500)

uploaded_fallback_files = st.file_uploader(
    "Optional: upload attachment files manually if Hive download fails",
    accept_multiple_files=True,
)

run_clicked = st.button("Run ECN Review", type="primary")

# =========================================================
# Main app logic
# =========================================================
if run_clicked:
    if not ecn_text.strip():
        st.error("Please paste ECN content first.")
        st.stop()

    text = normalize_text(ecn_text)
    sharepoint_id = extract_sharepoint_id(text)
    attachments = extract_attachments(text)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("SharePoint ID", sharepoint_id if sharepoint_id else "Not found")
    with col2:
        st.metric("Parsed attachments", len(attachments))
    with col3:
        st.metric("Manual uploads", len(uploaded_fallback_files) if uploaded_fallback_files else 0)

    st.subheader("Parsed Attachments")
    if attachments:
        for idx, name in enumerate(attachments, start=1):
            st.write(f"{idx}. {name}")
    else:
        st.warning("No attachments were parsed from the pasted ECN text.")

    st.subheader("Hive Attachment Links")
    if sharepoint_id and attachments:
        for name in attachments:
            st.code(convert_to_hive_url(name, sharepoint_id, use_nbsp=False))
            if try_nbsp:
                st.caption(f"Fallback: {convert_to_hive_url(name, sharepoint_id, use_nbsp=True)}")
    else:
        if not sharepoint_id:
            st.warning("SharePoint ID not found, so Hive links could not be built.")
        if not attachments:
            st.warning("No attachment names found, so Hive links could not be built.")

    st.subheader("Attachment Download Status")
    download_results = {}
    downloaded_files = {}

    if attachments and sharepoint_id:
        for filename in attachments:
            if try_nbsp:
                success, data, error, working_url = download_file_from_candidates(session, filename, sharepoint_id)
            else:
                url = convert_to_hive_url(filename, sharepoint_id, use_nbsp=False)
                success, data, error = try_download(session, url)
                working_url = url

            download_results[filename] = {
                "success": success,
                "url": working_url,
                "error": error,
            }

            if success:
                downloaded_files[filename] = data
                st.success(f"Downloaded: {filename}")
                st.caption(working_url)
            else:
                st.error(f"Failed: {filename}")
                st.caption(error)

        auth_needed = any(
            (not meta["success"]) and meta["error"] and ("sign-in" in meta["error"].lower() or "authentication" in meta["error"].lower() or "html" in meta["error"].lower())
            for meta in download_results.values()
        )
        if auth_needed:
            st.warning(
                "Hive appears to require authentication for one or more files. "
                "Paste your authenticated Cookie header in the sidebar or upload the files manually below."
            )

    fallback_files = {}
    if uploaded_fallback_files:
        for uploaded in uploaded_fallback_files:
            fallback_files[uploaded.name] = uploaded.read()

    if fallback_files:
        st.subheader("Manual Upload Fallback")
        for name in fallback_files:
            st.info(f"Available via upload: {name}")

    all_available_files = dict(downloaded_files)
    for name, data in fallback_files.items():
        if name not in all_available_files:
            all_available_files[name] = data

    if all_available_files:
        zip_buffer = build_zip(all_available_files)
        st.download_button(
            label="Download All Available Attachments",
            data=zip_buffer,
            file_name=f"ECN_{sharepoint_id or 'unknown'}_attachments.zip",
            mime="application/zip",
        )
    else:
        st.warning("No files are currently available to package into a ZIP.")

    st.subheader("Rule-Based Risk Review")
    risks, risk_score = rule_based_risk_checks(text)
    risk_rating = score_to_rating(risk_score)

    risk_col1, risk_col2 = st.columns(2)
    with risk_col1:
        st.metric("Rule-based risk rating", risk_rating)
    with risk_col2:
        st.metric("Rule-based risk score", risk_score)

    if risks:
        for risk in risks:
            st.warning(risk)
    else:
        st.success("No obvious rule-based risks were detected.")

    st.subheader("Due Diligence Completeness Check")
    dd_results = dd_completeness_check(text)
    for row in dd_results:
        if row["present"]:
            st.success(f"Present: {row['item']}")
        else:
            st.error(f"Missing: {row['item']}")

    st.subheader("AI Risk Assessment")
    if use_ai:
        if client:
            with st.spinner("Running AI analysis..."):
                try:
                    ai_output = ai_risk_analysis(
                        client=client,
                        ecn_text=text,
                        attachments=attachments,
                        download_results=download_results,
                    )
                    st.write(ai_output)
                except Exception as exc:
                    st.error(f"AI analysis failed: {exc}")
        else:
            st.error(
                "No OpenAI API key available. Add OPENAI_API_KEY to Streamlit secrets, "
                "set it as an environment variable, or paste it in the sidebar."
            )
    else:
        st.info("AI analysis is disabled in the sidebar.")

    st.subheader("Summary")
    downloaded_count = sum(1 for v in download_results.values() if v["success"])
    failed_count = sum(1 for v in download_results.values() if not v["success"])

    summary = {
        "SharePoint ID": sharepoint_id,
        "Parsed attachments": len(attachments),
        "Downloaded from Hive": downloaded_count,
        "Failed Hive downloads": failed_count,
        "Manual uploads available": len(fallback_files),
        "Rule-based risk score": risk_score,
        "Rule-based risk rating": risk_rating,
    }
    st.json(summary)
