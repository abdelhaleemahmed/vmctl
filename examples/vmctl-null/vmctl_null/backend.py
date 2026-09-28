"""The smallest thing that satisfies vmctl's provider contract."""

from vmctl.core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from vmctl.core.devices import BusType, DeviceKind, DiskFormat
from vmctl.core.naming import check_name
from vmctl.core.plan import Plan
from vmctl.core.storage import directory
from vmctl.providers.base import BaseProvider

CAPABILITIES = Capabilities(
    provider="null",
    buses={BusType.SATA: BusSpec("sata", "ahci", 1, 4, default_ports=4, controller_name="sata0")},
    # Every (kind, bus) pair, including the noes: the conformance suite refuses an
    # *undeclared* pair, because a missing cell is an unanswered question rather than
    # a no, and validation would then reject or accept it by accident.
    attach={
        (DeviceKind.DISK, BusType.SATA): True,
        (DeviceKind.CDROM, BusType.SATA): True,
        (DeviceKind.FLOPPY, BusType.SATA): False,
    },
    formats={DiskFormat.RAW: FormatSpec(Support.NATIVE, ("raw",), "raw", ("thin",))},
    native_format=DiskFormat.RAW,
    native_buses={DeviceKind.DISK: BusType.SATA, DeviceKind.CDROM: BusType.SATA},
    evidence="none: this provider does nothing, which is the point",
)


class NullBackend(BaseProvider):
    """A provider that plans everything and does nothing."""

    @property
    def name(self):
        return "null"

    @property
    def capabilities(self):
        return CAPABILITIES

    def storage_location(self):
        return directory("/tmp/null-vms", nest_per_vm=True)

    def list_vms(self):
        return []

    def read_vm(self, vm_name):
        raise NotImplementedError

    def create_vm(self, vm, execute=True, policy=None):
        # The emitter checks the name, not only the validator: a name reaches the
        # filesystem through a provider, so every provider has to refuse one that
        # would escape (A-08).
        check_name(vm.name, self.capabilities)
        plan = Plan("null")
        plan.exec(["true", vm.name], f"pretend to create {vm.name}")
        return plan

    def delete_vm(self, vm_name):
        return True

    def start_vm(self, vm_name):
        return True

    def stop_vm(self, vm_name, force=False, wait=0):
        return True

    def get_vm_status(self, vm_name):
        return "stopped"

    def vm_exists(self, vm_name):
        return False
