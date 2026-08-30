#!/usr/bin/env bash
#
# Install Screenkeeper as a host-native, versioned, self-updating deployment.
#
# The layout this script creates is the one `signage-controller upgrade`
# maintains afterwards:
#
#   /opt/screenkeeper/versions/<version>/venv/   immutable per-version install
#   /opt/screenkeeper/current -> versions/<ver>  atomically swapped symlink
#   /etc/screenkeeper/config.yaml                survives upgrades
#   /var/lib/signage-controller/                 pairing state, survives upgrades
#
# Both runtime processes are systemd *user* units owned by a dedicated,
# lingering account. mpv needs that user's graphical session, and running the
# TV controller as the same user means `signage-controller upgrade` needs no
# root at all.
#
# Re-running this script is safe. Once a release tag exists, prefer
# `signage-controller upgrade` for updates.

set -euo pipefail

REPOSITORY="${SCREENKEEPER_REPOSITORY:-castab/screenkeeper}"
INSTALL_ROOT="${SCREENKEEPER_INSTALL_ROOT:-/opt/screenkeeper}"
CONFIG_DIR="/etc/screenkeeper"
STATE_DIR="/var/lib/signage-controller"
SERVICE_USER="signage"
VERSION=""
REF=""
INSTALL_MPV="yes"
INSTALL_UNITS="yes"
INSTALL_ALLOY="no"
FORCE="no"

usage() {
    cat <<'USAGE'
Usage: install.sh [options]

  --version X.Y.Z     Install a specific published release (default: latest).
  --ref REF           Install from a branch or tag archive instead of a release.
                      Use this before the first release tag exists, e.g. --ref main.
  --repository O/R    GitHub repository to install from (default: castab/screenkeeper).
  --user NAME         Service account to own and run the install (default: signage).
  --install-root DIR  Versioned install tree (default: /opt/screenkeeper).
  --config-dir DIR    Configuration directory (default: /etc/screenkeeper).
  --state-dir DIR     Pairing-state directory (default: /var/lib/signage-controller).
  --no-mpv            Do not install mpv (TV control only).
  --no-units          Do not install the systemd user units.
  --install-alloy     Also install and configure Grafana Alloy, the optional
                      host telemetry agent (apt-based hosts only). Remote-write
                      credentials in /etc/alloy/screenkeeper.env still need to
                      be filled in by hand afterwards -- see deploy/alloy/README.md.
  --force             Rebuild the version directory even if it already exists.
  -h, --help          Show this help.
USAGE
}

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version)      VERSION="${2:?--version needs a value}"; shift 2 ;;
        --ref)          REF="${2:?--ref needs a value}"; shift 2 ;;
        --repository)   REPOSITORY="${2:?--repository needs a value}"; shift 2 ;;
        --user)         SERVICE_USER="${2:?--user needs a value}"; shift 2 ;;
        --install-root) INSTALL_ROOT="${2:?--install-root needs a value}"; shift 2 ;;
        --config-dir)   CONFIG_DIR="${2:?--config-dir needs a value}"; shift 2 ;;
        --state-dir)    STATE_DIR="${2:?--state-dir needs a value}"; shift 2 ;;
        --no-mpv)       INSTALL_MPV="no"; shift ;;
        --no-units)     INSTALL_UNITS="no"; shift ;;
        --install-alloy) INSTALL_ALLOY="yes"; shift ;;
        --force)        FORCE="yes"; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              usage >&2; die "unknown option: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run this script as root (it creates a service user and system directories)"
[ -n "$VERSION" ] && [ -n "$REF" ] && die "--version and --ref are mutually exclusive"

# --- system dependencies -----------------------------------------------------
# python-snappy 0.7.x is backed by cramjam wheels, so no libsnappy headers and
# no compiler are needed here.
log "Installing system dependencies"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    packages="python3 python3-venv ca-certificates curl tar"
    [ "$INSTALL_MPV" = "yes" ] && packages="$packages mpv"
    [ "$INSTALL_ALLOY" = "yes" ] && packages="$packages gnupg jq"
    apt-get update -qq
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends $packages
else
    warn "no apt-get found; ensure python3 (3.11+), python3-venv, curl, tar$([ "$INSTALL_MPV" = yes ] && echo ', mpv') are installed"
    [ "$INSTALL_ALLOY" = "yes" ] && warn "--install-alloy needs apt-get; install Grafana Alloy manually, see deploy/alloy/README.md"
fi

# --- Grafana Alloy (optional host telemetry agent) --------------------------
# Alloy is host infrastructure, not part of Screenkeeper: it is never a
# startup dependency in either direction (see docs/architecture/observability.md).
# This block only installs the package and the ready-to-use config; the
# remote-write credentials in /etc/alloy/screenkeeper.env are always left for
# an operator to fill in by hand -- they must never be scripted or committed.
if [ "$INSTALL_ALLOY" = "yes" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        if [ ! -e /etc/apt/keyrings/grafana.gpg ]; then
            log "Adding the Grafana apt repository"
            install -d -m 0755 /etc/apt/keyrings
            curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
            echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
                > /etc/apt/sources.list.d/grafana.list
        fi
        log "Installing Grafana Alloy"
        apt-get update -qq
        if ! apt-get install -y --no-install-recommends alloy; then
            warn "could not install the alloy package; install it manually, see deploy/alloy/README.md"
            INSTALL_ALLOY="no"
        fi
    else
        warn "--install-alloy needs apt-get; skipping. Install Alloy manually, see deploy/alloy/README.md"
        INSTALL_ALLOY="no"
    fi
fi

command -v python3 >/dev/null 2>&1 || die "python3 is required"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "python3 3.11 or newer is required (found $(python3 --version 2>&1))"
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 \
    || die "python3 venv support is missing; install the python3-venv package"

# --- service account and directories -----------------------------------------
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    log "Creating service user $SERVICE_USER"
    useradd --create-home --shell /usr/sbin/nologin --comment "Screenkeeper signage" "$SERVICE_USER"
fi
SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
[ -n "$SERVICE_HOME" ] || die "could not determine the home directory of $SERVICE_USER"

log "Preparing $INSTALL_ROOT, $CONFIG_DIR, and $STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$INSTALL_ROOT" "$INSTALL_ROOT/versions"
install -d -o root -g "$SERVICE_USER" -m 0750 "$CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR"

# --- resolve and download the source archive ---------------------------------
WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

if [ -n "$REF" ]; then
    ARCHIVE_URL="https://github.com/$REPOSITORY/archive/refs/heads/$REF.tar.gz"
    log "Downloading $REPOSITORY ref $REF"
    if ! curl -fsSL "$ARCHIVE_URL" -o "$WORK_DIR/source.tar.gz"; then
        # A tag or commit-ish that is not a branch.
        ARCHIVE_URL="https://github.com/$REPOSITORY/archive/$REF.tar.gz"
        curl -fsSL "$ARCHIVE_URL" -o "$WORK_DIR/source.tar.gz" \
            || die "could not download ref '$REF' from $REPOSITORY"
    fi
else
    if [ -n "$VERSION" ]; then
        API_URL="https://api.github.com/repos/$REPOSITORY/releases/tags/v${VERSION#v}"
    else
        API_URL="https://api.github.com/repos/$REPOSITORY/releases/latest"
    fi
    log "Resolving release from $REPOSITORY"
    if ! curl -fsSL -H 'Accept: application/vnd.github+json' "$API_URL" -o "$WORK_DIR/release.json"; then
        die "$REPOSITORY has no matching published release yet. Before the first tag exists, install from a branch: $0 --ref main"
    fi
    ARCHIVE_URL="$(python3 - "$WORK_DIR/release.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    release = json.load(handle)
for asset in release.get("assets") or []:
    if str(asset.get("name", "")).endswith(".tar.gz"):
        print(asset["browser_download_url"])
        break
else:
    print(release.get("tarball_url", ""))
PY
)"
    [ -n "$ARCHIVE_URL" ] || die "the resolved release publishes no installable archive"
    log "Downloading $ARCHIVE_URL"
    curl -fsSL "$ARCHIVE_URL" -o "$WORK_DIR/source.tar.gz" || die "could not download the release archive"
fi

mkdir -p "$WORK_DIR/extracted"
tar -xzf "$WORK_DIR/source.tar.gz" -C "$WORK_DIR/extracted"
SOURCE_DIR="$(find "$WORK_DIR/extracted" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[ -n "$SOURCE_DIR" ] || die "the downloaded archive contained no source directory"

# The version directory is always named after the version declared in the
# source, so a release install and a `--ref` install of the same commit land in
# the same place and `signage-controller upgrade` can compare them.
RESOLVED_VERSION="$(python3 - "$SOURCE_DIR/src/signage_controller/__init__.py" <<'PY'
import re, sys
source = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
print(match.group(1) if match else "")
PY
)"
[ -n "$RESOLVED_VERSION" ] || die "could not read __version__ from the downloaded source"
log "Installing version $RESOLVED_VERSION"

# --- build the version directory ---------------------------------------------
# The venv is built at its final path, never staged and moved: pip bakes
# absolute paths into console-script shebangs and pyvenv.cfg, so a relocated
# virtual environment is broken exactly when it is activated. Safety comes from
# the version directory being one the running version never shares, and from
# `current` being repointed only after the smoke test below.
VERSION_DIR="$INSTALL_ROOT/versions/$RESOLVED_VERSION"
if [ -e "$VERSION_DIR/venv/bin/signage-controller" ] && [ "$FORCE" != "yes" ]; then
    log "Version $RESOLVED_VERSION is already installed; reusing it (pass --force to rebuild)"
else
    rm -rf "$VERSION_DIR"
    trap 'rm -rf "$VERSION_DIR"; cleanup' EXIT

    python3 -m venv "$VERSION_DIR/venv"
    "$VERSION_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
    "$VERSION_DIR/venv/bin/python" -m pip install --quiet "$SOURCE_DIR"

    # Smoke-test before this version can become the active one.
    REPORTED="$("$VERSION_DIR/venv/bin/signage-controller" --version)"
    case "$REPORTED" in
        *"$RESOLVED_VERSION"*) ;;
        *) die "built install reports '$REPORTED', expected $RESOLVED_VERSION" ;;
    esac

    chown -R "$SERVICE_USER:$SERVICE_USER" "$VERSION_DIR"
    chmod 0755 "$VERSION_DIR"
    trap cleanup EXIT
fi

# --- activate ----------------------------------------------------------------
log "Activating $RESOLVED_VERSION"
ln -sfn "versions/$RESOLVED_VERSION" "$INSTALL_ROOT/.current-$$"
mv -T "$INSTALL_ROOT/.current-$$" "$INSTALL_ROOT/current"
chown -h "$SERVICE_USER:$SERVICE_USER" "$INSTALL_ROOT/current"
ln -sfn "$INSTALL_ROOT/current/venv/bin/signage-controller" /usr/local/bin/signage-controller

if [ ! -e "$CONFIG_DIR/config.yaml" ] && [ -e "$SOURCE_DIR/config.example.yaml" ]; then
    log "Seeding $CONFIG_DIR/config.yaml from config.example.yaml"
    install -o root -g "$SERVICE_USER" -m 0640 \
        "$SOURCE_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi

# --- systemd user units ------------------------------------------------------
if [ "$INSTALL_UNITS" = "yes" ] && [ -d "$SOURCE_DIR/packaging" ]; then
    UNIT_DIR="$SERVICE_HOME/.config/systemd/user"
    log "Installing systemd user units into $UNIT_DIR"
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 \
        "$SERVICE_HOME/.config" "$SERVICE_HOME/.config/systemd" "$UNIT_DIR"
    for unit in "$SOURCE_DIR"/packaging/*.service "$SOURCE_DIR"/packaging/*.timer; do
        [ -e "$unit" ] || continue
        sed -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
            -e "s|@CONFIG_DIR@|$CONFIG_DIR|g" \
            -e "s|@STATE_DIR@|$STATE_DIR|g" \
            "$unit" > "$UNIT_DIR/$(basename "$unit")"
        chown "$SERVICE_USER:$SERVICE_USER" "$UNIT_DIR/$(basename "$unit")"
    done

    loginctl enable-linger "$SERVICE_USER" >/dev/null 2>&1 \
        || warn "could not enable linger for $SERVICE_USER; user units will not start at boot"

    SERVICE_UID="$(id -u "$SERVICE_USER")"
    if ! runuser -u "$SERVICE_USER" -- env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
        systemctl --user daemon-reload >/dev/null 2>&1; then
        warn "could not reach the user systemd manager for $SERVICE_USER"
        warn "run this once the user session exists: sudo -u $SERVICE_USER XDG_RUNTIME_DIR=/run/user/$SERVICE_UID systemctl --user daemon-reload"
    fi
fi

# --- Grafana Alloy config (only after the alloy package installed above) ----
if [ "$INSTALL_ALLOY" = "yes" ]; then
    if [ ! -e /etc/alloy/config.alloy ]; then
        log "Installing Alloy config from deploy/alloy/edge.alloy"
        install -o root -g root -m 0644 \
            "$SOURCE_DIR/deploy/alloy/edge.alloy" /etc/alloy/config.alloy
    else
        log "/etc/alloy/config.alloy already exists; leaving it unchanged"
    fi

    log "Installing the player/location identity bridge"
    install -o root -g root -m 0755 \
        "$SOURCE_DIR/deploy/alloy/render-identity-env.sh" \
        /usr/local/bin/screenkeeper-render-identity-env

    install -d -m 0755 /etc/systemd/system/alloy.service.d
    if [ ! -e /etc/systemd/system/alloy.service.d/screenkeeper.conf ]; then
        cat > /etc/systemd/system/alloy.service.d/screenkeeper.conf <<'UNIT'
[Service]
ExecStartPre=-/usr/local/bin/screenkeeper-render-identity-env
EnvironmentFile=-/etc/alloy/screenkeeper-identity.env
EnvironmentFile=/etc/alloy/screenkeeper.env
UNIT
    fi

    if [ ! -e /etc/alloy/screenkeeper.env ]; then
        log "Seeding /etc/alloy/screenkeeper.env (fill in real values before Alloy can export anything)"
        ( umask 077
          cat > /etc/alloy/screenkeeper.env <<'ENVFILE'
# Alloy's own environment. Screenkeeper never reads this file or its values.
# Fill in real values before Alloy can export telemetry anywhere -- these
# placeholders let Alloy start and scrape/tail locally while exporting
# nothing. Bearer tokens, not basic auth: see deploy/alloy/README.md.
METRICS_REMOTE_WRITE_URL=
METRICS_REMOTE_WRITE_TOKEN=
TRACES_OTLP_ENDPOINT=
TRACES_OTLP_TOKEN=
LOGS_REMOTE_WRITE_URL=
LOGS_REMOTE_WRITE_TOKEN=
SCREENKEEPER_ENVIRONMENT=production
ENVFILE
        )
        chown root:root /etc/alloy/screenkeeper.env
        chmod 0600 /etc/alloy/screenkeeper.env
    fi

    if ! systemctl daemon-reload || ! systemctl enable --now alloy >/dev/null 2>&1; then
        warn "could not enable/start alloy; check it manually (systemctl status alloy)"
        warn "see deploy/alloy/README.md for the manual setup steps"
    fi
fi

cat <<EOF

Screenkeeper $RESOLVED_VERSION is installed.

  Binary:   /usr/local/bin/signage-controller
  Versions: $INSTALL_ROOT/versions
  Config:   $CONFIG_DIR/config.yaml
  State:    $STATE_DIR

Next steps:
  1. Edit $CONFIG_DIR/config.yaml (TV host, desired_input, players).
  2. Pair each TV:
       sudo -u $SERVICE_USER signage-controller --config $CONFIG_DIR/config.yaml \\
         --state-dir $STATE_DIR pair <tv-id>
  3. Enable the TV controller:
       sudo -u $SERVICE_USER XDG_RUNTIME_DIR=/run/user/$(id -u "$SERVICE_USER") \\
         systemctl --user enable --now screenkeeper.service
  4. Playback starts with the graphical session (see README, "mpv Playback").
  5. After enrollment and content configuration, optionally enable:
       systemctl --user enable --now screenkeeper-agent.service
       systemctl --user enable --now screenkeeper-content.service

Later updates need no root:
  signage-controller upgrade --check
  signage-controller upgrade
  signage-controller rollback
EOF

if [ "$INSTALL_ALLOY" = "yes" ]; then
    cat <<'EOF'

Grafana Alloy (host telemetry agent) is installed and enabled, but it will not
export anything yet:
  1. Fill in /etc/alloy/screenkeeper.env with the real remote-write URL and
     credentials (0600, root-owned, never committed anywhere).
  2. sudo systemctl restart alloy
See deploy/alloy/README.md for the full setup, and
docs/architecture/observability.md for the architecture. Screenkeeper's own
units never depend on Alloy and keep running with or without it.
EOF
fi
