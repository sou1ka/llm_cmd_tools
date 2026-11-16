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
SCRIPT_NAME = "domain_expiry.py"
CONFIG_KEYS = ("DOMAIN_NAMES", "DOMAIN_WARN_DAYS", "DOMAIN_CRIT_DAYS", "DOMAIN_TIMEOUT")

CliResult = namedtuple("CliResult", "returncode stdout stderr")


def write_env_file(path, values):
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    Path(path).write_text(text, encoding="utf-8")


def build_env(values):
    """子プロセス用の環境変数を作る。CONFIG_KEYS は取り除いてから values を足す（values の None は除く）。"""
    env = {key: value for key, value in os.environ.items() if key not in CONFIG_KEYS}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.update({key: value for key, value in values.items() if value is not None})
    return env


def run_script(script, args, env):
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(script.parent),
        env=env,
        capture_output=True,
        timeout=60,
    )
    return CliResult(proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", errors="replace"))


class CliTestCase(unittest.TestCase):
    """スクリプトを一時ディレクトリへコピーして CLI として実行する基底クラス。

    作業ディレクトリ構成:
      work/app/domain_expiry.py  （.env の影響を避けるため、ここにだけ置く）
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.app_dir = self.work / "app"
        self.app_dir.mkdir()
        self.script = self.app_dir / SCRIPT_NAME
        shutil.copyfile(PROJECT_DIR / SCRIPT_NAME, self.script)

    def write_env(self, values):
        write_env_file(self.app_dir / ".env", values)

    def run_cli(self, config):
        return run_script(self.script, [], build_env(config))
