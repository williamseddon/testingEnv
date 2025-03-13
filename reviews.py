import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

def load_data(file):
    df = pd.read_csv(file)
    return df

def main():
    st.set_page_config(page_title="SharkNinja Review Analysis", layout="wide")
    st.title("📊 SharkNinja Review Analysis Dashboard")
    
    # Increase file upload limit
    st.write("### Maximum File Upload Size: 1GB")
    st.warning("Larger files may take longer to process")
    
    uploaded_file = st.file_uploader("📂 Upload a CSV file", type=["csv"], accept_multiple_files=False)
    
    if uploaded_file is not None:
        df = load_data(uploaded_file)
        
        st.sidebar.header("🔍 Data Filters")
        product_filter = st.sidebar.multiselect("Select Products", options=df['Product name'].dropna().unique())
        product_id_filter = st.sidebar.multiselect("Select Product IDs", options=df['Product ID'].dropna().unique())
        rating_filter = st.sidebar.slider("Select Rating Range", min_value=1, max_value=5, value=(1,5))
        moderation_filter = st.sidebar.multiselect("Select Moderation Status", options=df['Moderation status'].dropna().unique())
        incentivized_filter = st.sidebar.radio("Incentivized Reviews", ["All", "Yes", "No"])
        verified_filter = st.sidebar.radio("Verified Purchasers", ["All", "Yes", "No"])
        review_length_filter = st.sidebar.slider("Filter by Review Length", min_value=0, max_value=1000, value=(0,1000))
        star_rating_filter = st.sidebar.multiselect("Filter by Star Rating", options=df['Rating'].dropna().unique())
        
        # Convert dates and add date filter
        df['Submission date'] = pd.to_datetime(df['Submission date'], errors='coerce')
        min_date, max_date = df['Submission date'].min(), df['Submission date'].max()
        date_filter = st.sidebar.date_input("Select Date Range", [min_date, max_date], min_value=min_date, max_value=max_date)
        
        # Apply filters
        if product_filter:
            df = df[df['Product name'].isin(product_filter)]
        if product_id_filter:
            df = df[df['Product ID'].isin(product_id_filter)]
        df = df[(df['Rating'] >= rating_filter[0]) & (df['Rating'] <= rating_filter[1])]
        if moderation_filter:
            df = df[df['Moderation status'].isin(moderation_filter)]
        if incentivized_filter == "Yes":
            df = df[df['Incentivized review'] == True]
        elif incentivized_filter == "No":
            df = df[df['Incentivized review'] == False]
        if verified_filter == "Yes":
            df = df[df['VerifiedPurchaser'] == True]
        elif verified_filter == "No":
            df = df[df['VerifiedPurchaser'] == False]
        df = df[(df['Submission date'] >= pd.to_datetime(date_filter[0])) & (df['Submission date'] <= pd.to_datetime(date_filter[1]))]
        df = df[df['Review text'].str.len().between(review_length_filter[0], review_length_filter[1], inclusive="both")]
        if star_rating_filter:
            df = df[df['Rating'].isin(star_rating_filter)]
        
        st.write("### 🔹 Data Preview:")
        st.dataframe(df.head(20))
        
        # Basic Metrics
        total_reviews = df.shape[0]
        avg_rating = df['Rating'].mean()
        verified_purchasers = df['VerifiedPurchaser'].sum()
        incentivized_reviews = df['Incentivized review'].sum()
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Reviews", total_reviews)
        col2.metric("Average Rating", round(avg_rating, 2))
        col3.metric("Verified Purchasers", verified_purchasers)
        col4.metric("Incentivized Reviews", incentivized_reviews)
        
        # Moderation Status Breakdown
        st.write("### 📌 Moderation Status Breakdown")
        mod_status_counts = df['Moderation status'].value_counts()
        st.bar_chart(mod_status_counts)
        
        # Average rating per product
        st.write("### ⭐ Average Rating Per Product")
        avg_rating_per_product = df.groupby('Product name')['Rating'].mean().sort_values()
        st.bar_chart(avg_rating_per_product)
        
        # Improved Rating Distribution using Matplotlib
        st.write("### 📊 Rating Distribution")
        fig, ax = plt.subplots(figsize=(8, 4))
        df['Rating'].hist(bins=5, edgecolor='black', alpha=0.7, color='blue')
        ax.set_xlabel("Rating")
        ax.set_ylabel("Count")
        ax.set_title("Distribution of Ratings")
        st.pyplot(fig)
        
        # Reviews per Submission Date
        st.write("### 📅 Reviews Over Time")
        reviews_per_date = df.groupby(df['Submission date'].dt.date).size()
        st.line_chart(reviews_per_date)
        
if __name__ == "__main__":
    main()



