# Free Configs add-on

> This is a **fork feature**. It is not part of upstream [PasarGuard/panel](https://github.com/PasarGuard/panel).

Harvests proxy URIs from public community lists, health-checks them, and appends the
healthy ones to the subscription output of opted-in users.

Everything is **off by default**: with `FREE_CONFIGS_ENABLED` unset, the panel behaves
exactly like upstream.

---

## Why it exists, and what it is not

PasarGuard manages *nodes* — machines you own, running the node daemon, where the panel
mints a unique UUID per customer, meters their traffic, and can cut off one user without
touching the rest.

Free community configs are the opposite of that: they are pre-built URIs pointing at
**servers operated by strangers**, with credentials that were published to the whole
internet. So they cannot be nodes, and this feature does not pretend otherwise:

|                                | Real node | Free config |
| ------------------------------ | :-------: | :---------: |
| Per-user credentials           | ✅ | ❌ (shared, public) |
| Traffic metering / data limits | ✅ | ❌ |
| Disconnect a single user       | ✅ | ❌ |
| Uptime you control             | ✅ | ❌ |

**Do not sell these as a metered service.** They are appropriate as a clearly-labelled
free tier, a trial, or a fallback — and every entry is prefixed in the client
(`🆓` by default) so users can see which is which.

---

## How it works

```
sources (DB)  ──fetch──▶  parse & dedupe  ──TCP health check──▶  pool (DB)
                                                                    │
                                             cached 60s ────────────┤
                                                                    ▼
                                    subscription render ──▶ appended to links output
```

1. A scheduled job (default: every 24h) fetches every enabled source.
2. Lines are parsed into `(protocol, address, port)`; unparsable lines are dropped and
   duplicates removed by SHA-256 of the URI.
3. Configs are grouped by `(address, port)` and **each endpoint is probed once**, with a
   latency measurement, under bounded concurrency. Community lists overlap heavily —
   roughly twelve thousand configs come from an order of magnitude fewer servers — so
   this is what makes checking the whole pool affordable instead of an arbitrary slice.
4. At most `MAX_PER_ENDPOINT` configs from any one server are kept, since they all fail
   together when it goes down.
5. The pool is replaced wholesale — entries that vanished upstream stop being served.
6. On a subscription request, eligible users get the fastest `MAX_PER_SUBSCRIPTION` of
   them appended.

### Two honest limitations

- **"Healthy" means the port answered a TCP connect from your panel's server.** It does
  not prove the proxy protocol works, and it does not prove the endpoint is reachable
  from your end user's network. If your panel and your users are in different countries,
  these are very different questions. In practice the pass rate is high — an endpoint
  behind Cloudflare answers on 443 whether or not its proxy is alive — so treat the
  latency ordering, not the healthy flag, as the useful signal. Real verification would
  mean speaking each protocol with xray-core; that is not implemented here.
- **Only the `links` and `links_base64` subscription formats get free configs.** Clash,
  sing-box, Xray, Outline and WireGuard describe outbounds field by field and cannot
  carry a foreign URI verbatim. This is the same limitation upstream's own
  `EXTERNAL_CONFIG` option has.

---

## Install

On a fresh VPS:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mezixa/pasarguard-free-configs/main/install.sh)" @ install
```

This is a thin wrapper around **PasarGuard's own installer**, not a second one.
Theirs already handles five database backends, Let's Encrypt certificates (by
domain or by IP), automatic renewal, backup/restore, systemd, the CLI and TUI,
and updates — roughly 1,750 lines of it. Reimplementing any of that would only
add bugs. So the wrapper runs the official installer and then changes the two
things that make this a fork:

1. points `/opt/pasarguard/docker-compose.yml` at this fork's image
2. adds the `FREE_CONFIGS_*` settings to `/opt/pasarguard/.env`

Every flag goes straight through to the official installer, so anything
documented there works:

```bash
... @ install --database postgresql --ssl-domain panel.example.com
```

Fork-specific flags: `--image <ref>` (use a different or locally built image),
`--no-seed`, `--no-enable`.

Day-to-day management is the official command — `pasarguard logs | restart |
status | cli | backup | uninstall`. Only these are the wrapper's:

| Command | Purpose |
| --- | --- |
| `install` | official install, then apply the fork |
| `apply` | re-apply the fork to an existing install |
| `update` | official update, then re-apply the fork |
| `refresh` | rebuild the free-configs pool now |

> **An official `pasarguard update` resets the image back to upstream**, since
> the version is hardcoded there. Use `install.sh update`, or run
> `install.sh apply` afterwards.

> The image is `ghcr.io/mezixa/pasarguard-free-configs:latest`, published by
> [`build-fork.yml`](.github/workflows/build-fork.yml) on every push to `main`.
> **After the first CI run, make the GHCR package public** (repo → Packages →
> Package settings → Change visibility), or the pull fails for everyone else.
> Until then, build it yourself and pass `--image`:
> ```bash
> git clone https://github.com/Mezixa/pasarguard-free-configs.git
> cd pasarguard-free-configs
> docker build --network=host -t pasarguard-free-configs:dev .
> sudo bash install.sh install --image pasarguard-free-configs:dev
> ```
> (`--network=host` because Docker's bridge network cannot resolve DNS on many
> VPSes, which makes `apt-get` inside the build fail.)
>
> A local build is a complete panel, web UI included: the Dockerfile compiles
> the dashboard in its own stage, so a clone and one `docker build` give the
> same image CI publishes.

## Running from source (development)

### 1. Migrate

```bash
alembic upgrade head
```

Creates `free_config_sources`, `free_configs`, and `free_config_group_access`.

### 2. Configure

In `.env`:

```ini
FREE_CONFIGS_ENABLED = true
FREE_CONFIGS_MODE = "groups"          # or "all"
FREE_CONFIGS_REFRESH_INTERVAL = 86400
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `FREE_CONFIGS_ENABLED` | `false` | master switch |
| `FREE_CONFIGS_MODE` | `all` | `all` = every user, `groups` = only opted-in groups |
| `FREE_CONFIGS_REFRESH_INTERVAL` | `86400` | seconds between pool rebuilds |
| `FREE_CONFIGS_FETCH_TIMEOUT` | `20` | per-source HTTP timeout |
| `FREE_CONFIGS_HEALTH_CHECK` | `true` | set `false` to keep every config unchecked |
| `FREE_CONFIGS_TCP_TIMEOUT` | `3` | per-endpoint connect timeout |
| `FREE_CONFIGS_MAX_CONCURRENCY` | `50` | parallel health checks |
| `FREE_CONFIGS_MAX_CONFIGS` | `0` | cap on configs checked per refresh (0 = no cap) |
| `FREE_CONFIGS_MAX_PER_ENDPOINT` | `3` | how many configs one server may contribute (0 = all) |
| `FREE_CONFIGS_MAX_PER_SUBSCRIPTION` | `100` | cap per user subscription (0 = all healthy) |
| `FREE_CONFIGS_REMARK_PREFIX` | `🆓` | label prepended to each free entry |

### 3. Add sources

Either seed the well-known community lists:

```bash
python3 scripts/seed_free_configs.py
python3 scripts/seed_free_configs.py --list
```

The seeded list is the one published by
[patterniha/free-configs](https://github.com/patterniha/free-configs) (MIT), plus that
project's own aggregated output as a tenth source. None of its code is used here — this
is an independent implementation of the same idea: aggregate several public lists, then
filter by real connectivity instead of trusting the list. Sources are ordinary DB rows,
so add, disable or remove any of them at will.

…or add your own through the API:

```bash
curl -X POST https://panel.example.com/api/free-configs/sources \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/list.txt", "remark": "my list", "is_base64": false}'
```

### 4. Choose who gets them

With `FREE_CONFIGS_MODE=groups`, opt specific groups in:

```bash
curl -X PUT https://panel.example.com/api/free-configs/groups \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"group_ids": [3]}'
```

### 5. Refresh

```bash
curl -X POST https://panel.example.com/api/free-configs/refresh -H "Authorization: Bearer $TOKEN"
curl https://panel.example.com/api/free-configs/status -H "Authorization: Bearer $TOKEN"
```

---

## The panel page

**Free Configs** appears in the dashboard's sidebar for the owner. Three tabs:

- **Pool** - every config, fastest first. Search by address or URI, filter by
  protocol or state, switch entries on and off individually or in bulk, rename
  them, and paste in configs by hand. Click a row to open the **editor**:
  address, port, UUID or password, transport, host, path, TLS, SNI,
  fingerprint, ALPN - every parameter the config carries, with the resulting URI
  shown live as you type. Parameters can be added or removed, including keys the
  panel has never heard of.
- **Sources** - add, rename, enable, disable and delete the lists that get
  harvested, with each one's last fetch count and error.
- **Settings** - everything below, editable without a restart, plus a switch
  that stops serving free configs entirely.

Two decisions worth knowing about, because they are not obvious from the UI:

- **Your choices are stored against the config's content hash, not its row.**
  The pool is emptied and rebuilt on every refresh, so a "don't serve this"
  stored on a row would be gone within a day. Keyed by hash, it is re-applied to
  whatever the next refresh brings back - and still there if a config disappears
  from every source for a week and then returns.
- **The page can switch the feature off, never on.** `FREE_CONFIGS_ENABLED`
  stays in `.env`, so an install that never opted in cannot be enabled through a
  web form. Everything else in the settings tab overrides its `.env` value; an
  empty field means "use `.env`".

Configs added by hand are kept across refreshes, are never dropped by the
per-server cap, and are served even if the health check cannot reach them - you
added them deliberately, and the pool does not second-guess that.

**Editing follows from that.** Change an address, credential or SNI and it is no
longer the config the source published, so it is saved as a hand-added entry and
the original is switched off rather than deleted - the next refresh harvests the
original again, and the override keeps it off instead of letting it reappear
beside its own replacement. Editing only the name leaves the config where it is.

Nothing is dropped in the process: a URI is split into the parts that are
structural (scheme, credentials, address, port, name) and a plain dictionary of
everything else, then rebuilt from the same. A parameter the panel does not
recognise still shows as an editable row and survives the round trip.

## API

All endpoints are **owner-only** — injecting third-party servers into user subscriptions
is a panel-wide trust decision, so it is not delegated to sub-admins. (It also means this
fork adds no new RBAC resource, which keeps the diff against upstream small.)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/free-configs/sources` | list sources |
| `POST` | `/api/free-configs/sources` | add a source |
| `PUT` | `/api/free-configs/sources/{id}` | enable/disable, edit remark |
| `DELETE` | `/api/free-configs/sources/{id}` | delete a source |
| `GET` | `/api/free-configs/status` | settings, last refresh, pool size |
| `GET` | `/api/free-configs/configs` | page through the pool (search, filter) |
| `PUT` | `/api/free-configs/configs/{hash}` | enable/disable, rename, annotate |
| `GET` | `/api/free-configs/configs/{hash}/fields` | the config broken into editable fields |
| `PUT` | `/api/free-configs/configs/{hash}/fields` | save an edited config |
| `POST` | `/api/free-configs/configs/bulk` | enable/disable many at once |
| `POST` | `/api/free-configs/configs/manual` | add a config by hand |
| `DELETE` | `/api/free-configs/configs/{hash}` | forget an override |
| `GET` | `/api/free-configs/settings` | effective settings, overrides and defaults |
| `PUT` | `/api/free-configs/settings` | change settings without a restart |
| `POST` | `/api/free-configs/refresh` | trigger a refresh (async, `202`) |
| `GET` | `/api/free-configs/groups` | read group opt-in list |
| `PUT` | `/api/free-configs/groups` | replace group opt-in list |

---

## Files in this fork

Additive (new files):

```
app/free_configs/{__init__,models,parser,fetcher,crud,service,schemas,subscription}.py
app/jobs/free_configs.py
app/routers/free_configs.py
app/db/migrations/versions/9f2c1a7b4e05_add_free_configs_tables.py
scripts/seed_free_configs.py
tests/test_free_configs_parser.py
install.sh
.github/workflows/build-fork.yml
FREE_CONFIGS.md
```

Modified upstream files — deliberately kept tiny so rebasing on new upstream releases
stays cheap:

| File | Change |
| --- | --- |
| `config.py` | `FreeConfigsSettings` class + instance |
| `app/subscription/share.py` | one import + one call before `conf.render()` |
| `app/routers/__init__.py` | register the router |
| `.env.example` | document the new variables |

The models live in `app/free_configs/models.py` and register themselves on the shared
`Base.metadata`, so `app/db/models.py` is untouched.

## Tests

```bash
pytest tests/test_free_configs_parser.py
```

Covers URI parsing for vmess/vless/trojan/shadowsocks (both forms)/hysteria2/tuic,
base64 bodies with and without padding, deduplication, and remark labelling
(including idempotency and never mangling a config it cannot rewrite).
