import random
from typing import Any

import pytest

from ledger.predict import (
    CATEGORICAL,
    NUMERIC,
    build_report,
    evaluate,
    fit_models,
    model_rows,
    split_by_year,
)


def _ledger_row(deal_id: int, date: str, **changes: str) -> dict[str, str]:
    row = {
        "deal_id": str(deal_id),
        "edinetCode": "E1",
        "industry": "卸売業",
        "acquisition_date": date,
        "base_year": "2019",
        "primary": "True",
        "success": "True",
        "delta_roa": "0.0",
        "excess_delta_roa": "0.0",
        "matched_excess_delta_roa": "0.01",
        "excess_roa_before": "-0.02",
        "size_ratio": "0.1",
        "consideration_yen": "1000",
        "goodwill_yen": "300",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
    }
    return row | changes


def 成否を付けた主の行から列を作り直前2年の買収の件数を数える() -> None:
    rows = [
        _ledger_row(1, "2017-04-01", success="", matched_excess_delta_roa=""),
        _ledger_row(2, "2018-04-01", primary="False"),
        _ledger_row(3, "2019-04-01"),
        _ledger_row(4, "2019-05-01", matched_excess_delta_roa="-0.01"),
        # 台帳の始まりから2年たっていないので、直前2年の件数はそろわない
        _ledger_row(5, "2018-06-01"),
    ]

    built = model_rows(rows, {"3-E1": "卸売業", "4-E1": "小売業"})

    assert [r["prior_deals"] for r in built] == [3, 3, None]
    assert [r["target"] for r in built] == [True, False, True]
    assert built[0]["same_industry"] == 1.0
    assert built[1]["same_industry"] == 0.0
    assert built[0]["goodwill_ratio"] == pytest.approx(0.3)
    assert built[0]["legal_form"] == "株式取得"
    assert built[0]["voting_ratio"] == 100


def 基準の年で学習と確かめに分ける() -> None:
    rows: list[dict[str, Any]] = [{"base_year": year} for year in (2017, 2019, 2020, 2022)]

    train, test = split_by_year(rows, 2019)

    assert [r["base_year"] for r in train] == [2017, 2019]
    assert [r["base_year"] for r in test] == [2020, 2022]


def 評価指標を出す() -> None:
    truth = [True, False, True, False, True, False, False, False, False, False]
    perfect = [0.9, 0.1, 0.8, 0.2, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1]

    scores = evaluate(truth, perfect)

    assert scores["auc"] == pytest.approx(1.0)
    assert scores["pr_auc"] == pytest.approx(1.0)
    assert scores["base_rate"] == pytest.approx(0.3)
    assert scores["top10_rate"] == pytest.approx(1.0)
    assert evaluate(truth, [0.5] * 10)["auc"] == pytest.approx(0.5)


def どれも同じ確率なら上位10パーセントの成功率は全体の成功率にする() -> None:
    truth = [True] + [False] * 9

    assert evaluate(truth, [0.4] * 10)["top10_rate"] == pytest.approx(0.1)


def 三つのモデルを学習して確かめの評価指標を並べる() -> None:
    rng = random.Random(0)
    rows: list[dict[str, Any]] = []
    for i in range(600):
        size = rng.random()
        rows.append(
            {
                "base_year": 2017 + i % 6,
                "same_industry": float(i % 2),
                "goodwill_ratio": rng.random() if i % 7 else None,
                "log_size_ratio": size,
                "excess_roa_before": rng.gauss(0, 0.02),
                "voting_ratio": 100.0,
                "prior_deals": i % 4,
                "industry": "卸売業",
                "acquiree_industry": "卸売業" if i % 3 else None,
                "legal_form": "株式取得",
                "target": rng.random() < (0.8 if size > 0.5 else 0.2),
            }
        )

    results = fit_models(rows, 2019, NUMERIC, CATEGORICAL)

    assert list(results) == ["全件の成功率", "ロジスティック回帰", "勾配ブースティング"]
    assert results["全件の成功率"]["auc"] == pytest.approx(0.5)
    assert results["ロジスティック回帰"]["auc"] > 0.7
    assert results["勾配ブースティング"]["auc"] > 0.7


def 学習と確かめの件数とモデルごとの評価指標を表にする() -> None:
    scores = {"auc": 0.512, "pr_auc": 0.431, "brier": 0.245, "base_rate": 0.4, "top10_rate": 0.42}

    report = build_report(train_n=900, test_n=600, results={"ロジスティック回帰": scores})

    assert "学習 900 件" in report
    assert "確かめ 600 件" in report
    assert "| ロジスティック回帰 | 0.512 | 0.431 | 0.245 | 40.0% | 42.0% |" in report
