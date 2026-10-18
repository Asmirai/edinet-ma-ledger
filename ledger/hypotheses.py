"""成否を付けた取得で、企画書の仮説（同業・のれん・買う会社の業績・買収前の収益性でそろえた比較）を確かめる。

使い方：uv run python -m ledger.hypotheses
（ledger.roa と、ledger.industries で買われた会社の業種を先に用意しておく）
"""

import csv
import importlib
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from ledger.industries import read_industries
from ledger.roa import LABELED_PATH

INDUSTRIES_DIR = Path("data/industries")
REPORT_PATH = Path("data/hypotheses.md")
BOOTSTRAP_ROUNDS = 2000
SEED = 20261018
# 総資産が小さい会社では ROA が、取得原価が1円のような案件ではのれんの割合が極端な値になる。
# 回帰では、成否の物差しと連続値の列の上下1%を端の値にそろえる
WINSOR = 0.01
QUARTILE_LABELS = ("第1四分位（低い）", "第2四分位", "第3四分位", "第4四分位（高い）")
TERMS = ("same_industry", "goodwill_ratio", "log_size_ratio", "excess_roa_before")
# statsmodels には型の情報が無いので、境目で Any として扱う
smf: Any = importlib.import_module("statsmodels.formula.api")


def _number(text: str) -> float | None:
    return float(text) if text else None


def deal_features(row: dict[str, str], acquiree_industry: str | None) -> dict[str, Any]:
    """台帳の1行（CSV の文字列）から、仮説に使う列を出す。分からない値は None。

    のれんの割合は のれん ÷ 取得原価。のれんが書かれていない買収は、負ののれんか記載の省略なので None にする。
    """
    consideration = _number(row["consideration_yen"])
    goodwill = _number(row["goodwill_yen"])
    goodwill_ratio = None
    if goodwill is not None and consideration is not None and consideration > 0:
        goodwill_ratio = goodwill / consideration
    return {
        "edinetCode": row["edinetCode"],
        "industry": row["industry"],
        "base_year": int(row["base_year"]),
        "success": row["success"] == "True",
        "delta_roa": float(row["delta_roa"]),
        "excess_delta_roa": float(row["excess_delta_roa"]),
        "matched_excess_delta_roa": _number(row["matched_excess_delta_roa"]),
        "excess_roa_before": _number(row["excess_roa_before"]),
        "size_ratio": _number(row["size_ratio"]),
        "goodwill_ratio": goodwill_ratio,
        "same_industry": None
        if acquiree_industry is None
        else acquiree_industry == row["industry"],
    }


def quartiles(deals: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """key の値の小さい順に並べて4つの群に分ける。値が None の案件は入れない。"""
    known = sorted((d for d in deals if d[key] is not None), key=lambda d: d[key])
    size = len(known)
    return {
        label: known[size * index // 4 : size * (index + 1) // 4]
        for index, label in enumerate(QUARTILE_LABELS)
    }


def summarize(groups: dict[str, list[dict[str, Any]]], seed: int) -> list[dict[str, Any]]:
    """群ごとに、件数、成功率、超過 ΔROA の中央値、買収前の ROA でそろえた超過 ΔROA の中央値と、
    それぞれのブートストラップの95%の幅を出す。"""
    rng = random.Random(seed)
    summaries: list[dict[str, Any]] = []
    for name, deals in groups.items():
        matched = [d for d in deals if d["matched_excess_delta_roa"] is not None]
        rates: list[float] = []
        medians: list[float] = []
        matched_medians: list[float] = []
        for _ in range(BOOTSTRAP_ROUNDS):
            sample = rng.choices(deals, k=len(deals))
            rates.append(sum(d["success"] for d in sample) / len(sample))
            medians.append(statistics.median(d["excess_delta_roa"] for d in sample))
            matched_sample = rng.choices(matched, k=len(matched))
            matched_medians.append(
                statistics.median(d["matched_excess_delta_roa"] for d in matched_sample)
            )
        rate_cuts = statistics.quantiles(rates, n=40)
        median_cuts = statistics.quantiles(medians, n=40)
        matched_cuts = statistics.quantiles(matched_medians, n=40)
        summaries.append(
            {
                "group": name,
                "n": len(deals),
                "success_rate": sum(d["success"] for d in deals) / len(deals),
                "success_low": rate_cuts[0],
                "success_high": rate_cuts[-1],
                "median_excess": statistics.median(d["excess_delta_roa"] for d in deals),
                "median_low": median_cuts[0],
                "median_high": median_cuts[-1],
                "median_matched": statistics.median(d["matched_excess_delta_roa"] for d in matched),
                "matched_low": matched_cuts[0],
                "matched_high": matched_cuts[-1],
            }
        )
    return summaries


def fit_regression(deals: list[dict[str, Any]], outcome: str) -> dict[str, Any]:
    """成否の物差し（outcome の列）を、同業か・のれんの割合・買収の大きさ（対数）・買収前の ROA の同業との差で回帰する。

    基準の年と業種の違いは固定効果で除く。同じ会社の買収どうしは似るので、標準誤差は会社ごとにまとめて出す。
    """
    complete = [
        d
        for d in deals
        if all(
            d[k] is not None
            for k in ("same_industry", "goodwill_ratio", "excess_roa_before", outcome)
        )
        and d["size_ratio"] is not None
        and d["size_ratio"] > 0
    ]
    frame = pd.DataFrame(
        {
            "outcome": [d[outcome] for d in complete],
            "same_industry": [float(d["same_industry"]) for d in complete],
            "goodwill_ratio": [d["goodwill_ratio"] for d in complete],
            "log_size_ratio": [math.log(d["size_ratio"]) for d in complete],
            "excess_roa_before": [d["excess_roa_before"] for d in complete],
            "base_year": [d["base_year"] for d in complete],
            "industry": [d["industry"] for d in complete],
            "edinetCode": [d["edinetCode"] for d in complete],
        }
    )
    for column in ("outcome", "goodwill_ratio", "log_size_ratio", "excess_roa_before"):
        low, high = frame[column].quantile([WINSOR, 1 - WINSOR])
        frame[column] = frame[column].clip(low, high)
    formula = "outcome ~ " + " + ".join(TERMS) + " + C(base_year) + C(industry)"
    fitted = smf.ols(formula, data=frame).fit(
        cov_type="cluster", cov_kwds={"groups": frame["edinetCode"].astype("category").cat.codes}
    )
    intervals = fitted.conf_int()
    result: dict[str, Any] = {"n": int(fitted.nobs)}
    for term in TERMS:
        result[term] = {
            "coef": float(fitted.params[term]),
            "low": float(intervals.loc[term, 0]),
            "high": float(intervals.loc[term, 1]),
            "p": float(fitted.pvalues[term]),
        }
    return result


def _table(summaries: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 群 | 件数 | 成功率 | 95%の幅 | 超過 ΔROA の中央値 | 95%の幅 "
        "| 買収前の ROA でそろえた超過 ΔROA | 95%の幅 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['group']} | {s['n']} | {s['success_rate']:.1%} | {s['success_low']:.1%}〜{s['success_high']:.1%} "
            f"| {s['median_excess'] * 100:+.2f}pt | {s['median_low'] * 100:+.2f}〜{s['median_high'] * 100:+.2f}pt "
            f"| {s['median_matched'] * 100:+.2f}pt | {s['matched_low'] * 100:+.2f}〜{s['matched_high'] * 100:+.2f}pt |"
        )
    return lines


def _hypothesis_tables(deals: list[dict[str, Any]]) -> list[str]:
    same = {
        "同業を買った": [d for d in deals if d["same_industry"] is True],
        "別の業種を買った": [d for d in deals if d["same_industry"] is False],
    }
    lines = ["#### 同業を買うほうがうまくいく", *_table(summarize(same, SEED)), ""]
    lines += ["#### 高く買う（のれんの割合が大きい）ほど失敗する"]
    lines += [*_table(summarize(quartiles(deals, "goodwill_ratio"), SEED)), ""]
    lines += ["#### 業績の良い会社（買収前の ROA が同業より高い）が買うとうまくいく"]
    lines += [*_table(summarize(quartiles(deals, "excess_roa_before"), SEED)), ""]
    return lines


def build_report(deals: list[dict[str, Any]]) -> str:
    """仮説ごとの表と回帰の結果を Markdown で返す。全件、大きい買収だけ、1社1件の3通りで出す。"""
    big = [d for d in deals if d["size_ratio"] is not None and d["size_ratio"] >= 0.05]
    first: dict[str, dict[str, Any]] = {}
    for d in sorted(deals, key=lambda d: d["base_year"]):
        first.setdefault(d["edinetCode"], d)
    lines = ["# 仮説を確かめた結果", ""]
    for title, subset in (
        ("全件", deals),
        ("取得原価が買収前の総資産の5%以上", big),
        ("1社につき最初の1件", list(first.values())),
    ):
        lines += [f"## {title}（{len(subset)}件）", "", *_hypothesis_tables(subset)]
        for outcome, label in (
            ("excess_delta_roa", "超過 ΔROA"),
            ("matched_excess_delta_roa", "買収前の ROA でそろえた超過 ΔROA"),
        ):
            regression = fit_regression(subset, outcome)
            lines += [
                f"#### 回帰：{label}（{regression['n']}件、基準の年と業種の固定効果、会社ごとの標準誤差）",
                "",
                "| 列 | 係数 | 95%の幅 | p 値 |",
                "|---|---|---|---|",
            ]
            for term in TERMS:
                r = regression[term]
                lines.append(
                    f"| {term} | {r['coef']:+.4f} | {r['low']:+.4f}〜{r['high']:+.4f} | {r['p']:.3f} |"
                )
            lines.append("")
    lines += ["## 買収後に ROA が下がるのは、買収前の収益性でそろえると消えるか", ""]
    lines += ["| 比べる相手 | ΔROA の中央値 | 下がった割合 |", "|---|---|---|"]
    for label, key in (
        ("比べない（そのままの ΔROA）", "delta_roa"),
        ("同じ業種の中央値", "excess_delta_roa"),
        ("買収前の ROA が同じ帯（10分位）の全社の中央値", "matched_excess_delta_roa"),
    ):
        values = [d[key] for d in deals if d[key] is not None]
        lines.append(
            f"| {label} | {statistics.median(values) * 100:+.2f}pt | {sum(v < 0 for v in values) / len(values):.1%} |"
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
