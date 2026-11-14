"""log_scan.main() のテスト。subprocess は log_scan.subprocess.run を mock する。"""
import json
import unittest
from datetime import date

from helpers import MainTestCase, FakeRunner, SYSTEMCTL_ARGS, bytes_list, completed

TODAY = date.today().isoformat()
JOURNAL_SINCE = ["journalctl", "-o", "json", "--no-pager", "--since", "24 hours ago"]
UNIT_LINE = "● foo.service loaded failed failed Foo\n"


def after_cursor_args(cursor):
    return ["journalctl", "-o", "json", "--no-pager", "--after-cursor", cursor]


def journal_line(cursor, message="", priority=None, ident=None, transport=None):
    """journal の1エントリ行。cursor が None なら __CURSOR を入れない。"""
    fields = {"MESSAGE": message}
    if cursor is not None:
        fields["__CURSOR"] = cursor
    if priority is not None:
        fields["PRIORITY"] = priority
    if ident is not None:
        fields["SYSLOG_IDENTIFIER"] = ident
    if transport is not None:
        fields["_TRANSPORT"] = transport
    return json.dumps(fields, ensure_ascii=False)


def err_line(cursor, ident="gdm", message="something failed"):
    return journal_line(cursor, message=message, priority="3", ident=ident)


def info_line(cursor):
    return journal_line(cursor, message="started", priority="6", ident="gdm")


def topics_of(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def ok_journal(*lines):
    return completed(JOURNAL_SINCE, 0, "\n".join(lines) + ("\n" if lines else ""))


class MainTest(MainTestCase):
    def cursor_text(self):
        path = self.dir / "cursor.txt"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()

    def write_cursor_file(self, text):
        (self.dir / "cursor.txt").write_text(text, encoding="utf-8")

    def write_ignore_file(self, text):
        (self.dir / "ignore.txt").write_text(text, encoding="utf-8")

    def test_main_without_cursor_file_calls_journal_with_since(self):
        runner = FakeRunner([ok_journal()])
        self.run_main(runner)
        self.assertEqual(runner.journal_calls(), [JOURNAL_SINCE])

    def test_main_with_cursor_file_calls_journal_with_after_cursor(self):
        self.write_cursor_file("s=prev\n")
        runner = FakeRunner([completed(after_cursor_args("s=prev"), 0, "")])
        self.run_main(runner)
        self.assertEqual(runner.journal_calls(), [after_cursor_args("s=prev")])

    def test_main_runs_systemctl_list_failed_units(self):
        runner = FakeRunner([ok_journal()])
        self.run_main(runner)
        self.assertEqual(runner.systemctl_calls(), [SYSTEMCTL_ARGS])

    def test_main_passes_text_decoding_kwargs_to_subprocess(self):
        runner = FakeRunner([ok_journal()])
        self.run_main(runner)
        expected = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        for command in ["journalctl", "systemctl"]:
            kwargs = runner.kwargs_of(command)
            self.assertIsNotNone(kwargs, command)
            for key, value in expected.items():
                self.assertEqual(kwargs[key], value, f"{command} の {key}")

    def test_main_retries_with_since_when_cursor_journal_fails(self):
        self.write_cursor_file("s=prev")
        runner = FakeRunner([
            completed(after_cursor_args("s=prev"), 1, ""),
            ok_journal(err_line("c9")),
        ])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(runner.journal_calls(), [after_cursor_args("s=prev"), JOURNAL_SINCE])
        self.assertEqual(code, 0)
        self.assertEqual(self.cursor_text(), "c9")
        self.assertEqual([t["ref"] for t in topics_of(stdout)], [f"log_err:{TODAY}"])

    def test_main_does_not_retry_without_cursor_when_journal_fails(self):
        runner = FakeRunner([completed(JOURNAL_SINCE, 1, "")])
        code, _, _ = self.run_main(runner)
        self.assertEqual(len(runner.journal_calls()), 1)
        self.assertEqual(code, 1)

    def test_main_gives_up_after_retry_fails_keeps_cursor_and_reports_units(self):
        self.write_cursor_file("s=prev")
        runner = FakeRunner(
            [
                completed(after_cursor_args("s=prev"), 1, ""),
                completed(JOURNAL_SINCE, 1, ""),
            ],
            systemctl_result=completed(SYSTEMCTL_ARGS, 0, UNIT_LINE),
        )
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(runner.journal_calls(), [after_cursor_args("s=prev"), JOURNAL_SINCE])
        self.assertEqual(code, 1)
        self.assertEqual(self.cursor_text(), "s=prev")
        self.assertEqual(topics_of(stdout), [{
            "kind": "system",
            "ref": f"unit_failed:foo.service:{TODAY}",
            "body": "foo.serviceが停止（failed）状態。",
            "priority": 2,
        }])

    def test_main_journal_failure_without_cursor_creates_no_cursor_file(self):
        runner = FakeRunner([completed(JOURNAL_SINCE, 1, "")])
        code, _, _ = self.run_main(runner)
        self.assertEqual(code, 1)
        self.assertIsNone(self.cursor_text())

    def test_main_writes_last_cursor_after_run(self):
        runner = FakeRunner([ok_journal(
            err_line("c1"),
            err_line("c2"),
            info_line("c3"),
        )])
        self.run_main(runner)
        self.assertEqual(self.cursor_text(), "c3")

    def test_main_keeps_cursor_when_no_entries(self):
        self.write_cursor_file("s=old")
        runner = FakeRunner([completed(after_cursor_args("s=old"), 0, "")])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(self.cursor_text(), "s=old")

    def test_main_keeps_cursor_when_entries_have_no_cursor_field(self):
        self.write_cursor_file("s=old")
        runner = FakeRunner([completed(
            after_cursor_args("s=old"), 0, journal_line(None, message="x", priority="3", ident="gdm") + "\n"
        )])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(self.cursor_text(), "s=old")
        self.assertEqual(len(topics_of(stdout)), 1)

    def test_main_systemctl_failure_exits_1_without_unit_topics_and_keeps_journal_topics(self):
        runner = FakeRunner(
            [ok_journal(err_line("c5"))],
            systemctl_result=completed(SYSTEMCTL_ARGS, 1, UNIT_LINE),
        )
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 1)
        refs = [t["ref"] for t in topics_of(stdout)]
        self.assertEqual(refs, [f"log_err:{TODAY}"])
        self.assertEqual(self.cursor_text(), "c5")

    def test_main_ignore_file_excludes_matching_err(self):
        self.write_ignore_file("^sshd:\n")
        runner = FakeRunner([ok_journal(
            err_line("c1", ident="sshd", message="Failed password for root"),
            err_line("c2", ident="gdm", message="timeout"),
        )])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 0)
        self.assertEqual(topics_of(stdout), [{
            "kind": "system",
            "ref": f"log_err:{TODAY}",
            "body": "エラーログは1種類・計1件。gdm 1件。",
            "priority": 0,
        }])

    def test_main_orders_io_oom_unit_err(self):
        runner = FakeRunner(
            [ok_journal(
                err_line("c1", ident="gdm"),
                journal_line("c2", "Out of memory: Killed process 1234 (python3) total-vm:1kB",
                             priority="2", transport="kernel"),
                journal_line("c3", "Buffer I/O error on dev sdb1, logical block 0",
                             priority="3", transport="kernel"),
            )],
            systemctl_result=completed(SYSTEMCTL_ARGS, 0, UNIT_LINE),
        )
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 0)
        self.assertEqual(
            [t["ref"] for t in topics_of(stdout)],
            [
                f"log_io:{TODAY}",
                f"log_oom:{TODAY}",
                f"unit_failed:foo.service:{TODAY}",
                f"log_err:{TODAY}",
            ],
        )

    def test_main_decodes_int_list_message_for_io(self):
        runner = FakeRunner([ok_journal(journal_line(
            "c1", bytes_list("Buffer I/O error on dev sdb1, logical block 0"),
            priority="3", transport="kernel",
        ))])
        _, stdout, _ = self.run_main(runner)
        self.assertEqual(topics_of(stdout)[0]["body"], "ディスクのI/Oエラーが計1件（sdb1 1件）。")

    def test_main_prints_nothing_and_exits_0_when_nothing_to_report(self):
        runner = FakeRunner([ok_journal(info_line("c1"))])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")

    def test_main_prints_japanese_unescaped(self):
        runner = FakeRunner([ok_journal(err_line("c1", ident="gdm"))])
        _, stdout, _ = self.run_main(runner)
        self.assertIn("エラーログは1種類・計1件。gdm 1件。", stdout)
        self.assertNotIn("\\u", stdout)

    def test_main_skips_invalid_json_lines_without_failing(self):
        stdout_text = "not json\n[1,2]\n" + err_line("c1") + "\n"
        runner = FakeRunner([completed(JOURNAL_SINCE, 0, stdout_text)])
        code, stdout, _ = self.run_main(runner)
        self.assertEqual(code, 0)
        self.assertEqual(len(topics_of(stdout)), 1)

    def test_main_invalid_ignore_regex_exits_1_without_calling_subprocess(self):
        self.write_ignore_file("(\n")
        runner = FakeRunner([])
        code, stdout, stderr = self.run_main(runner)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertNotEqual(stderr, "")
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
