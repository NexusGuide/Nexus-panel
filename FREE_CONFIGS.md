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
3. Each endpoint gets a TCP connect test with a latency measurement, run with bounded
   concurrency.
4. The pool is replaced wholesale — entries that vanished upstream stop being served.
5. On a subscription request, eligible users get the healthy pool appended, fastest first.

### Two honest limitations

- **"Healthy" means the port answered a TCP connect from your panel's server.** It does
  not prove the proxy protocol works, and it does not prove the endpoint is reachable
  from your end user's network. If your panel and your users are in different countries,
  these are very different questions.
- **Only the `links` and `links_base64` subscription formats get free configs.** Clash,
  sing-box, Xray, Outline and WireGuard describe outbounds field by field and cannot
  carry a foreign URI verbatim. This is the same limitation upstream's own
  `EXTERNAL_CONFIG` option has.

---

## Setup

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
| `FREE_CONFIGS_MAX_PER_SUBSCRIPTION` | `0` | cap per user subscription (0 = all healthy) |
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
| `GET` | `/api/free-configs/configs` | inspect the pool |
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
