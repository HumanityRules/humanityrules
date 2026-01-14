from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    s3_bucket: str = ""
    s3_model_key: str = ""
    aws_region: str = "us-east-1"
    model_source: str = "local"
    local_model_path: str = "sample_model.joblib"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
