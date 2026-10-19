"""公開用の台帳（ma_ledger.csv）の決まり：置き場所、列、値の種類、成否を付けられる最後の年。

書き出す ledger.export と、読む ledger.scorer・ledger.figures が使う。データ取得の層は import しない。
"""

from pathlib import Path
from typing import Literal, get_args

PUBLIC_PATH = Path("data/public/ma_ledger.csv")
# 成否（のれんの減損・ROA）と、買収の目的・業種の関係を付けるのは、買収後3期の有報がそろうこの年まで
LAST_BASE_YEAR = 2022
# のれんは取得原価から受け入れた純資産を引いた額なので、債務超過の会社を買えば取得原価を超える。
# それでも 1.5 倍を超えるものは、取得原価の単位（千円・百万円）の読み違いが多かった
MAX_GOODWILL_RATIO = 1.5

Relatedness = Literal["同業種", "関連業種", "異業種"]
RELATEDNESS: tuple[Relatedness, ...] = get_args(Relatedness)
Purpose = Literal[
    "同じ事業の拡大",
    "新しい事業への参入",
    "地域の拡大・海外進出",
    "仕入れ・販売の取り込み",
    "技術・人材の獲得",
    "再建・救済",
    "その他",
]
PURPOSES: tuple[Purpose, ...] = get_args(Purpose)

COLUMNS = (
    "deal_id",
    "primary",
    "acquirer_edinet_code",
    "acquirer_sec_code",
    "acquirer_name",
    "acquirer_industry",
    "acquiree_name",
    "acquiree_business",
    "acquisition_date",
    "legal_form",
    "voting_ratio",
    "consideration_yen",
    "goodwill_yen",
    "assets_total_yen",
    "size_ratio",
    "acquirer_equity_ratio",
    "purpose",
    "relatedness",
    "base_year",
    "delta_roa",
    "roa_beat_peers",
    "goodwill_impaired",
    "goodwill_impaired_note",
    "source_doc_ids",
)
