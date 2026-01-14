import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.model import model_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup."""
    logger.info("Starting ML Model API...")
    try:
        model_manager.load_model()
        logger.info("Model loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise
    yield
    logger.info("Shutting down ML Model API...")


app = FastAPI(
    title="ML Model Serving API",
    description="API for serving ML model predictions",
    version="1.0.0",
    lifespan=lifespan,
)


class PredictRequest(BaseModel):
    """Request schema for prediction endpoint."""

    features: list[list[float]] = Field(
        ...,
        description="List of feature vectors for prediction",
        example=[[5.1, 3.5, 1.4, 0.2], [6.2, 2.9, 4.3, 1.3]],
    )


class PredictionResult(BaseModel):
    """Single prediction result."""

    prediction: int
    class_name: str
    probabilities: dict[str, float]


class PredictResponse(BaseModel):
    """Response schema for prediction endpoint."""

    predictions: list[PredictionResult]


class HealthResponse(BaseModel):
    """Response schema for health check."""

    status: str
    model_loaded: bool


class ModelInfoResponse(BaseModel):
    """Response schema for model info."""

    name: str
    version: str
    loaded_at: str
    source: str
    feature_names: list[str]
    class_names: list[str]


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy" if model_manager.is_loaded else "degraded",
        model_loaded=model_manager.is_loaded,
    )


@app.get("/model/info", response_model=ModelInfoResponse)
async def model_info():
    """Get information about the loaded model."""
    if not model_manager.is_loaded or model_manager.info is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    return ModelInfoResponse(
        name=model_manager.info.name,
        version=model_manager.info.version,
        loaded_at=model_manager.info.loaded_at,
        source=model_manager.info.source,
        feature_names=model_manager.info.feature_names,
        class_names=model_manager.info.class_names,
    )


@app.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest):
    """Run prediction on input features."""
    if not model_manager.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    try:
        result = model_manager.predict(features=request.features)
        return PredictResponse(**result)
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")
