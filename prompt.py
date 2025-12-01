import streamlit as st
import pandas as pd
from io import BytesIO
from openai import OpenAI
from datetime import datetime
import json

# ---------------------------------------------------------
# Streamlit Configuration
# ---------------------------------------------------------
st.set_page_config(page_title="AI Data Assistant", layout="wide")
st.title("📊 AI Assistant for Row-by-Row Analysis (Flexible LLM-Driven)")

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def infer_year_from_relative(text, call_date_str):
    """
    Infer an approximate YYYY-MM-DD string when the text contains phrases like
    'last September', using the call date column as reference.
    Returns None if nothing can be inferred.
    """
    if not isinstance(call_date_str, str) or not call_date_str:
        return None
    try:
        # Expecting something like '2024-09-12 14:30:00' or '2024-09-12'
        first_token = call_date_str.split()[0]
        call_date = datetime.strptime(first_token, "%Y-%m-%d")
        call_year = call_date.year
    except Exception:
        return None

    if not isinstance(text, str):
        return None
    text_lower = text.lower()

    months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december"
    ]
    for idx, m in enumerate(months, start=1):
        if f"last {m}" in text_lower:
            # Approximate to the 1st of that month in the previous year
            return f"{call_year - 1}-{idx:02d}-01"
    return None


def suggest_text_column(df):
    """Heuristic suggestion for which column is the main text/summary column."""
    preferred = ["Zoom Summary", "zoom_summary", "Sentiment Text", "Comment", "Customer Issue"]
    for col in preferred:
        if col in df.columns:
            return col
    obj_cols = df.select_dtypes(include=["object"]).columns.tolist()
    return obj_cols[0] if obj_cols else (df.columns[0] if len(df.columns) else None)


def suggest_date_column(df):
    """Heuristic suggestion for which column holds the call date/time."""
    preferred = ["Start Time (Date/Time)", "Start Time", "Date Created", "Created At"]
    for col in preferred:
        if col in df.columns:
            return col
    # fallback: any col with 'date' or 'time' in the name
    for col in df.columns:
        name = str(col).lower()
        if "date" in name or "time" in name:
            return col
    return None


@st.cache_resource
def get_client(api_key: str):
    return OpenAI(api_key=api_key)


def call_llm_free_text(client, model, system_prompt, user_prompt):
    """Simple helper for free-text outputs."""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content


def call_llm_json(client, model, system_prompt, user_prompt, schema_name, schema):
    """
    Helper to request strict JSON output using json_schema response_format.
    Returns a dict (parsed JSON) or {'_raw': <content>} if parsing fails.
    """
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        },
    )
    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except Exception:
        return {"_raw": content}


# ---------------------------------------------------------
# Sidebar – API & Model (use secrets + optional override)
# ---------------------------------------------------------
st.sidebar.header("🔐 API & Model Settings")

# Try to pull from secrets first
default_api_key = st.secrets.get("OPENAI_API_KEY", "")
if default_api_key:
    st.sidebar.success("Using OPENAI_API_KEY from Streamlit secrets by default.")
else:
    st.sidebar.warning("No OPENAI_API_KEY found in secrets. You can paste one below.")

api_key_input = st.sidebar.text_input(
    "OpenAI API Key (optional override)",
    type="password",
    help="Leave blank to use OPENAI_API_KEY from Streamlit secrets.",
)

api_key = api_key_input or default_api_key

model_choice = st.sidebar.selectbox(
    "Model",
    ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
    index=1,
)

# Optional: helper to design prompts with AI
st.sidebar.markdown("---")
use_prompt_assistant = st.sidebar.checkbox("✨ Use AI to help design a custom prompt")


# ---------------------------------------------------------
# Step 1 – File Upload
# ---------------------------------------------------------
st.header("1️⃣ Upload Your File")
uploaded_file = st.file_uploader("Upload an Excel or CSV file", type=["xlsx", "csv"])

if not uploaded_file:
    st.stop()

# Load file
if uploaded_file.name.endswith(".csv"):
    df = pd.read_csv(uploaded_file)
else:
    df = pd.read_excel(uploaded_file)

st.write("### Preview of Uploaded Data")
st.dataframe(df.head())

if df.empty:
    st.warning("The uploaded file appears to be empty.")
    st.stop()

# ---------------------------------------------------------
# Step 2 – Column Selection (Flexible, not hard-coded)
# ---------------------------------------------------------
st.header("2️⃣ Configure Columns")

suggested_text = suggest_text_column(df)
text_col = st.selectbox(
    "Text column to send to the LLM (e.g., Zoom Summary, issue description)",
    options=df.columns,
    index=(list(df.columns).index(suggested_text) if suggested_text in df.columns else 0),
)

suggested_date = suggest_date_column(df)
date_col_options = ["<none>"] + list(df.columns)
default_date_index = 0
if suggested_date and suggested_date in df.columns:
    default_date_index = date_col_options.index(suggested_date)

call_date_col = st.selectbox(
    "Call date/time column (for ownership calculations & relative date hints)",
    options=date_col_options,
    index=default_date_index,
)
if call_date_col == "<none>":
    call_date_col = None

st.caption(
    "The app will use the selected text column for all prompts. "
    "If you choose a call date column, it will be used to interpret relative purchase dates "
    "and compute ownership period (days between purchase and call)."
)

# ---------------------------------------------------------
# Step 3 – Filters (with pre-populated values)
# ---------------------------------------------------------
st.header("3️⃣ Filter Rows (Optional)")

filtered_df = df.copy()

# Quick filter: Disposition Sub Group = 'Defective Product'
quick_filter_defective = st.checkbox("Quick Filter: Disposition Sub Group = 'Defective Product'")
if quick_filter_defective and "Disposition Sub Group" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Disposition Sub Group"] == "Defective Product"]

# Quick filter: Troubleshooting Result multi-select
troubleshooting_filter_enabled = st.checkbox("Quick Filter: Troubleshooting Result (multi-select values)")
if troubleshooting_filter_enabled and "Troubleshooting Result" in filtered_df.columns:
    all_tr_values = (
        filtered_df["Troubleshooting Result"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    all_tr_values = sorted(all_tr_values)
    troubleshooting_selected_values = st.multiselect(
        "Select Troubleshooting Result values to keep",
        options=all_tr_values,
        default=all_tr_values,  # behaves like 'not blank' unless user narrows
    )
    filtered_df = filtered_df[
        filtered_df["Troubleshooting Result"].astype(str).isin(troubleshooting_selected_values)
    ]

st.write("Custom filters (exact match across selected values):")
filter_cols = st.multiselect("Select additional columns to filter on", options=df.columns)

selected_filter_values = {}
for col in filter_cols:
    unique_vals = (
        filtered_df[col]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    unique_vals = sorted(unique_vals)
    chosen_vals = st.multiselect(
        f"Values to keep for '{col}'",
        options=unique_vals,
        key=f"filter_vals_{col}",
    )
    if chosen_vals:
        selected_filter_values[col] = chosen_vals

for col, vals in selected_filter_values.items():
    filtered_df = filtered_df[filtered_df[col].astype(str).isin(vals)]

st.write("### Filtered Result Preview")
st.dataframe(filtered_df.head())
st.caption(f"Rows selected for processing: {len(filtered_df)} / {len(df)}")

if filtered_df.empty:
    st.warning("No rows match the selected filters. Adjust filters or upload a different file.")
    st.stop()

# ---------------------------------------------------------
# Step 4 – Preset Tasks (user-selectable prompts)
# ---------------------------------------------------------
st.header("4️⃣ Choose Tasks / Prompts")

st.subheader("Preset LLM Tasks (Recommended)")

# Default preset prompt texts (user can override in UI)
default_purchase_prompt = (
    "You are given:\n"
    "- TEXT: A transcript snippet or summary of a customer support call.\n"
    "- CALL_DATE: The date/time of the call from the selected call date column (may be blank).\n"
    "- INFERRED_RELATIVE_DATE_HINT: An optional approximate YYYY-MM-DD value derived from phrases "
    "like 'last September', based on CALL_DATE.\n\n"
    "Task: Extract the date the customer purchased the product.\n"
    "Rules:\n"
    "- If the text clearly mentions a purchase date, return it in YYYY-MM-DD format.\n"
    "- If the text only contains a relative month phrase like 'last September', and you have "
    "CALL_DATE and INFERRED_RELATIVE_DATE_HINT, you may use the hint as the purchase date.\n"
    "- If there is no clear purchase date, use 'unknown'.\n\n"
    "Return ONLY a JSON object that matches the given schema."
)

default_symptom_prompt = (
    "You are given TEXT, a summary or transcript snippet of a customer support call.\n\n"
    "Task: Identify the primary technical/functional symptom driving the call.\n"
    "Return a concise phrase like 'no power', 'weak airflow', 'overheating', "
    "'burning smell', 'not heating', 'unit shuts off', 'error code' etc.\n"
    "If the complaint is unclear, choose the best available short description.\n\n"
    "Return ONLY a JSON object that matches the given schema."
)

# System prompts
purchase_task_system = (
    "You are an expert at extracting structured information from noisy customer support text."
)
symptom_task_system = (
    "You are an expert quality engineer classifying customer calls by technical symptom."
)

# User explicitly selects which presets to use
use_purchase_preset = st.checkbox(
    "📅 Use preset: Extract purchase date & ownership period "
    "(→ columns: purchase_date, ownership_period_days)"
)

purchase_task_template = None
if use_purchase_preset:
    purchase_task_template = st.text_area(
        "Purchase date preset prompt (you can edit this):",
        value=default_purchase_prompt,
        height=230,
        key="purchase_preset_prompt",
    )

use_symptom_preset = st.checkbox(
    "🩺 Use preset: Extract primary technical symptom "
    "(→ column: symptom)"
)

symptom_task_template = None
if use_symptom_preset:
    symptom_task_template = st.text_area(
        "Symptom preset prompt (you can edit this):",
        value=default_symptom_prompt,
        height=200,
        key="symptom_preset_prompt",
    )

# Structured JSON schemas
purchase_schema = {
    "type": "object",
    "properties": {
        "purchase_date": {
            "type": "string",
            "description": (
                "Date the customer purchased the product. "
                "Use YYYY-MM-DD format when possible. If unknown, use 'unknown'."
            ),
        }
    },
    "required": ["purchase_date"],
    "additionalProperties": False,
}

symptom_schema = {
    "type": "object",
    "properties": {
        "symptom": {
            "type": "string",
            "description": (
                "Primary technical/functional symptom driving the call. "
                "Examples: 'no power', 'weak airflow', 'overheating', 'smells bad', "
                "'unit shuts off', 'error code on display'."
            ),
        }
    },
    "required": ["symptom"],
    "additionalProperties": False,
}

# ---------------------------------------------------------
# Step 5 – Custom Prompts (with examples)
# ---------------------------------------------------------
st.subheader("Custom LLM Tasks")

custom_prompts = []
custom_output_cols = []

use_custom = st.checkbox("➕ Enable custom prompts")

# Example prompt suggestions
with st.expander("📌 Example custom prompts"):
    st.markdown(
        """
- **Failure Mode Classification**  
  `Classify the main failure mode into one of: airflow, power, overheating, noise, cosmetic, other. Return only the label.`

- **Resolution Status**  
  `Determine whether the issue was resolved during this call. Return one of: resolved, unresolved, unclear.`

- **Filter Maintenance Behavior**  
  `Identify whether the customer mentions cleaning or replacing the filter and summarize in one short sentence.`

- **Customer Sentiment**  
  `Classify the customer's sentiment as one of: calm, neutral, frustrated, angry. Return only the label.`
        """
    )

if use_custom:
    num_custom = st.number_input(
        "How many custom prompts?",
        min_value=1,
        max_value=10,
        value=1,
        step=1,
    )
    for i in range(num_custom):
        st.markdown(f"**Custom Task {i+1}**")
        col1, col2 = st.columns(2)
        with col1:
            p = st.text_area(f"Prompt {i+1}", key=f"prompt_{i}")
        with col2:
            o = st.text_input(f"Output column name {i+1}", key=f"outcol_{i}")
        if p.strip() and o.strip():
            custom_prompts.append(p.strip())
            custom_output_cols.append(o.strip())

# Optional: AI helper to design a custom prompt
if use_prompt_assistant and text_col:
    st.markdown("#### ✨ Let AI Suggest a Prompt")
    helper_desc = st.text_area(
        "Describe what you want the model to extract or classify from the text column:",
        placeholder="Example: I want to classify whether the issue is related to airflow, power, or noise...",
    )
    if st.button("Generate Prompt Suggestion with AI"):
        if not api_key:
            st.error("Enter your API key (via secrets or override) to use the prompt assistant.")
        else:
            client = get_client(api_key)
            examples = (
                filtered_df[text_col]
                .dropna()
                .astype(str)
                .head(5)
                .tolist()
            )
            example_block = "\n\n".join(f"- {e}" for e in examples)
            system_msg = (
                "You are an expert at designing high-quality prompts for large language models "
                "to analyze customer support text row-by-row in a spreadsheet."
            )
            user_msg = (
                f"The analyst wants to achieve the following:\n{helper_desc}\n\n"
                f"Here are some example texts from the selected column '{text_col}':\n"
                f"{example_block}\n\n"
                "Write a SINGLE clear prompt they can reuse for each row. Do not include examples, "
                "just the prompt text itself."
            )
            try:
                suggestion = call_llm_free_text(client, model_choice, system_msg, user_msg)
                st.success("Suggested prompt:")
                st.code(suggestion)
            except Exception as e:
                st.error(f"Error generating prompt suggestion: {e}")

# ---------------------------------------------------------
# Step 6 – Run Processing
# ---------------------------------------------------------
st.header("5️⃣ Run LLM Processing")

if st.button("🚀 Run on filtered rows"):
    if not api_key:
        st.error("No API key available. Add OPENAI_API_KEY to secrets or provide an override.")
    else:
        tasks_selected = any([use_purchase_preset, use_symptom_preset, use_custom])
        if not tasks_selected:
            st.error("Select at least one preset task or add a custom prompt.")
        else:
            client = get_client(api_key)
            results_df = df.copy()

            total_rows = len(filtered_df)
            progress = st.progress(0.0)
            status = st.empty()

            for i, (idx, row) in enumerate(filtered_df.iterrows(), start=1):
                text_val = str(row.get(text_col, ""))
                call_date_val = str(row.get(call_date_col, "")) if call_date_col else ""
                inferred_hint = infer_year_from_relative(text_val, call_date_val) if call_date_col else None

                # 1) Purchase date preset (structured JSON)
                if use_purchase_preset and purchase_task_template:
                    user_prompt = (
                        purchase_task_template
                        + f"\n\nTEXT:\n{text_val}\n\n"
                        + f"CALL_DATE: {call_date_val}\n"
                        + f"INFERRED_RELATIVE_DATE_HINT: {inferred_hint}"
                    )

                    try:
                        data = call_llm_json(
                            client,
                            model_choice,
                            purchase_task_system,
                            user_prompt,
                            schema_name="purchase_date_extraction",
                            schema=purchase_schema,
                        )
                        purchase_date_str = None
                        if isinstance(data, dict):
                            purchase_date_str = data.get("purchase_date") or data.get("_raw")
                        results_df.loc[idx, "purchase_date"] = purchase_date_str
                    except Exception as e:
                        results_df.loc[idx, "purchase_date"] = f"ERROR: {e}"

                # 2) Symptom preset (structured JSON)
                if use_symptom_preset and symptom_task_template:
                    user_prompt = symptom_task_template + f"\n\nTEXT:\n{text_val}\n"
                    try:
                        data = call_llm_json(
                            client,
                            model_choice,
                            symptom_task_system,
                            user_prompt,
                            schema_name="symptom_extraction",
                            schema=symptom_schema,
                        )
                        symptom_val = None
                        if isinstance(data, dict):
                            symptom_val = data.get("symptom") or data.get("_raw")
                        results_df.loc[idx, "symptom"] = symptom_val
                    except Exception as e:
                        results_df.loc[idx, "symptom"] = f"ERROR: {e}"

                # 3) Custom free-text prompts
                for p, out_col in zip(custom_prompts, custom_output_cols):
                    full_prompt = (
                        f"TEXT: {text_val}\n"
                        f"CALL_DATE: {call_date_val}\n\n"
                        f"TASK: {p}"
                    )
                    try:
                        out = call_llm_free_text(
                            client,
                            model_choice,
                            system_prompt="You are a helpful assistant analyzing customer support data.",
                            user_prompt=full_prompt,
                        )
                    except Exception as e:
                        out = f"ERROR: {e}"
                    results_df.loc[idx, out_col] = out

                progress.progress(i / total_rows)
                status.write(f"Processed {i} / {total_rows} rows...")

            # After loop: compute ownership period in days if we have purchase_date + call_date_col
            if use_purchase_preset and call_date_col:
                if "purchase_date" in results_df.columns and call_date_col in results_df.columns:
                    try:
                        purchase_dates = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                        call_dates = pd.to_datetime(results_df[call_date_col], errors="coerce")
                        ownership_days = (call_dates - purchase_dates).dt.days
                        results_df["ownership_period_days"] = ownership_days
                    except Exception as e:
                        st.warning(f"Could not compute ownership_period_days: {e}")
                else:
                    st.warning(
                        "purchase_date or call date column missing from results, so ownership_period_days "
                        "could not be computed."
                    )

            st.success("LLM processing complete. Preview, analyze, and download below.")

            # ---------------------------------------------------------
            # Step 7 – Quick Analysis of New Data
            # ---------------------------------------------------------
            st.header("6️⃣ Quick Analysis of New Data")

            st.subheader("Preview of Enriched Dataset")
            st.dataframe(results_df.head())

            # Ownership period analysis
            if "ownership_period_days" in results_df.columns:
                st.subheader("Ownership Period (Days)")
                ownership = results_df["ownership_period_days"].dropna()
                if not ownership.empty:
                    st.write("Summary statistics:")
                    st.write(ownership.describe())

                    # Bucket ownership into bands
                    bins = [-1, 30, 90, 180, 365, 730, 3650]
                    labels = ["0–30", "31–90", "91–180", "181–365", "366–730", ">730"]
                    binned = pd.cut(ownership, bins=bins, labels=labels)
                    bucket_counts = binned.value_counts().sort_index()
                    st.write("Ownership bands (days):")
                    st.bar_chart(bucket_counts)
                else:
                    st.info("No valid ownership_period_days values to analyze.")

            # Symptom distribution
            if "symptom" in results_df.columns:
                st.subheader("Symptom Distribution")
                symptom_series = results_df["symptom"].dropna().astype(str).str.strip()
                symptom_series = symptom_series[symptom_series != ""]
                if not symptom_series.empty:
                    top_symptoms = symptom_series.value_counts().head(15)
                    st.write("Top 15 symptoms:")
                    st.bar_chart(top_symptoms)
                    st.table(top_symptoms.to_frame("count"))
                else:
                    st.info("No non-empty symptom values to analyze.")

            # Purchase date by year
            if "purchase_date" in results_df.columns:
                st.subheader("Purchases by Year (from extracted purchase_date)")
                purchase_dt = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                purchase_dt = purchase_dt.dropna()
                if not purchase_dt.empty:
                    by_year = purchase_dt.dt.year.value_counts().sort_index()
                    st.bar_chart(by_year)
                else:
                    st.info("No valid purchase_date values to analyze.")

            # Call volume by call date if available
            if call_date_col and call_date_col in results_df.columns:
                st.subheader(f"Calls Over Time (based on '{call_date_col}')")
                call_dt = pd.to_datetime(results_df[call_date_col], errors="coerce")
                call_dt = call_dt.dropna()
                if not call_dt.empty:
                    by_month = call_dt.dt.to_period("M").value_counts().sort_index()
                    by_month.index = by_month.index.astype(str)
                    st.line_chart(by_month)
                else:
                    st.info("No valid call date values to analyze.")

            # ---------------------------------------------------------
            # Download Output
            # ---------------------------------------------------------
            buffer = BytesIO()
            results_df.to_excel(buffer, index=False)
            buffer.seek(0)

            st.download_button(
                "⬇️ Download processed file",
                data=buffer,
                file_name="processed_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
ame(df.head())

if df.empty:
    st.warning("The uploaded file appears to be empty.")
    st.stop()

# ---------------------------------------------------------
# Step 2 – Column Selection (Flexible, not hard-coded)
# ---------------------------------------------------------
st.header("2️⃣ Configure Columns")

suggested_text = suggest_text_column(df)
text_col = st.selectbox(
    "Text column to send to the LLM (e.g., Zoom Summary, issue description)",
    options=df.columns,
    index=(list(df.columns).index(suggested_text) if suggested_text in df.columns else 0),
)

suggested_date = suggest_date_column(df)
date_col_options = ["<none>"] + list(df.columns)
default_date_index = 0
if suggested_date and suggested_date in df.columns:
    default_date_index = date_col_options.index(suggested_date)

call_date_col = st.selectbox(
    "Call date/time column (for ownership calculations & relative date hints)",
    options=date_col_options,
    index=default_date_index,
)
if call_date_col == "<none>":
    call_date_col = None

st.caption(
    "The app will use the selected text column for all prompts. "
    "If you choose a call date column, it will be used to interpret relative purchase dates "
    "and compute ownership period (days between purchase and call)."
)

# ---------------------------------------------------------
# Step 3 – Filters (with pre-populated values)
# ---------------------------------------------------------
st.header("3️⃣ Filter Rows (Optional)")

filtered_df = df.copy()

# Quick filter: Disposition Sub Group = 'Defective Product'
quick_filter_defective = st.checkbox("Quick Filter: Disposition Sub Group = 'Defective Product'")
if quick_filter_defective and "Disposition Sub Group" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Disposition Sub Group"] == "Defective Product"]

# Quick filter: Troubleshooting Result multi-select
troubleshooting_filter_enabled = st.checkbox("Quick Filter: Troubleshooting Result (multi-select values)")
if troubleshooting_filter_enabled and "Troubleshooting Result" in filtered_df.columns:
    all_tr_values = (
        filtered_df["Troubleshooting Result"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    all_tr_values = sorted(all_tr_values)
    troubleshooting_selected_values = st.multiselect(
        "Select Troubleshooting Result values to keep",
        options=all_tr_values,
        default=all_tr_values,  # behaves like 'not blank' unless user narrows
    )
    filtered_df = filtered_df[
        filtered_df["Troubleshooting Result"].astype(str).isin(troubleshooting_selected_values)
    ]

st.write("Custom filters (exact match across selected values):")
filter_cols = st.multiselect("Select additional columns to filter on", options=df.columns)

selected_filter_values = {}
for col in filter_cols:
    unique_vals = (
        filtered_df[col]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    unique_vals = sorted(unique_vals)
    chosen_vals = st.multiselect(
        f"Values to keep for '{col}'",
        options=unique_vals,
        key=f"filter_vals_{col}",
    )
    if chosen_vals:
        selected_filter_values[col] = chosen_vals

for col, vals in selected_filter_values.items():
    filtered_df = filtered_df[filtered_df[col].astype(str).isin(vals)]

st.write("### Filtered Result Preview")
st.dataframe(filtered_df.head())
st.caption(f"Rows selected for processing: {len(filtered_df)} / {len(df)}")

if filtered_df.empty:
    st.warning("No rows match the selected filters. Adjust filters or upload a different file.")
    st.stop()

# ---------------------------------------------------------
# Step 4 – Preset Tasks (user-selectable prompts)
# ---------------------------------------------------------
st.header("4️⃣ Choose Tasks / Prompts")

st.subheader("Preset LLM Tasks (Recommended)")

# Default preset prompt texts (user can override in UI)
default_purchase_prompt = (
    "You are given:\n"
    "- TEXT: A transcript snippet or summary of a customer support call.\n"
    "- CALL_DATE: The date/time of the call from the selected call date column (may be blank).\n"
    "- INFERRED_RELATIVE_DATE_HINT: An optional approximate YYYY-MM-DD value derived from phrases "
    "like 'last September', based on CALL_DATE.\n\n"
    "Task: Extract the date the customer purchased the product.\n"
    "Rules:\n"
    "- If the text clearly mentions a purchase date, return it in YYYY-MM-DD format.\n"
    "- If the text only contains a relative month phrase like 'last September', and you have "
    "CALL_DATE and INFERRED_RELATIVE_DATE_HINT, you may use the hint as the purchase date.\n"
    "- If there is no clear purchase date, use 'unknown'.\n\n"
    "Return ONLY a JSON object that matches the given schema."
)

default_symptom_prompt = (
    "You are given TEXT, a summary or transcript snippet of a customer support call.\n\n"
    "Task: Identify the primary technical/functional symptom driving the call.\n"
    "Return a concise phrase like 'no power', 'weak airflow', 'overheating', "
    "'burning smell', 'not heating', 'unit shuts off', 'error code' etc.\n"
    "If the complaint is unclear, choose the best available short description.\n\n"
    "Return ONLY a JSON object that matches the given schema."
)

# System prompts
purchase_task_system = (
    "You are an expert at extracting structured information from noisy customer support text."
)
symptom_task_system = (
    "You are an expert quality engineer classifying customer calls by technical symptom."
)

# User explicitly selects which presets to use
use_purchase_preset = st.checkbox(
    "📅 Use preset: Extract purchase date & ownership period "
    "(→ columns: purchase_date, ownership_period_days)"
)

purchase_task_template = None
if use_purchase_preset:
    purchase_task_template = st.text_area(
        "Purchase date preset prompt (you can edit this):",
        value=default_purchase_prompt,
        height=230,
        key="purchase_preset_prompt",
    )

use_symptom_preset = st.checkbox(
    "🩺 Use preset: Extract primary technical symptom "
    "(→ column: symptom)"
)

symptom_task_template = None
if use_symptom_preset:
    symptom_task_template = st.text_area(
        "Symptom preset prompt (you can edit this):",
        value=default_symptom_prompt,
        height=200,
        key="symptom_preset_prompt",
    )

# Structured JSON schemas
purchase_schema = {
    "type": "object",
    "properties": {
        "purchase_date": {
            "type": "string",
            "description": (
                "Date the customer purchased the product. "
                "Use YYYY-MM-DD format when possible. If unknown, use 'unknown'."
            ),
        }
    },
    "required": ["purchase_date"],
    "additionalProperties": False,
}

symptom_schema = {
    "type": "object",
    "properties": {
        "symptom": {
            "type": "string",
            "description": (
                "Primary technical/functional symptom driving the call. "
                "Examples: 'no power', 'weak airflow', 'overheating', 'smells bad', "
                "'unit shuts off', 'error code on display'."
            ),
        }
    },
    "required": ["symptom"],
    "additionalProperties": False,
}

# ---------------------------------------------------------
# Step 5 – Custom Prompts (with examples)
# ---------------------------------------------------------
st.subheader("Custom LLM Tasks")

custom_prompts = []
custom_output_cols = []

use_custom = st.checkbox("➕ Enable custom prompts")

# Example prompt suggestions
with st.expander("📌 Example custom prompts"):
    st.markdown(
        """
- **Failure Mode Classification**  
  `Classify the main failure mode into one of: airflow, power, overheating, noise, cosmetic, other. Return only the label.`

- **Resolution Status**  
  `Determine whether the issue was resolved during this call. Return one of: resolved, unresolved, unclear.`

- **Filter Maintenance Behavior**  
  `Identify whether the customer mentions cleaning or replacing the filter and summarize in one short sentence.`

- **Customer Sentiment**  
  `Classify the customer's sentiment as one of: calm, neutral, frustrated, angry. Return only the label.`
        """
    )

if use_custom:
    num_custom = st.number_input(
        "How many custom prompts?",
        min_value=1,
        max_value=10,
        value=1,
        step=1,
    )
    for i in range(num_custom):
        st.markdown(f"**Custom Task {i+1}**")
        col1, col2 = st.columns(2)
        with col1:
            p = st.text_area(f"Prompt {i+1}", key=f"prompt_{i}")
        with col2:
            o = st.text_input(f"Output column name {i+1}", key=f"outcol_{i}")
        if p.strip() and o.strip():
            custom_prompts.append(p.strip())
            custom_output_cols.append(o.strip())

# Optional: AI helper to design a custom prompt
if use_prompt_assistant and text_col:
    st.markdown("#### ✨ Let AI Suggest a Prompt")
    helper_desc = st.text_area(
        "Describe what you want the model to extract or classify from the text column:",
        placeholder="Example: I want to classify whether the issue is related to airflow, power, or noise...",
    )
    if st.button("Generate Prompt Suggestion with AI"):
        if not api_key:
            st.error("Enter your API key in the sidebar to use the prompt assistant.")
        else:
            client = get_client(api_key)
            examples = (
                filtered_df[text_col]
                .dropna()
                .astype(str)
                .head(5)
                .tolist()
            )
            example_block = "\n\n".join(f"- {e}" for e in examples)
            system_msg = (
                "You are an expert at designing high-quality prompts for large language models "
                "to analyze customer support text row-by-row in a spreadsheet."
            )
            user_msg = (
                f"The analyst wants to achieve the following:\n{helper_desc}\n\n"
                f"Here are some example texts from the selected column '{text_col}':\n"
                f"{example_block}\n\n"
                "Write a SINGLE clear prompt they can reuse for each row. Do not include examples, "
                "just the prompt text itself."
            )
            try:
                suggestion = call_llm_free_text(client, model_choice, system_msg, user_msg)
                st.success("Suggested prompt:")
                st.code(suggestion)
            except Exception as e:
                st.error(f"Error generating prompt suggestion: {e}")

# ---------------------------------------------------------
# Step 6 – Run Processing
# ---------------------------------------------------------
st.header("5️⃣ Run LLM Processing")

if st.button("🚀 Run on filtered rows"):
    if not api_key:
        st.error("Please enter your OpenAI API key in the sidebar.")
    else:
        tasks_selected = any([use_purchase_preset, use_symptom_preset, use_custom])
        if not tasks_selected:
            st.error("Select at least one preset task or add a custom prompt.")
        else:
            client = get_client(api_key)
            results_df = df.copy()

            total_rows = len(filtered_df)
            progress = st.progress(0.0)
            status = st.empty()

            for i, (idx, row) in enumerate(filtered_df.iterrows(), start=1):
                text_val = str(row.get(text_col, ""))
                call_date_val = str(row.get(call_date_col, "")) if call_date_col else ""
                inferred_hint = infer_year_from_relative(text_val, call_date_val) if call_date_col else None

                # 1) Purchase date preset (structured JSON)
                if use_purchase_preset and purchase_task_template:
                    user_prompt = (
                        purchase_task_template
                        + f"\n\nTEXT:\n{text_val}\n\n"
                        + f"CALL_DATE: {call_date_val}\n"
                        + f"INFERRED_RELATIVE_DATE_HINT: {inferred_hint}"
                    )

                    try:
                        data = call_llm_json(
                            client,
                            model_choice,
                            purchase_task_system,
                            user_prompt,
                            schema_name="purchase_date_extraction",
                            schema=purchase_schema,
                        )
                        purchase_date_str = None
                        if isinstance(data, dict):
                            purchase_date_str = data.get("purchase_date") or data.get("_raw")
                        results_df.loc[idx, "purchase_date"] = purchase_date_str
                    except Exception as e:
                        results_df.loc[idx, "purchase_date"] = f"ERROR: {e}"

                # 2) Symptom preset (structured JSON)
                if use_symptom_preset and symptom_task_template:
                    user_prompt = symptom_task_template + f"\n\nTEXT:\n{text_val}\n"
                    try:
                        data = call_llm_json(
                            client,
                            model_choice,
                            symptom_task_system,
                            user_prompt,
                            schema_name="symptom_extraction",
                            schema=symptom_schema,
                        )
                        symptom_val = None
                        if isinstance(data, dict):
                            symptom_val = data.get("symptom") or data.get("_raw")
                        results_df.loc[idx, "symptom"] = symptom_val
                    except Exception as e:
                        results_df.loc[idx, "symptom"] = f"ERROR: {e}"

                # 3) Custom free-text prompts
                for p, out_col in zip(custom_prompts, custom_output_cols):
                    full_prompt = (
                        f"TEXT: {text_val}\n"
                        f"CALL_DATE: {call_date_val}\n\n"
                        f"TASK: {p}"
                    )
                    try:
                        out = call_llm_free_text(
                            client,
                            model_choice,
                            system_prompt="You are a helpful assistant analyzing customer support data.",
                            user_prompt=full_prompt,
                        )
                    except Exception as e:
                        out = f"ERROR: {e}"
                    results_df.loc[idx, out_col] = out

                progress.progress(i / total_rows)
                status.write(f"Processed {i} / {total_rows} rows...")

            # After loop: compute ownership period in days if we have purchase_date + call_date_col
            if use_purchase_preset and call_date_col:
                if "purchase_date" in results_df.columns and call_date_col in results_df.columns:
                    try:
                        purchase_dates = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                        call_dates = pd.to_datetime(results_df[call_date_col], errors="coerce")
                        ownership_days = (call_dates - purchase_dates).dt.days
                        results_df["ownership_period_days"] = ownership_days
                    except Exception as e:
                        st.warning(f"Could not compute ownership_period_days: {e}")
                else:
                    st.warning(
                        "purchase_date or call date column missing from results, so ownership_period_days "
                        "could not be computed."
                    )

            st.success("LLM processing complete. Preview, analyze, and download below.")

            # ---------------------------------------------------------
            # Step 7 – Quick Analysis of New Data
            # ---------------------------------------------------------
            st.header("6️⃣ Quick Analysis of New Data")

            st.subheader("Preview of Enriched Dataset")
            st.dataframe(results_df.head())

            # Ownership period analysis
            if "ownership_period_days" in results_df.columns:
                st.subheader("Ownership Period (Days)")
                ownership = results_df["ownership_period_days"].dropna()
                if not ownership.empty:
                    st.write("Summary statistics:")
                    st.write(ownership.describe())

                    # Bucket ownership into bands
                    bins = [-1, 30, 90, 180, 365, 730, 3650]
                    labels = ["0–30", "31–90", "91–180", "181–365", "366–730", ">730"]
                    binned = pd.cut(ownership, bins=bins, labels=labels)
                    bucket_counts = binned.value_counts().sort_index()
                    st.write("Ownership bands (days):")
                    st.bar_chart(bucket_counts)
                else:
                    st.info("No valid ownership_period_days values to analyze.")

            # Symptom distribution
            if "symptom" in results_df.columns:
                st.subheader("Symptom Distribution")
                symptom_series = results_df["symptom"].dropna().astype(str).str.strip()
                symptom_series = symptom_series[symptom_series != ""]
                if not symptom_series.empty:
                    top_symptoms = symptom_series.value_counts().head(15)
                    st.write("Top 15 symptoms:")
                    st.bar_chart(top_symptoms)
                    st.table(top_symptoms.to_frame("count"))
                else:
                    st.info("No non-empty symptom values to analyze.")

            # Purchase date by year
            if "purchase_date" in results_df.columns:
                st.subheader("Purchases by Year (from extracted purchase_date)")
                purchase_dt = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                purchase_dt = purchase_dt.dropna()
                if not purchase_dt.empty:
                    by_year = purchase_dt.dt.year.value_counts().sort_index()
                    st.bar_chart(by_year)
                else:
                    st.info("No valid purchase_date values to analyze.")

            # Call volume by call date if available
            if call_date_col and call_date_col in results_df.columns:
                st.subheader(f"Calls Over Time (based on '{call_date_col}')")
                call_dt = pd.to_datetime(results_df[call_date_col], errors="coerce")
                call_dt = call_dt.dropna()
                if not call_dt.empty:
                    by_month = call_dt.dt.to_period("M").value_counts().sort_index()
                    by_month.index = by_month.index.astype(str)
                    st.line_chart(by_month)
                else:
                    st.info("No valid call date values to analyze.")

            # ---------------------------------------------------------
            # Download Output
            # ---------------------------------------------------------
            buffer = BytesIO()
            results_df.to_excel(buffer, index=False)
            buffer.seek(0)

            st.download_button(
                "⬇️ Download processed file",
                data=buffer,
                file_name="processed_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


