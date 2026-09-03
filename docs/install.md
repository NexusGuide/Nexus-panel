# Installing Nexus Panel

One command, on a fresh Debian or Ubuntu server, as root:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install
```

The installer is a single self-contained file. It downloads nothing else at run
time, so what you read in `install.sh` is what runs, and a dropped connection
half way through cannot leave you with a mismatched set of scripts.

It installs Docker if the machine has none, writes a compose file and an `.env`,
starts the panel and a database, and leaves you a `nexus` command. At the end it
prints the dashboard URL and a temporary login, shown in yellow. That login is
printed once and not stored anywhere — change the password on first sign-in.

## What ends up where

| Path | What is in it |
| --- | --- |
| `/opt/nexus` | `docker-compose.yml`, `.env`, backups |
| `/var/lib/nexus` | the database, subscription data, everything with state |
| `/var/lib/nexus/certs` | the TLS certificate and key |
| `/usr/local/bin/nexus` | the command |

The compose project is called `nexus` and its services are `panel` and `db`, so
`docker compose -p nexus ps` shows them if you would rather use Docker directly.

Nothing is written to `/opt/pasarguard`. An existing PasarGuard install on the
same machine is left alone — see [Coming from PasarGuard](#coming-from-pasarguard).

## Choosing a database

```bash
... @ install --database postgresql
```

`sqlite` (the default), `mysql`, `mariadb`, `postgresql`, `timescaledb`.

SQLite needs no second container and is the right answer for a panel with a few
hundred users. The others run as a `db` service beside the panel, with a
generated password written into `/opt/nexus/.env`; the database port is bound to
`127.0.0.1` only, so it is not reachable from outside the machine.

TimescaleDB is PostgreSQL with the time-series extension, and is worth the extra
memory only if you care about long usage histories.

The choice is recorded at install time and every later command reads it back, so
you never pass `--database` again.

## TLS

```bash
... @ install --ssl-domain panel.example.com     # Let's Encrypt, issued now
... @ install --cert /path/fullchain.pem --key /path/privkey.pem
... @ install --no-ssl                           # plain HTTP, see the warning
```

For `--ssl-domain` the domain's A record must already point at this server and
port 80 must be free, because the certificate is issued over HTTP-01. A renewal
hook is installed so the panel picks up each renewal without you doing anything.

`--no-ssl` serves the dashboard over plain HTTP. Your admin password and every
subscription link then travel in clear text. It is there for a panel behind a
reverse proxy that terminates TLS itself, and for nothing else.

To add or replace a certificate later:

```bash
nexus ssl --domain panel.example.com
nexus ssl --cert /path/fullchain.pem --key /path/privkey.pem
```

## Other flags

| Flag | Effect |
| --- | --- |
| `--port 8000` | which port the dashboard listens on |
| `--image REF` | install a specific image instead of `ghcr.io/nexusguide/nexus-panel:latest` |
| `--no-enable` | install with the free-config feature switched off |
| `-y`, `--yes` | answer yes to every prompt — for unattended installs |

`--no-enable` sets `FREE_CONFIGS_ENABLED=false` in `.env`. That is the only
switch that can turn the feature on: the panel's own settings page can switch it
off but never on, so a panel that never opted in cannot be enabled by anyone who
gets as far as a web form.

## Updating

```bash
nexus apply
```

Pulls the current image, recreates the containers and runs any database
migrations. Your `.env`, your data and your certificate are untouched. This is
the whole update procedure; there is no separate migration step to remember.

New versions are announced in the dashboard when this repository publishes a
release, and the banner shows the same command.

## Coming from PasarGuard

```bash
nexus migrate
```

Stops the containers at `/opt/pasarguard`, **copies** (never moves) the data to
`/var/lib/nexus`, rewrites the parts of `.env` that name paths or ports, and
starts the panel under the new name. `/opt/pasarguard` and `/var/lib/pasarguard`
are left exactly as they were, so if anything goes wrong you can start the old
containers again and be back where you started.

Users, admins, nodes, groups and usage history all come across — it is the same
database schema.

## Backups

```bash
nexus backup           # writes /opt/nexus/backups/nexus-<timestamp>.tar.gz
nexus restore FILE     # puts one back
```

The archive holds `/var/lib/nexus` and `/opt/nexus`, with a consistent SQL dump
in place of the live database directory. Restoring overwrites the current
install, so take a backup before you restore one.

Copy the archives somewhere else. A backup that lives only on the machine it
backs up is not a backup.

## Uninstalling

```bash
nexus uninstall
```

Removes the containers, `/opt/nexus` and the `nexus` command. It asks separately
before deleting `/var/lib/nexus`, because that is where your users are. Answer
no and the data stays for a later reinstall.

## When something is wrong

```bash
nexus status          # are the containers up
nexus logs            # the last 200 lines
nexus logs -f         # follow
nexus restart         # recreate the containers
```

`nexus restart` recreates rather than restarts, because a container keeps the
environment it was created with — after editing `.env`, a plain `docker restart`
looks like it worked and changes nothing.
