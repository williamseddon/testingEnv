# -*- coding: utf-8 -*-
# Star Walk — Symptomize Reviews (v3-native)
# Model-native semantics (no hardcoded negators/synonyms) + Embedding Evidence + Ensemble Rerank
# Streamlit 1.38+
#
# To run:
#   pip install streamlit openpyxl openai pandas numpy
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_walk_app_v3.py

import io
import os
import re
import json
import time
import math
import hashlib
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# Optional OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Optional: preserve workbook formatting
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------------- Force Light Mode ----------------
st_html(
    """
<script>
(function () {
  function setLight() {
    try {
      document.documentElement.setAttribute('data-theme','light');
      document.body && document.body.setAttribute('data-theme','light');
      window.localStorage.setItem('theme','light');
    } catch (e) {}
  }
  setLight();
  new MutationObserver(setLight).observe(
    document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] }
  );
})();
</script>
""",
    height=0,
)

# ---------------- Global CSS (14" compact) ----------------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 78px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }
  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#94a3b8; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626; --warn:#b45309;
    --gap-sm:10px; --gap-md:16px; --gap-lg:24px;
  }
  html, body, .stApp { background: var(--bg-app); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, sans-serif; color: var(--text); }
  .block-container { padding-top:.5rem; padding-bottom:.9rem; max-width: 1280px; }
  .hero-wrap{ position:relative; overflow:hidden; border-radius:12px; min-height:92px; margin:.1rem 0 .6rem 0; box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05); background:linear-gradient(90deg, var(--bg-card) 0% 60%, transparent 60% 100%); }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:8px 14px; color:var(--text); }
  .hero-title{ font-size:clamp(18px,2.3vw,30px); font-weight:800; margin:0; line-height:1.1; }
  .hero-sub{ margin:2px 0 0 0; color:var(--muted); font-size:clamp(11px,1vw,14px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:36%; }
  .sn-logo{ height:36px; width:auto; display:block; opacity:.92; }
  .card{ background:var(--bg-card); border-radius:12px; padding:12px; box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05); }
  .pill{ padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:var(--bg-tile); font-weight:700; font-size:12px; }
  .review-quote { white-space:pre-wrap; background:var(--bg-tile); border:1px solid var(--border); border-radius:10px; padding:8px 10px; font-size:13px; }
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
  .chip{padding:6px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg-tile);font-weight:700;font-size:.86rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .evd{display:block;margin-top:3px;font-size:12px;color:#334155}
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------------- Header ----------------
st.markdown(
    """
    <div class="hero-wrap">
      <div class="hero-inner">
        <div>
          <div class="hero-title">Star Walk — Symptomize Reviews (v3-native)</div>
          <div class="hero-sub">Model-native semantics; no hardcoded negators or synonyms. Evidence via quotes or embeddings.</div>
        </div>
        <div class="hero-right"><img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"/></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ========================== SIDEBAR ==========================
with st.sidebar:
    st.header("📁 Upload Star Walk File")
    uploaded = st.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

    st.markdown("---")
    st.subheader("⚙️ Presets")
    preset = st.radio("Choose defaults", ["Accuracy-first (recommended)", "Throughput-first"], index=0, horizontal=True)

    st.subheader("Run Settings")
    if preset.startswith("Accuracy"):
        speed_mode = st.toggle("Speed mode (shortest first)", value=True)
        strictness = st.slider("Strictness (min conf)", 0.55, 0.95, 0.78, 0.01)
        require_evidence = st.checkbox("Require textual evidence", value=True)
        evidence_sim_threshold = st.slider("Embedding evidence threshold", 0.10, 0.60, 0.32, 0.01)
        model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=2)
        max_output_tokens = st.number_input("Max output tokens", 64, 4000, 380, 10)
        candidate_cap = st.slider("Cap candidates per review", 20, 200, 80, 5)
        api_concurrency = st.slider("API concurrency", 1, 16, 4)
        use_embeddings = st.checkbox("Use semantic recall (embeddings)", value=True)
        emb_model = st.selectbox("Embeddings model", ["text-embedding-3-small", "text-embedding-3-large"], index=1)
        max_sentences = st.slider("Max sentences analyzed", 6, 40, 18, 2)
    else:
        speed_mode = st.toggle("Speed mode (shortest first)", value=True)
        strictness = st.slider("Strictness (min conf)", 0.55, 0.95, 0.80, 0.01)
        require_evidence = st.checkbox("Require textual evidence", value=True)
        evidence_sim_threshold = st.slider("Embedding evidence threshold", 0.10, 0.60, 0.30, 0.01)
        model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=1)
        max_output_tokens = st.number_input("Max output tokens", 64, 4000, 320, 10)
        candidate_cap = st.slider("Cap candidates per review", 20, 200, 60, 5)
        api_concurrency = st.slider("API concurrency", 1, 16, 6)
        use_embeddings = st.checkbox("Use semantic recall (embeddings)", value=True)
        emb_model = st.selectbox("Embeddings model", ["text-embedding-3-small", "text-embedding-3-large"], index=0)
        max_sentences = st.slider("Max sentences analyzed", 6, 40, 12, 2)

    # Native semantics mode (no lexicons)
    native_semantics = st.checkbox("Model-native semantics (no lexicons)", value=True)
    auto_relax = st.checkbox("Auto-relax if nothing found", value=True)
    ensemble_check = st.checkbox("Ensemble rerank (lexical+embed+LLM)", value=True)

    st.info("Evidence acceptance: quote present in the review OR label-to-sentence embedding similarity >= threshold.")

# Persist raw bytes for formatting-preserving save
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

# ---------------- Load main sheet ----------------
@st.cache_data(show_spinner=False)
def _load_main_sheet(_uploaded: io.BytesIO) -> pd.DataFrame:
    try:
        try:
            return pd.read_excel(_uploaded, sheet_name="Star Walk scrubbed verbatims")
        except ValueError:
            return pd.read_excel(_uploaded)
    except Exception as e:
        raise RuntimeError(f"Could not read the Excel file: {e}")

try:
    df = _load_main_sheet(uploaded)
except Exception as e:
    st.error(str(e))
    st.stop()

# ---------------- Identify Symptom Columns ----------------
explicit_cols = [f"Symptom {i}" for i in range(1,21)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()
if not SYMPTOM_COLS:
    st.error("Couldn't locate Symptom 1-20 columns (K-AD).")
    st.stop()

# Missing symptom rows
is_empty = df[SYMPTOM_COLS].isna() | (
    df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"})
)
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Review length IQR for ETA
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
q1 = verb_series.str.len().quantile(0.25) if not verb_series.empty else 0
q3 = verb_series.str.len().quantile(0.75) if not verb_series.empty else 0
IQR = (q3 - q1) if (q3 or q1) else 0

# ---------------- Symptom palette loader ----------------
import io as _io

def _norm(s: str) -> str:
    if s is None: return ""
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    return "symptom" in _norm(name)

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> str | None:
    names = xls.sheet_names
    cands = [n for n in names if _looks_like_symptom_sheet(n)]
    if cands:
        return min(cands, key=lambda n: len(_norm(n)))
    return names[0] if names else None

@st.cache_data(show_spinner=False)
def load_symptom_lists_robust(raw_bytes: bytes, user_sheet: str | None = None):
    meta = {"sheet": None, "strategy": None, "columns": [], "note": ""}
    if not raw_bytes:
        meta["note"] = "No raw bytes provided"
        return [], [], meta
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw_bytes))
    except Exception as e:
        meta["note"] = f"Could not open Excel: {e}"
        return [], [], meta
    sheet = user_sheet or autodetect_symptom_sheet(xls)
    if not sheet:
        meta["note"] = "No sheets found"
        return [], [], meta
    meta["sheet"] = sheet
    try:
        s = pd.read_excel(xls, sheet_name=sheet)
    except Exception as e:
        meta["note"] = f"Could not read sheet '{sheet}': {e}"
        return [], [], meta
    # Heuristics: try common column names, else first two non-empty
    best_del = None; best_det = None
    for c in s.columns:
        c_norm = _norm(c)
        if any(tok in c_norm for tok in ["delight","pro","positive","like"]): best_del = c if best_del is None else best_del
        if any(tok in c_norm for tok in ["detract","con","negative","issue","problem","dislike","complaint"]): best_det = c if best_det is None else best_det
    if best_del or best_det:
        dels = s.get(best_del, pd.Series(dtype=str)) if best_del else pd.Series(dtype=str)
        dets = s.get(best_det, pd.Series(dtype=str)) if best_det else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"fuzzy-headers","columns":list(s.columns)})
        return dels, dets, meta
    non_empty_cols = []
    for c in s.columns:
        vals = [str(x).strip() for x in s[c].dropna().tolist() if str(x).strip()]
        if vals:
            non_empty_cols.append((c, vals))
        if len(non_empty_cols) >= 2: break
    if non_empty_cols:
        dels = non_empty_cols[0][1]
        dets = non_empty_cols[1][1] if len(non_empty_cols) > 1 else []
        meta.update({"strategy":"first-two-nonempty","columns":list(s.columns)})
        return dels, dets, meta
    return [], [], {"strategy":"none","columns":list(s.columns)}

raw_bytes = st.session_state.get("uploaded_bytes", b"")
sheet_names = []
try:
    _xls_tmp = pd.ExcelFile(_io.BytesIO(raw_bytes))
    sheet_names = _xls_tmp.sheet_names
except Exception:
    pass

auto_sheet = autodetect_symptom_sheet(_xls_tmp) if sheet_names else None

with st.sidebar:
    chosen_sheet = st.selectbox(
        "Choose the sheet that contains Delighters/Detractors",
        options=sheet_names if sheet_names else ["(no sheets detected)"],
        index=(sheet_names.index(auto_sheet) if (sheet_names and auto_sheet in sheet_names) else 0)
    )

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes, user_sheet=chosen_sheet if sheet_names else None
)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]

# ========================== TOP KPIs ==========================
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<span class='pill'>🧾 Total reviews: <b>{len(df)}</b></span>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<span class='pill'>❌ Missing symptoms: <b>{missing_count}</b></span>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<span class='pill'>✂ IQR chars: <b>{int(IQR)}</b></span>", unsafe_allow_html=True)
with colD:
    st.caption("Native semantics enabled.")

left, right = st.columns([1.45, 2.55], gap="small")

with left:
    st.markdown("### Run Controller")
    batch_n = st.slider("How many to process this run", 1, 50, min(16, max(1, missing_count)) if missing_count else 16)

    # ETA (heuristic)
    MODEL_TPS = {"gpt-4o-mini": 55, "gpt-4o": 25, "gpt-4.1": 16, "gpt-5": 12}
    MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
    rows = min(batch_n, missing_count)
    chars_est = max(200, int((q1+q3)/2)) if (q1 or q3) else 400
    tok_est = int(chars_est/4)
    rt = rows * (MODEL_LAT.get(model_choice,1.0) + tok_est/max(8, MODEL_TPS.get(model_choice,12)))
    eta_secs = int(round(rt))
    st.caption(f"Will attempt {rows} rows • Rough ETA: ~{eta_secs}s")

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    if missing_count and not _HAS_OPENAI:
        st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
    if missing_count and _HAS_OPENAI and not api_key:
        st.warning("Set `OPENAI_API_KEY` (env or secrets) to enable AI labeling.")

    st.markdown("---")
    can_run = missing_count > 0 and ((_HAS_OPENAI and api_key is not None) or True)

    col_runA, col_runB = st.columns([1,1])
    with col_runA:
        run = st.button(
            f"✨ Symptomize next {min(batch_n, missing_count)}",
            disabled=not can_run,
            help="Runs on the next batch of reviews missing symptoms.",
            use_container_width=True,
        )
    with col_runB:
        enable_all = st.checkbox("Enable ALL (bulk)")
        run_all = st.button(
            f"⚡ Symptomize ALL {missing_count}",
            disabled=(not can_run) or missing_count==0 or (not enable_all),
            help="Processes every review that has empty Symptom 1-20. Uses many API calls.",
            use_container_width=True,
        )
    st.caption("Tip: Use batch mode first to review accuracy, then run ALL.")

with right:
    st.markdown("### Live Processing")
    if "live_rows" not in st.session_state:
        st.session_state["live_rows"] = []  # list of rows for the live table
    live_table_ph = st.empty()
    live_progress = st.progress(0)

# ---------------- Session State (non-UI) ----------------
st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("approved_new_delighters", set())
st.session_state.setdefault("approved_new_detractors", set())

# ---------------- Text & evidence utils ----------------
STOP = set("""a an and the or but so of in on at to for from with without as is are was were be been being 
have has had do does did not no nor never very really quite just only almost about into out by this that these those 
it its they them i we you he she my your our their his her mine ours yours theirs""".split())

_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

# NOTE: No NEGATORS/ALIAS/SYN_SEEDS in native mode; the model handles negation/paraphrase.

# Small helpers

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+"," ", (name or "").lower()).strip()

# ---- Sentence splitter ----

def _sentences(review: str) -> List[str]:
    parts = re.split(r'(?<=[\.!\?])\s+|\n+', (review or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 12]

# ---- OpenAI helpers ----
@st.cache_resource(show_spinner=False)
def _get_openai_client_cached(key: str):
    return OpenAI(api_key=key) if (_HAS_OPENAI and key) else None

@st.cache_resource(show_spinner=False)
def _get_store():
    return {
        "pick_cache": {},
        "label_emb": {},
        "sent_emb_cache": {},
        "lock": threading.Lock()
    }

def _chat_with_retry(client, req, retries: int = 3, base_delay: float = 0.7):
    for i in range(retries):
        try:
            return client.chat.completions.create(**req)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(base_delay * (2 ** i))

def _embed_with_retry(client, model: str, inputs: List[str], retries: int = 3, base_delay: float = 0.7):
    for i in range(retries):
        try:
            out = client.embeddings.create(model=model, input=inputs)
            return [np.array(v.embedding, dtype=np.float32) for v in out.data]
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(base_delay * (2 ** i))

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)

# embed cache helpers

def _get_sent_pairs(review: str, client, emb_model: str, max_sentences: int = 18):
    store = _get_store()
    lock = store["lock"]
    sent_cache = store.setdefault("sent_emb_cache", {})
    key = hashlib.sha256((review or "").encode("utf-8")).hexdigest()
    with lock:
        pairs = sent_cache.get(key)
    if pairs is None and client is not None:
        sents = _sentences(review)[:max_sentences]
        try:
            embs = _embed_with_retry(client, emb_model, sents)
            pairs = list(zip(sents, embs))
            with lock:
                sent_cache[key] = pairs
        except Exception:
            pairs = [(s, None) for s in _sentences(review)[:max_sentences]]
    return pairs or []

@st.cache_resource(show_spinner=False)
def _get_label_emb(label: str, client, emb_model: str):
    store = _get_store()
    lock = store["lock"]
    label_emb = store.setdefault("label_emb", {})
    with lock:
        e = label_emb.get(label)
    if e is None and client is not None:
        try:
            arr = _embed_with_retry(client, emb_model, [label])[0]
            with lock:
                label_emb[label] = arr
            e = arr
        except Exception:
            e = None
    return e

# ---- Candidate shortlist (embeddings-first) ----

def _shortlist_by_embeddings(review: str, labels: list[str], client, emb_model: str,
                              max_sentences: int = 18, top_k: int = 60) -> list[str]:
    pairs = _get_sent_pairs(review, client, emb_model, max_sentences)
    scored: list[tuple[str,float]] = []
    for L in labels:
        e_l = _get_label_emb(L, client, emb_model)
        best = 0.0
        if e_l is not None:
            for s, e_s in pairs:
                if e_s is None: continue
                sim = _cosine(e_l, e_s)
                if sim > best: best = sim
        scored.append((L, best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [l for l,_ in scored[:top_k]]


def _prefilter_candidates(review: str, allowed: list[str], cap: int, client, emb_model: str,
                          max_sentences: int) -> list[str]:
    if client is not None:
        try:
            return _shortlist_by_embeddings(review, allowed, client, emb_model, max_sentences=max_sentences, top_k=cap)
        except Exception:
            pass
    # Fallback: lexical (very simple; no synonyms)
    text = " " + _normalize_name(review) + " "
    scored = []
    for L in allowed:
        toks = [t for t in _normalize_name(L).split() if len(t) > 2]
        hits = sum(1 for t in toks if f" {t} " in text)
        if hits:
            scored.append((L, hits/len(toks) if toks else 0))
    if not scored:
        return allowed[:cap]
    scored.sort(key=lambda x: -x[1])
    return [l for l,_ in scored[:cap]]

# ---- LLM picker with evidence quotes (native) ----

def _llm_pick(review: str, stars, allowed_del: list[str], allowed_det: list[str], min_conf: float,
              require_evidence: bool, evidence_sim_threshold: float,
              candidate_cap: int, max_output_tokens: int,
              use_embeddings: bool, emb_model: str, max_sentences: int,
              auto_relax: bool, ensemble_check: bool):
    if not review or (not allowed_del and not allowed_det):
        return [], [], {}, {}

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    client = _get_openai_client_cached(api_key) if (_HAS_OPENAI and api_key) else None

    allowed_del_f = _prefilter_candidates(review, allowed_del, candidate_cap, client, emb_model, max_sentences)
    allowed_det_f = _prefilter_candidates(review, allowed_det, candidate_cap, client, emb_model, max_sentences)

    cache_key = "|".join([
        str(model_choice), str(min_conf), str(require_evidence), str(evidence_sim_threshold),
        str(candidate_cap), str(use_embeddings), emb_model, str(max_sentences),
        hashlib.sha256("\x1f".join(sorted(allowed_del_f)).encode()).hexdigest(),
        hashlib.sha256("\x1f".join(sorted(allowed_det_f)).encode()).hexdigest(),
        hashlib.sha256((review or "").encode("utf-8")).hexdigest(), str(stars)
    ])
    store = _get_store()
    with store["lock"]:
        if cache_key in store["pick_cache"]:
            return store["pick_cache"][cache_key]

    sys_prompt = (
        """
You label one customer review. Choose ONLY from the provided lists.
Return compact JSON:
{
 "delighters":[{"name":"", "confidence":0.00, "quote":""}],
 "detractors":[{"name":"", "confidence":0.00, "quote":""}]
}
Rules:
- Use your own reasoning for synonyms and paraphrases; do not assume a lexicon exists.
- Respect negation and hedging: if the text states the opposite, do NOT select that label.
- Provide a SHORT verbatim quote (5-18 words) that supports each pick.
- If stars are 1-2, prioritize detractors; if 4-5, prioritize delighters; 3 is neutral.
- Prefer precision over recall; remove near-duplicates. Max 10 per group. Confidence in [0,1].
        """
    )

    user =  {
        "review": review,
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del_f[:120],
        "allowed_detractors": allowed_det_f[:120]
    }

    evidence_map: dict[str, list[str]] = {}

    def _best_sim(label: str) -> float:
        if client is None: return 0.0
        e_l = _get_label_emb(label, client, emb_model)
        if e_l is None: return 0.0
        best = 0.0
        for s, e_s in _get_sent_pairs(review, client, emb_model, max_sentences):
            if e_s is None: continue
            sim = _cosine(e_l, e_s)
            if sim > best: best = sim
        return float(best)

    def _post_process(items, allowed_set, text):
        out_pairs: list[tuple[str,float,str]] = []
        for d in items or []:
            name = (d.get("name") or "").strip()
            conf = float(d.get("confidence", 0))
            quote = (d.get("quote") or "").strip()
            if not name:
                continue
            ev_ok = True
            if require_evidence:
                ev_ok = False
                # (a) exact normalized quote fragment present
                if quote and _normalize_name(quote) in _normalize_name(text):
                    ev_ok = True
                # (b) OR label-to-sentence embedding similarity exceeds threshold
                if not ev_ok:
                    sim = _best_sim(name)
                    if sim >= float(evidence_sim_threshold):
                        ev_ok = True
            if ev_ok and name in allowed_set:
                out_pairs.append((name, max(0.0, min(1.0, conf)), quote))
        # dedupe keep highest conf
        best: Dict[str,Tuple[float,str]] = {}
        for n,c,q in out_pairs:
            if n not in best or c > best[n][0]:
                best[n] = (c,q)
        final = sorted(best.items(), key=lambda x: -x[1][0])[:10]
        for n,(c,q) in final:
            if q:
                evidence_map.setdefault(n, []).append(q)
        return [(n, c) for n,(c,_) in final]

    def _dedupe_keep_top(items: list[tuple[str,float]], top_n: int = 10, min_conf_: float = 0.60) -> list[str]:
        kept: list[tuple[str,float]] = []
        for n, c in sorted(items, key=lambda x: -x[1]):
            if c >= min_conf_ and n and n not in [k for k,_ in kept]:
                kept.append((n, c))
            if len(kept) >= top_n:
                break
        return [n for n,_ in kept]

    dels_final: list[str] = []
    dets_final: list[str] = []

    try:
        if client is not None:
            req = {
                "model": model_choice,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user)}
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": max_output_tokens,
            }
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.0
            out = _chat_with_retry(client, req)
            content = out.choices[0].message.content or "{}"
            data = json.loads(content)

            dels_pairs = _post_process(data.get("delighters", []), set(allowed_del_f), review)
            dets_pairs = _post_process(data.get("detractors", []), set(allowed_det_f), review)

            dels_final = _dedupe_keep_top(dels_pairs, 10, min_conf)
            dets_final = _dedupe_keep_top(dets_pairs, 10, min_conf)

            if ensemble_check and client is not None:
                def _boost(lst: list[str]):
                    scored = [(n, _best_sim(n)) for n in lst]
                    scored.sort(key=lambda x: -x[1])
                    return [n for n,_ in scored]
                dels_final = _boost(dels_final)
                dets_final = _boost(dets_final)

            if auto_relax and (not dels_final and not dets_final):
                req_relaxed = req.copy()
                req_relaxed["messages"] = [
                    {"role":"system","content": sys_prompt + "\nIf nothing is clearly present, choose items ONLY with direct textual evidence (quotes)."},
                    {"role":"user","content": json.dumps(user)}
                ]
                out2 = _chat_with_retry(client, req_relaxed)
                content2 = out2.choices[0].message.content or "{}"
                data2 = json.loads(content2)
                dels_pairs2 = _post_process(data2.get("delighters", []), set(allowed_del_f), review)
                dets_pairs2 = _post_process(data2.get("detractors", []), set(allowed_det_f), review)
                dels_final = [n for n,_ in sorted(dels_pairs2, key=lambda x: -x[1])[:10]]
                dets_final = [n for n,_ in sorted(dets_pairs2, key=lambda x: -x[1])[:10]]
    except Exception:
        pass

    # Conservative fallback (no API): lexical only
    if (not dels_final and not dets_final):
        text = " " + _normalize_name(review) + " "
        def pick_from_allowed(allowed: list[str]) -> list[str]:
            scored = []
            for a in allowed:
                toks = [t for t in _normalize_name(a).split() if len(t) > 2]
                hits = sum(1 for t in toks if f" {t} " in text)
                if hits:
                    scored.append((a, 0.60 + 0.1*min(3, hits)))
            scored.sort(key=lambda x: -x[1])
            return [n for n,_ in scored[:10]]
        dels_final = pick_from_allowed(allowed_del_f)
        dets_final = pick_from_allowed(allowed_det_f)

    result = (dels_final, dets_final, evidence_map, {"allowed_del": allowed_del_f, "allowed_det": allowed_det_f})
    with store["lock"]:
        store["pick_cache"][cache_key] = result
    return result

# ---------------- Live renderer helpers ----------------

def _render_live_table():
    rows = st.session_state.get("live_rows", [])
    if not rows:
        live_table_ph.info("Nothing processed yet. Click Symptomize to start.")
        return
    live_df = pd.DataFrame(rows, columns=[
        "Row #", "Stars", "Len", "Detractors #", "Delighters #", "Status"
    ])
    live_table_ph.dataframe(
        live_df,
        use_container_width=True,
        hide_index=True,
        height=320,
    )


def _render_chips(kind: str, names: List[str], evidence_map: Dict[str, List[str]]):
    if not names:
        st.code("-")
        return
    html = "<div class='chips'>"
    for x in names:
        evs = [e for e in (evidence_map.get(x, []) or []) if e]
        ev_html = f"<span class='evd'>{(evs[0][:160] + '...' if len(evs[0])>160 else evs[0])}</span>" if evs else ""
        cls = "pos" if kind=="delighters" else "neg"
        html += f"<span class='chip {cls}'>{x}{ev_html}</span>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# ---------------- Run Symptomize (parallel) ----------------
if (run or run_all) and missing_idx:
    # determine todo order
    missing_idx_sorted = sorted(missing_idx, key=lambda i: len(str(df.loc[i].get("Verbatim","")))) if speed_mode else missing_idx
    todo = missing_idx_sorted if run_all else missing_idx_sorted[:batch_n]

    # Seed the queue view
    st.session_state["live_rows"] = [
        [int(idx),
         (float(df.loc[idx].get("Star Rating")) if pd.notna(df.loc[idx].get("Star Rating")) else None),
         int(len(str(df.loc[idx].get("Verbatim", "")))),
         0, 0,
         "queued"]
        for idx in todo
    ]
    _render_live_table()

    def _process_one(idx: int):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        return idx, _llm_pick(
            review_txt,
            stars,
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            strictness,
            require_evidence=require_evidence,
            evidence_sim_threshold=evidence_sim_threshold,
            candidate_cap=candidate_cap,
            max_output_tokens=max_output_tokens,
            use_embeddings=use_embeddings,
            emb_model=emb_model,
            max_sentences=max_sentences,
            auto_relax=auto_relax,
            ensemble_check=ensemble_check,
        )

    with st.status("Processing reviews...", expanded=True) as status_box:
        completed = 0
        errors = 0
        live_progress.progress(0)
        if st.session_state["live_rows"]:
            st.session_state["live_rows"][0][-1] = "processing"
            _render_live_table()

        with ThreadPoolExecutor(max_workers=api_concurrency) as ex:
            futures = {ex.submit(_process_one, idx): idx for idx in todo}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    idx, (dels, dets, evidence_map, _meta) = fut.result()
                    # find display row by df idx
                    try:
                        row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                    except ValueError:
                        row_pos = None
                    if row_pos is not None:
                        st.session_state["live_rows"][row_pos][3] = len(dets)
                        st.session_state["live_rows"][row_pos][4] = len(dels)
                        st.session_state["live_rows"][row_pos][-1] = "done"
                    # Save suggestion bundle
                    st.session_state["symptom_suggestions"].append({
                        "row_index": int(idx),
                        "stars": float(df.loc[idx].get("Star Rating")) if pd.notna(df.loc[idx].get("Star Rating")) else None,
                        "review": str(df.loc[idx].get("Verbatim", "") or "").strip(),
                        "delighters": dels,
                        "detractors": dets,
                        "evidence_map": evidence_map,
                    })
                except Exception as e:
                    errors += 1
                    try:
                        row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                        st.session_state["live_rows"][row_pos][-1] = "error"
                    except Exception:
                        pass
                    status_box.write(f"Warning: Error on df idx {idx}: {type(e).__name__}")
                finally:
                    completed += 1
                    live_progress.progress(completed/len(todo))
                    _render_live_table()

        status_box.update(label="Finished generating suggestions! Review below, then Apply to write into the sheet.", state="complete")
    st.rerun()

# ---------------- Review & Approve ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## Review & Approve Suggestions")

    with st.expander("View allowed symptom palettes", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Allowed Detractors** ({len(ALLOWED_DETRACTORS)}):")
            if ALLOWED_DETRACTORS:
                st.markdown("<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in ALLOWED_DETRACTORS]) + "</div>", unsafe_allow_html=True)
            else:
                st.caption("None detected")
        with c2:
            st.markdown(f"**Allowed Delighters** ({len(ALLOWED_DELIGHTERS)}):")
            if ALLOWED_DELIGHTERS:
                st.markdown("<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in ALLOWED_DELIGHTERS]) + "</div>", unsafe_allow_html=True)
            else:
                st.caption("None detected")

    # Bulk actions
    with st.expander("Bulk actions", expanded=True):
        c1,c2,c3,c4,c5 = st.columns([1,1,1,2,3])
        if "sug_selected" not in st.session_state:
            st.session_state["sug_selected"] = set()
        total = len(sugs)
        with c1:
            if st.button("Select all (fast)"):
                st.session_state["sug_selected"] = set(range(total))
                for i in range(total):
                    st.session_state[f"sel_{i}"] = True
        with c2:
            if st.button("Clear all"):
                st.session_state["sug_selected"] = set()
                for i in range(total):
                    st.session_state[f"sel_{i}"] = False
        with c3:
            if st.button("Invert"):
                newset = set()
                for i in range(total):
                    cur = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
                    cur = not cur
                    st.session_state[f"sel_{i}"] = cur
                    if cur: newset.add(i)
                st.session_state["sug_selected"] = newset
        with c4:
            if st.button("Only with suggestions"):
                keep = {i for i,s in enumerate(sugs) if s["delighters"] or s["detractors"]}
                st.session_state["sug_selected"] = keep
                for i in range(total):
                    st.session_state[f"sel_{i}"] = (i in keep)
        with c5:
            max_apply = st.slider("Max rows to apply now", 1, total, min(20, total))

    for i, s in enumerate(sugs):
        label = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i==0)):
            default_checked = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
            checked = st.checkbox("Select for apply", value=default_checked, key=f"sel_{i}")
            if checked:
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            if s["review"]:
                st.markdown("**Full review:**")
                st.markdown(f"<div class='review-quote'>{s['review']}</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Full review:** (empty)")

            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors (<=10)**")
                _render_chips("detractors", s["detractors"], s.get("evidence_map", {}))
            with c2:
                st.write("**Delighters (<=10)**")
                _render_chips("delighters", s["delighters"], s.get("evidence_map", {}))

    if st.button("Apply selected to DataFrame", use_container_width=True):
        picked = [i for i in st.session_state["sug_selected"]]
        if not picked:
            st.warning("Nothing selected.")
        else:
            picked = picked[:max_apply]
            for i in picked:
                s = sugs[i]
                ri = s["row_index"]
                dets_final = s["detractors"][:10]
                dels_final = s["delighters"][:10]
                for j, name in enumerate(dets_final, start=1):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                for j, name in enumerate(dels_final, start=11):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
            st.success(f"Applied {len(picked)} row(s) to DataFrame.")

# ---------------- Download Updated Workbook ----------------

def offer_downloads():
    st.markdown("### Download Updated Workbook")
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first.")
        return
    raw = st.session_state["uploaded_bytes"]
    # Formatting-preserving write when possible
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = "Star Walk scrubbed verbatims"
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name): return headers.get(name)
            for df_row_idx, row in df.reset_index(drop=True).iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci:
                        ws.cell(row=excel_row, column=ci).value = row.get(c, None)
            out = io.BytesIO()
            wb.save(out)
            st.download_button("Download updated workbook (.xlsx)", data=out.getvalue(), file_name="StarWalk_updated.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        except Exception:
            pass
    # Fallback simple write
    out2 = io.BytesIO()
    with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
    st.download_button("Download updated workbook (.xlsx) — no formatting", data=out2.getvalue(), file_name="StarWalk_updated_basic.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads()

