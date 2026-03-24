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
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_sharepoint_id(text: str):
    text = normalize_text(text)
    match = re.search(r"SharePoint ID\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_field(text: str, field_name: str):
    """
    Best-effort extractor for simple ECN fields.
    """
    text = normalize_text(text)
    pattern = rf"{re.escape(field_name)}\s*\n(.+?)(?=\n[A-Z][^\n]*\n|\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value else None


def extract_attachments(text: str):
    """
    Extract lines between 'Attachments' and the next known section.
    Handles blank lines robustly.
    """
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

        # Keep likely file lines only
        if re.search(
            r"\.(pdf|xlsx|xls|doc|docx|ppt|pptx|msg|zip|csv|txt)$",
            clean,
            flags=re.IGNORECASE,
        ):
            files.append(clean)

    # Remove duplicates while preserving order
    seen = set()
    deduped = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    return deduped


def convert_to_hive_url(filename: str, sharepoint_id: str):
    """
    Hive appears to use non-breaking spaces in attachment URLs.
    """
    filename_nbsp = filename.replace(" ", "\u00A0")
    encoded_filename = urllib.parse.quote(filename_nbsp, safe="()-.")
    return f"{HIVE_BASE}/{sharepoint_id}/{encoded_filename}"


# =========================================================
# Helpers: download
# =========================================================
def download_file(url: str, timeout: int = 30):
    """
    Attempts to download a file.
    Returns (success, content_bytes, error_message)
    """
    try:
        response = requests.get(url, timeout=timeout)
        content_type = response.headers.get("content-type", "").lower()

        if response.status_code != 200:
            return False, None, f"HTTP {response.status_code}"

        # Catch likely login / HTML pages
        if "text/html" in content_type:
            return False, None, "Received HTML instead of file (likely auth required)"

        return True, response.content, None

    except requests.RequestException as exc:
        return False, None, str(exc)


def build_zip(file_dict: dict):
    """
    file_dict = {filename: bytes}
    """
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
    t = normalize_text(text)
    tl = t.lower()

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

    results = []
    for item in required_items:
        present = item.lower() in t.lower()
        results.append({"item": item, "present": present})

    return results


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

Your task:
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
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )
    return response.choices[0].message.content


# =========================================================
# Sidebar: API key setup
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

api_key = stored_api_key or manual_api_key
client = OpenAI(api_key=api_key) if (api_key and use_ai) else None

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

    # -------------------------
    # Parse basics
    # -------------------------
    sharepoint_id = extract_sharepoint_id(text)
    attachments = extract_attachments(text)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("SharePoint ID", sharepoint_id if sharepoint_id else "Not found")
    with col2:
        st.metric("Parsed attachments", len(attachments))
    with col3:
        st.metric("Manual uploads", len(uploaded_fallback_files) if uploaded_fallback_files else 0)

    # -------------------------
    # Attachment preview + URLs
    # -------------------------
    st.subheader("Parsed Attachments")

    if attachments:
        for idx, name in enumerate(attachments, start=1):
            st.write(f"{idx}. {name}")
    else:
        st.warning("No attachments were parsed from the pasted ECN text.")

    st.subheader("Hive Attachment Links")
    attachment_urls = []

    if sharepoint_id and attachments:
        for name in attachments:
            url = convert_to_hive_url(name, sharepoint_id)
            attachment_urls.append((name, url))
            st.code(url)
    else:
        if not sharepoint_id:
            st.warning("SharePoint ID not found, so Hive links could not be built.")
        if not attachments:
            st.warning("No attachment names found, so Hive links could not be built.")

    # -------------------------
    # Download attachments from Hive
    # -------------------------
    st.subheader("Attachment Download Status")

    download_results = {}
    downloaded_files = {}

    if attachment_urls:
        for filename, url in attachment_urls:
            success, data, error = download_file(url)
            download_results[filename] = {
                "success": success,
                "url": url,
                "error": error,
            }

            if success:
                downloaded_files[filename] = data
                st.success(f"Downloaded: {filename}")
            else:
                st.error(f"Failed: {filename} — {error}")

    # -------------------------
    # Manual upload fallback
    # -------------------------
    fallback_files = {}
    if uploaded_fallback_files:
        uploaded_names = {f.name: f for f in uploaded_fallback_files}

        for expected_name in attachments:
            if expected_name in uploaded_names:
                fallback_files[expected_name] = uploaded_names[expected_name].read()

        # Include any extra uploaded files too
        for uploaded in uploaded_fallback_files:
            if uploaded.name not in fallback_files:
                fallback_files[uploaded.name] = uploaded.read()

    if fallback_files:
        st.subheader("Manual Upload Fallback")
        for name in fallback_files:
            st.info(f"Available via upload: {name}")

    # Combine downloaded + uploaded fallback for ZIP
    all_available_files = dict(downloaded_files)
    for name, data in fallback_files.items():
        if name not in all_available_files:
            all_available_files[name] = data

    # -------------------------
    # Download-all ZIP
    # -------------------------
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

    # -------------------------
    # Rule-based risk review
    # -------------------------
    st.subheader("Rule-Based Risk Review")

    risks, risk_score = rule_based_risk_checks(text)
    risk_rating = score_to_rating(risk_score)

    st.metric("Rule-based risk rating", risk_rating)
    st.metric("Rule-based risk score", risk_score)

    if risks:
        for risk in risks:
            st.warning(risk)
    else:
        st.success("No obvious rule-based risks were detected.")

    # -------------------------
    # DD completeness
    # -------------------------
    st.subheader("Due Diligence Completeness Check")

    dd_results = dd_completeness_check(text)
    for row in dd_results:
        if row["present"]:
            st.success(f"Present: {row['item']}")
        else:
            st.error(f"Missing: {row['item']}")

    # -------------------------
    # AI analysis
    # -------------------------
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

    # -------------------------
    # Summary
    # -------------------------
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
