import streamlit as st
import re
import urllib.parse
import requests
from io import BytesIO
from openai import OpenAI
import zipfile
import os

st.set_page_config(page_title="ECN Risk Assessment Tool", layout="wide")

st.title("📊 ECN Risk Assessment Tool (AI Enhanced)")

# -----------------------------
# OpenAI Setup
# -----------------------------

def get_api_key():
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

stored_api_key, api_source = get_api_key()

if api_source == "streamlit_secrets":
    st.success("OpenAI API key loaded from Streamlit secrets.")
elif api_source == "environment":
    st.info("OpenAI API key loaded from environment variable.")
else:
    st.warning("No OpenAI API key found in Streamlit secrets or environment. Please paste your API key below.")

manual_api_key = st.text_input("Enter OpenAI API Key", type="password") if not stored_api_key else ""
api_key = stored_api_key or manual_api_key
client = OpenAI(api_key=api_key) if api_key else None

# -----------------------------
# Helpers
# -----------------------------

def extract_sharepoint_id(text):
    match = re.search(r"SharePoint ID\s*#\s*(\d+)", text)
    return match.group(1) if match else None


def extract_attachments(text):
    match = re.search(
        r"\\bAttachments\\b\\s*(.*?)\\s*(?:\\bPriority\\b|\\bDisposition of Stock\\b|\\bChecklist \(Initiator, Lead Engineer, Engineer Delegate\)\\b)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    block = match.group(1)
    files = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        files.append(line)
    return files


def convert_to_url(filename, sp_id):
    filename_nbsp = filename.replace(" ", "\u00A0")
    encoded = urllib.parse.quote(filename_nbsp, safe="()-.%")
    return f"https://hive.sharkninja.com/quality/root/Lists/ECN%20Live/Attachments/{sp_id}/{encoded}"


def download_file(url):
    try:
        r = requests.get(url)
        if r.status_code == 200:
            return r.content
        else:
            return None
    except:
        return None


def ai_risk_analysis(text, attachments, client):
    if not client:
        return "No API key provided"

    prompt = f"""
    You are a senior Quality Engineer reviewing an Engineering Change Notice (ECN).

    Analyze the ECN and identify:
    1. Key risks
    2. Missing due diligence
    3. Contradictions in the ECN
    4. Supplier / cost / compliance risks
    5. Final recommendation (GO / CONDITIONAL GO / NO GO)

    ECN TEXT:
    {text}

    ATTACHMENTS:
    {attachments}
    """

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message.content

# -----------------------------
# UI
# -----------------------------

user_input = st.text_area("Paste ECN Content Here", height=400)

if st.button("Run ECN Risk Assessment"):
    if not user_input:
        st.warning("Please paste ECN data")
    else:
        sp_id = extract_sharepoint_id(user_input)

        st.subheader("🔗 SharePoint ID")
        st.write(sp_id)

        attachments = extract_attachments(user_input)

        st.subheader("📎 Attachment Links")
        urls = []
        for f in attachments:
            url = convert_to_url(f, sp_id)
            urls.append((f, url))
            st.write(url)

        st.subheader("⬇️ Download Status")
        downloaded_files = {}
        for filename, url in urls:
            file_data = download_file(url)
            if file_data:
                st.success(f"Downloaded: {filename}")
                downloaded_files[filename] = file_data
            else:
                st.error(f"Failed: {filename}")

        # -----------------------------
        # ZIP DOWNLOAD BUTTON
        # -----------------------------
        if downloaded_files:
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for filename, data in downloaded_files.items():
                    zip_file.writestr(filename, data)

            zip_buffer.seek(0)

            st.download_button(
                label="📦 Download All Attachments",
                data=zip_buffer,
                file_name=f"ECN_{sp_id}_attachments.zip",
                mime="application/zip"
            )

                st.subheader("🤖 AI Risk Assessment")
        if client:
            ai_output = ai_risk_analysis(user_input, [u[0] for u in urls], client)
            st.write(ai_output)
        else:
            st.error("No OpenAI API key available. Add OPENAI_API_KEY to Streamlit secrets, set an environment variable, or paste your API key above.")

        st.subheader("📊 Summary")
        st.write({
            "SharePoint ID": sp_id,
            "# Attachments": len(attachments)
        })
