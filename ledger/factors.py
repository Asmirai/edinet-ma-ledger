"""買収を判断するときに見る要因ごとに、うまくいかなかった割合を数え、モデルの当たり具合を比べる。

物差しは2つ。
- のれんの減損：その買収で生じたのれんを、買収後3期のうちに減損したか（のれんが生じた買収）
- 大きい買収の ROA：取得原価が買い手の総資産の10%以上の買収で、買収前の ROA が近い会社より ROA が伸びたか
小さい買収は、買い手全体の ROA をほとんど動かさないので、ROA では見ない。

使い方：uv run python -m ledger.factors
（roa・industries・impairments・purposes を先に用意しておく）
"""

import csv
import importlib
import json
import math
import random
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd

from ledger.hypotheses import INDUSTRIES_DIR, bootstrap, deal_features, quartiles, share
from ledger.impairments import IMPAIRMENTS_PATH
from ledger.industries import read_industries
from ledger.notes import DOCUMENTS_DIR, NOTES_DIR
from ledger.predict import (
    CATEGORICAL,
    NUMERIC,
    fit_models,
    legal_form_kind,
    recent_deals,
    voting_number,
)
from ledger.profiles import acquirer_profile
from ledger.purposes import LAST_BASE_YEAR, consideration_kind, read_purposes
from ledger.roa import LABELED_PATH

PURPOSES_DIR = Path("data/purposes")
PROFILES_PATH = Path("data/profiles.json")
REPORT_PATH = Path("data/factors.md")
BIG_DEAL = 0.1
SEED = 20261019
# 件数がこれより少ない群は、割合が揺れすぎるので表に出さない
MIN_GROUP = 30
PROFILE_FIELDS = ("sales_growth", "equity_ratio", "cash_ratio", "ocf_ratio")
NEW_NUMERIC = (*NUMERIC, *PROFILE_FIELDS)
NEW_CATEGORICAL = (*CATEGORICAL, "purpose", "relatedness", "consideration")
LAST_TRAIN_YEAR = 2019
WINSOR = 0.01
# statsmodels には型の情報が無いので、境目で Any として扱う
smf: Any = importlib.import_module("statsmodels.formula.api")


def deal_table(
    rows: list[dict[str, str]],
    acquiree_industries: dict[str, str | None],
    purposes: dict[str, tuple[str, str]],
    impairments: dict[str, bool],
    profiles: dict[str, dict[str, float | None]],
) -> list[dict[str, Any]]:
    """成否を付けられる年の主の行について、列と2つの物差しを1行にまとめる。分からない値は None。"""
    dates: defaultdict[str, list[date]] = defaultdict(list)
    for row in rows:
        if row["acquisition_date"]:
            dates[row["edinetCode"]].append(date.fromisoformat(row["acquisition_date"]))
    ledger_start = min(d for found in dates.values() for d in found)
    deals: list[dict[str, Any]] = []
    for row in rows:
        if (
            row["primary"] != "True"
            or not row["acquisition_date"]
            or not row["base_year"]
            or int(row["base_year"]) > LAST_BASE_YEAR
        ):
            continue
        key = f"{row['deal_id']}-{row['edinetCode']}"
        acquiree_industry = acquiree_industries.get(key)
        features = deal_features(row, acquiree_industry)
        size, same = features["size_ratio"], features["same_industry"]
        purpose, relatedness = purposes.get(key, (None, None))
        profile = profiles.get(key, {})
        deals.append(
            {
                "key": key,
                "edinetCode": row["edinetCode"],
                "base_year": features["base_year"],
                "impaired": impairments.get(key),
                "roa_success": features["matched_success"]
                if size is not None and size >= BIG_DEAL
                else None,
                "same_industry": None if same is None else float(same),
                "goodwill_ratio": features["goodwill_ratio"],
                "size_ratio": size,
                "log_size_ratio": math.log(size) if size is not None and size > 0 else None,
                "excess_roa_before": features["excess_roa_before"],
                "voting_ratio": voting_number(row["voting_ratio"]),
                "prior_deals": recent_deals(
                    dates[row["edinetCode"]],
                    date.fromisoformat(row["acquisition_date"]),
                    ledger_start,
                ),
                "industry": row["industry"],
                "acquiree_industry": acquiree_industry,
                "legal_form": legal_form_kind(row["legal_form"]),
                "consideration": consideration_kind(row["legal_form"]),
                "purpose": purpose,
                "relatedness": relatedness,
            }
            | {field: profile.get(field) for field in PROFILE_FIELDS}
        )
    return deals


def factor_summary(
    deals: list[dict[str, Any]], factor: str, outcome: str, seed: int
) -> list[dict[str, Any]]:
    """factor の群ごとに、outcome が真の割合と95%の幅を出す。

    数値の列は値の小さい順に4つの群に分け、その順に並べる。種類の列は割合の高い順に並べる。
    幅は会社を単位にしたブートストラップ。件数が30件に満たない群は出さない。
    """
    known = [d for d in deals if d[outcome] is not None and d[factor] is not None]
    numeric = all(
        isinstance(d[factor], (int, float)) and not isinstance(d[factor], bool) for d in known
    )
    if numeric:
        groups = quartiles(known, factor)
    else:
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for deal in known:
            grouped[str(deal[factor])].append(deal)
        groups = dict(grouped)
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for name, members in groups.items():
        if len(members) < MIN_GROUP and not numeric:
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
    if not numeric:
        rows.sort(key=lambda r: r["rate"], reverse=True)
    return rows


def adjusted_effects(
    deals: list[dict[str, Any]],
    outcome: str,
    numeric: tuple[str, ...],
    categorical: tuple[str, ...],
) -> list[dict[str, Any]]:
    """ほかの要因・基準の年・業種をそろえたとき、各要因が outcome の起こりやすさをどれだけ変えるか（ロジスティック回帰）。

    数値の列は標準化し、1標準偏差あたりのオッズ比にする。上下1%は端の値にそろえる。
    種類の列は、いちばん件数の多い群を基準にしたオッズ比。標準誤差は会社ごとにまとめる。
    """
    needed = (outcome, *numeric, *categorical)
    complete = [d for d in deals if all(d[k] is not None for k in needed)]
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
    references = {name: str(frame[name].mode()[0]) for name in categorical}
    terms = [*numeric] + [
        f"C({name}, Treatment(reference='{references[name]}'))" for name in categorical
    ]
    formula = "outcome ~ " + " + ".join([*terms, "C(base_year)", "C(industry)"])
    fitted = smf.logit(formula, data=frame).fit(
        disp=0,
        cov_type="cluster",
        cov_kwds={"groups": frame["edinetCode"].astype("category").cat.codes},
    )
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
    return effects


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
}


def _profiles(rows: list[dict[str, str]]) -> dict[str, dict[str, float | None]]:
    """案件ごとに、買い手の買収直前の期（基準の年の前年が決算日の年）の有報から財務を読む。読んだ値は残す。"""
    saved: dict[str, dict[str, float | None]] = {}
    if PROFILES_PATH.exists():
        saved = cast(
            dict[str, dict[str, float | None]],
            json.loads(PROFILES_PATH.read_text(encoding="utf-8")),
        )
    reports: dict[tuple[str, int], str] = {}
    for notes_path in NOTES_DIR.glob("*.json"):
        report = cast(dict[str, Any], json.loads(notes_path.read_text(encoding="utf-8")))
        reports[(report["edinetCode"], date.fromisoformat(report["periodEnd"]).year)] = report[
            "docID"
        ]
    for row in rows:
        key = f"{row['deal_id']}-{row['edinetCode']}"
        if key in saved or row["primary"] != "True" or not row["base_year"]:
            continue
        doc_id = reports.get((row["edinetCode"], int(row["base_year"]) - 1))
        if doc_id is not None:
            saved[key] = acquirer_profile((DOCUMENTS_DIR / f"{doc_id}.zip").read_bytes())
    PROFILES_PATH.write_text(json.dumps(saved), encoding="utf-8")
    return saved


def main() -> None:
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    with IMPAIRMENTS_PATH.open(encoding="utf-8-sig", newline="") as file:
        impairments = {
            r["key"]: r["impaired"] == "True" for r in csv.DictReader(file) if r["impaired"]
        }
    deals = deal_table(
        rows,
        read_industries(INDUSTRIES_DIR),
        read_purposes(PURPOSES_DIR),
        impairments,
        _profiles(rows),
    )
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
        effects = adjusted_effects(subset, outcome, numeric, categorical)
        lines += [
            f"## ほかの要因・年・業種をそろえても残る差：{label}",
            "",
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
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(deals)} 件：{REPORT_PATH}")


if __name__ == "__main__":
    main()
