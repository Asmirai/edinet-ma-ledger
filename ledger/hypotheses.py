"""成否を付けた取得で、企画書の仮説（同業・のれん・買う会社の業績・買収前の収益性でそろえた比較）を確かめる。

成否の物差しは2つある。
- 超過 ΔROA：同じ業種の会社の ΔROA の中央値との差
- 買収前の ROA でそろえた超過 ΔROA：買収前の ROA が同じ帯（10分位）の全社の ΔROA の中央値との差
ΔROA は「買収後 − 買収前」なので、買収前の ROA が高い会社ほど下がって見える（平均への回帰）。
買う会社の業績を見る仮説は、そろえた物差しで読む。回帰もそろえた物差しで行う。

使い方：uv run python -m ledger.hypotheses
（ledger.roa と、ledger.industries で買われた会社の業種を先に用意しておく）
"""

import csv
import importlib
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from ledger.industries import INDUSTRIES, read_industries
from ledger.roa import LABELED_PATH

INDUSTRIES_DIR = Path("data/industries")
REPORT_PATH = Path("data/hypotheses.md")
BOOTSTRAP_ROUNDS = 2000
SEED = 20261018
# のれんは取得原価から受け入れた純資産を引いた額なので、債務超過の会社を買えば取得原価を超える。
# それでも 1.5 倍を超えるものは、取得原価の単位（千円・百万円）の読み違いが多かった
MAX_GOODWILL_RATIO = 1.5
# 総資産が小さい会社では ROA が極端な値になる。回帰では、成否の物差しと連続値の列の上下1%を端の値にそろえる
WINSOR = 0.01
QUARTILE_LABELS = ("第1四分位（低い）", "第2四分位", "第3四分位", "第4四分位（高い）")
TERMS = ("same_industry", "goodwill_ratio", "log_size_ratio", "excess_roa_before")
# のれんの割合が出ない案件（のれんの記載が無い・負ののれん・単位の読み違い）を落とさずに、ほかの仮説を見る
TERMS_WITHOUT_GOODWILL = ("same_industry", "log_size_ratio", "excess_roa_before")
# statsmodels には型の情報が無いので、境目で Any として扱う
smf: Any = importlib.import_module("statsmodels.formula.api")


def _number(text: str) -> float | None:
    return float(text) if text else None


def deal_features(row: dict[str, str], acquiree_industry: str | None) -> dict[str, Any]:
    """台帳の1行（CSV の文字列）から、仮説に使う列を出す。分からない値は None。

    のれんの割合は のれん ÷ 取得原価。のれんが書かれていない買収（負ののれんか記載の省略）と、
    割合が 1.5 を超える買収（単位の読み違いが多い）は None にする。
    """
    consideration = _number(row["consideration_yen"])
    goodwill = _number(row["goodwill_yen"])
    goodwill_ratio = None
    if goodwill is not None and consideration is not None and consideration > 0:
        ratio = goodwill / consideration
        goodwill_ratio = ratio if ratio <= MAX_GOODWILL_RATIO else None
    matched = _number(row["matched_excess_delta_roa"])
    known_industries = acquiree_industry is not None and row["industry"] in INDUSTRIES
    return {
        "edinetCode": row["edinetCode"],
        "industry": row["industry"],
        "base_year": int(row["base_year"]),
        "success": None if not row["success"] else row["success"] == "True",
        "matched_success": None if matched is None else matched > 0,
        "delta_roa": _number(row["delta_roa"]),
        "excess_delta_roa": _number(row["excess_delta_roa"]),
        "matched_excess_delta_roa": matched,
        "excess_roa_before": _number(row["excess_roa_before"]),
        "size_ratio": _number(row["size_ratio"]),
        "goodwill_ratio": goodwill_ratio,
        "same_industry": acquiree_industry == row["industry"] if known_industries else None,
    }


def quartiles(deals: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """key の値の小さい順に並べて4つの群に分ける。値が None の案件は入れない。"""
    known = sorted((d for d in deals if d[key] is not None), key=lambda d: d[key])
    size = len(known)
    return {
        label: known[size * index // 4 : size * (index + 1) // 4]
        for index, label in enumerate(QUARTILE_LABELS)
    }


def share(key: str) -> Callable[[list[dict[str, Any]]], float]:
    """key が真の案件の割合を出す関数。None の案件は数えない。"""

    def rate(deals: list[dict[str, Any]]) -> float:
        known = [d[key] for d in deals if d[key] is not None]
        return sum(known) / len(known)

    return rate


def median_of(key: str) -> Callable[[list[dict[str, Any]]], float]:
    """key の中央値を出す関数。None の案件は数えない。"""

    def median(deals: list[dict[str, Any]]) -> float:
        return statistics.median(d[key] for d in deals if d[key] is not None)

    return median


def bootstrap(
    deals: list[dict[str, Any]],
    stats: dict[str, Callable[[list[dict[str, Any]]], float]],
    rng: random.Random,
) -> dict[str, float]:
    """会社を単位にしたブートストラップで、各統計量の値と95%の幅（name_low・name_high）を出す。

    同じ会社の買収どうしは似るので、会社を復元抽出し、選んだ会社の案件をまとめて入れる。
    """
    by_company: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for deal in deals:
        by_company[deal["edinetCode"]].append(deal)
    companies = list(by_company.values())
    rounds: defaultdict[str, list[float]] = defaultdict(list)
    for _ in range(BOOTSTRAP_ROUNDS):
        sample = [d for company in rng.choices(companies, k=len(companies)) for d in company]
        for name, compute in stats.items():
            rounds[name].append(compute(sample))
    result: dict[str, float] = {}
    for name, compute in stats.items():
        cuts = statistics.quantiles(rounds[name], n=40)
        result |= {name: compute(deals), f"{name}_low": cuts[0], f"{name}_high": cuts[-1]}
    return result


GROUP_STATS = {
    "success_rate": share("success"),
    "matched_success_rate": share("matched_success"),
    "median_excess": median_of("excess_delta_roa"),
    "median_matched": median_of("matched_excess_delta_roa"),
}


def summarize(groups: dict[str, list[dict[str, Any]]], seed: int) -> list[dict[str, Any]]:
    """群ごとに、件数、成功率（2つの物差し）、中央値（2つの物差し）と、それぞれの95%の幅を出す。

    幅のキーは success_low・matched_success_low・excess_low・matched_low（high も同じ）。
    """
    rng = random.Random(seed)
    summaries: list[dict[str, Any]] = []
    for name, deals in groups.items():
        values = bootstrap(deals, GROUP_STATS, rng)
        summaries.append(
            {"group": name, "n": len(deals)}
            | values
            | {
                "success_low": values["success_rate_low"],
                "success_high": values["success_rate_high"],
                "matched_success_low": values["matched_success_rate_low"],
                "matched_success_high": values["matched_success_rate_high"],
                "excess_low": values["median_excess_low"],
                "excess_high": values["median_excess_high"],
                "matched_low": values["median_matched_low"],
                "matched_high": values["median_matched_high"],
            }
        )
    return summaries


def fit_regression(
    deals: list[dict[str, Any]], outcome: str, terms: tuple[str, ...]
) -> dict[str, Any]:
    """成否の物差し（outcome の列）を terms の列で回帰する。

    基準の年と業種の違いは固定効果で除く。同じ会社の買収どうしは似るので、標準誤差は会社ごとにまとめて出す。
    """
    needed = [t for t in terms if t != "log_size_ratio"] + [outcome]
    complete = [
        d
        for d in deals
        if all(d[k] is not None for k in needed)
        and ("log_size_ratio" not in terms or (d["size_ratio"] or 0) > 0)
    ]
    columns: dict[str, list[Any]] = {
        "outcome": [d[outcome] for d in complete],
        "base_year": [d["base_year"] for d in complete],
        "industry": [d["industry"] for d in complete],
        "edinetCode": [d["edinetCode"] for d in complete],
    }
    for term in terms:
        if term == "log_size_ratio":
            columns[term] = [math.log(d["size_ratio"]) for d in complete]
        else:
            columns[term] = [float(d[term]) for d in complete]
    frame = pd.DataFrame(columns)
    for column in ("outcome", *(t for t in terms if t != "same_industry")):
        low, high = frame[column].quantile([WINSOR, 1 - WINSOR])
        frame[column] = frame[column].clip(low, high)
    formula = "outcome ~ " + " + ".join(terms) + " + C(base_year) + C(industry)"
    fitted = smf.ols(formula, data=frame).fit(
        cov_type="cluster", cov_kwds={"groups": frame["edinetCode"].astype("category").cat.codes}
    )
    intervals = fitted.conf_int()
    result: dict[str, Any] = {"n": int(fitted.nobs)}
    for term in terms:
        result[term] = {
            "coef": float(fitted.params[term]),
            "low": float(intervals.loc[term, 0]),
            "high": float(intervals.loc[term, 1]),
            "p": float(fitted.pvalues[term]),
        }
    return result


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _pt(value: float) -> str:
    return f"{value * 100:+.2f}pt"


def _table(summaries: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 群 | 件数 | 成功率 | 95%の幅 | 成功率（そろえた） | 95%の幅 "
        "| 超過 ΔROA の中央値 | 95%の幅 | そろえた超過 ΔROA の中央値 | 95%の幅 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        cells = [
            s["group"],
            str(s["n"]),
            _pct(s["success_rate"]),
            f"{_pct(s['success_low'])}〜{_pct(s['success_high'])}",
            _pct(s["matched_success_rate"]),
            f"{_pct(s['matched_success_low'])}〜{_pct(s['matched_success_high'])}",
            _pt(s["median_excess"]),
            f"{_pt(s['excess_low'])}〜{_pt(s['excess_high'])}",
            _pt(s["median_matched"]),
            f"{_pt(s['matched_low'])}〜{_pt(s['matched_high'])}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _regression_table(deals: list[dict[str, Any]], terms: tuple[str, ...], title: str) -> list[str]:
    regression = fit_regression(deals, "matched_excess_delta_roa", terms)
    lines = [
        f"#### 回帰：{title}（{regression['n']}件）",
        "",
        "| 列 | 係数 | 95%の幅 | p 値 |",
        "|---|---|---|---|",
    ]
    for term in terms:
        r = regression[term]
        lines.append(
            f"| {term} | {r['coef']:+.4f} | {r['low']:+.4f}〜{r['high']:+.4f} | {r['p']:.3f} |"
        )
    return [*lines, ""]


def _section(deals: list[dict[str, Any]]) -> list[str]:
    same = {
        "同業を買った": [d for d in deals if d["same_industry"] is True],
        "別の業種を買った": [d for d in deals if d["same_industry"] is False],
    }
    lines = ["#### 同業を買うほうがうまくいく", *_table(summarize(same, SEED)), ""]
    lines += ["#### 高く買う（のれんの割合が大きい）ほど失敗する"]
    lines += [*_table(summarize(quartiles(deals, "goodwill_ratio"), SEED)), ""]
    lines += [
        "#### 業績の良い会社（買収前の ROA が同業より高い）が買うとうまくいく（そろえた物差しで読む）"
    ]
    lines += [*_table(summarize(quartiles(deals, "excess_roa_before"), SEED)), ""]
    lines += _regression_table(deals, TERMS, "のれんの割合も入れる（のれんの割合が出る案件だけ）")
    lines += _regression_table(deals, TERMS_WITHOUT_GOODWILL, "のれんの割合を入れない")
    return lines


def build_report(deals: list[dict[str, Any]]) -> str:
    """仮説ごとの表と回帰の結果を Markdown で返す。全件、大きい買収だけ、1社1件の3通りで出す。"""
    big = [d for d in deals if d["size_ratio"] is not None and d["size_ratio"] >= 0.05]
    first: dict[str, dict[str, Any]] = {}
    for d in sorted(deals, key=lambda d: d["base_year"]):
        first.setdefault(d["edinetCode"], d)
    no_ratio = sum(1 for d in deals if d["goodwill_ratio"] is None)
    lines = [
        "# 仮説を確かめた結果",
        "",
        "- 95%の幅は、会社を単位にしたブートストラップ（2,000回）",
        "- 「そろえた」は、買収前の ROA が同じ帯（10分位）の全社の ΔROA の中央値と比べたもの",
        "- 回帰の物差しは、そろえた超過 ΔROA。基準の年と業種の固定効果を入れ、標準誤差は会社ごとにまとめる。"
        "成否の物差しと連続値の列は上下1%を端の値にそろえる",
        f"- のれんの割合が出ない案件は {no_ratio} 件（のれんの記載が無い・負ののれん・"
        f"割合が {MAX_GOODWILL_RATIO} を超え単位の読み違いが多いもの）",
        "",
    ]
    for title, subset in (
        ("全件", deals),
        ("取得原価が買収前の総資産の5%以上", big),
        ("1社につき最初の1件", list(first.values())),
    ):
        lines += [f"## {title}（{len(subset)}件）", "", *_section(subset)]
    lines += ["## 買収後に ROA が下がるのは、買収前の収益性でそろえると消えるか", ""]
    lines += [
        "| 比べる相手 | ΔROA の中央値 | 95%の幅 | 下がった割合 | 95%の幅 |",
        "|---|---|---|---|---|",
    ]
    rng = random.Random(SEED)
    for label, key in (
        ("比べない（そのままの ΔROA）", "delta_roa"),
        ("同じ業種の中央値", "excess_delta_roa"),
        ("買収前の ROA が同じ帯（10分位）の全社の中央値", "matched_excess_delta_roa"),
    ):
        rows = [d | {"value": d[key], "declined": d[key] < 0} for d in deals if d[key] is not None]
        v = bootstrap(rows, {"median": median_of("value"), "declined": share("declined")}, rng)
        lines.append(
            f"| {label} | {_pt(v['median'])} | {_pt(v['median_low'])}〜{_pt(v['median_high'])} "
            f"| {_pct(v['declined'])} | {_pct(v['declined_low'])}〜{_pct(v['declined_high'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    industries = read_industries(INDUSTRIES_DIR)
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    deals = [
        deal_features(row, industries.get(f"{row['deal_id']}-{row['edinetCode']}"))
        for row in rows
        if row["primary"] == "True" and row["success"]
    ]
    REPORT_PATH.write_text(build_report(deals), encoding="utf-8")
    counts: defaultdict[str, int] = defaultdict(int)
    for d in deals:
        counts["成功" if d["success"] else "失敗"] += 1
    print(f"{len(deals)} 件（{dict(counts)}）：{REPORT_PATH}")


if __name__ == "__main__":
    main()
