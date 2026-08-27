# Signage Controller

Local desired-state control for LG webOS televisions. It connects directly to
each TV over the LAN, selects the configured HDMI input, and sets an absolute
volume without muting. It does not use LG ThinQ, cloud services, CEC,
Wake-on-LAN, media playback, or display-layout control.

This is Phase 1 of the signage controller. The configuration supports multiple
TVs, but start by commissioning one TV completely before adding more.

## Requirements

- Linux with Python 3.11 or newer
- Network reachability between the Linux host and the TV
- LG webOS local/mobile control enabled on the TV, if its firmware exposes the
  setting

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

## Observability

Telemetry is optional and is active only for the long-running `run` command.
Commissioning commands (`pair`, `status`, `inputs`, and `apply`) keep their logs
local and do not publish metrics.

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

## Real-TV Validation

Before relying on this controller in production, verify on every TV model:

- Pairing approval and persisted-key reconnect
- Exact HDMI command IDs from `inputs`
- Input switching and volume-zero behavior
- Manual power-off and power-on recovery
- Network behavior while the TV is powered off

Phase 1 intentionally excludes mpv playback, physical display layout,
systemd units, and HDMI/DP EDID behavior when a display is powered off.
