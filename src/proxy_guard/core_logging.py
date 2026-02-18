"""structured json logging and prometheus-style metrics registry"""

import json
import logging


class JSONFormatter(logging.Formatter):
    """format log records as json objects"""

    def format(self, record):
        log_obj = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "lvl": record.levelname,
            "msg": record.getMessage(),
        }
        props = getattr(record, "props", None)
        if props:
            log_obj["props"] = props
        return json.dumps(log_obj)


def setup_logger(name="ProxyGuard", level=logging.INFO):
    """create and configure a logger with json formatting"""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    log = logging.getLogger(name)
    log.setLevel(level)

    if not log.handlers:
        log.addHandler(handler)

    return log


logger = setup_logger()


def _format_labels(labels):
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


class MetricsRegistry:
    """collects counter and gauge metrics in prometheus exposition format"""

    def __init__(self):
        # {name: {frozen_labels_tuple: value}}
        self._counters = {}
        self._gauges = {}
        self._meta = {}  # {name: ("counter"|"gauge", help_text)}

    def _register(self, name, kind, help_text=""):
        if name not in self._meta:
            self._meta[name] = (kind, help_text)

    def inc(self, name, labels=None, help_text=""):
        """increment a counter by 1"""
        labels = labels or {}
        self._register(name, "counter", help_text)
        key = tuple(sorted(labels.items()))
        bucket = self._counters.setdefault(name, {})
        bucket[key] = bucket.get(key, 0) + 1

    def inc_by(self, name, value, labels=None, help_text=""):
        """increment a counter by an arbitrary value"""
        labels = labels or {}
        self._register(name, "counter", help_text)
        key = tuple(sorted(labels.items()))
        bucket = self._counters.setdefault(name, {})
        bucket[key] = bucket.get(key, 0) + value

    def set_gauge(self, name, value, labels=None, help_text=""):
        """set a gauge to the given value"""
        labels = labels or {}
        self._register(name, "gauge", help_text)
        key = tuple(sorted(labels.items()))
        bucket = self._gauges.setdefault(name, {})
        bucket[key] = value

    def generate_output(self):
        """render all metrics in prometheus exposition format"""
        lines = []
        all_names = set(self._counters.keys()) | set(self._gauges.keys())

        for name in sorted(all_names):
            kind, help_text = self._meta.get(name, ("counter", ""))
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")

            if name in self._counters:
                for label_tuple, value in sorted(self._counters[name].items()):
                    labels_str = _format_labels(dict(label_tuple))
                    lines.append(f"{name}{labels_str} {value}")

            if name in self._gauges:
                for label_tuple, value in sorted(self._gauges[name].items()):
                    labels_str = _format_labels(dict(label_tuple))
                    lines.append(f"{name}{labels_str} {value}")

        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()
