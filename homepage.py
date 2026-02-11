import streamlit as st
from datetime import datetime
from typing import Optional, List, Tuple
import hashlib
import re

try:
    import pandas as pd
except ImportError:
    pd = None

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
# Styles (simple, calm, “internal tool” vibe)
# -----------------------------
st.markdown(
    """
<style>
:root {
  --card-bg: rgba(255,255,255,0.06);
  --card-border: rgba(255,255,255,0.12);
  --muted: rgba(255,255,255,0.70);
  --muted2: rgba(255,255,255,0.60);
}

.block-container { padding-top: 1.3rem; padding-bottom: 2.0rem; }
h1, h2, h3 { letter-spacing: -0.02em; }

.sn-hero {
  padding: 18px 18px;
  border: 1px solid var(--card-border);
  background: linear-gradient(135deg, rgba(83,167,255,0.10), rgba(255,255,255,0.04));
  border-radius: 18px;
}
.sn-subtle { color: var(--muted); }
.sn-small { color: var(--muted2); font-size: 0.92rem; }
.sn-card {
  padding: 16px 16px;
  border: 1px solid var(--card-border);
  background: var(--card-bg);
  border-radius: 18px;
}
.sn-stepnum {
  display:inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--card-border);
  background: rgba(0,0,0,0.15);
  font-size: 0.86rem;
  margin-bottom: 10px;
}
.sn-divider {
  height: 1px;
  background: rgba(255,255,255,0.10);
  margin: 14px 0;
}
.sn-quickgrid {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 10px;
}
@media(max-width: 980px){
  .sn-quickgrid { grid-template-columns: repeat(2, minmax(160px, 1fr)); }
}
@media(max-width: 560px){
  .sn-quickgrid { grid-template-columns: 1fr; }
}
.sn-linkbtn {
  display: inline-block;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(255,255,255,0.06);
  text-decoration: none !important;
  color: white !important;
  font-weight: 600;
}
.sn-linkbtn:hover { border-color: rgba(83,167,255,0.55); }
</style>
""",
    unsafe_allow_html=True,
)

# -----------------------------
# Helpers
# -----------------------------
def section_header(title: str, subtitle: str):
    st.markdown(
        f"""
<div class="sn-hero">
  <h2 style="margin:0;">{title}</h2>
  <div class="sn-subtle" style="margin-top:6px;">{subtitle}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.write("")


def link_button(label: str, url: str, help_text: Optional[str] = None):
    if help_text:
        st.caption(help_text)
    if hasattr(st, "link_button"):
        st.link_button(label, url, use_container_width=True)
    else:
        st.markdown(
            f"<a class='sn-linkbtn' href='{url}' target='_blank' rel='noopener noreferrer'>{label}</a>",
            unsafe_allow_html=True,
        )


def quick_actions():
    st.markdown(
        f"""
<div class="sn-card">
  <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
    <div>
      <div style="font-weight:700; font-size:1.05rem;">Quick actions</div>
      <div class="sn-small">Open the right tool fast—no hunting through bookmarks.</div>
    </div>
  </div>
  <div class="sn-divider"></div>
  <div class="sn-quickgrid">
    <a class="sn-linkbtn" href="{REV_CONVERTER_URL}" target="_blank" rel="noopener noreferrer">🧱 Convert reviews</a>
    <a class="sn-linkbtn" href="{REV_VIEWER_URL}" target="_blank" rel="noopener noreferrer">🔎 View reviews</a>
    <a class="sn-linkbtn" href="{REVIEW_PROMPT_URL}" target="_blank" rel="noopener noreferrer">📊 Review prompts</a>
    <a class="sn-linkbtn" href="{CALL_PROMPT_URL}" target="_blank" rel="noopener noreferrer">🎧 Call prompts</a>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.write("")


def prompt_template_reviews(file_ref: str, symptoms: List[str], extra_context: str):
    symptom_line = ", ".join(symptoms) if symptoms else "«add your symptom list here»"
    file_line = file_ref.strip() if file_ref.strip() else "«paste the converted review file name/link here»"
    ctx = extra_context.strip()
    ctx_block = f"\n\nContext / notes:\n- {ctx}" if ctx else ""
    return f"""You are analyzing a converted web-review dataset produced by the Starwalk Converter.

Dataset reference: {file_line}

Task:
1) Explore the dataset and quantify these custom web review symptoms:
   - {symptom_line}
2) For each symptom, provide:
   - count of reviews mentioning it
   - % of total reviews
   - top 5 representative excerpts (short quotes) with the review ID if available
   - any common co-occurring symptoms
3) Call out any ambiguity (e.g., “hot”, “warm”, “burning”) and how you interpreted it.
4) Output results as a clean table + short bullet summary.

If the dataset contains product/model fields, break down results by model where meaningful.{ctx_block}"""


def prompt_template_calls(source_ref: str, questions: List[str], extra_context: str):
    src = source_ref.strip() if source_ref.strip() else "«paste the Zoom summary/transcript reference here»"
    qs = "\n".join([f"- {q}" for q in questions]) if questions else "- «add your questions here»"
    ctx = extra_context.strip()
    ctx_block = f"\n\nContext / notes:\n- {ctx}" if ctx else ""
    return f"""You are analyzing Zoom call summaries / transcript-derived notes.

Source reference: {src}

Answer the following questions using only what is supported by the summaries:
{qs}

For each answer:
- cite the supporting phrasing from the summary (short excerpt)
- highlight uncertainty if the summary is vague
- end with a concise “so what” takeaway (1–2 lines)

If multiple calls are present, compare themes and quantify frequency where possible.{ctx_block}"""


def fingerprint_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def guess_id_column(cols: List[str]) -> Optional[str]:
    candidates = [
        "review_id", "reviewid", "id", "row_id", "rowid",
        "call_id", "ticket_id", "case_id"
    ]
    lower_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def guess_text_columns(cols: List[str]) -> List[str]:
    # Heuristic: likely free-text fields
    text_keys = ["review", "comment", "text", "body", "feedback", "summary", "notes", "transcript"]
    hits = []
    for c in cols:
        cl = c.lower()
        if any(k in cl for k in text_keys):
            hits.append(c)
    return hits[:4]  # keep it sane by default


def safe_str(x) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


def build_corpus(df, text_cols: List[str]) -> List[str]:
    if not text_cols:
        return [""] * len(df)
    # Combine text columns into one field per row
    combined = (
        df[text_cols]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .tolist()
    )
    return combined


@st.cache_data(show_spinner=False)
def read_tabular(file_bytes: bytes, filename: str):
    if pd is None:
        raise RuntimeError("pandas is required for this feature.")
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(pd.io.common.BytesIO(file_bytes))
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(pd.io.common.BytesIO(file_bytes))
    # Try CSV as fallback
    return pd.read_csv(pd.io.common.BytesIO(file_bytes))


@st.cache_data(show_spinner=False)
def build_tfidf_index(corpus: List[str]):
    # Cached on corpus content (works well for small/medium files)
    vectorizer = TfidfVectorizer(stop_words="english", max_features=60000)
    X = vectorizer.fit_transform(corpus)
    return vectorizer, X


def retrieve_rows(
    df,
    corpus: List[str],
    question: str,
    k: int = 25,
) -> Tuple[List[int], List[float], str]:
    """
    Returns (row_indices, scores, method)
    """
    q = (question or "").strip()
    if not q:
        return [], [], "none"

    if SKLEARN_OK and len(df) > 0:
        vectorizer, X = build_tfidf_index(corpus)
        qv = vectorizer.transform([q])
        sims = cosine_similarity(qv, X).flatten()
        top_idx = sims.argsort()[::-1][:k]
        return top_idx.tolist(), sims[top_idx].tolist(), "tfidf"

    # Fallback: simple token overlap scoring
    q_tokens = set(re.findall(r"[a-z0-9']+", q.lower()))
    scores = []
    for i, txt in enumerate(corpus):
        tokens = re.findall(r"[a-z0-9']+", (txt or "").lower())
        if not tokens:
            scores.append(0.0)
            continue
        overlap = sum(1 for t in tokens if t in q_tokens)
        scores.append(float(overlap) / max(1.0, float(len(tokens))))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    ranked_scores = [scores[i] for i in ranked]
    return ranked, ranked_scores, "token_overlap"


def make_evidence_block(df_evidence, id_col: Optional[str], cols: List[str], max_chars: int = 420) -> str:
    lines = []
    for _, row in df_evidence.iterrows():
        rid = safe_str(row[id_col]) if id_col and id_col in df_evidence.columns else safe_str(row.name)
        parts = []
        for c in cols:
            if c not in df_evidence.columns:
                continue
            val = safe_str(row[c])
            val = re.sub(r"\s+", " ", val).strip()
            if len(val) > max_chars:
                val = val[:max_chars] + "…"
            parts.append(f"{c}={val}")
        joined = "; ".join(parts)
        lines.append(f"- ROW {rid}: {joined}")
    return "\n".join(lines)


def call_llm_answer(
    api_key: str,
    base_url: Optional[str],
    model: str,
    question: str,
    df_evidence,
    id_col: Optional[str],
    evidence_cols: List[str],
) -> str:
    """
    OpenAI-compatible chat call. Keeps it strict: only answer from evidence.
    """
    try:
        from openai import OpenAI
    except Exception:
        raise RuntimeError("openai package not installed. Add it to requirements.txt (openai>=1.0.0).")

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    client = OpenAI(**kwargs)

    cols_list = ", ".join(list(df_evidence.columns))
    evidence_text = make_evidence_block(df_evidence, id_col=id_col, cols=evidence_cols)

    system = (
        "You are a careful analyst answering questions about an uploaded dataset.\n"
        "RULES:\n"
        "1) Use ONLY the provided EVIDENCE ROWS. Do not assume anything not present.\n"
        "2) If the evidence is insufficient, say exactly what is missing.\n"
        "3) When you make a claim, cite the supporting row ID(s) like [ROW 123].\n"
        "4) Do not invent numbers. If you compute something, explain how from the evidence.\n"
        "5) Keep the answer structured and concise."
    )

    user = (
        f"QUESTION:\n{question}\n\n"
        f"AVAILABLE COLUMNS IN EVIDENCE:\n{cols_list}\n\n"
        f"EVIDENCE ROWS:\n{evidence_text}\n\n"
        "Return:\n"
        "- A direct answer\n"
        "- Bullet list of supporting evidence with row citations\n"
        "- Any uncertainty/limits"
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


# -----------------------------
# Header / Sidebar Nav
# -----------------------------
st.title("🧭 Starwalk Hub")
st.caption("A simple workspace that connects your existing review + call apps into one guided flow.")

nav = st.sidebar.radio(
    "Navigate",
    ["Reviews", "Calls", "Ask Data (Optional LLM)", "About"],
    index=0,
)

# Persisted state defaults
st.session_state.setdefault("review_file_ref", "")
st.session_state.setdefault("review_symptoms", ["Overheating", "Weak performance", "Noise", "Battery issues"])
st.session_state.setdefault("review_context", "")

st.session_state.setdefault("call_source_ref", "")
st.session_state.setdefault("call_questions", ["What are the top 3 customer pain points mentioned?", "Any repeat issues across calls?"])
st.session_state.setdefault("call_context", "")

# LLM settings (optional)
st.session_state.setdefault("llm_enabled", False)
st.session_state.setdefault("llm_provider_base_url", "")  # supports custom OpenAI-compatible endpoints
st.session_state.setdefault("llm_api_key", "")
st.session_state.setdefault("llm_model", "gpt-4o-mini")  # user-editable


# -----------------------------
# Reviews Page
# -----------------------------
if nav == "Reviews":
    section_header(
        "Reviews workflow",
        "Convert → view → quantify symptoms. This page helps you move through the review tools in a clear sequence.",
    )

    quick_actions()

    c1, c2, c3 = st.columns(3, gap="large")

    with c1:
        st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
        st.markdown("<div class='sn-stepnum'>Step 1</div>", unsafe_allow_html=True)
        st.subheader("Convert raw review data")
        st.markdown(
            """
Convert raw exports from **Axion** (or other review formats) into the **Starwalk Converter acceptable format**.  
This makes the file consistent so downstream viewing + prompting behave predictably.
"""
        )
        link_button("Open Starwalk Converter", REV_CONVERTER_URL, help_text="Opens in a new tab")
        st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sn-small'>Tip: Keep your converted file name stable (e.g., date + source + product) so it’s easy to reference later.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
        st.markdown("<div class='sn-stepnum'>Step 2</div>", unsafe_allow_html=True)
        st.subheader("View & explore converted reviews")
        st.markdown(
            """
Upload the converted output into the **Starwalk Viewer** for enhanced viewing.  
Use this for browsing, filtering, and quick qualitative read-through before you quantify anything.
"""
        )
        link_button("Open Starwalk Viewer", REV_VIEWER_URL, help_text="Opens in a new tab")
        st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sn-small'>Recommended: sanity-check the first ~20 rows after upload (IDs, dates, rating, model fields) to confirm the conversion didn’t shift columns.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with c3:
        st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
        st.markdown("<div class='sn-stepnum'>Step 3</div>", unsafe_allow_html=True)
        st.subheader("Quantify custom symptoms")
        st.markdown(
            """
Use the **Review Prompt** tool to ask structured questions over the converter-acceptable dataset.  
This is where you quantify *custom web-review symptoms* and turn qualitative noise into measurable signals.
"""
        )
        link_button("Open Review Prompt Tool", REVIEW_PROMPT_URL, help_text="Opens in a new tab")
        st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sn-small'>This hub can generate copy-ready prompts so your team stays consistent across analyses.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")
    st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
    st.subheader("Prompt builder (for Step 3)")
    st.caption("Fill this in, then paste the generated prompt into the Review Prompt Tool.")

    left, right = st.columns([1.2, 1], gap="large")

    with left:
        st.session_state.review_file_ref = st.text_input(
            "Converted review file reference (name/link/ID)",
            value=st.session_state.review_file_ref,
            placeholder="e.g., 2026-02-10_Axion_HT300_converted.csv",
        )
        st.session_state.review_symptoms = st.multiselect(
            "Symptoms to quantify (edit freely)",
            options=[
                "Overheating",
                "Weak performance",
                "Noise",
                "Battery issues",
                "Filter clogging / maintenance confusion",
                "Durability / breakage",
                "Error lights / indicators",
                "Smell / burning odor",
                "Poor instructions / setup friction",
                "Shipping / damaged on arrival",
                "Comfort / ergonomics",
                "UI/controls confusion",
            ],
            default=st.session_state.review_symptoms,
        )
        st.session_state.review_context = st.text_area(
            "Optional context / constraints",
            value=st.session_state.review_context,
            placeholder="e.g., Focus on MB1 units only. Prioritize detractors. Treat 'hot scalp' as overheating only if it implies discomfort.",
            height=100,
        )

    with right:
        st.markdown("**Good symptom prompts usually:**")
        st.markdown(
            """
- define what *counts* as the symptom  
- ask for **counts + % + excerpts**  
- request **co-occurrence**  
- require ambiguity callouts (“hot” vs “overheating”)
"""
        )
        st.markdown("**If you want consistency across teammates:**")
        st.markdown(
            """
- reuse a shared symptom list  
- keep output format stable (table + bullets)  
- enforce “cite excerpts” so results stay audit-able
"""
        )

    built_prompt = prompt_template_reviews(
        st.session_state.review_file_ref,
        st.session_state.review_symptoms,
        st.session_state.review_context,
    )

    st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
    st.text_area("Copy this into the Review Prompt Tool:", value=built_prompt, height=260)

    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------
# Calls Page
# -----------------------------
elif nav == "Calls":
    section_header(
        "Calls workflow",
        "Ask questions over Zoom summaries/transcript-derived notes—without digging through raw transcripts.",
    )

    quick_actions()

    st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
    st.subheader("Call prompts")
    st.markdown(
        """
This tool can answer questions based on its understanding of **Zoom summaries from transcripts**.
Use it to pull themes, quantify repeated issues, and turn “what people said” into a structured story.
"""
    )
    link_button("Open Call Prompt Tool", CALL_PROMPT_URL, help_text="Opens in a new tab")

    st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
    st.subheader("Prompt builder")

    left, right = st.columns([1.2, 1], gap="large")

    with left:
        st.session_state.call_source_ref = st.text_input(
            "Zoom summary/transcript reference (name/link/ID)",
            value=st.session_state.call_source_ref,
            placeholder="e.g., 2026-02_Wk2_ZoomSummaries_HT300_team.txt",
        )
        call_qs = st.text_area(
            "Questions (one per line)",
            value="\n".join(st.session_state.call_questions),
            placeholder="e.g., What are the top issues?\nWhich issues correlate with returns?\nAny new failure modes emerging?",
            height=150,
        )
        st.session_state.call_questions = [q.strip() for q in call_qs.splitlines() if q.strip()]
        st.session_state.call_context = st.text_area(
            "Optional context / constraints",
            value=st.session_state.call_context,
            placeholder="e.g., Focus on NA calls only. If something is not supported by summaries, explicitly say so.",
            height=90,
        )

    with right:
        st.markdown("**High-signal call questions:**")
        st.markdown(
            """
- “What changed week-over-week?”  
- “Which issues are most emotionally charged?”  
- “What’s new vs known?”  
- “What do people *try* before contacting support?”
"""
        )
        st.markdown("**If you’re comparing calls to reviews:**")
        st.markdown(
            """
- align symptom labels across both  
- ask for overlap + deltas  
- flag anything that only shows up in one channel
"""
        )

    built_call_prompt = prompt_template_calls(
        st.session_state.call_source_ref,
        st.session_state.call_questions,
        st.session_state.call_context,
    )

    st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
    st.text_area("Copy this into the Call Prompt Tool:", value=built_call_prompt, height=260)
    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------
# Ask Data (Optional LLM) Page
# -----------------------------
elif nav == "Ask Data (Optional LLM)":
    section_header(
        "Ask your uploaded file",
        "Optional LLM Q&A with evidence: upload a CSV/XLSX and ask questions with row-level citations.",
    )

    st.info(
        "Accuracy comes from **evidence-first answering** (retrieve rows → answer only from those rows → cite IDs). "
        "This avoids the classic LLM failure mode of inventing numbers."
    )

    if pd is None:
        st.error("This feature needs pandas. Add pandas to your environment.")
        st.stop()

    quick_actions()

    # LLM settings
    with st.sidebar.expander("Optional LLM settings", expanded=False):
        st.session_state.llm_enabled = st.toggle("Enable LLM answering", value=st.session_state.llm_enabled)
        st.session_state.llm_model = st.text_input("Model", value=st.session_state.llm_model)
        st.session_state.llm_provider_base_url = st.text_input(
            "Base URL (optional, for OpenAI-compatible endpoints)",
            value=st.session_state.llm_provider_base_url,
            placeholder="Leave blank for default OpenAI base URL",
        )
        st.session_state.llm_api_key = st.text_input(
            "API Key",
            value=st.session_state.llm_api_key,
            type="password",
            help="Prefer setting this in st.secrets for deployed apps.",
        )

        if st.session_state.llm_enabled and not st.session_state.llm_api_key.strip():
            st.warning("LLM is enabled but API key is empty. You can still use retrieval-only mode below.")

    st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
    st.subheader("1) Upload data")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

    df = None
    fp = None

    if uploaded is not None:
        file_bytes = uploaded.getvalue()
        fp = fingerprint_bytes(file_bytes)

        try:
            df = read_tabular(file_bytes, uploaded.name)
        except Exception as e:
            st.error(f"Could not read file: {e}")
            st.stop()

        st.success(f"Loaded **{len(df):,} rows** × **{len(df.columns):,} columns**")
        with st.expander("Preview", expanded=False):
            st.dataframe(df.head(50), use_container_width=True)

        st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)

        st.subheader("2) Configure evidence retrieval")
        cols = list(df.columns)

        default_id = guess_id_column(cols)
        default_text_cols = guess_text_columns(cols)

        cA, cB = st.columns([1, 1], gap="large")
        with cA:
            id_col = st.selectbox(
                "Row ID column (used for citations)",
                options=["(use row index)"] + cols,
                index=(cols.index(default_id) + 1) if default_id in cols else 0,
            )
            id_col = None if id_col == "(use row index)" else id_col

        with cB:
            text_cols = st.multiselect(
                "Text columns to search",
                options=cols,
                default=[c for c in default_text_cols if c in cols],
                help="These columns are combined into a searchable 'corpus' for finding relevant rows.",
            )

        if not text_cols:
            st.warning("Pick at least one text column for retrieval. If your file is mostly numeric, select a column that contains descriptions.")
        corpus = build_corpus(df, text_cols) if text_cols else [""] * len(df)

        # Evidence columns to show/cite
        default_evidence_cols = []
        if id_col:
            default_evidence_cols.append(id_col)
        default_evidence_cols += text_cols[:2]
        # Add a couple common fields if present
        for maybe in ["rating", "stars", "model", "sku", "country", "date", "created_at"]:
            for c in cols:
                if c.lower() == maybe and c not in default_evidence_cols:
                    default_evidence_cols.append(c)

        evidence_cols = st.multiselect(
            "Columns to include in evidence shown to the LLM",
            options=cols,
            default=[c for c in default_evidence_cols if c in cols],
            help="Keep this tight: fewer columns usually means higher fidelity and less noise.",
        )

        k = st.slider("Max evidence rows", min_value=5, max_value=60, value=25, step=5)
        method_badge = "TF-IDF" if SKLEARN_OK else "Token overlap"
        st.caption(f"Retrieval method: **{method_badge}** (auto-fallback if sklearn is unavailable).")

        st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
        st.subheader("3) Ask a question")

        question = st.text_input(
            "Question",
            placeholder="e.g., How often do people mention overheating, and what do they say exactly?",
        )

        col1, col2 = st.columns([1, 1], gap="large")
        with col1:
            run_retrieval = st.button("Find evidence rows", use_container_width=True)
        with col2:
            run_answer = st.button("Answer (uses LLM if enabled)", use_container_width=True)

        # Always allow retrieval-only mode
        if (run_retrieval or run_answer) and question.strip():
            idxs, scores, method = retrieve_rows(df, corpus, question, k=k)
            if not idxs:
                st.warning("No evidence rows found. Try adding/adjusting text columns or ask a more specific question.")
            else:
                df_evidence = df.iloc[idxs].copy()
                df_evidence["_relevance"] = scores

                st.markdown("**Evidence rows** (highest relevance first):")
                show_cols = [c for c in (evidence_cols or text_cols) if c in df_evidence.columns]
                if id_col and id_col in df_evidence.columns and id_col not in show_cols:
                    show_cols = [id_col] + show_cols

                # Limit display width and noise
                display_cols = (["_relevance"] + show_cols)[:12]
                st.dataframe(df_evidence[display_cols], use_container_width=True, height=350)

                # A small “evidence strength” heuristic (not a fake confidence)
                best = max(scores) if scores else 0.0
                st.caption(f"Top relevance score: **{best:.3f}** (higher means closer match).")

                if run_answer:
                    if st.session_state.llm_enabled and st.session_state.llm_api_key.strip():
                        with st.spinner("Drafting answer from evidence…"):
                            try:
                                answer = call_llm_answer(
                                    api_key=st.session_state.llm_api_key.strip(),
                                    base_url=st.session_state.llm_provider_base_url.strip() or None,
                                    model=st.session_state.llm_model.strip(),
                                    question=question.strip(),
                                    df_evidence=df_evidence,
                                    id_col=id_col,
                                    evidence_cols=(evidence_cols if evidence_cols else show_cols),
                                )
                                st.markdown("<div class='sn-divider'></div>", unsafe_allow_html=True)
                                st.subheader("Answer (evidence-cited)")
                                st.markdown(answer)
                            except Exception as e:
                                st.error(f"LLM call failed: {e}")
                                st.info("You can still use retrieval-only mode and manually inspect the evidence rows.")
                    else:
                        st.info(
                            "LLM is disabled (or missing API key). You’re in retrieval-only mode: "
                            "use the evidence table above to answer with high confidence."
                        )
        elif (run_retrieval or run_answer) and not question.strip():
            st.warning("Type a question first.")

    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Notes on accuracy (what makes this trustworthy)", expanded=False):
        st.markdown(
            """
- **Evidence-first**: the model sees only retrieved rows, so it can’t “freewheel” across imaginary data.
- **Row citations**: every claim should point back to row IDs you can audit.
- **Temperature=0**: reduces creative variance.
- **Practical limitation**: if the answer requires scanning the full dataset (global counts, exact totals), this method is *good* but not perfect unless you add deterministic computation (next upgrade).
"""
        )

# -----------------------------
# About Page
# -----------------------------
else:
    section_header(
        "About this hub",
        "A lightweight connector layer for your existing Streamlit apps—designed to reduce friction and keep workflows consistent.",
    )

    st.markdown("<div class='sn-card'>", unsafe_allow_html=True)
    st.markdown(
        f"""
### What this is
A **guided launcher + prompt builder** that connects:
- Starwalk Converter (format standardization)
- Starwalk Viewer (enhanced browsing)
- Review Prompt (symptom quantification)
- Call Prompt (Q&A over Zoom summaries)
- **Ask Data (Optional LLM)** (upload → retrieve evidence → answer w/ citations)

### What this isn’t (yet)
- It doesn’t pass files automatically between apps (each tool handles uploads separately).
- It doesn’t unify authentication/permissions across tools.

### Why it helps
- Teams stop “winging it” with inconsistent prompts
- Everyone follows the same sequence
- Outputs become repeatable and audit-able

**Last updated:** {datetime.now().strftime("%Y-%m-%d")}
"""
    )
    st.markdown("</div>", unsafe_allow_html=True)

st.caption("Built as a simple workflow layer—keep the tools you already have, but make them feel like one system.")
