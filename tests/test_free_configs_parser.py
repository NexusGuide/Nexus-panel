"""Unit tests for the free-configs URI parser (fork feature).

Pure-stdlib module, so these run without a database, network, or event loop.
"""

import base64
import json

from app.free_configs.parser import (
    ParsedConfig,
    decode_body,
    label_uri,
    looks_like_config,
    parse_many,
    parse_uri,
)


def _vmess(payload: dict) -> str:
    return "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()


# --------------------------------------------------------------------------- #
# parse_uri
# --------------------------------------------------------------------------- #


def test_parse_vmess():
    uri = _vmess({"v": "2", "ps": "node-1", "add": "example.com", "port": "443", "id": "uuid"})
    parsed = parse_uri(uri)
    assert parsed == ParsedConfig(uri=uri, protocol="vmess", address="example.com", port=443)


def test_parse_vmess_unpadded_base64():
    raw = json.dumps({"add": "a.example.com", "port": 8443}).encode()
    uri = "vmess://" + base64.b64encode(raw).decode().rstrip("=")
    parsed = parse_uri(uri)
    assert parsed is not None
    assert (parsed.address, parsed.port) == ("a.example.com", 8443)


def test_parse_vmess_rejects_missing_fields():
    assert parse_uri(_vmess({"ps": "no address"})) is None
    assert parse_uri(_vmess({"add": "example.com", "port": 0})) is None
    assert parse_uri("vmess://not-base64!!") is None


def test_parse_vless_and_trojan():
    vless = parse_uri("vless://uuid@1.2.3.4:443?type=tcp&security=reality#remark")
    assert vless is not None
    assert (vless.protocol, vless.address, vless.port) == ("vless", "1.2.3.4", 443)

    trojan = parse_uri("trojan://password@host.example:8443?sni=x#t")
    assert trojan is not None
    assert (trojan.protocol, trojan.address, trojan.port) == ("trojan", "host.example", 8443)


def test_parse_hysteria2_aliases_normalize():
    for scheme in ("hysteria2", "hy2"):
        parsed = parse_uri(f"{scheme}://auth@h.example:9000?insecure=1#r")
        assert parsed is not None
        assert parsed.protocol == "hysteria2"
        assert parsed.port == 9000


def test_parse_shadowsocks_uri_form():
    parsed = parse_uri("ss://Y2hhY2hhMjA6cGFzcw@ss.example:8388#remark")
    assert parsed is not None
    assert (parsed.protocol, parsed.address, parsed.port) == ("shadowsocks", "ss.example", 8388)


def test_parse_shadowsocks_legacy_base64_form():
    inner = base64.b64encode(b"chacha20-ietf-poly1305:secret@ss.example:8388").decode()
    parsed = parse_uri(f"ss://{inner}#remark")
    assert parsed is not None
    assert (parsed.address, parsed.port) == ("ss.example", 8388)


def test_parse_shadowsocks_legacy_password_containing_at():
    inner = base64.b64encode(b"aes-256-gcm:p@ss@word@ss.example:9999").decode()
    parsed = parse_uri(f"ss://{inner}")
    assert parsed is not None
    # rsplit means the LAST @ separates credentials from host
    assert (parsed.address, parsed.port) == ("ss.example", 9999)


def test_parse_rejects_junk():
    for bad in ("", "   ", "http://example.com", "hello world", "vless://no-port@host", "trojan://x@host:notaport"):
        assert parse_uri(bad) is None


def test_parse_rejects_out_of_range_port():
    assert parse_uri(_vmess({"add": "example.com", "port": 70000})) is None


def test_looks_like_config():
    assert looks_like_config("vless://x@h:443")
    assert not looks_like_config("https://example.com/list.txt")


# --------------------------------------------------------------------------- #
# decode_body
# --------------------------------------------------------------------------- #


def test_decode_body_plain():
    body = "vless://a@h:443\n\n  trojan://b@h:8443  \n"
    assert decode_body(body, is_base64=False) == ["vless://a@h:443", "trojan://b@h:8443"]


def test_decode_body_base64_roundtrip():
    inner = "vless://a@h:443\ntrojan://b@h:8443"
    encoded = base64.b64encode(inner.encode()).decode()
    assert decode_body(encoded, is_base64=True) == ["vless://a@h:443", "trojan://b@h:8443"]


def test_decode_body_base64_missing_padding():
    inner = "vless://a@h:443"
    encoded = base64.b64encode(inner.encode()).decode().rstrip("=")
    assert decode_body(encoded, is_base64=True) == [inner]


def test_decode_body_handles_broken_base64():
    assert decode_body("!!!not base64!!!", is_base64=True) == []


def test_decode_body_empty():
    assert decode_body("", is_base64=False) == []
    assert decode_body("   ", is_base64=True) == []


# --------------------------------------------------------------------------- #
# parse_many
# --------------------------------------------------------------------------- #


def test_parse_many_dedupes_and_drops_unparsable():
    lines = [
        "vless://a@h:443",
        "vless://a@h:443",  # exact duplicate
        "garbage",
        "trojan://b@h2:8443",
    ]
    result = parse_many(lines)
    assert [c.address for c in result] == ["h", "h2"]


def test_uri_hash_is_stable_and_distinct():
    a = parse_uri("vless://a@h:443")
    b = parse_uri("vless://a@h:443")
    c = parse_uri("vless://a@h:444")
    assert a.uri_hash == b.uri_hash
    assert a.uri_hash != c.uri_hash


# --------------------------------------------------------------------------- #
# label_uri
# --------------------------------------------------------------------------- #


def test_label_uri_generic_prepends_prefix():
    labelled = label_uri("vless://a@h:443#Tokyo", "🆓")
    assert labelled.startswith("vless://a@h:443#")
    assert "Tokyo" in labelled
    # the remark is percent-encoded, so decode before asserting on the prefix
    from urllib.parse import unquote

    assert unquote(labelled.split("#", 1)[1]) == "🆓 Tokyo"


def test_label_uri_generic_without_existing_remark():
    from urllib.parse import unquote

    labelled = label_uri("trojan://p@h:8443", "FREE")
    assert unquote(labelled.split("#", 1)[1]) == "FREE"


def test_label_uri_vmess_rewrites_ps_field():
    uri = _vmess({"add": "example.com", "port": 443, "ps": "Node A"})
    labelled = label_uri(uri, "🆓")
    payload = json.loads(base64.b64decode(labelled[len("vmess://") :] + "==").decode())
    assert payload["ps"] == "🆓 Node A"
    assert payload["add"] == "example.com"


def test_label_uri_is_idempotent():
    once = label_uri("vless://a@h:443#Tokyo", "🆓")
    twice = label_uri(once, "🆓")
    assert once == twice

    vmess_once = label_uri(_vmess({"add": "h", "port": 1, "ps": "x"}), "🆓")
    assert label_uri(vmess_once, "🆓") == vmess_once


def test_label_uri_empty_prefix_is_noop():
    uri = "vless://a@h:443#Tokyo"
    assert label_uri(uri, "") == uri
    assert label_uri(uri, "   ") == uri


def test_label_uri_never_raises_on_junk():
    # a cosmetic step must never destroy or reject a config
    assert label_uri("vmess://!!!broken!!!", "🆓") == "vmess://!!!broken!!!"


def test_labelled_uri_still_parses():
    original = "vless://uuid@1.2.3.4:443?type=tcp#Tokyo"
    labelled = label_uri(original, "🆓")
    parsed = parse_uri(labelled)
    assert parsed is not None
    assert (parsed.address, parsed.port) == ("1.2.3.4", 443)
