"""
Plain QEMU: no daemon, no registry, just a command line.

The provider that tests whether the abstraction is really about *hypervisors* or
just about *managers of hypervisors* (P-05 in PLAN.md). VirtualBox has a CLI that
holds state, libvirt has a daemon that holds state, and QEMU has neither: a VM is
a process, and its configuration is the argument list that started it.

So this provider's native artifact is a **shell script** -- the argv, written out
and runnable on its own -- and reading a VM back means parsing that script. That
is the same parser/emitter duality as the other two providers, with the native
format being a command line rather than a document or a key/value dump, which is
exactly the shape :class:`~vmctl.core.plan.Plan` was introduced for (A-01).
"""

from .backend import QemuBackend

__all__ = ["QemuBackend"]
