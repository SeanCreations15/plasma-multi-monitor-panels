"""Linux file events used to wake the sync service."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import select
import struct
from typing import Callable, Protocol


PLASMA_CONFIG_NAME = "plasma-org.kde.plasma.desktop-appletsrc"

_IN_MODIFY = 0x00000002
_IN_ATTRIB = 0x00000004
_IN_CLOSE_WRITE = 0x00000008
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_DELETE_SELF = 0x00000400
_IN_MOVE_SELF = 0x00000800
_IN_Q_OVERFLOW = 0x00004000
_IN_IGNORED = 0x00008000

_TARGET_EVENTS = (
    _IN_MODIFY
    | _IN_ATTRIB
    | _IN_CLOSE_WRITE
    | _IN_MOVED_FROM
    | _IN_MOVED_TO
    | _IN_CREATE
    | _IN_DELETE
)
_WATCH_EVENTS = _TARGET_EVENTS | _IN_DELETE_SELF | _IN_MOVE_SELF
_INVALID_WATCH_EVENTS = _IN_DELETE_SELF | _IN_MOVE_SELF | _IN_IGNORED
_EVENT_HEADER = struct.Struct("iIII")
_READ_SIZE = 64 * 1024

InotifyInit = Callable[[int], int]
InotifyAddWatch = Callable[[int, bytes, int], int]


class EventSource(Protocol):
    def wait(self, timeout: float) -> bool: ...

    def close(self) -> None: ...


def plasma_config_path() -> Path:
    """Return Plasma's applet configuration path."""

    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        candidate = Path(configured)
        if candidate.is_absolute():
            return candidate / PLASMA_CONFIG_NAME
    return Path.home() / ".config" / PLASMA_CONFIG_NAME


def _load_inotify_functions() -> tuple[InotifyInit, InotifyAddWatch]:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        inotify_init = libc.inotify_init1
        inotify_add_watch = libc.inotify_add_watch
    except (AttributeError, OSError) as error:
        raise OSError(
            errno.ENOSYS,
            "Linux inotify is not available",
        ) from error

    inotify_init.argtypes = [ctypes.c_int]
    inotify_init.restype = ctypes.c_int
    inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    inotify_add_watch.restype = ctypes.c_int
    return inotify_init, inotify_add_watch


class InotifyEventSource:
    """Report changes to one file through an inotify directory watch."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self._fd = -1
        self._watch = -1
        inotify_init, inotify_add_watch = _load_inotify_functions()

        flags = os.O_NONBLOCK | os.O_CLOEXEC
        file_descriptor = inotify_init(flags)
        if file_descriptor < 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))

        self._fd = file_descriptor
        watch = inotify_add_watch(
            file_descriptor,
            os.fsencode(target.parent),
            _WATCH_EVENTS,
        )
        if watch < 0:
            error_number = ctypes.get_errno()
            self.close()
            raise OSError(
                error_number,
                f"could not watch {target.parent}: {os.strerror(error_number)}",
            )
        self._watch = watch

    @property
    def closed(self) -> bool:
        return self._fd < 0

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
            self._watch = -1

    def __enter__(self) -> InotifyEventSource:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def wait(self, timeout: float) -> bool:
        """Wait up to timeout seconds and report a target-file event."""

        if self._fd < 0:
            raise OSError(errno.EBADF, "inotify watch is closed")
        try:
            readable, _, _ = select.select(
                [self._fd],
                [],
                [],
                max(0.0, timeout),
            )
        except (OSError, ValueError) as error:
            raise OSError(errno.EIO, f"inotify wait failed: {error}") from error
        if not readable:
            return False

        changed = False
        while True:
            try:
                data = os.read(self._fd, _READ_SIZE)
            except BlockingIOError:
                break
            except OSError as error:
                raise OSError(
                    error.errno or errno.EIO,
                    f"inotify read failed: {error}",
                ) from error
            if not data:
                raise OSError(errno.EIO, "inotify watch closed unexpectedly")
            changed = self._read_events(data) or changed
        return changed

    def _read_events(self, data: bytes) -> bool:
        changed = False
        offset = 0
        while offset < len(data):
            if len(data) - offset < _EVENT_HEADER.size:
                raise OSError(errno.EPROTO, "inotify returned a partial event")
            watch, mask, _cookie, name_length = _EVENT_HEADER.unpack_from(
                data,
                offset,
            )
            offset += _EVENT_HEADER.size
            end = offset + name_length
            if end > len(data):
                raise OSError(errno.EPROTO, "inotify returned a partial name")
            raw_name = data[offset:end].split(b"\0", 1)[0]
            offset = end

            if mask & _IN_Q_OVERFLOW:
                changed = True
            if watch == self._watch and mask & _INVALID_WATCH_EVENTS:
                raise OSError(errno.EIO, "inotify directory watch was lost")
            if (
                watch == self._watch
                and mask & _TARGET_EVENTS
                and os.fsdecode(raw_name) == self.target.name
            ):
                changed = True
        return changed
