#!/bin/bash
# Runs upstream Klipper on the FLSun T1 Pro, beside FLSun's own.
# Run as the pi user from the cloned repo: ~/FLSUN_klipper/install.sh
#
#   1. clones upstream Klipper into ~/klipper-upstream, with its own Python
#      environment in ~/klippy-env-upstream (Python 3.10 or newer),
#   2. links this repo's config into ~/printer_data/config/flsun_klipper and
#      writes printer_upstream.cfg from printer.cfg ([probe] and calibration),
#   3. points the klipper service at upstream Klipper with a systemd drop-in.
#      If it doesn't come up ready, the service is switched back to FLSun's
#      Klipper and the install fails.
#
# FLSun's ~/klipper, ~/klippy-env, printer.cfg and klipper.service are never
# changed, and the mainboard isn't flashed: upstream Klipper talks to FLSun's
# mainboard firmware. uninstall.sh switches back.
#
# Safe to re-run. Klipper stays at whatever version the update manager last
# installed; KLIPPER_REF=<commit, tag or branch> switches it.

# The upstream Klipper this release was tested with on a T1 Pro.
TESTED_KLIPPER_REF="9cb99754c035b90206ef0a02660f674fc9517e9b"   # v0.13.0-800

set -e
trap 'printf "\033[1;31mERROR:\033[0m \"%s\" failed at line %s\n" "$BASH_COMMAND" "$LINENO" >&2' ERR

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLIPPER_REPO="https://github.com/Klipper3d/klipper.git"
KLIPPER_DIR="${HOME}/klipper-upstream"
KLIPPY_ENV="${HOME}/klippy-env-upstream"
CONFIG_DIR="${HOME}/printer_data/config"
ASKED_REF="${KLIPPER_REF:-}"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the pi user, not root (sudo is used where needed)"
[ -f "${CONFIG_DIR}/printer.cfg" ] || die "${CONFIG_DIR}/printer.cfg not found"

# For unattended runs: SUDO_ASKPASS=<script that prints pi's password>.
if [ -n "${SUDO_ASKPASS:-}" ]; then
    sudo() { command sudo -A "$@"; }
fi

case "$(curl -s --max-time 5 "http://127.0.0.1:7125/printer/objects/query?print_stats=state")" in
    *'"printing"'*|*'"paused"'*) die "a print is running; run this again when it has finished" ;;
esac

# --- Python and compiler -------------------------------------------------------------
# FLSun's image has Python 3.7 and no compiler. FLSUN_moonraker installs
# Python 3.13 into /usr/local and puts the compiler back.
PY=""
for v in 3.13 3.12 3.11 3.10; do
    if command -v "python$v" > /dev/null; then PY="$(command -v "python$v")"; break; fi
done
[ -n "$PY" ] || die "Python 3.10 or newer not found. Install FLSUN_moonraker first (https://github.com/DIEHARDave/FLSUN_moonraker); it adds Python 3.13 and the compiler."
echo 'int main(void){return 0;}' > /tmp/flsun_klipper_cc.c
gcc -o /tmp/flsun_klipper_cc /tmp/flsun_klipper_cc.c 2> /dev/null \
    || die "the C compiler doesn't work. Install FLSUN_moonraker first; it puts the compiler back."
rm -f /tmp/flsun_klipper_cc /tmp/flsun_klipper_cc.c
msg "Using $PY"

# --- Upstream Klipper ----------------------------------------------------------------
if [ ! -d "${KLIPPER_DIR}/.git" ]; then
    msg "Cloning upstream Klipper into ${KLIPPER_DIR}"
    git clone "$KLIPPER_REPO" "$KLIPPER_DIR"
    REF="${ASKED_REF:-$TESTED_KLIPPER_REF}"
elif [ -n "$ASKED_REF" ]; then
    git -C "$KLIPPER_DIR" fetch --tags origin
    REF="$ASKED_REF"
else
    REF=""
    msg "Keeping upstream Klipper at $(git -C "$KLIPPER_DIR" describe --tags --always)"
fi
if [ -n "$REF" ]; then
    msg "Checking out Klipper ${REF}"
    # A branch that tracks origin/master, so the update manager accepts the repo.
    git -C "$KLIPPER_DIR" checkout -q -B master "$REF"
    git -C "$KLIPPER_DIR" branch -q -u origin/master
fi

# This repo's Klipper module, linked in; git ignores it, so the update manager
# doesn't call the repo modified.
ln -sfn "${REPO_DIR}/extras/t1_box_light.py" "${KLIPPER_DIR}/klippy/extras/t1_box_light.py"
grep -qx "klippy/extras/t1_box_light.py" "${KLIPPER_DIR}/.git/info/exclude" 2> /dev/null \
    || echo "klippy/extras/t1_box_light.py" >> "${KLIPPER_DIR}/.git/info/exclude"

REQS="${KLIPPER_DIR}/scripts/klippy-requirements.txt"
STAMP="${KLIPPY_ENV}/.flsun_klipper_requirements"
if [ ! -x "${KLIPPY_ENV}/bin/python" ]; then
    msg "Creating ${KLIPPY_ENV}"
    "$PY" -m venv "$KLIPPY_ENV"
fi
if ! cmp -s "$REQS" "$STAMP"; then
    msg "Installing Klipper's Python packages (greenlet, cffi and msgspec are compiled; about 5 minutes)"
    "${KLIPPY_ENV}/bin/pip" install -q --upgrade pip
    "${KLIPPY_ENV}/bin/pip" install -q -r "$REQS"
    cp "$REQS" "$STAMP"
fi
msg "Building Klipper's C helper"
( cd "${KLIPPER_DIR}/klippy" && "${KLIPPY_ENV}/bin/python" -c "import chelper; chelper.get_ffi()" )

# --- Config --------------------------------------------------------------------------
msg "Linking the config into ${CONFIG_DIR}/flsun_klipper"
ln -sfn "${REPO_DIR}/config" "${CONFIG_DIR}/flsun_klipper"
python3 "${REPO_DIR}/scripts/flsun_klipper_cfg.py" create
python3 "${REPO_DIR}/scripts/flsun_klipper_cfg.py" includes

# --- Switch --------------------------------------------------------------------------
msg "Switching the klipper service to upstream Klipper"
if ! sudo bash "${REPO_DIR}/scripts/switch_klipper.sh" upstream; then
    msg "Upstream Klipper didn't start; switching back to FLSun's Klipper"
    sudo bash "${REPO_DIR}/scripts/switch_klipper.sh" stock || true
    die "upstream Klipper didn't come up ready; see the klippy.log lines above"
fi

msg "Done. Upstream Klipper is running with printer_upstream.cfg."
