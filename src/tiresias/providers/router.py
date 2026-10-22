from __future__ import annotations

import logging
from typing import Callable

import httpx

from .base import BaseProvider
from .health import HealthTracker
from tiresias.proxy.headers import merge_provider_headers

logger = logging.getLogger(__name__)

_PROVIDER_TIMEOUT = 120.0   # seconds per provider attempt


class ProviderCascadeExhausted(Exception):
    """Raised when all providers in the cascade have failed."""


class ProviderRouter:
    """Routes requests through a cascade of providers with automatic failover."""

    def __init__(
        self,
        cascade: list[str],
        health: HealthTracker,
        builder: Callable[[str], BaseProvider],
        http_client: httpx.AsyncClient,
    ) -> None:
        self._cascade = cascade
        self._health = health
        self._builder = builder
        self._http_client = http_client

    def _detect_provider_from_model(self, model: str) -> str | None:
        """If the model name has a provider prefix (e.g. ``ollama/llama3.1:8b``),
        return the provider name when it is in our cascade. Otherwise None."""
        if "/" in model:
            prefix = model.split("/", 1)[0].lower()
            if prefix in self._cascade:
                return prefix
        return None

    async def execute(
        self,
        request_body: dict,
        extra_headers: dict,
    ) -> tuple[dict, str]:
        """Try providers in cascade order. Returns (normalized_response, provider_name).

        If the model name contains a provider prefix that matches a configured
        provider (e.g. ``ollama/llama3.1:8b``), that provider is tried first and
        exclusively -- no cascade fallback to unrelated providers.

        Raises:
            ProviderCascadeExhausted: if every provider in the cascade returns 5xx / times out.
        """
        model = request_body.get("model", "")
        pinned = self._detect_provider_from_model(model)
        ordered = [pinned] if pinned else self._health.get_ordered_providers()
        last_exc: Exception | None = None

        for provider_name in ordered:
            provider = self._builder(provider_name)
            try:
                url, provider_headers, provider_body = provider.format_request(request_body)
                # Case-insensitive: a plain dict merge kept the client's
                # lower-cased "authorization" next to the provider's
                # "Authorization" and httpx sent both. Also drops host.
                merged_headers = merge_provider_headers(extra_headers, provider_headers)

                resp = await self._http_client.post(
                    url,
                    headers=merged_headers,
                    json=provider_body,
                    timeout=_PROVIDER_TIMEOUT,
                )
            except httpx.TimeoutException as exc:
                logger.warning("Provider %s timed out: %s", provider_name, exc)
                self._health.record_error(provider_name)
                last_exc = exc
                continue
            except httpx.RequestError as exc:
                logger.warning("Provider %s request error: %s", provider_name, exc)
                self._health.record_error(provider_name)
                last_exc = exc
                continue

            if provider.is_error(resp.status_code):
                logger.warning(
                    "Provider %s returned %s, trying next", provider_name, resp.status_code
                )
                self._health.record_error(provider_name)
                last_exc = RuntimeError(
                    f"Provider {provider_name} returned HTTP {resp.status_code}"
                )
                continue

            if provider.is_client_error(resp.status_code):
                # Client errors (4xx except 429) should not cascade -- the
                # request itself is invalid (e.g. deprecated model, bad params).
                # Surface the upstream error instead of silently returning empty.
                try:
                    error_body = resp.json()
                except Exception:
                    error_body = {"error": resp.text}
                error_detail = error_body.get("error", {})
                if isinstance(error_detail, dict):
                    msg = error_detail.get("message", str(error_detail))
                else:
                    msg = str(error_detail)
                logger.warning(
                    "Provider %s returned client error %s: %s",
                    provider_name, resp.status_code, msg,
                )
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Provider {provider_name}: {msg}",
                )

            # Success
            self._health.record_success(provider_name)
            try:
                response_json = resp.json()
            except Exception:
                response_json = {}
            normalized = provider.parse_response(response_json)
            return normalized, provider_name

        raise ProviderCascadeExhausted(
            f"All providers exhausted. Last error: {last_exc}"
        )

