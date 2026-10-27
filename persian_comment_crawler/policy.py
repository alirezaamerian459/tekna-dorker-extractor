from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeUrl(ValueError):
    pass


def normalize_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise UnsafeUrl("empty URL")
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeUrl("only HTTP and HTTPS are allowed")
    if not parts.hostname:
        raise UnsafeUrl("URL has no hostname")
    if parts.username or parts.password:
        raise UnsafeUrl("embedded credentials are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrl("invalid URL port") from exc
    hostname = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeUrl("local hostnames are not allowed")
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), hostname + (f":{port}" if port else ""), path, parts.query, ""))


def is_private_hostname(hostname: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    except (OSError, socket.gaierror):
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def validate_public_url(raw: str, resolve_dns: bool = True) -> str:
    normalized = normalize_url(raw)
    hostname = urlsplit(normalized).hostname or ""
    if resolve_dns and is_private_hostname(hostname):
        raise UnsafeUrl("private or local network targets are not allowed")
    return normalized
