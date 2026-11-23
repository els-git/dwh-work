"""I13-13: server load sampler on sample /proc and cgroup files, and the administrator-only route."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DWH_APP_PASSWORD", "unused")
os.environ["DWH_SERVER_LOAD_ENABLED"] = "0"

from fastapi import HTTPException  # noqa: E402

from dwh_api import server_load as sl  # noqa: E402

MEMINFO = """MemTotal:       32000000 kB
MemFree:         1000000 kB
MemAvailable:   24000000 kB
Buffers:          100000 kB
Cached:          6000000 kB
SwapCached:            0 kB
SReclaimable:     400000 kB
SwapTotal:       4000000 kB
SwapFree:        3000000 kB
"""

NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: {lo} 10 0 0 0 0 0 0 {lo} 10 0 0 0 0 0 0
  eth0: {rx} 100 0 0 0 0 0 0 {tx} 100 0 0 0 0 0 0
"""


def stat_text(total: list[int], cores: list[list[int]]) -> str:
    lines = ["cpu  " + " ".join(map(str, total))]
    lines += [f"cpu{i} " + " ".join(map(str, c)) for i, c in enumerate(cores)]
    return "\n".join(lines + ["intr 1 2 3", "ctxt 5"]) + "\n"


def diskstats(read: int, written: int, io_ms: int) -> str:
    return (f"   8       0 sda 100 0 {read} 50 200 0 {written} 70 0 {io_ms} 120 0 0 0 0\n"
            f"   7       0 loop0 1 0 999999 1 0 0 999999 1 0 1 1 0 0 0 0\n"
            f" 253       0 dm-0 1 0 999999 1 0 0 999999 1 0 1 1 0 0 0 0\n")


class World:
    """A directory tree with /proc, /sys/block and cgroup files that a test rewrites between samples."""

    def __init__(self, root: Path):
        self.root = root
        for d in ("proc/net", "block/sda/device", "block/sr0/device", "block/loop0", "block/dm-0",
                  "cg/system.slice/dwh-api.service", "cg/system.slice/system-postgresql.slice/postgresql@17-main.service"):
            (root / d).mkdir(parents=True)
        (root / "proc/meminfo").write_text(MEMINFO)
        (root / "proc/loadavg").write_text("1.50 0.75 0.25 2/300 1234\n")
        self.now = 1000.0
        self.statvfs_calls: list[str] = []

    def write(self, total, cores, disk, net, api_usec, pg_usec):
        (self.root / "proc/stat").write_text(stat_text(total, cores))
        (self.root / "proc/diskstats").write_text(diskstats(*disk))
        (self.root / "proc/net/dev").write_text(NETDEV.format(lo=10 ** 9, rx=net[0], tx=net[1]))
        api = self.root / "cg/system.slice/dwh-api.service"
        (api / "cpu.stat").write_text(f"usage_usec {api_usec}\nuser_usec 1\nsystem_usec 1\n")
        (api / "memory.current").write_text("209715200\n")
        pg = self.root / "cg/system.slice/system-postgresql.slice/postgresql@17-main.service"
        (pg / "cpu.stat").write_text(f"usage_usec {pg_usec}\n")
        (pg / "memory.current").write_text("1073741824\n")

    def statvfs(self, path):
        self.statvfs_calls.append(path)
        return SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=250)

    def sources(self) -> sl.Sources:
        devs = {"/": 1, "/var/lib/postgresql": 2, "/backup": 1, "/missing": None}

        def stat_dev(path):
            if devs.get(path) is None:
                raise FileNotFoundError(path)
            return devs[path]

        return sl.Sources(proc=self.root / "proc", sys_block=self.root / "block", cgroup=self.root / "cg",
                          units=("dwh-api.service", "postgresql@17-main.service", "dwh-llm.service"),
                          mounts=("/", "/var/lib/postgresql", "/backup", "/missing"),
                          statvfs=self.statvfs, stat_dev=stat_dev)


class ParserTest(unittest.TestCase):
    def test_memory_matches_free(self):
        mem = sl.parse_meminfo(MEMINFO)
        self.assertEqual(mem["used"], (32000000 - 24000000) * 1024)  # free: used = total - available
        self.assertEqual(mem["cache"], (6000000 + 100000 + 400000) * 1024)
        self.assertEqual(mem["swap_used"], 1000000 * 1024)

    def test_cpu_percent_as_top(self):
        prev = [100, 0, 100, 700, 100, 0, 0, 0]
        cur = [150, 0, 150, 1000, 200, 0, 0, 0]  # +50 user +50 system +300 idle +100 iowait
        self.assertEqual(sl.cpu_percent(prev, cur), {"busy": 20.0, "iowait": 20.0, "steal": 0.0})
        self.assertEqual(sl.cpu_percent(cur, cur)["busy"], 0.0)

    def test_net_excludes_loopback_and_disks_only_physical(self):
        self.assertEqual(sl.parse_netdev(NETDEV.format(lo=5, rx=10, tx=20)), (10, 20))
        self.assertEqual(sl.parse_diskstats(diskstats(1, 2, 3), {"sda"}), {"sda": (1, 2, 3)})
        self.assertEqual(sl.parse_loadavg("1.50 0.75 0.25 2/300 1234"), [1.5, 0.75, 0.25])
        self.assertIsNone(sl.parse_cpu_stat("user_usec 5\n"))


class SamplerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.world = World(Path(self.tmp.name))
        self.pg_calls = 0

        def pg():
            self.pg_calls += 1
            if self.pg_calls == 2:
                raise RuntimeError("database is down")
            return {"dwh_app": 3, "dwh_etl": 1}

        self.sampler = sl.Sampler(self.world.sources(), pg_counter=pg, clock=lambda: self.world.now)

    def tearDown(self):
        self.tmp.cleanup()

    def two_samples(self):
        core = [10, 0, 10, 80, 0, 0, 0, 0]
        self.world.write([20, 0, 20, 160, 0, 0, 0, 0], [core, core], (0, 0, 0), (1000, 2000), 1_000_000, 0)
        self.assertIsNone(self.sampler.sample())  # the first reading only remembers counters
        self.world.now += 2.0
        # cpu0 fully busy, cpu1 idle; total = half; 4096 sectors read, 2048 written, 1000 ms of I/O in 2 s
        self.world.write([220, 0, 20, 360, 0, 0, 0, 0], [[210, 0, 10, 80, 0, 0, 0, 0], [10, 0, 10, 280, 0, 0, 0, 0]],
                         (4096, 2048, 1000), (5000, 3000), 3_000_000, 1_000_000)
        return self.sampler.sample()

    def test_rates_per_second(self):
        item = self.two_samples()
        self.assertEqual(item["cpu"], 50.0)
        self.assertEqual(item["cores"], [100.0, 0.0])
        self.assertEqual(item["disks"], {"sda": {"read": 4096 * 512 // 2, "write": 2048 * 512 // 2, "busy": 50.0}})
        self.assertEqual(item["net"], {"rx": 2000, "tx": 500})
        self.assertEqual(item["load"], [1.5, 0.75, 0.25])
        self.assertEqual(item["memory"]["used"], 8000000 * 1024)

    def test_services_share_of_all_cores(self):
        item = self.two_samples()
        # dwh-api: 2 s of CPU in 2 s on 2 cores = 50 %; postgresql: 1 s = 25 %; dwh-llm does not run
        self.assertEqual(item["services"]["dwh-api.service"], {"running": True, "cpu": 50.0, "memory": 209715200})
        self.assertEqual(item["services"]["postgresql@17-main.service"]["cpu"], 25.0)
        self.assertEqual(item["services"]["dwh-llm.service"], {"running": False, "cpu": None, "memory": None})

    def test_history_since_and_postgres_failure(self):
        self.two_samples()
        snap = self.sampler.snapshot(None)
        self.assertIsNone(snap["samples"])
        self.assertEqual(snap["current"]["postgres"], {"dwh_app": 3, "dwh_etl": 1})
        self.assertEqual(snap["cores"], 2)
        self.assertEqual(snap["memory_total"], 32000000 * 1024)
        self.assertEqual(len(self.sampler.snapshot(0)["samples"]), 1)
        self.assertEqual(self.sampler.snapshot(snap["current"]["at"])["samples"], [])
        # the next PostgreSQL reading is due in 10 s and fails: no data, retried in 60 s, sampling goes on
        self.world.now += sl.PG_INTERVAL_SECONDS
        self.assertIsNone(self.sampler.sample()["postgres"])
        self.world.now += 2.0
        self.assertIsNone(self.sampler.sample()["postgres"])
        self.assertEqual(self.pg_calls, 2)

    def test_only_server_disks(self):
        self.assertEqual(self.sampler.sources.physical_disks(), {"sda"})  # no loop, dm or sr0 (CD drive)

    def test_file_systems_once_per_device(self):
        snap = self.sampler.snapshot(None)
        self.assertEqual([f["path"] for f in snap["file_systems"]], ["/", "/var/lib/postgresql"])
        self.assertEqual(snap["file_systems"][0]["used_percent"], 75.0)

    def test_history_is_one_hour(self):
        self.assertEqual(self.sampler.history.maxlen, 1800)


class AccessTest(unittest.TestCase):
    def state(self, level, must_change=False):
        return SimpleNamespace(user=SimpleNamespace(account_level=level, must_change_password=must_change))

    def test_only_administrators(self):
        from dwh_api.auth import require_admin
        for level in ("SUPERADMIN", "ADMIN"):
            self.assertIsNotNone(sl.require_server_admin(require_admin(self.state(level))))
        for level in ("USER", "AUDITOR", ""):
            with self.assertRaises(HTTPException) as err:
                require_admin(self.state(level))
            self.assertEqual(err.exception.status_code, 403)
        with self.assertRaises(HTTPException) as err:
            sl.require_server_admin(self.state("ADMIN", must_change=True))
        self.assertEqual(err.exception.status_code, 403)

    def test_route_over_http(self):
        from unittest import mock

        from fastapi.testclient import TestClient

        from dwh_api import auth
        from dwh_api.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/api/admin/server/load").status_code, 401)  # no session
        client.cookies.set(auth.SESSION_COOKIE_NAME, "t" * 40)
        fake = sl.Sampler(sl.Sources(mounts=()), pg_counter=None)
        for level, code in (("USER", 403), ("ADMIN", 200), ("SUPERADMIN", 200)):
            state = SimpleNamespace(user=SimpleNamespace(account_level=level, must_change_password=False),
                                    permissions=(), resources=())
            with mock.patch.object(auth, "_load_auth_state_by_hash", return_value=state), \
                    mock.patch.object(sl, "get_sampler", return_value=fake):
                response = client.get("/api/admin/server/load", params={"since": 0})
            self.assertEqual(response.status_code, code, level)
            if code == 200:
                self.assertEqual(response.json()["samples"], [])
                self.assertEqual(response.json()["interval_seconds"], 2.0)

    def test_sampling_can_be_turned_off(self):
        self.assertFalse(sl.enabled())


if __name__ == "__main__":
    unittest.main()
