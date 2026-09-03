#!/usr/bin/env bash
#
# Installer for Nexus Panel.
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ install
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
#   apply      re-apply Nexus Panel to an existing install
#              (run this after an upstream update reverts the image)
#   update     official update, then re-apply the fork
#
# Day-to-day management is the `nexus` command this installs:
#   nexus logs | restart | status | cli | backup | uninstall ...
#
# `nexus` is a thin front for the `pasarguard` command that upstream's
# installer creates. That command, /opt/pasarguard and the container names
# keep their upstream spelling on purpose: they are created by upstream's
# installer, and renaming them would mean forking those ~1750 lines and
# breaking every backup, path and systemd unit an existing install has.
#
# Fork-specific options:
#   --image <ref>   use this image instead of the published one, e.g. a local
#                   build:  --image nexus-panel:dev
#   --no-enable     install the fork's image but leave the feature switched off
#
set -euo pipefail

REPO="NexusGuide/Nexus-panel"
IMAGE="ghcr.io/nexusguide/nexus-panel:latest"
UPSTREAM_INSTALLER="https://github.com/PasarGuard/scripts/raw/main/pasarguard.sh"

APP_NAME="pasarguard"
APP_DIR="/opt/${APP_NAME}"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"
ENV_FILE="${APP_DIR}/.env"

# set by --image: only then does a locally present image mean "do not pull"
IMAGE_OVERRIDDEN=0
ENABLE=true

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { echo "${GREEN}==>${RESET} $*"; }
warn() { echo "${YELLOW}==>${RESET} $*"; }
die()  { echo "${RED}==> error:${RESET} $*" >&2; exit 1; }

require_root() { [ "$(id -u)" -eq 0 ] || die "run this as root (use sudo)"; }

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Which container is the panel? The one running this fork's image.
#
# The obvious `compose config --services | head -1` is wrong: that list comes out
# in dependency order, not the order the file declares, so on a TimescaleDB
# install it returns the database. Every readiness probe and the whole seed step
# then ran inside the database container, which has neither /code nor the app
# package - the probe silently timed out, and seeding resolved its script path
# against "/" and reported `can't open file '//scripts/seed_free_configs.py'`.
#
# Matching on the image instead of a position is correct by construction: the
# panel is, by definition, the container running the image we just pointed the
# compose file at.
panel_container() {
    local id
    id="$(docker ps --filter "ancestor=${IMAGE}" --format '{{.ID}}' | head -1)"
    if [ -z "$id" ]; then
        # a name we can fall back on: upstream always calls the panel service
        # after the app itself
        id="$(compose ps -q "$APP_NAME" 2>/dev/null | head -1)"
    fi
    printf '%s' "$id"
}

# /code is where the Dockerfile puts the project and where `python scripts/...`
# and `import app` both need to resolve from.
panel_exec() {
    local id
    id="$(panel_container)"
    if [ -z "$id" ]; then
        return 1
    fi
    docker exec -i -w /code "$id" "$@"
}

run_upstream() {
    # $1 = subcommand, rest = passthrough flags
    local script
    script="$(mktemp)"
    info "fetching the upstream installer that Nexus Panel builds on ..."
    curl -fsSL "$UPSTREAM_INSTALLER" -o "$script" || die "could not download ${UPSTREAM_INSTALLER}"
    chmod +x "$script"
    info "running the upstream installer: $*"
    warn "it ends by tailing the container logs - press Ctrl+C there and this"
    warn "script will carry on and finish setting up Nexus Panel"

    # The upstream installer finishes with `docker compose logs -f`, which blocks
    # until interrupted. Without a trap, that Ctrl+C reaches this script too and
    # kills it before apply_fork runs - which looks exactly like a successful
    # install of upstream, with none of the fork in it. A *handled* trap (rather
    # than an ignored one) is reset to the default in the child, so the installer
    # still stops on Ctrl+C while this shell survives to finish the job.
    local status=0
    trap 'echo' INT
    bash "$script" "$@" || status=$?
    trap - INT

    # 130 is the log tail being interrupted, which is the normal way that
    # installer ends. Anything else is a real failure.
    if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
        rm -f "$script"
        die "the upstream installer exited with status ${status}"
    fi
    rm -f "$script"
}

apply_fork() {
    [ -f "$COMPOSE_FILE" ] || die "no panel found at ${COMPOSE_FILE} - run \`install\` first"

    info "pointing the compose file at ${IMAGE} ..."
    # A fresh install has upstream's hardcoded pasarguard/panel:<version>, but
    # re-applying finds whatever a previous apply wrote - so matching only the
    # upstream name meant you could switch to the fork once and never again
    # (say, from a local build to the published image).
    # Derived from IMAGE rather than hardcoded, so renaming the project means
    # editing one line at the top and nothing here.
    local image_name="${IMAGE%%:*}"
    image_name="${image_name##*/}"
    # delimiter is '#', not '|', because the pattern needs '|' for alternation
    sed -i -E \
        "s#^([[:space:]]*image:[[:space:]]*)(pasarguard/panel|.*${image_name}).*\$#\1${IMAGE}#" \
        "$COMPOSE_FILE"

    if ! grep -q "$IMAGE" "$COMPOSE_FILE"; then
        # A custom --image from an earlier run leaves a name we cannot guess.
        # Fall back to the first service's image line, which is the panel - the
        # same service every other command in this script talks to.
        warn "no recognisable panel image line; rewriting the first one instead"
        awk -v img="$IMAGE" '
            !done && $1 == "image:" { sub(/image:.*/, "image: " img); done = 1 }
            { print }
        ' "$COMPOSE_FILE" > "${COMPOSE_FILE}.tmp" && mv "${COMPOSE_FILE}.tmp" "$COMPOSE_FILE"
    fi

    if ! grep -q "$IMAGE" "$COMPOSE_FILE"; then
        die "could not rewrite the image line in ${COMPOSE_FILE} - check it by hand"
    fi

    info "adding the free-configs settings to ${ENV_FILE} ..."
    touch "$ENV_FILE"

    if ! grep -q "^# --- free configs add-on" "$ENV_FILE"; then
        cat >> "$ENV_FILE" <<EOF

# --- free configs add-on (${REPO}) ----------------------------------------
# Community proxy lists are harvested, TCP health-checked, and appended to the
# subscription output of eligible users. See FREE_CONFIGS.md in the repo.
EOF
    fi

    # Set each key on its own rather than writing the block once. The old
    # version appended the whole block only when FREE_CONFIGS_ENABLED was
    # absent, so an existing install never received a setting added in a later
    # release - it kept running with a stale value and no sign of it.
    # A value already in the file is left alone (it may be a deliberate choice)
    # but is reported when it differs from what this version recommends.
    ENV_CHANGED=0
    set_env() {
        local key="$1" value="$2" current
        if grep -qE "^${key}=" "$ENV_FILE"; then
            current="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
            if [ "$current" != "$value" ]; then
                warn "${key} is ${current} in ${ENV_FILE}; this version suggests ${value} (left as-is)"
            fi
        else
            printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
            ENV_CHANGED=1
        fi
    }

    # the master switch is the one thing the flags own outright
    if grep -qE "^FREE_CONFIGS_ENABLED=" "$ENV_FILE"; then
        sed -i -E "s|^FREE_CONFIGS_ENABLED=.*|FREE_CONFIGS_ENABLED=${ENABLE}|" "$ENV_FILE"
    else
        printf 'FREE_CONFIGS_ENABLED=%s\n' "$ENABLE" >> "$ENV_FILE"
    fi

    set_env FREE_CONFIGS_MODE all
    set_env FREE_CONFIGS_REFRESH_INTERVAL 86400
    # 0 = check every config found. Endpoints are probed once and the answer is
    # shared by every config on them, so the whole pool is affordable.
    set_env FREE_CONFIGS_MAX_CONFIGS 0
    set_env FREE_CONFIGS_MAX_PER_ENDPOINT 3
    set_env FREE_CONFIGS_MAX_PER_SUBSCRIPTION 100
    set_env FREE_CONFIGS_TCP_TIMEOUT 3
    set_env FREE_CONFIGS_MAX_CONCURRENCY 50

    info "pulling and restarting ..."
    # Only an explicit --image means "this was built here, do not go looking for
    # it in a registry". Skipping the pull merely because the image is present
    # locally was wrong for the published one: after the first apply it is always
    # present, so every later apply silently kept running the old image and no
    # amount of re-running would ever pick up a new release.
    if [ "$IMAGE_OVERRIDDEN" -eq 1 ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
        info "using the local image ${IMAGE} (built here, skipping pull)"
    elif ! compose pull; then
        warn "could not pull ${IMAGE}"
        warn "if the GHCR package is still private, make it public:"
        warn "  https://github.com/${REPO} -> Packages -> Package settings -> Change visibility"
        warn "or build it yourself and re-run with --image <your-tag>:"
        warn "  git clone https://github.com/${REPO}.git && cd nexus-panel"
        warn "  docker build --network=host -t ${IMAGE} ."
        die "aborting before restarting, so your panel keeps running on its current image"
    fi
    # A container keeps the environment it was started with. `compose restart`
    # does not re-read .env at all, and `compose up -d` will happily report
    # "up-to-date" when only env_file contents changed - so a settings change
    # would silently not take effect. Recreate whenever we touched .env.
    if [ "$ENV_CHANGED" -eq 1 ]; then
        info "settings changed - recreating the container so it picks them up ..."
        compose up -d --force-recreate
    else
        compose up -d
    fi

    install_command
}

# The panel's own command is `pasarguard`, created by upstream's installer along
# with /opt/pasarguard, the systemd unit and the backup paths. Renaming any of
# that would mean forking their installer and breaking every existing install,
# so instead this puts a `nexus` command in front of it: our own subcommands are
# handled here, everything else is passed straight through.
install_command() {
    local target="/usr/local/bin/nexus"
    info "installing the ${BOLD}nexus${RESET} command ..."
    cat > "$target" <<EOF
#!/usr/bin/env bash
# Nexus Panel - ${REPO}
set -euo pipefail

INSTALLER="https://raw.githubusercontent.com/${REPO}/main/install.sh"

case "\${1:-}" in
    apply|update|refresh)
        exec sudo bash -c "\$(curl -fsSL "\$INSTALLER")" @ "\$@"
        ;;
    ""|help|-h|--help)
        cat <<'USAGE'
Nexus Panel

  nexus apply      pull the latest Nexus Panel image and restart
  nexus update     take an upstream release, then re-apply Nexus Panel
  nexus refresh    rebuild the free-config pool now

  nexus logs | restart | status | cli | tui | backup | restore | uninstall
                   passed through to the panel's own command

Files: /opt/pasarguard   Settings: /opt/pasarguard/.env
USAGE
        ;;
    *)
        exec pasarguard "\$@"
        ;;
esac
EOF
    chmod +x "$target"
}

summary() {
    cat <<EOF

${GREEN}${BOLD}Done.${RESET} Nexus Panel is installed and running.

  image     ${IMAGE}
  files     ${APP_DIR}
  manage    ${BOLD}nexus${RESET} logs | restart | status | cli | backup | uninstall
  update    ${BOLD}nexus apply${RESET}

${BOLD}Free configs${RESET}
  sources   the panel's Free Configs page -> Sources -> "Add default sources"
  settings  ${ENV_FILE}  (FREE_CONFIGS_*)
  API       /api/free-configs/...   (owner only)
  refresh   ${BOLD}nexus refresh${RESET}

${BOLD}Note${RESET}
  Nexus Panel is built on PasarGuard, so its installer still creates
  ${APP_DIR} and a \`pasarguard\` command. \`nexus\` passes through to it.
  Running \`pasarguard update\` directly puts the upstream image back -
  use ${BOLD}nexus update${RESET} instead, or ${BOLD}nexus apply${RESET} afterwards.

  These are third-party servers, health-checked from this machine only. Free
  and best-effort, with no guarantees - do not sell them as a metered service.
EOF
}

cmd_refresh() {
    panel_exec python -c \
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
            --image)     IMAGE="$2"; IMAGE_OVERRIDDEN=1; shift 2 ;;
            --no-enable) ENABLE=false; shift ;;
            *)           passthrough+=("$1"); shift ;;
        esac
    done

    case "$action" in
        install)
            require_root
            # The official installer refuses to run over an existing install
            # unless you agree to wipe it. Someone who already runs PasarGuard
            # and wants the fork almost never means that - they want `apply`.
            # Say so here rather than letting them find out at a y/n prompt
            # whose "yes" destroys a working panel.
            if [ -f "$COMPOSE_FILE" ]; then
                warn "a panel is already installed at ${APP_DIR}."
                warn "To switch it to Nexus Panel without touching your data, run:"
                warn "    $0 apply"
                die "not running the upstream installer over an existing install"
            fi
            run_upstream install "${passthrough[@]+"${passthrough[@]}"}"
            apply_fork
            summary
            ;;
        apply)
            require_root
            apply_fork
            # seeding is idempotent and the refresh is skipped when the pool is
            # already populated, so this is safe to run on every apply
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
Everything else: nexus logs | restart | status | cli | backup | uninstall" ;;
    esac
}

main "$@"
