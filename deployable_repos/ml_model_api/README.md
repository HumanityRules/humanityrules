# ML Model Serving API

A FastAPI-based service for serving ML model predictions. Demonstrates a common enterprise pattern for data science teams - serving scikit-learn models via REST API with S3 integration for model storage.

## Features

- **/predict** - Model inference endpoint accepting JSON input
- **/health** - Health check endpoint
- **/model/info** - Model version and metadata endpoint
- Loads models from S3 (production) or local filesystem (development)
- Pre-trained Iris classifier included for testing

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run locally with sample model

The repository includes a pre-trained model (`sample_model.joblib`). To run locally:

```bash
# Uses local model by default (MODEL_SOURCE=local)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Test the API

Health check:
```bash
curl http://localhost:8000/health
```

Model info:
```bash
curl http://localhost:8000/model/info
```

Make a prediction:
```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"features": [[5.1, 3.5, 1.4, 0.2], [6.2, 2.9, 4.3, 1.3]]}'
```

## Training a New Model

To retrain the model:

```bash
# Train and save locally
python train_model.py

# Train and upload to S3
python train_model.py --upload
```

The training script:
- Uses the Iris dataset (150 samples, 4 features, 3 classes)
- Trains a RandomForest classifier
- Outputs accuracy metrics
- Saves model with metadata (feature names, class names, version)

## Configuration

Configuration is via environment variables. Copy `.env.example` to `.env` and customize:

- **MODEL_SOURCE** - Where to load the model from: `local` or `s3`
- **LOCAL_MODEL_PATH** - Path to local model file (default: `sample_model.joblib`)
- **S3_BUCKET** - S3 bucket name for model storage
- **S3_MODEL_KEY** - S3 object key for the model file
- **AWS_REGION** - AWS region (default: `us-east-1`)

### Local Development

```bash
MODEL_SOURCE=local
LOCAL_MODEL_PATH=sample_model.joblib
```

### Production (S3)

```bash
MODEL_SOURCE=s3
S3_BUCKET=your-ml-models-bucket
S3_MODEL_KEY=models/iris_classifier.joblib
AWS_REGION=us-east-1
```

## Uploading Model to S3

```bash
# Set environment variables
export S3_BUCKET=your-ml-models-bucket
export S3_MODEL_KEY=models/iris_classifier.joblib
export AWS_REGION=us-east-1

# Train and upload
python train_model.py --upload
```

## API Endpoints

### GET /health

Returns service health status.

**Response:**
```json
{
  "status": "healthy",
  "model_loaded": true
}
```

### GET /model/info

Returns information about the loaded model.

**Response:**
```json
{
  "name": "iris-classifier",
  "version": "20240115-143022",
  "loaded_at": "2024-01-15T14:30:25.123456",
  "source": "sample_model.joblib",
  "feature_names": ["sepal length (cm)", "sepal width (cm)", "petal length (cm)", "petal width (cm)"],
  "class_names": ["setosa", "versicolor", "virginica"]
}
```

### POST /predict

Run inference on input features.

**Request:**
```json
{
  "features": [
    [5.1, 3.5, 1.4, 0.2],
    [6.2, 2.9, 4.3, 1.3]
  ]
}
```

**Response:**
```json
{
  "predictions": [
    {
      "prediction": 0,
      "class_name": "setosa",
      "probabilities": {
        "setosa": 0.98,
        "versicolor": 0.01,
        "virginica": 0.01
      }
    },
    {
      "prediction": 1,
      "class_name": "versicolor",
      "probabilities": {
        "setosa": 0.02,
        "versicolor": 0.85,
        "virginica": 0.13
      }
    }
  ]
}
```

## Port

The API runs on port **8000** by default.

## Requirements

- Python 3.12+
- FastAPI
- scikit-learn
- boto3 (for S3 model loading)
