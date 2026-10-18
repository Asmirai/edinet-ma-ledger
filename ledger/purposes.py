"""買収の目的と、買い手の事業との近さを、注記の「企業結合を行った主な理由」から読み取らせる。

注記全体を渡すと重いので、相手の名前の後にある「主な理由」の部分（300字まで）だけを切り出して渡す。
対価の種類（現金か株式か）は、法的形式の書き方から機械で読む。

使い方：
  uv run python -m ledger.purposes split data/purposes
  （in/chunk_*.jsonl を1つずつサブエージェントに渡し、purpose_instructions.md の指示で out/chunk_*.json に書かせる）
"""

import csv
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from ledger.acquisitions import CLAUDE_DEALS_DIR
from ledger.chunks import read_outputs, write_chunks
from ledger.claude_deals import read_chunks, year_dirs
from ledger.impairments import core_name
from ledger.roa import LABELED_PATH

PURPOSES = (
    "同じ事業の拡大",
    "新しい事業への参入",
    "地域の拡大・海外進出",
    "仕入れ・販売の取り込み",
    "技術・人材の獲得",
    "再建・救済",
    "その他",
)
RELATEDNESS = ("同じ事業", "近い事業", "遠い事業")
# 成否を付けられるのは、買収後3期の有報がそろう年まで
LAST_BASE_YEAR = 2022
EXCERPT_LENGTH = 300
# 相手の名前から「主な理由」までは、事業の内容をはさんでも1,500字に収まっていた
SEARCH_LENGTH = 1500
CHUNK_SIZE = 200


def reason_excerpt(note_text: str, name: str) -> str | None:
    """相手の名前の後にある「主な理由」の部分を、300字まで切り出す。見つからなければ None。"""
    text = unicodedata.normalize("NFKC", note_text)
    at = text.find(core_name(name))
    if at == -1:
        return None
    reason_at = text.find("主な理由", at, at + SEARCH_LENGTH)
    if reason_at == -1:
        return None
    start = reason_at + len("主な理由")
    return text[start : start + EXCERPT_LENGTH].strip()


def consideration_kind(legal_form: str) -> str:
    """法的形式の書き方から、対価の種類（現金・株式・現金と株式・不明）を読む。"""
    cash = "現金" in legal_form
    stock = any(word in legal_form for word in ("株式交換", "株式を対価", "当社株式", "普通株式"))
    if cash and stock:
        return "現金と株式"
    if cash:
        return "現金"
    if stock:
        return "株式"
    return "不明"


def purpose_items(
    rows: list[dict[str, str]], texts_by_doc: dict[str, list[str]], last_base_year: int
) -> list[dict[str, Any]]:
    """成否を付けられる年の主の行について、目的と近さを聞く入力を作る。主な理由が見つからなければ None を入れる。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        if (
            row["primary"] != "True"
            or not row["base_year"]
            or int(row["base_year"]) > last_base_year
        ):
            continue
        excerpts = (
            reason_excerpt(text, row["acquiree_name"])
            for doc_id in row["docIDs"].split()
            for text in texts_by_doc.get(doc_id, [])
        )
        items.append(
            {
                "key": f"{row['deal_id']}-{row['edinetCode']}",
                "acquirer": row["filerName"],
                "acquirer_industry": row["industry"],
                "acquiree_name": row["acquiree_name"],
                "business": row["business"],
                "reason": next((e for e in excerpts if e), None),
            }
        )
    return items


def read_purposes(out_dir: Path) -> dict[str, tuple[str, str]]:
    """読み取らせた（目的、近さ）を案件ごとに読む。一覧に無い値があれば止める。"""
    purposes: dict[str, tuple[str, str]] = {}
    for result in read_outputs(out_dir):
        purpose, relatedness = str(result["purpose"]), str(result["relatedness"])
        if purpose not in PURPOSES or relatedness not in RELATEDNESS:
            raise ValueError(f"{result['key']} の分類が一覧に無い：{purpose}・{relatedness}")
        purposes[str(result["key"])] = (purpose, relatedness)
    return purposes


def main() -> None:
    command, out_dir = sys.argv[1], Path(sys.argv[2])
    if command != "split":
        raise SystemExit(f"知らないコマンド：{command}（split）")
    texts_by_doc: defaultdict[str, list[str]] = defaultdict(list)
    for year_dir in year_dirs(CLAUDE_DEALS_DIR):
        for source, _ in read_chunks(year_dir):
            texts_by_doc[source["docID"]].append(source["text"])
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    items = purpose_items(rows, texts_by_doc, LAST_BASE_YEAR)
    write_chunks(items, out_dir, CHUNK_SIZE)
    found = sum(item["reason"] is not None for item in items)
    print(f"{len(items)} 件（主な理由を切り出せたもの {found} 件）")


if __name__ == "__main__":
    main()
