from __future__ import annotations

from datetime import datetime
from hashlib import sha1
from typing import Any, Optional

import pandas as pd
import streamlit as st

try:
    from ops_ui.athena_query import execute_athena_query, validate_athena_settings
    from ops_ui.drilldown import (
        DRILLDOWN_SPECS,
        DrilldownRequest,
        allowed_identifiers,
        metric_options,
        parse_drilldown_request,
        resolve_drilldown_request,
    )
    from ops_ui.iceberg_inspector import (
        SnapshotInfo,
        TableSummary,
        load_catalog_summaries,
        load_table_data_profile,
        load_table_detail,
        refresh_row_count,
    )
    from ops_ui.s3_inspector import latest_object
except ModuleNotFoundError:
    from athena_query import execute_athena_query, validate_athena_settings
    from drilldown import (
        DRILLDOWN_SPECS,
        DrilldownRequest,
        allowed_identifiers,
        metric_options,
        parse_drilldown_request,
        resolve_drilldown_request,
    )
    from iceberg_inspector import (
        SnapshotInfo,
        TableSummary,
        load_catalog_summaries,
        load_table_data_profile,
        load_table_detail,
        refresh_row_count,
    )
    from s3_inspector import latest_object


st.set_page_config(page_title="Iceberg 운영 현황", layout="wide")


PROFILE_TABLES = {
    "oliveyoung_db.oliveyoung_silver_current",
    "oliveyoung_db.oliveyoung_silver_error",
}


@st.cache_data(ttl=300, show_spinner=False)
def cached_summaries() -> dict[str, list[TableSummary]]:
    return load_catalog_summaries()


def format_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "N/A"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "N/A"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size):,} {unit}"
            return f"{size:,.1f} {unit}"
        size /= 1024
    return "N/A"


def format_elapsed_ms(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value < 1000:
        return f"{value:,.1f} ms"
    return f"{value / 1000:,.2f} s"


def summary_frame(rows: list[TableSummary]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "table": row.table_name,
                "identifier": row.identifier,
                "table_location": row.table_location,
                "metadata_location": row.metadata_location,
                "snapshot_id": row.current_snapshot_id or "N/A",
                "snapshot_commit_time": format_dt(row.snapshot_committed_at),
                "latest_batch_date": format_dt(row.latest_batch_date),
                "estimated_run_id": row.estimated_run_id,
                "row_count": row.row_count,
                "data_size": format_bytes(row.total_data_size_bytes),
                "load_time": format_elapsed_ms(row.load_elapsed_ms),
                "status": row.load_error or "OK",
            }
            for row in rows
        ]
    )


def render_summary_table(catalog_label: str, rows: list[TableSummary]) -> None:
    st.subheader(catalog_label)
    st.dataframe(
        summary_frame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "table_location": st.column_config.TextColumn(width="large"),
            "metadata_location": st.column_config.TextColumn(width="large"),
            "status": st.column_config.TextColumn(width="medium"),
        },
    )


def snapshot_options(snapshots: list[SnapshotInfo]) -> list[tuple[str, Optional[int]]]:
    options: list[tuple[str, Optional[int]]] = [("현재 스냅샷", None)]
    for snapshot in snapshots:
        label = f"{snapshot.snapshot_id} | {format_dt(snapshot.committed_at)} | {snapshot.operation}"
        options.append((label, snapshot.snapshot_id))
    return options


def render_profile_tables(catalog_label: str, identifier: str, snapshots: list[SnapshotInfo]) -> None:
    if identifier not in PROFILE_TABLES:
        return

    st.markdown("#### 데이터 요약")
    options = snapshot_options(snapshots)
    selected_label = st.selectbox(
        "조회 스냅샷",
        options=[label for label, _ in options],
        key=f"profile-snapshot:{catalog_label}:{identifier}",
    )
    snapshot_id = dict(options)[selected_label]

    with st.spinner("선택한 스냅샷의 batch_date 목록을 조회하는 중입니다."):
        unfiltered_profile = load_table_data_profile(catalog_label, identifier, snapshot_id)

    batch_date_options = ["전체"] + [str(row["batch_date"]) for row in unfiltered_profile.date_rows]
    selected_batch_date = st.selectbox(
        "batch_date",
        options=batch_date_options,
        key=f"profile-batch-date:{catalog_label}:{identifier}:{snapshot_id}",
    )

    if selected_batch_date == "전체":
        profile = unfiltered_profile
    else:
        with st.spinner("선택한 batch_date의 데이터를 집계하는 중입니다."):
            profile = load_table_data_profile(catalog_label, identifier, snapshot_id, selected_batch_date)

    metric_label = "선택 범위 row count" if selected_batch_date == "전체" else f"{selected_batch_date} row count"
    st.metric(metric_label, f"{profile.row_count:,}")

    date_tab, batch_tab, category_tab, error_tab, sample_tab = st.tabs(
        ["날짜별", "배치별", "카테고리별", "오류유형별", "샘플"]
    )
    with date_tab:
        st.dataframe(pd.DataFrame(profile.date_rows), use_container_width=True, hide_index=True)
    with batch_tab:
        st.dataframe(pd.DataFrame(profile.batch_rows), use_container_width=True, hide_index=True)
    with category_tab:
        st.dataframe(pd.DataFrame(profile.category_rows), use_container_width=True, hide_index=True)
    with error_tab:
        if profile.error_rows:
            st.dataframe(pd.DataFrame(profile.error_rows), use_container_width=True, hide_index=True)
        else:
            st.info("이 테이블에는 error_type 컬럼이 없습니다.")
    with sample_tab:
        st.dataframe(pd.DataFrame(profile.sample_rows), use_container_width=True, hide_index=True)


def render_detail(catalog_label: str, identifier: str) -> None:
    with st.spinner("테이블 상세 정보를 조회하는 중입니다."):
        detail = load_table_detail(catalog_label, identifier)

    st.markdown(f"### {identifier}")
    if detail.summary.load_error:
        st.error(detail.summary.load_error)
        return

    snapshot_col, commit_col, rows_col, size_col, load_col = st.columns(5)
    snapshot_col.metric("Current snapshot", detail.summary.current_snapshot_id or "N/A")
    commit_col.metric("Snapshot commit", format_dt(detail.summary.snapshot_committed_at))
    rows_col.metric("Row count", detail.summary.row_count)
    size_col.metric("Data size", format_bytes(detail.summary.total_data_size_bytes))
    load_col.metric("Load time", format_elapsed_ms(detail.summary.load_elapsed_ms))

    if st.button("row count 새로고침", key=f"count:{catalog_label}:{identifier}"):
        with st.spinner("전체 테이블을 스캔해 row 수를 계산하는 중입니다."):
            st.session_state.setdefault("row_counts", {})[identifier] = refresh_row_count(catalog_label, identifier)

    if identifier in st.session_state.get("row_counts", {}):
        st.info(f"명시적 scan row count: {st.session_state['row_counts'][identifier]:,}")

    st.markdown("#### 위치")
    st.code(
        f"table location: {detail.summary.table_location}\n"
        f"metadata location: {detail.summary.metadata_location}",
        language="text",
    )

    with st.expander("S3 LastModified 보조 정보"):
        try:
            obj = latest_object(detail.summary.table_location)
            if obj is None:
                st.write("S3 객체 정보를 찾지 못했습니다.")
            else:
                st.write(
                    {
                        "key": obj.key,
                        "last_modified": format_dt(obj.last_modified),
                        "size": obj.size,
                    }
                )
        except Exception as exc:
            st.warning(f"S3 객체 조회 실패: {exc}")

    render_profile_tables(catalog_label, identifier, detail.snapshots)

    spec_tab, schema_tab, snapshots_tab = st.tabs(["테이블 구조", "Schema", "Snapshots"])
    with spec_tab:
        st.markdown("#### Partition spec")
        st.code(detail.partition_spec, language="text")
        st.markdown("#### Sort order")
        st.code(detail.sort_order, language="text")

    with schema_tab:
        st.dataframe(pd.DataFrame(detail.schema_rows), use_container_width=True, hide_index=True)

    with snapshots_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "committed_at": format_dt(snapshot.committed_at),
                        "operation": snapshot.operation,
                        "summary": snapshot.summary,
                    }
                    for snapshot in detail.snapshots
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def query_params_dict() -> dict[str, Any]:
    return dict(st.query_params)


def set_query_params(**params: str) -> None:
    st.query_params.clear()
    for key, value in params.items():
        if value:
            st.query_params[key] = value


def request_signature(request: DrilldownRequest) -> str:
    raw = "|".join(
        [
            request.catalog,
            request.table,
            request.metric,
            request.snapshot_mode,
            request.as_of,
            request.snapshot_id,
            request.batch_date,
            request.previous_as_of,
            request.previous_snapshot_id,
            request.previous_batch_date,
            request.batch_job,
            request.error_type,
            request.main_category,
            request.sub_category,
            request.from_ts,
            request.to_ts,
        ]
    )
    return sha1(raw.encode("utf-8")).hexdigest()[:12]


def _drilldown_snapshot_options(snapshots: list[SnapshotInfo]) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for snapshot in snapshots:
        if snapshot.snapshot_id is None:
            continue
        label = f"{snapshot.snapshot_id} | {format_dt(snapshot.committed_at)} | {snapshot.operation}"
        options.append((label, str(snapshot.snapshot_id)))
    return options


def _current_drilldown_snapshot_options(snapshots: list[SnapshotInfo]) -> list[tuple[str, str]]:
    return [("현재 스냅샷", "__latest__"), *_drilldown_snapshot_options(snapshots)]


def _index_or_zero(options: list[str], value: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0


def _metric_filter_defaults(metric: str, params: dict[str, Any]) -> dict[str, str]:
    spec = DRILLDOWN_SPECS[metric]
    defaults = {
        "batch_job": str(params.get("batch_job", "")).strip(),
        "error_type": str(params.get("error_type", "")).strip(),
        "main_category": str(params.get("main_category", "")).strip(),
        "sub_category": str(params.get("sub_category", "")).strip(),
        "from": str(params.get("from", "")).strip(),
        "to": str(params.get("to", "")).strip(),
    }
    if not defaults["error_type"] and "error_type" in spec.required_filters:
        defaults["error_type"] = "category_parse_failed"
    return defaults


def render_drilldown_controls(params: dict[str, Any]) -> None:
    metric_keys = [key for key, _label in metric_options()]
    selected_metric = str(params.get("metric", "")).strip() or metric_keys[0]
    if selected_metric not in DRILLDOWN_SPECS:
        selected_metric = metric_keys[0]
    spec = DRILLDOWN_SPECS[selected_metric]

    identifier_options = sorted(allowed_identifiers())
    current_identifier = str(params.get("table", "")).strip()
    if not current_identifier:
        current_identifier = f"{spec.default_catalog}.{spec.default_table}"
    elif "." not in current_identifier:
        current_identifier = f"{str(params.get('catalog', spec.default_catalog)).strip()}.{current_identifier}"
    if current_identifier not in identifier_options:
        identifier_options = [current_identifier, *identifier_options]

    current_catalog, current_table = current_identifier.split(".", 1)
    try:
        detail = load_table_detail(current_catalog, current_identifier)
    except Exception as exc:
        detail = None
        st.warning(f"비교 UI용 스냅샷 정보를 불러오지 못했습니다: {exc}")

    current_snapshot_options = _current_drilldown_snapshot_options(detail.snapshots if detail else [])
    current_snapshot_labels = [label for label, _value in current_snapshot_options]
    current_snapshot_values = dict(current_snapshot_options)
    compare_snapshot_options = _drilldown_snapshot_options(detail.snapshots if detail else [])
    compare_snapshot_labels = [label for label, _value in compare_snapshot_options]
    compare_snapshot_values = dict(compare_snapshot_options)
    latest_batch_date = ""
    if detail and detail.summary.latest_batch_date is not None:
        latest_batch_date = detail.summary.latest_batch_date.strftime("%Y-%m-%d")

    compare_enabled = any(
        str(params.get(key, "")).strip()
        for key in ("previous_as_of", "previous_snapshot_id", "previous_batch_date")
    )
    defaults = _metric_filter_defaults(selected_metric, params)

    with st.expander("비교 조건", expanded=True):
        with st.form("drilldown-controls"):
            metric = st.selectbox(
                "Metric",
                options=metric_keys,
                index=_index_or_zero(metric_keys, selected_metric),
                format_func=lambda key: DRILLDOWN_SPECS[key].label,
            )
            identifier = st.selectbox(
                "Table",
                options=identifier_options,
                index=_index_or_zero(identifier_options, current_identifier),
            )

            batch_default = str(params.get("batch_date", "")).strip() or latest_batch_date
            batch_date = st.text_input("Current batch_date", value=batch_default, placeholder="YYYY-MM-DD")

            requested_snapshot_id = str(params.get("snapshot_id", "")).strip()
            current_snapshot_default = "현재 스냅샷"
            if str(params.get("snapshot_mode", "latest")).strip() == "asof" and requested_snapshot_id:
                current_snapshot_default = next(
                    (label for label, value in current_snapshot_options if value == requested_snapshot_id),
                    current_snapshot_default,
                )
            current_snapshot_label = st.selectbox(
                "Current snapshot",
                options=current_snapshot_labels,
                index=_index_or_zero(current_snapshot_labels, current_snapshot_default),
            )

            compare_enabled = st.checkbox("비교 모드", value=compare_enabled)
            previous_batch_date = ""
            previous_snapshot_label = ""
            if compare_enabled:
                previous_batch_default = str(params.get("previous_batch_date", "")).strip() or batch_date
                previous_batch_date = st.text_input(
                    "Previous batch_date",
                    value=previous_batch_default,
                    placeholder="YYYY-MM-DD",
                )
                requested_previous_snapshot_id = str(params.get("previous_snapshot_id", "")).strip()
                previous_selected_label = next(
                    (label for label, value in compare_snapshot_options if value == requested_previous_snapshot_id),
                    compare_snapshot_labels[0] if compare_snapshot_labels else "",
                )
                if compare_snapshot_labels:
                    previous_snapshot_label = st.selectbox(
                        "Previous snapshot",
                        options=compare_snapshot_labels,
                        index=_index_or_zero(compare_snapshot_labels, previous_selected_label),
                    )
                else:
                    st.caption("선택 가능한 이전 스냅샷이 없습니다.")

            filter_left, filter_right = st.columns(2)
            with filter_left:
                main_category = st.text_input("main_category", value=defaults["main_category"])
                batch_job = st.text_input("batch_job", value=defaults["batch_job"])
                from_ts = st.text_input("from", value=defaults["from"], placeholder="ISO8601")
            with filter_right:
                sub_category = st.text_input("sub_category", value=defaults["sub_category"])
                error_type = st.text_input("error_type", value=defaults["error_type"])
                to_ts = st.text_input("to", value=defaults["to"], placeholder="ISO8601")

            submitted = st.form_submit_button("조건 적용", use_container_width=True)

        if submitted:
            next_spec = DRILLDOWN_SPECS[metric]
            selected_identifier = identifier if "." in identifier else f"{next_spec.default_catalog}.{identifier}"
            catalog, table = selected_identifier.split(".", 1)
            next_params: dict[str, str] = {
                "view": "drilldown",
                "metric": metric,
                "catalog": catalog,
                "table": table,
                "batch_date": batch_date.strip(),
                "batch_job": batch_job.strip(),
                "error_type": error_type.strip(),
                "main_category": main_category.strip(),
                "sub_category": sub_category.strip(),
                "from": from_ts.strip(),
                "to": to_ts.strip(),
            }
            selected_current_snapshot = current_snapshot_values.get(current_snapshot_label, "__latest__")
            if selected_current_snapshot == "__latest__":
                next_params["snapshot_mode"] = "latest"
            else:
                next_params["snapshot_mode"] = "asof"
                next_params["snapshot_id"] = selected_current_snapshot

            if compare_enabled:
                next_params["previous_batch_date"] = previous_batch_date.strip()
                if previous_snapshot_label:
                    next_params["previous_snapshot_id"] = compare_snapshot_values[previous_snapshot_label]

            set_query_params(**next_params)
            st.rerun()


def render_drilldown_sidebar() -> None:
    st.sidebar.markdown("### Drilldown 링크 예시")
    for metric_key, label in metric_options():
        if st.sidebar.button(label, key=f"drilldown-nav:{metric_key}", use_container_width=True):
            spec = DRILLDOWN_SPECS[metric_key]
            set_query_params(
                view="drilldown",
                metric=metric_key,
                catalog=spec.default_catalog,
                table=spec.default_table,
                snapshot_mode="latest",
            )
            st.rerun()


def render_drilldown_view() -> None:
    st.title("Grafana Drilldown")
    render_drilldown_sidebar()
    if st.button("홈 UI로 돌아가기", use_container_width=True):
        set_query_params()
        st.rerun()

    missing_vars = validate_athena_settings()
    if missing_vars:
        st.error(f"Athena 설정이 부족합니다: {', '.join(missing_vars)}")
        return

    params = query_params_dict()
    render_drilldown_controls(params)
    try:
        request = parse_drilldown_request(params)
    except Exception as exc:
        st.error(f"Drilldown 파라미터 오류: {exc}")
        render_drilldown_help()
        return

    if request is None:
        render_drilldown_help()
        return

    try:
        resolved = resolve_drilldown_request(request)
    except Exception as exc:
        st.error(f"Drilldown 컨텍스트 조회 실패: {exc}")
        return

    st.caption(resolved.spec.description)
    st.dataframe(pd.DataFrame(resolved.context_rows), use_container_width=True, hide_index=True)

    sql_key = f"drilldown-sql:{request_signature(resolved.request)}"
    auto_key = f"drilldown-auto:{request_signature(resolved.request)}"
    if sql_key not in st.session_state:
        st.session_state[sql_key] = resolved.generated_sql
        st.session_state[auto_key] = True

    st.markdown("#### SQL")
    st.text_area("Athena SQL", key=sql_key, height=260)
    run_clicked = st.button("쿼리 실행", type="primary", use_container_width=True)
    auto_run = st.session_state.get(auto_key, False)

    if run_clicked or auto_run:
        st.session_state[auto_key] = False
        with st.spinner("Athena 쿼리를 실행하는 중입니다."):
            try:
                result = execute_athena_query(st.session_state[sql_key])
            except Exception as exc:
                st.error(normalize_athena_error(exc, resolved.request))
                return

        left, middle, right = st.columns(3)
        left.metric("Rows", f"{result.row_count:,}")
        middle.metric("실행 시간", format_elapsed_ms(float(result.execution_time_ms)))
        right.metric("Query ID", result.query_execution_id)
        st.dataframe(pd.DataFrame(result.rows), use_container_width=True, hide_index=True)

    with st.expander("현재 테이블 상세 보기"):
        render_detail(request.catalog, request.identifier)


def render_drilldown_help() -> None:
    st.info("`view=drilldown` 과 `metric` 파라미터로 진입하면 Grafana 연동 쿼리를 자동 실행합니다.")
    examples = pd.DataFrame(
        [
            {
                "metric": "category_failure",
                "label": DRILLDOWN_SPECS["category_failure"].label,
                "example": "?view=drilldown&metric=category_failure&catalog=oliveyoung_db&table=oliveyoung_silver_error&main_category=스킨케어&sub_category=토너&snapshot_mode=latest",
            },
            {
                "metric": "error_type_spike",
                "label": DRILLDOWN_SPECS["error_type_spike"].label,
                "example": "?view=drilldown&metric=error_type_spike&catalog=oliveyoung_db&table=oliveyoung_silver_error&error_type=category_parse_failed&snapshot_mode=latest",
            },
            {
                "metric": "category_success_count",
                "label": DRILLDOWN_SPECS["category_success_count"].label,
                "example": "?view=drilldown&metric=category_success_count&catalog=oliveyoung_db&table=oliveyoung_silver_current&snapshot_mode=latest&batch_date=2026-07-11&main_category=메이크업",
            },
        ]
    )
    st.dataframe(examples, use_container_width=True, hide_index=True)


def normalize_athena_error(exc: Exception, request: DrilldownRequest) -> str:
    message = str(exc)
    if request.snapshot_mode == "asof":
        lowered = message.lower()
        if (
            "expire" in lowered
            or "expired" in lowered
            or "snapshot" in lowered
            or "version as of" in lowered
            or "timestamp as of" in lowered
        ):
            return (
                "만료되었거나 존재하지 않는 스냅샷 시점입니다. "
                f"as_of={request.as_of or 'N/A'}, snapshot_id={request.snapshot_id or 'N/A'}"
            )
    return f"Athena 실행 실패: {message}"


def render_overview_view() -> None:
    st.title("테이블 운영 현황")

    with st.sidebar:
        st.header("조회")
        if st.button("새로고침", use_container_width=True):
            cached_summaries.clear()
            st.rerun()
        if st.button("Drilldown 화면 열기", use_container_width=True):
            set_query_params(view="drilldown")
            st.rerun()
        st.caption("AWS 인증은 boto3/PyIceberg 기본 자격증명 체인을 사용합니다.")

    summaries = cached_summaries()
    loaded_rows = [row for rows in summaries.values() for row in rows if not row.load_error]
    loaded = len(loaded_rows)
    failed = sum(1 for rows in summaries.values() for row in rows if row.load_error)
    total_data_size = sum(row.total_data_size_bytes or 0 for row in loaded_rows)
    load_times = [row.load_elapsed_ms for row in loaded_rows if row.load_elapsed_ms is not None]
    avg_load_time = sum(load_times) / len(load_times) if load_times else None

    metric_left, metric_size, metric_time, metric_right = st.columns(4)
    metric_left.metric("로드된 테이블", loaded)
    metric_size.metric("전체 데이터 크기", format_bytes(total_data_size) if total_data_size else "N/A")
    metric_time.metric("평균 로드 시간", format_elapsed_ms(avg_load_time))
    metric_right.metric("오류", failed)

    tabs = st.tabs(list(summaries.keys()))
    for tab, (catalog_label, rows) in zip(tabs, summaries.items()):
        with tab:
            render_summary_table(catalog_label, rows)

            selectable = [row for row in rows if not row.load_error]
            if selectable:
                selected = st.selectbox(
                    "상세 테이블",
                    options=[row.identifier for row in selectable],
                    key=f"detail-select:{catalog_label}",
                )
                render_detail(catalog_label, selected)
            else:
                for row in rows:
                    st.error(row.load_error)


if query_params_dict().get("view") == "drilldown":
    render_drilldown_view()
else:
    render_overview_view()
