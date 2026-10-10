# Frigate API reference

Verified read-only against the installed Frigate 0.18 container source on
October 10, 2026. This deployment includes local patches; inspect the installed
routes again after upgrading. Paths below include the public `/api` prefix
unless explicitly stated otherwise. No live configuration was changed during
this research.

## Transport and authentication

- Authenticated HTTPS exposes these routes through Frigate's nginx frontend.
  Use a trusted certificate and `Authorization: Bearer <JWT>` from a private
  token file, or the configured session cookie. Bearer credentials take
  precedence over cookies. Never put credentials in a URL, command argument,
  printed request, or repository file.
- `POST /api/login` accepts JSON `{"user":"...","password":"..."}`. Success
  is HTTP 200 with an **empty body** and a `Set-Cookie` JWT; extract that cookie
  privately rather than expecting a JSON access-token field. The cookie name
  is configurable. Cookie refresh is supported; bearer tokens are not refreshed
  by the same cookie-refresh mechanism.
- `GET /api/profile` returns username, role and `allowed_cameras`.
- The deployment's internal HTTP frontend is `http://127.0.0.1:5000` on the
  server. Access it through authorized SSH when using the local trusted route.
  Do not expose that port or manufacture `remote-role` headers externally.
- Camera access is enforced on event/media routes and list filters. `all`
  means cameras permitted to the current user, not necessarily every camera.
- `GET /api/version` gives the installed version. `GET /api/` is the simple
  application-health route; health alone does not prove camera capture.

Source: `api/auth.py` (`login`, `auth`, `profile`), `api/app.py`, installed
`/usr/local/nginx/conf/nginx.conf`.

## Event discovery

| Route | Parameters and behavior |
| --- | --- |
| `GET /api/events` | `limit=100` by default; newest first. Prefer plural CSV filters `cameras`, `labels`, `sub_labels`, `zones`; default `all`. Legacy singular `camera`, `label`, `sub_label`, `zone` are supported, but avoid mixing them with plural filters. |
| `GET /api/events/search` | Requires nonempty `query` **or** `event_id`; requires semantic search enabled, otherwise HTTP 400. `query` searches embeddings; `event_id` performs visual-thumbnail similarity search. Default `limit=50`, `search_type=thumbnail`. |
| `GET /api/events/{event_id}` | Full event metadata for an authorized camera; 404 if missing. |
| `GET /api/event_ids?ids=id1,id2` | Retrieve known event IDs; camera permissions still apply. Keep caller-supplied ID lists small. |
| `GET /api/events/summary` | Daily event counts; `timezone=utc`, optional integer `has_clip`/`has_snapshot`. |
| `GET /api/labels`, `GET /api/sub_labels` | Discover label and sub-label values before filtering. |

Common event-list filters:

- `after` / `before`: epoch seconds, compared to **event start time** using
  strict `>` / `<`; they are not interval-overlap filters. Find simultaneous
  activity by comparing returned start/end intervals, then inspect images or
  footage. An open event has `end_time=null`.
- `time_range=HH:MM,HH:MM` plus `timezone` (default `utc`) filters time of day;
  default `00:00,24:00`. Overnight ranges use OR. Prefer explicit epoch bounds
  for DST-sensitive or exact time windows.
- `has_clip`, `has_snapshot`, `is_submitted`: list route uses integer flags;
  search route uses booleans. List additionally supports `in_progress` and
  `favorites` integer flags and `min_length` / `max_length` in seconds.
- Both routes accept `min_score`, `max_score`, `min_speed`, `max_speed`,
  `recognized_license_plate`, and CSV `attributes`. Score filtering uses
  `data.score`, not a guaranteed maximum-ever confidence. Attribute filtering
  matches stored classification values.
- List sort: `date_desc` (default), `date_asc`, `score_asc`, `score_desc`,
  `speed_asc`, `speed_desc`. Search also supports `relevance` (default).
- Search filters are plural CSV `cameras`, `labels`, `sub_labels`, `zones`,
  plus the common time/score/media filters above. Search `search_type` accepts
  comma-separated `thumbnail` and/or `description` for a text query.
- Use `include_thumbnails=0` to avoid embedding large thumbnail data in JSON.
  This option is marked deprecated in query definitions, but the installed
  implementation still conditionally selects thumbnail bytes. Fetch media
  through the dedicated routes instead.

Safe event summaries include ID, camera, label/sub-label, start/end time,
zones, media availability, `data.score`, `data.top_score`, and description.
Search results additionally return `search_distance` and `search_source`;
**lower distance ranks better**. It is not object confidence, calibrated
probability, or proof that the described activity happened. The search response
also contains selected event metadata and may contain a review thumbnail path.
Do not treat a description or embedding match as visual verification.

Source: `api/event.py`, `api/defs/query/events_query_parameters.py`,
`api/defs/response/event_response.py`.

## Images and footage

| Route | Parameters / result |
| --- | --- |
| `GET /api/events/{id}/thumbnail.{extension}` | `extension`: `jpg`, `jpeg`, `png`, `webp`; `format=ios` (default) or `android`; `max_cache_age` default 2592000 seconds. |
| `GET /api/events/{id}/snapshot.jpg` | `bbox`, `timestamp`, `crop` integer flags; optional `height`, `quality`; `download` boolean. Omitted display settings inherit camera snapshot configuration. May return 404 when no saved snapshot exists. |
| `GET /api/events/{id}/snapshot-clean.webp` | Optional `download` boolean; clean saved snapshot when available. |
| `GET /api/{camera}/latest.{extension}` | Latest frame, with preview fallback when offline. Formats above; `height`, `quality=70`, and integer `bbox`, `timestamp`, `zones`, `mask`, `motion`, `paths`, `regions`, `store`. |
| `GET /api/{camera}/recordings/{frame_time}/snapshot.{format}` | Epoch frame time; `format` is `png` or `jpg`; optional `height`. Extracts a frame from a retained recording. |
| `GET /api/events/{id}/clip.mp4` | `padding` integer seconds, default 0; requires `has_clip`; ongoing event ends at current time. |
| `GET /api/review/{review_id}/clip.mp4` | Review footage, optional `padding` integer seconds. Review IDs and event IDs are different entities. |
| `GET /api/{camera}/start/{start_ts}/end/{end_ts}/clip.mp4` | Progressive MP4 assembled from retained recordings for epoch bounds. |
| `GET /api/{camera}/recordings?after=...&before=...` | Segment metadata ordered by start time; overlap test is `end_time >= after` and `start_time <= before`. Set both bounds explicitly: the Python default timestamps are evaluated at module load. |
| `GET /api/{camera}/recordings/summary?timezone=...` | Camera day/hour recording summary; default timezone `utc`. |
| `GET /api/recordings/summary?cameras=...&timezone=...` | Recording existence by day for permitted CSV cameras; defaults `all`, `utc`. |

HLS paths are handled by **nginx**, not by appending a suffix to the API's JSON
response indiscriminately:

- App metadata: `GET /api/vod/{camera}/start/{start_ts}/end/{end_ts}`,
  `GET /api/vod/event/{id}?padding=...`, and
  `GET /api/vod/clip/{camera}/start/{start_ts}/end/{end_ts}`.
- Playback URLs (no `/api`):
  `/vod/{camera}/start/{start_ts}/end/{end_ts}/master.m3u8`,
  `/vod/event/{id}/master.m3u8`, or
  `/vod/clip/{camera}/start/{start_ts}/end/{end_ts}/master.m3u8`.
  `index.m3u8` is also documented by the installed route descriptions.
- The clip VOD route forces discontinuities. Prefer HLS for Safari/iOS; the
  installed MP4 route explicitly warns about progressive MP4 handling there.
- Retention and storage cleanup may remove media while metadata remains.
  A successful list request or `has_clip` value is not a playback check.

Source: `api/media.py`, `api/record.py`, query definitions under `api/defs/query/`,
and nginx `location /vod/` / `vod_upstream_location /api`.

## Exports

`POST /api/export/{camera}/start/{start_time}/end/{end_time}` queues a durable
export. Times are epoch seconds. JSON body supports:

- `source`: `recordings` (default) or `preview`.
- `name`: optional friendly name, maximum 256 characters.
- `image_path`: optional existing export-thumbnail source; normally omit it.
- `export_case_id`: optional existing case (attaching requires admin).
- `chapters`: optional camera export chapter-mode override; normally omit to
  inherit the camera's setting.

Success is **HTTP 202**, with `success`, `export_id`, and `status="queued"`.
It does not mean encoding is complete. Queue-full returns 503. Poll
`GET /api/jobs/export/{export_id}`; `GET /api/jobs/export` lists active jobs.
Retrieve completed metadata with `GET /api/exports/{export_id}` or list with
`GET /api/exports` (`cameras` CSV, `export_case_id`, `start_date`, `end_date`).
Metadata includes video/thumbnail paths and `in_progress`. Confirm job status
and media existence before reporting completion.

`POST /api/exports/batch` accepts 1–50 `items`, each containing `camera`,
`start_time`, `end_time` (end strictly later), plus optional `friendly_name`,
`client_item_id`, `image_path`. Top-level optional `export_case_id`,
`new_case_name`, `new_case_description` choose a case. Results are per-item;
inspect each result rather than treating batch acceptance as full success.
Attaching an existing case requires admin.

Export media are served at `/exports/...` (no `/api`), rooted at
`/media/frigate`, with authentication. Convert a returned
`/media/frigate/exports/...` path to the corresponding URL without assuming
`/api/exports/{id}` itself is a download. Encode URL components.

Source: `api/export.py`, `api/defs/request/export_recordings_body.py`,
`batch_export_body.py`, `api/defs/response/export_response.py`, `record/export.py`,
nginx `location /exports/`.

## Camera state and ONVIF

Admin camera state API:
`PUT /api/camera/{camera}/set/{feature}` with JSON `{"value":"ON"}` or
`{"value":"OFF"}`. Use the internal camera key, not its display name.
`camera="*"` targets all cameras. Valid toggles: `enabled`, `detect`, `motion`,
`recordings` (**plural**), `snapshots`, `audio`, `audio_transcription`,
`notifications`, `review_alerts`, `review_detections`, `object_descriptions`,
`review_descriptions`, `improve_contrast`, `ptz_autotracker`, `birdseye`.

Other values: `birdseye_mode` is `CONTINUOUS`, `MOTION`, `OBJECTS`;
`motion_contour_area` / `motion_threshold` are integer strings. `motion_mask`,
`object_mask`, `zone` require
`/api/camera/{camera}/set/{feature}/{sub_command}`, where `sub_command` names
the mask or zone, and value is ON/OFF. Other features reject sub-commands.
Global profile changes require `PUT /api/camera/*/set/profile` with
`{"value":"profile_id"}` or `{"value":"none"}`. Read
`GET /api/profiles` / `GET /api/profile/active` for profile state.

Feature endpoints dispatch commands and return `{"success":true}`. Verify
effective state afterwards; dispatch acceptance is not hardware success or a
promise that YAML was rewritten. Some features cannot be enabled unless their
required input/configuration was already enabled at startup (notably audio).

ONVIF:

- `GET /api/{camera}/ptz/info` reads configured camera capabilities and presets.
- Manual movement uses authenticated **WebSocket `/ws`**, not a REST feature
  called `ptz` and not necessarily an external MQTT broker. Send JSON such as
  `{"topic":"stairs/ptz","payload":"MOVE_LEFT"}`. Commands are case-insensitive:
  `MOVE_UP`, `MOVE_DOWN`, `MOVE_LEFT`, `MOVE_RIGHT`, `ZOOM_IN`, `ZOOM_OUT`,
  `FOCUS_IN`, `FOCUS_OUT`, `STOP`; `PRESET_<name>` selects a preset (the
  dispatcher lowercases its parameter). Relative movement wire format is
  `MOVE_RELATIVE_<pan>_<tilt>[_<zoom>]`; values are parsed as floats and speed
  is set to 1. Available hardware capabilities vary.
- Send `STOP` after a bounded continuous move. WebSocket send/dispatch does not
  acknowledge physical completion; inspect the resulting camera image.
- nginx authenticates the handshake at `/ws`; camera-authorized users may send
  PTZ topics, while state-setting topics require admin. The server-side
  WebSocket communicator listens on loopback 5002 behind nginx; prefer the
  authenticated frontend path.
- `GET /api/onvif/probe` is an admin diagnostic with `host`, `port=80`,
  `username`, `password`, `test=false`, `auth_type=basic|digest`. Credentials
  are query parameters, so a generic logged-URL helper should not expose this
  operation. Read configured PTZ info when possible.

Source: `api/camera.py` (`camera_set`, `onvif_probe`), `api/media.py`,
`comms/dispatcher.py`, `comms/ws.py`, `ptz/onvif.py`, nginx `location /ws`.

## Configuration and safe status summaries

- `GET /api/config` returns effective configuration; `GET /api/config/raw` is
  admin-only raw YAML and can contain secrets. `/api/config/raw_paths` exposes
  unmasked camera/go2rtc URLs to admin users. Never print these whole responses.
  Effective config does mask several credentials, but it still includes
  usernames, paths/commands and (for admin) environment variables. Select only
  needed non-secret fields in memory.
- Suggested configuration allowlist: camera names; per-camera `enabled`,
  `detect` enabled/width/height/fps; `record` enabled/retention settings;
  `snapshots` enabled/retention; `audio` enabled/listen; `objects.track`;
  `review` label settings; semantic-search enabled/model settings; detector
  type/device/model dimensions. Do not echo FFmpeg inputs/commands, go2rtc
  sources, environment variables, MQTT/proxy/auth keys, ONVIF credentials or
  provider API keys.
- `GET /api/stats` is a sampled status object. Safe fields include
  `cameras.<name>.camera_fps`, `process_fps`, `skipped_fps`, `detection_fps`,
  `detection_enabled`, `audio_rms`, `audio_dBFS`, and
  `detectors.<name>.inference_speed` (milliseconds). Storage figures under
  `service.storage` are MiB. Never print full admin stats: `cpu_usages` can
  contain process command lines with camera credentials.
- `GET /api/config/schema.json` supplies the configuration JSON schema; it
  does not validate a candidate against full Frigate cross-field rules.
- `POST /api/config/save?save_option=save` accepts **text/plain raw YAML**,
  validates with `FrigateConfig.parse_yaml`, then overwrites the configuration.
  `save_option=restart` also attempts restart. Any non-`restart` value takes
  the save-only branch: **there is no validate-only save option**. Error text
  can contain supplied configuration values; filter it before displaying.
- `PUT /api/config/set` accepts JSON `config_data` (nested partial settings),
  `requires_restart=1`, optional `update_topic`, `skip_save=false`. Query
  updates, if present, take precedence over `config_data`. Normal operation
  patches YAML under a lock, validates it and restores the previous YAML on
  validation failure. `skip_save=true` is an **in-memory update**, not a dry
  run. `requires_restart=0` / update topics apply supported runtime changes;
  avoid inventing update topics from field names.
- For validation without applying, parse a private candidate with the
  installed `FrigateConfig.parse_yaml` in the container; do not call save/set.
  Preserve a private backup before authorized configuration changes.
- `POST /api/restart` requires admin. Restart accepted or save success does
  not prove retained state: after readiness, read selected effective fields,
  verify fresh camera capture/detection and relevant recording/playback.

Source: `api/app.py` (`config`, `config_save`, `config_set`, `stats`, `restart`),
`api/defs/request/app_body.py`, `stats/util.py`.
