"""SOCKS5/HTTP proxy pool with startup latency ranking and request failover.

Proxy file format: one URL per line, e.g. socks5://user:pass@host:1080.
Plain host:port lines are treated as SOCKS5 endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

log = logging.getLogger("heradeLegend.proxy")
NETWORK_ERROR_NAMES = ("proxy", "connection", "timeout", "network", "socket")


def _normalise_proxy(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None
    if "://" not in value:
        # Accept host:port and host:port:user:password.
        parts = value.split(":")
        if len(parts) == 2:
            value = f"socks5://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:
            host, port, username, password = parts
            value = f"socks5://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
        else:
            log.warning("Skipping malformed proxy line (credentials are not logged).")
            return None
    if not value.lower().startswith(("socks5://", "http://", "https://")):
        log.warning("Skipping unsupported proxy scheme.")
        return None
    return value


def load_proxy_candidates() -> list[str | None]:
    candidates: list[str | None] = []
    configured = os.getenv("TELEGRAM_PROXY", "").strip()
    if configured:
        candidate = _normalise_proxy(configured)
        if candidate:
            candidates.append(candidate)

    path = Path(os.getenv("TELEGRAM_PROXY_LIST", "proxies.txt"))
    try:
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                candidate = _normalise_proxy(line)
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
    except OSError as exc:
        log.warning("Could not read proxy list (%s).", type(exc).__name__)

    # Direct connection is always retained as a final fallback.
    candidates.append(None)
    return candidates


class RotatingProxySession(AiohttpSession):
    """Probe candidates at startup, then rotate after network failures."""

    def __init__(self, timeout: int = 20) -> None:
        self.candidates = load_proxy_candidates()
        self._proxy_index = 0
        self._rotation_lock = asyncio.Lock()
        super().__init__(proxy=self.candidates[0], timeout=timeout)

    @staticmethod
    async def _probe(token: str, proxy: str | None, semaphore: asyncio.Semaphore):
        async with semaphore:
            probe_session = AiohttpSession(proxy=proxy, timeout=7)
            probe_bot = Bot(token, session=probe_session)
            started = time.perf_counter()
            try:
                await probe_bot.get_me(request_timeout=7)
                return proxy, (time.perf_counter() - started) * 1000, True
            except Exception:
                return proxy, float("inf"), False
            finally:
                await probe_session.close()

    async def discover_best(self, token: str) -> None:
        # Avoid hammering Telegram or the host when a very large list is supplied.
        candidates = self.candidates[:100]
        semaphore = asyncio.Semaphore(15)
        results = await asyncio.gather(
            *(self._probe(token, proxy, semaphore) for proxy in candidates),
            return_exceptions=True,
        )
        working: list[tuple[str | None, float]] = []
        failed: list[str | None] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            proxy, latency_ms, ok = result
            if ok:
                working.append((proxy, latency_ms))
            else:
                failed.append(proxy)

        working.sort(key=lambda item: item[1])
        ordered: list[str | None] = [proxy for proxy, _ in working]
        ordered.extend(proxy for proxy in failed if proxy not in ordered)
        if None not in ordered:
            ordered.append(None)
        if not ordered:
            ordered = [None]

        self.candidates = ordered
        self._proxy_index = 0
        self._proxy = ordered[0]
        if working:
            log.info(
                "Proxy scan complete: %s working candidate(s); selected lowest-latency route (%d ms).",
                len(working),
                round(working[0][1]),
            )
        else:
            log.warning("No proxy passed the Telegram API probe; trying direct connection first.")

    async def _rotate(self) -> None:
        async with self._rotation_lock:
            self._proxy_index = (self._proxy_index + 1) % len(self.candidates)
            self._proxy = self.candidates[self._proxy_index]
            selected = self._proxy
            log.warning(
                "Network failure: switching Telegram route to %s.",
                "direct connection" if selected is None else "another configured proxy",
            )

    async def make_request(self, bot, method, timeout=None):
        # Retrying a mutating method can duplicate an action if Telegram accepted
        # it but the response was lost. Rotate for the next request, but retry only
        # safe connection/bootstrap methods here.
        safe_to_retry = getattr(method, "__api_method__", "") in {"getUpdates", "getMe"}
        attempts = len(self.candidates) if safe_to_retry else 1
        last_error = None
        for attempt in range(max(1, attempts)):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except TelegramAPIError as exc:
                if not isinstance(exc, TelegramNetworkError):
                    raise
                last_error = exc
            except Exception as exc:
                name = type(exc).__name__.lower()
                if not any(token in name for token in NETWORK_ERROR_NAMES):
                    raise
                last_error = exc

            await self._rotate()
            if not safe_to_retry:
                raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Proxy rotation ended without a request result.")
