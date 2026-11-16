"""cert_expiry.py の関数の単体テスト（socket / ssl はモックする。ネットワークには接続しない）。"""

import os
import socket
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import cert_expiry  # noqa: E402
from helpers import CONFIG_KEYS, make_verify_error, write_env_file  # noqa: E402

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
NOW = datetime(2026, 11, 14, 10, 30, 0, tzinfo=JST)
# UTC 2026-11-23 20:00 は JST では 11月24日 05:00
EXPIRY_UTC = datetime(2026, 11, 23, 20, 0, 0, tzinfo=UTC)
NOT_AFTER = "Nov 24 12:00:00 2026 GMT"


def timeout_of(call):
    """create_connection に渡された timeout（キーワードでも位置引数でも可）。"""
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
        self.isolate_env({"CERT_HOSTS": "a.example", "CERT_WARN_DAYS": "30"})
        self.assertEqual(
            cert_expiry.load_config(self.env_path),
            {"CERT_HOSTS": "a.example", "CERT_WARN_DAYS": "30"},
        )

    def test_load_config_returns_empty_dict_when_nothing_is_set(self):
        self.isolate_env()
        self.assertEqual(cert_expiry.load_config(self.env_path), {})

    def test_load_config_reads_values_from_env_file(self):
        self.isolate_env()
        write_env_file(self.env_path, {"CERT_HOSTS": "a.example,b.example:993", "CERT_TIMEOUT": "5"})
        self.assertEqual(
            cert_expiry.load_config(self.env_path),
            {"CERT_HOSTS": "a.example,b.example:993", "CERT_TIMEOUT": "5"},
        )

    def test_load_config_skips_comment_and_blank_lines_and_lines_without_equal_sign(self):
        self.isolate_env()
        self.write_raw("# comment line\n\nCERT_HOSTS=a.example\n   \n# CERT_WARN_DAYS=1\nnot_a_setting\n")
        self.assertEqual(cert_expiry.load_config(self.env_path), {"CERT_HOSTS": "a.example"})

    def test_load_config_strips_surrounding_whitespace_of_key_and_value(self):
        self.isolate_env()
        self.write_raw("  CERT_TIMEOUT  =   5  \n")
        self.assertEqual(cert_expiry.load_config(self.env_path), {"CERT_TIMEOUT": "5"})

    def test_load_config_keeps_equal_sign_inside_value(self):
        self.isolate_env()
        self.write_raw("CERT_HOSTS=a=b\n")
        self.assertEqual(cert_expiry.load_config(self.env_path), {"CERT_HOSTS": "a=b"})

    def test_load_config_environment_overrides_env_file(self):
        self.isolate_env({"CERT_WARN_DAYS": "14"})
        write_env_file(self.env_path, {"CERT_WARN_DAYS": "28"})
        self.assertEqual(cert_expiry.load_config(self.env_path), {"CERT_WARN_DAYS": "14"})

    def test_load_config_empty_environment_value_falls_back_to_env_file(self):
        self.isolate_env({"CERT_WARN_DAYS": ""})
        write_env_file(self.env_path, {"CERT_WARN_DAYS": "28"})
        self.assertEqual(cert_expiry.load_config(self.env_path), {"CERT_WARN_DAYS": "28"})

    def test_load_config_empty_environment_value_without_env_file_is_omitted(self):
        self.isolate_env({"CERT_HOSTS": ""})
        self.assertEqual(cert_expiry.load_config(self.env_path), {})


class ParseHostsTest(unittest.TestCase):
    def test_parse_hosts_uses_443_when_port_is_omitted(self):
        self.assertEqual(cert_expiry.parse_hosts("a.example"), [("a.example", 443)])

    def test_parse_hosts_reads_explicit_port(self):
        self.assertEqual(cert_expiry.parse_hosts("b.example:993"), [("b.example", 993)])

    def test_parse_hosts_keeps_order(self):
        self.assertEqual(
            cert_expiry.parse_hosts("c.example,a.example:8443,b.example"),
            [("c.example", 443), ("a.example", 8443), ("b.example", 443)],
        )

    def test_parse_hosts_strips_surrounding_whitespace_of_elements(self):
        self.assertEqual(
            cert_expiry.parse_hosts(" a.example:993 , b.example "),
            [("a.example", 993), ("b.example", 443)],
        )

    def test_parse_hosts_drops_empty_elements(self):
        self.assertEqual(
            cert_expiry.parse_hosts("a.example,,  ,b.example,"),
            [("a.example", 443), ("b.example", 443)],
        )

    def test_parse_hosts_empty_text_returns_empty_list(self):
        self.assertEqual(cert_expiry.parse_hosts(""), [])
        self.assertEqual(cert_expiry.parse_hosts("  ,  "), [])

    def test_parse_hosts_removes_duplicates_keeping_first(self):
        # "b.example" と "b.example:443" は同じ (host, port) なので重複
        self.assertEqual(
            cert_expiry.parse_hosts("b.example,a.example,b.example:443,a.example:993,a.example:993"),
            [("b.example", 443), ("a.example", 443), ("a.example", 993)],
        )

    def test_parse_hosts_accepts_port_bounds(self):
        self.assertEqual(
            cert_expiry.parse_hosts("a.example:1,b.example:65535"),
            [("a.example", 1), ("b.example", 65535)],
        )

    def test_parse_hosts_rejects_port_out_of_range(self):
        for value in ("a.example:0", "a.example:65536", "a.example:-1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cert_expiry.parse_hosts(value)

    def test_parse_hosts_rejects_non_integer_port(self):
        for value in ("a.example:abc", "a.example:443.0"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cert_expiry.parse_hosts(value)


class ParseSettingsTest(unittest.TestCase):
    def test_parse_settings_uses_defaults_for_empty_config(self):
        warn_days, crit_days, timeout = cert_expiry.parse_settings({})
        self.assertEqual((warn_days, crit_days, timeout), (28, 7, 10.0))
        self.assertIsInstance(warn_days, int)
        self.assertIsInstance(crit_days, int)
        self.assertIsInstance(timeout, float)

    def test_parse_settings_uses_given_values(self):
        self.assertEqual(
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "30", "CERT_CRIT_DAYS": "14", "CERT_TIMEOUT": "5.5"}),
            (30, 14, 5.5),
        )

    def test_parse_settings_empty_string_uses_default_per_key(self):
        config = {key: "" for key in CONFIG_KEYS}
        self.assertEqual(cert_expiry.parse_settings(config), (28, 7, 10.0))

    def test_parse_settings_fills_missing_keys_individually(self):
        self.assertEqual(cert_expiry.parse_settings({"CERT_WARN_DAYS": "14"}), (14, 7, 10.0))

    def test_parse_settings_allows_equal_warn_and_crit(self):
        self.assertEqual(
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "7", "CERT_CRIT_DAYS": "7"}),
            (7, 7, 10.0),
        )

    def test_parse_settings_allows_zero_days(self):
        self.assertEqual(
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "0", "CERT_CRIT_DAYS": "0"}),
            (0, 0, 10.0),
        )

    def test_parse_settings_accepts_fractional_timeout(self):
        self.assertEqual(cert_expiry.parse_settings({"CERT_TIMEOUT": "0.5"})[2], 0.5)

    def test_parse_settings_rejects_non_numeric_days(self):
        for value in ("abc", "1.5"):
            for key in ("CERT_WARN_DAYS", "CERT_CRIT_DAYS"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        cert_expiry.parse_settings({key: value})

    def test_parse_settings_rejects_negative_days(self):
        with self.assertRaises(ValueError):
            cert_expiry.parse_settings({"CERT_CRIT_DAYS": "-1"})
        with self.assertRaises(ValueError):
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "-1", "CERT_CRIT_DAYS": "-2"})

    def test_parse_settings_rejects_timeout_not_positive_or_non_numeric(self):
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cert_expiry.parse_settings({"CERT_TIMEOUT": value})

    def test_parse_settings_rejects_warn_below_crit(self):
        # crit は未設定なので既定値 7 と比べる
        with self.assertRaises(ValueError):
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "6"})
        with self.assertRaises(ValueError):
            cert_expiry.parse_settings({"CERT_WARN_DAYS": "3", "CERT_CRIT_DAYS": "7"})


class DisplayNameTest(unittest.TestCase):
    def test_display_name_is_host_for_443(self):
        self.assertEqual(cert_expiry.display_name("a.example", 443), "a.example")

    def test_display_name_includes_port_otherwise(self):
        self.assertEqual(cert_expiry.display_name("a.example", 993), "a.example:993")
        self.assertEqual(cert_expiry.display_name("a.example", 80), "a.example:80")
        self.assertEqual(cert_expiry.display_name("a.example", 8443), "a.example:8443")


class FetchExpiryTest(unittest.TestCase):
    def patch_tls(self, peer_cert=None, connect_error=None, handshake_error=None):
        """socket.create_connection と ssl.create_default_context をモックする。
        戻り値は (create_connection, wrap_socket) のモック。"""
        sock = mock.MagicMock(name="sock")
        sock.__enter__.return_value = sock
        ssock = mock.MagicMock(name="ssock")
        ssock.__enter__.return_value = ssock
        ssock.getpeercert.return_value = peer_cert
        context = mock.MagicMock(name="context")
        context.wrap_socket.return_value = ssock
        context.wrap_socket.side_effect = handshake_error
        create_connection = mock.Mock(return_value=sock, side_effect=connect_error)
        for target, new in (
            ("socket.create_connection", create_connection),
            ("ssl.create_default_context", mock.Mock(return_value=context)),
        ):
            patcher = mock.patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)
        return create_connection, context.wrap_socket

    def test_fetch_expiry_returns_not_after_as_utc_datetime(self):
        self.patch_tls(peer_cert={"notAfter": NOT_AFTER})
        result = cert_expiry.fetch_expiry("a.example", 443, 10)
        self.assertEqual(result, datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
        self.assertEqual(result.utcoffset(), timedelta(0))

    def test_fetch_expiry_connects_to_host_and_port_with_timeout(self):
        create_connection, _ = self.patch_tls(peer_cert={"notAfter": NOT_AFTER})
        cert_expiry.fetch_expiry("a.example", 993, 7.5)
        self.assertEqual(create_connection.call_args.args[0], ("a.example", 993))
        self.assertEqual(timeout_of(create_connection.call_args), 7.5)

    def test_fetch_expiry_wraps_socket_with_server_hostname(self):
        _, wrap_socket = self.patch_tls(peer_cert={"notAfter": NOT_AFTER})
        cert_expiry.fetch_expiry("a.example", 443, 10)
        self.assertEqual(wrap_socket.call_args.kwargs.get("server_hostname"), "a.example")

    def test_fetch_expiry_reraises_certificate_verification_error_unchanged(self):
        error = make_verify_error(20, "unable to get local issuer certificate")
        self.patch_tls(handshake_error=error)
        with self.assertRaises(ssl.SSLCertVerificationError) as cm:
            cert_expiry.fetch_expiry("a.example", 443, 10)
        self.assertIs(cm.exception, error)

    def test_fetch_expiry_reraises_oserror_unchanged(self):
        error = OSError("connection refused")
        self.patch_tls(connect_error=error)
        with self.assertRaises(OSError) as cm:
            cert_expiry.fetch_expiry("a.example", 443, 10)
        self.assertIs(cm.exception, error)

    def test_fetch_expiry_reraises_timeout_unchanged(self):
        error = socket.timeout("timed out")
        self.patch_tls(connect_error=error)
        with self.assertRaises(socket.timeout) as cm:
            cert_expiry.fetch_expiry("a.example", 443, 10)
        self.assertIs(cm.exception, error)


class DaysLeftTest(unittest.TestCase):
    def test_days_left_whole_days(self):
        self.assertEqual(cert_expiry.days_left(NOW + timedelta(days=5), NOW), 5)

    def test_days_left_truncates_partial_day(self):
        self.assertEqual(cert_expiry.days_left(NOW + timedelta(days=1, hours=23), NOW), 1)

    def test_days_left_just_under_one_day_is_zero(self):
        self.assertEqual(cert_expiry.days_left(NOW + timedelta(seconds=86399), NOW), 0)

    def test_days_left_is_zero_when_expiry_equals_now(self):
        self.assertEqual(cert_expiry.days_left(NOW, NOW), 0)

    def test_days_left_is_zero_for_hours_before_expiry(self):
        self.assertEqual(cert_expiry.days_left(NOW + timedelta(hours=5), NOW), 0)

    def test_days_left_negative_partial_day_is_minus_one(self):
        # 残り -1 時間 → -1
        self.assertEqual(cert_expiry.days_left(NOW - timedelta(hours=1), NOW), -1)

    def test_days_left_negative_whole_days(self):
        self.assertEqual(cert_expiry.days_left(NOW - timedelta(days=2), NOW), -2)

    def test_days_left_negative_rounds_down_not_toward_zero(self):
        # -(1日+1秒) は floor で -2
        self.assertEqual(cert_expiry.days_left(NOW - timedelta(days=1, seconds=1), NOW), -2)

    def test_days_left_across_timezones_uses_absolute_time(self):
        # NOW は UTC 01:30 と同じ。期限は UTC 12:00 なので 10.5 時間後 → 0
        self.assertEqual(cert_expiry.days_left(datetime(2026, 11, 14, 12, 0, tzinfo=UTC), NOW), 0)

    def test_days_left_across_timezones_is_not_wall_clock_difference(self):
        # 期限は JST 11/15 00:00 = UTC 11/14 15:00、now は UTC 11/14 16:00 → 1時間前なので -1
        # 壁時計の差（+8時間）で計算すると誤って 0 になる
        expiry = datetime(2026, 11, 15, 0, 0, tzinfo=JST)
        now = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
        self.assertEqual(cert_expiry.days_left(expiry, now), -1)

    def test_days_left_expiry_in_other_timezone_one_day_and_some_hours_ahead(self):
        # UTC 11/15 08:00 = JST 11/15 17:00。NOW から 1日6.5時間後 → 1
        self.assertEqual(cert_expiry.days_left(datetime(2026, 11, 15, 8, 0, tzinfo=UTC), NOW), 1)


class LevelTest(unittest.TestCase):
    def test_level_none_above_warn_days(self):
        self.assertIsNone(cert_expiry.level(29, 28, 7))

    def test_level_warn_at_warn_days(self):
        self.assertEqual(cert_expiry.level(28, 28, 7), "warn")

    def test_level_warn_just_above_crit_days(self):
        self.assertEqual(cert_expiry.level(8, 28, 7), "warn")

    def test_level_crit_at_crit_days(self):
        self.assertEqual(cert_expiry.level(7, 28, 7), "crit")

    def test_level_crit_below_crit_days(self):
        self.assertEqual(cert_expiry.level(6, 28, 7), "crit")
        self.assertEqual(cert_expiry.level(0, 28, 7), "crit")

    def test_level_crit_for_negative_days(self):
        self.assertEqual(cert_expiry.level(-1, 28, 7), "crit")
        self.assertEqual(cert_expiry.level(-365, 28, 7), "crit")

    def test_level_has_no_warn_band_when_warn_equals_crit(self):
        self.assertEqual(cert_expiry.level(7, 7, 7), "crit")
        self.assertIsNone(cert_expiry.level(8, 7, 7))

    def test_level_zero_thresholds(self):
        self.assertEqual(cert_expiry.level(0, 0, 0), "crit")
        self.assertIsNone(cert_expiry.level(1, 0, 0))


class ExpiryBodyTest(unittest.TestCase):
    def test_expiry_body_future_uses_days_and_date(self):
        self.assertEqual(
            cert_expiry.expiry_body("a.example", 10, EXPIRY_UTC, JST),
            "a.exampleの証明書の期限まであと10日（11月24日）。",
        )

    def test_expiry_body_date_is_taken_after_timezone_conversion(self):
        self.assertEqual(
            cert_expiry.expiry_body("a.example", 9, EXPIRY_UTC, UTC),
            "a.exampleの証明書の期限まであと9日（11月23日）。",
        )

    def test_expiry_body_date_is_not_zero_padded(self):
        expiry = datetime(2026, 3, 5, 9, 0, tzinfo=JST)
        self.assertEqual(
            cert_expiry.expiry_body("a.example", 1, expiry, JST),
            "a.exampleの証明書の期限まであと1日（3月5日）。",
        )

    def test_expiry_body_today(self):
        self.assertEqual(
            cert_expiry.expiry_body("a.example", 0, EXPIRY_UTC, JST),
            "a.exampleの証明書の期限は今日（11月24日）。",
        )

    def test_expiry_body_expired(self):
        self.assertEqual(
            cert_expiry.expiry_body("a.example", -1, EXPIRY_UTC, JST),
            "a.exampleの証明書が期限切れ。",
        )

    def test_expiry_body_uses_name_as_given(self):
        self.assertEqual(
            cert_expiry.expiry_body("a.example:8443", 3, EXPIRY_UTC, JST),
            "a.example:8443の証明書の期限まであと3日（11月24日）。",
        )


class VerifyErrorBodyTest(unittest.TestCase):
    def test_verify_error_body_code_10_means_expired(self):
        error = make_verify_error(10, "certificate has expired")
        self.assertEqual(
            cert_expiry.verify_error_body("a.example", error),
            "a.exampleの証明書が期限切れ。",
        )

    def test_verify_error_body_other_code_uses_verify_message(self):
        error = make_verify_error(20, "unable to get local issuer certificate")
        self.assertEqual(
            cert_expiry.verify_error_body("a.example", error),
            "a.exampleの証明書の検証に失敗（unable to get local issuer certificate）。",
        )

    def test_verify_error_body_hostname_mismatch_uses_verify_message(self):
        message = "Hostname mismatch, certificate is not valid for 'other.example'."
        error = make_verify_error(62, message)
        self.assertEqual(
            cert_expiry.verify_error_body("a.example", error),
            f"a.exampleの証明書の検証に失敗（{message}）。",
        )

    def test_verify_error_body_uses_name_as_given(self):
        error = make_verify_error(20, "unable to get local issuer certificate")
        self.assertEqual(
            cert_expiry.verify_error_body("a.example:8443", error),
            "a.example:8443の証明書の検証に失敗（unable to get local issuer certificate）。",
        )


class MakeTopicTest(unittest.TestCase):
    def test_make_topic_warn_returns_expected_dict(self):
        self.assertEqual(
            cert_expiry.make_topic("a.example", "warn", "本文です。", NOW),
            {
                "kind": "system",
                "ref": "cert:a.example:warn:2026-11-14",
                "body": "本文です。",
                "priority": 1,
                "expires_at": "2026-11-14T23:59:59+09:00",
            },
        )

    def test_make_topic_crit_has_priority_3(self):
        topic = cert_expiry.make_topic("a.example", "crit", "本文です。", NOW)
        self.assertEqual(topic["priority"], 3)
        self.assertEqual(topic["ref"], "cert:a.example:crit:2026-11-14")

    def test_make_topic_ref_uses_display_name_verbatim(self):
        topic = cert_expiry.make_topic("a.example:8443", "crit", "本文です。", NOW)
        self.assertEqual(topic["ref"], "cert:a.example:8443:crit:2026-11-14")

    def test_make_topic_body_is_passed_through(self):
        topic = cert_expiry.make_topic("a.example", "warn", "期限まであと20日", NOW)
        self.assertEqual(topic["body"], "期限まであと20日")

    def test_make_topic_expires_at_is_end_of_day_in_same_timezone(self):
        now_utc = datetime(2026, 11, 14, 0, 0, 0, tzinfo=UTC)
        topic = cert_expiry.make_topic("a.example", "warn", "本文です。", now_utc)
        self.assertEqual(topic["ref"], "cert:a.example:warn:2026-11-14")
        self.assertEqual(topic["expires_at"], "2026-11-14T23:59:59+00:00")

    def test_make_topic_expires_at_drops_microseconds(self):
        now = datetime(2026, 11, 14, 23, 59, 59, 999999, tzinfo=JST)
        topic = cert_expiry.make_topic("a.example", "warn", "本文です。", now)
        self.assertEqual(topic["expires_at"], "2026-11-14T23:59:59+09:00")
        self.assertEqual(topic["ref"], "cert:a.example:warn:2026-11-14")

    def test_make_topic_date_is_taken_in_timezone_of_now(self):
        # UTC の 2026-11-12 20:00 は、そのタイムゾーンでは 11/12 のまま
        now_utc = datetime(2026, 11, 12, 20, 0, 0, tzinfo=UTC)
        topic = cert_expiry.make_topic("a.example", "warn", "本文です。", now_utc)
        self.assertEqual(topic["ref"], "cert:a.example:warn:2026-11-12")
        self.assertEqual(topic["expires_at"], "2026-11-12T23:59:59+00:00")

    def test_make_topic_has_exactly_the_five_keys(self):
        topic = cert_expiry.make_topic("a.example", "warn", "本文です。", NOW)
        self.assertEqual(set(topic), {"kind", "ref", "body", "priority", "expires_at"})
        self.assertEqual(topic["kind"], "system")


if __name__ == "__main__":
    unittest.main()
