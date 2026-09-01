#!/usr/bin/env bash
#
# Installer for pasarguard-free-configs.
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mezixa/pasarguard-free-configs/main/install.sh)" @ install
#
# This is deliberately a thin wrapper around PasarGuard's own installer rather
# than a second installer. Theirs is ~1750 lines and already handles five
# database backends, Let's Encrypt certificates (domain and IP), automatic
# renewal, backup/restore, systemd, the CLI and TUI wrappers, and updates.
# Reimplementing any of that would only add bugs.
#
# So: run the official installer, then change the two things that make this
# fork this fork —
#   1. point the compose file at the fork's image instead of pasarguard/panel
#   2. add the FREE_CONFIGS_* settings to .env
#
# Every flag you pass to `install` goes straight through to the official
# installer, so anything documented there works here:
#
#   ... @ install --database postgresql --ssl-domain panel.example.com
#
# Subcommands:
#   install    official install, then apply this fork
#   apply      re-apply the fork to an existing PasarGuard install
#              (run this after an official update reverts the image)
#   update     official update, then re-apply the fork
#
# Anything else is handled by the official `pasarguard` command itself:
#   pasarguard logs | restart | status | cli | backup | uninstall ...
#
# Fork-specific options:
#   --image <ref>   use this image instead of the published one, e.g. a local
#                   build:  --image pasarguard-free-configs:dev
#   --no-seed       skip adding the default community sources
#   --no-enable     install the fork's image but leave the feature switched off
#
set -euo pipefail

REPO="Mezixa/pasarguard-free-configs"
IMAGE="ghcr.io/mezixa/pasarguard-free-configs:latest"
UPSTREAM_INSTALLER="https://github.com/PasarGuard/scripts/raw/main/pasarguard.sh"

APP_NAME="pasarguard"
APP_DIR="/opt/${APP_NAME}"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"
ENV_FILE="${APP_DIR}/.env"

DO_SEED=1
ENABLE=true

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { echo "${GREEN}==>${RESET} $*"; }
warn() { echo "${YELLOW}==>${RESET} $*"; }
die()  { echo "${RED}==> error:${RESET} $*" >&2; exit 1; }

require_root() { [ "$(id -u)" -eq 0 ] || die "run this as root (use sudo)"; }

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

run_upstream() {
    # $1 = subcommand, rest = passthrough flags
    local script
    script="$(mktemp)"
    info "fetching the official PasarGuard installer ..."
    curl -fsSL "$UPSTREAM_INSTALLER" -o "$script" || die "could not download ${UPSTREAM_INSTALLER}"
    chmod +x "$script"
    info "running the official installer: $*"
    bash "$script" "$@"
    rm -f "$script"
}

apply_fork() {
    [ -f "$COMPOSE_FILE" ] || die "no PasarGuard install found at ${COMPOSE_FILE} - run \`install\` first"

    info "pointing the compose file at ${IMAGE} ..."
    # the official installer hardcodes pasarguard/panel:<version>
    sed -i -E "s|^(\s*image:\s*).*pasarguard/panel:.*$|\1${IMAGE}|" "$COMPOSE_FILE"
    if ! grep -q "$IMAGE" "$COMPOSE_FILE"; then
        die "could not rewrite the image line in ${COMPOSE_FILE} - check it by hand"
    fi

    info "adding the free-configs settings to ${ENV_FILE} ..."
    touch "$ENV_FILE"
    if grep -q "^FREE_CONFIGS_ENABLED" "$ENV_FILE"; then
        sed -i -E "s|^FREE_CONFIGS_ENABLED=.*|FREE_CONFIGS_ENABLED=${ENABLE}|" "$ENV_FILE"
    else
        cat >> "$ENV_FILE" <<EOF

# --- free configs add-on (${REPO}) ----------------------------------------
# Community proxy lists are harvested, TCP health-checked, and appended to the
# subscription output of eligible users. See FREE_CONFIGS.md in the repo.
FREE_CONFIGS_ENABLED=${ENABLE}
# "all" = every user, "groups" = only members of opted-in groups
FREE_CONFIGS_MODE=all
FREE_CONFIGS_REFRESH_INTERVAL=86400
# 0 = no cap. Raise gradually and watch memory on a small VPS.
FREE_CONFIGS_MAX_CONFIGS=2000
FREE_CONFIGS_TCP_TIMEOUT=3
FREE_CONFIGS_MAX_CONCURRENCY=50
EOF
    fi

    info "pulling and restarting ..."
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        info "using the local image ${IMAGE} (already built here, skipping pull)"
    elif ! compose pull; then
        warn "could not pull ${IMAGE}"
        warn "if the GHCR package is still private, make it public:"
        warn "  https://github.com/${REPO} -> Packages -> Package settings -> Change visibility"
        warn "or build it yourself and re-run with --image <your-tag>:"
        warn "  git clone https://github.com/${REPO}.git && cd pasarguard-free-configs"
        warn "  docker build --network=host -t ${IMAGE} ."
        die "aborting before restarting, so your panel keeps running on its current image"
    fi
    compose up -d

    info "waiting for the panel ..."
    for _ in $(seq 1 45); do
        compose exec -T "$(compose config --services | head -1)" true >/dev/null 2>&1 && break
        sleep 2
    done
}

seed_and_refresh() {
    local svc
    svc="$(compose config --services | head -1)"

    info "adding the default community sources ..."
    compose exec -T "$svc" python scripts/seed_free_configs.py \
        || { warn "seeding failed - add sources via POST /api/free-configs/sources"; return; }

    info "building the pool for the first time (fetches and health-checks; takes a while) ..."
    compose exec -T "$svc" python -c \
        "import asyncio, json; from app.free_configs.service import refresh_pool; print(json.dumps(asyncio.run(refresh_pool()), indent=2))" \
        || warn "the first refresh did not finish - the scheduled job will retry"
}

summary() {
    cat <<EOF

${GREEN}${BOLD}Done.${RESET} PasarGuard is installed by its own installer and now runs this fork.

  image     ${IMAGE}
  files     ${APP_DIR}
  manage    ${BOLD}pasarguard${RESET} logs | restart | status | cli | backup | uninstall

${BOLD}Free configs${RESET}
  settings  ${ENV_FILE}  (FREE_CONFIGS_*)
  API       /api/free-configs/...   (owner only)
  refresh   $0 refresh

${BOLD}Note${RESET}
  An official \`pasarguard update\` resets the image back to upstream.
  Run ${BOLD}$0 update${RESET} instead, or ${BOLD}$0 apply${RESET} afterwards.

  These are third-party servers, health-checked from this machine only. Free
  and best-effort, with no guarantees - do not sell them as a metered service.
EOF
}

cmd_refresh() {
    local svc
    svc="$(compose config --services | head -1)"
    compose exec -T "$svc" python -c \
        "import asyncio, json; from app.free_configs.service import refresh_pool; print(json.dumps(asyncio.run(refresh_pool()), indent=2))"
}

main() {
    local action="install"
    if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
        action="$1"; shift
    fi

    # pull out our own flags, keep the rest for the official installer
    local passthrough=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --image)     IMAGE="$2"; shift 2 ;;
            --no-seed)   DO_SEED=0; shift ;;
            --no-enable) ENABLE=false; shift ;;
            *)           passthrough+=("$1"); shift ;;
        esac
    done

    case "$action" in
        install)
            require_root
            run_upstream install "${passthrough[@]+"${passthrough[@]}"}"
            apply_fork
            [ "$DO_SEED" -eq 1 ] && [ "$ENABLE" = "true" ] && seed_and_refresh
            summary
            ;;
        apply)
            require_root
            apply_fork
            summary
            ;;
        update)
            require_root
            run_upstream update "${passthrough[@]+"${passthrough[@]}"}"
            apply_fork
            summary
            ;;
        refresh) cmd_refresh ;;
        *) die "unknown command: ${action}
Use: install | apply | update | refresh
Everything else is the official command: pasarguard logs | restart | status | cli | backup | uninstall" ;;
    esac
}

main "$@"
