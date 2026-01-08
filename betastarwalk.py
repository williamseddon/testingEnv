import streamlit as st
import pandas as pd
import numpy as np
import openai
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import re
from io import BytesIO
from datetime import datetime
import os

st.set_page_config(page_title="Review Analysis Dashboard", page_icon="⭐", layout="wide")

st.title("Review Analysis Dashboard")
st.markdown("Analyze customer reviews with AI-generated insights and interactive visuals.")

# Sidebar controls for model and API key
openai_model = st.sidebar.selectbox("OpenAI Model", ["gpt-3.5-turbo", "gpt-4"], index=1)
openai_api_input = st.sidebar.text_input("OpenAI API Key", type="password")
if openai_api_input:
    openai.api_key = openai_api_input
elif "OPENAI_API_KEY" in st.secrets:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
elif "OPENAI_API_KEY" in os.environ:
    openai.api_key = os.environ["OPENAI_API_KEY"]

uploaded_file = st.file_uploader("Upload review data (Excel or CSV)", type=["xlsx", "csv"])
if uploaded_file:
    # Read data
    try:
        if uploaded_file.name.endswith(".xlsx"):
            # If multiple sheets, try to find the one containing "Verbatim"
            xls = pd.ExcelFile(uploaded_file)
            sheet_to_use = None
            for sheet in xls.sheet_names:
                sample = pd.read_excel(xls, sheet_name=sheet, nrows=1)
                if any(col for col in sample.columns if str(col).strip().lower().startswith("verbatim")):
                    sheet_to_use = sheet
                    break
            df = pd.read_excel(uploaded_file, sheet_name=sheet_to_use or 0)
        else:
            df = pd.read_csv(uploaded_file)
    except Exception as e:
        st.error(f"Error reading file: {e}")
        st.stop()
    # Basic cleaning and type conversions
    # Drop completely empty columns (often unnamed extra columns in Excel)
    df = df.loc[:, ~df.columns.astype(str).str.lower().str.startswith("unnamed")]
    # Convert dates
    date_col_candidates = [col for col in df.columns if "date" in str(col).lower()]
    if date_col_candidates:
        # Assume first date-like column is review date
        date_col = date_col_candidates[0]
        try:
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        except Exception:
            pass
    else:
        date_col = None
    # Identify necessary columns
    rating_col = None
    for col in df.columns:
        lc = str(col).lower()
        if "star" in lc and "rating" in lc:
            rating_col = col
            break
        if lc.startswith("rating"):
            rating_col = col
            break
    if rating_col is None:
        st.error("No rating column found in data.")
        st.stop()
    df[rating_col] = pd.to_numeric(df[rating_col], errors='coerce')
    # Total and average ratings
    total_reviews = len(df)
    avg_rating = df[rating_col].mean()
    # Distribution of star ratings
    star_counts = df[rating_col].value_counts().sort_index()
    star_dist = {int(k): v/total_reviews*100 for k, v in star_counts.items()} if total_reviews > 0 else {}
    # Identify symptom/category columns
    symptom_cols = [col for col in df.columns if str(col).strip().lower().startswith("symptom")]
    # Flatten all symptoms mentioned
    all_symptoms = []
    for col in symptom_cols:
        valid_vals = df[col].dropna()
        all_symptoms.extend([str(val).strip() for val in valid_vals if str(val).strip() != "" and str(val).strip() != 'nan'])
    from collections import Counter
    symptom_counter = Counter(all_symptoms)
    if symptom_counter:
        most_common_symptom, most_common_count = symptom_counter.most_common(1)[0]
    else:
        most_common_symptom, most_common_count = None, 0
    # Determine category type (detractor or delighter) based on first occurrence column index
    cat_min_col = {}
    for col in symptom_cols:
        m = re.findall(r'\d+', str(col))
        col_index = int(m[0]) if m else None
        for val in df[col].dropna().unique():
            if val is np.nan or str(val).strip() == "":
                continue
            if col_index is not None:
                if val not in cat_min_col or col_index < cat_min_col[val]:
                    cat_min_col[val] = col_index
    category_type = {}
    for cat, idx in cat_min_col.items():
        category_type[cat] = 'neg' if idx <= 10 else 'pos'
    for cat in symptom_counter:
        if cat not in category_type:
            category_type[cat] = 'pos'
    # Get top categories for positives and negatives
    most_common = symptom_counter.most_common()
    top_detractors = [cat for cat, cnt in most_common if category_type.get(cat) == 'neg'][:5]
    top_delighters = [cat for cat, cnt in most_common if category_type.get(cat) == 'pos'][:5]
    # Pick representative quotes for top categories
    cat_to_quote = {}
    for cat_list, ctype in [(top_detractors, 'neg'), (top_delighters, 'pos')]:
        for cat in cat_list:
            indices = set()
            for col in symptom_cols:
                indices.update(df.index[df[col] == cat].tolist())
            if not indices:
                continue
            chosen_idx = None
            if ctype == 'neg':
                cat_reviews = df.loc[list(indices)]
                if not cat_reviews.empty:
                    min_rating = cat_reviews[rating_col].min()
                    low_reviews = cat_reviews[cat_reviews[rating_col] == min_rating]
                    if not low_reviews.empty:
                        chosen_idx = low_reviews.index[0]
            else:
                cat_reviews = df.loc[list(indices)]
                if not cat_reviews.empty:
                    max_rating = cat_reviews[rating_col].max()
                    high_reviews = cat_reviews[cat_reviews[rating_col] == max_rating]
                    if not high_reviews.empty:
                        chosen_idx = high_reviews.index[0]
            if chosen_idx is None:
                chosen_idx = list(indices)[0]
            # If chosen review text doesn't mention the category, try to find one that does
            chosen_text = str(df.at[chosen_idx, df.columns[df.columns.str.lower().str.contains("verbatim")][0]]) if any(df.columns.str.lower().str.contains("verbatim")) else str(df.at[chosen_idx, df.columns[0]])
            if cat.lower() not in chosen_text.lower():
                for alt_idx in indices:
                    alt_text = str(df.at[alt_idx, df.columns[df.columns.str.lower().str.contains("verbatim")][0]]) if any(df.columns.str.lower().str.contains("verbatim")) else str(df.at[alt_idx, df.columns[0]])
                    if cat.lower() in alt_text.lower():
                        chosen_idx = alt_idx
                        chosen_text = alt_text
                        break
            review_text = chosen_text
            source = None
            source_cols = [col for col in df.columns if "source" in str(col).lower() or "retailer" in str(col).lower()]
            if source_cols:
                source = str(df.at[chosen_idx, source_cols[0]])
            rating_val = df.at[chosen_idx, rating_col]
            date_val = None
            if date_col:
                date_val = df.at[chosen_idx, date_col]
            # Extract a relevant sentence
            snippet = ""
            if isinstance(review_text, str):
                sentences = re.split(r'(?<=[.!?\n]) +', review_text)
                found_sent = None
                for sent in sentences:
                    if cat.lower() in sent.lower():
                        found_sent = sent.strip()
                        break
                if not found_sent:
                    if ctype == 'neg':
                        neg_cues = ["not ", "n't", "but", "however", "disappoint", "unfortunately", "too "]
                        for sent in sentences:
                            s_lower = sent.lower()
                            if any(cue in s_lower for cue in neg_cues):
                                found_sent = sent.strip()
                                break
                        if not found_sent and sentences:
                            found_sent = sentences[-1].strip()
                    else:
                        pos_cues = ["love", "great", "amazing", "awesome", "perfect", "easy to use", "best"]
                        for sent in sentences:
                            s_lower = sent.lower()
                            if any(cue in s_lower for cue in pos_cues):
                                found_sent = sent.strip()
                                break
                        if not found_sent and sentences:
                            found_sent = sentences[0].strip()
                snippet = found_sent if found_sent else review_text.strip()
                if len(snippet) > 250:
                    cut_idx = snippet.rfind(' ', 0, 250)
                    snippet = snippet[:cut_idx] + "..." if cut_idx != -1 else snippet[:250] + "..."
            else:
                snippet = str(review_text)
            star_str = f"{int(rating_val)}★" if not np.isnan(rating_val) else ""
            source_str = f"{source}" if source else "reviewer"
            date_str = ""
            if isinstance(date_val, (pd.Timestamp, datetime)):
                date_str = date_val.strftime("%b %Y")
            attribution = ""
            if star_str:
                attribution += f"{star_str} "
            attribution += f"{source_str}"
            if date_str:
                attribution += f", {date_str}"
            quote_text = snippet.replace('"', "'")
            full_quote = f"\"{quote_text}\" - {attribution}"
            cat_to_quote[cat] = full_quote
    # Detect trend spikes
    trending_info = []
    if date_col:
        df_sorted = df.dropna(subset=[date_col]).sort_values(by=date_col)
        if not df_sorted.empty:
            last_date = df_sorted[date_col].max()
            first_date = df_sorted[date_col].min()
        else:
            last_date = first_date = None
        if last_date is not None and first_date is not None and last_date != first_date:
            last_month = pd.Period(last_date, freq='M')
            prev_month = last_month - 1
            last_month_reviews = df_sorted[df_sorted[date_col].dt.to_period('M') == last_month]
            prev_month_reviews = df_sorted[df_sorted[date_col].dt.to_period('M') == prev_month]
            vol_last = len(last_month_reviews)
            vol_prev = len(prev_month_reviews)
            if vol_prev > 0:
                vol_change_pct = (vol_last - vol_prev) / vol_prev * 100
            else:
                vol_change_pct = None
            if vol_prev > 0 and vol_last > vol_prev and vol_change_pct is not None and vol_change_pct >= 50 and (vol_last - vol_prev) >= 5:
                trending_info.append(f"Review volume increased from {vol_prev} to {vol_last} reviews last month (↑{vol_change_pct:.0f}%).")
            elif vol_prev == 0 and vol_last >= 5:
                trending_info.append(f"Review volume jumped to {vol_last} in {last_month.strftime('%b %Y')} (from 0 previous month).")
            # Category spikes
            last_syms = []
            prev_syms = []
            for col in symptom_cols:
                last_syms.extend([str(val).strip() for val in last_month_reviews[col].dropna() if str(val).strip() not in ["", "nan"]])
                prev_syms.extend([str(val).strip() for val in prev_month_reviews[col].dropna() if str(val).strip() not in ["", "nan"]])
            last_count = Counter(last_syms)
            prev_count = Counter(prev_syms)
            for cat, cnt in last_count.items():
                prev_cnt = prev_count.get(cat, 0)
                if prev_cnt == 0 and cnt >= 3:
                    trending_info.append(f"New issue **{cat}** emerged with {cnt} mentions last month (previously none).")
                elif prev_cnt > 0:
                    increase = cnt - prev_cnt
                    increase_pct = (increase / prev_cnt * 100) if prev_cnt > 0 else 0
                    if increase > 0 and increase_pct >= 50 and increase >= 3:
                        trending_info.append(f"Mentions of **{cat}** spiked from {prev_cnt} to {cnt} last month (↑{increase_pct:.0f}%).")
    # Summary cards
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Reviews", f"{total_reviews}")
    avg_display = f"{avg_rating:.2f}/5" if not np.isnan(avg_rating) else "N/A"
    col2.metric("Avg Rating", avg_display)
    if most_common_symptom:
        col3.metric("Top Symptom", f"{most_common_symptom}"[:30] + ("..." if len(str(most_common_symptom)) > 30 else ""))
    else:
        col3.metric("Top Symptom", "N/A")
    if trending_info:
        trend_lines = "\n".join([f"- {t}" for t in trending_info])
        st.warning(f"**Notable Trends:**\n{trend_lines}")
    # AI summary generation
    ai_summary = None
    if openai.api_key:
        cache_key = f"summary_{uploaded_file.name}_{openai_model}"
        if cache_key in st.session_state:
            ai_summary = st.session_state[cache_key]
        else:
            overview_stats = f"Total reviews: {total_reviews}; Average rating: {avg_rating:.1f}/5."
            if star_dist:
                dist_text = " ".join([f"{star}★: {perc:.0f}%," for star, perc in sorted(star_dist.items())]).rstrip(",")
                overview_stats += f" Rating distribution: {dist_text}."
            strengths = ", ".join([f"{cat} ({symptom_counter[cat]} mentions)" for cat in top_delighters]) or "None"
            weaknesses = ", ".join([f"{cat} ({symptom_counter[cat]} mentions)" for cat in top_detractors]) or "None"
            summary_prompt = (
                f"Overview:\n{overview_stats}\n"
                f"Top strengths (delighters): {strengths}\n"
                f"Top weaknesses (detractors): {weaknesses}\n\n"
                "Using the above information, write an overall summary of the product reviews, highlighting the general customer sentiment, key positive aspects, and key negative aspects. "
                "Use clear, structured formatting (for example, use bold for metrics or aspect names) and include quantitative metrics (counts or percentages) where relevant. "
                "Incorporate a brief direct quote from a review if appropriate to illustrate a point (with a citation of the source or rating)."
            )
            try:
                response = openai.ChatCompletion.create(
                    model=openai_model,
                    messages=[
                        {"role": "system", "content": "You are an AI assistant analyzing consumer product reviews. Provide a concise, insightful summary using the provided data. Do not speculate beyond the data."},
                        {"role": "user", "content": summary_prompt}
                    ],
                    temperature=0
                )
                ai_summary = response['choices'][0]['message']['content']
            except Exception as e:
                st.error(f"OpenAI API error during summary: {e}")
                ai_summary = None
            if ai_summary:
                st.session_state[cache_key] = ai_summary
    else:
        st.info("💡 Enter your OpenAI API key to generate AI insights.")
    if ai_summary:
        st.subheader("AI-Generated Product Summary")
        st.markdown(ai_summary)
    # Interactive Q&A
    if openai.api_key:
        st.subheader("AI Insights Assistant")
        st.markdown("Ask specific questions or use the preset insight buttons:")
        # Layout for preset questions
        preset1, preset2, preset3 = st.columns(3)
        preset4, preset5, _ = st.columns([1, 1, 1])
        user_question = st.text_input("Custom question:")
        ask_button = st.button("Ask")
        if 'qa_history' not in st.session_state:
            st.session_state.qa_history = {}
        prev_model = st.session_state.get('qa_model')
        prev_file = st.session_state.get('qa_file')
        if (prev_model and prev_model != openai_model) or (prev_file and prev_file != uploaded_file.name):
            st.session_state.qa_history.clear()
        st.session_state['qa_model'] = openai_model
        st.session_state['qa_file'] = uploaded_file.name
        def generate_insight_answer(question_key, context_data, instruction):
            try:
                messages = [
                    {"role": "system", "content": "You are an AI assistant analyzing product review data. Use only the provided data to answer, and provide evidence."},
                    {"role": "user", "content": context_data + "\n" + instruction}
                ]
                resp = openai.ChatCompletion.create(model=openai_model, messages=messages, temperature=0)
                answer = resp['choices'][0]['message']['content']
            except Exception as e:
                answer = f"*(Error generating answer: {e})*"
            st.session_state.qa_history[question_key] = answer
        if preset1.button("What are top delighters?"):
            if top_delighters:
                data_str = "Top Delighters:\n"
                for cat in top_delighters:
                    count = symptom_counter[cat]
                    perc = (count / total_reviews) * 100 if total_reviews else 0
                    quote = cat_to_quote.get(cat, "")
                    data_str += f"- {cat}: mentioned by {count} reviews ({perc:.1f}% of reviews). Example quote: {quote}\n"
                instr = "Identify the top aspects customers loved about the product (the 'delighters') using the above data. Format the answer as bullet points. Each bullet should start with the aspect in **bold**, include how many or what percentage of reviewers mentioned it, and incorporate the provided quote as supporting evidence (with attribution)."
            else:
                data_str = "- (No positive aspects were frequently mentioned by customers.)"
                instr = "Customers did not specifically highlight any top positive aspects in their reviews."
            generate_insight_answer("What are top delighters?", data_str, instr)
        if preset2.button("What are biggest detractors?"):
            if top_detractors:
                data_str = "Top Detractors:\n"
                for cat in top_detractors:
                    count = symptom_counter[cat]
                    perc = (count / total_reviews) * 100 if total_reviews else 0
                    quote = cat_to_quote.get(cat, "")
                    data_str += f"- {cat}: mentioned by {count} reviews ({perc:.1f}% of reviews). Example quote: {quote}\n"
                instr = "Identify the biggest detractors (the most common complaints or negatives) from the reviews, based on the above data. Provide the answer in bullet points, with each issue in **bold**, stating how many or what percentage of reviewers mentioned it, and including the example quote as evidence (with attribution)."
            else:
                data_str = "- (No significant negative aspects were mentioned by customers.)"
                instr = "Customers did not express any notable complaints or negative feedback about the product."
            generate_insight_answer("What are biggest detractors?", data_str, instr)
        if preset3.button("What are new trends or watch-outs?"):
            if trending_info:
                data_str = "Recent Trends:\n"
                for t in trending_info:
                    data_str += f"- {t}\n"
                trend_cat = None
                match = re.search(r"\*\*(.+?)\*\*", " ".join(trending_info))
                if match:
                    trend_cat = match.group(1)
                if trend_cat and trend_cat in cat_to_quote:
                    data_str += f'Example recent review about {trend_cat}: {cat_to_quote[trend_cat]}\n'
                instr = "Based on the above recent trends in customer feedback, highlight any new or significantly increased issues (or positive trends) that have emerged. Answer in bullet points, including details of the spike and the provided example quote if applicable."
            else:
                data_str = "- No notable new trends or spikes were observed in recent reviews."
                instr = "There were no significant new trends or watch-outs in the recent customer feedback."
            generate_insight_answer("What are new trends or watch-outs?", data_str, instr)
        if preset4.button("What are customers saying?"):
            pos_list = top_delighters[:3]
            neg_list = top_detractors[:3]
            data_str = "Customer Feedback Summary:\n"
            pos_str = ", ".join([f"{cat} ({(symptom_counter[cat] / total_reviews * 100 if total_reviews else 0):.0f}%)" for cat in pos_list]) if pos_list else "none"
            neg_str = ", ".join([f"{cat} ({(symptom_counter[cat] / total_reviews * 100 if total_reviews else 0):.0f}%)" for cat in neg_list]) if neg_list else "none"
            data_str += f"- Positive aspects mentioned: {pos_str}\n"
            data_str += f"- Negative aspects mentioned: {neg_str}\n"
            if pos_list:
                ex_pos = pos_list[0]
                if ex_pos in cat_to_quote:
                    data_str += f'Example positive quote: {cat_to_quote[ex_pos]}\n'
            if neg_list:
                ex_neg = neg_list[0]
                if ex_neg in cat_to_quote:
                    data_str += f'Example negative quote: {cat_to_quote[ex_neg]}\n'
            instr = "Summarize what customers are saying about the product overall, using the above information about positive and negative themes. The answer should address both what customers like and what they dislike about the product. Use a clear format (bullet points or short paragraphs) and include the example quotes provided (with attribution) to illustrate their sentiments."
            generate_insight_answer("What are customers saying?", data_str, instr)
        if preset5.button("What could be improved?"):
            if top_detractors:
                data_str = "Areas for Improvement:\n"
                for cat in top_detractors:
                    count = symptom_counter[cat]
                    perc = (count / total_reviews) * 100 if total_reviews else 0
                    quote = cat_to_quote.get(cat, "")
                    data_str += f"- {cat}: mentioned by {count} reviews ({perc:.1f}% of reviews). Example quote: {quote}\n"
                instr = "Based on the above data, what do customers think could be improved about the product? Answer in bullet points, phrasing each as an improvement or suggestion. Start each bullet with the issue in **bold**, mention how many or what percentage of reviewers raised it, and include the example quote (with attribution) to illustrate the feedback."
            else:
                data_str = "- (Customers did not suggest specific improvements.)"
                instr = "Customers did not explicitly suggest any improvements for the product."
            generate_insight_answer("What could be improved?", data_str, instr)
        if ask_button:
            if user_question and user_question.strip():
                context_segments = []
                context_segments.append(f"Total reviews: {total_reviews}; Avg rating: {avg_rating:.1f}/5")
                if star_dist:
                    dist_summary = ", ".join([f"{star}★: {perc:.0f}%" for star, perc in sorted(star_dist.items())])
                    context_segments.append(f"Rating distribution: {dist_summary}")
                if top_delighters:
                    pos_summary = ", ".join([f"{cat} ({symptom_counter[cat]})" for cat in top_delighters])
                    context_segments.append(f"Top positive aspects: {pos_summary}")
                if top_detractors:
                    neg_summary = ", ".join([f"{cat} ({symptom_counter[cat]})" for cat in top_detractors])
                    context_segments.append(f"Top negative aspects: {neg_summary}")
                if trending_info:
                    trends_summary = " | ".join(trending_info)
                    context_segments.append(f"Recent trends: {trends_summary}")
                if any(word in user_question.lower() for word in ["country", "region", "retailer", "source", "amazon", "sephora", "us ", "uk", "europe", "asia", "market"]):
                    try:
                        geo_col = None
                        src_col = None
                        for col in df.columns:
                            cl = str(col).lower()
                            if not geo_col and ("country" in cl or "region" in cl):
                                geo_col = col
                            if not src_col and ("source" in cl or "retailer" in cl):
                                src_col = col
                        if geo_col and src_col:
                            pivot = df.groupby([df[geo_col], df[src_col]])[rating_col].agg(['count', 'mean']).reset_index()
                            region_info = []
                            for region in pivot[geo_col].unique():
                                region_data = pivot[pivot[geo_col] == region]
                                parts = []
                                for _, row in region_data.iterrows():
                                    src = row[src_col]
                                    cnt = int(row['count'])
                                    avg = row['mean']
                                    parts.append(f"{src}: {cnt} reviews (avg {avg:.1f})")
                                region_info.append(f"{region} -> " + "; ".join(parts))
                            if region_info:
                                context_segments.append("Regional/Retailer breakdown: " + " | ".join(region_info))
                    except Exception:
                        pass
                context_data = "\n".join(context_segments)
                instr = f"Answer the question: {user_question}\nUse only the above data to support your answer."
                generate_insight_answer(user_question, context_data, instr)
            else:
                st.error("Please enter a question before clicking Ask.")
        # Display Q&A results
        for q, ans in st.session_state.qa_history.items():
            st.markdown(f"**Q: {q}**")
            st.markdown(ans)
            st.divider()
    # Visualizations
    st.subheader("Review Trends Over Time")
    if date_col:
        timeline = df.dropna(subset=[date_col]).copy()
        if not timeline.empty:
            timeline.set_index(date_col, inplace=True)
            timeline = timeline.resample('M')[rating_col].agg(['count', 'mean'])
            timeline = timeline.dropna(subset=['count'])
        else:
            timeline = pd.DataFrame(columns=['count', 'mean'])
    else:
        timeline = pd.DataFrame(columns=['count', 'mean'])
    if not timeline.empty:
        timeline.index = timeline.index.to_timestamp() if isinstance(timeline.index, pd.PeriodIndex) else timeline.index
        fig_timeline = make_subplots(specs=[[{"secondary_y": True}]])
        fig_timeline.add_trace(go.Bar(x=timeline.index, y=timeline['count'], name="Review Count", marker_color='rgba(100,150,250,0.6)'), secondary_y=False)
        fig_timeline.add_trace(go.Scatter(x=timeline.index, y=timeline['mean'], name="Avg Rating", mode='lines+markers', marker=dict(color='orange')), secondary_y=True)
        fig_timeline.update_yaxes(title_text="Review Count", secondary_y=False)
        fig_timeline.update_yaxes(title_text="Avg Rating (out of 5)", range=[1, 5], tickvals=[1, 2, 3, 4, 5], secondary_y=True)
        fig_timeline.update_xaxes(title_text="Month", tickformat="%b %Y", tickangle=-45)
        fig_timeline.update_layout(margin=dict(t=30, b=40, l=40, r=40), legend_title_text="", legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01))
        st.plotly_chart(fig_timeline, use_container_width=True)
    else:
        st.write("_No timeline available (missing or invalid date data)._")
    st.subheader("Top Delighters and Detractors")
    det_names = top_detractors
    det_counts = [symptom_counter[c] for c in det_names]
    del_names = top_delighters
    del_counts = [symptom_counter[c] for c in del_names]
    colA, colB = st.columns(2)
    with colA:
        st.markdown("**Top Detractors** (most mentioned negatives)")
        if det_names:
            n = len(det_names)
            fig_det = go.Figure(go.Bar(x=det_counts[::-1], y=det_names[::-1], orientation='h', marker_color='crimson', text=det_counts[::-1], textposition='auto'))
            fig_det.update_layout(margin=dict(t=30, b=30, l=150, r=30), xaxis_title="Mentions", yaxis_title="", yaxis=dict(autorange="reversed", automargin=True), height=max(400, 60 * n))
            fig_det.update_traces(marker=dict(opacity=0.8))
            st.plotly_chart(fig_det, use_container_width=True)
        else:
            st.write("_No detractor categories available._")
    with colB:
        st.markdown("**Top Delighters** (most mentioned positives)")
        if del_names:
            m = len(del_names)
            fig_del = go.Figure(go.Bar(x=del_counts[::-1], y=del_names[::-1], orientation='h', marker_color='seagreen', text=del_counts[::-1], textposition='auto'))
            fig_del.update_layout(margin=dict(t=30, b=30, l=150, r=30), xaxis_title="Mentions", yaxis_title="", yaxis=dict(autorange="reversed", automargin=True), height=max(400, 60 * m))
            fig_del.update_traces(marker=dict(opacity=0.8))
            st.plotly_chart(fig_del, use_container_width=True)
        else:
            st.write("_No delighter categories available._")
    if 'country' in df.columns.str.lower().tolist() and 'source' in df.columns.str.lower().tolist():
        st.subheader("Ratings by Region and Retailer")
        country_col = [col for col in df.columns if 'country' in str(col).lower()][0]
        source_col = [col for col in df.columns if 'source' in str(col).lower()][0]
        pivot_data = df.groupby([df[country_col], df[source_col]])[rating_col].agg(['count', 'mean']).reset_index()
        if not pivot_data.empty:
            pivot_table = pivot_data.pivot(index=country_col, columns=source_col, values=['count', 'mean'])
            flattened_cols = []
            for metric, src in pivot_table.columns:
                flattened_cols.append(f"{src} {'Count' if metric == 'count' else 'Avg Rating'}")
            pivot_table.columns = flattened_cols
            for col in pivot_table.columns:
                if col.endswith("Count"):
                    pivot_table[col] = pivot_table[col].fillna(0).astype(int)
                if col.endswith("Avg Rating"):
                    pivot_table[col] = pivot_table[col].round(1)
            st.dataframe(pivot_table, use_container_width=True)
        else:
            st.write("_No regional/retailer breakdown available._")
