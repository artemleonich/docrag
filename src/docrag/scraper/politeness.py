"""Вежливый HTTP-клиент для скрейпинга example.com.

Соблюдает robots.txt, выдерживает Crawl-delay, честный User-Agent с контактом,
экспоненциальный backoff на 429/5xx, кэширование скачанного HTML.
"""

from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from docrag.common.logging import get_logger
from docrag.settings import settings

log = get_logger("scraper.politeness")


class RetryableHTTP(Exception):
    """HTTP-ошибка, которую имеет смысл повторить (429/5xx/сетевые сбои)."""


class RobotsChecker:
    """Обёртка над urllib.robotparser с кэшем и Crawl-delay."""

    def __init__(self, base_url: str, user_agent: str):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self._rp = urllib.robotparser.RobotFileParser()
        self._loaded = False
        self.crawl_delay: float | None = None

    def load(self) -> None:
        robots_url = urljoin(self.base_url + "/", "robots.txt")
        try:
            resp = httpx.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=30.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                self._rp.parse(resp.text.splitlines())
                log.info("robots.txt загружен (%d байт)", len(resp.text))
            else:
                log.warning("robots.txt отдал %s — считаем всё разрешённым", resp.status_code)
                self._rp.allow_all = True
        except Exception as e:  # noqa: BLE001
            log.warning("robots.txt недоступен (%s) — считаем всё разрешённым", e)
            self._rp.allow_all = True
        self._loaded = True
        try:
            cd = self._rp.crawl_delay(self.user_agent) or self._rp.crawl_delay("*")
            self.crawl_delay = float(cd) if cd else None
        except Exception:  # noqa: BLE001
            self.crawl_delay = None

    def can_fetch(self, url: str) -> bool:
        if not self._loaded:
            self.load()
        if getattr(self._rp, "allow_all", False):
            return True
        return self._rp.can_fetch(self.user_agent, url)


class PoliteClient:
    """HTTP-клиент с задержкой между запросами, backoff и кэшем HTML."""

    def __init__(
        self,
        base_url: str | None = None,
        user_agent: str | None = None,
        delay: float | None = None,
    ):
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.user_agent = user_agent or settings.scraper_ua
        self.delay = delay if delay is not None else settings.scraper_delay
        self.robots = RobotsChecker(self.base_url, self.user_agent)
        self.robots.load()
        # если сайт задаёт Crawl-delay больше нашего — уважаем его
        if self.robots.crawl_delay and self.robots.crawl_delay > self.delay:
            log.info("Уважаем Crawl-delay сайта: %.1fс", self.robots.crawl_delay)
            self.delay = self.robots.crawl_delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=settings.scraper_timeout,
            follow_redirects=True,
        )
        self._cache_dir = settings.cache_dir / "html"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.time()

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()[:16]
        return self._cache_dir / f"{h}.html"

    @retry(
        retry=retry_if_exception_type(RetryableHTTP),
        stop=stop_after_attempt(settings.scraper_max_retries),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _request(self, method: str, url: str) -> httpx.Response:
        self._throttle()
        try:
            resp = self._client.request(method, url)
        except httpx.HTTPError as e:
            raise RetryableHTTP(f"сетевая ошибка: {e}") from e
        if resp.status_code in (429,) or resp.status_code >= 500:
            raise RetryableHTTP(f"HTTP {resp.status_code} на {url}")
        return resp

    # ------------------------------------------------------------------ #
    def get_html(self, url: str, use_cache: bool = True) -> str | None:
        url = urljoin(self.base_url + "/", url)
        if not is_same_host(url, self.base_url):
            log.warning("Внешний хост запрещён политикой: %s — пропуск", url)
            return None
        if not self.robots.can_fetch(url):
            log.warning("robots.txt запрещает: %s — пропуск", url)
            return None
        cache = self._cache_path(url)
        if use_cache and cache.exists():
            log.info("HTML из кэша: %s", url)
            return cache.read_text(encoding="utf-8", errors="replace")
        resp = self._request("GET", url)
        if resp.status_code != 200:
            log.warning("HTTP %s на %s", resp.status_code, url)
            return None
        cache.write_text(resp.text, encoding="utf-8")
        log.info("Скачан HTML: %s (%d байт)", url, len(resp.text))
        return resp.text

    def download_file(self, url: str, dest: Path, use_cache: bool = True) -> Path | None:
        url = urljoin(self.base_url + "/", url)
        # офлайн-политика: качаем только с целевого хоста, не с внешних/иностранных
        if not is_same_host(url, self.base_url):
            log.warning("Внешний хост запрещён политикой: %s — пропуск", url)
            return None
        if not self.robots.can_fetch(url):
            log.warning("robots.txt запрещает файл: %s — пропуск", url)
            return None
        if use_cache and dest.exists() and dest.stat().st_size > 0:
            log.info("Файл уже скачан: %s", dest.name)
            return dest
        resp = self._request("GET", url)
        if resp.status_code != 200:
            log.warning("HTTP %s при скачивании %s", resp.status_code, url)
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        log.info("Скачан файл: %s (%.1f КБ)", dest.name, len(resp.content) / 1024)
        return dest

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def is_same_host(url: str, base_url: str) -> bool:
    return urlparse(url).netloc in ("", urlparse(base_url).netloc)
