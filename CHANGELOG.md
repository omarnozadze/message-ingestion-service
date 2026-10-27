# Changelog

Every change below is already applied to the source in `app/` and
`whatsapp-bridge/`. The old patch folders (`*-v24` … `*-v43`, `v34build`,
`payload`) and `.patch-backups` were removed during the 2026-10-19 cleanup;
this file preserves what each one did.

## 2026-10-19 — logic review and simplification

Bug fixes (each covered by a new regression test):
- **"დღევანდელიდან ამოიღე და ოთხშაბათს ჩასვი" was parsed as REMOVE from
  Wednesday.** "დღევანდელიდან" was not recognised as today, and remove + add
  with two weekdays is now a MOVE (source = the "-იდან" day). With three or
  more weekdays the parser refuses to guess (AI fallback / review).
- **v42 turned removals into SET.** "მხოლოდ" now upgrades only ADD to SET;
  "… მხოლოდ დღეს ამოიღე" stays a removal.
- **v42 split conversational messages on commas** ("გაუმარჯოს ომარ, …" became
  a client). The compact multi-client form now requires every part to be a
  short "name + branch code" phrase.
- **Exact-name matching (v40c) only accepted whole-query equality**, so
  "ნინო ბერიძე" lost to the alias "ნინო" and "გვირილა 0101" no longer flagged
  the conflict with code 0101. Names now match as whole phrases inside the
  client text; the v41 `შმს`/`შპს` and ` * ` rules are unchanged.
- **Branches of one chain sharing a name** ("ორი ნაბიჯი") went straight to
  review; outlet/address evidence (e.g. "ფრიდონ ხალვაშის 101გ") now gets to
  decide first.

Simplification:
- `odata.py` 2220 → ~1230 lines: removed the disabled direct-row fast path
  (v32–v37), the unused bulk reader (v29), the dormant plan auto-creation code
  (business rule: WhatsApp never creates plans) and dead helpers.
- The write workflow is now four explicit steps: DataVersion check → PATCH →
  single Post → verify. The redundant pre-Post verification round-trips are gone.
- v42 monkey-patch layers folded into `parse_visit_request` /
  `parse_visit_instruction` (no more duplicate function definitions).
- Each action is matched against the full index once (was twice); the result
  is reused for `MATCH_DIAGNOSTICS`.
- 14 tests that had silently drifted from v28–v41 behaviour were fixed:
  184 tests, all passing.

## 2026-10-19 — project cleanup
- Removed 21 applied patch folders, `.patch-backups`, root `payload/`, and the
  one-off `debug_*` / `fix_*` / root `test_*` scripts that read or wrote
  production 1C data directly.
- `install.ps1` is now a real environment installer (it used to be the v35
  patch installer, which `start-api.ps1 -> install.ps1 -SkipBridge` would have run by mistake).
- `run_api.py` honours `API_HOST` / `API_PORT` / `LOG_LEVEL` from `start-api.ps1`.
- WhatsApp bridge default API URL is `127.0.0.1:8011` (was `8000`); `start-whatsapp.ps1`
  polls every 1 s, as v28 intended.
- Background launchers no longer hard-code the project path.
- v41 / v42 / v43 self-tests moved into `tests/unit/` so `pytest` covers them.
- `check_database.py` moved to `scripts/check_database.py`.
- Env snippet files merged into `.env.example`; `.gitignore` expanded.

## v43 — WhatsApp command gate
Only visit-plan commands are forwarded to FastAPI; ordinary chat from
authorized senders is silently ignored and does not cancel a pending client choice.

## v42 — multi-client parser
Several clients can share one instruction (comma, `და`, or compact
name + branch-code list). `მხოლოდ <day> … ჩასვი` is a SET operation.

## v41 — stable 1C system
Exact identity ignores only `შმს`/`შპს` prefixes and the ` * ` address suffix.
`/choices` ranks with the agent's active-plan refs. Automatic Unpost removed;
posted documents are PATCHed and then Posted once. PATCH timeout 180 s
(360 s for > 300 rows), Post timeout 180 s (300 s for > 300 rows).

## v40b–v40e
- v40e: > 300-row plans get a 360 s parent-PATCH timeout.
- v40d: after a PATCH timeout, verify up to 12 × 3 s before giving up.
- v40c: restored `MatchingEngine.rank_for_review` accidentally lost in v40b.
- v40b: exact client identity when 1C appends address text to the name.

## v39 — force re-post
A PATCHed posted document is explicitly Posted again so 1C rebuilds its
posting side effects.

## v38 — read concurrency
Child-row PATCH turned out to be a no-op in this 1C publication and was
disabled. Full row snapshot loaded with bounded keyed GET concurrency (18).

## v32–v37 — direct-row fast path (later disabled by v38)
Targeted row read/PATCH with fallback to the parent-document workflow;
encoding-safe `[1C-TIMING]` output for Windows scheduled tasks (v36/v37).

## v29–v31 — 1C latency
Bulk paginated plan-row read (v29), shorter PATCH/Post/verify timeouts and
skipping redundant Post (v30), `[1C-TIMING]` instrumentation (v31).

## v24–v28
- v28: deterministic parse first, AI only as fallback; agent-plan subset searched first; 1 s bridge polling.
- v27: exact S/K/D branch codes before fuzzy; duplicate detection; sync reconciliation report.
- v26: WhatsApp candidate list with numeric reply; `MATCH_DIAGNOSTICS` audit event.
- v25: `N278`/`#278`/`278` equivalent; `S278`/`K278`/`D278` distinct.
- v24: excluded client chains, candidate cache, AI circuit breaker, advisory lock for sync.

## clean-v20 (2026-10-07)
Baseline clean distribution: Selector event loop launcher, workflow timeout,
restart recovery, sender-map migration utility, 120 tests.
