"""サブエージェントが抜き出した取得の案件から、同じ案件の重複をまとめて台帳にする。

同じ案件は、次の2通りで重ねて載っている。
- 同じ会社の翌年の有報に、前期の案件として載る。暫定の金額が翌年に確定していることもある
- 親会社と子会社（どちらも上場）が、それぞれの有報に載せる

使い方：uv run python -m ledger.acquisitions
"""

import argparse
import csv
import html
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from ledger.claude_deals import read_chunks, year_dirs
from ledger.deals import ACQUISITION, to_yen
from ledger.notes import KEPT_FIELDS, NOTES_DIR

CLAUDE_DEALS_DIR = Path("data/claude_deals")
ACQUISITIONS_PATH = Path("data/acquisitions.csv")
# 「株式会社新生銀行（現株式会社ＳＢＩ新生銀行）」のように、後ろに今の名前や前の名前を書いたものがある
FORMER_OR_CURRENT_NAME = re.compile(r"\((現|旧)[^)]*\)")
COMPANY_TYPES = re.compile(r"株式会社|有限会社|合同会社|\(株\)|\(有\)")
SYMBOLS = re.compile(r"[\s・,.、。()「」\"']")
DATE = re.compile(r"(\d{4}|平成元|平成\d+|令和元|令和\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")
ERA_START = {"平成": 1989, "令和": 2019}
# 同じ会社が同じ名前の会社を、これより空けて買っていれば別の案件とみなす
SAME_DEAL_DAYS = 365
TEXT_FIELDS = ("business", "legal_form", "voting_ratio")
# 期首に取得していたと仮定した売上高は、差額を入れた担当と全体の額を入れた担当に割れたので、台帳に入れない
AMOUNT_FIELDS = ("consideration", "goodwill", "assets_total")
COLUMNS = (
    "deal_id",
    "edinetCode",
    "secCode",
    "filerName",
    "docIDs",
    "acquiree_name",
    "acquisition_date",
    *TEXT_FIELDS,
    *(f"{field}_yen" for field in AMOUNT_FIELDS),
)


def normalize_name(name: str) -> str:
    """今の名前・前の名前の書き添え、会社の種類（株式会社など）、空白・記号を除き、全角・半角と大文字・小文字をそろえる。"""
    normalized = unicodedata.normalize("NFKC", html.unescape(name))
    # 書き添えの中の「(株)」の括弧で切れないように、会社の種類を先に除く
    normalized = FORMER_OR_CURRENT_NAME.sub("", COMPANY_TYPES.sub("", normalized))
    return SYMBOLS.sub("", normalized).lower()


def parse_date(text: str | None) -> date | None:
    """企業結合日の書かれ方（西暦・和暦）から日付を読む。2つあれば初めの日付（株式の取得日など）。"""
    match = DATE.search(unicodedata.normalize("NFKC", text or ""))
    if match is None:
        return None
    year_text, month, day = match.groups()
    era = year_text[:2]
    if era in ERA_START:
        year = ERA_START[era] + (0 if year_text[2:] == "元" else int(year_text[2:]) - 1)
    else:
        year = int(year_text)
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def merge_acquisitions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """取得の記録から、買った会社ごとに同じ案件を1行にまとめる。

    同じ会社・同じ名前で、企業結合日が1年以内なら同じ案件とする。日付の読めない記録は、
    その会社・その名前の案件が1つだけならそれにまとめ、2つ以上なら別の行にする。
    値は、後の有報に載ったものを優先する（暫定の金額より確定の金額を使うため）。
    別の会社が同じ日に同じ名前の会社を買っていれば、親会社と子会社とみなして同じ deal_id を付ける。
    どちらの会社で分析するかは、ここでは決めない。
    名前が無いもの、「当社」（逆取得）、「その他の企業結合」は、案件を特定できないので除く。
    """
    by_acquirer: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        name = cast(str | None, record["acquiree_name"])
        if name is None or normalize_name(name) == "当社" or name.startswith("その他"):
            continue
        by_acquirer[(record["edinetCode"], normalize_name(name))].append(record)
    rows: list[dict[str, Any]] = []
    for group in by_acquirer.values():
        dated = sorted(
            (r for r in group if parse_date(r["acquisition_date"])),
            key=lambda r: cast(date, parse_date(r["acquisition_date"])),
        )
        deals: list[list[dict[str, Any]]] = []
        for record in dated:
            first_date = parse_date(deals[-1][0]["acquisition_date"]) if deals else None
            record_date = cast(date, parse_date(record["acquisition_date"]))
            if first_date is not None and (record_date - first_date).days <= SAME_DEAL_DAYS:
                deals[-1].append(record)
            else:
                deals.append([record])
        undated = [r for r in group if parse_date(r["acquisition_date"]) is None]
        # 同じ名前の案件が2つ以上あれば、どちらの記録か決められないので別の行にする
        if len(deals) == 1:
            deals[0].extend(undated)
        elif undated:
            deals.append(undated)
        rows.extend(_merge(deal) for deal in deals)
    rows.sort(
        key=lambda row: (row["acquisition_date"] or "", row["acquiree_name"], row["edinetCode"])
    )
    deal_ids: dict[tuple[str, str], int] = {}
    for row in rows:
        when = row["acquisition_date"] or f"日付なし {row['edinetCode']}"
        key = (normalize_name(row["acquiree_name"]), when)
        row["deal_id"] = deal_ids.setdefault(key, len(deal_ids) + 1)
    return sorted(rows, key=lambda row: (row["deal_id"], row["edinetCode"]))


def _merge(records: list[dict[str, Any]]) -> dict[str, Any]:
    """後の有報の値を優先し、空の項目だけ前の有報の値で埋める。"""
    ordered = sorted(records, key=lambda r: (r["periodEnd"], r["docID"]), reverse=True)

    def latest(field: str) -> Any:
        return next((r[field] for r in ordered if r[field] is not None), None)

    dates = (parse_date(r["acquisition_date"]) for r in ordered)
    acquisition_date = next((d for d in dates if d is not None), None)
    row: dict[str, Any] = {
        "edinetCode": ordered[0]["edinetCode"],
        "secCode": ordered[0]["secCode"],
        "filerName": ordered[0]["filerName"],
        "docIDs": " ".join(sorted({r["docID"] for r in records})),
        "acquiree_name": ordered[0]["acquiree_name"],
        "acquisition_date": acquisition_date.isoformat() if acquisition_date else None,
    }
    row |= {field: latest(field) for field in TEXT_FIELDS}
    for field in AMOUNT_FIELDS:
        amount = cast(dict[str, str] | None, latest(field))
        row[f"{field}_yen"] = to_yen(amount["value"], amount["unit"]) if amount else None
    return row


def load_records(claude_deals_dir: Path, notes_dir: Path) -> list[dict[str, Any]]:
    """各年の出力から取得の案件を読み、有報の会社の情報（EDINET コード・決算期など）を付ける。"""
    records: list[dict[str, Any]] = []
    for year_dir in year_dirs(claude_deals_dir):
        for source, result in read_chunks(year_dir):
            notes_path = notes_dir / f"{source['docID']}.json"
            report = cast(dict[str, Any], json.loads(notes_path.read_text(encoding="utf-8")))
            company = {field: report[field] for field in KEPT_FIELDS}
            for deal in cast(list[dict[str, Any]], result["deals"]):
                if deal["kind"] == ACQUISITION:
                    records.append(company | deal)
    return records


def main() -> None:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    records = load_records(CLAUDE_DEALS_DIR, NOTES_DIR)
    rows = merge_acquisitions(records)
    # Excel で開いても文字化けしないように BOM を付ける
    with ACQUISITIONS_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    deals = len({row["deal_id"] for row in rows})
    print(
        f"取得の記録 {len(records)} 件 → 台帳 {len(rows)} 行（案件 {deals} 件）：{ACQUISITIONS_PATH}"
    )


if __name__ == "__main__":
    main()
