# log_scan

rag-server の定期ジョブ（`[[server.job]]`）。systemd の失敗ユニットと journal（前回の続きから）をルールで集計し、異常があれば話題 JSON を標準出力へ出す。

- LLM は使わない。文面はテンプレート
- 将来のトリアージ（LLM で解説を付ける）は未対応

## 動作環境

- Linux (Fedora)
- Python 3、標準ライブラリのみ
- `journalctl` と `systemctl`
- 実行ユーザーがシステム全体の journal を読めること（`systemd-journal` / `wheel` / `adm` グループ等）。確認方法: 同じユーザーで `journalctl -p err` を実行し、他ユーザー・システムのログが表示されるか見る

## 設置

1. ディレクトリごと配置する（例: `/opt/agent/jobs/log_scan/`）
2. （任意）`ignore.txt.example` を同じディレクトリに `ignore.txt` としてコピーして編集する。無ければ除外なし

   ```sh
   cp ignore.txt.example ignore.txt
   ```

3. rag-server の `config.toml` の `[server.topics]` の `kinds` に `"system"` が必要（`disk_health` と共通）

`.env` や環境変数による設定は無い。

## ファイル

| ファイル | 説明 |
| --- | --- |
| `cursor.txt` | 前回読んだ journal の位置。スクリプトの横に自動作成される |
| `ignore.txt` | エラーログの除外パターン（任意） |

## 読み取り範囲

- `cursor.txt` があればその続きから、無ければ直近24時間
- 保存済みカーソルが無効で `journalctl` が失敗したら、直近24時間で1回読み直す
- カーソルは話題を出力した後に更新する。journal 取得失敗時、エントリ0件のときは更新しない
- `ignore.txt` の調整中に同じ範囲で試し直すには `cursor.txt` を消す

## 判定と出力

話題 JSON を1行ずつ標準出力に出す（ログは stderr）。`kind` は `"system"`。

| 種類 | 判定 | body 例 | ref | priority |
| --- | --- | --- | --- | --- |
| I/O エラー | カーネルのログ | ディスクのI/Oエラーが計5件（sdb 4件、sdc 1件）。 | `log_io:日付` | 3 |
| OOM | カーネルの Out of memory と systemd-oomd | 1件: メモリ不足でfirefoxが強制終了された。 / 複数: メモリ不足でプロセスが計3回強制終了された（firefox、chrome）。 | `log_oom:日付` | 2 |
| 失敗ユニット | `systemctl list-units --state=failed`（実行時点の状態） | nginx.serviceが停止（failed）状態。 | `unit_failed:ユニット:日付` | 2 |
| エラーログ | PRIORITY 3 以下で、上記と ignore に当たらないもの。プログラム名ごとに集計 | エラーログは3種類・計12件。gdm 2件、wireplumber 1件、ほか1種類。 | `log_err:日付` | 0 |

- 出力順は I/O、OOM、失敗ユニット、エラーログ
- 件数の多い順に並べ、200文字を超える分は「ほか◯種類/台/件」にまとめる
- `expires_at` は付けない（rag-server の既定期限）
- `priority` は大きいほど重要（抽選の重みは `priority+1`）

## ignore.txt の書式

- 1行1正規表現。空行と `#` 行は無視
- 「プログラム名: 本文」に対して `re.search` で照合する
- エラーログの集計にだけ適用する。I/O エラーと OOM には適用しない
- 不正な正規表現なら出力なしで exit 1

```
# エラーログの集計から除外するパターン（1行1正規表現）。ignore.txt にコピーして編集する
# 「プログラム名: 本文」に対して検索する（re.search）。I/O エラーと OOM には適用しない
^dbus-broker-launch: Ignoring duplicate name
^gdm-password\]?: gkr-pam: unable to locate daemon control file
^gdm: GLib: Source ID \d+ was not found
WARNING: convert_pos\(\) in njd2jpcommon\.c
```

## 終了コード

| 状況 | 終了コード |
| --- | --- |
| `journalctl` または `systemctl` が失敗 | 1（もう一方から作れた話題は出力され取り込まれる） |
| `ignore.txt` が不正 | 1（出力なし） |
| それ以外（該当なしでも） | 0 |

## 注意

`ref` は日付単位なので、同じ日に手動実行した後の cron 実行の話題は重複として弾かれる。

## rag-server への登録（config.toml）

`/path/to` は環境に合わせて置き換える。

```toml
[[server.job]]
name    = "log_scan"
cron    = "0 0 7 * * *"          # 毎朝7時（秒から始まる6フィールド）
cmd     = ["python3", "/path/to/log_scan/log_scan.py"]
timeout = 120.0
```

## 単体での確認

```sh
python3 log_scan.py; echo $?
```

標準エラーに `findings=N failed_units=[...]` が出る。

出力が JSON として正しいか検証する。

```sh
python3 log_scan.py 2>/dev/null | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]'
```

## テスト

`log_scan` ディレクトリで実行する。`journalctl` と `systemctl` は不要（subprocess をモックしている）。

```sh
python3 -m unittest discover -s tests
```
