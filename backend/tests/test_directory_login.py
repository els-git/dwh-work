"""I20: domain login (directory_login) against a fake controller: trust before passwords, reasons, fail-over."""
from __future__ import annotations

import socket
import unittest

from dwh_api import directory_login as dl
from ldap_fake import FakeDirectory

BASE = "OU=Users,DC=vtbaze,DC=local"
BASE2 = "OU=Staff,DC=vtbaze,DC=local"
GUID = "0a1b2c3d-0000-4000-8000-00000000aaaa"
USERS = {
    "ivanov": {"dn": f"CN=Ivanov,{BASE}", "password": "Correct-Pass-1", "guid": GUID, "uac": 512},
    "petrov": {"dn": f"CN=Petrov,{BASE2}", "password": "Correct-Pass-2", "guid": "11111111-2222-4333-8444-555555555555"},
    "off": {"dn": f"CN=Off,{BASE}", "password": "x-Pass-3", "guid": "22222222-2222-4333-8444-555555555555", "uac": 514},
    "locked": {"dn": f"CN=Locked,{BASE}", "password": "x-Pass-4", "guid": "33333333-2222-4333-8444-555555555555",
               "bind_error": "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 775, v4563"},
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DirectoryLoginTest(unittest.TestCase):
    def setUp(self):
        self.dc = FakeDirectory(USERS, base_dns=(BASE, BASE2))
        self.addCleanup(self.dc.close)

    def settings(self, servers=None, pins=None) -> dl.Settings:
        return dl.Settings(servers=servers or [("localhost", self.dc.port)],
                           pins=pins if pins is not None else [self.dc.fingerprint], ca_file=None,
                           domain="vtbaze.local", netbios="VTBAZE", base_dns=[BASE, BASE2],
                           bind_user="svc@vtbaze.local", timeout=3)

    def login(self, name, password, **kw):
        return dl.authenticate(name, password, self.settings(**kw), service_secret="svc-pass")

    def test_normalize_username(self):
        s = self.settings()
        self.assertEqual(dl.normalize_username("Ivanov", s), "ivanov")
        self.assertEqual(dl.normalize_username("vtbaze\\Ivanov", s), "ivanov")
        self.assertEqual(dl.normalize_username(" ivanov@VTBAZE.LOCAL ", s), "ivanov")
        for bad in ("OTHER\\ivanov", "ivanov@other.local", "CN=x,DC=y", "a*b", "", "x)(objectClass=*"):
            self.assertIsNone(dl.normalize_username(bad, s), bad)

    def test_success_and_guid(self):
        result = self.login("VTBAZE\\ivanov", "Correct-Pass-1")
        self.assertEqual(result.reason, dl.OK)
        self.assertEqual(result.guid, GUID)
        self.assertEqual(self.dc.binds, ["svc@vtbaze.local", f"CN=Ivanov,{BASE}"])

    def test_second_base_dn(self):
        result = self.login("petrov@vtbaze.local", "Correct-Pass-2")
        self.assertEqual(result.reason, dl.OK)

    def test_wrong_password(self):
        self.assertEqual(self.login("ivanov", "wrong").reason, dl.INVALID_CREDENTIALS)

    def test_not_found(self):
        self.assertEqual(self.login("nobody", "whatever").reason, dl.LDAP_NOT_FOUND)
        self.assertEqual(self.dc.binds, ["svc@vtbaze.local"])

    def test_disabled_without_user_bind(self):
        self.assertEqual(self.login("off", "x-Pass-3").reason, dl.LDAP_ACCOUNT_DISABLED)
        self.assertEqual(self.dc.binds, ["svc@vtbaze.local"])

    def test_locked_by_ad_subcode(self):
        self.assertEqual(self.login("locked", "x-Pass-4").reason, dl.LDAP_ACCOUNT_LOCKED)

    def test_empty_password_never_sent(self):
        self.assertEqual(self.login("ivanov", "").reason, dl.INVALID_CREDENTIALS)
        self.assertEqual(self.dc.binds, [])

    def test_wrong_pin_sends_nothing(self):
        result = self.login("ivanov", "Correct-Pass-1", pins=["00" * 32])
        self.assertEqual(result.reason, dl.LDAP_UNAVAILABLE)
        self.assertIn("fingerprint", result.details["error"])
        self.assertEqual(self.dc.binds, [])

    def test_no_pin_and_no_ca_is_unavailable(self):
        self.assertEqual(self.login("ivanov", "Correct-Pass-1", pins=[]).reason, dl.LDAP_UNAVAILABLE)
        self.assertEqual(self.dc.binds, [])

    def test_starttls_refused_sends_nothing(self):
        plain = FakeDirectory(USERS, base_dns=(BASE, BASE2), mode="refuse_starttls")
        self.addCleanup(plain.close)
        result = dl.authenticate("ivanov", "Correct-Pass-1", self.settings(servers=[("localhost", plain.port)],
                                                                          pins=[plain.fingerprint]), "svc-pass")
        self.assertEqual(result.reason, dl.LDAP_UNAVAILABLE)
        self.assertIn("StartTLS", result.details["error"])
        self.assertEqual(plain.binds, [])

    def test_failover_to_second_controller(self):
        down = ("localhost", free_port())
        result = self.login("ivanov", "Correct-Pass-1", servers=[down, ("localhost", self.dc.port)])
        self.assertEqual(result.reason, dl.OK)
        self.assertEqual(result.server, f"localhost:{self.dc.port}")

    def test_all_controllers_down(self):
        result = self.login("ivanov", "Correct-Pass-1", servers=[("localhost", free_port())])
        self.assertEqual(result.reason, dl.LDAP_UNAVAILABLE)

    def test_service_account_rejected_is_unavailable(self):
        result = dl.authenticate("ivanov", "Correct-Pass-1", self.settings(), service_secret="bad")
        self.assertEqual(result.reason, dl.LDAP_UNAVAILABLE)
        self.assertEqual(self.dc.binds, ["svc@vtbaze.local"])

    def test_ldaps_port(self):
        tls = FakeDirectory(USERS, base_dns=(BASE, BASE2), mode="ldaps")
        self.addCleanup(tls.close)
        settings = self.settings(servers=[("localhost", tls.port)], pins=[tls.fingerprint])
        settings_ldaps = dl.Settings(**{**settings.__dict__})
        # LDAPS is chosen by port 636 / 3269; a test port cannot be 636, so the connection helper is checked directly.
        conn = dl.Connection(dl._tls_context(settings_ldaps).wrap_socket(socket.create_connection(("localhost", tls.port)),
                                                                           server_hostname="localhost"), 3)
        try:
            result = dl.check_user(conn, settings_ldaps, "svc-pass", "ivanov", "Correct-Pass-1")
        finally:
            conn.close()
        self.assertEqual(result.reason, dl.OK)

    def test_settings_from_env_and_password_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, dl.CREDENTIAL_NAME).write_text("secret\n")
            env = {"DWH_LDAP_SERVERS": "dc1.vtbaze.local:389, dc2.vtbaze.local:389", "DWH_LDAP_PINS": "ab:cd",
                   "DWH_LDAP_BASE_DNS": f"{BASE};{BASE2}", "DWH_LDAP_DOMAIN": "VTBAZE.local",
                   "DWH_LDAP_NETBIOS": "vtbaze", "DWH_LDAP_BIND_USER": "svc@vtbaze.local",
                   "CREDENTIALS_DIRECTORY": tmp}
            settings = dl.settings_from_env(env)
            self.assertEqual(settings.servers, [("dc1.vtbaze.local", 389), ("dc2.vtbaze.local", 389)])
            self.assertEqual(settings.pins, ["ABCD"])
            self.assertEqual(settings.base_dns, [BASE, BASE2])
            self.assertEqual((settings.domain, settings.netbios), ("vtbaze.local", "VTBAZE"))
            self.assertEqual(settings.problems, [])
            self.assertEqual(dl.service_password(env), "secret")
        with self.assertRaises(dl.DirectoryError):
            dl.service_password({})
        with self.assertRaises(dl.DirectoryError):
            dl.settings_from_env({"DWH_LDAP_SERVERS": "nohost"})

    def test_ber_round_trip(self):
        message_id, tag, op = dl.parse_message(dl.search_request(7, BASE, "ivanov", ["objectGUID"]))
        self.assertEqual((message_id, tag), (7, 0x63))
        self.assertIn(b"sAMAccountName", op)
        self.assertEqual(dl.describe_code(49, "AcceptSecurityContext error, data 533, v4563"), dl.LDAP_ACCOUNT_DISABLED)
        self.assertEqual(dl.describe_code(49, "data 532"), dl.LDAP_PASSWORD_EXPIRED)
        self.assertEqual(dl.describe_code(49, ""), dl.INVALID_CREDENTIALS)


if __name__ == "__main__":
    unittest.main()
