# Review feedback: `sanctions-list-ops`

**Reviewed:** 2026-11-22, against `PLAN.md` (§17 "Done when")
**Verdict:** ❌ **NOT SIGNED OFF.** The implementation is roughly at milestones M1–M2 (partial). Most §17 boxes are unmet.

## 0. Read this first: the status report is inaccurate

`PROJECT_COMPLETION_STATUS.md` marks every §17 item as `[x]` complete. Running the checks shows most of those claims are false: tests don't exist, the CLI doesn't exist, the README is empty, there are no git commits, and the "daily run" never happened. **Delete that file.** From now on, mark a box only after you've pasted the command and its output as proof (see §4 below). Never write completion claims without running the check.

---

## 1. Evidence (commands run by the reviewer)

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | **35 errors**, including **20 × F821 undefined name** (real runtime crashes, not style issues) |
| Tests | `uv run pytest -q` | **"no tests ran"**. There are no `tests/test_*.py` files, only fixtures |
| Demo | `uv run slo offline-demo` | **`Failed to spawn: slo`**. No `[project.scripts]` entry and no `src/slo/cli.py` |
| Import | `python -c "from slo.parsers import eu"` | **`ModuleNotFoundError: No module named 'config'`** |
| Git | `git log` | **No commits at all.** Everything is untracked |
| README | `wc -c README.md` | **0 bytes** |
| DB contents | `data/slo.duckdb` | `entities`: 1 row. Every other table: **0 rows** |
| Report | `data/reports/2026-01-15/summary.json` | EU 1 entity, all other sources 0, screening metrics all 0, plus a "dq_score 100" for a list with 1 record |
| Raw data | `data/raw/*/` | **All empty**, so live data was never fetched or parsed |
| Exports | `data/exports/.../manifest.json` | `"system": ""`, `record_count: 0`, CSV is header-only |

## 2. What's missing entirely (files the plan requires that don't exist)

- `src/slo/cli.py` + `[project.scripts] slo = "slo.cli:app"` in `pyproject.toml`
- Parsers: `parsers/__init__.py` (registry), `un.py`, `ofac.py`, `uk.py`, `nl_ftm.py` (only `eu.py` exists)
- `normalise/` package: **empty dir** (`names.py`, `dates.py`, `countries.py`, `ids.py` missing)
- `dq/rules.py`, `dq/fixes.py` (only `engine.py`)
- `crosslist.py`, `requests_log.py` (there's an **empty dir** `requests_log/` instead), `screening/evaluate.py`
- Empty stray directory `src/slo/export/`, which conflicts with `src/slo/export.py` as a package name. Delete it.
- `requests/requests.csv`, `requests/internal_list.yaml`
- `docs/dq_rules.md`
- **All tests**: `test_parsers.py`, `test_normalise.py`, `test_dq.py`, `test_delta.py`, `test_matcher.py`, `test_requests.py`, `test_cli_smoke.py`
- Screening outputs: `metrics.json`, the 3 PNG charts, `top_false_positives.csv`
- `data/deltas/` is empty. `dq_scorecard.json` doesn't exist.

## 3. Defects in code that does exist (fix in this order)

### 3.1 Blockers
1. **Import paths are broken everywhere.** Modules import `from src.slo.models ...` (`delta.py:6`, `export.py:9-10`, `report.py:8-11`) and `from config import ...` (top-level `config/` package outside `src/`). Fix:
   - Move `config/__init__.py` loader code to `src/slo/config.py`. Keep YAML files in `/config/` and resolve them relative to the repo root (`Path(__file__).resolve().parents[2] / "config"`).
   - Use `from slo.models import ...` everywhere. Remove all `src.` prefixes.
   - Configure the setuptools src layout: `[tool.setuptools.packages.find] where = ["src"]`.
   - Delete the stray `src/fin_eco_crime_list_data_handler/` and `src/sanctions_list_ops.egg-info/`. Add `*.egg-info/` to `.gitignore` (it's already listed, but the files exist and should be removed).
2. **`parsers/eu.py` is broken:**
   - `Dict` / `List` are used but never imported (F821 ×14). Use built-in `dict` / `list`.
   - `_parse_eu_date` is called (`eu.py:333`) but never defined. Date parsing belongs in `normalise/dates.py::parse_dob`.
   - **Namespace bug:** `elem.findall("birthdate")` / `findall("identification")` (`eu.py:329,347`) find **nothing**, because the EU XML has the default namespace `http://eu.europa.ec/fpi/fsd/export`. Use `findall("{ns}birthdate")` or `elem.iterfind("ns:birthdate", namespaces={"ns": NS})`. This is likely why `names`/`birth_dates` have 0 rows. Add a fixture test that asserts names > 0.
   - Blind `except Exception: pass` (`eu.py:231`) hides errors. Catch the specific exception, or record a DQ issue.
3. **`export.py` lines 222–249 reference an undefined `source`.** It's probably missing a function parameter or loop variable. The manifest also writes `"system": ""`, so it should be populated from the `--system` argument.
4. **Synthetic "planted hits" are wrong. This is the most important logic bug in the experiment.** `truth.parquet` shows `original_name` values like `"Julian van Leuven"`, which are Faker customer names, perturbed with `"-ski"` suffixes. Plan §10.1 says to **sample 300 listed persons/orgs from the sanctions lists** and plant perturbed copies of *those listed names* as customers, with `truth` mapping `cust_id → entity_key`. As built, there is no link to any list entity (`entity_key` column absent), so recall cannot be measured. Also:
   - Perturbations should follow §10.1 (typo, token_swap, drop_middle, translit_variant table, alias_used = weak alias from the list). Appending `-ski` is not a transliteration variant.
   - The stored hashes are 8-char truncations (`"e597737c"`). Use the full sha256 and test determinism by regenerating and comparing.
   - The plan uses polars, but `synth.py`/`matcher.py` import pandas, which isn't a declared dependency. Pick one, preferably polars as planned, and declare it.
5. **CI workflow YAML is invalid:** `ci.yml` has `uses:astral-sh/setup-uv@v4` (missing space after the colon). `daily.yml` passes `permissions:` as an input to `actions/checkout` (not a valid input; job-level `permissions` is enough). `setup-uv` doesn't take `python-version` in v4 the way it's used here, so add `uv python install 3.12` or use `actions/setup-python`. Prefer `ubuntu-latest` (cheaper/faster). Validate with `actionlint`.
6. **Root-level `test_insert.py`** is a debug scratch file with a hard-coded absolute path to the user's home directory. Delete it.

### 3.2 Correctness / quality
- `dq_score` is 100 for a list with 1 record and 0 for empty lists. An empty or failed source must show `status=failed` / `dq_score=null`, not a misleading number. DQ060 should fire when the parsed count (1) ≠ the EU `<sanctionEntity>` count (~6.3k).
- Report dates (`2026-01-15`, `2026-01-01`) don't match the run date. Use the actual `snapshot_date` of the run (`date.today()` unless `--date` is given).
- `store.py:160` blind except. `config/__init__.py` has `load_screening_config` defined twice (F811).
- `Makefile`: `dash` should be `uv run streamlit run ...`. Add the `all` target (listed in `.PHONY` but not defined). `test` should include `--cov-report=term-missing --cov-fail-under=80`.
- `pyproject.toml`: add `pytest-cov` to the dev group (it's only in optional-deps, and `make test` uses `--cov`). Add a `[tool.ruff]` config. Lint is currently running with rules (`BLE`, `S`) the plan didn't require, so either configure them deliberately or drop them.
- `docs/source_notes.md` lists EU quirks, which is fine, but every quirk needs an **example value copied from the live file** (§17). Several entries describe the schema rather than a data-quality observation.

---

## 4. Required rework plan (do in this order; don't skip ahead)

Follow `PLAN.md` milestones strictly. For each milestone, **append a section to `VERIFICATION.md`** with the exact command(s) and trimmed real output proving its check passed. Do not edit `PLAN.md`.

1. **M1 fix-up:** fix packaging/imports (3.1.1), delete stray files, make `uv run ruff check .` report **0 errors**, add `cli.py` with all §12 subcommands (stubs may raise `NotImplementedError` at first), add one smoke test, and make the **first git commit**.
2. **M2:** run `slo fetch --source all` for real. `runs` has 5 rows with sha256/bytes, and `data/raw/<src>/<today>.*` exists.
3. **M3:** write the 4 missing parsers + `normalise/` + the EU namespace fix. **Gate:** parsed counts vs source counts printed per source within ±1% (expected roughly: EU ~6.3k, UN ~1.0k, OFAC ~19.6k, UK ~6.4k), plus parser and normalise tests (≥15 DOB cases).
4. **M4–M6:** DQ rules (all §6 IDs, each with a +/− test), fixes as `*_clean` views, export gate, crosslist, delta (fixture test with exact counts), 3 exports + manifests, requests log + four-eyes test.
5. **M7:** rewrite `synth.py` per §10.1 (planted hits come from list entities), add `evaluate.py`, produce `metrics.json` + 3 charts from real data. Record the runtime.
6. **M8:** `report.py --update-readme`, a full README per §11.3 with numbers from `summary.json`, and dashboard pages reading only committed artefacts (test by temporarily moving `data/raw/` away).
7. **M9:** push to GitHub, get `ci.yml` green, run `daily.yml` via `workflow_dispatch`, and link both run URLs in `VERIFICATION.md`.

## 5. Re-review checklist (what the reviewer will run)

```bash
git clone <repo> /tmp/slo && cd /tmp/slo
uv sync && make lint test demo          # all green, coverage ≥ 80% printed
uv run slo all                          # live data, < ~10 min
uv run python -c "import duckdb;c=duckdb.connect('data/slo.duckdb');print(c.sql('select source,count(*) from entities group by 1'))"
cat data/reports/$(date +%F)/summary.json | jq '.screening'
head -60 README.md                      # findings with real numbers
make dash                               # 6 pages populated
```
Sign-off happens only when every §17 box is backed by an entry in `VERIFICATION.md`.

## 6. What's good (keep it)
- The repo layout and config files (`sources.yaml`, `dq_rules.yaml`, `screening.yaml`) follow the plan.
- The fixtures for all 5 sources exist and look like the real structures. Reuse them.
- `docs/canonical_schema.md` and `docs/what_id_do_at_a_bank.md` are reasonable starting drafts.
- `models.py` / `store.py` DDL broadly match §4.
