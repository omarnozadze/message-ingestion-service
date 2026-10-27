# Project Audit and Cleanup

## Confirmed root causes

1. Agent `202` was absent from the linked self-test phone's `AGENT_MULTI_SENDER_MAP`; backend preflight correctly returned `authorized=false`.
2. Earlier bridge-only authorization bypass patches were conceptually wrong: they bypassed the first check but did not give the backend a valid agent context.
3. Standard Windows `fastapi dev` used the Proactor event loop, while async Psycopg requires a Selector event loop.
4. The old root `install.ps1` was a historical patch installer, not a project/environment installer.
5. FastAPI and bridge defaults disagreed on ports (`8000` vs `8011`).
6. Detached messages could remain non-terminal after restart or an unbounded workflow.
7. A WhatsApp reply could use a socket captured before reconnect and fail with HTTP-like status 428 `Connection Closed`.

## Source fixes

- Added safe `.env` sender-map migration utility and self-test wrapper.
- Added lazy SQLAlchemy engine/session construction.
- Added workflow timeout and terminal status persistence.
- Expanded restart recovery to all non-terminal processing states.
- Added canonical Windows API launcher.
- Standardized bridge/API URL and polling windows.
- Added latest-socket reply retry after WhatsApp reconnect.
- Added/updated regression tests and operational documentation.

## Removed from the clean distribution

- historical patch folders `v4` through `v17-fixed`;
- `.patch-backups` and old backup trees;
- duplicate `app/app`, `app/tests`, `app/scripts`, and stale `app/pyproject.toml` copies;
- root one-off scripts that directly read/write production 1C data;
- `*.log`, `.pytest_cache`, `__pycache__`, and compiled bytecode;
- `.env` and any environment backups;
- `.venv`, `.venv-approved`, bridge `node_modules`, and WhatsApp `auth` state.

The external update script preserves the target machine's `.env` and WhatsApp `auth` folder, keeps the entire old project as a timestamped backup, and rebuilds disposable environments.

## Validation

- Python compilation: passed.
- Unit tests in mock/offline mode: 120 passed after final cleanup.
- WhatsApp JavaScript syntax check: passed.
- Configuration migration is idempotent and preserves existing sender codes.
- Secret scan is run on the final ZIP; local `.env` and WhatsApp credentials are excluded.
