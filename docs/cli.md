# The `nexus` command

Installed at `/usr/local/bin/nexus`. Run it with no arguments for a summary.

Commands that change the install need root. Ones that only read do not.

## Running the panel

| Command | What it does |
| --- | --- |
| `nexus status` | which containers are up, from `docker compose ps` |
| `nexus logs` | the last 200 lines |
| `nexus logs -f` | follow |
| `nexus logs panel` | one service only (`panel` or `db`) |
| `nexus start` | start the containers |
| `nexus stop` | stop them |
| `nexus restart` | recreate them |

`restart` recreates rather than restarts on purpose. A running container keeps
the environment it was created with, so restarting it after an `.env` edit
appears to work and quietly changes nothing.

## Updating

| Command | What it does |
| --- | --- |
| `nexus apply` | pull the current image, recreate, run migrations |
| `nexus apply --image REF` | the same, with a specific image |

`nexus update` is accepted as a spelling of `apply`.

`--image` also tells the installer the image is yours, so it will not pull over
it — which is what makes a locally built image usable for testing.

## Certificates

| Command | What it does |
| --- | --- |
| `nexus ssl --domain panel.example.com` | issue a Let's Encrypt certificate now |
| `nexus ssl --cert FILE --key FILE` | install a certificate you already have |

Either way the panel is recreated afterwards so it picks the certificate up.

## Free configs

| Command | What it does |
| --- | --- |
| `nexus refresh` | rebuild the free-config pool now, and print what happened |

`refresh` runs the same work the panel's "Rebuild pool" button starts, but in
the foreground, and prints the run's own numbers as JSON:

```json
{
  "sources": 10,
  "fetched": 9591,
  "unique": 7715,
  "candidates": 6506,
  "duration_seconds": 37.6,
  "errors": [{"url": "https://example.com/list.txt", "error": "timed out"}]
}
```

That is the first thing to run when a rebuild seems to do nothing: `sources: 0`
means no source is switched on, an empty `candidates` with a large `fetched`
means the endpoints were unreachable from this server, and `errors` names the
lists that failed. [FREE_CONFIGS.md](../FREE_CONFIGS.md) explains what the
feature does with the results.

## Administration

| Command | What it does |
| --- | --- |
| `nexus cli ...` | the panel's own CLI inside the container |
| `nexus tui` | the panel's terminal interface |
| `nexus edit` | open `docker-compose.yml` in `$EDITOR` |
| `nexus edit-env` | open `.env` in `$EDITOR` |

`nexus cli` and `nexus tui` attach a terminal when they have one, which is what
keeps their colours and prompts working — without it the temp key below prints
in plain white and is easy to miss.

The CLI is small on purpose; almost everything is done from the dashboard. What
it has:

```bash
nexus cli generate-temp-key   # a one-time key for creating or resetting the owner
nexus cli version             # this panel's version, and the upstream it tracks
nexus cli --help
```

`generate-temp-key` is the way back in when you are locked out: it prints a key,
valid once, that the dashboard's owner-setup screen accepts to create, reset or
delete the owner account. It is printed in yellow and not stored anywhere.

After `nexus edit-env`, run `nexus restart` — the running container is still
using the old values until it is recreated.

## Data

| Command | What it does |
| --- | --- |
| `nexus backup` | write `/opt/nexus/backups/nexus-<timestamp>.tar.gz` |
| `nexus restore FILE` | put one back, replacing the current install |
| `nexus migrate` | adopt an existing PasarGuard install at `/opt/pasarguard` |
| `nexus uninstall` | remove the containers, `/opt/nexus` and the command |

`uninstall` asks separately before deleting `/var/lib/nexus`. Say no and your
users survive a reinstall.

## Global flags

These are accepted anywhere on the line:

| Flag | Meaning |
| --- | --- |
| `--database NAME` | only meaningful for `install`; every other command reads the recorded one |
| `--image REF` | the image to use |
| `--port N` | the dashboard port |
| `--ssl-domain D` / `--domain D` | issue for this domain |
| `--cert FILE` `--key FILE` | use this certificate |
| `--no-ssl` | plain HTTP |
| `--no-enable` | install with free configs off |
| `-y`, `--yes` | assume yes at every prompt |
