# Message Ingestion Service → 1C

FastAPI backend that receives WhatsApp/Viber-style free-form Georgian messages, resolves the sales agent from sender metadata, matches the client, parses requested visit weekdays, updates the active `апПланАгента` document through standard 1C OData, posts the document, verifies the result, and stores an audit trail.

## Current working flow

```text
sender_id / sender_name
        ↓
agent 111 / 112 / ... → PS111 / PS112 / ...
        ↓
message text
        ↓
client code/name/alias/fuzzy match
        ↓
ADD / MOVE / SET + weekday(s) / today
        ↓
active Document_апПланАгента
        ↓
ПланПосещений parent-document PATCH
        ↓
Post → verify → synced
```

The 1C write path intentionally uses the parent document because the published tabular-row EntitySet returns HTTP 200 for row PATCH but does not persist that isolated row change in this database. The parent PATCH may take several minutes on large plans; the integration verifies the final state because 1C can finish server-side after the HTTP client times out.

## Message examples

These are supported without an agent code in the text when `sender_id` or `sender_name` resolves the agent:

```text
გვირილა 0223 ჩასვი სამშაბათი ხუთშაბათზე
#452 ორი ნაბიჯი ჩამისვი დღევანდელზე
#223 ნიკორა ჩასვი დღევანდელზე და პარასკევზე
გვირილა 0223 სამშაბათიდან ხუთშაბათზე გადაიტანე
```

Rules:

- `ჩასვი`, `ჩამისვი`, `დაამატე` → ADD: requested days are enabled; existing days remain.
- `გადაიტანე` → MOVE: if source day is named, only that source day is removed; without a source day, D1..D7 are replaced by the requested target day(s).
- `დღეს` / `დღევანდელზე` → current weekday in `BUSINESS_TIMEZONE`.
- Multiple requested days are supported.
- Human short codes such as `#223`, `0223`, `8947` can match the numeric suffix of a 1C code such as `00-00000223`. Ambiguous suffixes require review unless an exact name/alias disambiguates them.

## Agent resolution

Agent resolution priority:

1. configured fixed `sender_id` mapping;
2. configured multi-agent sender rule;
3. configured `sender_name` mapping;
4. a standalone 3-digit code in sender name, e.g. `111` or `PS111`;
5. optional legacy leading code only for API/manual requests that do not have sender metadata.

Current sender configuration:

```text
+995555000117 → 117 → PS117
+995555000115 → 115 → PS115
+995555000111 → 111 → PS111
+995555000112 → 112 → PS112
+995555000103 → 103 → PS103
+995555000402 → 402 → PS402
+995555000403 → 403 → PS403
+995555000110 → 110 → PS110

+995555000504 → 403 OR 504
```

For the shared `+995555000504` sender, the message must explicitly start with `403` or `504`:

```text
504 #223 ნიკორა ჩასვი პარასკევზე
403 გვირილა 0223 ჩასვი სამშაბათზე
```

If that sender omits the agent code, or writes a code other than `403`/`504`, the message goes to `needs_review`. Fixed senders also go to review if they explicitly write a conflicting agent code.

Configure the rules in `.env`:

```env
AGENT_SENDER_MAP={"id:+995555000117":"117","id:+995555000115":"115","id:+995555000111":"111","id:+995555000112":"112","id:+995555000103":"103","id:+995555000402":"402","id:+995555000403":"403","id:+995555000110":"110"}
AGENT_MULTI_SENDER_MAP={"id:+995555000504":["403","504"]}
```

Phone IDs are normalized, so spaces and `+` do not matter; Georgian local mobile forms such as `555000117` are normalized to the same international identity. The corresponding 1C exchange-plan description is built as `PS` + code.

Unknown WhatsApp/Viber sender IDs are not allowed to choose an arbitrary agent by simply typing `111`, `112`, etc. This prevents an unconfigured sender from writing into another agent's plan.

## Quick start (Windows)

Requirements: Python 3.12+, `uv`, PostgreSQL (Docker or local), Node.js 20+ for the WhatsApp bridge.

```powershell
.\install.ps1                 # .env from template, uv sync, alembic upgrade, npm ci
# edit .env: DATABASE_URL, ONE_C_*, OPENAI_API_KEY, sender maps
uv run python -m scripts.check_database
uv run python -m scripts.sync_one_c_clients
.\start-api.ps1               # http://127.0.0.1:8011/docs
.\start-whatsapp.ps1          # second window; scan the QR code on first run
```

Always start the API with `start-api.ps1` / `run_api.py`, not `fastapi dev`:
on Windows only the Selector event loop works with async Psycopg, and the
service must run as **one** process (1C write locks are in-process).

Example API test with sender name resolving PS111:

```json
{
  "source": "api",
  "sender_name": "111",
  "external_message_id": "demo-001",
  "text": "#223 ნიკორა ჩასვი დღევანდელზე და პარასკევზე"
}
```

For a real WhatsApp adapter, populate `sender_id` with the provider's stable sender identifier/phone number and `sender_name` with the profile/display name when available.

## Client sync

Run a full client sync from `Catalog_Партнеры`:

```powershell
uv run python -m scripts.sync_one_c_clients
```

The OData query encoding uses `%20` rather than `+`, because this 1C publication is sensitive to query-string encoding.

## Safety / review behavior

The service does not guess when strong signals conflict. It returns `needs_review` for cases such as:

- unknown sender/agent;
- ambiguous short client code;
- code points to one client while exact name points to another;
- client has no 1C Partner Ref_Key;
- multiple active plans are indistinguishable;
- 1C write/post result cannot be verified.

## Tests

```powershell
uv run pytest -q
```

## Production (Windows scheduled tasks)

| Task | Script | Log |
|---|---|---|
| `MessageIngestionService-API` | `start_api_background.ps1` (uses `.venv-approved`) | `logs\api-*.log` |
| `MessageIngestionService-WhatsAppBridge` | `start_whatsapp_bridge_background.ps1` | `logs\whatsapp-bridge-*.log` |

```powershell
Stop-ScheduledTask  -TaskName MessageIngestionService-WhatsAppBridge
Stop-ScheduledTask  -TaskName MessageIngestionService-API
Start-ScheduledTask -TaskName MessageIngestionService-API
Start-ScheduledTask -TaskName MessageIngestionService-WhatsAppBridge
Invoke-RestMethod http://127.0.0.1:8011/health
.\verify-selftest.ps1                       # sender/agent preflight check
```

1C timing diagnostics: `Get-Content <latest api log> -Tail 1000 | Select-String "\[1C-TIMING\]"`.

## Project layout

```text
app/
  api/routes/          HTTP endpoints (incoming messages, health, WhatsApp webhook)
  core/                settings, enums, exceptions
  db/                  SQLAlchemy engine/session
  integrations/one_c/  1C OData client (+ mock)
  integrations/whatsapp/
  models/ repositories/ schemas/
  services/            agent resolution, parser, matching engine, AI, 1C sync
alembic/               database migrations
scripts/               sync, seed, sender config, DB check
tests/unit/            pytest suite (offline; no 1C or network needed)
whatsapp-bridge/       Node.js Baileys bridge (src/index.js)
run_api.py             canonical API launcher
install.ps1            environment installer
start-*.ps1            interactive / background launchers
CHANGELOG.md           history of the v24–v43 patches
```

## Meta WhatsApp Cloud API webhook

The service now exposes a Meta-compatible webhook at:

- `GET /webhooks/whatsapp` — webhook verification challenge.
- `POST /webhooks/whatsapp` — inbound WhatsApp events.

Only inbound text messages are processed for now. Delivery/read statuses and
non-text messages are acknowledged and ignored. The endpoint immediately
returns HTTP 200 and processes the message in a FastAPI background task, so a
slow 1C write does not keep the Meta webhook request open.

Required `.env` values:

```env
WHATSAPP_VERIFY_TOKEN=replace-with-a-long-random-token
WHATSAPP_APP_SECRET=
```

`WHATSAPP_APP_SECRET` enables `X-Hub-Signature-256` verification and should be
set before exposing the webhook publicly.

A real Meta inbound text event is converted to the existing internal payload:

```json
{
  "source": "whatsapp",
  "external_message_id": "wamid....",
  "sender_id": "995555000111",
  "sender_name": "111",
  "text": "#223 ნიკორა ჩასვი პარასკევზე"
}
```

The existing sender routing, client matching, visit instruction parser and 1C
OData synchronization then run unchanged. WhatsApp message IDs are used for
idempotency, so webhook retries do not create duplicate incoming messages.

For this prototype the slow work uses FastAPI `BackgroundTasks`. A durable job
queue should replace it before a production deployment where process restarts
must not lose already-acknowledged work.
