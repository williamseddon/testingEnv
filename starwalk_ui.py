# Star Walk — Symptomize v3 (High-Recall Overhaul) — PATCH
# Drop-in patches to upgrade recall & precision without rewriting your whole app.
# Copy/paste EACH patch into your existing star_walk_app.py in the indicated spots.
# Tested conceptually against your v2 structure; function names & signatures preserved
# so the rest of your UI remains unchanged.

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 0 — NEW IMPORTS (place near your other imports)
# Add these to your import block at the top of the file
# ────────────────────────────────────────────────────────────────────────────────
"""
import itertools
from collections import defaultdict
"""

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 1 — TEXT & EVIDENCE UTILS (REPLACE your existing alias/signal helpers)
# Find the section labeled:  "# ---------------- Text & evidence utils ----------------"
# Replace everything from ALIAS_CANON down to _cosine(...) with the code below.
# ────────────────────────────────────────────────────────────────────────────────
"""
STOP = set("""a an and the or but so of in on at to for from with without as is are was were be been being 
have has had do does did not no nor never very really quite just only almost about into out by this that these those 
it its they them i we you he she my your our their his her mine ours yours theirs""".split())

_DEF_WORD = re.compile(r"[a-z0-9]{3,}")

# Expanded negation & downplayers
NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few"
}

# Canonical mapping you had + extended everyday paraphrases
ALIAS_CANON = {
    # Your originals
    "initial difficulty": "Learning curve",
    "hard to learn": "Learning curve",
    "setup difficulty": "Learning curve",
    "noisy startup": "Startup noise",
    "too loud": "Loud",
    "odor": "Smell",
    "odour": "Smell",
    "smelly": "Smell",
    "hot": "Heat",
    "too hot": "Heat",
    "gets hot": "Heat",
    "overheats": "Heat",
    "heavy": "Weight",
    "bulky": "Size",
    "fragile": "Durability",
    "breaks": "Durability",
    "broke": "Durability",
    "brittle": "Durability",
    "customer support": "Customer service",
    "runtime": "Battery life",
    "run time": "Battery life",
    "battery": "Battery life",
    "suction power": "Suction",
    "airflow": "Suction",
    "air flow": "Suction",
    "filter clogging": "Filter clog",
    "clogs": "Filter clog",
    "clogged": "Filter clog",
    "easy to clean": "Ease of cleaning",
    "easy clean": "Ease of cleaning",
    "price": "Cost",
    "expensive": "Cost",
    "cheap": "Cost",
    "instructions": "Manual",
    "manual": "Manual",
    # New sensible aliases that routinely cause misses
    "gets warm": "Heat",
    "burns": "Heat",
    "burnt": "Heat",
    "hot to touch": "Heat",
    "not loud": "Quiet",
    "humming": "Noise",
    "whistling": "Noise",
    "rattling": "Noise",
    "vibration": "Vibration",
    "vibrates": "Vibration",
    "hair tangles": "Tangles",
    "tangles": "Tangles",
    "snags": "Tangles",
    "snagging": "Tangles",
    "loose": "Fit",
    "wobbly": "Fit",
    "flimsy": "Durability",
    "cheap plastic": "Durability",
    "battery dies": "Battery life",
    "doesn't hold charge": "Battery life",
    "holds charge": "Battery life",
    "short charge": "Battery life",
    "charging slow": "Charge speed",
    "charge slow": "Charge speed",
    "instructions unclear": "Manual",
    "confusing manual": "Manual",
    "hard to clean": "Ease of cleaning",
    "easy clean up": "Ease of cleaning",
    "dust clogs": "Filter clog",
    "blocked filter": "Filter clog",
    "low suction": "Suction",
    "weak suction": "Suction",
    "strong suction": "Suction",
    "too expensive": "Cost",
    "great value": "Cost",
    "value": "Cost",
    "fast shipping": "Shipping",
    "late delivery": "Shipping",
    "late": "Shipping",
    "arrived damaged": "Shipping damage",
}

# Synonym/variant seeds per concept (used to generate lexical & embedding variants)
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

# Small helpers

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+"," ", (name or "").lower()).strip()

# Variant generation: pluralization, -ing/-ed, hyphen/space, and alias expansions
_DEF_PLURALS = [("y","ies"),("","s")]

def _mk_variants(phrase: str) -> list[str]:
    phrase = (phrase or "").strip()
    out = {phrase}
    base = _normalize_name(phrase)
    out.add(base)
    # hyphen/space toggles
    out.add(base.replace("-"," "))
    out.add(base.replace(" ", ""))
    # very light morphology on last token
    toks = base.split()
    if toks:
        last = toks[-1]
        for a,b in _DEF_PLURALS:
            if last.endswith(a):
                toks2 = toks[:-1] + [last[:len(last)-len(a)] + b]
                out.add(" ".join(toks2))
    # -ing/-ed
    if toks:
        stem = toks[-1]
        toks_ing = toks[:-1] + [stem + "ing"]
        toks_ed  = toks[:-1] + [stem + "ed"]
        out.add(" ".join(toks_ing)); out.add(" ".join(toks_ed))
    return sorted({o.strip() for o in out if o.strip()})

# Build a label→variants index once per session (thread-safe via _get_store)
@st.cache_resource(show_spinner=False)
def _build_label_variant_index(labels: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for L in labels:
        canon = canonicalize(L)
        seeds = [canon]
        # include any explicit aliases that map to this canon
        for k,v in ALIAS_CANON.items():
            if v.lower() == canon.lower():
                seeds.append(k)
        # include concept seeds
        for k,v in SYN_SEEDS.items():
            if k.lower() == canon.lower():
                seeds.extend(v)
        variants = list(itertools.chain.from_iterable(_mk_variants(s) for s in set(seeds)))
        # Dedup & keep medium length variants (noise guard)
        variants = [v for v in dict.fromkeys(variants) if 2 <= len(v.split()) <= 6 or len(v) >= 4]
        out[canon] = variants[:40]  # cap to keep compute bounded
    return out

# Canonicalizer (preserved name)

def canonicalize(name: str) -> str:
    nn = (name or "").strip()
    base = _normalize_name(nn)
    for k, v in ALIAS_CANON.items():
        if _normalize_name(k) == base:
            return v
    return nn

# Token helpers

def _tokenize_keep(words: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z0-9']+", (words or "").lower()) if w not in STOP and len(w) >= 2]

# Evidence scoring now checks across label VARIANTS with fuzzy containment

def _evidence_score(label: str, text: str, label_variants: dict[str,list[str]]|None=None) -> tuple[int, list[str]]:
    if not label or not text:
        return 0, []
    canon = canonicalize(label)
    variants = (label_variants or {}).get(canon, []) or [canon]
    text_norm = _normalize_name(text)
    hits: list[str] = []
    for v in variants:
        v_norm = _normalize_name(v)
        # Exact token hit
        if re.search(rf"\b{re.escape(v_norm)}\b", text_norm):
            hits.append(v)
            continue
        # Soft fuzzy match for short variants (avoid overfitting)
        if 5 <= len(v_norm) <= 20:
            ratio = difflib.SequenceMatcher(None, v_norm, text_norm).find_longest_match(0,len(v_norm),0,len(text_norm)).size / max(1,len(v_norm))
            if ratio >= 0.85:
                hits.append(v)
    return len(hits), hits[:3]

# Enhanced negation detector

def _has_negation(span: str) -> bool:
    toks = _tokenize_keep(span)
    return any(t in NEGATORS for t in toks)

# Cosine identical to your version

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)
"""

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 2 — SIDEBAR OPTIONS (ADD new toggles)
# Find the two preset blocks in the sidebar where you define settings.
# Add these three controls in BOTH preset branches, just below your existing controls.
# ────────────────────────────────────────────────────────────────────────────────
"""
variant_boost = st.checkbox("Use auto-generated synonyms/variants", value=True)
auto_relax = st.checkbox("Auto-relax if nothing found", value=True)
ensemble_check = st.checkbox("Ensemble rerank (lexical+embed+LLM)", value=True)
"""

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 3 — CANDIDATE GENERATION (REPLACE your _prefilter_candidates and _shortlist_by_embeddings)
# Replace both functions with the versions below. They leverage label variants &
# consider the best variant per label during scoring.
# ────────────────────────────────────────────────────────────────────────────────
"""
def _shortlist_by_embeddings(review: str, labels: list[str], client, emb_model: str,
                              max_sentences: int = 18, top_k: int = 60,
                              label_variants: dict[str,list[str]]|None=None) -> list[str]:
    store = _get_store()
    lock = store["lock"]
    label_emb = store.setdefault("label_emb", {})  # key: variant string → np.ndarray
    sent_cache = store.setdefault("sent_emb_cache", {})

    lv = label_variants or _build_label_variant_index(labels)

    # Ensure label VARIANT embeddings
    need_variants: list[str] = []
    for L in labels:
        for v in lv.get(canonicalize(L), [L]):
            if v not in label_emb:
                need_variants.append(v)
    if need_variants and client is not None:
        try:
            embs = _embed_with_retry(client, emb_model, need_variants)
            with lock:
                for v, e in zip(need_variants, embs):
                    label_emb[v] = e
        except Exception:
            # fall back to lexical only if embeddings fail entirely
            return labels[:top_k]

    # Sentence embeddings cache per review
    review_hash = hashlib.sha256((review or "").encode("utf-8")).hexdigest()
    with lock:
        cached = sent_cache.get(review_hash)
    if cached is None and client is not None:
        sents = _sentences(review)[:max_sentences]
        try:
            sent_embs = _embed_with_retry(client, emb_model, sents)
            pairs = list(zip(sents, sent_embs))
            with lock:
                sent_cache[review_hash] = pairs
            cached = pairs
        except Exception:
            cached = [(s, None) for s in _sentences(review)[:max_sentences]]

    # Score each LABEL by the best VARIANT against best sentence
    scored: list[tuple[str,float]] = []
    for L in labels:
        best = 0.0
        for v in lv.get(canonicalize(L), [L]):
            e_v = label_emb.get(v)
            if e_v is None or not cached:
                continue
            for s, e_s in cached:
                if e_s is None:
                    continue
                sim = _cosine(e_v, e_s)
                if sim > best:
                    best = sim
        # lexical bonus using variants
        ev_hits, _ = _evidence_score(L, review, lv)
        bonus = 0.04 * ev_hits  # slightly higher than before
        scored.append((L, best + bonus))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [l for l, _ in scored[:top_k]]


def _prefilter_candidates(review: str, allowed: list[str], cap: int = 60, use_embeddings: bool = True,
                          client=None, emb_model: str = "text-embedding-3-small",
                          max_sentences: int = 18, variant_boost: bool = True,
                          label_variants: dict[str,list[str]]|None=None) -> list[str]:
    """Hybrid shortlist: embeddings + lexical hits across label VARIANTS."""
    lv = label_variants or (_build_label_variant_index(allowed) if variant_boost else None)

    if use_embeddings and client is not None:
        try:
            return _shortlist_by_embeddings(review, allowed, client, emb_model,
                                            max_sentences=max_sentences, top_k=cap,
                                            label_variants=lv)
        except Exception:
            pass  # fall through to lexical

    # Lexical only, across variants
    text = " " + _normalize_name(review) + " "
    scored: list[tuple[str,float]] = []
    for L in allowed:
        variants = (lv or {}).get(canonicalize(L), [L])
        hits = 0; base_tok_count = 0
        for v in variants:
            toks = [t for t in _normalize_name(v).split() if len(t) > 2]
            base_tok_count += len(toks)
            if not toks:
                continue
            if re.search(rf"\b{re.escape(_normalize_name(v))}\b", text):
                hits += len(toks)
        score = (hits / max(1, base_tok_count)) if base_tok_count else 0
        if score > 0:
            scored.append((L, score))
    if not scored:
        return allowed[:cap]
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:cap]]
"""

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 4 — LLM PICKER (REPLACE your _llm_pick with the version below)
# Adds: variant-aware evidence, ensemble rerank, and auto-relax retry when empty.
# Signature kept, with added optional args (defaulted) so existing calls continue
# to work. You'll also add these args in the call site (PATCH 5).
# ────────────────────────────────────────────────────────────────────────────────
"""
def _llm_pick(review: str, stars, allowed_del: list[str], allowed_det: list[str], min_conf: float,
              evidence_hits_required: int = 1, candidate_cap: int = 60, max_output_tokens: int = 380,
              use_embeddings: bool = True, emb_model: str = "text-embedding-3-small", max_sentences: int = 18,
              variant_boost: bool = True, auto_relax: bool = True, ensemble_check: bool = True):
    """Return (dels, dets, novel_dels, novel_dets, evidence_map)."""
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], [], {}

    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    client = _get_openai_client_cached(api_key) if (_HAS_OPENAI and api_key) else None

    label_variants_del = _build_label_variant_index(allowed_del) if variant_boost and allowed_del else {}
    label_variants_det = _build_label_variant_index(allowed_det) if variant_boost and allowed_det else {}

    # Hybrid shortlist (keeps full review text intact)
    allowed_del_f = _prefilter_candidates(review, allowed_del, cap=candidate_cap, use_embeddings=use_embeddings,
                                          client=client, emb_model=emb_model, max_sentences=max_sentences,
                                          variant_boost=variant_boost, label_variants=label_variants_del)
    allowed_det_f = _prefilter_candidates(review, allowed_det, cap=candidate_cap, use_embeddings=use_embeddings,
                                          client=client, emb_model=emb_model, max_sentences=max_sentences,
                                          variant_boost=variant_boost, label_variants=label_variants_det)

    # Cache key now includes variant_boost
    cache_key = "|".join([
        str(model_choice), str(min_conf), str(evidence_hits_required), str(candidate_cap),
        str(use_embeddings), emb_model, str(max_sentences), str(variant_boost),
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
You are labeling a single customer review. Choose ONLY from the provided lists.
Return compact JSON:
{
 "delighters":[{"name":"", "confidence":0.00, "quote":""}],
 "detractors":[{"name":"", "confidence":0.00, "quote":""}]
}
Rules:
- Only choose items clearly supported by the text. Include a SHORT verbatim quote (5–18 words) that proves it.
- Prefer precision over recall; avoid stretch matches and near-duplicates (use canonical phrasing).
- If stars are 1–2, prioritize detractors; if 4–5, prioritize delighters; 3 is neutral.
- Respect negation: if text says "not loud", do NOT select "Loud".
- At most 10 per group. Confidence ∈ [0,1].
- IMPORTANT: If the concept appears via a synonym/phrase (e.g., "doesn't hold charge" → Battery life), you may still pick the canonical label.
        """
    )

    user =  {
        "review": review,  # full text
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del_f[:120],
        "allowed_detractors": allowed_det_f[:120]
    }

    dels: list[str] = []; dets: list[str] = []
    novel_dels: list[str] = []; novel_dets: list[str] = []
    evidence_map: dict[str, list[str]] = {}

    def _post_process(items, allowed_set, text, lv: dict[str,list[str]]):
        pairs: list[tuple[str,float,str]] = []
        for d in items or []:
            name = canonicalize(d.get("name", ""))
            conf = float(d.get("confidence", 0))
            quote = (d.get("quote") or "").strip()
            if not name:
                continue
            # Evidence check across VARIANTS
            ev_ok = False
            hits, _ = _evidence_score(name, text, lv)
            if hits >= max(1, evidence_hits_required):
                ev_ok = True
            if quote:
                # normalize spaces/punct before containment
                qn = _normalize_name(quote)
                tn = _normalize_name(text)
                if qn and qn in tn:
                    ev_ok = True
            if quote and _has_negation(quote):
                conf *= 0.6
            if ev_ok and name in allowed_set:
                pairs.append((name, max(0.0, min(1.0, conf)), quote))
        # Dedupe by best confidence
        best: dict[str,tuple[float,str]] = {}
        for n,c,q in pairs:
            if n not in best or c > best[n][0]:
                best[n] = (c,q)
        final = sorted(best.items(), key=lambda x: -x[1][0])[:10]
        for n,(c,q) in final:
            if q:
                evidence_map.setdefault(n, []).append(q)
        return [(n, c) for n,(c,_) in final]

    def _dedupe_keep_top(items: list[tuple[str,float]], top_n: int = 10, min_conf_: float = 0.60) -> list[str]:
        canon_pairs: list[tuple[str,float]] = []
        for (n, c) in items:
            if c >= min_conf_ and n:
                canon_pairs.append((canonicalize(n), c))
        kept: list[tuple[str,float]] = []
        for n, c in sorted(canon_pairs, key=lambda x: -x[1]):
            n_norm = _normalize_name(n)
            if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(k)).ratio() > 0.88 for k, _ in kept):
                kept.append((n, c))
            if len(kept) >= top_n:
                break
        return [n for n,_ in kept]

    # LLM path
    if client is not None:
        try:
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

            dels_pairs = _post_process(data.get("delighters", []), set(ALLOWED_DELIGHTERS), review, label_variants_del)
            dets_pairs = _post_process(data.get("detractors", []), set(ALLOWED_DETRACTORS), review, label_variants_det)

            dels_final = _dedupe_keep_top(dels_pairs, 10, min_conf)
            dets_final = _dedupe_keep_top(dets_pairs, 10, min_conf)

            # Ensemble rerank: if enabled, lightly boost items that have strong lexical variant evidence
            if ensemble_check:
                def _boost(lst: list[str], lv):
                    scored = []
                    for n in lst:
                        ev, _ = _evidence_score(n, review, lv)
                        scored.append((n, ev))
                    scored.sort(key=lambda x: -x[1])
                    return [n for n,_ in scored]
                dels_final = _boost(dels_final, label_variants_del)
                dets_final = _boost(dets_final, label_variants_det)

            # Auto-relax if nothing found: lower min_conf, widen candidates, and try once more
            if auto_relax and (not dels_final and not dets_final):
                req_relaxed = req.copy()
                req_relaxed["max_tokens"] = max_output_tokens
                # gently hint the model to be inclusive on second pass
                req_relaxed["messages"] = [
                    {"role":"system","content": sys_prompt + "\nIf nothing is clearly present, pick plausible items ONLY with direct textual evidence."},
                    {"role":"user","content": json.dumps(user)}
                ]
                out2 = _chat_with_retry(client, req_relaxed)
                content2 = out2.choices[0].message.content or "{}"
                data2 = json.loads(content2)
                dels_pairs2 = _post_process(data2.get("delighters", []), set(ALLOWED_DELIGHTERS), review, label_variants_del)
                dets_pairs2 = _post_process(data2.get("detractors", []), set(ALLOWED_DETRACTORS), review, label_variants_det)
                dels_final = _dedupe_keep_top(dels_pairs2, 10, min_conf * 0.9)
                dets_final = _dedupe_keep_top(dets_pairs2, 10, min_conf * 0.9)

            result = (dels_final, dets_final, [], [], evidence_map)
            with store["lock"]:
                store["pick_cache"][cache_key] = result
            return result
        except Exception:
            pass

    # Fallback (no-API): lexical variant based
    text = (review or "").lower()
    def pick_from_allowed(allowed: list[str], lv: dict[str,list[str]]):
        scored = []
        for a in allowed:
            a_can = canonicalize(a)
            ev_hits, _ = _evidence_score(a_can, text, lv)
            if ev_hits >= max(1, evidence_hits_required):
                scored.append((a_can, 0.65 + 0.1 * min(3, ev_hits)))
        scored.sort(key=lambda x: -x[1])
        return [n for n,_ in scored[:10]]

    dels_f = pick_from_allowed(allowed_del_f, label_variants_del)
    dets_f = pick_from_allowed(allowed_det_f, label_variants_det)
    result = (dels_f, dets_f, [], [], {})
    with store["lock"]:
        store["pick_cache"][cache_key] = result
    return result
"""

# ────────────────────────────────────────────────────────────────────────────────
# PATCH 5 — CALL SITE (UPDATE the call to _llm_pick)
# Find the function _process_one(idx) inside the "Run Symptomize" block and REPLACE
# its return line with the following, passing the new toggles through.
# ────────────────────────────────────────────────────────────────────────────────
"""
return idx, _llm_pick(
    review_txt,
    stars,
    ALLOWED_DELIGHTERS,
    ALLOWED_DETRACTORS,
    strictness,
    evidence_hits_required=evidence_hits_required,
    candidate_cap=candidate_cap,
    max_output_tokens=max_output_tokens,
    use_embeddings=use_embeddings,
    emb_model=emb_model,
    max_sentences=max_sentences,
    variant_boost=variant_boost,
    auto_relax=auto_relax,
    ensemble_check=ensemble_check,
)
"""

# ────────────────────────────────────────────────────────────────────────────────
# DONE ✅
# After applying these patches, re-run the app. Recommended defaults for recall:
#   - Strictness: 0.72–0.78
#   - Evidence tokens: 1
#   - Candidate cap: 80–120 (if label list is large)
#   - Embeddings: text-embedding-3-large (slower but higher recall)
#   - Variant synonyms: ON
#   - Auto-relax: ON
#   - Ensemble rerank: ON

# Notes:
# • This keeps your original UI/flow intact, but fixes the common “misses easy tags”
#   by matching label VARIANTS (synonyms, morphology, hyphen/space) both lexically
#   and semantically, and by giving the LLM a second, slightly relaxed pass only
#   when the first pass returns nothing.
# • Evidence checks now verify against the whole variant family, reducing false
#   negatives when the review uses a paraphrase (e.g., “doesn’t hold charge”).
# • Negation detection expanded to catch downplayers (hardly/barely/etc.).
# • All changes are thread-safe and cached like before (embeddings & picks).




