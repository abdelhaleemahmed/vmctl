"""
Image conversion for VirtualBox, with ``VBoxManage clonemedium``.

VirtualBox can write the formats it can create -- VDI, VMDK, VHD and the rest of
its ``createmedium`` set -- and read a little more besides. It cannot produce a
VHDX, so converting *to* one is not possible here even though VirtualBox will
happily attach one.
"""

import shutil
from typing import List, Optional

from ...core.convert import ConversionRequest, describe
from ...core.plan import Step, StepKind
from ...core.vmconfig import DiskFormat
from .capabilities import FORMATS


class CloneMediumConverter:
    """Converts images by running ``VBoxManage clonemedium disk``."""

    @classmethod
    def is_available(cls) -> bool:
        """Whether ``VBoxManage`` is on PATH."""
        return shutil.which("VBoxManage") is not None

    def can_convert(self, source: Optional[DiskFormat], target: DiskFormat) -> bool:
        """Whether this converter handles the pair.

        The target must be a format VirtualBox can create, which is exactly the
        capability declaration's answer -- so this cannot drift from it.
        """
        target_spec = FORMATS.get(target)
        if target_spec is None or not target_spec.support.creatable:
            return False
        if source is None:
            return True
        source_spec = FORMATS.get(source)
        return source_spec is not None and source_spec.support.usable

    def steps(self, request: ConversionRequest) -> List[Step]:
        """Return the steps that convert one image."""
        target_spec = FORMATS.get(request.target_format)
        native = target_spec.native_name if target_spec else request.target_format.value
        return [
            Step(
                kind=StepKind.EXEC,
                description=describe(request),
                argv=[
                    "VBoxManage",
                    "clonemedium",
                    "disk",
                    request.source,
                    request.target,
                    "--format",
                    native,
                ],
                undo=Step(
                    kind=StepKind.EXEC,
                    description=f"discard the cloned {request.label or 'image'}",
                    argv=["VBoxManage", "closemedium", "disk", request.target, "--delete"],
                    destructive=True,
                ),
            )
        ]
