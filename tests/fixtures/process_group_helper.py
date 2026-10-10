#!/usr/bin/env python3
"""Spawn a TERM-ignoring child in the current process group for cleanup tests."""

import os
import signal
import sys
import time
from pathlib import Path


pid_file = Path(sys.argv[1])
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    temporary_file = pid_file.with_suffix(".tmp")
    temporary_file.write_text(str(os.getpid()), encoding="ascii")
    os.replace(temporary_file, pid_file)
    while True:
        time.sleep(1)


def stop_leader(_signum, _frame):
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop_leader)
while True:
    time.sleep(1)
