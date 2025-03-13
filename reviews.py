import streamlit as st
import pandas as pd
import plotly.express as px
import matplotlib.pyplot as plt
from io import BytesIO

def load_data(file):
    df = pd.read_csv(file)
    return df

def download_csv(df):
    return df.to_csv(index=False).encode('utf-8')

def download_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Reviews')
    return output.getvalue()

def main():
    st.set_page_config(page_title="SharkNinja Review Analysis", layout="wide", initial_sidebar_state="expanded")
    
    st.markdown("<h1 style='text-align: center; color: #2E3B55;'>SharkNinja Review Analysis Dashboard</h1>", unsafe_allow_html=True)

    uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"], accept_multiple_files=False)
    
    if uploaded_file is not None:
        df = load_data(uploaded_file)

        st.sidebar.header("Data Filters")
        product_filter = st.sidebar.multiselect("Filter by Product ID", df['Product ID'].dropna().unique())
        category_filter = st.sidebar.multiselect("Filter by Category", df['Category name'].dropna().unique()) if 'Category name' in df.columns else []
        rating_filter = st.sidebar.slider("Filter by Rating", 1, 5, (1, 5))
        age_filter = st.sidebar.multiselect("Filter by Age Group", df['Age'].dropna().unique()) if 'Age' in df.columns else []
        gender_filter = st.sidebar.multiselect("Filter by Gender", df['Gender'].dropna().unique()) if 'Gender' in df.columns else []
        search_keyword = st.sidebar.text_input("Search in Reviews (Title & Text)")

        df['Submission date'] = pd.to_datetime(df['Submission date'], errors='coerce')
        min_date, max_date = df['Submission date'].min(), df['Submission date'].max()
        date_filter = st.sidebar.date_input("Select Date Range", [min_date, max_date], min_value=min_date, max_value=max_date)

        review_display_count = st.sidebar.selectbox("Number of Reviews to Display", [10, 20, 50, 100], index=1)
        sort_option = st.sidebar.selectbox("Sort Reviews By", ["Date", "Rating", "Review Length"], index=0)

        # Apply filters
        if product_filter:
            df = df[df['Product ID'].isin(product_filter)]
        if category_filter:
            df = df[df['Category name'].isin(category_filter)]
        if age_filter:
            df = df[df['Age'].isin(age_filter)]
        if gender_filter:
            df = df[df['Gender'].isin(gender_filter)]
        df = df[(df['Rating'] >= rating_filter[0]) & (df['Rating'] <= rating_filter[1])]
        if search_keyword:
            df = df[df['Review title'].str.contains(search_keyword, case=False, na=False) | df['Review text'].str.contains(search_keyword, case=False, na=False)]
        df = df[(df['Submission date'] >= pd.to_datetime(date_filter[0])) & (df['Submission date'] <= pd.to_datetime(date_filter[1]))]

        if sort_option == "Date":
            df = df.sort_values(by=['Submission date'], ascending=False)
        elif sort_option == "Rating":
            df = df.sort_values(by=['Rating'], ascending=False)
        elif sort_option == "Review Length":
            df = df.sort_values(by=['Review text'], key=lambda x: x.str.len(), ascending=False)

        st.write("### Data Overview")
        st.dataframe(df.head(review_display_count))

        total_reviews = df.shape[0]
        avg_rating = df['Rating'].mean()

        col1, col2 = st.columns(2)
        col1.metric("Total Reviews", total_reviews)
        col2.metric("Average Rating", round(avg_rating, 2))

        st.markdown("<h2 style='color: #2E3B55;'>Average Rating Per Product ID</h2>", unsafe_allow_html=True)
        avg_rating_per_product = df.groupby('Product ID')[['Rating']].mean().reset_index()
        fig = px.bar(avg_rating_per_product, x='Product ID', y='Rating', title="Average Rating Per Product ID")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("<h2 style='color: #2E3B55;'>Rating Distribution</h2>", unsafe_allow_html=True)
        fig = px.histogram(df, x='Rating', nbins=5, title="Distribution of Ratings")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("<h2 style='color: #2E3B55;'>Reviews Over Time</h2>", unsafe_allow_html=True)
        reviews_per_date = df.groupby(df['Submission date'].dt.date).size().reset_index()
        reviews_per_date.columns = ['Date', 'Count']
        fig = px.line(reviews_per_date, x='Date', y='Count', title="Reviews Over Time")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("<h2 style='color: #2E3B55;'>Customer Reviews</h2>", unsafe_allow_html=True)
        for _, row in df.head(review_display_count).iterrows():
            with st.container():
                st.markdown(f"**{row['Review title'] if pd.notna(row['Review title']) else 'No Title'}** ({'⭐' * int(row['Rating'])})")
                st.markdown(f"Date: {row['Submission date'].strftime('%Y-%m-%d')} | Product ID: {row['Product ID']} | Incentivized: {'Yes' if row.get('Incentivized review', False) else 'No'}")
                st.markdown(f"Gender: {row.get('Gender', 'Unknown')} | Age: {row.get('Age', 'Unknown')} | Category: {row.get('Category name', 'Unknown')}")
                st.write(row['Review text'] if pd.notna(row['Review text']) else "No review text available.")
                st.write("---")

        st.write("### Download Filtered Data")
        st.download_button("Download CSV", download_csv(df), "filtered_reviews.csv", "text/csv")
        st.download_button("Download Excel", download_excel(df), "filtered_reviews.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    main()





