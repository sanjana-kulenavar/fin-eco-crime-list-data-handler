# Source notes: quirks observed in the live files

Every example below was copied from the files downloaded on **2026-11-22** (`slo fetch`). The keys are the
canonical `entity_key` (`<source>:<id from the file>`), so each one can be re-checked in `data/slo.duckdb`
after a run. Raw file fingerprints for that run are in `data/reports/2026-11-22/runs.csv`.

| # | List | Quirk | Real example | How the pipeline handles it |
|---|---|---|---|---|
| 1 | EU | Unknown country published as code `00` / `UNKNOWN` (1,756 country fields) | `eu:20` citizenship `countryIso2Code="00"` | Mapped to `NULL`, reported as **DQ020** |
| 2 | EU | Only 87 of 6,303 records carry the UN reference (`unitedNationId`) | `eu:13` (Saddam Hussein Al-Tikriti) has `unitedNationId=""`; the UN lists him as `IQi.001` | UN→EU linking falls back to name + DOB (`crosslist.py`) |
| 3 | EU | Every `nameAlias` has `strong="true"`, so the EU file has no weak-alias signal | 31,265 of 31,265 names | Strength kept as published; payments export cannot drop weak EU aliases |
| 4 | EU | Double spaces inside names (2,149 names) | `eu:640` `"Mehmood,  Bashir Uddin"`, `eu:750` `"Salem  al-Sharif"` | **DQ002**, auto-fixed in `names_clean` |
| 5 | EU | Solar Hijri (Persian calendar) years used as birth years | `eu:127411` DOB `1340`, `eu:127388` `1343` | Year < 1900 → precision `invalid`, **DQ014** |
| 6 | EU | Some EU records cite UN references without zero padding | EU cites `LYI.28`, the UN list has `LYi.028` | References normalised to `PREFIX.NNN` before comparing |
| 7 | UN | Empty alias elements `<INDIVIDUAL_ALIAS><QUALITY/><ALIAS_NAME/></INDIVIDUAL_ALIAS>` (292) | `un:6907993` (ERIC BADEGE) | Kept as an empty name, **DQ004**, dropped in `names_clean` |
| 8 | UN | Trailing whitespace inside document numbers | `un:6908994` passport `"AB187304 "`, `un:6909613` `"DP0000149 "` | **DQ030**, normalised to `AB187304` |
| 9 | UN | Document type contains an embedded newline and indentation | `TYPE_OF_DOCUMENT` = `"National Identification\n            Number"` | Whitespace collapsed before mapping to `national_id` |
| 10 | UN | Countries are names, not codes | `"Democratic Republic of the Congo"` | Mapped with `pycountry` + an override table → `CD` |
| 11 | UN | `QUALITY` mixes alias strength with alias type: `Good`, `Low`, `a.k.a.`, `f.k.a.`, empty | `un:6908048` | `Low`/`f.k.a.` → weak, `f.k.a.` → `former` |
| 12 | OFAC | `<idList>` holds metadata as well as identifiers (`Gender`, `Website`, `Secondary sanctions risk:`, `Organization Established Date`) | 12,667 rows are `Secondary sanctions risk:` | Only real identifier types are kept (`normalise/ids.py`) |
| 13 | OFAC | Free-text DOBs: `circa 1951`, `1955 to 1957`, `01 Jan 1973 to 31 Dec 1973`, `09 Feb 1951` | `ofac:7782` `circa 1951`, `ofac:8868` `1955 to 1957` | Explicit per-format parser, circa widened ±1 year |
| 14 | OFAC | Place of birth is free text and often has no country | `ofac:7204` `"Tripoli"`, `ofac:7265` `"Khartoum Sudan"` (no comma) | Last comma part, else last word; unresolved → **DQ020** |
| 15 | UK | Birth dates published with literal placeholders, plus one row per possible year | `uk:AFG0006` `dd/mm/1945`, `dd/mm/1946`, … `dd/mm/1958` | Precision `year` (**DQ011**); consecutive years collapsed to a range (**DQ012**) |
| 16 | UK | Malformed DOB | `uk:RUS2701` `15/08/19yy` | **DQ013** (unparseable) |
| 17 | UK | `NameType` spelled several ways: `Primary Name`, `Primary name`, `Primary Name Variation`, `Primary name variation`, `ALias` | `ALias` on the record whose surname is `al-Samarrai` | Case-insensitive mapping; exactly one primary name per designation |
| 18 | UK | The same identifier repeated inside one designation | `uk:RUS2323` IMO `IMO9250531` ×2, `uk:CAF0014` passport `06FBO2262` ×2 | **DQ031**, de-duplicated in `identifiers_clean` |
| 19 | UK | Historical countries | `uk:RUS0017` `"Kazakh SSR"`, `uk:GHR0055` `"Former USSR"`, `"Ukrainian SSR (now Ukraine)"` | `(now X)` / `currently X` resolved; the rest → **DQ020** |
| 20 | UK | Empty `Name` elements | `uk:BEL0033`, `uk:BEL0038` | **DQ004** |
| 21 | NL | The official list is a PDF/ODS; OpenSanctions republishes it as FollowTheMoney NDJSON where the listing date and programme live on a separate `Sanction` entity | `nl:NK-ZUY6Az9Jx9AhuP7q3ADSu2` (Abdelhakim Boukich) ← `Sanction` record | Parser joins `Sanction.entity` → target to get `listed_on`/programme |
| 22 | NL | No nationality published for any person | 106 of 106 persons | Reported as **DQ021** (low) |

## Cross-list observations (same run)

- **DQ051** `eu:112314` shares UN reference `KPI.045` with `un:6908585`, but no name matches after normalisation.
- **DQ052** `eu:110845` cites `QDI.399`, which is not on the current UN list (delisted or never existed).
- **DQ040** `eu:164300` and `eu:165712` share the name key `kirill nikiforov sergejevic` and have overlapping DOBs (possible duplicate).
- **DQ032** `eu:174276` and `eu:172861` carry the same identifier `100386629`.

## Record counts (2026-11-22)

| Source | Published | Entities in file | Parsed | Raw bytes |
|---|---|---:|---:|---:|
| EU | 2026-11-20 | 6,303 | 6,303 | 26,066,969 |
| UN | 2026-11-22 | 1,007 | 1,007 | 2,180,873 |
| OFAC | 2026-11-20 | 19,599 (declared `Record_Count`) | 19,599 | 29,614,437 |
| UK | 2026-11-20 | 6,448 | 6,448 | 22,198,502 |
| NL | 2026-11-22 | 109 | 109 | 152,266 |
