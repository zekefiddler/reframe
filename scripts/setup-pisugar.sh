#!/bin/bash
# PiSugar 3 setup for reFrame.
#
# install.sh runs this. On a camera that is already installed, run it by itself:
#     bash scripts/setup-pisugar.sh
# If I2C was off it says so at the end: reboot, then run it once more to finish.
#
# It turns on I2C, lets the inactivity timeout power the camera off, installs
# PiSugar's power manager, and makes one press power the camera on and a long
# press shut it down cleanly. Safe to run again.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as the user who runs the camera, not with sudo or as root." >&2
    exit 1
fi
CURRENT_USER="$(id -un)"
SERVER_DEFAULTS=/etc/default/pisugar-server

say()  { echo "[pisugar] $1"; }
warn() { echo "[pisugar] WARNING: $1" >&2; }

# One command to pisugar-server; prints its reply (nothing if it is not running).
pisugar() { echo "$1" | nc -w 2 -q 0 127.0.0.1 8423 2>/dev/null || true; }

server_installed() { systemctl cat pisugar-server.service > /dev/null 2>&1; }

setting() {
    if [[ "$(pisugar "$1")" == *done* ]]; then
        say "$2"
    else
        warn "could not apply \"$1\". Try it by hand: echo '$1' | nc -q 0 127.0.0.1 8423"
    fi
}

if ! command -v wget > /dev/null || ! command -v nc > /dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq wget netcat-openbsd
fi

# ── I2C: how the Pi reads the button and the battery ──
# Switching it on only reaches the PiSugar after a reboot.
i2c_was_on=0
[ -e /dev/i2c-1 ] && i2c_was_on=1
sudo raspi-config nonint do_i2c 0
sudo usermod -a -G i2c "$CURRENT_USER"
say "I2C enabled"

# ── Let the inactivity timeout power the camera off ──
# The one passwordless command this user gets. Checked before it is installed:
# a broken file in /etc/sudoers.d locks sudo for everyone.
rule="$(mktemp)"
echo "$CURRENT_USER ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now" > "$rule"
if sudo visudo -cf "$rule" > /dev/null; then
    sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/reframe-shutdown
    say "The inactivity timeout may power the camera off"
else
    warn "the shutdown rule did not pass visudo, so it was not installed"
fi
rm -f "$rule"

# ── PiSugar Power Manager: battery level, settings, power cut after shutdown ──
if ! server_installed; then
    say "Installing PiSugar Power Manager"
    installer="$(mktemp)"
    wget -q -O "$installer" https://cdn.pisugar.com/release/pisugar-power-manager.sh
    bash "$installer" -c release --model 'PiSugar 3' --non-interactive
    rm -f "$installer"
fi

if ! server_installed; then
    warn "pisugar-server is not installed: the button still takes photos, but there is no battery level and the battery is not cut off after a shutdown."
    exit 0
fi

# Its web page and command ports change power settings and have no login by
# default, so keep all three on this machine only.
if [ -f "$SERVER_DEFAULTS" ]; then
    sudo sed -i -E 's/0\.0\.0\.0:(842[123])/127.0.0.1:\1/g' "$SERVER_DEFAULTS"
    sudo systemctl restart pisugar-server
fi
if [ -f "$SERVER_DEFAULTS" ] && ! grep -q '0\.0\.0\.0' "$SERVER_DEFAULTS" && grep -q '127\.0\.0\.1:8423' "$SERVER_DEFAULTS"; then
    say "pisugar-server listens on this machine only"
else
    warn "could not confirm that pisugar-server is limited to this machine. Check the addresses in $SERVER_DEFAULTS."
fi

if [ "$i2c_was_on" -eq 0 ]; then
    say "NEXT: I2C was off until now. Reboot, then run this script once more to finish the PiSugar settings."
    exit 0
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
    [[ "$(pisugar "get model")" == *model* ]] && break
    sleep 1
done
if [[ "$(pisugar "get model")" == *model* ]]; then
    setting "set_anti_mistouch false" "One press powers the camera on"
    setting "set_soft_poweroff_shell shutdown --poweroff 0" "A long press runs a clean shutdown"
    setting "set_soft_poweroff true" "A long press no longer cuts power outright"
else
    warn "pisugar-server is not answering on port 8423, so the PiSugar settings were not changed. Check: systemctl status pisugar-server"
fi
