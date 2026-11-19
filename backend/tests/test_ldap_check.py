"""I20 preparation: BER of the LDAP simple bind in tools/server/dwh_ldap_check.py."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "server"))
import dwh_ldap_check as ldap  # noqa: E402


def bind_response(code: int, diagnostic: str = "") -> bytes:
    body = ldap._tlv(0x0A, bytes([code])) + ldap._tlv(0x04, b"") + ldap._tlv(0x04, diagnostic.encode())
    return ldap._tlv(0x30, ldap._integer(1) + ldap._tlv(0x61, body))


class BindBerTest(unittest.TestCase):
    def test_request_layout(self):
        data = ldap.bind_request(1, "svc@bank.local", "s3cret")
        tag, message, end = ldap._read_tlv(data, 0)
        self.assertEqual((tag, end), (0x30, len(data)))
        _, msg_id, pos = ldap._read_tlv(message, 0)
        self.assertEqual(msg_id, b"\x01")
        tag, request, _ = ldap._read_tlv(message, pos)
        self.assertEqual(tag, 0x60)
        _, version, pos = ldap._read_tlv(request, 0)
        self.assertEqual(version, b"\x03")
        _, name, pos = ldap._read_tlv(request, pos)
        self.assertEqual(name, b"svc@bank.local")
        tag, secret, _ = ldap._read_tlv(request, pos)
        self.assertEqual((tag, secret), (0x80, b"s3cret"))

    def test_long_lengths(self):
        data = ldap.bind_request(7, "cn=" + "x" * 300, "p")
        tag, _, end = ldap._read_tlv(data, 0)
        self.assertEqual((tag, end), (0x30, len(data)))

    def test_response_and_ad_subcode(self):
        self.assertEqual(ldap.parse_bind_response(bind_response(0)), (0, ""))
        code, diagnostic = ldap.parse_bind_response(
            bind_response(49, "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 52e, v4563"))
        self.assertEqual(code, 49)
        self.assertEqual(ldap.describe_bind(code, diagnostic), "49 invalidCredentials (AD: wrong password)")
        self.assertIn("account disabled", ldap.describe_bind(49, "..., data 533, ..."))

    def test_not_a_bind_response(self):
        with self.assertRaises(ValueError):
            ldap.parse_bind_response(ldap._tlv(0x04, b"x"))


class StartTlsTest(unittest.TestCase):
    def test_request_and_response(self):
        data = ldap.starttls_request(1)
        tag, message, _ = ldap._read_tlv(data, 0)
        _, _, pos = ldap._read_tlv(message, 0)
        tag, request, _ = ldap._read_tlv(message, pos)
        self.assertEqual(tag, 0x77)
        self.assertEqual(ldap._read_tlv(request, 0)[:2], (0x80, b"1.3.6.1.4.1.1466.20037"))
        reply = ldap._tlv(0x30, ldap._integer(1) + ldap._tlv(
            0x78, ldap._tlv(0x0A, b"\x00") + ldap._tlv(0x04, b"") + ldap._tlv(0x04, b"")))
        self.assertEqual(ldap.parse_extended_response(reply), 0)
        with self.assertRaises(ValueError):
            ldap.parse_extended_response(bind_response(0))

    def test_pin(self):
        import hashlib
        der = b"certificate"
        fp = hashlib.sha256(der).hexdigest().upper()
        self.assertTrue(ldap.pin_matches(der, ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)).lower()))
        self.assertFalse(ldap.pin_matches(der, "AA"))
        self.assertFalse(ldap.pin_matches(der, None))


if __name__ == "__main__":
    unittest.main()
