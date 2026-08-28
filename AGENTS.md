# AGENTS.md

## Project Purpose

`signage-controller` is a small Linux appliance component that manages two
independent desired states for digital signage:

1. LG webOS television control over the LAN (Phase 1): converges each
   configured TV to a desired HDMI input and absolute volume. One `run`
   process owns one asynchronous manager per configured TV.
2. Local mpv signage playback (Phase 2): keeps a configured local video
   looping fullscreen per configured player, independent of whether the
   associated TV is reachable. One `playback run` process owns one
   asynchronous supervisor per configured player.

Both subsystems are configuration-driven from the same YAML file and are
intentionally kept architecturally independent — see Behavioral Invariants
below.

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
wsl -d Ubuntu -- bash -lc 'cd /mnt/<drive>/path/to/screenkeeper && ~/screenkeeper-venv/bin/pytest -q'
```

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

Tests must not require a real TV. Extend the fake television in
`tests/conftest.py` and add focused async pytest coverage for controller or
adapter changes.

## Source Layout

- `config.py`: typed YAML parsing and validation.
- `state_store.py`: local, permission-restricted pairing-key persistence.
- `tv/base.py`: narrow `Television` abstraction used by controller tests.
- `tv/lg_webos.py`: the only layer that depends on `aiowebostv`.
- `controller.py`: desired-state convergence, retry lifecycle, delayed
  reconciliation, and state interpretation.
- `cli.py`: `pair`, `status`, `inputs`, `apply`, `run`, the `playback`
  command group (`check`, `command`, `start`, `run`), and the installation
  commands (`upgrade`, `rollback`, `versions`). The installation commands are
  dispatched before `load_config` and must stay that way: an upgrade has to
  work on a host whose `config.yaml` is missing or broken.
- `updater.py`: release resolution, checksum verification, versioned venv
  builds, atomic activation/rollback, pruning, and the update lock. Network
  access and subprocess execution are injected (`opener`, `runner`) so tests
  never touch GitHub or build a real venv.
- `scripts/install.sh`: first-time appliance install. Creates the same layout
  `updater.py` maintains afterwards.
- `packaging/*.service`, `packaging/*.timer`: systemd user units, templated
  with `@INSTALL_ROOT@`, `@CONFIG_DIR@`, and `@STATE_DIR@` by the installer.
- `runtime_lock.py`: prevents concurrent processes sharing one lock name in
  one state directory. TV `run` and `playback run` use separate lock names
  (`controller.lock` / `playback.lock`) so they can run concurrently, since
  they are independent runtimes by design.
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
- `__version__` in `src/signage_controller/__init__.py` is the single source of
  truth for the version. `pyproject.toml` reads it dynamically. Do not
  reintroduce a hardcoded `version =` in `pyproject.toml`, and do not let a
  release tag disagree with it — `upgrade` rejects a build whose reported
  version does not match the release.

## State And Security

- Client keys are credentials. Never hardcode, commit, print, or log them.
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
- Do not run `scripts/install.sh` without an explicit request: it requires
  root, creates a system account, and writes outside the repository.
  `signage-controller upgrade` mutates a real installation and restarts units,
  so treat it the same way. `upgrade --check` and `versions` are read-only.

## Documentation Expectations

- Update `README.md` and `config.example.yaml` when configuration keys, CLI
  behavior, timing, logging, or commissioning steps change — this applies to
  `playback:` keys and playback CLI commands exactly as it does to `tvs:`
  keys and TV commands.
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
