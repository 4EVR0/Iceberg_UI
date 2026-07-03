# Olive Young Ops UI

Standalone Streamlit UI for checking Glue/Iceberg table status.

The app also supports Grafana-style drilldown links that open a dedicated
Athena-backed query view.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

The app uses the default boto3/PyIceberg AWS credential chain. No pipeline
package imports are required.

## Optional Environment Variables

```bash
OPS_UI_AWS_REGION=ap-northeast-2
OPS_UI_S3_BUCKET=oliveyoung-crawl-data
OPS_UI_OLIVEYOUNG_WAREHOUSE=s3://oliveyoung-crawl-data/olive_young_iceberg_metadata/
OPS_UI_INCI_WAREHOUSE=s3://oliveyoung-crawl-data/inci_iceberg_metadata/
OPS_UI_ICEBERG_CATALOG_NAME=glue
OPS_UI_ICEBERG_CATALOG_TYPE=glue
OPS_UI_ATHENA_DATABASE=oliveyoung_db
OPS_UI_ATHENA_WORKGROUP=primary
OPS_UI_ATHENA_OUTPUT=s3://your-athena-query-results-prefix/
```

Defaults match the current production paths.

## Drilldown URL Examples

```text
/?view=drilldown&metric=category_failure&catalog=oliveyoung_db&table=oliveyoung_silver_error&main_category=스킨케어&sub_category=토너&snapshot_mode=latest

/?view=drilldown&metric=error_type_spike&catalog=oliveyoung_db&table=oliveyoung_silver_error&error_type=category_parse_failed&snapshot_mode=latest
```
