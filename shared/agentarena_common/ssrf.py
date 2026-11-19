"""Server-side request forgery guard for customer-supplied agent endpoints.

AgentArena calls URLs that customers register, so a hostile URL could otherwise
reach cloud metadata services or internal APIs. Hosts must resolve to public
addresses unless they are explicitly allow-listed.

The check runs at registration and again immediately before every call, which
narrows DNS rebinding but cannot remove it entirely. The Kubernetes egress
NetworkPolicy on workers is the second layer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit


class UnsafeEndpointError(ValueError):
    """The URL must not be called."""


def parse_allowlist(raw: str) -> frozenset[str]:
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def check_url_shape(url: str, *, allow_http: bool) -> str:
    """Validate the URL itself and return its lower-cased hostname."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeEndpointError("Endpoint must use http or https")
    if parts.scheme == "http" and not allow_http:
        raise UnsafeEndpointError("Endpoint must use https")
    if parts.username or parts.password:
        raise UnsafeEndpointError("Endpoint must not contain credentials")
    if not parts.hostname:
        raise UnsafeEndpointError("Endpoint must include a host")
    return parts.hostname.lower()


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


async def resolve_host(host: str, port: int | None) -> Iterable[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return {str(info[4][0]) for info in infos}


async def assert_safe_endpoint(url: str, *, allowlist: frozenset[str], allow_http: bool) -> None:
    host = check_url_shape(url, allow_http=allow_http)
    if host in allowlist:
        return
    try:
        addresses = await resolve_host(host, urlsplit(url).port)
    except OSError as exc:
        raise UnsafeEndpointError(f"Could not resolve host {host}") from exc
    for address in addresses:
        if not is_public_address(address):
            raise UnsafeEndpointError(f"Host {host} resolves to a non-public address")
