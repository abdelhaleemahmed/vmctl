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
from typing import Tuple

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
