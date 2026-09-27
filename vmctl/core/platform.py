"""
The virtual machine underneath the guest: architecture, machine type, CPU, NIC.

Three of vmctl's fields were VirtualBox-shaped in a way that only shows up once a
second hypervisor exists (A-10 in PLAN.md):

* **There was no architecture or machine type at all.** VirtualBox has neither --
  a VM runs the host's architecture on a fixed chipset -- so nothing in the model
  needed them. libvirt requires both in every domain, and refuses a machine type
  its QEMU build does not have ("machine type 'virt' is not supported"). Without
  a field, vmctl's libvirt provider had to hardcode ``q35`` in the emitter, which
  is the same mistake the guest OS map was (A-05).
* **A CPU was a number.** libvirt, VMware and Hyper-V all model sockets, cores
  and threads, and the topology is not free: libvirt refuses a domain whose
  topology does not multiply out to its vCPU count ("CPU topology doesn't match
  maximum vcpu count"). VirtualBox has only ``--cpus``, so a topology it cannot
  express is reported rather than quietly ignored.
* **A NIC was the string ``"82540EM"``** -- a VirtualBox chipset id in the
  neutral model. :class:`NicModel` names the chipset the guest sees, and each
  provider maps it: ``e1000`` is ``82540EM`` there and ``e1000`` on QEMU, while
  ``e1000e``, ``rtl8139`` and ``vmxnet3`` exist on QEMU and not on VirtualBox at
  all (measured -- it answers "Invalid NIC type 'e1000e' specified for NIC 1").

Everything here is a small closed vocabulary. What a provider actually offers is
declared in its capabilities, not implied by membership.
"""

from enum import Enum
from typing import List, Optional


class Arch(Enum):
    """The architecture the guest's virtual CPU presents.

    Spelled the way the kernels do (``x86_64``, not ``amd64``), which is also how
    libvirt and QEMU spell them, so no translation is needed for the common case.
    """

    X86_64 = "x86_64"
    I686 = "i686"
    AARCH64 = "aarch64"
    ARMV7 = "armv7l"
    PPC64LE = "ppc64le"
    S390X = "s390x"

    @property
    def is_x86(self) -> bool:
        """Whether this is one of the x86 architectures."""
        return self in (Arch.X86_64, Arch.I686)


class NicModel(Enum):
    """The network chipset the guest sees.

    ``VIRTIO`` is the paravirtualised one: fastest, and the reason a Linux guest
    under KVM uses it. The rest are emulations of real cards, which is what a
    guest without drivers needs -- a Windows installer, for instance, sees an
    ``e1000`` and nothing at all for virtio.
    """

    VIRTIO = "virtio"
    E1000 = "e1000"  # Intel PRO/1000 MT Desktop; VirtualBox calls it 82540EM
    E1000E = "e1000e"  # Intel PRO/1000 PT; QEMU only
    RTL8139 = "rtl8139"  # Realtek 8139; QEMU only
    PCNET = "pcnet"  # AMD PCnet-FAST III; VirtualBox calls it Am79C973
    NE2K = "ne2k"  # NE2000 clone; QEMU only
    VMXNET3 = "vmxnet3"  # VMware paravirtualised; QEMU can emulate it


#: What ``cpu.model`` may say beyond a named CPU model. ``host`` passes the host's
#: CPU through unchanged (fastest, and not migratable); ``host-model`` asks the
#: hypervisor for the closest model it can describe, which is migratable.
CPU_HOST_PASSTHROUGH = "host"
CPU_HOST_MODEL = "host-model"

#: Both neutral spellings, for a provider's table to check against.
CPU_MODEL_KEYWORDS = (CPU_HOST_PASSTHROUGH, CPU_HOST_MODEL)


def topology_product(sockets: Optional[int], cores: Optional[int], threads: Optional[int]) -> int:
    """Return how many vCPUs a topology describes, treating unset parts as 1.

    Args:
        sockets: Number of sockets, or None.
        cores: Cores per socket, or None.
        threads: Threads per core, or None.

    Returns:
        The product, which is what a hypervisor compares against the vCPU count.
    """
    return max(1, sockets or 1) * max(1, cores or 1) * max(1, threads or 1)


def describe_topology(sockets: Optional[int], cores: Optional[int], threads: Optional[int]) -> str:
    """Return a topology in the ``2 sockets x 4 cores x 2 threads`` form."""
    parts: List[str] = []
    for value, label in ((sockets, "socket"), (cores, "core"), (threads, "thread")):
        if value:
            parts.append(f"{value} {label}{'s' if value != 1 else ''}")
    return " x ".join(parts) or "unset"
