"""
Image conversion for libvirt, with ``qemu-img``.

``qemu-img`` is the general-purpose tool here: it reads more formats than any
hypervisor's own utility, including VirtualBox's VDI and VMware's VMDK, which is
what makes moving a VM onto libvirt practical.
"""

import shutil
from typing import List, Optional

from ...core.convert import ConversionRequest, describe
from ...core.plan import Step, StepKind
from ...core.vmconfig import DiskFormat
from .tables import FORMAT_TO_DRIVER


class QemuImgConverter:
    """Converts images by running ``qemu-img convert``."""

    #: Formats qemu-img can read. Wider than what libvirt will *create* a guest
    #: disk in, which is the point: an image arriving from another hypervisor is
    #: usually in that hypervisor's format.
    READABLE = frozenset(FORMAT_TO_DRIVER)

    #: Formats qemu-img can write.
    WRITABLE = frozenset(
        {
            DiskFormat.QCOW2,
            DiskFormat.RAW,
            DiskFormat.VMDK,
            DiskFormat.VDI,
            DiskFormat.VHD,
            DiskFormat.QED,
            DiskFormat.PARALLELS,
        }
    )

    @classmethod
    def is_available(cls) -> bool:
        """Whether ``qemu-img`` is on PATH."""
        return shutil.which("qemu-img") is not None

    def can_convert(self, source: Optional[DiskFormat], target: DiskFormat) -> bool:
        """Whether this converter handles the pair.

        Args:
            source: The source format, or None to let qemu-img detect it.
            target: The format to write.
        """
        if target not in self.WRITABLE:
            return False
        return source is None or source in self.READABLE

    def steps(self, request: ConversionRequest) -> List[Step]:
        """Return the steps that convert one image.

        The source format is stated when known: letting qemu-img guess is a known
        way to be surprised by a file whose contents suggest another format.
        """
        argv = ["qemu-img", "convert"]
        if request.source_format is not None:
            argv += ["-f", FORMAT_TO_DRIVER.get(request.source_format, "raw")]
        argv += [
            "-O",
            FORMAT_TO_DRIVER.get(request.target_format, "qcow2"),
            request.source,
            request.target,
        ]
        return [
            Step(
                kind=StepKind.EXEC,
                description=describe(request),
                argv=argv,
                undo=Step(
                    kind=StepKind.EXEC,
                    description=f"remove the converted {request.label or 'image'}",
                    argv=["rm", "-f", request.target],
                    destructive=True,
                ),
            )
        ]
