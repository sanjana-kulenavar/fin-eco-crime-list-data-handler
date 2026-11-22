# What I'd do differently at a bank

This project uses only public lists and synthetic customers. Here is how I would expect the same work to look in a
bank's list-management team, and where this prototype deliberately stops short.

**Official and vendor lists.** Official lists (EU, UN, OFAC, UK, and national lists such as the Dutch terrorism
list) are free and authoritative, but each has its own schema and quirks (see `docs/source_notes.md`). Most banks
screen against a vendor feed such as Dow Jones Risk & Compliance, LSEG World-Check or LexisNexis Bridger. Those feeds
aggregate the official lists, add PEP, adverse-media and ownership data, and deliver one format. The vendor feed does
not remove the need for list data handling: you still check it against the official sources (did the 63 UN entities
this run could not find on the EU list arrive in the vendor feed, and when?), manage the bank's internal list, and own
what is loaded into each screening engine.

**Golden source and release controls.** I would treat each list load as a release. The steps are: fingerprint the
file (sha256 and publisher date), check the record count against the publisher's count, run DQ rules, and block the
load on failure. That is what DQ060/DQ061 and the export gate do here. A real setup adds an explicit approval step for
the release, retention of every loaded file for audit, and a one-command rollback to the previous snapshot. This
project keeps every snapshot in DuckDB, so a rollback would be re-exporting yesterday's snapshot.

**Delta vs full loads.** Screening engines usually take daily deltas, plus periodic full reloads to correct drift.
Deltas are computed from a record hash over the entity and all its child rows, so an alias change counts as an
amendment and is not silently dropped. Full loads are worth reconciling against the engine's own counts.

**Four-eyes and record keeping.** Every manual change goes through `requests/requests.csv` with a maker and a
different checker. The code enforces this and refuses self-approval. Changes are applied by code, not by hand, and
every change lands in `fix_log` with before and after values. In a bank this would live in a case or workflow tool
with access control, but the principle is the same: no change to what screening sees without a ticket, a second
person, and a trail.

**Regulatory context (Netherlands).** EU sanctions regulations apply directly in all member states. The Dutch
Sanctions Act 1977 (Sanctiewet 1977) and its regulations provide the national framework, enforcement and the
national terrorism list. De Nederlandsche Bank supervises banks' compliance. UN listings take effect for EU
institutions once the EU has transposed them, so the UN→EU gap measured here is something a list team monitors
rather than a compliance failure by itself.

**Tuning governance.** The screening experiment shows why list changes and threshold changes have to be assessed
together. In the 2026-11-22 run, cleaning the lists barely moved recall at a fixed threshold but added alerts. After re-tuning to the same
95% recall target, the cleaned lists produced slightly fewer false positives. In a bank that comparison would be done
as documented below-the-line testing on real (historical) alerts, with model-risk sign-off before a threshold moves.

**Next scope.** PEP lists and adverse media need different data-quality rules (positions and their end dates,
relatives and close associates, source reliability), and ownership data (the EU/UK 50% rules, OFAC's 50% rule) needs
entity-relationship data that these lists do not contain.
