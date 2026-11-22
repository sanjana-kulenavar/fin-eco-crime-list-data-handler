"""Static PNG charts for the README / report (matplotlib, headless)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Reference palette (validated: scripts/validate_palette.js, light surface #fcfcfb)
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SERIES = {"raw": "#2a78d6", "clean": "#eb6834"}
LABELS = {"raw": "A · raw lists", "clean": "B · cleaned lists"}
SINGLE = "#2a78d6"


def _style(ax: plt.Axes, title: str, subtitle: str | None = None) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(
        title, loc="left", color=TEXT, fontsize=12, fontweight="bold", pad=22 if subtitle else 10
    )
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=TEXT_2, fontsize=9)


def _fig(w: float = 7.5, h: float = 4.2) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return path


def pr_curve(runs: dict, path: Path) -> Path:
    fig, ax = _fig()
    for mode, ev in runs.items():
        pts = [
            (s["recall"], s["precision"], s["threshold"])
            for s in ev["sweep"]
            if s["precision"] is not None
        ]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        ax.plot(
            xs,
            ys,
            color=SERIES[mode],
            linewidth=2,
            marker="o",
            markersize=4,
            markeredgecolor=SURFACE,
            markeredgewidth=1,
            label=LABELS[mode],
        )
        d = next(p for p in pts if p[2] == ev["default_threshold"])
        ax.scatter(
            [d[0]], [d[1]], s=70, color=SERIES[mode], edgecolor=SURFACE, linewidth=2, zorder=5
        )
        ax.annotate(
            f"t={d[2]}",
            (d[0], d[1]),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=8,
            color=TEXT_2,
        )
    ax.set_xlabel("Recall (planted true hits alerted)", color=TEXT_2, fontsize=9)
    ax.set_ylabel("Precision (alerted customers that are true hits)", color=TEXT_2, fontsize=9)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_2, loc="upper right")
    _style(
        ax,
        "Precision vs recall across alert thresholds 75–95",
        "Each point is one threshold; large dot = default threshold",
    )
    return _save(fig, path)


def recall_by_perturbation(runs: dict, path: Path) -> Path:
    fig, ax = _fig(8, 4.2)
    kinds = sorted(next(iter(runs.values()))["recall_by_perturbation"])
    width = 0.38
    for j, (mode, ev) in enumerate(runs.items()):
        vals = [ev["recall_by_perturbation"][k]["recall"] for k in kinds]
        xs = [i + (j - 0.5) * (width + 0.02) for i in range(len(kinds))]
        bars = ax.bar(
            xs,
            vals,
            width=width,
            color=SERIES[mode],
            label=LABELS[mode],
            edgecolor=SURFACE,
            linewidth=1,
        )
        for b, v in zip(bars, vals, strict=True):
            if v < 0.995:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    v + 0.015,
                    f"{v:.0%}",
                    ha="center",
                    fontsize=7.5,
                    color=TEXT_2,
                )
    ax.set_xticks(range(len(kinds)), [k.replace("_", " ") for k in kinds])
    ax.set_ylim(0, 1.22)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_2, loc="upper right", ncols=2)
    t = next(iter(runs.values()))["default_threshold"]
    _style(
        ax,
        "Recall by perturbation type",
        f"Share of planted hits alerted at threshold {t}; labels shown below 100%",
    )
    return _save(fig, path)


def fp_comparison(runs: dict, path: Path) -> Path:
    fig, ax = _fig(7.5, 3.8)
    groups = ["default threshold", "≥95% recall operating point"]
    width = 0.36
    for j, (mode, ev) in enumerate(runs.items()):
        op = ev["operating_point_95_recall"]
        vals = [
            ev["at_default_threshold"]["fp_per_1k_customers"],
            op["fp_per_1k_customers"] if op else None,
        ]
        for i, v in enumerate(vals):
            x = i + (j - 0.5) * (width + 0.02)
            if v is None:
                ax.text(
                    x,
                    1,
                    "95% recall\nnot reached",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                    color=MUTED,
                )
                continue
            thr = ev["default_threshold"] if i == 0 else op["threshold"]
            ax.bar(
                x,
                v,
                width=width,
                color=SERIES[mode],
                edgecolor=SURFACE,
                linewidth=1,
                label=LABELS[mode] if i == 0 else None,
            )
            ax.text(
                x,
                v + 1,
                f"{v:.0f}\n(t={thr})",
                ha="center",
                va="bottom",
                fontsize=7.5,
                color=TEXT_2,
            )
    ax.set_xticks(range(len(groups)), groups)
    ymax = max(
        [ev["at_default_threshold"]["fp_per_1k_customers"] for ev in runs.values()]
        + [
            ev["operating_point_95_recall"]["fp_per_1k_customers"]
            for ev in runs.values()
            if ev["operating_point_95_recall"]
        ]
    )
    ax.set_ylim(0, max(ymax, 1.0) * 1.3)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_2, loc="upper left")
    _style(ax, "False-positive customers per 1,000 screened", "Non-planted customers with ≥1 alert")
    return _save(fig, path)


def dq_scores(scorecards: dict, path: Path) -> Path:
    fig, ax = _fig(7, 3.4)
    items = [(s, c["dq_score"]) for s, c in scorecards.items() if c.get("dq_score") is not None]
    items.sort(key=lambda kv: kv[1])
    ys = range(len(items))
    ax.barh(list(ys), [v for _, v in items], color=SINGLE, height=0.55, edgecolor=SURFACE)
    for y, (_, v) in zip(ys, items, strict=True):
        ax.text(v + 1, y, f"{v:.1f}", va="center", fontsize=8.5, color=TEXT_2)
    ax.set_yticks(list(ys), [s.upper() for s, _ in items])
    ax.set_xlim(0, 108)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    _style(ax, "DQ score by list (0–100)", "Severity-weighted share of records without open issues")
    return _save(fig, path)
