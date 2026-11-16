#!/usr/bin/env python3
"""rag-server の定期ジョブ: ドメインの登録期限が近いものを話題にする（WHOIS を直接問い合わせる）。"""
import json
import os
import re
import socket
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_KEYS = ("DOMAIN_NAMES", "DOMAIN_WARN_DAYS", "DOMAIN_CRIT_DAYS", "DOMAIN_TIMEOUT")
KIND = "system"
PRIORITY = {"warn": 1, "crit": 3}
DEFAULTS = {"DOMAIN_WARN_DAYS": "60", "DOMAIN_CRIT_DAYS": "14", "DOMAIN_TIMEOUT": "30"}
IANA_SERVER = "whois.iana.org"
WHOIS_PORT = 43
SERVER_LINE = re.compile(r"^(?:refer|whois):[ \t]*(\S+)", re.IGNORECASE | re.MULTILINE)
EXPIRY_LINE = re.compile(
    r"^(?:[ \t]*(?:Registry Expiry Date|Registrar Registration Expiration Date):[ \t]*(\d{4}-\d{2}-\d{2})"
    r"|\[Expires on\][ \t]*(\d{4}/\d{2}/\d{2})"
    r"|\[State\][ \t]*Connected \((\d{4}/\d{2}/\d{2})\))",
    re.MULTILINE,
)


def log(message):
    print(message, file=sys.stderr)


def load_config(env_path):
    """.env を読み、同名の環境変数（空文字は未設定扱い）で上書きした dict を返す。"""
    config = {}
    env_path = Path(env_path)
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    for key in CONFIG_KEYS:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def parse_domains(text):
    """カンマ区切り → 小文字化したドメイン名のリスト。前後空白・末尾の "." 除去、空要素・重複を除く（順序維持）。
    "." を含まない要素は ValueError。"""
    domains = []
    for item in text.split(","):
        domain = item.strip().rstrip(".").lower()
        if not domain:
            continue
        if "." not in domain:
            raise ValueError(f"invalid domain: {item.strip()}")
        domains.append(domain)
    return list(dict.fromkeys(domains))


def parse_settings(config):
    """既定値を補って (warn_days int, crit_days int, timeout float) を返す。
    数値でない・負・timeout が 0 以下・warn < crit なら ValueError。"""
    warn, crit = (int(config.get(k) or DEFAULTS[k]) for k in ("DOMAIN_WARN_DAYS", "DOMAIN_CRIT_DAYS"))
    timeout = float(config.get("DOMAIN_TIMEOUT") or DEFAULTS["DOMAIN_TIMEOUT"])
    if crit < 0 or warn < crit:
        raise ValueError(f"invalid days: warn={warn} crit={crit}")
    if not timeout > 0:  # nan も弾く
        raise ValueError(f"DOMAIN_TIMEOUT must be > 0: {timeout}")
    return warn, crit, timeout


def whois_query(server, query, timeout):
    """server の 43 番に接続して query + CRLF を送り、切断まで読んだ応答を str で返す（UTF-8、不正バイトは置換）。
    接続・送受信の失敗は OSError をそのまま送出する。"""
    with socket.create_connection((server, WHOIS_PORT), timeout=timeout) as sock:
        sock.sendall(f"{query}\r\n".encode("utf-8"))
        chunks = []
        while chunk := sock.recv(4096):
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def parse_server(text):
    """IANA の応答から WHOIS サーバー名を返す。`refer:` または `whois:` 行（行頭、大文字小文字を区別しない）の値。
    無ければ None。"""
    m = SERVER_LINE.search(text)
    return m.group(1) if m else None


def query_string(domain):
    """WHOIS サーバーに送る文字列。.jp で終わるドメインは英語表示用に "/e" を付ける。"""
    return f"{domain}/e" if domain.endswith(".jp") else domain


def parse_expiry(text):
    """WHOIS の応答から期限日（date）を返す。見つからなければ None。最初に見つかった行を使う。
    - `Registry Expiry Date: 2027-04-26T10:47:13Z`（行頭の空白可。日付部分 YYYY-MM-DD だけ使う）
    - `Registrar Registration Expiration Date: 2027-04-26T10:47:13Z`（同上）
    - `[Expires on]  2027/04/30`（.jp）
    - `[State]  Connected (2027/01/31)`（属性型 JP）
    日付として不正（2027-02-30 など）なら None。"""
    m = EXPIRY_LINE.search(text)
    if not m:
        return None
    try:
        return date(*map(int, re.split(r"[-/]", m.group(m.lastindex))))
    except ValueError:
        return None


def level(days, warn_days, crit_days):
    """None / "warn" / "crit"。days <= crit_days で crit、days <= warn_days で warn。"""
    if days <= crit_days:
        return "crit"
    if days <= warn_days:
        return "warn"
    return None


def expiry_body(domain, days, expiry):
    """本文。
    days > 0: 「{domain}のドメインの期限まであと{days}日（4月26日）。」
    days == 0: 「{domain}のドメインの期限は今日（4月26日）。」
    days < 0: 「{domain}のドメインが期限切れ。」"""
    if days < 0:
        return f"{domain}のドメインが期限切れ。"
    day = f"{expiry.month}月{expiry.day}日"
    if days == 0:
        return f"{domain}のドメインの期限は今日（{day}）。"
    return f"{domain}のドメインの期限まであと{days}日（{day}）。"


def make_topic(domain, lvl, body, now):
    """話題の dict。ref は domain:{domain}:{lvl}:{now の日付}、expires_at は now の日の 23:59:59。"""
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return {
        "kind": KIND,
        "ref": f"domain:{domain}:{lvl}:{now.date().isoformat()}",
        "body": body,
        "priority": PRIORITY[lvl],
        "expires_at": end_of_day.isoformat(),
    }


def main():
    """.env を読み、ドメインごとに IANA で WHOIS サーバーを調べ（同じ TLD は1回の実行で1回だけ）、
    期限を問い合わせて話題を1行1 JSON で出す。残り日数は (期限日 - 今日の日付).days。
    DOMAIN_NAMES が空・設定値が不正なら exit 1。
    問い合わせの失敗（OSError）・サーバーが分からない・期限が読めないドメインは話題にせずログを出し、
    全ドメインを確認した後に exit 1。"""
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(SCRIPT_DIR / ".env")
    try:
        domains = parse_domains(config.get("DOMAIN_NAMES", ""))
        warn_days, crit_days, timeout = parse_settings(config)
    except ValueError as e:
        log(f"invalid config: {e}")
        sys.exit(1)
    if not domains:
        log("DOMAIN_NAMES is not set")
        sys.exit(1)
    now = datetime.now().astimezone()

    servers = {}  # TLD → WHOIS サーバー
    failed = False
    for domain in domains:
        tld = domain.rsplit(".", 1)[-1]
        try:
            if tld not in servers:
                servers[tld] = parse_server(whois_query(IANA_SERVER, tld, timeout))
            if not servers[tld]:
                raise ValueError(f"no whois server for .{tld}")
            expiry = parse_expiry(whois_query(servers[tld], query_string(domain), timeout))
            if not expiry:
                raise ValueError("expiry date not found")
        except (OSError, ValueError) as e:
            log(f"skip {domain}: {e!r}")
            failed = True
            continue
        days = (expiry - now.date()).days
        lvl = level(days, warn_days, crit_days)
        log(f"{domain}: expires {expiry} days={days} level={lvl}")
        if lvl:
            topic = make_topic(domain, lvl, expiry_body(domain, days, expiry), now)
            print(json.dumps(topic, ensure_ascii=False), flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
