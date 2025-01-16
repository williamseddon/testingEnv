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
        background-color: #f0f8ff;
        padding: 15px;
        margin: 10px 0;
        border-left: 4px solid #4CAF50;
        border-radius: 8px;
        font-family: Arial, sans-serif;
        line-height: 1.5;
    }
    .highlight-up {
        color: green;
        font-weight: bold;
    }
    .highlight-down {
        color: red;
        font-weight: bold;
    }
    .highlight-stable {
        color: gray;
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
if uploaded_file:
    # Load data
    data = pd.read_excel(uploaded_file, sheet_name='Your Jira Issues')

    # Preprocess date
    data['Date Identified'] = pd.to_datetime(data['Date Identified'], errors='coerce')

    # Sidebar Filters
    st.sidebar.header("Filters")
    sku_filter = st.sidebar.multiselect(
        "Filter by SKU", options=['ALL'] + list(data['SKU(s)'].dropna().unique()), default=['ALL']
    )
    symptom_filter = st.sidebar.multiselect(
        "Filter by Symptom", options=['ALL'] + list(data['Symptom'].dropna().unique()), default=['ALL']
    )
    disposition_filter = st.sidebar.multiselect(
        "Filter by Disposition", options=['ALL'] + list(data['Disposition'].dropna().unique()), default=['ALL']
    )
    date_filter = st.sidebar.selectbox(
        "Date Range", ["Last Week", "Last Month", "Last Year", "All Time"], index=3
    )

    # Input for setting periods for table
    period_days_table = st.sidebar.number_input("Set Table Period Length (days)", min_value=1, value=30, step=1)

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

    # Apply filters for table and graphs
    filtered_data_table = data.copy()
    if 'ALL' not in sku_filter:
        filtered_data_table = filtered_data_table[filtered_data_table['SKU(s)'].isin(sku_filter)]
    if 'ALL' not in symptom_filter:
        filtered_data_table = filtered_data_table[filtered_data_table['Symptom'].isin(symptom_filter)]
    if 'ALL' not in disposition_filter:
        filtered_data_table = filtered_data_table[filtered_data_table['Disposition'].isin(disposition_filter)]
    filtered_data_table = filtered_data_table[filtered_data_table['Date Identified'] >= previous_start_date_table]

    filtered_data_graph = data.copy()
    filtered_data_graph = filtered_data_graph[filtered_data_graph['Date Identified'] >= start_date_graph]

    # Search Bar
    st.sidebar.header("Search")
    search_query = st.sidebar.text_input("Search Descriptions")
    if search_query:
        filtered_data_table = filtered_data_table[filtered_data_table['Description'].str.contains(search_query, case=False, na=False)]
        filtered_data_graph = filtered_data_graph[filtered_data_graph['Description'].str.contains(search_query, case=False, na=False)]

    # Summary Section with Icons
    st.header("🔍 Summary")
    total_issues = len(filtered_data_graph)
    unique_skus = filtered_data_graph['SKU(s)'].nunique()
    unique_symptoms = filtered_data_graph['Symptom'].nunique()
    st.write(f"**Total Issues:** {total_issues}")
    st.write(f"**Unique SKUs:** {unique_skus}")
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

    # Ensure no missing values in Current Period and Previous Period
    symptom_rank[f"Last {period_days_table} Days"] = symptom_rank['Symptom'].apply(lambda x: current_counts.get(x, 0) if x in current_counts.index else 0)
    symptom_rank[f"Previous {period_days_table} Days"] = symptom_rank['Symptom'].apply(lambda x: previous_counts.get(x, 0) if x in previous_counts.index else 0)

    symptom_rank['Delta'] = symptom_rank['Symptom'].apply(lambda x: current_counts.get(x, 0) - previous_counts.get(x, 0))
    symptom_rank['Delta (%)'] = symptom_rank.apply(
        lambda row: round((row['Delta'] / row[f"Previous {period_days_table} Days"]) * 100, 2) if row[f"Previous {period_days_table} Days"] > 0 else None, axis=1
    )
    symptom_rank['Trend'] = symptom_rank['Symptom'].apply(
        lambda x: f"<span style='color:green'><b>Up</b></span>" if current_counts.get(x, 0) > previous_counts.get(x, 0) else (
            f"<span style='color:red'><b>Down</b></span>" if current_counts.get(x, 0) < previous_counts.get(x, 0) else f"<span style='color:gray'><b>Stable</b></span>"
        )
    )

    # Display Ranked Symptoms Table with Scrollable Option
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
    st.header("📝 Descriptions")
    descriptions = filtered_data_table['Description'].dropna().sort_values(ascending=False).reset_index(drop=True)
    page = st.number_input("Page", min_value=1, max_value=(len(descriptions) // 10) + 1, step=1)
    start_idx = (page - 1) * 10
    end_idx = start_idx + 10
    st.write("### Descriptions (Filtered)")
    for idx, desc in enumerate(descriptions[start_idx:end_idx], start=start_idx + 1):
        st.markdown(f"<div class='description-box'><strong>{idx}.</strong> {desc}</div>", unsafe_allow_html=True)

    # Add Download Option
    st.sidebar.download_button(
        label="Download Filtered Data",
        data=filtered_data_table.to_csv(index=False),
        file_name="filtered_jira_issues.csv",
        mime="text/csv"
    )
