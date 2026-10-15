"""EDINET API v2 の書類一覧 API と書類取得 API（仕様書 Version 2 の 3-1、3-2）。"""

from collections.abc import Mapping
from datetime import date
from typing import Any, cast

import httpx

DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
# 書類取得 API の type。XBRL を CSV に変換したものの ZIP
CSV_ZIP = "5"
ANNUAL_REPORT = "120"
# 企業内容等の開示に関する内閣府令。特定有価証券（投資信託などのファンド）は "030"
CORPORATE_ORDINANCE = "010"


class EdinetError(Exception):
    pass


def fetch_document_list(client: httpx.Client, day: date, api_key: str) -> list[dict[str, Any]]:
    """その日に提出・登録された書類の一覧を返す。"""
    response = client.get(
        DOCUMENTS_URL,
        params={"date": day.isoformat(), "type": "2", "Subscription-Key": api_key},
    )
    # メンテナンス中は JSON ではなくエラー画面が返る
    response.raise_for_status()
    body = cast(dict[str, Any], response.json())
    _raise_if_error(body)
    return cast(list[dict[str, Any]], body.get("results", []))


def fetch_document_csv_zip(client: httpx.Client, doc_id: str, api_key: str) -> bytes:
    """書類の XBRL を CSV に変換したものを、ZIP のまま返す。"""
    response = client.get(
        DOCUMENT_URL.format(doc_id=doc_id), params={"type": CSV_ZIP, "Subscription-Key": api_key}
    )
    response.raise_for_status()
    # 書類取得 API はエラーでも HTTP では 200 で返る。成否は Content-Type で見分ける（3-3）
    if response.headers.get("Content-Type", "").startswith("application/octet-stream"):
        return response.content
    body = cast(dict[str, Any], response.json())
    _raise_if_error(body)
    raise EdinetError(f"ZIP でもエラーでもない応答：{response.headers.get('Content-Type')}")


def _raise_if_error(body: dict[str, Any]) -> None:
    """パラメータの誤りなどは HTTP では 200 で返り、状態は中身に入る。401 と 429 だけ形が違う（3-3）。

    書類一覧と書類取得の2つの API で、同じエラーの形を同じに読むために関数にした。
    """
    if "StatusCode" in body:
        raise EdinetError(f"{body['StatusCode']} {body['message']}")
    metadata = cast(dict[str, Any], body["metadata"])
    if metadata["status"] != "200":
        raise EdinetError(f"{metadata['status']} {metadata['message']}")


def is_annual_report(document: Mapping[str, object]) -> bool:
    """今も取得できる、会社の有価証券報告書か。

    一覧には、書類のほかに「書類情報を修正した」「不開示にした」という通知の行も混ざる（3-1-6、3-1-7）。
    """
    return (
        document["ordinanceCode"] == CORPORATE_ORDINANCE
        and document["docTypeCode"] == ANNUAL_REPORT
        and document["withdrawalStatus"] == "0"
        and document["docInfoEditStatus"] != "1"
        and document["disclosureStatus"] == "0"
        and document["legalStatus"] in ("1", "2")
    )
