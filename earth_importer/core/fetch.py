"""Telechargement : nouvelles tentatives, limite de debit, cache disque, annulation."""
import hashlib
import threading
import time
from pathlib import Path

import requests


class Cancelled(Exception):
    pass


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, cfg, cache_dir=None, cancel=None, log=None):
        self.base = cfg["base_url"]
        self.timeout = cfg.get("timeout", 30)
        self.max_retries = cfg.get("max_retries", 5)
        self.min_interval = cfg.get("min_interval", 0.0)
        self.cache_dir = Path(cache_dir) if cache_dir and cfg.get("cache", True) else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cancel = cancel or threading.Event()
        self.log = log or (lambda level, msg: None)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.get("user_agent", "earth-importer")
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self.downloaded_bytes = 0

    def _throttle(self):
        with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            time.sleep(wait)

    def _cache_path(self, resource):
        return self.cache_dir / (hashlib.sha1(resource.encode()).hexdigest() + ".bin")

    def get(self, resource):
        if self.cache_dir:
            p = self._cache_path(resource)
            if p.exists():
                return p.read_bytes()
        url = self.base + resource
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            if self.cancel.is_set():
                raise Cancelled()
            self._throttle()
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                err = f"reseau : {e.__class__.__name__}"
            else:
                if r.status_code == 200:
                    data = r.content
                    self.downloaded_bytes += len(data)
                    if self.cache_dir:
                        self._cache_path(resource).write_bytes(data)
                    return data
                if r.status_code in (400, 403, 404):
                    raise FetchError(f"HTTP {r.status_code} sur {resource}")
                err = f"HTTP {r.status_code}"
            if attempt == self.max_retries:
                raise FetchError(f"{err} sur {resource} apres {attempt} essais")
            self.log("WARN", f"{err} sur {resource.split('/')[0]}, nouvel essai dans {backoff:.0f} s")
            if self.cancel.wait(backoff):
                raise Cancelled()
            backoff = min(backoff * 2, 16)
