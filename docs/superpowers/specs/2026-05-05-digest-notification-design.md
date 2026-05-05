# Digest-Notification — Design

**Status:** Approved
**Date:** 2026-05-05
**Scope:** Phase 1
**Related Spec:** `2026-04-19-daily-newsroom-design.md` §4.4 step 10, §4.5

## 1. Motivation

Die Hauptspec definiert in §4.4 Schritt 10 und §4.5 (End-to-End-Beispiel) eine
macOS-Notification, die nach jedem fertiggestellten Digest gefeuert wird:

> `pync.notify("Digest bereit: 12 Items", open=file_path)`
> Beispielausgaben: `"Morgen-Digest bereit (20 Items)"`, `"Abend-Digest bereit (14 Items)"`.

Diese Verhaltensweise ist bisher nicht implementiert. `digester.generate_digest()`
schreibt das Markdown-File und finalisiert den DB-Slot, sendet aber keine
Benachrichtigung. Ergebnis: Nutzer wissen nicht zuverlässig, wann der morgendliche
oder abendliche Digest gelaufen ist, ohne `newsroom status` aufzurufen oder das
Verzeichnis `~/Documents/!AI/news/` zu prüfen.

## 2. Ziel

Nach jedem erfolgreich finalisierten, **nicht-leeren** Digest-Slot wird genau eine
macOS-Notification ausgelöst, deren Click die Digest-Datei in der Default-App öffnet.

## 3. Architektur

### 3.1 Aufrufkette

```
cli.digest()              ─┐
                           ├──→ generate_digest(state, slot, date, notifier=Notifier())
cli._maybe_run_catchup()  ─┘                                          │
                                                                      ▼
                                                            (item_count > 0)
                                                                      │
                                                                      ▼
                                                         notifier.notify_digest_ready(
                                                             slot, item_count, file_path)
```

`generate_digest()` erhält einen optionalen `notifier`-Parameter. Beide Aufrufpfade
in `cli.py` (regulärer `digest`-Subcommand und `_maybe_run_digest_catchup`)
instanziieren einen `Notifier()` und reichen ihn durch. Der Catchup-Pfad bekommt
die Notification dadurch automatisch ohne Doppel-Verdrahtung.

### 3.2 Architektur-Invarianten-Verhältnis

CLAUDE.md verlangt: *"All inter-module communication is via state DB, never direct
Python calls between fetcher/scorer/digester/notifier."* Dieses Design verletzt
diese Invariante bewusst — es ist die gleiche Klasse von pragmatischem Kompromiss
wie der bereits dokumentierte "Digest catchup deferral". Beide Verletzungen
gehören zu derselben Phase-2-Refactor-Gruppe (z. B. eine `digest_events`-Tabelle,
die ein separater Worker abarbeitet). Sie sind sicher, weil:

- `generate_digest()` ist die einzige Stelle, an der ein Digest finalisiert wird.
- Der `claim_digest_slot`-Mechanismus (Commit `b1b84a4`) garantiert genau einen
  Sieger pro Slot → genau eine Notification.
- Notifier-Fehler werden lokal abgefangen und beeinträchtigen nie den Digest-Erfolg.

CLAUDE.md "Known Architecture Deferrals" wird um diesen Punkt erweitert.

## 4. Notifier-API

Neue Methode auf `notifier.Notifier`, getrennt von `maybe_notify` (welche eine
ganz andere Domäne bedient: Item-Pushes mit Importance-Threshold und Quiet-Hours).

```python
async def notify_digest_ready(
    self,
    *,
    slot: str,                       # "morning" | "evening"
    item_count: int,
    file_path: Path | str,
) -> None:
    """System-Notification bei fertigem Digest.

    Anders als `maybe_notify`: keine Threshold-, Quiet-Hour- oder Bundling-Logik.
    Digests sind System-Events, keine Item-Pushes — sie sind selten genug
    (max. 2/Tag) und ein gewünschtes Resultat-Signal.
    """
    label = "Morgen-Digest" if slot == "morning" else "Abend-Digest"
    title = "Newsroom"
    message = f"{label} bereit ({item_count} Items)"
    try:
        await self._send(title=title, message=message, url=str(file_path))
    except Exception as e:
        logger.warning("digest notification send failed: %s", e)
```

### 4.1 Format-Vorgaben

| Feld     | Wert                                              | Begründung |
|----------|---------------------------------------------------|------------|
| `title`  | `"Newsroom"`                                      | System-Event, keine Category-Prefix-Hierarchie wie bei Item-Pushes |
| `message`| `"Morgen-Digest bereit (N Items)"` resp. `"Abend-Digest bereit (N Items)"` | Spec §4.5 wörtlich |
| `url`    | absoluter File-Pfad zur Digest-Markdown           | `pync` reicht den Wert an `terminal-notifier --open` weiter; macOS' `open`-Tool akzeptiert sowohl `https://`-URLs als auch absolute Datei-Pfade → Click öffnet die Datei in der Default-App (z. B. Bear, Obsidian, je nach `.md`-Default-Mapping) |

### 4.2 Reuse des Send-Pfads

`notify_digest_ready` ruft `self._send` — denselben Injektionspunkt, den
`maybe_notify` nutzt. Tests können also die existierende `send_fn`-Injection
verwenden, um echte pync-Calls zu vermeiden.

### 4.3 Fehlerbehandlung

`try/except` lokal um `self._send`. Notification-Fehler werden geloggt und
verschluckt — der Digest-Erfolg hängt nie von der Notification ab (Spec §6
"best-effort notifications").

## 5. Digester-Änderung

`generate_digest()` bekommt einen optionalen Parameter und einen einzigen neuen
Call am Ende des Items-Branches:

```python
async def generate_digest(
    *,
    state,
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
    notifier: "Notifier | None" = None,   # ← NEU
) -> Path:
    ...
    state.finalize_digest(
        date=date.isoformat(), slot=slot,
        file_path=str(target_file), item_count=len(items),
    )
    if notifier is not None:                                              # ← NEU
        await notifier.notify_digest_ready(                               # ← NEU
            slot=slot, item_count=len(items), file_path=target_file,      # ← NEU
        )                                                                 # ← NEU
    return target_file
```

### 5.1 Verhalten in den verschiedenen Pfaden

| Pfad                                          | Notify? | Mechanismus |
|-----------------------------------------------|---------|-------------|
| Erfolgreicher Items-Digest                    | **Ja** (1×) | Erreicht den Notify-Aufruf nach `finalize_digest` |
| Empty-Slot-Branch (`not items`)              | Nein    | Returned in der `if not items:`-Branch *vor* dem Notify-Punkt — kostenlose No-Op |
| Idempotenz-Skip (`existing and not force`)    | Nein    | Returned in Zeile 124-128 vor allem Token-Verbrauch und vor Notify |
| `--force`-Regeneration                        | **Ja** (1×) | Durchläuft denselben Items-Branch wie der Erstlauf |
| Race-Loser (`claim_digest_slot` returns False)| Nein    | Returned in Zeile 139-141 vor Notify — der Sieger notifiziert |
| Opus-Failure mit Fallback-Digest              | **Ja** (1×) | Fallback-Digest wird trotzdem als File geschrieben und finalisiert |

### 5.2 Forward-Reference für Type-Hint

Der Notifier-Type-Hint wird als Forward-String (`"Notifier | None"`) notiert,
damit `digester.py` keinen Top-Level-`from newsroom.notifier import Notifier`
braucht. Das vermeidet ein Circular-Import-Risiko, falls Notifier später Code
importiert, das selbst aus anderen newsroom-Modulen lädt.

## 6. CLI-Verdrahtung

Zwei kleine Änderungen in `cli.py`:

**`digest()` (~Zeile 222):**
```python
from newsroom.notifier import Notifier
path = asyncio.run(generate_digest(
    state=state, slot=slot, date=date, force=force,
    notifier=Notifier(),
))
```

**`_maybe_run_digest_catchup()` (~Zeile 177):**
```python
asyncio.run(generate_digest(
    state=state, slot=slot, date=now.date(),
    notifier=Notifier(),
))
```

Der `Notifier()`-Default-Constructor erzeugt einen async `send_fn`, der `pync`
über `anyio.to_thread.run_sync` aufruft — selbes Pattern wie heute schon für
Item-Pushes verwendet.

## 7. Tests

| Test                                                   | Datei              | Prüft |
|--------------------------------------------------------|--------------------|-------|
| `test_notify_digest_ready_morning_format`              | `test_notifier.py` | `send_fn` mit `title="Newsroom"`, `message="Morgen-Digest bereit (5 Items)"`, `url=<path>` |
| `test_notify_digest_ready_evening_format`              | `test_notifier.py` | analog für "Abend-Digest" |
| `test_notify_digest_ready_send_failure_swallowed`      | `test_notifier.py` | `send_fn` raised → keine Exception nach außen, Warning geloggt |
| `test_generate_digest_notifies_when_items_present`     | `test_digester.py` | Injizierter Mock-Notifier wird genau 1× aufgerufen |
| `test_generate_digest_silent_on_empty_slot`            | `test_digester.py` | Bei `not items` wird der Notifier nicht aufgerufen |
| `test_generate_digest_no_notify_on_idempotent_skip`    | `test_digester.py` | Wenn Slot schon claimed/finalized ist, kein Notify |
| `test_generate_digest_force_regen_notifies_again`      | `test_digester.py` | Nach `--force` erfolgt erneuter Notify-Call |
| `test_generate_digest_no_notifier_works`               | `test_digester.py` | `notifier=None` ist erlaubt, kein Crash |

Mock-Notifier in Digester-Tests via `Notifier(send_fn=AsyncMock())` — derselbe
Injektionspunkt wie für `maybe_notify`-Tests heute.

## 8. Dokumentations-Updates

- **`CLAUDE.md` "Known Architecture Deferrals"**: zweiten Bullet hinzufügen, der
  den direkten `digester → notifier`-Call dokumentiert. Begründung: gleiche
  Verletzungs-Klasse wie der bestehende Catchup-Deferral; Phase-2 refactort
  beide gemeinsam.
- **Hauptspec** bleibt unverändert — sie definiert das Verhalten bereits in §4.4
  Schritt 10 und §4.5; nur die Implementierung war ausstehend.

## 9. Out of Scope

- **Keine Bundling-Logik** zwischen `notify_digest_ready` und `maybe_notify` —
  Digest-Notifications und Item-Pushes sind getrennte Kanäle.
- **Keine Quiet-Hours** für Digest-Notifications. Digests feuern um 07:00 (knapp
  am Quiet-Ende) und 20:00 (vor Quiet-Start) — beide außerhalb. Catchup an
  ungewöhnlichen Zeiten ist selten genug, um nicht extra zu unterdrücken.
- **Keine separate `digest_notifications`-Tabelle.** Idempotenz erbt sich vom
  existierenden `claim_digest_slot`-Race-Schutz.
- **Keine Sound-/Icon-Customization** über pync-Defaults hinaus.

## 10. Akzeptanzkriterien

1. Nach `newsroom digest` mit ≥1 Item erscheint genau eine macOS-Notification.
   Titel-Zeile (fett): `Newsroom`. Body-Zeile: `Morgen-Digest bereit (N Items)`
   resp. `Abend-Digest bereit (N Items)`.
2. Click auf die Notification öffnet die geschriebene Markdown-Datei.
3. Bei leerem Digest erscheint keine Notification, das File-Placeholder wird
   trotzdem geschrieben.
4. Bei wiederholtem `newsroom digest` (ohne `--force`) erscheint keine zweite
   Notification — der Idempotenz-Skip greift davor.
5. `newsroom digest --force` erzeugt erneut genau eine Notification.
6. Catchup-Lauf in `_maybe_run_digest_catchup` erzeugt ebenfalls genau eine
   Notification.
7. Wenn `pync` nicht verfügbar ist oder `_send` raised, läuft der Digest
   trotzdem erfolgreich durch (Exit-Code 0, File geschrieben, DB finalisiert);
   eine Warning steht im Log.
8. Alle Tests aus Sektion 7 sind grün; Coverage in `notifier.py` und
   `digester.py` bleibt ≥ Projekt-Ziel (70 %).
