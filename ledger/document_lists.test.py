import json
from datetime import date
from pathlib import Path

import httpx

from ledger.document_lists import count_annual_reports_by_month, save_document_lists


def _client(seen: list[str]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        day = request.url.params["date"]
        seen.append(day)
        results = [{"docID": f"S-{day}", "docTypeCode": "120"}]
        return httpx.Response(
            200, json={"metadata": {"status": "200", "message": "OK"}, "results": results}
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


def _document(doc_type: str) -> dict[str, str]:
    return {
        "ordinanceCode": "010",
        "docTypeCode": doc_type,
        "withdrawalStatus": "0",
        "docInfoEditStatus": "0",
        "disclosureStatus": "0",
        "legalStatus": "1",
    }


def 期間の各日の一覧を日ごとのファイルに保存する(tmp_path: Path) -> None:
    seen: list[str] = []

    save_document_lists(_client(seen), date(2025, 6, 30), date(2025, 7, 1), "key", tmp_path, 0)

    assert seen == ["2025-06-30", "2025-07-01"]
    saved = json.loads((tmp_path / "2025-07-01.json").read_text(encoding="utf-8"))
    assert saved == [{"docID": "S-2025-07-01", "docTypeCode": "120"}]


def 保存済みの日は取り直さない(tmp_path: Path) -> None:
    (tmp_path / "2025-06-30.json").write_text("[]", encoding="utf-8")
    seen: list[str] = []

    save_document_lists(_client(seen), date(2025, 6, 30), date(2025, 7, 1), "key", tmp_path, 0)

    assert seen == ["2025-07-01"]


def 月ごとに有報の件数を数える(tmp_path: Path) -> None:
    lists = {
        "2025-06-27": [_document("120"), _document("120"), _document("180")],
        "2025-06-30": [_document("120")],
        "2025-07-01": [],
    }
    for day, documents in lists.items():
        (tmp_path / f"{day}.json").write_text(json.dumps(documents), encoding="utf-8")

    assert count_annual_reports_by_month(tmp_path) == {"2025-06": 3, "2025-07": 0}
