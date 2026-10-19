"""記事の図を作る。値はすべて data の下の台帳と、ledger.factors・ledger.scorer の計算から取る。

図の決まり：タイトルに結論を書いて左にそろえる。凡例は使わず、値を図に直接書く。
色は灰色を基本にし、安全な側と危ない側だけに色を付ける。件数と出典を必ず入れる。
フォントは macOS のヒラギノ角ゴシック。

使い方：uv run --group figures python -m ledger.figures <出力するフォルダ>
"""

import importlib
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from ledger.factors import SEED, factor_summary, load_deals
from ledger.purposes import LAST_BASE_YEAR
from ledger.scorer import LAST_TRAIN_YEAR, complete_rows, fit_scorer, holdout, predict, price_curve
from ledger.scorer import deal_inputs as scorer_inputs

# matplotlib の型の情報は一部しか無いので、境目で Any として扱う
matplotlib: Any = importlib.import_module("matplotlib")
matplotlib.use("Agg")
plt: Any = importlib.import_module("matplotlib.pyplot")
font_manager: Any = importlib.import_module("matplotlib.font_manager")

FONT_DIR = Path("/System/Library/Fonts")
REGULAR = font_manager.FontProperties(fname=FONT_DIR / "ヒラギノ角ゴシック W3.ttc")
BOLD = font_manager.FontProperties(fname=FONT_DIR / "ヒラギノ角ゴシック W6.ttc")
INK = "#1f2328"
GREY = "#6e7781"
LIGHT = "#d0d7de"
SAFE = "#2a6f97"
RISK = "#c8553d"
SOURCE = (
    "出典：EDINET（公共データ利用規約 第1.0版）。合同会社Asmirai が有価証券報告書から抜き出し、加工"
)
# 架空の買い手と、その候補。金額の単位は億円
ACQUIRER = {"acquirer_assets": 500.0, "equity_ratio": 0.55}
CANDIDATES: list[dict[str, Any]] = [
    {
        "name": "B社",
        "label": "同じ事業",
        "price": 15.0,
        "target_net_assets": 12.0,
        "relatedness": "同じ事業",
    },
    {
        "name": "C社",
        "label": "近い事業",
        "price": 20.0,
        "target_net_assets": 6.0,
        "relatedness": "近い事業",
    },
    {
        "name": "D社",
        "label": "遠い事業",
        "price": 30.0,
        "target_net_assets": 3.0,
        "relatedness": "遠い事業",
    },
    {
        "name": "E社",
        "label": "同じ事業",
        "price": 40.0,
        "target_net_assets": 2.0,
        "relatedness": "同じ事業",
    },
]
CURVE_CANDIDATE = "C社"


def _figure(title: str, subtitle: str, note: str) -> Any:
    plt.rcParams["font.family"] = REGULAR.get_name()
    font_manager.fontManager.addfont(str(FONT_DIR / "ヒラギノ角ゴシック W3.ttc"))
    fig = plt.figure(figsize=(8, 4.5), dpi=200, facecolor="white")
    fig.text(0.04, 0.925, title, fontproperties=BOLD, fontsize=15, color=INK)
    fig.text(0.04, 0.865, subtitle, fontproperties=REGULAR, fontsize=9, color=GREY)
    fig.text(0.04, 0.055, note, fontproperties=REGULAR, fontsize=7, color=GREY)
    fig.text(0.04, 0.025, SOURCE, fontproperties=REGULAR, fontsize=7, color=GREY)
    return fig


def _clean(ax: Any, keep_bottom: bool = True) -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_visible(keep_bottom)
    ax.spines["bottom"].set_color(LIGHT)
    ax.tick_params(length=0, colors=INK, labelsize=9)
    ax.set_yticks([])
    for label in ax.get_xticklabels():
        label.set_fontproperties(REGULAR)


def _one_in(rate: float) -> str:
    return f"{round(1 / rate)}件に1件"


def holdout_groups(deals: list[dict[str, Any]], path: Path) -> None:
    result = holdout(deals, LAST_TRAIN_YEAR)
    groups = result["groups"]
    rates = [g["rate"] * 100 for g in groups]
    ratio = groups[-1]["rate"] / groups[0]["rate"]
    fig = _figure(
        f"買う前の4つの数字だけで、失敗率に約{round(ratio)}倍の差がついた",
        f"{LAST_TRAIN_YEAR}年度までの買収で作ったモデルを、作るときに使っていない"
        f"{LAST_TRAIN_YEAR + 1}〜{LAST_BASE_YEAR}年度の{result['n']}件で試した",
        "失敗＝買収から3年以内に、払いすぎを認めて損失にしたもの（のれんの減損）。",
    )
    ax = fig.add_axes((0.06, 0.24, 0.9, 0.54))
    ax.set_xlabel(
        "モデルが出した失敗の確率の低い順に、5等分したグループ",
        fontproperties=REGULAR,
        fontsize=8.5,
        color=GREY,
        labelpad=6,
    )
    colors = [SAFE, LIGHT, LIGHT, LIGHT, RISK]
    labels = [
        f"最も安全な20%\n（{groups[0]['n']}件）",
        "",
        "",
        "",
        f"最も危ない20%\n（{groups[-1]['n']}件）",
    ]
    ax.bar(range(5), rates, color=colors, width=0.62)
    for i, (rate, group) in enumerate(zip(rates, groups)):
        ax.text(
            i,
            rate + 0.8,
            f"{rate:.1f}%",
            ha="center",
            fontproperties=BOLD,
            fontsize=13,
            color=INK if i in (0, 4) else GREY,
        )
        if i in (0, 4):
            ax.text(
                i,
                rate + 4,
                _one_in(group["rate"]),
                ha="center",
                fontproperties=REGULAR,
                fontsize=9,
                color=colors[i],
            )
    base = result["base_rate"] * 100
    ax.axhline(base, color=GREY, lw=0.8, ls=(0, (3, 3)), zorder=0)
    ax.text(
        -0.45,
        base + 0.6,
        f"{result['n']}件全体 {base:.1f}%",
        ha="left",
        fontproperties=REGULAR,
        fontsize=8,
        color=GREY,
    )
    ax.set_xticks(range(5), labels)
    ax.set_ylim(0, max(rates) + 8)
    _clean(ax)
    fig.savefig(path)


def size_buckets(deals: list[dict[str, Any]], path: Path) -> None:
    sizes = [
        d["size_ratio"] for d in deals if d["impaired"] is not None and d["size_ratio"] is not None
    ]
    edges = [0, 0.01, 0.02, 0.05, 0.1, math.inf]
    labels = ["1%未満", "1〜2%", "2〜5%", "5〜10%", "10%以上"]
    shares = [
        sum(lo <= s < hi for s in sizes) / len(sizes) * 100 for lo, hi in zip(edges, edges[1:])
    ]
    fig = _figure(
        "買収の約半分は、買い手の総資産の2%にも満たない",
        f"買収額 ÷ 買った会社の総資産（買収の直前の期）。分析した買収のうち、値の分かる{len(sizes):,}件",
        "これだけ小さいと、1件の買収の良し悪しは、会社全体の利益率（ROA）の上下に埋もれる。",
    )
    ax = fig.add_axes((0.06, 0.22, 0.9, 0.54))
    colors = [INK, INK, LIGHT, LIGHT, LIGHT]
    ax.bar(range(5), shares, color=colors, width=0.62)
    for i, share in enumerate(shares):
        ax.text(
            i,
            share + 1,
            f"{share:.0f}%",
            ha="center",
            fontproperties=BOLD,
            fontsize=13,
            color=INK if i < 2 else GREY,
        )
    ax.set_xticks(range(5), labels)
    ax.set_ylim(0, max(shares) + 8)
    _clean(ax)
    fig.savefig(path)


def _ends(deals: list[dict[str, Any]], factor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = factor_summary(deals, factor, "impaired", SEED)
    if factor == "relatedness":
        by_name = {r["group"]: r for r in rows}
        return by_name["同じ事業"], by_name["遠い事業"]
    return rows[0], rows[-1]


def _percent(value: float) -> str:
    return f"{value:.1%}" if value < 0.01 else f"{value:.0%}"


def _bounds(deals: list[dict[str, Any]], factor: str) -> tuple[float, float]:
    """factor_summary と同じ四分位の境目のうち、両端の群を分ける2つ（第1四分位は未満、第4四分位は以上）。"""
    cuts = statistics.quantiles([d[factor] for d in deals if d[factor] is not None], n=4)
    return cuts[0], cuts[2]


def signals(deals: list[dict[str, Any]], path: Path) -> None:
    items: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    safe, risky = _ends(deals, "relatedness")
    items.append(("事業の近さ", "同じ事業 → 遠い事業", safe, risky))
    for factor, name in (
        ("goodwill_ratio", "上乗せ率"),
        ("equity_ratio", "自社の自己資本比率"),
        ("size_ratio", "自社に対する大きさ"),
    ):
        low, high = _bounds(deals, factor)
        safe, risky = _ends(deals, factor)
        items.append((name, f"{_percent(low)}未満 → {_percent(high)}以上", safe, risky))
    # 差の大きい（高い側 ÷ 低い側が大きい）数字を上に置く
    items.sort(key=lambda item: item[3]["rate"] / item[2]["rate"])
    fig = _figure(
        "4つの数字は、どれも失敗率をはっきり分けた",
        "それぞれの数字で、最も低い4分の1と最も高い4分の1（事業の近さは、同じ事業と遠い事業）の失敗率",
        "上乗せ率＝（買収額 − 相手の純資産）÷ 買収額。大きさ＝買収額 ÷ 自社の総資産。境目は全件の4分の1ずつに分けた値。",
    )
    ax = fig.add_axes((0.34, 0.2, 0.6, 0.58))
    for y, (name, detail, safe, risky) in enumerate(items):
        lo, hi = safe["rate"] * 100, risky["rate"] * 100
        ax.plot([lo, hi], [y, y], color=LIGHT, lw=3, solid_capstyle="round", zorder=1)
        ax.scatter([lo], [y], s=90, color=SAFE, zorder=2)
        ax.scatter([hi], [y], s=90, color=RISK, zorder=2)
        ax.text(
            lo - 0.8,
            y,
            f"{lo:.1f}%",
            ha="right",
            va="center",
            fontproperties=BOLD,
            fontsize=11,
            color=SAFE,
        )
        ax.text(
            hi + 0.8,
            y,
            f"{hi:.1f}%",
            ha="left",
            va="center",
            fontproperties=BOLD,
            fontsize=11,
            color=RISK,
        )
        fig.text(
            0.04,
            0.2 + 0.58 * (y + 0.5) / len(items) + 0.012,
            name,
            fontproperties=BOLD,
            fontsize=10.5,
            color=INK,
            va="center",
        )
        fig.text(
            0.04,
            0.2 + 0.58 * (y + 0.5) / len(items) - 0.035,
            detail,
            fontproperties=REGULAR,
            fontsize=8,
            color=GREY,
            va="center",
        )
    ax.set_ylim(-0.5, len(items) - 0.5)
    ax.set_xlim(0, max(item[3]["rate"] for item in items) * 100 + 6)
    ax.set_xticks([0, 10, 20, 30], ["0%", "10%", "20%", "30%"])
    _clean(ax)
    fig.savefig(path)


def confounding(deals: list[dict[str, Any]], path: Path) -> None:
    known = [d for d in deals if d["impaired"] is not None and d["purpose"] and d["relatedness"]]
    entry = [d for d in known if d["purpose"] == "新しい事業への参入"]
    other = [d for d in known if d["purpose"] != "新しい事業への参入"]

    def rate(group: list[dict[str, Any]]) -> float:
        return sum(d["impaired"] for d in group) / len(group) * 100

    def far(group: list[dict[str, Any]]) -> float:
        return sum(d["relatedness"] == "遠い事業" for d in group) / len(group) * 100

    fig = _figure(
        "新規参入が危なく見えたのは、畑違いの買収が多く混ざっていたから",
        f"買う目的が新しい事業への参入だった買収（{len(entry)}件）と、それ以外（{len(other):,}件）",
        "事業の近さは、有価証券報告書の「企業結合を行った主な理由」を AI に読ませて分けた。",
    )
    left = fig.add_axes((0.06, 0.22, 0.38, 0.48))
    right = fig.add_axes((0.56, 0.22, 0.4, 0.48))
    fig.text(0.06, 0.76, "遠い事業の買収が占める割合", fontproperties=BOLD, fontsize=10, color=INK)
    fig.text(
        0.56,
        0.76,
        "失敗率：すべての買収と、事業が「近い」買収だけ",
        fontproperties=BOLD,
        fontsize=10,
        color=INK,
    )
    shares = [far(entry), far(other)]
    left.bar([0, 1], shares, color=[RISK, LIGHT], width=0.55)
    for i, share in enumerate(shares):
        left.text(
            i,
            share + 1.5,
            f"{share:.0f}%",
            ha="center",
            fontproperties=BOLD,
            fontsize=13,
            color=RISK if i == 0 else GREY,
        )
    left.set_xticks([0, 1], ["新規参入", "それ以外"])
    left.set_ylim(0, max(shares) * 1.3)
    _clean(left)
    near_entry = [d for d in entry if d["relatedness"] == "近い事業"]
    near_other = [d for d in other if d["relatedness"] == "近い事業"]
    pairs = [
        ("すべて", rate(entry), rate(other)),
        ("近い事業だけ", rate(near_entry), rate(near_other)),
    ]
    for i, (_, a, b) in enumerate(pairs):
        right.bar(i - 0.17, a, width=0.32, color=RISK)
        right.bar(i + 0.17, b, width=0.32, color=LIGHT)
        right.text(
            i - 0.17, a + 0.8, f"{a:.1f}%", ha="center", fontproperties=BOLD, fontsize=11, color=INK
        )
        right.text(
            i + 0.17,
            b + 0.8,
            f"{b:.1f}%",
            ha="center",
            fontproperties=BOLD,
            fontsize=11,
            color=GREY,
        )
        right.text(
            i - 0.17, 1, "参入", ha="center", fontproperties=REGULAR, fontsize=7.5, color="white"
        )
        right.text(
            i + 0.17, 1, "それ以外", ha="center", fontproperties=REGULAR, fontsize=7.5, color=GREY
        )
    right.set_xticks([0, 1], [p[0] for p in pairs])
    right.set_ylim(0, max(max(a, b) for _, a, b in pairs) * 1.25)
    _clean(right)
    fig.savefig(path)


def _candidate_inputs(candidate: dict[str, Any]) -> dict[str, Any]:
    return scorer_inputs(
        candidate["price"],
        candidate["target_net_assets"],
        ACQUIRER["acquirer_assets"],
        ACQUIRER["equity_ratio"],
        candidate["relatedness"],
    )


def candidates(deals: list[dict[str, Any]], path: Path) -> None:
    model = fit_scorer(deals)
    rows = complete_rows(deals)
    base = sum(bool(r["impaired"]) for r in rows) / len(rows) * 100
    years = [r["base_year"] for r in rows]
    probabilities = predict(model, [_candidate_inputs(c) for c in CANDIDATES])
    scored = sorted(zip(probabilities, CANDIDATES), key=lambda pair: pair[0], reverse=True)
    fig = _figure(
        f"同じ買い手でも、候補によって失敗の確率は{min(probabilities):.0%}から{max(probabilities):.0%}まで開く",
        f"架空の A 社（総資産{ACQUIRER['acquirer_assets']:.0f}億円、自己資本比率"
        f"{ACQUIRER['equity_ratio']:.0%}）が4社を検討しているとき。モデルは{min(years)}〜{max(years)}年度の{len(rows):,}件で学習",
        "確率は、過去の似た買収のうち、3年以内に払いすぎを認めて損失にした（のれんを減損した）割合の見積もり。",
    )
    ax = fig.add_axes((0.36, 0.2, 0.58, 0.57))
    for y, (probability, candidate) in enumerate(scored):
        value = probability * 100
        inputs = _candidate_inputs(candidate)
        ax.barh(y, value, color=RISK if value > base else SAFE, height=0.55)
        ax.text(
            value + 0.6,
            y,
            f"{value:.0f}%",
            va="center",
            fontproperties=BOLD,
            fontsize=13,
            color=RISK if value > base else SAFE,
        )
        top = 0.2 + 0.57 * (y + 0.5) / len(scored)
        fig.text(
            0.04,
            top + 0.015,
            f"{candidate['name']}（{candidate['label']}）",
            fontproperties=BOLD,
            fontsize=10.5,
            color=INK,
            va="center",
        )
        fig.text(
            0.04,
            top - 0.035,
            f"{candidate['price']:.0f}億円で買う・上乗せ率{inputs['goodwill_ratio']:.0%}",
            fontproperties=REGULAR,
            fontsize=8,
            color=GREY,
            va="center",
        )
    ax.axvline(base, color=GREY, lw=0.8, ls=(0, (3, 3)), zorder=0)
    ax.text(
        base,
        len(scored) - 0.45,
        f"過去の平均 {base:.1f}%",
        ha="center",
        fontproperties=REGULAR,
        fontsize=8,
        color=GREY,
    )
    ax.set_ylim(-0.6, len(scored) - 0.3)
    ax.set_xlim(0, max(p for p, _ in scored) * 100 + 8)
    ax.set_xticks([])
    _clean(ax, keep_bottom=False)
    fig.savefig(path)


def curve(deals: list[dict[str, Any]], path: Path) -> None:
    model = fit_scorer(deals)
    rows = complete_rows(deals)
    base = sum(bool(r["impaired"]) for r in rows) / len(rows)
    candidate = next(c for c in CANDIDATES if c["name"] == CURVE_CANDIDATE)
    prices = [candidate["target_net_assets"] * (1 + i / 20) for i in range(1, 400)]
    values = price_curve(
        model,
        candidate["target_net_assets"],
        ACQUIRER["acquirer_assets"],
        ACQUIRER["equity_ratio"],
        candidate["relatedness"],
        prices,
    )
    if values[0] > base or values[-1] <= base:
        raise ValueError(f"{candidate['name']}の確率が、値段の範囲の中で平均を上回らない")
    limit = next(p for p, v in zip(prices, values) if v > base)
    shown = [(p, v) for p, v in zip(prices, values) if p <= limit * 2.2]
    fig = _figure(
        f"{candidate['name']}を{limit:.0f}億円より高く買うと、似た過去の買収の失敗率は平均を上回る",
        f"{candidate['name']}（純資産{candidate['target_net_assets']:.0f}億円、{candidate['label']}）の"
        "買収額だけを変えて、モデルに入れたときの失敗の確率",
        "買収額が上がると、上乗せ率と、自社に対する大きさの両方が上がる。",
    )
    ax = fig.add_axes((0.08, 0.2, 0.86, 0.56))
    xs = [p for p, _ in shown]
    ys = [v * 100 for _, v in shown]
    ax.plot(
        [x for x in xs if x <= limit], [y for x, y in zip(xs, ys) if x <= limit], color=SAFE, lw=2.5
    )
    ax.plot(
        [x for x in xs if x >= limit], [y for x, y in zip(xs, ys) if x >= limit], color=RISK, lw=2.5
    )
    ax.axhline(base * 100, color=GREY, lw=0.8, ls=(0, (3, 3)), zorder=0)
    ax.text(
        xs[0],
        base * 100 + 0.4,
        f"過去の平均 {base:.1%}",
        fontproperties=REGULAR,
        fontsize=8,
        color=GREY,
    )
    ax.axvline(limit, color=INK, lw=0.8, zorder=0)
    ax.text(
        limit + 0.5, min(ys) + 0.3, f"{limit:.0f}億円", fontproperties=BOLD, fontsize=12, color=INK
    )
    current = candidate["price"]
    current_value = predict(model, [_candidate_inputs(candidate)])[0] * 100
    ax.scatter([current], [current_value], s=50, color=INK, zorder=3)
    ax.text(
        current,
        current_value - 1.6,
        f"いまの提示額 {current:.0f}億円なら {current_value:.0f}%",
        ha="left",
        fontproperties=REGULAR,
        fontsize=8.5,
        color=INK,
    )
    ticks = [t for t in range(10, int(xs[-1]) + 1, 10)]
    ax.set_xticks(ticks, [f"{t}億円" for t in ticks])
    _clean(ax)
    low, high = math.floor(min(ys) / 5) * 5, math.ceil(max(ys) / 5) * 5
    ax.set_yticks(range(low, high + 1, 5), [f"{v}%" for v in range(low, high + 1, 5)])
    ax.tick_params(axis="y", labelsize=8, labelcolor=GREY)
    ax.grid(axis="y", color=LIGHT, lw=0.5)
    ax.set_axisbelow(True)
    fig.savefig(path)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    deals = load_deals()
    holdout_groups(deals, out / "fig1_holdout.png")
    size_buckets(deals, out / "fig2_size.png")
    signals(deals, out / "fig3_signals.png")
    confounding(deals, out / "fig4_confounding.png")
    candidates(deals, out / "fig5_candidates.png")
    curve(deals, out / "fig6_price_curve.png")


if __name__ == "__main__":
    main()
