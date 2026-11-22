# Data-quality rules

Configured in `config/dq_rules.yaml` (severity, auto-fix, enabled) and implemented in `src/slo/dq/rules.py` (single-list) and `src/slo/crosslist.py` (DQ050–DQ052). Every rule has a positive and a negative test in `tests/test_dq.py`.

| id | severity | auto-fix | title | logic |
|---|---|---|---|---|
| DQ001 | high | no | Entity has no primary name | `primary_name` empty or NULL. |
| DQ002 | medium | yes | Name has leading/trailing/double whitespace | `full_name` differs from its whitespace-collapsed form. Fix: collapse whitespace. |
| DQ003 | medium | yes | Non-Latin name without any Latin-script name on the entity | Entity has non-Latin names (Arabic, Cyrillic, CJK, other) and no Latin-script name. Fix: add a transliterated alias (`generated = true`, weak). |
| DQ004 | low | yes | Empty alias element | Name element present but empty. Fix: drop it. |
| DQ005 | medium | yes | Duplicate names within an entity after normalisation | Two names on one entity with the same normalised key in the same script. Fix: keep the primary, then the strong one. |
| DQ010 | high | no | Person has no date of birth | Person without any DOB row. |
| DQ011 | medium | no | Person has no full (day-precision) date of birth | Person whose DOBs are all year, month or range precision (e.g. UK `dd/mm/1965`). |
| DQ012 | low | yes | Consecutive single-year DOB rows that should be one range | Two or more single-year DOB rows in consecutive years. Fix: collapse to a range (`1945 to 1958`). |
| DQ013 | high | no | Date of birth cannot be parsed | DOB text matches no supported format (e.g. `15/08/19yy`). |
| DQ014 | high | no | Date of birth in the future or before 1900 | DOB before 1900 or after today (e.g. Solar Hijri `1340`). |
| DQ020 | medium | no | Country code unknown ('00') or country name not mappable to ISO 3166 | Country value that maps to no ISO 3166-1 code (`00`, `USSR`, a city). |
| DQ021 | low | no | Person has no nationality or citizenship | Person without a mappable nationality or citizenship. |
| DQ030 | medium | yes | Identifier number contains whitespace/punctuation noise | Identifier contains whitespace or `-./`. Fix: normalised number. |
| DQ031 | medium | yes | Same identifier repeated within one entity | Same `(id_type, number)` repeated on one entity. Fix: de-duplicate. |
| DQ032 | high | no | Same identifier on different entities in the same list (possible duplicate) | Same `(id_type, number, country)` on two entities of one list (length ≥ 5). |
| DQ033 | low | no | Passport/national ID without issuing country | Passport or national ID without issuing country. |
| DQ040 | high | no | Possible duplicate entity: same name key, same type, overlapping DOB | Two entities of the same type with the same sorted name key; persons also need overlapping DOBs. |
| DQ050 | high | no | UN-listed entity not found on this list (transposition gap) | UN entity with no EU (or UK) counterpart by UN reference or by name + DOB. Message gives days since UN listing. |
| DQ051 | medium | no | Same UN reference but names or DOBs disagree between lists | EU/UK record and UN record share a UN reference but no name matches. |
| DQ052 | low | no | List cites a UN reference that is not on the current UN list | EU/UK record cites a UN reference not on the current UN list. |
| DQ060 | high | no | Record count mismatch vs source, parse failures, or >5% swing vs previous snapshot | Parsed count ≠ count declared by the source (OFAC `Record_Count`) or counted in the file, parse failures, or > 5% change in entity count vs the previous snapshot. **Blocks export.** |
| DQ061 | high | no | File unchanged but publish date changed (or the reverse) | Raw file sha256 unchanged but publish date changed, or the reverse. **Blocks export.** |

## DQ score

`dq_score = 100 − 100 × (10·H + 3·M + 1·L) / (10 × records)`, where H, M and L count the **entities with an open (unfixed) issue** of that severity. A list where every record has one open high-severity issue scores 0. Counting entities rather than issue rows stops one messy record from dominating the score. `PLAN.md` §6 sketched a formula based on issue counts. On the 2026-11-22 data that formula put every list between 0 and 3 (EU, UN and UK all at 0), so it could not tell them apart. This deviation is deliberate.

## Export gate

`slo export` refuses any source with an open DQ060 or DQ061 issue, or a failed run. The manifest lists the blocked sources and the reasons, and the command exits with status 1.

