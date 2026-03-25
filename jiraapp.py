from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jira_dashboard.charts import (
    activity_line,
    author_bar,
    completeness_bar,
    investigation_status_bar,
    issue_health_scatter,
    issue_type_donut,
    sku_quality_scatter,
    stacked_status_by_base_sku,
    top_base_sku_bar,
    top_tag_bar,
)
from jira_dashboard.insights import (
    DashboardFilters,
    build_activity_series,
    build_author_rollup,
    build_completeness_frame,
    build_issue_first_seen_series,
    build_issue_table,
    build_kpis,
    build_risk_queue,
    build_sku_rollup,
    filter_comments,
    filter_issues,
    split_tag_values,
    unique_non_empty,
)
from jira_dashboard.preprocess import process_csv, process_uploaded_bytes
from jira_dashboard.styles import app_css, badge_row, format_dt, issue_text_block, pct, timeline_card

APP_DIR = Path(__file__).resolve().parent
RAW_PATH = APP_DIR / "data" / "raw" / "Jira - SharkNinja (20).csv"
PROCESSED_DIR = APP_DIR / "data" / "processed"
ISSUES_PATH = PROCESSED_DIR / "issues_clean.csv"
COMMENTS_PATH = PROCESSED_DIR / "comments_long.csv"
METADATA_PATH = PROCESSED_DIR / "metadata.json"


st.set_page_config(
    page_title="Jira Quality Command Center",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(show_spinner=False)
def load_packaged_data() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if not ISSUES_PATH.exists() or not COMMENTS_PATH.exists():
        process_csv(RAW_PATH, PROCESSED_DIR)

    issues = pd.read_csv(ISSUES_PATH, parse_dates=["first_comment_at", "latest_comment_at"])
    comments = pd.read_csv(COMMENTS_PATH, parse_dates=["comment_timestamp"])
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8")) if METADATA_PATH.exists() else {}
    return issues, comments, metadata


@st.cache_data(show_spinner=False)
def load_uploaded_data(file_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    processed = process_uploaded_bytes(file_bytes)
    return processed.issues, processed.comments, processed.metadata


def percent_delta(filtered_value: float, full_value: float) -> str:
    delta_pp = (filtered_value - full_value) * 100
    return f"{delta_pp:+.1f} pts vs full export"


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


st.markdown(app_css(), unsafe_allow_html=True)

with st.sidebar:
    st.title("Jira Quality")
    st.caption("Clean operational dashboard built from the uploaded Jira export.")

    uploaded_file = st.file_uploader("Swap in a new Jira CSV export", type=["csv"])

    if uploaded_file is not None:
        issues, comments, metadata = load_uploaded_data(uploaded_file.getvalue())
        data_source = uploaded_file.name
    else:
        issues, comments, metadata = load_packaged_data()
        data_source = RAW_PATH.name

    st.markdown("---")
    st.caption(f"Data source: **{data_source}**")
    if metadata.get("activity_end"):
        st.caption(f"Latest activity captured: **{format_dt(metadata['activity_end'])}**")
    st.caption("Timeline charts use comment timestamps because this export does not include created or resolved issue dates.")

    st.subheader("Filters")
    base_sku_options = unique_non_empty(issues["base_sku"])
    issue_type_options = unique_non_empty(issues["issue_type"])
    failure_mode_options = split_tag_values(issues["failure_modes"])
    component_options = split_tag_values(issues["components"])

    selected_base_skus = st.multiselect("Base SKU", base_sku_options)
    selected_issue_types = st.multiselect("Issue type", issue_type_options, default=issue_type_options)
    investigation_view = st.selectbox(
        "Investigation view",
        [
            "All",
            "Has root cause",
            "Missing root cause",
            "Has corrective action",
            "Missing corrective action",
            "Missing both",
            "Root cause + action",
        ],
    )
    selected_failure_modes = st.multiselect("Failure mode tags", failure_mode_options)
    selected_components = st.multiselect("Component tags", component_options)

    max_comments = int(issues["comment_count"].max()) if not issues.empty else 0
    min_comments = st.slider("Minimum comments per issue", min_value=0, max_value=max_comments, value=0)

    latest_activity_range = None
    if issues["latest_comment_at"].notna().any():
        min_date = issues["latest_comment_at"].dropna().dt.date.min()
        max_date = issues["latest_comment_at"].dropna().dt.date.max()
        selected_dates = st.date_input(
            "Latest activity date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
            latest_activity_range = selected_dates

    search_query = st.text_input("Search issue text", placeholder="Issue key, symptom, root cause, comment keywords")

    full_filters = DashboardFilters(
        base_skus=tuple(selected_base_skus),
        issue_types=tuple(selected_issue_types),
        failure_modes=tuple(selected_failure_modes),
        components=tuple(selected_components),
        investigation_view=investigation_view,
        min_comments=min_comments,
        latest_activity_range=latest_activity_range,
        search_query=search_query,
    )

    filtered_issues = filter_issues(issues, full_filters)
    filtered_comments = filter_comments(comments, filtered_issues)

    st.markdown("---")
    st.subheader("Downloads")
    st.download_button("Processed issues_clean.csv", csv_bytes(filtered_issues), file_name="issues_clean_filtered.csv", mime="text/csv")
    st.download_button("Processed comments_long.csv", csv_bytes(filtered_comments), file_name="comments_long_filtered.csv", mime="text/csv")


full_kpis = build_kpis(issues, comments)
filtered_kpis = build_kpis(filtered_issues, filtered_comments)

hero_text = f"""
<div class="hero-card">
    <h1>Jira Quality Command Center</h1>
    <p>
        Built on a cleaned issue table plus a normalized comment timeline so this dashboard can grow into AI summaries,
        semantic search, and failure-mode clustering later without changing the core data model.
        Current view: {filtered_kpis['issue_count']:,} issues and {filtered_kpis['comment_count']:,} comments.
    </p>
</div>
"""
st.markdown(hero_text, unsafe_allow_html=True)

scope_note = f"""
<div class="soft-note">
    <strong>What you are looking at:</strong> one normalized issue record per Jira ticket, plus one long-form comment record per comment.
    The export contains {metadata.get('comment_columns_detected', 'many')} comment columns in the raw file, so the app reshapes them into a timeline automatically.
    Root-cause and corrective-action coverage can now be tracked directly across products and investigation queues.
</div>
"""
st.markdown(scope_note, unsafe_allow_html=True)

metric_columns = st.columns(6)
metric_columns[0].metric("Issues in view", f"{filtered_kpis['issue_count']:,}", f"{filtered_kpis['issue_count'] / max(full_kpis['issue_count'], 1):.0%} of full export")
metric_columns[1].metric("Comments in view", f"{filtered_kpis['comment_count']:,}")
metric_columns[2].metric("Base SKUs in view", f"{filtered_kpis['base_sku_count']:,}")
metric_columns[3].metric(
    "Root cause coverage",
    pct(filtered_kpis["root_cause_coverage"]),
    percent_delta(filtered_kpis["root_cause_coverage"], full_kpis["root_cause_coverage"]),
)
metric_columns[4].metric(
    "Corrective action coverage",
    pct(filtered_kpis["corrective_action_coverage"]),
    percent_delta(filtered_kpis["corrective_action_coverage"], full_kpis["corrective_action_coverage"]),
)
metric_columns[5].metric("Median comments / issue", f"{filtered_kpis['median_comments']:.1f}")

overview_tab, quality_tab, investigation_tab, explorer_tab, ai_tab = st.tabs(
    [
        "Executive overview",
        "Quality drivers",
        "Investigation health",
        "Issue explorer",
        "AI-ready foundation",
    ]
)

with overview_tab:
    st.markdown('<div class="section-caption">Fast scan of the current filtered cohort.</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(issue_type_donut(filtered_issues), use_container_width=True)
    with col2:
        st.plotly_chart(top_base_sku_bar(filtered_issues), use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        st.plotly_chart(activity_line(build_activity_series(filtered_comments), title="Comment activity by month"), use_container_width=True)
    with col4:
        st.plotly_chart(top_tag_bar(filtered_issues, "failure_modes", "Top extracted failure modes"), use_container_width=True)

    with st.expander("Show filtered issue table", expanded=False):
        issue_table = build_issue_table(filtered_issues)
        st.dataframe(issue_table, use_container_width=True, height=420, hide_index=True)
        st.download_button("Download filtered issue table", csv_bytes(issue_table), file_name="filtered_issue_table.csv", mime="text/csv")

with quality_tab:
    st.markdown('<div class="section-caption">Which product families and failure themes are driving the workload.</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(top_tag_bar(filtered_issues, "failure_modes", "Failure mode concentration"), use_container_width=True)
    with col2:
        st.plotly_chart(top_tag_bar(filtered_issues, "components", "Component concentration"), use_container_width=True)

    sku_rollup = build_sku_rollup(filtered_issues, top_n=18)
    col3, col4 = st.columns(2)
    with col3:
        st.plotly_chart(sku_quality_scatter(sku_rollup), use_container_width=True)
    with col4:
        st.plotly_chart(stacked_status_by_base_sku(filtered_issues), use_container_width=True)

    col5, col6 = st.columns(2)
    with col5:
        st.plotly_chart(top_tag_bar(filtered_issues, "evidence_tags", "Evidence and test-signal tags"), use_container_width=True)
    with col6:
        st.plotly_chart(
            activity_line(build_issue_first_seen_series(filtered_issues), title="Issues first seen by month (comment-based proxy)"),
            use_container_width=True,
        )

    with st.expander("Base SKU rollup", expanded=False):
        st.dataframe(sku_rollup, use_container_width=True, hide_index=True)

with investigation_tab:
    st.markdown('<div class="section-caption">Coverage, queue risk, and investigation discipline.</div>', unsafe_allow_html=True)

    missing_root = int((~filtered_issues["has_root_cause"]).sum()) if not filtered_issues.empty else 0
    missing_action = int((~filtered_issues["has_corrective_action"]).sum()) if not filtered_issues.empty else 0
    missing_both = int((filtered_issues["investigation_status"] == "Missing both").sum()) if not filtered_issues.empty else 0

    row = st.columns(4)
    row[0].metric("Missing root cause", f"{missing_root:,}")
    row[1].metric("Missing corrective action", f"{missing_action:,}")
    row[2].metric("Missing both", f"{missing_both:,}")
    row[3].metric("High-discussion, no root cause", f"{filtered_kpis['high_discussion_missing_rc']:,}")

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(investigation_status_bar(filtered_issues), use_container_width=True)
    with col2:
        st.plotly_chart(completeness_bar(build_completeness_frame(filtered_issues)), use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        st.plotly_chart(issue_health_scatter(filtered_issues), use_container_width=True)
    with col4:
        st.plotly_chart(author_bar(build_author_rollup(filtered_comments)), use_container_width=True)

    risk_queue = build_risk_queue(filtered_issues)
    st.markdown('<div class="section-caption">Priority queue: discussion-heavy issues with no root cause captured yet.</div>', unsafe_allow_html=True)
    st.dataframe(risk_queue, use_container_width=True, height=380, hide_index=True)

with explorer_tab:
    st.markdown('<div class="section-caption">Single-issue drilldown with cleaned narrative fields and the full comment trail.</div>', unsafe_allow_html=True)

    if filtered_issues.empty:
        st.info("No issues match the current filters.")
    else:
        issue_picker_df = filtered_issues.sort_values(["latest_comment_at", "comment_count"], ascending=[False, False])
        issue_options = issue_picker_df["issue_key"].tolist()
        selected_issue_key = st.selectbox("Choose an issue", issue_options)
        selected_issue = filtered_issues[filtered_issues["issue_key"] == selected_issue_key].iloc[0]
        selected_comments = filtered_comments[filtered_comments["issue_key"] == selected_issue_key].sort_values("comment_index")

        st.markdown(
            f"""
            <div class="soft-card">
                <strong>{selected_issue['issue_key']}</strong> · {selected_issue['issue_type']} · Base SKU: {selected_issue['base_sku'] or '—'}
                <div class="small-muted">
                    Investigation status: {selected_issue['investigation_status']} · Comments: {selected_issue['comment_count']} ·
                    Latest activity: {format_dt(selected_issue['latest_comment_at'])}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        info_cols = st.columns(4)
        info_cols[0].metric("Comments", f"{int(selected_issue['comment_count'])}")
        info_cols[1].metric("Unique authors", f"{int(selected_issue['unique_comment_authors'])}")
        info_cols[2].metric("Activity window", f"{int(selected_issue['activity_window_days'])} days")
        info_cols[3].metric("Image evidence", "Yes" if bool(selected_issue['has_image_evidence']) else "No")

        st.markdown('<div class="section-caption">Extracted issue tags</div>', unsafe_allow_html=True)
        failure_badges = [(tag, "") for tag in str(selected_issue["failure_modes"]).split(" | ") if tag.strip()]
        component_badges = [(tag, "secondary") for tag in str(selected_issue["components"]).split(" | ") if tag.strip()]
        evidence_badges = [(tag, "warning") for tag in str(selected_issue["evidence_tags"]).split(" | ") if tag.strip()]
        st.markdown(badge_row(failure_badges + component_badges + evidence_badges), unsafe_allow_html=True)

        left, right = st.columns(2)
        with left:
            st.markdown(issue_text_block("Description", selected_issue.get("description_clean", "")), unsafe_allow_html=True)
            st.markdown(issue_text_block("Root cause", selected_issue.get("root_cause_clean", "")), unsafe_allow_html=True)
        with right:
            st.markdown(issue_text_block("Corrective action", selected_issue.get("corrective_action_clean", "")), unsafe_allow_html=True)
            st.markdown(
                issue_text_block(
                    "Structured fields",
                    "\n".join(
                        [
                            f"Base SKU: {selected_issue.get('base_sku', '') or '—'}",
                            f"SKU(s): {selected_issue.get('skus', '') or '—'}",
                            f"Serial number: {selected_issue.get('serial_number', '') or '—'}",
                            f"Symptom: {selected_issue.get('symptom', '') or '—'}",
                            f"Primary failure mode: {selected_issue.get('primary_failure_mode', '') or 'Unclassified'}",
                            f"Primary component: {selected_issue.get('primary_component', '') or 'Unclassified'}",
                        ]
                    ),
                ),
                unsafe_allow_html=True,
            )

        st.markdown('<div class="section-caption">Comment timeline</div>', unsafe_allow_html=True)
        if selected_comments.empty:
            st.info("No comments available for this issue.")
        else:
            for row in selected_comments.itertuples(index=False):
                st.markdown(
                    timeline_card(
                        timestamp=format_dt(row.comment_timestamp),
                        author_id=row.comment_author_id,
                        body=row.comment_body_clean,
                        index=int(row.comment_index),
                    ),
                    unsafe_allow_html=True,
                )

with ai_tab:
    st.markdown('<div class="section-caption">A clean base to add AI capabilities without reworking the app later.</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            """
            <div class="soft-card">
                <h4>Ready now</h4>
                <p>Each issue has a consolidated analysis text field, normalized comment history, and rule-based tags for failure mode, component, and evidence.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            """
            <div class="soft-card">
                <h4>Best next AI step</h4>
                <p>Add semantic search and issue summaries over the cleaned issue + comment text. That gives immediate value without changing the front-end structure.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            """
            <div class="soft-card">
                <h4>After that</h4>
                <p>Layer in failure-mode clustering, duplicate detection, and corrective-action suggestion workflows using the normalized tables already in this package.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div class="section-caption">Fields already prepared for future AI workflows</div>', unsafe_allow_html=True)
    ai_ready_columns = [
        "issue_key",
        "base_sku",
        "primary_failure_mode",
        "primary_component",
        "evidence_tags",
        "investigation_status",
        "analysis_text",
    ]
    ai_ready_view = filtered_issues[ai_ready_columns].copy() if not filtered_issues.empty else filtered_issues.iloc[0:0]
    ai_ready_view["analysis_text"] = ai_ready_view["analysis_text"].fillna("").astype(str).str.slice(0, 320)
    st.dataframe(ai_ready_view, use_container_width=True, height=420, hide_index=True)

    st.markdown(
        """
        <div class="soft-note">
            Recommended phase order: <strong>1)</strong> validate this dashboard and the cleaned schema,
            <strong>2)</strong> add semantic search + issue summaries, <strong>3)</strong> add clustering and duplicate-detection,
            <strong>4)</strong> add guided root-cause and corrective-action copilots.
        </div>
        """,
        unsafe_allow_html=True,
    )
