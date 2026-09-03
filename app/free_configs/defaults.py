"""The community source lists a fresh install can start from.

Kept in the app rather than in the seeding script so the panel can offer them
too: the Sources tab has a button that adds whichever of these are missing, and
the installer does not touch the source list at all - sources are content, and
content belongs in the panel.

These URLs point at third-party community projects. They aggregate free,
publicly posted proxies; nobody involved operates the servers behind them, and
they can change or disappear without notice. See FREE_CONFIGS.md for the caveats
that come with serving them.

The list is the one published by patterniha/free-configs
(https://github.com/patterniha/free-configs, MIT), whose approach - aggregate
several public lists, then filter by real connectivity rather than by whether
GitHub can reach them - this feature follows. No code from that project is used
here; only its choice of sources.
"""

# (url, is_base64, remark)
DEFAULT_SOURCES = [
    ("https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/verified/configs.txt", False, "0xRadikal"),
    ("https://raw.githubusercontent.com/itsyebekhe/PSG/main/subscriptions/xray/mix", False, "PSG mix"),
    (
        "https://github.com/Delta-Kronecker/V2ray-Config/raw/refs/heads/main/config/all_configs.txt",
        False,
        "Delta-Kronecker",
    ),
    (
        "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt",
        False,
        "MahsaFreeConfig",
    ),
    ("https://raw.githubusercontent.com/iampedii/whitedns-sub/refs/heads/main/base64.txt", True, "whitedns"),
    ("https://openproxylist.com/v2ray/rawlist/text", False, "openproxylist.com"),
    (
        "https://raw.githubusercontent.com/4n0nymou3/multi-proxy-config-fetcher/refs/heads/main/configs/proxy_configs_tested.txt",
        False,
        "4n0nymou3 (tested)",
    ),
    ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY_RAW.txt", False, "roosterkid"),
    (
        "https://raw.githubusercontent.com/iampedii/whitedns-sub/refs/heads/main/cloudflare-base64.txt",
        True,
        "whitedns cloudflare",
    ),
    # patterniha's own already-filtered output: an aggregate of the lists above,
    # published as a single file. Harmless to include - duplicates are collapsed
    # by URI hash during a refresh - and it adds whatever that project filtered
    # in that our own pass may have missed.
    (
        "https://raw.githubusercontent.com/patterniha/free-configs/main/configs.txt",
        False,
        "patterniha (aggregated)",
    ),
]
