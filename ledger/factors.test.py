import random
from typing import Any

import pytest

from ledger.factors import adjusted_effects, bootstrap, factor_summary, share


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
    # 目的変数が空の案件も、境目を決めるのには使う
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
                "relatedness": "異業種" if far else "同業種",
                "goodwill_ratio": rng.random(),
                "impaired": rng.random() < (0.4 if far else 0.1),
            }
        )

    result = adjusted_effects(deals, "impaired", ("goodwill_ratio",), ("relatedness",))

    assert result["n"] == 1200
    far = next(e for e in result["effects"] if e["term"] == "relatedness：異業種")
    assert far["odds_ratio"] > 3
    assert far["p"] < 0.001


def 危険信号の数は値ごとの群に分ける() -> None:
    deals: list[dict[str, Any]] = [
        {"edinetCode": f"E{i}", "risk_count": i % 5, "impaired": i % 5 >= 2} for i in range(200)
    ]

    rows = factor_summary(deals, "risk_count", "impaired", seed=1)

    assert [r["group"] for r in rows] == ["0個", "1個", "2個", "3個以上"]


def 幅は会社ごとにまとめて引くので同じ会社の案件が多いと広がる() -> None:
    spread = [{"edinetCode": f"E{i}", "impaired": i % 2 == 0} for i in range(40)]
    clustered = [{"edinetCode": f"E{i // 10}", "impaired": i // 10 % 2 == 0} for i in range(40)]
    stats = {"rate": share("impaired")}

    loose = bootstrap(spread, stats, random.Random(1))
    tight = bootstrap(clustered, stats, random.Random(1))

    assert loose["rate"] == tight["rate"] == 0.5
    assert tight["rate_high"] - tight["rate_low"] > loose["rate_high"] - loose["rate_low"]
