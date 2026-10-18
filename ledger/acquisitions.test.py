import json
from datetime import date
from pathlib import Path

from ledger.acquisitions import load_records, merge_acquisitions, normalize_name, parse_date


def 会社の種類と空白と記号を除いて名前をそろえる() -> None:
    assert normalize_name("株式会社　アドウィル") == "アドウィル"
    assert normalize_name("㈱アドウィル") == "アドウィル"
    assert normalize_name("アドウィル（株）") == "アドウィル"
    assert normalize_name("Ｃ＆Ａ Tool Engineering, Inc.") == "c&atoolengineeringinc"


def 名前の後ろの今の名前や前の名前を除く() -> None:
    assert normalize_name("株式会社新生銀行（現株式会社ＳＢＩ新生銀行）") == "新生銀行"
    assert normalize_name("高瀬アルミ株式会社(現：株式会社AQR)") == "高瀬アルミ"
    assert normalize_name("㈱SKIYAKI APPS（旧㈱SKIYAKI OFFLINE）") == "skiyakiapps"


def 西暦と和暦の日付を読む() -> None:
    assert parse_date("2024年５月８日") == date(2024, 5, 8)
    assert parse_date("平成29年12月15日") == date(2017, 12, 15)
    assert parse_date("令和元年10月１日") == date(2019, 10, 1)


def 日付が2つあれば初めの日付を読む() -> None:
    text = "2021年10月26日（株式取得日）2021年12月31日（みなし取得日）"

    assert parse_date(text) == date(2021, 10, 26)


def 数字の前後に空白のある日付も読む() -> None:
    assert parse_date("2020年 2 月28日（持分取得日）") == date(2020, 2, 28)


def 日付の書かれていないものは読まない() -> None:
    assert parse_date(None) is None
    assert parse_date("平成30月１月19日") is None


def _record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "docID": "S1",
        "edinetCode": "E00001",
        "secCode": "10000",
        "filerName": "テスト株式会社",
        "periodEnd": "2024-03-31",
        "acquiree_name": "株式会社アドウィル",
        "kind": "取得",
        "period": "当期",
        "business": "広告",
        "acquisition_date": "2023年５月８日",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
        "consideration": {"value": "1,410", "unit": "百万円"},
        "goodwill": {"value": "121", "unit": "百万円"},
        "assets_total": {"value": "2,000", "unit": "百万円"},
        "proforma_sales": None,
    }
    return record | changes


def 一つだけの案件は金額を円に直して1行にする() -> None:
    rows = merge_acquisitions([_record()])

    assert rows == [
        {
            "deal_id": 1,
            "edinetCode": "E00001",
            "secCode": "10000",
            "filerName": "テスト株式会社",
            "docIDs": "S1",
            "acquiree_name": "株式会社アドウィル",
            "acquisition_date": "2023-05-08",
            "business": "広告",
            "legal_form": "株式取得",
            "voting_ratio": "100％",
            "consideration_yen": 1_410_000_000,
            "goodwill_yen": 121_000_000,
            "assets_total_yen": 2_000_000_000,
        }
    ]


def 翌年の有報に載った同じ案件は1行にし後の有報の値を使う() -> None:
    first = _record()
    later = _record(
        docID="S2",
        periodEnd="2025-03-31",
        period="前期",
        acquiree_name="㈱アドウィル",
        goodwill={"value": "130", "unit": "百万円"},
        assets_total=None,
    )

    [row] = merge_acquisitions([later, first])

    assert row["docIDs"] == "S1 S2"
    assert row["acquiree_name"] == "㈱アドウィル"
    assert row["goodwill_yen"] == 130_000_000
    assert row["assets_total_yen"] == 2_000_000_000


def 同じ会社から同じ名前の会社を1年より空けて買えば別の案件にする() -> None:
    later = _record(docID="S2", periodEnd="2026-03-31", acquisition_date="2025年６月１日")

    rows = merge_acquisitions([_record(), later])

    assert [row["deal_id"] for row in rows] == [1, 2]


def 日付の読めない記録は同じ会社の同じ名前の案件にまとめる() -> None:
    later = _record(docID="S2", periodEnd="2025-03-31", acquisition_date=None)

    [row] = merge_acquisitions([_record(), later])

    assert row["acquisition_date"] == "2023-05-08"


def 同じ名前の案件が2つある会社では日付の読めない記録をどちらにもまとめない() -> None:
    second = _record(docID="S2", periodEnd="2026-03-31", acquisition_date="2025年６月１日")
    undated = _record(docID="S3", periodEnd="2027-03-31", acquisition_date=None)

    rows = merge_acquisitions([_record(), second, undated])

    assert [row["docIDs"] for row in rows] == ["S3", "S1", "S2"]


def 親会社と子会社が同じ日に同じ会社を買えば同じ案件の番号を付ける() -> None:
    parent = _record(docID="S9", edinetCode="E00009", filerName="親株式会社")

    rows = merge_acquisitions([_record(), parent])

    assert [(row["filerName"], row["deal_id"]) for row in rows] == [
        ("テスト株式会社", 1),
        ("親株式会社", 1),
    ]


def 名前で案件を特定できない記録は除く() -> None:
    records = [
        _record(acquiree_name=None),
        _record(acquiree_name="当社"),
        _record(acquiree_name="その他の企業結合"),
    ]

    assert merge_acquisitions(records) == []


def 抜き出した取得の案件に有報の会社の情報を付けて読む(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    report: dict[str, object] = {
        "docID": "S1",
        "edinetCode": "E00001",
        "secCode": "10000",
        "filerName": "テスト株式会社",
        "periodEnd": "2024-03-31",
        "notes": [],
    }
    (notes_dir / "S1.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    year_dir = tmp_path / "deals" / "y2024"
    (year_dir / "in").mkdir(parents=True)
    (year_dir / "out").mkdir()
    source = {"docID": "S1", "filerName": "テスト株式会社", "element": "E", "text": "原文"}
    (year_dir / "in" / "chunk_000.jsonl").write_text(
        json.dumps(source, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    deal = {key: value for key, value in _record().items() if key not in report}
    other = deal | {"acquiree_name": "グループ会社", "kind": "共通支配下の取引"}
    result = [{"docID": "S1", "element": "E", "deals": [deal, other]}]
    (year_dir / "out" / "chunk_000.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )

    assert load_records(tmp_path / "deals", notes_dir) == [_record()]
