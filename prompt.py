import streamlit as st
import pandas as pd
from io import BytesIO
from openai import OpenAI

# -----------------------------
# Streamlit UI Configuration
# -----------------------------
st.set_page_config(page_title="AI Data Assistant", layout="wide")
st.title("📊 AI Assistant for Row-by-Row Analysis")

# -----------------------------
# API Key Input
# -----------------------------
st.sidebar.header("🔐 API Settings")
api_key = st.sidebar.text_input("Enter your OpenAI API Key", type="password")
model_choice = st.sidebar.selectbox(
    "Select Model", ["gpt-4.1", "gpt-4o-mini", "gpt-4o", "o1"]
)

# -----------------------------
# File Upload
# -----------------------------
st.header("1️⃣ Upload Your File")
uploaded_file = st.file_uploader("Upload Excel or CSV file", type=["xlsx", "csv"])

if uploaded_file:
    # Load data
    if uploaded_file.name.endswith("csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)

    st.write("### Preview of Uploaded Data")
    st.dataframe(df.head())

    # -----------------------------
    # Filter Settings
    # -----------------------------
    st.header("2️⃣ Apply Optional Filters Before Processing")

    filter_disposition = st.checkbox("Filter: Disposition Sub Group = 'Defective Product'")
    filter_troubleshoot = st.checkbox("Filter: Troubleshooting Result (must not be blank)")

    # Additional filter placeholders
    additional_filters = st.multiselect(
        "Additional Filters (Exact Match)", options=df.columns
    )
    additional_filter_values = {}

    for col in additional_filters:
        val = st.text_input(f"Value for {col}")
        if val:
            additional_filter_values[col] = val

    # Apply Filters
    filtered_df = df.copy()

    if filter_disposition and "Disposition Sub Group" in df.columns:
        filtered_df = filtered_df[filtered_df["Disposition Sub Group"] == "Defective Product"]

    if filter_troubleshoot and "Troubleshooting Result" in df.columns:
        filtered_df = filtered_df[filtered_df["Troubleshooting Result"].notna()]

    for col, val in additional_filter_values.items():
        filtered_df = filtered_df[filtered_df[col] == val]

    st.write("### Filtered Result Preview")
    st.dataframe(filtered_df.head())

    # -----------------------------
    # Prompt Settings
    # -----------------------------
    st.header("3️⃣ Select Prompts and Process Data")

    multi_prompt = st.checkbox("Enable Multiple Prompts")

    if multi_prompt:
        num_prompts = st.number_input(
            "How many prompts?", min_value=1, max_value=10, step=1, value=1
        )
        prompts = []
        for i in range(num_prompts):
            prompt = st.text_area(f"Prompt {i+1}")
            prompts.append(prompt)
    else:
        single_prompt = st.text_area("Enter Prompt")
        prompts = [single_prompt]

    # -----------------------------
    # Column selection (Fixed: Only Zoom Summary)
    # -----------------------------
    st.write("### Column Fixed to: 'Zoom Summary'")
    if "Zoom Summary" not in df.columns:
        st.error("Zoom Summary column not found in the file.")
        st.stop()

    # -----------------------------
    # Run Processing
    # -----------------------------
    if st.button("Run AI Processing"):
        if not api_key:
            st.error("Please enter your API key in the sidebar.")
            st.stop()

        client = OpenAI(api_key=api_key)

        results_df = df.copy()

        for idx, row in filtered_df.iterrows():
            zoom_text = str(row.get("Zoom Summary", ""))

            for p_i, prompt in enumerate(prompts):
                if not prompt.strip():
                    continue
                try:
                    response = client.chat.completions.create(
                        model=model_choice,
                        messages=[
                            {"role": "system", "content": "You are a helpful AI assistant."},
                            {
                                "role": "user",
                                "content": f"PROMPT: {prompt}\nTEXT: {zoom_text}"
                            }
                        ]
                    )
                    output_text = response.choices[0].message["content"]
                except Exception as e:
                    output_text = f"ERROR: {e}"

                col_name = f"AI_Output_{p_i+1}"
                results_df.loc[idx, col_name] = output_text

        # -----------------------------
        # Download Output
        # -----------------------------
        st.success("Processing complete! Download your file below.")

        buffer = BytesIO()
        results_df.to_excel(buffer, index=False)
        buffer.seek(0)

        st.download_button(
            label="Download Processed File",
            data=buffer,
            file_name="processed_output.xlsx",
            mime="application/vnd.ms-excel"
        )

