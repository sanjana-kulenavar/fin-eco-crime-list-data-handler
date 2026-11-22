# Verification

Every block below was produced by running the shown command in this repository on **2026-11-22** (macOS, Python 3.12, uv).
Outputs are pasted, not retyped. The final commit hash is listed at the bottom; re-run `make lint test demo all` to reproduce.

## 1. Lint
```
$ uv run ruff check . && uv run ruff format --check .
All checks passed!
62 files already formatted
exit=0
```

## 2. Fixtures are valid and come from the live files
`tests/fixtures/*` were produced by `scripts/make_fixtures.py` from the 2026-11-22 downloads (real records, unchanged).
```
$ xmllint --noout tests/fixtures/*.xml && NDJSON check
8 NDJSON records OK
exit=0
```

## 3. Tests and coverage (gate: 80%; `cli.py` excluded per PLAN §14, but smoke-tested in `tests/test_cli.py`)
```
$ uv run pytest --cov=slo --cov-report=term --cov-fail-under=80
........................................................................ [ 51%]
.....................................................................    [100%]
================================ tests coverage ================================
______________ coverage: platform darwin, python 3.12.15-final-0 _______________

Name                             Stmts   Miss  Cover   Missing
--------------------------------------------------------------
src/slo/charts.py                  104      3    97%   115, 150-159
src/slo/config.py                   51      1    98%   83
src/slo/delta.py                    61      1    98%   36
src/slo/dq/engine.py                69      2    97%   86, 185
src/slo/dq/fixes.py                127      1    99%   85
src/slo/dq/rules.py                111      1    99%   403
src/slo/export.py                   92      1    99%   204
src/slo/normalise/countries.py      43      1    98%   158
src/slo/normalise/dates.py         101      7    93%   70, 74-77, 91, 97
src/slo/normalise/names.py          57      2    96%   63-64
src/slo/parsers/_xml.py             28      1    96%   24
src/slo/parsers/eu.py               63      5    92%   44, 46, 49, 140-141
src/slo/parsers/nl_ftm.py           58      3    95%   66, 87-88
src/slo/parsers/ofac.py             67      2    97%   141-142
src/slo/parsers/uk.py               70      7    90%   76-77, 83-84, 96, 144-145
src/slo/parsers/un.py               61      2    97%   130-131
src/slo/pipeline.py                165      4    98%   89, 280-282
src/slo/report.py                  102      1    99%   274
src/slo/screening/synth.py         177      5    97%   148, 174, 203, 247, 277
--------------------------------------------------------------
TOTAL                             2270     50    98%

15 files skipped due to complete coverage.
Required test coverage of 80% reached. Total coverage: 97.80%
141 passed in 8.47s
exit=0
```

## 4. Offline demo (no network, fixtures only)
```
$ rm -rf data/demo && uv run slo offline-demo; echo exit=$?
fetch     eu             85,154 bytes  sha256=cbe229ecafdf
normalise eu       status=ok parsed=5 declared/elements=5 names=154
fetch     un             15,011 bytes  sha256=9f328f5fcc8e
normalise un       status=ok parsed=6 declared/elements=6 names=33
fetch     ofac            9,453 bytes  sha256=e2340efb686c
normalise ofac     status=ok parsed=6 declared/elements=6 names=24
fetch     uk             19,216 bytes  sha256=980fad585bd2
normalise uk       status=ok parsed=6 declared/elements=6 names=21
fetch     nl              6,041 bytes  sha256=fab46b6ff2b2
normalise nl       status=ok parsed=4 declared/elements=4 names=4
dq        eu            17 issues
dq        un             6 issues
dq        ofac           5 issues
dq        uk             6 issues
dq        nl             3 issues
dq        internal       7 issues
crosslist UN->EU 1/6, UN->UK 1/6
fix       eu            17 fixes
fix       un             3 fixes
fix       ofac           1 fixes
fix       uk             4 fixes
fix       nl             0 fixes
fix       internal       2 fixes
delta     eu       {'added': 5, 'removed': 0, 'amended': 0} (initial load)
delta     un       {'added': 6, 'removed': 0, 'amended': 0} (initial load)
delta     ofac     {'added': 6, 'removed': 0, 'amended': 0} (initial load)
delta     uk       {'added': 6, 'removed': 0, 'amended': 0} (initial load)
delta     nl       {'added': 4, 'removed': 0, 'amended': 0} (initial load)
delta     internal {'added': 10, 'removed': 0, 'amended': 0} (initial load)
export    payments_screening       198 records  gate_passed=True  blocked=[]
export    customer_screening        37 records  gate_passed=True  blocked=[]
export    delta_feed                37 records  gate_passed=True  blocked=[]
screen    raw    recall=1.0 fp/1k=0.5 (0.0s)
screen    clean  recall=1.0 fp/1k=0.5 (0.0s)
report    data/demo/reports/2026-11-22  (0.9s total)
offline demo OK -> data/demo/reports/2026-11-22/summary.json
real 1.50
user 1.43
sys 0.69
exit=0
```

## 5. Live pipeline: `uv run slo all` (real downloads)
```
$ rm -f data/slo.duckdb && uv run slo all
fetch     eu         26,066,969 bytes  sha256=3eea03e34a44
normalise eu       status=ok parsed=6303 declared/elements=6303 names=31265
fetch     un          2,180,873 bytes  sha256=1f72a7b1a827
normalise un       status=ok parsed=1007 declared/elements=1007 names=4429
fetch     ofac       29,614,437 bytes  sha256=8a8e2c8b2fd2
normalise ofac     status=ok parsed=19599 declared/elements=19599 names=44294
fetch     uk         22,198,502 bytes  sha256=6b558b1981c2
normalise uk       status=ok parsed=6448 declared/elements=6448 names=19826
fetch     nl            152,266 bytes  sha256=0de96a36dfd4
normalise nl       status=ok parsed=109 declared/elements=109 names=112
dq        eu         8,444 issues
dq        un         1,118 issues
dq        ofac       7,384 issues
dq        uk         5,177 issues
dq        nl           106 issues
dq        internal       7 issues
crosslist UN->EU 944/1007, UN->UK 1006/1007
fix       eu         3,274 fixes
fix       un           544 fixes
fix       ofac       4,042 fixes
fix       uk         1,557 fixes
fix       nl             0 fixes
fix       internal       2 fixes
delta     eu       {'added': 6303, 'removed': 0, 'amended': 0} (initial load)
delta     un       {'added': 1007, 'removed': 0, 'amended': 0} (initial load)
delta     ofac     {'added': 19599, 'removed': 0, 'amended': 0} (initial load)
delta     uk       {'added': 6448, 'removed': 0, 'amended': 0} (initial load)
delta     nl       {'added': 109, 'removed': 0, 'amended': 0} (initial load)
delta     internal {'added': 10, 'removed': 0, 'amended': 0} (initial load)
export    payments_screening    81,353 records  gate_passed=True  blocked=[]
export    customer_screening    33,476 records  gate_passed=True  blocked=[]
export    delta_feed            33,476 records  gate_passed=True  blocked=[]
screen    raw    recall=0.96 fp/1k=46.25 (3.9s)
screen    clean  recall=0.9633 fp/1k=57.95 (4.8s)
report    data/reports/2026-11-22  (25.2s total)
exit=0
```

## 6. Database contents (demo DB, then live DB)
```
$ duckdb data/demo/slo.duckdb  (count query)
┌─────────────┬──────────┬───────┐
│      t      │  source  │   n   │
│   varchar   │ varchar  │ int64 │
├─────────────┼──────────┼───────┤
│ birth_dates │ eu       │    14 │
│ birth_dates │ internal │     4 │
│ birth_dates │ nl       │     3 │
│ birth_dates │ ofac     │     4 │
│ birth_dates │ uk       │     6 │
│ birth_dates │ un       │     6 │
│ dq_issues   │ eu       │    22 │
│ dq_issues   │ internal │     7 │
│ dq_issues   │ nl       │     3 │
│ dq_issues   │ ofac     │     5 │
│     ·       │ ·        │     · │
│     ·       │ ·        │     · │
│     ·       │ ·        │     · │
│ identifiers │ eu       │     1 │
│ identifiers │ ofac     │     3 │
│ identifiers │ uk       │     3 │
│ identifiers │ un       │     2 │
│ names       │ eu       │   154 │
│ names       │ internal │    17 │
│ names       │ nl       │     4 │
│ names       │ ofac     │    24 │
│ names       │ uk       │    21 │
│ names       │ un       │    33 │
└─────────────┴──────────┴───────┘
  28 rows (20 shown)   3 columns

┌──────────┬─────────┬────────────────┬─────────────────────┬─────────────────────┬───────────┬──────────────────┐
│  source  │ status  │ published_date │ record_count_source │ record_count_parsed │ raw_bytes │      sha256      │
│ varchar  │ varchar │      date      │        int32        │        int32        │   int64   │     varchar      │
├──────────┼─────────┼────────────────┼─────────────────────┼─────────────────────┼───────────┼──────────────────┤
│ eu       │ ok      │ 2026-11-20     │                   5 │                   5 │     85154 │ cbe229ecafdff87e │
│ internal │ ok      │ NULL           │                  10 │                  10 │      1757 │ 11da2cf86f7e6a6b │
│ nl       │ ok      │ 2026-11-22     │                   4 │                   4 │      6041 │ fab46b6ff2b2a902 │
│ ofac     │ ok      │ 2026-11-20     │                   6 │                   6 │      9453 │ e2340efb686ce80b │
│ uk       │ ok      │ 2026-11-20     │                   6 │                   6 │     19216 │ 980fad585bd226ee │
│ un       │ ok      │ 2026-11-22     │                   6 │                   6 │     15011 │ 9f328f5fcc8e0772 │
└──────────┴─────────┴────────────────┴─────────────────────┴─────────────────────┴───────────┴──────────────────┘

$ duckdb data/slo.duckdb  (count query)
┌─────────────┬──────────┬───────┐
│      t      │  source  │   n   │
│   varchar   │ varchar  │ int64 │
├─────────────┼──────────┼───────┤
│ birth_dates │ eu       │  4228 │
│ birth_dates │ internal │     4 │
│ birth_dates │ nl       │   106 │
│ birth_dates │ ofac     │  8249 │
│ birth_dates │ uk       │  3812 │
│ birth_dates │ un       │   890 │
│ dq_issues   │ eu       │  8512 │
│ dq_issues   │ internal │     7 │
│ dq_issues   │ nl       │   106 │
│ dq_issues   │ ofac     │  7384 │
│     ·       │ ·        │    ·  │
│     ·       │ ·        │    ·  │
│     ·       │ ·        │    ·  │
│ identifiers │ eu       │  3106 │
│ identifiers │ ofac     │ 19060 │
│ identifiers │ uk       │  2709 │
│ identifiers │ un       │   502 │
│ names       │ eu       │ 31265 │
│ names       │ internal │    17 │
│ names       │ nl       │   112 │
│ names       │ ofac     │ 44294 │
│ names       │ uk       │ 19826 │
│ names       │ un       │  4429 │
└─────────────┴──────────┴───────┘
  28 rows (20 shown)   3 columns

┌──────────┬─────────┬────────────────┬─────────────────────┬─────────────────────┬───────────┬──────────────────┐
│  source  │ status  │ published_date │ record_count_source │ record_count_parsed │ raw_bytes │      sha256      │
│ varchar  │ varchar │      date      │        int32        │        int32        │   int64   │     varchar      │
├──────────┼─────────┼────────────────┼─────────────────────┼─────────────────────┼───────────┼──────────────────┤
│ eu       │ ok      │ 2026-11-20     │                6303 │                6303 │  26066969 │ 3eea03e34a447659 │
│ internal │ ok      │ NULL           │                  10 │                  10 │      1757 │ 11da2cf86f7e6a6b │
│ nl       │ ok      │ 2026-11-22     │                 109 │                 109 │    152266 │ 0de96a36dfd40088 │
│ ofac     │ ok      │ 2026-11-20     │               19599 │               19599 │  29614437 │ 8a8e2c8b2fd2d56d │
│ uk       │ ok      │ 2026-11-20     │                6448 │                6448 │  22198502 │ 6b558b1981c232ef │
│ un       │ ok      │ 2026-11-22     │                1007 │                1007 │   2180873 │ 1f72a7b1a8275d88 │
└──────────┴─────────┴────────────────┴─────────────────────┴─────────────────────┴───────────┴──────────────────┘
```

## 7. CLI surface
```
$ uv run slo --help
                                                                                
 Usage: slo [OPTIONS] COMMAND [ARGS]...                                         
                                                                                
 sanctions-list-ops: a mini sanctions-list management pipeline.                 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help        <bool>  Show this message and exit.                            │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ version       Show the version.                                              │
│ fetch         Download raw list files into data/raw/<source>/<date>.*        │
│ normalise     Parse already-fetched raw files and load the canonical tables. │
│ dq            Run data-quality rules and print the scorecard.                │
│ crosslist     Link UN entities to EU/UK/OFAC and run DQ050-DQ052.            │
│ fix           Build the *_clean tables (auto-fixes + approved requests) and  │
│               log every change.                                              │
│ delta         Compute adds/removes/amendments against the previous snapshot. │
│ export        Write screening-system exports. Sources failing the DQ gate    │
│               are blocked (exit 1).                                          │
│ screen        Run the raw-vs-clean screening experiment.                     │
│ report        Regenerate summary.json/md, charts and (optionally) the README │
│               findings block.                                                │
│ all           Full live pipeline: fetch -> normalise -> dq -> crosslist ->   │
│               fix -> delta -> export -> screen -> report.                    │
│ offline-demo  Run the whole pipeline on tests/fixtures with no network       │
│               access.                                                        │
│ request       List change requests (maker/checker).                          │
╰──────────────────────────────────────────────────────────────────────────────╯

$ uv run slo request --help
                                                                                
 Usage: slo request [OPTIONS] COMMAND [ARGS]...                                 
                                                                                
 List change requests (maker/checker).                                          
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help        <bool>  Show this message and exit.                            │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ list                                                                         │
│ add                                                                          │
│ approve                                                                      │
│ reject                                                                       │
│ apply                                                                        │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## 8. Export gate demo: OFAC declares 7 records, the file has 6
```
$ cp -R tests/fixtures /tmp/fx && sed -i "" "s#<Record_Count>6<#<Record_Count>7<#" /tmp/fx/ofac.xml
$ uv run slo offline-demo --fixtures-dir /tmp/fx --data-dir /tmp/demo
fetch     ofac            9,453 bytes  sha256=7d0cd504aab9
normalise ofac     status=ok parsed=6 declared/elements=7 names=24
dq        ofac           6 issues
fix       ofac           1 fixes
delta     ofac     {'added': 6, 'removed': 0, 'amended': 0} (initial load)
export    payments_screening       176 records  gate_passed=False  blocked=['ofac']
export    customer_screening        31 records  gate_passed=False  blocked=['ofac']
export    delta_feed                31 records  gate_passed=False  blocked=['ofac']
export gate blocked: ['ofac'] (see dq_issues DQ060/DQ061)
exit=1
```

## 9. Workflows and leftover stubs
```
$ actionlint .github/workflows/*.yml
exit=0
$ grep -rn "NotImplementedError\|stub\|TODO" src/ app/
matches_exit=1 (1 = none found)
```

## PLAN §17 checklist

| §17 item | Status | Evidence |
|---|---|---|
| `uv sync && make lint test demo` passes | ✅ locally (macOS) | §1, §3, §4 |
| `ci.yml` green on `main` in GitHub Actions | ⏳ not yet | needs a GitHub remote; see *Not done yet* |
| Coverage ≥ 80% printed in CI | ✅ locally (97.8%) | §3; `make test` uses `--cov-fail-under=80` |
| `slo offline-demo` < 30 s, writes `summary.json` | ✅ 1.5 s | §4 |
| No raw files / `.duckdb` tracked | ✅ | `.gitignore`; `git ls-files data/raw '*.duckdb'` output at the bottom |
| `slo all` live for 5 sources, all `status=ok` | ✅ | §5, §6 `runs` table |
| Parsed counts = source counts (asserted by DQ060) | ✅ exact for all 5 | §6 `record_count_source` = `record_count_parsed`; DQ060 tests in `tests/test_dq.py` |
| All canonical tables + `docs/canonical_schema.md` | ✅ | `src/slo/build.py` `SCHEMAS`; docs generated from it |
| `docs/source_notes.md` ≥ 8 live quirks with examples | ✅ 22 quirks | each with a real `entity_key` from the 2026-11-22 files |
| All DQ rules configurable, documented, ± tests | ✅ 22 rules | `config/dq_rules.yaml`, `docs/dq_rules.md`, `tests/test_dq.py` (`test_every_configured_rule_is_implemented`) |
| `dq_scorecard.json` per source | ✅ | `data/reports/2026-11-22/dq_scorecard.json` |
| Fixes → `*_clean` + `fix_log`, raw unchanged (hash test) | ✅ | `tests/test_fixes.py::test_fixes_build_clean_tables_and_leave_raw_untouched` |
| Export gate blocks on DQ060 (test + demo command) | ✅ | `tests/test_export.py::test_exports_and_gate`, `tests/test_dq.py::test_dq060_count_mismatch_and_gate`; §8 (real run, exit 1), `tests/test_cli.py::test_export_gate_blocks_offline_demo` |
| Cross-list overlaps + UN→EU/UK gaps in `summary.md` | ✅ | README findings; `data/reports/2026-11-22/crosslist.json` |
| `data/deltas/<source>/<date>.json` + exact-count delta test | ✅ | `data/deltas/*/2026-11-22.json` (initial load); `tests/test_delta.py` |
| 3 exports with manifests + 50-row samples | ✅ | `data/exports/samples/` |
| ≥ 5 requests, all types; maker = checker fails; apply → data + `fix_log` | ✅ 6 requests | `requests/requests.csv`; `tests/test_requests.py`, `tests/test_fixes.py::test_requests_applied_in_clean_tables`, `tests/test_cli.py` |
| 20k customers + 300 hits, deterministic (hash test) | ✅ | `tests/test_screening.py::test_generate_is_deterministic_and_plants_hits`; sha256 in `metrics.json` |
| A and B < 5 min | ✅ raw 3.9 s, clean 4.8 s | `metrics.json` `runtime_seconds` |
| `metrics.json` with recall/precision/alerts/FP/1k/95% point, per perturbation | ✅ | `data/reports/2026-11-22/screening/metrics.json` |
| 3 charts embedded in README | ✅ | README findings block |
| A vs B reported as measured, incl. trade-off | ✅ | README: +0.3 pt recall, +11.7 FP/1k at t=85; fewer FPs once re-tuned to ≥95% recall |
| README findings auto-filled between markers | ✅ | `slo all` / `slo report --update-readme` rewrite the block |
| Dashboard: 6 pages from committed artefacts only, works without `data/raw/` | ✅ | `tests/test_dashboard.py` (Streamlit AppTest, no raw/DB) + same check on live artefacts |
| `docs/what_id_do_at_a_bank.md` | ✅ | written as what I *would* do; no claimed bank experience |
| `daily.yml` ran via `workflow_dispatch` and committed | ⏳ not yet | needs a GitHub remote; `actionlint` passes (§9) |
| No secrets / real customer data / paid data; OpenSanctions attribution | ✅ | customers synthetic, internal list fictional, attribution in README |

## Not done yet

- **GitHub Actions runs.** The repository has no remote yet, so neither `ci.yml` nor `daily.yml` has run on GitHub.
  Both pass `actionlint`, and every command they run passes locally (above). To finish: create the GitHub repository,
  push `main`, confirm CI is green, then start *Daily list update* from the Actions tab (`workflow_dispatch`).
- **Daily deltas.** Only the initial load exists (2026-11-22). Real add/remove/amend deltas appear from the second
  daily run onward. The delta logic is covered by `tests/test_delta.py`.

## Deliberate deviations from PLAN.md

- **DQ score** counts *entities with open issues* by severity, not issue rows. The plan's formula scored every list
  0–3 on real data and could not tell them apart (`docs/dq_rules.md`).
- **≥95% recall operating point** is the *strictest* threshold that still reaches 95% recall. That is the useful
  tuning point; the plan said "lowest".
- **Fixtures** are real records copied from the live files (generator committed). They are not hand-written. Each
  quirk is a real occurrence; DQ014 and DQ003 are covered by synthetic cases in `tests/test_dq.py`.

## Fresh-clone check and tracked files (commit `7dad6d0`)
```
$ git clone <repo> clone && cd clone && uv sync && make lint && make test && make demo
uv run ruff check .
All checks passed!
uv run ruff format --check .
62 files already formatted
Required test coverage of 80% reached. Total coverage: 97.80%
141 passed in 15.03s
offline demo OK -> clone/data/demo/reports/2026-11-22/summary.json
fresh_clone_exit=0

$ git ls-files data/raw '*.duckdb' | wc -l
0
```
