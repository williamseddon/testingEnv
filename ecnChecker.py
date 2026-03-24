import streamlit as st
import re
import urllib.parse
import requests
from io import BytesIO

st.set_page_config(page_title="ECN Risk Assessment Tool", layout="wide")

st.title("📊 ECN Risk Assessment Tool")

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
            return BytesIO(r.content)
        else:
            return None
    except:
        return None


def risk_checks(text):
    risks = []

    if "Need DQTP" in text or "Proposed DQTP" in text:
        risks.append("⚠️ DQTP not confirmed complete")

    if "no design" in text.lower() and "supplier" in text.lower():
        risks.append("⚠️ Supplier change with 'no design impact' → high hidden risk")

    if "Need EE" in text or "Electrical Changes Required" in text:
        risks.append("⚠️ Electrical impact not fully validated")

    if "No impact confirmed" in text:
        risks.append("⚠️ Compliance marked 'no impact' → verify with evidence")

    if "Cost Reduction" in text or "VAVE" in text:
        risks.append("⚠️ Cost reduction ECN → risk of performance degradation")

    return risks

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
            urls.append(url)
            st.write(url)

        st.subheader("⬇️ Download Status")
        downloaded_files = {}
        for url in urls:
            file_data = download_file(url)
            if file_data:
                st.success(f"Downloaded: {url.split('/')[-1]}")
                downloaded_files[url] = file_data
            else:
                st.error(f"Failed: {url}")

        st.subheader("🧠 Risk Assessment")
        risks = risk_checks(user_input)

        if risks:
            for r in risks:
                st.warning(r)
        else:
            st.success("No major risks detected")

        st.subheader("📋 DD Completeness Check")

        checklist_items = [
            "Explanation of Change",
            "Redlined EBOM",
            "Proposed DQTP",
            "Compliance Plan",
            "Electrical Changes Required",
        ]

        for item in checklist_items:
            if item in user_input:
                st.success(f"✔ {item} present")
            else:
                st.error(f"✖ Missing: {item}")

        st.subheader("📊 Summary")

        st.write({
            "SharePoint ID": sp_id,
            "# Attachments": len(attachments),
            "# Risks": len(risks)
        })
