"""Downloads a URL for the docs-link and GitHub connectors, safely:
- only http and https;
- every address the host resolves to (and every redirect hop) must be public, so a link can never
  make the api read internal services such as ollama:11434, the database host or 169.254.169.254;
- at most MAX_REDIRECTS redirects and max_bytes of body.
ALLOW_PRIVATE_URLS=true turns the address check off (tests and local experiments only)."""

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS = 5
USER_AGENT = "BharatAgentStudio/0.1 (+document connector)"


class FetchError(ValueError):
    pass


class BlockedURL(FetchError):
    pass


@dataclass
class Fetched:
    url: str            # final URL after redirects
    content_type: str
    body: bytes


def _allow_private() -> bool:
    return os.getenv("ALLOW_PRIVATE_URLS", "").lower() in ("1", "true", "yes")


def check_url(url: str) -> None:
    """Raise BlockedURL unless the URL is http(s) and every address of its host is public."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BlockedURL(f"Only http and https links are supported, not '{url}'.")
    if _allow_private():
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"Cannot find the host '{parsed.hostname}'.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise BlockedURL(f"'{parsed.hostname}' points to a private or internal address ({ip}); "
                             "only public links can be ingested.")


def fetch(url: str, max_bytes: int, headers: dict | None = None,
          transport: httpx.BaseTransport | None = None) -> Fetched:
    """GET the URL, following redirects one hop at a time so each hop is checked.
    transport lets tests replace the network."""
    current = url
    with httpx.Client(follow_redirects=False, timeout=30, transport=transport,
                      headers={"User-Agent": USER_AGENT, **(headers or {})}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            try:
                with client.stream("GET", current) as r:
                    if r.is_redirect:
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise FetchError(f"{current} returned HTTP {r.status_code}.")
                    body = bytearray()
                    for part in r.iter_bytes():
                        body += part
                        if len(body) > max_bytes:
                            raise FetchError(f"{current} is larger than {max_bytes // (1024 * 1024)} MB.")
                    return Fetched(str(r.url), r.headers.get("content-type", "").split(";")[0].strip().lower(),
                                   bytes(body))
            except httpx.HTTPError as exc:
                raise FetchError(f"Could not download {current}: {type(exc).__name__}: {exc}") from exc
    raise FetchError(f"{url} redirected more than {MAX_REDIRECTS} times.")
