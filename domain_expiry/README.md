# domain_expiry

rag-server の定期ジョブ（`[[server.job]]`）。指定したドメインの登録期限を WHOIS で調べ、期限が近いものごとに話題 JSON を標準出力へ1行ずつ出す。

- 見るのはドメインの登録期限。TLS 証明書の期限ではない（それは `cert_expiry`）
- WHOIS（TCP 43）を `socket` で直接問い合わせる。外部コマンド・ライブラリは不要
- LLM は使わない。文面はテンプレート

## 動作環境

- Linux (Fedora)
- Python 3（標準ライブラリのみ）
- TCP 43 番で外部に接続できること

## 設置

1. ディレクトリごと配置する（例: `/opt/agent/jobs/domain_expiry/`）
2. `.env.example` を `.env` にコピーして編集する

   ```sh
   cp .env.example .env
   ```

3. rag-server の `config.toml` の `[server.topics]` の `kinds` に `"system"` が必要（`disk_health` と共通）

同名の環境変数があれば `.env` より優先される。空文字の環境変数は未設定扱い。

## 設定項目

| 項目 | 必須 | 説明 |
| --- | --- | --- |
| `DOMAIN_NAMES` | 必須 | 確認するドメイン（カンマ区切り） |
| `DOMAIN_WARN_DAYS` | 任意（既定 60） | 警告にする残り日数 |
| `DOMAIN_CRIT_DAYS` | 任意（既定 14） | 危険にする残り日数 |
| `DOMAIN_TIMEOUT` | 任意（既定 30） | WHOIS サーバーへの接続・応答のタイムアウト（秒） |

- `DOMAIN_NAMES` は小文字に直し、前後の空白と末尾の `.` を除く。空要素と重複は無視する。`.` を含まない要素はエラー
- `*_DAYS` は 0 以上の整数。`DOMAIN_WARN_DAYS` が `DOMAIN_CRIT_DAYS` より小さいとエラー
- `DOMAIN_TIMEOUT` は 0 より大きい数

```
DOMAIN_NAMES=example.net,example.jp,example.co.jp
```

## 問い合わせの流れ

1. `whois.iana.org` に TLD を聞き、返った `refer:` または `whois:` 行から WHOIS サーバーを決める。同じ TLD は1回の実行で1回だけ
2. そのサーバーにドメインを問い合わせ、応答から期限を読む。`.jp` で終わるドメインは `/e` を付けて英語表示で問い合わせる

RDAP は `.jp` が未対応のため使っていない。

## 読める期限の書式

応答のうち最初に見つかった次のいずれかの行から日付（日単位）を読む。

| 書式 | 主な対象 |
| --- | --- |
| `Registry Expiry Date: 2027-04-26T10:47:13Z` | gTLD（`.com` / `.net` 等） |
| `Registrar Registration Expiration Date: 2027-04-26T10:47:13Z` | gTLD |
| `[Expires on]  2027/04/30` | `.jp` |
| `[State]  Connected (2027/01/31)` | 属性型 JP（`.co.jp` 等） |

- 行頭の空白は許す。ISO 形式は日付部分（`YYYY-MM-DD`）だけ使う
- 日付として不正（`2027-02-30` など）なら、期限が読めない扱い

## 判定

| 段階 | 条件 | priority |
| --- | --- | --- |
| 危険（crit） | 残り日数 <= `DOMAIN_CRIT_DAYS`（期限切れを含む） | 3 |
| 警告（warn） | 残り日数 <= `DOMAIN_WARN_DAYS` | 1 |

- 残り日数は日付単位（期限日 − 今日）。期限を過ぎていれば負
- 問い合わせの失敗、WHOIS サーバーが分からない、期限が読めないドメインは話題にしない。stderr に理由を出し、全ドメインを確認した後に exit 1

## 出力

話題 JSON を1行ずつ標準出力に出す（ログは stderr）。`kind` は `"system"`。

| 状況 | body 例 |
| --- | --- |
| 残り1日以上 | example.netのドメインの期限まであと45日（4月26日）。 |
| 期限が今日 | example.netのドメインの期限は今日（4月26日）。 |
| 期限切れ | example.netのドメインが期限切れ。 |

```json
{"kind":"system","ref":"domain:example.net:warn:2027-03-12","body":"example.netのドメインの期限まであと45日（4月26日）。","priority":1,"expires_at":"2027-03-12T23:59:59+09:00"}
```

- `ref` は `domain:{ドメイン}:{warn|crit}:{日付}`。状態が続く間は1日1回話題になる。警告から危険に上がった日は、もう一度話題になる
- `expires_at` はその日の 23:59:59（ローカルタイムゾーン）

## 終了コード

| 状況 | stdout | 終了コード |
| --- | --- | --- |
| 該当なし | 空 | 0 |
| 期限が近い・期限切れがある | 話題の行 | 0 |
| 問い合わせ失敗・サーバー不明・期限が読めないドメインがある | 他のドメインの出力のみ | 1 |
| `DOMAIN_NAMES` 未設定、設定値が不正 | 空 | 1 |

該当のドメインはスキップして stderr に理由を出し、他は続行して最後に exit 1 にする。出力済みの行は取り込まれる。

## 注意

- WHOIS は問い合わせ回数に制限がある。実行は1日1回で十分
- ドメイン1件につき最大2回（IANA と WHOIS サーバー）接続する。ドメインが多いときは `timeout` を延ばす
- `ref` は日付単位なので、同じ日に手動実行した後の cron 実行の話題は重複として弾かれる

## rag-server への登録（config.toml）

`/path/to` は環境に合わせて置き換える。

```toml
[[server.job]]
name    = "domain_expiry"
cron    = "0 0 9 * * *"          # 毎朝9時（秒から始まる6フィールド）
cmd     = ["python3", "/path/to/domain_expiry/domain_expiry.py"]
timeout = 180.0
```

## 単体での確認

```sh
python3 domain_expiry.py; echo $?
```

標準エラーに `ドメイン: expires 日付 days=N level=...` が出る。

警告の閾値を一時的に大きくすると話題が出る。

```sh
DOMAIN_WARN_DAYS=9999 python3 domain_expiry.py
```

出力が JSON として正しいか検証する。

```sh
python3 domain_expiry.py 2>/dev/null | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]'
```

## テスト

`domain_expiry` ディレクトリで実行する。

```sh
python -m unittest discover -s tests
```

## ファイル構成

| ファイル | 説明 |
| --- | --- |
| `domain_expiry.py` | ジョブ本体 |
| `.env.example` | 設定の例。`.env` にコピーして使う |
| `tests/` | 単体テスト（`helpers.py`、`test_domain_expiry.py`、`test_main_cli.py`） |
