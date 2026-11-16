#!/usr/bin/env python3
"""rag-server の定期ジョブ: TLS 証明書の期限が近いホストを話題にする。"""
import json
import math
import os
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_KEYS = ("CERT_HOSTS", "CERT_WARN_DAYS", "CERT_CRIT_DAYS", "CERT_TIMEOUT")
KIND = "system"
PRIORITY = {"warn": 1, "crit": 3}
DEFAULTS = {"CERT_WARN_DAYS": "28", "CERT_CRIT_DAYS": "7", "CERT_TIMEOUT": "10"}
DEFAULT_PORT = 443
EXPIRED_CODE = 10  # X509_V_ERR_CERT_HAS_EXPIRED


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


def parse_hosts(text):
    """`a.com,b.com:993` → [("a.com", 443), ("b.com", 993)]。
    前後空白除去、空要素・重複を除く（順序維持）。ポートが 1-65535 の整数でなければ ValueError。"""
    hosts = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        host, sep, port = item.rpartition(":")
        if not sep:
            host, port = item, str(DEFAULT_PORT)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError(f"invalid port: {item}")
        hosts.append((host.strip(), int(port)))
    return list(dict.fromkeys(hosts))


def parse_settings(config):
    """既定値を補って (warn_days int, crit_days int, timeout float) を返す。
    数値でない・負・timeout が 0 以下・warn < crit なら ValueError。"""
    warn, crit = (int(config.get(k) or DEFAULTS[k]) for k in ("CERT_WARN_DAYS", "CERT_CRIT_DAYS"))
    timeout = float(config.get("CERT_TIMEOUT") or DEFAULTS["CERT_TIMEOUT"])
    if crit < 0 or warn < crit:
        raise ValueError(f"invalid days: warn={warn} crit={crit}")
    if not timeout > 0:  # nan も弾く
        raise ValueError(f"CERT_TIMEOUT must be > 0: {timeout}")
    return warn, crit, timeout


def display_name(host, port):
    """443 なら host、それ以外は "host:port"。"""
    return host if port == DEFAULT_PORT else f"{host}:{port}"


def fetch_expiry(host, port, timeout):
    """検証ありで TLS 接続し、証明書の notAfter を UTC の aware datetime で返す。
    検証の失敗は ssl.SSLCertVerificationError、接続の失敗は OSError をそのまま送出する。"""
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)


def days_left(expiry, now):
    """残り日数（切り捨て）。期限を過ぎていれば負。expiry・now はタイムゾーン付き。"""
    return math.floor((expiry - now).total_seconds() / 86400)


def level(days, warn_days, crit_days):
    """None / "warn" / "crit"。days <= crit_days で crit、days <= warn_days で warn。"""
    if days <= crit_days:
        return "crit"
    if days <= warn_days:
        return "warn"
    return None


def expiry_body(name, days, expiry, tz):
    """期限の話題の本文。日付は tz に変換して表示する。
    days > 0: 「{name}の証明書の期限まであと{days}日（11月24日）。」
    days == 0: 「{name}の証明書の期限は今日（11月24日）。」
    days < 0: 「{name}の証明書が期限切れ。」"""
    if days < 0:
        return f"{name}の証明書が期限切れ。"
    local = expiry.astimezone(tz)
    day = f"{local.month}月{local.day}日"
    if days == 0:
        return f"{name}の証明書の期限は今日（{day}）。"
    return f"{name}の証明書の期限まであと{days}日（{day}）。"


def verify_error_body(name, error):
    """検証の失敗の本文。error は ssl.SSLCertVerificationError。
    verify_code 10（期限切れ）: 「{name}の証明書が期限切れ。」
    それ以外: 「{name}の証明書の検証に失敗（{error.verify_message}）。」"""
    if error.verify_code == EXPIRED_CODE:
        return f"{name}の証明書が期限切れ。"
    return f"{name}の証明書の検証に失敗（{error.verify_message}）。"


def make_topic(name, lvl, body, now):
    """話題の dict。ref は cert:{name}:{lvl}:{now の日付}、expires_at は now の日の 23:59:59。"""
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return {
        "kind": KIND,
        "ref": f"cert:{name}:{lvl}:{now.date().isoformat()}",
        "body": body,
        "priority": PRIORITY[lvl],
        "expires_at": end_of_day.isoformat(),
    }


def main():
    """.env を読み、ホストを順に確認して話題を1行1 JSON で出す。
    CERT_HOSTS が空・設定値が不正なら exit 1。検証の失敗は crit の話題にする。
    接続の失敗は話題にせずログを出し、全ホストを確認した後に exit 1。"""
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(SCRIPT_DIR / ".env")
    try:
        hosts = parse_hosts(config.get("CERT_HOSTS", ""))
        warn_days, crit_days, timeout = parse_settings(config)
    except ValueError as e:
        log(f"invalid config: {e}")
        sys.exit(1)
    if not hosts:
        log("CERT_HOSTS is not set")
        sys.exit(1)
    now = datetime.now().astimezone()

    failed = False
    for host, port in hosts:
        name = display_name(host, port)
        try:
            expiry = fetch_expiry(host, port, timeout)
        except ssl.SSLCertVerificationError as e:
            log(f"{name}: verify failed: {e!r}")
            topic = make_topic(name, "crit", verify_error_body(name, e), now)
        except OSError as e:
            log(f"skip {name}: {e!r}")
            failed = True
            continue
        else:
            days = days_left(expiry, now)
            lvl = level(days, warn_days, crit_days)
            log(f"{name}: expires {expiry.isoformat()} days={days} level={lvl}")
            if not lvl:
                continue
            topic = make_topic(name, lvl, expiry_body(name, days, expiry, now.tzinfo), now)
        print(json.dumps(topic, ensure_ascii=False), flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
