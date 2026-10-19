"""買収で生じたのれんを、買収後3期のうちに減損したかを拾う。

減損は、減損損失の注記・会計上の見積り・セグメント情報などに散らばって書かれる。
そこで、基準の期から3期後までの有報の文章から、「減損」と相手の名前の両方を含む箇所を機械で拾う。
拾えた案件だけを Claude Code のサブエージェントに読ませ、その買収ののれんを減損したかを確かめさせる。

使い方：
  uv run python -m ledger.impairments split data/impairments
  （in/chunk_*.jsonl を1つずつサブエージェントに渡し、impairment_instructions.md の指示で out/chunk_*.json に書かせる）
  uv run python -m ledger.impairments label data/impairments
"""

import csv
import html
import io
import json
import sys
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

from ledger.acquisitions import COMPANY_TYPES, FORMER_OR_CURRENT_NAME
from ledger.chunks import read_outputs, write_chunks
from ledger.features import legal_form_kind
from ledger.notes import DOCUMENTS_DIR, NOTES_DIR, REPORT_CSV_PREFIX
from ledger.roa import LABELED_PATH

IMPAIRMENTS_PATH = Path("data/impairments.csv")
YEARS_AFTER = 3
# 名前の前後に何字まで渡すか。減損の文は、名前の前後200字に収まっていた
WINDOW = 200
# これより短い名前は、別の語の一部として拾ってしまう
MIN_NAME_LENGTH = 3
CHUNK_SIZE = 40


def core_name(name: str) -> str:
    """会社の種類（株式会社など）と、今の名前・前の名前の書き添えを除いた名前。全角・半角はそろえる。"""
    normalized = unicodedata.normalize("NFKC", html.unescape(name))
    return FORMER_OR_CURRENT_NAME.sub("", COMPANY_TYPES.sub("", normalized)).strip()


def impairment_passages(archive: bytes, name: str) -> list[str]:
    """有報の文章から、相手の名前の前後200字のうち「減損」を含むものを抜き出す。"""
    core = core_name(name)
    if len(core) < MIN_NAME_LENGTH:
        return []
    passages: list[str] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for file_name in sorted(files.namelist()):
            if not file_name.startswith(REPORT_CSV_PREFIX):
                continue
            text_file = io.StringIO(files.read(file_name).decode("utf-16"))
            for row in list(csv.reader(text_file, delimiter="\t"))[1:]:
                text = unicodedata.normalize("NFKC", row[8])
                if "減損" not in text:
                    continue
                start = text.find(core)
                while start != -1:
                    passage = text[max(0, start - WINDOW) : start + len(core) + WINDOW]
                    if "減損" in passage and passage not in passages:
                        passages.append(passage)
                    start = text.find(core, start + len(core))
    return passages


def candidate_items(
    deals: list[dict[str, Any]],
    reports_by_company: dict[str, list[tuple[date, str]]],
    read_archive: Callable[[str], bytes],
) -> list[dict[str, Any]]:
    """基準の期から3期後までの有報で、減損と相手の名前を含む箇所が拾えた案件を返す。"""
    items: list[dict[str, Any]] = []
    for deal in deals:
        base_year = int(deal["base_year"])
        passages: list[dict[str, str]] = []
        for period_end, doc_id in sorted(reports_by_company.get(deal["edinetCode"], [])):
            if base_year <= period_end.year <= base_year + YEARS_AFTER:
                for text in impairment_passages(read_archive(doc_id), deal["acquiree_name"]):
                    passages.append({"periodEnd": period_end.isoformat(), "text": text})
        if passages:
            items.append(
                {
                    "key": deal["key"],
                    "acquirer": deal["filerName"],
                    "acquiree_name": deal["acquiree_name"],
                    "base_year": base_year,
                    "passages": passages,
                }
            )
    return items


def read_impairments(out_dir: Path) -> dict[str, bool]:
    """サブエージェントが確かめた結果を、案件ごとに読む。"""
    return {str(r["key"]): bool(r["impaired"]) for r in read_outputs(out_dir)}


def label_impairments(
    deals: list[dict[str, Any]],
    reports_by_company: dict[str, list[tuple[date, str]]],
    confirmed: dict[str, bool],
) -> dict[str, tuple[bool | None, str | None]]:
    """案件ごとに、（のれんを減損したか、分からない理由）。分からない案件は （None, 理由）。

    候補に拾えなかった案件は、減損していないとみなす。ただし次は拾えたかどうかが当てにならないので None にする。
    - 3期後の有報が無い（まだ出ていない、または上場廃止などで出なくなった）
    - 事業譲受：相手の名前に売り手の会社名が入り、減損の注記は事業の名前で書かれるので、名前で探しても当たらない
    - 会社の種類を除いた名前が短すぎて、探していない
    """
    labels: dict[str, tuple[bool | None, str | None]] = {}
    for deal in deals:
        years = {end.year for end, _ in reports_by_company.get(deal["edinetCode"], [])}
        if int(deal["base_year"]) + YEARS_AFTER not in years:
            labels[deal["key"]] = (None, "3期後の有報が無い")
        elif legal_form_kind(deal["legal_form"]) == "事業譲受":
            labels[deal["key"]] = (None, "事業譲受")
        elif len(core_name(deal["acquiree_name"])) < MIN_NAME_LENGTH:
            labels[deal["key"]] = (None, "名前が短すぎる")
        else:
            labels[deal["key"]] = (confirmed.get(deal["key"], False), None)
    return labels


def count_outcomes(
    labels: dict[str, tuple[bool | None, str | None]], base_years: dict[str, int], latest_year: int
) -> dict[str, int]:
    """判定の結果ごとの件数。3期後の有報が無い案件は、まだ出ていないもの（データの最後の年より後）と、
    提出が止まったものに分けて数える。
    """
    counts: defaultdict[str, int] = defaultdict(int)
    for key, (value, reason) in labels.items():
        if reason == "3期後の有報が無い":
            not_yet = base_years[key] + YEARS_AFTER > latest_year
            reason = "3期後の有報がまだ出ていない" if not_yet else "3期後の有報の提出が止まった"
        counts[reason or ("減損した" if value else "減損していない")] += 1
    return dict(counts)


def _deals_and_reports() -> tuple[list[dict[str, Any]], dict[str, list[tuple[date, str]]]]:
    """のれんが生じた主の行と、会社ごとの有報（決算日、docID）を読む。"""
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    deals = [
        row | {"key": f"{row['deal_id']}-{row['edinetCode']}"}
        for row in rows
        if row["primary"] == "True"
        and row["base_year"]
        and row["goodwill_yen"]
        and float(row["goodwill_yen"]) > 0
    ]
    reports: defaultdict[str, list[tuple[date, str]]] = defaultdict(list)
    for notes_path in NOTES_DIR.glob("*.json"):
        report = cast(dict[str, Any], json.loads(notes_path.read_text(encoding="utf-8")))
        reports[report["edinetCode"]].append(
            (date.fromisoformat(report["periodEnd"]), report["docID"])
        )
    return deals, dict(reports)


def main() -> None:
    command, out_dir = sys.argv[1], Path(sys.argv[2])
    deals, reports = _deals_and_reports()
    if command == "split":
        items = candidate_items(
            deals, reports, lambda doc_id: (DOCUMENTS_DIR / f"{doc_id}.zip").read_bytes()
        )
        write_chunks(items, out_dir, CHUNK_SIZE)
        print(f"のれんが生じた案件 {len(deals)} 件のうち、減損の候補 {len(items)} 件")
        return
    if command != "label":
        raise SystemExit(f"知らないコマンド：{command}（split か label）")
    labels = label_impairments(deals, reports, read_impairments(out_dir))
    with IMPAIRMENTS_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["key", "impaired", "reason"])
        writer.writerows(
            (key, "" if value is None else value, reason or "")
            for key, (value, reason) in labels.items()
        )
    latest_year = max(end.year for found in reports.values() for end, _ in found)
    base_years = {deal["key"]: int(deal["base_year"]) for deal in deals}
    print(f"{count_outcomes(labels, base_years, latest_year)}：{IMPAIRMENTS_PATH}")


if __name__ == "__main__":
    main()
