import json
import shlex
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Axion Ray Event Exporter", page_icon="📥", layout="wide")

DEFAULT_OFFSET_PARAM = "offset"
DEFAULT_LIMIT_PARAM = "limit"
DEFAULT_PAGE_SIZE = 1000
DEFAULT_ROWS_PATH = "events"
DEFAULT_TOTAL_PATH = "count"

EXCLUDED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "origin",
    "referer",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "priority",
    "traceparent",
    "tracestate",
    "x-datadog-origin",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-trace-id",
}

SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key"}

POSSIBLE_LIST_KEYS = ["events", "items", "results", "data", "rows", "records", "entries"]
POSSIBLE_TOTAL_KEYS = ["count", "total", "totalCount", "total_count", "numFound"]


def parse_fetch_snippet(fetch_text: str):
    text = fetch_text.strip()

    prefix_double = 'fetch("'
    prefix_single = "fetch('"

    if text.startswith(prefix_double):
        quote = '"'
        start = len(prefix_double)
    elif text.startswith(prefix_single):
        quote = "'"
        start = len(prefix_single)
    else:
        raise ValueError("Expected text starting with fetch(\"...\") or fetch('...').")

    end_url = text.find(f"{quote},", start)
    if end_url == -1:
        raise ValueError("Could not parse fetch URL.")

    url = text[start:end_url]

    obj_start = text.find("{", end_url)
    obj_end = text.rfind("}")
    if obj_start == -1 or obj_end == -1:
        raise ValueError("Could not parse fetch options object.")

    obj_text = text[obj_start:obj_end + 1]

    try:
        opts = json.loads(obj_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse fetch options JSON: {e}") from e

    return {
        "method": opts.get("method", "GET").upper(),
        "url": url,
        "headers": opts.get("headers", {}),
        "body": opts.get("body"),
    }


def parse_curl(curl_text: str):
    parts = shlex.split(curl_text)
    method = "GET"
    url = None
    headers = {}
    body = None

    i = 0
    while i < len(parts):
        part = parts[i]

        if part == "curl":
            i += 1
            continue

        if part in ["-X", "--request"] and i + 1 < len(parts):
            method = parts[i + 1].upper()
            i += 2
            continue

        if part in ["-H", "--header"] and i + 1 < len(parts):
            header = parts[i + 1]
            if ":" in header:
                k, v = header.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
            continue

        if part in ["--data", "--data-raw", "--data-binary", "-d"] and i + 1 < len(parts):
            body = parts[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
            continue

        if part.startswith("http://") or part.startswith("https://"):
            url = part
            i += 1
            continue

        i += 1

    if not url:
        raise ValueError("Could not find a URL in the cURL command.")

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
    }


def parse_request_text(text: str):
    stripped = text.strip()
    if stripped.startswith("fetch("):
        return parse_fetch_snippet(stripped)
    if stripped.startswith("curl "):
        return parse_curl(stripped)
    raise ValueError("Paste either a browser fetch(...) snippet or a curl command.")


def clean_headers(headers: dict):
    cleaned = {}
    for k, v in headers.items():
        if k.lower() in EXCLUDED_HEADERS:
            continue
        cleaned[k] = v
    return cleaned


def redact_headers(headers: dict):
    redacted = {}
    for k, v in headers.items():
        if k.lower() in SENSITIVE_HEADERS:
            redacted[k] = (v[:20] + "...REDACTED...") if v else "REDACTED"
        else:
            redacted[k] = v
    return redacted


def try_json_loads(text):
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_nested(obj, path: str):
    if not path:
        return obj

    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(f"Path not found: {path}")
    return current


def auto_detect_list_path(payload):
    if isinstance(payload, list):
        return ""
    if not isinstance(payload, dict):
        return None

    for key in POSSIBLE_LIST_KEYS:
        if key in payload and isinstance(payload[key], list):
            return key

    for key, value in payload.items():
        if isinstance(value, dict):
            for nested_key in POSSIBLE_LIST_KEYS:
                if nested_key in value and isinstance(value[nested_key], list):
                    return f"{key}.{nested_key}"

    return None


def auto_detect_total_path(payload):
    if not isinstance(payload, dict):
        return None

    for key in POSSIBLE_TOTAL_KEYS:
        if key in payload and isinstance(payload[key], int):
            return key

    for key, value in payload.items():
        if isinstance(value, dict):
            for nested_key in POSSIBLE_TOTAL_KEYS:
                if nested_key in value and isinstance(value[nested_key], int):
                    return f"{key}.{nested_key}"

    return None


def update_url_query(url: str, updates: dict):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for k, v in updates.items():
        query[k] = str(v)
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def dedupe_records(records):
    seen = set()
    output = []

    for item in records:
        try:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
        except Exception:
            fingerprint = str(item)

        if fingerprint not in seen:
            seen.add(fingerprint)
            output.append(item)

    return output


def build_effective_headers(raw_headers: dict):
    headers = clean_headers(raw_headers)

    # Normalize common required Axion Ray headers if casing varies
    normalized = {}
    for k, v in headers.items():
        normalized[k] = v

    return normalized


def fetch_page(
    session,
    method,
    url,
    headers,
    body_text,
    offset_param,
    limit_param,
    offset_value,
    limit_value,
    pagination_location,
):
    body_json = try_json_loads(body_text)

    if pagination_location == "query":
        request_url = update_url_query(
            url,
            {
                offset_param: offset_value,
                limit_param: limit_value,
            },
        )

        if method == "GET":
            response = session.get(request_url, headers=headers, timeout=60)
        else:
            if body_json is not None:
                response = session.request(
                    method,
                    request_url,
                    headers=headers,
                    json=body_json,
                    timeout=60,
                )
            else:
                response = session.request(
                    method,
                    request_url,
                    headers=headers,
                    data=body_text,
                    timeout=60,
                )
    else:
        if body_json is None:
            raise ValueError("Body pagination selected, but the request body is not valid JSON.")

        body_json[offset_param] = offset_value
        body_json[limit_param] = limit_value

        response = session.request(
            method,
            url,
            headers=headers,
            json=body_json,
            timeout=60,
        )

    response.raise_for_status()
    return response.json()


def flatten_for_csv(records):
    if not records:
        return pd.DataFrame()
    return pd.json_normalize(records, sep=".")


st.title("Axion Ray Event Exporter")
st.caption("Paste a fetch(...) snippet or cURL from DevTools, fetch all pages, and download JSON/CSV.")

with st.expander("Security note", expanded=True):
    st.write("Use a fresh pasted request at runtime. Do not store bearer tokens in source code or shared files.")

st.subheader("Recommended defaults for your Axion Ray request")
c1, c2, c3 = st.columns(3)
c1.metric("Method", "POST")
c2.metric("Pagination", "query: offset + limit")
c3.metric("Rows path", DEFAULT_ROWS_PATH)

left, right = st.columns([1.35, 1])

with left:
    request_text = st.text_area(
        "Paste fetch(...) or cURL",
        height=320,
        placeholder='fetch("https://sharkninja.axionray.com/api/events/query-paginated?...',
    )

    if st.button("Parse request", type="primary"):
        try:
            st.session_state["parsed_request"] = parse_request_text(request_text)
            st.success("Request parsed.")
        except Exception as e:
            st.error(str(e))

    parsed = st.session_state.get("parsed_request")
    if parsed:
        effective_headers = build_effective_headers(parsed["headers"])
        body_preview = try_json_loads(parsed["body"])

        st.subheader("Parsed request preview")
        st.code(
            json.dumps(
                {
                    "method": parsed["method"],
                    "url": parsed["url"],
                    "headers": redact_headers(effective_headers),
                    "body": body_preview if body_preview is not None else parsed["body"],
                },
                indent=2,
            ),
            language="json",
        )

with right:
    st.subheader("Export settings")
    pagination_location = st.selectbox("Where are offset/limit fields?", ["query", "body"], index=0)
    offset_param = st.text_input("Offset parameter", value=DEFAULT_OFFSET_PARAM)
    limit_param = st.text_input("Limit parameter", value=DEFAULT_LIMIT_PARAM)
    start_offset = st.number_input("Start offset", min_value=0, value=0)
    page_size = st.number_input("Page size", min_value=1, value=DEFAULT_PAGE_SIZE)
    max_requests = st.number_input("Safety max requests", min_value=1, value=200)
    rows_path = st.text_input("Rows path", value=DEFAULT_ROWS_PATH)
    total_path = st.text_input("Total count path", value=DEFAULT_TOTAL_PATH)
    dedupe = st.checkbox("De-duplicate combined rows", value=True)
    export_csv = st.checkbox("Also create CSV", value=True)
    delay_seconds = st.number_input("Delay between requests (seconds)", min_value=0.0, value=0.25, step=0.25)
    output_prefix = st.text_input("Output filename prefix", value="axionray_events_export")

p1, p2 = st.columns(2)

with p1:
    if st.button("Preview first page"):
        parsed = st.session_state.get("parsed_request")
        if not parsed:
            st.error("Parse a request first.")
        else:
            try:
                session = requests.Session()
                headers = build_effective_headers(parsed["headers"])

                st.write("Headers being sent:")
                st.json(redact_headers(headers))

                payload = fetch_page(
                    session=session,
                    method=parsed["method"],
                    url=parsed["url"],
                    headers=headers,
                    body_text=parsed["body"],
                    offset_param=offset_param,
                    limit_param=limit_param,
                    offset_value=int(start_offset),
                    limit_value=int(page_size),
                    pagination_location=pagination_location,
                )

                st.write("Auto rows path:", auto_detect_list_path(payload) or "Not found")
                st.write("Auto total path:", auto_detect_total_path(payload) or "Not found")
                st.json(payload)

            except Exception as e:
                st.error(str(e))

with p2:
    if st.button("Fetch all pages"):
        parsed = st.session_state.get("parsed_request")
        if not parsed:
            st.error("Parse a request first.")
        else:
            try:
                session = requests.Session()
                headers = build_effective_headers(parsed["headers"])

                st.write("Headers being sent:")
                st.json(redact_headers(headers))

                combined_rows = []
                current_offset = int(start_offset)
                expected_total = None
                last_payload = None

                progress = st.progress(0)
                status = st.empty()

                for i in range(int(max_requests)):
                    payload = fetch_page(
                        session=session,
                        method=parsed["method"],
                        url=parsed["url"],
                        headers=headers,
                        body_text=parsed["body"],
                        offset_param=offset_param,
                        limit_param=limit_param,
                        offset_value=current_offset,
                        limit_value=int(page_size),
                        pagination_location=pagination_location,
                    )
                    last_payload = payload

                    rows = extract_nested(payload, rows_path.strip()) if rows_path.strip() else payload
                    if not isinstance(rows, list):
                        raise ValueError(f"Rows path '{rows_path}' did not resolve to a list.")

                    if total_path.strip():
                        try:
                            expected_total = extract_nested(payload, total_path.strip())
                        except Exception:
                            pass

                    status.write(f"Fetched offset {current_offset}: {len(rows)} rows")

                    if not rows:
                        break

                    combined_rows.extend(rows)

                    if expected_total is not None and len(combined_rows) >= int(expected_total):
                        break

                    if len(rows) < int(page_size):
                        break

                    current_offset += int(page_size)
                    progress.progress(min((i + 1) / int(max_requests), 1.0))

                    if delay_seconds > 0:
                        time.sleep(delay_seconds)

                if dedupe:
                    combined_rows = dedupe_records(combined_rows)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                export_bundle = {
                    "metadata": {
                        "exported_at": datetime.now().isoformat(),
                        "source_url": parsed["url"],
                        "method": parsed["method"],
                        "row_count": len(combined_rows),
                        "expected_total": expected_total,
                        "pagination": {
                            "location": pagination_location,
                            "offset_param": offset_param,
                            "limit_param": limit_param,
                            "start_offset": int(start_offset),
                            "page_size": int(page_size),
                        },
                    },
                    "rows": combined_rows,
                    "last_payload": last_payload,
                }

                json_bytes = json.dumps(export_bundle, indent=2, ensure_ascii=False).encode("utf-8")

                st.success(f"Export complete. Combined {len(combined_rows)} rows.")

                st.download_button(
                    "Download JSON",
                    data=json_bytes,
                    file_name=f"{output_prefix}_{timestamp}.json",
                    mime="application/json",
                )

                if export_csv:
                    df = flatten_for_csv(combined_rows)
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        "Download CSV",
                        data=csv_bytes,
                        file_name=f"{output_prefix}_{timestamp}.csv",
                        mime="text/csv",
                    )
                    st.write(f"CSV columns: {len(df.columns)}")

                if combined_rows:
                    st.subheader("Preview rows")
                    st.json(combined_rows[:3])

            except Exception as e:
                st.error(str(e))

st.divider()
st.markdown(
    """
### Axion Ray-specific notes
- Request method is `POST`.
- Pagination is in the URL query string using `offset` and `limit`.
- Filter logic stays in the JSON request body.
- `x-tenant-id` and `x-workspace-id` must be preserved.
- If `includeCount=false`, the response may not include a total count.
- In that case, exporting stops when a page returns fewer than the requested limit.
"""
)
