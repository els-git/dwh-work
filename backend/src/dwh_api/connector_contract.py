"""Connector adapter contract (I03-04).

Every source technology (PostgreSQL in I04, Oracle in I10, files in I12, API
objects in I11) is added as an adapter implementing the same three operations,
so the core never changes when a new technology arrives:

  test_connection(config, credential) -> ConnectionTestResult
  discover(config, credential)        -> list[DiscoveredObject]
  read(config, credential, request)   -> iterator of row dictionaries

`config` is the non-secret connection_config of the source; `credential` is a
SecretValue (or None) whose value is never shown by repr/str. Adapters must
not log the credential, must open read-only sessions and must report errors
without connection strings or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable


class ConnectorError(Exception):
    """Sanitized adapter failure: never contains credentials or DSNs."""


class SecretValue:
    """Holds a credential; repr/str never reveal it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ConnectorError("Invalid credential")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(<hidden>)"

    __str__ = __repr__


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class DiscoveredColumn:
    name: str
    data_type: str
    nullable: bool
    position: int


@dataclass(frozen=True)
class DiscoveredObject:
    schema: str
    name: str
    object_type: str
    columns: tuple[DiscoveredColumn, ...] = ()


@dataclass(frozen=True)
class ReadRequest:
    schema: str
    name: str
    columns: tuple[str, ...]
    batch_size: int = 10_000
    filters: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ConnectorAdapter(Protocol):
    adapter_code: str
    source_kind: str  # DATABASE | FILE | API

    def test_connection(self, config: Mapping[str, Any], credential: SecretValue | None) -> ConnectionTestResult: ...

    def discover(self, config: Mapping[str, Any], credential: SecretValue | None) -> list[DiscoveredObject]: ...

    def read(
        self, config: Mapping[str, Any], credential: SecretValue | None, request: ReadRequest
    ) -> Iterator[dict[str, Any]]: ...


_ADAPTERS: dict[str, ConnectorAdapter] = {}


def register_adapter(adapter: ConnectorAdapter) -> None:
    if not isinstance(adapter, ConnectorAdapter):
        raise ConnectorError("Object does not implement the connector contract")
    code = adapter.adapter_code
    if not isinstance(code, str) or not code.isupper() or not code.replace("_", "").isalnum():
        raise ConnectorError("Invalid adapter code")
    if adapter.source_kind not in ("DATABASE", "FILE", "API"):
        raise ConnectorError("Invalid source kind")
    if code in _ADAPTERS:
        raise ConnectorError("Adapter already registered")
    _ADAPTERS[code] = adapter


def get_adapter(code: str) -> ConnectorAdapter:
    try:
        return _ADAPTERS[code]
    except KeyError:
        raise ConnectorError("No adapter for this source type") from None


def registered_adapters() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))
