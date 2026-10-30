from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(PROJECT_ROOT / ".env")


def _required(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value.strip()


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "prefer"


def get_dwh_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="127.0.0.1",
        port=int(os.getenv("DWH_DB_PORT", "5433")),
        database=_required("DWH_DB_NAME"),
        user="dwh_etl",
        password=_required("DWH_ETL_PASSWORD"),
        sslmode="disable",
    )


def get_crm_config() -> DatabaseConfig:
    return DatabaseConfig(
        host=_required("CRM_DB_HOST"),
        port=int(os.getenv("CRM_DB_PORT", "5432")),
        database=_required("CRM_DB_NAME"),
        user=_required("CRM_DB_USER"),
        password=_required("CRM_DB_PASSWORD"),
        sslmode=os.getenv("CRM_DB_SSLMODE", "prefer"),
    )