"""petlibro-cli: minimal standalone CLI for the PetLibro cloud API.

Endpoints and login flow are taken from the reverse-engineered Home Assistant
integration at https://github.com/jjjonesjr33/petlibro (api.py).

Safety: no loops, schedulers, or retries anywhere. `feed`, `close` and `rotate`
each send at most one action POST per invocation, and only with --no-dry-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.us.petlibro.com"
REGION = "US"
APP_ID = 1
APP_SN = "c35772530d1041699c87fe62348507a8"
TOKEN_PATH = Path.home() / ".petlibro-cli" / "token.json"
TIMEOUT = 15.0

CODE_OK = 0
CODE_NOT_LOGGED_IN = 1009

MANUAL_FEED_NOW_PATH = "/device/wetFeedingPlan/manualFeedNow"
STOP_FEED_NOW_PATH = "/device/wetFeedingPlan/stopFeedNow"
PLATE_POSITION_CHANGE_PATH = "/device/wetFeedingPlan/platePositionChange"


class CliError(Exception):
    pass


def timezone() -> str:
    return os.environ.get("PETLIBRO_TIMEZONE", "America/Chicago")


def base_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "source": "ANDROID",
        "language": "EN",
        "timezone": timezone(),
        "version": "1.3.45",
        "Content-Type": "application/json",
    }
    if token:
        headers["token"] = token
    return headers


def redact(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    if "token" in out:
        out["token"] = out["token"][:6] + "...(redacted)"
    return out


def load_dotenv() -> None:
    """Load KEY=VALUE lines from ./.env without overriding the real environment."""
    path = Path.cwd() / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def credentials() -> tuple[str, str]:
    email = (
        os.environ.get("PETLIBRO_EMAIL")
        or os.environ.get("PETLIBRO_USERNAME")
        or os.environ.get("PL_USERNAME")
    )
    password = os.environ.get("PETLIBRO_PASSWORD") or os.environ.get("PL_PASSWORD")
    if not email or not password:
        raise CliError("Set PETLIBRO_EMAIL and PETLIBRO_PASSWORD (env or ./.env).")
    return email, password


def load_token() -> str:
    try:
        token = json.loads(TOKEN_PATH.read_text())["token"]
    except (OSError, ValueError, KeyError):
        raise CliError(f"No cached token at {TOKEN_PATH}. Run `petlibro-cli login` first.")
    return token


def save_token(token: str, email: str) -> None:
    TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({"token": token, "email": email, "region": REGION}, indent=2))
    TOKEN_PATH.chmod(0o600)


def post(path: str, body: dict[str, Any], token: str | None = None) -> Any:
    """Send exactly one POST. Never retries."""
    try:
        resp = httpx.post(BASE_URL + path, json=body, headers=base_headers(token), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise CliError(f"HTTP error calling {path}: {e}")
    if resp.status_code != 200:
        raise CliError(f"{path} returned HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
    except ValueError:
        raise CliError(f"{path} returned non-JSON: {resp.text[:300]}")
    code = payload.get("code")
    if code == CODE_NOT_LOGGED_IN:
        raise CliError(
            "Token expired or invalid (code 1009). Run `petlibro-cli login` and try again."
        )
    if code != CODE_OK:
        raise CliError(f"{path} failed: code={code} msg={payload.get('msg')}")
    return payload.get("data")


def serial_body(serial: str) -> dict[str, str]:
    return {"id": serial, "deviceSn": serial}


def cmd_login(args: argparse.Namespace) -> None:
    email, password = credentials()
    data = post(
        "/member/auth/login",
        {
            "appId": APP_ID,
            "appSn": APP_SN,
            "country": REGION,
            "email": email,
            "password": hashlib.md5(password.encode("utf-8")).hexdigest(),
            "phoneBrand": "",
            "phoneSystemVersion": "",
            "timezone": timezone(),
            "thirdId": None,
            "type": None,
        },
    )
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        raise CliError("Login succeeded but no token was returned.")
    save_token(data["token"], email)
    print(f"Logged in as {email}. Token cached at {TOKEN_PATH}")


def cmd_devices(args: argparse.Namespace) -> None:
    devices = post("/device/device/list", {}, load_token()) or []
    if args.json:
        print(json.dumps(devices, indent=2))
        return
    if not devices:
        print("No devices found.")
        return
    rows = [("NAME", "MODEL", "SERIAL", "ONLINE")]
    for d in devices:
        model = d.get("productName") or "?"
        if d.get("productIdentifier"):
            model += f" ({d['productIdentifier']})"
        rows.append(
            (
                str(d.get("name") or "?"),
                model,
                str(d.get("deviceSn") or "?"),
                "yes" if d.get("online") else "no",
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def cmd_status(args: argparse.Namespace) -> None:
    token = load_token()
    status = {"realInfo": post("/device/device/realInfo", serial_body(args.serial), token)}
    if args.full:
        status["wetFeedingPlan"] = post(
            "/device/wetFeedingPlan/wetListV3", serial_body(args.serial), token
        )
        status["grainStatus"] = post("/device/data/grainStatus", serial_body(args.serial), token)
        status["attributeSettings"] = post(
            "/device/setting/getAttributeSetting", serial_body(args.serial), token
        )
    print(json.dumps(status if args.full else status["realInfo"], indent=2))


def current_plate(serial: str, token: str) -> int:
    """Read-only lookup of the bowl currently under the lid."""
    real_info = post("/device/device/realInfo", serial_body(serial), token) or {}
    plate = real_info.get("platePosition")
    if plate not in (1, 2, 3):
        raise CliError(f"Could not determine plate position (got {plate!r}).")
    return plate


def send_action(path: str, body: dict[str, Any], token: str, dry_run: bool, action: str) -> None:
    """Print the exact request, then send it once unless this is a dry run. Never retries."""
    print(f"POST {BASE_URL}{path}")
    print("Headers: " + json.dumps(redact(base_headers(token)), indent=2))
    print("Body: " + json.dumps(body, indent=2))

    if dry_run:
        print(f"\nDRY RUN: nothing was sent. Re-run with --no-dry-run to {action}.")
        return

    data = post(path, body, token)
    print("\nSent. Response data: " + json.dumps(data))


def cmd_feed(args: argparse.Namespace) -> None:
    token = load_token()
    plate = args.plate if args.plate is not None else current_plate(args.serial, token)
    send_action(
        MANUAL_FEED_NOW_PATH,
        {"deviceSn": args.serial, "plate": plate},
        token,
        args.dry_run,
        "open the feeder",
    )


def cmd_close(args: argparse.Namespace) -> None:
    token = load_token()
    feed_id = args.feed_id
    if feed_id is None:
        plan = post("/device/wetFeedingPlan/wetListV3", serial_body(args.serial), token) or {}
        feed_id = plan.get("manualFeedId")
        if feed_id is None:
            raise CliError(
                "No active manual feed (manualFeedId is empty). Pass --feed-id to force."
            )
    send_action(
        STOP_FEED_NOW_PATH,
        {"deviceSn": args.serial, "feedId": feed_id},
        token,
        args.dry_run,
        "close the lid",
    )


def cmd_rotate(args: argparse.Namespace) -> None:
    token = load_token()
    plate = current_plate(args.serial, token)
    print(f"Current plate: {plate}. One rotation step turns the tray one bowl counter-clockwise.\n")
    # The device ignores "plate" and always rotates exactly one step; 1 matches the HA integration.
    send_action(
        PLATE_POSITION_CHANGE_PATH,
        {"deviceSn": args.serial, "plate": 1},
        token,
        args.dry_run,
        "rotate one step",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="petlibro-cli", description="Standalone CLI for PetLibro feeders (US region)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="authenticate and cache the token")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("devices", help="list devices")
    p.add_argument("--json", action="store_true", help="print the raw device list JSON")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("status", help="dump a device's raw status JSON")
    p.add_argument("serial")
    p.add_argument(
        "--full", action="store_true", help="also include wet feeding plan, grain status, settings"
    )
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("feed", help="manual feed / Open Now (dry run unless --no-dry-run)")
    p.add_argument("serial")
    p.add_argument(
        "--plate",
        type=int,
        choices=(1, 2, 3),
        help="bowl to open (default: current plate position)",
    )
    add_dry_run_flags(p)
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser(
        "close", help="close the lid / stop manual feed (dry run unless --no-dry-run)"
    )
    p.add_argument("serial")
    p.add_argument(
        "--feed-id",
        type=int,
        help="manual feed id to stop (default: manualFeedId from the wet feeding plan)",
    )
    add_dry_run_flags(p)
    p.set_defaults(func=cmd_close)

    p = sub.add_parser(
        "rotate", help="rotate the tray one bowl counter-clockwise (dry run unless --no-dry-run)"
    )
    p.add_argument("serial")
    add_dry_run_flags(p)
    p.set_defaults(func=cmd_rotate)

    return parser


def add_dry_run_flags(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="print the request without sending it (default)",
    )
    group.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false", help="actually send the request, once"
    )


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    try:
        args.func(args)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
