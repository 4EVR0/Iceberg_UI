from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Iterable, Optional

import pandas as pd

try:
    from ops_ui.settings import catalog_configs as configured_catalogs
except ModuleNotFoundError:
    from settings import catalog_configs as configured_catalogs


@dataclass(frozen=True)
class CatalogConfig:
    label: str
    database: str
    get_catalog: Callable[[], Any]
    configured_tables: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: Optional[int]
    committed_at: Optional[datetime]
    operation: str = "N/A"
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableSummary:
    catalog_label: str
    database: str
    identifier: str
    table_name: str
    table_location: str = "N/A"
    metadata_location: str = "N/A"
    current_snapshot_id: Optional[int] = None
    snapshot_committed_at: Optional[datetime] = None
    latest_batch_date: Optional[datetime] = None
    estimated_run_id: str = "N/A"
    row_count: str = "N/A"
    total_data_size_bytes: Optional[int] = None
    load_elapsed_ms: Optional[float] = None
    load_error: str = ""


@dataclass(frozen=True)
class TableDetail:
    summary: TableSummary
    schema_rows: list[dict[str, Any]]
    partition_spec: str
    sort_order: str
    snapshots: list[SnapshotInfo]


@dataclass(frozen=True)
class TableDataProfile:
    snapshot_id: Optional[int]
    batch_date: Optional[str]
    row_count: int
    date_rows: list[dict[str, Any]]
    batch_rows: list[dict[str, Any]]
    category_rows: list[dict[str, Any]]
    error_rows: list[dict[str, Any]]
    sample_rows: list[dict[str, Any]]


def catalog_configs() -> tuple[CatalogConfig, ...]:
    return tuple(
        CatalogConfig(
            label=label,
            database=database,
            get_catalog=get_catalog,
            configured_tables=configured_tables,
        )
        for label, database, get_catalog, configured_tables in configured_catalogs()
    )


def load_catalog_summaries() -> dict[str, list[TableSummary]]:
    result: dict[str, list[TableSummary]] = {}
    for config in catalog_configs():
        try:
            catalog = config.get_catalog()
            identifiers = list_table_identifiers(catalog, config.database, config.configured_tables)
            result[config.label] = [
                inspect_table_summary(catalog, config.label, config.database, identifier)
                for identifier in identifiers
            ]
        except Exception as exc:
            result[config.label] = [
                TableSummary(
                    catalog_label=config.label,
                    database=config.database,
                    identifier=f"{config.database}.*",
                    table_name="카탈로그 로드 실패",
                    load_error=_friendly_error(exc),
                )
            ]
    return result


def load_table_detail(catalog_label: str, identifier: str) -> TableDetail:
    config = _config_by_label(catalog_label)
    catalog = config.get_catalog()
    summary = inspect_table_summary(catalog, config.label, config.database, identifier)
    if summary.load_error:
        return TableDetail(summary, [], "N/A", "N/A", [])

    table = catalog.load_table(identifier)
    return TableDetail(
        summary=summary,
        schema_rows=_schema_rows(table),
        partition_spec=_stringify(_call_or_value(table, "spec")),
        sort_order=_stringify(_call_or_value(table, "sort_order")),
        snapshots=_snapshot_infos(_call_or_value(table, "snapshots") or []),
    )


def refresh_row_count(catalog_label: str, identifier: str) -> int:
    config = _config_by_label(catalog_label)
    table = config.get_catalog().load_table(identifier)
    return table.scan().to_arrow().num_rows


def load_table_data_profile(
    catalog_label: str,
    identifier: str,
    snapshot_id: Optional[int] = None,
    batch_date: Optional[str] = None,
) -> TableDataProfile:
    config = _config_by_label(catalog_label)
    table = config.get_catalog().load_table(identifier)
    fields = _field_names(table)

    aggregate_fields = tuple(
        field
        for field in (
            "batch_date",
            "batch_job",
            "main_category",
            "sub_category",
            "error_type",
            "product_id",
            "product_brand",
            "product_name",
            "product_name_raw",
            "crawled_at",
        )
        if field in fields
    )
    if not aggregate_fields:
        return TableDataProfile(snapshot_id, batch_date, 0, [], [], [], [], [])

    df = table.scan(selected_fields=aggregate_fields, snapshot_id=snapshot_id).to_arrow().to_pandas()
    if df.empty:
        return TableDataProfile(snapshot_id, batch_date, 0, [], [], [], [], [])

    date_rows = _date_counts(df)
    filtered_df = _filter_batch_date(df, batch_date)
    if filtered_df.empty:
        return TableDataProfile(snapshot_id, batch_date, 0, date_rows, [], [], [], [])

    return TableDataProfile(
        snapshot_id=snapshot_id,
        batch_date=batch_date,
        row_count=len(filtered_df),
        date_rows=date_rows,
        batch_rows=_group_counts(filtered_df, ["batch_job", "batch_date"]),
        category_rows=_group_counts(filtered_df, ["main_category", "sub_category"]),
        error_rows=_group_counts(filtered_df, ["error_type"]),
        sample_rows=_sample_rows(filtered_df),
    )


def inspect_table_summary(catalog: Any, catalog_label: str, database: str, identifier: str) -> TableSummary:
    started_at = perf_counter()
    table_name = identifier.split(".")[-1]
    try:
        table = catalog.load_table(identifier)
        current_snapshot = _call_or_value(table, "current_snapshot")
        latest_batch_date, estimated_run_id = _latest_batch_metadata(table)

        return TableSummary(
            catalog_label=catalog_label,
            database=database,
            identifier=identifier,
            table_name=table_name,
            table_location=_table_location(table),
            metadata_location=_metadata_location(table),
            current_snapshot_id=getattr(current_snapshot, "snapshot_id", None),
            snapshot_committed_at=_snapshot_timestamp(current_snapshot),
            latest_batch_date=latest_batch_date,
            estimated_run_id=estimated_run_id,
            row_count=_snapshot_row_count(current_snapshot),
            total_data_size_bytes=_snapshot_data_size(current_snapshot),
            load_elapsed_ms=_elapsed_ms(started_at),
        )
    except Exception as exc:
        return TableSummary(
            catalog_label=catalog_label,
            database=database,
            identifier=identifier,
            table_name=table_name,
            load_elapsed_ms=_elapsed_ms(started_at),
            load_error=_friendly_error(exc),
        )


def list_table_identifiers(catalog: Any, database: str, configured_tables: Iterable[str]) -> list[str]:
    identifiers = list(configured_tables)
    try:
        identifiers.extend(_identifier_to_str(item) for item in catalog.list_tables(database))
    except Exception:
        pass
    return sorted(set(identifiers))


def _config_by_label(label: str) -> CatalogConfig:
    for config in catalog_configs():
        if config.label == label:
            return config
    raise ValueError(f"알 수 없는 카탈로그입니다: {label}")


def _identifier_to_str(identifier: Any) -> str:
    if isinstance(identifier, str):
        return identifier
    if isinstance(identifier, tuple):
        return ".".join(str(part) for part in identifier)
    return str(identifier)


def _call_or_value(obj: Any, name: str) -> Any:
    attr = getattr(obj, name, None)
    if callable(attr):
        return attr()
    return attr


def _table_location(table: Any) -> str:
    location = _call_or_value(table, "location")
    if location:
        return str(location)
    metadata = getattr(table, "metadata", None)
    return str(getattr(metadata, "location", "N/A"))


def _metadata_location(table: Any) -> str:
    location = _call_or_value(table, "metadata_location")
    if location:
        return str(location)
    metadata = getattr(table, "metadata", None)
    return str(getattr(metadata, "metadata_location", "N/A"))


def _snapshot_timestamp(snapshot: Any) -> Optional[datetime]:
    if snapshot is None:
        return None
    timestamp_ms = getattr(snapshot, "timestamp_ms", None)
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _snapshot_row_count(snapshot: Any) -> str:
    if snapshot is None:
        return "N/A"
    summary = getattr(snapshot, "summary", None) or {}
    for key in ("total-records", "total_records"):
        value = summary.get(key)
        if value is not None:
            return str(value)
    return "N/A"


def _snapshot_data_size(snapshot: Any) -> Optional[int]:
    if snapshot is None:
        return None
    summary = getattr(snapshot, "summary", None) or {}
    for key in ("total-files-size", "total_file_size", "total-files-size-bytes"):
        value = summary.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 1)


def _latest_batch_metadata(table: Any) -> tuple[Optional[datetime], str]:
    schema = _call_or_value(table, "schema")
    field_names = {field.name for field in getattr(schema, "fields", [])}
    if not {"batch_job", "batch_date"}.issubset(field_names):
        return None, "N/A"

    try:
        arrow_table = table.scan(selected_fields=("batch_job", "batch_date")).to_arrow()
        df = arrow_table.to_pandas()
    except Exception:
        return None, "N/A"

    if df.empty or "batch_date" not in df.columns:
        return None, "N/A"

    batch_dates = pd.to_datetime(df["batch_date"], utc=True, errors="coerce")
    valid_dates = batch_dates.dropna()
    if valid_dates.empty:
        return None, "N/A"

    idx = valid_dates.idxmax()
    batch_date = valid_dates.loc[idx].to_pydatetime()
    batch_job = df.loc[idx, "batch_job"] if "batch_job" in df.columns else None
    if pd.isna(batch_job) or not str(batch_job).strip():
        return batch_date, "N/A"

    return batch_date, f"{batch_job}_{batch_date.strftime('%Y%m%d_%H%M%S')}"


def _field_names(table: Any) -> set[str]:
    schema = _call_or_value(table, "schema")
    return {field.name for field in getattr(schema, "fields", [])}


def _date_counts(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "batch_date" not in df.columns:
        return []

    dates = pd.to_datetime(df["batch_date"], utc=True, errors="coerce")
    frame = pd.DataFrame({"batch_date": dates.dt.date.astype("string")})
    frame = frame[frame["batch_date"].notna()]
    if frame.empty:
        return []

    grouped = frame.value_counts("batch_date").reset_index(name="row_count")
    grouped = grouped.sort_values("batch_date", ascending=False)
    return grouped.to_dict("records")


def _filter_batch_date(df: pd.DataFrame, batch_date: Optional[str]) -> pd.DataFrame:
    if not batch_date or batch_date == "전체" or "batch_date" not in df.columns:
        return df

    dates = pd.to_datetime(df["batch_date"], utc=True, errors="coerce").dt.date.astype("string")
    return df.loc[dates == batch_date].copy()


def _group_counts(df: pd.DataFrame, columns: list[str], limit: int = 200) -> list[dict[str, Any]]:
    available = [column for column in columns if column in df.columns]
    if not available:
        return []

    frame = df[available].copy()
    for column in available:
        if column == "batch_date":
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        frame[column] = frame[column].fillna("N/A").astype(str)

    grouped = frame.groupby(available, dropna=False).size().reset_index(name="row_count")
    grouped = grouped.sort_values("row_count", ascending=False).head(limit)
    return grouped.to_dict("records")


def _sample_rows(df: pd.DataFrame, limit: int = 100) -> list[dict[str, Any]]:
    preferred = [
        "batch_date",
        "batch_job",
        "main_category",
        "sub_category",
        "error_type",
        "product_id",
        "product_brand",
        "product_name",
        "product_name_raw",
        "crawled_at",
    ]
    columns = [column for column in preferred if column in df.columns]
    if not columns:
        return []

    sample = df[columns].head(limit).copy()
    for column in sample.columns:
        if pd.api.types.is_datetime64_any_dtype(sample[column]):
            sample[column] = sample[column].dt.strftime("%Y-%m-%d %H:%M:%S")
        sample[column] = sample[column].where(sample[column].notna(), "N/A")
    return sample.to_dict("records")


def _schema_rows(table: Any) -> list[dict[str, Any]]:
    schema = _call_or_value(table, "schema")
    rows: list[dict[str, Any]] = []
    for field in getattr(schema, "fields", []):
        rows.append(
            {
                "id": getattr(field, "field_id", ""),
                "name": getattr(field, "name", ""),
                "type": str(getattr(field, "field_type", "")),
                "required": bool(getattr(field, "required", False)),
                "doc": getattr(field, "doc", "") or "",
            }
        )
    return rows


def _snapshot_infos(snapshots: Iterable[Any]) -> list[SnapshotInfo]:
    infos = []
    for snapshot in snapshots:
        summary = _snapshot_summary(getattr(snapshot, "summary", None))
        infos.append(
            SnapshotInfo(
                snapshot_id=getattr(snapshot, "snapshot_id", None),
                committed_at=_snapshot_timestamp(snapshot),
                operation=str(summary.get("operation", "N/A")),
                summary=summary,
            )
        )
    return sorted(infos, key=lambda item: item.committed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _snapshot_summary(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {}
    if isinstance(summary, dict):
        return dict(summary)

    result: dict[str, Any] = {}
    operation = getattr(summary, "operation", None)
    if operation is not None:
        result["operation"] = operation

    additional = getattr(summary, "_additional_properties", None)
    if isinstance(additional, dict):
        result.update({str(key): value for key, value in additional.items()})

    return result


def _stringify(value: Any) -> str:
    if value is None:
        return "N/A"
    return str(value)


def _friendly_error(exc: Exception) -> str:
    message = str(exc) or type(exc).__name__
    lowered = message.lower()
    if "credential" in lowered or "accessdenied" in lowered or "access denied" in lowered:
        return f"AWS 인증 또는 권한을 확인하세요: {message}"
    if "not found" in lowered or "nosuch" in lowered:
        return f"테이블 또는 S3 경로를 찾을 수 없습니다: {message}"
    return message
