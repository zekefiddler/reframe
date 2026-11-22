# Hardware Porting Notes

reFrame is intentionally small and mostly contained in `reframe.py`. If you want to use a different camera, display, or button, you can keep the same high-level logic and swap the hardware-specific adapter code.

## Display

The default display adapter is `EInkDisplay` in `reframe.py`, using the bundled Waveshare `epd4in0e` driver.

To port another e-ink display:

- Update `DISPLAY_IMAGE_WIDTH`, `DISPLAY_IMAGE_HEIGHT`, `DISPLAY_PANEL_WIDTH`, and `DISPLAY_PANEL_HEIGHT`.
- Update the palettes and hardware color mapping in `ImageProcessor`.
- Keep `EInkDisplay`'s public methods compatible: `prepare_async`, `is_busy`, `display_image`, `display_buffer`, `display_buffer_async(buffer, on_done)`, `clear_display(on_done)`, `display_dashboard_qr(access_info, rotation, on_done)`, and `sleep`. The async methods also take `on_start`. They return False (or `{"error": "display_busy"}`) while a refresh is running, call `on_start` before touching the panel, and call `on_done` only after a refresh completes.

You can find similar E Ink Spectra 6 displays from other brands like Pimoroni, GooDisplay, etc.

## Camera

The default camera adapter is `CameraManager`, backed by Picamera2 and Raspberry Pi Camera Module 3.

To port another camera:

- Keep `capture_image_with_metadata()` returning `(result_dict, PIL_image)`.
- Make `capture_image()` return a PIL `RGB` image.
- Keep `configure_camera()`, `reload_settings()`, and `apply_camera_settings()` present, even if some settings become no-ops.
- Update or remove `scripts/enable_hdr.sh` and the `_enable_camera_hdr()` startup call in `reframe.py` if your camera does not use Camera Module 3 HDR.
- Update the `settings_defaults.py` camera defaults for the resolution and controls your camera supports.

## Button And Power

The default button path reads the PiSugar 3 power button over I2C (`PowerButton` in `reframe.py`). To port another trigger:

- Replace `PowerButton.pressed()` so it returns whether your button is held.
- Keep the short-press behavior calling `handle_button_press()`, which takes the photo.
- Skip `scripts/setup-pisugar.sh` in `install.sh`; it is specific to the PiSugar.
- Keep long-press shutdown behavior in the power manager if your hardware has one; otherwise implement it explicitly.

## Dashboard

The dashboard talks to the hardware service through the API routes in `reframe.py`. If the adapter methods above remain compatible, the dashboard should not need display- or camera-specific changes.
