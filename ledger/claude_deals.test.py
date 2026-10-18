import json
from datetime import date
from pathlib import Path

import pytest

from ledger.claude_deals import check_outputs, split_notes

CONSOLIDATED = "jpcrp_cor:NotesBusinessCombinationsConsolidatedFinancialStatementsTextBlock"
NOTE = (
    "（企業結合等関係）取得による企業結合（１）企業結合の概要① 被取得企業の名称及びその事業の内容"
    "被取得企業の名称：株式会社アドウィル事業の内容：コミュニケーションデザインサービス"
    "③ 企業結合日2024年５月８日④ 企業結合の法的形式株式取得⑥ 取得した議決権比率100％"
    "取得原価 1,410 百万円 発生したのれんの金額１２１百万円"
)
BOILERPLATE = "（企業結合等関係）該当事項はありません。"


def _document(doc_id: str, doc_type: str = "120", csv_flag: str = "1") -> dict[str, str]:
    return {
        "docID": doc_id,
        "ordinanceCode": "010",
        "docTypeCode": doc_type,
        "withdrawalStatus": "0",
        "docInfoEditStatus": "0",
        "disclosureStatus": "0",
        "legalStatus": "1",
        "csvFlag": csv_flag,
    }


def _write_list(lists_dir: Path, day: str, documents: list[dict[str, str]]) -> None:
    lists_dir.mkdir(parents=True, exist_ok=True)
    (lists_dir / f"{day}.json").write_text(json.dumps(documents), encoding="utf-8")


def _write_notes(notes_dir: Path, doc_id: str, texts: list[str]) -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes = [{"element": CONSOLIDATED, "context": "CurrentYearDuration", "text": t} for t in texts]
    saved = {"docID": doc_id, "filerName": f"{doc_id}株式会社", "notes": notes}
    (notes_dir / f"{doc_id}.json").write_text(
        json.dumps(saved, ensure_ascii=False), encoding="utf-8"
    )


def _read_chunk(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def 期間内の有報の中身のある注記を30件ずつのファイルに分ける(tmp_path: Path) -> None:
    doc_ids = [f"S{i:03d}" for i in range(31)]
    _write_list(tmp_path / "lists", "2024-04-01", [_document(i) for i in doc_ids])
    for doc_id in doc_ids:
        _write_notes(tmp_path / "notes", doc_id, [NOTE + doc_id])

    counts = split_notes(
        tmp_path / "lists", tmp_path / "notes", date(2024, 4, 1), date(2024, 4, 30), tmp_path / "y"
    )

    assert counts == (31, 31)
    first = _read_chunk(tmp_path / "y" / "in" / "chunk_000.jsonl")
    assert len(first) == 30
    assert first[0] == {
        "docID": "S000",
        "filerName": "S000株式会社",
        "element": CONSOLIDATED,
        "text": NOTE + "S000",
    }
    assert [n["docID"] for n in _read_chunk(tmp_path / "y" / "in" / "chunk_001.jsonl")] == ["S030"]
    assert (tmp_path / "y" / "out").is_dir()


def 期間外の日と有報でない書類とCSVの無い書類と定型文は入れない(tmp_path: Path) -> None:
    _write_list(tmp_path / "lists", "2024-03-31", [_document("BEFORE")])
    documents = [
        _document("KEEP"),
        _document("QUARTER", doc_type="140"),
        _document("NOCSV", csv_flag="0"),
        _document("EMPTY"),
    ]
    _write_list(tmp_path / "lists", "2024-04-01", documents)
    _write_list(tmp_path / "lists", "2024-05-01", [_document("AFTER")])
    for doc_id in ("BEFORE", "KEEP", "QUARTER", "NOCSV", "AFTER"):
        _write_notes(tmp_path / "notes", doc_id, [NOTE])
    _write_notes(tmp_path / "notes", "EMPTY", [BOILERPLATE])

    counts = split_notes(
        tmp_path / "lists", tmp_path / "notes", date(2024, 4, 1), date(2024, 4, 30), tmp_path / "y"
    )

    assert counts == (2, 1)
    assert [n["docID"] for n in _read_chunk(tmp_path / "y" / "in" / "chunk_000.jsonl")] == ["KEEP"]


def 注記を取っていない有報があれば止める(tmp_path: Path) -> None:
    _write_list(tmp_path / "lists", "2024-04-01", [_document("S1")])
    (tmp_path / "notes").mkdir()

    with pytest.raises(FileNotFoundError):
        split_notes(
            tmp_path / "lists",
            tmp_path / "notes",
            date(2024, 4, 1),
            date(2024, 4, 30),
            tmp_path / "y",
        )


def _deal(name: str, kind: str = "取得", **changes: object) -> dict[str, object]:
    details: dict[str, object] = {
        "business": "コミュニケーションデザインサービス",
        "acquisition_date": "2024年５月８日",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
        "consideration": {"value": "1,410", "unit": "百万円"},
        "goodwill": {"value": "１２１", "unit": "百万円"},
        "assets_total": None,
        "proforma_sales": None,
    }
    if kind != "取得":
        details = {field: None for field in details}
    return {"acquiree_name": name, "kind": kind, "period": "当期"} | details | changes


def _write_chunk(
    year_dir: Path, inputs: list[dict[str, str]], results: list[dict[str, object]]
) -> None:
    (year_dir / "in").mkdir(parents=True, exist_ok=True)
    (year_dir / "out").mkdir(exist_ok=True)
    lines = "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in inputs)
    (year_dir / "in" / "chunk_000.jsonl").write_text(lines, encoding="utf-8")
    (year_dir / "out" / "chunk_000.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8"
    )


def _result(doc_id: str, deals: list[dict[str, object]]) -> dict[str, object]:
    return {"docID": doc_id, "element": CONSOLIDATED, "deals": deals}


def _input(doc_id: str, text: str) -> dict[str, str]:
    return {"docID": doc_id, "filerName": "テスト株式会社", "element": CONSOLIDATED, "text": text}


def 案件を種類ごとに数え原文に無い項目のある取得を返す(tmp_path: Path) -> None:
    inputs = [_input("S1", NOTE + "株式会社ダンボールワンを吸収合併"), _input("S2", BOILERPLATE)]
    deals = [
        _deal("株式会社アドウィル"),
        _deal("株式会社アドウィル", period="前期", goodwill={"value": "450", "unit": "百万円"}),
        _deal("株式会社ダンボールワン", kind="共通支配下の取引"),
    ]
    results = [
        _result("S1", deals),
        _result("S2", []),
    ]
    _write_chunk(tmp_path, inputs, results)

    kinds, mismatches = check_outputs(tmp_path)

    assert kinds == {"取得": 2, "共通支配下の取引": 1}
    assert mismatches == [("テスト株式会社", "株式会社アドウィル", ["goodwill"])]


def 原文の文字参照を戻した表記でも照合する(tmp_path: Path) -> None:
    text = NOTE.replace("株式会社アドウィル", "AT&amp;T株式会社")
    results = [_result("S1", [_deal("AT&T株式会社")])]
    _write_chunk(tmp_path, [_input("S1", text)], results)

    assert check_outputs(tmp_path)[1] == []


def 出力の書類の並びが入力と違えば止める(tmp_path: Path) -> None:
    inputs = [_input("S1", NOTE), _input("S2", NOTE)]
    results = [
        _result("S2", []),
        _result("S1", []),
    ]
    _write_chunk(tmp_path, inputs, results)

    with pytest.raises(ValueError, match="chunk_000"):
        check_outputs(tmp_path)


def 出力の無い入力があれば止める(tmp_path: Path) -> None:
    _write_chunk(tmp_path, [_input("S1", NOTE)], [])
    (tmp_path / "out" / "chunk_000.json").unlink()

    with pytest.raises(FileNotFoundError):
        check_outputs(tmp_path)


def 前に分けた入力がある所には分けない(tmp_path: Path) -> None:
    _write_list(tmp_path / "lists", "2024-04-01", [_document("S1")])
    _write_notes(tmp_path / "notes", "S1", [NOTE])
    (tmp_path / "y" / "in").mkdir(parents=True)
    (tmp_path / "y" / "in" / "chunk_000.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(FileExistsError):
        split_notes(
            tmp_path / "lists",
            tmp_path / "notes",
            date(2024, 4, 1),
            date(2024, 4, 30),
            tmp_path / "y",
        )


def 入力が1つも無ければ止める(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        check_outputs(tmp_path)


def 同じ書類の中で注記の並びが入力と違えば止める(tmp_path: Path) -> None:
    separate = "jpcrp_cor:NotesBusinessCombinationsFinancialStatementsTextBlock"
    inputs = [_input("S1", NOTE), _input("S1", NOTE + "個別") | {"element": separate}]
    results = [_result("S1", []) | {"element": separate}, _result("S1", [])]
    _write_chunk(tmp_path, inputs, results)

    with pytest.raises(ValueError, match="chunk_000"):
        check_outputs(tmp_path)
