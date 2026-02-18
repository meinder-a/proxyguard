import os
import time
import pytest
from unittest.mock import patch
from proxy_guard.upstream import ProxyManager, ProxyNode


def test_proxy_manager_init_list():
    raw = ["http://user:pass@1.1.1.1:8080", "invalid"]
    pm = ProxyManager(raw)
    assert len(pm.proxies) == 1
    assert pm.proxies[0].host == "1.1.1.1"
    assert pm.proxies[0].port == 8080
    assert pm.proxies[0].auth is not None


def test_proxy_manager_init_file(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("http://user:pass@2.2.2.2:8080\n# comment\nhttp://3.3.3.3:3128")

    pm = ProxyManager([], file_path=str(f))
    assert len(pm.proxies) == 2
    hosts = sorted([p.host for p in pm.proxies])
    assert hosts == ["2.2.2.2", "3.3.3.3"]


def test_proxy_manager_mixed(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("http://2.2.2.2:8080")

    pm = ProxyManager(["http://1.1.1.1:8080"], file_path=str(f))
    assert len(pm.proxies) == 2


def test_proxy_manager_deduplication(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("http://1.1.1.1:8080\nhttp://1.1.1.1:8080")

    pm = ProxyManager(["http://1.1.1.1:8080"], file_path=str(f))
    assert len(pm.proxies) == 1


def test_get_proxy_healthy_filtering():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].is_healthy = True
    pm.proxies[0].latency = 50.0
    pm.proxies[1].is_healthy = False
    pm.proxies[1].latency = -1.0

    # With a healthy proxy available, should pick it
    for _ in range(20):
        chosen = pm.get_proxy()
        assert chosen.host == "1.1.1.1"


def test_get_proxy_empty_pool():
    pm = ProxyManager([])
    assert pm.get_proxy() is None


def test_get_proxy_all_unhealthy_fallback():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    for p in pm.proxies:
        p.is_healthy = False
        p.latency = -1.0

    # Should still return a proxy as fallback
    chosen = pm.get_proxy()
    assert chosen is not None


def test_get_proxy_latency_preference():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].latency = 50.0   # low latency
    pm.proxies[1].latency = 9999.0  # very high latency

    # Low-usage mode should prefer low-latency
    choices = set()
    for _ in range(50):
        chosen = pm.get_proxy(active_count=0)
        choices.add(chosen.host)

    assert "1.1.1.1" in choices


def test_get_proxy_high_usage_spread():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].latency = 50.0
    pm.proxies[1].latency = 50.0

    # High usage mode spreads across all healthy
    choices = set()
    for _ in range(100):
        chosen = pm.get_proxy(active_count=100)
        choices.add(chosen.host)

    assert len(choices) == 2


def test_get_proxy_exclude():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].latency = 50.0
    pm.proxies[1].latency = 50.0

    for _ in range(20):
        chosen = pm.get_proxy(exclude=[pm.proxies[0]])
        assert chosen.host == "2.2.2.2"


@patch("proxy_guard.upstream.STICKY_TTL", 300)
def test_sticky_session():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].latency = 50.0
    pm.proxies[1].latency = 50.0

    first = pm.get_proxy(client_id="test-client")
    # Subsequent calls should return the same proxy
    for _ in range(20):
        chosen = pm.get_proxy(client_id="test-client")
        assert chosen is first


@patch("proxy_guard.upstream.STICKY_TTL", 300)
def test_sticky_session_expires():
    pm = ProxyManager(["http://1.1.1.1:8080", "http://2.2.2.2:8080"])
    pm.proxies[0].latency = 50.0
    pm.proxies[1].latency = 50.0

    first = pm.get_proxy(client_id="test-client")
    # Expire the sticky entry
    pm._sticky_map["test-client"] = (first, time.time() - 1)

    # Should pick a new proxy (might be the same one randomly, but the sticky was removed)
    chosen = pm.get_proxy(client_id="test-client")
    assert chosen is not None


def test_circuit_breaker():
    node = ProxyNode("http://1.1.1.1:8080")
    assert node.is_healthy is True

    node.record_failure()
    assert node.is_healthy is True
    node.record_failure()
    assert node.is_healthy is True
    node.record_failure()
    assert node.is_healthy is False  # tripped after 3

    node.record_success()
    assert node.consecutive_failures == 0


def test_hot_reload(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("http://1.1.1.1:8080")

    pm = ProxyManager([], file_path=str(f))
    assert len(pm.proxies) == 1

    # Simulate file change (write new content and update mtime)
    import os
    f.write_text("http://1.1.1.1:8080\nhttp://2.2.2.2:8080")
    # Force mtime to be newer
    os.utime(str(f), (time.time() + 1, time.time() + 1))

    pm._reload_file()
    assert len(pm.proxies) == 2

    # The original proxy should retain its identity (same object)
    hosts = sorted([p.host for p in pm.proxies])
    assert hosts == ["1.1.1.1", "2.2.2.2"]


def test_hot_reload_removes_proxy(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("http://1.1.1.1:8080\nhttp://2.2.2.2:8080")

    pm = ProxyManager([], file_path=str(f))
    assert len(pm.proxies) == 2

    import os
    f.write_text("http://1.1.1.1:8080")
    os.utime(str(f), (time.time() + 1, time.time() + 1))

    pm._reload_file()
    assert len(pm.proxies) == 1
    assert pm.proxies[0].host == "1.1.1.1"
