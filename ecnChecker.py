import streamlit as st
import re
import urllib.parse
import requests
from io import BytesIO
from openai import OpenAI
import zipfile

st.set_page_config(page_title="ECN Risk Assessment Tool", layout="wide")

st.title("📊 ECN Risk Assessment Tool (AI Enhanced)")

# -----------------------------
# OpenAI Setup
# -----------------------------

api_key = st.text_input("Enter OpenAI API Key", type="password")
client = OpenAI(api_key=api_key) if api_key else None

# -----------------------------
# Helpers
# -----------------------------

def extract_sharepoint_id(text):
    match = re.search(r"SharePoint ID\s*#\s*(\d+)", text)
    return match.group(1) if match else None


def extract_attachments(text):
    if "Attachments" not in text:
        return []
    section = text.split("Attachments", 1)[1]
    lines = section.split("\n")
    files = []
    for line in lines:
        line = line.strip()
        if line == "" or line.startswith("Priority"):
            break
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
        ai_output = ai_risk_analysis(user_input, [u[0] for u in urls], client)
        st.write(ai_output)

        st.subheader("📊 Summary")
        st.write({
            "SharePoint ID": sp_id,
            "# Attachments": len(attachments)
        })

