import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx

from ledger.notes import annual_reports, extract_business_combination_notes, save_notes

HEADER = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]


def _row(element: str, context: str, value: str) -> list[str]:
    return [element, "項目", context, "当期", "連結", "期間", "－", "－", value]


def _csv(rows: list[list[str]]) -> bytes:
    lines = ["\t".join(f'"{cell}"' for cell in row) for row in [HEADER, *rows]]
    return "\r\n".join(lines).encode("utf-16")


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def 日本基準とIFRSの企業結合の注記を取り出す() -> None:
    rows = [
        _row(
            "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock",
            "CurrentYearDuration",
            "（取得による企業結合）A社",
        ),
        _row(
            "jpigp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsIFRSTextBlock",
            "CurrentYearDuration",
            "８．企業結合 B社",
        ),
    ]
    archive = _zip({"XBRL_TO_CSV/jpcrp030000-asr-001_E00001.csv": _csv(rows)})

    assert extract_business_combination_notes(archive) == [
        {
            "element": "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock",
            "context": "CurrentYearDuration",
            "text": "（取得による企業結合）A社",
        },
        {
            "element": "jpigp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsIFRSTextBlock",
            "context": "CurrentYearDuration",
            "text": "８．企業結合 B社",
        },
    ]


def ほかの注記と監査報告書は取り出さない() -> None:
    report = _csv(
        [_row("jpcrp_cor:NotesRegardingDividendTextBlock", "CurrentYearDuration", "配当")]
    )
    audit = _csv(
        [
            _row(
                "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock",
                "CurrentYearDuration",
                "監査報告書に紛れた行",
            )
        ]
    )
    archive = _zip(
        {
            "XBRL_TO_CSV/jpcrp030000-asr-001_E00001.csv": report,
            "XBRL_TO_CSV/jpaud-aar-cn-001_E00001.csv": audit,
        }
    )

    assert extract_business_combination_notes(archive) == []


def _document(doc_id: str) -> dict[str, object]:
    return {
        "docID": doc_id,
        "edinetCode": "E00001",
        "secCode": "10000",
        "filerName": "テスト株式会社",
        "periodEnd": "2025-03-31",
    }


def _client(seen: list[str]) -> httpx.Client:
    note = _row(
        "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock",
        "CurrentYearDuration",
        "（取得による企業結合）A社",
    )
    archive = _zip({"XBRL_TO_CSV/jpcrp030000-asr-001_E00001.csv": _csv([note])})

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            200, content=archive, headers={"Content-Type": "application/octet-stream"}
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


def 有報ごとにZIPと注記を保存する(tmp_path: Path) -> None:
    seen: list[str] = []

    save_notes(_client(seen), [_document("S1")], "key", tmp_path / "docs", tmp_path / "notes", 0)

    assert seen == ["S1"]
    assert (tmp_path / "docs" / "S1.zip").exists()
    saved = json.loads((tmp_path / "notes" / "S1.json").read_text(encoding="utf-8"))
    assert saved["filerName"] == "テスト株式会社"
    assert saved["notes"][0]["text"] == "（取得による企業結合）A社"


def 注記を保存済みの有報は取り直さない(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "S1.json").write_text("{}", encoding="utf-8")
    seen: list[str] = []

    save_notes(
        _client(seen),
        [_document("S1"), _document("S2")],
        "key",
        tmp_path / "docs",
        tmp_path / "notes",
        0,
    )

    assert seen == ["S2"]


def _listed(doc_id: str, csv_flag: str) -> dict[str, str]:
    return {
        "docID": doc_id,
        "ordinanceCode": "010",
        "docTypeCode": "120",
        "withdrawalStatus": "0",
        "docInfoEditStatus": "0",
        "disclosureStatus": "0",
        "legalStatus": "1",
        "csvFlag": csv_flag,
    }


def CSVが無い有報は集めない(tmp_path: Path) -> None:
    documents = [_listed("S1", "1"), _listed("S2", "0")]
    (tmp_path / "2025-06-27.json").write_text(json.dumps(documents), encoding="utf-8")

    reports = annual_reports(tmp_path, date(2025, 6, 27), date(2025, 6, 27))

    assert [report["docID"] for report in reports] == ["S1"]
