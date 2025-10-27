import streamlit as st
import pandas as pd
import openai
import os
import numpy as np

# Set page title and layout
st.set_page_config(page_title="Star Walk Analysis Dashboard", layout="wide")

# Inject global CSS for Helvetica font and force light mode
st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important;
    }
    html {
        color-scheme: light !important;
    }
    </style>
    """, unsafe_allow_html=True)

# Title of the app
st.title("Star Walk Analysis Dashboard")

# Load data (only once, using session state to avoid reloading on every run)
if 'data_loaded' not in st.session_state:
    try:
        # Read the Excel file and required sheets
        main_df = pd.read_excel("Shark HD600 Valentino Starwalk (NEW).xlsx", sheet_name="Star Walk scrubbed verbatims")
        symptoms_df = pd.read_excel("Shark HD600 Valentino Starwalk (NEW).xlsx", sheet_name="Symptoms")
    except Exception as e:
        st.error(f"Error loading Excel file: {e}")
        st.stop()
    # Compile official symptoms list from 'Symptoms' sheet (both Detractors and Delighters)
    official_symptoms = []
    if 'Detractors' in symptoms_df.columns:
        official_symptoms += [str(x).strip() for x in symptoms_df['Detractors'].dropna().tolist()]
    if 'Delighters' in symptoms_df.columns:
        official_symptoms += [str(x).strip() for x in symptoms_df['Delighters'].dropna().tolist()]
    # Remove duplicates while preserving order
    seen = set()
    official_symptoms_unique = []
    for sym in official_symptoms:
        if sym and sym not in seen:
            seen.add(sym)
            official_symptoms_unique.append(sym)
    # Store dataframes and list in session state
    st.session_state['main_df'] = main_df
    st.session_state['official_symptoms'] = official_symptoms_unique
    st.session_state['data_loaded'] = True
else:
    main_df = st.session_state['main_df']
    official_symptoms_unique = st.session_state['official_symptoms']

# Identify reviews where all 20 symptom columns are blank or NaN
symptom_cols = [f"Symptom {i}" for i in range(1, 21)]
missing_mask = main_df[symptom_cols].isna().all(axis=1)
missing_indices = main_df.index[missing_mask].tolist()
missing_count = len(missing_indices)

# Display the count of reviews without symptoms
st.write(f"{missing_count} reviews without symptoms detected.")

if missing_count > 0:
    # Obtain OpenAI API key (from environment or user input)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        openai_api_key = st.text_input("Enter OpenAI API Key", type="password")
    # Button to trigger AI symptom extraction
    if st.button("Auto-symptomize missing reviews with OpenAI"):
        if not openai_api_key:
            st.error("Please provide an OpenAI API key to continue.")
        else:
            openai.api_key = openai_api_key
            suggestions = {}
            with st.spinner("Analyzing reviews for symptoms..."):
                for idx in missing_indices:
                    review_text = str(main_df.at[idx, 'Verbatim'])
                    # Construct prompt with the official symptoms list
                    symptom_list_str = "; ".join(official_symptoms_unique)
                    prompt = (
                        "You are an AI assistant that extracts product feedback symptoms from reviews.\n"
                        "We have an official list of known symptoms (issues or highlights) for the product. The list of official symptoms is:\n"
                        f"{symptom_list_str}\n\n"
                        "Based on the following review, identify up to 10 symptoms from the official list that are relevant. "
                        "If the review mentions any issue or benefit that is not in the official list, include it as well marked as 'new'.\n"
                        f"Review:\n\"{review_text}\"\n\n"
                        "Provide the symptoms as a comma-separated list."
                    )
                    try:
                        response = openai.ChatCompletion.create(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0
                        )
                        content = response["choices"][0]["message"]["content"].strip()
                    except Exception as e:
                        content = ""
                        st.error(f"OpenAI API call failed for a review: {e}")
                    # Parse the AI response to extract symptoms
                    extracted = []
                    if content:
                        # Replace newlines with commas for uniform splitting
                        text = content.replace("\n", ", ")
                        parts = [p.strip() for p in text.split(",") if p.strip()]
                        for term in parts:
                            # Clean up each term (remove bullets/numbers and "(new)" marker)
                            cleaned = term.lstrip("-*•0123456789. ").strip()
                            if cleaned.lower().endswith("(new)"):
                                cleaned = cleaned[:cleaned.lower().rfind("(new)")].strip()
                            if cleaned:
                                # Match to official list (case-insensitive) to preserve official wording
                                match = next((off for off in official_symptoms_unique if off.lower() == cleaned.lower()), None)
                                extracted.append(match if match else cleaned)
                    # Deduplicate extracted terms (case-insensitive) while preserving order
                    seen_terms = set()
                    final_terms = []
                    for term in extracted:
                        t_low = term.lower()
                        if t_low not in seen_terms:
                            seen_terms.add(t_low)
                            final_terms.append(term)
                    suggestions[idx] = final_terms
            # Store suggestions in session state for display
            st.session_state['suggestions'] = suggestions
            st.success("AI-generated symptom suggestions are ready below. Please review and adjust if necessary.")
    
    # If suggestions have been generated, display each review with its suggested symptoms
    if 'suggestions' in st.session_state:
        suggestions = st.session_state['suggestions']
        for idx in missing_indices:
            review_text = str(main_df.at[idx, 'Verbatim'])
            suggested_terms = suggestions.get(idx, [])
            # Prepare options for multiselect: all official symptoms plus any new suggestions
            options = list(official_symptoms_unique)
            options.sort(key=lambda x: x.lower())  # sort alphabetically for convenience
            default_selection = []
            for term in suggested_terms:
                # Check if term is an official symptom (case-insensitive match)
                if term.lower() in (off.lower() for off in official_symptoms_unique):
                    # Use official term with correct casing
                    match_off = next((off for off in official_symptoms_unique if off.lower() == term.lower()), term)
                    default_selection.append(match_off)
                else:
                    # Term is not in official list (new) – mark it and include in options
                    new_label = f"{term} (new)"
                    if new_label not in options:
                        options.append(new_label)
                    default_selection.append(new_label)
            # Display the review text
            st.write(f"**Review (ID {idx})**:")
            st.markdown(f"> {review_text}")
            # Display multiselect for the symptoms, pre-populated with suggestions
            st.multiselect(
                "Symptoms:", options=options, default=default_selection,
                key=f"symptoms_select_{idx}"
            )
        # Button to apply updates to the DataFrame
        if st.button("Update Data with new symptoms"):
            updated_count = 0
            for idx in missing_indices:
                selected = st.session_state.get(f"symptoms_select_{idx}", [])
                # Build final list of symptoms from the selection (remove " (new)" tags)
                final_list = []
                for term in selected:
                    term = str(term).strip()
                    if term.lower().endswith("(new)"):
                        term = term[:term.lower().rfind("(new)")].strip()
                    final_list.append(term)
                # Pad the list with NaN up to 20 columns
                final_list_padded = final_list[:20] + [np.nan] * (20 - len(final_list))
                # Update the DataFrame row with the final symptoms
                main_df.loc[idx, symptom_cols] = final_list_padded
                updated_count += 1
            # Save updated DataFrame back to session state
            st.session_state['main_df'] = main_df
            # Clear suggestions from session state (optional, to reset the UI)
            if 'suggestions' in st.session_state:
                del st.session_state['suggestions']
            # Show summary message
            st.success(f"Updated {updated_count} reviews with new symptoms.")
else:
    # If no reviews are missing symptoms
    st.info("All reviews already have symptoms. No action needed.")

