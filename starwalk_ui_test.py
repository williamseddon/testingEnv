import streamlit as st 
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from googletrans import Translator
import pandas as pd
import io
        
        
# Set widescreen layout
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

# Functions for styling ratings
def style_rating_cells(value):
    """Styles cells: Green for ratings 4.5 and above, red for below 4.5."""
    if isinstance(value, (float, int)):
        if value >= 4.5:
            return "color: green;"
        elif value < 4.5:
            return "color: red;"
    return ""

# File Upload Section
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

if uploaded_file:
    try:
        st.markdown("---")  # Separator line
        # Load Excel file
        verbatims = pd.read_excel(uploaded_file, sheet_name='Star Walk scrubbed verbatims')

        # Normalize string columns
        string_columns = ['Country', 'Source', 'Model (SKU)', 'Seeded', 'New Review']
        for col in string_columns:
            if col in verbatims.columns:
                verbatims[col] = verbatims[col].astype(str).fillna('').str.upper()

        # Ensure numeric columns are properly converted back to numeric types
        numeric_columns = ['Star Rating', 'Symptom 1', 'Symptom 2', 'Symptom 3', 'Symptom 4', 'Symptom 5']  # Add all numeric column names
        for col in numeric_columns:
            if col in verbatims.columns:
                verbatims[col] = pd.to_numeric(verbatims[col], errors='coerce')

        # Proceed with further processing (e.g., filtering, aggregations, comparisons)
        if 'Review Date' in verbatims.columns:
            verbatims['Review Date'] = pd.to_datetime(verbatims['Review Date'], errors='coerce')

        # Sidebar Filters Section
        st.sidebar.header("🔍 Filters")

        # Add Timeframe Selector
        timeframe = st.sidebar.selectbox(
            "Select Timeframe",
            options=["All Time", "Last Week", "Last Month", "Last Year", "Custom Range"]
        )
        today = datetime.today()

        # Add a date range picker for "Custom Range"
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

        # Time-based filtering
        if timeframe == "Last Week":
            start_date = today - timedelta(days=7)
            end_date = today
        elif timeframe == "Last Month":
            start_date = today - timedelta(days=30)
            end_date = today
        elif timeframe == "Last Year":
            start_date = today - timedelta(days=365)
            end_date = today

        if start_date and end_date and 'Review Date' in verbatims.columns:
            filtered_verbatims = verbatims[
                (verbatims['Review Date'] >= pd.Timestamp(start_date)) &
                (verbatims['Review Date'] <= pd.Timestamp(end_date))
            ]
        else:
            filtered_verbatims = verbatims.copy()

        # Star Rating Filter
        st.sidebar.markdown("### 🌟 Filter by Star Rating")
        selected_ratings = st.sidebar.multiselect(
            "Select Star Ratings",
            options=["All"] + [1, 2, 3, 4, 5],  # Add "All" to the options
            default=["All"]
        )

        # Apply Star Rating Filter
        if "All" not in selected_ratings and 'Star Rating' in filtered_verbatims.columns:
            filtered_verbatims = filtered_verbatims[filtered_verbatims['Star Rating'].isin(selected_ratings)]

        # Apply standard filters
        def apply_filter(dataframe, column_name, filter_name):
            selected_filter = st.sidebar.multiselect(
                f"Select {filter_name}",
                options=["ALL"] + sorted(dataframe[column_name].dropna().unique().tolist()),
                default=["ALL"]
            )
            if "ALL" not in selected_filter:
                return dataframe[dataframe[column_name].isin(selected_filter)], selected_filter
            return dataframe, ["ALL"]

        filtered_verbatims, _ = apply_filter(filtered_verbatims, 'Country', 'Country')
        filtered_verbatims, _ = apply_filter(filtered_verbatims, 'Source', 'Source')
        filtered_verbatims, _ = apply_filter(filtered_verbatims, 'Model (SKU)', 'Model (SKU)')
        filtered_verbatims, _ = apply_filter(filtered_verbatims, 'Seeded', 'Seeded')
        filtered_verbatims, _ = apply_filter(filtered_verbatims, 'New Review', 'New Review')

        # Inventory Delighter and Detractor Symptoms
        delighter_columns = ['Symptom 11', 'Symptom 12', 'Symptom 13', 'Symptom 14', 'Symptom 15',
                            'Symptom 16', 'Symptom 17', 'Symptom 18', 'Symptom 19', 'Symptom 20']
        detractor_columns = ['Symptom 1', 'Symptom 2', 'Symptom 3', 'Symptom 4', 'Symptom 5',
                            'Symptom 6', 'Symptom 7', 'Symptom 8', 'Symptom 9', 'Symptom 10']

        # Extract unique symptoms for each group
        delighter_symptoms = pd.unique(filtered_verbatims[delighter_columns].values.ravel())
        delighter_symptoms = [symptom for symptom in delighter_symptoms if pd.notna(symptom)]

        detractor_symptoms = pd.unique(filtered_verbatims[detractor_columns].values.ravel())
        detractor_symptoms = [symptom for symptom in detractor_symptoms if pd.notna(symptom)]

        # Filters for Delighters and Detractors (Grouped)
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

        # Apply Filters for Delighter and Detractor Symptoms
        if "All" not in selected_delighter:
            filtered_verbatims = filtered_verbatims[
                filtered_verbatims[delighter_columns].isin(selected_delighter).any(axis=1)
            ]

        if "All" not in selected_detractor:
            filtered_verbatims = filtered_verbatims[
                filtered_verbatims[detractor_columns].isin(selected_detractor).any(axis=1)
            ]

        # Dynamic Additional Filters for Columns after Symptom 20
        additional_columns = verbatims.columns[20:]  # Columns from the 21st onward
        if len(additional_columns) > 0:
            st.sidebar.header("📋 Additional Filters")
            for column in additional_columns:
                if column not in delighter_columns + detractor_columns:  # Avoid duplicating delighter/detractor filters
                    filtered_verbatims, _ = apply_filter(filtered_verbatims, column, column)
        else:
            st.sidebar.info("No additional filters available.")

        st.markdown("---")  # Separator linehttps://github.com/williamseddon/testingEnv/blob/main/starwalk_ui_test.py#L13C47
            
                    
     # Metrics Summary Section
        st.markdown("""
            ### ⭐ Star Rating Metrics
            <p style="text-align: center; font-size: 14px; color: gray;">
                A summary of customer feedback and review distribution.
            </p>
            """, unsafe_allow_html=True)
        
        # Calculate the metrics
        total_reviews = len(filtered_verbatims)
        avg_rating = filtered_verbatims['Star Rating'].mean()
        star_counts = filtered_verbatims['Star Rating'].value_counts().sort_index()
        percentages = (star_counts / total_reviews * 100).round(1)  # Calculate percentages
        star_labels = [f"{int(star)} stars" for star in star_counts.index]
        
        # Display metrics in a single centered row
        metrics_container = st.container()
        with metrics_container:
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Total Reviews", f"{total_reviews:,}")
            with col2:
                st.metric("Avg Star Rating", f"{avg_rating:.1f}", delta_color="inverse")
        
        # Add a star rating distribution as an interactive horizontal bar chart
        fig_bar_horizontal = go.Figure(go.Bar(
            x=star_counts.values,
            y=star_labels,
            orientation='h',
            text=[f"{value} reviews ({percentage}%)" for value, percentage in zip(star_counts.values, percentages)],
            textposition='auto',
            marker=dict(color=['#FFA07A', '#FA8072', '#FFD700', '#ADFF2F', '#32CD32']),
            hoverinfo="y+x+text"
        ))
        
        fig_bar_horizontal.update_layout(
            title="<b>Star Rating Distribution</b>",
            xaxis=dict(
                title="Number of Reviews",
                title_font=dict(size=14),
                tickfont=dict(size=12),
                showgrid=False,
            ),
            yaxis=dict(
                title="Star Ratings",
                title_font=dict(size=14),
                tickfont=dict(size=12),
                showgrid=False,
            ),
            title_font=dict(size=18),
            plot_bgcolor="white",
            template="plotly_white",
            margin=dict(l=50, r=50, t=50, b=50)
        )
        
        st.plotly_chart(fig_bar_horizontal, use_container_width=True)
        
        # Calculate percentages for 1-star reviews
        one_star_count = star_counts.get(1, 0)  # Safely get 1-star count or default to 0 if missing
        one_star_percentage = (one_star_count / total_reviews * 100) if total_reviews > 0 else 0
        
        # Evaluate review quality
        if one_star_percentage < 10:
            review_quality = "high"
            review_insight = "Most customers are satisfied, with less than 10% reporting 1-star reviews."
        elif one_star_percentage >= 10 and one_star_percentage < 20:
            review_quality = "moderate"
            review_insight = "There are moderate concerns, with 10-20% reporting 1-star reviews."
        else:
            review_quality = "low"
            review_insight = "Customer satisfaction is low, with over 20% reporting 1-star reviews."
        
        # Find the most common star rating
        most_common_rating = star_counts.idxmax() if not star_counts.empty else None
        most_common_count = star_counts[most_common_rating] if most_common_rating else 0
        most_common_percentage = percentages[most_common_rating] if most_common_rating else 0
        
        # Display insights
        st.markdown(f"""
            <p style="text-align: center; font-size: 14px; color: gray;">
                <strong>Review Quality:</strong> {review_quality.title()}<br>
                {review_insight}
            </p>
            <p style="text-align: center; font-size: 14px; color: gray;">
                The majority of reviews ({most_common_count} reviews, {most_common_percentage}%) are {most_common_rating} stars,
                indicating strong customer sentiment.
            </p>
            """, unsafe_allow_html=True)
        
        # Add country and source breakdown
        st.markdown("### 🌍 Country & Source Breakdown")
        
        if 'Country' in filtered_verbatims.columns and 'Source' in filtered_verbatims.columns:
            country_source_stats = (
                filtered_verbatims
                .groupby(['Country', 'Source'])
                .agg(Avg_Rating=('Star Rating', 'mean'), Review_Count=('Star Rating', 'count'))
                .reset_index()
            )
        
            # Calculate overall average and review count by country
            country_overall = (
                filtered_verbatims
                .groupby('Country')
                .agg(Avg_Rating=('Star Rating', 'mean'), Review_Count=('Star Rating', 'count'))
                .reset_index()
            )
            country_overall['Source'] = 'Overall'
        
            # Append the overall data to the detailed data
            combined_stats = pd.concat([country_source_stats, country_overall], ignore_index=True)
        
            styled_table = combined_stats.style.format({
                'Avg_Rating': '{:.1f}',
                'Review_Count': '{:,}'
            })
        
            st.markdown("""
                <style>
                    .dataframe tbody tr:hover {
                        background-color: #f1f1f1;
                    }
                </style>
            """, unsafe_allow_html=True)
        
            st.dataframe(styled_table, use_container_width=True)
        else:
            st.warning("Country or Source data is missing in the uploaded file.")


        # Graph Over Time
        st.markdown("### 📈 Graph Over Time")

        if 'Review Date' not in filtered_verbatims.columns:
            st.error("The 'Review Date' column is missing from the data. Please upload a valid file.")
            st.stop()

        filtered_verbatims['Review Date'] = pd.to_datetime(filtered_verbatims['Review Date'], errors='coerce')

        # Add a dropdown for selecting bar size below the graph header
        st.markdown("#### Select Bar Size")
        bar_size = st.selectbox(
            "Choose the aggregation level for review mentions:",
            options=["Daily", "Weekly", "Monthly"]
        )

        # Adjust the aggregation level based on the selected bar size
        if bar_size == "Weekly":
            filtered_verbatims['TimePeriod'] = filtered_verbatims['Review Date'].dt.to_period("W").dt.start_time
        elif bar_size == "Monthly":
            filtered_verbatims['TimePeriod'] = filtered_verbatims['Review Date'].dt.to_period("M").dt.start_time
        else:  # Default to Daily
            filtered_verbatims['TimePeriod'] = filtered_verbatims['Review Date'].dt.date

        # Sort data by time period to ensure cumulative calculations are accurate
        filtered_verbatims = filtered_verbatims.sort_values(by=['Country', 'TimePeriod'])

        # Calculate cumulative sums and averages for each country
        filtered_verbatims['Cumulative_Total_Reviews'] = filtered_verbatims.groupby('Country')['Star Rating'].cumcount() + 1
        filtered_verbatims['Cumulative_Sum_Rating'] = filtered_verbatims.groupby('Country')['Star Rating'].cumsum()
        filtered_verbatims['Cumulative_Avg_Rating'] = (
            filtered_verbatims['Cumulative_Sum_Rating'] / filtered_verbatims['Cumulative_Total_Reviews']
        )

        # Aggregate total reviews and cumulative average for plotting
        grouped = filtered_verbatims.groupby(['TimePeriod', 'Country']).agg(
            Total_Reviews=('Star Rating', 'count'),
            Cumulative_Avg_Rating=('Cumulative_Avg_Rating', 'last')  # Take the latest cumulative average for the period
        ).reset_index()

        if grouped.empty:
            st.warning("No data available for the selected filters.")
            st.stop()

        fig = go.Figure()

        # Define a consistent color palette for regions
        region_colors = {
            "UK": "#FF7F50",  # Coral
            "USA": "#4682B4",  # Steel Blue
            "Canada": "#32CD32"  # Lime Green
        }

        default_color = "#808080"  # Fallback color for undefined regions

        # Add bars for total reviews and lines for cumulative average rating
        for country in grouped['Country'].unique():
            country_data = grouped[grouped['Country'] == country]
            color = region_colors.get(country, default_color)

            # Add bar for total review counts
            fig.add_trace(go.Bar(
                x=country_data['TimePeriod'],
                y=country_data['Total_Reviews'],
                name=f"{country} Reviews ({bar_size})",
                marker=dict(color=color),
                opacity=0.7,
                yaxis="y"
            ))

            # Add line for cumulative average rating
            fig.add_trace(go.Scatter(
                x=country_data['TimePeriod'],
                y=country_data['Cumulative_Avg_Rating'],
                mode='lines+markers',
                name=f"{country} Cumulative Average Rating",
                line=dict(color=color, width=2),
                yaxis="y2"
            ))

        # Update layout for dual-axis
        fig.update_layout(
            title=f"Country-wise Review Mentions and Over-Time Average Ratings ({bar_size})",
            xaxis=dict(title="Time Period", tickformat="%b %d", title_font=dict(size=14)),
            yaxis=dict(title="Review Mentions", title_font=dict(size=14), showgrid=False),
            yaxis2=dict(
                title="Cumulative Star Rating (1-5)",
                overlaying="y",
                side="right",
                range=[1, 5.2],  # Added extra headroom above 5 stars
                title_font=dict(size=14),
                showgrid=False
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.4,  # Adjusted to move the legend away from the graph axes
                xanchor="center",
                x=0.5
            ),
            barmode="stack",
            template="plotly_white",
            margin=dict(l=50, r=50, t=70, b=70)  # Adjusted margins for better responsiveness
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")  # Separator line

        # Updated Delighters and Detractors Analysis Section

        st.markdown("### 🌟 Delighters and Detractors Analysis")

        # Function to style only the 'Avg Star' column
        def style_star_ratings(value):
            """Styles cells in the Avg Star column: Green for ratings ≥4.5, red for <4.5."""
            if isinstance(value, (float, int)):
                if value >= 4.5:
                    return "color: green;"
                elif value < 4.5:
                    return "color: red;"
            return ""

        def analyze_delighters_detractors(symptom_columns):
            """Analyze delighter/detractor symptoms and calculate metrics."""
            # Check if symptom columns contain any valid values
            if filtered_verbatims[symptom_columns].notna().sum().sum() == 0:
                return pd.DataFrame(columns=['Item', 'Avg Star', 'Mentions', '% Total'])

            # Extract unique non-NaN symptoms
            unique_items = pd.unique(filtered_verbatims[symptom_columns].values.ravel())
            unique_items = [item for item in unique_items if pd.notna(item) and item]
            
            results = []

            for item in unique_items:
                matched_rows = filtered_verbatims[filtered_verbatims[symptom_columns].isin([item]).any(axis=1)]
                total_star_rating = matched_rows['Star Rating'].sum()
                count = matched_rows['Star Rating'].count()
                avg_star_rating = total_star_rating / count if count > 0 else 0
                percentage_mentions = (count / len(filtered_verbatims)) * 100 if len(filtered_verbatims) > 0 else 0
                results.append({
                    'Item': item.title(),
                    'Avg Star': round(avg_star_rating, 1),
                    'Mentions': count,
                    '% Total': f"{round(percentage_mentions, 1)}%"  # Format as percentage with % sign
                })

            results_df = pd.DataFrame(results)
            return results_df.sort_values(by="Mentions", ascending=False)

        # Process detractors and delighters
        detractors_results = analyze_delighters_detractors(detractor_columns)
        delighters_results = analyze_delighters_detractors(delighter_columns)

        # Display results
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("All Detractors")
            if detractors_results.empty:
                st.write("No detractor symptoms found.")
            else:
                st.dataframe(
                    detractors_results.style.applymap(style_star_ratings, subset=['Avg Star'])  # Style only Avg Star
                    .format({'Avg Star': '{:.1f}', 'Mentions': '{:.0f}'}),  # Don't format % Total again, it's already a string
                    use_container_width=True
                )

        with col2:
            st.subheader("All Delighters")
            if delighters_results.empty:
                st.write("No delighter symptoms found.")
            else:
                st.dataframe(
                    delighters_results.style.applymap(style_star_ratings, subset=['Avg Star'])  # Style only Avg Star
                    .format({'Avg Star': '{:.1f}', 'Mentions': '{:.0f}'}),  # Don't format % Total again, it's already a string
                    use_container_width=True
                )

        st.markdown("---")  # Separator line


        # Initialize translator
        translator = Translator()

        # Enhanced Reviews Display Section with Pagination
        st.markdown("### 📝 All Reviews")

        # Add the Translate button below the header
        translate_to_english = st.button("Translate All Reviews to English")

        reviews_per_page = 10

        # Initialize pagination state
        if "review_page" not in st.session_state:
            st.session_state["review_page"] = 0

        # Function to reset scroll position to the top of the section
        def scroll_to_top():
            st.experimental_rerun()

        # Determine start and end indices for the current page
        current_page = st.session_state["review_page"]
        start_index = current_page * reviews_per_page
        end_index = start_index + reviews_per_page

        # Slice the paginated reviews
        paginated_reviews = filtered_verbatims.iloc[start_index:end_index]

        if paginated_reviews.empty:
            st.warning("No reviews match the selected criteria.")
        else:
            for _, row in paginated_reviews.iterrows():
                if translate_to_english:
                    # Translate review if the button is pressed
                    try:
                        translated_review = translator.translate(row['Verbatim'], dest="en").text
                    except Exception as e:
                        st.error(f"Error translating review: {e}")
                        translated_review = row['Verbatim']  # Fallback to original text
                else:
                    # Show the original review
                    translated_review = row['Verbatim']

                # Prepare delighter and detractor badges
                delighter_badges = [
                    f'<div style="display:inline-block; padding:5px 10px; background-color:lightgreen; color:black; border-radius:5px; margin:5px;">{row[col]}</div>'
                    for col in delighter_columns if col in row and pd.notna(row[col])
                ]
                detractor_badges = [
                    f'<div style="display:inline-block; padding:5px 10px; background-color:lightcoral; color:black; border-radius:5px; margin:5px;">{row[col]}</div>'
                    for col in detractor_columns if col in row and pd.notna(row[col])
                ]

                # If no delighter or detractor badges are present, display a message
                delighter_message = "<i>No delighter symptoms reported</i>" if not delighter_badges else " ".join(delighter_badges)
                detractor_message = "<i>No detractor symptoms reported</i>" if not detractor_badges else " ".join(detractor_badges)

                # Display review
                st.markdown(
                    f"""
                    <div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 10px; border-radius: 5px; background-color: #f9f9f9;">
                        <p><strong>Source:</strong> {row['Source']} | <strong>Model:</strong> {row['Model (SKU)']}</p>
                        <p><strong>Country:</strong> {row['Country']}</p>
                        <p><strong>Rating:</strong> {'⭐' * int(row['Star Rating'])} ({row['Star Rating']}/5)</p>
                        <p><strong>Review:</strong> {translated_review}</p>
                        <div><strong>Delighter Symptoms:</strong> {delighter_message}</div>
                        <div><strong>Detractor Symptoms:</strong> {detractor_message}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # Add navigation buttons for pagination
        col1, col2, col3 = st.columns([1, 1, 1])

        # "Go Back" button
        with col1:
            if current_page > 0 and st.button("⬅ Go Back", key="go_back"):
                st.session_state["review_page"] -= 1
                scroll_to_top()

        # Page indicator
        with col2:
            total_pages = (len(filtered_verbatims) + reviews_per_page - 1) // reviews_per_page  # Calculate total pages
            st.markdown(
                f"<div style='text-align: center; font-weight: bold;'>Page {current_page + 1} of {total_pages}</div>",
                unsafe_allow_html=True,
            )

        # "View More" button
        with col3:
            if end_index < len(filtered_verbatims) and st.button("➡ View More", key="view_more"):
                st.session_state["review_page"] += 1
                scroll_to_top()

        st.markdown("---")  # Separator line
 
        # Word Cloud Visualization
        st.markdown("### 🌟 Word Cloud for Delighters and Detractors")

        # Prepare text for detractors and delighters
        detractors_text = " ".join(filtered_verbatims[detractor_columns].stack())
        delighters_text = " ".join(filtered_verbatims[delighter_columns].stack())

        # Generate high-resolution word clouds with better scaling and layout
        wordcloud_detractors = WordCloud(
            background_color="white",
            colormap="Reds",
            width=1600,  # Higher resolution
            height=800,
            max_words=100,  # Limit the number of words
            contour_width=3,  # Add contour for better visual appeal
            contour_color="red",
            scale=3  # Enhance scaling for better clarity
        ).generate(detractors_text)

        wordcloud_delighters = WordCloud(
            background_color="white",
            colormap="Greens",
            width=1600,  # Higher resolution
            height=800,
            max_words=100,
            contour_width=3,
            contour_color="green",
            scale=3
        ).generate(delighters_text)

        # Display detractors word cloud
        st.markdown("#### 😠 Detractors")
        fig, ax = plt.subplots(figsize=(10, 5))  # Larger figure size for better clarity
        ax.imshow(wordcloud_detractors, interpolation='bilinear')
        ax.axis("off")
        st.pyplot(fig)

        # Display delighters word cloud
        st.markdown("#### 😊 Delighters")
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(wordcloud_delighters, interpolation='bilinear')
        ax.axis("off")
        st.pyplot(fig)
    except Exception as e:
            st.error(f"An error occurred: {e}")
else:
    st.info("Please upload an Excel file to get started.")
