#!/bin/bash
# PiSugar 3 setup for reFrame.
#
# install.sh runs this. On a camera that is already installed, run it by itself:
#     bash scripts/setup-pisugar.sh
# If I2C was off it says so at the end: reboot, then run it once more to finish.
#
# It turns on I2C, lets the inactivity timeout power the camera off, installs
# PiSugar's power manager so that only root can reach it, and makes one press
# power the camera on and a long press shut it down cleanly. Safe to run again.
#
# Everything is in functions and nothing runs when this file is sourced, so
# the tests can call root_only_server_options on a machine that is not a Pi.
set -euo pipefail

SERVER_DEFAULTS=/etc/default/pisugar-server
SERVER_DROPIN=/etc/systemd/system/pisugar-server.service.d/reframe.conf
SOCKET_DIR=/run/pisugar-server
SOCKET="$SOCKET_DIR/pisugar-server.sock"

say()  { echo "[pisugar] $1"; }
warn() { echo "[pisugar] WARNING: $1" >&2; }

# Filter for /etc/default/pisugar-server: drop the TCP, WebSocket and web
# listeners and move the command socket into a root-only directory.
#
# pisugar-server runs as root and will run shell commands it is sent (the
# long-press and button actions). None of its listeners has a login, and its
# socket is world-writable by default, so on its stock settings any program on
# the Pi can become root, and anything on the network can too. Binding to
# 127.0.0.1 only fixes the second.
root_only_server_options() {
    sed -E \
        -e 's/--(tcp|ws|http|web)[ =][^ "]+ ?//g' \
        -e 's/--uds-mode[ =][^ "]+ ?//g' \
        -e "s#--uds[ =][^ \"]+#--uds $SOCKET#" \
        -e 's/ +"/"/'
}

# One command to pisugar-server over its root-only socket; prints the reply.
pisugar() { echo "$1" | sudo nc -U -w 2 -q 0 "$SOCKET" 2>/dev/null || true; }

server_installed() { systemctl cat pisugar-server.service > /dev/null 2>&1; }

setting() {
    if [[ "$(pisugar "$1")" == *done* ]]; then
        say "$2"
    else
        warn "could not apply \"$1\". Try it by hand: echo '$1' | sudo nc -U -q 0 $SOCKET"
    fi
}

allow_shutdown() {
    # The one passwordless command this user gets. Checked before it is
    # installed: a broken file in /etc/sudoers.d locks sudo for everyone.
    local rule
    rule="$(mktemp)"
    echo "$CURRENT_USER ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now" > "$rule"
    if sudo visudo -cf "$rule" > /dev/null; then
        sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/reframe-shutdown
        say "The inactivity timeout may power the camera off"
    else
        warn "the shutdown rule did not pass visudo, so it was not installed"
    fi
    rm -f "$rule"
}

install_server() {
    local installer
    say "Installing PiSugar Power Manager"
    installer="$(mktemp)"
    wget -q -O "$installer" https://cdn.pisugar.com/release/pisugar-power-manager.sh
    bash "$installer" -c release --model 'PiSugar 3' --non-interactive
    rm -f "$installer"
}

# True when nothing but root can send pisugar-server a command.
server_is_root_only() {
    local listening
    listening="$(ss -ltn 2> /dev/null || true)"
    [[ "$listening" =~ :842[123][[:space:]] ]] && return 1
    [ "$(sudo stat -c '%a %U' "$SOCKET_DIR" 2> /dev/null)" = "700 root" ] && sudo test -S "$SOCKET"
}

lock_down_server() {
    local options
    options="$(mktemp)"
    sudo cat "$SERVER_DEFAULTS" | root_only_server_options > "$options"
    sudo install -o root -g root -m 0644 "$options" "$SERVER_DEFAULTS"
    rm -f "$options"

    # systemd makes the socket's directory, owned by root and closed to others.
    sudo install -d -o root -g root -m 0755 "$(dirname "$SERVER_DROPIN")"
    printf '[Service]\nRuntimeDirectory=pisugar-server\nRuntimeDirectoryMode=0700\n' \
        | sudo tee "$SERVER_DROPIN" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl restart pisugar-server || true

    for _ in 1 2 3 4 5 6 7 8 9 10; do
        server_is_root_only && return 0
        sleep 1
    done
    return 1
}

main() {
    if [ "$(id -u)" -eq 0 ]; then
        echo "Run this as the user who runs the camera, not with sudo or as root." >&2
        exit 1
    fi
    if ! command -v raspi-config > /dev/null; then
        echo "This sets up a Raspberry Pi; raspi-config is not here. Nothing was changed." >&2
        exit 1
    fi
    CURRENT_USER="$(id -un)"

    if ! command -v wget > /dev/null || ! command -v nc > /dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq wget netcat-openbsd
    fi

    # I2C is how the Pi reads the button and the battery. Switching it on only
    # reaches the PiSugar after a reboot.
    local i2c_was_on=0
    [ -e /dev/i2c-1 ] && i2c_was_on=1
    sudo raspi-config nonint do_i2c 0
    sudo usermod -a -G i2c "$CURRENT_USER"
    say "I2C enabled"

    allow_shutdown

    # PiSugar Power Manager: clean long-press shutdown, low-battery shutdown,
    # and cutting the battery once the Pi has shut down.
    server_installed || install_server
    if ! server_installed || [ ! -f "$SERVER_DEFAULTS" ]; then
        warn "pisugar-server is not installed: the button still takes photos, but a long press cuts power without a clean shutdown."
        exit 0
    fi

    if lock_down_server; then
        say "pisugar-server takes commands from root only, and has no network port"
    else
        sudo systemctl disable --now pisugar-server || true
        warn "pisugar-server could not be limited to root, so it was stopped and disabled. Until that is fixed there is no clean long-press shutdown. Check: systemctl status pisugar-server"
        exit 1
    fi

    if [ "$i2c_was_on" -eq 0 ]; then
        say "NEXT: I2C was off until now. Reboot, then run this script once more to finish the PiSugar settings."
        exit 0
    fi

    if [[ "$(pisugar "get model")" == *model* ]]; then
        setting "set_anti_mistouch false" "One press powers the camera on"
        setting "set_soft_poweroff_shell shutdown --poweroff 0" "A long press runs a clean shutdown"
        setting "set_soft_poweroff true" "A long press no longer cuts power outright"
    else
        warn "pisugar-server is not answering, so the PiSugar settings were not changed. Check: systemctl status pisugar-server"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
