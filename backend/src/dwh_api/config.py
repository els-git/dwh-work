from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


@dataclass(frozen=True)
class ApiConfig:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    api_host: str
    api_port: int


def require_env(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value.strip()


def get_api_config() -> ApiConfig:
    return ApiConfig(
        db_host=os.getenv(
            "DWH_DB_HOST",
            "127.0.0.1",
        ),
        db_port=int(
            os.getenv(
                "DWH_DB_PORT",
                "5433",
            )
        ),
        db_name=os.getenv(
            "DWH_DB_NAME",
            "dwh",
        ),

        # API must never connect as dwh_admin.
        db_user="dwh_app",
        db_password=require_env(
            "DWH_APP_PASSWORD"
        ),

        api_host="127.0.0.1",
        api_port=int(
            os.getenv(
                "DWH_API_PORT",
                "8000",
            )
        ),
    )