"""disk_health.py の CLI テスト（subprocess で実行する）。"""

import json
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from helpers import ALWAYS_CRIT, ALWAYS_WARN, NEVER, CliTestCase  # noqa: E402

TOPIC_KEYS = {"kind", "ref", "body", "priority", "expires_at"}
RFC3339_END_OF_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}T23:59:59(?:[+-]\d{2}:\d{2}|Z)$")


def today_local():
    return datetime.now().astimezone().date().isoformat()


def body_pattern(name):
    """「<name>の空きが残り<空き>（使用率<N>%）。」に一致する正規表現。空きは 4.3GB / 45GB 形式。"""
    return re.compile(rf"^{re.escape(name)}の空きが残り\d+(?:\.\d)?GB（使用率\d+%）。$")


class MainOutputTest(CliTestCase):
    def test_warn_outputs_one_json_line_with_expected_fields(self):
        result = self.run_cli({"DISK_MOUNTS": self.anchor, **ALWAYS_WARN})
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        topic = json.loads(lines[0])
        self.assertEqual(set(topic), TOPIC_KEYS)
        self.assertEqual(topic["kind"], "system")
        self.assertEqual(topic["ref"], f"disk:{self.anchor}:warn:{today_local()}")
        self.assertIsInstance(topic["priority"], int)
        self.assertEqual(topic["priority"], 1)
        self.assertRegex(topic["body"], body_pattern(self.anchor))
        self.assertLessEqual(len(topic["body"]), 200)

    def test_crit_outputs_priority_3_and_crit_ref(self):
        result = self.run_cli({"DISK_MOUNTS": self.anchor, **ALWAYS_CRIT})
        self.assertEqual(result.returncode, 0)
        topic = json.loads(result.stdout)
        self.assertEqual(topic["ref"], f"disk:{self.anchor}:crit:{today_local()}")
        self.assertEqual(topic["priority"], 3)

    def test_no_output_and_exit_zero_when_no_threshold_is_reached(self):
        result = self.run_cli({"DISK_MOUNTS": self.anchor, **NEVER})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_expires_at_is_rfc3339_end_of_today_in_local_timezone(self):
        result = self.run_cli({"DISK_MOUNTS": self.anchor, **ALWAYS_WARN})
        expires_at = json.loads(result.stdout)["expires_at"]
        self.assertRegex(expires_at, RFC3339_END_OF_DAY)
        parsed = datetime.fromisoformat(expires_at)
        now = datetime.now().astimezone()
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.date(), now.date())
        self.assertEqual(parsed.utcoffset(), now.utcoffset())

    def test_label_from_disk_labels_is_used_in_body(self):
        result = self.run_cli({
            "DISK_MOUNTS": self.anchor,
            "DISK_LABELS": f"{self.anchor}=システムディスク",
            **ALWAYS_WARN,
        })
        self.assertEqual(result.returncode, 0)
        # ensure_ascii=False のため、日本語はエスケープされず生の文字として出る
        self.assertIn("システムディスク", result.stdout)
        topic = json.loads(result.stdout)
        self.assertRegex(topic["body"], body_pattern("システムディスク"))

    def test_mount_name_is_used_when_label_is_missing(self):
        result = self.run_cli({
            "DISK_MOUNTS": self.anchor,
            "DISK_LABELS": "/home=データ用ディスク",
            **ALWAYS_WARN,
        })
        topic = json.loads(result.stdout)
        self.assertRegex(topic["body"], body_pattern(self.anchor))

    def test_duplicate_mounts_output_one_line(self):
        result = self.run_cli({
            "DISK_MOUNTS": f" {self.anchor} , {self.anchor} ",
            **ALWAYS_WARN,
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_free_gb_threshold_makes_crit_without_percent_threshold(self):
        result = self.run_cli({
            "DISK_MOUNTS": self.anchor,
            "DISK_WARN_PERCENT": "100",
            "DISK_CRIT_PERCENT": "100",
            "DISK_WARN_FREE_GB": "2000000",
            "DISK_CRIT_FREE_GB": "1000000",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["priority"], 3)

    def test_free_gb_threshold_makes_warn_without_percent_threshold(self):
        result = self.run_cli({
            "DISK_MOUNTS": self.anchor,
            "DISK_WARN_PERCENT": "100",
            "DISK_CRIT_PERCENT": "100",
            "DISK_WARN_FREE_GB": "2000000",
            "DISK_CRIT_FREE_GB": "0",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["priority"], 1)


class MainFailureTest(CliTestCase):
    def test_missing_disk_mounts_exits_1_with_empty_stdout(self):
        result = self.run_cli(dict(ALWAYS_WARN))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_empty_disk_mounts_exits_1_with_empty_stdout(self):
        result = self.run_cli({"DISK_MOUNTS": "", **ALWAYS_WARN})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_invalid_thresholds_exit_1_with_empty_stdout(self):
        cases = [
            {"DISK_WARN_PERCENT": "90.5"},
            {"DISK_CRIT_PERCENT": "abc"},
            {"DISK_WARN_PERCENT": "101"},
            {"DISK_WARN_PERCENT": "96"},
            {"DISK_WARN_FREE_GB": "-1"},
            {"DISK_CRIT_FREE_GB": "x"},
            {"DISK_WARN_FREE_GB": "4"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli({"DISK_MOUNTS": self.anchor, **extra})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_non_mount_path_is_skipped_and_exit_1(self):
        for mounts in (f"{self.not_mount},{self.anchor}", f"{self.anchor},{self.not_mount}"):
            with self.subTest(mounts=mounts):
                result = self.run_cli({"DISK_MOUNTS": mounts, **ALWAYS_WARN})
                self.assertEqual(result.returncode, 1)
                lines = result.stdout.splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])["ref"], f"disk:{self.anchor}:warn:{today_local()}")
                self.assertNotEqual(result.stderr.strip(), "")

    def test_only_non_mount_path_exits_1_with_empty_stdout(self):
        result = self.run_cli({"DISK_MOUNTS": str(self.not_mount), **ALWAYS_WARN})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")

    def test_nonexistent_path_is_skipped_and_exit_1(self):
        missing = self.work / "does_not_exist"
        result = self.run_cli({"DISK_MOUNTS": str(missing), **ALWAYS_WARN})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


class EnvFileTest(CliTestCase):
    def test_env_file_in_script_dir_is_loaded(self):
        self.write_env({"DISK_MOUNTS": self.anchor, **ALWAYS_WARN})
        result = self.run_cli({})
        self.assertEqual(result.returncode, 0)
        topic = json.loads(result.stdout)
        self.assertEqual(topic["priority"], 1)
        self.assertEqual(topic["ref"], f"disk:{self.anchor}:warn:{today_local()}")

    def test_environment_overrides_env_file(self):
        self.write_env({"DISK_MOUNTS": self.anchor, **NEVER})
        result = self.run_cli(dict(ALWAYS_WARN))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["priority"], 1)

    def test_empty_environment_value_falls_back_to_env_file(self):
        self.write_env({"DISK_MOUNTS": self.anchor, **ALWAYS_WARN})
        result = self.run_cli({"DISK_WARN_PERCENT": ""})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["priority"], 1)

    def test_env_file_with_comments_blank_lines_and_equal_sign_in_label(self):
        self.app_dir.joinpath(".env").write_text(
            "# disk watch settings\n"
            "\n"
            f"DISK_MOUNTS={self.anchor}\n"
            f"DISK_LABELS={self.anchor}=data=disk\n"
            "DISK_WARN_PERCENT=0 \n"
            "DISK_CRIT_PERCENT=100\n"
            "DISK_WARN_FREE_GB=0\n"
            "DISK_CRIT_FREE_GB=0\n",
            encoding="utf-8",
        )
        result = self.run_cli({})
        self.assertEqual(result.returncode, 0)
        topic = json.loads(result.stdout)
        self.assertRegex(topic["body"], body_pattern("data=disk"))


if __name__ == "__main__":
    unittest.main()
