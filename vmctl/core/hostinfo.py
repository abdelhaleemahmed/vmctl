"""
Facts about the machine vmctl is running on.

Not about a hypervisor: the host's bridges are the same list whether libvirt, plain
QEMU or something else is asked, so the answer belongs here rather than being written
twice. Live probing (E-05) needs it to validate a bridged adapter's name, which is
otherwise only discovered when a VM fails to start.

Everything here answers "nothing" rather than raising. A fact vmctl cannot establish
has to leave the static declaration alone, because refusing to work is a worse answer
than a conservative one.
"""

import os
import shutil
from typing import Optional, Tuple

#: Where Linux lists network interfaces. A bridge is one with a ``bridge/``
#: subdirectory, which is how ``ip`` itself decides.
NET_CLASS = "/sys/class/net"


def host_bridges() -> Tuple[str, ...]:
    """Return the host's bridge interfaces, in name order.

    Read from sysfs rather than by running ``ip``: the same information without a
    second tool that can be absent, and an unreadable ``/sys`` simply means "not
    probed" -- which is not the same as "there are none".

    Returns:
        tuple[str, ...]: bridge names, empty when they cannot be established.
    """
    try:
        names = sorted(os.listdir(NET_CLASS))
    except OSError:
        return ()
    return tuple(name for name in names if os.path.isdir(os.path.join(NET_CLASS, name, "bridge")))


def memory_mb() -> Optional[int]:
    """Return the host's total RAM in MB, or None when it cannot be established.

    Two ways, because vmctl runs on both: ``/proc/meminfo`` on Linux, and Windows'
    own ``GlobalMemoryStatusEx`` -- asked through ``ctypes`` rather than by running
    ``wmic``, which is deprecated and absent from recent Windows.

    Returns:
        int | None: total RAM in MB.
    """
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:  # pragma: no cover - the Windows path, exercised on the host
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys) // (1024 * 1024)
    except Exception:
        pass
    return None


def free_space_mb(path: str) -> Optional[int]:
    """Return the free space in MB on the filesystem holding *path*.

    The directory itself may not exist yet -- a provider's image directory is
    created on first use -- so the nearest existing parent is measured, which is the
    filesystem that will hold it.

    Returns:
        int | None: free MB, or None when it cannot be established.
    """
    candidate = os.path.abspath(path or os.sep)
    while candidate and not os.path.isdir(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    try:
        return shutil.disk_usage(candidate).free // (1024 * 1024)
    except OSError:
        return None


def hardware_virtualisation() -> Optional[bool]:
    """Whether the host offers hardware virtualisation to vmctl.

    On Linux that is ``/dev/kvm``: present *and* openable, because a machine can
    have KVM while the user is not in the group that may use it, and "there is a
    device file" would then be a wrong answer to the question people actually ask.

    Returns:
        bool | None: None when the question does not have a local answer, which is
        the case on Windows -- the hypervisor knows, and its own diagnostics say so.
    """
    if not os.path.exists("/dev/kvm"):
        return False if os.path.isdir("/sys/module") else None
    return os.access("/dev/kvm", os.R_OK | os.W_OK)
