"""garbage_day.py の CLI テスト（subprocess で実行する）。"""

import json
import sys
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from helpers import CliTestCase  # noqa: E402

TOPIC_KEYS = {"kind", "ref", "body", "priority", "expires_at"}
WEEKDAY_JA = "月火水木金土日"
ALL_WEEKDAYS_TOML = '["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]'

# 全曜日に「燃やすごみ」が出る。明日は必ず収集される
EVERY_DAY_CALENDAR = (
    'deadline = "08:00"\n'
    "\n"
    "[[rule]]\n"
    'name = "燃やすごみ"\n'
    f"weekdays = {ALL_WEEKDAYS_TOML}\n"
)
# rule 0個。明日は必ず収集されない
NO_RULE_CALENDAR = 'deadline = "08:00"\n'


def tomorrow():
    return date.today() + timedelta(days=1)


def expected_body(day):
    return f"{day.month}月{day.day}日({WEEKDAY_JA[day.weekday()]})は燃やすごみの日。朝8時までに出す。"


class MainOutputTest(CliTestCase):
    def test_outputs_one_json_line_with_expected_fields(self):
        self.write_calendar(EVERY_DAY_CALENDAR)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        topic = json.loads(lines[0])
        self.assertEqual(set(topic), TOPIC_KEYS)
        self.assertEqual(topic["kind"], "life")
        self.assertEqual(topic["ref"], f"garbage:{tomorrow().isoformat()}")
        self.assertEqual(topic["priority"], 1)

    def test_body_mentions_tomorrow_date_and_weekday(self):
        self.write_calendar(EVERY_DAY_CALENDAR)
        result = self.run_cli()
        self.assertEqual(json.loads(result.stdout)["body"], expected_body(tomorrow()))

    def test_expires_at_is_tomorrow_deadline_with_local_timezone(self):
        self.write_calendar(EVERY_DAY_CALENDAR)
        result = self.run_cli()
        local_tz = datetime.now().astimezone().tzinfo
        expected = datetime.combine(tomorrow(), time(8, 0), tzinfo=local_tz).isoformat()
        self.assertEqual(json.loads(result.stdout)["expires_at"], expected)

    def test_japanese_is_output_raw_not_escaped(self):
        self.write_calendar(EVERY_DAY_CALENDAR)
        result = self.run_cli()
        self.assertIn("燃やすごみ", result.stdout)
        self.assertNotIn("\\u", result.stdout)

    def test_no_rule_outputs_nothing_and_exits_0(self):
        self.write_calendar(NO_RULE_CALENDAR)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_skip_exception_on_tomorrow_suppresses_output(self):
        self.write_calendar(
            EVERY_DAY_CALENDAR
            + f'\n[[exception]]\ndate = "{tomorrow().isoformat()}"\nskip = ["燃やすごみ"]\n'
        )
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_skip_exception_on_other_date_does_not_suppress_output(self):
        self.write_calendar(
            EVERY_DAY_CALENDAR
            + f'\n[[exception]]\ndate = "{date.today().isoformat()}"\nskip = ["燃やすごみ"]\n'
        )
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)


class CalendarPathTest(CliTestCase):
    def test_default_calendar_is_read_from_script_directory(self):
        self.write_calendar(EVERY_DAY_CALENDAR)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_working_directory_calendar_is_ignored_when_script_directory_has_none(self):
        self.write_calendar(EVERY_DAY_CALENDAR, self.work / "calendar.toml")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_script_directory_calendar_is_used_even_if_working_directory_has_one(self):
        self.write_calendar(NO_RULE_CALENDAR)
        self.write_calendar(EVERY_DAY_CALENDAR, self.work / "calendar.toml")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class MainFailureTest(CliTestCase):
    def test_missing_default_calendar_exits_1_with_empty_stdout(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")

    def test_invalid_calendar_exits_1_with_empty_stdout(self):
        cases = {
            "toml_syntax_error": 'deadline = "08:00\n',
            "invalid_deadline": 'deadline = "25:00"\n',
            "missing_deadline": "[[rule]]\n" 'name = "燃やすごみ"\n' 'weekdays = ["Tue"]\n',
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                self.write_calendar(text)
                result = self.run_cli()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr.strip(), "")


if __name__ == "__main__":
    unittest.main()
