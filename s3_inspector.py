from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import boto3


@dataclass(frozen=True)
class LatestS3Object:
    key: str
    last_modified: datetime
    size: int


def split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"S3 URI 형식이 아닙니다: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def latest_object(uri: Optional[str], max_keys: int = 1000) -> Optional[LatestS3Object]:
    """Return the most recently modified object under an S3 table prefix."""
    if not uri or not uri.startswith("s3://"):
        return None

    bucket, prefix = split_s3_uri(uri)
    client = boto3.client("s3")

    paginator = client.get_paginator("list_objects_v2")
    latest: Optional[LatestS3Object] = None
    seen = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            seen += 1
            candidate = LatestS3Object(
                key=obj["Key"],
                last_modified=obj["LastModified"],
                size=obj["Size"],
            )
            if latest is None or candidate.last_modified > latest.last_modified:
                latest = candidate
            if seen >= max_keys:
                return latest

    return latest
