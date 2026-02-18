"""async connect proxy server with authentication, retry logic, and metrics"""

import asyncio
import pathlib
import re
import signal
import socket

from aiohttp import web  # pylint: disable=import-error

from .config import (
    PROXY_PORT,
    METRICS_PORT,
    CONNECT_TIMEOUT,
    BUFFER_SIZE,
    LOG_SAMPLE_RATE,
    UPSTREAM_LIST,
    UPSTREAM_FILE,
    ENABLE_AUTH,
    RE_REQUEST_LINE,
)
from .core_logging import logger, metrics
from .upstream import ProxyManager
from .auth import parse_auth_header, verify_signature

# regex to extract user-agent from client connect headers
RE_USER_AGENT = re.compile(rb"(?i)User-Agent:\s*([^\r\n]+)")

MAX_RETRIES = 3

# global manager instance (set in start())
MANAGER = None

# deterministic sampling counter (replaces random)
_LOG_COUNTER = 0

ACTIVE_CONNECTIONS = 0


def set_fast_socket(writer):
    """enable tcp_nodelay and tcp_keepalive on the underlying socket"""
    sock = writer.get_extra_info("socket")
    if sock:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass


async def pipe(reader, writer, on_data=None):
    """
    bidirectional data pump
    reads from reader and writes to writer until eof or error
    """
    try:
        while not reader.at_eof():
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            if on_data:
                on_data(len(data))
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionError):
        pass
    except OSError:
        logger.debug("pipe error", exc_info=True)
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def _try_upstream(upstream, target, user_agent):
    """
    attempt to connect through an upstream proxy
    returns (us_reader, us_writer) on success, raises on failure
    """
    us_reader, us_writer = await asyncio.wait_for(
        asyncio.open_connection(upstream.host, upstream.port),
        timeout=CONNECT_TIMEOUT,
    )
    set_fast_socket(us_writer)

    req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
    if user_agent:
        req += f"User-Agent: {user_agent}\r\n"
    req += "Proxy-Connection: Keep-Alive\r\n"
    if upstream.auth:
        req += f"Proxy-Authorization: Basic {upstream.auth}\r\n"
    req += "\r\n"

    us_writer.write(req.encode())
    await us_writer.drain()

    response_buffer = bytearray()
    while b"\r\n\r\n" not in response_buffer:
        chunk = await asyncio.wait_for(
            us_reader.read(4096),
            timeout=CONNECT_TIMEOUT,
        )
        if not chunk:
            raise ConnectionError("upstream closed connection")
        response_buffer.extend(chunk)
        if len(response_buffer) > 16384:
            raise ConnectionError("upstream response headers too large")

    first_line = response_buffer.split(b"\r\n", 1)[0]
    if b"200" not in first_line:
        us_writer.close()
        raise ConnectionError(f"upstream refused: {first_line.decode(errors='ignore')}")

    return us_reader, us_writer


# pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-return-statements
async def handle_client(reader, writer):
    """
    handle a single client connection
    performs authentication, connects to an upstream proxy (with retries)
    and establishes a tunnel
    """
    global ACTIVE_CONNECTIONS, _LOG_COUNTER  # pylint: disable=global-statement
    ACTIVE_CONNECTIONS += 1
    metrics.set_gauge(
        "pg_active_connections",
        ACTIVE_CONNECTIONS,
        help_text="current active client connections",
    )

    set_fast_socket(writer)
    cid = "unknown"
    us_writer = None
    upstream = None

    try:
        # ---------- 1. read client headers ----------
        header_buffer = bytearray()
        while b"\r\n\r\n" not in header_buffer:
            chunk = await reader.read(8192)
            if not chunk:
                return
            header_buffer.extend(chunk)
            if len(header_buffer) > 16384:
                logger.warning("client header too large")
                writer.write(b"HTTP/1.1 413 Payload Too Large\r\n\r\n")
                await writer.drain()
                return

        header_block = bytes(header_buffer)

        # ---------- 2. parse request line ----------
        match_req = RE_REQUEST_LINE.search(header_block)
        if not match_req:
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await writer.drain()
            return

        target = match_req.group(1).decode()

        # ---------- 2b. extract client user-agent ----------
        ua_match = RE_USER_AGENT.search(header_block)
        user_agent = ua_match.group(1).decode().strip() if ua_match else None

        # ---------- 3. authentication ----------
        if ENABLE_AUTH:
            auth_val = parse_auth_header(header_block)
            if not auth_val:
                logger.warning("auth header missing")
                writer.write(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="ProxyGuard"\r\n\r\n'
                )
                await writer.drain()
                return

            is_valid, extracted_cid = verify_signature(auth_val)
            if extracted_cid:
                cid = extracted_cid

            if not is_valid:
                writer.write(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="ProxyGuard"\r\n\r\n'
                )
                await writer.drain()
                return

        # ---------- 4-6. get upstream proxy with retry ----------
        tried = []
        last_error = None
        us_reader = None

        for attempt in range(MAX_RETRIES):
            upstream = MANAGER.get_proxy(
                active_count=ACTIVE_CONNECTIONS,
                client_id=cid,
                exclude=tried,
            )
            if not upstream:
                break

            upstream.active_connections += 1
            upstream.total_connections += 1

            try:
                us_reader, us_writer = await _try_upstream(upstream, target, user_agent)
                upstream.record_success()
                break  # success
            except asyncio.TimeoutError:
                last_error = "timeout"
                logger.error(
                    "timeout connecting to upstream %s (attempt %d/%d)",
                    upstream.host,
                    attempt + 1,
                    MAX_RETRIES,
                )
                upstream.active_connections -= 1
                upstream.record_failure()
                metrics.inc("pg_upstream_failures_total", {"proxy": upstream.host})
                tried.append(upstream)
                upstream = None
                us_writer = None
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = str(exc)
                logger.error(
                    "upstream connection failed: %s (attempt %d/%d)",
                    exc,
                    attempt + 1,
                    MAX_RETRIES,
                )
                upstream.active_connections -= 1
                upstream.record_failure()
                metrics.inc("pg_upstream_failures_total", {"proxy": upstream.host})
                tried.append(upstream)
                upstream = None
                us_writer = None
        else:
            # all retries exhausted
            if last_error == "timeout":
                writer.write(b"HTTP/1.1 504 Gateway Timeout\r\n\r\n")
            else:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        if not upstream:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            await writer.drain()
            return

        # ---------- 7. confirm tunnel to client ----------
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # ---------- 8. metrics and sample logging ----------
        metrics.inc("pg_tunnels", {"client": cid})
        _LOG_COUNTER = (_LOG_COUNTER + 1) % LOG_SAMPLE_RATE
        if _LOG_COUNTER == 0:
            logger.info(
                "tunnel sample",
                extra={"props": {"client": cid, "dst": target, "proxy": upstream.host}},
            )

        # ---------- 9. start bidirectional pumping ----------
        def update_up(n):
            upstream.bytes_sent += n
            metrics.inc_by("pg_bytes_total", n, {"direction": "up"})

        def update_down(n):
            upstream.bytes_received += n
            metrics.inc_by("pg_bytes_total", n, {"direction": "down"})

        t1 = asyncio.create_task(pipe(reader, us_writer, update_up))
        t2 = asyncio.create_task(pipe(us_reader, writer, update_down))
        _done, pending = await asyncio.wait(
            [t1, t2],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)

    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("unhandled exception in handle_client", exc_info=True)
    finally:
        ACTIVE_CONNECTIONS -= 1
        metrics.set_gauge("pg_active_connections", ACTIVE_CONNECTIONS)
        if upstream:
            upstream.active_connections -= 1
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass
        if us_writer:
            try:
                us_writer.close()
                await us_writer.wait_closed()
            except OSError:
                pass


async def start():
    """start the metrics server and the main proxy server"""
    global MANAGER  # pylint: disable=global-statement
    MANAGER = ProxyManager(UPSTREAM_LIST, UPSTREAM_FILE)

    # start health check loop in background
    asyncio.create_task(MANAGER.health_check_loop())

    # metrics endpoint
    app = web.Application()
    app.router.add_get(
        "/metrics", lambda r: web.Response(text=metrics.generate_output())
    )

    async def get_proxies(_request):
        data = []
        if MANAGER:
            data = MANAGER.get_all_proxies()
        return web.json_response(data)

    app.router.add_get("/api/proxies", get_proxies)

    async def get_status(_request):
        return web.json_response(
            {
                "auth_enabled": ENABLE_AUTH,
                "active_connections": ACTIVE_CONNECTIONS,
            }
        )

    app.router.add_get("/api/status", get_status)

    async def dashboard(_request):
        html_path = pathlib.Path(__file__).parent / "dashboard.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(text="dashboard not found", status=404)

    app.router.add_get("/dashboard", dashboard)
    app.router.add_get("/", lambda r: web.HTTPFound("/dashboard"))

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", METRICS_PORT).start()

    # main proxy server
    server = await asyncio.start_server(
        handle_client, "0.0.0.0", PROXY_PORT, backlog=4096
    )

    # graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("shutdown signal received, draining connections...")
        server.close()
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    print(f"ProxyGuard | Port {PROXY_PORT}")

    async with server:
        await shutdown_event.wait()

    # wait for server to close (drains existing connections)
    await server.wait_closed()
    # give in-flight tunnels a moment to finish
    logger.info("waiting for active connections to drain...")
    for _ in range(30):  # up to 30 seconds
        if ACTIVE_CONNECTIONS <= 0:
            break
        await asyncio.sleep(1)

    await runner.cleanup()
    logger.info("shutdown complete")
