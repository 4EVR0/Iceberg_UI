from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

try:
    from ops_ui.iceberg_inspector import TableDetail, load_table_detail
    from ops_ui.settings import catalog_configs as configured_catalogs
except ModuleNotFoundError:
    from iceberg_inspector import TableDetail, load_table_detail
    from settings import catalog_configs as configured_catalogs


DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000


@dataclass(frozen=True)
class DrilldownRequest:
    catalog: str
    table: str
    metric: str
    snapshot_mode: str = "latest"
    batch_date: str = ""
    batch_job: str = ""
    error_type: str = ""
    main_category: str = ""
    sub_category: str = ""
    from_ts: str = ""
    to_ts: str = ""
    limit: int = DEFAULT_ROW_LIMIT

    @property
    def identifier(self) -> str:
        return f"{self.catalog}.{self.table}"


@dataclass(frozen=True)
class ResolvedDrilldown:
    request: DrilldownRequest
    spec: "DrilldownSpec"
    detail: TableDetail
    resolved_batch_date: str
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
        batch_date=_param(params, "batch_date"),
        batch_job=_param(params, "batch_job"),
        error_type=_param(params, "error_type"),
        main_category=_param(params, "main_category"),
        sub_category=_param(params, "sub_category"),
        from_ts=_param(params, "from"),
        to_ts=_param(params, "to"),
        limit=_int_param(params, "limit", DEFAULT_ROW_LIMIT),
    )
    validate_request(request)
    return request


def validate_request(request: DrilldownRequest) -> None:
    allowed = allowed_identifiers()
    if request.identifier not in allowed:
        raise ValueError(f"허용되지 않은 테이블입니다: {request.identifier}")

    if request.snapshot_mode != "latest":
        raise ValueError(f"지원하지 않는 snapshot_mode 입니다: {request.snapshot_mode}")

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

    generated_sql = build_drilldown_sql(
        request,
        spec,
        resolved_batch_date,
        str(detail.summary.current_snapshot_id or "N/A"),
    )
    context_rows = [
        {"key": "metric", "value": spec.metric},
        {"key": "label", "value": spec.label},
        {"key": "table", "value": request.identifier},
        {"key": "snapshot_mode", "value": request.snapshot_mode},
        {"key": "current_snapshot_id", "value": str(detail.summary.current_snapshot_id or "N/A")},
        {"key": "snapshot_commit", "value": _display_datetime(detail.summary.snapshot_committed_at)},
        {"key": "resolved_batch_date", "value": resolved_batch_date or "N/A"},
    ]
    return ResolvedDrilldown(
        request=request,
        spec=spec,
        detail=detail,
        resolved_batch_date=resolved_batch_date,
        generated_sql=generated_sql,
        context_rows=context_rows,
    )


def build_drilldown_sql(
    request: DrilldownRequest,
    spec: DrilldownSpec,
    resolved_batch_date: str,
    current_snapshot_id: str,
) -> str:
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

    limit = min(max(request.limit, 1), MAX_ROW_LIMIT)
    select_clause = ",\n    ".join(spec.result_columns)
    where_clause = "\n  AND ".join(filters)
    order_clause = ", ".join(spec.order_by)
    return (
        f"-- metric: {spec.metric}\n"
        f"-- snapshot_mode: {request.snapshot_mode}\n"
        f"-- current_snapshot_id: {current_snapshot_id}\n"
        f"SELECT\n"
        f"    {select_clause}\n"
        f"FROM {request.identifier}\n"
        f"WHERE {where_clause}\n"
        f"ORDER BY {order_clause}\n"
        f"LIMIT {limit}"
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
