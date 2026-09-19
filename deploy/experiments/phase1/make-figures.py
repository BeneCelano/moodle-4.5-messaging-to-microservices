"""The two SVG figures of phase 1: capacity curve and comparison of the configurations.

    python3 make-figures.py --calibration results/<calibration> --campaign results/<campaign> --out figures
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analyze import CONFIGS, read_run  # noqa: E402

LABELS = {
    "capacity_title": "Monolith capacity curve",
    "capacity_x": "Concurrent requests",
    "capacity_y": "p95 latency (ms, log scale)",
    "capacity_stable": "stable operating region",
    "capacity_knee": "knee of the curve",
    "capacity_chosen": "adopted operating point",
    "compare_title": "p95 latency by configuration",
    "compare_y": "p95 latency (ms)",
    "compare_note": "bars show the mean over repeated runs; whiskers show one standard deviation",
    "compare_c0": "C0\nbaseline",
    "compare_c1": "C1\nabstraction",
}

APDEX_T, APDEX_F = 500.0, 1500.0
"""Thresholds used to read the files, consistent with those of the campaign."""


def svg_open(width: int, height: int) -> list[str]:
    """Open an SVG document with a white background."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" font-family="Helvetica,Arial,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]


def write(parts: list[str], path: Path) -> None:
    """Close the document and save it."""
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"  {path}")


def capacity_figure(directory: Path, out: Path, chosen: int) -> None:
    """Capacity curve: latency as concurrency grows."""
    points: list[tuple[int, float]] = []
    for path in sorted(directory.glob("threads-*.jtl"), key=lambda p: int(p.stem.split("-")[1])):
        points.append((int(path.stem.split("-")[1]), read_run(path, APDEX_T, APDEX_F).p95_ms))

    if not points:
        print(f"  no calibration data in {directory}", file=sys.stderr)
        return

    w, h = 760, 440
    left, right, top, bottom = 78, 28, 46, 66
    pw, ph = w - left - right, h - top - bottom

    values = [p95 for _, p95 in points]
    ymin = 10 ** math.floor(math.log10(min(values) * 0.7))
    ymax = 10 ** math.ceil(math.log10(max(values) * 1.3))

    def px(i: int) -> float:
        """The levels are equally spaced: their numeric distances are not meaningful."""
        return left + pw * i / max(1, len(points) - 1)

    def py(value: float) -> float:
        """Vertical position on the logarithmic scale."""
        lo, hi = math.log10(ymin), math.log10(ymax)
        return top + ph * (hi - math.log10(value)) / (hi - lo)

    parts = svg_open(w, h)
    chosen_index = next((i for i, (t, _) in enumerate(points) if t == chosen), None)

    # Stable region: up to the level preceding the widest jump between two consecutive
    # points.
    jumps = [(points[i + 1][1] / points[i][1], i) for i in range(len(points) - 1)]
    knee = max(jumps)[1] if jumps else len(points) - 1
    parts.append(f'<rect x="{left}" y="{top}" width="{px(knee) - left:.1f}" height="{ph}" fill="#eef4fa"/>')
    parts.append(
        f'<text x="{(left + px(knee)) / 2:.1f}" y="{top + 16}" font-size="11" fill="#5b7fa6"'
        f' text-anchor="middle">{LABELS["capacity_stable"]}</text>'
    )

    tick = ymin
    while tick <= ymax:
        for multiple in (1, 2, 5):
            value = tick * multiple
            if ymin <= value <= ymax:
                yy = py(value)
                parts.append(
                    f'<line x1="{left}" y1="{yy:.1f}" x2="{left + pw}" y2="{yy:.1f}"'
                    ' stroke="#dcdcdc" stroke-width="1"/>'
                )
                parts.append(
                    f'<text x="{left - 10}" y="{yy + 4:.1f}" font-size="11" fill="#555"'
                    f' text-anchor="end">{value:g}</text>'
                )
        tick *= 10

    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + ph}" stroke="#333" stroke-width="1.2"/>')
    parts.append(
        f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" stroke="#333" stroke-width="1.2"/>'
    )

    line = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, (_, v) in enumerate(points))
    parts.append(f'<polyline points="{line}" fill="none" stroke="#1f4e79" stroke-width="2.4"/>')

    for i, (threads, p95) in enumerate(points):
        is_chosen = i == chosen_index
        colour = "#c0392b" if is_chosen else "#1f4e79"
        parts.append(f'<circle cx="{px(i):.1f}" cy="{py(p95):.1f}" r="{6 if is_chosen else 4.2}" fill="{colour}"/>')
        parts.append(
            f'<text x="{px(i):.1f}" y="{py(p95) - 13:.1f}" font-size="11.5" fill="{colour}"'
            f' text-anchor="middle" font-weight="{"bold" if is_chosen else "normal"}">{p95:.0f}</text>'
        )
        parts.append(
            f'<text x="{px(i):.1f}" y="{top + ph + 20}" font-size="12" fill="#333"'
            f' text-anchor="middle">{threads}</text>'
        )

    if chosen_index is not None:
        parts.append(
            f'<line x1="{px(chosen_index):.1f}" y1="{top}" x2="{px(chosen_index):.1f}" y2="{top + ph}"'
            ' stroke="#c0392b" stroke-width="1" stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<text x="{px(chosen_index):.1f}" y="{top + ph + 40}" font-size="11.5" fill="#c0392b"'
            f' text-anchor="middle" font-weight="bold">{LABELS["capacity_chosen"]}</text>'
        )

    if knee < len(points) - 1:
        parts.append(
            f'<text x="{(px(knee) + px(knee + 1)) / 2:.1f}" y="{top + ph - 8}" font-size="11"'
            f' fill="#8a5a00" text-anchor="middle">{LABELS["capacity_knee"]}</text>'
        )

    parts.append(
        f'<text x="{left + pw / 2:.1f}" y="{h - 12}" font-size="12.5" fill="#333"'
        f' text-anchor="middle">{LABELS["capacity_x"]}</text>'
    )
    parts.append(
        f'<text transform="translate(18,{top + ph / 2:.1f}) rotate(-90)" font-size="12.5" fill="#333"'
        f' text-anchor="middle">{LABELS["capacity_y"]}</text>'
    )
    parts.append(f'<text x="{left}" y="26" font-size="14" fill="#111" font-weight="bold">{LABELS["capacity_title"]}</text>')

    write(parts, out)


def comparison_figure(directory: Path, out: Path) -> None:
    """Comparison between the configurations, with the dispersion between repeated runs.
    """
    data: dict[str, tuple[float, float]] = {}
    for config in CONFIGS:
        runs = [read_run(p, APDEX_T, APDEX_F).p95_ms for p in sorted((directory / config).glob("*.jtl"))]
        if runs:
            data[config] = (statistics.fmean(runs), statistics.stdev(runs) if len(runs) > 1 else 0.0)

    if len(data) < 2:
        print(f"  not enough data for the comparison in {directory}", file=sys.stderr)
        return

    w, h = 640, 440
    left, right, top, bottom = 74, 28, 46, 86
    pw, ph = w - left - right, h - top - bottom
    ymax = max(mean + sd for mean, sd in data.values()) * 1.25

    def py(value: float) -> float:
        """Vertical position on the linear scale."""
        return top + ph * (1 - value / ymax)

    parts = svg_open(w, h)

    step = 50 if ymax <= 400 else 100
    tick = 0
    while tick <= ymax:
        yy = py(tick)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + pw}" y2="{yy:.1f}" stroke="#dcdcdc"/>')
        parts.append(f'<text x="{left - 10}" y="{yy + 4:.1f}" font-size="11" fill="#555" text-anchor="end">{tick}</text>')
        tick += step

    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + ph}" stroke="#333" stroke-width="1.2"/>')
    parts.append(f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" stroke="#333" stroke-width="1.2"/>')

    slot = pw / len(data)
    width = slot * 0.42

    for i, (config, (mean, sd)) in enumerate(data.items()):
        cx = left + slot * (i + 0.5)
        parts.append(
            f'<rect x="{cx - width / 2:.1f}" y="{py(mean):.1f}" width="{width:.1f}"'
            f' height="{top + ph - py(mean):.1f}" fill="#9dc3e6" stroke="#1f4e79" stroke-width="1.2"/>'
        )
        parts.append(
            f'<line x1="{cx:.1f}" y1="{py(mean - sd):.1f}" x2="{cx:.1f}" y2="{py(mean + sd):.1f}"'
            ' stroke="#1f4e79" stroke-width="1.6"/>'
        )
        for edge in (mean - sd, mean + sd):
            parts.append(
                f'<line x1="{cx - 10:.1f}" y1="{py(edge):.1f}" x2="{cx + 10:.1f}" y2="{py(edge):.1f}"'
                ' stroke="#1f4e79" stroke-width="1.6"/>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="{py(mean + sd) - 10:.1f}" font-size="12" fill="#1f4e79"'
            f' text-anchor="middle" font-weight="bold">{mean:.0f} ms</text>'
        )
        for offset, chunk in enumerate(LABELS[f"compare_{config.lower()}"].split("\n")):
            parts.append(
                f'<text x="{cx:.1f}" y="{top + ph + 20 + offset * 15}" font-size="12" fill="#333"'
                f' text-anchor="middle">{chunk}</text>'
            )

    parts.append(
        f'<text x="{left + pw / 2:.1f}" y="{h - 12}" font-size="11" fill="#666"'
        f' text-anchor="middle">{LABELS["compare_note"]}</text>'
    )
    parts.append(
        f'<text transform="translate(18,{top + ph / 2:.1f}) rotate(-90)" font-size="12.5" fill="#333"'
        f' text-anchor="middle">{LABELS["compare_y"]}</text>'
    )
    parts.append(f'<text x="{left}" y="26" font-size="14" fill="#111" font-weight="bold">{LABELS["compare_title"]}</text>')

    write(parts, out)


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Generates the phase 1 figures from the raw data.")
    parser.add_argument("--calibration", type=Path, help="Folder produced by calibrate.sh")
    parser.add_argument("--campaign", type=Path, help="Folder produced by run-campaign.sh")
    parser.add_argument("--threads", type=int, default=50, help="Adopted concurrency, highlighted on the curve")
    parser.add_argument("--out", type=Path, default=Path("figures"), help="Destination folder")
    args = parser.parse_args()

    if not any((args.calibration, args.campaign)):
        parser.error("give at least one of --calibration and --campaign")

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Figures in {args.out}:")

    if args.calibration:
        capacity_figure(args.calibration, args.out / "capacity-curve.svg", args.threads)
    if args.campaign:
        comparison_figure(args.campaign, args.out / "configuration-comparison.svg")

    return 0


if __name__ == "__main__":
    sys.exit(main())
