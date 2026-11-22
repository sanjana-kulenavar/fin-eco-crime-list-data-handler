# Review feedback, round 3: `sanctions-list-ops`

**Reviewed:** 2026-11-22, against `PLAN.md`, `REVIEW_FEEDBACK.md` and `REVIEW_FEEDBACK_ROUND2.md`
**Verdict:** ❌ **NOT SIGNED OFF.** Estimated progress: about M3 started. Nothing runs end-to-end yet.

## 0. Honesty: improved, not resolved

- ✅ **Good:** stubs no longer print success, and `VERIFICATION.md` now has an honest **"Not done yet"** section. Keep doing that.
- ❌ **Still a problem:** the hand-off message said *"All feedback from REVIEW_FEEDBACK_ROUND2.md has been addressed"*. That isn't true (see below). It also said "19 of 26 pass; 8 failures", but the real figure is 19 passed and 7 failed out of 26. Other specific claims were false too: "normalise_name: NFKC + transliteration…" (the file is **unchanged**), and `slo --help` lists `offline-demo` (it doesn't).
- **Rule for the next hand-off:** the summary must be produced by pasting the actual output of the §5 commands below. No prose claims without output next to them.

## 1. Reviewer's evidence

| Check | Result |
|---|---|
| `uv run ruff check .` | ❌ **39 errors**, a regression from 0 in round 2 (11 unused imports, 10 unused variables, 4 redefinitions, **1 undefined name**) |
| `uv run pytest --cov=slo` | ❌ **7 failed / 19 passed**, coverage **45%** (needs ≥ 80%) |
| `uv run slo --help` | ⚠️ Works (entry point added ✅), but lists 8 commands. Missing: `offline-demo`, `fix`, `crosslist`, `delta`, `screen`, `request`. Still spelled `normalize` |
| `uv run slo offline-demo` | ❌ `No such command 'offline-demo'`, exit 2 |
| CLI commands | ❌ All 7 working commands `raise NotImplementedError`. Parsers/DQ/export code exists but **nothing is wired to the CLI** |
| `make all` / `make demo` | ❌ Both call the non-existent `offline-demo` |
| `README.md` | 1.6 KB (was 0). Content not yet backed by any run |
| `git log` | ❌ **Still no commits** (third round asking) |
| `data/raw/*` | ❌ Still empty, so no live fetch has been done |

## 2. What improved (verified)

- ✅ `[project.scripts] slo = "slo.cli:app"`, so `uv run slo` works.
- ✅ UN/OFAC/UK/NL parsers now contain real code, and there's a registry in `parsers/__init__.py`.
- ✅ `parse_dob` is much better. Correct for `1965`, `dd/mm/1945`, `circa 1960`, `Jan 1961`, `10 Dec 1948`, `1948 to 1950`, `28/04/1937`, ISO, `""`, `garbage`.
- ✅ `normalise_country` handles `00` → None, `Russian Federation` → RU, `United Kingdom` → GB.
- ✅ The stale fake-dated report was deleted, and `.coverage` is gitignored.

## 3. Defects to fix (in this order)

### 3.1 Test fixtures are broken. This is the root cause of 5 of the 7 failures
- `tests/fixtures/un_fixture.xml` **isn't well-formed XML**: `XMLSyntaxError: error parsing attribute name, line 16, column 19`. Validate every fixture with `xmllint --noout tests/fixtures/*.xml`.
- `tests/fixtures/nl_fixture.json` **isn't valid FollowTheMoney**: record 2 has `"name":[{"full_name":"Mohammed Ali","language":"arabic"}]`. In FtM, every property value is a **list of strings**. The parser also loads it as a single JSON document (`JSONDecodeError: Extra data`). It's NDJSON, so parse it **line by line**.
- The fixtures use placeholder data (`John`, `Johan Svensson`, `UN DATAID` = `CDi.001`, `dateGenerated="2026-01-15"`). Rebuild each fixture by **copying 3–5 real records** from the live files (after the first `slo fetch`), then edit them minimally to add the quirks in PLAN §14. In UN, `DATAID` is numeric and `REFERENCE_NUMBER` is `CDi.001`.

### 3.2 Parser ↔ DuckDB insert bugs
- `test_eu_fixture`: `ConversionException: invalid date field format: ""`. Empty strings are being inserted into `DATE` columns. Convert `""` → `None` before inserting, and pass `datetime.date` objects, not strings.
- `test_ofac_fixture` / `test_uk_fixture`: `Parameter argument/count mismatch … excess parameters: 12`. The INSERT has 8 placeholders but 12 values are passed. **Use one shared insert helper in `store.py`** that builds the column list and placeholders from the pydantic model / DDL, so the parsers can't drift from the schema. Better still, have the parsers return polars DataFrames per table and let `store.load_snapshot()` insert them (`con.register` + `INSERT … SELECT`).
- The parsers catch errors and only `logger.warning(...)` them per entity. That's why bugs show up as silently missing rows. Count the failures, write them to `runs.error`, and make the run `status=failed` if any entity fails to parse. Tests must assert `failures == 0`.

### 3.3 DQ tests
- `test_run_dq_rules_with_fixture`: DQ013 didn't fire. That follows from the bad fixture/parse above, so re-check once 3.1/3.2 are fixed.
- `test_run_dq_rules_clean_source_no_issues`: the "clean" source got 1 issue. Print the issue and decide whether the rule or the fixture is wrong. Don't weaken the assertion.

### 3.4 Normalisers: still incomplete
- `normalise/names.py` is **byte-for-byte unchanged** from round 2: it still Title-Cases and does nothing else. Implement PLAN §5 (NFKC → `anyascii` → lowercase → strip punctuation → company suffix canonicalisation → `norm_tokens_sorted`), and add tests: `"Saddam Hussein Al-Tikriti"` → `"al saddam tikriti hussein"`-style sorted key, `"حاجی خيرالله"` → a Latin transliteration, `"ANGLO-CARIBBEAN CO., LTD."` → canonical suffix tokens.
- `normalise_country`: these **still fail** and are listed explicitly in PLAN §5: `"Democratic Republic of the Congo"`, `"Iran (Islamic Republic of)"` and `"Korea, North"` are returned uppercased instead of `CD` / `IR` / `KP`. Add the override dict, and return `None` (plus a DQ020 issue) for anything unmapped. **Never return the raw string as an ISO code.**
- `parse_dob("2099-01-01")` returns precision `day`. Future dates must be flagged (`invalid`, DQ014).

### 3.5 Lint regression
Run `uv run ruff check . --fix`, then fix the remaining ones by hand, especially the **F821 undefined name** (a runtime crash). Add `ruff check` as a pre-commit step so this doesn't regress again.

## 4. Scope for round 4: one milestone only

Don't touch screening, export, the dashboard or CI yet. Deliver just this:

> **Offline demo end-to-end.** `uv run slo offline-demo` loads all 5 fixtures → parse → normalise → DuckDB → DQ → writes `data/reports/<today>/dq_scorecard.json` and `summary.json`, and exits 0.

Acceptance:
1. `ruff check .` → 0 errors.
2. `pytest` → **0 failures**. Each parser test asserts exact row counts in `entities`, `names`, `birth_dates`, `identifiers`, plus one specific parsed value per table.
3. `slo offline-demo; echo exit=$?` → `exit=0`, then this DuckDB count query is pasted:
   ```
   select 'entities',source,count(*) from entities group by all union all
   select 'names',source,count(*) from names group by all union all
   select 'birth_dates',source,count(*) from birth_dates group by all union all
   select 'dq_issues',source,count(*) from dq_issues group by all order by 1,2;
   ```
   Every source has > 0 rows in entities and names.
4. `git init` (if needed) and **at least one commit** containing this state.
5. `VERIFICATION.md` updated with the pasted output of §5, and the "Not done yet" list kept accurate.

Coverage ≥ 80% isn't required for round 4. It becomes required at final sign-off.

## 5. Commands the reviewer will run next time

```bash
uv sync
uv run ruff check .
xmllint --noout tests/fixtures/*.xml && python -c "import json;[json.loads(l) for l in open('tests/fixtures/nl_fixture.json')]"
uv run pytest -q --cov=slo
rm -f data/slo.duckdb && uv run slo offline-demo; echo "exit=$?"
# + the DuckDB count query from §4.3
git log --oneline
```
