"""domain_expiry.py の関数の単体テスト（socket はモックする。ネットワークには接続しない）。"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import domain_expiry  # noqa: E402
from helpers import CONFIG_KEYS, write_env_file  # noqa: E402

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
NOW = datetime(2026, 11, 14, 10, 30, 0, tzinfo=JST)
EXPIRY = date(2027, 4, 26)

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
    "remarks:      Registration information: http://jprs.jp/\n"
)

VERISIGN_TEXT = (
    "   Domain Name: EXAMPLE.NET\n"
    "   Registry Domain ID: 0000000000_DOMAIN_NET-VRSN\n"
    "   Updated Date: 2025-03-20T09:12:01Z\n"
    "   Creation Date: 1995-08-14T04:00:00Z\n"
    "   Registry Expiry Date: 2027-03-15T04:00:00Z\n"
    "   Registrar: EXAMPLE REGISTRAR, INC.\n"
    "\n"
    ">>> Last update of whois database: 2026-11-14T01:23:45Z <<<\n"
    "\n"
    "NOTICE: The expiration date displayed in this record is the date the\n"
    "registrar's sponsorship of the domain name registration in the registry is\n"
    "currently set to expire. This date does not necessarily reflect the expiration\n"
    "date of the domain name registrant's agreement with the sponsoring registrar.\n"
)


def timeout_of(call):
    """whois_query / create_connection に渡された timeout（キーワードでも位置引数でも可）。"""
    if "timeout" in call.kwargs:
        return call.kwargs["timeout"]
    return call.args[1]


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
        self.isolate_env({"DOMAIN_NAMES": "example.net", "DOMAIN_WARN_DAYS": "30"})
        self.assertEqual(
            domain_expiry.load_config(self.env_path),
            {"DOMAIN_NAMES": "example.net", "DOMAIN_WARN_DAYS": "30"},
        )

    def test_load_config_returns_empty_dict_when_nothing_is_set(self):
        self.isolate_env()
        self.assertEqual(domain_expiry.load_config(self.env_path), {})

    def test_load_config_reads_values_from_env_file(self):
        self.isolate_env()
        write_env_file(self.env_path, {"DOMAIN_NAMES": "example.net,example.co.jp", "DOMAIN_TIMEOUT": "5"})
        self.assertEqual(
            domain_expiry.load_config(self.env_path),
            {"DOMAIN_NAMES": "example.net,example.co.jp", "DOMAIN_TIMEOUT": "5"},
        )

    def test_load_config_skips_comment_and_blank_lines_and_lines_without_equal_sign(self):
        self.isolate_env()
        self.write_raw("# comment line\n\nDOMAIN_NAMES=example.net\n   \n# DOMAIN_WARN_DAYS=1\nnot_a_setting\n")
        self.assertEqual(domain_expiry.load_config(self.env_path), {"DOMAIN_NAMES": "example.net"})

    def test_load_config_strips_surrounding_whitespace_of_key_and_value(self):
        self.isolate_env()
        self.write_raw("  DOMAIN_TIMEOUT  =   5  \n")
        self.assertEqual(domain_expiry.load_config(self.env_path), {"DOMAIN_TIMEOUT": "5"})

    def test_load_config_keeps_equal_sign_inside_value(self):
        self.isolate_env()
        self.write_raw("DOMAIN_NAMES=a=b.net\n")
        self.assertEqual(domain_expiry.load_config(self.env_path), {"DOMAIN_NAMES": "a=b.net"})

    def test_load_config_environment_overrides_env_file(self):
        self.isolate_env({"DOMAIN_WARN_DAYS": "14"})
        write_env_file(self.env_path, {"DOMAIN_WARN_DAYS": "60"})
        self.assertEqual(domain_expiry.load_config(self.env_path), {"DOMAIN_WARN_DAYS": "14"})

    def test_load_config_empty_environment_value_falls_back_to_env_file(self):
        self.isolate_env({"DOMAIN_WARN_DAYS": ""})
        write_env_file(self.env_path, {"DOMAIN_WARN_DAYS": "60"})
        self.assertEqual(domain_expiry.load_config(self.env_path), {"DOMAIN_WARN_DAYS": "60"})

    def test_load_config_empty_environment_value_without_env_file_is_omitted(self):
        self.isolate_env({"DOMAIN_NAMES": ""})
        self.assertEqual(domain_expiry.load_config(self.env_path), {})


class ParseDomainsTest(unittest.TestCase):
    def test_parse_domains_normalizes_case_and_trailing_dot_and_drops_duplicates(self):
        self.assertEqual(
            domain_expiry.parse_domains("Example.NET. , foo.jp,,example.net"),
            ["example.net", "foo.jp"],
        )

    def test_parse_domains_keeps_order(self):
        self.assertEqual(domain_expiry.parse_domains("c.net,a.jp,b.com"), ["c.net", "a.jp", "b.com"])

    def test_parse_domains_strips_surrounding_whitespace(self):
        self.assertEqual(domain_expiry.parse_domains(" a.net \t, b.jp "), ["a.net", "b.jp"])

    def test_parse_domains_drops_empty_elements(self):
        self.assertEqual(domain_expiry.parse_domains("a.net,,  ,b.jp,"), ["a.net", "b.jp"])

    def test_parse_domains_empty_text_returns_empty_list(self):
        self.assertEqual(domain_expiry.parse_domains(""), [])
        self.assertEqual(domain_expiry.parse_domains("  ,  "), [])

    def test_parse_domains_removes_duplicates_keeping_first(self):
        self.assertEqual(domain_expiry.parse_domains("b.net,a.jp,B.NET,a.jp."), ["b.net", "a.jp"])

    def test_parse_domains_accepts_multi_label_domain(self):
        self.assertEqual(domain_expiry.parse_domains("example.co.jp"), ["example.co.jp"])

    def test_parse_domains_rejects_element_without_dot(self):
        with self.assertRaises(ValueError):
            domain_expiry.parse_domains("localhost")
        with self.assertRaises(ValueError):
            domain_expiry.parse_domains("a.net,localhost")


class ParseSettingsTest(unittest.TestCase):
    def test_parse_settings_uses_defaults_for_empty_config(self):
        warn_days, crit_days, timeout = domain_expiry.parse_settings({})
        self.assertEqual((warn_days, crit_days, timeout), (60, 14, 30.0))
        self.assertIsInstance(warn_days, int)
        self.assertIsInstance(crit_days, int)
        self.assertIsInstance(timeout, float)

    def test_parse_settings_uses_given_values(self):
        self.assertEqual(
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "30", "DOMAIN_CRIT_DAYS": "7", "DOMAIN_TIMEOUT": "5.5"}),
            (30, 7, 5.5),
        )

    def test_parse_settings_empty_string_uses_default_per_key(self):
        config = {key: "" for key in CONFIG_KEYS}
        self.assertEqual(domain_expiry.parse_settings(config), (60, 14, 30.0))

    def test_parse_settings_fills_missing_keys_individually(self):
        self.assertEqual(domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "30"}), (30, 14, 30.0))
        self.assertEqual(domain_expiry.parse_settings({"DOMAIN_CRIT_DAYS": "7"}), (60, 7, 30.0))

    def test_parse_settings_allows_equal_warn_and_crit(self):
        self.assertEqual(
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "14", "DOMAIN_CRIT_DAYS": "14"}),
            (14, 14, 30.0),
        )

    def test_parse_settings_allows_zero_days(self):
        self.assertEqual(
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "0", "DOMAIN_CRIT_DAYS": "0"}),
            (0, 0, 30.0),
        )

    def test_parse_settings_accepts_fractional_timeout(self):
        self.assertEqual(domain_expiry.parse_settings({"DOMAIN_TIMEOUT": "0.5"})[2], 0.5)

    def test_parse_settings_rejects_non_numeric_days(self):
        for value in ("abc", "1.5"):
            for key in ("DOMAIN_WARN_DAYS", "DOMAIN_CRIT_DAYS"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        domain_expiry.parse_settings({key: value})

    def test_parse_settings_rejects_negative_days(self):
        with self.assertRaises(ValueError):
            domain_expiry.parse_settings({"DOMAIN_CRIT_DAYS": "-1"})
        with self.assertRaises(ValueError):
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "-1", "DOMAIN_CRIT_DAYS": "-2"})

    def test_parse_settings_rejects_timeout_not_positive_or_non_numeric(self):
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    domain_expiry.parse_settings({"DOMAIN_TIMEOUT": value})

    def test_parse_settings_rejects_warn_below_crit(self):
        # crit は未設定なので既定値 14 と比べる
        with self.assertRaises(ValueError):
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "13"})
        with self.assertRaises(ValueError):
            domain_expiry.parse_settings({"DOMAIN_WARN_DAYS": "3", "DOMAIN_CRIT_DAYS": "7"})


class WhoisQueryTest(unittest.TestCase):
    def patch_socket(self, recv=(b"",), connect_error=None):
        """socket.create_connection をモックする。recv は side_effect の列（例外を入れると送出される）。
        戻り値は (create_connection, sock)。sock は with 文でも直接でも同じオブジェクトを返す。"""
        sock = mock.MagicMock(name="sock")
        sock.__enter__.return_value = sock
        sock.recv.side_effect = list(recv)
        create_connection = mock.Mock(return_value=sock, side_effect=connect_error)
        patcher = mock.patch("socket.create_connection", create_connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        return create_connection, sock

    def test_whois_query_connects_to_port_43_with_timeout(self):
        create_connection, _ = self.patch_socket()
        domain_expiry.whois_query("whois.example", "example.net", 7.5)
        self.assertEqual(create_connection.call_args.args[0], ("whois.example", 43))
        self.assertEqual(timeout_of(create_connection.call_args), 7.5)

    def test_whois_query_sends_query_followed_by_crlf(self):
        _, sock = self.patch_socket()
        domain_expiry.whois_query("whois.example", "example.net", 5)
        sent = b"".join(c.args[0] for c in sock.sendall.call_args_list) + b"".join(
            c.args[0] for c in sock.send.call_args_list
        )
        self.assertEqual(sent, b"example.net\r\n")

    def test_whois_query_reads_chunks_until_eof_and_joins_them(self):
        _, sock = self.patch_socket(recv=[b"Domain Name: EXAMPLE", b".NET\r\n", b"Expiry: 2027\r\n", b""])
        result = domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertEqual(result, "Domain Name: EXAMPLE.NET\r\nExpiry: 2027\r\n")
        self.assertEqual(sock.recv.call_count, 4)

    def test_whois_query_decodes_utf8_after_joining_chunks(self):
        data = "期限: 2027/02/28\n".encode("utf-8")
        split = 4  # 「限」の 1 バイト目の後で分割し、文字の途中で chunk が切れる場合を確認する
        self.patch_socket(recv=[data[:split], data[split:], b""])
        result = domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertEqual(result, "期限: 2027/02/28\n")

    def test_whois_query_replaces_invalid_utf8_bytes(self):
        self.patch_socket(recv=[b"ab\xffcd\n", b""])
        result = domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertEqual(result, "ab�cd\n")

    def test_whois_query_returns_empty_string_when_server_closes_immediately(self):
        self.patch_socket(recv=[b""])
        self.assertEqual(domain_expiry.whois_query("whois.example", "example.net", 5), "")

    def test_whois_query_propagates_connection_oserror_unchanged(self):
        error = OSError("connection refused")
        self.patch_socket(connect_error=error)
        with self.assertRaises(OSError) as cm:
            domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertIs(cm.exception, error)

    def test_whois_query_propagates_timeout_error_unchanged(self):
        error = TimeoutError("timed out")
        self.patch_socket(connect_error=error)
        with self.assertRaises(TimeoutError) as cm:
            domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertIs(cm.exception, error)

    def test_whois_query_propagates_error_raised_while_receiving(self):
        error = ConnectionResetError("reset by peer")
        self.patch_socket(recv=[b"partial", error])
        with self.assertRaises(OSError) as cm:
            domain_expiry.whois_query("whois.example", "example.net", 5)
        self.assertIs(cm.exception, error)


class ParseServerTest(unittest.TestCase):
    def test_parse_server_reads_whois_line_of_iana_response(self):
        self.assertEqual(domain_expiry.parse_server(IANA_JP_TEXT), "whois.jprs.jp")

    def test_parse_server_reads_refer_line(self):
        self.assertEqual(
            domain_expiry.parse_server("refer:        whois.verisign-grs.com\n"),
            "whois.verisign-grs.com",
        )

    def test_parse_server_key_is_case_insensitive(self):
        self.assertEqual(domain_expiry.parse_server("WHOIS:        whois.jprs.jp\n"), "whois.jprs.jp")
        self.assertEqual(domain_expiry.parse_server("Refer: whois.verisign-grs.com\n"), "whois.verisign-grs.com")

    def test_parse_server_strips_surrounding_whitespace_of_value(self):
        self.assertEqual(domain_expiry.parse_server("whois:\twhois.jprs.jp  \r\n"), "whois.jprs.jp")

    def test_parse_server_returns_none_when_no_server_line(self):
        text_without_whois = "".join(
            line for line in IANA_JP_TEXT.splitlines(keepends=True) if not line.startswith("whois:")
        )
        self.assertIsNone(domain_expiry.parse_server(text_without_whois))
        self.assertIsNone(domain_expiry.parse_server(""))

    def test_parse_server_ignores_indented_key(self):
        self.assertIsNone(domain_expiry.parse_server("  whois:        whois.jprs.jp\n"))

    def test_parse_server_ignores_key_not_at_line_start(self):
        self.assertIsNone(domain_expiry.parse_server("remarks:      whois: whois.example.net\n"))


class QueryStringTest(unittest.TestCase):
    def test_query_string_adds_english_suffix_for_jp(self):
        self.assertEqual(domain_expiry.query_string("foo.jp"), "foo.jp/e")

    def test_query_string_adds_english_suffix_for_second_level_jp(self):
        self.assertEqual(domain_expiry.query_string("foo.co.jp"), "foo.co.jp/e")

    def test_query_string_is_plain_domain_for_gtld(self):
        self.assertEqual(domain_expiry.query_string("foo.net"), "foo.net")

    def test_query_string_does_not_add_suffix_when_jp_is_not_the_ending(self):
        self.assertEqual(domain_expiry.query_string("foo.jp.example"), "foo.jp.example")


class ParseExpiryTest(unittest.TestCase):
    def test_parse_expiry_reads_registry_expiry_date_with_notice_after_it(self):
        self.assertEqual(domain_expiry.parse_expiry(VERISIGN_TEXT), date(2027, 3, 15))

    def test_parse_expiry_ignores_notice_line_mentioning_expiration_date(self):
        notice = "NOTICE: The expiration date displayed in this record is the date the\n"
        self.assertIsNone(domain_expiry.parse_expiry(notice))

    def test_parse_expiry_reads_registrar_registration_expiration_date(self):
        self.assertEqual(
            domain_expiry.parse_expiry("Registrar Registration Expiration Date: 2027-04-26T10:47:13Z\n"),
            date(2027, 4, 26),
        )

    def test_parse_expiry_reads_jprs_expires_on_format(self):
        self.assertEqual(
            domain_expiry.parse_expiry("[Expires on]                    2027/02/28\n"),
            date(2027, 2, 28),
        )

    def test_parse_expiry_reads_jp_attribute_format(self):
        self.assertEqual(
            domain_expiry.parse_expiry("[State]                         Connected (2027/01/31)\n"),
            date(2027, 1, 31),
        )

    def test_parse_expiry_uses_first_matching_line(self):
        registry_first = (
            "   Registry Expiry Date: 2027-03-15T04:00:00Z\n"
            "Registrar Registration Expiration Date: 2027-04-26T10:47:13Z\n"
        )
        registrar_first = (
            "Registrar Registration Expiration Date: 2027-04-26T10:47:13Z\n"
            "   Registry Expiry Date: 2027-03-15T04:00:00Z\n"
        )
        self.assertEqual(domain_expiry.parse_expiry(registry_first), date(2027, 3, 15))
        self.assertEqual(domain_expiry.parse_expiry(registrar_first), date(2027, 4, 26))

    def test_parse_expiry_returns_none_when_no_expiry_line(self):
        self.assertIsNone(domain_expiry.parse_expiry("Domain Name: EXAMPLE.NET\n"))
        self.assertIsNone(domain_expiry.parse_expiry(""))

    def test_parse_expiry_returns_none_for_invalid_calendar_date(self):
        for text in (
            "   Registry Expiry Date: 2027-02-30T04:00:00Z\n",
            "[Expires on]                    2027/02/30\n",
            "[State]                         Connected (2027/02/30)\n",
        ):
            with self.subTest(text=text):
                self.assertIsNone(domain_expiry.parse_expiry(text))


class LevelTest(unittest.TestCase):
    def test_level_none_above_warn_days(self):
        self.assertIsNone(domain_expiry.level(61, 60, 14))

    def test_level_warn_at_warn_days(self):
        self.assertEqual(domain_expiry.level(60, 60, 14), "warn")

    def test_level_warn_just_above_crit_days(self):
        self.assertEqual(domain_expiry.level(15, 60, 14), "warn")

    def test_level_crit_at_crit_days(self):
        self.assertEqual(domain_expiry.level(14, 60, 14), "crit")

    def test_level_crit_below_crit_days(self):
        self.assertEqual(domain_expiry.level(13, 60, 14), "crit")
        self.assertEqual(domain_expiry.level(0, 60, 14), "crit")

    def test_level_crit_for_negative_days(self):
        self.assertEqual(domain_expiry.level(-1, 60, 14), "crit")
        self.assertEqual(domain_expiry.level(-365, 60, 14), "crit")

    def test_level_has_no_warn_band_when_warn_equals_crit(self):
        self.assertEqual(domain_expiry.level(14, 14, 14), "crit")
        self.assertIsNone(domain_expiry.level(15, 14, 14))

    def test_level_zero_thresholds(self):
        self.assertEqual(domain_expiry.level(0, 0, 0), "crit")
        self.assertIsNone(domain_expiry.level(1, 0, 0))


class ExpiryBodyTest(unittest.TestCase):
    def test_expiry_body_future_uses_days_and_date(self):
        self.assertEqual(
            domain_expiry.expiry_body("example.net", 10, EXPIRY),
            "example.netのドメインの期限まであと10日（4月26日）。",
        )

    def test_expiry_body_date_is_not_zero_padded(self):
        self.assertEqual(
            domain_expiry.expiry_body("example.net", 1, date(2027, 2, 5)),
            "example.netのドメインの期限まであと1日（2月5日）。",
        )

    def test_expiry_body_shows_month_and_day_of_expiry(self):
        self.assertEqual(
            domain_expiry.expiry_body("example.net", 3, date(2027, 12, 31)),
            "example.netのドメインの期限まであと3日（12月31日）。",
        )

    def test_expiry_body_today(self):
        self.assertEqual(
            domain_expiry.expiry_body("example.net", 0, EXPIRY),
            "example.netのドメインの期限は今日（4月26日）。",
        )

    def test_expiry_body_expired(self):
        self.assertEqual(
            domain_expiry.expiry_body("example.net", -1, EXPIRY),
            "example.netのドメインが期限切れ。",
        )

    def test_expiry_body_uses_domain_as_given(self):
        self.assertEqual(
            domain_expiry.expiry_body("foo.co.jp", 3, EXPIRY),
            "foo.co.jpのドメインの期限まであと3日（4月26日）。",
        )


class MakeTopicTest(unittest.TestCase):
    def test_make_topic_warn_returns_expected_dict(self):
        self.assertEqual(
            domain_expiry.make_topic("example.net", "warn", "本文です。", NOW),
            {
                "kind": "system",
                "ref": "domain:example.net:warn:2026-11-14",
                "body": "本文です。",
                "priority": 1,
                "expires_at": "2026-11-14T23:59:59+09:00",
            },
        )

    def test_make_topic_crit_has_priority_3(self):
        topic = domain_expiry.make_topic("example.net", "crit", "本文です。", NOW)
        self.assertEqual(topic["priority"], 3)
        self.assertEqual(topic["ref"], "domain:example.net:crit:2026-11-14")

    def test_make_topic_ref_uses_domain_verbatim(self):
        topic = domain_expiry.make_topic("foo.co.jp", "crit", "本文です。", NOW)
        self.assertEqual(topic["ref"], "domain:foo.co.jp:crit:2026-11-14")

    def test_make_topic_body_is_passed_through(self):
        topic = domain_expiry.make_topic("example.net", "warn", "期限まであと20日", NOW)
        self.assertEqual(topic["body"], "期限まであと20日")

    def test_make_topic_expires_at_is_end_of_day_in_same_timezone(self):
        now_utc = datetime(2026, 11, 14, 0, 0, 0, tzinfo=UTC)
        topic = domain_expiry.make_topic("example.net", "warn", "本文です。", now_utc)
        self.assertEqual(topic["ref"], "domain:example.net:warn:2026-11-14")
        self.assertEqual(topic["expires_at"], "2026-11-14T23:59:59+00:00")

    def test_make_topic_expires_at_drops_microseconds(self):
        now = datetime(2026, 11, 14, 23, 59, 59, 999999, tzinfo=JST)
        topic = domain_expiry.make_topic("example.net", "warn", "本文です。", now)
        self.assertEqual(topic["expires_at"], "2026-11-14T23:59:59+09:00")
        self.assertEqual(topic["ref"], "domain:example.net:warn:2026-11-14")

    def test_make_topic_date_is_taken_in_timezone_of_now(self):
        # UTC の 2026-11-12 20:00 は、そのタイムゾーンでは 11/12 のまま（JST にすると 11/13）
        now_utc = datetime(2026, 11, 12, 20, 0, 0, tzinfo=UTC)
        topic = domain_expiry.make_topic("example.net", "warn", "本文です。", now_utc)
        self.assertEqual(topic["ref"], "domain:example.net:warn:2026-11-12")
        self.assertEqual(topic["expires_at"], "2026-11-12T23:59:59+00:00")

    def test_make_topic_has_exactly_the_five_keys(self):
        topic = domain_expiry.make_topic("example.net", "warn", "本文です。", NOW)
        self.assertEqual(set(topic), {"kind", "ref", "body", "priority", "expires_at"})
        self.assertEqual(topic["kind"], "system")


if __name__ == "__main__":
    unittest.main()
