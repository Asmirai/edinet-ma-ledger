"""買収の時点で分かる情報だけで、その買収が成功するかを当てられるかを確かめる。

当てるものは、買収前の ROA でそろえた成否（そろえた超過 ΔROA が0より大きいか）。
そろえない成否だと、買収前の ROA から平均への回帰を当てるだけになる。
基準の年が2019年までの案件で学習し、2020年からの案件で確かめる。

使い方：uv run python -m ledger.predict
"""

import csv
import importlib
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ledger.features import deal_features, legal_form_kind, recent_deals, voting_number
from ledger.industries import INDUSTRIES_DIR, read_industries
from ledger.roa import LABELED_PATH

REPORT_PATH = Path("data/predict.md")
LAST_TRAIN_YEAR = 2019
NUMERIC = (
    "same_industry",
    "goodwill_ratio",
    "log_size_ratio",
    "excess_roa_before",
    "voting_ratio",
    "prior_deals",
)
CATEGORICAL = ("industry", "acquiree_industry", "legal_form")
TOP_SHARE = 0.1
# scikit-learn には型の情報が無いので、境目で Any として扱う
sk_compose: Any = importlib.import_module("sklearn.compose")
sk_ensemble: Any = importlib.import_module("sklearn.ensemble")
sk_impute: Any = importlib.import_module("sklearn.impute")
sk_linear: Any = importlib.import_module("sklearn.linear_model")
sk_metrics: Any = importlib.import_module("sklearn.metrics")
sk_pipeline: Any = importlib.import_module("sklearn.pipeline")
sk_preprocessing: Any = importlib.import_module("sklearn.preprocessing")


def model_rows(
    rows: list[dict[str, str]], acquiree_industries: dict[str, str | None]
) -> list[dict[str, Any]]:
    """台帳の行（CSV の文字列）から、買収前の ROA でそろえた成否が付いた主の行の列を作る。

    直前2年の買収の件数は、成否を付けたかどうかにかかわらず、その会社の台帳の行で企業結合日が
    2年前からその前日までのものを数える。台帳の始まりから2年たっていない案件は、件数がそろわないので None。
    全期間の件数にすると、台帳が長くなる後の年ほど増え、年の代わりになってしまう。
    """
    dates: defaultdict[str, list[date]] = defaultdict(list)
    for row in rows:
        if row["acquisition_date"]:
            dates[row["edinetCode"]].append(date.fromisoformat(row["acquisition_date"]))
    ledger_start = min(d for found in dates.values() for d in found)
    built: list[dict[str, Any]] = []
    for row in rows:
        if row["primary"] != "True" or not row["success"] or not row["matched_excess_delta_roa"]:
            continue
        acquiree_industry = acquiree_industries.get(f"{row['deal_id']}-{row['edinetCode']}")
        features = deal_features(row, acquiree_industry)
        size = features["size_ratio"]
        same = features["same_industry"]
        built.append(
            {
                "base_year": features["base_year"],
                "same_industry": None if same is None else float(same),
                "goodwill_ratio": features["goodwill_ratio"],
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
                "target": features["matched_success"],
            }
        )
    return built


def split_by_year(
    rows: list[dict[str, Any]], last_train_year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """基準の年が last_train_year までを学習に、それより後を確かめに使う。"""
    train = [r for r in rows if r["base_year"] <= last_train_year]
    test = [r for r in rows if r["base_year"] > last_train_year]
    return train, test


def evaluate(truth: list[bool], probabilities: list[float]) -> dict[str, float]:
    """AUC、PR-AUC、Brier スコア、全体の成功率、予測の上位10%の成功率を出す。

    どの案件も同じ確率なら上位を選べないので、上位10%の成功率は全体の成功率にする。
    """
    base_rate = sum(truth) / len(truth)
    ranked = sorted(zip(probabilities, truth), key=lambda pair: pair[0], reverse=True)
    top = ranked[: max(1, math.ceil(len(ranked) * TOP_SHARE))]
    top_rate = sum(t for _, t in top) / len(top) if len(set(probabilities)) > 1 else base_rate
    return {
        "auc": float(sk_metrics.roc_auc_score(truth, probabilities)),
        "pr_auc": float(sk_metrics.average_precision_score(truth, probabilities)),
        "brier": float(sk_metrics.brier_score_loss(truth, probabilities)),
        "base_rate": base_rate,
        "top10_rate": top_rate,
    }


def _frame(
    rows: list[dict[str, Any]], numeric: tuple[str, ...], categorical: tuple[str, ...]
) -> pd.DataFrame:
    columns: dict[str, list[Any]] = {
        name: [math.nan if r[name] is None else float(r[name]) for r in rows] for name in numeric
    }
    columns |= {name: [r[name] or "不明" for r in rows] for name in categorical}
    return pd.DataFrame(columns)


def fit_models(
    rows: list[dict[str, Any]],
    last_train_year: int,
    numeric: tuple[str, ...],
    categorical: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    """全件の成功率・ロジスティック回帰・勾配ブースティングを学習し、確かめの案件での物差しを返す。

    当てるものは各行の target。使う列は numeric（数値）と categorical（種類）で選ぶ。
    """
    train, test = split_by_year(rows, last_train_year)
    train_y = [bool(r["target"]) for r in train]
    test_y = [bool(r["target"]) for r in test]
    categories = sk_preprocessing.OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    logistic = sk_pipeline.make_pipeline(
        sk_compose.ColumnTransformer(
            [
                (
                    "numeric",
                    sk_pipeline.make_pipeline(
                        sk_impute.SimpleImputer(strategy="median", add_indicator=True),
                        sk_preprocessing.StandardScaler(),
                    ),
                    list(numeric),
                ),
                ("categorical", categories, list(categorical)),
            ]
        ),
        sk_linear.LogisticRegression(max_iter=1000),
    )
    boosting = sk_pipeline.make_pipeline(
        sk_compose.ColumnTransformer(
            [
                ("numeric", "passthrough", list(numeric)),
                (
                    "categorical",
                    sk_preprocessing.OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    list(categorical),
                ),
            ]
        ),
        # 欠けた値はそのまま扱える。学習の件数が少ないので、木を浅く、学習の速さを小さくする
        sk_ensemble.HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=200, random_state=0
        ),
    )
    results = {"全件の成功率": evaluate(test_y, [sum(train_y) / len(train_y)] * len(test_y))}
    for name, model in (("ロジスティック回帰", logistic), ("勾配ブースティング", boosting)):
        model.fit(_frame(train, numeric, categorical), train_y)
        probabilities = [
            float(p) for p in model.predict_proba(_frame(test, numeric, categorical))[:, 1]
        ]
        results[name] = evaluate(test_y, probabilities)
    return results


def main() -> None:
    industries = read_industries(INDUSTRIES_DIR)
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = model_rows(list(csv.DictReader(file)), industries)
    train, test = split_by_year(rows, LAST_TRAIN_YEAR)
    results = fit_models(rows, LAST_TRAIN_YEAR, NUMERIC, CATEGORICAL)
    lines = [
        "# 買収の成否を当てられるか",
        "",
        f"- 当てるもの：買収前の ROA でそろえた成否。学習 {len(train)} 件（基準の年が{LAST_TRAIN_YEAR}年まで）、"
        f"確かめ {len(test)} 件（{LAST_TRAIN_YEAR + 1}年から）",
        "",
        "| モデル | AUC | PR-AUC | Brier | 全体の成功率 | 上位10%の成功率 |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in results.items():
        lines.append(
            f"| {name} | {s['auc']:.3f} | {s['pr_auc']:.3f} | {s['brier']:.3f} "
            f"| {s['base_rate']:.1%} | {s['top10_rate']:.1%} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
