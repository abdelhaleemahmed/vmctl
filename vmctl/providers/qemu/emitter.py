"""
Build a QEMU command line, and the plan that writes and runs it.

The native artifact here is a **shell script**: the argv, one option per line, with
a shebang so it runs on its own. That is not a convenience -- QEMU has nowhere to
*put* a definition, so the script is the VM's configuration in the only form QEMU
has. Writing it is a :class:`~vmctl.core.plan.StepKind.WRITE_FILE` step and
starting it is an ``EXEC``, which is the shape :class:`~vmctl.core.plan.Plan`
exists for (A-01).

Two things follow from QEMU having no state of its own:

* the script carries ``-pidfile`` and ``-daemonize``, because something has to
  know whether the VM is running, and QEMU will not remember;
* the guest OS has no representation at all. VirtualBox stores an OS type and
  libvirt records a libosinfo id; QEMU has neither, so ``guest_os`` is reported as
  dropped rather than quietly forgotten (A-04).
"""

import os
import shlex
from pathlib import Path
from typing import List, Optional

from ...core.capabilities import Capabilities
from ...core.exceptions import ProviderError
from ...core.mapping import asks_for_something
from ...core.naming import check_name, safe_filename
from ...core.plan import Plan, Step, StepKind
from ...core.platform import CPU_HOST_MODEL, CPU_HOST_PASSTHROUGH
from ...core.slots import place
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy, Substitution, TranslationReport, Translator
from ...core.vmconfig import (
    DEFAULT_DISK_MB,
    DeviceKind,
    NetworkConfig,
    VMConfig,
    keeping_existing_images,
)
from .capabilities import QemuCapabilities
from .tables import (
    BOOT_LETTER,
    BUS_DEVICES,
    FORMAT_TO_DRIVER,
    NETWORK_TO_NETDEV,
    NIC_MODEL_TO_QEMU,
)

#: Settings QEMU has no way to express. Same idea as the libvirt list: a setting
#: that disappears without a word is how a config comes to describe a machine that
#: does not exist.
UNTRANSLATABLE = (
    ("guest_os", "QEMU has no guest OS field; it boots what the disk contains"),
    ("memory.vram_mb", "video memory belongs to the display device, not the VM"),
    ("cpu.execution_cap", "a CPU cap needs cgroup limits outside QEMU"),
    ("cpu.hotplug", "CPU hotplug needs a -smp maxcpus reservation vmctl does not emit"),
    ("memory.page_fusion", "QEMU has no page-fusion setting (KSM is host-wide)"),
    ("clipboard_mode", "clipboard sharing needs a SPICE agent channel"),
    ("draganddrop", "drag and drop needs a SPICE agent channel"),
    ("firmware.tpm", "a TPM needs an external swtpm socket"),
    ("firmware.secure_boot", "secure boot needs an OVMF variables file per VM"),
)


class QemuEmitter:
    """Turn a :class:`VMConfig` into a QEMU command line and a plan."""

    def __init__(
        self,
        vm_name: str,
        location: Optional[StorageLocation] = None,
        binary: str = "qemu-system-x86_64",
        accel: Optional[str] = None,
        capabilities: Optional[Capabilities] = None,
        policy: Policy = Policy.STRICT,
    ):
        """Initialise the emitter.

        Args:
            vm_name: The VM's name, which is also its directory.
            location: Where images and the script go. Per-VM directories, because
                a VM here *is* a directory.
            binary: The QEMU binary to run.
            accel: ``kvm`` or ``tcg``; the backend decides from ``/dev/kvm``.
            capabilities: What this build supports.
            policy: What to do about values QEMU cannot express.
        """
        self.vm_name = vm_name
        self.capabilities = capabilities or QemuCapabilities.get()
        self.policy = policy
        self.location = location or directory(
            os.path.expanduser("~/.local/share/vmctl/qemu"), nest_per_vm=True
        )
        self.binary = binary
        self.accel = accel or "tcg"
        self.report: Optional[TranslationReport] = None

    # -- paths ---------------------------------------------------------------

    def script_path(self, vm_name: str) -> str:
        """Return the path of the VM's run script -- its native artifact."""
        return self.location.image_path(vm_name, "run.sh")

    def pidfile_path(self, vm_name: str) -> str:
        """Return the path of the pidfile the script writes."""
        return self.location.image_path(vm_name, "qemu.pid")

    def image_path(self, vm: VMConfig, device, fmt=None) -> str:
        """Return where a device's image lives.

        Args:
            vm: The VM it belongs to.
            device: The device.
            fmt: The format it will actually be created in. Using the device's own
                format instead produced ``moved_root.vmdk`` holding a qcow2 after a
                migration: the file worked, because QEMU is told its format, but the
                name said something else (F-34).
        """
        spec = self.capabilities.format_spec(
            fmt or device.format or self.capabilities.native_format
        )
        return self.location.image_path(
            vm.name, f"{vm.name}_{safe_filename(device.name)}.{spec.extension}"
        )

    # -- the command line ----------------------------------------------------

    def build_argv(self, vm: VMConfig, translator: Optional[Translator] = None) -> List[str]:
        """Return the whole QEMU command line for *vm*.

        Args:
            vm: The configuration to express.
            translator: Records anything that could not be expressed exactly.

        Returns:
            The argv, starting with the binary.
        """
        argv: List[str] = [self.binary, "-name", vm.name]
        argv += ["-machine", self._machine(vm, translator)]
        argv += ["-accel", self.accel]
        argv += ["-m", str(vm.memory.mb)]
        argv += ["-smp", self._smp(vm)]

        cpu = self._cpu_model(vm)
        if cpu:
            argv += ["-cpu", cpu]

        order = "".join(BOOT_LETTER[d] for d in vm.boot.order if d in BOOT_LETTER)
        if order:
            argv += ["-boot", f"order={order}"]

        argv += self._storage_argv(vm, translator)
        argv += self._network_argv(vm, translator)

        # No window: a VM vmctl starts is a background process, and a display
        # would tie it to whoever ran the command.
        argv += ["-display", "none"]
        argv += ["-pidfile", self.pidfile_path(vm.name), "-daemonize"]

        if translator is not None:
            for path, reason in UNTRANSLATABLE:
                # A field at its default asks for nothing; see
                # :func:`~vmctl.core.mapping.asks_for_something`.
                if asks_for_something(vm, path):
                    translator.drop(path, _get(vm, path), reason)
        return argv

    def _machine(self, vm: VMConfig, translator: Optional[Translator]) -> str:
        """Return the machine type, substituting one this build has not got."""
        default = self.capabilities.default_machine or "q35"
        wanted = vm.machine
        if not wanted:
            return default
        if wanted in self.capabilities.machine_types:
            return wanted
        if translator is not None:
            translator.report.substitutions.append(
                Substitution(
                    "machine",
                    wanted,
                    default,
                    f"this build offers {', '.join(self.capabilities.machine_types)}",
                )
            )
        return default

    @staticmethod
    def _smp(vm: VMConfig) -> str:
        """Return the ``-smp`` value, stating a topology when one is asked for."""
        parts = [str(vm.cpu.count)]
        for name, value in (
            ("sockets", vm.cpu.sockets),
            ("cores", vm.cpu.cores),
            ("threads", vm.cpu.threads),
        ):
            if value:
                parts.append(f"{name}={value}")
        return ",".join(parts)

    @staticmethod
    def _cpu_model(vm: VMConfig) -> Optional[str]:
        """Return the ``-cpu`` value, or None to leave QEMU's default.

        Nested virtualisation needs the host's own CPU exposed, which is what
        ``host`` means here -- the same reason libvirt uses host-passthrough.
        """
        model = (vm.cpu.model or "").strip()
        if vm.cpu.nested_virt or model == CPU_HOST_PASSTHROUGH:
            return "host"
        if model == CPU_HOST_MODEL:
            # QEMU's nearest equivalent: the best model it can name for this host.
            return "max"
        return model or None

    # -- storage -------------------------------------------------------------

    def _storage_argv(self, vm: VMConfig, translator: Optional[Translator]) -> List[str]:
        """Return the drives, their devices, and the controllers they need."""
        argv: List[str] = []
        placements = place(vm, self.capabilities)
        controllers: List[str] = []

        for index, (device, placement) in enumerate(zip(vm.storage, placements)):
            bus = (
                translator.bus_for(device, f"storage[{index}].bus")
                if translator is not None
                else device.bus
            )
            spec = BUS_DEVICES.get(bus)
            if spec is None:
                raise ProviderError(
                    f"device {device.name!r} asks for the {bus.value!r} bus, "
                    f"which this QEMU build does not have"
                )

            kind_device = {
                DeviceKind.DISK: spec.disk,
                DeviceKind.CDROM: spec.cdrom,
                DeviceKind.FLOPPY: spec.floppy,
            }.get(device.kind)
            if kind_device is None:
                raise ProviderError(
                    f"the {bus.value} bus carries no {device.kind.value} devices "
                    f"in this QEMU build"
                )

            controller_id = f"{bus.value}0"
            if spec.controller and controller_id not in controllers:
                controllers.append(controller_id)
                argv += ["-device", spec.controller.format(bus=controller_id)]

            drive_id = f"drive-{safe_filename(device.name)}"
            argv += ["-drive", self._drive(vm, device, drive_id, translator, index)]
            argv += [
                "-device",
                kind_device.format(bus=controller_id, port=placement.port, drive=drive_id),
            ]
        return argv

    def _drive(self, vm: VMConfig, device, drive_id: str, translator, index: int) -> str:
        """Return one ``-drive`` value.

        ``if=none`` throughout: the drive is the backing store and the ``-device``
        is what the guest sees, which is the only form that lets vmctl choose the
        bus rather than letting QEMU guess from the file name.
        """
        # `source` means "attach this image"; `disk_path` only says where the
        # device was read *from*, which for a migrated VM is a path on another
        # host. Using it here pointed a QEMU command line at C:\vms\sys.vdi.
        if device.is_removable:
            source = device.source or ""
            parts = [f"file={source}", "if=none", f"id={drive_id}"]
            parts.append("media=cdrom" if device.kind is DeviceKind.CDROM else "media=disk")
        else:
            chosen = (
                translator.format_for(device, f"storage[{index}].format")
                if translator is not None
                else device.format or self.capabilities.native_format
            )
            source = device.source or self.image_path(vm, device, chosen)
            parts = [f"file={source}", "if=none", f"id={drive_id}"]
            parts.append(f"format={FORMAT_TO_DRIVER.get(chosen, 'qcow2')}")
        if device.readonly:
            parts.append("readonly=on")
        if device.discard:
            parts.append("discard=unmap")
        return ",".join(parts)

    # -- networking ----------------------------------------------------------

    def _network_argv(self, vm: VMConfig, translator: Optional[Translator]) -> List[str]:
        """Return the netdevs and their devices."""
        argv: List[str] = []
        for index, net in enumerate(vm.networks):
            backend = NETWORK_TO_NETDEV.get(net.network_type)
            netdev_id = f"net{index}"
            if backend is None:
                if translator is not None:
                    translator.drop(
                        f"networks[{index}].network_type",
                        net.network_type.value,
                        "QEMU has only user networking and a bridge helper; a "
                        "host-only or internal network is created by a manager",
                    )
                backend = "user"
            netdev = f"{backend},id={netdev_id}"
            if backend == "bridge" and net.adapter_name:
                netdev += f",br={net.adapter_name}"
            argv += ["-netdev", netdev]
            argv += [
                "-device",
                self._nic_device(net, netdev_id, f"networks[{index}].model", translator),
            ]
        return argv

    def _nic_device(
        self,
        net: NetworkConfig,
        netdev_id: str,
        where: str,
        translator: Optional[Translator],
    ) -> str:
        """Return the NIC ``-device`` value."""
        native = NIC_MODEL_TO_QEMU.get(net.model)
        if native is None:
            fallback = self.capabilities.nic_model_fallback()
            if translator is not None and fallback is not None:
                translator.report.substitutions.append(
                    Substitution(
                        where,
                        net.model.value,
                        fallback.value,
                        "this QEMU build has no such network device",
                    )
                )
            native = NIC_MODEL_TO_QEMU.get(fallback, "e1000") if fallback else "e1000"
        device = f"{native},netdev={netdev_id}"
        if net.mac_address:
            device += f",mac={_format_mac(net.mac_address)}"
        return device

    # -- plans ---------------------------------------------------------------

    def render_script(self, argv: List[str]) -> str:
        """Return the run script: the argv, readable and runnable.

        One option per line, quoted by :mod:`shlex`, so a person can read it, a
        shell can run it, and the parser can read it back -- which is what makes
        this provider's "native format" a real one rather than a vmctl file.
        """
        head = argv[0]
        rest = argv[1:]
        lines = [
            "#!/bin/sh",
            "# Generated by vmctl. This file *is* the VM: QEMU keeps no",
            "# configuration of its own, so `vmctl edit` rewrites it in place.",
            f"exec {shlex.quote(head)} \\",
        ]
        pairs: List[str] = []
        position = 0
        while position < len(rest):
            option = rest[position]
            if position + 1 < len(rest) and not rest[position + 1].startswith("-"):
                pairs.append(f"  {option} {shlex.quote(rest[position + 1])}")
                position += 2
            else:
                pairs.append(f"  {option}")
                position += 1
        lines += [f"{pair} \\" for pair in pairs[:-1]] + [pairs[-1]] if pairs else []
        return "\n".join(lines) + "\n"

    def emit_create_vm(self, vm: VMConfig) -> Plan:
        """Return the plan that creates *vm*: images, then the script.

        Nothing is started. A VM here is a directory with a script in it, so
        "created" means the script exists and its disks do.
        """
        check_name(vm.name, self.capabilities)
        plan = Plan("qemu")
        translator = Translator(self.capabilities, self.policy)

        target = self.location.directory_for(vm.name)
        plan.add(
            Step(
                kind=StepKind.EXEC,
                description=f"make the VM's directory {target}",
                argv=["mkdir", "-p", target],
            )
        )

        for index, device in enumerate(vm.storage):
            if device.is_removable or device.source:
                continue  # inserted, or an image that already exists
            chosen = translator.format_for(device, f"storage[{index}].format")
            allocation = translator.allocation_for(device, chosen, f"storage[{index}]")
            path = self.image_path(vm, device, chosen)
            argv = [
                "qemu-img",
                "create",
                "-f",
                FORMAT_TO_DRIVER.get(chosen, "qcow2"),
            ]
            if allocation.value == "thick":
                argv += ["-o", "preallocation=full"]
            argv += [path, f"{device.size_mb or DEFAULT_DISK_MB}M"]
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=f"create the {device.name} image",
                    argv=argv,
                    undo=Step(
                        kind=StepKind.EXEC,
                        description=f"remove the {device.name} image",
                        argv=["rm", "-f", path],
                        destructive=True,
                    ),
                )
            )

        script = self.script_path(vm.name)
        plan.add(
            Step(
                kind=StepKind.WRITE_FILE,
                description=f"write the QEMU command line to {script}",
                path=Path(script),
                content=self.render_script(self.build_argv(vm, translator)),
                undo=Step(
                    kind=StepKind.EXEC,
                    description="remove the command line",
                    argv=["rm", "-f", script],
                    destructive=True,
                ),
            )
        )
        plan.add(
            Step(
                kind=StepKind.EXEC,
                description="make the command line executable",
                argv=["chmod", "+x", script],
            )
        )

        translator.finish()
        for line in translator.report.lines():
            plan.warn(line)
        self.report = translator.report
        plan.report = translator.report
        return plan

    def emit_modify_vm(self, current: VMConfig, desired: VMConfig) -> Plan:
        """Return the plan that rewrites the command line.

        There is nothing to modify in place: the script is the configuration, so
        editing means writing a new one. Unlike VirtualBox, that costs nothing --
        and unlike libvirt, it does not even need the hypervisor's agreement.
        """
        # Never over the VM's own images: the creation plan makes disks (F-39).
        plan = self.emit_create_vm(keeping_existing_images(current, desired))
        if _running_shape(current) != _running_shape(desired):
            plan.warn(
                "the command line changed; the VM has to be restarted for it to "
                "take effect, because QEMU reads it only at startup"
            )
        return plan


def _running_shape(vm: VMConfig):
    """Return the parts of a config a running QEMU process cannot be told about."""
    return (vm.memory.mb, vm.cpu.count, len(vm.storage), len(vm.networks))


def _format_mac(mac: str) -> str:
    """Return a MAC in QEMU's colon-separated form.

    A config may carry VirtualBox's unseparated spelling (``080027AA0001``), which
    QEMU rejects outright.
    """
    clean = mac.replace(":", "").replace("-", "").strip()
    if len(clean) != 12:
        return mac
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2)).lower()


def _get(source, path: str):
    """Read a dotted path off a configuration."""
    value = source
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value
