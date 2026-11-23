"""I13-13: server load for administrators (decisions N1-N5 = A).

The API process (dwh-api, one worker) samples Linux counters every 2 seconds and keeps the last 60 minutes
in memory; nothing is written to the database or to disk.

  /proc/stat       CPU total and per core (busy = 100 - idle - iowait, as top), iowait, steal
  /proc/meminfo    used = MemTotal - MemAvailable (as free), cache, swap
  /proc/loadavg    load average
  /proc/diskstats  read/write bytes per second and active time of physical disks (/sys/block/<d>/device)
  /proc/net/dev    received/sent bytes per second, all interfaces except lo
  statvfs          free space of /, /var/lib/postgresql, /backup (one line per file system)
  cgroup v2        CPU and memory of the DWH services (cpu.stat usage_usec, memory.current)
  PostgreSQL       connections by role from pg_stat_activity (dwh_app sees only the count and the role)

GET /api/admin/server/load              current values (the badge)
GET /api/admin/server/load?since=<t>    current values and the samples taken after t (0 = all history)

Only SUPERADMIN and ADMIN (N1 = A); the router checks the level itself, as auth.require_admin does.
Settings: DWH_SERVER_LOAD_ENABLED (1 by default, 0 turns sampling off), DWH_SERVER_LOAD_UNITS (services,
comma separated), DWH_SERVER_LOAD_MOUNTS (file systems, comma separated).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from fastapi import HTTPException

from dwh_api.auth import AuthState, require_admin

INTERVAL_SECONDS = 2.0
HISTORY_SECONDS = 3600
HISTORY_SIZE = int(HISTORY_SECONDS / INTERVAL_SECONDS)
PG_INTERVAL_SECONDS = 10.0
PG_RETRY_SECONDS = 60.0

DEFAULT_UNITS = ("dwh-api.service", "dwh-dispatcher.service", "dwh-llm.service", "postgresql@17-main.service")
DEFAULT_MOUNTS = ("/", "/var/lib/postgresql", "/backup")
SECTOR_BYTES = 512


# ------------------------------------------------------------------ parsers (pure, tested on sample text)

def parse_stat(text: str) -> dict[str, list[int]]:
    """cpu lines of /proc/stat: name -> counters (user nice system idle iowait irq softirq steal ...)."""
    result: dict[str, list[int]] = {}
    for line in text.splitlines():
        if line.startswith("cpu"):
            parts = line.split()
            result[parts[0]] = [int(v) for v in parts[1:9]] + [0] * max(0, 8 - len(parts[1:9]))
    return result


def cpu_percent(prev: list[int], cur: list[int]) -> dict[str, float]:
    """busy / iowait / steal in percent between two readings of one cpu line (guest time is inside user)."""
    delta = [max(0, c - p) for p, c in zip(prev, cur)]
    total = sum(delta)
    if total <= 0:
        return {"busy": 0.0, "iowait": 0.0, "steal": 0.0}
    idle, iowait, steal = delta[3], delta[4], delta[7]
    return {"busy": round(100.0 * (total - idle - iowait) / total, 1),
            "iowait": round(100.0 * iowait / total, 1),
            "steal": round(100.0 * steal / total, 1)}


def parse_meminfo(text: str) -> dict[str, int]:
    """Memory in bytes: total, used (total - available, as free), available, cache, swap_total, swap_used."""
    kb: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        value = rest.split()
        if value and value[0].isdigit():
            kb[name.strip()] = int(value[0])
    total = kb.get("MemTotal", 0)
    available = kb.get("MemAvailable", kb.get("MemFree", 0))
    swap_total = kb.get("SwapTotal", 0)
    return {"total": total * 1024, "used": max(0, total - available) * 1024, "available": available * 1024,
            "cache": (kb.get("Cached", 0) + kb.get("Buffers", 0) + kb.get("SReclaimable", 0)) * 1024,
            "swap_total": swap_total * 1024, "swap_used": max(0, swap_total - kb.get("SwapFree", 0)) * 1024}


def parse_loadavg(text: str) -> list[float]:
    return [float(v) for v in text.split()[:3]]


def parse_diskstats(text: str, disks: set[str]) -> dict[str, tuple[int, int, int]]:
    """Physical disks: name -> (sectors read, sectors written, ms doing I/O)."""
    result: dict[str, tuple[int, int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 13 and parts[2] in disks:
            result[parts[2]] = (int(parts[5]), int(parts[9]), int(parts[12]))
    return result


def parse_netdev(text: str) -> tuple[int, int]:
    """Received and sent bytes of all interfaces except lo."""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        values = rest.split()
        if name.strip() and name.strip() != "lo" and len(values) >= 9:
            rx += int(values[0])
            tx += int(values[8])
    return rx, tx


def parse_cpu_stat(text: str) -> int | None:
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == "usage_usec" and value.strip().isdigit():
            return int(value)
    return None


# ------------------------------------------------------------------ reading the system

@dataclass
class Sources:
    """Where the counters are read; tests point it at a directory with sample files."""
    proc: Path = Path("/proc")
    sys_block: Path = Path("/sys/block")
    cgroup: Path = Path("/sys/fs/cgroup")
    units: tuple[str, ...] = DEFAULT_UNITS
    mounts: tuple[str, ...] = DEFAULT_MOUNTS
    statvfs: Callable[[str], Any] = os.statvfs
    stat_dev: Callable[[str], int] = field(default=lambda path: os.stat(path).st_dev)

    def read(self, name: str) -> str:
        return (self.proc / name).read_text(encoding="ascii", errors="replace")

    def physical_disks(self) -> set[str]:
        """Disks with a device behind them (sda, vda, nvme0n1); loop, ram and dm devices have none.
        Optical drives (sr0: the virtual CD drive of the VM) are not disks of the server."""
        try:
            return {p.name for p in self.sys_block.iterdir()
                    if (p / "device").exists() and not p.name.startswith("sr")}
        except OSError:
            return set()

    def unit_dir(self, unit: str) -> Path | None:
        """cgroup of a service: system.slice/<unit> or, for a template, system.slice/system-<name>.slice/<unit>."""
        base = self.cgroup / "system.slice"
        direct = base / unit
        if direct.is_dir():
            return direct
        if "@" in unit:
            nested = base / f"system-{unit.split('@', 1)[0]}.slice" / unit
            if nested.is_dir():
                return nested
        return None

    def unit_counters(self, unit: str) -> tuple[int | None, int | None]:
        """(CPU microseconds, memory bytes) of a running service, None when it does not run."""
        path = self.unit_dir(unit)
        if path is None:
            return None, None
        try:
            usec = parse_cpu_stat((path / "cpu.stat").read_text(encoding="ascii"))
        except OSError:
            usec = None
        try:
            mem = int((path / "memory.current").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            mem = None
        return usec, mem

    def file_systems(self) -> list[dict[str, Any]]:
        """Free space of the configured mount points; one line per file system (the first path that has it)."""
        seen: set[int] = set()
        result = []
        for mount in self.mounts:
            try:
                dev = self.stat_dev(mount)
                if dev in seen:
                    continue
                seen.add(dev)
                st = self.statvfs(mount)
            except OSError:
                continue
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            result.append({"path": mount, "total": total, "free": free,
                           "used_percent": round(100.0 * (total - free) / total, 1) if total else 0.0})
        return result


@dataclass
class _Reading:
    at: float
    cpu: dict[str, list[int]]
    disks: dict[str, tuple[int, int, int]]
    net: tuple[int, int]
    units: dict[str, tuple[int | None, int | None]]


class Sampler:
    """Takes a sample every interval; sample() is also called directly by the tests."""

    def __init__(self, sources: Sources | None = None, pg_counter: Callable[[], dict[str, int]] | None = None,
                 clock: Callable[[], float] = time.time):
        self.sources = sources or Sources()
        self.pg_counter = pg_counter
        self.clock = clock
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.lock = threading.Lock()
        self.cores = 0
        self._prev: _Reading | None = None
        self._disks: set[str] | None = None
        self._pg: dict[str, int] | None = None
        self._pg_at = 0.0
        self._pg_next = 0.0
        self._started = False

    def _read(self) -> _Reading:
        s = self.sources
        if self._disks is None:
            self._disks = s.physical_disks()
        return _Reading(self.clock(), parse_stat(s.read("stat")), parse_diskstats(s.read("diskstats"), self._disks),
                        parse_netdev(s.read("net/dev")), {u: s.unit_counters(u) for u in s.units})

    def _postgres(self, now: float) -> dict[str, int] | None:
        """Connections by role, every PG_INTERVAL_SECONDS; after a failure retried in PG_RETRY_SECONDS."""
        if self.pg_counter is None or now < self._pg_next:
            return self._pg
        try:
            self._pg = self.pg_counter()
            self._pg_at = now
            self._pg_next = now + PG_INTERVAL_SECONDS
        except Exception:  # the panel shows "no data"; the load must not break the API
            self._pg = None
            self._pg_next = now + PG_RETRY_SECONDS
        return self._pg

    def sample(self) -> dict[str, Any] | None:
        """Read the counters; the first call only remembers them (rates need two readings)."""
        cur = self._read()
        prev, self._prev = self._prev, cur
        if prev is None:
            return None
        seconds = max(cur.at - prev.at, 0.001)
        cores = sorted((k for k in cur.cpu if k != "cpu"), key=lambda k: int(k[3:]))
        self.cores = len(cores)
        total = cpu_percent(prev.cpu.get("cpu", [0] * 8), cur.cpu.get("cpu", [0] * 8))
        mem = parse_meminfo(self.sources.read("meminfo"))
        disks = {}
        for name, (r, w, io_ms) in cur.disks.items():
            pr, pw, pio = prev.disks.get(name, (r, w, io_ms))
            disks[name] = {"read": round((r - pr) * SECTOR_BYTES / seconds),
                           "write": round((w - pw) * SECTOR_BYTES / seconds),
                           "busy": round(min(100.0, (io_ms - pio) / (seconds * 10.0)), 1)}
        services = {}
        for unit, (usec, mem_bytes) in cur.units.items():
            pusec = prev.units.get(unit, (None, None))[0]
            cpu = None
            if usec is not None and pusec is not None and usec >= pusec and self.cores:
                cpu = round(100.0 * (usec - pusec) / (seconds * 1e6 * self.cores), 1)
            services[unit] = {"running": mem_bytes is not None or usec is not None, "cpu": cpu, "memory": mem_bytes}
        item = {
            "at": round(cur.at, 3),
            "cpu": total["busy"], "iowait": total["iowait"], "steal": total["steal"],
            "cores": [cpu_percent(prev.cpu.get(k, cur.cpu[k]), cur.cpu[k])["busy"] for k in cores],
            "memory": {"used": mem["used"], "cache": mem["cache"], "swap_used": mem["swap_used"]},
            "load": parse_loadavg(self.sources.read("loadavg")),
            "disks": disks,
            "net": {"rx": round((cur.net[0] - prev.net[0]) / seconds), "tx": round((cur.net[1] - prev.net[1]) / seconds)},
            "services": services,
            "postgres": self._postgres(cur.at),
        }
        with self.lock:
            self.history.append(item)
            self._totals = {"memory_total": mem["total"], "swap_total": mem["swap_total"]}
        return item

    def snapshot(self, since: float | None) -> dict[str, Any]:
        with self.lock:
            items = list(self.history)
            totals = dict(getattr(self, "_totals", {}))
        return {
            "interval_seconds": INTERVAL_SECONDS,
            "history_seconds": HISTORY_SECONDS,
            "sampling": self._started,
            "cores": self.cores,
            "memory_total": totals.get("memory_total"),
            "swap_total": totals.get("swap_total"),
            "current": items[-1] if items else None,
            "file_systems": self.sources.file_systems(),
            "postgres_at": round(self._pg_at, 3) if self._pg_at else None,
            "samples": None if since is None else [i for i in items if i["at"] > since],
        }

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.sample()
            except Exception:  # a broken counter file must not stop the thread
                pass
            stop.wait(max(0.1, INTERVAL_SECONDS - (time.monotonic() - started)))


# ------------------------------------------------------------------ PostgreSQL and the running sampler

def postgres_connections() -> dict[str, int]:
    """Client connections to the DWH database server by role (pg_stat_activity, read-only dwh_app session)."""
    from contextlib import closing

    from dwh_api.config import get_api_config
    from dwh_api.db import connect_readonly

    with closing(connect_readonly(get_api_config())) as conn:
        try:
            rows = conn.execute(
                "SELECT coalesce(usename, '?') AS role, count(*) AS n FROM pg_catalog.pg_stat_activity "
                "WHERE backend_type = 'client backend' GROUP BY 1 ORDER BY 1").fetchall()
        finally:
            conn.rollback()
    return {r["role"]: int(r["n"]) for r in rows}


def _split_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name, "").strip()
    return tuple(v.strip() for v in value.split(",") if v.strip()) if value else default


_sampler: Sampler | None = None
_stop = threading.Event()
_start_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("DWH_SERVER_LOAD_ENABLED", "1").strip() != "0" and Path("/proc/stat").exists()


def get_sampler() -> Sampler:
    """The process sampler; the background thread starts on the first call when sampling is enabled."""
    global _sampler
    with _start_lock:
        if _sampler is None:
            _sampler = Sampler(Sources(units=_split_env("DWH_SERVER_LOAD_UNITS", DEFAULT_UNITS),
                                       mounts=_split_env("DWH_SERVER_LOAD_MOUNTS", DEFAULT_MOUNTS)),
                               pg_counter=postgres_connections)
        if not _sampler._started and enabled():
            _sampler._started = True
            threading.Thread(target=_sampler.run_forever, args=(_stop,), name="dwh-server-load",
                             daemon=True).start()
        return _sampler


def start_sampling() -> None:
    """Startup of the API: history is collected from the start, not from the first opening of the panel."""
    if enabled():
        get_sampler()


def require_server_admin(state: AuthState = Depends(require_admin)) -> AuthState:
    """SUPERADMIN / ADMIN (N1 = A) with a changed temporary password, as the other administration pages."""
    if state.user is not None and state.user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return state


router = APIRouter(prefix="/api/admin/server", tags=["server-load"])


@router.get("/load")
def server_load(since: float | None = Query(default=None, ge=0),
                _state: AuthState = Depends(require_server_admin)) -> dict[str, Any]:
    return get_sampler().snapshot(since)
