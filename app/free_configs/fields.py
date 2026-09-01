"""Take a proxy URI apart into editable fields, and put it back together.

The panel needs to show a config the way a client does - address, port, UUID,
transport, TLS, SNI, fingerprint, ALPN - and write the edits back as a valid
URI.

The design choice worth knowing: there is no per-protocol schema listing which
parameters exist. Proxy URIs are an informal format, every list generator emits
slightly different keys, and a fixed schema would silently drop whatever it had
not heard of - which for a proxy config means quietly breaking it. So a URI is
split into the few parts that are structural (scheme, credentials, address,
port, name) and a plain dictionary of everything else, and rebuilt from the
same. Unknown parameters survive a round trip untouched.

Labels and dropdown options are cosmetic on top of that dictionary: a key we
recognise gets a friendly name and a list of common values, and a key we have
never seen still shows up as an editable row.
"""

import base64
import binascii
import json
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

from app.free_configs.parser import SUPPORTED_SCHEMES, _b64decode

# Presentation only. A parameter missing from here is still editable - it just
# shows under its own key with no dropdown.
LABELS = {
    "id": "UUID (id)",
    "password": "Password",
    "method": "Encryption method",
    "encryption": "Encryption",
    "flow": "Flow",
    "security": "TLS",
    "sni": "SNI",
    "fp": "Fingerprint",
    "alpn": "ALPN",
    "type": "Transport (network)",
    "headerType": "Header type",
    "host": "Host",
    "path": "Path",
    "serviceName": "gRPC service name",
    "mode": "gRPC mode",
    "pbk": "Reality public key",
    "sid": "Reality short id",
    "spx": "Reality spiderX",
    "allowInsecure": "Allow insecure",
    "insecure": "Allow insecure",
    "aid": "AlterId",
    "scy": "Encryption (scy)",
    "obfs": "Obfuscation",
    "obfs-password": "Obfuscation password",
    "congestion_control": "Congestion control",
    "udp_relay_mode": "UDP relay mode",
    "plugin": "Plugin",
}

OPTIONS = {
    "security": ["", "none", "tls", "reality", "xtls"],
    "type": ["", "tcp", "raw", "ws", "grpc", "http", "h2", "httpupgrade", "xhttp", "quic", "kcp"],
    "headerType": ["", "none", "http"],
    "fp": ["", "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized"],
    "alpn": ["", "h2", "http/1.1", "h2,http/1.1", "h3"],
    "flow": ["", "xtls-rprx-vision", "xtls-rprx-vision-udp443"],
    "encryption": ["", "none"],
    "mode": ["", "gun", "multi"],
    "congestion_control": ["", "cubic", "bbr", "new_reno"],
    "udp_relay_mode": ["", "native", "quic"],
    "scy": ["", "auto", "none", "zero", "aes-128-gcm", "chacha20-poly1305"],
    "method": [
        "",
        "aes-128-gcm",
        "aes-256-gcm",
        "chacha20-ietf-poly1305",
        "2022-blake3-aes-128-gcm",
        "2022-blake3-aes-256-gcm",
    ],
}

# Shown in this order when present; anything else follows, alphabetically.
PREFERRED_ORDER = [
    "id",
    "password",
    "method",
    "encryption",
    "flow",
    "aid",
    "scy",
    "type",
    "headerType",
    "host",
    "path",
    "serviceName",
    "mode",
    "security",
    "sni",
    "fp",
    "alpn",
    "pbk",
    "sid",
    "spx",
    "allowInsecure",
    "insecure",
    "obfs",
    "obfs-password",
    "congestion_control",
    "udp_relay_mode",
    "plugin",
]

# Parameters worth offering even when the URI does not carry them, so an admin
# can add a fingerprint or an SNI that the source omitted.
SUGGESTED = {
    "vless": ["id", "encryption", "flow", "type", "host", "path", "serviceName", "security", "sni", "fp", "alpn"],
    "trojan": ["password", "type", "host", "path", "security", "sni", "fp", "alpn"],
    "vmess": ["id", "aid", "scy", "net", "type", "host", "path", "tls", "sni", "fp", "alpn"],
    "shadowsocks": ["method", "password", "plugin"],
    "hysteria2": ["password", "sni", "insecure", "obfs", "obfs-password", "alpn"],
    "tuic": ["password", "sni", "alpn", "congestion_control", "udp_relay_mode"],
}

# vmess keeps its settings in a JSON body rather than a query string. These are
# the structural ones; every other key is an ordinary parameter.
_VMESS_STRUCTURAL = {"add", "port", "ps", "v"}


class ConfigFieldsError(ValueError):
    """The URI could not be taken apart, or the edit could not be written back."""


def _split_ss_userinfo(raw: str) -> tuple[str, str]:
    """Return (method, password) from a shadowsocks userinfo, plain or base64."""
    candidate = unquote(raw)
    if ":" not in candidate:
        try:
            candidate = _b64decode(candidate)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return "", candidate
    method, _, password = candidate.partition(":")
    return method, password


def describe(uri: str) -> dict:
    """Split a URI into structural parts plus a dictionary of parameters."""
    uri = (uri or "").strip()
    if not uri.startswith(SUPPORTED_SCHEMES):
        raise ConfigFieldsError("Unsupported or malformed URI")

    if uri.startswith("vmess://"):
        try:
            payload = json.loads(_b64decode(uri[len("vmess://") :]))
        except (ValueError, binascii.Error) as exc:
            raise ConfigFieldsError(f"Could not decode the vmess body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigFieldsError("The vmess body is not an object")
        if not str(payload.get("add") or "").strip() or not (0 < int(payload.get("port") or 0) < 65536):
            raise ConfigFieldsError("The vmess body has no usable address and port")
        params = {k: ("" if v is None else str(v)) for k, v in payload.items() if k not in _VMESS_STRUCTURAL}
        return {
            "protocol": "vmess",
            "alias": str(payload.get("ps") or ""),
            "address": str(payload.get("add") or ""),
            "port": int(payload.get("port") or 0),
            "params": params,
        }

    parsed = urlparse(uri)
    protocol = parsed.scheme.lower()
    if protocol in ("hy2", "hysteria2"):
        protocol = "hysteria2"

    params = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    alias = unquote(parsed.fragment) if parsed.fragment else ""

    if protocol == "ss":
        protocol = "shadowsocks"

    if protocol == "shadowsocks":
        if parsed.hostname:
            method, password = _split_ss_userinfo(parsed.username or "")
            address, port = parsed.hostname, int(parsed.port or 0)
        else:
            # legacy ss://base64(method:password@host:port)#name
            body = uri[len("ss://") :].split("#", 1)[0].split("?", 1)[0]
            try:
                decoded = _b64decode(body)
            except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
                raise ConfigFieldsError("Could not decode the shadowsocks body") from exc
            creds, _, hostport = decoded.rpartition("@")
            method, _, password = creds.partition(":")
            address, _, port_raw = hostport.rpartition(":")
            port = int(port_raw or 0)
        if not address or not (0 < port < 65536):
            raise ConfigFieldsError("The shadowsocks URI has no usable host and port")
        params = {"method": method, "password": password, **params}
        return {"protocol": "shadowsocks", "alias": alias, "address": address, "port": port, "params": params}

    if not parsed.hostname:
        raise ConfigFieldsError("The URI has no host")

    # The whole userinfo, not parsed.username: tuic writes "uuid:password"
    # there, and reading only the username half silently discarded the password.
    userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    credential = unquote(userinfo)
    key = "id" if protocol == "vless" else "password"
    params = {key: credential, **params}
    port = int(parsed.port or 0)
    if not (0 < port < 65536):
        raise ConfigFieldsError("The URI has no usable port")
    return {
        "protocol": protocol,
        "alias": alias,
        "address": parsed.hostname,
        "port": port,
        "params": params,
    }


def build(protocol: str, alias: str, address: str, port: int, params: dict) -> str:
    """Rebuild a URI. The inverse of :func:`describe`."""
    protocol = (protocol or "").strip().lower()
    address = (address or "").strip()
    alias = (alias or "").strip()
    params = {str(k): ("" if v is None else str(v)) for k, v in (params or {}).items()}

    if not address:
        raise ConfigFieldsError("Address is required")
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ConfigFieldsError("Port must be a number") from exc
    if not (0 < port < 65536):
        raise ConfigFieldsError("Port must be between 1 and 65535")

    if protocol == "vmess":
        payload = {"v": "2", "ps": alias, "add": address, "port": str(port)}
        payload.update({k: v for k, v in params.items() if k not in _VMESS_STRUCTURAL})
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        return f"vmess://{encoded}"

    if protocol == "shadowsocks":
        method = params.pop("method", "")
        password = params.pop("password", "")
        userinfo = base64.b64encode(f"{method}:{password}".encode("utf-8")).decode("utf-8").rstrip("=")
        query = urlencode({k: v for k, v in params.items() if v != ""})
        uri = f"ss://{userinfo}@{address}:{port}"
        if query:
            uri += f"?{query}"
        return uri + (f"#{quote(alias, safe='')}" if alias else "")

    key = "id" if protocol == "vless" else "password"
    credential = params.pop(key, "")
    query = urlencode({k: v for k, v in params.items() if v != ""})
    # ':' stays literal: tuic's userinfo is "uuid:password" and percent-encoding
    # the separator would change what the URI means.
    uri = f"{protocol}://{quote(credential, safe=':')}@{address}:{port}"
    if query:
        uri += f"?{query}"
    return uri + (f"#{quote(alias, safe='')}" if alias else "")


def _order(keys) -> list[str]:
    ranked = {key: index for index, key in enumerate(PREFERRED_ORDER)}
    return sorted(keys, key=lambda k: (ranked.get(k, len(ranked)), k))


def as_form(uri: str) -> dict:
    """``describe`` plus the labels and dropdown options the panel renders."""
    described = describe(uri)
    params = described["params"]
    suggested = [key for key in SUGGESTED.get(described["protocol"], []) if key not in params]

    fields = [
        {
            "key": key,
            "label": LABELS.get(key, key),
            "value": params[key],
            "options": OPTIONS.get(key),
            "secret": key in ("id", "password"),
        }
        for key in _order(params)
    ]
    return {
        **described,
        "fields": fields,
        "suggested": [{"key": key, "label": LABELS.get(key, key), "options": OPTIONS.get(key)} for key in suggested],
        "uri": uri,
    }
