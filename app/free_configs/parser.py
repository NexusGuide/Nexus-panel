"""Parsing helpers for raw proxy URIs harvested from public config lists.

Pure functions only - no I/O - so they are cheap to unit test.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

SUPPORTED_SCHEMES = (
    "vmess://",
    "vless://",
    "trojan://",
    "ss://",
    "hysteria://",
    "hysteria2://",
    "hy2://",
    "tuic://",
    "wireguard://",
)


@dataclass(frozen=True)
class ParsedConfig:
    uri: str
    protocol: str
    address: str
    port: int

    @property
    def uri_hash(self) -> str:
        return hashlib.sha256(self.uri.encode("utf-8")).hexdigest()


def _b64decode(data: str) -> str:
    """Decode base64 that may be url-safe and/or missing its padding."""
    data = data.strip().replace("-", "+").replace("_", "/")
    data += "=" * (-len(data) % 4)
    return base64.b64decode(data).decode("utf-8", errors="ignore")


def decode_body(text: str, is_base64: bool) -> list[str]:
    """Turn a source's response body into a list of candidate URI lines."""
    text = text.strip()
    if not text:
        return []

    if is_base64:
        try:
            text = _b64decode(text)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return []

    return [line.strip() for line in text.splitlines() if line.strip()]


def looks_like_config(line: str) -> bool:
    return line.startswith(SUPPORTED_SCHEMES)


def parse_uri(uri: str) -> ParsedConfig | None:
    """Extract (protocol, address, port) from a proxy URI.

    Returns ``None`` when the URI is malformed or its scheme is unsupported -
    callers treat that as "skip this entry".
    """
    uri = uri.strip()
    if not looks_like_config(uri):
        return None

    try:
        if uri.startswith("vmess://"):
            return _parse_vmess(uri)
        if uri.startswith("ss://"):
            return _parse_shadowsocks(uri)
        return _parse_generic(uri)
    except (ValueError, TypeError, AttributeError, KeyError, binascii.Error, json.JSONDecodeError):
        return None


def _parse_vmess(uri: str) -> ParsedConfig | None:
    payload = json.loads(_b64decode(uri[len("vmess://") :]))
    address = str(payload.get("add") or "").strip()
    port = int(payload.get("port") or 0)
    if not address or not (0 < port < 65536):
        return None
    return ParsedConfig(uri=uri, protocol="vmess", address=address, port=port)


def _parse_shadowsocks(uri: str) -> ParsedConfig | None:
    parsed = urlparse(uri)
    if parsed.hostname and parsed.port:
        return ParsedConfig(uri=uri, protocol="shadowsocks", address=parsed.hostname, port=int(parsed.port))

    # legacy form: ss://base64(method:password@host:port)#remark
    body = uri[len("ss://") :].split("#", 1)[0]
    if "@" in body:
        return None
    decoded = _b64decode(body)
    if "@" not in decoded:
        return None
    hostport = decoded.rsplit("@", 1)[1]
    if ":" not in hostport:
        return None
    address, _, port_raw = hostport.rpartition(":")
    port = int(port_raw.split("/", 1)[0])
    if not address or not (0 < port < 65536):
        return None
    return ParsedConfig(uri=uri, protocol="shadowsocks", address=address, port=port)


def _parse_generic(uri: str) -> ParsedConfig | None:
    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.port:
        return None
    port = int(parsed.port)
    if not (0 < port < 65536):
        return None
    protocol = parsed.scheme.lower()
    if protocol in ("hy2", "hysteria2"):
        protocol = "hysteria2"
    return ParsedConfig(uri=uri, protocol=protocol, address=parsed.hostname, port=port)


def label_uri(uri: str, prefix: str) -> str:
    """Prefix a config's display remark so free entries are obvious in the client.

    Falls back to the original URI whenever the entry cannot be rewritten safely -
    a cosmetic feature must never drop a working config.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return uri

    try:
        if uri.startswith("vmess://"):
            payload = json.loads(_b64decode(uri[len("vmess://") :]))
            remark = str(payload.get("ps") or "")
            if remark.startswith(prefix):
                return uri
            payload["ps"] = f"{prefix} {remark}".strip()
            encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
            return f"vmess://{encoded}"

        base, sep, fragment = uri.partition("#")
        remark = unquote(fragment) if sep else ""
        if remark.startswith(prefix):
            return uri
        return f"{base}#{quote(f'{prefix} {remark}'.strip(), safe='')}"
    except (ValueError, TypeError, AttributeError, KeyError, binascii.Error, json.JSONDecodeError):
        return uri


def parse_many(lines: list[str]) -> list[ParsedConfig]:
    """Parse a batch of lines, dropping unparsable ones and de-duplicating by URI."""
    seen: set[str] = set()
    result: list[ParsedConfig] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        parsed = parse_uri(line)
        if parsed is not None:
            result.append(parsed)
    return result
