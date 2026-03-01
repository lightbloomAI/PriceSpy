from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    serpapi_key: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    database_path: str = "./data/pricespy.db"  # SQLite database path

    # Auth
    secret_key: str = "change-me-in-production"  # Signs session cookie
    cron_secret: str = ""  # Bearer token for cron job bypass
    auth_email: str = ""  # Seeded user email
    auth_password_hash: str = ""  # bcrypt hash of password

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
