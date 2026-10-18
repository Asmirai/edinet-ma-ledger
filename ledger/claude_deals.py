"""Claude Code のサブエージェントで注記から買収案件を抜き出すための、入力の用意と結果の照合。

抜き出しそのものはサブエージェントが行う。指示は claude_deals_instructions.md にある。

使い方：
  uv run python -m ledger.claude_deals split 2023-10-01 2024-09-30 data/claude_deals/y2024
  （in/chunk_*.jsonl を1つずつサブエージェントに渡し、out/chunk_*.json に書かせる）
  uv run python -m ledger.claude_deals check data/claude_deals/y2024
"""

import html
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, cast

from ledger.deals import ACQUISITION, notes_to_read, unverified_fields
from ledger.document_lists import LISTS_DIR
from ledger.edinet import is_annual_report
from ledger.notes import NOTES_DIR

CHUNK_SIZE = 30


def split_notes(
    lists_dir: Path, notes_dir: Path, start: date, end: date, year_dir: Path
) -> tuple[int, int]:
    """期間内に出た有報の、中身のある注記を30件ずつ year_dir/in/chunk_NNN.jsonl に書く。

    返すのは有報の件数と注記の件数。注記を取っていない有報があれば止める（数え漏れを防ぐため）。
    前に分けた入力があれば止める。上書きすると、前の回の末尾の入力と出力が残って照合に混ざる。
    """
    if any((year_dir / "in").glob("chunk_*.jsonl")):
        raise FileExistsError(f"{year_dir / 'in'} に前に分けた入力がある")
    doc_ids = [
        document["docID"]
        for path in sorted(lists_dir.glob("*.json"))
        if start <= date.fromisoformat(path.stem) <= end
        for document in cast(list[dict[str, Any]], json.loads(path.read_text(encoding="utf-8")))
        if is_annual_report(document) and document["csvFlag"] == "1"
    ]
    items: list[dict[str, str]] = []
    for doc_id in doc_ids:
        report = cast(
            dict[str, Any], json.loads((notes_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        )
        for note in notes_to_read(cast(list[dict[str, str]], report["notes"])):
            item = {"docID": doc_id, "filerName": report["filerName"]}
            items.append(item | {"element": note["element"], "text": note["text"]})
    (year_dir / "in").mkdir(parents=True, exist_ok=True)
    (year_dir / "out").mkdir(exist_ok=True)
    for start_index in range(0, len(items), CHUNK_SIZE):
        chunk = items[start_index : start_index + CHUNK_SIZE]
        lines = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunk)
        path = year_dir / "in" / f"chunk_{start_index // CHUNK_SIZE:03d}.jsonl"
        path.write_text(lines, encoding="utf-8")
    return len(doc_ids), len(items)


def check_outputs(year_dir: Path) -> tuple[Counter[str], list[tuple[str, str, list[str]]]]:
    """サブエージェントの出力を入力の原文と照らし合わせる。

    返すのは種類ごとの案件数と、取得のうち原文に載っていない項目があるものの（会社名、案件名、項目）。
    入力が1つも無ければ止める（ディレクトリの指定違いを、0 件として通さないため）。
    原文には「&amp;」のような文字参照が残っている。戻す前と戻した後のどちらかに載っていれば、載っているとみなす。
    """
    kinds: Counter[str] = Counter()
    mismatches: list[tuple[str, str, list[str]]] = []
    in_paths = sorted((year_dir / "in").glob("chunk_*.jsonl"))
    if not in_paths:
        raise FileNotFoundError(f"{year_dir / 'in'} に入力が無い")
    for in_path in in_paths:
        out_path = year_dir / "out" / f"{in_path.stem}.json"
        inputs = [
            cast(dict[str, str], json.loads(line))
            for line in in_path.read_text(encoding="utf-8").splitlines()
        ]
        results = cast(list[dict[str, Any]], json.loads(out_path.read_text(encoding="utf-8")))
        # 1つの有報から、連結と個別など複数の注記が入ることがある
        output_order = [(r["docID"], r["element"]) for r in results]
        if output_order != [(i["docID"], i["element"]) for i in inputs]:
            raise ValueError(f"{out_path.name} の注記の並びが入力と違う")
        for source, result in zip(inputs, results):
            for deal in cast(list[dict[str, Any]], result["deals"]):
                kinds[deal["kind"]] += 1
                if deal["kind"] != ACQUISITION:
                    continue
                unescaped = unverified_fields(deal, html.unescape(source["text"]))
                missing = [f for f in unverified_fields(deal, source["text"]) if f in unescaped]
                if missing:
                    mismatches.append((source["filerName"], deal["acquiree_name"], missing))
    return kinds, mismatches


def main() -> None:
    command, *args = sys.argv[1:]
    if command == "split":
        start, end, year_dir = date.fromisoformat(args[0]), date.fromisoformat(args[1]), args[2]
        reports, notes = split_notes(LISTS_DIR, NOTES_DIR, start, end, Path(year_dir))
        print(f"{start}〜{end}：有報 {reports} 件、中身のある注記 {notes} 件")
        return
    if command != "check":
        raise SystemExit(f"知らないコマンド：{command}（split か check）")
    kinds, mismatches = check_outputs(Path(args[0]))
    acquisitions = kinds[ACQUISITION]
    print(
        f"案件の種類：{dict(kinds)}。取得のうち全項目を照合できたもの：{acquisitions - len(mismatches)}/{acquisitions} 件"
    )
    for filer_name, acquiree_name, fields in mismatches:
        print(f"{filer_name}　{acquiree_name}　{', '.join(fields)}")


if __name__ == "__main__":
    main()
