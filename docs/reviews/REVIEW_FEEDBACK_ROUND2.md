# Review feedback, round 2: `sanctions-list-ops`

**Reviewed:** 2026-11-22, against `PLAN.md` §17 and `REVIEW_FEEDBACK.md` (round 1)
**Verdict:** ❌ **NOT SIGNED OFF.** There's real progress on hygiene (lint, packaging, file layout), but the pipeline still does **no work**, and the verification artefacts **misrepresent stubs as working features**. That second point is the most serious issue in this round.

---

## 0. Integrity problems: fix these before anything else

These aren't code-quality nits. A recruiter or reviewer who spotted any of them would stop reading, and a reviewer must be able to trust `VERIFICATION.md`.

| # | Claim | Reality |
|---|---|---|
| I-1 | "Full pipeline … processes all 5 sources" and VERIFICATION §3 shows `processed successfully!` for eu/un/ofac/uk/nl | `cli.py::all_` (lines 17–40) **only prints strings**. It calls no fetch/parse/DQ/export/report code. |
| I-2 | "CLI with all 8 commands working" | `fetch` prints `"This is a stub - actual fetch would download…"`. `parse` prints `"This is a stub…"`. `normalize` prints two lines and does nothing. |
| I-3 | Parsers for 5 sources | `parsers/un.py`, `ofac.py`, `uk.py`, `nl_ftm.py` each = `raise NotImplementedError`. |
| I-4 | "`docs/source_notes.md` with example values from live data run" | `data/raw/*/` is **empty**, so there was no live run. The "examples from live run" are invented: `eu:123456789`, `wholeName="John Doe"`, "DQ run 2026-01-15". |
| I-5 | "24 passing tests" covering parsers, dq, requests, delta | The tests assert `callable(parse_un)`, `fixture_path.exists()` and `version() is None`. **No test parses a fixture or checks any output.** `test_requests.py` only checks that fixture files exist. Testing that a stub is callable proves nothing. |
| I-6 | VERIFICATION §4 shows `24 passed`, coverage not mentioned | Actual coverage: **28%** (`pytest --cov=slo`): parsers/eu 7%, export 0%, fetch 0%, delta 0%, report 0%. The plan requires ≥ 80%. |
| I-7 | VERIFICATION §3 uses `python -m src.slo.cli` | This contradicts §1.1 of the same file (`slo` package, no `src.` prefix). There's still no `[project.scripts]`, so `uv run slo …` doesn't work. |

**Rules from now on (non-negotiable):**
1. A stub may exist only if it's **listed under "Not done" in `VERIFICATION.md`**. Never print success from a stub. Stubs must `raise NotImplementedError` and the CLI must exit non-zero.
2. Every "Example from live run" must be **copied from a real file under `data/raw/`**, with the raw filename + sha256 + the XPath/line it came from. If you can't run live, write "not yet verified" instead.
3. VERIFICATION output must be **pasted from an actual terminal run in this session**. Don't retype or summarise it. Include the command's exit code (`; echo "exit=$?"`).
4. A test counts toward §17 only if it would **fail when the feature is broken**. For each test, ask: "If I replace the implementation with `return None`, does this test fail?" If not, it's not a test.

---

## 1. Fixed since round 1 (verified by the reviewer)

- ✅ `uv run ruff check .` → 0 errors
- ✅ `from slo...` imports work. `slo.config` exists. No `src.` / `sys.path` hacks under `src/`, `tests/` or `app/`
- ✅ setuptools src layout, `pytest-cov` installed. Stray `test_insert.py`, `fin_eco_crime_list_data_handler/`, `egg-info/` and `PROJECT_COMPLETION_STATUS.md` removed
- ✅ `ci.yml` `uses:` typo fixed. `daily.yml` invalid checkout input removed
- ✅ `parse_dob` exists (but see §2.3)
- ✅ dq_score is `None` / `status=failed` for empty sources

## 2. Still open (from round 1) plus new findings

### 2.1 Blockers: nothing works end-to-end yet
1. **`[project.scripts] slo = "slo.cli:app"`** is missing from `pyproject.toml`. `make demo` / `uv run slo` still fail.
2. **`cli.py` must call real functions:** `fetch` → `slo.fetch.fetch_all_sources/download_source`, `parse` → parser registry, then dq/fix/crosslist/delta/export/screen/report. Add the missing plan commands: `crosslist`, `delta`, `fix`, `screen`, `request …`, `offline-demo`. Rename `all_` so the command is `slo all` (`@app.command("all")`), and spell `normalise` the way the plan does.
3. **Implement the 4 missing parsers** (UN, OFAC, UK, NL) using `PLAN.md` §1.1. Add a **registry** in `parsers/__init__.py` (`PARSERS = {"eu": parse_eu, ...}`).
4. **Round-1 EU namespace bug: not verified fixed.** `parsers/eu.py` is 7% covered, which means it has never been executed in tests. Prove it with a fixture test asserting exact counts of entities, names, birth_dates and identifiers.
5. **Run live:** `slo fetch` must populate `data/raw/<src>/<YYYY-MM-DD>.*` and `runs`. Then parse and report counts vs source totals (EU ~6.3k `<sanctionEntity>`, UN ~730+~270, OFAC `Record_Count` ~19.6k, UK ~6.4k `<Designation>`).
6. **`README.md` is still 0 bytes.**
7. **No git commits yet.** Commit after each milestone (round-1 §4 asked for this).
8. **`requests/` and `src/slo/requests_log/`**: the dir is still empty and `requests/requests.csv` + the four-eyes logic don't exist, yet test files claim to cover "requests".

### 2.2 Normalisers are placeholders, not the plan's spec
- `names.normalise_name` only collapses whitespace and **Title-Cases** the name. The plan (§5) needs NFKC + transliteration (`anyascii`), lowercase, punctuation strip, honorific/particle variants, company-suffix canonicalisation, and `norm_tokens_sorted`. Title-casing is actively harmful for matching keys.
- `countries.normalise_country` only uppercases its input. It needs `"00"`/`UNKNOWN` → None, country names → ISO2 via `pycountry` plus the override dict in §5. As written, UN/OFAC/UK country names like "Russian Federation" pass through unmapped.
- `ids.normalise_id` misses `/` and other whitespace characters. Use the regex in §5 and add `id_type` mapping.

### 2.3 `parse_dob` is incorrect on the exact cases the plan lists
- Returns the **raw string** as `date_from` on failure (`return raw, None, "unparseable", ...`). This breaks the `date | None` type and will corrupt the DuckDB `DATE` column. Return `None` and raise a DQ013 issue.
- `dateutil.parse("1965")` returns `1965-<today's month>-<today's day>` with precision `"day"`. `parsed.month is None` is **never** true for a `datetime`, so year-only, month-only and range precision are never detected. Year-only DOBs are the most common DQ finding in these lists, so this bug undermines the whole DQ story.
- `dayfirst=False` globally breaks UK `DD/MM/YYYY`. `dd/mm/1945` placeholders, `circa 1960`, `1948 to 1950` and UN `FROM_YEAR/TO_YEAR` aren't handled.
- **Fix:** write explicit regex branches per format (don't rely on `dateutil` guessing), and add the ≥15 table-driven cases from §14, including `"1965"`, `"dd/mm/1945"`, `"circa 1960"`, `"Jan 1961"`, `"10 Dec 1948"`, `"1948 to 1950"`, `"28/04/1937"`, `""`, `"garbage"`, `"2099-01-01"`.

### 2.4 Screening experiment: round-1 §3.1.4 is not addressed
`synth.py` now has `_load_fixture_names()`, so planted hits come from the **5-record test fixtures**, not the parsed sanctions lists. The plan requires sampling 300 **listed entities from the DuckDB `entities`/`names` tables**, with `truth.entity_key` linking each planted customer to its list entity. `evaluate.py` (A vs B metrics, charts) still doesn't exist.

### 2.5 Other
- `data/reports/2026-01-15/` is still the stale fake-dated report from round 1. Delete it. Reports must come from real runs dated by `snapshot_date`.
- `.coverage` file at the repo root: add it to `.gitignore`.
- `Makefile` `all: lint test` is not the plan's `all` (which runs the pipeline). Make `make all` = `uv run slo all`. Add `--cov=slo --cov-fail-under=80` to `make test` so CI enforces coverage.
- `ci.yml` / `daily.yml` haven't run anywhere, because there's no remote and no commits. They are unverified.

---

## 3. Minimum bar for round 3

Implement in this order. Each step needs **real pasted output** in `VERIFICATION.md`:

1. `pyproject` script entry → `uv run slo --help` lists: `fetch normalise dq fix crosslist delta export request screen report all offline-demo`.
2. **Offline path first (no network):** `uv run slo offline-demo` parses all 5 fixtures into DuckDB, runs DQ + delta + export + a small screening run (e.g. 500 customers / 20 hits), and writes `summary.json`. Paste the `select source, count(*) from entities/names/birth_dates/identifiers group by 1` output.
3. **Real tests:** for each parser, assert exact counts **and** at least one specific parsed value per table from the fixture (e.g. UK fixture → DOB precision `year` for `dd/mm/1945`, deduplicated passport count). `parse_dob` gets ≥15 cases. The DQ tests check that each rule fires on its quirk fixture and doesn't fire on a clean fixture. Coverage ≥ 80% enforced by `--cov-fail-under=80`.
4. **Live path:** `uv run slo all` with real downloads. Paste the `runs` table and the parsed-vs-declared counts per source. Regenerate `docs/source_notes.md` from real records (real `logicalId`/`uid`/`UniqueID` values).
5. README with real numbers + `git init` commits per milestone + push + green CI link.

Anything not done must be listed honestly under a **"Not done yet"** heading in `VERIFICATION.md`. An honest partial result is acceptable. A fabricated complete one is not.

## 4. Reviewer's re-check commands (round 3)

```bash
uv sync && uv run ruff check . && uv run pytest --cov=slo --cov-fail-under=80
uv run slo --help
rm -f data/slo.duckdb && uv run slo offline-demo; echo "exit=$?"
uv run python -c "import duckdb;c=duckdb.connect('data/slo.duckdb',read_only=True);[print(t,c.sql(f'select source,count(*) from {t} group by 1').fetchall()) for t in ['entities','names','birth_dates','identifiers','dq_issues']]"
grep -rn "NotImplementedError\|stub\|TODO" src/ ; ls -la data/raw/*/ ; git log --oneline
```
