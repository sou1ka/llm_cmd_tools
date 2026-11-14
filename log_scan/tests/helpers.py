"""tests 配下で共有するヘルパー。"""
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import log_scan

SYSTEMCTL_ARGS = ["systemctl", "list-units", "--state=failed", "--plain", "--no-legend"]


def jline(**fields):
    """journalctl -o json の1行相当。日本語はエスケープしない。"""
    return json.dumps(fields, ensure_ascii=False)


def bytes_list(text):
    """journal が MESSAGE をバイト列で出す形（整数のリスト）。"""
    return list(text.encode("utf-8"))


def completed(args, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class FakeRunner:
    """subprocess.run の代役。

    journalctl は journal_results を先頭から1回ずつ返す。
    systemctl は systemctl_result を返す。
    """

    def __init__(self, journal_results, systemctl_result=None):
        self.journal_results = list(journal_results)
        self.systemctl_result = systemctl_result or completed(SYSTEMCTL_ARGS)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[0] == "journalctl":
            if not self.journal_results:
                raise AssertionError(f"想定外の journalctl 呼び出し: {args}")
            return self.journal_results.pop(0)
        if args[0] == "systemctl":
            return self.systemctl_result
        raise AssertionError(f"想定外のコマンド: {args}")

    def journal_calls(self):
        return [args for args, _ in self.calls if args[0] == "journalctl"]

    def systemctl_calls(self):
        return [args for args, _ in self.calls if args[0] == "systemctl"]

    def kwargs_of(self, command):
        for args, kwargs in self.calls:
            if args[0] == command:
                return kwargs
        return None


class TempDirTestCase(unittest.TestCase):
    """一時ディレクトリ self.dir を用意する基底クラス。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


class MainTestCase(TempDirTestCase):
    """SCRIPT_DIR を self.dir へ向け、subprocess なしで main を実行する基底クラス。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(log_scan, "SCRIPT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, runner):
        """main を実行し (終了コード, stdout, stderr) を返す。

        終了は sys.exit による SystemExit と、正常 return のどちらも受け付ける。
        """
        out_buf = io.BytesIO()
        out = io.TextIOWrapper(out_buf, encoding="utf-8", write_through=True)
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch("log_scan.subprocess.run", side_effect=runner):
            try:
                returned = log_scan.main()
            except SystemExit as e:
                code = 0 if e.code is None else e.code
            else:
                code = 0 if returned is None else returned
        out.flush()
        return code, out_buf.getvalue().decode("utf-8"), err.getvalue()
