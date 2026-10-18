import random
from typing import Any

import pytest

from ledger.hypotheses import TERMS, deal_features, fit_regression, quartiles, summarize


def _row(**changes: str) -> dict[str, str]:
    row = {
        "deal_id": "1",
        "edinetCode": "E1",
        "industry": "情報・通信業",
        "base_year": "2020",
        "success": "True",
        "delta_roa": "0.01",
        "excess_delta_roa": "0.02",
        "matched_excess_delta_roa": "0.015",
        "excess_roa_before": "-0.01",
        "size_ratio": "0.1",
        "consideration_yen": "1000",
        "goodwill_yen": "400",
    }
    return row | changes


def 同業かとのれんの割合と買収の大きさを出す() -> None:
    features = deal_features(_row(), "情報・通信業")

    assert features["same_industry"] is True
    assert features["goodwill_ratio"] == pytest.approx(0.4)
    assert features["size_ratio"] == pytest.approx(0.1)
    assert features["success"] is True
    assert features["matched_success"] is True
    assert features["excess_delta_roa"] == pytest.approx(0.02)


def 買った会社の業種が33業種に無ければ同業かを空にする() -> None:
    row = _row(industry="内国法人・組合（有価証券報告書等の提出義務者以外）")

    assert deal_features(row, "情報・通信業")["same_industry"] is None


def のれんの割合が1点5を超える案件は単位の読み違いとみなして空にする() -> None:
    assert deal_features(_row(goodwill_yen="1500"), "卸売業")["goodwill_ratio"] == 1.5
    assert deal_features(_row(goodwill_yen="1501"), "卸売業")["goodwill_ratio"] is None


def 分からない値は空にする() -> None:
    row = _row(goodwill_yen="", size_ratio="", matched_excess_delta_roa="", consideration_yen="0")

    features = deal_features(row, None)

    assert features["same_industry"] is None
    assert features["goodwill_ratio"] is None
    assert features["size_ratio"] is None
    assert features["matched_excess_delta_roa"] is None
    assert features["matched_success"] is None


def のれんが0ならのれんの割合を0にし書かれていなければ空にする() -> None:
    features = deal_features(_row(goodwill_yen=""), "卸売業")

    assert features["goodwill_ratio"] is None
    assert deal_features(_row(goodwill_yen="0"), "卸売業")["goodwill_ratio"] == 0


def 値の小さい順に4つの群に分ける() -> None:
    deals = [{"x": float(i)} for i in range(8)] + [{"x": None}]

    groups = quartiles(deals, "x")

    assert list(groups) == ["第1四分位（低い）", "第2四分位", "第3四分位", "第4四分位（高い）"]
    assert [[d["x"] for d in g] for g in groups.values()] == [[0, 1], [2, 3], [4, 5], [6, 7]]


def _deal(company: str, success: bool, excess: float, matched: float | None) -> dict[str, Any]:
    return {
        "edinetCode": company,
        "success": success,
        "excess_delta_roa": excess,
        "matched_excess_delta_roa": matched,
        "matched_success": None if matched is None else matched > 0,
    }


def 群ごとに成功率と中央値と95パーセントの幅を出す() -> None:
    deals = [_deal(f"E{i}", i % 4 == 0, i / 100, i / 200 - 0.1) for i in range(100)]
    deals.append(_deal("E100", False, 0.495, None))

    [summary] = summarize({"全部": deals}, seed=1)

    assert summary["group"] == "全部"
    assert summary["n"] == 101
    assert summary["success_rate"] == pytest.approx(25 / 101)
    assert summary["success_low"] < 25 / 101 < summary["success_high"]
    assert summary["matched_success_rate"] == pytest.approx(0.79)
    assert summary["matched_success_low"] < 0.79 < summary["matched_success_high"]
    assert summary["median_excess"] == pytest.approx(0.495)
    assert summary["median_matched"] == pytest.approx(0.1475)
    assert summary["matched_low"] < 0.1475 < summary["matched_high"]
    assert summarize({"全部": deals}, seed=1) == [summary]


def 幅は会社ごとにまとめて引くので同じ会社の案件が多いと広がる() -> None:
    spread = [_deal(f"E{i}", i % 2 == 0, 0.0, 0.0) for i in range(40)]
    clustered = [_deal(f"E{i // 10}", i // 10 % 2 == 0, 0.0, 0.0) for i in range(40)]

    [loose] = summarize({"散らばる": spread}, seed=1)
    [tight] = summarize({"かたまる": clustered}, seed=1)

    assert (
        tight["success_high"] - tight["success_low"] > loose["success_high"] - loose["success_low"]
    )


def 回帰では極端な値を上下1パーセントの端の値にそろえる() -> None:
    rng = random.Random(0)
    deals: list[dict[str, Any]] = [
        {
            "edinetCode": f"E{i % 50}",
            "industry": "卸売業",
            "base_year": 2018,
            "same_industry": i % 2 == 0,
            # 取得原価が1円のような案件で、のれんの割合が極端に大きくなる
            "goodwill_ratio": 1e9 if i == 0 else rng.random(),
            "size_ratio": rng.random() + 0.01,
            "excess_roa_before": rng.gauss(0, 0.02),
            "excess_delta_roa": rng.gauss(0, 0.005),
        }
        for i in range(300)
    ]
    for d in deals:
        d["excess_delta_roa"] = (
            -0.05 * d["goodwill_ratio"] + rng.gauss(0, 0.001) if d["goodwill_ratio"] < 2 else 0.0
        )

    result = fit_regression(deals, "excess_delta_roa", TERMS)

    assert result["goodwill_ratio"]["coef"] == pytest.approx(-0.05, abs=0.01)


def 回帰で同業の効果を取り出す() -> None:
    rng = random.Random(0)
    deals = [
        {
            "edinetCode": f"E{i % 50}",
            "industry": "卸売業" if i % 2 else "小売業",
            "base_year": 2018 + i % 3,
            "same_industry": i % 5 == 0,
            "goodwill_ratio": rng.random(),
            "size_ratio": rng.random() + 0.01,
            "excess_roa_before": rng.gauss(0, 0.02),
            "excess_delta_roa": (0.02 if i % 5 == 0 else 0) + rng.gauss(0, 0.005),
        }
        for i in range(300)
    ]

    result = fit_regression(deals, "excess_delta_roa", TERMS)

    assert result["same_industry"]["coef"] == pytest.approx(0.02, abs=0.003)
    assert result["same_industry"]["p"] < 0.001
    assert result["n"] == 300
