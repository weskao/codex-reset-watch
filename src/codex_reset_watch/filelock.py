"""Cross-platform non-blocking/blocking advisory file lock.

POSIX uses fcntl.flock; Windows has no flock, so msvcrt.locking is used on a
1-byte region of the same file instead. Both give the same observable
contract: a second `lock()` on the same path fails fast unless `blocking=True`.
One difference: `blocking=True` waits indefinitely on POSIX, but LK_LOCK gives
up after ~10s on Windows and reports the lock as not acquired.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import sys
from typing import Iterator

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextlib.contextmanager
def lock(path: pathlib.Path, blocking: bool = False) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            # msvcrt.locking() locks relative to the CURRENT file position, so
            # the seek is load-bearing: without it the caller that created the
            # file locks byte 1 (position left there by the write) while every
            # later caller locks byte 0, and the two never conflict.
            os.lseek(fd, 0, os.SEEK_SET)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(fd, mode, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
