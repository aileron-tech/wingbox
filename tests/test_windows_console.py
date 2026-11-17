"""Nothing Wingbox starts in the background opens a console window on Windows."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import portable  # noqa: E402

# A console program reports its console window to a file: the handle (0: none) and whether it is visible.
REPORT = textwrap.dedent('''
    import ctypes, json, sys
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    visible = bool(hwnd) and bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    with open(sys.argv[1], "a", encoding="utf-8") as out:
        out.write(json.dumps({"who": sys.argv[2], "hwnd": int(hwnd or 0), "visible": visible}) + "\\n")
''')

# The background process: reports itself, then starts what the refresh starts - a console child that inherits
# its console (a git call), a child through the helper, and a multiprocessing spawn worker.
BACKGROUND = textwrap.dedent('''
    import multiprocessing, subprocess, sys
    sys.path.insert(0, sys.argv[3])
    import portable

    def worker(report, out):
        subprocess.run([sys.executable, report, out, "worker"], check=True)

    if __name__ == "__main__":
        report, out = sys.argv[1], sys.argv[2]
        subprocess.run([sys.executable, report, out, "background"], check=True)
        subprocess.run([sys.executable, report, out, "inherited child"], check=True)
        subprocess.run([sys.executable, report, out, "helper child"], check=True, **portable.hidden_process_options())
        process = multiprocessing.get_context("spawn").Process(target=worker, args=(report, out))
        process.start()
        process.join()
''')


@unittest.skipUnless(os.name == "nt", "console windows exist only on Windows")
class WindowsConsoleTest(unittest.TestCase):
    def run_background(self, options: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            report, background, out = Path(tmp) / "report.py", Path(tmp) / "background.py", Path(tmp) / "out.jsonl"
            report.write_text(REPORT, encoding="utf-8")
            background.write_text(BACKGROUND, encoding="utf-8")
            process = subprocess.Popen([sys.executable, str(background), str(report), str(out), str(REPO / "scripts")],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
            process.wait(timeout=120)
            return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    def test_the_background_refresh_and_its_children_show_no_console_window(self) -> None:
        """/* REQ-PORT-011: the detached refresh (started with portable.detached_process_options, as the hook starts it), a console child it starts as it is, a child started through portable.hidden_process_options and a multiprocessing spawn worker have no visible console window; with the flags of 0.7.0 to 0.8.1 (DETACHED_PROCESS) the same children get a console window of their own, which is the window the report saw. */"""
        now = self.run_background(portable.detached_process_options())
        self.assertEqual({"background", "inherited child", "helper child", "worker"}, {item["who"] for item in now})
        self.assertEqual([], [item for item in now if item["visible"]], now)
        old = self.run_background({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                                   "close_fds": True})
        print(f"\nwith DETACHED_PROCESS: {old}\nwith the fix: {now}", file=sys.stderr)
        self.assertTrue([item for item in old if item["who"] != "background" and item["hwnd"]], old)


class HiddenOptionsTest(unittest.TestCase):
    def test_the_helpers_add_flags_only_on_windows(self) -> None:
        """/* REQ-PORT-012: portable.hidden_process_options gives CREATE_NO_WINDOW and a hidden STARTUPINFO on Windows and nothing elsewhere; portable.detached_process_options never combines CREATE_NO_WINDOW with DETACHED_PROCESS or CREATE_NEW_CONSOLE (Windows then ignores it). */"""
        hidden = portable.hidden_process_options()
        detached = portable.detached_process_options()
        if os.name != "nt":
            self.assertEqual({}, hidden)
            self.assertTrue(detached["start_new_session"])
            return
        self.assertTrue(hidden["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(subprocess.SW_HIDE, hidden["startupinfo"].wShowWindow)
        flags = detached["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        self.assertFalse(flags & (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_CONSOLE))


if __name__ == "__main__":
    unittest.main()
