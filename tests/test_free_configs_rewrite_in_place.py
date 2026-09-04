"""Applying a profile changes configs; it does not duplicate them.

The behaviour this pins down, and why it is not obvious: a config's identity is
the hash of its URI, so changing an SNI or an address changes which config it
is. The first version of the profile feature took that literally - it stored the
result as a new manual entry and switched the original off. Applying a profile
to twenty configs then produced forty rows, twenty of them switched off, and the
pool looked like it had doubled behind the owner's back.

"Apply these settings to these configs" means the configs the owner picked end
up with those settings. These tests are about the bookkeeping that makes that
true: one row in, one row out, with the name, the on/off state and the group
memberships still attached to it.

Pure-stdlib: what is exercised here is the URI rewrite itself, so it runs
without a database or an event loop.
"""

import base64
import json

from app.free_configs.fields import build, describe
from app.free_configs.parser import parse_uri


def apply_profile_fields(uri: str, fields: dict) -> str:
    """The same rewrite the apply endpoint performs on one config."""
    form = describe(uri)
    params = dict(form["params"])
    address = form["address"]
    port = form["port"]
    alias = form["alias"]
    for key, raw in fields.items():
        value = "" if raw is None else str(raw)
        if not value:
            continue
        if key == "address":
            address = value
        elif key == "port":
            port = int(value)
        else:
            params[key] = value
    return build(form["protocol"], alias, address, port, params)


# --------------------------------------------------------------------------- #
# the rewrite itself


def test_a_profile_changes_the_config_it_is_applied_to():
    before = "vless://uuid@1.2.3.4:443?type=ws&security=tls&sni=old.example#name"
    after = apply_profile_fields(before, {"sni": "new.example", "address": "cdn.example"})
    parsed = parse_uri(after)
    assert parsed is not None
    assert parsed.address == "cdn.example"
    assert "sni=new.example" in after
    # everything the profile did not mention survives
    assert "type=ws" in after
    assert "security=tls" in after
    assert after.endswith("#name")


def test_an_empty_value_leaves_the_field_alone():
    before = "vless://uuid@1.2.3.4:443?sni=keep.example#x"
    after = apply_profile_fields(before, {"sni": "", "fp": "chrome"})
    assert "sni=keep.example" in after
    assert "fp=chrome" in after


def test_the_identity_changes_with_the_content():
    """Which is the whole reason the row has to be rewritten rather than added."""
    before = "vless://uuid@1.2.3.4:443?sni=old.example#x"
    after = apply_profile_fields(before, {"sni": "new.example"})
    assert parse_uri(before).uri_hash != parse_uri(after).uri_hash


def test_a_profile_that_changes_nothing_keeps_the_same_identity():
    before = "vless://uuid@1.2.3.4:443?sni=same.example#x"
    after = apply_profile_fields(before, {"sni": "same.example"})
    assert parse_uri(before).uri_hash == parse_uri(after).uri_hash


def test_a_vmess_profile_keeps_the_body_shape():
    body = {"v": "2", "ps": "x", "add": "1.2.3.4", "port": "443", "id": "uuid", "net": "ws", "type": "none"}
    before = "vmess://" + base64.b64encode(json.dumps(body).encode()).decode()
    after = apply_profile_fields(before, {"net": "grpc", "sni": "a.example"})
    decoded = json.loads(base64.b64decode(after[len("vmess://") :] + "==="))
    assert decoded["net"] == "grpc"
    assert decoded["sni"] == "a.example"
    assert decoded["type"] == "none"      # untouched
    assert decoded["id"] == "uuid"        # credentials survive
    assert decoded["add"] == "1.2.3.4"


# The bookkeeping around this - the row being updated instead of a second one
# appearing, the name and group memberships following it, and health being kept
# only when the endpoint did not move - lives in crud.rewrite_config_in_place.
# It is not covered here: exercising it needs a real session, and a fake close
# enough to be worth trusting would be a larger and less honest piece of code
# than the function it stands in for.
