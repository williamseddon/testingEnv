import pandas as pd
import numpy as np
import streamlit as st


# ---------------------------
# Helper functions
# ---------------------------

REQUIRED_COLUMNS = [
    "Base Model",
    "Star Rating",
    "Curl Wrap",
    "Curl Inconsistency",
    "Curl Fall Off",
    "Curler Mention Experience",
    "Ownership Period",
]


def check_required_columns(df: pd.DataFrame):
    df.columns = df.columns.str.strip()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "These required columns are missing from your file: "
            + ", ".join(missing)
        )


def normalize_yn_notmentioned(value: str) -> str:
    """
    Normalize Yes/No/Not Mentioned style fields.
    """
    s = str(value).strip()
    lowered = s.lower()

    if lowered == "yes":
        return "Yes"
    if lowered == "no":
        return "No"
    if lowered in ("not mentioned", "notmentionned", "notmentioned"):
        return "Not Mentioned"
    if lowered in ("", "nan"):
        return "Not Mentioned"
    # Fallback: keep original text
    return s


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean columns and add helper columns:
    - Any Curl Issue
    - Curler Experience Bucket
    - Ownership Bucket
    """
    check_required_columns(df)

    # Strip column names again just to be safe
    df = df.copy()
    df.columns = df.columns.str.strip()

    # Clean Y/N style fields
    for col in ["Curl Wrap", "Curl Inconsistency", "Curl Fall Off"]:
        df[col] = df[col].apply(normalize_yn_notmentioned)

    # Normalize Curler Mention Experience
    def map_experience(v):
        s = str(v).strip().lower()
        if s == "positive":
            return "Positive"
        if s == "negative":
            return "Negative"
        if s in ("not mentioned", "", "nan"):
            return "Not Mentioned"
        # Anything else: treat as not mentioned for summary purposes
        return "Not Mentioned"

    df["Curler Mention Experience"] = df["Curler Mention Experience"].apply(
        normalize_yn_notmentioned
    )
    df["Curler Experience Bucket"] = df["Curler Mention Experience"].apply(
        map_experience
    )

    # Any Curl Issue = Yes if any of the 3 curl flags is Yes
    def any_curl_issue(row):
        return (
            "Yes" if any(row[col] == "Yes" for col in
                         ["Curl Wrap", "Curl Inconsistency", "Curl Fall Off"])
            else "No"
        )

    df["Any Curl Issue"] = df.apply(any_curl_issue, axis=1)

    # Ownership bucket
    def bucket_ownership(v):
        if pd.isna(v):
            return "Not Mentioned"
        s = str(v).strip().lower()
        if s in ("", "not mentioned"):
            return "Not Mentioned"

        # Short: clearly sub-month / “use count” phrases
        short_keywords = [
            "day", "days",
            "week", "weeks",
            "hour", "hours",
            "minute", "minutes",
            "first use",
            "once",
            "less than 1 day",
            "less than a day",
            "1 week",
            "2 weeks",
            "3 weeks",
            "4 weeks",
            "since christmas",   # ~1 week-ish examples
        ]
        # Medium: months / ~1 year
        medium_keywords = [
            "month", "months",
            "1 year",
            "12 months",
            "less than a year",
            "within a year",
            "9-12 months",
            "10 months",
        ]
        # Long: clearly multi-year
        long_keywords = [
            "years",
            "over a year",
            "more than a year",
            "2 years",
            "3 years",
            "4 years",
            "5 years",
            "20 years",
            "2.5 years",
            "two and a half years",
        ]

        # Use count phrases – treat as short (light usage so far)
        use_count_keywords = [
            "use", "uses", "used once", "a few times", "a couple of times",
            "handful of times", "many years"  # though this could be long, but they often say it explicitly with "years" above
        ]

        if any(k in s for k in short_keywords):
            return "Short (<1 month)"
        if any(k in s for k in medium_keywords):
            return "Medium (1–12 months)"
        if any(k in s for k in long_keywords):
            return "Long (>1 year)"
        if "year" in s and "less than" not in s:
            # Any remaining "year" reference without "less than" -> long
            return "Long (>1 year)"
        if any(k in s for k in use_count_keywords):
            return "Short (<1 month)"

        # Ambiguous: just call this Not Mentioned for now
        return "Not Mentioned"

    df["Ownership Bucket"] = df["Ownership Period"].apply(bucket_ownership)

    # Make sure Star Rating is numeric
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

    return df


def summarize_by_base_model(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one 'great summary table' with percentages by Base Model
    + an overall 'All Models' row.
    """
    def pct(series, condition):
        s = series.copy()
        total = len(s)
        if total == 0:
            return 0.0
        # condition function: takes series -> boolean index
        mask = condition(s)
        return 100.0 * mask.mean()

    rows = []
    base_models = sorted(df["Base Model"].dropna().unique().tolist())
    group_names = base_models + ["All Models"]

    for model in group_names:
        if model == "All Models":
            grp = df
        else:
            grp = df[df["Base Model"] == model]

        if grp.empty:
            continue

        row = {}
        row["Base Model"] = model
        row["N Reviews"] = len(grp)
        row["Avg Star Rating"] = grp["Star Rating"].mean()

        # Any curl issue
        row["% Any Curl Issue"] = pct(
            grp["Any Curl Issue"], lambda s: s == "Yes"
        )

        # Individual curl flags
        row["% Curl Wrap = Yes"] = pct(
            grp["Curl Wrap"], lambda s: s.astype(str).str.lower() == "yes"
        )
        row["% Curl Inconsistency = Yes"] = pct(
            grp["Curl Inconsistency"], lambda s: s.astype(str).str.lower() == "yes"
        )
        row["% Curl Fall Off = Yes"] = pct(
            grp["Curl Fall Off"], lambda s: s.astype(str).str.lower() == "yes"
        )

        # Curler experience buckets
        row["% Curler Exp = Positive"] = pct(
            grp["Curler Experience Bucket"], lambda s: s == "Positive"
        )
        row["% Curler Exp = Negative"] = pct(
            grp["Curler Experience Bucket"], lambda s: s == "Negative"
        )
        row["% Curler Exp = Not Mentioned"] = pct(
            grp["Curler Experience Bucket"], lambda s: s == "Not Mentioned"
        )

        # Ownership buckets
        row["% Ownership = Short (<1 month)"] = pct(
            grp["Ownership Bucket"], lambda s: s == "Short (<1 month)"
        )
        row["% Ownership = Medium (1–12 months)"] = pct(
            grp["Ownership Bucket"], lambda s: s == "Medium (1–12 months)"
        )
        row["% Ownership = Long (>1 year)"] = pct(
            grp["Ownership Bucket"], lambda s: s == "Long (>1 year)"
        )
        row["% Ownership = Not Mentioned"] = pct(
            grp["Ownership Bucket"], lambda s: s == "Not Mentioned"
        )

        rows.append(row)

    summary_df = pd.DataFrame(rows)

    # Order: All Models first, then each model
    if "All Models" in summary_df["Base Model"].values:
        ordered = ["All Models"] + base_models
        summary_df["Base Model"] = pd.Categorical(
            summary_df["Base Model"], categories=ordered, ordered=True
        )
        summary_df = summary_df.sort_values("Base Model")

    return summary_df.reset_index(drop=True)


# ---------------------------
# Streamlit app
# ---------------------------

st.set_page_config(
    page_title="Curl Performance Summary",
    layout="wide",
)

st.title("Curl Performance Summary")
st.write(
    """
Upload a CSV or Excel file with the following columns:

- **Base Model**
- **Star Rating**
- **Curl Wrap**
- **Curl Inconsistency**
- **Curl Fall Off**
- **Curler Mention Experience**
- **Ownership Period**

The app will:
- Normalize the data
- Add helper columns (Any Curl Issue, Curler Experience Bucket, Ownership Bucket)
- Build a summary table with **exact % mentions per Base Model**
"""
)

uploaded_file = st.file_uploader(
    "Upload CSV or Excel file",
    type=["csv", "xlsx", "xls"],
)

if uploaded_file is not None:
    # Read file
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Error reading file: {e}")
        st.stop()

    st.subheader("Raw Data Preview")
    st.dataframe(df.head())

    # Preprocess
    try:
        df_processed = preprocess(df)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    st.subheader("Processed Data (with helper columns)")
    st.dataframe(df_processed.head())

    # Summary table
    st.subheader("Summary Table by Base Model (Exact % Mentions)")
    summary_df = summarize_by_base_model(df_processed)

    # Formatting
    percent_cols = [c for c in summary_df.columns if c.startswith("%")]
    format_dict = {col: "{:.1f}%" for col in percent_cols}
    format_dict["Avg Star Rating"] = "{:.2f}"

    st.dataframe(summary_df.style.format(format_dict))

    # Download button
    csv_bytes = summary_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download Summary as CSV",
        data=csv_bytes,
        file_name="curl_summary_table.csv",
        mime="text/csv",
    )
else:
    st.info("👆 Upload a file to generate the summary table.")

