"""Which client request headers may reach an upstream.

Two rules, one per kind of route.

LLM routes (``/v1/chat/completions`` and the ``/v1/{path}`` catch-all) use an
ALLOWLIST. A header reaches the provider only if it is named below. Anything
else, including every Tiresias header (``x-tiresias-api-key``,
``x-tiresias-session-id``, ``x-soulkey``) and any header a client happens to
send, stays at the proxy. The catch-all used to forward every header except
``host``, ``content-length`` and ``transfer-encoding``, which sent the caller's
Tiresias API key to the upstream provider.

The generic ``/api/{path}`` reverse proxy fronts arbitrary third-party APIs
(Stripe, Twilio, ...), each with its own custom headers, so an allowlist would
break it by design. It uses a STRIP list instead: hop-by-hop headers plus every
header that carries a Tiresias credential.

``Authorization`` is special on both kinds of route. It is how a caller passes
its own provider or API key through the proxy, so it is forwarded, EXCEPT when
it carries a Tiresias credential: a ``Bearer tir_...`` value (the SaaS key
format), or the value the auth middleware actually accepted as the Tiresias
key for this request (recorded on ``request.state``).
"""
from __future__ import annotations

from typing import Mapping

# Headers that authenticate the caller TO TIRESIAS. Never forwarded anywhere.
TIRESIAS_CREDENTIAL_HEADERS = frozenset({
    "x-tiresias-api-key",
    "x-soulkey",
})

# Tiresias-internal request headers that are not credentials but mean nothing
# to an upstream and leak proxy internals.
TIRESIAS_INTERNAL_HEADERS = frozenset({
    "x-tiresias-session-id",
})

# RFC 9110 hop-by-hop headers plus framing httpx recomputes.
HOP_BY_HOP_HEADERS = frozenset({
    "host", "content-length", "connection", "transfer-encoding", "keep-alive",
    "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection",
})

# The only client headers an LLM provider receives.
LLM_FORWARD_ALLOWLIST = frozenset({
    "accept",
    "content-type",
    "user-agent",
    "idempotency-key",
    "x-request-id",
    # OpenRouter attribution
    "http-referer",
    "x-title",
    # OpenAI
    "openai-organization",
    "openai-project",
    "openai-beta",
    # Anthropic
    "anthropic-version",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    # Provider credentials a caller passes through on purpose (legacy
    # single-upstream mode). In multi-provider mode the provider's own
    # credential replaces these, see merge_provider_headers().
    "authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
})

# request.state attribute set by the auth middleware to the lower-cased name of
# the header that carried the accepted Tiresias credential.
CREDENTIAL_HEADER_STATE_ATTR = "tiresias_credential_header"

# request.state attribute holding the accepted credential VALUE. Any header
# that contains it is dropped, whatever its name: a client that sends the key
# in X-Tiresias-Api-Key AND in Authorization must not have the second copy
# forwarded.
CREDENTIAL_VALUE_STATE_ATTR = "tiresias_credential_value"

# Credentials a client may pass through to ITS OWN provider. In multi-provider
# (router) mode the proxy supplies each provider's credential itself, so these
# are dropped: on failover they would otherwise reach a different provider.
CLIENT_PROVIDER_CREDENTIAL_HEADERS = frozenset({
    "authorization", "x-api-key", "api-key", "x-goog-api-key",
})


def _is_tiresias_authorization(value: str, credential_header: str | None) -> bool:
    if credential_header == "authorization":
        return True
    v = value.strip()
    return v.lower().startswith("bearer ") and v[7:].strip().startswith("tir_")


def _credential_header_for(request_state) -> str | None:
    if request_state is None:
        return None
    return getattr(request_state, CREDENTIAL_HEADER_STATE_ATTR, None)


def _credential_value_for(request_state) -> str | None:
    if request_state is None:
        return None
    v = getattr(request_state, CREDENTIAL_VALUE_STATE_ATTR, None)
    return v or None


def _carries(value: str, secret: str | None) -> bool:
    return bool(secret) and secret in value


def strip_client_provider_credentials(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop the client's own provider credentials (router mode)."""
    return {k: v for k, v in headers.items() if k.lower() not in CLIENT_PROVIDER_CREDENTIAL_HEADERS}


def llm_upstream_headers(headers: Mapping[str, str], request_state=None) -> dict[str, str]:
    """Allowlisted subset of ``headers`` to send to an LLM provider."""
    cred_hdr = _credential_header_for(request_state)
    cred_val = _credential_value_for(request_state)
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk not in LLM_FORWARD_ALLOWLIST:
            continue
        if lk == "authorization" and _is_tiresias_authorization(v, cred_hdr):
            continue
        if _carries(v, cred_val):
            continue
        out[lk] = v
    return out


def generic_upstream_headers(headers: Mapping[str, str], request_state=None) -> dict[str, str]:
    """``headers`` minus hop-by-hop and Tiresias headers, for the generic /api proxy."""
    cred_hdr = _credential_header_for(request_state)
    cred_val = _credential_value_for(request_state)
    drop = HOP_BY_HOP_HEADERS | TIRESIAS_CREDENTIAL_HEADERS | TIRESIAS_INTERNAL_HEADERS
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in drop:
            continue
        if lk == "authorization" and _is_tiresias_authorization(v, cred_hdr):
            continue
        if _carries(v, cred_val):
            continue
        out[lk] = v
    return out


def merge_provider_headers(client_headers: Mapping[str, str],
                           provider_headers: Mapping[str, str]) -> dict[str, str]:
    """Provider headers override client headers, compared case-insensitively.

    A plain ``{**client, **provider}`` keeps both ``authorization`` (client,
    lower-cased by Starlette) and ``Authorization`` (provider), and httpx then
    sends BOTH, so the client's credential still reached the provider.
    """
    override = {k.lower() for k in provider_headers}
    merged = {k: v for k, v in client_headers.items() if k.lower() not in override}
    merged.update(provider_headers)
    merged.pop("host", None)
    merged.pop("Host", None)
    return merged
