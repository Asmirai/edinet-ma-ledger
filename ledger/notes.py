"""会社の有報を1件ずつ取り、「企業結合等関係」の注記の文章を取り出して保存する。

使い方：uv run --env-file .env python -m ledger.notes 2024-10-01 2025-09-30
（書類一覧を ledger.document_lists で先に取っておく）
"""

import argparse
import csv
import io
import json
import os
import time
import zipfile
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx

from ledger.document_lists import LISTS_DIR, WAIT_SECONDS
from ledger.edinet import fetch_document_csv_zip, is_annual_report

DOCUMENTS_DIR = Path("data/documents")
NOTES_DIR = Path("data/notes")
# 有報の本体の CSV。監査報告書（jpaud-）などは読まない
REPORT_CSV_PREFIX = "XBRL_TO_CSV/jpcrp"
# 日本基準は jpcrp_cor:NotesBusinessCombinations…TextBlock、IFRS は jpigp_cor:…IFRSTextBlock
NOTE_PREFIX = "NotesBusinessCombinations"
NOTE_SUFFIX = "TextBlock"
# 書類一覧から注記のファイルへ写す項目
KEPT_FIELDS = ("docID", "edinetCode", "secCode", "filerName", "periodEnd")


def extract_business_combination_notes(archive: bytes) -> list[dict[str, str]]:
    """CSV の ZIP から、企業結合の注記（要素ID・コンテキストID・文章）を取り出す。"""
    notes: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for name in sorted(files.namelist()):
            if not name.startswith(REPORT_CSV_PREFIX):
                continue
            # UTF-16（BOM 付き）のタブ区切り。1行目は見出し
            text = files.read(name).decode("utf-16")
            for row in list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]:
                element, context, value = row[0], row[2], row[8]
                local_name = element.split(":")[-1]
                if local_name.startswith(NOTE_PREFIX) and local_name.endswith(NOTE_SUFFIX):
                    notes.append({"element": element, "context": context, "text": value})
    return notes


def save_notes(
    client: httpx.Client,
    documents: Iterable[Mapping[str, object]],
    api_key: str,
    documents_dir: Path,
    notes_dir: Path,
    wait_seconds: float,
) -> None:
    """有報ごとに ZIP を documents_dir に、注記を notes_dir/{docID}.json に保存する。

    注記が無い有報も空の一覧で保存し、取得済みの印にする。保存済みの有報は飛ばす。
    """
    documents_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)
    for document in documents:
        doc_id = str(document["docID"])
        notes_path = notes_dir / f"{doc_id}.json"
        if notes_path.exists():
            continue
        archive = fetch_document_csv_zip(client, doc_id, api_key)
        (documents_dir / f"{doc_id}.zip").write_bytes(archive)
        saved = {field: document[field] for field in KEPT_FIELDS}
        saved["notes"] = extract_business_combination_notes(archive)
        notes_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        time.sleep(wait_seconds)


def annual_reports(lists_dir: Path, start: date, end: date) -> list[dict[str, Any]]:
    """保存した書類一覧から、期間に提出された会社の有報のうち、CSV があるものを集める。

    外国の会社などの有報には XBRL が無く、CSV を取ろうとすると 404 になる（1年分の約3%）。
    """
    reports: list[dict[str, Any]] = []
    for path in sorted(lists_dir.glob("*.json")):
        if start <= date.fromisoformat(path.stem) <= end:
            documents = cast(list[dict[str, Any]], json.loads(path.read_text(encoding="utf-8")))
            reports.extend(
                document
                for document in documents
                if is_annual_report(document) and document["csvFlag"] == "1"
            )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args()
    reports = annual_reports(LISTS_DIR, args.start, args.end)
    with httpx.Client(timeout=120) as client:
        save_notes(
            client, reports, os.environ["EDINET_API_KEY"], DOCUMENTS_DIR, NOTES_DIR, WAIT_SECONDS
        )
    with_notes = sum(
        1
        for report in reports
        if json.loads((NOTES_DIR / f"{report['docID']}.json").read_text(encoding="utf-8"))["notes"]
    )
    print(f"有報 {len(reports)} 件のうち、企業結合の注記があるのは {with_notes} 件")


if __name__ == "__main__":
    main()
