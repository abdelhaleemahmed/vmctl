"""
Image conversion for VMware, with ``vmware-vdiskmanager``.

The narrowest converter of the three, and deliberately declared that way:
``vmware-vdiskmanager -r`` reads **VMDK and nothing else**. It changes a VMDK's
*type* -- growable to preallocated, single file to split -- and cannot read a qcow2,
a VDI or a VHD at all.

So moving a VM *onto* VMware from libvirt or QEMU needs ``qemu-img`` to write the
VMDK first, and vmctl says so rather than pretending its own tool can. That is the
`Support.UNSUPPORTED` half of the matrix doing its job: a capability declaration is
only useful if it is willing to say no.
"""

import shutil
from typing import List, Optional

from ...core.convert import ConversionRequest, describe
from ...core.devices import Allocation, DiskFormat
from ...core.plan import Step, StepKind
from .tables import ALLOCATION_TO_DISK_TYPE


class VDiskManagerConverter:
    """Converts VMDKs by running ``vmware-vdiskmanager -r``."""

    #: What it can read: its own format.
    READABLE = frozenset({DiskFormat.VMDK})

    #: And what it can write.
    WRITABLE = frozenset({DiskFormat.VMDK})

    def __init__(self, tool: str = "vmware-vdiskmanager"):
        """Initialise the converter.

        Args:
            tool: How to invoke vdiskmanager. On Windows it is not on PATH, so the
                backend passes its full path.
        """
        self.tool = tool

    def is_available(self) -> bool:
        """Whether the tool can be found."""
        return self.tool.startswith("/") or "\\" in self.tool or shutil.which(self.tool) is not None

    def can_convert(self, source: Optional[DiskFormat], target: DiskFormat) -> bool:
        """Whether this converter handles the pair.

        Args:
            source: The source format, or None when it is not known.
            target: The format to write.
        """
        if target not in self.WRITABLE:
            return False
        return source is None or source in self.READABLE

    def steps(self, request: ConversionRequest) -> List[Step]:
        """Return the steps that convert one image.

        Args:
            request: What to convert.

        Returns:
            One EXEC step.
        """
        # A ConversionRequest carries no allocation, and for this tool the type *is*
        # the conversion, so growable is the honest default: it is what
        # `vmware-vdiskmanager -c` makes and what nothing has asked to change.
        disk_type = ALLOCATION_TO_DISK_TYPE[Allocation.THIN]
        return [
            Step(
                kind=StepKind.EXEC,
                description=describe(request),
                argv=[self.tool, "-q", "-r", request.source, "-t", disk_type, request.target],
                undo=Step(
                    kind=StepKind.EXEC,
                    description=f"remove the converted {request.label or 'disk'}",
                    argv=["rm", "-f", request.target],
                    destructive=True,
                ),
            )
        ]
