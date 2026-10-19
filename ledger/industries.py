"""買われた会社の業種を、Claude Code のサブエージェントに「事業の内容」から東証の33業種へ分けさせる。

買った会社の業種は EDINET コードリストにあるが、買われた会社の業種はどこにも無い。
同業を買ったかどうかを見るため、成否を付けた主の行だけを分けさせる。

使い方：
  uv run python -m ledger.industries split data/industries
  （in/chunk_*.jsonl を1つずつサブエージェントに渡し、industry_instructions.md の指示で out/chunk_*.json に書かせる）
"""

import argparse
import csv
from pathlib import Path
from typing import Any, cast

from ledger.chunks import read_outputs, write_chunks
from ledger.roa import LABELED_PATH

# EDINET コードリストの「提出者業種」と同じ書き方にする
INDUSTRIES_DIR = Path("data/industries")
INDUSTRIES = (
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", "陸運業", "海運業", "空運業",
    "倉庫・運輸関連", "情報・通信業", "卸売業", "小売業", "銀行業", "証券、商品先物取引業", "保険業",
    "その他金融業", "不動産業", "サービス業",
)  # fmt: skip
CHUNK_SIZE = 150


def split_businesses(rows: list[dict[str, Any]], out_dir: Path) -> None:
    """成否を付けた主の行の、買われた会社の名前と事業の内容を150件ずつ out_dir/in に書く。

    前に分けた入力があれば止める。分け直すと、分け終えた出力と入力が食い違う。
    """
    items = [
        {
            "key": f"{row['deal_id']}-{row['edinetCode']}",
            "acquirer": row["filerName"],
            "acquiree_name": row["acquiree_name"],
            "business": row["business"],
        }
        for row in rows
        if row["primary"] is True and row["success"] is not None
    ]
    write_chunks(items, out_dir, CHUNK_SIZE)


def read_industries(out_dir: Path) -> dict[str, str | None]:
    """サブエージェントが分けた業種を、案件ごと（deal_id-EDINET コード）に読む。

    出力の並びが入力と違うとき、33業種に無い業種があるときは止める。
    """
    industries: dict[str, str | None] = {}
    for result in read_outputs(out_dir):
        industry = cast(str | None, result["industry"])
        if industry is not None and industry not in INDUSTRIES:
            raise ValueError(f"33業種に無い業種がある：{industry}")
        industries[str(result["key"])] = industry
    return industries


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "split", help="成否を付けた主の行の「事業の内容」を、サブエージェントに渡す入力に分ける"
    ).add_argument("out_dir", type=Path)
    out_dir: Path = parser.parse_args().out_dir
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    labeled: list[dict[str, Any]] = [
        row | {"primary": row["primary"] == "True", "success": row["success"] or None}
        for row in rows
    ]
    split_businesses(labeled, out_dir)


if __name__ == "__main__":
    main()
