#!/bin/bash
set -e

# ─────────────────────────────────────────────
#  reFrame installer
#  Run this on a fresh Raspberry Pi OS Lite
# ─────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }
step()  { echo -e "\n${GREEN}── $1 ──${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOT_CONFIG="/boot/config.txt"
[ -f "/boot/firmware/config.txt" ] && BOOT_CONFIG="/boot/firmware/config.txt"
BOOT_CMDLINE="/boot/cmdline.txt"
[ -f "/boot/firmware/cmdline.txt" ] && BOOT_CMDLINE="/boot/firmware/cmdline.txt"
CURRENT_USER="$(id -un)"
CURRENT_GROUP="$(id -gn)"

if [ "$(id -u)" -eq 0 ]; then
    error "Do not run install.sh with sudo or as root."
    error "Log in as the user who will run the camera and run: cd $SCRIPT_DIR && ./install.sh"
    exit 1
fi

if ! [[ "$SCRIPT_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    error "Clone reFrame to a path without spaces or special characters (now: $SCRIPT_DIR)"
    exit 1
fi

echo ""
echo "  ┌─────────────────────────────┐"
echo "  │   reFrame camera installer  │"
echo "  └─────────────────────────────┘"
echo ""

# ── 1. System packages ──────────────────────
step "Installing system packages"

sudo apt update -qq
sudo apt install -y -qq \
    git \
    python3-pip \
    python3-venv \
    python3-picamera2 \
    python3-spidev \
    python3-gpiozero \
    python3-lgpio \
    v4l-utils \
    netcat-openbsd

info "System packages installed"

# ── 2. Enable hardware interfaces ───────────
step "Enabling hardware interfaces"

# Enable SPI
sudo raspi-config nonint do_spi 0
info "SPI enabled"

if ! grep -q "^camera_auto_detect=1" "$BOOT_CONFIG" 2>/dev/null; then
    echo "camera_auto_detect=1" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    info "Camera auto-detect enabled in $BOOT_CONFIG"
else
    info "Camera auto-detect already enabled"
fi

# ── 3. Boot-speed hardware trims ────────────
step "Applying boot-speed hardware trims"

ensure_boot_config_line() {
    local key="$1"
    local line="$2"
    if grep -q "^${key}=" "$BOOT_CONFIG" 2>/dev/null; then
        sudo sed -i "s|^${key}=.*|${line}|" "$BOOT_CONFIG"
    else
        echo "$line" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    fi
}

ensure_exact_boot_config_line() {
    local line="$1"
    if ! grep -q "^${line}$" "$BOOT_CONFIG" 2>/dev/null; then
        echo "$line" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    fi
}

ensure_exact_boot_config_line "dtoverlay=disable-bt"
ensure_boot_config_line "dtparam=audio" "dtparam=audio=off"
ensure_boot_config_line "hdmi_blanking" "hdmi_blanking=2"
ensure_boot_config_line "boot_delay" "boot_delay=0"
ensure_boot_config_line "display_auto_detect" "display_auto_detect=0"
ensure_boot_config_line "disable_splash" "disable_splash=1"
ensure_boot_config_line "disable_poe_fan" "disable_poe_fan=1"
ensure_boot_config_line "force_eeprom_read" "force_eeprom_read=0"
ensure_boot_config_line "enable_tvout" "enable_tvout=0"
sudo sed -i 's/^dtparam=audio=on/#dtparam=audio=on # disabled by reFrame fast boot/' "$BOOT_CONFIG"

# Keep kernel messages in the journal while avoiding blocking serial-console
# output and routine console chatter on this completely headless build.
sudo sed -i -E 's/(^|[[:space:]])console=serial0,[^[:space:]]+//g; s/[[:space:]]+/ /g; s/^ //; s/ $//' "$BOOT_CMDLINE"
if ! grep -qw "quiet" "$BOOT_CMDLINE"; then
    sudo sed -i '1 s/$/ quiet/' "$BOOT_CMDLINE"
fi
if ! grep -qw "loglevel=3" "$BOOT_CMDLINE"; then
    sudo sed -i '1 s/$/ loglevel=3/' "$BOOT_CMDLINE"
fi

info "Applied headless Bluetooth, audio, display-probe, console, and boot-delay trims"

# ── 4. User permissions ─────────────────────
step "Setting up user permissions"

sudo usermod -a -G video,spi,gpio "$CURRENT_USER"
info "Added $CURRENT_USER to hardware groups (video, spi, gpio)"

# ── 5. Photo directories ────────────────────
step "Creating photo directories"

# Create photo directories
mkdir -p "$SCRIPT_DIR/photos"
mkdir -p "$SCRIPT_DIR/dithered_photos"
info "Photo directories ready"

# ── 6. Runtime scripts ──────────────────────
step "Setting up runtime scripts"

chmod +x "$SCRIPT_DIR/scripts/enable_hdr.sh"
chmod +x "$SCRIPT_DIR/scripts/reframe-python"
info "Runtime scripts marked executable"

# Tell the root-owned update helper which user and checkout to use.
printf 'REFRAME_USER=%s\nREFRAME_GROUP=%s\nREFRAME_REPO=%s\n' "$CURRENT_USER" "$CURRENT_GROUP" "$SCRIPT_DIR" \
    | sudo tee /etc/default/reframe > /dev/null
sudo chown root:root /etc/default/reframe
sudo chmod 644 /etc/default/reframe

# Install the privileged update helper. No passwordless sudo rule: the helper
# installs files this user can edit, so a rule would make the account root.
# Finish a dashboard update over SSH with: sudo /usr/local/sbin/reframe-apply-update
sudo install -o root -g root -m 0755 "$SCRIPT_DIR/scripts/reframe-apply-update" /usr/local/sbin/reframe-apply-update
sudo rm -f /etc/sudoers.d/reframe-update-helper
info "Installed software update helper"

# ── 7. Systemd services ─────────────────────
step "Installing systemd services"

# The update helper builds the Python environment and installs the three
# service units, filled in with this user and checkout.
sudo /usr/local/sbin/reframe-apply-update
info "Installed Python environment and reframe services"

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl reenable reframe.service
sudo systemctl enable reframe-dashboard.service
sudo systemctl enable reframe-dashboard-proxy.service
info "Services enabled (will start on boot)"

# ── 8. Boot-speed service trims ─────────────
step "Disabling nonessential boot services"

# These are reversible trims for a headless camera. WiFi, SSH, mDNS/Avahi,
# and all reFrame services are intentionally left enabled.
sudo systemctl disable --now \
    ModemManager.service \
    triggerhappy.service \
    triggerhappy.socket \
    udisks2.service \
    rsync.service \
    rpi-eeprom-update.service \
    bluetooth.service \
    hciuart.service \
    bluealsa.service \
    networking.service \
    keyboard-setup.service \
    console-setup.service \
    rsyslog.service \
    apt-daily.timer \
    apt-daily-upgrade.timer \
    man-db.timer \
    e2scrub_all.timer \
    fstrim.timer \
    2>/dev/null || true

info "Disabled unused desktop/modem/Bluetooth/update timer services when present"

# ── Done ────────────────────────────────────
echo ""
echo -e "${GREEN}  ┌─────────────────────────────────────┐${NC}"
echo -e "${GREEN}  │   Installation complete!             │${NC}"
echo -e "${GREEN}  └─────────────────────────────────────┘${NC}"
echo ""
echo "  Next steps:"
echo "    1. Reboot:  sudo reboot"
echo "    2. reFrame will start automatically on boot"
echo "    3. Dashboard: http://$(hostname).local"
echo "       Fallback:  http://$(hostname).local:8000"
echo ""
echo -ne "  Reboot now? [y/N] "
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    sudo reboot
fi
