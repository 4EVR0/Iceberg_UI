from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

try:
    from ops_ui.iceberg_inspector import TableDetail, load_table_detail
    from ops_ui.settings import catalog_configs as configured_catalogs
except ModuleNotFoundError:
    from iceberg_inspector import TableDetail, load_table_detail
    from settings import catalog_configs as configured_catalogs

@dataclass(frozen=True)
class DrilldownRequest:
    catalog: str
    table: str
    metric: str
    snapshot_mode: str = "latest"
    as_of: str = ""
    snapshot_id: str = ""
    batch_date: str = ""
    previous_as_of: str = ""
    previous_snapshot_id: str = ""
    previous_batch_date: str = ""
    batch_job: str = ""
    error_type: str = ""
    main_category: str = ""
    sub_category: str = ""
    from_ts: str = ""
    to_ts: str = ""

    @property
    def identifier(self) -> str:
        return f"{self.catalog}.{self.table}"

    @property
    def compare_enabled(self) -> bool:
        return any((self.previous_as_of, self.previous_snapshot_id, self.previous_batch_date))

    @property
    def auto_compare_candidate(self) -> bool:
        return bool(self.batch_date) and not self.compare_enabled


@dataclass(frozen=True)
class ResolvedDrilldown:
    request: DrilldownRequest
    spec: "DrilldownSpec"
    detail: TableDetail
    resolved_batch_date: str
    resolved_snapshot_id: str
    resolved_as_of: str
    resolved_previous_batch_date: str
    resolved_previous_snapshot_id: str
    resolved_previous_as_of: str
    generated_sql: str
    context_rows: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DrilldownSpec:
    metric: str
    label: str
    description: str
    default_catalog: str
    default_table: str
    result_columns: tuple[str, ...]
    order_by: tuple[str, ...]
    required_filters: tuple[str, ...] = ()
    implied_filters: tuple[tuple[str, str], ...] = ()


DRILLDOWN_SPECS: dict[str, DrilldownSpec] = {
    "category_failure": DrilldownSpec(
        metric="category_failure",
        label="카테고리 실패 Drilldown",
        description="카테고리별 실패 건을 최신 Iceberg 상태 기준으로 조회합니다.",
        default_catalog="oliveyoung_db",
        default_table="oliveyoung_silver_error",
        result_columns=(
            "batch_date",
            "batch_job",
            "main_category",
            "sub_category",
            "error_type",
            "product_id",
            "product_brand",
            "product_name",
            "crawled_at",
        ),
        order_by=("batch_date DESC", "crawled_at DESC"),
    ),
    "error_type_spike": DrilldownSpec(
        metric="error_type_spike",
        label="오류유형 급증 Drilldown",
        description="특정 error_type의 최근 실패 샘플을 조회합니다.",
        default_catalog="oliveyoung_db",
        default_table="oliveyoung_silver_error",
        result_columns=(
            "batch_date",
            "batch_job",
            "error_type",
            "main_category",
            "sub_category",
            "product_id",
            "product_name",
            "crawled_at",
        ),
        order_by=("batch_date DESC", "crawled_at DESC"),
        required_filters=("error_type",),
    ),
    "batch_failure": DrilldownSpec(
        metric="batch_failure",
        label="배치 실패 Drilldown",
        description="특정 배치 실행의 실패 row를 조회합니다.",
        default_catalog="oliveyoung_db",
        default_table="oliveyoung_silver_error",
        result_columns=(
            "batch_date",
            "batch_job",
            "error_type",
            "main_category",
            "sub_category",
            "product_id",
            "product_name",
            "crawled_at",
        ),
        order_by=("batch_date DESC", "crawled_at DESC"),
        required_filters=("batch_job",),
    ),
    "category_success_count": DrilldownSpec(
        metric="category_success_count",
        label="카테고리 성공 Drilldown",
        description="성공 적재 테이블에서 카테고리 기준 샘플을 조회합니다.",
        default_catalog="oliveyoung_db",
        default_table="oliveyoung_silver_current",
        result_columns=(
            "batch_date",
            "batch_job",
            "main_category",
            "sub_category",
            "product_id",
            "product_brand",
            "product_name",
            "crawled_at",
        ),
        order_by=("batch_date DESC", "crawled_at DESC"),
    ),
    "ingestion_volume": DrilldownSpec(
        metric="ingestion_volume",
        label="수집량 Drilldown",
        description="최근 적재량 증가 지표에서 실제 row 샘플을 조회합니다.",
        default_catalog="oliveyoung_db",
        default_table="oliveyoung_silver_current",
        result_columns=(
            "batch_date",
            "batch_job",
            "main_category",
            "sub_category",
            "product_id",
            "product_brand",
            "product_name",
            "crawled_at",
        ),
        order_by=("batch_date DESC", "crawled_at DESC"),
    ),
}


def parse_drilldown_request(params: Mapping[str, Any]) -> Optional[DrilldownRequest]:
    metric = _param(params, "metric")
    if not metric:
        return None

    if metric not in DRILLDOWN_SPECS:
        raise ValueError(f"지원하지 않는 metric 입니다: {metric}")

    spec = DRILLDOWN_SPECS[metric]
    catalog = _param(params, "catalog") or spec.default_catalog
    table = _param(params, "table") or spec.default_table
    if "." in table:
        catalog, table = table.split(".", 1)
    request = DrilldownRequest(
        catalog=catalog,
        table=table,
        metric=metric,
        snapshot_mode=_param(params, "snapshot_mode") or "latest",
        as_of=_param(params, "as_of"),
        snapshot_id=_param(params, "snapshot_id"),
        batch_date=_param(params, "batch_date"),
        previous_as_of=_param(params, "previous_as_of"),
        previous_snapshot_id=_param(params, "previous_snapshot_id"),
        previous_batch_date=_param(params, "previous_batch_date"),
        batch_job=_param(params, "batch_job"),
        error_type=_param(params, "error_type"),
        main_category=_param(params, "main_category"),
        sub_category=_param(params, "sub_category"),
        from_ts=_param(params, "from"),
        to_ts=_param(params, "to"),
    )
    validate_request(request)
    return request


def validate_request(request: DrilldownRequest) -> None:
    allowed = allowed_identifiers()
    if request.identifier not in allowed:
        raise ValueError(f"허용되지 않은 테이블입니다: {request.identifier}")

    if request.snapshot_mode not in {"latest", "asof"}:
        raise ValueError(f"지원하지 않는 snapshot_mode 입니다: {request.snapshot_mode}")

    if request.snapshot_mode == "asof":
        if not request.as_of and not request.snapshot_id:
            raise ValueError("snapshot_mode=asof 는 as_of 또는 snapshot_id 파라미터가 필요합니다.")
        if request.as_of:
            _parse_as_of_value(request.as_of)
        if request.snapshot_id:
            _parse_snapshot_id(request.snapshot_id)

    if request.compare_enabled:
        if not request.previous_batch_date:
            raise ValueError("비교 조회에는 previous_batch_date 파라미터가 필요합니다.")
        if not request.previous_as_of and not request.previous_snapshot_id:
            raise ValueError("비교 조회에는 previous_as_of 또는 previous_snapshot_id 파라미터가 필요합니다.")
        if request.previous_as_of:
            _parse_as_of_value(request.previous_as_of)
        if request.previous_snapshot_id:
            _parse_snapshot_id(request.previous_snapshot_id)

    spec = DRILLDOWN_SPECS[request.metric]
    for field_name in spec.required_filters:
        if not getattr(request, field_name):
            raise ValueError(f"{request.metric} metric 은 {field_name} 파라미터가 필요합니다.")


def resolve_drilldown_request(request: DrilldownRequest) -> ResolvedDrilldown:
    spec = DRILLDOWN_SPECS[request.metric]
    detail = load_table_detail(request.catalog, request.identifier)
    if detail.summary.load_error:
        raise RuntimeError(detail.summary.load_error)

    resolved_batch_date = request.batch_date
    if not resolved_batch_date and detail.summary.latest_batch_date is not None:
        resolved_batch_date = detail.summary.latest_batch_date.strftime("%Y-%m-%d")

    effective_request = _apply_auto_compare_defaults(request, detail, resolved_batch_date)
    resolved_snapshot_id = _resolve_snapshot_id(detail, effective_request)
    resolved_as_of = _resolve_as_of_display(request)
    resolved_previous_batch_date = effective_request.previous_batch_date or "N/A"
    resolved_previous_snapshot_id = _resolve_snapshot_id(
        detail,
        DrilldownRequest(
            catalog=effective_request.catalog,
            table=effective_request.table,
            metric=effective_request.metric,
            snapshot_mode="asof",
            as_of=effective_request.previous_as_of,
            snapshot_id=effective_request.previous_snapshot_id,
        ),
    ) if effective_request.compare_enabled else "N/A"
    resolved_previous_as_of = _resolve_as_of_display(
        DrilldownRequest(
            catalog=effective_request.catalog,
            table=effective_request.table,
            metric=effective_request.metric,
            snapshot_mode="asof",
            as_of=effective_request.previous_as_of,
            snapshot_id=effective_request.previous_snapshot_id,
        )
    ) if effective_request.compare_enabled else "N/A"

    generated_sql = build_drilldown_sql(
        effective_request,
        spec,
        resolved_batch_date,
        str(detail.summary.current_snapshot_id or "N/A"),
    )
    context_rows = [
        {"key": "metric", "value": spec.metric},
        {"key": "label", "value": spec.label},
        {"key": "table", "value": request.identifier},
        {"key": "compare_mode", "value": "enabled" if effective_request.compare_enabled else "disabled"},
        {"key": "snapshot_mode", "value": effective_request.snapshot_mode},
        {"key": "auto_compare_mode", "value": "date_delete_vs_latest" if _is_auto_compare_active(request, effective_request) else "disabled"},
        {"key": "requested_as_of", "value": resolved_as_of},
        {"key": "requested_snapshot_id", "value": request.snapshot_id or "N/A"},
        {"key": "resolved_snapshot_id", "value": resolved_snapshot_id},
        {"key": "previous_requested_as_of", "value": resolved_previous_as_of},
        {"key": "previous_requested_snapshot_id", "value": effective_request.previous_snapshot_id or "N/A"},
        {"key": "previous_resolved_snapshot_id", "value": resolved_previous_snapshot_id},
        {"key": "current_snapshot_id", "value": str(detail.summary.current_snapshot_id or "N/A")},
        {"key": "snapshot_commit", "value": _display_datetime(detail.summary.snapshot_committed_at)},
        {"key": "resolved_batch_date", "value": resolved_batch_date or "N/A"},
        {"key": "previous_batch_date", "value": resolved_previous_batch_date},
    ]
    return ResolvedDrilldown(
        request=effective_request,
        spec=spec,
        detail=detail,
        resolved_batch_date=resolved_batch_date,
        resolved_snapshot_id=resolved_snapshot_id,
        resolved_as_of=resolved_as_of,
        resolved_previous_batch_date=resolved_previous_batch_date,
        resolved_previous_snapshot_id=resolved_previous_snapshot_id,
        resolved_previous_as_of=resolved_previous_as_of,
        generated_sql=generated_sql,
        context_rows=context_rows,
    )


def build_drilldown_sql(
    request: DrilldownRequest,
    spec: DrilldownSpec,
    resolved_batch_date: str,
    current_snapshot_id: str,
) -> str:
    if request.compare_enabled:
        return build_compare_sql(request, spec, resolved_batch_date, current_snapshot_id)

    filters: list[str] = ["1 = 1"]
    for column, value in spec.implied_filters:
        filters.append(f"{column} = {_quote(value)}")

    filter_values = {
        "batch_job": request.batch_job,
        "error_type": request.error_type,
        "main_category": request.main_category,
        "sub_category": request.sub_category,
    }
    if resolved_batch_date:
        filters.append(f"CAST(batch_date AS date) = DATE {_quote(resolved_batch_date)}")
    for column, value in filter_values.items():
        if value:
            filters.append(f"{column} = {_quote(value)}")

    if request.from_ts:
        filters.append(f"crawled_at >= from_iso8601_timestamp({_quote(request.from_ts)})")
    if request.to_ts:
        filters.append(f"crawled_at <= from_iso8601_timestamp({_quote(request.to_ts)})")

    select_clause = ",\n    ".join(spec.result_columns)
    where_clause = "\n  AND ".join(filters)
    order_clause = ", ".join(spec.order_by)
    from_clause = f"FROM {request.identifier}{_time_travel_clause(request)}"
    return (
        f"-- metric: {spec.metric}\n"
        f"-- snapshot_mode: {request.snapshot_mode}\n"
        f"-- requested_as_of: {request.as_of or 'N/A'}\n"
        f"-- requested_snapshot_id: {request.snapshot_id or 'N/A'}\n"
        f"-- current_snapshot_id: {current_snapshot_id}\n"
        f"SELECT\n"
        f"    {select_clause}\n"
        f"{from_clause}\n"
        f"WHERE {where_clause}\n"
        f"ORDER BY {order_clause}"
    )


def build_compare_sql(
    request: DrilldownRequest,
    spec: DrilldownSpec,
    resolved_batch_date: str,
    current_snapshot_id: str,
) -> str:
    current_filters = _filters_for_request(request, spec, resolved_batch_date)
    previous_filters = _filters_for_request(request, spec, request.previous_batch_date)
    key_columns = _comparison_key_columns(spec.result_columns)
    compare_columns = tuple(
        column for column in spec.result_columns if column not in key_columns and column != "batch_date"
    )
    select_columns = ",\n        ".join(spec.result_columns)
    current_cte = _compare_cte_sql(
        "current_rows",
        request.identifier,
        _time_travel_clause(request),
        select_columns,
        current_filters,
    )
    previous_time_clause = _time_travel_clause(
        DrilldownRequest(
            catalog=request.catalog,
            table=request.table,
            metric=request.metric,
            snapshot_mode="asof",
            as_of=request.previous_as_of,
            snapshot_id=request.previous_snapshot_id,
        )
    )
    previous_cte = _compare_cte_sql(
        "previous_rows",
        request.identifier,
        previous_time_clause,
        select_columns,
        previous_filters,
    )
    key_selects = [
        f"COALESCE(current_rows.{column}, previous_rows.{column}) AS {column}" for column in key_columns
    ]
    value_selects = [
        "current_rows.batch_date AS current_batch_date",
        "previous_rows.batch_date AS previous_batch_date",
    ]
    value_selects.extend(
        f"current_rows.{column} AS current_{column}" for column in compare_columns
    )
    value_selects.extend(
        f"previous_rows.{column} AS previous_{column}" for column in compare_columns
    )
    join_clause = " AND ".join(
        f"current_rows.{column} = previous_rows.{column}" for column in key_columns
    )
    diff_predicates = [
        f"COALESCE(CAST(current_rows.{column} AS varchar), '') <> COALESCE(CAST(previous_rows.{column} AS varchar), '')"
        for column in compare_columns
    ]
    current_presence = _presence_expression("current_rows", key_columns)
    previous_presence = _presence_expression("previous_rows", key_columns)
    select_items = [
        "\n".join(
            [
                "CASE",
                f"        WHEN {previous_presence} THEN 'added'",
                f"        WHEN {current_presence} THEN 'removed'",
                "        ELSE 'changed'",
                "    END AS diff_type",
            ]
        ),
        *key_selects,
        *value_selects,
    ]
    diff_select = ",\n    ".join(select_items)
    diff_where_parts = [
        previous_presence,
        current_presence,
    ]
    if diff_predicates:
        diff_where_parts.append("(" + " OR ".join(diff_predicates) + ")")
    diff_where = "\n    OR ".join(diff_where_parts)
    order_columns = ", ".join(key_columns)
    return (
        f"-- metric: {spec.metric}\n"
        f"-- compare_mode: enabled\n"
        f"-- snapshot_mode: {request.snapshot_mode}\n"
        f"-- requested_as_of: {request.as_of or 'N/A'}\n"
        f"-- requested_snapshot_id: {request.snapshot_id or 'N/A'}\n"
        f"-- previous_requested_as_of: {request.previous_as_of or 'N/A'}\n"
        f"-- previous_requested_snapshot_id: {request.previous_snapshot_id or 'N/A'}\n"
        f"-- current_snapshot_id: {current_snapshot_id}\n"
        f"WITH\n{current_cte},\n{previous_cte}\n"
        f"SELECT\n    {diff_select}\n"
        f"FROM current_rows\n"
        f"FULL OUTER JOIN previous_rows\n"
        f"  ON {join_clause}\n"
        f"WHERE {diff_where}\n"
        f"ORDER BY diff_type, {order_columns}"
    )


def allowed_identifiers() -> set[str]:
    allowed: set[str] = set()
    for label, _database, _get_catalog, configured_tables in configured_catalogs():
        for identifier in configured_tables:
            if identifier.startswith(f"{label}."):
                allowed.add(identifier)
    return allowed


def metric_options() -> list[tuple[str, str]]:
    return [(key, spec.label) for key, spec in DRILLDOWN_SPECS.items()]


def _param(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).strip()


def _int_param(params: Mapping[str, Any], key: str, default: int) -> int:
    raw = _param(params, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _quote(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _display_datetime(value: Any) -> str:
    if value is None:
        return "N/A"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _time_travel_clause(request: DrilldownRequest) -> str:
    if request.snapshot_mode != "asof":
        return ""
    if request.snapshot_id:
        return f" FOR VERSION AS OF {_parse_snapshot_id(request.snapshot_id)}"
    return f" FOR TIMESTAMP AS OF TIMESTAMP {_quote(_as_athena_timestamp_literal(request.as_of))}"


def _as_athena_timestamp_literal(value: str) -> str:
    parsed = _parse_as_of_value(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def _parse_as_of_value(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("as_of 값이 비어 있습니다.")

    if raw.isdigit():
        epoch = int(raw)
        if len(raw) >= 13:
            return datetime.fromtimestamp(epoch / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 as_of 형식입니다: {value}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_snapshot_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"snapshot_id 는 정수여야 합니다: {value}") from exc


def _resolve_snapshot_id(detail: TableDetail, request: DrilldownRequest) -> str:
    if request.snapshot_mode != "asof":
        return str(detail.summary.current_snapshot_id or "N/A")
    if request.snapshot_id:
        return request.snapshot_id
    if not request.as_of:
        return "N/A"

    target = _parse_as_of_value(request.as_of)
    candidates = [
        snapshot
        for snapshot in detail.snapshots
        if snapshot.snapshot_id is not None and snapshot.committed_at is not None
    ]
    candidates.sort(key=lambda snapshot: snapshot.committed_at)
    resolved = None
    for snapshot in candidates:
        committed_at = snapshot.committed_at.astimezone(timezone.utc)
        if committed_at <= target:
            resolved = snapshot
        else:
            break
    return str(resolved.snapshot_id) if resolved and resolved.snapshot_id is not None else "N/A"


def _resolve_as_of_display(request: DrilldownRequest) -> str:
    if not request.as_of:
        return "N/A"
    return _parse_as_of_value(request.as_of).strftime("%Y-%m-%d %H:%M:%S UTC")


def _apply_auto_compare_defaults(
    request: DrilldownRequest,
    detail: TableDetail,
    resolved_batch_date: str,
) -> DrilldownRequest:
    if not request.auto_compare_candidate or not resolved_batch_date:
        return request

    previous_snapshot_id = _find_delete_snapshot_id_for_date(detail, resolved_batch_date)
    if not previous_snapshot_id:
        return request

    return DrilldownRequest(
        catalog=request.catalog,
        table=request.table,
        metric=request.metric,
        snapshot_mode=request.snapshot_mode,
        as_of=request.as_of,
        snapshot_id=request.snapshot_id,
        batch_date=request.batch_date,
        previous_snapshot_id=previous_snapshot_id,
        previous_batch_date=resolved_batch_date,
        batch_job=request.batch_job,
        error_type=request.error_type,
        main_category=request.main_category,
        sub_category=request.sub_category,
        from_ts=request.from_ts,
        to_ts=request.to_ts,
    )


def _is_auto_compare_active(request: DrilldownRequest, effective_request: DrilldownRequest) -> bool:
    return (
        request.auto_compare_candidate
        and effective_request.compare_enabled
        and not request.previous_snapshot_id
        and effective_request.previous_snapshot_id != ""
    )


def _find_delete_snapshot_id_for_date(detail: TableDetail, batch_date: str) -> str:
    try:
        target_date = datetime.strptime(batch_date, "%Y-%m-%d").date()
    except ValueError:
        return ""

    candidates = [
        snapshot
        for snapshot in detail.snapshots
        if snapshot.snapshot_id is not None
        and snapshot.committed_at is not None
        and snapshot.operation.upper() == "DELETE"
        and snapshot.committed_at.astimezone(timezone.utc).date() == target_date
    ]
    if not candidates:
        return ""

    selected = max(candidates, key=lambda snapshot: snapshot.committed_at)
    return str(selected.snapshot_id)


def _filters_for_request(request: DrilldownRequest, spec: DrilldownSpec, batch_date: str) -> list[str]:
    filters: list[str] = ["1 = 1"]
    for column, value in spec.implied_filters:
        filters.append(f"{column} = {_quote(value)}")
    if batch_date:
        filters.append(f"CAST(batch_date AS date) = DATE {_quote(batch_date)}")

    filter_values = {
        "batch_job": request.batch_job,
        "error_type": request.error_type,
        "main_category": request.main_category,
        "sub_category": request.sub_category,
    }
    for column, value in filter_values.items():
        if value:
            filters.append(f"{column} = {_quote(value)}")

    if request.from_ts:
        filters.append(f"crawled_at >= from_iso8601_timestamp({_quote(request.from_ts)})")
    if request.to_ts:
        filters.append(f"crawled_at <= from_iso8601_timestamp({_quote(request.to_ts)})")
    return filters


def _comparison_key_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    preferred = ("product_id", "batch_job", "error_type", "main_category", "sub_category")
    selected = tuple(column for column in preferred if column in columns)
    if selected:
        return selected
    fallback = tuple(column for column in columns if column not in {"batch_date", "crawled_at"})
    if fallback:
        return fallback
    return ("batch_date",)


def _compare_cte_sql(
    cte_name: str,
    identifier: str,
    time_travel_clause: str,
    select_columns: str,
    filters: list[str],
) -> str:
    where_clause = "\n      AND ".join(filters)
    return (
        f"  {cte_name} AS (\n"
        f"    SELECT\n"
        f"        {select_columns}\n"
        f"    FROM {identifier}{time_travel_clause}\n"
        f"    WHERE {where_clause}\n"
        f"  )"
    )


def _presence_expression(alias: str, key_columns: tuple[str, ...]) -> str:
    return " AND ".join(f"{alias}.{column} IS NULL" for column in key_columns)
