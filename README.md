# Signage Controller

Screenkeeper Edge manages four independent concerns for digital signage from
one YAML configuration file:

1. **LG webOS television state over the LAN** (Phase 1): connects directly to
   each TV, selects the configured HDMI input, and sets an absolute volume
   without muting. It does not use LG ThinQ, cloud services, CEC, or
   Wake-on-LAN.
2. **Local signage playback through mpv** (Phase 2): keeps a configured local
   video looping fullscreen on the host's display, independent of whether the
   paired TV is reachable.
3. **Device identity, inventory, and reporting** (Phase 3, optional): gives the
   appliance a permanent identity, lets an administrator claim it with a short
   pairing code, and reports the displays and configured relationships it
   observes to a control plane. See "Control Plane" below.
4. **Content synchronization** (Phase 4, optional): reconciles control-plane
   assignments into a verified local cache and lets playback switch only after
   a complete, checksum-valid download. See "Content Distribution" below.

These subsystems are intentionally decoupled. A TV being powered off does not
stop its player from looping; a player being unavailable does not stop TV
convergence; content synchronization and reporting are peer runtimes that stop
neither. When staff turns a TV back on, Screenkeeper switches it to the correct
HDMI input and the video is already there.

The configuration supports multiple TVs and multiple players, but start by
commissioning one TV and one player completely before adding more.

**Signage keeps running without the Internet.** TV control is local to the LAN
and mpv always plays local files. A WAN, control-plane, or object-storage outage
prevents new assignments from arriving but does not interrupt the active video.

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

There are two ways to install, for two different jobs.

### Appliance Install

On the signage host, use the installer. It creates a versioned, self-updating
deployment and the systemd user units:

```bash
curl -fsSL https://raw.githubusercontent.com/castab/screenkeeper/main/scripts/install.sh \
  -o install.sh
sudo bash install.sh
```

**Before the first release tag exists**, there is nothing for the installer to
resolve, so install from a branch instead:

```bash
sudo bash install.sh --ref main
```

Review the script before running it as root. Useful options:

| Option | Purpose |
| --- | --- |
| `--ref main` | Install from a branch instead of a release. |
| `--version 0.2.0` | Install a specific published release. |
| `--user NAME` | Service account to own and run the install (default `signage`). |
| `--no-mpv` | Skip mpv; TV control only. |
| `--no-units` | Skip the systemd user units. |
| `--install-alloy` | Also install and configure Grafana Alloy, the optional host telemetry agent (apt-based hosts only). See "Observability". |
| `--force` | Rebuild the version directory even if it already exists. |

The installer is idempotent, so re-running it is safe. Once a release tag
exists, prefer `signage-controller upgrade` for updates — see "Updating".

What it creates:

```text
/opt/screenkeeper/versions/<version>/venv/   immutable per-version install
/opt/screenkeeper/current -> versions/<ver>  atomically swapped symlink
/usr/local/bin/signage-controller            -> current/venv/bin/signage-controller
/etc/screenkeeper/config.yaml                survives upgrades
/var/lib/signage-controller/                 pairing keys and device identity,
                                             survives upgrades
```

Every runtime process is a systemd **user** unit owned by a dedicated,
lingering account. mpv needs that user's graphical session, and running the TV
controller as the same user is what lets `signage-controller upgrade` work
without root. The installer templates the units into place but enables nothing;
see "Deployment Shape" for which ones to enable and when.

### Development Install

For working on the project from a checkout, use a plain virtual environment.

On Debian or Ubuntu, install virtual-environment support if needed:

```bash
sudo apt install python3-venv
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

A development checkout is updated with `git`, not with
`signage-controller upgrade`; the upgrade command refuses to run against
anything but a managed install tree.

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

# Optional local Prometheus metrics. See "Observability" below.
metrics:
  enabled: true
  host: 127.0.0.1
  port: 9464

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

The optional `playback:`, `metrics:`, `control_plane:`, and `content:` sections are
documented in their own sections below. Omitting any of them preserves the
behavior described above exactly.

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
the target hardware, not assumed in advance. `signage-controller device
inventory` reports the connector names this host actually has. Physical display
topology management, EDID *forcing*, Xorg provisioning, autologin, and
production multi-monitor mode-setting are deferred to that later deployment
phase — see "Real-Hardware Validation" below.

## Control Plane

Optional. Everything above works without it, and nothing below can interfere
with TV control or mpv playback.

The control plane exists to answer questions you cannot answer by walking up to
a box: which appliance is this, where is it installed, and what displays are
actually plugged into it? It distinguishes three things:

```text
IDENTITY        Who am I?                              implemented
OBSERVED STATE  What hardware do I currently see?      implemented
DESIRED CONTENT Which immutable video should play?     implemented
```

### What it does not change

- **LG TV control stays local.** The appliance talks to each TV over the LAN,
  exactly as before. The control plane is never in that path.
- **mpv stays local.** Players loop verified local files with no network
  dependency. Signed URLs are consumed only by the content runtime.
- **Signage survives a WAN failure.** A control-plane outage stops heartbeats
  and nothing else. TVs keep converging, video keeps looping.
- **The player initiates everything.** There is no inbound connection, no
  listening port, no callback, and no remote shell. A Screenkeeper host needs
  no inbound Internet access at all.
- **Location is assigned, never inferred.** The appliance does not look at
  public IP, GPS, Wi-Fi SSID, hostname, timezone, or network topology to guess
  where it is. An administrator assigns it to an organization and location when
  they claim it.

### Configuration

```yaml
control_plane:
  base_url: https://screenkeeper.example.com
  heartbeat_interval: 30
  request_timeout: 10
```

`base_url` is the server root; the appliance appends `/api/v1` itself. Omitting
the whole section preserves current behavior exactly.

HTTPS is required, because every request after enrollment carries a bearer
token. Plain HTTP is accepted in exactly two cases, and never silently: a
loopback host (`localhost`, `127.0.0.1`, `::1`), or an explicit
`allow_insecure_http: true` for development against a local server. Any other
`http://` URL is a configuration error.

`heartbeat_interval` and `request_timeout` are positive numbers of seconds,
defaulting to `30` and `10`.

### Identity

On first use the appliance generates its own identity and stores it in the
state directory beside the TV pairing keys:

```text
$STATE_DIR/device.json     0600, inside a 0700 directory
```

| Field | Meaning |
| --- | --- |
| `installation_id` | A UUIDv4. Public identity, safe to display and log. |
| `device_token` | 256 bits of cryptographic randomness. **A credential.** |

The token is sent once, in the enrollment request body, and is a bearer token
thereafter. The server stores only a hash of it and never sends it back — so
the control plane never has to issue a permanent secret to the player. It is
never printed, never logged, and never appears in `device status`.

Identity is permanent. It survives restarts, upgrades, and rollbacks, because
the state directory lives outside the versioned install tree. A host that keeps
its state directory keeps its identity. A wiped state directory is a new
installation and gets a new one.

### Enrollment

```bash
signage-controller device enroll
```

```text
Screenkeeper enrollment

Pairing code:

    Q7KM-4HF2

Waiting for this player to be claimed...
```

An administrator enters that code in the control plane and assigns the player
to a location. The command then prints what was assigned and exits:

```text
Player claimed.

Player:       Main Menu Wall
Organization: Example Restaurant
Location:     Downtown
```

The assignment is stored locally, so `device status` can report it offline.

Enrollment needs neither the TV controller nor mpv running. If the code expires
before anyone claims it, the appliance requests a new one **with the same
identity** and shows the new code — it never generates a new device token to
recover, because that would abandon an enrollment an administrator may be about
to claim. Enrollment is always operator-initiated; no daemon requests one.

### Inventory

```bash
signage-controller device inventory
signage-controller device inventory --json
```

Reads `/sys/class/drm` directly rather than shelling out to `xrandr` or
`drm_info`, so it works over SSH, headless, and before any graphical session
exists. It needs no X11, no Wayland, no mpv, no television, and no Internet,
and it contacts nothing.

```text
GPUs:
  card0  vendor=0x8086  device=0x5912  driver=i915
Connectors:
  DP-1: connected, enabled
    modes: 1920x1080, 1280x720
    edid: 9f2c1a4e8b6d3f57…  GSM  LG TV  serial=205NTKM1G347
  HDMI-A-1: disconnected, disabled
```

For each connector it reports the name, status, whether it is enabled, its
modes, and an EDID summary. Every EDID field except the SHA-256 hash is
optional and is omitted rather than guessed. The hash is the useful part even
on its own: a changed hash means somebody swapped the physical display.

Collection is best-effort and degrades gracefully. Partial data on VMs, WSL,
headless servers, and unusual drivers is expected, and **no DRM devices at all
is a valid result**, not an error.

### What a heartbeat reports

Two things, deliberately kept apart:

```text
OBSERVED     DP-1 is connected and its EDID hashes to abc123
CONFIGURED   dev-menu is configured for DP-1 and dev-tv
```

The first comes from the kernel. The second comes from the `tvs:` and
`playback.players:` sections you already wrote. Reporting them separately lets
the control plane notice later that they disagree — a display swapped, or a
player bound to a connector that no longer exists.

The configured half is assembled from an allowlist, so it never carries LG
client keys, the device token, metrics configuration, or local media paths.
Content synchronization status is reported separately from heartbeats and
contains immutable revision IDs, never paths or signed URLs.

A heartbeat is a snapshot of the present, not an event log.

### Running the agent

```bash
signage-controller agent run
```

Long-running and outbound-only. It requires an existing enrollment, collects
inventory, sends heartbeats on the configured interval, and retries safely when
the control plane is unavailable — 5s, 10s, 20s, 30s, then 60s, with jitter so
a fleet that lost the WAN together does not reconnect in lockstep.

It logs transitions, not retries:

```text
control plane reachable
control plane unavailable
heartbeat restored
authentication rejected
```

A rejected credential (`401`/`403`) is treated differently from a network
failure: the agent logs an actionable error and backs off heavily rather than
hammering the server, and it never quietly creates a new identity to "fix"
itself.

The agent holds its own `agent.lock`, separate from `controller.lock` and
`playback.lock`, so it runs alongside the other two runtimes without
interfering with either.

### The shared contract

`contracts/openapi.yaml` at the repository root is the source of truth for
communication between Screenkeeper Edge and Screenkeeper Control. Both
implement it; neither imports the other's code, which is what lets the two ship
independently even though they live in one repository. Within `/api/v1`,
clients tolerate unknown response fields and evolution is additive — edge and
control releases never need to match versions.

## Content Distribution

Optional. Add `content:` only after configuring and enrolling with a control
plane:

```yaml
content:
  enabled: true
  reconcile_interval: 300
  download_timeout: 3600
  # cache_dir: /var/lib/signage-controller/media
```

Presence defaults `enabled` to `true`. The cache defaults to
`<state-dir>/media`; an override must be absolute and outside `/etc`. Omitting
the block preserves local-only playback exactly.

The content runtime fetches assignment metadata from Screenkeeper Control and
downloads media bytes directly from an S3-compatible store through short-lived
HTTPS URLs. It does not persist those URLs. Objects are deduplicated and named
only by lowercase SHA-256, downloaded into a unique `.part` file, flushed and
fsynced, checked for exact byte size and digest, and atomically installed. Only
then does the selected mapping change. Original filenames never become local
or object-storage paths.

```bash
signage-controller content status
signage-controller content status --json
signage-controller content sync
signage-controller content run
```

`status` is local-only. `sync` performs one reconciliation under
`content.lock` and exits nonzero when the desired revision could not be made
available, without replacing valid active media. `run` repeats the same core
with jittered capped backoff and heavier authentication-failure backoff.

Verified remote content wins over a player's YAML `media` path. That YAML path
remains the fallback: clearing an assignment returns to it when the file still
exists; otherwise Screenkeeper retains the last verified remote selection.
Playback gracefully restarts mpv only when the resolved local path changes.
Selected and actually-playing revisions remain distinct, so an mpv failure is
not mistaken for successful playback.

Cached objects are retained indefinitely in this MVP. A bad manifest, expired
URL, interrupted transfer, checksum mismatch, restart, reporting failure, or
WAN/storage outage leaves the selected mapping untouched. Existing signage
therefore continues looping while a replacement is unavailable.

## Observability

Screenkeeper produces telemetry locally; it never transports it anywhere.
`signage-controller run`, `playback run`, and `agent run` can each expose a
local Prometheus `/metrics` endpoint, bound to `127.0.0.1` by default, for a
host-level agent such as [Grafana Alloy](https://grafana.com/docs/alloy/) to
scrape and forward. Screenkeeper holds no remote URL and no telemetry
credential of any kind — see `docs/architecture/observability.md` for the full
architecture, the host-agent setup, and the "Screenkeeper knows X, does not
know Y" boundary. `scripts/install.sh --install-alloy` automates installing
and configuring Alloy on an edge host (see `deploy/alloy/README.md`); remote-
write credentials are always left for an operator to fill in by hand.
Commissioning and content commands publish no metrics; only `run`, `playback
run`, and `agent run` do, and only when `metrics.enabled` is true.

Set `metrics:` in `config.yaml`:

```yaml
metrics:
  enabled: true
  host: 127.0.0.1
  port: 9464
```

`run` binds `metrics.port` (default `9464`); `playback run` binds
`metrics.port + 1` (default `9465`); `agent run` binds `metrics.port + 2`
(default `9466`) — since all three commands read the same config file and
commonly run on the same host. Omitting `metrics:` entirely, or setting
`enabled: false`, disables the endpoint — the controller, playback
supervisor, and heartbeat agent behave identically either way; nothing about
TV control, mpv playback, or control-plane reporting depends on metrics
being enabled, reachable, or scraped.

`run` publishes these metrics, each labeled by `tv_id` unless noted:

- `signage_controller_tv_connection_up`: `1` while webOS is connected and `0`
  after a connection loss or graceful controller shutdown.
- `signage_controller_tv_power_on`: the latest reported on (`1`) or off (`0`)
  state.
- `signage_controller_tv_volume`: the latest reported absolute volume.
- `signage_controller_tv_input{tv_id,input_id}`: the latest reported input has
  value `1`.
- `signage_controller_tv_status_updated_timestamp_seconds`: when the
  controller last observed a connection or TV state update.
- `signage_controller_tv_commands_total{tv_id,command,result}`: TV commands
  issued (`command` is `set_input` or `set_volume`; `result` is `success` or
  `failure`).
- `signage_controller_tv_command_duration_seconds{tv_id,command}`: TV command
  latency.

`playback run` publishes, labeled by `player_id`:

- `signage_controller_player_up`: `1` while a player's mpv process is running
  normally.
- `signage_controller_player_restarts_total`: mpv restarts after an
  unexpected exit.
- `signage_controller_player_status_updated_timestamp_seconds`: when a
  player's status was last observed.

`agent run` publishes, unlabeled (there is one heartbeat agent per player):

- `signage_controller_control_plane_reachable`: `1` if the last heartbeat
  attempt reached the control plane, `0` otherwise.
- `signage_controller_control_plane_auth_rejected`: `1` if the control plane
  is currently rejecting this device's credential — re-enroll with
  `signage-controller device enroll` when this is set.
- `signage_controller_control_plane_heartbeats_total{result}`: heartbeat
  attempts, `result` is `success` or `failure`.
- `signage_controller_control_plane_last_success_timestamp_seconds`: when a
  heartbeat last succeeded.

All three processes also expose the standard `prometheus_client` process
collectors (`process_start_time_seconds`, `process_resident_memory_bytes`,
etc.) with no extra code, which is enough to answer "when did this process
last start" without a dedicated uptime metric.

When a TV is unavailable, its last known power, input, and volume remain
published while `connection_up` becomes `0`. Alert on both `connection_up` and
the status-updated timestamp so an unreachable TV is not mistaken for a fresh
observation.

`grafana/signage-controller-dashboard.json` is an importable dashboard for
these metrics. Grafana prompts for a Prometheus datasource during import; it
includes controller/TV/player selectors, current-state summary, connection
and power history, volume history, current input, playback state, and TV
command outcomes.

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
`DEBUG` so normal logs emphasize state changes and failures.
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
in normal operation. A host-level agent (e.g. Grafana Alloy) can tail these
logs via systemd's journal without any Screenkeeper-side log shipping code;
see `docs/architecture/observability.md`.

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

## Local State

Credential state and content state live in the state directory:

```text
$XDG_STATE_HOME/signage-controller/state.json    LG TV pairing keys
$XDG_STATE_HOME/signage-controller/device.json   device identity and enrollment
$XDG_STATE_HOME/signage-controller/content/      desired/selected/playing state
$XDG_STATE_HOME/signage-controller/media/        verified hash-named media
```

When `XDG_STATE_HOME` is unset, the default is
`~/.local/state/signage-controller/`.

The state directory is created with mode `0700` and each JSON file with mode
`0600` where supported. Both are written atomically — a temporary file, fsynced
and renamed into place — so an interrupted write cannot leave a half-written
credential behind.

An appliance install puts the directory at `/var/lib/signage-controller`
instead, which every systemd unit passes through `--state-dir`. It is outside
the versioned install tree, so upgrades and rollbacks never touch pairing keys
or device identity. Do not commit these files.

`device.json` holds the `device_token`, which is a credential in the same class
as an LG client key: never printed, never logged, and absent from
`signage-controller device status`. Deleting the state directory discards the
appliance's identity — the host becomes a new installation and has to enroll
again.

Runtime lock files also live here — `controller.lock`, `playback.lock`,
`agent.lock`, and `content.lock` — one per independent runtime, which is what
lets all four run at the same time.

## Deployment Shape

Screenkeeper Edge is not containerized. The TV controller, mpv players,
control-plane agent, and content reconciler run as host-native peer processes
on the same Linux machine, reading the same `config.yaml` and sharing the state
directory through independent locks. mpv needs direct access to the graphical
session, and the appliance only makes outbound connections.

All are ordinary foreground commands, and all are supervised by systemd user
units on an installed host:

| Unit | Runs | Started by |
| --- | --- | --- |
| `screenkeeper.service` | `run` (TV control) | `default.target`, at boot with linger |
| `screenkeeper-playback.service` | `playback run` (mpv) | `graphical-session.target` |
| `screenkeeper-agent.service` | `agent run` (reporting) | Installed but **not enabled** |
| `screenkeeper-content.service` | `content run` (synchronization) | Installed but **not enabled** |
| `screenkeeper-upgrade.timer` | `upgrade` | Installed but **not enabled** |

The playback unit is deliberately bound to `graphical-session.target` rather
than started at boot, because mpv needs a real graphical session. Setting that
session up automatically — autologin and kiosk desktop configuration — is part
of the deferred physical-display work under "Real-Hardware Validation" below.
Until then, playback starts when the signage user's graphical session does.

The agent and content units ship installed but disabled, because they are
useless until the corresponding configuration exists and the player has been
claimed. Enable them once enrollment succeeds:

```bash
systemctl --user enable --now screenkeeper-agent.service
systemctl --user enable --now screenkeeper-content.service
```

Existing installations are unaffected by its arrival. `signage-controller
upgrade` refreshes both with `try-restart`, which does nothing unless a unit is
already running, so an upgrade never starts a runtime nobody enabled. If either
is started without its required configuration it logs that fact and exits 0
rather than restart-looping.

The units are independent by design, matching the invariant that a TV being off
must never stop playback and vice versa — and that a control plane being
unreachable stops neither. No unit `Requires` another. The controller, agent,
and content units have no `network-online.target` dependency because they
already treat an unreachable peer as normal and retry with capped backoff.

## Updating

On an installed host, updates need no root — the service account owns the
install tree:

```bash
signage-controller upgrade --check    # is a newer release published?
signage-controller upgrade            # install it, activate it, restart the units
signage-controller versions           # what is installed, and what is active
signage-controller rollback           # go back to the previous version
```

`upgrade --check` exits `0` when the host is current and `10` when a newer
release is available, so it is usable from a monitoring script.

### How An Upgrade Works

1. Resolve the latest release from the GitHub Releases API.
2. Download the published sdist and verify it against the release's
   `SHA256SUMS` asset. Pass `--require-checksum` to make a missing checksum
   fatal rather than a warning.
3. Build a **new** virtual environment under
   `/opt/screenkeeper/versions/<new-version>/`. The running version is never
   touched, because it lives in its own directory.
4. Smoke-test the new build by running its `signage-controller --version` and
   confirming it reports the version the release claims.
5. Only then repoint `current` with a single atomic symlink rename.
6. Restart the units and prune old versions, keeping the newest `--keep`
   (default 3).

If any step before 5 fails, the partial build is removed and the host keeps
running exactly what it was running. That is why `rollback` is instant: the
previous version is still installed, so rolling back is one more symlink swap.

The virtual environment is built at its final path rather than staged and
moved, because pip bakes absolute paths into console-script shebangs and
`pyvenv.cfg`. A venv built somewhere else and relocated is broken at exactly
the moment it is activated.

### Unattended Upgrades

The timer is installed but **not enabled**, deliberately. An upgrade restarts
playback, and an unattended restart during business hours is a worse failure
than running one version behind. Enable it only once the window in
`screenkeeper-upgrade.timer` is genuinely outside business hours for these
displays:

```bash
systemctl --user enable --now screenkeeper-upgrade.timer
```

### Publishing A Release

`upgrade` reads GitHub Releases, so an update only becomes available when a
release exists. Pushing a `v*` tag runs `.github/workflows/release.yml`, which
runs the tests, verifies the tag matches `__version__`, builds the sdist and
wheel, and publishes them with a `SHA256SUMS` asset.

The version has one source of truth: `__version__` in
`src/signage_controller/__init__.py`. `pyproject.toml` reads it, `--version`
reports it, and the release workflow refuses to publish a tag that disagrees
with it. To cut a release, bump that constant, commit, then:

```bash
git tag v0.2.0
git push origin v0.2.0
```

## Developing on Windows with WSL

The project is Linux-only at runtime — `runtime_lock.py` uses `fcntl`, mpv IPC
uses Unix-domain sockets, and the installer and units are Linux-native — so
the test suite cannot run on Windows directly. WSL is the practical way to
develop this from a Windows machine, and the repository can stay on the
Windows filesystem:

```powershell
wsl -d Ubuntu
```

```bash
sudo apt install python3-venv       # needed; see the note below
python3 -m venv ~/screenkeeper-venv
~/screenkeeper-venv/bin/python -m pip install --upgrade pip
cd /mnt/c/path/to/screenkeeper      # or /mnt/h/..., wherever the checkout is
~/screenkeeper-venv/bin/python -m pip install -e '.[test]'
~/screenkeeper-venv/bin/pytest
```

Three things are worth knowing:

- **Put the virtual environment on the Linux filesystem**, not under `/mnt/`.
  A venv on a Windows drive is markedly slower and cannot represent the Unix
  permissions the state store and IPC sockets rely on. Keeping the checkout on
  `/mnt/` and the venv in `$HOME` works well.
- **`python3-venv` is a separate package** on Debian and Ubuntu. Without it,
  `python3 -m venv` fails with an opaque `Failing command: .../bin/python3`.
  The installer and `signage-controller upgrade` both check for it up front
  and say so plainly.
- **Line endings matter.** `scripts/install.sh` and the unit files are pinned
  to LF in `.gitattributes`, because bash and systemd both reject CRLF. Do not
  remove those rules when editing from Windows.

`pytest` writes its temporary directories to the Linux filesystem, which is
what the symlink and permission tests need — running them against a `/mnt/`
path would fail for reasons that have nothing to do with the code.

For anything beyond the test suite, WSL is a development environment, not a
deployment target. It has no LG TV on its LAN by default, no graphical session
for mpv, and in many configurations no systemd user manager, so `pair`,
`playback run`, and the systemd units still belong on the real signage host.

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
- Local Prometheus `/metrics` exposition for TV and playback status
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
- Update handling: version ordering and pre-release precedence, release
  resolution from the GitHub API (including the "no release published yet"
  and unknown-tag cases), checksum verification and mismatch rejection,
  building a version without activating it, leaving the running install
  untouched when a build fails, refusing a build that reports the wrong
  version, building the venv at its final path, atomic activation and
  rollback, ignoring interrupted builds, pruning that never removes the
  active version, and update locking
- Device identity: stable UUIDv4 installation ID, a 256-bit token, persistence
  across restarts, `0700`/`0600` permissions, the token's absence from `repr`
  and from `device status`, and a wiped state directory becoming a new
  installation
- Control-plane configuration: absent section still valid, malformed URL
  rejected, plain HTTP rejected by default, loopback HTTP and an explicit
  `allow_insecure_http` accepted
- EDID parsing against committed binary fixtures: a valid blob, a truncated
  read, a bad checksum, and a bad header — the last three reporting a hash and
  inventing nothing
- DRM inventory against a synthetic sysfs tree: connected and disconnected
  connectors, missing and empty EDID, GPU vendor/device/driver, and a missing
  `/sys/class/drm` yielding a valid empty inventory
- Reported bindings: configured TVs without client keys, player `tv_id` and
  screen mapping, and no local media path or telemetry credential in the
  payload
- Enrollment: creating an enrollment, the displayed code, pending and claimed
  polls, an expired code starting a new enrollment without changing device
  identity, and clear handling of an invalid credential
- Heartbeat: the bearer header, a serialized report matching the contract, a
  transient failure not killing the agent, backoff progression and jitter
  bounds, recovery resetting failure state, an auth rejection backing off
  heavily without touching identity, and clean shutdown
- Content synchronization: optional configuration, strict manifest parsing,
  URL-free persisted metadata, cache hits, shared-object deduplication,
  streamed size/SHA-256 verification, atomic installation, invisible `.part`
  files, outage-safe active selection, traversal resistance, playing markers,
  and local-first playback resolution
- The shared contract: the committed `contracts/examples/*.json` matching what
  the client produces and consumes, plus end-to-end enrollment, heartbeat, and
  content request coverage over real HTTP against a loopback server

None of the automated coverage above requires `mpv`, an X server, Wayland, a
GPU, a physical display, a real LG TV, or a real control plane.

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
