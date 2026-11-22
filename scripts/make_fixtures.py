"""Extract real records from live downloads into small test fixtures.

Usage: uv run python scripts/make_fixtures.py <dir with eu.xml un.xml sdn.xml uk.xml nl.json> tests/fixtures
"""

import copy
import json
import sys
from pathlib import Path

from lxml import etree as ET

D, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
parser = ET.XMLParser(remove_blank_text=True, huge_tree=True)


def write(root, name):
    ET.indent(root, space="  ")
    (OUT / name).write_bytes(
        ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    )


# EU
t = ET.parse(str(D / "eu.xml"), parser)
r = t.getroot()
ns = r.nsmap[None]
want = ["13", "25", "833", "3822", "201"]
new = ET.Element(r.tag, r.attrib, nsmap=r.nsmap)
by = {e.get("logicalId"): e for e in r.findall(f"{{{ns}}}sanctionEntity")}
for k in want:
    new.append(copy.deepcopy(by[k]))
write(new, "eu.xml")

# UN
t = ET.parse(str(D / "un.xml"), parser)
r = t.getroot()
new = ET.Element(r.tag, r.attrib, nsmap=r.nsmap)
ind = ET.SubElement(new, "INDIVIDUALS")
ent = ET.SubElement(new, "ENTITIES")
want_i = ["6908048", "6907993", "6908994", "6908021", "6909614"]
want_e = ["6908024"]
for e in r.iter("INDIVIDUAL"):
    if e.findtext("DATAID") in want_i:
        ind.append(copy.deepcopy(e))
for e in r.iter("ENTITY"):
    if e.findtext("DATAID") in want_e:
        ent.append(copy.deepcopy(e))
write(new, "un.xml")

# OFAC
t = ET.parse(str(D / "sdn.xml"), parser)
r = t.getroot()
ns = r.nsmap[None]
Q = f"{{{ns}}}"
want = ["7843", "7782", "3754", "4238", "906", "8868"]
new = ET.Element(r.tag, r.attrib, nsmap=r.nsmap)
pub = copy.deepcopy(r.find(f"{Q}publshInformation"))
pub.find(f"{Q}Record_Count").text = str(len(want))
new.append(pub)
for e in r.findall(f"{Q}sdnEntry"):
    if e.findtext(f"{Q}uid") in want:
        new.append(copy.deepcopy(e))
write(new, "ofac.xml")

# UK
t = ET.parse(str(D / "uk.xml"), parser)
r = t.getroot()
want = ["IRQ0062", "RUS2322", "DPR0227", "DPR0075", "BEL0033", "RUS2701"]
new = ET.Element(r.tag, r.attrib, nsmap=r.nsmap)
new.append(copy.deepcopy(r.find("DateGenerated")))
for e in r.findall("Designation"):
    if e.findtext("UniqueID") in want:
        new.append(copy.deepcopy(e))
write(new, "uk.xml")

# NL (NDJSON): 3 persons, the legal entities, and their Sanction records
recs = [json.loads(line) for line in (D / "nl.json").read_text().splitlines() if line.strip()]
persons = [x for x in recs if x["schema"] == "Person"][:3]
orgs = [x for x in recs if x["schema"] == "LegalEntity"][:1]
keep = {x["id"] for x in persons + orgs}
sanc = [
    x for x in recs if x["schema"] == "Sanction" and set(x["properties"].get("entity", [])) & keep
]
with open(OUT / "nl.ftm.json", "w") as fh:
    for x in persons + orgs + sanc:
        fh.write(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n")
print("ok")
