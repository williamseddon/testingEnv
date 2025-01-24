import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import plotly.io as pio

# Page Config
st.set_page_config(page_title="Jira Issues Dashboard", layout="wide", page_icon="📊")

# Title with Styling
st.markdown(
    """
    <style>
    h1 {
        color: #4CAF50;
        text-align: center;
        font-family: Arial, sans-serif;
    }
    .scrollable-table {
        overflow-y: auto;
        max-height: 400px;
        border: 1px solid #ddd;
        padding: 10px;
        border-radius: 8px;
        background-color: #f9f9f9;
    }
    .description-box {
        background-color: #ffffff;
        padding: 20px;
        margin: 15px 0;
        border-left: 5px solid #4CAF50;
        border-radius: 10px;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        line-height: 1.6;
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .description-box:hover {
        transform: scale(1.02);
        box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
    }
    .description-box h4 {
        color: #4CAF50;
        margin-bottom: 10px;
    }
    .description-field {
        margin-bottom: 8px;
    }
    .description-field strong {
        color: #333;
    }
    .pagination {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin: 20px 0;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    .pagination span {
        font-size: 14px;
        color: #555;
    }
    .delta-positive {
        color: green;
        font-weight: bold;
    }
    .delta-negative {
        color: red;
        font-weight: bold;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 Jira Issues Dashboard")

# File Upload with Custom Message
uploaded_file = st.file_uploader(
    "Upload your Excel file (must contain 'Your Jira Issues' tab)", type=['xlsx']
)

# Text for required columns with a modern download button and additional instructions
if not uploaded_file:
    st.markdown(
        """
        ### Required Column Format:
        To use this dashboard, please upload an Excel file containing the following columns:
        
        - **Date Identified**: The date the issue was identified (e.g., 2024-01-01).
        - **SKU(s)**: The SKU(s) related to the issue.
        - **Base SKU**: The base SKU category.
        - **Region**: The region where the issue occurred.
        - **Symptom**: The reported symptom or issue.
        - **Disposition**: The resolution or status of the issue.
        - **Description**: A detailed description of the issue.
        - **Serial Number**: The serial number of the affected unit.

        Make sure your file has a tab named **'Your Jira Issues'**.
        """,
        unsafe_allow_html=True
    )

    # Add a modern, animated button to download the template
    st.markdown(
        """
        <style>
        .modern-button {
            display: inline-block;
            background-color: #4CAF50;
            color: white; /* Font color set to white */
            font-size: 16px;
            font-weight: bold;
            padding: 10px 25px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .modern-button:hover {
            transform: scale(1.05);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.3);
        }
        </style>
        <a href="https://sharkninja.atlassian.net/issues/?filter=19767" target="_blank" class="modern-button">
            View Template Here
        </a>
        """,
        unsafe_allow_html=True
    )

    # Add instructions below the button
    st.markdown(
        """
        **After visiting the webpage:**
        1. Change Base SKU or adjust filters accordingly.
        2. Press **Apps** in the top bar.
        3. Choose **Open in Microsoft Excel** to get your download file.
        """,
        unsafe_allow_html=True
    )



if uploaded_file:
    try:
        # Load data
        data = pd.read_excel(uploaded_file, sheet_name='Your Jira Issues')
        
        # Validate required columns
        required_columns = ['Date Identified', 'SKU(s)', 'Base SKU', 'Region', 'Symptom', 'Disposition', 'Description', 'Serial Number']
        missing_columns = [col for col in required_columns if col not in data.columns]
        
        if missing_columns:
            st.error(f"The following required columns are missing: {', '.join(missing_columns)}")
        else:
            # Preprocess date
            data['Date Identified'] = pd.to_datetime(data['Date Identified'], errors='coerce')
        
            # Standardize SKU(s) and Base SKU to uppercase
            data['SKU(s)'] = data['SKU(s)'].str.upper().str.strip()
            data['Base SKU'] = data['Base SKU'].str.upper().str.strip()
        
            # Proceed with filtering and dashboard logic


            # Sidebar Filters
            st.sidebar.header("Filters")
            sku_filter = st.sidebar.multiselect(
                "Filter by SKU", options=['ALL'] + list(data['SKU(s)'].dropna().unique()), default=['ALL']
            )
            base_sku_filter = st.sidebar.multiselect(
                "Filter by Base SKU", options=['ALL'] + list(data['Base SKU'].dropna().unique()), default=['ALL']
            )
            region_filter = st.sidebar.multiselect(
                "Filter by Region", options=['ALL'] + list(data['Region'].dropna().unique()), default=['ALL']
            )
            symptom_filter = st.sidebar.multiselect(
                "Filter by Symptom", options=['ALL'] + list(data['Symptom'].dropna().unique()), default=['ALL']
            )
            disposition_filter = st.sidebar.multiselect(
                "Filter by Disposition", options=['ALL'] + list(data['Disposition'].dropna().unique()), default=['ALL']
            )
            tsf_only_filter = st.sidebar.checkbox("TSF Only", value=True)
            top_10_symptoms_filter = st.sidebar.checkbox("Top 10 Symptoms Only", value=False)
            top_10_dispositions_filter = st.sidebar.checkbox("Top 10 Dispositions Only", value=False)
            date_filter = st.sidebar.selectbox(
                "Date Range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3
            )

            # Input for setting periods for table
            period_days_table = st.sidebar.number_input("Set Table Period Length (days)", min_value=1, value=30, step=1)

            search_query = st.sidebar.text_input("Search Descriptions")

            # Adjust start_date for graphs and table
            if date_filter == "Last Week":
                start_date_graph = datetime.now() - timedelta(weeks=1)
                period_label_graph = "Last 7 Days"
            elif date_filter == "Last Month":
                start_date_graph = datetime.now() - timedelta(days=30)
                period_label_graph = "Last 30 Days"
            elif date_filter == "Last Year":
                start_date_graph = datetime.now() - timedelta(days=365)
                period_label_graph = "Last 365 Days"
            else:
                start_date_graph = data['Date Identified'].min()
                period_label_graph = "All Time"

            start_date_table = datetime.now() - timedelta(days=period_days_table)
            previous_start_date_table = start_date_table - timedelta(days=period_days_table)
            period_label_table = f"Last {period_days_table} Days"

            # Standardize SKU(s) and Base SKU to uppercase
            data['SKU(s)'] = data['SKU(s)'].str.upper().str.strip()
            data['Base SKU'] = data['Base SKU'].str.upper().str.strip()


            if 'ALL' not in region_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Region'].isin(region_filter)]
            if 'ALL' not in symptom_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Symptom'].isin(symptom_filter)]
            if 'ALL' not in disposition_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Disposition'].isin(disposition_filter)]
            # Initialize filtered_data_table with the complete dataset
            filtered_data_table = data.copy()
            
            # Apply "TSF Only" filter
            if tsf_only_filter:
                filtered_data_table = filtered_data_table[
                    filtered_data_table['Disposition'].str.contains('_ts_failed|_replaced', case=False, na=False)
                ]
            
            # Apply "Top 10 Symptoms Only" filter
            if top_10_symptoms_filter:
                top_symptoms = filtered_data_table['Symptom'].value_counts().nlargest(10).index
                filtered_data_table['Symptom'] = filtered_data_table['Symptom'].apply(lambda x: x if x in top_symptoms else 'Other')
            
            # Apply "Top 10 Dispositions Only" filter
            if top_10_dispositions_filter:
                top_dispositions = filtered_data_table['Disposition'].value_counts().nlargest(10).index
                filtered_data_table['Disposition'] = filtered_data_table['Disposition'].apply(lambda x: x if x in top_dispositions else 'Other')

            
            # Apply keyword search filter separately from table period logic
            if search_query:
                search_query = search_query.strip().lower()
                keyword_filtered_data = data[
                    data['Description']
                    .fillna('')
                    .str.lower()
                    .str.contains(search_query, na=False)
                ]
            else:
                keyword_filtered_data = data.copy()
            
            # Apply additional filters to the keyword-filtered data
            filtered_data_table = keyword_filtered_data.copy()
            if 'ALL' not in sku_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['SKU(s)'].isin(sku_filter)]
            if 'ALL' not in base_sku_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Base SKU'].isin(base_sku_filter)]
            if 'ALL' not in region_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Region'].isin(region_filter)]
            if 'ALL' not in symptom_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Symptom'].isin(symptom_filter)]
            if 'ALL' not in disposition_filter:
                filtered_data_table = filtered_data_table[filtered_data_table['Disposition'].isin(disposition_filter)]
            
            # Apply the date filter for tables and graphs separately
            date_filtered_data = filtered_data_table[
                filtered_data_table['Date Identified'] >= start_date_table
            ]
            
            # Use keyword-filtered data directly for descriptions
            descriptions = keyword_filtered_data[
                ['Description', 'SKU(s)', 'Base SKU', 'Region', 'Disposition', 'Symptom', 'Date Identified', 'Serial Number']
            ].dropna(subset=['Description']).reset_index(drop=True)
            
                        
            filtered_data_graph = data.copy()
            filtered_data_graph = filtered_data_graph[filtered_data_graph['Date Identified'] >= start_date_graph]
            if tsf_only_filter:
                filtered_data_graph = filtered_data_graph[
                    filtered_data_graph['Disposition'].str.contains('_ts_failed|_replaced', case=False, na=False)
                ]
            if top_10_symptoms_filter:
                top_symptoms = filtered_data_graph['Symptom'].value_counts().nlargest(10).index
                filtered_data_graph['Symptom'] = filtered_data_graph['Symptom'].apply(lambda x: x if x in top_symptoms else 'Other')
            if top_10_dispositions_filter:
                top_dispositions = filtered_data_graph['Disposition'].value_counts().nlargest(10).index
                filtered_data_graph['Disposition'] = filtered_data_graph['Disposition'].apply(lambda x: x if x in top_dispositions else 'Other')
            if search_query:
                filtered_data_graph = filtered_data_graph[
                    filtered_data_graph['Description']
                    .fillna('')
                    .str.lower()
                    .str.contains(search_query, na=False)
                ]

            # Summary Section
            st.header("🔍 Summary")
            total_issues = len(filtered_data_graph)
            unique_skus = filtered_data_graph['SKU(s)'].nunique()
            unique_base_skus = filtered_data_graph['Base SKU'].nunique()
            unique_regions = filtered_data_graph['Region'].nunique()
            unique_symptoms = filtered_data_graph['Symptom'].nunique()
            st.write(f"**Total Issues:** {total_issues}")
            st.write(f"**Unique SKUs:** {unique_skus}")
            st.write(f"**Unique Base SKUs:** {unique_base_skus}")
            st.write(f"**Unique Regions:** {unique_regions}")
            st.write(f"**Unique Symptoms:** {unique_symptoms}")

            # Toggle for combining lesser symptoms into "Other"
            combine_other = st.checkbox("Combine lesser symptoms into 'Other'")

            # Symptom Issues Over Time (Graph)
            st.header("📅 Symptom Issues Over Time (Graph)")
            aggregation = st.selectbox("Aggregate By", ["Day", "Week", "Month"], index=1)
            aggregation_mapping = {"Day": 'D', "Week": 'W', "Month": 'M'}
            agg_freq = aggregation_mapping[aggregation]

            symptom_time_data_graph = filtered_data_graph.groupby([pd.Grouper(key='Date Identified', freq=agg_freq), 'Symptom']).size().reset_index(name='Count')

            if combine_other:
                # Combine lesser symptoms into "Other"
                top_symptoms = symptom_time_data_graph.groupby('Symptom')['Count'].sum().nlargest(10).index
                symptom_time_data_graph['Symptom'] = symptom_time_data_graph['Symptom'].apply(lambda x: x if x in top_symptoms else 'Other')
                symptom_time_data_graph = symptom_time_data_graph.groupby(['Date Identified', 'Symptom']).sum().reset_index()

            # Interactive Plot for Symptom Issues (Graph)
            st.subheader(f"Symptom Trends ({period_label_graph})")
            fig = px.bar(
                symptom_time_data_graph,
                x='Date Identified',
                y='Count',
                color='Symptom',
                title=f"Symptom Trends Over Time ({period_label_graph})",
                labels={'Count': 'Number of Issues', 'Date Identified': 'Date'},
                template="plotly_white",
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig.update_layout(barmode='stack', 
                              xaxis_title=dict(text='Date', font=dict(size=14, weight='bold')),
                              yaxis_title=dict(text='Count', font=dict(size=14, weight='bold')),
                              margin=dict(t=40))
            st.plotly_chart(fig, use_container_width=True)

            # Dispositions Over Time (Graph)
            st.header("📅 Dispositions Over Time (Graph)")
            disposition_time_data_graph = filtered_data_graph.groupby([pd.Grouper(key='Date Identified', freq=agg_freq), 'Disposition']).size().reset_index(name='Count')

            if combine_other:
                # Combine lesser dispositions into "Other"
                top_dispositions = disposition_time_data_graph.groupby('Disposition')['Count'].sum().nlargest(10).index
                disposition_time_data_graph['Disposition'] = disposition_time_data_graph['Disposition'].apply(lambda x: x if x in top_dispositions else 'Other')
                disposition_time_data_graph = disposition_time_data_graph.groupby(['Date Identified', 'Disposition']).sum().reset_index()

            # Interactive Plot for Dispositions (Graph)
            st.subheader(f"Disposition Trends ({period_label_graph})")
            fig = px.bar(
                disposition_time_data_graph,
                x='Date Identified',
                y='Count',
                color='Disposition',
                title=f"Disposition Trends Over Time ({period_label_graph})",
                labels={'Count': 'Number of Issues', 'Date Identified': 'Date'},
                template="plotly_white",
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig.update_layout(barmode='stack', 
                              xaxis_title=dict(text='Date', font=dict(size=14, weight='bold')),
                              yaxis_title=dict(text='Count', font=dict(size=14, weight='bold')),
                              margin=dict(t=40))
            st.plotly_chart(fig, use_container_width=True)

           # Ranked Symptoms with Metrics (Table)
            st.header("📊 Ranked Symptoms (Table)")
            symptom_rank = filtered_data_table['Symptom'].value_counts().reset_index()
            symptom_rank.columns = ['Symptom', 'Count']
            
            # Calculate additional metrics
            current_period = filtered_data_table[filtered_data_table['Date Identified'] >= start_date_table]
            previous_period = filtered_data_table[(filtered_data_table['Date Identified'] < start_date_table) &
                                                  (filtered_data_table['Date Identified'] >= previous_start_date_table)]
            
            current_counts = current_period['Symptom'].value_counts()
            previous_counts = previous_period['Symptom'].value_counts()
            
            symptom_rank[f"Last {period_days_table} Days"] = symptom_rank['Symptom'].apply(lambda x: current_counts.get(x, 0))
            symptom_rank[f"Previous {period_days_table} Days"] = symptom_rank['Symptom'].apply(lambda x: previous_counts.get(x, 0))
            
            symptom_rank['Delta'] = symptom_rank[f"Last {period_days_table} Days"] - symptom_rank[f"Previous {period_days_table} Days"]
            symptom_rank['Delta (%)'] = symptom_rank.apply(
                lambda row: round((row['Delta'] / row[f"Previous {period_days_table} Days"]) * 100, 2)
                if row[f"Previous {period_days_table} Days"] > 0 else None, axis=1
            )
            
            # Add Trend Column with Green Arrow for Down Only
            symptom_rank['Trend'] = symptom_rank['Delta'].apply(
                lambda x: "🔺 Up" if x > 0
                else ("<span class='delta-negative' style='color:green'>🔻 Down</span>" if x < 0
                      else "➖ No Change")
            )

            
            # Limit to Top 10 Rows
            symptom_rank = symptom_rank.head(10)
            
            # Display Ranked Symptoms Table in Scrollable Box
            st.subheader("Ranked Symptoms Table")
            st.markdown(
                f"""
                <div class="scrollable-table">
                    {symptom_rank.to_html(escape=False, index=False)}
                </div>
                """,
                unsafe_allow_html=True
            )


            # Paginated Descriptions
            st.header("🗒 Descriptions")
            descriptions = filtered_data_table[['Description', 'SKU(s)', 'Base SKU', 'Region', 'Disposition', 'Symptom', 'Date Identified', 'Serial Number']].dropna().reset_index(drop=True)

            # Handle empty descriptions
            total_items = len(descriptions)
            items_per_page = st.selectbox("Items per page:", [10, 25, 50, 100], index=0)
            total_pages = max(1, -(-total_items // items_per_page))  # Ensure at least one page exists
            current_page = st.number_input("Page:", min_value=1, max_value=total_pages, value=1, step=1)

            # Calculate start and end indices for pagination
            start_idx = (current_page - 1) * items_per_page
            end_idx = start_idx + items_per_page

            if total_items == 0:
                st.warning("No descriptions match your search criteria.")
            else:
                st.write("### Descriptions (Filtered)")
                for idx, row in descriptions.iloc[start_idx:end_idx].iterrows():
                    st.markdown(
                        f"""
                        <div class='description-box'>
                            <h4>Issue Details</h4>
                            <div class='description-field'><strong>SKU:</strong> {row['SKU(s)']}</div>
                            <div class='description-field'><strong>Base SKU:</strong> {row['Base SKU']}</div>
                            <div class='description-field'><strong>Region:</strong> {row['Region']}</div>
                            <div class='description-field'><strong>Disposition:</strong> {row['Disposition']}</div>
                            <div class='description-field'><strong>Symptom:</strong> {row['Symptom']}</div>
                            <div class='description-field'><strong>Date Identified:</strong> {row['Date Identified'].strftime('%Y-%m-%d') if pd.notnull(row['Date Identified']) else 'N/A'}</div>
                            <div class='description-field'><strong>Serial Number:</strong> {row['Serial Number']}</div>
                            <div class='description-field'><strong>Description:</strong> {row['Description']}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                # Pagination Controls
                st.markdown(
                    f"""
                    <div class='pagination'>
                        <span>Total Items: {total_items}</span>
                        <span>Page {current_page} of {total_pages}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            # Add Download Option
            st.sidebar.download_button(
                label="Download Filtered Data",
                data=filtered_data_table.to_csv(index=False),
                file_name="filtered_jira_issues.csv",
                mime="text/csv"
            )
    except Exception as e:
        st.error(f"An error occurred while processing the file: {str(e)}")
