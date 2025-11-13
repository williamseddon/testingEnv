# Extend the Streamlit app with an "ASOS Review Texts" tab that fetches paginated review text
# via Axesso's /aso/lookup-product-reviews endpoint, with API-call estimates and CSV/JSON export.

import os, re, json, zipfile

project_dir = "/mnt/data/streamlit_axesso_seller_products"
app_path = os.path.join(project_dir, "streamlit_app.py")

with open(app_path, "r", encoding="utf-8") as f:
    src = f.read()

# 1) Update tabs to add new "ASOS Review Texts" tab.
src = src.replace(
    'tab_amz, tab_asos, tab_docs, tab_snippets = st.tabs(["Search & Results (Amazon)", "ASOS Reviews", "API Docs", "Code Snippets"])',
    'tab_amz, tab_asos, tab_asos_reviews, tab_docs, tab_snippets = st.tabs(["Search & Results (Amazon)", "ASOS Reviews", "ASOS Review Texts", "API Docs", "Code Snippets"])'
)

# 2) In the existing ASOS tab, save discovered productIds to session_state for reuse by the new reviews tab.
save_ids_snippet = r'''
                # Store productIds for the Reviews tab
                try:
                    st.session_state["asos_last_productIds"] = [x for x in df_asos["productId"].astype(str).tolist() if x and x != "nan"]
                except Exception:
                    pass
'''
if save_ids_snippet not in src:
    src = src.replace('                st.subheader("ASOS Results — Table")', save_ids_snippet + '\n                st.subheader("ASOS Results — Table")')

# 3) Insert new "ASOS Review Texts" tab implementation before Docs tab.
if "# ASOS Review Texts Tab" not in src:
    insert_point = "\nwith tab_docs:"
    reviews_tab_block = r'''
# -----------------------------
# ASOS Review Texts Tab
# -----------------------------
with tab_asos_reviews:
    st.subheader("ASOS Review Texts (by productId)")
    st.caption("Fetch paginated review text for ASOS products via Axesso’s `/aso/lookup-product-reviews`.")

    # Domain selection (ASOS domains are typically region codes like 'us', 'gb', etc.)
    asos_domains = ["us","gb","de","fr","it","es","nl","se","pl","au"]
    rcol1, rcol2 = st.columns(2)
    with rcol1:
        asos_domain = st.selectbox("ASOS domainCode", options=asos_domains, index=1, key="asos_reviews_domain")
    with rcol2:
        include_variants = st.checkbox("Also include productIds from ASOS Products tab (if available)", value=True)

    st.markdown("#### Product IDs")
    default_ids = ""
    if include_variants and st.session_state.get("asos_last_productIds"):
        default_ids = "\n".join(st.session_state["asos_last_productIds"][:50])  # guard against huge lists
    product_ids_input = st.text_area("Enter one productId per line", value=default_ids, height=120)

    st.markdown("#### Fetch Options")
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    with r2c1:
        fetch_all_pages = st.checkbox("Fetch ALL pages", value=True, help="Loop pages for each productId until last page (or cap).")
    with r2c2:
        start_page_r = st.number_input("Start page", value=1, min_value=1, step=1)
    with r2c3:
        max_pages_cap_r = st.number_input("Max pages (cap)", value=50, min_value=1, step=1)
    with r2c4:
        delay_r = st.number_input("Delay between requests (sec)", value=0.5, min_value=0.0, step=0.1)

    # API call estimate (minimal)
    pid_list = [p.strip() for p in (product_ids_input or "").splitlines() if p.strip()]
    # Use per-product cached lastPage if available
    lastpage_cache = st.session_state.get("asos_reviews_lastpage_cache", {})  # key: f"{productId}:{domain}" -> int
    def estimate_calls_for_pid(pid):
        key = f"{pid}:{asos_domain}"
        if fetch_all_pages:
            if isinstance(lastpage_cache.get(key), int):
                lp = lastpage_cache[key]
                planned = max(0, min(int(max_pages_cap_r), int(lp) - int(start_page_r) + 1))
            else:
                planned = int(max_pages_cap_r)
            return planned
        return 1  # single page
    total_est_calls = sum(estimate_calls_for_pid(pid) for pid in pid_list)
    st.caption(f"**API call estimate** — Fetch Reviews: **{total_est_calls}** (across {len(pid_list)} productId{'s' if len(pid_list)!=1 else ''})")

    # Fetch button
    go_reviews = st.button("📥 Fetch ASOS Reviews", use_container_width=True)

    def parse_reviews_from_payload(payload: dict):
        # Try a few common key shapes
        candidates = [
            "reviews", "reviewList", "productReviews", "reviewsData", "data", "items"
        ]
        for k in candidates:
            v = payload.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # Sometimes nested
        for k in candidates:
            v = payload.get(k)
            if isinstance(v, dict):
                for kk in candidates:
                    vv = v.get(kk)
                    if isinstance(vv, list) and vv and isinstance(vv[0], dict):
                        return vv
        return []

    def normalize_review(pid: str, domain: str, meta: dict, item: dict):
        # Try to normalize common fields
        def g(*keys, default=""):
            for k in keys:
                if k in item and item[k] is not None:
                    return item[k]
                if k in meta and meta[k] is not None:
                    return meta[k]
            return default
        return {
            "productId": pid,
            "domainCode": domain,
            "title": g("title","reviewTitle"),
            "text": g("text","content","reviewText","body"),
            "rating": g("rating","stars","starRating","ratingValue"),
            "author": g("author","reviewer","user","reviewerName","nickname"),
            "date": g("date","reviewDate","submissionTime","createdAt"),
            "helpful": g("helpful","helpfulCount","helpfulVotes","votes"),
            "url": meta.get("url",""),
            "productTitle": meta.get("productTitle",""),
            "manufacturer": meta.get("manufacturer",""),
        }

    def do_request_reviews(pid: str, page: int):
        endpoint = "http://api.axesso.de/aso/lookup-product-reviews"
        q = {"productId": pid, "domainCode": asos_domain, "page": page}
        r = requests.get(endpoint, params=q, headers=parsed_headers, timeout=20)
        return r

    reviews_rows = []
    reviews_meta = []
    fetch_errs = []

    if go_reviews:
        if not pid_list:
            st.error("Please provide at least one productId.")
        else:
            progress = st.progress(0)
            total_calls = max(1, total_est_calls)
            call_idx = 0

            for pid in pid_list:
                last_page_seen = None
                # Determine plan per product
                pages_to_try = [int(start_page_r)]
                if fetch_all_pages:
                    # optimistic plan using cache or cap; we'll break early if lastPage is reached
                    if isinstance(lastpage_cache.get(f"{pid}:{asos_domain}"), int):
                        lp = lastpage_cache[f"{pid}:{asos_domain}"]
                        end_p = min(int(max_pages_cap_r), int(lp))
                    else:
                        end_p = int(start_page_r) + int(max_pages_cap_r) - 1
                    pages_to_try = list(range(int(start_page_r), int(end_p) + 1))

                for page in pages_to_try:
                    try:
                        r = do_request_reviews(pid, page)
                        call_idx += 1
                        progress.progress(min(1.0, call_idx / float(total_calls)))
                        if r.status_code != 200:
                            fetch_errs.append((pid, page, f"HTTP {r.status_code}: {r.text[:200]}"))
                            break
                        data = r.json()
                        # Try to detect lastPage/currentPage if available
                        current_page = data.get("currentPage") or page
                        last_page = data.get("lastPage")
                        if isinstance(last_page, int):
                            st.session_state.setdefault("asos_reviews_lastpage_cache", {})[f"{pid}:{asos_domain}"] = last_page
                            last_page_seen = last_page

                        # Collect meta (one per page in case fields differ)
                        meta_fields = ["productTitle","manufacturer","url","countReview","productRating"]
                        meta = {k: data.get(k) for k in meta_fields}
                        meta["productId"] = pid
                        meta["domainCode"] = asos_domain
                        meta["page"] = current_page
                        reviews_meta.append(meta)

                        # Extract review items
                        items = parse_reviews_from_payload(data)
                        for it in items:
                            reviews_rows.append(normalize_review(pid, asos_domain, meta, it))

                        # stop if at last page
                        if isinstance(last_page, int) and int(current_page) >= int(last_page):
                            break

                        # Optional early stop if empty list and no lastPage
                        if not items and not isinstance(last_page, int):
                            break

                        # delay if configured
                        if delay_r and delay_r > 0:
                            import time as _t
                            _t.sleep(float(delay_r))
                    except Exception as e:
                        fetch_errs.append((pid, page, str(e)))
                        break

            # Render results
            import pandas as _pd
            if reviews_rows:
                df_rev = _pd.DataFrame(reviews_rows)
                st.success(f"Collected {len(df_rev)} reviews across {len(pid_list)} productId(s).")
                st.dataframe(df_rev, use_container_width=True)
                c1, c2 = st.columns(2)
                with c1:
                    st.download_button("⬇️ Download Reviews (JSON)", data=json.dumps(reviews_rows, indent=2), file_name="asos_reviews.json", mime="application/json", use_container_width=True)
                with c2:
                    st.download_button("⬇️ Download Reviews (CSV)", data=df_rev.to_csv(index=False), file_name="asos_reviews.csv", mime="text/csv", use_container_width=True)

            if reviews_meta:
                df_meta = _pd.DataFrame(reviews_meta)
                with st.expander("Show meta (per page)", expanded=False):
                    st.dataframe(df_meta, use_container_width=True)

            if fetch_errs:
                with st.expander("Show errors"):
                    for pid, page, err in fetch_errs:
                        st.write(f"- productId={pid} page={page} — {err}")
'''
    src = src.replace(insert_point, reviews_tab_block + insert_point)

# Save the modified app
with open(app_path, "w", encoding="utf-8") as f:
    f.write(src)

# Repack ZIP
zip_path = "/mnt/data/streamlit_axesso_seller_products.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(project_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, project_dir)
            z.write(full, arcname=os.path.join("streamlit_axesso_seller_products", rel))

(app_path, zip_path)

