# garbage_day

rag-server の定期ジョブ（`[[server.job]]`）。前日夕方に実行し、翌日（ローカル日付）のゴミ収集を `calendar.toml` から求めて、該当があれば話題 JSON を標準出力へ1行出す。

- LLM は使わない。文面はテンプレート
- 祝日の自動判定はしない（必要なら `exception` で手書きする）

## 動作環境

- Linux (Fedora)
- Python 3.11 以上（標準ライブラリの `tomllib` を使用）
- 標準ライブラリのみ

## 設置

1. ディレクトリごと配置する（例: `/opt/agent/jobs/garbage_day/`）
2. `calendar.toml.example` を同じディレクトリに `calendar.toml` としてコピーして編集する

   ```sh
   cp calendar.toml.example calendar.toml
   ```

3. rag-server の `config.toml` の `[server.topics]` の `kinds` に `"life"` を追加する（ユーザー作業。`life` は家の予定全般用の kind。下記参照）

`.env` や環境変数による設定は無い。`calendar.toml` は必ずスクリプトと同じディレクトリに置く（作業ディレクトリ基準ではない）。

## calendar.toml の書式

```toml
deadline = "08:00"

[[rule]]
name     = "燃やすごみ"
weekdays = ["Tue", "Fri"]          # 毎週

[[rule]]
name     = "小型金属類"
weekdays = ["Thu"]
nth      = [2]                     # 第2木曜

[[exception]]
date = "2026-12-31"
skip = ["燃やすごみ"]               # この日は収集しない

[[exception]]
date = "2027-01-04"
add  = ["燃やすごみ"]               # この日に収集する
```

| 項目 | 必須 | 説明 |
| --- | --- | --- |
| `deadline` | 必須 | 何時までに出すか。`"HH:MM"` |
| `[[rule]]` `name` | 必須 | ゴミの種類名 |
| `[[rule]]` `weekdays` | 必須 | 収集する曜日のリスト。`Mon` `Tue` `Wed` `Thu` `Fri` `Sat` `Sun`（大文字小文字を区別する） |
| `[[rule]]` `nth` | 任意 | 月の第N回目のその曜日（1〜5 のリスト）。省略すると毎週 |
| `[[exception]]` `date` | 必須 | 対象日。TOML の日付リテラルまたは `"YYYY-MM-DD"` |
| `[[exception]]` `skip` | 任意 | その日は収集しない種類名のリスト |
| `[[exception]]` `add` | 任意 | その日に追加で収集する種類名のリスト |

- 同名の `rule` を複数書いてよい
- 同じ `date` の `exception` は複数書けない
- `skip` / `add` に `rule` に無い名前は書けない
- 書式が不正なら出力なしで exit 1

## 判定

1. `rule` を記述順に見て、その日の曜日（と `nth`）に合うものの名前を集める
2. その日の `exception` があれば、`skip` の名前を除き、`add` の名前を末尾に追加する
3. 重複を除く

## 出力

話題 JSON を1行標準出力に出す（ログは stderr）。

```json
{"kind":"life","ref":"garbage:2026-11-14","body":"11月14日(土)は燃やすごみと資源ごみの日。朝8時までに出す。","priority":1,"expires_at":"2026-11-14T08:00:00+09:00"}
```

- 種類が3つ以上のときは「A、BとC」の形にする
- 時刻は分が 0 なら「8時」、それ以外は「8時30分」。正午より前は「朝」を付ける
- `expires_at` は収集日の `deadline`（ローカルタイムゾーン）
- `ref` は収集日なので、同じ日の話題は1回だけになる

## 終了コード

| 状況 | stdout | 終了コード |
| --- | --- | --- |
| `calendar.toml` が無い・不正 | 空 | 1 |
| 翌日に収集なし | 空 | 0 |

## rag-server への登録（config.toml）

`/path/to` は環境に合わせて置き換える。

```toml
[server.topics]
kinds = ["news", "system", "life"]   # 既存の kinds に "life" を追加

[[server.job]]
name    = "garbage_day"
cron    = "0 0 18 * * *"             # 毎日18時（秒から始まる6フィールド）
cmd     = ["python3", "/path/to/garbage_day/garbage_day.py"]
timeout = 30.0
```

## 単体での確認

```sh
python3 garbage_day.py; echo $?
```

明日が収集日なら1行出る。

出力が JSON として正しいか検証する。

```sh
python3 garbage_day.py 2>/dev/null | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]'
```

## テスト

`garbage_day` ディレクトリで実行する。

```sh
python3 -m unittest discover -s tests
```
