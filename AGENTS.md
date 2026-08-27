# AGENTS.md

## Project Purpose

`signage-controller` is a small Linux appliance component for local LG webOS
television control. Phase 1 converges each configured TV to a desired HDMI
input and absolute volume. It is configuration-driven: one `run` process owns
one asynchronous manager per configured TV.

Keep the project deliberately boring and reliable. Prefer standard-library
Python, asyncio, explicit YAML configuration, local network control, and small
testable abstractions.

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
- `cli.py`: `pair`, `status`, `inputs`, `apply`, and `run` commands.
- `runtime_lock.py`: prevents concurrent `run` processes sharing one state
  directory.

Keep `aiowebostv` types and protocol response shapes inside `tv/lg_webos.py`.
Before changing adapter calls, inspect the installed pinned library API rather
than guessing its signatures or response fields.

## Behavioral Invariants

- Use only local webOS control. Do not add LG ThinQ, cloud APIs, CEC,
  Wake-on-LAN, IR, smart plugs, or automated power scheduling.
- Do not add mpv, physical-display, systemd, or multi-display deployment work
  unless the user explicitly starts a later phase.
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

## State And Security

- Client keys are credentials. Never hardcode, commit, print, or log them.
- Default runtime state is under `$XDG_STATE_HOME/signage-controller/` or
  `~/.local/state/signage-controller/`; production can use
  `/var/lib/signage-controller/` through `--state-dir`.
- Preserve `0700` state-directory and `0600` state-file permissions where the
  host supports them.
- Do not run `pair`, `apply`, or `run` against a real TV unless the user has
  explicitly requested that external action. `inputs` and `status` can also
  create a pairing state on an unpaired TV, so treat them as real-device
  operations too.

## Documentation Expectations

- Update `README.md` and `config.example.yaml` when configuration keys, CLI
  behavior, timing, logging, or commissioning steps change.
- Keep the README one-TV walkthrough accurate and executable from a clean
  checkout.
- Document actual command IDs as TV-specific values discovered through
  `signage-controller inputs <tv-id>`; never assume a configured HDMI label is
  universally valid.
