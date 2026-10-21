"""Default settings shared by the camera service and the dashboard."""

import copy

DEFAULT_SETTINGS = {
    "camera": {
        "resolution": {"width": 1200, "height": 800},
        "exposure_value": 0,
        "sharpness": 3,
        "autofocus_mode": 2,
        "rotate_180": False
    },
    "processing": {
        "saturation": 0.75,
        "brightness_factor": 1.1,
        "color_factor": 1.6,
        "contrast": 0.85,
        "dithering_method": "floyd_steinberg",
        "bayer_size": 4,
        "threshold_scale": 1.0
    },
    "display": {
        "auto_display": True,
        "display_timeout": 0,
        "fast_refresh": False,
        "orientation": "landscape",
        "portrait_rotation": 90,
        "slideshow_interval_seconds": 0
    },
    "system": {
        "auto_refresh_interval": 30,
        "auto_timeout_minutes": 10,
        "auto_timeout_enabled": False,
        "show_dashboard_qr_on_wifi_connect": False,
        "camera_name": ""
    },
    "exports": {
        "upscale_dithered_2x": False
    },
    "extensions": {
        "arena": {
            "enabled": False,
            "channel": "",
            "access_token": ""
        }
    }
}


def _deep_merge(base, override):
    for key, value in override.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def merge_defaults(saved):
    """Return a fresh copy of the defaults with saved settings merged over it."""
    saved = copy.deepcopy(saved or {})
    system = saved.get("system")
    if isinstance(system, dict) and "show_dashboard_qr_on_first_network" in system:
        # Older settings files used this name; an explicitly saved new key wins.
        legacy = system.pop("show_dashboard_qr_on_first_network")
        system.setdefault("show_dashboard_qr_on_wifi_connect", legacy)
    return _deep_merge(copy.deepcopy(DEFAULT_SETTINGS), saved)
