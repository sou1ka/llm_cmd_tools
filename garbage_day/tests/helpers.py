"""テスト共通ヘルパー: スクリプトを一時ディレクトリへコピーして CLI として実行する。"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "garbage_day.py"

CliResult = namedtuple("CliResult", "returncode stdout stderr")


def build_env():
    """子プロセス用の環境変数を作る（入出力の UTF-8 を強制する）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_script(script, args, env, cwd):
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        timeout=60,
    )
    return CliResult(proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", errors="replace"))


class CliTestCase(unittest.TestCase):
    """スクリプトを一時ディレクトリへコピーして CLI として実行する基底クラス。

    作業ディレクトリ構成:
      work/app/garbage_day.py  （スクリプトの場所。calendar.toml もここに置く）
      work/                    （実行時の作業ディレクトリ。スクリプトの場所とは別にする）
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.app_dir = self.work / "app"
        self.app_dir.mkdir()
        self.script = self.app_dir / SCRIPT_NAME
        shutil.copyfile(PROJECT_DIR / SCRIPT_NAME, self.script)

    def write_calendar(self, text, path=None):
        path = Path(path) if path else self.app_dir / "calendar.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_cli(self):
        return run_script(self.script, [], build_env(), self.work)
