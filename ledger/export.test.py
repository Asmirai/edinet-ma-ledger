from ledger.export import COLUMNS, public_rows


def _row(**changes: str) -> dict[str, str]:
    row = {
        "deal_id": "1",
        "edinetCode": "E1",
        "secCode": "10000",
        "filerName": "買い手株式会社",
        "docIDs": "S1 S2",
        "acquiree_name": "株式会社アドウィル",
        "acquisition_date": "2021-05-08",
        "business": "広告",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
        "consideration_yen": "1410000000",
        "goodwill_yen": "121000000",
        "assets_total_yen": "",
        "base_year": "2022",
        "delta_roa": "0.01",
        "matched_excess_delta_roa": "0.002",
        "size_ratio": "0.12",
        "industry": "サービス業",
        "unlabeled_reason": "",
        "primary": "True",
        "roa_before": "0.05",
    }
    return row | changes


def 台帳の事実と分析で付けた値を公開用の列にする() -> None:
    rows = public_rows(
        [_row()],
        {"1-E1": ("False", "")},
        {"1-E1": ("同じ事業の拡大", "同じ事業")},
    )

    assert list(rows[0]) == list(COLUMNS)
    assert rows[0]["acquirer_name"] == "買い手株式会社"
    assert rows[0]["source_doc_ids"] == "S1 S2"
    assert rows[0]["goodwill_impaired"] == "False"
    assert rows[0]["roa_beat_peers"] == "True"
    assert rows[0]["purpose"] == "同じ事業の拡大"


def 主の行でない行も残し分からない値は空にする() -> None:
    rows = public_rows([_row(primary="False", matched_excess_delta_roa="")], {}, {})

    assert rows[0]["primary"] == "False"
    assert rows[0]["roa_beat_peers"] == ""
    assert rows[0]["goodwill_impaired"] == ""
    assert rows[0]["goodwill_impaired_note"] == ""
    assert rows[0]["purpose"] == ""


def 減損を判定できなかった理由を写す() -> None:
    rows = public_rows([_row()], {"1-E1": ("", "事業譲受")}, {})

    assert rows[0]["goodwill_impaired"] == ""
    assert rows[0]["goodwill_impaired_note"] == "事業譲受"
