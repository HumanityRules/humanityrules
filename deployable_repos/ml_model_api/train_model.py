#!/usr/bin/env python3
"""
Training script for the sample Iris classifier model.

This script trains a simple RandomForest classifier on the Iris dataset
and saves it in a format that can be loaded by the ML API.

Usage:
    python train_model.py                    # Save locally as sample_model.joblib
    python train_model.py --upload           # Upload to S3 after training

Environment variables for S3 upload:
    S3_BUCKET: Target S3 bucket
    S3_MODEL_KEY: Object key for the model file
    AWS_REGION: AWS region (default: us-east-1)
"""

import argparse
import os
import sys
from datetime import datetime

import boto3
import joblib
from botocore.exceptions import ClientError
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score, train_test_split


def train_model():
    """Train a RandomForest classifier on the Iris dataset."""
    print("Loading Iris dataset...")
    iris = load_iris()
    X, y = iris.data, iris.target

    print("Splitting data into train/test sets...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    print("Training RandomForest classifier...")
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=5,
        random_state=42,
    )
    model.fit(X_train, y_train)

    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)
    cv_scores = cross_val_score(model, X, y, cv=5)

    print(f"Training accuracy: {train_score:.4f}")
    print(f"Test accuracy: {test_score:.4f}")
    print(f"Cross-validation accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    model_data = {
        "model": model,
        "name": "iris-classifier",
        "version": datetime.utcnow().strftime("%Y%m%d-%H%M%S"),
        "feature_names": list(iris.feature_names),
        "class_names": list(iris.target_names),
        "metrics": {
            "train_accuracy": train_score,
            "test_accuracy": test_score,
            "cv_mean": cv_scores.mean(),
            "cv_std": cv_scores.std(),
        },
    }

    return model_data


def save_model_local(model_data: dict, path: str) -> None:
    """Save model to local filesystem."""
    print(f"Saving model to {path}...")
    joblib.dump(model_data, path)
    print(f"Model saved successfully to {path}")


def upload_model_to_s3(model_data: dict) -> None:
    """Upload model to S3."""
    bucket = os.environ.get("S3_BUCKET")
    key = os.environ.get("S3_MODEL_KEY")
    region = os.environ.get("AWS_REGION", "us-east-1")

    if not bucket or not key:
        print("Error: S3_BUCKET and S3_MODEL_KEY environment variables must be set")
        sys.exit(1)

    print(f"Uploading model to s3://{bucket}/{key}...")

    temp_path = "/tmp/model_upload.joblib"
    joblib.dump(model_data, temp_path)

    s3_client = boto3.client("s3", region_name=region)

    try:
        s3_client.upload_file(
            Filename=temp_path,
            Bucket=bucket,
            Key=key,
        )
        print(f"Model uploaded successfully to s3://{bucket}/{key}")
    except ClientError as e:
        print(f"Error uploading to S3: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def main():
    parser = argparse.ArgumentParser(description="Train and save the Iris classifier model")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload model to S3 after training",
    )
    parser.add_argument(
        "--output",
        default="sample_model.joblib",
        help="Local output path for the model (default: sample_model.joblib)",
    )
    args = parser.parse_args()

    model_data = train_model()

    save_model_local(model_data=model_data, path=args.output)

    if args.upload:
        upload_model_to_s3(model_data=model_data)


if __name__ == "__main__":
    main()
