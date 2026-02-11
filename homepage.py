import streamlit as st
from datetime import datetime
from typing import Optional, List, Tuple
import hashlib
import re
import io

# Data deps
import pandas as pd

# Optional retrieval dependency
SKLEARN_OK = True
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except Exception:
    SKLEARN_OK = False


# -----------------------------
# Config
# -----------------------------
APP_NAME = "Starwalk Hub"

REV_CONVERTER_URL = "https://starwalkconverter.streamlit.app/"
REV_VIEWER_URL = "https://starwalk.streamlit.app/"
REVIEW_PROMPT_URL = "https://reviewprompt.streamlit.app/"
CALL_PROMPT_URL = "https://callprompt.streamlit.app/"

st.set_page_config(page_title=APP_NAME, page_icon="🧭", layout="wide")


# -----------------------------
# Minimal UI polish
# -----------------------------
st.markdown(
    """
<style>
.block-container { padding-top: 1.1rem; padding-bottom: 2.2rem; max-width: 1200px; }
.small-muted { opacity: 0.75; font-size: 0.92rem; }
hr { margin: 1.0rem 0; opacity: 0.25; }
</style>
""",
    unsafe_allow_html=True,
)


# -----------------------------
# Helpers
# -----------------------------
def fingerprint_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def nice_container():
    """Use bordered containers when available; fallback cleanly."""
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


def tool_bar():
    """Top toolbar: quick launch to each external app."""
    with nice_container():
        c1, c2, c3, c4 = st.columns(4, gap="small")
        with c1:
            st.link_button("🧱 Convert reviews", REV_CONVERTER_URL, use_container_width=True)
        with c2:
            st.link_button("🔎 View reviews", REV_VIEWER_URL, use_container_width=True)
        with c3:
            st.link_button("📊 Review prompts", REVIEW_PROMPT_URL, use_container_width=True)
        with c4:
            st.link_button("🎧 Call prompts", CALL_PROMPT_URL, use_container_width=True)


def guess_id_column(cols: List[str]) -> Optional[str]:
    candidates = ["review_id", "reviewid", "id", "row_id", "rowid", "call_id", "ticket_id", "case_id"]
    lower_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def guess_text_columns(cols: List[str]) -> List[str]:
    text_keys = ["review", "comment", "text", "body", "feedback", "summary", "notes", "transcript"]
    hits = []
    for c in cols:
        cl = c.lower()
        if any(k in cl for k in text_keys):
            hits.append(c)
    return hits[:4]


def safe_str(x) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


def build_corpus(df: pd.DataFrame, text_cols: List[str]) -> List[str]:
    if not text_cols:
        return [""] * len(df)
    return (
        df[text_cols]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .tolist()
    )


@st.cache_data(show_spinner=False)
def read_tabular(file_bytes: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(file_bytes))
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(file_bytes))
    # fallback
    return pd.read_csv(io.BytesIO(file_bytes))


@st.cache_resource(show_spinner=False)
def build_tfidf_index(corpus: Tuple[str, ...]):
    vectorizer = TfidfVectorizer(stop_words="english", max_features=60000)
    X = vectorizer.fit_transform(list(corpus))
    return vectorizer, X


def retrieve_rows(df: pd.DataFrame, corpus: List[str], question: str, k: int = 25) -> Tuple[List[int], List[float], str]:
    q = (question or "").strip()
    if not q:
        return [], [], "none"

    if SKLEARN_OK and len(df) > 0:
        vec, X = build_tfidf_index(tuple(corpus))
        qv = vec.transform([q])
        sims = cosine_similarity(qv, X).flatten()
        top_idx = sims.argsort()[::-1][:k]
        return top_idx.tolist(), sims[top_idx].tolist(), "tfidf"

    # fallback: token overlap
    q_tokens = set(re.findall(r"[a-z0-9']+", q.lower()))
    scores = []
    for txt in corpus:
        tokens = re.findall(r"[a-z0-9']+", (txt or "").lower())
        if not tokens:
            scores.append(0.0)
            continue
        overlap = sum(1 for t in tokens if t in q_tokens)
        scores.append(float(overlap) / max(1.0, float(len(tokens))))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return ranked, [scores[i] for i in ranked], "token_overlap"


def make_evidence_block(df_evidence: pd.DataFrame, id_col: Optional[str], cols: List[str], max_chars: int = 420) -> str:
    lines = []
    for _, row in df_evidence.iterrows():
        rid = safe_str(row[id_col]) if id_col and id_col in df_evidence.columns else safe_str(row.name)
        parts = []
        for c in cols:
            if c not in df_evidence.columns:
                continue
            val = re.sub(r"\s+", " ", safe_str(row[c])).strip()
            if len(val) > max_chars:
                val = val[:max_chars] + "…"
            parts.append(f"{c}={val}")
        lines.append(f"- ROW {rid}: " + "; ".join(parts))
    return "\n".join(lines)


def call_llm_answer(
    api_key: str,
    base_url: Optional[str],
    model: str,
    question: str,
    df_evidence: pd.DataFrame,
    id_col: Optional[str],
    evidence_cols: List[str],
) -> str:
    try:
        from openai import OpenAI
    except Exception:
        raise RuntimeError("openai package not installed. Add: openai>=1.0.0")

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    evidence_text = make_evidence_block(df_evidence, id_col=id_col, cols=evidence_cols)

    system = (
        "You are a careful analyst answering questions about an uploaded dataset.\n"
        "RULES:\n"
        "1) Use ONLY the provided EVIDENCE ROWS.\n"
        "2) If evidence is insufficient, say exactly what is missing.\n"
        "3) Every claim must cite row IDs like [ROW 123].\n"
        "4) Do not invent numbers.\n"
        "5) Keep the answer structured and concise."
    )

    user = (
        f"QUESTION:\n{question}\n\n"
        f"EVIDENCE ROWS:\n{evidence_text}\n\n"
        "Return:\n"
        "- Direct answer\n"
        "- Supporting evidence bullets w/ row citations\n"
        "- Uncertainty/limits"
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


def review_prompt_template(file_ref: str, symptoms: List[str], notes: str) -> str:
    symptoms_line = ", ".join(symptoms) if symptoms else "«add symptoms»"
    file_line = file_ref.strip() if file_ref.strip() else "«converted file reference»"
    notes = notes.strip()
    notes_block = f"\n\nNotes:\n- {notes}" if notes else ""
    return f"""You are analyzing a converted web-review dataset produced by the Starwalk Converter.

Dataset reference: {file_line}

Quantify these custom symptoms:
- {symptoms_line}

For each symptom:
- count and % of total
- top 5 representative excerpts (with review ID if available)
- common co-occurring symptoms
- ambiguity callouts (e.g., “hot” vs “overheating”) and how you interpreted them

Output as: (1) a clean table, (2) short bullet summary.{notes_block}"""


def call_prompt_template(source_ref: str, questions: List[str], notes: str) -> str:
    source_ref = source_ref.strip() if source_ref.strip() else "«Zoom summary reference»"
    q_block = "\n".join([f"- {q}" for q in questions]) if questions else "- «add questions»"
    notes = notes.strip()
    notes_block = f"\n\nNotes:\n- {notes}" if notes else ""
    return f"""You are analyzing Zoom call summaries / transcript-derived notes.

Source reference: {source_ref}

Answer:
{q_block}

Rules:
- use only what is supported by the summaries
- include short supporting excerpts
- highlight uncertainty when summaries are vague
- end with a 1–2 line “so what” takeaway{notes_block}"""


# -----------------------------
# Header
# -----------------------------
st.title("🧭 Starwalk Hub")
st.caption("Inspect your reviews and calls data here.")
tool_bar()

tab_reviews, tab_calls, tab_ask, tab_about = st.tabs(["Reviews", "Calls", "Ask Data (Optional LLM)", "About"])


# -----------------------------
# Reviews Tab
# -----------------------------
with tab_reviews:
    st.subheader("Reviews workflow")
    st.markdown('<div class="small-muted">Convert → View → Quantify. Keep everyone on the same steps.</div>', unsafe_allow_html=True)
    st.write("")

    c1, c2, c3 = st.columns(3, gap="large")

    with c1:
        with nice_container():
            st.markdown("### 1) Convert")
            st.markdown('<div class="small-muted">Turn Axion / other exports into the converter-acceptable format.</div>', unsafe_allow_html=True)
            st.link_button("Open Starwalk Converter", REV_CONVERTER_URL, use_container_width=True)

    with c2:
        with nice_container():
            st.markdown("### 2) View")
            st.markdown('<div class="small-muted">Upload the converted file for enhanced browsing + filtering.</div>', unsafe_allow_html=True)
            st.link_button("Open Starwalk Viewer", REV_VIEWER_URL, use_container_width=True)

    with c3:
        with nice_container():
            st.markdown("### 3) Quantify")
            st.markdown('<div class="small-muted">Use structured prompts to quantify symptoms consistently.</div>', unsafe_allow_html=True)
            st.link_button("Open Review Prompt Tool", REVIEW_PROMPT_URL, use_container_width=True)

    st.write("")
    with nice_container():
        st.markdown("### Prompt builder")
        with st.form("review_prompt_form"):
            file_ref = st.text_input("Converted file reference", placeholder="e.g., 2026-02-10_Axion_HT300_converted.csv")
            symptoms = st.multiselect(
                "Symptoms to quantify",
                options=[
                    "Overheating", "Weak performance", "Noise", "Battery issues",
                    "Filter clogging / maintenance confusion", "Durability / breakage",
                    "Error lights / indicators", "Smell / burning odor",
                    "Poor instructions / setup friction", "Shipping / damaged on arrival",
                    "Comfort / ergonomics", "UI/controls confusion",
                ],
                default=["Overheating", "Weak performance", "Noise"],
            )
            notes = st.text_area("Optional notes", placeholder="e.g., Focus on detractors; break down by model if present.", height=90)
            submitted = st.form_submit_button("Generate prompt", use_container_width=True)

        prompt = review_prompt_template(file_ref, symptoms, notes)
        st.code(prompt, language="text")


# -----------------------------
# Calls Tab
# -----------------------------
with tab_calls:
    st.subheader("Calls workflow")
    st.markdown('<div class="small-muted">Launch the call prompt tool + keep question structure consistent.</div>', unsafe_allow_html=True)
    st.write("")

    with nice_container():
        st.markdown("### Call prompts")
        st.markdown('<div class="small-muted">This tool answers questions from Zoom summaries / transcript-derived notes.</div>', unsafe_allow_html=True)
        st.link_button("Open Call Prompt Tool", CALL_PROMPT_URL, use_container_width=True)

    st.write("")
    with nice_container():
        st.markdown("### Prompt builder")
        with st.form("call_prompt_form"):
            source_ref = st.text_input("Summary reference", placeholder="e.g., 2026-02_Wk2_ZoomSummaries_HT300.txt")
            questions_raw = st.text_area(
                "Questions (one per line)",
                value="What are the top 3 customer pain points?\nAny repeat issues across calls?\nWhat changed week-over-week?",
                height=120,
            )
            notes = st.text_area("Optional notes", placeholder="e.g., NA calls only; call out uncertainty explicitly.", height=90)
            submitted = st.form_submit_button("Generate prompt", use_container_width=True)

        questions = [q.strip() for q in questions_raw.splitlines() if q.strip()]
        prompt = call_prompt_template(source_ref, questions, notes)
        st.code(prompt, language="text")


# -----------------------------
# Ask Data Tab (cleaner, focused)
# -----------------------------
with tab_ask:
    st.subheader("Ask your uploaded file")
    st.markdown(
        '<div class="small-muted">Evidence-first Q&A: retrieve relevant rows → answer only from those rows → cite row IDs.</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    left, right = st.columns([1.05, 1.0], gap="large")

    with left:
        with nice_container():
            st.markdown("### 1) Upload")
            uploaded = st.file_uploader("CSV or Excel", type=["csv", "xlsx", "xls"])

    # Keep settings out of the way
    with st.expander("Optional LLM settings", expanded=False):
        llm_enabled = st.toggle("Enable LLM answering", value=False)
        llm_model = st.text_input("Model", value="gpt-4o-mini")
        llm_base_url = st.text_input("Base URL (optional)", value="", placeholder="Leave blank for default")
        llm_api_key = st.text_input("API key", value="", type="password")

        if llm_enabled and not llm_api_key.strip():
            st.warning("LLM enabled but no API key. You can still use retrieval-only mode.")

    df = None
    corpus = None
    id_col = None
    text_cols = None
    evidence_cols = None
    k = 25

    if uploaded is not None:
        file_bytes = uploaded.getvalue()
        df = read_tabular(file_bytes, uploaded.name)
        cols = list(df.columns)

        with left:
            with nice_container():
                st.markdown("### 2) Configure")
                id_guess = guess_id_column(cols)
                text_guess = guess_text_columns(cols)

                id_pick = st.selectbox(
                    "Row ID column (for citations)",
                    options=["(use row index)"] + cols,
                    index=(cols.index(id_guess) + 1) if id_guess in cols else 0,
                )
                id_col = None if id_pick == "(use row index)" else id_pick

                text_cols = st.multiselect(
                    "Text columns to search",
                    options=cols,
                    default=[c for c in text_guess if c in cols],
                )

                if not text_cols:
                    st.info("Pick at least one text column for accurate retrieval.")

                corpus = build_corpus(df, text_cols) if text_cols else [""] * len(df)

                default_evidence = []
                if id_col:
                    default_evidence.append(id_col)
                default_evidence += (text_cols[:2] if text_cols else [])
                evidence_cols = st.multiselect(
                    "Columns included as evidence",
                    options=cols,
                    default=[c for c in default_evidence if c in cols],
                    help="Keep this tight — fewer columns usually increases fidelity.",
                )

                k = st.slider("Evidence rows", 5, 60, 25, 5)
                st.caption(f"Retrieval: {'TF-IDF' if SKLEARN_OK else 'Token overlap (fallback)'}")

        with right:
            with nice_container():
                st.markdown("### 3) Ask")
                st.markdown(f'<div class="small-muted">Loaded: {len(df):,} rows × {len(df.columns):,} cols</div>', unsafe_allow_html=True)
                question = st.text_input("Question", placeholder="e.g., What are the most common overheating phrases and examples?")
                btns = st.columns(2, gap="small")
                run_retrieval = btns[0].button("Find evidence", use_container_width=True)
                run_answer = btns[1].button("Answer", use_container_width=True)

        if df is not None and corpus is not None and (run_retrieval or run_answer) and question.strip():
            idxs, scores, method = retrieve_rows(df, corpus, question, k=k)

            with right:
                st.write("")
                with nice_container():
                    st.markdown("### Evidence")
                    if not idxs:
                        st.info("No matching rows. Try adjusting text columns or making the question more specific.")
                    else:
                        df_evidence = df.iloc[idxs].copy()
                        df_evidence["_relevance"] = scores

                        show_cols = [c for c in (evidence_cols or text_cols or []) if c in df_evidence.columns]
                        if id_col and id_col in df_evidence.columns and id_col not in show_cols:
                            show_cols = [id_col] + show_cols

                        st.dataframe(df_evidence[["_relevance"] + show_cols][:], use_container_width=True, height=320)

                        if run_answer:
                            st.write("")
                            st.markdown("### Answer")
                            if llm_enabled and llm_api_key.strip():
                                try:
                                    answer = call_llm_answer(
                                        api_key=llm_api_key.strip(),
                                        base_url=llm_base_url.strip() or None,
                                        model=llm_model.strip(),
                                        question=question.strip(),
                                        df_evidence=df_evidence,
                                        id_col=id_col,
                                        evidence_cols=(evidence_cols if evidence_cols else show_cols),
                                    )
                                    st.markdown(answer)
                                except Exception as e:
                                    st.error(f"LLM call failed: {e}")
                                    st.info("You can still rely on the evidence table above.")
                            else:
                                st.info("LLM is off (or missing API key). Use the evidence table for high-confidence answers.")
        elif (run_retrieval or run_answer) and uploaded is not None and not question.strip():
            st.warning("Type a question first.")

    with st.expander("Accuracy notes (why this is safer than typical chat)", expanded=False):
        st.markdown(
            """
- The model sees only the **retrieved evidence rows**, not your entire file.
- Every claim is required to cite **row IDs**.
- Temperature is set to **0**.
- If you want “true global counts / exact % across all rows”, the next upgrade is adding deterministic aggregation (pandas/DuckDB) and letting the LLM explain those computed results.
"""
        )


# -----------------------------
# About Tab
# -----------------------------
with tab_about:
    with nice_container():
        st.markdown("### What this hub does")
        st.markdown(
            """
- Connects your existing review + call Streamlit apps into one workflow.
- Provides consistent prompt builders so outputs don’t drift across teammates.
- Optional “Ask Data” supports evidence-first Q&A with row citations.
"""
        )
        st.markdown("---")
        st.markdown(f'<div class="small-muted">Last updated: {datetime.now().strftime("%Y-%m-%d")}</div>', unsafe_allow_html=True)

