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


CRM_SOURCE_CODE = "CRM_RETAIL"


def _crm_password() -> str:
    # I04-05: the CRM credential comes from SecretStore (source CRM_RETAIL).
    # A CRM_DB_PASSWORD left in .env is still honoured, with a warning, so that
    # the switch can be undone by restoring that line.
    legacy = os.getenv("CRM_DB_PASSWORD")
    if legacy is not None and legacy.strip():
        import sys
        print("WARNING: CRM_DB_PASSWORD from .env is used instead of SecretStore; "
              "remove it from .env (I04-05)", file=sys.stderr, flush=True)
        return legacy.strip()
    from dwh.source_secret import read_source_credential
    return read_source_credential(CRM_SOURCE_CODE)


def get_crm_config() -> DatabaseConfig:
    return DatabaseConfig(
        host=_required("CRM_DB_HOST"),
        port=int(os.getenv("CRM_DB_PORT", "5432")),
        database=_required("CRM_DB_NAME"),
        user=_required("CRM_DB_USER"),
        password=_crm_password(),
        sslmode=os.getenv("CRM_DB_SSLMODE", "prefer"),
    )