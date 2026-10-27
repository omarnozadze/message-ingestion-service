# Backend Matching Architecture

This document describes the active backend design (build v43 + 2026-10-19 refactor) from the current source code and regression tests. It is the authoritative description shipped with this cleaned project.

## 1. Ingestion and identity boundary

Each request enters as an `IncomingMessageCreate` with:

- source and external message ID;
- sender ID/name;
- raw message text.

The WhatsApp bridge first calls `/api/v1/incoming-message/preflight`. The backend resolves the sender to an agent before the bridge creates a database message or replies to the sender.

Routing rules:

1. A fixed `AGENT_SENDER_MAP` entry wins.
2. An `AGENT_MULTI_SENDER_MAP` entry requires a leading allowed agent code.
3. An unknown WhatsApp/Viber sender cannot impersonate an agent by typing a code.
4. Prefix-only routing remains available only when sender metadata is absent, for manual/API compatibility.
5. A fixed sender that types a different **known** agent code is rejected as a conflict.

For the linked self-test phone, the correct design is a multi-agent mapping. Authorization must not be bypassed in JavaScript.

## 2. Request parsing

After agent resolution, the leading agent code is removed from the client text. The request parser derives one or more client actions and visit operations:

- add to one or more days;
- remove from a day;
- move from one day to another;
- remove from the whole plan.

The deterministic Georgian parser is the baseline. Optional OpenAI-assisted parsing/matching is guarded by confidence thresholds and converts uncertainty to `needs_review` rather than guessing.

## 3. Client identity extraction

Operation words, weekday words, and narrow connector tokens are removed before matching. Human outlet identifiers normalize equivalent forms such as:

- `N0158`, `0158`, and `158`;
- punctuation/case/Unicode variants;
- configured human aliases;
- typed address aliases, kept separate from outlet-number aliases.

## 4. Explainable matching order

The matching engine uses this priority:

1. complete 1C catalog code;
2. exact human alias;
3. outlet number plus name;
4. address evidence;
5. exact client name;
6. fuzzy human name/alias;
7. short 1C-code suffix only as a last-resort compatibility fallback.

Active agent-plan membership is a preference, not a hard filter. A legitimate new client may be absent from the current plan and still must be matchable so that the writer can append it.

Every match stores method, score, and evidence. Ambiguous candidates go to `needs_review` instead of silently choosing one.

## 5. 1C write boundary

Before a plan mutation, the service sets `sync_pending` and commits the audit trail. The OData writer:

- resolves the agent plan (never creates one; falls back to the latest posted plan);
- loads every plan row by key (18 concurrent GETs; child-row PATCH is a no-op in this publication, so it is not used);
- applies all requested row changes in memory;
- re-checks `DataVersion`, PATCHes the parent document with `If-Match`
  (180 s, or 360 s for plans over 300 rows; on timeout it polls for the result);
- calls `Post` exactly once for posted documents — never skipped, never repeated after a timeout, never `Unpost`;
- verifies the requested rows and the Posted flag.

Two in-process lock layers protect writes:

- per-agent lock, preventing concurrent writes to the same plan;
- global plan-write lock, serializing large parent-document mutations because 1C became unstable under parallel writes.

The service must therefore run with one Uvicorn worker. These locks are not distributed locks.

## 6. Detached processing and idempotency

`POST /api/v1/incoming-message` returns HTTP 202 and runs the long workflow in a detached task. The bridge polls by `(source, external_message_id)`.

Safety mechanisms:

- database unique constraint for external-message idempotency;
- in-process inflight set to avoid duplicate task starts;
- configurable hard timeout;
- terminal failure persistence after unexpected exceptions;
- startup recovery of non-terminal rows after process restart.

A timeout/restart becomes `needs_review`, not an automatic retry, because the client may have lost confirmation after 1C committed.

## 7. Message state machine

```text
received
   ├─> needs_review       (sender/parser/match ambiguity)
   ├─> matched
   └─> sync_pending
          ├─> synced      (verified success)
          ├─> needs_review (uncertain outcome/review condition)
          └─> failed      (definite failure)
```

Terminal states are `synced`, `needs_review`, and `failed`.

## 8. Auditability

Important decisions are recorded in `audit_logs`, including:

- message received;
- agent resolution;
- parser or AI review conditions;
- matching context and alternatives;
- parsed visit instruction;
- 1C success/failure/review result;
- background timeout/cancellation/failure.

The audit log and explainable match evidence are the primary tools for diagnosing a request without replaying it against 1C.

## 9. Known deployment boundary

The current detached queue and 1C locks are process-local. For horizontal scaling or crash-safe automatic continuation, the next architecture step would be a durable queue plus a distributed lock/lease. Until then:

- run one API process;
- do not automatically resend uncertain operations;
- retain the PostgreSQL state and audit records;
- verify 1C before manual retry.
