#!/usr/bin/env bash
#
# Nexus Panel installer.
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install
#
# This is Nexus Panel's own installer: one self-contained file that owns its
# paths, its command and its compose files. It deliberately does not download
# helper libraries at runtime - everything it needs is here, so what you read is
# what runs, and a network hiccup halfway through cannot leave a half-updated
# set of scripts behind.
#
# The panel is a fork of PasarGuard, and their installer is a fine piece of
# work, but it is published with no licence at all - which means nobody may copy
# or redistribute it. So none of it is reused here. This is written from scratch
# against what the panel actually needs, which turns out to be little: a compose
# file, an .env, a data directory and a certificate.
#
# Commands:
#   install     set up a new panel
#   apply       pull the current image and restart - this is how you update
#   migrate     adopt an existing PasarGuard install at /opt/pasarguard
#   ssl         issue or replace the certificate
#   logs | status | restart | start | stop | cli | tui | edit | edit-env
#   backup | restore | refresh | uninstall
#
set -euo pipefail

BRAND="Nexus Panel"
REPO="NexusGuide/Nexus-panel"
IMAGE_DEFAULT="ghcr.io/nexusguide/nexus-panel:latest"
INSTALLER_URL="https://raw.githubusercontent.com/${REPO}/main/install.sh"

APP_NAME="nexus"
APP_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
CERT_DIR="${DATA_DIR}/certs"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"
ENV_FILE="${APP_DIR}/.env"
CMD_PATH="/usr/local/bin/${APP_NAME}"
COMPOSE_PROJECT="${APP_NAME}"
# which backend this install uses, written at install time (see record_database)
BACKEND_FILE="${APP_DIR}/backend"

# the install this fork grew out of, for `migrate`
LEGACY_DIR="/opt/pasarguard"
LEGACY_DATA="/var/lib/pasarguard"

IMAGE="$IMAGE_DEFAULT"
DATABASE="sqlite"
PANEL_PORT="8000"
SSL_MODE=""
SSL_DOMAIN=""
SSL_CERT=""
SSL_KEY=""
FREE_CONFIGS_ENABLED="true"
ASSUME_YES=0

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { printf '%s==>%s %s\n' "$GREEN" "$RESET" "$*"; }
step() { printf '%s==>%s %s\n' "$BLUE" "$RESET" "$*"; }
warn() { printf '%s==>%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%s==> error:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

require_root() { [ "$(id -u)" -eq 0 ] || die "run this as root (use sudo)"; }

compose() { docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@"; }

confirm() {
    # A run piped from curl has no usable stdin, and cron has no tty at all.
    # Defaulting to "no" there is what keeps an unattended run from hanging
    # forever on a question nobody will answer.
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ -e /dev/tty ] || return 1
    local reply
    read -r -p "$1 [y/N] " reply </dev/tty || return 1
    # Some terminals - web consoles especially - deliver the carriage return
    # along with the newline, so the answer arrives as $'y\r' and an exact match
    # against "y" quietly rejects a perfectly good yes. Strip whitespace before
    # comparing rather than trusting the terminal to be tidy.
    reply="${reply//[[:space:]]/}"
    case "$reply" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

random_secret() {
    # tr exits non-zero once head has taken its fill and closes the pipe; the
    # subshell keeps `set -o pipefail` from treating that as a failure.
    ( LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "${1:-32}" ) || true
}

# --------------------------------------------------------------------------- #
# prerequisites
# --------------------------------------------------------------------------- #

detect_pkg_manager() {
    if command -v apt-get >/dev/null 2>&1; then echo apt
    elif command -v dnf >/dev/null 2>&1; then echo dnf
    elif command -v yum >/dev/null 2>&1; then echo yum
    else echo ""; fi
}

install_packages() {
    local pm
    pm="$(detect_pkg_manager)"
    case "$pm" in
        apt) DEBIAN_FRONTEND=noninteractive apt-get update -qq &&
             DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
        dnf) dnf install -y -q "$@" ;;
        yum) yum install -y -q "$@" ;;
        *)   die "unsupported distribution: install $* by hand, then re-run" ;;
    esac
}

ensure_base_tools() {
    local missing=()
    command -v curl >/dev/null 2>&1 || missing+=(curl)
    command -v tar >/dev/null 2>&1 || missing+=(tar)
    if [ "${#missing[@]}" -gt 0 ]; then
        step "installing ${missing[*]} ..."
        install_packages "${missing[@]}" ca-certificates
    fi
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        return
    fi
    step "installing Docker ..."
    # Docker's own convenience script is the method Docker documents and it
    # covers every distribution we care about; hand-rolling repository setup per
    # distro would be a second installer hiding inside this one.
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "could not download the Docker installer"
    sh /tmp/get-docker.sh >/dev/null || die "Docker installation failed"
    rm -f /tmp/get-docker.sh
    systemctl enable --now docker >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing after install"
}

# --------------------------------------------------------------------------- #
# compose + env
# --------------------------------------------------------------------------- #

db_service_block() {
    # Nothing at all for sqlite. Everything else gets a container whose port is
    # published on the loopback only: the panel reaches it over the host network
    # and nothing outside this machine has any business connecting to it.
    case "$DATABASE" in
        sqlite) return ;;
        mysql|mariadb)
            local image="mysql:8"
            [ "$DATABASE" = "mariadb" ] && image="mariadb:11"
            cat <<EOF

  db:
    image: ${image}
    restart: always
    environment:
      MYSQL_ROOT_PASSWORD: \${DB_PASSWORD}
      MYSQL_DATABASE: \${DB_NAME}
    volumes:
      - ${DATA_DIR}/db:/var/lib/mysql
    ports:
      - "127.0.0.1:3306:3306"
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p\"\$\$MYSQL_ROOT_PASSWORD\""]
      interval: 10s
      timeout: 5s
      retries: 12
EOF
            ;;
        postgresql|timescaledb)
            local image="postgres:17"
            [ "$DATABASE" = "timescaledb" ] && image="timescale/timescaledb:latest-pg17"
            cat <<EOF

  db:
    image: ${image}
    restart: always
    environment:
      POSTGRES_PASSWORD: \${DB_PASSWORD}
      POSTGRES_DB: \${DB_NAME}
      POSTGRES_USER: \${DB_USER}
    volumes:
      - ${DATA_DIR}/db:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \"\$\$POSTGRES_USER\" -d \"\$\$POSTGRES_DB\""]
      interval: 10s
      timeout: 5s
      retries: 12
EOF
            ;;
    esac
}

write_compose() {
    mkdir -p "$APP_DIR"
    {
        cat <<EOF
# Generated by the ${BRAND} installer - edit with \`${APP_NAME} edit\`.
#
# network_mode: host, because the panel serves the dashboard and every inbound
# proxy port straight from this machine, and which ports those are is decided in
# the panel at runtime - there is no fixed set worth publishing.
services:
  panel:
    image: ${IMAGE}
    restart: always
    env_file: ${ENV_FILE}
    network_mode: host
    volumes:
      - ${DATA_DIR}:${DATA_DIR}
EOF
        if [ "$DATABASE" != "sqlite" ]; then
            cat <<EOF
    depends_on:
      db:
        condition: service_healthy
EOF
        fi
        db_service_block
    } > "$COMPOSE_FILE"
    chmod 600 "$COMPOSE_FILE"
}

database_url() {
    # $1 password, $2 name, $3 user
    case "$DATABASE" in
        sqlite)                 printf 'sqlite+aiosqlite:///%s/db.sqlite3' "$DATA_DIR" ;;
        mysql|mariadb)          printf 'mysql+asyncmy://root:%s@127.0.0.1:3306/%s' "$1" "$2" ;;
        postgresql|timescaledb) printf 'postgresql+asyncpg://%s:%s@127.0.0.1:5432/%s' "$3" "$1" "$2" ;;
    esac
}

set_env() {
    # Add a key if it is missing. A value already in the file is left alone - it
    # may be a deliberate choice - but is reported when it differs from what
    # this version suggests, so a stale setting is visible rather than silent.
    local key="$1" value="$2" current
    if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
        current="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed 's/^ *//; s/^"//; s/"$//')"
        if [ "$current" != "$value" ]; then
            warn "${key} is ${current} in ${ENV_FILE}; this version suggests ${value} (left as-is)"
        fi
        return 1
    fi
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    return 0
}

set_env_value() {
    # overwrite unconditionally - for the values the flags own outright
    local key="$1" value="$2"
    touch "$ENV_FILE"
    if grep -qE "^${key}[[:space:]]*=" "$ENV_FILE"; then
        sed -i -E "s|^${key}[[:space:]]*=.*|${key} = ${value}|" "$ENV_FILE"
    else
        printf '%s = %s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

write_env() {
    local db_password db_name db_user
    db_password="$(random_secret 24)"
    db_name="${APP_NAME}"
    db_user="${APP_NAME}"

    mkdir -p "$APP_DIR"
    cat > "$ENV_FILE" <<EOF
# ${BRAND} - ${REPO}
# Generated $(date -u '+%Y-%m-%d %H:%M:%S UTC'). Edit with \`${APP_NAME} edit-env\`.

UVICORN_HOST = "0.0.0.0"
UVICORN_PORT = ${PANEL_PORT}

SQLALCHEMY_DATABASE_URL = "$(database_url "$db_password" "$db_name" "$db_user")"
EOF

    if [ "$DATABASE" != "sqlite" ]; then
        cat >> "$ENV_FILE" <<EOF

DB_NAME = "${db_name}"
DB_USER = "${db_user}"
DB_PASSWORD = "${db_password}"
EOF
    fi

    cat >> "$ENV_FILE" <<EOF

# --- free configs ---------------------------------------------------------
# Community proxy lists are harvested, TCP health-checked, and appended to the
# subscription output of eligible users. The lists themselves are managed in the
# panel: Free Configs -> Sources -> "Add default sources".
FREE_CONFIGS_ENABLED=${FREE_CONFIGS_ENABLED}
FREE_CONFIGS_MODE=all
FREE_CONFIGS_REFRESH_INTERVAL=86400
FREE_CONFIGS_MAX_CONFIGS=0
FREE_CONFIGS_MAX_PER_ENDPOINT=3
FREE_CONFIGS_MAX_PER_SUBSCRIPTION=100
FREE_CONFIGS_TCP_TIMEOUT=3
FREE_CONFIGS_MAX_CONCURRENCY=50
EOF
    chmod 600 "$ENV_FILE"
}

refresh_env_settings() {
    # for `apply` on an install made before a setting existed
    touch "$ENV_FILE"
    set_env FREE_CONFIGS_MODE all || true
    set_env FREE_CONFIGS_REFRESH_INTERVAL 86400 || true
    set_env FREE_CONFIGS_MAX_CONFIGS 0 || true
    set_env FREE_CONFIGS_MAX_PER_ENDPOINT 3 || true
    set_env FREE_CONFIGS_MAX_PER_SUBSCRIPTION 100 || true
    set_env FREE_CONFIGS_TCP_TIMEOUT 3 || true
    set_env FREE_CONFIGS_MAX_CONCURRENCY 50 || true
}

# --------------------------------------------------------------------------- #
# TLS
# --------------------------------------------------------------------------- #

is_port_busy() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
    else
        return 1
    fi
}

copy_certificate() {
    local domain="$1" live="/etc/letsencrypt/live/$1"
    mkdir -p "$CERT_DIR"
    install -m 644 "${live}/fullchain.pem" "${CERT_DIR}/fullchain.pem"
    install -m 600 "${live}/privkey.pem" "${CERT_DIR}/privkey.pem"
}

install_renewal_hook() {
    # The container only sees ${DATA_DIR}, so it reads copies rather than
    # /etc/letsencrypt directly. Copies do not follow a renewal on their own,
    # which is exactly how a panel ends up serving an expired certificate on day
    # ninety-one. This hook is what keeps them in step.
    local domain="$1" hook="/etc/letsencrypt/renewal-hooks/deploy/${APP_NAME}.sh"
    mkdir -p "$(dirname "$hook")"
    cat > "$hook" <<EOF
#!/bin/sh
# installed by the ${BRAND} installer
install -m 644 /etc/letsencrypt/live/${domain}/fullchain.pem ${CERT_DIR}/fullchain.pem
install -m 600 /etc/letsencrypt/live/${domain}/privkey.pem ${CERT_DIR}/privkey.pem
docker compose -p ${COMPOSE_PROJECT} -f ${COMPOSE_FILE} restart panel
EOF
    chmod +x "$hook"
}

issue_certificate() {
    local domain="$1"
    [ -n "$domain" ] || die "no domain given"

    command -v certbot >/dev/null 2>&1 || { step "installing certbot ..."; install_packages certbot; }

    if is_port_busy 80; then
        die "port 80 is in use, and certbot needs it to prove the domain - free it and re-run"
    fi

    step "issuing a certificate for ${domain} ..."
    certbot certonly --standalone --non-interactive --agree-tos \
        --register-unsafely-without-email -d "$domain" \
        || die "certbot could not issue a certificate for ${domain}
check that its DNS A record points at this server's public address"

    copy_certificate "$domain"
    install_renewal_hook "$domain"

    set_env_value UVICORN_SSL_CERTFILE "\"${CERT_DIR}/fullchain.pem\""
    set_env_value UVICORN_SSL_KEYFILE "\"${CERT_DIR}/privkey.pem\""
    set_env_value UVICORN_HOST '"0.0.0.0"'
}

use_custom_certificate() {
    [ -f "$SSL_CERT" ] || die "certificate not found: ${SSL_CERT}"
    [ -f "$SSL_KEY" ] || die "key not found: ${SSL_KEY}"
    mkdir -p "$CERT_DIR"
    install -m 644 "$SSL_CERT" "${CERT_DIR}/fullchain.pem"
    install -m 600 "$SSL_KEY" "${CERT_DIR}/privkey.pem"
    set_env_value UVICORN_SSL_CERTFILE "\"${CERT_DIR}/fullchain.pem\""
    set_env_value UVICORN_SSL_KEYFILE "\"${CERT_DIR}/privkey.pem\""
    set_env_value UVICORN_HOST '"0.0.0.0"'
}

setup_ssl() {
    case "$SSL_MODE" in
        domain) issue_certificate "$SSL_DOMAIN" ;;
        custom) use_custom_certificate ;;
        *)
            # Without a certificate the panel binds to the loopback on purpose:
            # a dashboard and subscription links served over plain http to the
            # open internet is not a default anyone should get by accident.
            warn "no certificate configured, so the panel will listen on localhost only."
            warn "reach it with:  ssh -L ${PANEL_PORT}:localhost:${PANEL_PORT} root@<this server>"
            warn "add one later with:  ${APP_NAME} ssl --domain panel.example.com"
            set_env_value UVICORN_HOST '"127.0.0.1"'
            ;;
    esac
}

# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #

installed() { [ -f "$COMPOSE_FILE" ]; }

require_installed() {
    installed || die "no ${BRAND} install found at ${APP_DIR} - run \`${APP_NAME} install\` first"
}

panel_container() { compose ps -q panel 2>/dev/null | head -1; }

panel_exec() {
    local id
    id="$(panel_container)"
    [ -n "$id" ] || die "the panel container is not running - try \`${APP_NAME} logs\`"
    docker exec -i -w /code "$id" "$@"
}

wait_for_panel() {
    # The container accepts exec long before it is useful: start.sh runs the
    # migrations first. So wait for the schema, not for the process.
    step "waiting for the database migrations to finish ..."
    local id
    for _ in $(seq 1 90); do
        id="$(panel_container)"
        if [ -n "$id" ] && docker exec -i -w /code "$id" python -c "
import asyncio
from sqlalchemy import text
from app.db import GetDB
async def main():
    async with GetDB() as db:
        await db.execute(text('select 1 from free_config_sources'))
asyncio.run(main())" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    warn "the panel did not finish migrating within three minutes - check \`${APP_NAME} logs\`"
    return 1
}

pull_and_up() {
    step "pulling ${IMAGE} ..."
    compose pull panel || die "could not pull ${IMAGE}
if the package is still private, make it public:
  https://github.com/${REPO} -> Packages -> Package settings -> Change visibility"
    step "starting ..."
    # A container keeps the environment it was given when it was created:
    # `restart` never re-reads the env file, and a plain `up -d` reports
    # "up-to-date" when only that file changed. Recreating is the one thing that
    # reliably picks up both a new image and new settings.
    compose up -d --force-recreate
}

record_database() { mkdir -p "$APP_DIR"; printf '%s\n' "$DATABASE" > "$BACKEND_FILE"; }

detect_database() {
    # The url cannot answer this on its own: TimescaleDB and PostgreSQL share
    # postgresql+asyncpg, and MariaDB and MySQL share asyncmy. Guessing wrong
    # matters - rewriting a TimescaleDB install as plain postgres:17 leaves the
    # server unable to open its own data directory. So the answer is recorded at
    # install time and the url is only a fallback for installs made before this,
    # and for `restore`, where the archive may predate the file.
    if [ -s "$BACKEND_FILE" ]; then
        tr -d '[:space:]' < "$BACKEND_FILE"
        return
    fi
    local url="" image=""
    [ -f "$ENV_FILE" ] && url="$(grep -E '^SQLALCHEMY_DATABASE_URL' "$ENV_FILE" | tail -1 || true)"
    [ -f "$COMPOSE_FILE" ] && image="$(grep -E '^[[:space:]]*image:' "$COMPOSE_FILE" | tail -1 || true)"
    case "${url}${image}" in
        *timescale*)       echo timescaledb ;;
        *postgresql*|*postgres:*) echo postgresql ;;
        *mariadb*)         echo mariadb ;;
        *asyncmy*|*mysql*) echo mysql ;;
        *)                 echo sqlite ;;
    esac
}

install_command() {
    step "installing the ${BOLD}${APP_NAME}${RESET} command ..."
    if ! curl -fsSL "$INSTALLER_URL" -o "${CMD_PATH}.tmp" 2>/dev/null; then
        # running from a local checkout rather than the published one-liner
        cp "$0" "${CMD_PATH}.tmp" 2>/dev/null || {
            warn "could not install the ${APP_NAME} command; use the curl one-liner instead"
            return 0
        }
    fi
    chmod +x "${CMD_PATH}.tmp"
    mv "${CMD_PATH}.tmp" "$CMD_PATH"
}

# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

cmd_install() {
    require_root
    if installed; then
        warn "${BRAND} is already installed at ${APP_DIR}."
        warn "to update it, run:  ${APP_NAME} apply"
        die "refusing to install over an existing install"
    fi
    if [ -d "$LEGACY_DIR" ]; then
        warn "there is a PasarGuard install at ${LEGACY_DIR}."
        warn "to keep its users and data, run:  ${APP_NAME} migrate"
        confirm "install a fresh panel anyway?" || die "aborted"
    fi

    ensure_base_tools
    ensure_docker

    mkdir -p "$APP_DIR" "$DATA_DIR"
    chmod 700 "$DATA_DIR"

    step "writing ${COMPOSE_FILE} (${DATABASE}) ..."
    write_compose
    record_database
    step "writing ${ENV_FILE} ..."
    write_env
    setup_ssl

    pull_and_up
    wait_for_panel || true
    install_command
    summary
}

cmd_apply() {
    require_root
    require_installed
    # re-point at whatever --image says, or at the published default
    sed -i -E "s#^([[:space:]]*image:[[:space:]]*).*(nexus-panel|pasarguard/panel).*\$#\1${IMAGE}#" "$COMPOSE_FILE"
    refresh_env_settings
    pull_and_up
    install_command
    summary
}

cmd_migrate() {
    require_root
    [ -d "$LEGACY_DIR" ] || die "nothing to migrate: ${LEGACY_DIR} does not exist"
    ! installed || die "${APP_DIR} already exists - move it aside first"

    warn "this moves the panel from ${LEGACY_DIR} to ${APP_DIR}."
    warn "the old containers are stopped and the data is copied, not moved, so"
    warn "${LEGACY_DIR} and ${LEGACY_DATA} stay untouched as a fallback."
    confirm "continue?" || die "aborted"

    ensure_docker

    if [ -f "${LEGACY_DIR}/docker-compose.yml" ]; then
        step "stopping the old panel ..."
        docker compose -f "${LEGACY_DIR}/docker-compose.yml" down 2>/dev/null || true
    fi

    mkdir -p "$APP_DIR" "$DATA_DIR"
    chmod 700 "$DATA_DIR"
    if [ -d "$LEGACY_DATA" ]; then
        step "copying data ..."
        cp -a "${LEGACY_DATA}/." "${DATA_DIR}/"
    fi
    if [ -f "${LEGACY_DIR}/.env" ]; then
        step "carrying over settings ..."
        cp -a "${LEGACY_DIR}/.env" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        # every path in the old file points into the old directories
        sed -i "s#${LEGACY_DATA}#${DATA_DIR}#g; s#${LEGACY_DIR}#${APP_DIR}#g" "$ENV_FILE"
    fi

    DATABASE="$(detect_legacy_database)"
    step "detected database: ${DATABASE}"
    if [ "$DATABASE" != "sqlite" ]; then
        warn "an external database keeps its own volume. This writes a fresh db"
        warn "container definition; if the old one held your data, restore it into"
        warn "the new one with:  ${APP_NAME} restore <backup>"
    fi
    write_compose
    record_database
    refresh_env_settings

    pull_and_up
    wait_for_panel || true
    install_command
    summary
    warn "the old install is still at ${LEGACY_DIR} - remove it once you are happy."
}

detect_legacy_database() {
    # the old install's compose file names the image, which is the only place
    # timescaledb and postgresql actually differ
    local image=""
    [ -f "${LEGACY_DIR}/docker-compose.yml" ] &&
        image="$(grep -E '^[[:space:]]*image:' "${LEGACY_DIR}/docker-compose.yml" || true)"
    case "$image" in
        *timescale*)  echo timescaledb ;;
        *postgres*)   echo postgresql ;;
        *mariadb*)    echo mariadb ;;
        *mysql*)      echo mysql ;;
        *)            echo sqlite ;;
    esac
}

cmd_ssl() {
    require_root
    require_installed
    case "$SSL_MODE" in
        domain) issue_certificate "$SSL_DOMAIN" ;;
        custom) use_custom_certificate ;;
        *) die "use:  ${APP_NAME} ssl --domain panel.example.com
 or:  ${APP_NAME} ssl --cert /path/fullchain.pem --key /path/privkey.pem" ;;
    esac
    compose up -d --force-recreate panel
    info "done - the dashboard should answer on https://${SSL_DOMAIN:-your-domain}:${PANEL_PORT}/dashboard/"
}

dump_database() {
    local id
    id="$(compose ps -q db 2>/dev/null | head -1)"
    [ -n "$id" ] || die "the database container is not running"
    case "$DATABASE" in
        mysql|mariadb)
            docker exec -i "$id" sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' ;;
        postgresql|timescaledb)
            docker exec -i "$id" sh -c 'exec pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' ;;
    esac
}

cmd_backup() {
    require_root
    require_installed
    local stamp archive dump
    stamp="$(date -u '+%Y%m%d-%H%M%S')"
    mkdir -p "${APP_DIR}/backups"
    archive="${APP_DIR}/backups/${APP_NAME}-${stamp}.tar.gz"

    dump="${DATA_DIR}/database.sql"
    if [ "$DATABASE" != "sqlite" ]; then
        step "dumping the ${DATABASE} database ..."
        dump_database > "$dump" || { rm -f "$dump"; die "database dump failed"; }
    fi

    step "archiving ..."
    # the db directory is the live datadir of a running server; the dump above
    # is the consistent copy, so there is no reason to carry both
    tar -czf "$archive" \
        --exclude="${DATA_DIR#/}/db" \
        --exclude="${APP_DIR#/}/backups" \
        -C / "${DATA_DIR#/}" "${APP_DIR#/}"
    rm -f "$dump"
    info "backup written to ${archive}"
}

cmd_restore() {
    require_root
    local archive="${1:-}"
    [ -n "$archive" ] || die "use:  ${APP_NAME} restore /path/to/backup.tar.gz"
    [ -f "$archive" ] || die "no such file: ${archive}"

    warn "this replaces ${DATA_DIR} and ${APP_DIR} with the contents of the archive."
    confirm "continue?" || die "aborted"

    if installed; then compose down 2>/dev/null || true; fi
    tar -xzf "$archive" -C /
    DATABASE="$(detect_database)"

    pull_and_up
    if [ "$DATABASE" != "sqlite" ] && [ -f "${DATA_DIR}/database.sql" ]; then
        step "loading the database dump ..."
        local id
        for _ in $(seq 1 60); do
            id="$(compose ps -q db 2>/dev/null | head -1)"
            [ -n "$id" ] && break
            sleep 2
        done
        [ -n "$id" ] || die "the database container did not start"
        sleep 10
        case "$DATABASE" in
            mysql|mariadb)
                docker exec -i "$id" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < "${DATA_DIR}/database.sql" ;;
            postgresql|timescaledb)
                docker exec -i "$id" sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "${DATA_DIR}/database.sql" >/dev/null ;;
        esac
        compose restart panel
    fi
    info "restored from ${archive}"
}

cmd_refresh() {
    require_installed
    panel_exec python -c "
import asyncio, json
from app.free_configs.service import refresh_pool
print(json.dumps(asyncio.run(refresh_pool()), indent=2))"
}

cmd_uninstall() {
    require_root
    require_installed
    local purge=0
    warn "this removes the containers, ${APP_DIR} and the ${APP_NAME} command."
    confirm "also delete ${DATA_DIR} (users, database, certificates)?" && purge=1
    confirm "proceed with the uninstall?" || die "aborted"
    compose down --remove-orphans 2>/dev/null || true
    rm -rf "$APP_DIR"
    rm -f "$CMD_PATH" "/etc/letsencrypt/renewal-hooks/deploy/${APP_NAME}.sh"
    if [ "$purge" -eq 1 ]; then
        rm -rf "$DATA_DIR"
        info "removed, including ${DATA_DIR}"
    else
        info "removed - ${DATA_DIR} was kept"
    fi
}

summary() {
    local scheme="http" host="localhost"
    if grep -qE '^UVICORN_SSL_CERTFILE' "$ENV_FILE" 2>/dev/null; then scheme="https"; fi
    [ -n "$SSL_DOMAIN" ] && host="$SSL_DOMAIN"
    cat <<EOF

${GREEN}${BOLD}Done.${RESET} ${BRAND} is installed and running.

  dashboard   ${scheme}://${host}:${PANEL_PORT}/dashboard/
  image       ${IMAGE}
  files       ${APP_DIR}
  data        ${DATA_DIR}

${BOLD}Next${RESET}
  ${BOLD}${APP_NAME} cli generate-temp-key${RESET}    a one-time key to create the owner account
  then in the panel: Free Configs -> Sources -> "Add default sources" -> refresh

${BOLD}Commands${RESET}
  ${APP_NAME} apply | logs | status | restart | cli | tui | backup | restore | uninstall

  Free configs are third-party servers, health-checked from this machine only.
  Free and best-effort, with no guarantees - do not sell them as a metered service.
EOF
}

usage() {
    cat <<EOF
${BOLD}${BRAND}${RESET} - ${REPO}

  ${APP_NAME} install [--database sqlite|mysql|mariadb|postgresql|timescaledb]
                  [--ssl-domain panel.example.com | --cert FILE --key FILE]
                  [--port 8000] [--image REF] [--no-enable] [--yes]

  ${APP_NAME} apply              pull the current image and restart - this is the update
  ${APP_NAME} migrate            adopt an existing PasarGuard install at ${LEGACY_DIR}
  ${APP_NAME} ssl --domain D     issue or replace the certificate
  ${APP_NAME} refresh            rebuild the free-config pool now

  ${APP_NAME} logs [-f] | status | restart | start | stop
  ${APP_NAME} cli ... | tui | edit | edit-env
  ${APP_NAME} backup | restore FILE | uninstall
EOF
}

# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

main() {
    local action="${1:-help}"
    [ $# -gt 0 ] && shift || true

    local rest=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --database)   DATABASE="${2:-}"; shift 2 ;;
            --image)      IMAGE="${2:-}"; shift 2 ;;
            --port)       PANEL_PORT="${2:-}"; shift 2 ;;
            --ssl-domain|--domain) SSL_MODE=domain; SSL_DOMAIN="${2:-}"; shift 2 ;;
            --cert)       SSL_MODE=custom; SSL_CERT="${2:-}"; shift 2 ;;
            --key)        SSL_MODE=custom; SSL_KEY="${2:-}"; shift 2 ;;
            --no-ssl)     SSL_MODE=none; shift ;;
            --no-enable)  FREE_CONFIGS_ENABLED=false; shift ;;
            -y|--yes)     ASSUME_YES=1; shift ;;
            *)            rest+=("$1"); shift ;;
        esac
    done

    case "$DATABASE" in
        sqlite|mysql|mariadb|postgresql|timescaledb) ;;
        *) die "unknown database: ${DATABASE} (sqlite, mysql, mariadb, postgresql, timescaledb)" ;;
    esac

    # Anything acting on an existing install must know which backend it uses;
    # only `install` is told by a flag.
    case "$action" in
        install) ;;
        *) if installed; then DATABASE="$(detect_database)"; fi ;;
    esac

    case "$action" in
        install)      cmd_install ;;
        apply|update) cmd_apply ;;
        migrate)      cmd_migrate ;;
        ssl)          cmd_ssl ;;
        refresh)      cmd_refresh ;;
        backup)       cmd_backup ;;
        restore)      cmd_restore "${rest[0]:-}" ;;
        uninstall)    cmd_uninstall ;;
        logs)         require_installed; compose logs --tail=200 "${rest[@]+"${rest[@]}"}" ;;
        status)       require_installed; compose ps ;;
        restart)      require_root; require_installed; compose restart ;;
        start)        require_root; require_installed; compose up -d ;;
        stop)         require_root; require_installed; compose down ;;
        cli)          require_installed; panel_exec python /code/nexus-cli.py "${rest[@]+"${rest[@]}"}" ;;
        tui)          require_installed; panel_exec nexus-tui "${rest[@]+"${rest[@]}"}" ;;
        edit)         require_root; require_installed; "${EDITOR:-nano}" "$COMPOSE_FILE" ;;
        edit-env)     require_root; require_installed; "${EDITOR:-nano}" "$ENV_FILE" ;;
        help|-h|--help) usage ;;
        *)            usage; die "unknown command: ${action}" ;;
    esac
}

main "$@"
