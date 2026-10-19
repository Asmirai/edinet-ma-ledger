# CI は GitHub のサーバーで動かし、自前のランナーを使わない

- issue：#34
- PR：#37

## 決めたこと

CI（ruff・basedpyright・pytest）は、GitHub のサーバー（`ubuntu-latest`）で動かす。組織の自前のランナー（Mac Studio）は使わない。

## なぜ

| 決めたこと | なぜ |
|---|---|
| 自前のランナーを使わない | このリポジトリは公開している。公開リポジトリで自前のランナーを使うと、外部の人が出した PR のコードが手元の機械で動く。GitHub の文書（[Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)）も、誰でも PR を出せるため、公開リポジトリでは自前のランナーをほぼ使うべきでないとしている |
| 権限はリポジトリの読み取り（`contents: read`）だけにする | PR のコードが、リポジトリに書き込めないようにする |
| CI では `data/` を使わない | テストは EDINET の書類を使わないので、API キーを CI の secrets に置かずに済む |

## 経緯

組織には自前のランナー（Mac Studio）がある。このリポジトリには記事から外部の人が来て PR を出せるので、#37 で CI を入れるときに、自前のランナーではなく GitHub のサーバーを選んだ。
