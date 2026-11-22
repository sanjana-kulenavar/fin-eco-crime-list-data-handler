# Canonical schema

All sources are mapped into these DuckDB tables (`src/slo/store.py`, `src/slo/build.py`). Every row carries `source` and `snapshot_date`; re-running a snapshot replaces it (idempotent).

## `entities`

One row per listed party per snapshot. `entity_key = source:source_id` is stable across snapshots; `record_hash` (sha256 of the entity and all its child rows) drives the delta.

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `source_id` | String |
| `entity_key` | String |
| `entity_type` | String |
| `primary_name` | String |
| `programmes` | List(String) |
| `listed_on` | Date |
| `last_updated` | Date |
| `un_reference` | String |
| `gender` | String |
| `remarks` | String |
| `record_hash` | String |

## `names`

Every published name. `full_name` is exactly as published; `norm_name` is the matching key (NFKC, transliterated, lower-case, punctuation removed, company suffixes canonical); `norm_tokens_sorted` makes it order-independent. `generated` marks names added by fixes or approved requests (clean table only).

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `entity_key` | String |
| `name_id` | String |
| `full_name` | String |
| `first_name` | String |
| `middle_name` | String |
| `last_name` | String |
| `name_type` | String |
| `strength` | String |
| `script` | String |
| `language` | String |
| `norm_name` | String |
| `norm_tokens_sorted` | String |
| `generated` | Boolean |

## `birth_dates`

`raw` as published; `date_from`/`date_to` give the possible range; `precision` is day, month, year, range, unknown, unparseable or invalid; `circa` widens the range by one year each side.

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `entity_key` | String |
| `raw` | String |
| `date_from` | Date |
| `date_to` | Date |
| `precision` | String |
| `circa` | Boolean |

## `identifiers`

`id_type` is one of passport, national_id, tax_id, company_reg, imo, swift_bic, other. `norm_number` has whitespace and `-./` removed and is upper-cased.

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `entity_key` | String |
| `id_type` | String |
| `raw_number` | String |
| `norm_number` | String |
| `country_iso2` | String |
| `notes` | String |

## `countries`

`role` is nationality, citizenship, birth or registration. `country_iso2` is NULL when the published value is unknown (`00`) or historical (DQ020).

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `entity_key` | String |
| `role` | String |
| `raw` | String |
| `country_iso2` | String |

## `addresses`

Free-text address; only the country is normalised.

| column | type |
|---|---|
| `source` | String |
| `snapshot_date` | Date |
| `entity_key` | String |
| `line` | String |
| `city` | String |
| `postal_code` | String |
| `country_iso2` | String |
| `raw` | String |

## Clean tables

`names_clean`, `birth_dates_clean` and `identifiers_clean` have the same columns as their raw counterparts. They are rebuilt by `slo fix` from the raw tables plus approved change requests. Raw tables are never modified.

## Operational tables

| table | purpose |
|---|---|
| `runs` | one row per source per run: raw path, sha256, bytes, publisher date, declared and parsed counts, status, error |
| `dq_issues` | one row per finding: rule id, severity, entity, field, value, message, `auto_fixable`, `fixed` |
| `fix_log` | every automatic fix or request applied: rule id (or `REQ:<id>`), before, after, timestamp |
| `deltas` | added / removed / amended entities between consecutive snapshots, with `changed_fields` |
| `crosslinks` | each UN entity with its EU / UK / OFAC counterpart and how it was linked (`un_ref` or `name_dob`) |

