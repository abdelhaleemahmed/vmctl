"""
Image conversion for libvirt.

The tool is ``qemu-img``, which belongs to QEMU, so the converter lives with the
QEMU provider and is used from here. That dependency direction is the true one:
libvirt manages QEMU, and its format names are QEMU's own.

It is re-exported rather than subclassed because there is nothing libvirt-specific
about it -- which is itself worth stating, since the two providers' *capability*
declarations differ even though this does not (F-31).
"""

from ..qemu.convert import QemuImgConverter

__all__ = ["QemuImgConverter"]
