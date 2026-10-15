#!/usr/bin/env python3
"""The timer measures the child and refuses failed or silent checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TIMER = Path(__file__).resolve().parents[2] / 'scripts/lib/benchrun.py'


class Timing(unittest.TestCase):
    def invoke(self, code, *options):
        return subprocess.run([sys.executable, str(TIMER), '--once', *options,
                               '--', sys.executable, '-c', code],
                              capture_output=True, text=True)

    def test_child_elapsed_time(self):
        result = self.invoke('import time; time.sleep(0.15); print("OK")', '--expect-ok')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(float(result.stdout), 0.15)

    def test_failure_is_not_a_fast_success(self):
        result = self.invoke('import sys; print("OK"); sys.exit(7)', '--expect-ok')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('exited 7', result.stderr)

    def test_silent_check_is_refused(self):
        result = self.invoke('pass', '--expect-ok')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('without printing OK', result.stderr)

    def test_working_directory_and_answer(self):
        with tempfile.TemporaryDirectory(prefix='axiom timing ') as directory:
            root = Path(directory)
            (root / 'input').write_text('answer\n')
            result = self.invoke('from pathlib import Path; print(Path("input").read_text(), end="")',
                                 '--cwd', directory, '--stdout', str(root / 'output'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(float(result.stdout), 0)
            self.assertEqual((root / 'output').read_text(), 'answer\n')


if __name__ == '__main__':
    unittest.main()
