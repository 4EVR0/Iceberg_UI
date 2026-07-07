from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from pyiceberg.catalog import load_catalog

try:
    from ops_ui.env_loader import load_dotenv
except ModuleNotFoundError:
    from env_loader import load_dotenv


load_dotenv()


DEFAULT_BUCKET = "oliveyoung-crawl-data"
DEFAULT_REGION = "ap-northeast-2"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _s3_uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}/{prefix.strip('/')}/"


@dataclass(frozen=True)
class CatalogSettings:
    label: str
    database: str
    warehouse: str
    table_names: tuple[str, ...]

    @property
    def configured_tables(self) -> tuple[str, ...]:
        return tuple(f"{self.database}.{name}" for name in self.table_names)

    def get_catalog(self) -> Any:
        return load_catalog(
            _env("OPS_UI_ICEBERG_CATALOG_NAME", "glue"),
            **{
                "type": _env("OPS_UI_ICEBERG_CATALOG_TYPE", "glue"),
                "warehouse": self.warehouse,
                "s3.region": AWS_REGION,
                "glue.region": AWS_REGION,
            },
        )


AWS_REGION = _env("OPS_UI_AWS_REGION", _env("AWS_REGION", _env("AWS_DEFAULT_REGION", DEFAULT_REGION)))
S3_BUCKET = _env("OPS_UI_S3_BUCKET", _env("OLIVEYOUNG_S3_BUCKET", DEFAULT_BUCKET))

OLIVEYOUNG_WAREHOUSE = _env(
    "OPS_UI_OLIVEYOUNG_WAREHOUSE",
    _env("ICEBERG_WAREHOUSE", _s3_uri(S3_BUCKET, "olive_young_iceberg_metadata")),
)
INCI_WAREHOUSE = _env(
    "OPS_UI_INCI_WAREHOUSE",
    _s3_uri(S3_BUCKET, "inci_iceberg_metadata"),
)


CATALOGS: tuple[CatalogSettings, ...] = (
    CatalogSettings(
        label="oliveyoung_db",
        database="oliveyoung_db",
        warehouse=OLIVEYOUNG_WAREHOUSE,
        table_names=(
            "custom_ingredient_dict",
            "garbage_keywords",
            "gold_ingredient_frequency",
            "gold_product_change_log",
            "gold_product_ingredients",
            "neo4j_sync_checkpoint",
            "oliveyoung_category_master",
            "oliveyoung_silver_current",
            "oliveyoung_silver_error",
            "oliveyoung_silver_history",
            "typo_map",
        ),
    ),
    CatalogSettings(
        label="inci_db",
        database="inci_db",
        warehouse=INCI_WAREHOUSE,
        table_names=(
            "cosing_bronze_current",
            "cosing_bronze_history",
            "gold_kcia_cosing_ingredients_current",
            "gold_kcia_cosing_ingredients_history",
            "kcia_bronze_current",
            "kcia_bronze_history",
            "silver_kcia_cosing_fuzzy_review_current",
            "silver_kcia_cosing_graphrag_current",
            "silver_kcia_cosing_graphrag_history",
            "silver_kcia_cosing_matched_current",
            "silver_kcia_cosing_matched_history",
        ),
    ),
)


def catalog_configs() -> tuple[tuple[str, str, Callable[[], Any], tuple[str, ...]], ...]:
    return tuple(
        (config.label, config.database, config.get_catalog, config.configured_tables)
        for config in CATALOGS
    )
