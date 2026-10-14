"""SOCKS5 proxy pool using one IPv4:port endpoint per line.

Example proxies.txt line: 209.97.150.167:1080
Blank lines and lines beginning with # are ignored. Credentials and URL
schemes are intentionally not supported by the proxy-list parser.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import ssl
import time
from pathlib import Path

from aiogram import Bot
import certifi
from aiohttp import TCPConnector
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

log = logging.getLogger("heradeLegend.proxy")
NETWORK_ERROR_NAMES = ("proxy", "connection", "timeout", "network", "socket")


def _normalise_proxy(line: str) -> str | None:
    """Convert a plain IPv4:port line into an aiogram SOCKS5 proxy URL."""
    value = line.strip()
    if not value or value.startswith("#"):
        return None

    # The agreed list format is strictly IPv4:PORT; no credentials or schemes.
    parts = value.split(":")
    if len(parts) != 2:
        return None
    host, port_text = parts
    try:
        host = str(ipaddress.IPv4Address(host))
        port = int(port_text)
    except (ipaddress.AddressValueError, ValueError):
        return None
    if not 1 <= port <= 65535 or not port_text.isdigit():
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
            log.warning("TELEGRAM_PROXY ignored: expected plain IPv4:PORT format.")

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

    # Keep direct connection available and avoid duplicate entries.
    candidates.append(None)
    return candidates


class RotatingProxySession(AiohttpSession):
    """Probe a bounded proxy sample at startup and rotate after network failures."""

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
        # Limit startup probes to avoid hammering Telegram and the hosting network.
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
        # Direct connection is preferred if none of the probed proxies works.
        if not working:
            ordered.append(None)
        elif None not in ordered:
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
                "Proxy scan complete: %s working candidate(s); selected lowest-latency route (%d ms).",
                len(working),
                round(working[0][1]),
            )
        else:
            log.warning(
                "No proxy passed the Telegram API probe; trying direct connection first, then failed candidates."
            )

    def _apply_route(self, proxy: str | None) -> None:
        # AiohttpSession caches connector settings; reset them when switching to direct.
        if proxy is None:
            self._connector_type = TCPConnector
            self._connector_init = {
                "ssl": ssl.create_default_context(cafile=certifi.where()),
                "limit": 100,
                "ttl_dns_cache": 3600,
            }
            self._proxy = None
            self._should_reset_connector = True
        else:
            self.proxy = proxy

    async def _rotate(self) -> None:
        async with self._rotation_lock:
            self._proxy_index = (self._proxy_index + 1) % len(self.candidates)
            selected = self.candidates[self._proxy_index]
            self._apply_route(selected)
            log.warning(
                "Network failure: switching Telegram route to %s.",
                "direct connection" if selected is None else "another configured proxy",
            )

    async def make_request(self, bot, method, timeout=None):
        # Retrying mutating methods may duplicate actions if Telegram accepted them
        # but the response was lost. Retry only safe connection/bootstrap methods.
        safe_to_retry = getattr(method, "__api_method__", "") in {"getUpdates", "getMe"}
        attempts = len(self.candidates) if safe_to_retry else 1
        last_error = None
        for _ in range(max(1, attempts)):
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
