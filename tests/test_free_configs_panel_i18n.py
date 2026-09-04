"""Every string the Free Configs page shows must be translatable.

This page is one HTML file with its own translation tables rather than part of
the dashboard's i18n setup, and the failure mode that comes with that is a
slow leak: a string added in a hurry renders in English inside a Persian panel,
nobody notices which one, and the report comes back as "some texts don't
change" - a whole page to re-read by eye.

So the page is checked instead of read. Three things have to hold:

  * markup that shows text carries data-i18n (or data-i18n-ph / -title);
  * every key the script asks for exists in the English table or in the markup
    it falls back to, because a missing key renders as the key itself;
  * every language declares the same keys, so a translated panel never shows an
    English sentence in the middle of a Persian one.

The last two are what actually catch regressions. The first is a heuristic and
says so: it skips the places where English is the right answer - protocol names,
field keys, and the API's own vocabulary.
"""

import json
import re
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parent.parent / "app" / "free_configs" / "static" / "panel.html"
SOURCE = PANEL.read_text(encoding="utf-8")
BODY = SOURCE[SOURCE.index("<body>") : SOURCE.index("<script")]
SCRIPT = SOURCE[SOURCE.index("<script") :]

LANGUAGES = ("en", "fa", "ru", "zh")

# Words that are the same in every language: protocol names, transport names,
# and values the API itself defines. Translating these would be wrong.
NOT_TRANSLATABLE = {
    "vless", "vmess", "trojan", "shadowsocks", "hysteria2", "tuic", "ss",
    "all", "groups", "active", "not_disabled", "everyone",
    "tcp", "ws", "grpc", "http", "h2", "quic", "kcp", "none", "tls", "reality",
    "URI", "SNI", "ALPN", "TLS", "-",
}


def table(language: str) -> dict[str, str]:
    """The keys one language declares, read straight out of the page."""
    start = SOURCE.index(f"    {language}: {{")
    depth = 0
    for index in range(start, len(SOURCE)):
        if SOURCE[index] == "{":
            depth += 1
        elif SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                break
    block = SOURCE[start : index + 1]
    return dict(re.findall(r"'([a-zA-Z0-9_.\-]+)':\s*'((?:[^'\\]|\\.)*)'", block))


def keys_used_in_script() -> set[str]:
    """Keys the script looks up. A missing one renders as the key itself."""
    # The boundary matters: closest('tr') and get('sni') are not translations.
    return set(re.findall(r"[^A-Za-z0-9_]t\('([a-zA-Z][a-zA-Z0-9_.]*\.[a-zA-Z0-9_.]+|selected)'\)", SCRIPT))


def dynamic_prefixes() -> tuple[str, ...]:
    """Prefixes the script builds keys from at run time - t('field.' + key).

    A field label or a settings hint is looked up by a key assembled from data,
    so no literal appears anywhere for the checker to find. Without this, every
    one of those entries looks like a dead key.

    The prefix is not always spelled inside the t() call - fieldLabel builds it
    a line earlier - so what is looked for is any string literal that ends in a
    dot and is concatenated onto something.
    """
    return tuple(sorted(set(re.findall(r"'([a-zA-Z][a-zA-Z0-9_]*\.)'\s*\+", SCRIPT))))


def keys_in_markup() -> set[str]:
    return set(re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', SOURCE))


# --------------------------------------------------------------------------- #
# the checks that catch regressions


def test_every_language_is_present():
    for language in LANGUAGES:
        assert table(language), f"{language} has no translation table"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_script_never_asks_for_a_key_that_does_not_exist(language):
    """A key with no entry anywhere renders raw - "prof.lead" instead of a sentence."""
    available = set(table(language)) | set(table("en")) | keys_in_markup()
    missing = sorted(keys_used_in_script() - available)
    assert not missing, f"{language}: no text for {missing}"


@pytest.mark.parametrize("language", [lang for lang in LANGUAGES if lang != "en"])
def test_no_english_shows_through_in_a_translated_panel(language):
    """Each language must cover everything English covers, and vice versa."""
    english = set(table("en"))
    theirs = set(table(language))
    markup = keys_in_markup()
    # English-side keys come from two places: the table and the markup the
    # fallback reads. A translation has to carry both.
    expected = english | markup
    missing = sorted(expected - theirs)
    assert not missing, f"{language} is missing {len(missing)}: {missing[:12]}"


@pytest.mark.parametrize("language", [lang for lang in LANGUAGES if lang != "en"])
def test_a_translation_declares_nothing_the_page_never_asks_for(language):
    """Dead keys are how a table drifts out of step with the page."""
    known = set(table("en")) | keys_in_markup() | keys_used_in_script()
    prefixes = dynamic_prefixes()
    extra = sorted(
        key for key in set(table(language)) - known
        if not key.startswith(prefixes)
    )
    assert not extra, f"{language} declares unused keys: {extra[:12]}"


def test_placeholders_survive_translation():
    """A {n} dropped in translation turns a count into a sentence with a hole."""
    problems = []
    english = table("en")
    for language in LANGUAGES[1:]:
        for key, text in table(language).items():
            if key not in english:
                continue
            wanted = set(re.findall(r"\{(\w+)\}", english[key]))
            got = set(re.findall(r"\{(\w+)\}", text))
            if wanted != got:
                problems.append(f"{language}:{key} has {sorted(got)}, English has {sorted(wanted)}")
    assert not problems, problems


# --------------------------------------------------------------------------- #
# the heuristic


def test_visible_markup_carries_a_translation_key():
    offenders = []
    for match in re.finditer(r"<(\w+)([^>]*)>([^<>]*[A-Za-z]{3}[^<>]*)</\1>", BODY):
        attributes, text = match.group(2), match.group(3).strip()
        if "data-i18n" in attributes or not text or text.startswith("${"):
            continue
        if text in NOT_TRANSLATABLE:
            continue
        offenders.append(f"<{match.group(1)}> {text[:60]}")
    assert not offenders, "untranslatable markup: " + json.dumps(offenders, ensure_ascii=False)


def test_placeholder_and_title_attributes_carry_one_too():
    offenders = []
    for match in re.finditer(r"<\w+[^>]*>", BODY):
        tag = match.group(0)
        for attribute, key in (("placeholder", "data-i18n-ph"), ("title", "data-i18n-title")):
            found = re.search(rf'{attribute}="([^"]+)"', tag)
            if found and key not in tag and found.group(1) not in NOT_TRANSLATABLE:
                offenders.append(f"{attribute}={found.group(1)[:50]}")
    assert not offenders, "untranslatable attributes: " + json.dumps(offenders, ensure_ascii=False)


def test_the_script_shows_no_bare_english_sentence():
    """toast('Saved') and confirm('Really?') are the usual way one slips in."""
    offenders = []
    for call in ("toast", "confirm"):
        for match in re.finditer(call + r"\(\s*(['\"])([^'\"]{4,})\1", SCRIPT):
            offenders.append(f"{call}({match.group(2)[:50]})")
    assert not offenders, offenders
