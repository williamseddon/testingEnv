# -*- coding: utf-8 -*-
# Star Walk — Symptomize Reviews (v4, model-first)
# High-precision LLM extraction with self-consistency + evidence & negation validation
#
# To run:
#   pip install streamlit openpyxl openai pandas numpy
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_walk_app_v4.py

import io
import os
import re
import json
import time
import math
import hashlib
import threading
import itertools
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# -------- Optional OpenAI ----------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# -------- Optional: preserve workbook formatting ----------
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk — Symptomize (v4)")

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
    document.documentElement,{ attributes:true, attributeFilter:['data-theme'] }
  );
})();
</script>
""",
    height=0,
)

# ---------------- Global CSS (compact) ----------------
GLOBAL_CSS = """
<style>
  :root { scroll-behavior:smooth; scroll-padding-top:78px }
  *, ::before, ::after { box-sizing:border-box }
  :root{
    --text:#0f172a; --muted:#475569; --border:#cbd5e1; --border-strong:#94a3b8;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
  }
  html, body, .stApp { background:var(--bg-app); color:var(--text); font-family:ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, sans-serif; }
  .block-container { padding-top:.5rem; padding-bottom:.9rem; max-width:1280px }
  .hero-wrap{ position:relative; border-radius:12px; min-height:92px; margin:.1rem 0 .6rem 0;
    box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05);
    background:linear-gradient(90deg, var(--bg-card) 0% 60%, transparent 60% 100%);
  }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:8px 14px }
  .hero-title{ font-size:clamp(18px,2.3vw,30px); font-weight:800; margin:0; line-height:1.1 }
  .hero-sub{ margin:2px 0 0 0; color:#64748b; font-size:clamp(11px,1vw,14px) }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:36% }
  .sn-logo{ height:36px; width:auto; display:block; opacity:.92 }
  .card{ background:var(--bg-card); border-radius:12px; padding:12px;
    box-shadow:0 0 0 1px var(--border-strong), 0 6px 12px rgba(15,23,42,.05)
  }
  .pill{ padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:var(--bg-tile); font-weight:700; font-size:12px }
  .review-quote { white-space:pre-wrap; background:var(--bg-tile); border:1px solid var(--border); border-radius:10px; padding:8px 10px; font-size:13px }
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
          <div class="hero-title">Star Walk — Symptomize Reviews (v4)</div>
          <div class="hero-sub">Model-first extraction with self-consistency + validation. No hard-coded symptom rules.</div>
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
    st.subheader("⚙️ Run Settings (simplified)")
    model_choice = st.selectbox("Model", ["gpt-5", "gpt-4.1", "gpt-4o", "gpt-4o-mini"], index=0)
    n_samples = st.slider("Self-consistency samples", 1, 5, 3, 1,
                          help="Number of independent extractions to vote/merge.")
    api_concurrency = st.slider("API concurrency", 1, 16, 6)
    max_output_tokens = st.number_input("LLM max tokens", 128, 4000, 420, 10)
    use_embeddings = st.checkbox("Use semantic shortlist (embeddings)", value=True,
                                 help="Boosts recall; not rule-based.")
    emb_model = st.selectbox("Embeddings model", ["text-embedding-3-large", "text-embedding-3-small"], index=0)
    max_sentences = st.slider("Max sentences analyzed", 6, 40, 22, 2)

    st.info("No strictness knobs or keyword gates. The model extracts; we validate and rank by evidence.")

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
    st.error("Couldn't locate Symptom 1–20 columns (K–AD).")
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

# ---------------- Load symptom dictionary ----------------
import io as _io

def _norm(s: str) -> str:
    if s is None: return ""
    return re.sub(r"\s+", " ", str(s)).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    n = re.sub(r"[^a-z]+","",name.lower())
    return "symptom" in n or "palette" in n or "taxonomy" in n

def autodetect_symptom_sheet(xls: pd.ExcelFile) -> str | None:
    names = xls.sheet_names
    cands = [n for n in names if _looks_like_symptom_sheet(n)]
    if cands: return min(cands, key=len)
    return names[0] if names else None

@st.cache_data(show_spinner=False)
def load_symptom_lists_robust(raw_bytes: bytes, user_sheet: str | None = None,
                              user_del_col: str | None = None, user_det_col: str | None = None):
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

    # Guess columns by fuzzy titles
    def _col_score(colname: str, want: str) -> int:
        n = re.sub(r"[^a-z]+","",colname.lower())
        synonyms = {
            "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
            "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues","problems"],
        }
        return max((1 for token in synonyms[want] if token in n), default=0)

    best_del = None; best_det = None
    for c in s.columns:
        if _col_score(str(c), "delighters"): best_del = c if best_del is None else best_del
        if _col_score(str(c), "detractors"): best_det = c if best_det is None else best_det

    if user_del_col or user_det_col:
        dels = s.get(user_del_col, pd.Series(dtype=str)) if user_del_col in s.columns else pd.Series(dtype=str)
        dets = s.get(user_det_col, pd.Series(dtype=str)) if user_det_col in s.columns else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"manual-columns","columns":list(s.columns)})
        return dels, dets, meta

    if best_del is not None or best_det is not None:
        dels_ser = s.get(best_del, pd.Series(dtype=str)) if best_del is not None else pd.Series(dtype=str)
        dets_ser = s.get(best_det, pd.Series(dtype=str)) if best_det is not None else pd.Series(dtype=str)
        dels = [str(x).strip() for x in dels_ser.dropna().tolist() if str(x).strip()]
        dets = [str(x).strip() for x in dets_ser.dropna().tolist() if str(x).strip()]
        meta.update({"strategy":"fuzzy-headers","columns":list(s.columns)})
        return dels, dets, meta

    # Fallback: first two non-empty columns
    non_empty_cols = []
    for c in s.columns:
        vals = [str(x).strip() for x in s[c].dropna().tolist() if str(x).strip()]
        if vals:
            non_empty_cols.append((c, vals))
        if len(non_empty_cols) >= 2: break
    if non_empty_cols:
        dels = non_empty_cols[0][1]
        dets = non_empty_cols[1][1] if len(non_empty_cols) > 1 else []
        meta.update({"strategy":"first-two-nonempty","columns":[c for c,_ in non_empty_cols[:2]]})
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
    symp_cols_preview = []
    if sheet_names:
        try:
            _df_symp_prev = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name=chosen_sheet)
            symp_cols_preview = list(_df_symp_prev.columns)
        except Exception:
            _df_symp_prev = pd.DataFrame()
            symp_cols_preview = []

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, SYM_META = load_symptom_lists_robust(
    raw_bytes, user_sheet=chosen_sheet if sheet_names else None
)
ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

with st.sidebar:
    if ALLOWED_DELIGHTERS or ALLOWED_DETRACTORS:
        st.success(
            f"Loaded {len(ALLOWED_DELIGHTERS)} delighters, {len(ALLOWED_DETRACTORS)} detractors (sheet: '{SYM_META.get('sheet','?')}', mode: {SYM_META.get('strategy','?')})."
        )
    else:
        st.warning(
            f"Didn't find clear Delighters/Detractors lists in '{SYM_META.get('sheet','?')}'. Provide a palette sheet."
        )

# ========================== TOP KPIs ==========================
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<span class='pill'>🧾 Total reviews: <b>{len(df)}</b></span>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<span class='pill'>❌ Missing symptoms: <b>{missing_count}</b></span>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<span class='pill'>✂ IQR chars: <b>{int(IQR)}</b></span>", unsafe_allow_html=True)
with colD:
    st.caption("Model-first extraction. No keyword thresholds.")

left, right = st.columns([1.45, 2.55], gap="small")

with left:
    st.markdown("### Run Controller")
    batch_n = st.slider("How many to process this run", 1, 50, min(16, max(1, missing_count)) if missing_count else 16)

    # ETA (heuristic)
    MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
    rows = min(batch_n, missing_count)
    chars_est = max(200, int((q1+q3)/2)) if (q1 or q3) else 400
    tok_est = int(chars_est/4)
    rt = rows * (MODEL_LAT.get(model_choice,1.0) + tok_est/24)
    eta_secs = int(round(rt))
    st.caption(f"Will attempt {rows} rows • Rough ETA: ~{eta_secs}s")

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    if missing_count and not _HAS_OPENAI:
        st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
    if missing_count and _HAS_OPENAI and not api_key:
        st.warning("Set `OPENAI_API_KEY` (env or secrets) to enable AI labeling.")

    st.markdown("---")
    can_run = missing_count > 0 and ((not _HAS_OPENAI) or (api_key is not None))

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
            help="Processes every review that has empty Symptom 1–20. Uses many API calls.",
            use_container_width=True,
        )
    st.caption("Tip: Batch first; then run ALL if the output looks right.")

with right:
    st.markdown("### Live Processing")
    if "live_rows" not in st.session_state:
        st.session_state["live_rows"] = []  # list of rows for the live table
    live_table_ph = st.empty()
    live_progress = st.progress(0)

# ---------------- Session State (non-UI) ----------------
st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())

# ---------------- Text & evidence utils ----------------
STOP = set("""a an and the or but so of in on at to for from with without as is are was were be been being 
have has had do does did not no nor never very really quite just only almost about into out by this that these those 
it its they them i we you he she my your our their his her mine ours yours theirs""".split())
NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few","free","free-of"
}
_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+"," ", (name or "").lower()).strip()

def _tokenize_keep(words: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z0-9']+", (words or "").lower()) if w not in STOP and len(w) >= 2]

def _sentences(review: str) -> List[str]:
    parts = re.split(r'(?<=[\.!\?])\s+|\n+', (review or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 12]

def _quote_matches_text(quote: str, text: str, thresh: float = 0.62) -> bool:
    if not quote: return False
    q = _tokenize_keep(quote)
    t = _tokenize_keep(text)
    if not q or not t: return False
    overlap = len(set(q) & set(t)) / max(1, len(set(q)))
    # also accept exact substring
    return (overlap >= thresh) or (_normalize_name(quote) in _normalize_name(text))

def _context_has_negation(text_norm: str, start: int, window_chars: int = 120) -> bool:
    left_ctx = text_norm[max(0, start - window_chars): start]
    toks = _tokenize_keep(left_ctx)[-6:]
    return any(t in NEGATORS for t in toks)

def _find_quote_positions(text: str, snippet: str) -> Tuple[int,int]:
    tn = _normalize_name(text)
    qn = _normalize_name(snippet)
    i = tn.find(qn)
    if i < 0: return -1, -1
    return i, i+len(qn)

def _evidence_quality(quote: str, text: str) -> Tuple[int,int,bool]:
    """
    Returns (pos_hits, neg_hits, ok)
    We look for the quote in text (exact or normalized), then check local negation.
    """
    if not quote: return 0, 0, False
    if not _quote_matches_text(quote, text): return 0, 0, False
    tn = _normalize_name(text)
    start, end = _find_quote_positions(text, quote)
    if start < 0:
        # fallback: approximate position by scanning first occurrence of any token
        toks = _tokenize_keep(quote)
        pos = -1
        for t in toks:
            j = tn.find(_normalize_name(t))
            if j >= 0: pos = j; break
        start = pos
    if start < 0: return 1, 0, True  # treat as positive if we can't place it
    neg = 1 if _context_has_negation(tn, start) else 0
    return 1 if neg == 0 else 0, neg, (neg == 0)

# ---- OpenAI helpers ----
@st.cache_resource(show_spinner=False)
def _get_openai_client_cached(key: str):
    return OpenAI(api_key=key) if (_HAS_OPENAI and key) else None

def _chat_json(client, req, retries: int = 3, base_delay: float = 0.7):
    last_err = None
    for i in range(retries):
        try:
            out = client.chat.completions.create(**req)
            content = out.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as e:
            last_err = e
            if i == retries - 1: break
            time.sleep(base_delay * (2 ** i))
    raise last_err or RuntimeError("LLM call failed")

# ---- Embeddings (shortlist, not rule-based) ----
@st.cache_resource(show_spinner=False)
def _get_store():
    return {"label_emb": {}, "sent_emb_cache": {}, "lock": threading.Lock()}

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)

def _embed_with_retry(client, model: str, inputs: List[str], retries: int = 3, base_delay: float = 0.7):
    last_err = None
    for i in range(retries):
        try:
            out = client.embeddings.create(model=model, input=inputs)
            return [np.array(v.embedding, dtype=np.float32) for v in out.data]
        except Exception as e:
            last_err = e
            if i == retries - 1: break
            time.sleep(base_delay * (2 ** i))
    raise last_err or RuntimeError("Embedding call failed")

def _shortlist_labels_by_similarity(review: str, labels: list[str], client, emb_model: str,
                                    max_sentences: int = 22, top_k: int = 120) -> list[str]:
    store = _get_store(); lock = store["lock"]
    label_emb = store.setdefault("label_emb", {})
    sent_cache = store.setdefault("sent_emb_cache", {})

    # label embeddings
    need = [L for L in labels if L not in label_emb]
    if need and client is not None:
        embs = _embed_with_retry(client, emb_model, need)
        with lock:
            for L, e in zip(need, embs):
                label_emb[L] = e

    # sentence embeddings
    review_hash = hashlib.sha256((review or "").encode("utf-8")).hexdigest()
    with lock:
        cached = sent_cache.get(review_hash)
    if cached is None and client is not None:
        sents = _sentences(review)[:max_sentences]
        sent_embs = _embed_with_retry(client, emb_model, sents)
        pairs = list(zip(sents, sent_embs))
        with lock:
            sent_cache[review_hash] = pairs
        cached = pairs

    scored: list[tuple[str,float]] = []
    for L in labels:
        best = 0.0
        e_v = label_emb.get(L)
        if e_v is None or not cached:
            continue
        for s, e_s in cached:
            sim = _cosine(e_v, e_s)
            if sim > best: best = sim
        scored.append((L, best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [l for l,_ in scored[:top_k]] or labels[:top_k]

# ---- LLM extraction with self-consistency + validation ----
def _llm_extract(review: str, stars, allowed_del: list[str], allowed_det: list[str],
                 model_choice: str, max_output_tokens: int, n_samples: int,
                 use_embeddings: bool, emb_model: str, max_sentences: int):
    if not review or (not allowed_del and not allowed_det):
        return [], [], {}, {}

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    client = _get_openai_client_cached(api_key) if (_HAS_OPENAI and api_key) else None

    # Shortlist to reduce prompt length (not rule-based; similarity only)
    del_list = allowed_del
    det_list = allowed_det
    if use_embeddings and client is not None:
        try:
            del_list = _shortlist_labels_by_similarity(review, allowed_del, client, emb_model,
                                                       max_sentences=max_sentences, top_k=min(160, len(allowed_del)))
            det_list = _shortlist_labels_by_similarity(review, allowed_det, client, emb_model,
                                                       max_sentences=max_sentences, top_k=min(160, len(allowed_det)))
        except Exception:
            pass

    sys_prompt = """
You are a world-class review-symptom extractor.

You MUST:
- Choose only from the provided "allowed_delighters" and "allowed_detractors".
- Use semantics (synonyms/paraphrases are OK) to map the review to those labels.
- Treat negation correctly ("not loud" is NOT "Loud").
- Provide up to 10 items per group.
- For each item, include a SHORT evidence quote (ideally exact words from the review).
- If a verbatim excerpt is not exact, it may be a paraphrase, but mark "paraphrase": true.

Output STRICT JSON:
{
 "delighters":[{"name":"", "confidence":0.0, "quote":"", "paraphrase":false}],
 "detractors":[{"name":"", "confidence":0.0, "quote":"", "paraphrase":false}]
}
Confidence ∈ [0,1]. Be precise; avoid stretches.
"""
    user_payload = {
        "review": review,
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": del_list[:200],
        "allowed_detractors": det_list[:200]
    }

    # --- Multiple independent extractions (self-consistency) ---
    samples: List[Dict[str,Any]] = []
    if client is not None:
        for k in range(n_samples):
            req = {
                "model": model_choice,
                "messages": [
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": json.dumps(user_payload)}
                ],
                "response_format": {"type":"json_object"},
                "max_tokens": max_output_tokens,
                # tiny randomness across samples; GPT-5 handles this well
                "temperature": 0.2 + 0.1 * (k % 3)
            }
            try:
                data = _chat_json(client, req)
                samples.append(data)
            except Exception:
                pass

    # Fallback: no API or failures
    if not samples:
        return [], [], {}, {}

    # --- Merge votes and validate evidence ---
    vote_del: Dict[str, list[Tuple[float, str, bool]]] = defaultdict(list)
    vote_det: Dict[str, list[Tuple[float, str, bool]]] = defaultdict(list)

    for s in samples:
        for gkey, bucket in [("delighters","delighters"), ("detractors","detractors")]:
            for it in s.get(gkey, []) or []:
                name = _norm(it.get("name",""))
                if not name: continue
                conf = float(it.get("confidence", 0))
                quote = _norm(it.get("quote",""))
                paraphrase = bool(it.get("paraphrase", False))
                if gkey == "delighters":
                    vote_del[name].append((conf, quote, paraphrase))
                else:
                    vote_det[name].append((conf, quote, paraphrase))

    def aggregate(votes: Dict[str, list[Tuple[float,str,bool]]], allowed_set: set[str]) -> Tuple[List[Tuple[str,float]], Dict[str,List[str]]]:
        results: List[Tuple[str,float]] = []
        evmap: Dict[str,List[str]] = {}
        for raw_name, triples in votes.items():
            # normalize against allowed set by best fuzzy match on case/space
            # (labels are short; simple exact-insensitive match works well)
            matches = [a for a in allowed_set if _normalize_name(a) == _normalize_name(raw_name)]
            if not matches:
                # if model returns slightly different casing/spacing, keep anyway if close
                matches = [a for a in allowed_set if _normalize_name(raw_name) in _normalize_name(a) or _normalize_name(a) in _normalize_name(raw_name)]
            if not matches:
                continue
            name = matches[0]

            votes_n = len(triples)
            conf_mean = float(np.mean([t[0] for t in triples])) if triples else 0.0

            # evidence check across all quotes; keep the best one
            best_ev_score = -1.0
            best_quote = ""
            pos_total = 0; neg_total = 0
            for _, q, _ in triples:
                pos, neg, ok = _evidence_quality(q, review)
                pos_total += pos; neg_total += neg
                if ok:
                    # score: positive hit + longer quotes (a little) + overlap boost
                    tok_overlap = len(set(_tokenize_keep(q)) & set(_tokenize_keep(review)))
                    score = 1.0 + 0.05 * min(tok_overlap, 12)
                    if score > best_ev_score:
                        best_ev_score = score
                        best_quote = q
            # if none of the quotes validated, try deriving one from first sample tokens
            if best_ev_score < 0 and triples:
                q = triples[0][1]
                if _quote_matches_text(q, review):
                    best_quote = q
                    best_ev_score = 1.0

            # final confidence: blend of vote share, model conf, and evidence
            vote_share = votes_n / max(1, n_samples)
            ev_bonus = 0.15 if best_ev_score > 0 else 0.0
            neg_penalty = 0.25 if neg_total >= pos_total and (pos_total > 0) else 0.0
            final_conf = max(0.0, min(1.0, 0.45*vote_share + 0.45*conf_mean + ev_bonus - neg_penalty))

            if final_conf >= 0.55 and best_ev_score >= 0:
                results.append((name, final_conf))
                if best_quote:
                    evmap.setdefault(name, []).append(best_quote)

        # sort by confidence
        results.sort(key=lambda x: -x[1])
        return results[:10], evmap

    dels_pairs, dels_evmap = aggregate(vote_del, set(allowed_del))
    dets_pairs, dets_evmap = aggregate(vote_det, set(allowed_det))

    # star-aware tie-break (soft)
    if stars and pd.notna(stars):
        s = float(stars)
        if s >= 4.5:
            dels_pairs = [(n, min(1.0, c + 0.05)) for n,c in dels_pairs]
        elif s <= 2.0:
            dets_pairs = [(n, min(1.0, c + 0.05)) for n,c in dets_pairs]

    # final lists + combined ev map
    evmap = {}
    evmap.update(dels_evmap); evmap.update(dets_evmap)
    return [n for n,_ in dels_pairs], [n for n,_ in dets_pairs], evmap, {
        "del_pairs": dels_pairs, "det_pairs": dets_pairs
    }

# ---------------- Live rendering helpers ----------------
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

def _render_chips(kind: str, names: list[str], evidence_map: Dict[str, List[str]]):
    if not names:
        st.code("-")
        return
    html = "<div class='chips'>"
    for x in names:
        evs = [e for e in (evidence_map.get(x, []) or []) if e]
        ev_html = (
            f"<span class='evd'>{(evs[0][:160] + '...' if len(evs[0])>160 else evs[0])}</span>"
            if evs else ""
        )
        cls = "pos" if kind == "delighters" else "neg"
        html += f"<span class='chip {cls}'>{x}{ev_html}</span>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# ---------------- Run Symptomize (parallel) ----------------
if (run or run_all) and missing_idx:
    missing_idx_sorted = list(missing_idx)
    todo = missing_idx_sorted if run_all else missing_idx_sorted[:batch_n]

    st.session_state["live_rows"] = [
        [
            int(idx),
            (float(df.loc[idx].get("Star Rating")) if pd.notna(df.loc[idx].get("Star Rating")) else None),
            int(len(str(df.loc[idx].get("Verbatim", "")))),
            0, 0,
            "queued",
        ]
        for idx in todo
    ]
    _render_live_table()

    def _process_one(idx: int):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        return idx, _llm_extract(
            review_txt,
            stars,
            ALLOWED_DELIGHTERS,
            ALLOWED_DETRACTORS,
            model_choice=model_choice,
            max_output_tokens=max_output_tokens,
            n_samples=n_samples,
            use_embeddings=use_embeddings,
            emb_model=emb_model,
            max_sentences=max_sentences
        )

    with st.status("Processing reviews...", expanded=True) as status_box:
        completed = 0
        errors = 0
        live_progress.progress(0.0)
        if st.session_state["live_rows"]:
            st.session_state["live_rows"][0][-1] = "processing"
            _render_live_table()

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=api_concurrency) as ex:
            futures = {ex.submit(_process_one, idx): idx for idx in todo}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    idx, (dels, dets, evidence_map, debug_pairs) = fut.result()
                    # Update live table
                    try:
                        row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                    except ValueError:
                        row_pos = None
                    if row_pos is not None:
                        st.session_state["live_rows"][row_pos][3] = len(dets)
                        st.session_state["live_rows"][row_pos][4] = len(dels)
                        st.session_state["live_rows"][row_pos][-1] = "done"

                    # Persist suggestions
                    st.session_state["symptom_suggestions"].append({
                        "row_index": int(idx),
                        "stars": float(df.loc[idx].get("Star Rating"))
                            if pd.notna(df.loc[idx].get("Star Rating")) else None,
                        "review": str(df.loc[idx].get("Verbatim", "") or "").strip(),
                        "delighters": dels,
                        "detractors": dets,
                        "evidence_map": evidence_map,
                    })

                except Exception:
                    errors += 1
                    try:
                        row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                        st.session_state["live_rows"][row_pos][-1] = "error"
                    except Exception:
                        pass
                finally:
                    completed += 1
                    live_progress.progress(completed / max(1, len(todo)))
                    _render_live_table()

        status_box.update(
            label="Finished generating suggestions! Review below, then Apply to write into the sheet.",
            state="complete",
        )
    st.rerun()

# ---------------- Review & Apply ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## Review & Approve Suggestions")

    with st.expander("Palettes (from Symptoms sheet)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Allowed Detractors** ({len(ALLOWED_DETRACTORS)}):")
            st.markdown(
                "<div class='chips'>" + "".join(
                    [f"<span class='chip neg'>{x}</span>" for x in ALLOWED_DETRACTORS]
                ) + "</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(f"**Allowed Delighters** ({len(ALLOWED_DELIGHTERS)}):")
            st.markdown(
                "<div class='chips'>" + "".join(
                    [f"<span class='chip pos'>{x}</span>" for x in ALLOWED_DELIGHTERS]
                ) + "</div>",
                unsafe_allow_html=True,
            )

    with st.expander("Bulk actions", expanded=True):
        c1, c2, c3, c4 = st.columns([1, 1, 2, 3], gap="small")
        total = len(sugs)
        with c1:
            if st.button("Select all"):
                st.session_state["sug_selected"] = set(range(total))
                for i in range(total):
                    st.session_state[f"sel_{i}"] = True
        with c2:
            if st.button("Clear all"):
                st.session_state["sug_selected"] = set()
                for i in range(total):
                    st.session_state[f"sel_{i}"] = False
        with c3:
            if st.button("Only rows with suggestions"):
                keep = {i for i, s in enumerate(sugs) if s["delighters"] or s["detractors"]}
                st.session_state["sug_selected"] = keep
                for i in range(total):
                    st.session_state[f"sel_{i}"] = (i in keep)
        with c4:
            max_apply = st.slider("Max rows to apply now", 1, total, min(20, total))

    for i, s in enumerate(sugs):
        label = (
            f"Review #{i} • Stars: {s.get('stars','-')} • "
            f"{len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        )
        with st.expander(label, expanded=(i == 0)):
            default_checked = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
            checked = st.checkbox("Select for apply", value=default_checked, key=f"sel_{i}")
            if checked:
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            st.markdown("**Full review:**")
            st.markdown(f"<div class='review-quote'>{s['review']}</div>", unsafe_allow_html=True)

            c1, c2 = st.columns(2)
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

    # Try to preserve original formatting using openpyxl
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = "Star Walk scrubbed verbatims"
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]

            # Build header map
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
            def col_idx(name): return headers.get(name)

            # Write back only the Symptoms columns
            for df_row_idx, row in df.reset_index(drop=True).iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci:
                        ws.cell(row=excel_row, column=ci).value = row.get(c, None)

            out = io.BytesIO()
            wb.save(out)
            st.download_button(
                "Download updated workbook (.xlsx)",
                data=out.getvalue(),
                file_name="StarWalk_updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            return
        except Exception:
            pass  # fall through to simple writer

    # Simple writer (no formatting)
    out2 = io.BytesIO()
    with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
    st.download_button(
        "Download updated workbook (.xlsx) — no formatting",
        data=out2.getvalue(),
        file_name="StarWalk_updated_basic.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

offer_downloads()
