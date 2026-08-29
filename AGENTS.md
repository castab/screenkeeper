# AGENTS.md

## Project Purpose

`signage-controller` is a small Linux appliance component — Screenkeeper Edge —
that manages three independent concerns for digital signage:

1. LG webOS television control over the LAN (Phase 1): converges each
   configured TV to a desired HDMI input and absolute volume. One `run`
   process owns one asynchronous manager per configured TV.
2. Local mpv signage playback (Phase 2): keeps a configured local video
   looping fullscreen per configured player, independent of whether the
   associated TV is reachable. One `playback run` process owns one
   asynchronous supervisor per configured player.
3. Device identity, inventory, and reporting (Phase 3): gives the appliance a
   permanent identity, lets an administrator claim it with a short pairing
   code, and reports observed display hardware and configured bindings to an
   optional control plane. One `agent run` process, outbound-only.

All three are configuration-driven from the same YAML file and are
intentionally kept architecturally independent — see Behavioral Invariants
below.

This repository is a monorepo. The player application stays at the repository
root; `contracts/` holds the shared API contract, and `control/` will hold the
Screenkeeper Control application. The two applications depend on the contract,
never on each other's modules, so they remain independently deployable.

Keep the project deliberately boring and reliable. Prefer standard-library
Python, asyncio, explicit YAML configuration, local network/host control, and
small testable abstractions.

## Read First

- Read `README.md` before changing user-visible behavior or commissioning
  commands.
- Read the relevant files in `src/signage_controller/` and their tests before
  modifying controller behavior.
- Treat `config.yaml` and runtime pairing state as local, ignored files. Do not
  add them to Git or print their secrets.

## Setup And Verification

Use the project virtual environment when it exists:

```bash
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

The definitive automated check is:

```bash
.venv/bin/pytest
```

### Working From Windows

This project cannot be tested on Windows: `runtime_lock.py` and `updater.py`
use `fcntl`, mpv IPC uses Unix-domain sockets, and the installer and units are
Linux-native. `import signage_controller.cli` itself fails on Windows.

When the working directory is a Windows path, run everything through WSL
rather than reporting the suite as unrunnable:

```bash
wsl -d Ubuntu -- bash -lc 'cd /mnt/<drive>/path/to/screenkeeper && ~/sk-venv/bin/pytest -q'
```

The venv path is whatever you created; `~/sk-venv` is the one on the current
development machine.

- Keep the venv on the Linux filesystem (`$HOME`), not under `/mnt/`. Unix
  permissions and speed both suffer on a Windows drive, and the state-store
  and socket tests depend on those permissions.
- `python3 -m venv` needs the `python3-venv` package on Debian and Ubuntu.
  Without it the failure is an opaque `Failing command: .../bin/python3`.
- Never convert `scripts/install.sh` or `packaging/*.service` to CRLF. The
  `.gitattributes` rules pinning them to LF are load-bearing; bash and systemd
  both reject CRLF.

Also run syntax and whitespace checks after code changes:

```bash
.venv/bin/python -m compileall -q src tests
git diff --check
```

Tests must not require a real TV, a real control plane, GitHub, mpv, or an X
server. Extend the fakes in `tests/conftest.py` — `FakeTelevision`,
`FakeProcessLauncher`, `FakeTransport` (an injected control-plane transport),
and `FakeControlPlaneServer` (a loopback HTTP server for end-to-end contract
coverage) — and add focused async pytest coverage for behavior changes.

Prefer injecting a collaborator over monkeypatching: `opener`, `runner`,
`launcher`, `factory`, `transport`, and `sysfs_root` all exist for that.

## Source Layout

- `config.py`: typed YAML parsing and validation.
- `state_store.py`: local, permission-restricted pairing-key persistence, plus
  the `write_json_atomic` helper the device store shares.
- `http_client.py`: the no-redirect opener and JSON request helper shared by
  `observability.py` and the control-plane client. Redirects are refused so a
  30x can never hand a bearer token to another host. `updater.py` keeps its own
  GitHub-specific opener.
- `inventory/edid.py`: a deliberately small EDID reader. Always reports a
  SHA-256 of the raw bytes; every descriptive field is optional and omitted
  rather than guessed.
- `inventory/drm.py`: `/sys/class/drm` connector and GPU discovery. Reads sysfs
  rather than shelling out to `xrandr` or `drm_info`, so it works headless.
- `control_plane/identity.py`: `installation_id` and `device_token` persistence
  under `$STATE_DIR/device.json`.
- `control_plane/client.py`: the only implementation of
  `contracts/openapi.yaml` on the edge side. Its error taxonomy drives retry
  policy: unavailable is retried, an auth rejection is not.
- `control_plane/report.py`: the sanitization boundary. Everything sent to the
  control plane is assembled here from an allowlist.
- `control_plane/enrollment.py`: the operator-initiated pairing-code workflow.
- `control_plane/agent.py`: the heartbeat loop, mirroring `PlayerSupervisor`'s
  supervision shape.
- `tv/base.py`: narrow `Television` abstraction used by controller tests.
- `tv/lg_webos.py`: the only layer that depends on `aiowebostv`.
- `controller.py`: desired-state convergence, retry lifecycle, delayed
  reconciliation, and state interpretation.
- `cli.py`: `pair`, `status`, `inputs`, `apply`, `run`, the `playback`
  command group (`check`, `command`, `start`, `run`), the `device` command
  group (`status`, `inventory`, `enroll`), the `agent` command group (`run`),
  and the installation commands (`upgrade`, `rollback`, `versions`). The
  installation commands are dispatched before `load_config` and must stay that
  way: an upgrade has to work on a host whose `config.yaml` is missing or
  broken.
- `updater.py`: release resolution, checksum verification, versioned venv
  builds, atomic activation/rollback, pruning, and the update lock. Network
  access and subprocess execution are injected (`opener`, `runner`) so tests
  never touch GitHub or build a real venv.
- `scripts/install.sh`: first-time appliance install. Creates the same layout
  `updater.py` maintains afterwards.
- `packaging/*.service`, `packaging/*.timer`: systemd user units, templated
  with `@INSTALL_ROOT@`, `@CONFIG_DIR@`, and `@STATE_DIR@` by the installer.
- `runtime_lock.py`: prevents concurrent processes sharing one lock name in
  one state directory. TV `run`, `playback run`, and `agent run` use separate
  lock names (`controller.lock` / `playback.lock` / `agent.lock`) so they can
  run concurrently, since they are independent runtimes by design.
- `playback/mpv.py`: pure mpv argv construction (`build_mpv_argv`) and the
  `MpvPlayer` process-lifecycle abstraction (start/stop/query). The only
  module that calls `asyncio.create_subprocess_exec` for mpv.
- `playback/ipc.py`: a small client for mpv's JSON IPC protocol over its
  Unix-domain `--input-ipc-server` socket.
- `playback/supervisor.py`: `PlayerSupervisor` (one player's retry/backoff
  and missing-media handling) and `run_playback` (fans out one supervisor
  task per configured player, analogous to `controller.run_all`).

Keep `aiowebostv` types and protocol response shapes inside `tv/lg_webos.py`.
Before changing adapter calls, inspect the installed pinned library API rather
than guessing its signatures or response fields. Likewise, before changing
mpv argv construction, check the current mpv manual / `mpv --list-options`
rather than guessing flag names — mpv's own option set changes between
releases.

## Behavioral Invariants

- Use only local webOS control. Do not add LG ThinQ, cloud APIs, CEC,
  Wake-on-LAN, IR, smart plugs, or automated power scheduling.
- mpv playback (Phase 2) is implemented and authorized. Keep it fully
  independent of TV reachability: never gate player start/stop on TV
  connection state, and never make TV convergence depend on playback state.
  Do not write `if tv_online: start_player() else: stop_player()` or its
  equivalent — a TV being off is normal; the paired player keeps looping.
- Both subsystems are host-native. The project is deliberately not
  containerized: TV control and mpv playback run as two processes on the same
  Linux host. Do not reintroduce a Dockerfile, Compose file, or any other
  container packaging without explicit authorization.
- Playback configuration stays generic. Never hardcode Lenovo-specific
  connector names, resolutions, or refresh rates; `screen`/`screen_name`
  values are always operator-supplied.
- Physical monitor topology management, EDID forcing/capture, Xorg
  configuration generation, autologin, kiosk desktop setup, and production
  three-monitor mode-setting remain deferred pending verification on real
  Lenovo ThinkCentre M710q hardware. Do not add that work without explicit
  authorization, matching how mpv playback itself required this file's prior
  authorization before Phase 2 began. Scripted installation, systemd user-unit
  supervision, and self-update are authorized and implemented; the graphical
  session those units depend on is still part of the deferred work, which is
  why `screenkeeper-playback.service` binds to `graphical-session.target`
  instead of provisioning one.
- Control-plane connectivity (Phase 3) is implemented and authorized: permanent
  device identity, pairing-code enrollment, Linux hardware/display inventory,
  heartbeat reporting, the `agent run` runtime, the shared contract under
  `contracts/`, and `screenkeeper-agent.service`. Keep it a small slice. The
  following remain unauthorized without a further explicit grant: remote
  content distribution, object storage, playlists, schedules, deployment
  manifests, remote desired-configuration application, NATS/JetStream, remote
  shell, arbitrary server-issued commands, remote Linux administration, and
  additional television drivers. Leave extension points, not unused
  abstraction layers.
- The control plane is optional and must stay optional. Absence of a
  `control_plane:` section preserves current behavior exactly, and none of
  `run`, `playback run`, `pair`, `status`, `inputs`, `apply`, or
  `playback start` may require it. The appliance is fully functional offline.
- The agent is a peer of the other two runtimes, never a supervisor. It must
  not import or drive `Television` or `MpvPlayer`, and a control-plane outage,
  an auth rejection, or an agent crash must not affect TV convergence or mpv
  playback — nor may either of those stop heartbeats.
- Communication is outbound-only. Do not add an HTTP server on the player, port
  forwarding, remote shell, SSH exposure, or control-plane callbacks. The
  player initiates every request.
- Device identity is permanent. Generate `installation_id` and `device_token`
  once, on first use, and never regenerate them to repair an authentication
  failure — a new token abandons the enrollment an administrator claimed and
  makes the player unrecoverable. Upgrades and rollbacks must not replace them.
  A wiped state directory is a new installation and legitimately gets a new
  identity.
- The device token is a credential. Never print, log, commit, put it in YAML,
  or include it in `device status` output. It is `repr`-excluded on
  `DeviceIdentity` for this reason.
- Never infer the player's physical location from public IP, GPS, Wi-Fi SSID,
  hostname, timezone, or network topology. The control plane assigns the
  organization and location during enrollment. `hostname` is reported as an
  operational label only.
- Reported data is allowlisted in `control_plane/report.py`, never serialized
  wholesale from configuration. Never report LG client keys, the device token,
  bearer tokens, observability credentials, or local media paths.
- Keep observed and configured state separate in the heartbeat. `inventory` is
  what the kernel sees; `configured_bindings` is what an operator wrote. The
  control plane compares them, so merging them destroys the signal.
- Inventory collection is best-effort and must never raise. Partial data on
  WSL, VMs, headless hosts, and unusual drivers is expected, and no DRM devices
  at all is a valid result.
- Enrollment is operator-initiated. Do not request enrollments automatically
  from a long-running daemon.
- Distinguish `401`/`403` from a transient network failure. Log an actionable
  error and back off heavily rather than retrying a rejected credential at
  heartbeat frequency, and log control-plane reachability transitions rather
  than every failed retry.
- Within `/api/v1`, evolve the contract additively: tolerate unknown response
  fields, and never require edge and control releases to match versions. The
  monorepo gives coordinated development, not lockstep deployment.
- Never mute the TV as a substitute for setting `desired_volume: 0`.
- Desired-state changes are idempotent: read actual input and volume, then send
  only the command needed for a mismatch.
- Validate `desired_input` against TV-reported inputs before switching.
- LG input switching uses the device `id` such as `HDMI_1`; an app ID such as
  `com.webos.app.hdmi1` is not a valid `set_input` command value.
- A network failure means the TV is unavailable, not definitely powered off.
  Log shutdown only after an explicit webOS power-off or standby state.
- Keep the controller alive when a TV is unavailable. Use capped backoff and
  avoid repeated INFO-level retry noise.
- `run` waits `power_on_delay` after connect and observed power-on before the
  first desired-state reconciliation. Do not treat transient foreground-app
  callbacks as power events.
- Preserve the single-instance runtime lock. Do not instruct users to stop
  `run` with Ctrl-Z; use Ctrl-C for normal shutdown.
- An upgrade must never mutate the installation it is running from. Build each
  version under its own `versions/<version>/` directory, smoke-test it there,
  and only then repoint `current`. A failed build leaves the running version
  active and untouched.
- Build every venv at its final path. Never build in a staging directory and
  rename it into place: pip bakes absolute paths into console-script shebangs
  and `pyvenv.cfg`, so a relocated venv breaks the moment it is activated.
  This applies to `updater.py` and `scripts/install.sh` alike.
- Keep `config.yaml` and the state directory outside the versioned install
  tree, so upgrades and rollbacks cannot touch configuration or pairing keys.
- Never delete the active version when pruning, and keep rollback working by
  retaining at least one prior version.
- The unattended-upgrade timer ships installed but disabled. Do not enable it
  by default; an upgrade restarts playback.
- `screenkeeper-agent.service` ships installed but disabled, for the same
  reason and because it is useless before enrollment. `restart_services`
  therefore uses `try-restart` for it, not `restart`: `restart` would *start*
  the agent on every host that never enabled it. `agent run` exits 0 when no
  `control_plane:` section exists, so a manually started unit stops cleanly
  instead of restart-looping.
- `__version__` in `src/signage_controller/__init__.py` is the single source of
  truth for the version. `pyproject.toml` reads it dynamically. Do not
  reintroduce a hardcoded `version =` in `pyproject.toml`, and do not let a
  release tag disagree with it — `upgrade` rejects a build whose reported
  version does not match the release.

## State And Security

- Client keys and the device token are credentials. Never hardcode, commit,
  print, or log them. `state.json` holds pairing keys; `device.json` holds the
  device token. Both are `0600` inside a `0700` directory, written through
  `state_store.write_json_atomic`.
- Default runtime state is under `$XDG_STATE_HOME/signage-controller/` or
  `~/.local/state/signage-controller/`; production can use
  `/var/lib/signage-controller/` through `--state-dir`.
- Preserve `0700` state-directory and `0600` state-file permissions where the
  host supports them.
- mpv IPC sockets live under `$XDG_RUNTIME_DIR/screenkeeper/mpv/` (or a
  per-uid `/tmp` fallback when `XDG_RUNTIME_DIR` is unset), created `0700`.
  Never place them in a Git-controlled directory; they are removed/recreated
  per launch, not persisted state.
- Do not run `pair`, `apply`, or `run` against a real TV unless the user has
  explicitly requested that external action. `inputs` and `status` can also
  create a pairing state on an unpaired TV, so treat them as real-device
  operations too.
- Do not run `playback start` or `playback run` unless the user has
  explicitly requested that external action — both launch a real mpv process
  against the host's graphical session. `playback check` and
  `playback command` are diagnostic-only and never launch mpv.
- Do not run `device enroll` or `agent run` against a real control plane
  without an explicit request: enrollment registers this host with a remote
  service, and the agent reports to it continuously. `device status` and
  `device inventory` are local and read-only — `inventory` contacts nothing and
  `status` does not even create an identity.
- Do not run `scripts/install.sh` without an explicit request: it requires
  root, creates a system account, and writes outside the repository.
  `signage-controller upgrade` mutates a real installation and restarts units,
  so treat it the same way. `upgrade --check` and `versions` are read-only.

## Documentation Expectations

- Update `README.md` and `config.example.yaml` when configuration keys, CLI
  behavior, timing, logging, or commissioning steps change — this applies to
  `playback:` and `control_plane:` keys and their CLI commands exactly as it
  does to `tvs:` keys and TV commands.
- `contracts/openapi.yaml` is the source of truth for edge/control
  communication. Change it before changing either side's code, keep
  `contracts/examples/*.json` in step (`tests/test_contracts.py` enforces
  this), and do not generate a client from the control application.
- Keep `README.md`, `scripts/install.sh`, and `packaging/` consistent when the
  install layout, unit names, or update flow change. The installer and
  `updater.py` maintain the same tree, so a change to one is a change to both.
- Keep the README one-TV walkthrough and one-player mpv walkthrough accurate
  and executable from a clean checkout.
- Document actual command IDs as TV-specific values discovered through
  `signage-controller inputs <tv-id>`; never assume a configured HDMI label is
  universally valid.
- Before finalizing mpv argv construction or documentation, verify flag
  spellings against the current mpv manual or `mpv --list-options` rather
  than assuming this file's examples remain exact on every mpv release.
