import time
import hmac
import hashlib
import base64
import pytest
from unittest.mock import patch


def _make_signature(cid, ts, secret):
    return hmac.new(secret, f"{cid}{ts}".encode(), hashlib.sha256).hexdigest()


def test_verify_signature_valid():
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()))
    sig = _make_signature(cid, ts, secret)
    auth_val = f"{cid}:{ts}:{sig}"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        from proxy_guard.auth import verify_signature
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is True
        assert extracted_cid == cid


def test_verify_signature_expired():
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()) - 600)  # 10 minutes ago, beyond 300s window
    sig = _make_signature(cid, ts, secret)
    auth_val = f"{cid}:{ts}:{sig}"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        from proxy_guard.auth import verify_signature
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is False
        assert extracted_cid == cid


def test_verify_signature_wrong_sig():
    secret = b"test-secret"
    cid = "client-1"
    ts = str(int(time.time()))
    auth_val = f"{cid}:{ts}:deadbeefdeadbeef"

    with patch("proxy_guard.auth.SERVICE_SECRET", secret):
        from proxy_guard.auth import verify_signature
        is_valid, extracted_cid = verify_signature(auth_val)
        assert is_valid is False
        assert extracted_cid == cid


def test_verify_signature_malformed():
    with patch("proxy_guard.auth.SERVICE_SECRET", b"test-secret"):
        from proxy_guard.auth import verify_signature
        is_valid, extracted_cid = verify_signature("garbage")
        assert is_valid is False


def test_parse_auth_header_pg_auth():
    from proxy_guard.auth import parse_auth_header
    header = b"CONNECT example.com:443 HTTP/1.1\r\nx-pg-auth: client1:12345:abcdef\r\n\r\n"
    result = parse_auth_header(header)
    assert result == "client1:12345:abcdef"


def test_parse_auth_header_proxy_authorization():
    from proxy_guard.auth import parse_auth_header
    creds = base64.b64encode(b"client1:12345:abcdef").decode()
    header = f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {creds}\r\n\r\n".encode()
    result = parse_auth_header(header)
    assert result == "client1:12345:abcdef"


def test_parse_auth_header_missing():
    from proxy_guard.auth import parse_auth_header
    header = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
    result = parse_auth_header(header)
    assert result is None
