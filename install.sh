#!/usr/bin/env bash
#
# One-line installer for pasarguard-free-configs.
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mezixa/pasarguard-free-configs/main/install.sh)"
#
# Subcommands:
#   install     install and start the panel (default)
#   update      pull the newest image and restart
#   uninstall   stop and remove the panel (asks before deleting data)
#   status      show container status and free-configs pool stats
#   logs        follow the logs
#   seed        add the default community config sources
#   refresh     rebuild the free-configs pool now
#
# Options for `install`:
#   --port <n>        panel port (default 8000)
#   --listen <addr>   bind address (default 127.0.0.1 - keep it behind a proxy)
#   --no-seed         do not add the default sources
#   --no-free-configs install plain upstream behaviour, feature disabled
#   --build           build the image from source instead of pulling it
#                     (happens automatically if the pull fails)
#
set -euo pipefail

REPO="Mezixa/pasarguard-free-configs"
IMAGE="ghcr.io/mezixa/pasarguard-free-configs:latest"
INSTALL_DIR="/opt/pasarguard-free-configs"
DATA_DIR="/var/lib/pasarguard"
COMPOSE="docker compose -f ${INSTALL_DIR}/docker-compose.yml"

PORT=8000
LISTEN="127.0.0.1"
DO_SEED=1
FREE_CONFIGS=true
FROM_SOURCE=0

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info()  { echo "${GREEN}==>${RESET} $*"; }
warn()  { echo "${YELLOW}==>${RESET} $*"; }
die()   { echo "${RED}==> error:${RESET} $*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "run this as root (use sudo)"
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        info "docker is already installed"
        return
    fi
    info "installing docker ..."
    curl -fsSL https://get.docker.com | sh
    command -v docker >/dev/null 2>&1 || die "docker installation failed"
    systemctl enable --now docker >/dev/null 2>&1 || true
}

write_compose() {
    mkdir -p "$INSTALL_DIR" "$DATA_DIR"
    cat > "${INSTALL_DIR}/docker-compose.yml" <<EOF
services:
  panel:
    image: ${IMAGE}
    container_name: pasarguard-free-configs
    restart: always
    # Host networking: matches upstream, and avoids the broken bridge-network
    # DNS that many VPSes have (which would stop the config fetcher from
    # reaching its sources). Exposure is controlled by UVICORN_HOST in .env.
    network_mode: host
    env_file: ${INSTALL_DIR}/.env
    volumes:
      - ${DATA_DIR}:/var/lib/pasarguard
EOF
}

write_env() {
    if [ -f "${INSTALL_DIR}/.env" ]; then
        info "keeping the existing .env"
        return
    fi
    cat > "${INSTALL_DIR}/.env" <<EOF
# --- panel ---------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///${DATA_DIR}/db.sqlite3"
UVICORN_HOST = "${LISTEN}"
UVICORN_PORT = "${PORT}"

# --- free configs add-on -------------------------------------------------
# Community proxy lists are harvested, TCP health-checked, and appended to the
# subscription output of eligible users. See FREE_CONFIGS.md in the repo.
FREE_CONFIGS_ENABLED = ${FREE_CONFIGS}
# "all" = every user, "groups" = only members of opted-in groups
FREE_CONFIGS_MODE = "all"
FREE_CONFIGS_REFRESH_INTERVAL = 86400
# 0 = no cap. Raise gradually and watch memory on a small VPS.
FREE_CONFIGS_MAX_CONFIGS = 2000
FREE_CONFIGS_TCP_TIMEOUT = 3
FREE_CONFIGS_MAX_CONCURRENCY = 50
EOF
    chmod 600 "${INSTALL_DIR}/.env"
}

build_from_source() {
    local src="${INSTALL_DIR}/src"
    info "building the image from source (a few minutes on first run) ..."

    if command -v git >/dev/null 2>&1; then
        if [ -d "${src}/.git" ]; then
            git -C "$src" fetch --depth 1 origin main && git -C "$src" reset --hard origin/main
        else
            rm -rf "$src"
            git clone --depth 1 "https://github.com/${REPO}.git" "$src"
        fi
    else
        info "git is missing, downloading a source tarball instead ..."
        rm -rf "$src" && mkdir -p "$src"
        curl -fsSL "https://github.com/${REPO}/archive/refs/heads/main.tar.gz" \
            | tar -xz -C "$src" --strip-components=1
    fi

    # --network=host: Docker's bridge network cannot resolve DNS on many VPSes,
    # which makes apt-get inside the build fail with "Temporary failure resolving".
    docker build --network=host -t "$IMAGE" "$src" \
        || die "the build failed. Check the output above; if it stopped at apt-get, it is a DNS problem in Docker - see FREE_CONFIGS.md."

    warn "built without the compiled dashboard, so the web UI is not served."
    warn "the API and the free-configs feature work normally; publish the CI image for the full UI."
}

wait_for_health() {
    info "waiting for the panel to come up ..."
    for _ in $(seq 1 60); do
        if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
            info "panel is up"
            return 0
        fi
        sleep 2
    done
    warn "the panel did not answer /health in time - check: ${BOLD}$0 logs${RESET}"
    return 1
}

cmd_seed() {
    info "adding the default community sources ..."
    $COMPOSE exec -T panel python scripts/seed_free_configs.py || \
        warn "seeding failed - add sources yourself via POST /api/free-configs/sources"
}

cmd_refresh() {
    info "rebuilding the free-configs pool (this fetches and health-checks, it takes a while) ..."
    $COMPOSE exec -T panel python -c \
        "import asyncio, json; from app.free_configs.service import refresh_pool; print(json.dumps(asyncio.run(refresh_pool()), indent=2))"
}

cmd_install() {
    require_root
    install_docker
    write_compose
    write_env

    if [ "$FROM_SOURCE" -eq 1 ]; then
        build_from_source
    else
        info "pulling ${IMAGE} ..."
        if ! $COMPOSE pull 2>&1; then
            warn "could not pull ${IMAGE}"
            warn "the GHCR package is probably still private or not published yet:"
            warn "  https://github.com/${REPO} -> Packages -> Package settings -> Change visibility"
            warn "falling back to building from source ..."
            build_from_source
        fi
    fi

    info "starting ..."
    $COMPOSE up -d
    wait_for_health || true

    if [ "$DO_SEED" -eq 1 ] && [ "$FREE_CONFIGS" = "true" ]; then
        cmd_seed
        cmd_refresh || warn "the first refresh did not finish - it will retry on schedule"
    fi

    cat <<EOF

${GREEN}${BOLD}Installed.${RESET}

  panel      http://${LISTEN}:${PORT}
  files      ${INSTALL_DIR}
  data       ${DATA_DIR}

${BOLD}Next: create the owner admin${RESET}

  ${COMPOSE} exec panel pasarguard-cli generate-temp-key

  Then open the setup page with that key to create your first admin.

${BOLD}The panel listens on ${LISTEN} only.${RESET}
  To reach it from your own machine:   ssh -L ${PORT}:127.0.0.1:${PORT} root@<this-server>
  For real use, put Nginx or Caddy in front with a TLS certificate, or set
  UVICORN_SSL_CERTFILE / UVICORN_SSL_KEYFILE in ${INSTALL_DIR}/.env.

${BOLD}Free configs${RESET}
  status    $0 status
  refresh   $0 refresh
  These are third-party servers, health-checked from THIS machine only. Free,
  best-effort, no guarantees - do not sell them as a metered service.
EOF
}

cmd_update() {
    require_root
    info "pulling the newest image ..."
    $COMPOSE pull
    $COMPOSE up -d
    wait_for_health || true
    info "updated"
}

cmd_uninstall() {
    require_root
    $COMPOSE down || true
    read -r -p "Delete the database and config pool in ${DATA_DIR}? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        rm -rf "$DATA_DIR"
        info "data removed"
    else
        info "data kept in ${DATA_DIR}"
    fi
    rm -rf "$INSTALL_DIR"
    info "uninstalled"
}

cmd_status() {
    $COMPOSE ps
    echo
    $COMPOSE exec -T panel python scripts/seed_free_configs.py --list 2>/dev/null \
        || warn "could not read the pool (is the panel running?)"
}

cmd_logs() { $COMPOSE logs -f --tail 100; }

main() {
    # `install` is the default action, so options may come first:
    #   install.sh --port 9000     ==  install.sh install --port 9000
    local action="install"
    if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
        action="$1"
        shift
    fi

    while [ $# -gt 0 ]; do
        case "$1" in
            --port)   PORT="$2"; shift 2 ;;
            --listen) LISTEN="$2"; shift 2 ;;
            --no-seed) DO_SEED=0; shift ;;
            --no-free-configs) FREE_CONFIGS=false; shift ;;
            --build) FROM_SOURCE=1; shift ;;
            *) die "unknown option: $1" ;;
        esac
    done

    case "$action" in
        install)   cmd_install ;;
        update)    cmd_update ;;
        uninstall) cmd_uninstall ;;
        status)    cmd_status ;;
        logs)      cmd_logs ;;
        seed)      cmd_seed ;;
        refresh)   cmd_refresh ;;
        *) die "unknown command: ${action} (install|update|uninstall|status|logs|seed|refresh)" ;;
    esac
}

main "$@"
