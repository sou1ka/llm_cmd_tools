"""domain_expiry.py の CLI テスト。

- 設定エラー系は subprocess で実行する（WHOIS / IANA には接続しない）。
- 正常系・問い合わせ失敗・期限の読めない応答は domain_expiry を import し、whois_query をモックして確認する。
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import domain_expiry  # noqa: E402
from helpers import CONFIG_KEYS, CliTestCase, write_env_file  # noqa: E402

TOPIC_KEYS = {"kind", "ref", "body", "priority", "expires_at"}
END_OF_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}T23:59:59(?:[+-]\d{2}:\d{2}|Z)$")
IANA = domain_expiry.IANA_SERVER

IANA_JP_TEXT = (
    "% IANA WHOIS server\n"
    "% for more information on IANA, visit http://www.iana.org\n"
    "\n"
    "domain:       JP\n"
    "\n"
    "organisation: Japan Registry Services Co., Ltd.\n"
    "\n"
    "whois:        whois.jprs.jp\n"
    "\n"
    "status:       ACTIVE\n"
)
IANA_NET_TEXT = "refer:        whois.verisign-grs.com\n"


def jprs_response(day):
    return f"[Expires on]                    {day:%Y/%m/%d}\n"


def jprs_state_response(day):
    return f"[State]                         Connected ({day:%Y/%m/%d})\n"


def verisign_response(day):
    return f"   Registry Expiry Date: {day.isoformat()}T04:00:00Z\n"


def jp_date(day):
    return f"{day.month}月{day.day}日"


class MainConfigErrorTest(CliTestCase):
    def test_missing_domain_names_exits_1_with_empty_stdout(self):
        result = self.run_cli({})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_empty_domain_names_exits_1_with_empty_stdout(self):
        result = self.run_cli({"DOMAIN_NAMES": ""})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_domain_names_with_only_separators_exits_1_with_empty_stdout(self):
        result = self.run_cli({"DOMAIN_NAMES": " , ,"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_domain_without_dot_exits_1_with_empty_stdout(self):
        for names in ("localhost", "a.net,localhost"):
            with self.subTest(names=names):
                result = self.run_cli({"DOMAIN_NAMES": names})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_invalid_days_exit_1_with_empty_stdout(self):
        cases = [
            {"DOMAIN_WARN_DAYS": "abc"},
            {"DOMAIN_CRIT_DAYS": "-1"},
            {"DOMAIN_WARN_DAYS": "1.5"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli({"DOMAIN_NAMES": "a.net", **extra})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_warn_below_crit_exits_1_with_empty_stdout(self):
        cases = [
            {"DOMAIN_WARN_DAYS": "13"},  # crit は既定値 14
            {"DOMAIN_WARN_DAYS": "5", "DOMAIN_CRIT_DAYS": "7"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli({"DOMAIN_NAMES": "a.net", **extra})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")


class MainWithMockedWhoisTest(unittest.TestCase):
    """whois_query をモックし、main() を直接呼んで stdout と終了コードを確認する。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.script_dir = Path(tmp.name)
        self.today = date.today()
        self.iana = {"jp": IANA_JP_TEXT, "net": IANA_NET_TEXT}  # TLD -> IANA 応答、または送出する例外
        self.responses = {}  # WHOIS に送る query -> 応答テキスト、または送出する例外
        self.calls = []  # whois_query に渡された (server, query, timeout)

        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in CONFIG_KEYS:
            os.environ.pop(key, None)

        for patcher in (
            mock.patch.object(domain_expiry, "SCRIPT_DIR", self.script_dir),
            mock.patch.object(domain_expiry, "whois_query", side_effect=self.fake_whois),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_whois(self, server, query, timeout=None):
        self.calls.append((server, query, timeout))
        if server == IANA:
            outcome = self.iana[query]
        else:
            outcome = self.responses[query]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def day(self, offset):
        return self.today + timedelta(days=offset)

    def write_names(self, names, **extra):
        write_env_file(self.script_dir / ".env", {"DOMAIN_NAMES": names, **extra})

    def run_main(self):
        buf = io.BytesIO()
        stdout = io.TextIOWrapper(buf, encoding="utf-8", write_through=True)
        code = 0
        with mock.patch("sys.stdout", stdout):
            try:
                domain_expiry.main()
            except SystemExit as e:
                code = e.code or 0
        stdout.flush()
        topics = [json.loads(line) for line in buf.getvalue().decode("utf-8").splitlines()]
        return code, topics

    def whois_calls(self):
        return [(server, query) for server, query, _ in self.calls if server != IANA]

    def test_only_domains_within_warn_days_are_output(self):
        self.write_names("near.net,far.net")
        self.responses = {"near.net": verisign_response(self.day(20)), "far.net": verisign_response(self.day(100))}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        topic = topics[0]
        self.assertEqual(set(topic), TOPIC_KEYS)
        self.assertEqual(topic["kind"], "system")
        self.assertEqual(topic["ref"], f"domain:near.net:warn:{self.today.isoformat()}")
        self.assertEqual(topic["priority"], 1)
        self.assertEqual(topic["body"], f"near.netのドメインの期限まであと20日（{jp_date(self.day(20))}）。")

    def test_topic_expires_at_is_end_of_today(self):
        self.write_names("near.net")
        self.responses = {"near.net": verisign_response(self.day(20))}
        _, topics = self.run_main()
        expires_at = topics[0]["expires_at"]
        self.assertRegex(expires_at, END_OF_DAY)
        self.assertEqual(expires_at[:10], self.today.isoformat())

    def test_topics_keep_config_order(self):
        self.write_names("c.net,a.jp,b.net")
        self.responses = {
            "c.net": verisign_response(self.day(1)),
            "a.jp/e": jprs_response(self.day(1)),
            "b.net": verisign_response(self.day(1)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(
            [topic["ref"] for topic in topics],
            [
                f"domain:c.net:crit:{self.today.isoformat()}",
                f"domain:a.jp:crit:{self.today.isoformat()}",
                f"domain:b.net:crit:{self.today.isoformat()}",
            ],
        )
        for topic in topics:
            self.assertEqual(topic["priority"], 3)

    def test_iana_is_queried_once_per_tld_with_tld_string(self):
        self.write_names("a.jp,b.co.jp,c.net")
        self.responses = {
            "a.jp/e": jprs_response(self.day(100)),
            "b.co.jp/e": jprs_state_response(self.day(100)),
            "c.net": verisign_response(self.day(100)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(topics, [])
        iana_queries = [query for server, query, _ in self.calls if server == IANA]
        self.assertEqual(iana_queries, ["jp", "net"])

    def test_whois_server_and_query_follow_iana_and_query_string(self):
        self.write_names("a.jp,b.co.jp,c.net")
        self.responses = {
            "a.jp/e": jprs_response(self.day(100)),
            "b.co.jp/e": jprs_state_response(self.day(100)),
            "c.net": verisign_response(self.day(100)),
        }
        self.run_main()
        self.assertEqual(
            self.whois_calls(),
            [
                ("whois.jprs.jp", "a.jp/e"),
                ("whois.jprs.jp", "b.co.jp/e"),
                ("whois.verisign-grs.com", "c.net"),
            ],
        )

    def test_duplicate_domains_are_queried_once(self):
        self.write_names("a.net,A.NET.")
        self.responses = {"a.net": verisign_response(self.day(100))}
        self.run_main()
        self.assertEqual(self.whois_calls(), [("whois.verisign-grs.com", "a.net")])

    def test_timeout_is_passed_to_every_query(self):
        self.write_names("a.net", DOMAIN_TIMEOUT="3")
        self.responses = {"a.net": verisign_response(self.day(100))}
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([timeout for *_, timeout in self.calls], [3, 3])

    def test_default_timeout_is_30_seconds(self):
        self.write_names("a.net")
        self.responses = {"a.net": verisign_response(self.day(100))}
        self.run_main()
        self.assertEqual([timeout for *_, timeout in self.calls], [30, 30])

    def test_default_thresholds_are_inclusive_at_60_and_14_days(self):
        self.write_names("w60.net,w61.net,c14.net,w15.net")
        self.responses = {
            "w60.net": verisign_response(self.day(60)),
            "w61.net": verisign_response(self.day(61)),
            "c14.net": verisign_response(self.day(14)),
            "w15.net": verisign_response(self.day(15)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(
            [(topic["ref"], topic["priority"]) for topic in topics],
            [
                (f"domain:w60.net:warn:{self.today.isoformat()}", 1),
                (f"domain:c14.net:crit:{self.today.isoformat()}", 3),
                (f"domain:w15.net:warn:{self.today.isoformat()}", 1),
            ],
        )

    def test_custom_thresholds_are_applied(self):
        self.write_names("w30.net,w31.net,c7.net,w8.net", DOMAIN_WARN_DAYS="30", DOMAIN_CRIT_DAYS="7")
        self.responses = {
            "w30.net": verisign_response(self.day(30)),
            "w31.net": verisign_response(self.day(31)),
            "c7.net": verisign_response(self.day(7)),
            "w8.net": verisign_response(self.day(8)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(
            [(topic["ref"], topic["priority"]) for topic in topics],
            [
                (f"domain:w30.net:warn:{self.today.isoformat()}", 1),
                (f"domain:c7.net:crit:{self.today.isoformat()}", 3),
                (f"domain:w8.net:warn:{self.today.isoformat()}", 1),
            ],
        )

    def test_expired_domain_is_crit_topic_with_expired_body(self):
        self.write_names("old.net")
        self.responses = {"old.net": verisign_response(self.day(-2))}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"domain:old.net:crit:{self.today.isoformat()}")
        self.assertEqual(topics[0]["priority"], 3)
        self.assertEqual(topics[0]["body"], "old.netのドメインが期限切れ。")

    def test_domain_expiring_today_is_crit_topic_with_today_body(self):
        self.write_names("now.net")
        self.responses = {"now.net": verisign_response(self.day(0))}
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["priority"], 3)
        self.assertEqual(topics[0]["body"], f"now.netのドメインの期限は今日（{jp_date(self.day(0))}）。")

    def test_jp_responses_in_both_formats_are_read(self):
        self.write_names("a.jp,b.co.jp")
        self.responses = {
            "a.jp/e": jprs_response(self.day(5)),
            "b.co.jp/e": jprs_state_response(self.day(5)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(
            [topic["body"] for topic in topics],
            [
                f"a.jpのドメインの期限まであと5日（{jp_date(self.day(5))}）。",
                f"b.co.jpのドメインの期限まであと5日（{jp_date(self.day(5))}）。",
            ],
        )

    def test_whois_oserror_skips_domain_processes_rest_and_exits_1(self):
        self.write_names("down.net,up.net")
        self.responses = {
            "down.net": OSError("connection refused"),
            "up.net": verisign_response(self.day(1)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(self.whois_calls(), [("whois.verisign-grs.com", "down.net"), ("whois.verisign-grs.com", "up.net")])
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"domain:up.net:crit:{self.today.isoformat()}")

    def test_timeout_error_is_treated_as_failure(self):
        self.write_names("slow.net,far.net")
        self.responses = {
            "slow.net": TimeoutError("timed out"),
            "far.net": verisign_response(self.day(100)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.whois_calls()), 2)
        self.assertEqual(topics, [])

    def test_all_whois_failures_output_nothing_and_exit_1(self):
        self.write_names("down1.net,down2.net")
        self.responses = {
            "down1.net": OSError("connection refused"),
            "down2.net": OSError("connection refused"),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(topics, [])

    def test_iana_oserror_skips_domain_processes_rest_and_exits_1(self):
        self.write_names("x.net,up.jp")
        self.iana["net"] = OSError("connection refused")
        self.responses = {"up.jp/e": jprs_response(self.day(1))}
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"domain:up.jp:crit:{self.today.isoformat()}")

    def test_unknown_whois_server_skips_domain_and_exits_1(self):
        self.write_names("x.net,up.jp")
        self.iana["net"] = "% no match\n"
        self.responses = {"up.jp/e": jprs_response(self.day(1))}
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertNotIn("x.net", [query for _, query in self.whois_calls()])
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"domain:up.jp:crit:{self.today.isoformat()}")

    def test_unreadable_expiry_skips_domain_and_exits_1(self):
        self.write_names("nodate.net,up.jp")
        self.responses = {
            "nodate.net": "Domain Name: NODATE.NET\n",
            "up.jp/e": jprs_response(self.day(1)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["ref"], f"domain:up.jp:crit:{self.today.isoformat()}")

    def test_invalid_calendar_date_in_response_skips_domain_and_exits_1(self):
        self.write_names("bad.net")
        self.responses = {"bad.net": "   Registry Expiry Date: 2027-02-30T04:00:00Z\n"}
        code, topics = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(topics, [])

    def test_all_domains_outside_range_exit_0_without_topics(self):
        self.write_names("far.net,far.jp")
        self.responses = {
            "far.net": verisign_response(self.day(100)),
            "far.jp/e": jprs_response(self.day(100)),
        }
        code, topics = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(topics, [])


if __name__ == "__main__":
    unittest.main()
