"""Parsers run on fixtures copied from the live files (scripts/make_fixtures.py)."""

from datetime import date

import pytest

from slo.build import build_frames
from slo.parsers import PARSERS
from tests.conftest import FIXTURES

FILES = {"eu": "eu.xml", "un": "un.xml", "ofac": "ofac.xml", "uk": "uk.xml", "nl": "nl.ftm.json"}

# (entities, names, birth_dates, identifiers, countries, addresses)
EXPECTED = {
    "eu": (5, 154, 14, 1, 17, 6),
    "un": (6, 33, 6, 2, 8, 6),
    "ofac": (6, 24, 4, 3, 7, 3),
    "uk": (6, 21, 6, 3, 10, 2),
    "nl": (4, 4, 3, 0, 0, 0),
}


def _parse(source):
    result = PARSERS[source](FIXTURES / FILES[source])
    frames = build_frames(result, date(2026, 11, 22))
    return result, frames


@pytest.mark.parametrize("source", list(FILES))
def test_counts_and_no_failures(source):
    result, frames = _parse(source)
    assert result.failures == []
    assert result.element_count == len(result.entities)
    counts = tuple(
        frames[t].height
        for t in ("entities", "names", "birth_dates", "identifiers", "countries", "addresses")
    )
    assert counts == EXPECTED[source]
    assert frames["entities"]["entity_key"].n_unique() == frames["entities"].height
    assert all(k.startswith(f"{source}:") for k in frames["entities"]["entity_key"])


def _row(df, **where):
    for r in df.iter_rows(named=True):
        if all(r[k] == v for k, v in where.items()):
            return r
    raise AssertionError(f"no row matching {where}")


def test_eu_values_namespace_and_quirks():
    result, f = _parse("eu")
    assert result.published_date == date(2026, 11, 20)
    saddam = _row(f["entities"], entity_key="eu:13")
    assert saddam["primary_name"] == "Saddam Hussein Al-Tikriti"
    assert saddam["programmes"] == ["IRQ"] and saddam["gender"] == "M"
    assert _row(f["birth_dates"], entity_key="eu:13")["date_from"] == date(1937, 4, 28)
    # circa birth date widened by a year; countryIso2Code "00" maps to no country
    circa = _row(f["birth_dates"], entity_key="eu:25")
    assert (circa["circa"], circa["date_from"], circa["date_to"]) == (
        True,
        date(1956, 1, 1),
        date(1958, 12, 31),
    )
    assert _row(f["countries"], entity_key="eu:25", raw="00")["country_iso2"] is None
    assert _row(f["identifiers"], entity_key="eu:3822")["id_type"] == "company_reg"
    assert _row(f["entities"], entity_key="eu:3822")["entity_type"] == "organisation"
    # double whitespace preserved in raw full_name (it is a DQ002 finding)
    assert any(
        "  " in n for n in f["names"].filter(f["names"]["entity_key"] == "eu:833")["full_name"]
    )
    scripts = set(f["names"].filter(f["names"]["entity_key"] == "eu:201")["script"])
    assert {"latin", "cyrillic", "other"} <= scripts  # e.g. Russian and Greek renderings


def test_un_values():
    result, f = _parse("un")
    assert result.published_date == date(2026, 11, 22)
    assert _row(f["entities"], entity_key="un:6908048")["un_reference"] == "IQI.001"
    assert _row(f["birth_dates"], entity_key="un:6908021")["precision"] == "range"
    assert (
        _row(f["names"], entity_key="un:6907993", name_type="alias")["full_name"] == ""
    )  # empty <ALIAS_NAME/>
    assert (
        _row(f["identifiers"], entity_key="un:6908994", norm_number="AB187304")["raw_number"]
        == "AB187304 "
    )
    assert _row(f["names"], entity_key="un:6908048", name_type="non_latin")["script"] == "arabic"
    assert _row(f["entities"], entity_key="un:6908024")["entity_type"] == "organisation"
    assert _row(f["countries"], entity_key="un:6907993")["country_iso2"] == "CD"


def test_ofac_values():
    result, f = _parse("ofac")
    assert (result.declared_count, result.published_date) == (6, date(2026, 11, 20))
    assert _row(f["entities"], entity_key="ofac:4238")["entity_type"] == "vessel"
    ids = f["identifiers"].filter(f["identifiers"]["entity_key"] == "ofac:906")
    assert set(ids["id_type"]) == {
        "company_reg",
        "swift_bic",
    }  # metadata rows (e.g. Website) dropped
    assert _row(f["names"], entity_key="ofac:3754", strength="weak")["name_type"] == "alias"
    circa = _row(f["birth_dates"], entity_key="ofac:7782")
    assert (circa["raw"], circa["circa"], circa["precision"]) == ("circa 1951", True, "year")
    assert _row(f["birth_dates"], entity_key="ofac:8868")["precision"] == "range"


def test_uk_values():
    result, f = _parse("uk")
    assert result.published_date == date(2026, 11, 20)
    dobs = f["birth_dates"].filter(f["birth_dates"]["entity_key"] == "uk:DPR0227")
    assert set(dobs["raw"]) == {"dd/mm/1967", "dd/mm/1968", "dd/mm/1969"}
    assert set(dobs["precision"]) == {"year"}
    assert _row(f["birth_dates"], entity_key="uk:RUS2701")["precision"] == "unparseable"
    imo = f["identifiers"].filter(f["identifiers"]["entity_key"] == "uk:RUS2322")
    assert imo.height == 2 and imo["norm_number"].n_unique() == 1  # repeated IMO number
    assert _row(f["entities"], entity_key="uk:IRQ0062")["un_reference"] == "IQI.001"
    primaries = f["names"].filter(f["names"]["name_type"] == "primary")
    assert primaries["entity_key"].n_unique() == primaries.height == 6  # exactly one primary each


def test_nl_values():
    _, f = _parse("nl")
    boukich = _row(f["entities"], entity_key="nl:NK-ZUY6Az9Jx9AhuP7q3ADSu2")
    assert boukich["primary_name"] == "Abdelhakim Boukich"
    assert boukich["listed_on"] is not None  # from the linked Sanction record
    assert "NL-UNSC1373" in boukich["programmes"]
    assert (
        _row(f["entities"], entity_key="nl:NK-meMPT4YjU9UpTecXL9nQkH")["entity_type"]
        == "organisation"
    )


def test_nl_bad_line_is_reported(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text((FIXTURES / "nl.ftm.json").read_text() + "{not json\n")
    result = PARSERS["nl"](p)
    assert len(result.failures) == 1 and "line" in result.failures[0]
    assert len(result.entities) == 4
