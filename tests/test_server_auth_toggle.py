"""tests for proxy_guard.server auth toggle behavior"""

# pylint: disable=duplicate-code
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from proxy_guard import server


def _make_mock_writer():
    """create a mock asyncio StreamWriter"""
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()
    return writer


@pytest.mark.asyncio
async def test_handle_client_auth_disabled():
    """test that auth verification is skipped when auth is disabled"""
    reader = AsyncMock()
    writer = _make_mock_writer()

    reader.read.side_effect = [
        b"CONNECT example.com:80 HTTP/1.1\r\n\r\n",
        b"",
    ]

    mock_proxy = MagicMock()
    mock_proxy.host = "1.2.3.4"
    mock_proxy.port = 8080
    mock_proxy.auth = None
    mock_proxy.active_connections = 0
    mock_proxy.total_connections = 0
    mock_proxy.bytes_sent = 0
    mock_proxy.bytes_received = 0
    mock_proxy.record_success = MagicMock()
    mock_proxy.record_failure = MagicMock()

    us_reader = AsyncMock()
    us_writer = _make_mock_writer()

    async def mock_try_upstream(_upstream, _target, _user_agent):
        return us_reader, us_writer

    with (
        patch("proxy_guard.server.MANAGER") as mock_manager,
        patch("proxy_guard.server.ENABLE_AUTH", False),
        patch("proxy_guard.server.verify_signature") as mock_verify,
        patch("proxy_guard.server._try_upstream", side_effect=mock_try_upstream),
        patch("proxy_guard.server.pipe", new_callable=AsyncMock),
    ):
        mock_manager.get_proxy.return_value = mock_proxy
        await server.handle_client(reader, writer)

        mock_verify.assert_not_called()
        mock_manager.get_proxy.assert_called()


@pytest.mark.asyncio
async def test_handle_client_auth_enabled_no_header():
    """test that missing auth header returns 407 when auth is enabled"""
    reader = AsyncMock()
    writer = _make_mock_writer()

    reader.read.side_effect = [
        b"CONNECT example.com:80 HTTP/1.1\r\n\r\n",
        b"",
    ]

    with (
        patch("proxy_guard.server.ENABLE_AUTH", True),
        patch("proxy_guard.server.verify_signature") as mock_verify,
    ):
        await server.handle_client(reader, writer)

        calls = writer.write.call_args_list
        assert any(b"407 Proxy Authentication Required" in c[0][0] for c in calls)
        mock_verify.assert_not_called()
