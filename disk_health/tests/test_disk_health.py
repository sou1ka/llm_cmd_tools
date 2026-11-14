"""disk_health.py の関数の単体テスト（shutil.disk_usage / os.path.ismount はモックする）。"""

import os
import sys
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import disk_health  # noqa: E402
from disk_health import Thresholds  # noqa: E402
from helpers import CONFIG_KEYS, write_env_file  # noqa: E402

GIB = 1024 ** 3
DEFAULT_THRESHOLDS = Thresholds(90, 95, 20.0, 5.0)
DiskUsage = namedtuple("DiskUsage", "total used free")
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 13, 10, 30, 0, tzinfo=JST)


class IsolatedEnvTestCase(unittest.TestCase):
    def isolate_env(self, values=None):
        """os.environ を一時的に差し替える。CONFIG_KEYS は全て外してから values を入れる。"""
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in CONFIG_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values or {})


class LoadConfigTest(IsolatedEnvTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_path = Path(tmp.name) / ".env"

    def write_raw(self, text):
        self.env_path.write_text(text, encoding="utf-8")

    def test_load_config_without_env_file_reads_only_environment(self):
        self.isolate_env({"DISK_MOUNTS": "/", "DISK_WARN_PERCENT": "80"})
        self.assertEqual(
            disk_health.load_config(self.env_path),
            {"DISK_MOUNTS": "/", "DISK_WARN_PERCENT": "80"},
        )

    def test_load_config_returns_empty_dict_when_nothing_is_set(self):
        self.isolate_env()
        self.assertEqual(disk_health.load_config(self.env_path), {})

    def test_load_config_reads_values_from_env_file(self):
        self.isolate_env()
        write_env_file(self.env_path, {"DISK_MOUNTS": "/,/home", "DISK_WARN_PERCENT": "85"})
        self.assertEqual(
            disk_health.load_config(self.env_path),
            {"DISK_MOUNTS": "/,/home", "DISK_WARN_PERCENT": "85"},
        )

    def test_load_config_skips_comment_and_blank_lines(self):
        self.isolate_env()
        self.write_raw("# comment line\n\nDISK_MOUNTS=/\n   \n# DISK_WARN_PERCENT=1\n")
        self.assertEqual(disk_health.load_config(self.env_path), {"DISK_MOUNTS": "/"})

    def test_load_config_strips_surrounding_whitespace_of_value(self):
        self.isolate_env()
        self.write_raw("DISK_MOUNTS=   /home  \n")
        self.assertEqual(disk_health.load_config(self.env_path), {"DISK_MOUNTS": "/home"})

    def test_load_config_keeps_equal_sign_inside_value(self):
        self.isolate_env()
        self.write_raw("DISK_LABELS=/=system,/home=data\n")
        self.assertEqual(
            disk_health.load_config(self.env_path),
            {"DISK_LABELS": "/=system,/home=data"},
        )

    def test_load_config_environment_overrides_env_file(self):
        self.isolate_env({"DISK_WARN_PERCENT": "70"})
        write_env_file(self.env_path, {"DISK_WARN_PERCENT": "85"})
        self.assertEqual(disk_health.load_config(self.env_path), {"DISK_WARN_PERCENT": "70"})

    def test_load_config_empty_environment_value_falls_back_to_env_file(self):
        self.isolate_env({"DISK_WARN_PERCENT": ""})
        write_env_file(self.env_path, {"DISK_WARN_PERCENT": "85"})
        self.assertEqual(disk_health.load_config(self.env_path), {"DISK_WARN_PERCENT": "85"})

    def test_load_config_empty_environment_value_without_env_file_is_omitted(self):
        self.isolate_env({"DISK_LABELS": ""})
        self.assertEqual(disk_health.load_config(self.env_path), {})


class ParseMountsTest(unittest.TestCase):
    def test_parse_mounts_keeps_order(self):
        self.assertEqual(disk_health.parse_mounts("/,/home"), ["/", "/home"])
        self.assertEqual(disk_health.parse_mounts("/home,/"), ["/home", "/"])

    def test_parse_mounts_strips_surrounding_whitespace(self):
        self.assertEqual(disk_health.parse_mounts(" / , /home "), ["/", "/home"])

    def test_parse_mounts_drops_empty_elements(self):
        self.assertEqual(disk_health.parse_mounts("/,,/home,"), ["/", "/home"])
        self.assertEqual(disk_health.parse_mounts("  ,  /  "), ["/"])

    def test_parse_mounts_removes_duplicates_keeping_first(self):
        self.assertEqual(disk_health.parse_mounts("/home,/,/home"), ["/home", "/"])

    def test_parse_mounts_empty_input_returns_empty_list(self):
        self.assertEqual(disk_health.parse_mounts(""), [])
        self.assertEqual(disk_health.parse_mounts("   "), [])

    def test_parse_mounts_keeps_windows_drive_roots_verbatim(self):
        self.assertEqual(disk_health.parse_mounts("C:\\,D:\\"), ["C:\\", "D:\\"])


class ParseLabelsTest(unittest.TestCase):
    def test_parse_labels_maps_mount_to_name(self):
        self.assertEqual(
            disk_health.parse_labels("/=システムディスク,/home=データ用ディスク"),
            {"/": "システムディスク", "/home": "データ用ディスク"},
        )

    def test_parse_labels_strips_whitespace_of_key_and_value(self):
        self.assertEqual(
            disk_health.parse_labels(" / = システム , /home=データ "),
            {"/": "システム", "/home": "データ"},
        )

    def test_parse_labels_splits_at_first_equal_sign(self):
        self.assertEqual(disk_health.parse_labels("/=a=b"), {"/": "a=b"})

    def test_parse_labels_ignores_element_without_equal_sign(self):
        self.assertEqual(disk_health.parse_labels("/,/home=データ"), {"/home": "データ"})

    def test_parse_labels_ignores_empty_key(self):
        self.assertEqual(disk_health.parse_labels("=名前,/home=データ"), {"/home": "データ"})

    def test_parse_labels_ignores_empty_value(self):
        self.assertEqual(disk_health.parse_labels("/=,/home=データ"), {"/home": "データ"})
        self.assertEqual(disk_health.parse_labels("/=   ,/home=データ"), {"/home": "データ"})

    def test_parse_labels_empty_string_returns_empty_dict(self):
        self.assertEqual(disk_health.parse_labels(""), {})


class ParseThresholdsTest(unittest.TestCase):
    def test_parse_thresholds_uses_defaults_for_empty_config(self):
        result = disk_health.parse_thresholds({})
        self.assertEqual(result, DEFAULT_THRESHOLDS)
        self.assertIsInstance(result.warn_percent, int)
        self.assertIsInstance(result.crit_percent, int)
        self.assertIsInstance(result.warn_free_gb, float)
        self.assertIsInstance(result.crit_free_gb, float)

    def test_parse_thresholds_uses_given_values(self):
        result = disk_health.parse_thresholds({
            "DISK_WARN_PERCENT": "80",
            "DISK_CRIT_PERCENT": "97",
            "DISK_WARN_FREE_GB": "30.5",
            "DISK_CRIT_FREE_GB": "2.5",
        })
        self.assertEqual(result, Thresholds(80, 97, 30.5, 2.5))
        self.assertIsInstance(result.warn_percent, int)
        self.assertIsInstance(result.warn_free_gb, float)

    def test_parse_thresholds_empty_string_uses_default_per_key(self):
        config = {key: "" for key in CONFIG_KEYS}
        self.assertEqual(disk_health.parse_thresholds(config), DEFAULT_THRESHOLDS)

    def test_parse_thresholds_fills_missing_keys_individually(self):
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_PERCENT": "80"}),
            Thresholds(80, 95, 20.0, 5.0),
        )

    def test_parse_thresholds_percent_rejects_non_integer(self):
        for value in ("90.5", "abc"):
            with self.subTest(key="DISK_WARN_PERCENT", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_WARN_PERCENT": value})
            with self.subTest(key="DISK_CRIT_PERCENT", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_CRIT_PERCENT": value})

    def test_parse_thresholds_percent_rejects_out_of_range(self):
        for value in ("-1", "101"):
            with self.subTest(key="DISK_WARN_PERCENT", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_WARN_PERCENT": value})
            with self.subTest(key="DISK_CRIT_PERCENT", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_CRIT_PERCENT": value})

    def test_parse_thresholds_percent_accepts_bounds_0_and_100(self):
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_PERCENT": "0", "DISK_CRIT_PERCENT": "100"}),
            Thresholds(0, 100, 20.0, 5.0),
        )

    def test_parse_thresholds_free_gb_rejects_negative_and_non_numeric(self):
        for value in ("-0.5", "abc"):
            with self.subTest(key="DISK_WARN_FREE_GB", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_WARN_FREE_GB": value})
            with self.subTest(key="DISK_CRIT_FREE_GB", value=value):
                with self.assertRaises(ValueError):
                    disk_health.parse_thresholds({"DISK_CRIT_FREE_GB": value})

    def test_parse_thresholds_free_gb_accepts_zero_and_decimal(self):
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_FREE_GB": "0", "DISK_CRIT_FREE_GB": "0"}),
            Thresholds(90, 95, 0.0, 0.0),
        )
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_FREE_GB": "0.5", "DISK_CRIT_FREE_GB": "0.25"}),
            Thresholds(90, 95, 0.5, 0.25),
        )

    def test_parse_thresholds_rejects_warn_percent_above_crit_percent(self):
        with self.assertRaises(ValueError):
            disk_health.parse_thresholds({"DISK_WARN_PERCENT": "50", "DISK_CRIT_PERCENT": "40"})

    def test_parse_thresholds_rejects_warn_percent_above_default_crit(self):
        # crit は未設定なので既定値 95 と比べる
        with self.assertRaises(ValueError):
            disk_health.parse_thresholds({"DISK_WARN_PERCENT": "96"})

    def test_parse_thresholds_accepts_equal_percent(self):
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_PERCENT": "95"}),
            Thresholds(95, 95, 20.0, 5.0),
        )

    def test_parse_thresholds_rejects_warn_free_below_crit_free(self):
        with self.assertRaises(ValueError):
            disk_health.parse_thresholds({"DISK_WARN_FREE_GB": "4"})

    def test_parse_thresholds_accepts_equal_free(self):
        self.assertEqual(
            disk_health.parse_thresholds({"DISK_WARN_FREE_GB": "5"}),
            Thresholds(90, 95, 5.0, 5.0),
        )


class UsageTest(unittest.TestCase):
    def mocked_usage(self, used, free, mount="/"):
        """ismount を真にし、disk_usage を (used, free) で差し替えて usage() を呼ぶ。"""
        disk_usage = mock.Mock(return_value=DiskUsage(used + free, used, free))
        with mock.patch("shutil.disk_usage", disk_usage), \
                mock.patch("os.path.ismount", return_value=True):
            result = disk_health.usage(mount)
        disk_usage.assert_called_once_with(mount)
        return result

    def test_usage_rounds_partial_percent_up(self):
        # 901 / 1000 = 90.1 -> 91（切り捨てや四捨五入ではない）
        self.assertEqual(self.mocked_usage(used=901, free=99), (91, 99))

    def test_usage_rounds_up_one_third(self):
        self.assertEqual(self.mocked_usage(used=1, free=2), (34, 2))

    def test_usage_rounds_up_two_thirds(self):
        self.assertEqual(self.mocked_usage(used=2, free=1), (67, 1))

    def test_usage_exact_division_is_not_rounded_up(self):
        self.assertEqual(self.mocked_usage(used=50, free=50), (50, 50))
        self.assertEqual(self.mocked_usage(used=7, free=3), (70, 3))

    def test_usage_zero_used_gives_zero_percent(self):
        self.assertEqual(self.mocked_usage(used=0, free=100), (0, 100))

    def test_usage_full_disk_gives_100_percent(self):
        self.assertEqual(self.mocked_usage(used=100, free=0), (100, 0))

    def test_usage_zero_total_gives_zero_percent_and_zero_free(self):
        self.assertEqual(self.mocked_usage(used=0, free=0), (0, 0))

    def test_usage_raises_value_error_when_not_mount(self):
        disk_usage = mock.Mock(return_value=DiskUsage(100, 50, 50))
        with mock.patch("shutil.disk_usage", disk_usage), \
                mock.patch("os.path.ismount", return_value=False):
            with self.assertRaises(ValueError):
                disk_health.usage("/not/a/mount")

    def test_usage_real_temp_root_returns_int_pair_in_range(self):
        percent, free_bytes = disk_health.usage(Path(tempfile.gettempdir()).anchor)
        self.assertIsInstance(percent, int)
        self.assertIsInstance(free_bytes, int)
        self.assertGreaterEqual(percent, 0)
        self.assertLessEqual(percent, 100)
        self.assertGreaterEqual(free_bytes, 0)

    def test_usage_real_non_mount_directory_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                disk_health.usage(tmp)


class LevelTest(unittest.TestCase):
    def test_level_none_when_below_all_thresholds(self):
        self.assertIsNone(disk_health.level(0, 100 * GIB, DEFAULT_THRESHOLDS))

    def test_level_warn_at_percent_threshold(self):
        self.assertEqual(disk_health.level(90, 100 * GIB, DEFAULT_THRESHOLDS), "warn")

    def test_level_none_just_below_percent_threshold(self):
        self.assertIsNone(disk_health.level(89, 100 * GIB, DEFAULT_THRESHOLDS))

    def test_level_crit_at_percent_threshold(self):
        self.assertEqual(disk_health.level(95, 100 * GIB, DEFAULT_THRESHOLDS), "crit")

    def test_level_warn_just_below_crit_percent_threshold(self):
        self.assertEqual(disk_health.level(94, 100 * GIB, DEFAULT_THRESHOLDS), "warn")

    def test_level_warn_when_free_below_warn_free(self):
        self.assertEqual(disk_health.level(10, 20 * GIB - 1, DEFAULT_THRESHOLDS), "warn")

    def test_level_none_when_free_equals_warn_free(self):
        self.assertIsNone(disk_health.level(10, 20 * GIB, DEFAULT_THRESHOLDS))

    def test_level_crit_when_free_below_crit_free(self):
        self.assertEqual(disk_health.level(10, 5 * GIB - 1, DEFAULT_THRESHOLDS), "crit")

    def test_level_warn_when_free_equals_crit_free(self):
        self.assertEqual(disk_health.level(10, 5 * GIB, DEFAULT_THRESHOLDS), "warn")

    def test_level_crit_when_percent_and_free_both_exceeded(self):
        self.assertEqual(disk_health.level(100, 0, DEFAULT_THRESHOLDS), "crit")

    def test_level_crit_by_free_even_with_low_percent(self):
        self.assertEqual(disk_health.level(0, 1 * GIB, DEFAULT_THRESHOLDS), "crit")

    def test_level_warn_by_free_even_with_low_percent(self):
        self.assertEqual(disk_health.level(0, 19 * GIB, DEFAULT_THRESHOLDS), "warn")

    def test_level_fractional_free_thresholds_boundary(self):
        thresholds = Thresholds(90, 95, 0.5, 0.25)
        self.assertIsNone(disk_health.level(0, GIB // 2, thresholds))
        self.assertEqual(disk_health.level(0, GIB // 2 - 1, thresholds), "warn")
        self.assertEqual(disk_health.level(0, GIB // 4 - 1, thresholds), "crit")


class FormatFreeTest(unittest.TestCase):
    def test_format_free_zero(self):
        self.assertEqual(disk_health.format_free(0), "0.0GB")

    def test_format_free_truncates_not_rounds_below_10(self):
        self.assertEqual(disk_health.format_free(int(4.39 * GIB)), "4.3GB")
        self.assertEqual(disk_health.format_free(int(9.99 * GIB)), "9.9GB")

    def test_format_free_truncates_small_fraction_to_zero_point_zero(self):
        self.assertEqual(disk_health.format_free(int(0.05 * GIB)), "0.0GB")

    def test_format_free_just_below_one_gib(self):
        self.assertEqual(disk_health.format_free(GIB - 1), "0.9GB")

    def test_format_free_exact_one_gib(self):
        self.assertEqual(disk_health.format_free(GIB), "1.0GB")

    def test_format_free_five_and_a_half_gib(self):
        self.assertEqual(disk_health.format_free(5 * GIB + GIB // 2), "5.5GB")

    def test_format_free_just_below_ten_gib(self):
        self.assertEqual(disk_health.format_free(10 * GIB - 1), "9.9GB")

    def test_format_free_exact_ten_gib_is_integer(self):
        self.assertEqual(disk_health.format_free(10 * GIB), "10GB")

    def test_format_free_truncates_integer_part_above_10(self):
        self.assertEqual(disk_health.format_free(int(45.9 * GIB)), "45GB")

    def test_format_free_large_value(self):
        self.assertEqual(disk_health.format_free(100 * GIB), "100GB")


class MakeTopicTest(unittest.TestCase):
    def test_make_topic_warn_returns_expected_dict(self):
        self.assertEqual(
            disk_health.make_topic("/", "システムディスク", "warn", 91, int(45.9 * GIB), NOW),
            {
                "kind": "system",
                "ref": "disk:/:warn:2026-11-13",
                "body": "システムディスクの空きが残り45GB（使用率91%）。",
                "priority": 1,
                "expires_at": "2026-11-13T23:59:59+09:00",
            },
        )

    def test_make_topic_crit_has_priority_3_and_crit_ref(self):
        topic = disk_health.make_topic("/", "システムディスク", "crit", 100, 0, NOW)
        self.assertEqual(topic["ref"], "disk:/:crit:2026-11-13")
        self.assertEqual(topic["priority"], 3)
        self.assertEqual(topic["body"], "システムディスクの空きが残り0.0GB（使用率100%）。")

    def test_make_topic_ref_uses_mount_verbatim(self):
        topic = disk_health.make_topic("/home", "データ用ディスク", "warn", 50, 10 * GIB, NOW)
        self.assertEqual(topic["ref"], "disk:/home:warn:2026-11-13")

    def test_make_topic_body_uses_label_as_given(self):
        topic = disk_health.make_topic("/home", "/home", "warn", 50, 10 * GIB, NOW)
        self.assertTrue(topic["body"].startswith("/homeの空きが残り"))

    def test_make_topic_body_formats_free_with_format_free(self):
        topic = disk_health.make_topic("/", "システムディスク", "warn", 90, int(4.39 * GIB), NOW)
        self.assertIn("空きが残り4.3GB", topic["body"])

    def test_make_topic_expires_at_is_end_of_day_in_same_timezone(self):
        now_utc = datetime(2026, 11, 13, 0, 0, 0, tzinfo=timezone.utc)
        topic = disk_health.make_topic("/", "system", "warn", 90, 0, now_utc)
        self.assertEqual(topic["expires_at"], "2026-11-13T23:59:59+00:00")

    def test_make_topic_expires_at_drops_microseconds(self):
        now = datetime(2026, 11, 13, 23, 59, 59, 999999, tzinfo=JST)
        topic = disk_health.make_topic("/", "system", "warn", 90, 0, now)
        self.assertEqual(topic["expires_at"], "2026-11-13T23:59:59+09:00")
        self.assertEqual(topic["ref"], "disk:/:warn:2026-11-13")

    def test_make_topic_date_is_taken_in_timezone_of_now(self):
        # UTC の 2026-11-12 20:00 は、そのタイムゾーンでは 11/12 のまま
        now_utc = datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone.utc)
        topic = disk_health.make_topic("/", "system", "warn", 90, 0, now_utc)
        self.assertEqual(topic["ref"], "disk:/:warn:2026-11-12")
        self.assertEqual(topic["expires_at"], "2026-11-12T23:59:59+00:00")

    def test_make_topic_has_exactly_the_five_keys(self):
        topic = disk_health.make_topic("/", "system", "warn", 90, 0, NOW)
        self.assertEqual(set(topic), {"kind", "ref", "body", "priority", "expires_at"})
        self.assertEqual(topic["kind"], "system")


if __name__ == "__main__":
    unittest.main()
