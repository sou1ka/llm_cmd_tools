#!/usr/bin/env python3
"""rag-server の定期ジョブ: journal と systemd をルールで集計し、異常を話題にする。"""
import json
import re
import subprocess
import sys
from collections import Counter, namedtuple
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CURSOR_FILE = "cursor.txt"
IGNORE_FILE = "ignore.txt"
KIND = "system"
MAX_BODY = 200
PRIORITY = {"unit": 2, "io": 3, "oom": 2, "err": 0}

# category: "io" / "oom" / "err"、key: io はデバイス名（不明は "不明"）、oom はプロセス名、err はプログラム名
Finding = namedtuple("Finding", "category key")


def log(message):
    print(message, file=sys.stderr)


def read_cursor(path):
    """カーソル文字列。ファイルが無い・空なら None。"""
    path = Path(path)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_cursor(path, cursor):
    Path(path).write_text(cursor, encoding="utf-8")


def load_ignore(path):
    """1行1正規表現のリスト（コンパイル済み）。空行と # 行は無視。ファイルが無ければ []。"""
    path = Path(path)
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [re.compile(line) for line in lines if line and not line.startswith("#")]


def journal_args(cursor):
    """journalctl の引数リスト。cursor が None なら直近24時間。"""
    base = ["journalctl", "-o", "json", "--no-pager"]
    if cursor:
        return base + ["--after-cursor", cursor]
    return base + ["--since", "24 hours ago"]


def parse_entries(lines):
    """journalctl -o json の各行を dict にして返すジェネレータ。不正な行は読み飛ばす。"""
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        # 表示できないバイトを含む MESSAGE は数値の配列で出力される
        if isinstance(entry.get("MESSAGE"), list):
            entry["MESSAGE"] = bytes(entry["MESSAGE"]).decode("utf-8", errors="replace")
        yield entry


IO_DEV = (re.compile(r"dev ([\w-]+)"), re.compile(r"\b(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|mmcblk\d+|hd[a-z]+)"))
KERNEL_OOM = re.compile(r"Out of memory: Killed process \d+ \((.+?)\)")
SYSTEMD_OOMD = re.compile(r"Killed (\S+) due to")


def classify(entry, ignore):
    """1件を Finding にする。対象外なら None。"""
    message = entry.get("MESSAGE") or ""
    ident = entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or entry.get("_SYSTEMD_UNIT") or "unknown"
    kernel = entry.get("_TRANSPORT") == "kernel"
    if kernel and re.search("I/O error", message, re.IGNORECASE):
        for pattern in IO_DEV:
            if m := pattern.search(message):
                return Finding("io", m.group(1))
        return Finding("io", "不明")
    if kernel and (m := KERNEL_OOM.search(message)):
        return Finding("oom", m.group(1))
    if ident == "systemd-oomd" and (m := SYSTEMD_OOMD.search(message)):
        return Finding("oom", m.group(1).rstrip("/").rsplit("/", 1)[-1])
    try:
        priority = int(entry.get("PRIORITY"))
    except (TypeError, ValueError):
        return None
    if priority <= 3 and not any(p.search(f"{ident}: {message}") for p in ignore):
        return Finding("err", ident)
    return None


def parse_failed_units(text):
    """`systemctl list-units --state=failed --plain --no-legend` の出力 → ユニット名のリスト。"""
    units = []
    for line in text.splitlines():
        tokens = line.replace("●", " ").lstrip(" *").split()
        if tokens:
            units.append(tokens[0])
    return units


def _fit(head, items, tail, other_unit):
    """body が MAX_BODY に収まるまで items を末尾から減らし「ほかM{other_unit}」を付ける。"""
    for keep in range(len(items), -1, -1):
        rest = len(items) - keep
        parts = items[:keep] + ([f"ほか{rest}{other_unit}"] if rest else [])
        body = head + "、".join(parts) + tail
        if len(body) <= MAX_BODY:
            return body
    return body


def _ranked(keys):
    """(キー, 件数) を件数の多い順に。同数は最初に出た順（Counter の挿入順を安定ソートで保つ）。"""
    return sorted(Counter(keys).items(), key=lambda kv: -kv[1])


def make_topics(findings, failed_units, day):
    """Finding のリストと失敗ユニットから話題 dict のリストを作る。day は実行日の date。"""
    by_cat = {cat: [f.key for f in findings if f.category == cat] for cat in ("io", "oom", "err")}
    topics = []

    def add(ref, body, priority):
        topics.append({"kind": KIND, "ref": f"{ref}:{day.isoformat()}", "body": body, "priority": priority})

    if io := by_cat["io"]:
        items = [f"{k} {n}件" for k, n in _ranked(io)]
        add("log_io", _fit(f"ディスクのI/Oエラーが計{len(io)}件（", items, "）。", "台"), PRIORITY["io"])
    if oom := by_cat["oom"]:
        if len(oom) == 1:
            body = f"メモリ不足で{oom[0]}が強制終了された。"
        else:
            names = [k for k, _ in _ranked(oom)]
            body = _fit(f"メモリ不足でプロセスが計{len(oom)}回強制終了された（", names, "）。", "件")
        add("log_oom", body, PRIORITY["oom"])
    for unit in failed_units:
        add(f"unit_failed:{unit}", f"{unit}が停止（failed）状態。", PRIORITY["unit"])
    if err := by_cat["err"]:
        ranked = _ranked(err)
        items = [f"{k} {n}件" for k, n in ranked]
        add("log_err", _fit(f"エラーログは{len(ranked)}種類・計{len(err)}件。", items, "。", "種類"), PRIORITY["err"])
    return topics


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cursor_path = SCRIPT_DIR / CURSOR_FILE
    try:
        ignore = load_ignore(SCRIPT_DIR / IGNORE_FILE)
    except re.error as e:
        log(f"invalid pattern in {IGNORE_FILE}: {e}")
        sys.exit(1)
    cursor = read_cursor(cursor_path)
    failed = False

    journal = _run(journal_args(cursor))
    if journal.returncode != 0 and cursor:
        # ローテーション等でカーソルが無効になった可能性。直近24時間で読み直す
        log(f"journalctl failed with cursor, retry last 24h: {journal.stderr.strip()}")
        journal = _run(journal_args(None))
    findings, last_cursor = [], None
    if journal.returncode == 0:
        for entry in parse_entries(journal.stdout.splitlines()):
            last_cursor = entry.get("__CURSOR", last_cursor)
            if finding := classify(entry, ignore):
                findings.append(finding)
    else:
        log(f"journalctl failed: {journal.stderr.strip()}")
        failed = True

    units_proc = _run(["systemctl", "list-units", "--state=failed", "--plain", "--no-legend"])
    units = []
    if units_proc.returncode == 0:
        units = parse_failed_units(units_proc.stdout)
    else:
        log(f"systemctl failed: {units_proc.stderr.strip()}")
        failed = True

    log(f"findings={len(findings)} failed_units={units}")
    for topic in make_topics(findings, units, date.today()):
        print(json.dumps(topic, ensure_ascii=False))
    sys.stdout.flush()
    # 出力し終えてから進める。途中で落ちたら次回同じ範囲を読み直す
    if journal.returncode == 0 and last_cursor:
        write_cursor(cursor_path, last_cursor)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
