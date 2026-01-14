"""Configuration from environment variables."""

import os


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self):
        self.dynamodb_connections_table = os.environ.get("DYNAMODB_CONNECTIONS_TABLE")
        self.dynamodb_messages_table = os.environ.get("DYNAMODB_MESSAGES_TABLE")
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.port = int(os.environ.get("PORT", "8000"))

    @property
    def use_dynamodb(self) -> bool:
        """Check if DynamoDB tables are configured."""
        return bool(self.dynamodb_connections_table and self.dynamodb_messages_table)


settings = Settings()
