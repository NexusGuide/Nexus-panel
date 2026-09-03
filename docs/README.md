# Nexus Panel documentation

Written for this fork. Where the fork differs from upstream — the installer, the
paths, the command name, the free-config feature — this is the authority, and
upstream's documentation is wrong for you.

## This project's own

- **[Installing](install.md)** — the install command, databases, TLS, updating,
  backups, coming from PasarGuard, and what to do when something is wrong.
- **[The `nexus` command](cli.md)** — every subcommand and flag.
- **[Free configs](../FREE_CONFIGS.md)** — the feature this fork exists for:
  harvesting community proxy lists, the candidate tray, per-protocol profiles,
  and how they reach a subscription.

## Inherited from upstream

Users, admins, groups, nodes, hosts, templates, the core editor and the
subscription formats are PasarGuard's work and behave here exactly as they do
there. Their documentation covers all of it:

[English](https://docs.pasarguard.org/en) ·
[فارسی](https://docs.pasarguard.org/fa) ·
[Русский](https://docs.pasarguard.org/ru) ·
[简体中文](https://docs.pasarguard.org/zh-cn)

Two warnings about reading it, both about the same thing — that is a different
project's documentation, describing a different install:

- **Ignore its installation, update and CLI pages.** They install PasarGuard at
  `/opt/pasarguard` with a `pasarguard` command. Running them on a Nexus Panel
  machine gets you a second, unrelated panel. Use [Installing](install.md) and
  [the `nexus` command](cli.md) instead.
- **Paths and commands differ everywhere else too.** Where their docs say
  `/opt/pasarguard/.env`, yours is `/opt/nexus/.env`; where they say
  `pasarguard restart`, yours is `nexus restart`. The panel's own screens are
  the same, so the rest reads across without trouble.
