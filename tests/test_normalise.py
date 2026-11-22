from datetime import date, timedelta

import pytest

from slo.normalise.countries import to_iso2
from slo.normalise.dates import parse_date, parse_dob
from slo.normalise.ids import map_eu_id_type, map_ofac_id_type, map_un_id_type, normalise_id_number
from slo.normalise.names import (
    detect_script,
    naive_key,
    name_variants,
    normalise_name,
    strip_honorifics,
    tokens_sorted,
)

DOB_CASES = [
    ("1937-04-28", date(1937, 4, 28), date(1937, 4, 28), "day", False),
    ("28/04/1937", date(1937, 4, 28), date(1937, 4, 28), "day", False),
    ("1965", date(1965, 1, 1), date(1965, 12, 31), "year", False),
    ("dd/mm/1945", date(1945, 1, 1), date(1945, 12, 31), "year", False),
    ("00/00/1975", date(1975, 1, 1), date(1975, 12, 31), "year", False),
    ("dd/04/1960", date(1960, 4, 1), date(1960, 4, 30), "month", False),
    ("circa 1960", date(1959, 1, 1), date(1961, 12, 31), "year", True),
    ("c. 1960", date(1959, 1, 1), date(1961, 12, 31), "year", True),
    ("Jan 1961", date(1961, 1, 1), date(1961, 1, 31), "month", False),
    ("Feb 2000", date(2000, 2, 1), date(2000, 2, 29), "month", False),
    ("10 Dec 1948", date(1948, 12, 10), date(1948, 12, 10), "day", False),
    ("1948 to 1950", date(1948, 1, 1), date(1950, 12, 31), "range", False),
    ("between 1948 and 1950", date(1948, 1, 1), date(1950, 12, 31), "range", False),
    ("1961 - 1963", date(1961, 1, 1), date(1963, 12, 31), "range", False),
    ("01 Jan 1973 to 31 Dec 1973", date(1973, 1, 1), date(1973, 12, 31), "range", False),
    ("1975-06", date(1975, 6, 1), date(1975, 6, 30), "month", False),
    ("", None, None, "unknown", False),
    ("   ", None, None, "unknown", False),
    ("garbage", None, None, "unparseable", False),
    ("15/08/19yy", None, None, "unparseable", False),
    ("31/02/1970", None, None, "unparseable", False),
    ("Foo 1970", None, None, "unparseable", False),
    ("2099-01-01", None, None, "invalid", False),
    ("1340", None, None, "invalid", False),
]


@pytest.mark.parametrize(("raw", "lo", "hi", "precision", "circa"), DOB_CASES)
def test_parse_dob(raw, lo, hi, precision, circa):
    assert parse_dob(raw) == (lo, hi, precision, circa)


def test_parse_dob_circa_flag_passthrough_and_month_first():
    assert parse_dob("1950", circa=True).circa is True
    assert parse_dob("1950", circa=True).date_from == date(1949, 1, 1)
    assert parse_dob("04/28/1937", dayfirst=False).date_from == date(1937, 4, 28)


def test_parse_dob_future_relative_to_today():
    tomorrow = date.today() + timedelta(days=1)
    assert parse_dob(tomorrow.isoformat()).precision == "invalid"


def test_parse_date():
    assert parse_date("20/11/2026", "%d/%m/%Y") == date(2026, 11, 20)
    assert parse_date("2026-11-20T16:51:00", "%Y-%m-%d") == date(2026, 11, 20)
    assert parse_date("11/20/2026", "%m/%d/%Y") == date(2026, 11, 20)
    assert parse_date("nonsense", "%Y-%m-%d") is None
    assert parse_date(None, "%Y-%m-%d") is None


@pytest.mark.parametrize(
    ("raw", "iso2"),
    [
        ("IQ", "IQ"),
        ("nl", "NL"),
        ("00", None),
        ("UNKNOWN", None),
        ("", None),
        (None, None),
        ("QQ", None),
        ("IRQ", "IQ"),
        ("Russian Federation", "RU"),
        ("Russia", "RU"),
        ("Democratic Republic of the Congo", "CD"),
        ("Congo (Democratic Republic)", "CD"),
        ("Iran (Islamic Republic of)", "IR"),
        ("Korea, North", "KP"),
        ("North Korea", "KP"),
        ("Syrian Arab Republic", "SY"),
        ("United Republic of Tanzania", "TZ"),
        ("Türkiye", "TR"),
        ("Kosovo", "XK"),
        ("Burma", "MM"),
        ("Bosnia-Herzegovina", "BA"),
        ("Ukrainian SSR (now Ukraine)", "UA"),
        ("Former USSR Currently Belarus", "BY"),
        ("Russia (USSR)", "RU"),
        ("USSR", None),
        ("Serbia and Montenegro", None),
        ("Hebei Province", None),
        ("Germany", "DE"),
        ("France", "FR"),
    ],
)
def test_to_iso2(raw, iso2):
    assert to_iso2(raw) == iso2


def test_names():
    assert normalise_name("Saddam Hussein Al-Tikriti") == "saddam hussein al tikriti"
    assert tokens_sorted(normalise_name("Saddam Hussein Al-Tikriti")) == "al hussein saddam tikriti"
    assert normalise_name("ANGLO-CARIBBEAN CO., LTD.", is_org=True) == "anglo caribbean co ltd"
    assert normalise_name("Example Trading B.V.", is_org=True) == "example trading bv"
    assert normalise_name("OOO Romashka", is_org=True) == "llc romashka"
    assert normalise_name("Ma'ruf  Amin") == "maruf amin"
    assert normalise_name("صدام حسين") == "sdm hsyn"
    assert normalise_name("Сергей Иванов") == "sergey ivanov"
    assert normalise_name("Éric Müller") == "eric muller"
    assert normalise_name(None) == ""
    assert strip_honorifics("haji abd al rahman") == "rahman"
    assert name_variants("sheikh ahmed") == ["sheikh ahmed", "ahmed"]
    assert name_variants("ahmed ali") == ["ahmed ali"]
    assert name_variants("") == []
    assert naive_key("  ERIC   Badege ") == "eric badege"


def test_detect_script():
    assert detect_script("Saddam") == "latin"
    assert detect_script("صدام حسين") == "arabic"
    assert detect_script("Сергей") == "cyrillic"
    assert detect_script("金正恩") == "cjk"
    assert detect_script("ქართული") == "other"
    assert detect_script("123 -") == "unknown"
    assert detect_script(None) == "unknown"


def test_ids():
    assert normalise_id_number(" AB-187.304/ 9 ") == "AB1873049"
    assert normalise_id_number(None) == ""
    assert map_eu_id_type("passport") == "passport"
    assert map_eu_id_type("regnumber") == "company_reg"
    assert map_eu_id_type("birthcert") == "other"
    assert map_ofac_id_type("Passport") == "passport"
    assert map_ofac_id_type("National ID No.") == "national_id"
    assert map_ofac_id_type("Company Number") == "company_reg"
    assert map_ofac_id_type("SWIFT/BIC") == "swift_bic"
    assert map_ofac_id_type("Gender") is None
    assert map_ofac_id_type("Secondary sanctions risk:") is None
    assert map_ofac_id_type("Website") is None
    assert map_un_id_type("Passport") == "passport"
    assert map_un_id_type("National Identification\n            Number") == "national_id"
    assert map_un_id_type("Driving licence") == "other"
