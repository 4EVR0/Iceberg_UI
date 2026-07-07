from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import boto3

try:
    from ops_ui.env_loader import load_dotenv
except ModuleNotFoundError:
    from env_loader import load_dotenv


load_dotenv()


DEFAULT_REGION = "ap-northeast-2"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 45.0


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


@dataclass(frozen=True)
class AthenaSettings:
    region: str
    database: str
    workgroup: str
    output_location: str


@dataclass(frozen=True)
class AthenaQueryResult:
    query: str
    query_execution_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: int


def athena_settings() -> AthenaSettings:
    region = _env("OPS_UI_AWS_REGION", _env("AWS_REGION", _env("AWS_DEFAULT_REGION", DEFAULT_REGION)))
    return AthenaSettings(
        region=region,
        database=_env("OPS_UI_ATHENA_DATABASE", "oliveyoung_db"),
        workgroup=_env("OPS_UI_ATHENA_WORKGROUP", "primary"),
        output_location=_env("OPS_UI_ATHENA_OUTPUT"),
    )


def validate_athena_settings() -> list[str]:
    settings = athena_settings()
    missing: list[str] = []
    if not settings.database:
        missing.append("OPS_UI_ATHENA_DATABASE")
    if not settings.workgroup:
        missing.append("OPS_UI_ATHENA_WORKGROUP")
    if not settings.output_location:
        missing.append("OPS_UI_ATHENA_OUTPUT")
    return missing


def execute_athena_query(
    query: str,
    *,
    max_rows: int = 200,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> AthenaQueryResult:
    settings = athena_settings()
    client = boto3.client("athena", region_name=settings.region)

    result_config: dict[str, Any] = {"OutputLocation": settings.output_location}
    response = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": settings.database},
        WorkGroup=settings.workgroup,
        ResultConfiguration=result_config,
    )
    execution_id = response["QueryExecutionId"]
    execution = _wait_for_query(client, execution_id, poll_interval_seconds, timeout_seconds)
    result_set = client.get_query_results(QueryExecutionId=execution_id, MaxResults=max_rows + 1)

    columns = [column.get("Name", "") for column in result_set["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    rows = _rows_from_result_set(result_set.get("ResultSet", {}).get("Rows", []), columns)
    execution_time_ms = execution.get("Statistics", {}).get("EngineExecutionTimeInMillis", 0)
    return AthenaQueryResult(
        query=query,
        query_execution_id=execution_id,
        columns=columns,
        rows=rows[:max_rows],
        row_count=len(rows),
        execution_time_ms=execution_time_ms,
    )


def _wait_for_query(
    client: Any,
    execution_id: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    started_at = time.monotonic()
    while True:
        response = client.get_query_execution(QueryExecutionId=execution_id)
        execution = response["QueryExecution"]
        status = execution.get("Status", {})
        state = status.get("State")
        if state == "SUCCEEDED":
            return execution
        if state in {"FAILED", "CANCELLED"}:
            reason = status.get("StateChangeReason", "Athena query failed")
            raise RuntimeError(reason)
        if time.monotonic() - started_at > timeout_seconds:
            raise TimeoutError(f"Athena query timed out after {timeout_seconds:.0f}s: {execution_id}")
        time.sleep(poll_interval_seconds)


def _rows_from_result_set(result_rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    if not result_rows:
        return []

    data_rows = result_rows[1:] if columns else result_rows
    parsed_rows: list[dict[str, Any]] = []
    for row in data_rows:
        values = row.get("Data", [])
        parsed_rows.append(
            {
                column: values[idx].get("VarCharValue", "") if idx < len(values) else ""
                for idx, column in enumerate(columns)
            }
        )
    return parsed_rows
