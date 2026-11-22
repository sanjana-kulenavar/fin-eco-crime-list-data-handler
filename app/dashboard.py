"""Streamlit dashboard. Reads only committed artefacts (data/reports, data/deltas, requests/).

Run:  uv run streamlit run app/dashboard.py
"""

from __future__ import annotations

import os
from pathlib import Path

import plotly.express as px
import polars as pl
import streamlit as st

from slo.artefacts import issues_matrix, load_artefacts, sweep_frame
from slo.config import PROJECT_ROOT, load_dq_rules

DATA_DIR = Path(os.environ.get("SLO_DATA_DIR", PROJECT_ROOT / "data"))
REQUESTS_DIR = Path(os.environ.get("SLO_REQUESTS_DIR", PROJECT_ROOT / "requests"))
SERIES = {"raw": "#2a78d6", "clean": "#eb6834"}  # validated categorical pair (see slo/charts.py)
MODE_LABEL = {"raw": "A · raw lists", "clean": "B · cleaned lists"}
SEQ = ["#e8f1fb", "#9cc3ef", "#2a78d6", "#123f75"]  # single-hue sequential ramp

st.set_page_config(page_title="sanctions-list-ops", layout="wide")


@st.cache_data
def _load(data_dir: str, requests_dir: str):
    return load_artefacts(Path(data_dir), Path(requests_dir))


art = _load(str(DATA_DIR), str(REQUESTS_DIR))
summary = art.summary
rules = load_dq_rules()
lists = [s for s in summary["sources"] if summary["sources"][s].get("records")]

st.title("sanctions-list-ops")
st.caption(
    f"Snapshot {art.snapshot_date} · EU, UN, OFAC, UK and Dutch sanctions lists plus a fictional internal list"
)

overview, quality, cross, deltas, screening, requests = st.tabs(
    ["Overview", "Data quality", "Cross-list", "Deltas", "Screening experiment", "Requests log"]
)

with overview:
    cols = st.columns(len(lists))
    for col, s in zip(cols, lists, strict=True):
        card = summary["sources"][s]
        col.metric(s.upper(), f"{card['records']:,}", help="entities in this snapshot")
        col.caption(
            f"DQ score **{card['dq_score']}** · published {card.get('published_date') or 'n/a'}"
        )
        d = summary["deltas"].get(s, {})
        col.caption(
            f"Δ +{d.get('added', 0)} / −{d.get('removed', 0)} / ~{d.get('amended', 0)}"
            + (" (initial load)" if d.get("initial_load") else "")
        )
    table = pl.DataFrame(
        [
            {
                "list": s.upper(),
                "entities": c["records"],
                "persons": c.get("persons"),
                "dq_score": c["dq_score"],
                "% persons full DOB": c.get("pct_persons_full_dob"),
                "% persons with nationality": c.get("pct_persons_with_nationality"),
                "% entities with identifier": c.get("pct_entities_with_identifier"),
                "export gate": "passed" if c.get("export_gate_passed") else "BLOCKED",
            }
            for s, c in summary["sources"].items()
        ]
    )
    st.dataframe(table, hide_index=True, width="stretch")
    exports = summary.get("exports", {})
    if exports:
        st.subheader("Screening-system exports")
        st.dataframe(
            pl.DataFrame(
                [
                    {
                        "system": k,
                        "records": v["record_count"],
                        "gate passed": v["dq_gate_passed"],
                        "sha256": v["sha256"][:16] + "…",
                    }
                    for k, v in exports.items()
                ]
            ),
            hide_index=True,
        )

with quality:
    m = issues_matrix(summary)
    if m.height:
        m = m.with_columns(
            pl.col("rule_id")
            .map_elements(lambda r: f"{r} · {rules[r]['title'][:48]}", return_dtype=pl.String)
            .alias("rule")
        )
        pivot = (
            m.pivot(on="source", index="rule", values="entities", aggregate_function="sum")
            .fill_null(0)
            .sort("rule")
        )
        fig = px.imshow(
            pivot.drop("rule").to_numpy(),
            x=pivot.columns[1:],
            y=pivot["rule"].to_list(),
            color_continuous_scale=SEQ,
            aspect="auto",
            labels={"color": "entities affected"},
            text_auto=True,
        )
        fig.update_layout(height=640, margin={"l": 10, "r": 10, "t": 30, "b": 10})
        st.subheader("Entities affected, by rule and list")
        st.plotly_chart(fig, width="stretch")
    st.subheader("Issue drill-down (sample, up to 50 per rule and list)")
    c1, c2 = st.columns(2)
    src = c1.multiselect("List", sorted(art.issues_sample["source"].unique().to_list()))
    rule = c2.multiselect("Rule", sorted(art.issues_sample["rule_id"].unique().to_list()))
    sample = art.issues_sample
    if src:
        sample = sample.filter(pl.col("source").is_in(src))
    if rule:
        sample = sample.filter(pl.col("rule_id").is_in(rule))
    st.dataframe(sample, hide_index=True, width="stretch", height=320)
    st.subheader("Raw vs cleaned: examples from the fix log")
    st.dataframe(pl.DataFrame(art.fix_examples), hide_index=True, width="stretch")

with cross:
    stats = summary["crosslist"]["stats"]
    gaps = summary["crosslist"]["transposition_gaps"]
    c = st.columns(4)
    c[0].metric("UN entities", stats.get("un_entities", 0))
    c[1].metric("found on EU list", stats.get("un_eu", 0), help="by UN reference or name + DOB")
    c[2].metric("found on UK list", stats.get("un_uk", 0))
    c[3].metric("on UN, EU and UK", stats.get("un_eu_uk", 0))
    overlap = stats.get("name_dob_overlap", {})
    if overlap:
        fig = px.bar(
            x=list(overlap.values()),
            y=[k.replace("_", " ∩ ").upper() for k in overlap],
            orientation="h",
            labels={"x": "entities of the first list matched on the second (name + DOB)", "y": ""},
            color_discrete_sequence=[SERIES["raw"]],
        )
        fig.update_layout(height=260, margin={"l": 10, "r": 10, "t": 10, "b": 10})
        st.plotly_chart(fig, width="stretch")
    for target in ("eu", "uk"):
        g = gaps[target]
        st.markdown(
            f"**UN entities missing from the {target.upper()} list (DQ050):** {g['count']} "
            f"(median {g['median_days_since_un_listing']} days since UN listing)"
        )
        if g["examples"]:
            st.dataframe(pl.DataFrame({"example": g["examples"]}), hide_index=True, width="stretch")

with deltas:
    if art.deltas.height:
        long = art.deltas.unpivot(
            index=["source", "to"],
            on=["added", "removed", "amended"],
            variable_name="change",
            value_name="entities",
        )
        fig = px.bar(
            long.to_pandas(),
            x="to",
            y="entities",
            color="change",
            facet_col="source",
            facet_col_wrap=3,
            color_discrete_map={"added": "#2a78d6", "removed": "#eb6834", "amended": "#1baf7a"},
        )
        fig.update_layout(height=520, margin={"l": 10, "r": 10, "t": 40, "b": 10})
        st.plotly_chart(fig, width="stretch")
        st.dataframe(art.deltas, hide_index=True, width="stretch")
        if art.deltas["initial_load"].all():
            st.info(
                "Only the initial load exists so far; the daily GitHub Actions job adds one delta per list per day."
            )

with screening:
    scr = summary.get("screening")
    if not scr:
        st.warning("No screening experiment in this report.")
    else:
        runs = scr["runs"]
        default_t = next(iter(runs.values()))["default_threshold"]
        st.caption(
            f"{scr['synthetic_data']['n_customers']:,} synthetic customers · {scr['synthetic_data']['n_hits']} planted hits drawn from the lists · seed {scr['synthetic_data']['seed']}"
        )
        sweep = sweep_frame(summary)
        t = st.slider(
            "Alert threshold",
            int(sweep["threshold"].min()),
            int(sweep["threshold"].max()),
            default_t,
        )
        cols = st.columns(2)
        for col, mode in zip(cols, ("raw", "clean"), strict=True):
            row = sweep.filter((pl.col("mode") == mode) & (pl.col("threshold") == t)).row(
                0, named=True
            )
            col.markdown(f"**{MODE_LABEL[mode]}**")
            a, b, c = col.columns(3)
            a.metric("recall", f"{row['recall']:.1%}")
            b.metric("alerts", f"{row['alerts']:,}")
            c.metric("FP customers / 1k", row["fp_per_1k_customers"])
        pr = sweep.with_columns(pl.col("mode").replace(MODE_LABEL).alias("run")).to_pandas()
        fig = px.line(
            pr,
            x="recall",
            y="precision",
            color="run",
            markers=True,
            hover_data=["threshold", "alerts", "fp_per_1k_customers"],
            color_discrete_map={MODE_LABEL[k]: v for k, v in SERIES.items()},
        )
        fig.update_layout(
            height=380,
            xaxis_tickformat=".0%",
            yaxis_tickformat=".0%",
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
        )
        st.plotly_chart(fig, width="stretch")
        per = pl.DataFrame(
            [
                {"run": MODE_LABEL[m], "perturbation": k, "recall": v["recall"]}
                for m, ev in runs.items()
                for k, v in ev["recall_by_perturbation"].items()
            ]
        )
        fig = px.bar(
            per.to_pandas(),
            x="perturbation",
            y="recall",
            color="run",
            barmode="group",
            color_discrete_map={MODE_LABEL[k]: v for k, v in SERIES.items()},
        )
        fig.update_layout(
            height=340, yaxis_tickformat=".0%", margin={"l": 10, "r": 10, "t": 10, "b": 10}
        )
        st.plotly_chart(fig, width="stretch")
        mode = st.radio(
            "Top false positives for", ["clean", "raw"], horizontal=True, format_func=MODE_LABEL.get
        )
        if mode in art.top_false_positives:
            st.dataframe(art.top_false_positives[mode], hide_index=True, width="stretch")

with requests:
    st.caption(
        "Maker/checker (four-eyes) log of list change requests. Source: requests/requests.csv"
    )
    st.dataframe(art.requests, hide_index=True, width="stretch")
