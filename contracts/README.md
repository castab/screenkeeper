# Screenkeeper API Contracts

`openapi.yaml` is the source of truth for communication between the two
independently deployable halves of this monorepo:

- **Screenkeeper Edge** — the Python signage appliance at the repository root
  (`src/signage_controller/`). It is the *client*.
- **Screenkeeper Control** — the control-plane application (`control/`, added by
  a later task). It is the *server*.

Neither side imports the other. The edge does not generate a client from the
server's code, and the server does not import the edge's modules. Both implement
this document. That is what lets the two ship on their own schedules even though
they live in one repository.

## API prefix

Every operation lives under an explicit version prefix:

```text
/api/v1/
```

The base URL is operator-configured on the appliance
(`control_plane.base_url` in `config.yaml`) and the prefix is appended by the
client, so an operator configures `https://screenkeeper.example.com`, not
`https://screenkeeper.example.com/api/v1`.

## Operations

| Operation | Method and path | Auth |
| --- | --- | --- |
| Request an enrollment | `POST /api/v1/enrollments` | none (carries the device token in the body) |
| Poll an enrollment | `GET /api/v1/enrollments/{enrollment_id}` | `Authorization: Bearer <device_token>` |
| Report observed state | `POST /api/v1/players/{player_id}/heartbeat` | `Authorization: Bearer <device_token>` |
| Fetch desired content | `GET /api/v1/players/{player_id}/content` | `Authorization: Bearer <device_token>` |
| Report content status | `PUT /api/v1/players/{player_id}/content-status` | `Authorization: Bearer <device_token>` |
| Create/inspect assets and revisions | `/api/v1/admin/assets...` | `Authorization: Bearer <admin_token>` |
| Assign/clear player content | `/api/v1/admin/players/{id}/content/{local_id}` | `Authorization: Bearer <admin_token>` |

All device operations are initiated by the appliance. **The control plane
never calls the appliance.** There is no callback URL, no inbound port, and no webhook: a
Screenkeeper host requires no inbound Internet connectivity at all.

## Compatibility

Within `/api/v1`:

- Clients **must** tolerate unknown response fields. The edge parses only the
  fields it knows and ignores the rest.
- The server **should** tolerate unknown request fields where reasonable.
- Evolution is **additive**. New optional fields are fine; removing a field,
  renaming one, or narrowing its type is not.
- Edge and control releases **do not** need matching versions. The monorepo
  gives us coordinated development, not lockstep deployment.

A breaking change means a new prefix (`/api/v2`), served alongside `/api/v1`
until every appliance has moved.

Response schemas are therefore declared `additionalProperties: true`, which
encodes the rule above directly in the contract rather than only in prose.

## Credentials

The appliance generates its own identity and never receives a permanent secret
from the server:

- `installation_id` — a UUIDv4. Public identity, safe to display and log.
- `device_token` — 256 bits of cryptographic randomness. A credential.

The token is sent in the enrollment request body and thereafter as a bearer
token. The server stores only a hash of it and **must never return it in any
response**. It does not appear as a response field anywhere in `openapi.yaml`.

Pairing codes (`Q7KM-4HF2`) are short-lived, human-facing identifiers for the
claim step — not credentials. An expired code is replaced by requesting a new
enrollment with the *same* device identity.

## Location

The appliance never infers where it is. It does not report public IP, GPS,
Wi-Fi SSID, timezone, or network topology for the purpose of locating itself.
The control plane assigns the player to an organization and location when an
administrator claims it, and tells the appliance the result:

```text
Organization
    └── Location
          └── Player
                ├── physical connectors     (observed)
                ├── configured TVs          (operator-configured)
                └── configured playback     (operator-configured)
```

## Observed vs. configured

The heartbeat deliberately reports two different things side by side, and the
distinction matters:

```text
OBSERVED     inventory.connectors[]      DP-1 is connected, its EDID hashes to abc123
CONFIGURED   configured_bindings         dev-menu is configured for DP-1 and dev-tv
```

The first is what the Linux kernel sees through `/sys/class/drm`. The second is
what an operator wrote in `config.yaml`. Keeping them separate lets the control
plane compare them later — to notice that a display was swapped, or that a
player is bound to a connector that no longer exists. Merging them would throw
that away.

## Examples

`examples/` holds one document per message shape. They are exercised by the
edge test suite (`tests/test_contracts.py`) so they cannot drift from the
implementation:

| File | Message |
| --- | --- |
| `enrollment-request.json` | `POST /api/v1/enrollments` request body |
| `enrollment-created.json` | `POST /api/v1/enrollments` 201 response |
| `enrollment-claimed.json` | `GET /api/v1/enrollments/{id}` response after a claim |
| `heartbeat.json` | `POST /api/v1/players/{id}/heartbeat` request body |
| `content-manifest.json` | `GET /api/v1/players/{id}/content` response |
| `content-status.json` | `PUT /api/v1/players/{id}/content-status` request body |

The pending-poll response is small enough to live inline in `openapi.yaml`:
`{"status": "pending"}`.

Content manifests identify immutable revisions by ID, revision number, size,
MIME type, and lowercase SHA-256. Their signed `download_url` is deliberately
short-lived: the edge ignores unknown fields and persists only the sanitized
metadata, never the URL or its query string. Content status is a latest
snapshot separate from the heartbeat, not an event stream.
