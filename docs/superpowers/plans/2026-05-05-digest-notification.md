# Digest-Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire one macOS notification per successfully finalized non-empty digest, opening the digest file on click — implementing spec §4.4 step 10 and §4.5.

**Architecture:** Add a new `Notifier.notify_digest_ready(...)` method (separate from item-push `maybe_notify`, no thresholds/quiet-hours) and inject an optional `Notifier` into `generate_digest()`. Both CLI call-sites (`digest` subcommand + `_maybe_run_digest_catchup`) construct a `Notifier()` and pass it through, so both paths notify uniformly. Empty slots stay silent because the empty-branch returns before the notify call; idempotent skips and race-losers also return before reaching it.

**Tech Stack:** Python 3.12, `pync` (existing dep), `pytest` with `asyncio_mode=auto`, `unittest.mock.AsyncMock`, `freezegun`. Ruff line-length 100. Project uses `uv` — no manual dep changes needed.

**Spec Reference:** `docs/superpowers/specs/2026-05-05-digest-notification-design.md`

---

## File Map

| Path | Type | Responsibility |
|------|------|----------------|
| `src/newsroom/notifier.py` | Modify | New method `Notifier.notify_digest_ready(...)`; reuses existing `_send` injection point |
| `src/newsroom/digester.py` | Modify | New optional `notifier` parameter on `generate_digest`; one new call after `finalize_digest` in the items branch |
| `src/newsroom/cli.py` | Modify | Two call-sites: pass `notifier=Notifier()` into `generate_digest(...)` |
| `tests/test_notifier.py` | Modify | 3 new tests for `notify_digest_ready` |
| `tests/test_digester.py` | Modify | 5 new tests for digester+notifier integration |
| `CLAUDE.md` | Modify | Add second bullet to "Known Architecture Deferrals" documenting digester→notifier direct call |

All other invariants in `CLAUDE.md` ("idempotency is sacred", "Europe/Berlin for slot determination") remain untouched.

---

## Task 1: Add `notify_digest_ready` method to Notifier

**Files:**
- Modify: `src/newsroom/notifier.py` (extend `Notifier` class)
- Test: `tests/test_notifier.py` (extend with 3 new tests)

- [ ] **Step 1.1: Write the failing test for morning format**

Append to `tests/test_notifier.py` (at end of file, after `test_notifier_pushes_again_after_15min_window`):

```python
# ── digest-ready notification (spec §4.4 step 10) ─────────────────────


async def test_notify_digest_ready_morning_format() -> None:
    """Morning slot produces 'Morgen-Digest bereit (N Items)' message."""
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    await notifier.notify_digest_ready(
        slot="morning",
        item_count=20,
        file_path="/tmp/2026-04-19.md",
    )
    send_mock.assert_awaited_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["title"] == "Newsroom"
    assert kwargs["message"] == "Morgen-Digest bereit (20 Items)"
    assert kwargs["url"] == "/tmp/2026-04-19.md"
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `uv run pytest tests/test_notifier.py::test_notify_digest_ready_morning_format -v`
Expected: FAIL with `AttributeError: 'Notifier' object has no attribute 'notify_digest_ready'`

- [ ] **Step 1.3: Implement `notify_digest_ready` method**

In `src/newsroom/notifier.py`, append this method to the `Notifier` class (after `maybe_notify`, keep class indentation):

```python
    async def notify_digest_ready(
        self,
        *,
        slot: str,
        item_count: int,
        file_path: object,
    ) -> None:
        """System-Notification bei fertigem Digest.

        Anders als `maybe_notify`: keine Threshold-, Quiet-Hour- oder Bundling-Logik.
        Digests sind System-Events, keine Item-Pushes — selten genug (max 2/Tag),
        und ein gewünschtes Resultat-Signal.
        """
        label = "Morgen-Digest" if slot == "morning" else "Abend-Digest"
        title = "Newsroom"
        message = f"{label} bereit ({item_count} Items)"
        try:
            await self._send(title=title, message=message, url=str(file_path))
        except Exception as e:  # noqa: BLE001 — best-effort; never abort the digest
            logger.warning("digest notification send failed: %s", e)
```

Note: `file_path: object` keeps the type hint permissive for both `Path` and `str` callers without importing `Path` here. The `str(file_path)` cast handles both.

- [ ] **Step 1.4: Run morning test to verify it passes**

Run: `uv run pytest tests/test_notifier.py::test_notify_digest_ready_morning_format -v`
Expected: PASS

- [ ] **Step 1.5: Write the failing test for evening format**

Append to `tests/test_notifier.py` after the morning test:

```python
async def test_notify_digest_ready_evening_format() -> None:
    """Evening slot produces 'Abend-Digest bereit (N Items)' message."""
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    await notifier.notify_digest_ready(
        slot="evening",
        item_count=14,
        file_path="/tmp/2026-04-19.md",
    )
    send_mock.assert_awaited_once()
    assert send_mock.call_args.kwargs["message"] == "Abend-Digest bereit (14 Items)"
```

- [ ] **Step 1.6: Run evening test to verify it passes**

Run: `uv run pytest tests/test_notifier.py::test_notify_digest_ready_evening_format -v`
Expected: PASS (the slot=="morning" else branch covers this).

- [ ] **Step 1.7: Write the failing test for swallowed send-failure**

Append to `tests/test_notifier.py`:

```python
async def test_notify_digest_ready_send_failure_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the send_fn raises, notify_digest_ready logs a warning and returns normally."""
    import logging

    async def raising_send(**_kwargs: object) -> None:
        raise RuntimeError("pync down")

    notifier = Notifier(send_fn=raising_send)
    with caplog.at_level(logging.WARNING, logger="newsroom.notifier"):
        await notifier.notify_digest_ready(
            slot="morning",
            item_count=1,
            file_path="/tmp/x.md",
        )
    assert any("digest notification send failed" in rec.message for rec in caplog.records)
```

Add `import pytest` at the top of `tests/test_notifier.py` if it's not already imported (current file uses `from freezegun import freeze_time` and `from unittest.mock import AsyncMock` — `pytest` is not yet imported).

- [ ] **Step 1.8: Run failure-swallowed test to verify it passes**

Run: `uv run pytest tests/test_notifier.py::test_notify_digest_ready_send_failure_swallowed -v`
Expected: PASS (the `try/except` in the implementation handles it).

- [ ] **Step 1.9: Run the full notifier test file to confirm no regressions**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: All previously-passing tests still pass; 3 new tests pass.

- [ ] **Step 1.10: Format and lint**

Run: `uv run ruff format src/newsroom/notifier.py tests/test_notifier.py`
Run: `uv run ruff check src/newsroom/notifier.py tests/test_notifier.py`
Expected: No errors. Fix any reported issues.

- [ ] **Step 1.11: Commit**

```bash
git add src/newsroom/notifier.py tests/test_notifier.py
git commit -m "$(cat <<'EOF'
feat(notifier): add notify_digest_ready for digest system-events

Introduces a separate notification path for digest-ready signals
(distinct from item-push maybe_notify): no threshold check, no
quiet-hours, no bundling — just a 'Morgen-Digest bereit (N Items)'
or 'Abend-Digest bereit (N Items)' message that opens the digest
file on click. Send failures are logged and swallowed so they
never break the digest pipeline.
EOF
)"
```

---

## Task 2: Wire notifier into `generate_digest()`

**Files:**
- Modify: `src/newsroom/digester.py` (signature + one call-site)
- Test: `tests/test_digester.py` (add 5 tests)

Test strategy: write all 5 tests first (4 will fail because `generate_digest` doesn't accept `notifier` yet, 1 will pass trivially since omitting an unknown param is fine — wait, all 5 will fail with `TypeError: unexpected keyword argument 'notifier'` until the param is added). Add the param + call once; all 5 should then pass.

- [ ] **Step 2.1: Write the failing test for notify-on-success**

Append to `tests/test_digester.py` (after `test_generate_digest_force_evening_replaces_previous_evening`):

```python
# ── digest-ready notification (spec §4.4 step 10) ─────────────────────


async def test_generate_digest_notifies_when_items_present(
    populated_state: State, tmp_path: Path
) -> None:
    """Successful non-empty digest fires exactly one notify_digest_ready call."""
    from newsroom.notifier import Notifier

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# News-Digest 19. April 2026 (Morgen)\n\nbody"
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    out = await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )

    notify_send.assert_awaited_once()
    kwargs = notify_send.call_args.kwargs
    assert kwargs["title"] == "Newsroom"
    assert "Morgen-Digest bereit (3 Items)" == kwargs["message"]
    assert kwargs["url"] == str(out)
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_notifies_when_items_present -v`
Expected: FAIL with `TypeError: generate_digest() got an unexpected keyword argument 'notifier'`.

- [ ] **Step 2.3: Write the failing test for silent-on-empty-slot**

Append to `tests/test_digester.py`:

```python
async def test_generate_digest_silent_on_empty_slot(
    state: State, tmp_path: Path
) -> None:
    """Empty digest (no scored items in window) does NOT notify."""
    from newsroom.notifier import Notifier

    mock_agent = AsyncMock()
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=state,  # empty fixture from conftest
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )

    notify_send.assert_not_awaited()
    # File-Placeholder is still written
    assert (tmp_path / "news" / "2026" / "04" / "2026-04-19.md").exists()
```

- [ ] **Step 2.4: Run test to verify it fails**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_silent_on_empty_slot -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'notifier'`.

- [ ] **Step 2.5: Write the failing test for no-notify-on-idempotent-skip**

Append to `tests/test_digester.py`:

```python
async def test_generate_digest_no_notify_on_idempotent_skip(
    populated_state: State, tmp_path: Path
) -> None:
    """Second call without --force returns early and does NOT notify a second time."""
    from newsroom.notifier import Notifier

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Body"
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    # First call: real digest, real notify
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1

    # Second call: idempotent skip — must NOT increment
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1  # unchanged
```

- [ ] **Step 2.6: Run test to verify it fails**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_no_notify_on_idempotent_skip -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'notifier'`.

- [ ] **Step 2.7: Write the failing test for no-notify-on-race-loser**

Append to `tests/test_digester.py`:

```python
async def test_generate_digest_no_notify_on_race_loser(
    populated_state: State,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When claim_digest_slot returns False (another process won the race),
    generate_digest must return early WITHOUT calling notify."""
    from newsroom.notifier import Notifier

    monkeypatch.setattr(populated_state, "claim_digest_slot", lambda **_kw: False)
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Should never be produced"
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    mock_agent.ask.assert_not_called()
    notify_send.assert_not_awaited()
```

Make sure `import pytest` is present at the top of `tests/test_digester.py` for the `pytest.MonkeyPatch` type. The file already imports pytest (`import pytest` on line 5), so no change needed.

- [ ] **Step 2.8: Run test to verify it fails**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_no_notify_on_race_loser -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'notifier'`.

- [ ] **Step 2.9: Write the failing test for force-regen-notifies-again**

Append to `tests/test_digester.py`:

```python
async def test_generate_digest_force_regen_notifies_again(
    populated_state: State, tmp_path: Path
) -> None:
    """--force regeneration fires another notify_digest_ready."""
    from newsroom.notifier import Notifier

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Body"
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
        force=True,
    )
    assert notify_send.await_count == 2  # noqa: PLR2004
```

- [ ] **Step 2.10: Run test to verify it fails**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_force_regen_notifies_again -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'notifier'`.

- [ ] **Step 2.11: Write the failing test for no-notifier-works**

Append to `tests/test_digester.py`:

```python
async def test_generate_digest_no_notifier_works(
    populated_state: State, tmp_path: Path
) -> None:
    """Omitting the notifier param (default None) is allowed and does not crash."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Body"

    out = await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    assert out.exists()
```

- [ ] **Step 2.12: Run test to verify it passes**

Run: `uv run pytest tests/test_digester.py::test_generate_digest_no_notifier_works -v`
Expected: PASS — this test mirrors the existing `test_generate_digest_writes_file` and proves we don't break the no-notifier callers.

- [ ] **Step 2.13: Implement the digester change**

Edit `src/newsroom/digester.py`:

**(a)** Update the signature of `generate_digest` (around line 103-112). Replace:

```python
async def generate_digest(
    *,
    state,  # noqa: ANN001
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
) -> Path:
```

with:

```python
async def generate_digest(
    *,
    state,  # noqa: ANN001
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
    notifier: "Notifier | None" = None,
) -> Path:
```

**(b)** Add a TYPE_CHECKING import block at the top of `src/newsroom/digester.py` so the forward-reference `"Notifier | None"` resolves for static checkers without a runtime import. Insert after the existing `from newsroom.agent_client import AgentClient` line (around line 12):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from newsroom.notifier import Notifier
```

**(c)** Add the notification call after `state.finalize_digest(...)` in the items branch (around line 185-190). Replace:

```python
    state.finalize_digest(
        date=date.isoformat(),
        slot=slot,
        file_path=str(target_file),
        item_count=len(items),
    )
    return target_file
```

with:

```python
    state.finalize_digest(
        date=date.isoformat(),
        slot=slot,
        file_path=str(target_file),
        item_count=len(items),
    )
    if notifier is not None:
        await notifier.notify_digest_ready(
            slot=slot,
            item_count=len(items),
            file_path=target_file,
        )
    return target_file
```

Note: the empty-slot branch (lines 146-162) keeps its early `return target_file` and never reaches the notify call — that's the silent-on-empty behaviour by construction, no extra `if`-guard needed.

- [ ] **Step 2.14: Run all 6 new digester tests**

Run: `uv run pytest tests/test_digester.py -v -k "notif or no_notifier_works or force_regen or idempotent_skip or silent_on_empty or race_loser"`
Expected: All 6 new tests PASS.

- [ ] **Step 2.15: Run the full digester test file to confirm no regressions**

Run: `uv run pytest tests/test_digester.py -v`
Expected: All previously-passing tests still pass.

- [ ] **Step 2.16: Format and lint**

Run: `uv run ruff format src/newsroom/digester.py tests/test_digester.py`
Run: `uv run ruff check src/newsroom/digester.py tests/test_digester.py`
Expected: No errors.

- [ ] **Step 2.17: Commit**

```bash
git add src/newsroom/digester.py tests/test_digester.py
git commit -m "$(cat <<'EOF'
feat(digester): notify on finalized non-empty digest

Adds optional `notifier` parameter to generate_digest. When the items
branch reaches finalize_digest successfully, calls notify_digest_ready
with slot/item_count/file_path. Empty slots, idempotent skips, and
race-losers all return before the notify call — no special-casing.
--force regenerations notify like fresh runs (consistent mental model).

Implements spec §4.4 step 10 / §4.5. CLI wire-up follows in next commit.
EOF
)"
```

---

## Task 3: Wire `Notifier()` into both CLI call-sites

**Files:**
- Modify: `src/newsroom/cli.py` (two call-sites: `digest()` ~line 222, `_maybe_run_digest_catchup()` ~line 177)

These changes are pure plumbing — they activate the notification path that Task 2 made available. No new tests at the CLI level: the digester-level tests in Task 2 cover the contract, and the CLI changes are 2-line edits where over-mocking would obscure rather than illuminate.

- [ ] **Step 3.1: Add `Notifier` to the import block in `cli.py`**

Open `src/newsroom/cli.py`. Find the existing import block (~line 23):

```python
from newsroom.notifier import (
    Notifier,
    ...
)
```

Verify `Notifier` is already imported (it is — `cli.py` already constructs `Notifier()` for the fetch-score-notify flow at line 136). No edit needed here. Confirm by reading.

- [ ] **Step 3.2: Wire `Notifier()` into `_maybe_run_digest_catchup`**

In `src/newsroom/cli.py`, find the catchup call (~line 177):

```python
            asyncio.run(generate_digest(state=state, slot=slot, date=now.date()))
```

Replace with:

```python
            asyncio.run(
                generate_digest(
                    state=state,
                    slot=slot,
                    date=now.date(),
                    notifier=Notifier(),
                )
            )
```

- [ ] **Step 3.3: Wire `Notifier()` into the `digest` subcommand**

In `src/newsroom/cli.py`, find the digest subcommand body (~line 222). Locate:

```python
    path = asyncio.run(generate_digest(state=state, slot=slot, date=date, force=force))
```

Replace with:

```python
    path = asyncio.run(
        generate_digest(
            state=state,
            slot=slot,
            date=date,
            force=force,
            notifier=Notifier(),
        )
    )
```

- [ ] **Step 3.4: Smoke-test the CLI wiring (no real notification needed)**

Run: `uv run newsroom digest --help`
Expected: Help output lists `--force` and `--date`. No traceback.

Run: `uv run newsroom --help`
Expected: Top-level help lists subcommands including `digest`. No traceback.

- [ ] **Step 3.5: Run the full test suite to confirm nothing broke**

Run: `uv run pytest -v`
Expected: All tests pass. (CLI tests in `test_cli.py` use mocks and won't hit pync.)

- [ ] **Step 3.6: Format and lint**

Run: `uv run ruff format src/newsroom/cli.py`
Run: `uv run ruff check src/newsroom/cli.py`
Expected: No errors.

- [ ] **Step 3.7: Commit**

```bash
git add src/newsroom/cli.py
git commit -m "$(cat <<'EOF'
feat(cli): wire Notifier() into digest + catchup paths

Both the regular `newsroom digest` subcommand and the safety-net
catchup in _maybe_run_digest_catchup now construct a Notifier() and
pass it into generate_digest, so a finalized non-empty digest fires
exactly one macOS notification regardless of which path produced it.
EOF
)"
```

---

## Task 4: Document the architectural deferral in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` ("Known Architecture Deferrals" section)

The digester→notifier direct call violates the "all inter-module communication via state DB" invariant — same class as the existing catchup deferral. Document it transparently so a future Phase-2 refactor folds both into one event-table change.

- [ ] **Step 4.1: Read the current "Known Architecture Deferrals" section**

Open `CLAUDE.md`, locate the heading `## Known Architecture Deferrals`. Read the existing single bullet (about digest catchup calling `generate_digest()` directly).

- [ ] **Step 4.2: Add the second bullet**

In `CLAUDE.md`, find this passage:

```markdown
- **Digest catchup calls `generate_digest()` directly from the fetch path**
  (`cli.py::_maybe_run_digest_catchup`). This crosses the
  "fetcher/scorer/digester/notifier communicate only via state DB" invariant.
  Race-safe since the `claim_digest_slot` fix (commit `b1b84a4`), but still a
  direct Python call between modules. Phase-2 refactor: fetcher writes a
  `missed_slot` marker to state; a dedicated `newsroom digest-catchup` command
  (or the regular `digest` command itself) consumes markers.
```

Append this second bullet directly after it (preserving the existing bullet, before the next `## ` heading):

```markdown
- **Digester calls `Notifier.notify_digest_ready()` directly after slot finalization**
  (`digester.py::generate_digest`, optional injected `notifier` param). Same
  invariant violation class as the catchup deferral above. Implements spec
  §4.4 step 10 — a notification is fired once per finalized non-empty digest.
  Race-safe via the existing `claim_digest_slot` mechanism (only the slot
  winner reaches the notify call). Phase-2 refactor: replace the direct call
  with a `digest_events` table that a dedicated notifier process consumes;
  fold this together with the catchup-deferral refactor into one change.
```

- [ ] **Step 4.3: Commit the doc update**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude): document digester→notifier deferral

Records the new direct call from generate_digest into
Notifier.notify_digest_ready as a documented architectural deferral
(same class as the existing catchup deferral). Phase-2 will fold
both into a single event-table refactor.
EOF
)"
```

---

## Final Verification

- [ ] **Step F.1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step F.2: Lint the whole project**

Run: `uv run ruff check src/ tests/`
Run: `uv run ruff format --check src/ tests/`
Expected: No errors, no reformat needed.

- [ ] **Step F.3: Confirm all four commits are present**

Run: `git log --oneline -5`
Expected: Top four commits (newest first) are:
1. `docs(claude): document digester→notifier deferral`
2. `feat(cli): wire Notifier() into digest + catchup paths`
3. `feat(digester): notify on finalized non-empty digest`
4. `feat(notifier): add notify_digest_ready for digest system-events`

- [ ] **Step F.4: Manual end-to-end smoke test (optional but recommended)**

The real-world test path that's hard to automate: trigger a real digest and watch for a real macOS notification. Run from the project root:

```bash
uv run newsroom digest --force
```

Expected:
1. CLI exits 0.
2. `~/Documents/!AI/news/YYYY/MM/YYYY-MM-DD.md` exists / has been updated.
3. A macOS notification appears: title "Newsroom", body e.g. "Morgen-Digest bereit (12 Items)" or "Abend-Digest bereit (8 Items)" depending on time.
4. Clicking the notification opens the markdown file in the default `.md` app.

If the notification does not appear: check `~/Library/Logs/newsroom.log` for `"digest notification send failed"` warnings, and verify `pync` is installed (`uv run python -c "import pync; print(pync.__version__)"`).

---

## Acceptance (matches spec §10)

1. ✅ Non-empty digest → one notification, correct title/body, click opens file.
2. ✅ Empty digest → no notification, file-placeholder still written.
3. ✅ Idempotent re-run → no second notification.
4. ✅ `--force` re-run → fresh notification.
5. ✅ Catchup-path → identical notification behaviour.
6. ✅ Send failure → digest still succeeds, warning logged.
7. ✅ All new tests pass; no regressions in pre-existing tests.
