import random
from datetime import date

import polars as pl

from slo.models import RawCountry, RawDob, RawName
from slo.requests_log import add_request, approve
from slo.screening.evaluate import evaluate, missed_hits, top_false_positives
from slo.screening.matcher import adjust, build_index, screen, screen_names
from slo.screening.synth import ListPerson, frame_hash, generate, load_list_targets, perturb
from tests.conftest import D, load, org, person

LIST = [
    person(
        "1",
        "Saddam Hussein Al-Tikriti",
        dobs=[RawDob("1937-04-28")],
        countries=[RawCountry("nationality", "IQ")],
        names=[
            RawName("Saddam Hussein Al-Tikriti", "primary"),
            RawName("Abu Ali", strength="weak"),
            RawName("صدام حسين التكريتي", "non_latin"),
        ],
    ),
    person(
        "2",
        "Sergey Petrovich Ivanov",
        dobs=[RawDob("1960")],
        countries=[RawCountry("nationality", "RU")],
    ),
    org("3", "Anglo-Caribbean Co., Ltd."),
]


def _index(con, mode="clean"):
    load(con, "tst", LIST)
    if mode == "clean":
        from slo.dq.fixes import build_clean

        build_clean(con, "tst", D)
    return build_index(con, D, ["tst"], mode)


def _score(df, ent):
    rows = df.filter(pl.col("entity_key") == ent)
    return rows["final_score"].max() if rows.height else 0.0


def test_matcher_scores(con):
    idx = _index(con)
    res = screen_names(
        ["Saddam Hussein Al-Tikriti", "Al-Tikriti Saddam Hussein", "Jan de Vries", "Sergey Ivanov"],
        idx,
    )
    assert _score(res.filter(pl.col("cust_id") == "Q0"), "tst:1") >= 95
    assert _score(res.filter(pl.col("cust_id") == "Q1"), "tst:1") >= 90  # token swap
    assert _score(res.filter(pl.col("cust_id") == "Q2"), "tst:1") < 60  # unrelated
    assert _score(res.filter(pl.col("cust_id") == "Q3"), "tst:2") >= 80  # middle name dropped


def test_dob_nationality_and_type_adjustments(con):
    idx = _index(con)
    customers = pl.DataFrame(
        {
            "cust_id": ["same", "far", "company"],
            "full_name": ["Saddam Hussein Al-Tikriti"] * 3,
            "dob": [date(1937, 4, 28), date(1990, 1, 1), None],
            "nationality_iso2": ["IQ", "NL", None],
            "type": ["person", "person", "company"],
        }
    )
    res = screen(customers, idx, keep_from=0)
    s = {c: _score(res.filter(pl.col("cust_id") == c), "tst:1") for c in ("same", "far", "company")}
    assert s["same"] > s["far"] and s["same"] > s["company"]
    assert s["same"] - s["far"] == 5 + 3 + 15 + 5  # +dob +nat vs -dob -nat
    assert adjust(90, None, None, "person", "person", [], set()) == 90
    assert (
        adjust(
            90,
            date(1938, 1, 1),
            None,
            "person",
            "person",
            [(date(1937, 4, 28), date(1937, 4, 28))],
            set(),
        )
        == 90
    )


def test_raw_vs_clean_modes(con):
    clean = _index(con)
    raw = build_index(con, D, ["tst"], "raw")
    assert "saddam hussein al-tikriti" in raw.names  # raw: lowercase only
    assert "saddam hussein al tikriti" in clean.names and "saddam hussein tikriti" in clean.names
    assert any(n == "sdm hsyn ltkryty" for n in clean.names)  # transliterated non-Latin name
    q = ["Saddam Hussein Al Tikriti"]
    assert _score(screen_names(q, clean), "tst:1") > _score(screen_names(q, raw), "tst:1")
    company = screen_names(["Anglo Caribbean Company Limited"], clean)
    assert company.filter(pl.col("entity_key") == "tst:3").height == 1


def test_suppression(con, requests_dir):
    load(con, "tst", LIST)
    path = requests_dir / "requests.csv"
    r = add_request(
        path,
        request_type="false_positive_suppression",
        target_source="tst",
        target_entity_key="tst:1",
        payload={"customer_name": "Saddam Hussein Al-Tikriti"},
        rationale="t",
        maker="a",
        requested_by="x",
    )
    approve(path, r.request_id, "b")
    from slo.dq.fixes import build_clean

    build_clean(con, "tst", D)
    idx = build_index(con, D, ["tst"], "clean", requests_dir=requests_dir)
    assert (
        screen_names(["Saddam Hussein Al-Tikriti"], idx)
        .filter(pl.col("entity_key") == "tst:1")
        .height
        == 0
    )


def _targets():
    return [
        ListPerson(
            "x:1",
            "x",
            "person",
            "Mohammed Ali Hassan",
            ["Abu Ali Hassan"],
            ["Abu Ali Hassan"],
            (date(1960, 1, 1), date(1960, 12, 31), "year"),
            "IQ",
        ),
        ListPerson("x:2", "x", "organisation", "Example Trading Company", [], [], None, None),
        ListPerson(
            "y:1",
            "y",
            "person",
            "Sergei Ivanov",
            ["Serge Ivanov"],
            [],
            (date(1970, 5, 1), date(1970, 5, 1), "day"),
            "RU",
        ),
        ListPerson("y:2", "y", "person", "Ivan Ivanovich Petrov", [], [], None, "RU"),
    ]


def test_perturbations():
    rng = random.Random(1)
    t = _targets()
    assert perturb(t[0], "exact", rng) == "Mohammed Ali Hassan"
    assert perturb(t[0], "token_swap", rng) == "Hassan Mohammed Ali"
    assert perturb(t[0], "drop_middle", rng) == "Mohammed Hassan"
    assert perturb(t[0], "alias_used", rng) == "Abu Ali Hassan"
    variant = perturb(t[0], "translit_variant", rng)
    assert (
        variant and variant != "Mohammed Ali Hassan" and variant.endswith("Hassan")
    ) or "Hasan" in variant
    assert perturb(t[1], "token_swap", rng) is None  # not for organisations
    assert perturb(t[3], "alias_used", rng) is None
    typo = perturb(t[0], "typo", rng)
    assert typo != "Mohammed Ali Hassan" and len(typo.split()) == 3


def test_generate_is_deterministic_and_plants_hits():
    c1, t1 = generate(_targets(), 50, 4, seed=3)
    c2, t2 = generate(_targets(), 50, 4, seed=3)
    assert frame_hash(c1) == frame_hash(c2) and frame_hash(t1) == frame_hash(t2)
    c3, _ = generate(_targets(), 50, 4, seed=4)
    assert frame_hash(c1) != frame_hash(c3)
    assert c1.height == 50 and t1.height == 4
    assert t1["entity_key"].n_unique() == 4
    planted = c1.join(t1, on="cust_id")
    assert (planted["full_name"] == planted["customer_name"]).all()
    assert set(c1["locale"]) <= {
        "nl_NL",
        "de_DE",
        "en_GB",
        "fr_FR",
        "tr_TR",
        "ar_AA",
        "ru_RU",
        "pl_PL",
        "es_ES",
    }


def test_load_list_targets(con):
    load(
        con,
        "tst",
        [*LIST, person("4", "Только Кириллица", names=[RawName("Только Кириллица", "primary")])],
    )
    targets = load_list_targets(con, D, ["tst"])
    assert {t.entity_key for t in targets} == {"tst:1", "tst:2", "tst:3"}  # non-Latin-only skipped
    saddam = next(t for t in targets if t.entity_key == "tst:1")
    assert saddam.weak_aliases == ["Abu Ali"] and saddam.nationality == "IQ"
    assert saddam.dob[0] == date(1937, 4, 28)


def test_evaluate():
    truth = pl.DataFrame(
        {"cust_id": ["C1", "C2"], "entity_key": ["eu:1", "eu:2"], "perturbation": ["exact", "typo"]}
    )
    pairs = pl.DataFrame(
        {
            "cust_id": ["C1", "C2", "C3", "C3", "C1"],
            "entity_key": ["uk:9", "eu:2", "eu:1", "eu:2", "eu:5"],
            "matched_name": ["a"] * 5,
            "name_score": [99.0, 80.0, 90.0, 86.0, 88.0],
            "final_score": [99.0, 80.0, 90.0, 86.0, 88.0],
        }
    )
    eq = {"eu:1": {"eu:1", "uk:9"}, "uk:9": {"eu:1", "uk:9"}}
    ev = evaluate(pairs, truth, 1000, eq, [80, 85, 90], 85)
    at85 = ev["at_default_threshold"]
    assert at85["recall"] == 0.5 and at85["true_hits_found"] == 1  # C1 via equivalent uk:9
    assert at85["alerted_customers"] == 2 and at85["false_positive_customers"] == 1
    assert at85["false_positive_alerts"] == 2 and at85["fp_per_1k_customers"] == 1.0
    assert at85["precision"] == 0.5
    assert ev["operating_point_95_recall"]["threshold"] == 80
    assert ev["recall_by_perturbation"]["typo"]["recall"] == 0.0
    missed = missed_hits(pairs, truth, eq, 85)
    assert missed["cust_id"].to_list() == ["C2"] and missed["best_true_score"].to_list() == [80.0]
    customers = pl.DataFrame(
        {"cust_id": ["C3"], "full_name": ["X"], "dob": [None], "nationality_iso2": ["NL"]}
    )
    fps = top_false_positives(pairs, truth, customers, {"eu:1": "One", "eu:2": "Two"}, 85)
    assert fps["entity_key"].to_list() == ["eu:1", "eu:2"] and fps[
        "list_primary_name"
    ].to_list() == ["One", "Two"]
