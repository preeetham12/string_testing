"""Run every test in the project and print one summary.
Usage: python run_all_tests.py"""
import re
import subprocess
import sys
import time

SUITES = [
    ("Part 1  note & pitch detection", [sys.executable, "-m", "demos.part1_pitch"]),
    ("Part 2  quality gate (designed cases)", [sys.executable, "-m", "demos.part2_quality"]),
    ("Part 2  quality gate (unseen cases)", [sys.executable, "-m", "tests.test_unseen"]),
    ("Part 3  comparison (designed students)", [sys.executable, "-m", "demos.part3_compare"]),
    ("Part 3  comparison (unseen students)", [sys.executable, "-m", "tests.test_part3_unseen"]),
    ("Part 4  scoring, feedback, practice loop", [sys.executable, "-m", "demos.part4_scoring"]),
    ("Part 4  scoring (unseen cases)", [sys.executable, "-m", "tests.test_part4_unseen"]),
    ("Part 5  backend API (pytest)", [sys.executable, "-m", "pytest", "tests/test_backend.py",
                                      "-q", "-p", "no:cacheprovider"]),
    ("Part 6  web app in a real browser", [sys.executable, "-m", "pytest", "tests/test_web.py",
                                           "-q", "-p", "no:cacheprovider"]),
    ("String tester results dashboard", [sys.executable, "-m", "pytest", "tests/test_tester_results.py",
                                         "-q", "-p", "no:cacheprovider"]),
    ("Part 7  teacher studio in a real browser", [sys.executable, "-m", "pytest", "tests/test_teacher_web.py",
                                                  "-q", "-p", "no:cacheprovider"]),
]
all_ok = True
for name, cmd in SUITES:
    t = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout
    m = re.findall(r"(\d+)/(\d+) (?:checks passed|recordings judged|judged|unseen students)", out)
    got, total = map(int, m[-1]) if m else (0, 1)
    p = re.search(r"(\d+) passed", out)
    if "pytest" in cmd and p:  # pytest prints "24 passed" / "1 failed, 23 passed"
        f = re.search(r"(\d+) failed", out)
        got = int(p.group(1))
        total = got + (int(f.group(1)) if f else 0)
    ok = got == total
    all_ok &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name:42s} {got}/{total}   {time.time() - t:4.0f}s", flush=True)
    if not ok:  # show what went wrong (the output is captured otherwise)
        print("\n".join("    | " + line for line in (out + proc.stderr).strip().splitlines()[-40:]))
print("\nALL TESTS PASSED" if all_ok else "\nSOME TESTS FAILED - details printed under each failed suite")
sys.exit(0 if all_ok else 1)  # CI needs a failing exit code
