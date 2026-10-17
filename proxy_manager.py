"""SOCKS5 proxy pool with validated automatic failover.

Proxy list format: one IPv4:PORT endpoint per line, e.g. 209.97.150.167:1080.
Blank lines and lines beginning with # are ignored.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import ssl
import time
from pathlib import Path

import aiohttp
import certifi
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiohttp import TCPConnector

log = logging.getLogger("heradeLegend.proxy")

# These methods are safe to retry after a transport failure. Do not retry
# sendMessage/sendPhoto/etc. automatically because Telegram may have accepted
# the request even if the response was lost.
SAFE_RETRY_METHODS = {
    "getUpdates",
    "getMe",
    "editMessageText",
    "editMessageReplyMarkup",
    "editMessageCaption",
    "editMessageMedia",
    "editMessageLiveLocation",
    "stopMessageLiveLocation",
    "deleteMessage",
    "deleteMessages",
    "answerCallbackQuery",
    "getChat",
    "getChatMember",
    "getChatAdministrators",
    "getChatMemberCount",
    "getUserProfilePhotos",
    "getFile",
}

NETWORK_ERROR_NAMES = (
    "proxy", "connection", "timeout", "network", "socket",
    "connector", "serverdisconnected",
)


def _normalise_proxy(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None

    parts = value.split(":")
    if len(parts) != 2:
        return None

    host, port_text = parts
    try:
        host = str(ipaddress.IPv4Address(host))
        port = int(port_text)
    except (ipaddress.AddressValueError, ValueError):
        return None

    if not port_text.isdigit() or not 1 <= port <= 65535:
        return None

    return f"socks5://{host}:{port}"


def load_proxy_candidates() -> list[str | None]:
    candidates: list[str | None] = []

    configured = os.getenv("TELEGRAM_PROXY", "").strip()
    if configured:
        candidate = _normalise_proxy(configured)
        if candidate:
            candidates.append(candidate)
        else:
            log.warning(
                "TELEGRAM_PROXY ignored: expected plain IPv4:PORT format."
            )

    path = Path(os.getenv("TELEGRAM_PROXY_LIST", "proxies.txt"))
    malformed = 0
    try:
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                candidate = _normalise_proxy(stripped)
                if candidate is None:
                    malformed += 1
                elif candidate not in candidates:
                    candidates.append(candidate)
    except OSError as exc:
        log.warning("Could not read proxy list (%s).", type(exc).__name__)

    if malformed:
        log.warning(
            "Skipped %d malformed proxy-list line(s); expected IPv4:PORT only.",
            malformed,
        )

    # Direct connection remains available as a final fallback.
    candidates.append(None)
    return candidates


class RotatingProxySession(AiohttpSession):
    """Probe candidates and switch to a verified route after network failures."""

    def __init__(self, timeout: int = 20) -> None:
        self.candidates = load_proxy_candidates()
        self._proxy_index = 0
        self._rotation_lock = asyncio.Lock()
        self._bot_token: str | None = None
        super().__init__(proxy=self.candidates[0], timeout=timeout)

    @staticmethod
    async def _probe(
        token: str,
        proxy: str | None,
        semaphore: asyncio.Semaphore,
        timeout: int = 6,
    ) -> tuple[str | None, float, bool]:
        async with semaphore:
            probe_session = AiohttpSession(proxy=proxy, timeout=timeout)
            probe_bot = Bot(token, session=probe_session)
            started = time.perf_counter()
            try:
                await probe_bot.get_me(request_timeout=timeout)
                return proxy, (time.perf_counter() - started) * 1000, True
            except Exception:
                return proxy, float("inf"), False
            finally:
                await probe_session.close()

    async def discover_best(self, token: str) -> None:
        self._bot_token = token

        # A huge public proxy list can contain tens of thousands of dead
        # endpoints. Probe only a bounded sample during startup.
        candidates = self.candidates[:100]
        semaphore = asyncio.Semaphore(15)
        log.info("Checking up to %d proxy candidates...", len(candidates))

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
        ordered = [proxy for proxy, _ in working]
        if None not in ordered:
            ordered.append(None)
        ordered.extend(
            proxy for proxy in failed
            if proxy is not None and proxy not in ordered
        )

        if not ordered:
            ordered = [None]

        self.candidates = ordered
        self._proxy_index = 0
        self._apply_route(ordered[0])

        if working:
            log.info(
                "Proxy scan complete: %d working route(s); selected route "
                "latency %d ms.",
                len(working),
                round(working[0][1]),
            )
        else:
            log.warning(
                "No proxy passed the Telegram API probe; direct connection "
                "will be tried first."
            )

    def _apply_route(self, proxy: str | None) -> None:
        """Apply route and force the aiohttp connector to be recreated."""
        if proxy is None:
            self._proxy = None
            self._connector_type = TCPConnector
            self._connector_init = {
                "ssl": ssl.create_default_context(cafile=certifi.where()),
                "limit": 100,
                "ttl_dns_cache": 300,
            }
        else:
            self.proxy = proxy

        self._should_reset_connector = True

    async def _rotate(self) -> bool:
        """Probe a batch concurrently and switch to a route verified by Telegram."""
        async with self._rotation_lock:
            if not self._bot_token:
                log.error("Proxy scan is not initialized with a bot token.")
                return False

            count = len(self.candidates)
            if count <= 1:
                return False

            # Public proxy lists are often mostly dead. Probe the next 20 in
            # parallel instead of spending 2 seconds on each candidate serially.
            batch_size = min(count - 1, 20)
            indexes = [
                (self._proxy_index + step) % count
                for step in range(1, batch_size + 1)
            ]
            semaphore = asyncio.Semaphore(10)
            results = await asyncio.gather(
                *(
                    self._probe(
                        self._bot_token,
                        self.candidates[index],
                        semaphore,
                        timeout=2,
                    )
                    for index in indexes
                ),
                return_exceptions=True,
            )

            verified: list[tuple[int, str | None, float]] = []
            for index, result in zip(indexes, results):
                if isinstance(result, Exception):
                    continue
                proxy, latency, ok = result
                if ok:
                    verified.append((index, proxy, latency))

            if verified:
                # Prefer the fastest verified route; use original pool order as
                # a deterministic tie-breaker.
                index, candidate, latency = min(
                    verified, key=lambda item: (item[2], indexes.index(item[0]))
                )
                self._proxy_index = index
                self._apply_route(candidate)
                log.warning(
                    "Failover selected a verified %s route (Telegram API %.0f ms).",
                    "direct" if candidate is None else "proxy",
                    latency,
                )
                return True

            # No route in this batch reached Telegram. Advance only one slot so
            # subsequent retries do not skip candidates that have not been tried.
            next_index = indexes[0]
            candidate = self.candidates[next_index]
            self._proxy_index = next_index
            self._apply_route(candidate)
            log.warning(
                "No verified route among %d candidates; advancing to the next "
                "candidate and allowing the request retry.",
                batch_size,
            )
            return True

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        return (
            isinstance(
                exc,
                (
                    TelegramNetworkError,
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    OSError,
                ),
            )
            or any(
                marker in type(exc).__name__.lower()
                for marker in NETWORK_ERROR_NAMES
            )
        )

    async def make_request(self, bot, method, timeout=None):
        method_name = getattr(method, "__api_method__", "")
        safe_to_retry = method_name in SAFE_RETRY_METHODS
        attempts = min(max(1, len(self.candidates)), 8) if safe_to_retry else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                return await super().make_request(
                    bot, method, timeout=timeout
                )
            except Exception as exc:
                if not self._is_network_error(exc):
                    raise
                last_error = exc

            log.warning(
                "Network failure on %s (attempt %d/%d); looking for a "
                "working Telegram route.",
                method_name or "unknown request",
                attempt + 1,
                attempts,
            )

            switched = await self._rotate()

            if not safe_to_retry:
                # Avoid duplicate sends/actions when Telegram may have accepted
                # the original request but its response was lost.
                raise last_error

            if not switched:
                # Do not hammer the same failed route if no route is available.
                continue

        if last_error is not None:
            raise last_error

        raise RuntimeError("Proxy failover ended without a request result.")
