<h1 align="center">Nexus Panel</h1>

<p align="center">
    <strong>Proxy management panel with a built-in free-config pool</strong>
</p>

<p align="center">
    <a href="https://github.com/NexusGuide/Nexus-panel/actions/workflows/build-fork.yml">
        <img src="https://img.shields.io/github/actions/workflow/status/NexusGuide/Nexus-panel/build-fork.yml?style=flat-square&label=image" />
    </a>
    <a href="https://github.com/NexusGuide/Nexus-panel/pkgs/container/nexus-panel">
        <img src="https://img.shields.io/badge/ghcr.io-nexus--panel-blue?style=flat-square&logo=docker" />
    </a>
    <a href="https://github.com/NexusGuide/Nexus-panel/blob/main/LICENSE">
        <img src="https://img.shields.io/github/license/NexusGuide/Nexus-panel?style=flat-square" />
    </a>
</p>

---

## About

Nexus Panel is a fork of [PasarGuard/panel](https://github.com/PasarGuard/panel). It
keeps everything upstream does — multi-node proxy management, VMess, VLESS, Trojan,
Shadowsocks, WireGuard and Hysteria2, TLS and REALITY, per-user traffic and expiry
limits, subscription links, REST API, CLI and Telegram bot — and adds features of its
own on top.

> This project is **not** affiliated with or endorsed by the PasarGuard team.
> Report problems with this fork here, not to them.

Everything the fork adds is off until you switch it on, so a default install behaves
exactly like upstream.

## What this fork adds

**Free Configs.** The panel harvests proxy URIs from public community lists,
health-checks them, and appends the working ones to the subscription output of the
groups you choose. It comes with a panel page to browse and search the pool, edit any
config field by field (address, port, UUID, SNI, fingerprint, ALPN and the rest), add
your own entries by hand, manage the source lists, and decide which group receives
which configs — assigned from the same Create/Edit Group dialog you already use when
creating a user.

Full documentation: **[FREE_CONFIGS.md](FREE_CONFIGS.md)**

## Installation

One command, same as upstream. Pick the database you want:

**TimescaleDB (recommended):**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install --database timescaledb
```

**SQLite:**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install
```

**MySQL:**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install --database mysql
```

**MariaDB:**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install --database mariadb
```

**PostgreSQL:**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install --database postgresql
```

The installer is a thin wrapper around PasarGuard's official one: it runs that
installer unchanged, then points the compose file at this fork's image and writes the
free-config settings into `.env`. Every flag the official installer accepts works
here too, including `--ssl-domain panel.example.com`.

### Fork-specific options

| Option | What it does |
| --- | --- |
| `--image <ref>` | Use a different image, e.g. a local build: `--image nexus-panel:dev` |
| `--no-seed` | Skip adding the default community source lists |
| `--no-enable` | Install the image but leave the free-config feature switched off |

### Other subcommands

```bash
# re-apply the fork after an official update reverted the image
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ apply

# official update, then re-apply the fork
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ update
```

Everything else is handled by the `pasarguard` command the official installer
provides: `pasarguard logs`, `restart`, `status`, `cli`, `backup`, `uninstall`.

## After installation

| | |
| --- | --- |
| Files | `/opt/pasarguard` |
| Config | `/opt/pasarguard/.env` |
| Data | `/var/lib/pasarguard` |
| Dashboard | `https://YOUR_DOMAIN:8000/dashboard/` |

Create the owner account:

```bash
pasarguard cli generate-temp-key
```

Enter the key it prints on the dashboard login page.

The dashboard needs a TLS certificate — see upstream's
[certificate guide](https://docs.pasarguard.org/en/examples/issue-ssl-certificate).
To try it without a domain, forward the port over SSH:

```bash
ssh -L 8000:localhost:8000 user@serverip
```

then open `http://localhost:8000/dashboard/`. This is for testing only; access ends
when the SSH session closes.

## Running from source

```bash
git clone https://github.com/NexusGuide/Nexus-panel.git
cd Nexus-panel
docker build -t nexus-panel:dev .
```

Then install with `--image nexus-panel:dev`.

## Documentation

Everything inherited from upstream is documented in PasarGuard's own docs, which
apply unchanged:

[English](https://docs.pasarguard.org/en) ·
[فارسی](https://docs.pasarguard.org/fa) ·
[Русский](https://docs.pasarguard.org/ru) ·
[简体中文](https://docs.pasarguard.org/zh-cn)

What this fork adds is documented in [FREE_CONFIGS.md](FREE_CONFIGS.md).

## Credits

Nexus Panel exists because of [PasarGuard](https://github.com/PasarGuard/panel) — the
panel, the dashboard, the installer and the node protocol are all their work, and this
fork tracks their releases. If the base panel is useful to you, consider
[supporting them](https://donate.pasarguard.org).

The default community source lists are public collections maintained by their own
authors, credited in [FREE_CONFIGS.md](FREE_CONFIGS.md).

## License

[AGPL-3.0](LICENSE), the same as upstream.
