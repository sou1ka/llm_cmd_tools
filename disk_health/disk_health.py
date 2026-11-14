#!/usr/bin/env python3
"""rag-server の定期ジョブ: ディスクの空きが閾値を超えたら話題を出す。"""
import json
import math
import os
import shutil
import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_KEYS = (
    "DISK_MOUNTS", "DISK_LABELS",
    "DISK_WARN_PERCENT", "DISK_CRIT_PERCENT", "DISK_WARN_FREE_GB", "DISK_CRIT_FREE_GB",
)
GIB = 1024 ** 3
KIND = "system"
PRIORITY = {"warn": 1, "crit": 3}
DEFAULTS = {
    "DISK_WARN_PERCENT": "90", "DISK_CRIT_PERCENT": "95",
    "DISK_WARN_FREE_GB": "20", "DISK_CRIT_FREE_GB": "5",
}

# percent は int、free_gb は float（GiB）
Thresholds = namedtuple("Thresholds", "warn_percent crit_percent warn_free_gb crit_free_gb")


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


def parse_mounts(text):
    """カンマ区切り。前後空白除去、空要素・重複を除く（順序維持）。"""
    return list(dict.fromkeys(m.strip() for m in text.split(",") if m.strip()))


def parse_labels(text):
    """`/=名前,/home=名前` → {"/": "名前", "/home": "名前"}。"""
    labels = {}
    for item in (text or "").split(","):
        mount, sep, name = item.partition("=")
        if sep and mount.strip() and name.strip():
            labels[mount.strip()] = name.strip()
    return labels


def _percent(config, key):
    value = int(config.get(key) or DEFAULTS[key])
    if not 0 <= value <= 100:
        raise ValueError(f"{key} must be 0-100: {value}")
    return value


def _free_gb(config, key):
    value = float(config.get(key) or DEFAULTS[key])
    if not value >= 0:  # nan も弾く
        raise ValueError(f"{key} must be >= 0: {value}")
    return value


def parse_thresholds(config):
    """既定値を補って Thresholds を返す。数値でない・WARN と CRIT の大小が逆なら ValueError。"""
    t = Thresholds(
        _percent(config, "DISK_WARN_PERCENT"), _percent(config, "DISK_CRIT_PERCENT"),
        _free_gb(config, "DISK_WARN_FREE_GB"), _free_gb(config, "DISK_CRIT_FREE_GB"),
    )
    if t.warn_percent > t.crit_percent or t.warn_free_gb < t.crit_free_gb:
        raise ValueError(f"warn/crit order is reversed: {t}")
    return t


def usage(mount):
    """df 相当の (使用率 int 切り上げ, 空きバイト数)。マウントポイントでなければ ValueError。"""
    if not os.path.ismount(mount):
        raise ValueError(f"not a mount point: {mount}")
    _, used, free = shutil.disk_usage(mount)
    if used + free == 0:
        return 0, free
    # df と同じく root 予約分を除いた used / (used + avail) を切り上げ
    return math.ceil(used * 100 / (used + free)), free


def level(percent, free_bytes, thresholds):
    """None / "warn" / "crit"。"""
    t = thresholds
    if percent >= t.crit_percent or free_bytes < t.crit_free_gb * GIB:
        return "crit"
    if percent >= t.warn_percent or free_bytes < t.warn_free_gb * GIB:
        return "warn"
    return None


def format_free(free_bytes):
    """10GB 未満は小数1桁、それ以上は整数。空きを多く言わないよう切り捨てる。"""
    if free_bytes < 10 * GIB:
        return f"{free_bytes * 10 // GIB / 10:.1f}GB"
    return f"{free_bytes // GIB}GB"


def make_topic(mount, label, lvl, percent, free_bytes, now):
    """話題の dict を返す。now はタイムゾーン付き datetime。"""
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return {
        "kind": KIND,
        "ref": f"disk:{mount}:{lvl}:{now.date().isoformat()}",
        "body": f"{label}の空きが残り{format_free(free_bytes)}（使用率{percent}%）。",
        "priority": PRIORITY[lvl],
        "expires_at": end_of_day.isoformat(),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(SCRIPT_DIR / ".env")
    mounts = parse_mounts(config.get("DISK_MOUNTS", ""))
    if not mounts:
        log("DISK_MOUNTS is not set")
        sys.exit(1)
    try:
        thresholds = parse_thresholds(config)
    except ValueError as e:
        log(f"invalid threshold: {e}")
        sys.exit(1)
    labels = parse_labels(config.get("DISK_LABELS", ""))
    now = datetime.now().astimezone()

    failed = False
    for mount in mounts:
        try:
            percent, free = usage(mount)
        except Exception as e:
            log(f"skip {mount}: {e!r}")
            failed = True
            continue
        lvl = level(percent, free, thresholds)
        log(f"{mount}: {percent}% free={free} level={lvl}")
        if lvl:
            topic = make_topic(mount, labels.get(mount, mount), lvl, percent, free, now)
            print(json.dumps(topic, ensure_ascii=False), flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
