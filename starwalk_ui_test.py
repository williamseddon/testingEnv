import streamlit as st 
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from googletrans import Translator
import io
import asyncio

# ---------------------------------------
# Page config
# ---------------------------------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# Dashboard Title
st.markdown(
    """
    <h1 style="text-align: center;">🌟 Star Walk Analysis Dashboard</h1>
    <p style="text-align: center; font-size: 16px;">
        Dive into insightful metrics, trends, and ratings to make data-driven decisions.
    </p>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------
# Utilities
# ---------------------------------------

def style_rating_cells(value):
    """Styles cells: Green for ratings 4.5 and above, red for below 4.5."""
    if isinstance(value, (float, int)):
        if value >= 4.5:
            return "color: green;"
        elif value < 4.5:
            return "color: red;"
    return ""


def apply_filter(dataframe: pd.DataFrame, column_name: str, filter_name: str):
    options = ["ALL"]
    if column_name in dataframe.columns:
        # Convert to string dtype for clean filtering; keep NA as NA
        col = dataframe[column_name].astype("string")
        options += sorted([x for x in col.dropna().unique().tolist() if str(x).strip() != ""])
    selected_filter = st.sidebar.multiselect(
        f"Select {filter_name}",
        options=options,
        default=["ALL"]
    )
    if "ALL" not in selected_filter and column_name in dataframe.columns:
        return dataframe[dataframe[column_name].astype("string").isin(selected_filter)], selected_filter
    return dataframe, ["ALL"]


def collect_unique_symptoms(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Collect a unique, ordered list of non-empty symptom strings from provided columns that exist."""
    vals = []
    seen = set()
    for c in cols:
        if c in df.columns:
            s = (
                df[c]
                .astype("string")
                .str.strip()
                .dropna()
            )
            for v in pd.unique(s.to_numpy()):
                item = str(v).strip()
                if item and item not in seen:
                    seen.add(item)
                    vals.append(item)
    return vals


def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    """Analyze delighter/detractor symptoms and calculate metrics, robust to empty/missing columns."""
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])

    s = (
        filtered_df[cols]
        .stack(dropna=True)
        .astype("string")
        .str.strip()
        .dropna()
    )
    s = s[s != ""]
    if s.empty:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])

    unique_items = pd.unique(s.to_numpy())
    results = []
    total_rows = len(filtered_df)

    for item in unique_items:
        item_str = str(item).strip()
        if not item_str:
            continue
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0:
            continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean()
        pct = (count / total_rows * 100) if total_rows else 0
        results.append({
            "Item": item_str.title(),
            "Avg Star": round(avg_star, 1) if pd.notna(avg_star) else None,
            "Mentions": count,
            "% Total": f"{round(pct, 1)}%",
        })

    if not results:
        return pd.DataFrame(columns=["Item", "Avg Star", "Mentions", "% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)


def build_wordcloud_text(df: pd.DataFrame, cols: list[str]) -> str:
    """Flatten text from given columns into a single string for wordcloud generation."""
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return ""
    s = (
        df[cols]
        .stack(dropna=True)
        .astype("string")
        .str.strip()
        .dropna()
    )
    s = s[s != ""]
    return " ".join(s.tolist())


def clean_text(x: str) -> str:
    """Fix common mojibake like â€™ and trim whitespace."""
    if x is None:
        return ""
    x = str(x)
    # Specific replacements requested
    x = x.replace("â€™", "'")
    return x.strip()


# ---- Translation helpers (robust to coroutine return) ----
async def _translate_async_call(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if asyncio.iscoroutine(res):
            res = await res
        return getattr(res, "text", text)
    except Exception:
        return text


def safe_translate(translator: Translator, text: str) -> str:
    """Synchronous wrapper that handles both sync and coroutine returns from translator.translate()."""
    try:
        res = translator.translate(text, dest="en")
        # Typical (sync) path
        if hasattr(res, "text"):
            return res.text
        # If coroutine, run it
        if asyncio.iscoroutine(res):
            try:
                return asyncio.run(_translate_async_call(translator, text))
            except RuntimeError:
                # If an event loop is already running, create a new one in a new policy
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    return loop.run_until_complete(_translate_async_call(translator, text))
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
    except Exception:
        pass
    return text


def apply_keyword_filter(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    """Filter rows where the keyword appears in the Review text only (Verbatim)."""
    if not keyword:
        return df

    kw = keyword.strip()
    if kw == "":
        return df

    verb_col = "Verbatim" if "Verbatim" in df.columns else None
    mask = pd.Series([False] * len(df))

    if verb_col:
        verb = df[verb_col].astype("string").fillna("").map(clean_text)
        mask = verb.str.contains(kw, case=False, na=False)

    return df[mask]


# ---------------------------------------
# File Upload
# ---------------------------------------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

if uploaded_file:
    try:
        st.markdown("---")  # Separator line

        # Load Excel
        verbatims = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")

        # Normalize known string columns (keep NA; uppercase; avoid creating literal 'nan')
        string_columns = ["Country", "Source", "Model (SKU)", "Seeded", "New Review"]
        for col in string_columns:
            if col in verbatims.columns:
                verbatims[col] = verbatims[col].astype("string").str.upper()

        # Coerce ONLY truly numeric columns
        numeric_columns = ["Star Rating"]
        for col in numeric_columns:
            if col in verbatims.columns:
                verbatims[col] = pd.to_numeric(verbatims[col], errors="coerce")

        # Make ALL symptom columns string dtype so they behave consistently
        all_symptom_cols = [c for c in verbatims.columns if c.startswith("Symptom")]
        for c in all_symptom_cols:
            verbatims[c] = verbatims[c].astype("string")

        # Clean review text for mojibake (â€™ -> ')
        if "Verbatim" in verbatims.columns:
            verbatims["Verbatim"] = verbatims["Verbatim"].astype("string").map(clean_text)

        # Date parsing
        if "Review Date" in verbatims.columns:
            verbatims["Review Date"] = pd.to_datetime(verbatims["Review Date"], errors="coerce")

        # ---------------------------------------
        # Sidebar Filters
        # ---------------------------------------
        st.sidebar.header("🔍 Filters")

        timeframe = st.sidebar.selectbox(
            "Select Timeframe",
            options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"]
        )
        today = datetime.today()

        start_date, end_date = None, None
        if timeframe == "Custom Range":
            st.sidebar.markdown("#### Select Date Range")
            start_date, end_date = st.sidebar.date_input(
                label="Date Range",
                value=(datetime.today() - timedelta(days=30), datetime.today()),
                min_value=datetime(2000, 1, 1),
                max_value=datetime.today(),
                label_visibility="collapsed"
            )

        if timeframe == "Last Week":
            start_date = today - timedelta(days=7)
            end_date = today
        elif timeframe == "Last Month":
            start_date = today - timedelta(days=30)
            end_date = today
        elif timeframe == "Last Year":
            start_date = today - timedelta(days=365)
            end_date = today

        if start_date and end_date and "Review Date" in verbatims.columns:
            filtered_verbatims = verbatims[
                (verbatims["Review Date"] >= pd.Timestamp(start_date)) &
                (verbatims["Review Date"] <= pd.Timestamp(end_date))
            ].copy()
        else:
            filtered_verbatims = verbatims.copy()

        # Star Rating Filter
        st.sidebar.markdown("### 🌟 Filter by Star Rating")
        selected_ratings = st.sidebar.multiselect(
            "Select Star Ratings",
            options=["All"] + [1, 2, 3, 4, 5],
            default=["All"]
        )
        if "All" not in selected_ratings and "Star Rating" in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[filtered_verbatims["Star Rating"].isin(selected_ratings)]

        # Standard filters
        filtered_verbatims, _ = apply_filter(filtered_verbatims, "Country", "Country")
        filtered_verbatims, _ = apply_filter(filtered_verbatims, "Source", "Source")
        filtered_verbatims, _ = apply_filter(filtered_verbatims, "Model (SKU)", "Model (SKU)")
        filtered_verbatims, _ = apply_filter(filtered_verbatims, "Seeded", "Seeded")
        filtered_verbatims, _ = apply_filter(filtered_verbatims, "New Review", "New Review")

        # ---------------------------------------
        # Define symptom columns
        # ---------------------------------------
        detractor_columns = [f"Symptom {i}" for i in range(1, 11)]
        delighter_columns = [f"Symptom {i}" for i in range(11, 21)]

        expected_detractor_columns = detractor_columns
        expected_delighter_columns = delighter_columns

        existing_detractor_columns = [c for c in expected_detractor_columns if c in filtered_verbatims.columns]
        existing_delighter_columns = [c for c in expected_delighter_columns if c in filtered_verbatims.columns]

        # Build unique symptom lists for filter options
        detractor_symptoms = collect_unique_symptoms(filtered_verbatims, existing_detractor_columns)
        delighter_symptoms = collect_unique_symptoms(filtered_verbatims, existing_delighter_columns)

        # ---------------------------------------
        # Delighter/Detractor Filters
        # ---------------------------------------
        st.sidebar.header("😊 Delighters and 😠 Detractors Filters")

        selected_delighter = st.sidebar.multiselect(
            "Select Delighter Symptoms",
            options=["All"] + sorted(delighter_symptoms),
            default=["All"]
        )

        selected_detractor = st.sidebar.multiselect(
            "Select Detractor Symptoms",
            options=["All"] + sorted(detractor_symptoms),
            default=["All"]
        )

        # Apply Symptom Filters using EXISTING columns only
        if "All" not in selected_delighter and existing_delighter_columns:
            mask = filtered_verbatims[existing_delighter_columns].isin(selected_delighter).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]

        if "All" not in selected_detractor and existing_detractor_columns:
            mask = filtered_verbatims[existing_detractor_columns].isin(selected_detractor).any(axis=1)
            filtered_verbatims = filtered_verbatims[mask]

        # ---------------------------------------
        # NEW 🔎 Keyword Mention Filter (below Delighters/Detractors)
        # ---------------------------------------
        st.sidebar.subheader("🔎 Keyword Mention Filter")
        keyword = st.sidebar.text_input(
            "Keyword to search (in review text)", value="", help="Case-insensitive contains match in the Review text. Cleaned for â€™ → '."
        )
        if keyword:
            filtered_verbatims = apply_keyword_filter(filtered_verbatims, keyword)

        # ---------------------------------------
        # Dynamic Additional Filters (post Symptom 20 by index)
        # ---------------------------------------
        additional_columns = verbatims.columns[20:]  # columns after the 21st (0-based)
        if len(additional_columns) > 0:
            st.sidebar.header("📋 Additional Filters")
            for column in additional_columns:
                if column not in (expected_detractor_columns + expected_delighter_columns):
                    filtered_verbatims, _ = apply_filter(filtered_verbatims, column, column)
        else:
            st.sidebar.info("No additional filters available.")

        st.markdown("---")

        # ---------------------------------------
        # Metrics Summary
        # ---------------------------------------
        st.markdown(
            """
            ### ⭐ Star Rating Metrics
            <p style="text-align: center; font-size: 14px; color: gray;">
                A summary of customer feedback and review distribution.
            </p>
            """,
            unsafe_allow_html=True
        )

        total_reviews = len(filtered_verbatims)
        if total_reviews == 0:
            st.warning("No data available for the selected filters.")
        avg_rating = filtered_verbatims["Star Rating"].mean() if total_reviews else 0.0
        star_counts = filtered_verbatims["Star Rating"].value_counts().sort_index()
        percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
        star_labels = [f"{int(star)} stars" for star in star_counts.index]

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Reviews", f"{total_reviews:,}")
        with col2:
            st.metric("Avg Star Rating", f"{avg_rating:.1f}", delta_color="inverse")

        fig_bar_horizontal = go.Figure(go.Bar(
            x=star_counts.values,
            y=star_labels,
            orientation="h",
            text=[f"{value} reviews ({percentages.get(idx, 0)}%)" for idx, value in zip(star_counts.index, star_counts.values)],
            textposition="auto",
            marker=dict(color=["#FFA07A", "#FA8072", "#FFD700", "#ADFF2F", "#32CD32"]),
            hoverinfo="y+x+text"
        ))

        fig_bar_horizontal.update_layout(
            title="<b>Star Rating Distribution</b>",
            xaxis=dict(title="Number of Reviews", title_font=dict(size=14), tickfont=dict(size=12), showgrid=False),
            yaxis=dict(title="Star Ratings", title_font=dict(size=14), tickfont=dict(size=12), showgrid=False),
            title_font=dict(size=18),
            plot_bgcolor="white",
            template="plotly_white",
            margin=dict(l=50, r=50, t=50, b=50)
        )

        st.plotly_chart(fig_bar_horizontal, use_container_width=True)

        # ---------------------------------------
        # Country-Specific Breakdown
        # ---------------------------------------
        st.markdown("### 🌍 Country-Specific Breakdown")

        if "Country" in filtered_verbatims.columns and "Source" in filtered_verbatims.columns:
            new_review_filtered = filtered_verbatims[
                filtered_verbatims["New Review"].astype("string").str.upper() == "YES"
            ]

            country_source_stats = (
                filtered_verbatims
                .groupby(["Country", "Source"])\
                .agg(Average_Rating=("Star Rating", "mean"), Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            new_review_stats = (
                new_review_filtered
                .groupby(["Country", "Source"])\
                .agg(New_Review_Average=("Star Rating", "mean"), New_Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            country_source_stats = country_source_stats.merge(new_review_stats, on=["Country", "Source"], how="left")

            country_overall = (
                filtered_verbatims
                .groupby("Country")\
                .agg(Average_Rating=("Star Rating", "mean"), Review_Count=("Star Rating", "count"))
                .reset_index()
            )

            overall_new_review_stats = (
                new_review_filtered
                .groupby("Country")\
                .agg(New_Review_Average=("Star Rating", "mean"), New_Review_Count=("Star Rating", "count"))
                .reset_index()
            )
            country_overall = country_overall.merge(overall_new_review_stats, on="Country", how="left")
            country_overall["Source"] = "Overall"

            def color_numeric(val):
                if pd.isna(val):
                    return ""
                try:
                    v = float(val)
                except Exception:
                    return ""
                if v >= 4.5:
                    return "color: green;"
                elif v < 4.5:
                    return "color: red;"
                return ""

            def formatter_rating(v):
                return "-" if pd.isna(v) else f"{v:.1f}"

            def formatter_count(v):
                return "-" if pd.isna(v) else f"{int(v):,}"

            for country in country_overall["Country"].unique():
                st.markdown(f"#### {country}")

                country_data = country_source_stats[country_source_stats["Country"] == country]
                overall_data = country_overall[country_overall["Country"] == country]

                combined_country_data = pd.concat([country_data, overall_data], ignore_index=True)
                combined_country_data["Sort_Order"] = combined_country_data["Source"].apply(lambda x: 1 if x == "Overall" else 0)
                combined_country_data = combined_country_data.sort_values(by="Sort_Order", ascending=True).drop(columns=["Sort_Order"])
                combined_country_data = combined_country_data.drop(columns=["Country"])

                combined_country_data.rename(columns={
                    "Source": "Source",
                    "Average_Rating": "Avg Rating",
                    "Review_Count": "Review Count",
                    "New_Review_Average": "New Review Average",
                    "New_Review_Count": "New Review Count"
                }, inplace=True)

                def bold_overall(row):
                    if row.name == len(combined_country_data) - 1:
                        return ["font-weight: bold;" for _ in row]
                    return ["" for _ in row]

                styled = (
                    combined_country_data.style
                    .format({
                        "Avg Rating": formatter_rating,
                        "Review Count": formatter_count,
                        "New Review Average": formatter_rating,
                        "New Review Count": formatter_count,
                    })
                    .applymap(color_numeric, subset=["Avg Rating", "New Review Average"])  # color only numbers
                    .apply(bold_overall, axis=1)
                    .set_properties(**{"text-align": "center"})
                    .set_table_styles([
                        {"selector": "th", "props": [("text-align", "center")]},
                        {"selector": "td", "props": [("text-align", "center")]},
                    ])
                )

                st.markdown(styled.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.warning("Country or Source data is missing in the uploaded file.")

        # ---------------------------------------
        # Graph Over Time
        # ---------------------------------------
        st.markdown("### 📈 Graph Over Time")

        if "Review Date" not in filtered_verbatims.columns:
            st.error("The 'Review Date' column is missing from the data. Please upload a valid file.")
            st.stop()

        filtered_verbatims["Review Date"] = pd.to_datetime(filtered_verbatims["Review Date"], errors="coerce")

        st.markdown("#### Select Bar Size")
        bar_size = st.selectbox(
            "Choose the aggregation level for review mentions:",
            options=["Daily", "Weekly", "Monthly"]
        )

        if bar_size == "Weekly":
            filtered_verbatims["TimePeriod"] = filtered_verbatims["Review Date"].dt.to_period("W").dt.start_time
        elif bar_size == "Monthly":
            filtered_verbatims["TimePeriod"] = filtered_verbatims["Review Date"].dt.to_period("M").dt.start_time
        else:
            filtered_verbatims["TimePeriod"] = filtered_verbatims["Review Date"].dt.date

        filtered_verbatims = filtered_verbatims.sort_values(by=["Country", "TimePeriod"])

        filtered_verbatims["Cumulative_Total_Reviews"] = filtered_verbatims.groupby("Country")["Star Rating"].cumcount() + 1
        filtered_verbatims["Cumulative_Sum_Rating"] = filtered_verbatims.groupby("Country")["Star Rating"].cumsum()
        filtered_verbatims["Cumulative_Avg_Rating"] = (
            filtered_verbatims["Cumulative_Sum_Rating"] / filtered_verbatims["Cumulative_Total_Reviews"]
        )

        grouped = filtered_verbatims.groupby(["TimePeriod", "Country"]).agg(
            Total_Reviews=("Star Rating", "count"),
            Cumulative_Avg_Rating=("Cumulative_Avg_Rating", "last")
        ).reset_index()

        if grouped.empty:
            st.warning("No data available for the selected filters.")
            st.stop()

        fig = go.Figure()

        region_colors = {
            "UK": "#FF7F50",
            "USA": "#4682B4",
            "Canada": "#32CD32"
        }
        default_color = "#808080"

        for country in grouped["Country"].unique():
            country_data = grouped[grouped["Country"] == country]
            color = region_colors.get(country, default_color)

            fig.add_trace(go.Bar(
                x=country_data["TimePeriod"],
                y=country_data["Total_Reviews"],
                name=f"{country} Reviews ({bar_size})",
                marker=dict(color=color),
                opacity=0.7,
                yaxis="y"
            ))

            fig.add_trace(go.Scatter(
                x=country_data["TimePeriod"],
                y=country_data["Cumulative_Avg_Rating"],
                mode="lines+markers",
                name=f"{country} Cumulative Average Rating",
                line=dict(color=color, width=2),
                yaxis="y2"
            ))

        fig.update_layout(
            title=f"Country-wise Review Mentions and Over-Time Average Ratings ({bar_size})",
            xaxis=dict(title="Time Period", tickformat="%b %d", title_font=dict(size=14)),
            yaxis=dict(title="Review Mentions", title_font=dict(size=14), showgrid=False),
            yaxis2=dict(
                title="Cumulative Star Rating (1-5)",
                overlaying="y",
                side="right",
                range=[1, 5.2],
                title_font=dict(size=14),
                showgrid=False
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.4,
                xanchor="center",
                x=0.5
            ),
            barmode="stack",
            template="plotly_white",
            margin=dict(l=50, r=50, t=70, b=70)
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ---------------------------------------
        # Delighters and Detractors Analysis
        # ---------------------------------------
        st.markdown("### 🌟 Delighters and Detractors Analysis")

        def style_star_ratings(value):
            if isinstance(value, (float, int)):
                if value >= 4.5:
                    return "color: green;"
                elif value < 4.5:
                    return "color: red;"
            return ""

        detractors_results = analyze_delighters_detractors(filtered_verbatims, existing_detractor_columns)
        delighters_results = analyze_delighters_detractors(filtered_verbatims, existing_delighter_columns)

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("All Detractors")
            if detractors_results.empty:
                st.write("No detractor symptoms found.")
            else:
                st.dataframe(
                    detractors_results.style.applymap(style_star_ratings, subset=["Avg Star"]).\
                    format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"}),
                    use_container_width=True
                )

        with col2:
            st.subheader("All Delighters")
            if delighters_results.empty:
                st.write("No delighter symptoms found.")
            else:
                st.dataframe(
                    delighters_results.style.applymap(style_star_ratings, subset=["Avg Star"]).\
                    format({"Avg Star": "{:.1f}", "Mentions": "{:.0f}"}),
                    use_container_width=True
                )

        st.markdown("---")

        # ---------------------------------------
        # Reviews (with optional translation) + Download All (filtered)
        # ---------------------------------------
        translator = Translator()
        st.markdown("### 📝 All Reviews")

        # Download all filtered reviews (not just current page)
        if not filtered_verbatims.empty:
            csv_bytes = filtered_verbatims.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="⬇️ Download ALL filtered reviews (CSV)",
                data=csv_bytes,
                file_name="filtered_reviews.csv",
                mime="text/csv",
                help="Exports all rows that match the active filters, regardless of pagination."
            )

        translate_to_english = st.button("Translate All Reviews to English")

        reviews_per_page = 10
        if "review_page" not in st.session_state:
            st.session_state["review_page"] = 0

        def scroll_to_top():
            st.experimental_rerun()

        current_page = st.session_state["review_page"]
        start_index = current_page * reviews_per_page
        end_index = start_index + reviews_per_page
        paginated_reviews = filtered_verbatims.iloc[start_index:end_index]

        if paginated_reviews.empty:
            st.warning("No reviews match the selected criteria.")
        else:
            for _, row in paginated_reviews.iterrows():
                review_text = row.get("Verbatim", pd.NA)
                review_text = "" if pd.isna(review_text) else clean_text(review_text)

                if translate_to_english:
                    translated_review = safe_translate(translator, review_text)
                else:
                    translated_review = review_text

                delighter_badges = [
                    f'<div style="display:inline-block; padding:5px 10px; background-color:lightgreen; color:black; border-radius:5px; margin:5px;">{row[col]}</div>'
                    for col in existing_delighter_columns if col in row and pd.notna(row[col])
                ]
                detractor_badges = [
                    f'<div style="display:inline-block; padding:5px 10px; background-color:lightcoral; color:black; border-radius:5px; margin:5px;">{row[col]}</div>'
                    for col in existing_detractor_columns if col in row and pd.notna(row[col])
                ]

                delighter_message = "<i>No delighter symptoms reported</i>" if not delighter_badges else " ".join(delighter_badges)
                detractor_message = "<i>No detractor symptoms reported</i>" if not detractor_badges else " ".join(detractor_badges)

                star_val = row.get("Star Rating", 0)
                try:
                    star_int = int(star_val) if pd.notna(star_val) else 0
                except Exception:
                    star_int = 0

                st.markdown(
                    f"""
                    <div style=\"border: 1px solid #ddd; padding: 15px; margin-bottom: 10px; border-radius: 5px; background-color: #f9f9f9;\">
                        <p><strong>Source:</strong> {row.get('Source', '')} | <strong>Model:</strong> {row.get('Model (SKU)', '')}</p>
                        <p><strong>Country:</strong> {row.get('Country', '')}</p>
                        <p><strong>Rating:</strong> {'⭐' * star_int} ({row.get('Star Rating', '')}/5)</p>
                        <p><strong>Review:</strong> {translated_review}</p>
                        <div><strong>Delighter Symptoms:</strong> {delighter_message}</div>
                        <div><strong>Detractor Symptoms:</strong> {detractor_message}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if current_page > 0 and st.button("⬅ Go Back", key="go_back"):
                st.session_state["review_page"] -= 1
                scroll_to_top()
        with col2:
            total_pages = (len(filtered_verbatims) + reviews_per_page - 1) // reviews_per_page
            st.markdown(
                f"<div style='text-align: center; font-weight: bold;'>Page {current_page + 1} of {max(total_pages,1)}</div>",
                unsafe_allow_html=True,
            )
        with col3:
            if end_index < len(filtered_verbatims) and st.button("➡ View More", key="view_more"):
                st.session_state["review_page"] += 1
                scroll_to_top()

        st.markdown("---")

        # ---------------------------------------
        # Word Clouds
        # ---------------------------------------
        st.markdown("### 🌟 Word Cloud for Delighters and Detractors")

        detractors_text = build_wordcloud_text(filtered_verbatims, existing_detractor_columns)
        delighters_text = build_wordcloud_text(filtered_verbatims, existing_delighter_columns)

        st.markdown("#### 😠 Detractors")
        if detractors_text:
            wc_det = WordCloud(
                background_color="white",
                colormap="Reds",
                width=1600,
                height=800,
                max_words=100,
                contour_width=3,
                contour_color="red",
                scale=3
            ).generate(detractors_text)
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.imshow(wc_det, interpolation="bilinear")
            ax.axis("off")
            st.pyplot(fig)
        else:
            st.info("Not enough detractor text to build a word cloud.")

        st.markdown("#### 😊 Delighters")
        if delighters_text:
            wc_del = WordCloud(
                background_color="white",
                colormap="Greens",
                width=1600,
                height=800,
                max_words=100,
                contour_width=3,
                contour_color="green",
                scale=3
            ).generate(delighters_text)
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.imshow(wc_del, interpolation="bilinear")
            ax.axis("off")
            st.pyplot(fig)
        else:
            st.info("Not enough delighter text to build a word cloud.")

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")
