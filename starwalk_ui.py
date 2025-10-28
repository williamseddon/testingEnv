# -*- coding: utf-8 -*-
# Star Walk — Symptomize Reviews (v5, model-first • one-review-per-call • timeouts & health check)
# To run:
#   pip install streamlit openpyxl openai pandas
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_walk_app_v5.py

import io
import os
import re
import json
import time
import hashlib
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, as_completed
from collections import defaultdict

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# ===== Optional OpenAI =====
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# ===== Optional openpyxl (preserve formatting) =====
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False


# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk — Symptomize (v5)")

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

# ---------------- Global CSS ----------------
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
          <div class="hero-title">Star Walk — Symptomize Reviews (v5)</div>
          <div class="hero-sub">One-review-per-call · strict JSON · timeouts · no hard-coded synonyms</div>
        </div>
        <div class="hero-right"><img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"/></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- Helpers (general) ----------------
def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    n = _normalize(name)
    return any(tok in n for tok in ["symptom","palette","taxonomy","glossi"])

def _find_symptoms_sheet(xls: pd.ExcelFile) -> str:
    cands = [n for n in xls.sheet_names if _looks_like_symptom_sheet(n)]
    if cands:
        return min(cands, key=len)
    return xls.sheet_names[0]

def _load_symptom_lists_from_excel_bytes(raw_bytes: bytes) -> Tuple[List[str], List[str], str, List[str]]:
    xls = pd.ExcelFile(io.BytesIO(raw_bytes))
    sheet = _find_symptoms_sheet(xls)
    df = pd.read_excel(xls, sheet_name=sheet)

    def score(col: str, want: str) -> int:
        n = _normalize(col)
        wants = {
            "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
            "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues","problems"],
        }
        return max((1 for t in wants[want] if t in n), default=0)

    del_col = None; det_col = None
    for c in df.columns:
        if del_col is None and score(str(c), "delighters"):
            del_col = c
        if det_col is None and score(str(c), "detractors"):
            det_col = c

    if del_col is None or det_col is None:
        non_empty = []
        for c in df.columns:
            vals = [str(x).strip() for x in df[c].dropna().tolist() if str(x).strip()]
            if vals: non_empty.append(c)
            if len(non_empty) >= 2: break
        if del_col is None and non_empty: del_col = non_empty[0]
        if det_col is None and len(non_empty) > 1: det_col = non_empty[1]

    dels = [str(x).strip() for x in df.get(del_col, pd.Series(dtype=str)).dropna().tolist() if str(x).strip()]
    dets = [str(x).strip() for x in df.get(det_col, pd.Series(dtype=str)).dropna().tolist() if str(x).strip()]

    # de-dup preserving order
    def dedupe(seq: List[str]) -> List[str]:
        seen = set(); out = []
        for x in seq:
            if x not in seen:
                seen.add(x); out.append(x)
        return out

    return dedupe(dels), dedupe(dets), sheet, list(map(str, df.columns))

def _find_review_sheet_and_columns(raw_bytes: bytes) -> Tuple[str, str, str]:
    xls = pd.ExcelFile(io.BytesIO(raw_bytes))
    preferred = [
        "Star Walk scrubbed verbatims","Reviews","Verbatims","Data",
        xls.sheet_names[0]
    ]
    sheet = None
    for cand in preferred:
        if cand in xls.sheet_names:
            sheet = cand; break
    sheet = sheet or xls.sheet_names[0]
    df = pd.read_excel(xls, sheet_name=sheet)

    # find review text column
    text_candidates = ["Verbatim","Review Text","review","text","comments","feedback"]
    rev_col = None
    for c in df.columns:
        n = _normalize(str(c))
        if any(tc in n for tc in [_normalize(x) for x in text_candidates]):
            rev_col = c; break
    if rev_col is None:
        lens = df.astype(str).applymap(len).sum().sort_values(ascending=False)
        rev_col = lens.index[0]

    star_col = ""
    for c in df.columns:
        n = _normalize(str(c))
        if any(tok in n for tok in ["star","stars","rating","score"]):
            star_col = c; break
    return sheet, rev_col, star_col

def _clean_review_text(s: str) -> str:
    if s is None: return ""
    s = str(s)
    replacements = {
        "â€™": "’", "Ã—": "×", "â€“": "–", "â€”": "—", "â€œ": "“", "â€": "”", "â€¦": "…", "Â": "",
        "Ita€™s": "It’s", "doesnd€™t": "doesn’t", "Ia€™m": "I’m",
    }
    for k,v in replacements.items(): s = s.replace(k,v)
    return s

NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few","free","free-of"
}

def _detect_negated_quote(quote: str, full_text: str) -> bool:
    if not quote: return False
    qn = _normalize(quote); tn = _normalize(full_text)
    i = tn.find(qn)
    if i < 0:
        toks = [t for t in qn.split() if len(t) > 2]
        for t in toks:
            j = tn.find(t)
            if j >= 0: i = j; break
    if i < 0: return False
    left_ctx = tn[max(0, i-120): i]
    toks = [t for t in left_ctx.split() if len(t) > 1][-8:]
    return any(t in NEGATORS for t in toks)

# ---------------- Prompting ----------------
PROMPT_INSTRUCTIONS = """You are Shark Glossi Review Analyzer, an AI assistant designed to evaluate and process customer reviews for the Shark Glossi (similar to SmoothStyle) hot tool.

Your job is to extract and clearly list all delighters and detractors from the review, using only the predefined items in the provided lists. Use semantics: synonyms and paraphrases may map to the closest item from the list, but DO NOT invent new items or use labels not present in the lists.

If the review mentions a concept only to state it did NOT occur (e.g., "no overheating"), do NOT mark that as a detractor.

Return STRICT JSON with this schema:
{
  "delighters": [
    {"name": "<item from delighters list>", "quote": "<short evidence snippet from the review>", "confidence": "High|Medium|Low"}
  ],
  "detractors": [
    {"name": "<item from detractors list>", "quote": "<short evidence snippet from the review>", "confidence": "High|Medium|Low"}
  ],
  "clarifications": "<optional short notes about edge cases or conflicts>",
  "confidence_overall": "High|Medium|Low"
}

Rules:
- Only choose items from the provided lists below.
- Prefer precision over recall; avoid stretches.
- Include a short verbatim evidence snippet for each selected item (exact or close paraphrase).
- If uncertain about a possible item, you may include it with confidence = "Low"; do not invent items that are not in the lists.
"""

def _build_system_message(delighters: List[str], detractors: List[str]) -> str:
    dlist = "\n".join(f"- {x}" for x in delighters)
    tlist = "\n".join(f"- {x}" for x in detractors)
    return (
        PROMPT_INSTRUCTIONS
        + "\n\n🟢 Delighters List (Look for positive mentions of):\n" + dlist
        + "\n\n🔴 Detractors List (Look for negative mentions of):\n" + tlist + "\n"
    )

def _build_user_message(review_text: str, stars: Any = None) -> str:
    s = f"Review:\n\"\"\"\n{review_text.strip()}\n\"\"\"\n"
    if stars is not None and stars != "":
        s += f"\nStar Rating: {stars}\n"
    s += "\nExtract all applicable delighters and detractors from the lists."
    return s

def _confidence_to_score(c: str) -> float:
    c = (c or "").strip().lower()
    if c.startswith("h"): return 0.9
    if c.startswith("m"): return 0.6
    if c.startswith("l"): return 0.3
    return 0.5

# ---------------- OpenAI helpers ----------------
@st.cache_resource(show_spinner=False)
def _get_openai_client_cached(key: str):
    return OpenAI(api_key=key) if (_HAS_OPENAI and key) else None

def _call_openai_for_review(client, model: str, system_prompt: str, user_prompt: str,
                            n_samples: int = 2, max_tokens: int = 700, timeout_s: int = 45) -> Dict[str, Any]:
    samples: List[Dict[str, Any]] = []
    for k in range(max(1, n_samples)):
        req = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.2 + 0.1 * (k % 2),
            "timeout": timeout_s,
        }
        out = client.chat.completions.create(**req)
        content = out.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except Exception:
            data = {"delighters": [], "detractors": [], "clarifications": "", "confidence_overall": "Medium"}
        data.setdefault("delighters", []); data.setdefault("detractors", [])
        data.setdefault("clarifications", ""); data.setdefault("confidence_overall", "Medium")
        samples.append(data)

    # Merge by vote + mean confidence; drop negated quotes lightly
    agg_del: Dict[str, List[Tuple[float,str]]] = defaultdict(list)
    agg_det: Dict[str, List[Tuple[float,str]]] = defaultdict(list)
    clar_notes: List[str] = []; overall_scores: List[float] = []

    for s in samples:
        clar = (s.get("clarifications") or "").strip()
        if clar: clar_notes.append(clar)
        overall_scores.append(_confidence_to_score(s.get("confidence_overall","Medium")))
        for bucket, target in [("delighters", agg_del), ("detractors", agg_det)]:
            for it in s.get(bucket, []) or []:
                name = (it.get("name") or "").strip()
                quote = (it.get("quote") or "").strip()
                conf = _confidence_to_score(it.get("confidence") or "Medium")
                if not name: continue
                target[name].append((conf, quote))

    # get review text back for negation guard
    m = re.search(r'Review:\s*"""(.*?)"""', user_prompt, flags=re.DOTALL)
    review_text = m.group(1) if m else user_prompt

    def finalize(agg: Dict[str, List[Tuple[float,str]]]) -> List[Dict[str, Any]]:
        out = []
        for name, lst in agg.items():
            confs = [c for c,_ in lst]
            quotes = [q for _,q in lst if q]
            penalty = 0.0
            for q in quotes:
                if _detect_negated_quote(q, review_text):
                    penalty = max(penalty, 0.1)
            score = max(0.0, min(1.0, sum(confs)/max(1,len(confs)) - penalty))
            out.append({"name": name, "score": score, "quotes": quotes[:2]})
        out.sort(key=lambda x: (-x["score"], x["name"]))
        return out[:10]

    return {
        "delighters": finalize(agg_del),
        "detractors": finalize(agg_det),
        "clarifications": " | ".join(clar_notes)[:2000],
        "confidence_overall": ("High" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.75
                               else ("Medium" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.5 else "Low"))
    }

# ---------------- Excel write helpers ----------------
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21)]

def _write_symptoms_into_workbook_bytes(raw_bytes: bytes, review_sheet: str,
                                        df: pd.DataFrame, results: Dict[int, Dict[str, Any]]) -> bytes:
    if not _HAS_OPENPYXL:
        # fallback: rebuild with pandas (loses formatting)
        out = io.BytesIO()
        # Write updated df first (we only change symptom cols)
        for idx, res in results.items():
            dets = [x["name"] for x in res.get("detractors", [])][:10]
            dels = [x["name"] for x in res.get("delighters", [])][:10]
            for j, name in enumerate(dets, start=1):
                col = f"Symptom {j}"
                if col in df.columns:
                    df.at[idx, col] = name
            for j, name in enumerate(dels, start=11):
                col = f"Symptom {j}"
                if col in df.columns:
                    df.at[idx, col] = name
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name=review_sheet)
        return out.getvalue()

    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    if review_sheet not in wb.sheetnames:
        review_sheet = wb.sheetnames[0]
    ws = wb[review_sheet]

    headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
    # ensure symptom columns exist
    last_col = ws.max_column
    for name in SYMPTOM_COLS:
        if name not in headers or headers[name] is None:
            last_col += 1
            ws.cell(row=1, column=last_col).value = name
            headers[name] = last_col

    # write rows
    for df_row_idx, res in results.items():
        excel_row = 2 + df_row_idx
        # clear existing
        for i in range(1, 21):
            ci = headers.get(f"Symptom {i}")
            if ci: ws.cell(row=excel_row, column=ci).value = None
        # write dets 1..10
        dets = [x["name"] for x in res.get("detractors", [])][:10]
        for j, name in enumerate(dets, start=1):
            ci = headers.get(f"Symptom {j}")
            if ci: ws.cell(row=excel_row, column=ci).value = name
        # write dels 11..20
        dels = [x["name"] for x in res.get("delighters", [])][:10]
        for j, name in enumerate(dels, start=11):
            ci = headers.get(f"Symptom {j}")
            if ci: ws.cell(row=excel_row, column=ci).value = name

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def _write_review_tagging_sheet(raw_bytes: bytes, tagging_rows: List[Dict[str, Any]]) -> bytes:
    if not _HAS_OPENPYXL:
        return raw_bytes  # skip if we can't edit in place
    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    if "Review Tagging" in wb.sheetnames:
        del wb["Review Tagging"]
    ws = wb.create_sheet("Review Tagging")

    headers = ["row_index","delighters","detractors","clarifications","confidence_overall","evidence_examples"]
    for ci, h in enumerate(headers, start=1):
        ws.cell(row=1, column=ci).value = h

    for ri, row in enumerate(tagging_rows, start=2):
        dels = ", ".join([x["name"] for x in row.get("delighters", [])])
        dets = ", ".join([x["name"] for x in row.get("detractors", [])])
        quotes = []
        for x in row.get("delighters", []):
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DEL] {x['name']}: {q}")
        for x in row.get("detractors", []):
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DET] {x['name']}: {q}")
        values = [
            row.get("row_index"),
            dels,
            dets,
            row.get("clarifications",""),
            row.get("confidence_overall",""),
            " | ".join(quotes)
        ]
        for ci, v in enumerate(values, start=1):
            ws.cell(row=ri, column=ci).value = v

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------- Sidebar / Controls ----------------
with st.sidebar:
    st.header("📁 Upload Star Walk File")
    uploaded = st.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

    st.markdown("---")
    st.subheader("⚙️ Run Settings")
    model_choice = st.selectbox("Model", ["gpt-4o", "gpt-4.1", "gpt-5", "gpt-4o-mini"], index=0)
    n_samples = st.slider("Self-consistency samples", 1, 5, 2, 1,
                          help="Independent extractions to merge for robustness.")
    api_concurrency = st.slider("API concurrency", 1, 8, 3)
    max_output_tokens = st.number_input("LLM max tokens", 128, 2000, 700, 10)
    st.caption("Tip: start small (samples=1–2, concurrency=1–3) to validate, then scale.")

    st.markdown("---")
    if st.button("🩺 Health check (key/model/connectivity)"):
        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        if not api_key:
            st.error("OPENAI_API_KEY is missing. Set it in env or `.streamlit/secrets.toml`.")
        else:
            try:
                client = _get_openai_client_cached(api_key)
                ping = client.chat.completions.create(
                    model=model_choice,
                    messages=[{"role":"system","content":"ping"}, {"role":"user","content":"ping"}],
                    max_tokens=1,
                    temperature=0,
                    timeout=20,
                )
                st.success(f"Health OK • model `{model_choice}` reachable • key present")
            except Exception as e:
                st.exception(e)

# Persist raw bytes for formatting-preserving save
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if _HAS_OPENAI and not api_key:
    st.error("OPENAI_API_KEY not set. The app cannot call the model without it.")
    st.stop()

# ---------------- Load sheets & columns ----------------
raw_bytes = st.session_state.get("uploaded_bytes", b"")
try:
    delighters, detractors, symp_sheet, symp_cols_preview = _load_symptom_lists_from_excel_bytes(raw_bytes)
except Exception as e:
    st.error(f"Failed to read Symptoms tab: {e}")
    st.stop()

try:
    review_sheet, review_col, star_col = _find_review_sheet_and_columns(raw_bytes)
    df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=review_sheet)
except Exception as e:
    st.error(f"Failed to read reviews: {e}")
    st.stop()

# Identify Symptom columns in df
explicit_cols = [f"Symptom {i}" for i in range(1,21)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()
if not SYMPTOM_COLS:
    # create placeholders in DF so we can write then export (if no openpyxl)
    for c in explicit_cols:
        df[c] = None
    SYMPTOM_COLS = explicit_cols

# Missing symptom rows
is_empty = df[SYMPTOM_COLS].isna() | (
    df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"})
)
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Review length IQR for ETA
verb_series = df.get(review_col, pd.Series(dtype=str)).fillna("").astype(str)
q1 = verb_series.str.len().quantile(0.25) if not verb_series.empty else 0
q3 = verb_series.str.len().quantile(0.75) if not verb_series.empty else 0
IQR = (q3 - q1) if (q3 or q1) else 0

# ---------------- TOP KPIs ----------------
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<span class='pill'>🧾 Total reviews: <b>{len(df)}</b></span>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<span class='pill'>❌ Missing symptoms: <b>{missing_count}</b></span>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<span class='pill'>✂ IQR chars: <b>{int(IQR)}</b></span>", unsafe_allow_html=True)
with colD:
    st.caption(f"Symptoms sheet: “{symp_sheet}”. Model: {model_choice}.")

left, right = st.columns([1.45, 2.55], gap="small")

with left:
    st.markdown("### Run Controller")
    batch_n = st.slider("How many to process this run", 1, 50, min(16, max(1, missing_count)) if missing_count else 16)

    # Rough ETA
    MODEL_LAT = {"gpt-4o-mini": 0.6, "gpt-4o": 0.9, "gpt-4.1": 1.1, "gpt-5": 1.3}
    rows = min(batch_n, missing_count)
    chars_est = max(200, int((q1+q3)/2)) if (q1 or q3) else 400
    tok_est = int(chars_est/4)
    rt = rows * (MODEL_LAT.get(model_choice,1.0) + tok_est/24)
    eta_secs = int(round(rt))
    st.caption(f"Will attempt {rows} rows • Rough ETA: ~{eta_secs}s")

    st.markdown("---")
    can_run = missing_count > 0 and ((_HAS_OPENAI and api_key) or (not _HAS_OPENAI))

    col_runA, col_runB = st.columns([1,1])
    with col_runA:
        run = st.button(
            f"✨ Symptomize next {min(batch_n, missing_count)}",
            disabled=not can_run,
            use_container_width=True,
        )
    with col_runB:
        enable_all = st.checkbox("Enable ALL (bulk)")
        run_all = st.button(
            f"⚡ Symptomize ALL {missing_count}",
            disabled=(not can_run) or missing_count==0 or (not enable_all),
            use_container_width=True,
        )
    st.caption("One review = one API call (fresh prompt). No hard-coded synonyms; lists come from the Symptoms tab.")

with right:
    st.markdown("### Live Processing")
    if "live_rows" not in st.session_state:
        st.session_state["live_rows"] = []  # [Row #, Stars, Len, Dets #, Dels #, Status]
    live_table_ph = st.empty()
    live_progress = st.progress(0)

# ---------------- Session State ----------------
st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("did_rerun", False)

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

# ---------------- Processing ----------------
def _process_one(idx: int, client, system_msg: str):
    row = df.loc[idx]
    review_txt = _clean_review_text(str(row.get(review_col, "") or ""))
    stars = row.get(star_col, "") if star_col else ""
    user_msg = _build_user_message(review_txt, stars)
    data = _call_openai_for_review(
        client=client,
        model=model_choice,
        system_prompt=system_msg,
        user_prompt=user_msg,
        n_samples=int(n_samples),
        max_tokens=int(max_output_tokens),
        timeout_s=45,
    )
    # Build simple maps for chips renderer (first quote)
    evmap: Dict[str, List[str]] = {}
    for x in data.get("delighters", []):
        if x.get("quotes"): evmap.setdefault(x["name"], []).append(x["quotes"][0])
    for x in data.get("detractors", []):
        if x.get("quotes"): evmap.setdefault(x["name"], []).append(x["quotes"][0])
    del_names = [x["name"] for x in data.get("delighters", [])]
    det_names = [x["name"] for x in data.get("detractors", [])]
    return idx, del_names, det_names, evmap, data

if (run or run_all) and missing_idx:
    # Health: in-flight soft cap
    inflight = int(api_concurrency) * int(n_samples)
    if inflight > 6:
        st.warning(f"In-flight calls = {inflight}. Lower Samples/Concurrency to ≤ 6 during testing to avoid rate limits.")

    client = _get_openai_client_cached(api_key)
    if not client:
        st.error("OpenAI client unavailable.")
        st.stop()

    # System message once per batch (lists from Symptoms tab)
    system_msg = _build_system_message(delighters, detractors)

    # Queue
    missing_idx_sorted = list(missing_idx)
    todo = missing_idx_sorted if run_all else missing_idx_sorted[:batch_n]

    st.session_state["live_rows"] = [
        [
            int(idx),
            (float(df.loc[idx].get(star_col)) if (star_col and pd.notna(df.loc[idx].get(star_col))) else None),
            int(len(str(df.loc[idx].get(review_col, "")))),
            0, 0, "queued",
        ]
        for idx in todo
    ]
    _render_live_table()

    # Run with a responsive loop
    results_by_idx: Dict[int, Dict[str, Any]] = {}
    tagging_rows: List[Dict[str, Any]] = []

    with st.status("Processing reviews...", expanded=True) as status_box:
        completed = 0
        live_progress.progress(0.0)
        if st.session_state["live_rows"]:
            st.session_state["live_rows"][0][-1] = "processing"
            _render_live_table()

        with ThreadPoolExecutor(max_workers=api_concurrency) as ex:
            futures = {ex.submit(_process_one, idx, client, system_msg): idx for idx in todo}

            # heartbeat loop prevents spinner lock if a call stalls
            while futures:
                done, not_done = wait(list(futures.keys()), timeout=15, return_when=FIRST_COMPLETED)
                if not done:
                    # heartbeat UI tick
                    p = min(0.99, (completed / max(1, len(todo))) + 0.001)
                    live_progress.progress(p)
                    _render_live_table()
                    continue

                for fut in done:
                    idx = futures.pop(fut)
                    try:
                        idx, dels, dets, evidence_map, raw = fut.result()
                        # Update live row
                        try:
                            row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                        except ValueError:
                            row_pos = None
                        if row_pos is not None:
                            st.session_state["live_rows"][row_pos][3] = len(dets)
                            st.session_state["live_rows"][row_pos][4] = len(dels)
                            st.session_state["live_rows"][row_pos][-1] = "done"
                        # Store for review/apply
                        st.session_state["symptom_suggestions"].append({
                            "row_index": int(idx),
                            "stars": float(df.loc[idx].get(star_col)) if (star_col and pd.notna(df.loc[idx].get(star_col))) else None,
                            "review": str(df.loc[idx].get(review_col, "") or "").strip(),
                            "delighters": dels,
                            "detractors": dets,
                            "evidence_map": evidence_map,
                        })
                        results_by_idx[idx] = raw
                        tagging_rows.append({"row_index": idx, **raw})
                    except Exception as e:
                        try:
                            row_pos = [r[0] for r in st.session_state["live_rows"]].index(int(idx))
                            st.session_state["live_rows"][row_pos][-1] = "error"
                        except Exception:
                            pass
                        st.write(f"Row {idx} failed: {e}")
                    finally:
                        completed += 1
                        live_progress.progress(completed / max(1, len(todo)))
                        _render_live_table()

        status_box.update(label="Finished generating suggestions! Review below, then Apply to write into the sheet.", state="complete")

    # Write back to a new workbook blob (in-memory) for download
    try:
        updated_bytes = _write_symptoms_into_workbook_bytes(raw_bytes, review_sheet, df.copy(), results_by_idx)
        updated_bytes = _write_review_tagging_sheet(updated_bytes, tagging_rows)
        st.session_state["updated_bytes"] = updated_bytes
    except Exception as e:
        st.warning(f"Could not prepare an updated workbook download: {e}")

    if not st.session_state["did_rerun"]:
        st.session_state["did_rerun"] = True
        st.rerun()

# ---------------- Review & Apply (in-app) ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## Review & Approve Suggestions")

    with st.expander("Palettes (from Symptoms sheet)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Allowed Detractors** ({len(detractors)}):")
            st.markdown("<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in detractors]) + "</div>", unsafe_allow_html=True)
        with c2:
            st.markdown(f"**Allowed Delighters** ({len(delighters)}):")
            st.markdown("<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in delighters]) + "</div>", unsafe_allow_html=True)

    with st.expander("Bulk actions", expanded=True):
        c1, c2, c3, c4 = st.columns([1, 1, 2, 3], gap="small")
        total = len(sugs)
        with c1:
            if st.button("Select all"):
                st.session_state["sug_selected"] = set(range(total))
                for i in range(total): st.session_state[f"sel_{i}"] = True
        with c2:
            if st.button("Clear all"):
                st.session_state["sug_selected"] = set()
                for i in range(total): st.session_state[f"sel_{i}"] = False
        with c3:
            if st.button("Only rows with suggestions"):
                keep = {i for i, s in enumerate(sugs) if s["delighters"] or s["detractors"]}
                st.session_state["sug_selected"] = keep
                for i in range(total): st.session_state[f"sel_{i}"] = (i in keep)
        with c4:
            max_apply = st.slider("Max rows to apply now", 1, total, min(20, total))

    for i, s in enumerate(sugs):
        label = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i == 0)):
            default_checked = st.session_state.get(f"sel_{i}", i in st.session_state["sug_selected"])
            checked = st.checkbox("Select for apply", value=default_checked, key=f"sel_{i}")
            if checked: st.session_state["sug_selected"].add(i)
            else: st.session_state["sug_selected"].discard(i)

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
st.markdown("### Download Updated Workbook")
if "updated_bytes" in st.session_state:
    st.download_button(
        "Download updated workbook (.xlsx)",
        data=st.session_state["updated_bytes"],
        file_name="StarWalk_updated.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.caption("Run a batch first to enable download.")

