import streamlit as st
import pandas as pd

st.set_page_config(page_title="Curl Experience Summary", layout="wide")

# Columns we expect in the processed review export
REQUIRED_COLUMNS = [
    "Base Model",
    "Star Rating",
    "Curl Wrap",
    "Curl Inconsistency",
    "Curl Fall Off",
    "Curler Mention Experience",
    "Ownership Period",
]


def validate_required_columns(df: pd.DataFrame) -> None:
    """Raise a nice error if any required columns are missing."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "These required columns are missing from your file: "
            + ", ".join(missing)
        )


def summarise_by_model(df: pd.DataFrame) -> pd.DataFrame:
    """Build summary with exact percentages for each Base Model."""
    df = df.copy()

    # Make sure rating is numeric
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")
    df = df.dropna(subset=["Star Rating"])

    results = []

    for model, g in df.groupby("Base Model"):
        total = len(g)
        if total == 0:
            continue

        # Rating distribution
        rating_counts = (
            g["Star Rating"]
            .value_counts()
            .reindex([1, 2, 3, 4, 5], fill_value=0)
        )
        rating_pct = rating_counts / total * 100

        def pct(col: str, target: str) -> float:
            s = g[col].astype(str).str.strip().str.lower()
            return float((s == target).mean() * 100)

        row = {
            "Base Model": model,
            "N Reviews": int(total),
            "Avg Rating": float(g["Star Rating"].mean()),
            "% 1★": rating_pct[1],
            "% 2★": rating_pct[2],
            "% 3★": rating_pct[3],
            "% 4★": rating_pct[4],
            "% 5★": rating_pct[5],
            "% Curl Wrap = yes": pct("Curl Wrap", "yes"),
            "% Curl Inconsistency = Yes": pct("Curl Inconsistency", "yes"),
            "% Curl Fall Off = Yes": pct("Curl Fall Off", "yes"),
            "% Curler Exp Positive": pct(
                "Curler Mention Experience", "positive"
            ),
            "% Curler Exp Negative": pct(
                "Curler Mention Experience", "negative"
            ),
            "% Curler Exp Not Mentioned": pct(
                "Curler Mention Experience", "not mentioned"
            ),
        }
        results.append(row)

    if not results:
        return pd.DataFrame()

    summary = pd.DataFrame(results).set_index("Base Model").sort_index()
    return summary


def summarise_overall(df: pd.DataFrame) -> pd.DataFrame:
    """Summarise all rows as a single 'All Models' pseudo-model."""
    tmp = df.copy()
    tmp["Base Model"] = "All Models"
    return summarise_by_model(tmp)


def main():
    st.title("HD600 / HD400 Curl Experience Summary")

    st.markdown(
        "Upload the **processed_reviews_output.xlsx** (or a similar file) "
        "and this app will calculate **exact percentage metrics** by base model."
    )

    uploaded = st.file_uploader(
        "Upload Excel or CSV file", type=["xlsx", "xls", "csv"]
    )

    if not uploaded:
        st.info("👆 Upload a file to get started.")
        return

    suffix = uploaded.name.split(".")[-1].lower()

    try:
        # --- Excel path: handle multiple sheets ---
        if suffix in ("xlsx", "xls"):
            xls = pd.ExcelFile(uploaded)

            # Guess a default sheet that contains all required columns
            default_sheet = xls.sheet_names[0]
            for sheet in xls.sheet_names:
                preview = pd.read_excel(xls, sheet_name=sheet, nrows=10)
                if all(col in preview.columns for col in REQUIRED_COLUMNS):
                    default_sheet = sheet
                    break

            sheet_name = st.selectbox(
                "Select sheet that contains the review rows:",
                xls.sheet_names,
                index=xls.sheet_names.index(default_sheet),
            )

            df = pd.read_excel(xls, sheet_name=sheet_name)

        # --- CSV path ---
        else:
            df = pd.read_csv(uploaded)

        # Make sure all required columns are there
        validate_required_columns(df)

    except ValueError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Problem reading file: {e}")
        return

    # Show some raw data
    st.subheader("Raw Data Preview")
    st.dataframe(df.head(50))

    # Build summaries
    summary_by_model = summarise_by_model(df)
    overall_summary = summarise_overall(df)

    if summary_by_model.empty:
        st.warning("No data to summarise after filtering.")
        return

    percent_cols = [c for c in summary_by_model.columns if c.startswith("%")]

    def format_percentages(summary: pd.DataFrame) -> pd.DataFrame:
        formatted = summary.copy()
        for c in percent_cols:
            formatted[c] = formatted[c].map(
                lambda x: f"{x:.2f}%" if pd.notnull(x) else ""
            )
        formatted["Avg Rating"] = formatted["Avg Rating"].map(
            lambda x: f"{x:.2f}"
        )
        return formatted

    st.subheader("Summary by Base Model")
    st.dataframe(format_percentages(summary_by_model))

    st.subheader("Overall Summary (All Models Combined)")
    st.dataframe(format_percentages(overall_summary))


if __name__ == "__main__":
    main()


