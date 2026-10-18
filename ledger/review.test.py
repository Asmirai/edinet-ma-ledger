import json
from pathlib import Path

import pytest

from ledger.review import sample_notes, score_reviews


def _deal(name: str, kind: str = "取得") -> dict[str, object]:
    return {
        "acquiree_name": name,
        "kind": kind,
        "period": "当期",
        "business": "広告",
        "acquisition_date": "2024年５月８日",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
        "consideration": {"value": "1,410", "unit": "百万円"},
        "goodwill": None,
        "assets_total": None,
        "proforma_sales": None,
    }


def _write_year(year_dir: Path, count: int) -> None:
    (year_dir / "in").mkdir(parents=True)
    (year_dir / "out").mkdir()
    inputs = [
        {"docID": f"S{i}", "filerName": f"会社{i}", "element": "E", "text": f"原文{i}"}
        for i in range(count)
    ]
    results = [
        {"docID": f"S{i}", "element": "E", "deals": [_deal(f"買われた{i}"), _deal("子", "その他")]}
        for i in range(count)
    ]
    lines = "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in inputs)
    (year_dir / "in" / "chunk_000.jsonl").write_text(lines, encoding="utf-8")
    (year_dir / "out" / "chunk_000.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8"
    )


def _read_items(review_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for path in sorted((review_dir / "in").glob("chunk_*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def 年ごとに決まった数の注記を無作為に選び10件ずつのファイルに分ける(tmp_path: Path) -> None:
    _write_year(tmp_path / "deals" / "y2017", 30)
    _write_year(tmp_path / "deals" / "y2018", 30)

    sample_notes(tmp_path / "deals", 6, 1, tmp_path / "review")

    items = _read_items(tmp_path / "review")
    assert len(items) == 12
    assert len(list((tmp_path / "review" / "in").glob("chunk_*.jsonl"))) == 2
    assert (tmp_path / "review" / "out").is_dir()
    assert len({item["docID"] for item in items[:6]}) == 6


def 選んだ注記には原文と取得の案件だけを付ける(tmp_path: Path) -> None:
    _write_year(tmp_path / "deals" / "y2017", 1)

    sample_notes(tmp_path / "deals", 1, 1, tmp_path / "review")

    [item] = _read_items(tmp_path / "review")
    assert item == {
        "docID": "S0",
        "filerName": "会社0",
        "element": "E",
        "text": "原文0",
        "extracted": [
            {
                "acquiree_name": "買われた0",
                "acquisition_date": "2024年５月８日",
                "voting_ratio": "100％",
                "consideration": {"value": "1,410", "unit": "百万円"},
                "goodwill": None,
                "assets_total": None,
            }
        ],
    }


def 同じ種で選べば同じ注記になる(tmp_path: Path) -> None:
    _write_year(tmp_path / "deals" / "y2017", 30)

    sample_notes(tmp_path / "deals", 5, 7, tmp_path / "first")
    sample_notes(tmp_path / "deals", 5, 7, tmp_path / "second")

    assert _read_items(tmp_path / "first") == _read_items(tmp_path / "second")


def 前に選んだ注記がある所には選ばない(tmp_path: Path) -> None:
    _write_year(tmp_path / "deals" / "y2017", 3)
    sample_notes(tmp_path / "deals", 1, 1, tmp_path / "review")

    with pytest.raises(FileExistsError):
        sample_notes(tmp_path / "deals", 1, 1, tmp_path / "review")


def _verdicts(**changes: str) -> dict[str, dict[str, str]]:
    fields = ("acquisition_date", "voting_ratio", "consideration", "goodwill", "assets_total")
    verdicts = {field: {"verdict": "正", "correct": "x"} for field in fields}
    for field, verdict in changes.items():
        verdicts[field] = {"verdict": verdict, "correct": "x"}
    return verdicts


def 見落とし誤った取得と項目ごとの誤りを数える(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    (review_dir / "in").mkdir(parents=True)
    (review_dir / "out").mkdir()
    source: dict[str, object] = {
        "docID": "S1",
        "filerName": "会社",
        "element": "E",
        "text": "原文",
        "extracted": [],
    }
    (review_dir / "in" / "chunk_000.jsonl").write_text(
        json.dumps(source, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    acquisitions = [
        {"name": "A社", "found": True, "fields": _verdicts()},
        {"name": "B社", "found": True, "fields": _verdicts(goodwill="誤", consideration="誤")},
        {"name": "C社", "found": False, "fields": _verdicts()},
    ]
    result = {
        "docID": "S1",
        "element": "E",
        "acquisitions": acquisitions,
        "false_acquisitions": ["D社"],
    }
    (review_dir / "out" / "chunk_000.json").write_text(
        json.dumps([result], ensure_ascii=False), encoding="utf-8"
    )

    scores = score_reviews(review_dir)

    assert scores == {
        "notes": 1,
        "acquisitions": 3,
        "missed": 1,
        "false_acquisitions": 1,
        "field_errors": {
            "acquisition_date": 0,
            "voting_ratio": 0,
            "consideration": 1,
            "goodwill": 1,
            "assets_total": 0,
        },
    }
