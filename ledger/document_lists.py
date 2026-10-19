"""期間の書類一覧を日ごとに保存し、月ごとの有報の件数を数える。

使い方：uv run --env-file .env python -m ledger.document_lists 2024-07-01 2025-06-30
"""

import argparse
import json
import os
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from ledger.edinet import fetch_document_list, is_annual_report

LISTS_DIR = Path("data/document_lists")
# 上限は公表されていない。429 が返ったら延ばす
WAIT_SECONDS = 1.0


def save_document_lists(
    client: httpx.Client,
    start: date,
    end: date,
    api_key: str,
    out_dir: Path,
    wait_seconds: float,
) -> None:
    """start から end までの各日の一覧を out_dir/YYYY-MM-DD.json に保存する。保存済みの日は飛ばす。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    day = start
    while day <= end:
        path = out_dir / f"{day.isoformat()}.json"
        if not path.exists():
            documents = fetch_document_list(client, day, api_key)
            path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
            time.sleep(wait_seconds)
        day += timedelta(days=1)


def count_annual_reports_by_month(lists_dir: Path) -> dict[str, int]:
    """保存した一覧から、月（YYYY-MM）ごとの有報の件数を数える。有報が無い月も 0 で出す。"""
    counts: Counter[str] = Counter()
    for path in sorted(lists_dir.glob("*.json")):
        month = path.stem[:7]
        documents = cast(list[dict[str, Any]], json.loads(path.read_text(encoding="utf-8")))
        counts[month] += sum(1 for document in documents if is_annual_report(document))
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args()
    # 当日の一覧はまだ増える。保存すると、保存済みとして二度と取り直さない
    if args.end >= date.today():
        parser.error("end は昨日以前の日付にする")
    with httpx.Client(timeout=60) as client:
        save_document_lists(
            client, args.start, args.end, os.environ["EDINET_API_KEY"], LISTS_DIR, WAIT_SECONDS
        )
    for month, count in count_annual_reports_by_month(LISTS_DIR).items():
        print(f"{month}\t{count}")


if __name__ == "__main__":
    main()
