# Signage Controller

Screenkeeper manages two independent desired states for digital signage from
one YAML configuration file:

1. **LG webOS television state over the LAN** (Phase 1): connects directly to
   each TV, selects the configured HDMI input, and sets an absolute volume
   without muting. It does not use LG ThinQ, cloud services, CEC, or
   Wake-on-LAN.
2. **Local signage playback through mpv** (Phase 2): keeps a configured local
   video looping fullscreen on the host's display, independent of whether the
   paired TV is reachable.

These two subsystems are intentionally decoupled. A TV being powered off does
not stop its player from looping; a player being unavailable does not stop TV
convergence. When staff turns a TV back on, Screenkeeper switches it to the
correct HDMI input and the video is already there.

The configuration supports multiple TVs and multiple players, but start by
commissioning one TV and one player completely before adding more.

## Requirements

- Linux with Python 3.11 or newer
- Network reachability between the Linux host and the TV
- LG webOS local/mobile control enabled on the TV, if its firmware exposes the
  setting
- `mpv`, only if using local signage playback (see "mpv Playback" below); not
  required for TV-only commands

On recent LG webOS versions, the relevant setting is commonly named **LG
Connect Apps** under **All Settings > General > Devices > External Devices**.
Menu names vary by firmware.

The television does not need Internet access. `aiowebostv` connects locally to
the TV's webOS service.

## Install

On Debian or Ubuntu, install virtual-environment support if needed:

```bash
sudo apt install python3.12-venv
```

Create the environment and install the project:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

Run the fake-TV test suite:

```bash
pytest
```

## One-TV Walkthrough

### 1. Create Configuration

Create the ignored local configuration:

```bash
cp config.example.yaml config.yaml
```

Set the TV's LAN address in `host`. Leave `desired_input` as a temporary value
until the TV reports its real command ID:

```yaml
reconcile_interval: 30
power_on_delay: 15

# Optional: used only by `signage-controller run`.
observability:
  loki:
    url: https://loki.example.com/loki/api/v1/push
    bearer_token_env: LOKI_TOKEN
    labels:
      environment: production
  prometheus:
    remote_write_url: https://prometheus.example.com/api/v1/write
    bearer_token_env: PROMETHEUS_TOKEN
    job: signage_controller
    instance: signage-host-01
    push_interval: 15

tvs:
  - id: dev-tv
    name: Development LG TV
    host: 192.168.1.50
    desired_input: HDMI_1
    desired_volume: 0
```

`config.yaml` is the default path, so the commands below do not need
`--config`. For another location, place global options before the command:

```bash
signage-controller --config /path/to/config.yaml status dev-tv
```

### 2. Pair the Host

With the TV powered on, run:

```bash
signage-controller pair dev-tv
```

On first use, approve the authorization request shown on the TV. The client key
is saved locally after approval and is not printed or logged. Later connections
reuse the key without another prompt.

### 3. Discover the HDMI Command ID

Run:

```bash
signage-controller inputs dev-tv
```

Use the exact ID printed at the start of the desired input line. For example:

```text
HDMI_1 (Signage Player)
HDMI_2 (AV Receiver)
```

Set `desired_input: HDMI_1` in `config.yaml` for the first example. Do not use
the webOS application identifier such as `com.webos.app.hdmi1`; it is useful
for status reporting but is rejected by the TV's input-switch command.

### 4. Inspect Current State

```bash
signage-controller status dev-tv
```

This displays pairing, connectivity, reported power state, current input, and
current volume. It does not change TV settings.

### 5. Apply Once

```bash
signage-controller apply dev-tv
```

`apply` connects once, validates the configured input, changes only values that
differ, prints what changed, and exits. It applies immediately; the power-on
delay is used by the long-running `run` command.

Run `apply` again to confirm idempotence. When both values are already correct,
it reports that desired state has already been reached.

### 6. Run Continuously

```bash
signage-controller run
```

Run starts one independent async manager per configured TV. After connecting,
it waits `power_on_delay` seconds before its first reconciliation, allowing
webOS and HDMI services to settle. Stop it with **Ctrl-C**.

Do not use **Ctrl-Z**. It suspends the process instead of stopping it and keeps
the single-instance lock held. If that happens, use `fg` to resume the job and
then press Ctrl-C, or terminate it with `kill`.

Only one `run` process may use a state directory at a time. A second instance
exits with:

```text
ERROR Another signage-controller run process is active.
```

## Configuration Reference

```yaml
reconcile_interval: 30
power_on_delay: 15

tvs:
  - id: menu-left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
```

`id` must be unique and is used for pairing-key storage and log prefixes.

`desired_volume` is an integer from `0` to `100`. Volume zero is not mute, so
the controller never calls the TV mute API.

`reconcile_interval` is a positive number of seconds. It defaults to `30` and
is the periodic correctness fallback after the initial reconciliation.

`power_on_delay` is a non-negative number of seconds. It defaults to `15` and
is used after each successful connection and an observed off-to-on state change.

## mpv Playback

Local looping video playback through `mpv`, configured in the same YAML file
as TV control and driven by a separate `signage-controller playback ...`
command group. Playback is architecturally independent of TV control: a
player keeps looping fullscreen whether or not its associated TV is
reachable, and `tv_id` on a player is used only for logs/diagnostics, never
to gate playback.

`mpv` runs directly on the host alongside the controller: both the TV-control
commands and the playback commands are run by the same machine, and `mpv`
needs direct access to that host's graphical/display session. Running TV-only
commands (`pair`, `status`, `inputs`, `apply`, `run`) never requires `mpv` to
be installed.

### Install

```bash
sudo apt install mpv
```

### Playback Configuration

Add an optional `playback:` section to `config.yaml`:

```yaml
playback:
  mpv_binary: mpv
  restart_delay: 2
  hwdec: auto
  fullscreen: true
  loop: true
  audio: false

  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4

      # Optional association for logs/metadata only. Playback never depends
      # on TV reachability.
      tv_id: dev-tv

      # Optional display selection for multi-monitor hosts (mutually
      # exclusive). Numeric index or connector/output name, e.g. from
      # `xrandr --query` under X11:
      # screen: 0
      # screen_name: DP-1
```

A configuration without a `playback:` section behaves exactly as it did in
Phase 1 — no `mpv` dependency or runtime behavior is introduced.

`mpv_binary` is the `mpv` executable to run; defaults to `mpv` (resolved from
`PATH`). `restart_delay` is a non-negative number of seconds used as the base
delay before restarting a crashed player, with capped backoff on repeated
failures; defaults to `2`. `hwdec` is passed through to mpv's `--hwdec`
option; defaults to `auto` (mpv's own safe hardware-decode probing, with
automatic software fallback). `fullscreen`, `loop`, and `audio` are booleans
defaulting to `true`, `true`, and `false` respectively; `audio: false`
disables mpv's audio output entirely so playback never depends on a working
ALSA/PulseAudio/PipeWire device — the TV's own volume is set to zero
separately by TV control.

Each player needs a unique `id`, a non-empty `name`, and a `media` path.
Relative `media` paths resolve against the directory containing `config.yaml`
(not the process's working directory), so `./media/menu.mp4` next to
`/etc/screenkeeper/config.yaml` resolves to
`/etc/screenkeeper/media/menu.mp4`. `media` does not need to exist when the
configuration is loaded — a valid-but-missing path is a runtime "waiting for
media" state, not a configuration error; the player supervisor waits and
retries without crash-looping, and starts playback once the file appears.

`screen` (a non-negative integer) and `screen_name` (a connector/output name
string) are mutually exclusive and both optional; omitting both lets mpv use
the default/current display, which is the normal case for the one-display
development walkthrough below. Screen selection uses mpv's own native
`--screen`/`--fs-screen` (numeric) or `--screen-name`/`--fs-screen-name`
(named) options — Screenkeeper does not reimplement display enumeration.
Numeric monitor ordering is not guaranteed stable across reboots, especially
under Wayland; prefer `screen_name` once real connector names are known for a
given deployment. Screenkeeper does not hardcode connector names for any
specific hardware — those are always discovered and configured per host.

Display resolution and refresh rate are **not** playback configuration.
`mpv` renders fullscreen into whatever mode the display system already
provides and scales the source video while preserving aspect ratio; fixed
display mode-setting belongs to a later physical-display deployment phase.

### One-Player Development Walkthrough

With `mpv` installed and a `playback:` section like the example above added
to `config.yaml` (using a real local video file for `media`):

```bash
signage-controller playback check
```

Reports the resolved `mpv` binary path and version, configured players,
whether each player's media currently exists, configured `hwdec`/
`fullscreen`/`loop`/`audio` settings, and `DISPLAY`/`WAYLAND_DISPLAY`
environment hints. If `mpv` cannot be found, it prints an actionable message
instead of starting anything.

```bash
signage-controller playback command dev-menu
```

Prints the exact, shell-quoted `mpv` argv Screenkeeper would execute for that
player. This is diagnostic only — it never runs `mpv` — and is especially
useful while commissioning new hardware.

```bash
signage-controller playback start dev-menu
```

Launches that one configured player in the foreground: fullscreen, looping
indefinitely, no on-screen controls or OSD, no audio (unless configured
otherwise). Press **Ctrl-C** to exit cleanly; a deliberate Ctrl-C here always
exits and never restarts.

Unlike `playback run`, this command fails fast with a clear message if the
media file is missing rather than waiting for it to appear — during
interactive commissioning an immediate error is more useful than a silent
wait. Waiting/retrying is `playback run`'s job.

```bash
signage-controller playback run
```

Starts the persistent supervisor for every configured player — one
independent task per player, so one player's failure never stops another's.
If you kill the running `mpv` process directly (`kill <pid>`), the supervisor
restarts it automatically after a short backoff. Stop with **Ctrl-C**; this
shuts every player down cleanly (asking `mpv` to quit over its IPC socket,
then `SIGTERM`, then `SIGKILL` only as a last resort) with no orphaned `mpv`
processes left behind.

`playback run` uses its own single-instance lock (independent of TV `run`'s
lock), so both commands can run concurrently on the same host and state
directory, matching the architecture's independence between TV control and
playback.

### X11 and Wayland

Deterministic multi-monitor placement is expected primarily under X11, since
Wayland compositors generally do not let applications choose arbitrary
window positions. For the one-display development case, playback works
under either X11 or Wayland on the default display. Screenkeeper does not
install or configure Xorg, and does not switch or manage the host's
graphical session.

### Future Multi-Display Deployment

The eventual production shape adds one player per physical display, each
with its own discovered `screen_name`:

```yaml
playback:
  players:
    - id: menu-left
      media: /var/lib/screenkeeper/media/left.mp4
      tv_id: menu-left
      screen_name: DP-1
    - id: menu-center
      media: /var/lib/screenkeeper/media/center.mp4
      tv_id: menu-center
      screen_name: DP-2
    - id: menu-right
      media: /var/lib/screenkeeper/media/right.mp4
      tv_id: menu-right
      screen_name: DP-3
```

Connector names above are illustrative only; real names are discovered on
the target hardware, not assumed in advance. Physical display topology
management, EDID handling, Xorg provisioning, autologin, and production
multi-monitor mode-setting are deferred to that later deployment phase — see
"Real-Hardware Validation" below.

## Observability

Telemetry is optional and is active only for the long-running `run` command.
Commissioning commands (`pair`, `status`, `inputs`, and `apply`) keep their logs
local and do not publish metrics. Loki and Prometheus Remote Write below cover
TV control only; `playback run` does not yet publish player metrics —
extending `PrometheusStatusReporter` with `player_id`-labeled gauges
(`signage_controller_player_up`, `_restarts_total`,
`_status_updated_timestamp_seconds`) is documented, low-risk follow-up work,
not part of this phase.

### Loki Logs

Set `observability.loki.url` to Loki's `/loki/api/v1/push` endpoint and provide
the name of an environment variable containing its bearer token in
`bearer_token_env`. Telemetry endpoints must use HTTPS so bearer tokens are not
sent in clear text. Enter tokens without placing them in shell history:

```bash
read -rsp "Loki token: " LOKI_TOKEN; printf '\n'
export LOKI_TOKEN
```

The token is never written to logs. Loki shipping uses a bounded background
queue, batches records, and retries delivery without blocking television
control. Records include `service="signage-controller"`, the host `instance`,
the log `level`, optional configured `labels`, and `tv_id` for controller logs.
When Prometheus Remote Write is also configured, its `instance` value is used
for Loki too, so dashboard queries identify one controller consistently. Loki
receives only this application's logs; dependency protocol diagnostics are not
shipped even with `--debug`.
If delivery remains unavailable, the controller continues running and writes a
local diagnostic to standard error.

### Prometheus Remote Write

Set `observability.prometheus.remote_write_url` to the receiver's complete
remote-write URL and set `bearer_token_env` to the name of its bearer-token
environment variable:

```bash
read -rsp "Prometheus token: " PROMETHEUS_TOKEN; printf '\n'
export PROMETHEUS_TOKEN
signage-controller run
```

The target must be a Prometheus Remote Write receiver. For Prometheus itself,
enable the receiver with `--web.enable-remote-write-receiver`; configuring
Prometheus's outbound `remote_write` section alone is not sufficient. The
controller sends the stable Remote Write 1.0 protobuf format with raw Snappy
compression, bearer authentication, and the required protocol headers.

`job` defaults to `signage_controller`; `instance` defaults to the controller
host name when omitted. Both are attached as metric labels. Configure a stable,
unique `instance` when multiple controller processes publish to one receiver.
`push_interval` is a positive number of seconds and defaults to `15`. Status
changes are written promptly and the full current status is also written at that
interval.

The controller publishes these gauges, each labeled by `tv_id` unless noted:

- `signage_controller_tv_connection_up`: `1` while webOS is connected and `0`
  after a connection loss or graceful controller shutdown.
- `signage_controller_tv_power_on`: the latest reported on (`1`) or off (`0`)
  state.
- `signage_controller_tv_volume`: the latest reported absolute volume.
- `signage_controller_tv_input{tv_id,input_id}`: the latest reported input has
  value `1`.
- `signage_controller_tv_status_updated_timestamp_seconds`: when the controller
  last observed a connection or TV state update.

When a TV is unavailable, its last known power, input, and volume remain
published while `connection_up` becomes `0`. Alert on both `connection_up` and
the status-updated timestamp so an unreachable TV is not mistaken for a fresh
observation. Remote-write delivery errors are logged and do not stop TV control.

`grafana/signage-controller-dashboard.json` is an importable dashboard for
these metrics and the Loki logs. Grafana prompts for the Prometheus and Loki
datasources during import; it includes controller and TV selectors,
current-state summary, connection and power history, volume history, current
input, and TV-scoped logs.

## Logs and Recovery

Normal logs include local timestamps:

```text
2026-08-26 19:10:00 INFO dev-tv: connected
2026-08-26 19:10:00 INFO dev-tv: waiting 15s for TV services to settle
2026-08-26 19:10:15 INFO dev-tv: desired state verified; monitoring
```

When settings differ, the controller logs and changes only the mismatched
field:

```text
INFO dev-tv: current input HDMI_2; switching to HDMI_1
INFO dev-tv: volume 12; setting to 0
INFO dev-tv: desired state reached
```

If the TV is manually powered off, unplugged, still booting, or temporarily
unreachable, `run` remains alive and retries after 5, 10, 15, then capped
30-second intervals.

Routine "already configured; no change" input and volume messages are logged at
`DEBUG` so normal logs and Loki dashboards emphasize state changes and failures.
After each connection or observed power-on settle delay, an `INFO` verification
confirms that desired state has been reached and periodic monitoring is active.

A network failure alone does not prove that the TV is off. When webOS explicitly
reports power-off or standby, logs show:

```text
INFO dev-tv: TV shutting down
INFO dev-tv: connection closed after TV reported shutdown
```

Otherwise, the controller reports the uncertainty and retries:

```text
INFO dev-tv: connection lost without a shutdown notification; treating TV as unavailable
INFO dev-tv: TV unavailable (power state unknown: it may be off, booting, or network-unreachable: ...); retrying in 5s
```

Foreground-app changes are not treated as power events. This prevents webOS
transition screens from causing repeated HDMI switching. The periodic
reconciliation loop remains the fallback if a state update is missed.

Use `--debug` before the command for protocol diagnostics:

```bash
signage-controller --debug run
```

Debug output can include detailed TV and protocol information, so leave it off
in normal operation. Loki ships the controller's own debug records but excludes
dependency protocol logs.

### Playback Logs

`playback run` logs state transitions rather than polling activity:

```text
INFO dev-menu: starting mpv for /var/lib/screenkeeper/media/menu.mp4
INFO dev-menu: mpv started pid=1234
INFO dev-menu: playback healthy
```

An unexpected exit is reported once per occurrence, with the delay before the
next attempt and a bounded tail of mpv's own output:

```text
WARNING dev-menu: mpv exited unexpectedly with status 1; retrying in 2.0s
```

Repeated failures back off (base `restart_delay`, then capped multiples of it)
instead of restarting in a tight loop. The backoff resets once a player has
stayed up for a sustained period.

Missing media logs one transition in each direction, not one line per poll:

```text
INFO dev-menu: media unavailable; waiting for /var/lib/screenkeeper/media/menu.mp4
INFO dev-menu: media became available
```

Shutdown is likewise logged once per player:

```text
INFO dev-menu: stopping mpv
INFO dev-menu: mpv stopped
```

mpv's own stdout/stderr is never dumped at `INFO`. It is captured in a bounded
buffer, emitted line-by-line only at `DEBUG` (`signage-controller --debug
playback run`), and summarized in the `WARNING` above on an abnormal exit.

## Pairing State

By default, keys are stored at:

```text
$XDG_STATE_HOME/signage-controller/state.json
```

When `XDG_STATE_HOME` is unset, the default is:

```text
~/.local/state/signage-controller/state.json
```

The state directory is created with mode `0700` and the JSON file with mode
`0600` where supported. Use `--state-dir /var/lib/signage-controller` for a
later system-service deployment. Do not commit this state file.

## Deployment Shape

Screenkeeper is not containerized. The TV controller and the mpv players run
as two host-native processes on the same Linux machine — `signage-controller
run` and `signage-controller playback run` — reading the same `config.yaml`
and sharing the same state directory through independent locks. mpv needs
direct access to that host's graphical session, and the controller only makes
outbound LAN connections to each TV (it never listens for inbound traffic),
so there is nothing a split or containerized deployment would buy here.

Both processes are ordinary foreground commands; a later deployment phase can
supervise them with two systemd units alongside the physical-display work
listed under "Real-Hardware Validation" below.

## Automated Coverage

The suite uses no real TV. It covers:

- YAML parsing, timing validation, and unique TV IDs
- Separate persisted client keys per TV ID
- Idempotent input and volume convergence
- Invalid input reporting
- Unavailable-TV retry and reconnection
- Delayed connection and power-on reconciliation
- Input-ID normalization for LG webOS
- Transient app-state callback filtering
- Shutdown versus generic connection-loss logs
- Single-controller runtime locking
- Optional Loki log delivery and Prometheus Remote Write TV-status reporting
- Playback configuration validation (players optional/absent, unique IDs,
  `screen`/`screen_name` mutual exclusion, `tv_id` cross-referencing,
  relative `media` resolution against the config file)
- mpv argv construction (fullscreen/loop/audio/hwdec/screen-selection flags,
  IPC socket flag, safe handling of media filenames with spaces or a leading
  `-`)
- mpv JSON IPC request/reply framing, including interleaved event lines and
  error responses, against a fake Unix-socket server
- Player supervisor lifecycle: successful spawn, crash-triggered restart,
  one failed player not affecting others, missing-media wait/retry,
  media-appears-later recovery, and clean shutdown with no orphaned `mpv`
  processes

None of the automated coverage above requires `mpv`, an X server, Wayland, a
GPU, a physical display, or a real LG TV.

## Real-TV Validation

Before relying on this controller in production, verify on every TV model:

- Pairing approval and persisted-key reconnect
- Exact HDMI command IDs from `inputs`
- Input switching and volume-zero behavior
- Manual power-off and power-on recovery
- Network behavior while the TV is powered off

## Real-Hardware Validation (deferred)

Phase 2 adds mpv playback but intentionally does not yet implement or
validate physical display provisioning. Before production deployment on the
target Lenovo ThinkCentre M710q (three DP-to-HDMI outputs, one LG TV each),
still verify:

- Actual connector names for `screen_name` (do not assume `DP-1`/`DP-2`/
  `DP-3`)
- X11 vs. an alternative graphical environment for multi-monitor placement
- Three simultaneous 1920x1080@60Hz outputs
- Intel VA-API hardware-decode behavior (`hwdec: vaapi`) versus the default
  `hwdec: auto`
- HDMI/DP hotplug behavior and EDID behavior when a TV is powered off
- Whether display topology changes when one TV disappears

Physical display topology management, EDID forcing/capture, Xorg
configuration generation, autologin, kiosk desktop setup, and production
three-monitor mode-setting are out of scope until that hardware validation.
