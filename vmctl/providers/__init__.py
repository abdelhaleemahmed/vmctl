"""
Hypervisor providers.

Each provider registers itself here with a **lazy** loader: the import happens
the first time the provider is actually used, so a provider whose bindings or
binaries are absent costs nothing and simply reports itself unavailable.
"""

from ..core import registry


def _virtualbox() -> type:
    from .virtualbox.backend import VirtualBoxBackend

    return VirtualBoxBackend


registry.register(
    "virtualbox",
    _virtualbox,
    "Oracle VirtualBox 7.0+ via VBoxManage",
)


def _libvirt() -> type:
    from .libvirt.backend import LibvirtBackend

    return LibvirtBackend


registry.register(
    "libvirt",
    _libvirt,
    "libvirt / QEMU-KVM via virsh",
)


def _qemu() -> type:
    from .qemu.backend import QemuBackend

    return QemuBackend


registry.register(
    "qemu",
    _qemu,
    "Plain QEMU: one process per VM, no daemon",
)
