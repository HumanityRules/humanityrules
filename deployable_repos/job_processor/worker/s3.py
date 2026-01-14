"""S3 operations for reading and writing job artifacts."""

import json
import logging

import boto3

logger = logging.getLogger(__name__)


class S3Client:
    """Client for S3 artifact operations."""

    def __init__(self, bucket: str, region: str):
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def read_json(self, key: str) -> dict:
        """Read a JSON object from S3."""
        logger.info("Reading s3://%s/%s", self._bucket, key)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        return json.loads(content)

    def write_json(self, key: str, data: dict) -> None:
        """Write a JSON object to S3."""
        logger.info("Writing s3://%s/%s", self._bucket, key)
        content = json.dumps(data, indent=2)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="application/json",
        )
