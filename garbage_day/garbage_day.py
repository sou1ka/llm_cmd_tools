#!/usr/bin/env python3
"""rag-server の定期ジョブ: 翌日のゴミ出しを話題にする（前日夕方に実行）。"""
import json
import sys
import tomllib
from collections import namedtuple
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# スクリプトと同じ場所に置く
CALENDAR_FILE = "calendar.toml"
KIND = "life"
PRIORITY = 1
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # date.weekday() の順
WEEKDAY_JA = "月火水木金土日"

# weekdays: frozenset[int]（0=月）、nth: frozenset[int]（空なら毎週）
Rule = namedtuple("Rule", "name weekdays nth")
# skip / add: tuple[str, ...]
Override = namedtuple("Override", "skip add")
# deadline: datetime.time、rules: list[Rule]、overrides: dict[date, Override]
Calendar = namedtuple("Calendar", "deadline rules overrides")


def log(message):
    print(message, file=sys.stderr)


def _str_list(value, what, allow_empty=True):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{what} must be a list of strings: {value!r}")
    if not allow_empty and not value:
        raise ValueError(f"{what} must not be empty")
    return value


def _parse_rule(raw):
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"rule name is required: {raw!r}")
    weekdays = _str_list(raw.get("weekdays"), f"weekdays of {name}", allow_empty=False)
    unknown = set(weekdays) - set(WEEKDAYS)
    if unknown:
        raise ValueError(f"unknown weekday in {name}: {sorted(unknown)}")
    nth = raw.get("nth", [])
    # bool は int のサブクラスなので明示的に弾く
    if not isinstance(nth, list) or not all(type(n) is int and 1 <= n <= 5 for n in nth):
        raise ValueError(f"nth of {name} must be a list of 1-5: {nth!r}")
    return Rule(name, frozenset(WEEKDAYS.index(w) for w in weekdays), frozenset(nth))


def _parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass
    raise ValueError(f"invalid date: {value!r}")


def load_calendar(path):
    """calendar.toml を読んで Calendar を返す。書式不正は ValueError。"""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    try:
        deadline = datetime.strptime(data["deadline"], "%H:%M").time()
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"deadline must be HH:MM: {data.get('deadline')!r}") from None
    rules = [_parse_rule(raw) for raw in data.get("rule", [])]
    names = {rule.name for rule in rules}
    overrides = {}
    for raw in data.get("exception", []):
        day = _parse_date(raw.get("date"))
        if day in overrides:
            raise ValueError(f"duplicate exception date: {day}")
        skip = _str_list(raw.get("skip", []), f"skip of {day}")
        add = _str_list(raw.get("add", []), f"add of {day}")
        unknown = set(skip + add) - names
        if unknown:
            raise ValueError(f"unknown rule name in exception {day}: {sorted(unknown)}")
        overrides[day] = Override(tuple(skip), tuple(add))
    return Calendar(deadline, rules, overrides)


def collections_on(calendar, day):
    """day（date）に収集する種類名のリスト（rule の記述順、skip 除外、add 追加、重複なし）。"""
    week = (day.day - 1) // 7 + 1
    names = [r.name for r in calendar.rules
             if day.weekday() in r.weekdays and (not r.nth or week in r.nth)]
    override = calendar.overrides.get(day)
    if override:
        names = [n for n in names if n not in override.skip] + list(override.add)
    return list(dict.fromkeys(names))


def _join(names):
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + "と" + names[-1]


def _time_text(t):
    text = f"{t.hour}時" + (f"{t.minute}分" if t.minute else "")
    return "朝" + text if t.hour < 12 else text


def make_topic(day, names, deadline, tz):
    """話題の dict を返す。tz は expires_at に付けるタイムゾーン。"""
    weekday = WEEKDAY_JA[day.weekday()]
    return {
        "kind": KIND,
        "ref": f"garbage:{day.isoformat()}",
        "body": f"{day.month}月{day.day}日({weekday})は{_join(names)}の日。{_time_text(deadline)}までに出す。",
        "priority": PRIORITY,
        "expires_at": datetime.combine(day, deadline, tzinfo=tz).isoformat(),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    path = SCRIPT_DIR / CALENDAR_FILE
    try:
        calendar = load_calendar(path)
    except Exception as e:
        log(f"failed to load calendar {path}: {e!r}")
        sys.exit(1)
    now = datetime.now().astimezone()
    day = now.date() + timedelta(days=1)
    names = collections_on(calendar, day)
    log(f"{day}: {names}")
    if names:
        print(json.dumps(make_topic(day, names, calendar.deadline, now.tzinfo), ensure_ascii=False))


if __name__ == "__main__":
    main()
