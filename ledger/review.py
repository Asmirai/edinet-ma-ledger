"""抜き出しの誤り率を出すため、注記を無作為に選び、Opus のサブエージェントが原文と見比べた結果を数える。

使い方：
  uv run python -m ledger.review sample data/review
  （in/chunk_*.jsonl を1つずつサブエージェントに渡し、review_instructions.md の指示で out/chunk_*.json に書かせる）
  uv run python -m ledger.review score data/review
"""

import argparse
import json
import random
from pathlib import Path
from typing import Any, cast

from ledger.acquisitions import CLAUDE_DEALS_DIR
from ledger.claude_deals import read_chunks, year_dirs
from ledger.deals import ACQUISITION

NOTES_PER_YEAR = 20
SEED = 20261018
CHUNK_SIZE = 10
# 台帳に入れる項目のうち、成否の判定に使うもの。事業の内容と法的形式は言い換えの揺れが大きく、数えない
FIELDS = ("acquisition_date", "voting_ratio", "consideration", "goodwill", "assets_total")


def sample_notes(claude_deals_dir: Path, per_year: int, seed: int, review_dir: Path) -> None:
    """年ごとに per_year 件の注記を無作為に選び、原文と抜き出した取得の案件を10件ずつ review_dir/in に書く。

    取得の案件が無い注記も選ぶ。取得の見落としも数えるため。
    前に選んだ注記があれば止める。選び直すと、見比べ済みの結果と入力が食い違う。
    """
    if any((review_dir / "in").glob("chunk_*.jsonl")):
        raise FileExistsError(f"{review_dir / 'in'} に前に選んだ注記がある")
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    for year_dir in year_dirs(claude_deals_dir):
        for source, result in rng.sample(read_chunks(year_dir), per_year):
            extracted = [
                {"acquiree_name": deal["acquiree_name"]} | {field: deal[field] for field in FIELDS}
                for deal in cast(list[dict[str, Any]], result["deals"])
                if deal["kind"] == ACQUISITION
            ]
            items.append(source | {"extracted": extracted})
    (review_dir / "in").mkdir(parents=True, exist_ok=True)
    (review_dir / "out").mkdir(exist_ok=True)
    for start in range(0, len(items), CHUNK_SIZE):
        lines = "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in items[start : start + CHUNK_SIZE]
        )
        (review_dir / "in" / f"chunk_{start // CHUNK_SIZE:03d}.jsonl").write_text(
            lines, encoding="utf-8"
        )


def score_reviews(review_dir: Path) -> dict[str, Any]:
    """見比べた結果から、正しい取得の数、見落とし、取得でないのに取得とした数、項目ごとの誤りを数える。

    項目の誤りは、見落とさなかった取得についてだけ数える。
    """
    notes = acquisitions = missed = false_acquisitions = 0
    field_errors = dict.fromkeys(FIELDS, 0)
    for _source, result in read_chunks(review_dir):
        notes += 1
        false_acquisitions += len(cast(list[str], result["false_acquisitions"]))
        for acquisition in cast(list[dict[str, Any]], result["acquisitions"]):
            acquisitions += 1
            if not acquisition["found"]:
                missed += 1
                continue
            for field in FIELDS:
                field_errors[field] += acquisition["fields"][field]["verdict"] == "誤"
    return {
        "notes": notes,
        "acquisitions": acquisitions,
        "missed": missed,
        "false_acquisitions": false_acquisitions,
        "field_errors": field_errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "sample", help="年ごとに注記を無作為に選び、採点の入力に分ける"
    ).add_argument("review_dir", type=Path)
    commands.add_parser("score", help="採点の出力から誤りを数える").add_argument(
        "review_dir", type=Path
    )
    args = parser.parse_args()
    review_dir: Path = args.review_dir
    if args.command == "sample":
        sample_notes(CLAUDE_DEALS_DIR, NOTES_PER_YEAR, SEED, review_dir)
        return
    scores = score_reviews(review_dir)
    found = scores["acquisitions"] - scores["missed"]
    print(
        f"注記 {scores['notes']} 件、正しい取得 {scores['acquisitions']} 件。"
        f"見落とし {scores['missed']} 件、取得でないのに取得とした {scores['false_acquisitions']} 件"
    )
    for field, errors in scores["field_errors"].items():
        print(f"{field}：誤り {errors}/{found} 件（{errors / found:.1%}）")


if __name__ == "__main__":
    main()
