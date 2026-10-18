from typing import Any

import pytest

from ledger.factors import BIG_DEAL, adjusted_effects, deal_table, factor_summary


def _row(deal_id: str, **changes: str) -> dict[str, str]:
    row = {
        "deal_id": deal_id,
        "edinetCode": "E1",
        "industry": "卸売業",
        "acquisition_date": "2020-06-01",
        "base_year": "2021",
        "primary": "True",
        "success": "True",
        "delta_roa": "0.01",
        "excess_delta_roa": "0.01",
        "matched_excess_delta_roa": "0.02",
        "delta_roa_1": "0.01",
        "delta_roa_2": "",
        "delta_roa_3": "0.03",
        "excess_roa_before": "0.0",
        "size_ratio": "0.2",
        "consideration_yen": "1000",
        "goodwill_yen": "300",
        "legal_form": "現金を対価とする株式取得",
        "voting_ratio": "100％",
    }
    return row | changes


def 案件ごとに列と2つの物差しをまとめる() -> None:
    rows = [
        _row("1"),
        _row("2", size_ratio="0.01", matched_excess_delta_roa="-0.01"),
        _row("3", base_year="2024"),
        _row("4", primary="False"),
    ]
    purposes = {"1-E1": ("同じ事業の拡大", "同じ事業")}
    profiles: dict[str, dict[str, float | None]] = {
        "1-E1": {"sales_growth": 0.1, "equity_ratio": 0.5, "cash_ratio": 0.2, "ocf_ratio": 0.05}
    }

    deals = deal_table(rows, {"1-E1": "卸売業"}, purposes, {"1-E1": True, "2-E1": False}, profiles)

    assert [d["key"] for d in deals] == ["1-E1", "2-E1"]
    first, second = deals
    assert first["impaired"] is True
    assert first["roa_success"] is True
    assert first["purpose"] == "同じ事業の拡大"
    assert first["consideration"] == "現金"
    assert first["equity_ratio"] == 0.5
    assert second["roa_success"] is None
    assert second["purpose"] is None
    assert second["equity_ratio"] is None


def 大きい買収の境目は総資産の10パーセント() -> None:
    assert BIG_DEAL == 0.1


def _deal(group: str, outcome: bool | None, company: str = "E1") -> dict[str, Any]:
    return {"edinetCode": company, "purpose": group, "impaired": outcome}


def 種類ごとに割合と95パーセントの幅を出す() -> None:
    deals = [_deal("同じ事業の拡大", i % 4 == 0, f"E{i}") for i in range(40)]
    deals += [_deal("新しい事業への参入", i % 2 == 0, f"F{i}") for i in range(40)]
    deals += [_deal("その他", None)]

    rows = factor_summary(deals, "purpose", "impaired", seed=1)

    assert [r["group"] for r in rows] == ["新しい事業への参入", "同じ事業の拡大"]
    assert rows[0]["n"] == 40
    assert rows[0]["rate"] == pytest.approx(0.5)
    assert rows[1]["rate"] == pytest.approx(0.25)
    assert rows[1]["low"] < 0.25 < rows[1]["high"]


def 数値の列は全案件の四分位の境目で分け境目を群の名前に入れる() -> None:
    deals: list[dict[str, Any]] = [
        {"edinetCode": f"E{i}", "equity_ratio": i / 10, "impaired": i >= 7} for i in range(8)
    ]
    # 物差しが空の案件も、境目を決めるのには使う
    deals.append({"edinetCode": "E9", "equity_ratio": 0.9, "impaired": None})

    rows = factor_summary(deals, "equity_ratio", "impaired", seed=1)

    assert [r["group"].split("（")[0] for r in rows] == [
        "第1四分位",
        "第2四分位",
        "第3四分位",
        "第4四分位",
    ]
    assert rows[0]["group"].startswith("第1四分位（〜")
    assert [r["rate"] for r in rows] == [0.0, 0.0, 0.0, 1.0]


def 買収の件数は値ごとの群に分ける() -> None:
    deals: list[dict[str, Any]] = [
        {"edinetCode": f"E{i}", "prior_deals": i % 5, "impaired": i % 5 >= 3} for i in range(200)
    ]

    rows = factor_summary(deals, "prior_deals", "impaired", seed=1)

    assert [r["group"] for r in rows] == ["0件", "1件", "2件", "3件以上"]
    assert rows[3]["n"] == 80


def ほかの要因をそろえても残る差を回帰で出す() -> None:
    import random

    rng = random.Random(0)
    deals: list[dict[str, Any]] = []
    for i in range(1200):
        far = i % 3 == 0
        deals.append(
            {
                "edinetCode": f"E{i % 300}",
                "industry": "卸売業" if i % 2 else "小売業",
                "base_year": 2017 + i % 4,
                "relatedness": "遠い事業" if far else "同じ事業",
                "goodwill_ratio": rng.random(),
                "impaired": rng.random() < (0.4 if far else 0.1),
            }
        )

    result = adjusted_effects(deals, "impaired", ("goodwill_ratio",), ("relatedness",))

    assert result["n"] == 1200
    far = next(e for e in result["effects"] if e["term"] == "relatedness：遠い事業")
    assert far["odds_ratio"] > 3
    assert far["p"] < 0.001
