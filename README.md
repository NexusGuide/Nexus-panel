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

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install
```

That gives you SQLite and a panel on localhost. The options worth knowing:

| Option | |
| --- | --- |
| `--database sqlite\|mysql\|mariadb\|postgresql\|timescaledb` | Backend to install. Default `sqlite` |
| `--ssl-domain panel.example.com` | Issue a Let's Encrypt certificate and listen publicly |
| `--cert FILE --key FILE` | Use a certificate you already have |
| `--port 8000` | Port for the dashboard and API |
| `--image REF` | Use a different image, e.g. a local build: `--image nexus-panel:dev` |
| `--no-enable` | Install with the free-config feature switched off |
| `--yes` | Answer every prompt with yes |

A typical real install:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install \
  --database timescaledb --ssl-domain panel.example.com
```

Without a certificate the panel binds to `127.0.0.1` on purpose — a dashboard and
subscription links served over plain HTTP to the open internet is not something you
should get by accident. Reach it over SSH while you try it out:

```bash
ssh -L 8000:localhost:8000 root@your-server
```

...or add a certificate later with `nexus ssl --domain panel.example.com`.

### Coming from PasarGuard

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ migrate
```

This stops the old panel, copies `/var/lib/pasarguard` to `/var/lib/nexus`, carries the
settings over and starts Nexus Panel. The old install is left in place, untouched, so
you can go back to it if something is wrong.

## Managing it

The installer leaves a `nexus` command behind:

| Command | |
| --- | --- |
| `nexus apply` | Pull the current image and restart — this is how you update |
| `nexus ssl --domain D` | Issue or replace the certificate |
| `nexus refresh` | Rebuild the free-config pool now |
| `nexus logs` `status` `restart` `start` `stop` | Day-to-day |
| `nexus cli ...` | The panel's own CLI, e.g. `nexus cli generate-temp-key` |
| `nexus backup` / `nexus restore FILE` | Archive of the data directory, settings and a database dump |
| `nexus edit` / `nexus edit-env` | Open the compose file or `.env` in `$EDITOR` |
| `nexus uninstall` | Remove it, with a separate prompt before deleting your data |

## After installation

| | |
| --- | --- |
| Files | `/opt/nexus` |
| Config | `/opt/nexus/.env` |
| Data | `/var/lib/nexus` |
| Certificates | `/var/lib/nexus/certs` |
| Dashboard | `https://YOUR_DOMAIN:8000/dashboard/` |

Create the owner account:

```bash
nexus cli generate-temp-key
```

Enter the key it prints on the dashboard login page.

## Releasing

Bump the version in `app/version.py`, `pyproject.toml`, `dashboard/package.json` and
`uv.lock` — all four, or the Docker build fails on a lock mismatch — and push. Changing
`app/version.py` on `main` publishes a GitHub Release automatically, and that Release is
what running panels compare themselves against: the update banner reads the Releases
API, so a version that is never released is a version nobody is told about.

## Running from source

```bash
git clone https://github.com/NexusGuide/Nexus-panel.git
cd Nexus-panel
docker build -t nexus-panel:dev .
```

Then install with `--image nexus-panel:dev`.

## Documentation

- [Installing](docs/install.md) — databases, TLS, updating, backups, coming from
  PasarGuard, and what to do when something is wrong
- [The `nexus` command](docs/cli.md) — every subcommand and flag
- [Free configs](FREE_CONFIGS.md) — the feature this fork exists for

## Credits

Nexus Panel exists because of [PasarGuard](https://github.com/PasarGuard/panel) — the
panel, the dashboard and the node protocol are their work, and this fork tracks their
releases. If the base panel is useful to you, consider
[supporting them](https://donate.pasarguard.org).

The installer here is not theirs: `install.sh` is written from scratch for this project,
because PasarGuard's installer scripts are published without a licence and so cannot be
copied or redistributed.

No source lists ship with the panel. Which lists to trust is a decision about whose
servers your users' traffic passes through, and it belongs to whoever runs the panel.

## License

[AGPL-3.0](LICENSE), the same as upstream.
