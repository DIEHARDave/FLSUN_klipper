#!/bin/bash
# Switches the klipper service back to FLSun's Klipper and removes what
# install.sh added. printer_upstream.cfg is kept (it holds the calibration
# made with upstream Klipper), so a later install picks it up again.

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOME}/printer_data/config"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the pi user, not root (sudo is used where needed)"
if [ -n "${SUDO_ASKPASS:-}" ]; then
    sudo() { command sudo -A "$@"; }
fi

msg "Switching the klipper service back to FLSun's Klipper"
sudo bash "${REPO_DIR}/scripts/switch_klipper.sh" stock \
    || die "couldn't switch back; nothing was removed"

msg "Removing upstream Klipper and its Python environment"
rm -rf "${HOME}/klipper-upstream" "${HOME}/klippy-env-upstream"
rm -f "${HOME}/printer_data/systemd/klipper_upstream.env"
rm -f "${CONFIG_DIR}/flsun_klipper" "${CONFIG_DIR}/flsun_klipper_includes.cfg"

msg "Done. printer_upstream.cfg is kept in ${CONFIG_DIR}."
