"""log_scan.py の関数単体テスト。"""
import re
import unittest
from datetime import date

import log_scan
from helpers import TempDirTestCase, bytes_list, jline

Finding = log_scan.Finding
DAY = date(2026, 11, 14)
DAY_TEXT = "2026-11-14"


def entry(**fields):
    """journal の1エントリ（dict）。"""
    return dict(fields)


class ReadCursorTest(TempDirTestCase):
    def test_read_cursor_missing_file_returns_none(self):
        self.assertIsNone(log_scan.read_cursor(self.dir / "cursor.txt"))

    def test_read_cursor_empty_file_returns_none(self):
        path = self.dir / "cursor.txt"
        path.write_text("", encoding="utf-8")
        self.assertIsNone(log_scan.read_cursor(path))

    def test_read_cursor_whitespace_only_file_returns_none(self):
        path = self.dir / "cursor.txt"
        path.write_text("  \n\t\n", encoding="utf-8")
        self.assertIsNone(log_scan.read_cursor(path))

    def test_read_cursor_strips_surrounding_whitespace(self):
        path = self.dir / "cursor.txt"
        path.write_text("  s=abc;i=1\n", encoding="utf-8")
        self.assertEqual(log_scan.read_cursor(path), "s=abc;i=1")


class WriteCursorTest(TempDirTestCase):
    def test_write_cursor_can_be_read_back(self):
        path = self.dir / "cursor.txt"
        log_scan.write_cursor(path, "s=0123;i=abc;b=def")
        self.assertEqual(log_scan.read_cursor(path), "s=0123;i=abc;b=def")

    def test_write_cursor_overwrites_previous_value(self):
        path = self.dir / "cursor.txt"
        log_scan.write_cursor(path, "old")
        log_scan.write_cursor(path, "new")
        self.assertEqual(log_scan.read_cursor(path), "new")

    def test_write_cursor_writes_utf8(self):
        path = self.dir / "cursor.txt"
        cursor = "s=ab;x=テスト"
        log_scan.write_cursor(path, cursor)
        self.assertEqual(path.read_bytes().decode("utf-8").strip(), cursor)


class LoadIgnoreTest(TempDirTestCase):
    def write_ignore(self, text):
        path = self.dir / "ignore.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_load_ignore_missing_file_returns_empty_list(self):
        self.assertEqual(log_scan.load_ignore(self.dir / "ignore.txt"), [])

    def test_load_ignore_returns_compiled_patterns_in_order(self):
        path = self.write_ignore("sshd: .*timeout\nnginx:\n")
        patterns = log_scan.load_ignore(path)
        self.assertEqual(len(patterns), 2)
        for pattern in patterns:
            self.assertIsInstance(pattern, re.Pattern)
        self.assertEqual([p.pattern for p in patterns], ["sshd: .*timeout", "nginx:"])

    def test_load_ignore_strips_surrounding_whitespace(self):
        path = self.write_ignore("  ^gdm:  \n")
        patterns = log_scan.load_ignore(path)
        self.assertEqual([p.pattern for p in patterns], ["^gdm:"])

    def test_load_ignore_skips_empty_and_blank_lines(self):
        path = self.write_ignore("\n   \n^a$\n\n")
        patterns = log_scan.load_ignore(path)
        self.assertEqual([p.pattern for p in patterns], ["^a$"])

    def test_load_ignore_skips_comment_lines(self):
        path = self.write_ignore("# comment\n^a$\n# ^b$\n")
        patterns = log_scan.load_ignore(path)
        self.assertEqual([p.pattern for p in patterns], ["^a$"])

    def test_load_ignore_only_comments_returns_empty_list(self):
        path = self.write_ignore("# a\n# b\n")
        self.assertEqual(log_scan.load_ignore(path), [])

    def test_load_ignore_keeps_hash_inside_line(self):
        path = self.write_ignore("a#b\n")
        patterns = log_scan.load_ignore(path)
        self.assertEqual([p.pattern for p in patterns], ["a#b"])

    def test_load_ignore_invalid_regex_raises_re_error(self):
        path = self.write_ignore("ok\n(\n")
        with self.assertRaises(re.error):
            log_scan.load_ignore(path)

    def test_load_ignore_unclosed_bracket_raises_re_error(self):
        path = self.write_ignore("[abc\n")
        with self.assertRaises(re.error):
            log_scan.load_ignore(path)


class JournalArgsTest(unittest.TestCase):
    def test_journal_args_without_cursor_uses_24_hours(self):
        self.assertEqual(
            log_scan.journal_args(None),
            ["journalctl", "-o", "json", "--no-pager", "--since", "24 hours ago"],
        )

    def test_journal_args_with_cursor_uses_after_cursor_verbatim(self):
        cursor = "s=0a1b;i=2c;b=3d;x=4e"
        self.assertEqual(
            log_scan.journal_args(cursor),
            ["journalctl", "-o", "json", "--no-pager", "--after-cursor", cursor],
        )


class ParseEntriesTest(unittest.TestCase):
    def test_parse_entries_returns_iterator(self):
        result = log_scan.parse_entries([])
        self.assertIs(iter(result), result)

    def test_parse_entries_yields_dicts_in_order(self):
        lines = [jline(MESSAGE="a", PRIORITY="3"), jline(MESSAGE="b")]
        self.assertEqual(
            list(log_scan.parse_entries(lines)),
            [{"MESSAGE": "a", "PRIORITY": "3"}, {"MESSAGE": "b"}],
        )

    def test_parse_entries_accepts_line_with_trailing_newline(self):
        lines = [jline(MESSAGE="x") + "\n"]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "x"}])

    def test_parse_entries_skips_empty_and_blank_lines(self):
        lines = ["", "  \n", jline(MESSAGE="x")]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "x"}])

    def test_parse_entries_skips_non_json_lines(self):
        lines = ["not json", "{broken", jline(MESSAGE="ok")]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "ok"}])

    def test_parse_entries_skips_json_values_that_are_not_dicts(self):
        lines = ["[1,2]", "42", '"text"', "null", "true", jline(MESSAGE="ok")]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "ok"}])

    def test_parse_entries_decodes_int_list_message_as_utf8(self):
        lines = [jline(MESSAGE=bytes_list("こんにちは"))]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "こんにちは"}])

    def test_parse_entries_replaces_invalid_utf8_bytes(self):
        lines = [jline(MESSAGE=[0xFF, 0x41])]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "�A"}])

    def test_parse_entries_keeps_string_message_unchanged(self):
        lines = [jline(MESSAGE="plain text")]
        self.assertEqual(list(log_scan.parse_entries(lines)), [{"MESSAGE": "plain text"}])

    def test_parse_entries_converts_only_message_field(self):
        lines = [jline(MESSAGE=bytes_list("x"), _PID=[1, 2])]
        self.assertEqual(
            list(log_scan.parse_entries(lines)),
            [{"MESSAGE": "x", "_PID": [1, 2]}],
        )


class ClassifyIoTest(unittest.TestCase):
    def test_classify_io_device_from_dev_prefix_buffer_io_error(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="Buffer I/O error on dev sdb1, logical block 0")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "sdb1"))

    def test_classify_io_device_from_blk_update_request(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="blk_update_request: I/O error, dev sdc, sector 123")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "sdc"))

    def test_classify_io_device_name_may_contain_hyphen(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="I/O error, dev dm-0, sector 5")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "dm-0"))

    def test_classify_io_device_fallback_nvme(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="I/O error on nvme0n1")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "nvme0n1"))

    def test_classify_io_device_fallback_vd(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="I/O error on vda")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "vda"))

    def test_classify_io_device_fallback_mmcblk(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="mmcblk0: I/O error")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "mmcblk0"))

    def test_classify_io_unknown_device_label(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="I/O error")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "不明"))

    def test_classify_io_matches_io_error_case_insensitively(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="buffer i/o error on dev sde")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "sde"))

    def test_classify_io_requires_kernel_transport(self):
        e = entry(_TRANSPORT="journal", MESSAGE="I/O error on dev sdb")
        self.assertIsNone(log_scan.classify(e, []))

    def test_classify_io_is_not_suppressed_by_ignore(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="I/O error on dev sdb")
        self.assertEqual(log_scan.classify(e, [re.compile("I/O error")]), Finding("io", "sdb"))

    def test_classify_io_has_priority_over_err_even_with_priority_3(self):
        e = entry(_TRANSPORT="kernel", PRIORITY="3", SYSLOG_IDENTIFIER="kernel",
                  MESSAGE="I/O error on dev sdb")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "sdb"))

    def test_classify_io_has_priority_over_oom(self):
        e = entry(_TRANSPORT="kernel",
                  MESSAGE="Out of memory: Killed process 1 (x) I/O error on dev sdb")
        self.assertEqual(log_scan.classify(e, []), Finding("io", "sdb"))


class ClassifyOomTest(unittest.TestCase):
    def test_classify_oom_kernel_killed_process(self):
        e = entry(_TRANSPORT="kernel",
                  MESSAGE="Out of memory: Killed process 1234 (python3) total-vm:123kB")
        self.assertEqual(log_scan.classify(e, []), Finding("oom", "python3"))

    def test_classify_oom_process_name_may_contain_space(self):
        e = entry(_TRANSPORT="kernel",
                  MESSAGE="Out of memory: Killed process 42 (chrome helper) total-vm:1kB")
        self.assertEqual(log_scan.classify(e, []), Finding("oom", "chrome helper"))

    def test_classify_oom_requires_kernel_transport(self):
        e = entry(_TRANSPORT="journal",
                  MESSAGE="Out of memory: Killed process 1 (python3) total-vm:1kB")
        self.assertIsNone(log_scan.classify(e, []))

    def test_classify_oom_without_killed_process_is_none(self):
        e = entry(_TRANSPORT="kernel", MESSAGE="Out of memory: no candidates")
        self.assertIsNone(log_scan.classify(e, []))

    def test_classify_oom_is_not_suppressed_by_ignore(self):
        e = entry(_TRANSPORT="kernel",
                  MESSAGE="Out of memory: Killed process 1 (python3) total-vm:1kB")
        self.assertEqual(
            log_scan.classify(e, [re.compile("Out of memory")]),
            Finding("oom", "python3"),
        )


class ClassifySystemdOomdTest(unittest.TestCase):
    def test_classify_systemd_oomd_uses_last_slice_component(self):
        message = (
            "Killed /user.slice/user-1000.slice/user@1000.service/app.slice/"
            "app-firefox.scope due to memory pressure for user.slice being 65.00% > 60.00%"
        )
        e = entry(SYSLOG_IDENTIFIER="systemd-oomd", MESSAGE=message)
        self.assertEqual(log_scan.classify(e, []), Finding("oom", "app-firefox.scope"))

    def test_classify_systemd_oomd_single_component_path(self):
        e = entry(SYSLOG_IDENTIFIER="systemd-oomd",
                  MESSAGE="Killed foo.scope due to memory pressure")
        self.assertEqual(log_scan.classify(e, []), Finding("oom", "foo.scope"))

    def test_classify_systemd_oomd_without_killed_message_is_none(self):
        e = entry(SYSLOG_IDENTIFIER="systemd-oomd", MESSAGE="Swap is low")
        self.assertIsNone(log_scan.classify(e, []))

    def test_classify_other_ident_with_killed_message_is_not_oom(self):
        e = entry(SYSLOG_IDENTIFIER="someone",
                  MESSAGE="Killed /user.slice/app-firefox.scope due to memory pressure")
        self.assertIsNone(log_scan.classify(e, []))


class ClassifyErrTest(unittest.TestCase):
    def test_classify_err_uses_syslog_identifier_first(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", _COMM="other", _SYSTEMD_UNIT="x.service",
                  PRIORITY="3", MESSAGE="x")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "gdm"))

    def test_classify_err_falls_back_to_comm(self):
        e = entry(_COMM="nginx", _SYSTEMD_UNIT="x.service", PRIORITY="3", MESSAGE="x")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "nginx"))

    def test_classify_err_falls_back_to_systemd_unit(self):
        e = entry(_SYSTEMD_UNIT="foo.service", PRIORITY="2", MESSAGE="x")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "foo.service"))

    def test_classify_err_unknown_when_no_identifier(self):
        e = entry(PRIORITY="0", MESSAGE="x")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "unknown"))

    def test_classify_err_missing_message_is_treated_as_empty(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="3")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "gdm"))

    def test_classify_err_priority_0_to_3_are_reported(self):
        for priority in ["0", "1", "2", "3"]:
            with self.subTest(priority=priority):
                e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY=priority, MESSAGE="x")
                self.assertEqual(log_scan.classify(e, []), Finding("err", "gdm"))

    def test_classify_err_priority_4_and_above_is_none(self):
        for priority in ["4", "6"]:
            with self.subTest(priority=priority):
                e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY=priority, MESSAGE="x")
                self.assertIsNone(log_scan.classify(e, []))

    def test_classify_err_invalid_priority_is_none(self):
        for priority in ["", "abc", "3.5"]:
            with self.subTest(priority=priority):
                e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY=priority, MESSAGE="x")
                self.assertIsNone(log_scan.classify(e, []))

    def test_classify_err_missing_priority_is_none(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", MESSAGE="x")
        self.assertIsNone(log_scan.classify(e, []))

    def test_classify_err_kernel_entry_without_match_uses_comm(self):
        e = entry(_TRANSPORT="kernel", _COMM="kworker", PRIORITY="3", MESSAGE="something")
        self.assertEqual(log_scan.classify(e, []), Finding("err", "kworker"))

    def test_classify_err_ignored_when_pattern_matches_formatted_text(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="3", MESSAGE="timeout occurred")
        self.assertIsNone(log_scan.classify(e, [re.compile(r"^gdm: .*timeout")]))

    def test_classify_err_ignore_matches_ident_colon_message_format(self):
        e = entry(SYSLOG_IDENTIFIER="sshd", PRIORITY="3", MESSAGE="Failed password for root")
        self.assertIsNone(log_scan.classify(e, [re.compile(r"^sshd: Failed password")]))

    def test_classify_err_ignore_pattern_for_other_ident_does_not_match(self):
        e = entry(SYSLOG_IDENTIFIER="sshd", PRIORITY="3", MESSAGE="Failed password for root")
        self.assertEqual(
            log_scan.classify(e, [re.compile(r"^nginx: ")]),
            Finding("err", "sshd"),
        )

    def test_classify_err_ignore_uses_search_not_fullmatch(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="3", MESSAGE="timeout occurred")
        self.assertIsNone(log_scan.classify(e, [re.compile("timeout")]))

    def test_classify_err_ignored_when_any_pattern_matches(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="3", MESSAGE="timeout occurred")
        ignore = [re.compile("nomatch"), re.compile("occurred")]
        self.assertIsNone(log_scan.classify(e, ignore))

    def test_classify_err_not_ignored_when_no_pattern_matches(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="3", MESSAGE="timeout occurred")
        self.assertEqual(
            log_scan.classify(e, [re.compile("nomatch")]),
            Finding("err", "gdm"),
        )

    def test_classify_returns_none_for_irrelevant_info_entry(self):
        e = entry(SYSLOG_IDENTIFIER="gdm", PRIORITY="6", MESSAGE="started")
        self.assertIsNone(log_scan.classify(e, []))


class ParseFailedUnitsTest(unittest.TestCase):
    def test_parse_failed_units_strips_bullet(self):
        text = "● foo.service loaded failed failed Foo\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["foo.service"])

    def test_parse_failed_units_strips_asterisk(self):
        text = "* bar.service loaded failed failed Bar\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["bar.service"])

    def test_parse_failed_units_without_bullet(self):
        text = "baz.service loaded failed failed Baz\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["baz.service"])

    def test_parse_failed_units_leading_spaces_are_ignored(self):
        text = "   d.service loaded failed failed D\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["d.service"])

    def test_parse_failed_units_keeps_line_order(self):
        text = "● b.service loaded failed failed B\n* a.service loaded failed failed A\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["b.service", "a.service"])

    def test_parse_failed_units_skips_blank_lines(self):
        text = "\n   \n● c.service loaded failed failed C\n\n"
        self.assertEqual(log_scan.parse_failed_units(text), ["c.service"])

    def test_parse_failed_units_empty_text_returns_empty_list(self):
        self.assertEqual(log_scan.parse_failed_units(""), [])

    def test_parse_failed_units_only_blank_lines_returns_empty_list(self):
        self.assertEqual(log_scan.parse_failed_units("  \n\n"), [])


class MakeTopicsTest(unittest.TestCase):
    def test_make_topics_empty_inputs_return_empty_list(self):
        self.assertEqual(log_scan.make_topics([], [], DAY), [])

    def test_make_topics_dict_has_only_four_keys_and_kind_system(self):
        topics = log_scan.make_topics([Finding("io", "sdb1")], [], DAY)
        self.assertEqual(len(topics), 1)
        self.assertEqual(set(topics[0].keys()), {"kind", "ref", "body", "priority"})
        self.assertEqual(topics[0]["kind"], "system")

    def test_make_topics_io_single_device(self):
        topics = log_scan.make_topics([Finding("io", "sdb1")], [], DAY)
        self.assertEqual(topics, [{
            "kind": "system",
            "ref": f"log_io:{DAY_TEXT}",
            "body": "ディスクのI/Oエラーが計1件（sdb1 1件）。",
            "priority": 3,
        }])

    def test_make_topics_io_sorted_by_count_descending(self):
        findings = [Finding("io", "sdd")] + [Finding("io", "sdb")] * 3 + [Finding("io", "sdc")] * 2
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "ディスクのI/Oエラーが計6件（sdb 3件、sdc 2件、sdd 1件）。")

    def test_make_topics_io_tie_keeps_first_seen_order(self):
        findings = [
            Finding("io", "sdc"), Finding("io", "sdb"),
            Finding("io", "sdb"), Finding("io", "sdc"),
            Finding("io", "sdd"),
        ]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "ディスクのI/Oエラーが計5件（sdc 2件、sdb 2件、sdd 1件）。")

    def test_make_topics_io_unknown_device_label(self):
        topics = log_scan.make_topics([Finding("io", "不明")], [], DAY)
        self.assertEqual(topics[0]["body"], "ディスクのI/Oエラーが計1件（不明 1件）。")

    def test_make_topics_io_ignores_other_categories_in_count(self):
        findings = [Finding("io", "sdb"), Finding("oom", "python3"), Finding("err", "gdm")]
        topics = log_scan.make_topics(findings, [], DAY)
        io_topics = [t for t in topics if t["ref"].startswith("log_io:")]
        self.assertEqual(io_topics[0]["body"], "ディスクのI/Oエラーが計1件（sdb 1件）。")

    def test_make_topics_oom_single_process(self):
        topics = log_scan.make_topics([Finding("oom", "python3")], [], DAY)
        self.assertEqual(topics, [{
            "kind": "system",
            "ref": f"log_oom:{DAY_TEXT}",
            "body": "メモリ不足でpython3が強制終了された。",
            "priority": 2,
        }])

    def test_make_topics_oom_two_same_process_uses_count_form(self):
        findings = [Finding("oom", "a"), Finding("oom", "a")]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "メモリ不足でプロセスが計2回強制終了された（a）。")

    def test_make_topics_oom_names_deduplicated_by_count_descending(self):
        findings = [Finding("oom", "a"), Finding("oom", "b"), Finding("oom", "a")]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "メモリ不足でプロセスが計3回強制終了された（a、b）。")

    def test_make_topics_oom_tie_keeps_first_seen_order(self):
        findings = [Finding("oom", "b"), Finding("oom", "a")]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "メモリ不足でプロセスが計2回強制終了された（b、a）。")

    def test_make_topics_unit_body_and_ref(self):
        topics = log_scan.make_topics([], ["foo.service"], DAY)
        self.assertEqual(topics, [{
            "kind": "system",
            "ref": f"unit_failed:foo.service:{DAY_TEXT}",
            "body": "foo.serviceが停止（failed）状態。",
            "priority": 2,
        }])

    def test_make_topics_units_keep_given_order(self):
        topics = log_scan.make_topics([], ["b.service", "a.service"], DAY)
        self.assertEqual(
            [t["ref"] for t in topics],
            [f"unit_failed:b.service:{DAY_TEXT}", f"unit_failed:a.service:{DAY_TEXT}"],
        )

    def test_make_topics_err_single_kind(self):
        topics = log_scan.make_topics([Finding("err", "gdm")], [], DAY)
        self.assertEqual(topics, [{
            "kind": "system",
            "ref": f"log_err:{DAY_TEXT}",
            "body": "エラーログは1種類・計1件。gdm 1件。",
            "priority": 0,
        }])

    def test_make_topics_err_sorted_by_count_descending(self):
        findings = [Finding("err", "gdm"), Finding("err", "gdm"), Finding("err", "sshd")]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "エラーログは2種類・計3件。gdm 2件、sshd 1件。")

    def test_make_topics_err_tie_keeps_first_seen_order(self):
        findings = [Finding("err", "sshd"), Finding("err", "gdm")]
        topics = log_scan.make_topics(findings, [], DAY)
        self.assertEqual(topics[0]["body"], "エラーログは2種類・計2件。sshd 1件、gdm 1件。")

    def test_make_topics_order_is_io_oom_units_err(self):
        findings = [Finding("err", "gdm"), Finding("oom", "python3"), Finding("io", "sdb")]
        topics = log_scan.make_topics(findings, ["u.service"], DAY)
        self.assertEqual(
            [t["ref"] for t in topics],
            [
                f"log_io:{DAY_TEXT}",
                f"log_oom:{DAY_TEXT}",
                f"unit_failed:u.service:{DAY_TEXT}",
                f"log_err:{DAY_TEXT}",
            ],
        )

    def test_make_topics_err_body_exactly_200_chars_is_not_truncated(self):
        prefix = "エラーログは1種類・計1件。"
        suffix = " 1件。"
        name = "x" * (200 - len(prefix) - len(suffix))
        topics = log_scan.make_topics([Finding("err", name)], [], DAY)
        self.assertEqual(topics[0]["body"], prefix + name + suffix)
        self.assertEqual(len(topics[0]["body"]), 200)

    def test_make_topics_err_body_over_200_chars_is_truncated(self):
        prefix = "エラーログは1種類・計1件。"
        suffix = " 1件。"
        name = "x" * (200 - len(prefix) - len(suffix) + 1)
        topics = log_scan.make_topics([Finding("err", name)], [], DAY)
        self.assertLessEqual(len(topics[0]["body"]), 200)
        self.assertEqual(topics[0]["body"], "エラーログは1種類・計1件。ほか1種類。")

    def test_make_topics_err_many_kinds_truncated_with_remaining_top_kinds(self):
        names = [f"program-with-a-long-name-{i:02d}-" + "x" * 20 for i in range(60)]
        findings = []
        for i, name in enumerate(names):
            findings += [Finding("err", name)] * (i + 1)
        topics = log_scan.make_topics(findings, [], DAY)
        body = topics[0]["body"]

        header = "エラーログは60種類・計1830件。"
        self.assertLessEqual(len(body), 200)
        self.assertTrue(body.startswith(header))
        rest = body[len(header):]
        self.assertTrue(rest.endswith("。"))
        parts = rest[:-1].split("、")
        match = re.fullmatch(r"ほか(\d+)種類", parts[-1])
        self.assertIsNotNone(match)
        omitted = int(match.group(1))
        self.assertGreaterEqual(omitted, 1)
        listed = parts[:-1]
        self.assertEqual(
            listed,
            [f"{names[i]} {i + 1}件" for i in range(59, omitted - 1, -1)],
        )

    def test_make_topics_io_many_devices_truncated_with_dai_suffix(self):
        names = [f"device-{'y' * 20}-{i:02d}" for i in range(60)]
        findings = [Finding("io", name) for name in names]
        topics = log_scan.make_topics(findings, [], DAY)
        body = topics[0]["body"]

        header = "ディスクのI/Oエラーが計60件（"
        self.assertLessEqual(len(body), 200)
        self.assertTrue(body.startswith(header))
        rest = body[len(header):]
        self.assertTrue(rest.endswith("）。"))
        parts = rest[:-2].split("、")
        match = re.fullmatch(r"ほか(\d+)台", parts[-1])
        self.assertIsNotNone(match)
        omitted = int(match.group(1))
        self.assertGreaterEqual(omitted, 1)
        self.assertEqual(parts[:-1], [f"{names[i]} 1件" for i in range(60 - omitted)])

    def test_make_topics_oom_many_processes_truncated_with_ken_suffix(self):
        names = [f"proc-{'z' * 20}-{i:02d}" for i in range(60)]
        findings = [Finding("oom", name) for name in names]
        topics = log_scan.make_topics(findings, [], DAY)
        body = topics[0]["body"]

        header = "メモリ不足でプロセスが計60回強制終了された（"
        self.assertLessEqual(len(body), 200)
        self.assertTrue(body.startswith(header))
        rest = body[len(header):]
        self.assertTrue(rest.endswith("）。"))
        parts = rest[:-2].split("、")
        match = re.fullmatch(r"ほか(\d+)件", parts[-1])
        self.assertIsNotNone(match)
        omitted = int(match.group(1))
        self.assertGreaterEqual(omitted, 1)
        self.assertEqual(parts[:-1], names[:60 - omitted])


if __name__ == "__main__":
    unittest.main()
