#!/usr/bin/env python3
"""Frigate 0.18 API client. Python 3.9+, standard library, secret-safe output.

Use --ssh user@host for the trusted server's loopback API, or --base-url HTTPS
and --token-file containing a Frigate JWT. Authentication is never printed.
"""
import argparse
import datetime as dt
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


class ApiError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError("Redirect rejected; use the final trusted Frigate URL")


# Executed on the SSH host; receives only a loopback request via stdin. Response
# bodies on failure are discarded because upstream errors may contain secrets.
REMOTE = '''import json,sys,urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args): raise ValueError("redirect")
try:
 p=json.load(sys.stdin)
 request=urllib.request.Request("http://127.0.0.1:5000/api"+p["path"],data=json.dumps(p["body"]).encode() if p["body"] is not None else None,method=p["method"],headers={"Content-Type":"application/json"})
 with urllib.request.build_opener(NoRedirect).open(request,timeout=p["timeout"]) as response:
  data=response.read(p["max_bytes"]+1)
  if len(data)>p["max_bytes"]: raise ValueError("size")
  sys.stdout.buffer.write(data)
except urllib.error.HTTPError as e:
 sys.stderr.write("HTTP "+str(e.code)); sys.exit(23)
except Exception:
 sys.stderr.write("Remote API request failed"); sys.exit(24)
'''


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("Use a camera/event identifier, not a path")
    return value


def epoch(value, timezone):
    try:
        result = float(value)
    except ValueError:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                if not timezone:
                    raise ValueError("missing timezone")
                zone = ZoneInfo(timezone)
                first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
                if first.utcoffset() != second.utcoffset():
                    raise ValueError("ambiguous/nonexistent local time")
                parsed = first
            result = parsed.timestamp()
        except (ValueError, KeyError):
            raise ApiError("Invalid/ambiguous time; use ISO 8601 with an explicit UTC offset") from None
    if not math.isfinite(result) or result < 0:
        raise ApiError("Time must be a finite nonnegative Unix timestamp")
    return result


def private_text(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ApiError("Credential file must be a regular private file (chmod 600)")
    if info.st_size > 65536:
        raise ApiError("Credential file is too large")
    return path.read_text().strip()


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Refuse overwrite/symlinks, including an existing paired camera artifact.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


class Client:
    def __init__(self, args):
        self.args = args
        self.token = private_text(args.token_file) if args.token_file else None
        if self.token and re.search(r"\s", self.token):
            raise ApiError("Token file must contain only the JWT")
        self.base = args.base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ApiError("Base URL must not contain credentials, query or fragment")
        if parsed.path not in ("", "/api"):
            raise ApiError("Base URL must be the Frigate origin or its /api path")
        local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ApiError("Use HTTPS or trusted loopback HTTP")
        if not parsed.hostname:
            raise ApiError("Missing Frigate hostname")
        if not self.base.endswith("/api"):
            self.base += "/api"
        if args.ssh and (args.ssh.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]+", args.ssh)):
            raise ApiError("Invalid SSH destination")

    def request(self, path, params=None, method="GET", body=None, binary=False):
        if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
            raise ApiError("Invalid API path")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if params:
            path += "?" + urllib.parse.urlencode(params)
        cap = 100 * 1024 * 1024 if binary else 8 * 1024 * 1024
        if self.args.ssh:
            command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
            for option in self.args.ssh_option:
                command += ["-o", option]
            command += ["--", self.args.ssh, "python3 -c " + shlex.quote(REMOTE)]
            payload = {"path": path, "method": method, "body": body,
                       "timeout": self.args.timeout, "max_bytes": cap}
            try:
                result = subprocess.run(command, input=json.dumps(payload).encode(),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=self.args.timeout + 15)
            except subprocess.TimeoutExpired:
                raise ApiError("SSH/API timeout") from None
            if result.returncode:
                error = result.stderr.decode(errors="replace")
                match = re.fullmatch(r"HTTP (\d{3})", error)
                raise ApiError("HTTP " + match[1] if match else "SSH/remote API failed; check access and service health")
            raw = result.stdout
        else:
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
                headers["Cookie"] = "frigate_token=" + self.token
            request = urllib.request.Request(self.base + path, method=method, headers=headers,
                                             data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.build_opener(NoRedirect).open(request, timeout=self.args.timeout) as response:
                    raw = response.read(cap + 1)
            except urllib.error.HTTPError as exc:
                raise ApiError("HTTP %s; check authentication, permissions and feature availability" % exc.code) from None
            except (urllib.error.URLError, TimeoutError):
                raise ApiError("API connection failed; check DNS, TLS and service health") from None
        if len(raw) > cap:
            raise ApiError("Response exceeds size limit; narrow the interval/query")
        if binary:
            return raw
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ApiError("Expected JSON response; check Frigate endpoint/version") from None

    def login(self, credentials_file, output, cookie_name):
        if self.args.ssh:
            raise ApiError("Login uses authenticated frontend HTTPS; SSH loopback needs no JWT")
        credentials = json.loads(private_text(credentials_file))
        if not isinstance(credentials, dict) or any(not isinstance(credentials.get(k), str) or not credentials[k] for k in ("user", "password")):
            raise ApiError('Credentials file must contain private JSON with user and password')
        request = urllib.request.Request(self.base + "/login", method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({k: credentials[k] for k in ("user", "password")}).encode())
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=self.args.timeout) as response:
                cookie = SimpleCookie()
                for value in response.headers.get_all("Set-Cookie", []):
                    cookie.load(value)
                if cookie_name not in cookie:
                    raise ApiError("Login returned no expected JWT cookie; check configured cookie name")
                token = cookie[cookie_name].value
                if not token or re.search(r"\s", token):
                    raise ApiError("Login returned an invalid token")
                write_private(output, token.encode())
        except urllib.error.HTTPError as exc:
            raise ApiError("Login HTTP %s; check account and rate limits" % exc.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise ApiError("Login connection failed; check DNS and TLS") from None
        return {"token_file": str(Path(output).resolve())}


def event_summary(event):
    fields = ("id", "camera", "label", "sub_label", "start_time", "end_time", "zones",
              "has_clip", "has_snapshot", "retain_indefinitely", "search_distance", "search_source")
    out = {k: event[k] for k in fields if k in event}
    data = event.get("data") or {}
    out["data"] = {k: data[k] for k in ("top_score", "score", "description", "type") if k in data}
    return out


def config_summary(config):
    def selected(obj, keys):
        return {k: obj[k] for k in keys if k in obj}
    out = {"semantic_search": selected(config.get("semantic_search", {}), ("enabled", "model", "model_size", "device")),
           "model": selected(config.get("model", {}), ("path", "model_type", "width", "height", "input_tensor", "input_pixel_format")),
           "detectors": {name: selected(value, ("type", "device")) for name, value in config.get("detectors", {}).items()},
           "genai": {name: selected(value, ("provider", "model", "roles")) for name, value in config.get("genai", {}).items()},
           "cameras": {}}
    for name, camera in config.get("cameras", {}).items():
        out["cameras"][name] = {"enabled": camera.get("enabled"),
            "detect": selected(camera.get("detect", {}), ("enabled", "width", "height", "fps")),
            "objects": {"track": camera.get("objects", {}).get("track", [])},
            "record": {"enabled": camera.get("record", {}).get("enabled")},
            "snapshots": {"enabled": camera.get("snapshots", {}).get("enabled")},
            "audio": {"enabled": camera.get("audio", {}).get("enabled")},
            "motion": selected(camera.get("motion", {}), ("enabled", "threshold", "contour_area")),
            "onvif": {"configured": bool(camera.get("onvif", {}).get("host")),
                      "autotracking": camera.get("onvif", {}).get("autotracking", {}).get("enabled")},
            "input_roles": [item.get("roles", []) for item in camera.get("ffmpeg", {}).get("inputs", [])]}
    return out


def stats_summary(stats):
    return {"cameras": {name: {k: value.get(k) for k in ("camera_fps", "process_fps", "skipped_fps", "detection_fps", "detection_enabled")}
                        for name, value in stats.get("cameras", {}).items()},
            "detectors": {name: {"inference_speed": value.get("inference_speed")}
                          for name, value in stats.get("detectors", {}).items()}}


def filters(args):
    out = {"limit": args.limit, "include_thumbnails": 0, "cameras": args.camera, "labels": args.label,
           "after": epoch(args.after, args.timezone) if args.after else None,
           "before": epoch(args.before, args.timezone) if args.before else None}
    if out["after"] is not None and out["before"] is not None and out["after"] >= out["before"]:
        raise ApiError("after must precede before")
    return out


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=os.environ.get("FRIGATE_URL", "https://frigate.example"))
    p.add_argument("--token-file", default=os.environ.get("FRIGATE_TOKEN_FILE"))
    p.add_argument("--ssh", default=os.environ.get("FRIGATE_SSH"))
    p.add_argument("--ssh-option", action="append", default=[], help="OpenSSH -o option; use existing identity/agent")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--timezone", default=os.environ.get("FRIGATE_TIMEZONE"), help="IANA zone for times without offset")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("version")
    commands.add_parser("config-summary")
    sub = commands.add_parser("login")
    sub.add_argument("--credentials-file", required=True, help='Private JSON file with user and password')
    sub.add_argument("--output", required=True, help="New private JWT file")
    sub.add_argument("--cookie-name", default="frigate_token")
    for name in ("events", "search"):
        sub = commands.add_parser(name)
        sub.add_argument("--camera", help="Camera names, comma separated")
        sub.add_argument("--label", help="Labels, comma separated")
        sub.add_argument("--after", help="Unix timestamp or ISO 8601")
        sub.add_argument("--before", help="Unix timestamp or ISO 8601")
        sub.add_argument("--limit", type=int, default=20)
        if name == "search":
            query = sub.add_mutually_exclusive_group(required=True)
            query.add_argument("--query")
            query.add_argument("--similar-id", type=identifier)
            sub.add_argument("--search-type", choices=("thumbnail", "description", "thumbnail,description"), default="thumbnail")
        else:
            sub.add_argument("--has-clip", action="store_true")
    sub = commands.add_parser("event")
    sub.add_argument("event_id", type=identifier)
    sub = commands.add_parser("media")
    sub.add_argument("event_id", type=identifier)
    sub.add_argument("--kind", choices=("snapshot", "thumbnail", "clip"), default="snapshot")
    sub.add_argument("--output", required=True)
    sub.add_argument("--padding", type=int, default=0)
    for name in ("recordings", "interval-clip"):
        sub = commands.add_parser(name)
        sub.add_argument("camera", type=identifier)
        sub.add_argument("--after", required=True)
        sub.add_argument("--before", required=True)
        if name == "interval-clip":
            sub.add_argument("--output", required=True)
    sub = commands.add_parser("control")
    sub.add_argument("camera", type=identifier)
    sub.add_argument("feature", choices=("detect", "recordings", "snapshots", "audio", "motion"))
    sub.add_argument("state", choices=("ON", "OFF"))
    sub.add_argument("--apply", action="store_true", help="Execute an already authorized camera change")
    return p


def main(argv=None):
    os.umask(0o077)
    p = parser()
    args = p.parse_args(argv)
    if not 1 <= args.timeout <= 600 or not 1 <= getattr(args, "limit", 20) <= 500:
        p.error("timeout must be 1–600 seconds and limit 1–500")
    if not 0 <= getattr(args, "padding", 0) <= 300:
        p.error("padding must be 0–300 seconds")
    try:
        client = Client(args)
        if args.command == "status":
            out = stats_summary(client.request("/stats"))
        elif args.command == "version":
            out = {"version": client.request("/version", binary=True).decode().strip('"\n')}
        elif args.command == "login":
            out = client.login(args.credentials_file, args.output, args.cookie_name)
        elif args.command == "config-summary":
            out = config_summary(client.request("/config"))
        elif args.command in ("search", "events"):
            params = filters(args)
            if args.command == "search":
                params.update(query=args.query, event_id=args.similar_id, search_type=args.search_type)
                if args.query is not None and not args.query.strip():
                    raise ApiError("Query cannot be empty")
            elif args.has_clip:
                params["has_clip"] = 1
            result = client.request("/events/search" if args.command == "search" else "/events", params)
            if not isinstance(result, list):
                raise ApiError("Expected a list of events; check API version")
            out = [event_summary(event) for event in result]
        elif args.command == "event":
            out = event_summary(client.request("/events/" + args.event_id))
            # These links require the same authentication as API requests.
            out["media_paths"] = {kind: "/api/events/" + args.event_id + "/" + suffix
                                  for kind, suffix in (("snapshot", "snapshot.jpg"), ("thumbnail", "thumbnail.jpg"), ("clip", "clip.mp4"))}
            out["vod_path"] = "/vod/event/" + args.event_id + "/master.m3u8"
        elif args.command == "media":
            suffix = {"snapshot": "snapshot.jpg", "thumbnail": "thumbnail.jpg", "clip": "clip.mp4"}[args.kind]
            params = {"padding": args.padding} if args.kind == "clip" else None
            raw = client.request("/events/" + args.event_id + "/" + suffix, params, binary=True)
            if not raw:
                raise ApiError("Empty media response")
            write_private(args.output, raw)
            out = {"saved": str(Path(args.output).resolve()), "bytes": len(raw), "kind": args.kind}
        elif args.command in ("recordings", "interval-clip"):
            after, before = epoch(args.after, args.timezone), epoch(args.before, args.timezone)
            if after >= before:
                raise ApiError("after must precede before")
            if args.command == "recordings":
                result = client.request("/" + args.camera + "/recordings", {"after": after, "before": before})
                out = [{k: row[k] for k in ("id", "camera", "start_time", "end_time", "duration", "motion", "objects", "segment_size") if k in row} for row in result]
            else:
                raw = client.request("/" + args.camera + "/start/" + str(after) + "/end/" + str(before) + "/clip.mp4", binary=True)
                if not raw:
                    raise ApiError("Empty media response")
                write_private(args.output, raw)
                out = {"saved": str(Path(args.output).resolve()), "bytes": len(raw), "kind": "interval-clip"}
        elif args.command == "control":
            path = "/camera/" + args.camera + "/set/" + args.feature
            body = {"value": args.state}
            if not args.apply:
                out = {"planned": {"method": "PUT", "path": "/api" + path, "body": body}}
            else:
                # Never expose a camera-control response which can embed config.
                response = client.request(path, method="PUT", body=body)
                readback = config_summary(client.request("/config"))["cameras"].get(args.camera)
                key = "record" if args.feature == "recordings" else args.feature
                observed = readback.get(key, {}).get("enabled") if readback else None
                out = {"dispatch_accepted": response.get("success") is True,
                       "state_verified": observed is (args.state == "ON"),
                       "camera": args.camera, "feature": args.feature, "requested_state": args.state,
                       "readback": readback}
        print(json.dumps(out, indent=2))
        return 0
    except (ApiError, OSError, ValueError, KeyError, TypeError) as exc:
        # OSError/parse failures may carry credential content or private URLs.
        print(str(exc) if isinstance(exc, ApiError) else "API/file operation failed; check access, input and API schema", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
