# Admin Run-Delete Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only `DELETE /api/runs` endpoint that removes `AgentRun` rows (and their `TranscriptStore` transcripts) by id, so cleaning up mistaken/test ingests never requires direct production database access again.

**Architecture:** A single new route in `backend/api/routes.py`, gated by the existing `require_admin` dependency (the same one already used by `/api/accounts`'s DELETE/PATCH routes). Deleting a run also cascades to any child subagent runs (`parent_id` pointing at it) and each deleted run's matching `TranscriptStore` row, capped at 100 ids per request, with a server-side log line recording who deleted what.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest.

## Global Constraints

- Endpoint: `DELETE /api/runs`, body `{"ids": ["<run-id>", ...]}` — bulk, not single-id-per-call.
- Gated by `require_admin` (from `backend/auth.py`, already imported in `backend/api/routes.py`) —
  identical auth behavior to `/api/accounts` DELETE/PATCH: 401 unauthenticated, 403 non-admin.
- Cascade: deleting a run also deletes any `AgentRun` rows with `parent_id` equal to the deleted
  run's id (one level only — subagents don't have their own subagents in this schema), plus the
  `TranscriptStore` row for every deleted `AgentRun.id` (requested id and any cascaded children's
  ids) — confirmed `TranscriptStore.session_id` always equals `AgentRun.id` for all three provider
  adapters.
- Batch cap: reject (422) if `ids` has more than 100 entries. Fixed constant, not configurable.
- Exact-id-only: no filter/date-range/provider-based deletion — every id is looked up individually.
- Response: `{"deleted": [...ids actually removed, including cascaded children...], "not_found":
  [...requested ids that didn't exist...]}`.
- Audit log line at `INFO` level recording the admin's username and the ids they deleted.
- Hard delete, no soft-delete/undo, no persistent audit table, no frontend UI — matches the design
  spec's explicit out-of-scope list.

---

### Task 1: `DELETE /api/runs` endpoint

**Files:**
- Modify: `backend/api/routes.py` (add `import logging` + module logger near the top; add
  `MAX_DELETE_BATCH` constant; add the new route after `get_run`, before `/providers`)
- Test: `backend/api/test_run_delete.py` (new file)

**Interfaces:**
- Produces: `DELETE /api/runs` — no other task in this plan consumes it (this is the only task).
  Reuses `backend.auth.require_admin`, `backend.models.{AgentRun, TranscriptStore, User}`, and
  `backend.db.get_session` exactly as already imported in `backend/api/routes.py` — no new
  cross-file dependencies.

- [ ] **Step 1: Write the failing tests**

Create `backend/api/test_run_delete.py`. This follows `backend/api/test_auth_routes.py`'s exact
pattern (`test_client` fixture from `backend/conftest.py`, `_seed_user` helper, direct
`db_module.engine` access for setup/assertions):

```python
import logging

from sqlmodel import Session

import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, TranscriptStore, User


def _seed_user(username: str, password: str, is_admin: bool = False) -> None:
    with Session(db_module.engine) as session:
        session.add(User(username=username, password_hash=hash_password(password), is_admin=is_admin))
        session.commit()


def _seed_run(run_id: str, *, parent_id: str | None = None, with_transcript: bool = True) -> None:
    with Session(db_module.engine) as session:
        session.add(AgentRun(id=run_id, provider="gemini", model="gemini-3.5-flash", parent_id=parent_id))
        if with_transcript:
            session.add(TranscriptStore(session_id=run_id, content="{}"))
        session.commit()


def _login_admin(test_client) -> None:
    _seed_user("gabby", "hunter2", is_admin=True)
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})


def test_delete_runs_removes_run_and_transcript(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 200
    assert res.json() == {"deleted": ["run1"], "not_found": []}
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is None
        assert session.get(TranscriptStore, "run1") is None


def test_delete_runs_cascades_to_children(test_client):
    _login_admin(test_client)
    _seed_run("parent1")
    _seed_run("child1", parent_id="parent1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["parent1"]})

    assert res.status_code == 200
    assert set(res.json()["deleted"]) == {"parent1", "child1"}
    assert res.json()["not_found"] == []
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "parent1") is None
        assert session.get(AgentRun, "child1") is None
        assert session.get(TranscriptStore, "child1") is None


def test_delete_runs_reports_not_found_for_missing_ids(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1", "nonexistent"]})

    assert res.status_code == 200
    assert res.json() == {"deleted": ["run1"], "not_found": ["nonexistent"]}


def test_delete_runs_rejects_batch_over_cap(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": [f"id-{i}" for i in range(101)]})

    assert res.status_code == 422
    with Session(db_module.engine) as session:
        # Nothing deleted — the cap check must happen before any deletion.
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_requires_admin(test_client):
    _seed_user("gabby", "hunter2", is_admin=True)
    _seed_user("bob", "x", is_admin=False)
    test_client.post("/api/login", json={"username": "bob", "password": "x"})
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 403
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_requires_authentication(test_client):
    # Once any account exists, the auth middleware blocks all unauthenticated
    # /api/* requests with 401 before the route's own admin check ever runs
    # (same behavior as test_create_account_after_bootstrap_requires_admin
    # in test_auth_routes.py).
    _seed_user("gabby", "hunter2", is_admin=True)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 401
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_logs_admin_username_and_ids(test_client, caplog):
    _login_admin(test_client)
    _seed_run("run1")

    with caplog.at_level(logging.INFO, logger="backend.api.routes"):
        test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert any("gabby" in r.message and "run1" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/api/test_run_delete.py -v` (from repo root)
Expected: every test fails with `404 Not Found` (the route doesn't exist yet) or a collection error
— confirms there's no accidental pre-existing route with this path.

- [ ] **Step 3: Add the logger and batch-size constant**

Replace `backend/api/routes.py`'s top imports:

```python
import gzip
import json
import asyncio
from datetime import datetime, timedelta
```

with:

```python
import gzip
import json
import asyncio
import logging
from datetime import datetime, timedelta
```

Add the logger definition right after the existing `PROVIDER_ADAPTERS`/`_select_parser` block, next
to the other module-level constants (`MAX_COMPRESSED_BYTES`/`MAX_INGEST_BYTES`). Replace:

```python
MAX_COMPRESSED_BYTES = 10 * 1024 * 1024   # 10 MB compressed
MAX_INGEST_BYTES = 100 * 1024 * 1024      # 100 MB decompressed

router = APIRouter()
```

with:

```python
MAX_COMPRESSED_BYTES = 10 * 1024 * 1024   # 10 MB compressed
MAX_INGEST_BYTES = 100 * 1024 * 1024      # 100 MB decompressed
MAX_DELETE_BATCH = 100                    # hard cap on ids per DELETE /runs call

logger = logging.getLogger(__name__)

router = APIRouter()
```

- [ ] **Step 4: Add the `DELETE /api/runs` route**

Insert this immediately after the existing `get_run` function (right before the `@router.get("/providers")` route):

```python
@router.delete("/runs")
def delete_runs(
    body: dict,
    current: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(status_code=422, detail="ids must be a list of strings")
    if len(ids) > MAX_DELETE_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot delete more than {MAX_DELETE_BATCH} runs per request",
        )

    deleted: list[str] = []
    not_found: list[str] = []

    for run_id in ids:
        run = session.get(AgentRun, run_id)
        if not run:
            not_found.append(run_id)
            continue

        children = session.exec(select(AgentRun).where(AgentRun.parent_id == run_id)).all()
        for child in children:
            _delete_run_and_transcript(session, child)
            deleted.append(child.id)

        _delete_run_and_transcript(session, run)
        deleted.append(run.id)

    session.commit()
    logger.info(f"[admin] {current.username} deleted runs: {deleted}")
    return {"deleted": deleted, "not_found": not_found}


def _delete_run_and_transcript(session: Session, run: AgentRun) -> None:
    stored = session.get(TranscriptStore, run.id)
    if stored:
        session.delete(stored)
    session.delete(run)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/api/test_run_delete.py -v`
Expected: `8 passed`

- [ ] **Step 6: Run the full backend test suite**

Run: `.venv/bin/python -m pytest backend/ -v`
Expected: all tests pass (previous count plus these 8 new ones), no regressions in
`test_auth_routes.py`/`test_routes.py`/`test_scoping.py`.

- [ ] **Step 7: Confirm the backend still imports cleanly**

Run: `.venv/bin/python -c "import backend.main"`
Expected: no output, no error.

- [ ] **Step 8: Commit**

```bash
git add backend/api/routes.py backend/api/test_run_delete.py
git commit -m "feat: add admin-only DELETE /api/runs endpoint with cascade + batch cap"
```

---

## Self-Review

**Spec coverage:** Bulk `DELETE /api/runs` with `{"ids": [...]}` body → Step 4. `require_admin`
gating (401/403) → Step 4 + tests in Step 1 (`test_delete_runs_requires_admin`,
`test_delete_runs_requires_authentication`). One-level cascade to children + their transcripts →
`_delete_run_and_transcript` + `test_delete_runs_cascades_to_children`. 100-id batch cap → Step 4 +
`test_delete_runs_rejects_batch_over_cap`. Exact-id-only (no filters) → the route only ever accepts
a list of ids, no other query parameters. Transparent `deleted`/`not_found` reporting →
`test_delete_runs_reports_not_found_for_missing_ids`. Audit log line → Step 4 +
`test_delete_runs_logs_admin_username_and_ids`. All spec requirements are covered by this single
task; no gaps.

**Placeholder scan:** No TBD/TODO; every step shows complete code, exact diffs, or exact commands
with expected output.

**Type consistency:** `_delete_run_and_transcript(session: Session, run: AgentRun) -> None` is
defined once and used identically for both the requested run and its cascaded children — no
duplicate/divergent deletion logic. The route's dependencies (`current: User = Depends(require_admin)`,
`session: Session = Depends(get_session)`) match the exact parameter names and types already used by
every other admin-gated route in this file (`list_keys`, `create_key`, `delete_key`).
