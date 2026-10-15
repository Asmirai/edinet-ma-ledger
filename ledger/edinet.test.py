import json
from datetime import date

import httpx
import pytest

from ledger.edinet import EdinetError, fetch_document_list, is_annual_report


def _client(status_code: int, body: str, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status_code, text=body)

    return httpx.Client(transport=httpx.MockTransport(handle))


def _ok(results: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "metadata": {"status": "200", "message": "OK", "resultset": {"count": len(results)}},
            "results": results,
        }
    )


def _annual_report(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "docID": "S1000001",
        "ordinanceCode": "010",
        "docTypeCode": "120",
        "withdrawalStatus": "0",
        "docInfoEditStatus": "0",
        "disclosureStatus": "0",
        "legalStatus": "1",
    }
    return document | changes


def 指定した日の書類一覧を返す() -> None:
    seen: list[httpx.Request] = []
    client = _client(200, _ok([_annual_report()]), seen)

    documents = fetch_document_list(client, date(2025, 6, 27), "key")

    assert documents == [_annual_report()]
    assert seen[0].url.params["date"] == "2025-06-27"
    assert seen[0].url.params["type"] == "2"
    assert seen[0].url.params["Subscription-Key"] == "key"


def 提出が無い日は空の一覧を返す() -> None:
    client = _client(200, _ok([]))

    assert fetch_document_list(client, date(2025, 6, 28), "key") == []


def メタデータのステータスが200でなければ止める() -> None:
    body = json.dumps({"metadata": {"status": "404", "message": "Not Found"}})
    client = _client(200, body)

    with pytest.raises(EdinetError, match="404 Not Found"):
        fetch_document_list(client, date(2025, 6, 27), "key")


def キーが無効なら止める() -> None:
    body = json.dumps(
        {"StatusCode": 401, "message": "Access denied due to invalid subscription key."}
    )
    client = _client(200, body)

    with pytest.raises(EdinetError, match="401 Access denied"):
        fetch_document_list(client, date(2025, 6, 27), "key")


def メンテナンス中でHTTPのエラーが返れば止める() -> None:
    client = _client(503, "<html>Sorry</html>")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_document_list(client, date(2025, 6, 27), "key")


def 縦覧中の有報は残す() -> None:
    assert is_annual_report(_annual_report())


def 延長期間中の有報も残す() -> None:
    assert is_annual_report(_annual_report(legalStatus="2"))


def 閲覧期間が満了した書類は除く() -> None:
    assert not is_annual_report(_annual_report(legalStatus="0"))


def 訂正有報は除く() -> None:
    assert not is_annual_report(_annual_report(docTypeCode="130"))


def 取り下げられた有報は除く() -> None:
    assert not is_annual_report(_annual_report(withdrawalStatus="2"))


def 書類情報を修正したことの通知は除く() -> None:
    assert not is_annual_report(_annual_report(docInfoEditStatus="1"))


def 書類情報が修正された有報は残す() -> None:
    assert is_annual_report(_annual_report(docInfoEditStatus="2"))


def 不開示の有報は除く() -> None:
    assert not is_annual_report(_annual_report(disclosureStatus="2"))


def 投資信託などのファンドの有報は除く() -> None:
    assert not is_annual_report(_annual_report(ordinanceCode="030"))
