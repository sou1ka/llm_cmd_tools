"""cert_expiry.py の CLI テスト。

- 設定エラー系は subprocess で実行する（ネットワークには接続しない）。
- 正常系・検証失敗・接続失敗は cert_expiry を import し、fetch_expiry をモックして確認する。
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import cert_expiry  # noqa: E402
from helpers import CONFIG_KEYS, CliTestCase, make_verify_error, write_env_file  # noqa: E402

TOPIC_KEYS = {"kind", "ref", "body", "priority", "expires_at"}
RFC3339_END_OF_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}T23:59:59(?:[+-]\d{2}:\d{2}|Z)$")


def today_local():
    return datetime.now().astimezone().date().isoformat()


class MainConfigErrorTest(CliTestCase):
    def test_missing_cert_hosts_exits_1_with_empty_stdout(self):
        result = self.run_cli({})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_empty_cert_hosts_exits_1_with_empty_stdout(self):
        result = self.run_cli({"CERT_HOSTS": ""})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_cert_hosts_with_only_separators_exits_1_with_empty_stdout(self):
        result = self.run_cli({"CERT_HOSTS": " , ,"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_invalid_port_exits_1_with_empty_stdout(self):
        for hosts in ("a.example:0", "a.example:65536", "a.example:abc"):
            with self.subTest(hosts=hosts):
                result = self.run_cli({"CERT_HOSTS": hosts})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_invalid_days_exit_1_with_empty_stdout(self):
        cases = [
            {"CERT_WARN_DAYS": "abc"},
            {"CERT_CRIT_DAYS": "-1"},
            {"CERT_WARN_DAYS": "1.5"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli({"CERT_HOSTS": "a.example", **extra})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_warn_below_crit_exits_1_with_empty_stdout(self):
        cases = [
            {"CERT_WARN_DAYS": "6"},
            {"CERT_WARN_DAYS": "5", "CERT_CRIT_DAYS": "7"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli({"CERT_HOSTS": "a.example", **extra})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")


class MainWithMockedFetchTest(unittest.TestCase):
    """fetch_expiry をモックし、main() を直接呼んで stdout と終了コードを確認する。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.script_dir = Path(tmp.name)
        self.now = datetime.now().astimezone()
        self.expiries = {}  # (host, port) -> expiry datetime、または送出する例外
        self.calls = []  # fetch_expiry に渡された (host, port, timeout)

        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in CONFIG_KEYS:
            os.environ.pop(key, None)

        for patcher in (
            mock.patch.object(cert_expiry, "SCRIPT_DIR", self.script_dir),
            mock.patch.object(cert_expiry, "fetch_expiry", side_effect=self.fake_fetch),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_fetch(self, host, port, timeout=None):
        self.calls.append((host, port, timeout))
        outcome = self.expiries[(host, port)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def days_from_now(self, days):
        # 日数の端を避けて 12 時間ずらす（テスト実行中の時間経過で日数が変わらないように）
        return self.now + timedelta(days=days, hours=12)

    def write_hosts(self, hosts, **extra):
        write_env_file(self.script_dir / ".env", {"CERT_HOSTS": hosts, **extra})

    def run_main(self):
        buf = io.BytesIO()
        stdout = io.TextIOWrapper(buf, encoding="utf-8", write_through=True)
        code = 0
        with mock.patch("sys.stdout", stdout):
            try:
                cert_expiry.main()
            except SystemExit as e:
                code = e.code or 0
        stdout.flush()
        topics = [json.loads(line) for line in buf.getvalue().decode("utf-8").splitlines()]
        return code, topics

    def test_only_hosts_within_warn_days_are_output(self):
        self.write_hosts("near.example,far.example")
        self.expiries = {
            ("near.example", 443): self.days_from_now(20),
            ("far.example", 443): self.days_from_now(100),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        topic = topics[0]
        self.assertEqual(set(topic), TOPIC_KEYS)
        self.assertEqual(topic["kind"], "system")
        self.assertEqual(topic["ref"], f"cert:near.example:warn:{today_local()}")
        self.assertEqual(topic["priority"], 1)
        self.assertTrue(topic["body"].startswith("near.example"))

    def test_topic_expires_at_is_end_of_today_in_local_timezone(self):
        self.write_hosts("near.example")
        self.expiries = {("near.example", 443): self.days_from_now(20)}
        _, topics = self.run_main()
        expires_at = topics[0]["expires_at"]
        self.assertRegex(expires_at, RFC3339_END_OF_DAY)
        parsed = datetime.fromisoformat(expires_at)
        self.assertEqual(parsed.date(), self.now.date())
        self.assertEqual(parsed.utcoffset(), self.now.utcoffset())

    def test_topics_keep_config_order_and_use_display_names(self):
        self.write_hosts("c.example,a.example:8443,b.example")
        self.expiries = {
            ("c.example", 443): self.days_from_now(1),
            ("a.example", 8443): self.days_from_now(1),
            ("b.example", 443): self.days_from_now(1),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(
            [topic["ref"] for topic in topics],
            [
                f"cert:c.example:crit:{today_local()}",
                f"cert:a.example:8443:crit:{today_local()}",
                f"cert:b.example:crit:{today_local()}",
            ],
        )
        for topic in topics:
            self.assertEqual(topic["priority"], 3)

    def test_all_hosts_outside_range_exit_0_without_topics(self):
        self.write_hosts("far.example")
        self.expiries = {("far.example", 443): self.days_from_now(100)}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(topics, [])

    def test_verification_failure_is_crit_topic_and_exit_0(self):
        self.write_hosts("bad.example")
        self.expiries = {
            ("bad.example", 443): make_verify_error(20, "unable to get local issuer certificate"),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"cert:bad.example:crit:{today_local()}")
        self.assertEqual(topics[0]["priority"], 3)
        self.assertEqual(
            topics[0]["body"],
            "bad.exampleの証明書の検証に失敗（unable to get local issuer certificate）。",
        )

    def test_verification_error_code_10_is_expired_topic(self):
        self.write_hosts("bad.example")
        self.expiries = {("bad.example", 443): make_verify_error(10, "certificate has expired")}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(topics[0]["priority"], 3)
        self.assertEqual(topics[0]["body"], "bad.exampleの証明書が期限切れ。")

    def test_already_expired_certificate_is_crit_topic_with_expired_body(self):
        self.write_hosts("old.example")
        self.expiries = {("old.example", 443): self.days_from_now(-2)}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"cert:old.example:crit:{today_local()}")
        self.assertEqual(topics[0]["priority"], 3)
        self.assertEqual(topics[0]["body"], "old.exampleの証明書が期限切れ。")

    def test_connection_failure_skips_topic_checks_remaining_hosts_and_exits_1(self):
        self.write_hosts("down.example,up.example")
        self.expiries = {
            ("down.example", 443): OSError("connection refused"),
            ("up.example", 443): self.days_from_now(1),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual([call[0] for call in self.calls], ["down.example", "up.example"])
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"cert:up.example:crit:{today_local()}")

    def test_connection_failure_after_ok_host_still_exits_1(self):
        self.write_hosts("up.example,down.example")
        self.expiries = {
            ("up.example", 443): self.days_from_now(1),
            ("down.example", 443): OSError("connection refused"),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"cert:up.example:crit:{today_local()}")

    def test_timeout_error_is_treated_as_connection_failure(self):
        self.write_hosts("slow.example,far.example")
        self.expiries = {
            ("slow.example", 443): TimeoutError("timed out"),
            ("far.example", 443): self.days_from_now(100),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(topics, [])

    def test_all_connection_failures_output_nothing_and_exit_1(self):
        self.write_hosts("down1.example,down2.example")
        self.expiries = {
            ("down1.example", 443): OSError("connection refused"),
            ("down2.example", 443): OSError("connection refused"),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(topics, [])

    def test_host_port_and_timeout_are_passed_to_fetch_expiry(self):
        self.write_hosts("a.example:8443", CERT_TIMEOUT="3")
        self.expiries = {("a.example", 8443): self.days_from_now(100)}
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [("a.example", 8443, 3)])


if __name__ == "__main__":
    unittest.main()
