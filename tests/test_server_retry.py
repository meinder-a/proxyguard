import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import asyncio

from proxy_guard import server


@pytest.mark.asyncio
async def test_handle_client_retries_on_upstream_failure():
    reader = AsyncMock()
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()

    reader.read.side_effect = [
        b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
        b"",
    ]

    proxy1 = MagicMock()
    proxy1.host = "1.2.3.4"
    proxy1.port = 8080
    proxy1.auth = None
    proxy1.active_connections = 0
    proxy1.total_connections = 0
    proxy1.bytes_sent = 0
    proxy1.bytes_received = 0
    proxy1.record_success = MagicMock()
    proxy1.record_failure = MagicMock()

    proxy2 = MagicMock()
    proxy2.host = "5.6.7.8"
    proxy2.port = 8080
    proxy2.auth = None
    proxy2.active_connections = 0
    proxy2.total_connections = 0
    proxy2.bytes_sent = 0
    proxy2.bytes_received = 0
    proxy2.record_success = MagicMock()
    proxy2.record_failure = MagicMock()

    call_count = 0

    def get_proxy_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return proxy1
        return proxy2

    mock_manager = MagicMock()
    mock_manager.get_proxy.side_effect = get_proxy_side_effect

    us_reader_ok = AsyncMock()
    us_writer_ok = MagicMock()
    us_writer_ok.close = MagicMock()
    us_writer_ok.wait_closed = AsyncMock()
    us_writer_ok.drain = AsyncMock()

    attempt = 0

    async def mock_try_upstream(upstream, target, user_agent):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise ConnectionError("Connection refused")
        return us_reader_ok, us_writer_ok

    with patch("proxy_guard.server.MANAGER", mock_manager), \
         patch("proxy_guard.server.ENABLE_AUTH", False), \
         patch("proxy_guard.server._try_upstream", side_effect=mock_try_upstream), \
         patch("proxy_guard.server.pipe", new_callable=AsyncMock):

        await server.handle_client(reader, writer)

    # proxy1 should have had record_failure called
    proxy1.record_failure.assert_called_once()
    # proxy2 should have had record_success called
    proxy2.record_success.assert_called_once()

    # Writer should have received 200 Connection Established
    write_calls = writer.write.call_args_list
    assert any(b"200 Connection Established" in c[0][0] for c in write_calls)


@pytest.mark.asyncio
async def test_handle_client_all_retries_exhausted():
    reader = AsyncMock()
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()

    reader.read.side_effect = [
        b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
        b"",
    ]

    proxy = MagicMock()
    proxy.host = "1.2.3.4"
    proxy.port = 8080
    proxy.auth = None
    proxy.active_connections = 0
    proxy.total_connections = 0
    proxy.record_failure = MagicMock()

    mock_manager = MagicMock()
    mock_manager.get_proxy.return_value = proxy

    async def mock_try_upstream(upstream, target, user_agent):
        raise ConnectionError("Connection refused")

    with patch("proxy_guard.server.MANAGER", mock_manager), \
         patch("proxy_guard.server.ENABLE_AUTH", False), \
         patch("proxy_guard.server._try_upstream", side_effect=mock_try_upstream):

        await server.handle_client(reader, writer)

    # Should have returned 502
    write_calls = writer.write.call_args_list
    assert any(b"502 Bad Gateway" in c[0][0] for c in write_calls)


@pytest.mark.asyncio
async def test_handle_client_timeout_returns_504():
    reader = AsyncMock()
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()

    reader.read.side_effect = [
        b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
        b"",
    ]

    proxy = MagicMock()
    proxy.host = "1.2.3.4"
    proxy.port = 8080
    proxy.auth = None
    proxy.active_connections = 0
    proxy.total_connections = 0
    proxy.record_failure = MagicMock()

    mock_manager = MagicMock()
    mock_manager.get_proxy.return_value = proxy

    async def mock_try_upstream(upstream, target, user_agent):
        raise asyncio.TimeoutError()

    with patch("proxy_guard.server.MANAGER", mock_manager), \
         patch("proxy_guard.server.ENABLE_AUTH", False), \
         patch("proxy_guard.server._try_upstream", side_effect=mock_try_upstream):

        await server.handle_client(reader, writer)

    write_calls = writer.write.call_args_list
    assert any(b"504 Gateway Timeout" in c[0][0] for c in write_calls)
