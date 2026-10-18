"""Atomic no-replace publication.

`os.rename(src, dst)` replaces an existing EMPTY destination directory, so "check then rename" is racy. The kernel
primitives below make the existence test and the move one atomic filesystem operation:

  macOS  renamex_np(src, dst, RENAME_EXCL)        (libSystem)
  Linux  renameat2(AT_FDCWD, src, AT_FDCWD, dst, RENAME_NOREPLACE)   (glibc >= 2.28, kernel >= 3.15)

Guarantee: if `dst` exists in any form at the instant of the call (file, empty or non-empty directory, live or dangling
symlink) the call raises FileExistsError and neither path is modified. Otherwise `src` becomes `dst` in one step.
There is no fallback: where the primitive or the filesystem does not support it the call raises PublishUnsupported and
nothing is moved (fail closed). `src` and `dst` must be on the same filesystem.
"""
import ctypes
import ctypes.util
import errno
import os
import sys

RENAME_NOREPLACE_LINUX = 1
RENAME_EXCL_DARWIN = 0x4
AT_FDCWD = -100


class PublishUnsupported(RuntimeError):
    """No atomic no-replace rename is available on this host/filesystem; publication is refused."""


def _libc():
    return ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)


def rename_noreplace(src, dst):
    s, d = os.fsencode(os.fspath(src)), os.fsencode(os.fspath(dst))
    if sys.platform == "darwin":
        fn = getattr(_libc(), "renamex_np", None)
        if fn is None:
            raise PublishUnsupported("renamex_np is not available")
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        rc = fn(s, d, RENAME_EXCL_DARWIN)
    elif sys.platform.startswith("linux"):
        fn = getattr(_libc(), "renameat2", None)
        if fn is None:
            raise PublishUnsupported("renameat2 is not available in this libc")
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        rc = fn(AT_FDCWD, s, AT_FDCWD, d, RENAME_NOREPLACE_LINUX)
    else:
        raise PublishUnsupported(f"no atomic no-replace rename known for platform {sys.platform}")
    if rc == 0:
        return
    err = ctypes.get_errno()
    if err == errno.EEXIST or err == errno.ENOTEMPTY:
        raise FileExistsError(err, os.strerror(err), os.fsdecode(d))
    if err in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS):
        raise PublishUnsupported(f"filesystem or kernel does not support no-replace rename ({os.strerror(err)})")
    raise OSError(err, os.strerror(err), os.fsdecode(s))


def self_test(directory):
    """True if the primitive refuses an existing empty directory under `directory` (used by tests / operators)."""
    a, b = os.path.join(directory, "_a"), os.path.join(directory, "_b")
    os.mkdir(a)
    os.mkdir(b)
    try:
        rename_noreplace(a, b)
        return False
    except FileExistsError:
        return True
    finally:
        os.rmdir(a)
        os.rmdir(b)
