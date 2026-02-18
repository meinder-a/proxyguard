"""upstream proxy node management, health checking, and load balancing"""

import asyncio
import base64
import os
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp  # pylint: disable=import-error

from .config import HEALTH_CHECK_INTERVAL, HIGH_USAGE_THRESHOLD, MAX_LATENCY, STICKY_TTL
from .core_logging import logger

CIRCUIT_BREAKER_THRESHOLD = 3


@dataclass
class ProxyNode:  # pylint: disable=too-many-instance-attributes
    """represents a single upstream proxy with health and connection state"""

    url: str
    host: str = field(init=False)
    port: int = field(init=False)
    auth: Optional[str] = field(init=False)
    is_healthy: bool = True
    latency: float = -1.0
    last_checked: float = 0.0
    location: str = "Unknown"
    country_code: Optional[str] = None
    exit_ip: Optional[str] = None

    # stats
    active_connections: int = 0
    total_connections: int = 0
    bytes_sent: int = 0  # bytes sent to upstream
    bytes_received: int = 0  # bytes received from upstream

    # circuit breaker
    consecutive_failures: int = 0

    def __post_init__(self):
        try:
            parsed = urlparse(self.url)
            self.host, self.port = parsed.hostname, parsed.port
            if parsed.username:
                self.auth = base64.b64encode(
                    f"{parsed.username}:{parsed.password}".encode()
                ).decode()
            else:
                self.auth = None
        except (ValueError, AttributeError):
            self.is_healthy = False

    def record_success(self):
        """reset consecutive failure count after a successful connection"""
        self.consecutive_failures = 0

    def record_failure(self):
        """increment failure count and trip circuit breaker if threshold reached"""
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self.is_healthy = False
            logger.warning(
                "circuit breaker tripped for %s:%s after %d consecutive failures",
                self.host,
                self.port,
                self.consecutive_failures,
            )


class ProxyManager:
    """manages a pool of upstream proxies with health checking and load balancing"""

    def __init__(self, raw_list: List[str], file_path: Optional[str] = None):
        self.proxies: List[ProxyNode] = []
        self._file_path = file_path
        self._file_mtime: float = 0.0
        self._sticky_map: Dict[str, Tuple[ProxyNode, float]] = {}
        self._env_urls: set = set()

        candidates = []

        # 1. load from raw list (env var)
        for r in raw_list:
            if r.strip():
                candidates.append(r.strip())
                self._env_urls.add(r.strip())

        # 2. load from file
        if file_path:
            file_urls = self._read_file(file_path)
            candidates.extend(file_urls)
            try:
                self._file_mtime = os.path.getmtime(file_path)
            except OSError:
                pass

        # 3. parse candidates (deduplicate by url)
        seen = set()
        for r in candidates:
            if r in seen:
                continue
            seen.add(r)
            try:
                node = ProxyNode(r)
                if node.is_healthy and node.host:
                    self.proxies.append(node)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        logger.info("initialized with %d upstreams", len(self.proxies))

    @staticmethod
    def _read_file(file_path: str) -> List[str]:
        """read proxy urls from a text file, one per line"""
        urls = []
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        urls.append(line)
        except FileNotFoundError:
            logger.debug("proxy file not found: %s", file_path)
        except OSError as exc:
            logger.error("error reading proxy file %s: %s", file_path, exc)
        return urls

    def _reload_file(self):
        """hot-reload proxy list from file if it has been modified"""
        if not self._file_path:
            return
        try:
            mtime = os.path.getmtime(self._file_path)
        except OSError:
            return
        if mtime <= self._file_mtime:
            return

        self._file_mtime = mtime
        new_urls = self._read_file(self._file_path)

        existing_by_url = {p.url: p for p in self.proxies}
        new_proxies = []
        seen = set()

        for url in new_urls:
            if url in seen:
                continue
            seen.add(url)
            if url in existing_by_url:
                new_proxies.append(existing_by_url[url])
            else:
                try:
                    node = ProxyNode(url)
                    if node.is_healthy and node.host:
                        new_proxies.append(node)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

        # keep env-based proxies that aren't in the file
        for p in self.proxies:
            if p.url in self._env_urls and p.url not in seen:
                new_proxies.append(p)
                seen.add(p.url)

        added = len(new_proxies) - len(self.proxies)
        self.proxies = new_proxies
        logger.info(
            "reloaded proxy file: %d upstreams (delta: %+d)",
            len(self.proxies),
            added,
        )

    def get_proxy(
        self,
        active_count: int = 0,
        client_id: Optional[str] = None,
        exclude: Optional[List[ProxyNode]] = None,
    ) -> Optional[ProxyNode]:
        """select the best available upstream proxy for a connection"""
        if not self.proxies:
            return None

        # sticky session lookup
        if STICKY_TTL > 0 and client_id:
            entry = self._sticky_map.get(client_id)
            if entry:
                node, expiry = entry
                if time.time() < expiry and node.is_healthy:
                    return node
                del self._sticky_map[client_id]

        exclude_set = set(id(p) for p in (exclude or []))

        # 1. filter healthy (and not excluded)
        healthy = [
            p
            for p in self.proxies
            if p.is_healthy and p.latency >= 0 and id(p) not in exclude_set
        ]

        if not healthy:
            # fallback to any non-excluded proxy
            pool = [p for p in self.proxies if id(p) not in exclude_set]
            if not pool:
                pool = self.proxies
            chosen = random.choice(pool)
        else:
            # 2. filter by latency
            low_latency = [p for p in healthy if p.latency <= MAX_LATENCY]

            # 3. decision logic
            if active_count >= HIGH_USAGE_THRESHOLD:
                chosen = random.choice(healthy)
            elif low_latency:
                chosen = random.choice(low_latency)
            else:
                chosen = random.choice(healthy)

        # record sticky mapping
        if STICKY_TTL > 0 and client_id:
            self._sticky_map[client_id] = (chosen, time.time() + STICKY_TTL)

        return chosen

    def get_all_proxies(self) -> List[dict]:
        """return serializable status info for all proxies"""
        return [
            {
                "host": p.host,
                "port": p.port,
                "has_auth": bool(p.auth),
                "is_healthy": p.is_healthy,
                "latency": p.latency,
                "last_checked": p.last_checked,
                "location": p.location,
                "country_code": p.country_code,
                "exit_ip": p.exit_ip,
                "active_connections": p.active_connections,
                "total_connections": p.total_connections,
                "bytes_sent": p.bytes_sent,
                "bytes_received": p.bytes_received,
                "consecutive_failures": p.consecutive_failures,
            }
            for p in self.proxies
        ]

    async def check_proxy(self, node: ProxyNode):
        """run a connect health check against a single proxy node"""
        start = time.time()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node.host, node.port),
                timeout=5.0,
            )
            # send a connect to a known target to verify proxy actually works
            req = b"CONNECT httpbin.org:443 HTTP/1.1\r\nHost: httpbin.org:443\r\n"
            if node.auth:
                req += f"Proxy-Authorization: Basic {node.auth}\r\n".encode()
            req += b"\r\n"
            writer.write(req)
            await writer.drain()

            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > 16384:
                    break

            writer.close()
            await writer.wait_closed()

            first_line = response.split(b"\r\n", 1)[0]
            if b"200" in first_line:
                node.latency = (time.time() - start) * 1000
                node.is_healthy = True
                node.consecutive_failures = 0
            else:
                node.is_healthy = False
                node.latency = -1.0
        except Exception:  # pylint: disable=broad-exception-caught
            node.is_healthy = False
            node.latency = -1.0
        finally:
            node.last_checked = time.time()

    async def resolve_location(self, node: ProxyNode):
        """resolve the geographic location and exit ip of a proxy node"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://ip-api.com/json/",
                    proxy=node.url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        cc = data.get("countryCode")
                        node.country_code = cc
                        node.exit_ip = data.get("query")
                        node.location = (
                            f"{data.get('city', 'Unknown')}, {cc or 'Unknown'}"
                        )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _cleanup_sticky(self):
        """remove expired entries from the sticky session map"""
        now = time.time()
        expired = [k for k, (_, exp) in self._sticky_map.items() if now >= exp]
        for k in expired:
            del self._sticky_map[k]

    async def health_check_loop(self):
        """periodically check proxy health and reload proxy file"""
        logger.info("starting health check loop")
        # initial check
        if self.proxies:
            tasks = [self.check_proxy(n) for n in self.proxies]
            for n in self.proxies:
                if n.location == "Unknown":
                    asyncio.create_task(self.resolve_location(n))
            await asyncio.gather(*tasks, return_exceptions=True)

        while True:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            # hot-reload proxy file
            self._reload_file()

            if not self.proxies:
                continue
            tasks = [self.check_proxy(node) for node in self.proxies]
            loc_tasks = [
                self.resolve_location(n)
                for n in self.proxies
                if n.location == "Unknown"
            ]
            if loc_tasks:
                tasks.extend(loc_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

            # cleanup expired sticky entries
            self._cleanup_sticky()
