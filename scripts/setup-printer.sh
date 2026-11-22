#!/bin/bash
# Bluetooth setup for printing photo strips on the MX06 cat printer.
#
# install.sh runs this. On a camera that is already installed, run it by itself:
#     bash scripts/setup-printer.sh
# If it changed the boot config it says so at the end: reboot, then run it once
# more to switch the radio on.
#
# It undoes the Bluetooth trims of earlier reFrame installs: the disable-bt boot
# overlay, the disabled bluetooth service, and the radio's rfkill soft block
# (systemd restores that block at every boot until it is cleared once).
#
# Everything is in functions and nothing runs when this file is sourced, so the
# tests can call bluetooth_enabled_config on a machine that is not a Pi.
set -euo pipefail

say()  { echo "[printer] $1"; }
warn() { echo "[printer] WARNING: $1" >&2; }

# Filter for config.txt: comment out the overlay that switches Bluetooth off.
bluetooth_enabled_config() {
    sed -E 's/^dtoverlay=disable-bt[[:space:]]*$/#dtoverlay=disable-bt/'
}

boot_config() {
    if [ -f /boot/firmware/config.txt ]; then echo /boot/firmware/config.txt; else echo /boot/config.txt; fi
}

# Returns 0 when the file changed, so a reboot is needed.
enable_in_boot_config() {
    local config new
    config="$(boot_config)"
    new="$(mktemp)"
    bluetooth_enabled_config < "$config" > "$new"
    if cmp -s "$config" "$new"; then
        rm -f "$new"
        return 1
    fi
    sudo cp "$config" "$config.before-bluetooth"
    sudo tee "$config" < "$new" > /dev/null   # in place: the boot partition is FAT, so no chown or rename
    rm -f "$new"
    say "Bluetooth switched back on in $config (old copy: $config.before-bluetooth)"
    return 0
}

# Clear the soft block on the Bluetooth radio, if the radio exists yet.
unblock_radio() {
    local device found=0
    for device in /sys/class/rfkill/rfkill*; do
        [ -e "$device/type" ] || continue
        [ "$(cat "$device/type")" = bluetooth ] || continue
        found=1
        if [ "$(cat "$device/soft")" != 0 ]; then
            echo 0 | sudo tee "$device/soft" > /dev/null
            say "Bluetooth radio unblocked"
        fi
    done
    [ "$found" -eq 1 ]
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

    local reboot_needed=0
    enable_in_boot_config && reboot_needed=1
    sudo systemctl enable --now bluetooth.service
    say "Bluetooth service enabled"

    if [ "$reboot_needed" -eq 1 ]; then
        say "NEXT: reboot, then run this script once more to switch the radio on."
        exit 0
    fi
    if unblock_radio; then
        say "Bluetooth is ready for printing"
    else
        warn "no Bluetooth radio found. If you have not rebooted since running this, reboot and run it again."
        exit 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
