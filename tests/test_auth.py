"""tests for proxy_guard.auth module"""

import base64
import hashlib
import hmac
import time
from unittest.mock import patch

from proxy_guard.auth import parse_auth_header, verify_signature


def _make_signature(cid, ts, secret):
    """create an HMAC signature for testing"""
    return hmac.new(secret, f"{cid}{ts}".encode(), hashlib.sha256).hexdigest()


def test_verify_signature_valid():
    """test that a valid signature is accepted"""
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()))
    sig = _make_signature(cid, ts, secret)
    auth_val = f"{cid}:{ts}:{sig}"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is True
        assert extracted_cid == cid


def test_verify_signature_expired():
    """test that an expired signature is rejected"""
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()) - 600)  # 10 minutes ago, beyond 300s window
    sig = _make_signature(cid, ts, secret)
    auth_val = f"{cid}:{ts}:{sig}"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is False
        assert extracted_cid == cid


def test_verify_signature_wrong_sig():
    """test that a wrong signature is rejected"""
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()))
    auth_val = f"{cid}:{ts}:capitalismsucks"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is False
        assert extracted_cid == cid


def test_verify_signature_malformed():
    """test that a malformed auth value is rejected"""
    with patch("proxy_guard.auth.SERVICE_SECRET", b"test-secret"):
        is_valid, _ = verify_signature("garbage")
        assert is_valid is False


def test_parse_auth_header_pg_auth():
    """test parsing x-pg-auth header"""
    header = (
        b"CONNECT example.com:443 HTTP/1.1\r\nx-pg-auth: client1:12345:abcdef\r\n\r\n"
    )
    result = parse_auth_header(header)
    assert result == "client1:12345:abcdef"


def test_parse_auth_header_proxy_authorization():
    """test parsing Proxy-Authorization header"""
    creds = base64.b64encode(b"client1:12345:abcdef").decode()
    header = (
        f"CONNECT example.com:443 HTTP/1.1\r\n"
        f"Proxy-Authorization: Basic {creds}\r\n\r\n"
    ).encode()
    result = parse_auth_header(header)
    assert result == "client1:12345:abcdef"


def test_parse_auth_header_missing():
    """test that missing auth header returns None"""
    header = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
    result = parse_auth_header(header)
    assert result is None
