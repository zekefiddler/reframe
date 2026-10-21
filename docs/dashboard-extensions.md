# Dashboard Upload Extensions

Dashboard extensions add extra per-photo action buttons in the web dashboard. They are server-side only, off by default, and configured through `settings.json` via the dashboard settings modal.

The first built-in extension is photo upload to an [Are.na](https://www.are.na/) channel.

## How Extensions Work

Extensions live in `dashboard.py` and are registered in `extension_registry`.

Each extension should provide:

- `id`: stable machine id used in settings and API paths
- `label`: human-readable name
- `action_label`: text shown on the photo action button
- `requires_dithered`: whether the action should only appear for photos with a dithered file
- `enabled(settings)`: whether the extension is enabled
- `configured(settings)`: whether all required settings are present
- `run(photo, settings)`: async server-side action

The browser doesn't receive secrets, it only asks:

```http
GET /api/extensions/actions
```

That returns safe action metadata for configured extensions. When a user taps an extension button, the dashboard calls:

```http
POST /api/extensions/{extension_id}/photos/{photo_id}
```

The dashboard server loads the photo metadata, reads private settings, and runs the extension.

For v1, extensions are not loaded dynamically from arbitrary folders. To add one, subclass `DashboardExtension` in `dashboard.py` and register it in `ExtensionRegistry([...])`.

## Settings Shape

Extension settings are stored under `extensions`:

```json
{
  "extensions": {
    "arena": {
      "enabled": false,
      "channel": "",
      "access_token": ""
    }
  }
}
```

Tokens are write-only in the dashboard API:

- `GET /api/settings` returns `access_token: ""`
- `GET /api/settings` also returns `access_token_configured: true` when a saved token exists
- saving settings with a blank token preserves the saved token
- entering a new token replaces the saved token
- using the clear-token control sets `access_token_clear: true` and removes the saved token

This avoids accidentally exposing tokens to the browser UI, but the dashboard is still trusted-LAN software. Do not expose it directly to the public internet.

## Are.na Upload

The Are.na extension uploads the dithered PNG version of a photo to a configured Are.na channel.

Example Are.na channel: [Shot on reFrame](https://www.are.na/kalo/shot-on-reframe).

### Setup

1. Create an Are.na channel.
2. Create an Are.na API access token with write access.
3. Open the reFrame dashboard.
4. Open settings.
5. Under `extensions`, set `are.na upload` to `enabled`.
6. Enter the channel slug or numeric id.
7. Paste the access token and save settings.

When the extension is fully configured, an `are.na` button appears next to photo actions for photos that have a dithered PNG.

### What Gets Uploaded

Only the dithered PNG is uploaded. Original JPEGs are not uploaded by this extension.

If the advanced 2× dithered export setting is enabled, the dashboard enlarges the PNG with nearest-neighbor scaling immediately before upload. It does not crop or modify the stored dithered photo. The setting is disabled by default.

The created Are.na block uses:

- title: `reFrame {photo_id}`
- description:

```text
Dithered photo shot on [reframe.camera](https://reframe.camera)

Captured on {timestamp}
```

If `system.camera_name` is set in dashboard settings, the captured line appends `by {camera_name}`.

- metadata:

```json
{
  "source": "reframe",
  "photo_id": "{photo_id}"
}
```

## Adding Another Extension

Add a new class in `dashboard.py`:

```python
class ExampleExtension(DashboardExtension):
    id = "example"
    label = "Example"
    action_label = "example"
    requires_dithered = True

    def configured(self, settings):
        extension_settings = self.get_settings(settings)
        return self.enabled(settings) and bool(extension_settings.get("api_key"))

    async def run(self, photo, settings):
        dithered_path = photo.get("dithered_path")
        if not dithered_path:
            raise HTTPException(status_code=400, detail="This photo does not have a dithered version")
        # Upload or process the file here.
        return {
            "status": "success",
            "message": "Uploaded photo",
            "extension": self.id
        }
```

Then register it:

```python
extension_registry = ExtensionRegistry([
    ArenaExtension(),
    ExampleExtension(),
])
```

Add its settings defaults to `settings_defaults.py` and the dashboard settings UI. If the extension has a secret, mirror the Are.na write-only token behavior.
