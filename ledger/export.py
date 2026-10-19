"""台帳と分析で付けた値を、公開用の CSV（data/public/ma_ledger.csv）に書く。

注記の文章そのものは入れない。出典と加工した者は、CSV に添える説明（docs/ma_ledger_csv.md）に書く。

使い方：uv run python -m ledger.export
"""

import csv

from ledger.dataset import PURPOSES_DIR, acquirer_profiles
from ledger.impairments import IMPAIRMENTS_PATH
from ledger.public import COLUMNS, PUBLIC_PATH, Purpose, Relatedness
from ledger.purposes import read_purposes
from ledger.roa import LABELED_PATH


def public_rows(
    ledger_rows: list[dict[str, str]],
    impairments: dict[str, tuple[str, str]],
    purposes: dict[str, tuple[Purpose, Relatedness]],
    profiles: dict[str, dict[str, float | None]],
) -> list[dict[str, str]]:
    """台帳の行に、のれんの減損（と判定できなかった理由）、目的、業種の関係、買い手の自己資本比率を付け、
    公開用の列にする。分からない値は空。
    """
    rows: list[dict[str, str]] = []
    for row in ledger_rows:
        key = f"{row['deal_id']}-{row['edinetCode']}"
        impaired, note = impairments.get(key, ("", ""))
        purpose, relatedness = purposes.get(key, ("", ""))
        equity_ratio = profiles.get(key, {}).get("equity_ratio")
        matched = row["matched_excess_delta_roa"]
        rows.append(
            {
                "deal_id": row["deal_id"],
                "primary": row["primary"],
                "acquirer_edinet_code": row["edinetCode"],
                "acquirer_sec_code": row["secCode"],
                "acquirer_name": row["filerName"],
                "acquirer_industry": row["industry"],
                "acquiree_name": row["acquiree_name"],
                "acquiree_business": row["business"],
                "acquisition_date": row["acquisition_date"],
                "legal_form": row["legal_form"],
                "voting_ratio": row["voting_ratio"],
                "consideration_yen": row["consideration_yen"],
                "goodwill_yen": row["goodwill_yen"],
                "assets_total_yen": row["assets_total_yen"],
                "size_ratio": row["size_ratio"],
                "acquirer_equity_ratio": "" if equity_ratio is None else str(equity_ratio),
                "purpose": purpose,
                "relatedness": relatedness,
                "base_year": row["base_year"],
                "delta_roa": row["delta_roa"],
                "roa_beat_peers": "" if not matched else str(float(matched) > 0),
                "goodwill_impaired": impaired,
                "goodwill_impaired_note": note,
                "source_doc_ids": row["docIDs"],
            }
        )
    return rows


def main() -> None:
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        ledger_rows = list(csv.DictReader(file))
    with IMPAIRMENTS_PATH.open(encoding="utf-8-sig", newline="") as file:
        impairments = {r["key"]: (r["impaired"], r["reason"]) for r in csv.DictReader(file)}
    rows = public_rows(
        ledger_rows, impairments, read_purposes(PURPOSES_DIR), acquirer_profiles(ledger_rows)
    )
    PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Excel で開いても文字化けしないように BOM を付ける
    with PUBLIC_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        fieldnames: list[str] = list(COLUMNS)
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} 行：{PUBLIC_PATH}")


if __name__ == "__main__":
    main()
