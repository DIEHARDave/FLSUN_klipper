#!/bin/bash
# Switch the FLSun T1 Pro between FLSun's stock Klipper and upstream Klipper.
#
#   sudo ~/FLSUN_klipper/scripts/switch_klipper.sh upstream   ~/klipper-upstream, printer_upstream.cfg
#   sudo ~/FLSUN_klipper/scripts/switch_klipper.sh stock      FLSun's ~/klipper, printer.cfg
#   ~/FLSUN_klipper/scripts/switch_klipper.sh status          which one is set up, and its state
#
# Upstream is a systemd drop-in that overrides only the Python, the Klipper path and
# the config file. FLSun's klipper.service and klipper.env are never edited; "stock"
# removes the drop-in. Both refuse to switch during a print.
set -u
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_PI=/home/pi
DROPIN_DIR=/etc/systemd/system/klipper.service.d
DROPIN=$DROPIN_DIR/flsun_klipper_upstream.conf
ENV_UP=$HOME_PI/printer_data/systemd/klipper_upstream.env
LOG=$HOME_PI/printer_data/logs/klippy.log
MOONRAKER=http://127.0.0.1:7125

state() {
    curl -s -m 5 "$MOONRAKER/printer/objects/query?webhooks=state&print_stats=state" \
        | grep -oE '"state": ?"[a-z]+"' | cut -d'"' -f4 | paste -sd' '
}

refuse_if_printing() {
    case " $(state) " in
        *" printing "*|*" paused "*) echo "A print is running; not switching."; exit 1 ;;
    esac
}

wait_ready() {
    for _ in $(seq 1 45); do
        sleep 2
        case " $(state) " in *" ready "*) echo "Klipper is ready."; return 0 ;; esac
    done
    echo "Klipper isn't ready after 90 s (state: $(state)). Last lines of klippy.log:"
    tail -15 "$LOG"
    return 1
}

need_root() { [ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }; }

case "${1:-status}" in
upstream)
    need_root
    for f in $HOME_PI/klipper-upstream/klippy/klippy.py \
             $HOME_PI/klippy-env-upstream/bin/python \
             $HOME_PI/printer_data/config/printer_upstream.cfg \
             "$REPO_DIR/scripts/flsun_klipper_cfg.py"; do
        [ -e "$f" ] || { echo "Missing $f; run install.sh first."; exit 1; }
    done
    refuse_if_printing
    cat > "$ENV_UP" <<EOF
KLIPPER_ARGS="$HOME_PI/klipper-upstream/klippy/klippy.py $HOME_PI/printer_data/config/printer_upstream.cfg -I $HOME_PI/printer_data/comms/klippy.serial -l $LOG -a $HOME_PI/printer_data/comms/klippy.sock"
EOF
    chown pi:pi "$ENV_UP"
    mkdir -p "$DROPIN_DIR"
    cat > "$DROPIN" <<EOF
# Written by FLSUN_klipper (switch_klipper.sh). Delete it, or run
# "sudo $REPO_DIR/scripts/switch_klipper.sh stock", to go back to FLSun's Klipper.
[Service]
WorkingDirectory=$HOME_PI/klipper-upstream
EnvironmentFile=
EnvironmentFile=$ENV_UP
ExecStartPre=-/usr/bin/python3 $REPO_DIR/scripts/flsun_klipper_cfg.py includes
ExecStart=
ExecStart=$HOME_PI/klippy-env-upstream/bin/python \$KLIPPER_ARGS
EOF
    systemctl daemon-reload
    systemctl restart klipper
    echo "Switched to upstream Klipper."
    wait_ready
    ;;
stock)
    need_root
    refuse_if_printing
    rm -f "$DROPIN"
    rmdir "$DROPIN_DIR" 2>/dev/null
    systemctl daemon-reload
    systemctl restart klipper
    echo "Switched to FLSun's stock Klipper."
    wait_ready
    ;;
status)
    if [ -f "$DROPIN" ]; then echo "Set up: upstream Klipper"; else echo "Set up: FLSun's stock Klipper"; fi
    echo "Klipper: $(state)"
    ;;
*)
    echo "usage: switch_klipper.sh upstream|stock|status"; exit 1 ;;
esac
