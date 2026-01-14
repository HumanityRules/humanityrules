import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
import joblib
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Metadata about the loaded model."""

    name: str
    version: str
    loaded_at: str
    source: str
    feature_names: list[str]
    class_names: list[str]


class ModelManager:
    """Manages ML model loading and inference."""

    def __init__(self):
        self.model: Any = None
        self.info: ModelInfo | None = None
        self._loaded = False

    def load_model(self) -> None:
        """Load the model from configured source (S3 or local)."""
        if settings.model_source == "s3":
            self._load_from_s3()
        else:
            self._load_from_local()

        self._loaded = True
        logger.info(f"Model loaded successfully from {settings.model_source}")

    def _load_from_s3(self) -> None:
        """Load model from S3 bucket."""
        if not settings.s3_bucket or not settings.s3_model_key:
            raise ValueError("S3_BUCKET and S3_MODEL_KEY must be set for S3 model source")

        logger.info(f"Loading model from s3://{settings.s3_bucket}/{settings.s3_model_key}")

        s3_client = boto3.client("s3", region_name=settings.aws_region)

        try:
            response = s3_client.get_object(
                Bucket=settings.s3_bucket,
                Key=settings.s3_model_key,
            )
            model_bytes = response["Body"].read()
            model_data = joblib.load(io.BytesIO(model_bytes))

            self._set_model_data(model_data=model_data, source=f"s3://{settings.s3_bucket}/{settings.s3_model_key}")

        except ClientError as e:
            logger.error(f"Failed to load model from S3: {e}")
            raise

    def _load_from_local(self) -> None:
        """Load model from local filesystem."""
        model_path = Path(settings.local_model_path)

        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")

        logger.info(f"Loading model from local path: {model_path}")
        model_data = joblib.load(model_path)

        self._set_model_data(model_data=model_data, source=str(model_path))

    def _set_model_data(self, model_data: dict, source: str) -> None:
        """Set model and metadata from loaded data."""
        self.model = model_data["model"]
        self.info = ModelInfo(
            name=model_data.get("name", "unknown"),
            version=model_data.get("version", "unknown"),
            loaded_at=datetime.utcnow().isoformat(),
            source=source,
            feature_names=[str(f) for f in model_data.get("feature_names", [])],
            class_names=[str(c) for c in model_data.get("class_names", [])],
        )

    def predict(self, features: list[list[float]]) -> dict:
        """Run prediction on input features."""
        if not self._loaded or self.model is None:
            raise RuntimeError("Model not loaded")

        predictions = self.model.predict(features)
        probabilities = self.model.predict_proba(features)

        results = []
        for i, pred in enumerate(predictions):
            class_name = self.info.class_names[pred] if self.info and self.info.class_names else str(pred)
            results.append({
                "prediction": int(pred),
                "class_name": str(class_name),
                "probabilities": {
                    str(name): float(prob)
                    for name, prob in zip(self.info.class_names if self.info else [], probabilities[i])
                },
            })

        return {"predictions": results}

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded and ready for inference."""
        return self._loaded and self.model is not None


model_manager = ModelManager()
