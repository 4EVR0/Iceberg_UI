# Olive Young Ops UI

Standalone Streamlit UI for checking Glue/Iceberg table status.

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
```

Defaults match the current production paths.
