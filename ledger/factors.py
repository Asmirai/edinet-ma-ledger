"""買収を判断するときに見る要因ごとに、うまくいかなかった割合を数え、モデルの当たり具合を比べる。

物差しは2つ。
- のれんの減損：その買収で生じたのれんを、買収後3期のうちに減損したか（のれんが生じた買収）
- 大きい買収の ROA：取得原価が買い手の総資産の10%以上の買収で、買収前の ROA が近い会社より ROA が伸びたか
小さい買収は、買い手全体の ROA をほとんど動かさないので、ROA では見ない。

使い方：uv run python -m ledger.factors
（roa・industries・impairments・purposes を先に用意しておく）
"""

import bisect
import importlib
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from ledger.dataset import BIG_DEAL, PROFILE_FIELDS, load_deals
from ledger.predict import CATEGORICAL, NUMERIC, fit_models

REPORT_PATH = Path("data/factors.md")
SEED = 20261019
BOOTSTRAP_ROUNDS = 2000
# 件数がこれより少ない群は、割合が揺れすぎるので表に出さない
MIN_GROUP = 30
# 値が小さな整数で、同じ値が多い列は、値ごとの群に分ける。値の後ろに付ける単位
COUNTED = {"prior_deals": "件", "risk_count": "個"}
# 回帰では、これより少ない群を「少数の群」にまとめる。表より少し下げ、大きい買収（約300件）の群も残す
MIN_REGRESSION_GROUP = 20
# 回帰でオッズ比の基準にする群。読み手が「ふつうの買収」と受け取るもの
REFERENCES = {"purpose": "同じ事業の拡大", "relatedness": "同業種", "consideration": "現金"}
NEW_NUMERIC = (*NUMERIC, *PROFILE_FIELDS)
NEW_CATEGORICAL = (*CATEGORICAL, "purpose", "relatedness", "consideration")
LAST_TRAIN_YEAR = 2019
WINSOR = 0.01
# statsmodels には型の情報が無いので、境目で Any として扱う
smf: Any = importlib.import_module("statsmodels.formula.api")


def share(key: str) -> Callable[[list[dict[str, Any]]], float]:
    """key が真の案件の割合を出す関数。None の案件は数えない。"""

    def rate(deals: list[dict[str, Any]]) -> float:
        known = [d[key] for d in deals if d[key] is not None]
        return sum(known) / len(known)

    return rate


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


def factor_summary(
    deals: list[dict[str, Any]], factor: str, outcome: str, seed: int
) -> list[dict[str, Any]]:
    """factor の群ごとに、outcome が真の割合と95%の幅を出す。

    数値の列は、factor が分かる全案件（物差しが空の案件も含む）の四分位の境目で4つの群に分け、
    境目を群の名前に入れる。物差しが違っても同じ境目になるので、表に並べても同じ群を指す。
    買収の件数は0の案件が半分近くあり四分位で分けられないので、0件・1件・2件・3件以上に分ける。
    種類の列は割合の高い順に並べる。幅は会社を単位にしたブートストラップ。件数が30件に満たない群は出さない。
    """
    known = [d for d in deals if d[outcome] is not None and d[factor] is not None]
    numeric = factor not in COUNTED and all(
        isinstance(d[factor], (int, float)) and not isinstance(d[factor], bool) for d in known
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    if numeric:
        cuts = statistics.quantiles([d[factor] for d in deals if d[factor] is not None], n=4)
        labels = [
            f"第1四分位（〜{cuts[0]:.2f}）",
            f"第2四分位（{cuts[0]:.2f}〜{cuts[1]:.2f}）",
            f"第3四分位（{cuts[1]:.2f}〜{cuts[2]:.2f}）",
            f"第4四分位（{cuts[2]:.2f}〜）",
        ]
        grouped = {label: [] for label in labels}
        for deal in known:
            grouped[labels[bisect.bisect_right(cuts, deal[factor])]].append(deal)
    elif factor in COUNTED:
        unit = COUNTED[factor]
        grouped = {f"{n}{unit}": [] for n in (0, 1, 2)} | {f"3{unit}以上": []}
        for deal in known:
            grouped[f"{deal[factor]}{unit}" if deal[factor] < 3 else f"3{unit}以上"].append(deal)
    else:
        for deal in known:
            grouped.setdefault(str(deal[factor]), []).append(deal)
    groups = grouped
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for name, members in groups.items():
        if len(members) < MIN_GROUP and not numeric or not members:
            continue
        values = bootstrap(members, {"rate": share(outcome)}, rng)
        rows.append(
            {
                "group": name,
                "n": len(members),
                "rate": values["rate"],
                "low": values["rate_low"],
                "high": values["rate_high"],
            }
        )
    if not numeric and factor not in COUNTED:
        rows.sort(key=lambda r: r["rate"], reverse=True)
    return rows


def adjusted_effects(
    deals: list[dict[str, Any]],
    outcome: str,
    numeric: tuple[str, ...],
    categorical: tuple[str, ...],
) -> dict[str, Any]:
    """ほかの要因と基準の年をそろえたとき、各要因が outcome の起こりやすさをどれだけ変えるか（ロジスティック回帰）。

    返すのは、使った件数（n）、outcome が真の件数（positives）、要因ごとのオッズ比（effects）。
    値のそろった案件だけを使う。数値の列は標準化し、1標準偏差あたりのオッズ比にする。上下1%は端の値にそろえる。
    種類の列は、REFERENCES の群を基準にしたオッズ比。20件に満たない群は「少数の群」にまとめる。
    業種はそろえない。業種ごとの件数が少なく、全部が成功や失敗になる業種があると、推定が収束しないため。
    標準誤差は会社ごとにまとめる。推定が収束しなければ止める。
    """
    needed = (outcome, *numeric, *categorical)
    complete = [d for d in deals if all(d[k] is not None for k in needed)]
    for name in categorical:
        counts = Counter(str(d[name]) for d in complete)
        complete = [
            d | {name: str(d[name]) if counts[str(d[name])] >= MIN_REGRESSION_GROUP else "少数の群"}
            for d in complete
        ]
    frame = pd.DataFrame(
        {
            "outcome": [float(d[outcome]) for d in complete],
            "base_year": [d["base_year"] for d in complete],
            "industry": [d["industry"] for d in complete],
            "edinetCode": [d["edinetCode"] for d in complete],
        }
        | {name: [float(d[name]) for d in complete] for name in numeric}
        | {name: [str(d[name]) for d in complete] for name in categorical}
    )
    for name in numeric:
        low, high = frame[name].quantile([WINSOR, 1 - WINSOR])
        clipped = frame[name].clip(low, high)
        frame[name] = (clipped - clipped.mean()) / clipped.std()
    references = {name: REFERENCES[name] for name in categorical}
    terms = [*numeric] + [
        f"C({name}, Treatment(reference='{references[name]}'))" for name in categorical
    ]
    formula = "outcome ~ " + " + ".join([*terms, "C(base_year)"])
    fitted = smf.logit(formula, data=frame).fit(
        disp=0,
        cov_type="cluster",
        cov_kwds={"groups": frame["edinetCode"].astype("category").cat.codes},
    )
    if not fitted.mle_retvals["converged"]:
        raise RuntimeError(f"{outcome} のロジスティック回帰が収束しない")
    intervals = fitted.conf_int()
    effects: list[dict[str, Any]] = []
    for term in fitted.params.index:
        label = f"{term}（1標準偏差）" if term in numeric else None
        for name in categorical:
            prefix = f"C({name}, Treatment(reference='{references[name]}'))[T."
            if term.startswith(prefix):
                label = f"{name}：{term.removeprefix(prefix).removesuffix(']')}"
        if label is None:
            continue
        effects.append(
            {
                "term": label,
                "odds_ratio": math.exp(float(fitted.params[term])),
                "low": math.exp(float(intervals.loc[term, 0])),
                "high": math.exp(float(intervals.loc[term, 1])),
                "p": float(fitted.pvalues[term]),
                "reference": ", ".join(f"{k}：{v}" for k, v in references.items()),
            }
        )
    return {
        "n": len(complete),
        "positives": int(sum(bool(d[outcome]) for d in complete)),
        "effects": effects,
    }


FACTORS = {
    "purpose": "買収の目的",
    "relatedness": "買い手の事業との近さ",
    "consideration": "対価の種類",
    "size_ratio": "買収の大きさ（取得原価 ÷ 買い手の総資産）",
    "goodwill_ratio": "のれんの割合（のれん ÷ 取得原価）",
    "excess_roa_before": "買い手の買収前の ROA（同業との差）",
    "sales_growth": "買い手の売上の伸び",
    "equity_ratio": "買い手の自己資本比率",
    "cash_ratio": "買い手の現金 ÷ 総資産",
    "prior_deals": "直前2年の買収の件数",
    "risk_count": "当てはまる危険信号の数（のれんの割合84%以上・異業種・自己資本比率65%以上・総資産の5%以上）",
    "basis": "買い手の会計基準（買収前の期）",
}


def build_report(deals: list[dict[str, Any]]) -> str:
    """要因ごとの減損率と ROA の伸び、ほかの要因をそろえた回帰、モデルの当たり具合を Markdown にする。"""
    impaired_deals = [d for d in deals if d["impaired"] is not None]
    big_deals = [d for d in deals if d["roa_success"] is not None]
    lines = [
        "# 買収の要因ごとの結果",
        "",
        f"- のれんの減損：のれんが生じ、買収後3期の有報がそろう {len(impaired_deals)} 件のうち "
        f"{sum(d['impaired'] for d in impaired_deals) / len(impaired_deals):.1%} が減損",
        f"- 大きい買収の ROA：取得原価が総資産の{BIG_DEAL:.0%}以上の {len(big_deals)} 件のうち "
        f"{sum(d['roa_success'] for d in big_deals) / len(big_deals):.1%} が、買収前の ROA が近い会社より ROA が伸びた",
        "- 95%の幅は会社を単位にしたブートストラップ。種類の群は30件以上のものだけ",
        "",
    ]
    for factor, label in FACTORS.items():
        lines += [
            f"## {label}",
            "",
            "| 群 | 減損した割合（件数） | 95%の幅 | 大きい買収で ROA が伸びた割合（件数） | 95%の幅 |",
            "|---|---|---|---|---|",
        ]
        impaired_rows = {r["group"]: r for r in factor_summary(deals, factor, "impaired", SEED)}
        roa_rows = {r["group"]: r for r in factor_summary(deals, factor, "roa_success", SEED)}
        names = list(impaired_rows) + [g for g in roa_rows if g not in impaired_rows]
        for name in names:
            cells = [name]
            for found in (impaired_rows.get(name), roa_rows.get(name)):
                if found is None:
                    cells += ["−", "−"]
                else:
                    cells += [
                        f"{found['rate']:.1%}（{found['n']}）",
                        f"{found['low']:.1%}〜{found['high']:.1%}",
                    ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    adjusted = (
        (
            "impaired",
            "のれんの減損",
            impaired_deals,
            (
                "goodwill_ratio",
                "log_size_ratio",
                "excess_roa_before",
                "equity_ratio",
                "cash_ratio",
                "sales_growth",
            ),
            ("purpose", "relatedness", "consideration"),
        ),
        (
            "roa_success",
            "大きい買収の ROA",
            big_deals,
            ("goodwill_ratio", "excess_roa_before"),
            ("relatedness",),
        ),
    )
    for outcome, label, subset, numeric, categorical in adjusted:
        result = adjusted_effects(subset, outcome, numeric, categorical)
        effects = result["effects"]
        lines += [
            f"## ほかの要因と年をそろえても残る差：{label}",
            "",
            f"値のそろった {result['n']} 件（うち真が {result['positives']} 件）で推定。"
            f"基準：{effects[0]['reference'] if effects else ''}。オッズ比が1より大きいほど起こりやすい",
            "",
            "| 要因 | オッズ比 | 95%の幅 | p 値 |",
            "|---|---|---|---|",
        ]
        for e in effects:
            lines.append(
                f"| {e['term']} | {e['odds_ratio']:.2f} | {e['low']:.2f}〜{e['high']:.2f} | {e['p']:.3f} |"
            )
        lines.append("")
    lines += [
        "## 大きい買収の境目を変えたとき",
        "",
        "| 境目（取得原価 ÷ 総資産） | 件数 | ROA が伸びた割合 | AUC（ロジスティック回帰、列を足した後） |",
        "|---|---|---|---|",
    ]
    for threshold in (0.05, 0.1, 0.2):
        subset = [
            d | {"target": d["matched_success"]}
            for d in deals
            if d["matched_success"] is not None and (d["size_ratio"] or 0) >= threshold
        ]
        models = fit_models(subset, LAST_TRAIN_YEAR, NEW_NUMERIC, NEW_CATEGORICAL)
        rate = sum(d["target"] for d in subset) / len(subset)
        lines.append(
            f"| {threshold:.0%}以上 | {len(subset)} | {rate:.1%} | {models['ロジスティック回帰']['auc']:.3f} |"
        )
    lines += [
        "",
        "## 大きい買収で、買収後1期ごとの ROA の変化（買収前の期との差）",
        "",
        "| 期 | 中央値 | 下がった割合 |",
        "|---|---|---|",
    ]
    for k in (1, 2, 3):
        values = [d[f"delta_roa_{k}"] for d in big_deals if d[f"delta_roa_{k}"] is not None]
        lines.append(
            f"| {k}期後 | {statistics.median(values) * 100:+.2f}pt | {sum(v < 0 for v in values) / len(values):.1%} |"
        )
    lines += [
        "",
        "## モデルの当たり具合（2019年までで学習し、2020年からで確かめる）",
        "",
        "| 物差し | 列 | モデル | AUC | 上位10%の割合 | 全体の割合 |",
        "|---|---|---|---|---|---|",
    ]
    for outcome, label, subset in (
        ("impaired", "のれんの減損", impaired_deals),
        ("roa_success", "大きい買収の ROA", big_deals),
    ):
        targeted = [d | {"target": d[outcome]} for d in subset]
        for columns, numeric, categorical in (
            ("前の8つ", NUMERIC, CATEGORICAL),
            ("足した後", NEW_NUMERIC, NEW_CATEGORICAL),
        ):
            for model, s in fit_models(targeted, LAST_TRAIN_YEAR, numeric, categorical).items():
                lines.append(
                    f"| {label} | {columns} | {model} | {s['auc']:.3f} | {s['top10_rate']:.1%} | {s['base_rate']:.1%} |"
                )
    return "\n".join(lines) + "\n"


def main() -> None:
    deals = load_deals()
    REPORT_PATH.write_text(build_report(deals), encoding="utf-8")
    print(f"{len(deals)} 件：{REPORT_PATH}")


if __name__ == "__main__":
    main()
