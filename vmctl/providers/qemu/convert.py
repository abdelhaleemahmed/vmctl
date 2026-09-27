"""
Image conversion with ``qemu-img``.

This lives with the QEMU provider because ``qemu-img`` *is* QEMU's tool; the
libvirt provider imports it from here rather than the other way round, which is the
real relationship between the two -- libvirt manages QEMU.

What it can write was measured on this build rather than recalled: ``qemu-img
create -f`` accepts qcow2, raw, vmdk, vdi, vhdx and vpc, and answers "Unknown file
format 'qed'" for qed and parallels. Note that this is a *wider* set than the
formats a VM can be given as a disk, because QEMU's block layer will only attach
four of them read-only (F-31) -- converting *to* vmdk to hand the file elsewhere is
useful even though a VM here could not boot from it.
"""

import shutil
from typing import List, Optional

from ...core.convert import ConversionRequest, describe
from ...core.devices import DiskFormat
from ...core.plan import Step, StepKind
from .tables import FORMAT_TO_DRIVER


class QemuImgConverter:
    """Converts images by running ``qemu-img convert``."""

    #: Formats qemu-img can read. Wider than what a VM can be given as a disk,
    #: which is the point: an image arriving from another hypervisor is in that
    #: hypervisor's format.
    READABLE = frozenset(FORMAT_TO_DRIVER)

    #: Formats ``qemu-img create``/``convert`` can write on this build. qed and
    #: parallels are absent from the binary entirely.
    WRITABLE = frozenset(
        {
            DiskFormat.QCOW2,
            DiskFormat.RAW,
            DiskFormat.VMDK,
            DiskFormat.VDI,
            DiskFormat.VHD,
            DiskFormat.VHDX,
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
