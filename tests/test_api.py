"""Offline checks of API trust boundaries and request semantics."""
import argparse
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import MagicMock, patch


SOURCE = Path(__file__).resolve().parents[1] / "frigate/scripts/frigate_api.py"
SPEC = importlib.util.spec_from_file_location("frigate_api_test_target", SOURCE)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def arguments(**overrides):
    defaults = dict(base_url="https://frigate.example", token_file=None,
                    ssh=None, ssh_option=[], timeout=60)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # main deliberately installs a restrictive process umask.
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)

    def token_file(self):
        path = self.root / "token"
        path.write_text("fixture-token-only\n")
        path.chmod(0o600)
        return path

    def response(self, raw):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = raw
        opener = MagicMock()
        opener.open.return_value = response
        return opener

    def invoke(self, argv):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = api.main(["--base-url", "https://frigate.example"] + argv)
        return code, output.getvalue(), errors.getvalue()

    def test_authentication_only_reaches_request_headers(self):
        client = api.Client(arguments(token_file=self.token_file()))
        opener = self.response(b'{"ok": true}')
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(client.request("/stats"), {"ok": True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token-only")
        self.assertEqual(request.get_header("Cookie"), "frigate_token=fixture-token-only")
        self.assertNotIn("fixture-token-only", request.full_url)

    def test_credential_file_rejects_public_file_and_symlink(self):
        path = self.token_file()
        path.chmod(0o644)
        with self.assertRaises(api.ApiError):
            api.private_text(path)
        path.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(api.ApiError):
            api.private_text(link)

    def test_untrusted_or_credential_bearing_urls_rejected(self):
        for url in ("http://frigate.example", "https://user:password@frigate.example",
                    "https://frigate.example?token=fixture", "https://frigate.example/#token",
                    "https://frigate.example/unrelated"):
            with self.subTest(url=url), self.assertRaises(api.ApiError):
                api.Client(arguments(base_url=url))
        self.assertEqual(api.Client(arguments(base_url="http://127.0.0.1:5000")).base,
                         "http://127.0.0.1:5000/api")

    def test_redirect_is_rejected_before_following(self):
        request = api.urllib.request.Request("https://frigate.example/api/stats")
        with self.assertRaisesRegex(api.ApiError, "Redirect rejected"):
            api.NoRedirect().redirect_request(request, None, 302, "Found", {},
                                              "https://other.example/token")

    def test_path_escape_rejected_without_transport(self):
        client = api.Client(arguments())
        with patch.object(api.urllib.request, "build_opener") as transport:
            for path in ("https://other.example/api", "/../config", "/stats?token=fixture", "/stats#fragment"):
                with self.subTest(path=path), self.assertRaises(api.ApiError):
                    client.request(path)
        transport.assert_not_called()

    def test_bounded_response_and_non_json_failure(self):
        client = api.Client(arguments())
        cap = 8 * 1024 * 1024
        opener = self.response(b"x" * (cap + 1))
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(api.ApiError, "size limit"):
                client.request("/events")
        opener.open.return_value.read.assert_called_once_with(cap + 1)
        opener = self.response(b"<html>fixture-secret</html>")
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(api.ApiError) as failure:
                client.request("/config")
        self.assertNotIn("fixture-secret", str(failure.exception))

    def test_http_error_does_not_print_upstream_secret_body(self):
        error = urllib.error.HTTPError("https://frigate.example/api/config", 401,
                                      "fixture-secret", {}, io.BytesIO(b"fixture-token-only"))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            code, output, errors = self.invoke(["config-summary"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("401", errors)
        self.assertNotIn("fixture-token-only", errors)
        self.assertNotIn("fixture-secret", errors)

    def test_config_and_stats_filter_sensitive_runtime_fields(self):
        config = {"auth": {"jwt_secret": "fixture-secret"},
                  "genai": {"provider": {"api_key": "fixture-secret"}},
                  "go2rtc": {"streams": {"stairs": "rtsp://fixture-secret@camera"}},
                  "cameras": {"stairs": {"detect": {"enabled": True, "fps": 5},
                    "record": {"enabled": True},
                    "onvif": {"host": "192.0.2.1", "password": "fixture-secret"},
                    "ffmpeg": {"inputs": [{"path": "rtsp://fixture-secret@camera",
                                            "roles": ["record", "detect"]}]}}}}
        summary = api.config_summary(config)
        self.assertTrue(summary["cameras"]["stairs"]["record"]["enabled"])
        self.assertEqual(summary["cameras"]["stairs"]["input_roles"], [["record", "detect"]])
        self.assertNotIn("fixture-secret", json.dumps(summary))
        self.assertNotIn("192.0.2.1", json.dumps(summary))
        stats = api.stats_summary({"cameras": {"stairs": {"camera_fps": 5,
                    "ffmpeg_cmd": "rtsp://fixture-secret@camera"}},
                    "detectors": {"gpu": {"inference_speed": 6.5, "pid": 99}},
                    "service": {"cmdline": "fixture-secret"}})
        self.assertEqual(stats["detectors"]["gpu"], {"inference_speed": 6.5})
        self.assertNotIn("fixture-secret", json.dumps(stats))

    def test_event_output_keeps_evidence_fields_and_drops_unselected_data(self):
        summary = api.event_summary({"id": "event-1", "camera": "cat_room",
                   "has_clip": True, "end_time": 100, "thumbnail": "private-binary",
                   "search_distance": .12, "data": {"description": "cat eating",
                   "top_score": .9, "unselected": "fixture-secret"}})
        self.assertEqual(summary["data"]["description"], "cat eating")
        self.assertTrue(summary["has_clip"])
        self.assertNotIn("private-binary", json.dumps(summary))
        self.assertNotIn("fixture-secret", json.dumps(summary))

    def test_explicit_offset_and_local_time_filters_agree(self):
        self.assertEqual(api.epoch("2026-10-09T21:00:00-07:00", None),
                         api.epoch("2026-10-09T21:00:00", "America/Los_Angeles"))
        self.assertEqual(api.epoch("2026-10-10T04:00:00Z", None),
                         api.epoch("2026-10-09T21:00:00-07:00", None))
        for value, zone in (("2026-11-01T01:30:00", "America/Los_Angeles"),
                            ("2026-03-08T02:30:00", "America/Los_Angeles"),
                            ("2026-10-09T21:00:00", None), ("nan", None), ("-1", None)):
            with self.subTest(value=value), self.assertRaises(api.ApiError):
                api.epoch(value, zone)

    def test_search_transmits_camera_label_time_and_rank_filters(self):
        opener = self.response(b"[]")
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            code, _, _ = self.invoke(["search", "--query", "white cat eating",
                "--camera", "cat_room", "--label", "cat", "--after", "100",
                "--before", "200", "--limit", "12"])
        self.assertEqual(code, 0)
        request = opener.open.call_args.args[0]
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual(parsed.path, "/api/events/search")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["cameras"], ["cat_room"])
        self.assertEqual(query["labels"], ["cat"])
        self.assertEqual(query["query"], ["white cat eating"])
        self.assertEqual(query["after"], ["100.0"])
        self.assertEqual(query["before"], ["200.0"])
        self.assertEqual(query["limit"], ["12"])
        self.assertEqual(query["include_thumbnails"], ["0"])
        self.assertNotIn("event_id", query)

    def test_invalid_window_causes_no_request(self):
        with patch.object(api.Client, "request") as request:
            code, _, errors = self.invoke(["events", "--after", "200", "--before", "100"])
        self.assertEqual(code, 1)
        self.assertIn("after must precede before", errors)
        request.assert_not_called()

    def test_private_media_download_does_not_emit_content_or_overwrite(self):
        target = self.root / "private" / "event.mp4"
        raw = b"fixture-private-media\x00\xff"
        with patch.object(api.Client, "request", return_value=raw) as request:
            code, output, errors = self.invoke(["media", "event-1", "--kind", "clip",
                        "--padding", "3", "--output", str(target)])
        self.assertEqual(code, 0, errors)
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("fixture-private-media", output)
        request.assert_called_once_with("/events/event-1/clip.mp4", {"padding": 3}, binary=True)
        with self.assertRaises(FileExistsError):
            api.write_private(target, b"replacement")
        link = self.root / "media-link"
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            api.write_private(link, b"replacement")
        self.assertEqual(target.read_bytes(), raw)

    def test_control_dry_run_never_calls_api(self):
        with patch.object(api.Client, "request") as request:
            code, output, errors = self.invoke(["control", "stairs", "detect", "OFF"])
        self.assertEqual(code, 0, errors)
        request.assert_not_called()
        plan = json.loads(output)["planned"]
        self.assertEqual(plan, {"method": "PUT", "path": "/api/camera/stairs/set/detect",
                               "body": {"value": "OFF"}})

    def test_authorized_control_changes_only_requested_feature_then_reads_back(self):
        config = {"cameras": {"stairs": {"detect": {"enabled": False},
                                         "record": {"enabled": True}}}}
        with patch.object(api.Client, "request", side_effect=[{"success": True}, config]) as request:
            code, output, errors = self.invoke(["control", "stairs", "detect", "OFF", "--apply"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args, ("/camera/stairs/set/detect",))
        self.assertEqual(request.call_args_list[0].kwargs, {"method": "PUT", "body": {"value": "OFF"}})
        result = json.loads(output)
        self.assertTrue(result["dispatch_accepted"])
        self.assertTrue(result["state_verified"])
        self.assertEqual(result["requested_state"], "OFF")
        self.assertFalse(result["readback"]["detect"]["enabled"])
        self.assertTrue(result["readback"]["record"]["enabled"])

    def test_accepted_control_with_mismatched_or_missing_readback_not_verified(self):
        for config in ({"cameras": {"stairs": {"detect": {"enabled": True}}}},
                       {"cameras": {}}):
            with self.subTest(config=config):
                with patch.object(api.Client, "request", side_effect=[{"success": True}, config]):
                    code, output, errors = self.invoke(["control", "stairs", "detect", "OFF", "--apply"])
                self.assertEqual(code, 0, errors)
                result = json.loads(output)
                self.assertTrue(result["dispatch_accepted"])
                self.assertFalse(result["state_verified"])
                self.assertNotIn("applied", result)

    def test_login_empty_response_cookie_saved_privately_without_output_secrets(self):
        credentials = self.root / "credentials.json"
        credentials.write_text(json.dumps({"user": "fixture-user", "password": "fixture-password",
                                          "unused": "fixture-unselected"}))
        credentials.chmod(0o600)
        target = self.root / "auth" / "token"
        opener = self.response(b"")
        headers = Message()
        headers.add_header("Set-Cookie", "unrelated=fixture-ignore; Path=/")
        headers.add_header("Set-Cookie", "frigate_token=fixture-jwt-only; HttpOnly; Secure; Path=/")
        opener.open.return_value.headers = headers
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            code, output, errors = self.invoke(["login", "--credentials-file", str(credentials),
                                              "--output", str(target)])
        self.assertEqual(code, 0, errors)
        self.assertEqual(target.read_text(), "fixture-jwt-only")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(output), {"token_file": str(target.resolve())})
        self.assertNotIn("fixture-jwt-only", output + errors)
        self.assertNotIn("fixture-password", output + errors)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://frigate.example/api/login")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"user": "fixture-user", "password": "fixture-password"})
        opener.open.return_value.read.assert_not_called()

    def test_login_missing_cookie_creates_no_file_or_secret_output(self):
        credentials = self.root / "credentials.json"
        credentials.write_text('{"user":"fixture-user","password":"fixture-password"}')
        credentials.chmod(0o600)
        target = self.root / "missing-token"
        opener = self.response(b"")
        opener.open.return_value.headers = Message()
        with patch.object(api.urllib.request, "build_opener", return_value=opener):
            code, output, errors = self.invoke(["login", "--credentials-file", str(credentials),
                                              "--output", str(target)])
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())
        self.assertEqual(output, "")
        self.assertNotIn("fixture-password", errors)
        self.assertIn("no expected JWT cookie", errors)

    def test_event_hls_uses_nginx_route_without_api_prefix(self):
        with patch.object(api.Client, "request", return_value={"id": "event-1", "camera": "cat_room"}):
            code, output, errors = self.invoke(["event", "event-1"])
        self.assertEqual(code, 0, errors)
        result = json.loads(output)
        self.assertEqual(result["vod_path"], "/vod/event/event-1/master.m3u8")
        self.assertEqual(result["media_paths"]["clip"], "/api/events/event-1/clip.mp4")

    def test_recording_metadata_filters_private_segment_paths(self):
        rows = [{"id": "segment-1", "camera": "cat_room", "start_time": 100,
                 "end_time": 110, "duration": 10, "path": "/private/fixture-media.mp4",
                 "unexpected": "fixture-secret"}]
        with patch.object(api.Client, "request", return_value=rows) as request:
            code, output, errors = self.invoke(["recordings", "cat_room", "--after", "100", "--before", "200"])
        self.assertEqual(code, 0, errors)
        request.assert_called_once_with("/cat_room/recordings", {"after": 100.0, "before": 200.0})
        self.assertEqual(json.loads(output)[0]["duration"], 10)
        self.assertNotIn("fixture-media", output)
        self.assertNotIn("fixture-secret", output)

    def test_interval_clip_uses_exact_bounds_and_private_save(self):
        target = self.root / "clips" / "interval.mp4"
        with patch.object(api.Client, "request", return_value=b"fixture-interval-video") as request:
            code, output, errors = self.invoke(["interval-clip", "cat_room", "--after", "100",
                                               "--before", "200", "--output", str(target)])
        self.assertEqual(code, 0, errors)
        request.assert_called_once_with("/cat_room/start/100.0/end/200.0/clip.mp4", binary=True)
        self.assertEqual(target.read_bytes(), b"fixture-interval-video")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fixture-interval-video", output)
        with patch.object(api.Client, "request") as request:
            code, _, _ = self.invoke(["interval-clip", "cat_room", "--after", "200", "--before", "100",
                                      "--output", str(self.root / "bad.mp4")])
        self.assertEqual(code, 1)
        request.assert_not_called()

    def test_ssh_errors_are_filtered_and_token_not_sent(self):
        client = api.Client(arguments(ssh="user@host", token_file=self.token_file()))
        result = argparse.Namespace(returncode=1, stdout=b"", stderr=b"fixture-secret SSH error")
        with patch.object(api.subprocess, "run", return_value=result) as run:
            with self.assertRaises(api.ApiError) as failure:
                client.request("/stats")
        self.assertNotIn("fixture-secret", str(failure.exception))
        self.assertNotIn("fixture-token-only", str(run.call_args))
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["path"], "/stats")


if __name__ == "__main__":
    unittest.main()
