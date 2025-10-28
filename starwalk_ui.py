# star_walk_app.py
# Symptomize v3 – Full Excel Dashboard (openpyxl + formatting preserved)
# ---------------------------------------------------------------
#   pip install streamlit openpyxl openai pandas numpy tqdm
#   export OPENAI_API_KEY=…
#   streamlit run star_walk_app.py
# ---------------------------------------------------------------

import io
import os
import re
import json
import hashlib
import itertools
import threading
import difflib
from typing import List, Tuple, Dict, Any

import numpy as np
import pandas as pd
import streamlit as st
from openai import OpenAI
from openpyxl import load_workbook

# ----------------------------------------------------------------------
# 1. GLOBALS & CANONICAL MAPS
# ----------------------------------------------------------------------
STOP = set(
    """a an and the or but so of in on at to for from with without as is are was were be been being
    have has had do does did not no nor never very really quite just only almost about into out by this that these those
    it its they them i we you he she my your our their his her mine ours yours theirs""".split()
)

NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few"
}

ALIAS_CANON = {
    "initial difficulty": "Learning curve","hard to learn": "Learning curve","setup difficulty": "Learning curve",
    "noisy startup": "Startup noise","too loud": "Loud","odor": "Smell","odour": "Smell","smelly": "Smell",
    "hot": "Heat","too hot": "Heat","gets hot": "Heat","overheats": "Heat","heavy": "Weight","bulky": "Size",
    "fragile": "Durability","breaks": "Durability","broke": "Durability","brittle": "Durability",
    "customer support": "Customer service","runtime": "Battery life","run time": "Battery life","battery": "Battery life",
    "suction power": "Suction","airflow": "Suction","air flow": "Suction","filter clogging": "Filter clog",
    "clogs": "Filter clog","clogged": "Filter clog","easy to clean": "Ease of cleaning","easy clean": "Ease of cleaning",
    "price": "Cost","expensive": "Cost","cheap": "Cost","instructions": "Manual","manual": "Manual",
    "gets warm": "Heat","burns": "Heat","burnt": "Heat","hot to touch": "Heat","not loud": "Quiet",
    "humming": "Noise","whistling": "Noise","rattling": "Noise","vibration": "Vibration","vibrates": "Vibration",
    "hair tangles": "Tangles","tangles": "Tangles","snags": "Tangles","snagging": "Tangles","loose": "Fit",
    "wobbly": "Fit","flimsy": "Durability","cheap plastic": "Durability","battery dies": "Battery life",
    "doesn't hold charge": "Battery life","holds charge": "Battery life","short charge": "Battery life",
    "charging slow": "Charge speed","charge slow": "Charge speed","instructions unclear": "Manual",
    "confusing manual": "Manual","hard to clean": "Ease of cleaning","easy clean up": "Ease of cleaning",
    "dust clogs": "Filter clog","blocked filter": "Filter clog","low suction": "Suction","weak suction": "Suction",
    "strong suction": "Suction","too expensive": "Cost","great value": "Cost","value": "Cost",
    "fast shipping": "Shipping","late delivery": "Shipping","late": "Shipping","arrived damaged": "Shipping damage",
}

SYN_SEEDS = {
    "Heat": ["hot","warm","warms up","overheat","burns","burnt","toasty","heat"],
    "Loud": ["loud","noisy","noise","loudness","too loud","very loud","blaring"],
    "Noise": ["noise","hum","whine","whistle","rattle","buzz","clatter"],
    "Vibration": ["vibrate","vibration","vibrates","shakes","rumbles"],
    "Smell": ["odor","odour","smell","stink","stinks","smelly","scent"],
    "Durability": ["breaks","broke","broken","flimsy","thin","cheap plastic","fragile","crack"],
    "Battery life": ["battery","charge lasts","holds charge","dies fast","short battery","runtime"],
    "Charge speed": ["charges slowly","slow charge","charging slow","takes long to charge"],
    "Suction": ["suction","airflow","air flow","pull","weak suction","strong suction","power"],
    "Filter clog": ["clog","clogged","block","blocked filter","dust clog","hair clog"],
    "Ease of cleaning": ["easy to clean","easy clean","hard to clean","cleanup","clean up"],
    "Cost": ["price","expensive","overpriced","cheap","value"],
    "Manual": ["instructions","manual","guide","how to","unclear manual"],
    "Learning curve": ["hard to learn","confusing","setup difficulty","learning curve"],
    "Customer service": ["customer support","service","support"],
    "Shipping": ["shipping","delivery","late","arrived late","fast shipping"],
    "Shipping damage": ["arrived damaged","damaged box","dented","scratched"]
}

# ----------------------------------------------------------------------
# 2. HELPERS
# ----------------------------------------------------------------------
def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

_DEF_PLURALS = [("y","ies"), ("","s")]

def _mk_variants(phrase: str) -> List[str]:
    phrase = (phrase or "").strip()
    out = {phrase}
    base = _normalize_name(phrase)
    out.add(base)
    out.add(base.replace("-"," "))
    out.add(base.replace(" ", ""))
    toks = base.split()
    if toks:
        last = toks[-1]
        for a, b in _DEF_PLURALS:
            if last.endswith(a):
                out.add(" ".join(toks[:-1] + [last[:len(last)-len(a)] + b]))
        stem = toks[-1]
        out.add(" ".join(toks[:-1] + [stem + "ing"]))
        out.add(" ".join(toks[:-1] + [stem + "ed"]))
    return [v for v in sorted({o.strip() for o in out if o.strip()})
            if len(v.split()) > 1 or len(v) >= 5]

@st.cache_resource(show_spinner=False)
def _build_label_variant_index(labels: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for L in labels:
        canon = canonicalize(L)
        seeds = [canon]
        for k, v in ALIAS_CANON.items():
            if v.lower() == canon.lower():
                seeds.append(k)
        for k, v in SYN_SEEDS.items():
            if k.lower() == canon.lower():
                seeds.extend(v)
        variants = list(itertools.chain.from_iterable(_mk_variants(s) for s in set(seeds)))
        variants = [v for v in dict.fromkeys(variants) if 2 <= len(v.split()) <= 6 or len(v) >= 4]
        out[canon] = variants[:40]
    return out

def canonicalize(name: str) -> str:
    base = _normalize_name(name or "")
    for k, v in ALIAS_CANON.items():
        if _normalize_name(k) == base:
            return v
    return name.strip()

def _tokenize_keep(words: str) -> List[str]:
    return [w for w in re.findall(r"[a-zA-Z0-9']+", (words or "").lower())
            if w not in STOP and len(w) >= 2]

def _evidence_score(label: str, text: str, label_variants: Dict[str, List[str]] | None = None) -> Tuple[int, List[str]]:
    if not label or not text:
        return 0, []
    canon = canonicalize(label)
    variants = (label_variants or {}).get(canon, []) or [canon]
    text_norm = _normalize_name(text)
    hits: List[str] = []
    for v in variants:
        v_norm = _normalize_name(v)
        if re.search(rf"\b{re.escape(v_norm)}\b", text_norm):
            hits.append(v); continue
        if 5 <= len(v_norm) <= 20:
            ratio = difflib.SequenceMatcher(None, v_norm, text_norm).find_longest_match(
                0, len(v_norm), 0, len(text_norm)).size / max(1, len(v_norm))
            if ratio >= 0.85:
                hits.append(v)
    return len(hits), hits[:3]

def _has_negation(span: str) -> bool:
    return any(t in NEGATORS for t in _tokenize_keep(span))

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    return float(np.dot(a, b) / denom)

# ----------------------------------------------------------------------
# 3. OPENAI & CACHING
# ----------------------------------------------------------------------
@st.cache_resource
def _get_openai_client_cached(_key: str):
    return OpenAI(api_key=_key)

def _get_store():
    if not hasattr(st.session_state, "_store"):
        st.session_state._store = {
            "lock": threading.Lock(),
            "pick_cache": {},
            "label_emb": {},
            "sent_emb_cache": {}
        }
    return st.session_state._store

def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s', text)
            if s.strip() and len(s.strip()) >= 12]

def _embed_with_retry(client, model: str, texts: List[str]):
    resp = client.embeddings.create(model=model, input=texts)
    return [np.array(e.embedding, dtype=np.float32) for e in resp.data]

def _chat_with_retry(client, req):
    return client.chat.completions.create(**req)

# ----------------------------------------------------------------------
# 4. SEMANTIC SHORT-LISTING
# ----------------------------------------------------------------------
def _shortlist_by_embeddings(review: str, labels: List[str], client, emb_model: str,
                             max_sentences: int, top_k: int,
                             label_variants: Dict[str, List[str]] | None) -> List[str]:
    store = _get_store()
    lock, label_emb, sent_cache = store["lock"], store.setdefault("label_emb", {}), store.setdefault("sent_emb_cache", {})
    lv = label_variants or _build_label_variant_index(labels)

    need = [v for L in labels for v in lv.get(canonicalize(L), [L]) if v not in label_emb]
    if need and client:
        embs = _embed_with_retry(client, emb_model, need)
        with lock:
            for v, e in zip(need, embs):
                label_emb[v] = e

    rev_hash = hashlib.sha256(review.encode()).hexdigest()
    with lock:
        cached = sent_cache.get(rev_hash)
    if cached is None and client:
        sents = _sentences(review)[:max_sentences]
        sent_embs = _embed_with_retry(client, emb_model, sents)
        cached = list(zip(sents, sent_embs))
        with lock:
            sent_cache[rev_hash] = cached
    elif not cached:
        cached = [(s, None) for s in _sentences(review)[:max_sentences]]

    scored: List[Tuple[str, float]] = []
    for L in labels:
        best = 0.0
        for v in lv.get(canonicalize(L), [L]):
            e_v = label_emb.get(v)
            if e_v is None: continue
            for _, e_s in cached:
                if e_s is None: continue
                best = max(best, _cosine(e_v, e_s))
        ev_hits, _ = _evidence_score(L, review, lv)
        scored.append((L, best + 0.05 * ev_hits))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [l for l, _ in scored[:top_k]]

def _prefilter_candidates(review: str, allowed: List[str], cap: int, use_embeddings: bool,
                          client, emb_model: str, max_sentences: int, variant_boost: bool,
                          label_variants: Dict[str, List[str]] | None) -> List[str]:
    lv = label_variants or (_build_label_variant_index(allowed) if variant_boost else None)
    if use_embeddings and client:
        try:
            return _shortlist_by_embeddings(review, allowed, client, emb_model,
                                            max_sentences, cap, lv)
        except Exception:
            pass
    text = " " + _normalize_name(review) + " "
    scored: List[Tuple[str, float]] = []
    for L in allowed:
        hits = 0; base_tok = 0
        for v in (lv or {}).get(canonicalize(L), [L]):
            toks = [t for t in _normalize_name(v).split() if len(t) > 2]
            base_tok += len(toks)
            if re.search(rf"\b{re.escape(_normalize_name(v))}\b", text):
                hits += len(toks)
        if base_tok:
            scored.append((L, hits / base_tok))
    if not scored:
        return allowed[:cap]
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:cap]]

# ----------------------------------------------------------------------
# 5. LLM PICKER
# ----------------------------------------------------------------------
def _llm_pick(review: str, stars, allowed_del: List[str], allowed_det: List[str],
              min_conf: float, evidence_hits_required: int, candidate_cap: int,
              max_output_tokens: int, use_embeddings: bool, emb_model: str,
              max_sentences: int, variant_boost: bool, auto_relax: bool,
              ensemble_check: bool):
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], [], {}

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    client = _get_openai_client_cached(api_key) if api_key else None

    lv_del = _build_label_variant_index(allowed_del) if variant_boost and allowed_del else {}
    lv_det = _build_label_variant_index(allowed_det) if variant_boost and allowed_det else {}

    allowed_del_f = _prefilter_candidates(review, allowed_del, candidate_cap,
                                          use_embeddings, client, emb_model,
                                          max_sentences, variant_boost, lv_del)
    allowed_det_f = _prefilter_candidates(review, allowed_det, candidate_cap,
                                          use_embeddings, client, emb_model,
                                          max_sentences, variant_boost, lv_det)

    cache_key = "|".join([
        model_choice, str(min_conf), str(evidence_hits_required), str(candidate_cap),
        str(use_embeddings), emb_model, str(max_sentences), str(variant_boost),
        hashlib.sha256("\x1f".join(sorted(allowed_del_f)).encode()).hexdigest(),
        hashlib.sha256("\x1f".join(sorted(allowed_det_f)).encode()).hexdigest(),
        hashlib.sha256(review.encode()).hexdigest(), str(stars)
    ])
    store = _get_store()
    with store["lock"]:
        if cache_key in store["pick_cache"]:
            return store["pick_cache"][cache_key]

    sys_prompt = """
You are labelling a single customer review. Choose ONLY from the provided lists.
Return compact JSON:
{
  "delighters":[{"name":"", "confidence":0.00, "quote":""}],
  "detractors":[{"name":"", "confidence":0.00, "quote":""}]
}
Rules:
- Only pick items that are clearly supported by the text.
- Include a **short verbatim quote** (5–18 words) that proves the item.
- Prefer precision over recall; avoid near-duplicates.
- If stars are 1–2 → prioritize detractors; 4–5 → prioritize delighters; 3 → neutral.
- Respect negation (e.g., “not loud” → do **not** pick “Loud”).
- At most 10 per group. Confidence ∈ [0,1].
- Synonyms/paraphrases are allowed **as long as the canonical label exists** in the list.
"""

    user_msg = {
        "review": review,
        "stars": float(stars) if stars is not None and not pd.isna(stars) else None,
        "allowed_delighters": allowed_del_f[:120],
        "allowed_detractors": allowed_det_f[:120]
    }

    def _post_process(items, allowed_set, text, lv):
        pairs: List[Tuple[str, float, str]] = []
        for d in items or []:
            name = canonicalize(d.get("name", ""))
            conf = float(d.get("confidence", 0))
            quote = (d.get("quote") or "").strip()
            if not name: continue
            ev_ok = _evidence_score(name, text, lv)[0] >= max(1, evidence_hits_required)
            if quote:
                if _normalize_name(quote) in _normalize_name(text):
                    ev_ok = True
                if _has_negation(quote):
                    conf *= 0.6
            if ev_ok and name in allowed_set:
                pairs.append((name, max(0.0, min(1.0, conf)), quote))
        best: Dict[str, Tuple[float, str]] = {}
        for n, c, q in pairs:
            if n not in best or c > best[n][0]:
                best[n] = (c, q)
        final = sorted(best.items(), key=lambda x: -x[1][0])[:10]
        evidence_map = {n: [q] for n, (_, q) in final if q}
        return [(n, c) for n, (c, _) in final], evidence_map

    def _dedupe_keep_top(pairs: List[Tuple[str, float]], top_n: int, min_c: float) -> List[str]:
        canon_pairs = [(canonicalize(n), c) for n, c in pairs if c >= min_c and n]
        kept: List[Tuple[str, float]] = []
        for n, c in sorted(canon_pairs, key=lambda x: -x[1]):
            n_norm = _normalize_name(n)
            if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(k)).ratio() > 0.88 for k, _ in kept):
                kept.append((n, c))
            if len(kept) >= top_n: break
        return [n for n, _ in kept]

    if client:
        try:
            req = {
                "model": model_choice,
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": json.dumps(user_msg)}],
                "response_format": {"type": "json_object"},
                "max_tokens": max_output_tokens,
                "temperature": 0.0
            }
            out = _chat_with_retry(client, req)
            data = json.loads(out.choices[0].message.content or "{}")

            dels_pairs, del_ev = _post_process(data.get("delighters", []), set(allowed_del_f), review, lv_del)
            dets_pairs, det_ev = _post_process(data.get("detractors", []), set(allowed_det_f), review, lv_det)

            dels = _dedupe_keep_top(dels_pairs, 10, min_conf)
            dets = _dedupe_keep_top(dets_pairs, 10, min_conf)

            if ensemble_check:
                def boost(lst, lv):
                    return [n for n, _ in sorted(
                        [(n, _evidence_score(n, review, lv)[0]) for n in lst],
                        key=lambda x: -x[1])][:10]
                dels = boost(dels, lv_del)
                dets = boost(dets, lv_det)

            if auto_relax and not (dels or dets):
                req2 = req.copy()
                req2["messages"][0]["content"] += "\nIf nothing is clearly present, pick plausible items ONLY with direct textual evidence."
                out2 = _chat_with_retry(client, req2)
                data2 = json.loads(out2.choices[0].message.content or "{}")
                dels2, del_ev2 = _post_process(data2.get("delighters", []), set(allowed_del_f), review, lv_del)
                dets2, det_ev2 = _post_process(data2.get("detractors", []), set(allowed_det_f), review, lv_det)
                dels = _dedupe_keep_top(dels2, 10, min_conf * 0.9)
                dets = _dedupe_keep_top(dets2, 10, min_conf * 0.9)

            result = (dels, dets, [], [], {**del_ev, **det_ev})
            with store["lock"]:
                store["pick_cache"][cache_key] = result
            return result
        except Exception:
            pass

    text_lc = review.lower()
    def lexical_pick(allowed, lv):
        scored = []
        for a in allowed:
            hits, _ = _evidence_score(canonicalize(a), text_lc, lv)
            if hits >= max(1, evidence_hits_required):
                scored.append((canonicalize(a), 0.65 + 0.1 * min(3, hits)))
        scored.sort(key=lambda x: -x[1])
        return [n for n, _ in scored[:10]]
    dels = lexical_pick(allowed_del_f, lv_del)
    dets = lexical_pick(allowed_det_f, lv_det)
    result = (dels, dets, [], [], {})
    with store["lock"]:
        store["pick_cache"][cache_key] = result
    return result

# ----------------------------------------------------------------------
# 6. UI & EXCEL HANDLING
# ----------------------------------------------------------------------
st.set_page_config(page_title="Star Walk – Symptomize v3", layout="wide")
st.title("Star Walk — Symptomize v3")

preset = st.sidebar.selectbox("Preset", ["Fast", "Accurate"])

with st.sidebar:
    if preset == "Fast":
        model_choice = "gpt-4o-mini"
        strictness = 0.75
        evidence_hits_required = 1
        candidate_cap = 60
        max_output_tokens = 380
        use_embeddings = True
        emb_model = "text-embedding-3-small"
        max_sentences = 18
    else:
        model_choice = "gpt-4o"
        strictness = 0.78
        evidence_hits_required = 1
        candidate_cap = 120
        max_output_tokens = 500
        use_embeddings = True
        emb_model = "text-embedding-3-large"
        max_sentences = 25

    variant_boost = st.checkbox("Use auto-generated synonyms/variants", value=True)
    auto_relax = st.checkbox("Auto-relax if nothing found", value=True)
    ensemble_check = st.checkbox("Ensemble rerank (lexical+embed+LLM)", value=True)

# ---------------- Excel Upload ----------------
uploaded = st.file_uploader("Upload Star Walk Workbook (.xlsx)", type=["xlsx"])
if uploaded:
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if "uploaded_bytes" not in st.session_state:
    st.info("Upload an `.xlsx` workbook to begin.")
    st.stop()

raw_bytes = st.session_state["uploaded_bytes"]

@st.cache_data(show_spinner=False)
def load_main_sheet(_bytes: bytes) -> pd.DataFrame:
    return pd.read_excel(_bytes, sheet_name="Star Walk scrubbed verbatims")

df = load_main_sheet(raw_bytes)
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21) if f"Symptom {i}" in df.columns]

# ---------------- Load Symptoms Sheet ----------------
def autodetect_symptom_sheet(xls) -> str | None:
    for name in xls.sheet_names:
        if "symptom" in name.lower():
            return name
    return xls.sheet_names[0] if xls.sheet_names else None

xls = pd.ExcelFile(io.BytesIO(raw_bytes))
symptom_sheet = autodetect_symptom_sheet(xls)

@st.cache_data
def load_symptoms(_bytes: bytes, sheet: str):
    df_s = pd.read_excel(_bytes, sheet_name=sheet)
    del_col = det_col = None
    for c in df_s.columns:
        if "delight" in str(c).lower(): del_col = c
        if "detract" in str(c).lower(): det_col = c
    dels = [str(x).strip() for x in df_s.get(del_col, []).dropna() if str(x).strip()]
    dets = [str(x).strip() for x in df_s.get(det_col, []).dropna() if str(x).strip()]
    return dels, dets

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS = load_symptoms(raw_bytes, symptom_sheet)

# ---------------- Run ----------------
missing_idx = df[df[SYMPTOM_COLS].isna().all(axis=1)].index.tolist()
batch = st.slider("Batch size", 1, 50, min(20, len(missing_idx)))

if st.button("Run Symptomize") and missing_idx:
    todo = missing_idx[:batch]
    progress = st.progress(0)
    results = []

    for i, idx in enumerate(todo):
        row = df.loc[idx]
        res = _llm_pick(
            str(row.get("Verbatim", "")),
            row.get("Star Rating"),
            ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS,
            strictness, evidence_hits_required, candidate_cap,
            max_output_tokens, use_embeddings, emb_model, max_sentences,
            variant_boost, auto_relax, ensemble_check
        )
        results.append({"row": idx, "delighters": res[0], "detractors": res[1]})
        progress.progress((i + 1) / len(todo))

    st.success("Done!")
    st.dataframe(pd.DataFrame(results))

# ---------------- Download Updated Workbook ----------------
def save_updated_workbook():
    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    ws = wb["Star Walk scrubbed verbatims"]
    headers = {cell.value: cell.column for cell in ws[1]}
    for res in results:
        row_idx = res["row"] + 2
        for i, name in enumerate(res["detractors"][:10], 1):
            col = headers.get(f"Symptom {i}")
            if col: ws.cell(row_idx, col).value = name
        for i, name in enumerate(res["delighters"][:10], 11):
            col = headers.get(f"Symptom {i}")
            if col: ws.cell(row_idx, col).value = name
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

if st.button("Download Updated Workbook"):
    data = save_updated_workbook()
    st.download_button("Download StarWalk_updated.xlsx", data, "StarWalk_updated.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
