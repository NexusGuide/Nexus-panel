"""What a profile may set, per protocol (fork feature).

The point these tests defend: protocols do not share a field vocabulary, and
the profile editor must not pretend they do. In a vmess body the transport is
"net" and "type" means the header type; in a vless URI the transport is "type"
and the header type is "headerType". Offering one merged list would let an
owner write "type=ws" into a vmess config and silently turn its header type
into nonsense - a config that still parses and no longer connects.

Pure-stdlib module, so these run without a database, network, or event loop.
"""

import pytest

from app.free_configs.fields import (
    LABELS,
    OPTIONS,
    PROFILE_FIELDS,
    build,
    describe,
    profile_fields,
    profile_protocols,
)


def keys(protocol: str) -> list[str]:
    return [field["key"] for field in profile_fields(protocol)]


# --------------------------------------------------------------------------- #
# the vocabularies really are different


def test_vmess_and_vless_disagree_about_transport():
    assert "net" in keys("vmess")
    assert "type" in keys("vless")
    assert "net" not in keys("vless")
    # vmess has a "type" too, but it means something else entirely
    assert "type" in keys("vmess")


def test_vmess_type_is_labelled_as_the_header_type():
    labels = {field["key"]: field["label"] for field in profile_fields("vmess")}
    assert "Header type" == labels["type"]
    assert "Transport" in labels["net"]


def test_vless_type_is_labelled_as_the_transport():
    labels = {field["key"]: field["label"] for field in profile_fields("vless")}
    assert "Transport" in labels["type"]
    assert "Header type" == labels["headerType"]


def test_vmess_says_tls_where_the_uri_protocols_say_security():
    assert "tls" in keys("vmess")
    assert "security" not in keys("vmess")
    assert "security" in keys("vless")
    assert "tls" not in keys("vless")


# --------------------------------------------------------------------------- #
# what a profile is not allowed to set


@pytest.mark.parametrize("protocol", sorted(PROFILE_FIELDS))
def test_no_protocol_offers_a_credential(protocol):
    """One UUID or password stamped across a hundred configs breaks all hundred."""
    assert "id" not in keys(protocol)
    assert "password" not in keys(protocol)


@pytest.mark.parametrize("protocol", sorted(PROFILE_FIELDS))
def test_every_protocol_can_set_address_and_port(protocol):
    assert keys(protocol)[:2] == ["address", "port"]


def test_shadowsocks_offers_only_what_it_has():
    # No SNI, no transport: a shadowsocks URI carries neither, and offering
    # them would write parameters that no client reads.
    assert keys("shadowsocks") == ["address", "port", "method", "plugin"]


def test_hysteria2_offers_its_own_obfuscation():
    assert "obfs" in keys("hysteria2")
    assert "obfs-password" in keys("hysteria2")
    assert "flow" not in keys("hysteria2")


# --------------------------------------------------------------------------- #
# the descriptors the panel renders


@pytest.mark.parametrize("protocol", sorted(PROFILE_FIELDS))
def test_every_field_has_a_label(protocol):
    for field in profile_fields(protocol):
        assert field["label"], field["key"]
        assert field["label"] != field["key"] or field["key"] not in LABELS


def test_dropdown_fields_carry_their_options():
    fields = {field["key"]: field for field in profile_fields("vless")}
    assert fields["security"]["options"] == OPTIONS["security"]
    assert fields["sni"]["options"] is None


def test_vmess_transport_dropdown_is_the_transport_list():
    fields = {field["key"]: field for field in profile_fields("vmess")}
    assert "ws" in fields["net"]["options"]
    assert "grpc" in fields["net"]["options"]
    # ...and its "type" gets the header-type list, not the transport one
    assert fields["type"]["options"] == OPTIONS["headerType"]


def test_an_unknown_protocol_offers_nothing():
    assert profile_fields("wireguard") == []
    assert profile_fields("") == []


def test_protocols_are_the_keys_of_the_table():
    assert profile_protocols() == list(PROFILE_FIELDS)


# --------------------------------------------------------------------------- #
# the keys survive a round trip through the editor


def test_a_vless_profile_key_lands_where_it_is_meant_to():
    uri = "vless://uuid@1.2.3.4:443?type=tcp&security=none#x"
    form = describe(uri)
    params = dict(form["params"])
    params["type"] = "ws"
    params["security"] = "tls"
    rebuilt = build("vless", "x", form["address"], form["port"], params)
    assert "type=ws" in rebuilt
    assert "security=tls" in rebuilt


def test_a_vmess_profile_key_lands_where_it_is_meant_to():
    import base64
    import json

    body = {"v": "2", "ps": "x", "add": "1.2.3.4", "port": "443", "id": "uuid", "net": "tcp", "type": "none"}
    uri = "vmess://" + base64.b64encode(json.dumps(body).encode()).decode()
    form = describe(uri)
    params = dict(form["params"])
    params["net"] = "ws"
    rebuilt = build("vmess", "x", form["address"], form["port"], params)
    decoded = json.loads(base64.b64decode(rebuilt[len("vmess://") :] + "==="))
    assert decoded["net"] == "ws"
    # the header type is untouched, which is exactly what a vless-shaped
    # profile writing "type" would have destroyed
    assert decoded["type"] == "none"
