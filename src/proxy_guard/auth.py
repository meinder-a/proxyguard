"""hmac-based authentication for proxy connections"""

import base64
import hashlib
import hmac
import time
from typing import Optional, Tuple

from .config import RE_AUTH_HEADER, RE_PROXY_AUTH_HEADER, SERVICE_SECRET
from .core_logging import logger


def parse_auth_header(header_block: bytes) -> Optional[str]:
    """
    extracts the auth string (client:ts:sig) from either x-pg-auth or Proxy-Authorization
    """
    match_auth = RE_AUTH_HEADER.search(header_block)
    if match_auth:
        val = match_auth.group(1).decode().strip()
        logger.debug("Found x-pg-auth header")
        return val

    match_proxy = RE_PROXY_AUTH_HEADER.search(header_block)
    if match_proxy:
        try:
            # decode "Basic <base64>" -> "user:pass"
            # we expect user:pass to be cid:ts:sig
            decoded = base64.b64decode(match_proxy.group(1)).decode()
            logger.debug("Decoded Proxy-Authorization header")
            return decoded
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("Failed to decode Proxy-Authorization: %s", exc)
    return None


def verify_signature(auth_val: str) -> Tuple[bool, Optional[str]]:
    """
    verifies the auth signature
    returns (is_valid, client_id)
    """
    try:
        # we expect cid:ts:sig
        # but if it came from basic auth, it was b64(cid:ts:sig) -> cid:ts:sig
        # standard basic auth is user:pass
        # if we have cid:ts:sig, split(":") gives 3 parts
        parts = auth_val.split(":")
        if len(parts) == 3:
            cid, ts, sig = parts
        elif len(parts) == 2:
            # maybe basic auth was cid:ts:sig encoded such that it's user=cid, pass=ts:sig
            cid = parts[0]
            # pass is ts:sig
            ts_sig = parts[1].split(":", 1)
            if len(ts_sig) == 2:
                ts, sig = ts_sig
            else:
                return False, cid
        else:
            return False, None

        if abs(time.time() - int(ts)) > 300:
            logger.warning(
                "Timestamp expired. Server: %d, Client: %s", int(time.time()), ts
            )
            return False, cid

        expected = hmac.new(
            SERVICE_SECRET, f"{cid}{ts}".encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, sig):
            logger.error("Sig Mismatch. Client: %s, Server Expects: %s", sig, expected)
            return False, cid

        return True, cid
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Auth verification error: %s", exc)
        return False, None
