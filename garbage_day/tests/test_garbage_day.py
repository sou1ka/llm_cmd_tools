"""garbage_day.py の関数の単体テスト（calendar.toml は一時ファイルとして作る）。"""

import sys
import tempfile
import unittest
from datetime import date, time, timedelta, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import garbage_day  # noqa: E402
from garbage_day import Calendar, Override, Rule  # noqa: E402

JST = timezone(timedelta(hours=9))
DEADLINE = time(8, 0)
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
ALL_WEEKDAYS_TOML = '["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]'
VALID_DEADLINE = 'deadline = "08:00"\n'
VALID_RULE = '[[rule]]\nname = "燃やすごみ"\nweekdays = ["Tue"]\n'


def nov(day):
    """2026年11月の日付。11/1 は日曜。"""
    return date(2026, 11, day)


def rule(name, weekdays, nth=()):
    return Rule(name, frozenset(weekdays), frozenset(nth))


def calendar(rules=(), overrides=None, deadline=DEADLINE):
    return Calendar(deadline, list(rules), dict(overrides or {}))


class CollectionsOnTest(unittest.TestCase):
    def test_no_rules_returns_empty_list(self):
        self.assertEqual(garbage_day.collections_on(calendar(), nov(12)), [])

    def test_weekly_rule_matches_every_week_on_its_weekday(self):
        cal = calendar([rule("燃やすごみ", [TUE, FRI])])
        for day in (nov(3), nov(6), nov(10), nov(13), nov(17)):
            with self.subTest(day=day):
                self.assertEqual(garbage_day.collections_on(cal, day), ["燃やすごみ"])

    def test_weekly_rule_does_not_match_other_weekdays(self):
        cal = calendar([rule("燃やすごみ", [TUE, FRI])])
        self.assertEqual(garbage_day.collections_on(cal, nov(4)), [])  # 水曜
        self.assertEqual(garbage_day.collections_on(cal, nov(7)), [])  # 土曜

    def test_nth_rule_matches_only_the_given_occurrence_of_weekday(self):
        cal = calendar([rule("小型金属類", [THU], nth=[2])])
        self.assertEqual(garbage_day.collections_on(cal, nov(5)), [])   # 第1木曜
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["小型金属類"])  # 第2木曜
        self.assertEqual(garbage_day.collections_on(cal, nov(19)), [])  # 第3木曜
        self.assertEqual(garbage_day.collections_on(cal, nov(26)), [])  # 第4木曜

    def test_nth_boundary_days_of_month_belong_to_correct_week(self):
        first_week = calendar([rule("A", [SAT], nth=[1])])
        self.assertEqual(garbage_day.collections_on(first_week, nov(7)), ["A"])   # 第1週の最終日
        self.assertEqual(garbage_day.collections_on(first_week, nov(14)), [])     # 第2週の初日
        fourth_week = calendar([rule("B", [SAT], nth=[4])])
        self.assertEqual(garbage_day.collections_on(fourth_week, nov(21)), [])    # 第3週の最終日
        self.assertEqual(garbage_day.collections_on(fourth_week, nov(28)), ["B"])  # 第4週の最終日

    def test_fifth_week_rule_matches_only_when_month_has_fifth_occurrence(self):
        cal = calendar([rule("第5日曜", [SUN], nth=[5])])
        self.assertEqual(garbage_day.collections_on(cal, nov(29)), ["第5日曜"])  # 11月の第5日曜
        self.assertEqual(garbage_day.collections_on(cal, nov(22)), [])

    def test_nth_list_matches_any_listed_occurrence(self):
        cal = calendar([rule("A", [THU], nth=[1, 3])])
        self.assertEqual(garbage_day.collections_on(cal, nov(5)), ["A"])
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), [])
        self.assertEqual(garbage_day.collections_on(cal, nov(19)), ["A"])

    def test_rules_on_same_day_keep_declaration_order(self):
        cal = calendar([rule("資源ごみ", [THU]), rule("燃やすごみ", [THU])])
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["資源ごみ", "燃やすごみ"])

        reversed_cal = calendar([rule("燃やすごみ", [THU]), rule("資源ごみ", [THU])])
        self.assertEqual(garbage_day.collections_on(reversed_cal, nov(12)), ["燃やすごみ", "資源ごみ"])

    def test_duplicate_names_are_collected_once_at_first_position(self):
        cal = calendar([rule("A", [THU]), rule("B", [THU]), rule("A", [THU])])
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["A", "B"])

    def test_skip_removes_name_on_that_day(self):
        cal = calendar(
            [rule("燃やすごみ", [THU]), rule("資源ごみ", [THU])],
            overrides={nov(12): Override(skip=("燃やすごみ",), add=())},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["資源ごみ"])

    def test_skip_of_name_not_collected_that_day_changes_nothing(self):
        cal = calendar(
            [rule("燃やすごみ", [THU])],
            overrides={nov(12): Override(skip=("資源ごみ",), add=())},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["燃やすごみ"])

    def test_skip_all_collected_names_returns_empty_list(self):
        cal = calendar(
            [rule("燃やすごみ", [THU])],
            overrides={nov(12): Override(skip=("燃やすごみ",), add=())},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), [])

    def test_add_appends_name_at_end_of_list(self):
        cal = calendar(
            [rule("燃やすごみ", [THU]), rule("資源ごみ", [MON])],
            overrides={nov(12): Override(skip=(), add=("資源ごみ",))},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["燃やすごみ", "資源ごみ"])

    def test_add_works_on_day_with_no_matching_rule(self):
        cal = calendar(
            [rule("燃やすごみ", [MON])],
            overrides={nov(12): Override(skip=(), add=("燃やすごみ",))},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["燃やすごみ"])

    def test_add_of_name_already_collected_is_not_duplicated(self):
        cal = calendar(
            [rule("燃やすごみ", [THU])],
            overrides={nov(12): Override(skip=(), add=("燃やすごみ",))},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["燃やすごみ"])

    def test_add_with_same_name_twice_is_collected_once(self):
        cal = calendar(overrides={nov(12): Override(skip=(), add=("資源ごみ", "資源ごみ"))})
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["資源ごみ"])

    def test_skip_and_add_together_on_same_day(self):
        cal = calendar(
            [rule("燃やすごみ", [THU]), rule("資源ごみ", [THU])],
            overrides={nov(12): Override(skip=("燃やすごみ",), add=("小型金属類",))},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), ["資源ごみ", "小型金属類"])

    def test_override_applies_only_to_its_own_date(self):
        cal = calendar(
            [rule("燃やすごみ", [THU])],
            overrides={nov(12): Override(skip=("燃やすごみ",), add=())},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(12)), [])
        self.assertEqual(garbage_day.collections_on(cal, nov(19)), ["燃やすごみ"])

    def test_add_override_does_not_affect_other_dates(self):
        cal = calendar(
            [rule("燃やすごみ", [MON])],
            overrides={nov(12): Override(skip=(), add=("資源ごみ",))},
        )
        self.assertEqual(garbage_day.collections_on(cal, nov(13)), [])
        self.assertEqual(garbage_day.collections_on(cal, nov(16)), ["燃やすごみ"])


class MakeTopicTest(unittest.TestCase):
    def test_topic_has_fixed_kind_ref_priority_body_and_expires_at(self):
        topic = garbage_day.make_topic(nov(14), ["燃やすごみ", "資源ごみ"], DEADLINE, JST)
        self.assertEqual(topic, {
            "kind": "life",
            "ref": "garbage:2026-11-14",
            "body": "11月14日(土)は燃やすごみと資源ごみの日。朝8時までに出す。",
            "priority": 1,
            "expires_at": "2026-11-14T08:00:00+09:00",
        })

    def test_expires_at_uses_given_timezone(self):
        topic = garbage_day.make_topic(nov(14), ["燃やすごみ"], DEADLINE, timezone.utc)
        self.assertEqual(topic["expires_at"], "2026-11-14T08:00:00+00:00")

    def test_expires_at_uses_given_deadline_time(self):
        topic = garbage_day.make_topic(nov(14), ["燃やすごみ"], time(23, 59), JST)
        self.assertEqual(topic["expires_at"], "2026-11-14T23:59:00+09:00")

    def test_body_with_one_name(self):
        topic = garbage_day.make_topic(nov(14), ["燃やすごみ"], DEADLINE, JST)
        self.assertEqual(topic["body"], "11月14日(土)は燃やすごみの日。朝8時までに出す。")

    def test_body_with_two_names_joins_with_to(self):
        topic = garbage_day.make_topic(nov(14), ["A", "B"], DEADLINE, JST)
        self.assertEqual(topic["body"], "11月14日(土)はAとBの日。朝8時までに出す。")

    def test_body_with_three_names_joins_with_comma_then_to(self):
        topic = garbage_day.make_topic(nov(14), ["A", "B", "C"], DEADLINE, JST)
        self.assertEqual(topic["body"], "11月14日(土)はA、BとCの日。朝8時までに出す。")

    def test_body_with_four_names_joins_all_but_last_with_comma(self):
        topic = garbage_day.make_topic(nov(14), ["A", "B", "C", "D"], DEADLINE, JST)
        self.assertEqual(topic["body"], "11月14日(土)はA、B、CとDの日。朝8時までに出す。")

    def test_month_and_day_are_not_zero_padded(self):
        topic = garbage_day.make_topic(nov(3), ["A"], DEADLINE, JST)
        self.assertEqual(topic["body"], "11月3日(火)はAの日。朝8時までに出す。")

    def test_weekday_is_single_japanese_character(self):
        expected = {
            nov(2): "月", nov(3): "火", nov(4): "水", nov(5): "木",
            nov(6): "金", nov(7): "土", nov(1): "日",
        }
        for day, weekday_ja in expected.items():
            with self.subTest(day=day):
                topic = garbage_day.make_topic(day, ["A"], DEADLINE, JST)
                self.assertIn(f"({weekday_ja})", topic["body"])

    def test_time_before_noon_gets_asa_prefix(self):
        cases = {
            time(8, 0): "朝8時",
            time(8, 30): "朝8時30分",
            time(11, 59): "朝11時59分",
            time(0, 0): "朝0時",
        }
        for deadline, expected in cases.items():
            with self.subTest(deadline=deadline):
                topic = garbage_day.make_topic(nov(14), ["A"], deadline, JST)
                self.assertTrue(topic["body"].endswith(f"{expected}までに出す。"))

    def test_time_at_or_after_noon_has_no_asa_prefix(self):
        cases = {
            time(12, 0): "12時",
            time(12, 30): "12時30分",
            time(13, 0): "13時",
            time(13, 5): "13時5分",
            time(23, 59): "23時59分",
        }
        for deadline, expected in cases.items():
            with self.subTest(deadline=deadline):
                topic = garbage_day.make_topic(nov(14), ["A"], deadline, JST)
                self.assertTrue(topic["body"].endswith(f"{expected}までに出す。"))
                self.assertNotIn("朝", topic["body"])


class LoadCalendarTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def load(self, text):
        path = self.dir / "calendar.toml"
        path.write_text(text, encoding="utf-8")
        return garbage_day.load_calendar(path)

    def assert_invalid(self, text):
        with self.assertRaises(ValueError):
            self.load(text)

    # --- 正常系 ---

    def test_load_calendar_reads_deadline_as_time(self):
        cal = self.load(VALID_DEADLINE)
        self.assertEqual(cal.deadline, time(8, 0))

    def test_deadline_accepts_single_digit_hour(self):
        self.assertEqual(self.load('deadline = "8:00"\n').deadline, time(8, 0))

    def test_deadline_accepts_day_boundaries(self):
        self.assertEqual(self.load('deadline = "00:00"\n').deadline, time(0, 0))
        self.assertEqual(self.load('deadline = "23:59"\n').deadline, time(23, 59))

    def test_missing_rule_and_exception_sections_give_empty_collections(self):
        cal = self.load(VALID_DEADLINE)
        self.assertEqual(cal.rules, [])
        self.assertEqual(cal.overrides, {})

    def test_weekday_names_become_monday_zero_ints(self):
        cal = self.load(
            VALID_DEADLINE
            + '[[rule]]\nname = "A"\nweekdays = ["Sun", "Mon", "Wed"]\n'
        )
        self.assertEqual(cal.rules[0].weekdays, frozenset({6, 0, 2}))

    def test_all_seven_weekday_names_are_accepted(self):
        cal = self.load(
            VALID_DEADLINE
            + f'[[rule]]\nname = "A"\nweekdays = {ALL_WEEKDAYS_TOML}\n'
        )
        self.assertEqual(cal.rules[0].weekdays, frozenset(range(7)))

    def test_rule_without_nth_is_weekly_with_empty_nth(self):
        cal = self.load(VALID_DEADLINE + VALID_RULE)
        self.assertEqual(cal.rules[0], Rule("燃やすごみ", frozenset({TUE}), frozenset()))

    def test_rule_with_empty_nth_list_is_weekly_with_empty_nth(self):
        cal = self.load(VALID_DEADLINE + '[[rule]]\nname = "A"\nweekdays = ["Tue"]\nnth = []\n')
        self.assertEqual(cal.rules[0].nth, frozenset())

    def test_nth_list_becomes_frozenset_including_boundary_values(self):
        cal = self.load(VALID_DEADLINE + '[[rule]]\nname = "A"\nweekdays = ["Thu"]\nnth = [1, 5]\n')
        self.assertEqual(cal.rules[0].nth, frozenset({1, 5}))

    def test_rules_keep_declaration_order_and_names(self):
        cal = self.load(
            VALID_DEADLINE
            + '[[rule]]\nname = "燃やすごみ"\nweekdays = ["Tue"]\n'
            + '[[rule]]\nname = "資源ごみ"\nweekdays = ["Wed"]\n'
        )
        self.assertEqual([r.name for r in cal.rules], ["燃やすごみ", "資源ごみ"])

    def test_rules_with_same_name_are_all_kept(self):
        cal = self.load(
            VALID_DEADLINE
            + '[[rule]]\nname = "A"\nweekdays = ["Tue"]\n'
            + '[[rule]]\nname = "A"\nweekdays = ["Fri"]\n'
        )
        self.assertEqual(len(cal.rules), 2)

    def test_exception_date_accepts_toml_date_literal(self):
        cal = self.load(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = 2026-12-31\nskip = ["燃やすごみ"]\n'
        )
        self.assertEqual(cal.overrides, {date(2026, 12, 31): Override(skip=("燃やすごみ",), add=())})

    def test_exception_date_accepts_yyyy_mm_dd_string(self):
        cal = self.load(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = "2026-12-31"\nskip = ["燃やすごみ"]\n'
        )
        self.assertEqual(cal.overrides, {date(2026, 12, 31): Override(skip=("燃やすごみ",), add=())})

    def test_exception_add_becomes_tuple(self):
        cal = self.load(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = "2027-01-04"\nadd = ["燃やすごみ"]\n'
        )
        self.assertEqual(cal.overrides[date(2027, 1, 4)], Override(skip=(), add=("燃やすごみ",)))

    def test_exception_without_skip_or_add_has_empty_tuples(self):
        cal = self.load(VALID_DEADLINE + '[[exception]]\ndate = "2027-01-04"\n')
        self.assertEqual(cal.overrides[date(2027, 1, 4)], Override(skip=(), add=()))

    def test_exception_skip_and_add_both_present(self):
        cal = self.load(
            VALID_DEADLINE + VALID_RULE + '[[rule]]\nname = "資源ごみ"\nweekdays = ["Wed"]\n'
            + '[[exception]]\ndate = "2026-12-31"\nskip = ["燃やすごみ"]\nadd = ["資源ごみ"]\n'
        )
        self.assertEqual(
            cal.overrides[date(2026, 12, 31)],
            Override(skip=("燃やすごみ",), add=("資源ごみ",)),
        )

    # --- 異常系 ---

    def test_missing_deadline_is_invalid(self):
        self.assert_invalid(VALID_RULE)

    def test_invalid_deadline_values_are_invalid(self):
        cases = ['"25:00"', '"abc"', '""', '"12:60"', "800"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(f"deadline = {value}\n")

    def test_rule_without_name_is_invalid(self):
        self.assert_invalid(VALID_DEADLINE + '[[rule]]\nweekdays = ["Tue"]\n')

    def test_rule_with_empty_or_non_string_name_is_invalid(self):
        for name in ['""', "123"]:
            with self.subTest(name=name):
                self.assert_invalid(
                    VALID_DEADLINE + f'[[rule]]\nname = {name}\nweekdays = ["Tue"]\n'
                )

    def test_rule_without_weekdays_is_invalid(self):
        self.assert_invalid(VALID_DEADLINE + '[[rule]]\nname = "A"\n')

    def test_invalid_weekdays_are_invalid(self):
        cases = ["[]", '["Monday"]', '["mon"]', '["MON"]', '"Tue"', "[1]"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(
                    VALID_DEADLINE + f'[[rule]]\nname = "A"\nweekdays = {value}\n'
                )

    def test_invalid_nth_values_are_invalid(self):
        cases = ["[0]", "[6]", "[-1]", "[true]", '["1"]', "[1.0]", "2"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(
                    VALID_DEADLINE + f'[[rule]]\nname = "A"\nweekdays = ["Thu"]\nnth = {value}\n'
                )

    def test_exception_without_date_is_invalid(self):
        self.assert_invalid(VALID_DEADLINE + VALID_RULE + '[[exception]]\nskip = ["燃やすごみ"]\n')

    def test_invalid_exception_dates_are_invalid(self):
        cases = ['"2026-13-01"', '"2026-02-30"', '"2026/12/31"', "20261231"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(
                    VALID_DEADLINE + VALID_RULE + f"[[exception]]\ndate = {value}\n"
                )

    def test_duplicate_exception_dates_are_invalid(self):
        self.assert_invalid(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = 2026-12-31\nskip = ["燃やすごみ"]\n'
            + '[[exception]]\ndate = "2026-12-31"\nadd = ["燃やすごみ"]\n'
        )

    def test_exception_skip_with_unknown_rule_name_is_invalid(self):
        self.assert_invalid(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = "2026-12-31"\nskip = ["存在しない"]\n'
        )

    def test_exception_add_with_unknown_rule_name_is_invalid(self):
        self.assert_invalid(
            VALID_DEADLINE + VALID_RULE
            + '[[exception]]\ndate = "2026-12-31"\nadd = ["存在しない"]\n'
        )

    def test_exception_skip_with_no_rules_defined_is_invalid(self):
        self.assert_invalid(VALID_DEADLINE + '[[exception]]\ndate = "2026-12-31"\nskip = ["燃やすごみ"]\n')

    def test_exception_skip_or_add_that_is_not_a_string_list_is_invalid(self):
        cases = ['"燃やすごみ"', "[1]"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(
                    VALID_DEADLINE + VALID_RULE
                    + f'[[exception]]\ndate = "2026-12-31"\nskip = {value}\n'
                )

    def test_missing_file_raises_exception(self):
        with self.assertRaises(Exception):
            garbage_day.load_calendar(self.dir / "does_not_exist.toml")

    def test_toml_syntax_error_raises_exception(self):
        with self.assertRaises(Exception):
            self.load('deadline = "08:00\n')


if __name__ == "__main__":
    unittest.main()
