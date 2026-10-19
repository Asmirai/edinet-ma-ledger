from datetime import date

import pytest

from ledger.features import deal_features, legal_form_kind, recent_deals, voting_number


def _row(**changes: str) -> dict[str, str]:
    row = {
        "deal_id": "1",
        "edinetCode": "E1",
        "industry": "情報・通信業",
        "base_year": "2020",
        "success": "True",
        "delta_roa": "0.01",
        "excess_delta_roa": "0.02",
        "matched_excess_delta_roa": "0.015",
        "excess_roa_before": "-0.01",
        "size_ratio": "0.1",
        "consideration_yen": "1000",
        "goodwill_yen": "400",
    }
    return row | changes


def 同業かとのれんの割合と買収の大きさを出す() -> None:
    features = deal_features(_row(), "情報・通信業")

    assert features["same_industry"] is True
    assert features["goodwill_ratio"] == pytest.approx(0.4)
    assert features["size_ratio"] == pytest.approx(0.1)
    assert features["success"] is True
    assert features["matched_success"] is True
    assert features["excess_delta_roa"] == pytest.approx(0.02)


def 買った会社の業種が33業種に無ければ同業かを空にする() -> None:
    row = _row(industry="内国法人・組合（有価証券報告書等の提出義務者以外）")

    assert deal_features(row, "情報・通信業")["same_industry"] is None


def のれんの割合が1点5を超える案件は単位の読み違いとみなして空にする() -> None:
    assert deal_features(_row(goodwill_yen="1500"), "卸売業")["goodwill_ratio"] == 1.5
    assert deal_features(_row(goodwill_yen="1501"), "卸売業")["goodwill_ratio"] is None


def 分からない値は空にする() -> None:
    row = _row(goodwill_yen="", size_ratio="", matched_excess_delta_roa="", consideration_yen="0")

    features = deal_features(row, None)

    assert features["same_industry"] is None
    assert features["goodwill_ratio"] is None
    assert features["size_ratio"] is None
    assert features["matched_excess_delta_roa"] is None
    assert features["matched_success"] is None


def のれんが0ならのれんの割合を0にし書かれていなければ空にする() -> None:
    features = deal_features(_row(goodwill_yen=""), "卸売業")

    assert features["goodwill_ratio"] is None
    assert deal_features(_row(goodwill_yen="0"), "卸売業")["goodwill_ratio"] == 0


def 法的形式を5つの種類にまとめる() -> None:
    assert legal_form_kind("現金を対価とする株式取得") == "株式取得"
    assert legal_form_kind("現金を対価とする持分の取得") == "株式取得"
    assert legal_form_kind("現金を対価とする事業譲受") == "事業譲受"
    assert legal_form_kind("吸収分割") == "事業譲受"
    assert legal_form_kind("当社を存続会社とする吸収合併") == "合併"
    assert legal_form_kind("株式交換") == "株式交換"
    assert legal_form_kind("第三者割当増資の引受け") == "その他"
    assert legal_form_kind("") == "不明"


def 議決権比率の数字を読む() -> None:
    assert voting_number("100％") == 100
    assert voting_number("５１.０％") == 51
    assert voting_number("取得前 20％ 取得後 60％") == 60
    assert voting_number("100％（うち間接保有 24.49%）") == 100
    assert voting_number("不二サッシ株式会社 97.0％、日海不二サッシ株式会社 3.0％") == 97
    assert voting_number("") is None


def 直前2年の買収を数え台帳の始まりから2年たっていなければ空にする() -> None:
    dates = [date(2018, 1, 1), date(2019, 6, 1), date(2020, 3, 1), date(2020, 5, 1)]
    start = date(2016, 10, 1)

    assert recent_deals(dates, date(2020, 5, 1), start) == 2
    assert recent_deals(dates, date(2018, 6, 1), start) is None
