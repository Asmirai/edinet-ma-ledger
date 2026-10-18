import json
from pathlib import Path

import httpx
import pytest

from ledger.deals import (
    clean_listed,
    extract_deals,
    is_substantive,
    save_deals,
    section_for,
    to_yen,
    unverified_fields,
)

NOTE = (
    "（企業結合等関係）取得による企業結合（１）企業結合の概要① 被取得企業の名称及びその事業の内容"
    "被取得企業の名称：株式会社アドウィル事業の内容：コミュニケーションデザインサービス"
    "③ 企業結合日2024年５月８日④ 企業結合の法的形式株式取得⑥ 取得した議決権比率100％"
    "取得原価 1,410 百万円 発生したのれんの金額１２１百万円"
)

DETAILS: dict[str, object] = {
    "business": "コミュニケーションデザインサービス",
    "acquisition_date": "2024年５月８日",
    "legal_form": "株式取得",
    "voting_ratio": "100％",
    "consideration": {"value": "1,410", "unit": "百万円"},
    "goodwill": {"value": "１２１", "unit": "百万円"},
    "assets_total": None,
    "proforma_sales": None,
}


def _listed(name: str, kind: str = "取得", period: str = "当期") -> dict[str, str]:
    return {"acquiree_name": name, "kind": kind, "period": period}


def _deal(**changes: object) -> dict[str, object]:
    return _listed("株式会社アドウィル") | DETAILS | changes


def 百万円を円に直す() -> None:
    assert to_yen("1,410", "百万円") == 1_410_000_000


def 全角の数字と千円も円に直す() -> None:
    assert to_yen("１，４１０", "千円") == 1_410_000


def 億円と円も円に直す() -> None:
    assert to_yen("3.5", "億円") == 350_000_000
    assert to_yen("12,000", "円") == 12_000


def 三角のついた数字は負にする() -> None:
    assert to_yen("△823", "百万円") == -823_000_000


def 知らない単位や数字でない値は円に直さない() -> None:
    assert to_yen("1,410", "ドル") is None
    assert to_yen("非開示", "百万円") is None


def 原文に全部載っていれば照合できない項目は無い() -> None:
    assert unverified_fields(_deal(), NOTE) == []


def 原文に無い名称と金額を照合できない項目として返す() -> None:
    deal = _deal(
        acquiree_name="株式会社ファーストライフ", goodwill={"value": "450", "unit": "百万円"}
    )

    assert unverified_fields(deal, NOTE) == ["acquiree_name", "goodwill"]


def 空の項目は照合しない() -> None:
    assert unverified_fields(_deal(business=None, consideration=None), NOTE) == []


def 該当事項なしの定型文はLLMに渡さない() -> None:
    assert not is_substantive("（企業結合等関係）　該当事項はありません。 ")


def 中身のある注記はLLMに渡す() -> None:
    assert is_substantive(NOTE)


def 前期の記載が無い注記の前期の案件は捨てる() -> None:
    listed = [_listed("A社"), _listed("B社", period="前期")]

    assert clean_listed(listed, "当連結会計年度（自2024年４月１日）A社 B社") == [_listed("A社")]


def 前期の記載がある注記の前期の案件は残す() -> None:
    listed = [_listed("A社"), _listed("B社", period="前期")]

    assert clean_listed(listed, "前連結会計年度 B社 当連結会計年度 A社") == listed


def 同じ名前と期の案件は1件にする() -> None:
    listed = [_listed("A社"), _listed("A社", kind="その他"), _listed("B社")]

    assert clean_listed(listed, "A社 B社") == [_listed("A社"), _listed("B社")]


def 案件の部分は名前が初めて出る所から次の案件の名前の前まで() -> None:
    note = "前置き。A社を取得した。A社の取得原価10。B社を取得した。B社の取得原価20。"

    assert section_for(note, ["A社", "B社"], 0) == "A社を取得した。A社の取得原価10。"
    assert section_for(note, ["A社", "B社"], 1) == "B社を取得した。B社の取得原価20。"


def 名前が原文に無い案件は注記の全文を渡す() -> None:
    assert section_for("A社の取得原価10。", ["A社", "C社"], 1) == "A社の取得原価10。"


def _ollama(listed: list[dict[str, str]], prompts: list[str]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompts.append(body["messages"][0]["content"])
        assert body["stream"] is False
        assert body["think"] is False
        assert body["options"] == {"temperature": 0, "num_ctx": 16384}
        is_listing = "deals" in body["format"]["properties"]
        content = {"deals": listed} if is_listing else DETAILS
        message = {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}
        return httpx.Response(200, json={"message": message})

    return httpx.Client(transport=httpx.MockTransport(handle), base_url="http://ollama")


def 取得の案件だけ詳細を聞いて一覧にする() -> None:
    prompts: list[str] = []
    listed = [
        _listed("株式会社アドウィル"),
        _listed("株式会社ダンボールワン", kind="共通支配下の取引"),
    ]
    note = NOTE + "（共通支配下の取引等）株式会社ダンボールワンを吸収合併"

    deals = extract_deals(_ollama(listed, prompts), note, "qwen3:8b")

    assert deals[0] == _deal() | {"unverified": []}
    assert deals[1]["kind"] == "共通支配下の取引"
    assert deals[1]["consideration"] is None
    assert len(prompts) == 2
    assert "ダンボールワン" not in prompts[1].split("注記：")[1]


def 金額の値がnullという文字なら空にする() -> None:
    details_with_null_text = DETAILS | {"proforma_sales": {"value": "null", "unit": "円"}}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        is_listing = "deals" in body["format"]["properties"]
        content = (
            {"deals": [_listed("株式会社アドウィル")]} if is_listing else details_with_null_text
        )
        message = {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}
        return httpx.Response(200, json={"message": message})

    client = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://ollama")

    assert extract_deals(client, NOTE, "qwen3:8b")[0]["proforma_sales"] is None


def LLMがJSONでない応答を返せば止める() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "すみません"}})

    client = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://ollama")

    with pytest.raises(json.JSONDecodeError):
        extract_deals(client, NOTE, "qwen3:8b")


def _notes_file(path: Path, doc_id: str, notes: list[dict[str, str]]) -> None:
    saved = {
        "docID": doc_id,
        "edinetCode": "E00001",
        "secCode": "10000",
        "filerName": "テスト株式会社",
        "periodEnd": "2025-03-31",
        "notes": notes,
    }
    (path / f"{doc_id}.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")


CONSOLIDATED = "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock"
NON_CONSOLIDATED = "jpcrp_cor:NotesBusinessCombinationsFinancialStatementsTextBlock"


def 連結の注記から案件を抜き出して保存する(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    notes = [
        {"element": CONSOLIDATED, "context": "CurrentYearDuration", "text": NOTE},
        {"element": NON_CONSOLIDATED, "context": "CurrentYearDuration", "text": NOTE + "個別"},
    ]
    _notes_file(tmp_path / "notes", "S1", notes)
    prompts: list[str] = []

    save_deals(
        _ollama([_listed("株式会社アドウィル")], prompts),
        tmp_path / "notes",
        tmp_path / "deals",
        "qwen3:8b",
    )

    saved = json.loads((tmp_path / "deals" / "S1.json").read_text(encoding="utf-8"))
    assert saved["filerName"] == "テスト株式会社"
    assert saved["deals"] == [_deal() | {"unverified": [], "element": CONSOLIDATED}]
    assert len(prompts) == 2


def 中身のある注記が無い有報はLLMを呼ばずに空で保存する(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    boilerplate = "（企業結合等関係）該当事項はありません。"
    _notes_file(
        tmp_path / "notes",
        "S1",
        [{"element": CONSOLIDATED, "context": "CurrentYearDuration", "text": boilerplate}],
    )
    prompts: list[str] = []

    save_deals(_ollama([], prompts), tmp_path / "notes", tmp_path / "deals", "qwen3:8b")

    saved = json.loads((tmp_path / "deals" / "S1.json").read_text(encoding="utf-8"))
    assert saved["deals"] == []
    assert prompts == []


def 抜き出しに失敗した有報はエラーを記録して次に進む(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    note = {"element": CONSOLIDATED, "context": "CurrentYearDuration", "text": NOTE}
    _notes_file(tmp_path / "notes", "S1", [note])
    _notes_file(tmp_path / "notes", "S2", [note])
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append("call")
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "すみません"}})

    client = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://ollama")

    save_deals(client, tmp_path / "notes", tmp_path / "deals", "qwen3:8b")

    for doc_id in ("S1", "S2"):
        saved = json.loads((tmp_path / "deals" / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert saved["deals"] == []
        assert "JSONDecodeError" in saved["error"]
