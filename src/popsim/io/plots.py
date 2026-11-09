"""matplotlib plots built from a run's CSV output, so old runs can be re-plotted."""

from __future__ import annotations

from pathlib import Path

from popsim.io.logs import read_csv

CITY_COLORS = ["#2a6fdb", "#e0731d", "#2e9e5b", "#b03a8c", "#7a6a2f"]


def _mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _smooth(vals: list, window: int) -> list:
    out, buf = [], []
    for v in vals:
        if v is not None:
            buf.append(v)
        buf = buf[-window:]
        out.append(sum(buf) / len(buf) if buf else None)
    return out


def _series(rows, city, good, key="vwap"):
    rs = sorted((r for r in rows if r["city"] == city and r["good"] == good), key=lambda r: r["day"])
    return [r["day"] for r in rs], [r[key] for r in rs]


def _event_lines(ax, events):
    for ev in events:
        ax.axvline(ev["day"], color="#888", ls="--", lw=1)
        ax.text(ev["day"], ax.get_ylim()[1], f" {ev['type']}", fontsize=8, color="#555", va="top")


def plot_run(run_dir: str | Path, smooth_days: int = 3) -> list[Path]:
    plt = _mpl()
    run = Path(run_dir)
    prices = read_csv(run / "prices_daily.csv")
    cities = read_csv(run / "cities_daily.csv")
    events = read_csv(run / "events.csv")
    flows = read_csv(run / "trade_flows.csv")
    names = sorted({r["city"] for r in cities})
    goods = sorted({r["good"] for r in prices})
    colors = {n: CITY_COLORS[i % len(CITY_COLORS)] for i, n in enumerate(names)}
    written = []

    fig, axes = plt.subplots(len(goods), 1, figsize=(10, 3.2 * len(goods)), sharex=True, squeeze=False)
    for ax, good in zip(axes[:, 0], goods):
        for c in names:
            days, v = _series(prices, c, good)
            ax.plot(days, _smooth(v, smooth_days), color=colors[c], lw=2, label=f"city {c}")
            ax.scatter(days, v, color=colors[c], s=6, alpha=0.35)
        ax.set_ylabel(f"{good} price (gold)")
        ax.set_yscale("log")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left", frameon=False)
        _event_lines(ax, events)
    axes[-1, 0].set_xlabel("day")
    fig.suptitle(f"Daily VWAP ({smooth_days}-day rolling mean; dots = daily)")
    fig.tight_layout()
    written.append(run / "prices.png")
    fig.savefig(written[-1], dpi=120)
    plt.close(fig)

    panels = [("population", "population"), ("mean_gold", "mean gold per NPC"),
              ("gini", "gold Gini"), ("mean_satiety", "mean satiety"),
              ("mean_daily_max", "mean daily_max stamina"), ("sick", "sick NPCs")]
    fig, axes = plt.subplots(3, 2, figsize=(11, 8), sharex=True)
    for ax, (key, label) in zip(axes.flat, panels):
        for c in names:
            rs = sorted((r for r in cities if r["city"] == c), key=lambda r: r["day"])
            ax.plot([r["day"] for r in rs], [r[key] for r in rs], color=colors[c], lw=1.8, label=c)
        ax.set_title(label, fontsize=10)
        ax.grid(alpha=0.3)
        _event_lines(ax, events)
    axes[0, 0].legend(frameon=False)
    fig.tight_layout()
    written.append(run / "society.png")
    fig.savefig(written[-1], dpi=120)
    plt.close(fig)

    if flows:
        fig, ax = plt.subplots(figsize=(10, 3.5))
        routes = sorted({(f["from"], f["to"], f["good"]) for f in flows})
        for route in routes:
            fs = sorted((f for f in flows if (f["from"], f["to"], f["good"]) == route), key=lambda f: f["tick"])
            total, xs, ys = 0, [], []
            for f in fs:
                total += f["qty"]
                xs.append(f["day"])
                ys.append(total)
            ax.step(xs, ys, where="post", lw=2, label=f"{route[2]}: {route[0]} → {route[1]}")
        ax.set_ylabel("cumulative units shipped")
        ax.set_xlabel("day")
        ax.grid(alpha=0.3)
        ax.legend(frameon=False)
        _event_lines(ax, events)
        fig.tight_layout()
        written.append(run / "trade_flows.png")
        fig.savefig(written[-1], dpi=120)
        plt.close(fig)
    return written


def plot_compare(run_dirs: list[str | Path], labels: list[str], out: str | Path,
                 smooth_days: int = 3) -> Path:
    """Overlay the same city/good price series from several runs."""
    plt = _mpl()
    runs = [(lab, read_csv(Path(d) / "prices_daily.csv"), read_csv(Path(d) / "events.csv"))
            for d, lab in zip(run_dirs, labels)]
    names = sorted({r["city"] for _, rows, _ in runs for r in rows})
    goods = sorted({r["good"] for _, rows, _ in runs for r in rows})
    fig, axes = plt.subplots(len(goods), len(names), figsize=(5 * len(names), 3.2 * len(goods)),
                             sharex=True, squeeze=False)
    styles = ["-", "--", ":", "-."]
    for gi, good in enumerate(goods):
        for ci, c in enumerate(names):
            ax = axes[gi, ci]
            for ri, (lab, rows, _) in enumerate(runs):
                days, v = _series(rows, c, good)
                ax.plot(days, _smooth(v, smooth_days), ls=styles[ri % 4], lw=2, label=lab)
            ax.set_title(f"{good} in {c}", fontsize=10)
            ax.set_yscale("log")
            ax.grid(alpha=0.3)
            for ev in runs[0][2] + [e for _, _, evs in runs[1:] for e in evs]:
                ax.axvline(ev["day"], color="#999", ls="--", lw=0.8)
    axes[0, 0].legend(frameon=False)
    fig.tight_layout()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
