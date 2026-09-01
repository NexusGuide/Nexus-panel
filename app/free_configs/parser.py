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


def _strip_remark(uri: str) -> str:
    """Return the URI with its display name removed, for identity comparison.

    Two forms carry a name: everything except vmess puts it in the fragment
    after ``#``; vmess hides it in the ``ps`` field of its base64 JSON body.
    Anything unparsable is returned unchanged - a config we cannot normalise
    should still be usable, just deduplicated less well.
    """
    uri = uri.strip()
    if uri.lower().startswith("vmess://"):
        try:
            payload = json.loads(_b64decode(uri[8:]))
            if not isinstance(payload, dict):
                return uri
            payload.pop("ps", None)
            payload.pop("remarks", None)
            return "vmess://" + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except Exception:  # noqa: BLE001 - identity is best-effort, never fatal
            return uri
    return uri.split("#", 1)[0]


# slots matter here: a refresh can hold tens of thousands of these at once,
# and dropping the per-instance __dict__ cuts that memory roughly in half.
@dataclass(frozen=True, slots=True)
class ParsedConfig:
    uri: str
    protocol: str
    address: str
    port: int

    @property
    def uri_hash(self) -> str:
        """Identity of the proxy itself, ignoring whatever it happens to be called.

        The same server is republished across community lists under a dozen
        different remarks. Hashing the whole URI made each of those a separate
        config, so a subscription opened with several byte-identical entries
        that differed only in their label. Hashing the URI *without* its name
        collapses them into one.
        """
        return hashlib.sha256(_strip_remark(self.uri).encode("utf-8")).hexdigest()


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


def label_uri(uri: str, prefix: str, remark_override: str | None = None) -> str:
    """Set a config's display remark: the admin's name if there is one, else prefixed.

    ``remark_override`` replaces the config's own name outright - that is the
    point of letting an admin rename an entry. The prefix is still applied on
    top, so a renamed config is still recognisable as a free one.

    Falls back to the original URI whenever the entry cannot be rewritten safely -
    a cosmetic feature must never drop a working config.
    """
    prefix = (prefix or "").strip()
    override = (remark_override or "").strip()
    if not prefix and not override:
        return uri

    try:
        if uri.startswith("vmess://"):
            payload = json.loads(_b64decode(uri[len("vmess://") :]))
            remark = override or str(payload.get("ps") or "")
            if prefix and not remark.startswith(prefix):
                remark = f"{prefix} {remark}".strip()
            payload["ps"] = remark
            encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
            return f"vmess://{encoded}"

        base, sep, fragment = uri.partition("#")
        remark = override or (unquote(fragment) if sep else "")
        if prefix and not remark.startswith(prefix):
            remark = f"{prefix} {remark}".strip()
        if not remark:
            return uri
        return f"{base}#{quote(remark, safe='')}"
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
