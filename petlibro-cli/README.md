# petlibro-cli

Small standalone Python CLI for PetLibro feeders on the newer (non-Tuya) PetLibro
app. No Home Assistant required. Built for the Polar Wet Food Feeder (PLAF109),
US region.

PetLibro has no public API. The endpoints here come from the reverse-engineered
Home Assistant integration at https://github.com/jjjonesjr33/petlibro and may
break whenever PetLibro changes their cloud.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `PETLIBRO_EMAIL` | yes | PetLibro account email (`PETLIBRO_USERNAME` and `PL_USERNAME` are accepted as fallbacks) |
| `PETLIBRO_PASSWORD` | yes | PetLibro account password (`PL_PASSWORD` is accepted as a fallback) |
| `PETLIBRO_TIMEZONE` | no | IANA timezone sent to the API, default `America/Chicago` |

Variables are read from the environment, then from a `.env` file in the current
directory. The password is only used by `login`; it is sent as an MD5 hex digest,
which is what the PetLibro app does.

**Heads up:** PetLibro allows one active session per account, so `login` will
probably sign your phone app out. To avoid that, create a second PetLibro account,
share the feeder to it from the app, and use that account here.

## Commands

### login

Authenticates and caches the token at `~/.petlibro-cli/token.json` (mode 0600).

```sh
PETLIBRO_EMAIL=you@example.com PETLIBRO_PASSWORD=secret petlibro-cli login
```

### devices

Lists name, model, serial and online status. Add `--json` for the raw list.

```sh
petlibro-cli devices
```

### status

Dumps the raw `realInfo` JSON for a device. Add `--full` to include the wet
feeding plan, food status and settings.

```sh
petlibro-cli status AB0123456789
```

### feed

Manual feed / "Open Now". For the Polar this opens the lid over one bowl via
`POST /device/wetFeedingPlan/manualFeedNow` with `{"deviceSn", "plate"}`.

Dry run is the default: it prints the exact request and sends nothing.

```sh
petlibro-cli feed AB0123456789
```

To really open the feeder, once:

```sh
petlibro-cli feed AB0123456789 --no-dry-run
```

`--plate 1|2|3` picks the bowl. Without it, the CLI reads the current
`platePosition` from the device status (a read-only call) and uses that, which
matches what the Home Assistant integration does.

### close

Closes the lid, i.e. stops the manual feed, via
`POST /device/wetFeedingPlan/stopFeedNow` with `{"deviceSn", "feedId"}`. The
`feedId` is `manualFeedId` from the wet feeding plan; if there is none, there is
no open manual feed and the command exits. `--feed-id N` overrides it. Dry run by
default.

```sh
petlibro-cli close AB0123456789 --no-dry-run
```

### rotate

Turns the tray one bowl counter-clockwise via
`POST /device/wetFeedingPlan/platePositionChange`. The device ignores the `plate`
value and always moves exactly one step, so reaching a given bowl can take two
invocations. Dry run by default.

```sh
petlibro-cli rotate AB0123456789 --no-dry-run
```

## Safety

There is no loop, scheduler or retry anywhere in this tool. `feed`, `close` and
`rotate` each send at most one action request per invocation. If the token has expired, it exits with an error
asking you to run `login` rather than re-authenticating and retrying.

This tool only targets the wet-feeder endpoint. Dry-food PetLibro feeders use a
different call (`/device/device/manualFeeding` with `grainNum`) and are not
supported by `feed`.
