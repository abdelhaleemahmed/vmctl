#!/usr/bin/env python3
"""Probe which (device kind, bus) pairs this QEMU build will actually start.

The same method as the VirtualBox and libvirt matrices: ask the product rather
than remember. Each pair is tried by starting QEMU with the device attached and
the guest stopped (``-S``), which is enough for QEMU to build the machine and
refuse anything it cannot; the process is then killed.

    python scripts/probe-qemu-matrix.py > tests/fixtures/qemu_attach_matrix.json
"""

import json
import os
import re
import subprocess
import sys
import tempfile

BINARY = os.environ.get("QEMU", "/usr/libexec/qemu-kvm")
MACHINE = os.environ.get("QEMU_MACHINE", "q35")

#: Per bus: the controller to add (if any), and the device per kind. ``{bus}`` is
#: the controller id and ``{drive}`` the drive id.
BUSES = {
    "ide": {
        "controller": None,  # the machine's own IDE bus, when it has one
        "disk": "ide-hd,drive={drive}",
        "cdrom": "ide-cd,drive={drive}",
    },
    "sata": {
        "controller": "ich9-ahci,id={bus}",
        "disk": "ide-hd,bus={bus}.0,drive={drive}",
        "cdrom": "ide-cd,bus={bus}.0,drive={drive}",
    },
    "scsi": {
        "controller": "lsi53c895a,id={bus}",
        "disk": "scsi-hd,bus={bus}.0,drive={drive}",
        "cdrom": "scsi-cd,bus={bus}.0,drive={drive}",
    },
    "sas": {
        "controller": "megasas,id={bus}",
        "disk": "scsi-hd,bus={bus}.0,drive={drive}",
        "cdrom": "scsi-cd,bus={bus}.0,drive={drive}",
    },
    "nvme": {
        "controller": None,
        "disk": "nvme,drive={drive},serial=probe",
        "cdrom": "nvme,drive={drive},serial=probe",
    },
    "virtio-blk": {
        "controller": None,
        "disk": "virtio-blk-pci,drive={drive}",
        "cdrom": "virtio-blk-pci,drive={drive}",
    },
    "virtio-scsi": {
        "controller": "virtio-scsi-pci,id={bus}",
        "disk": "scsi-hd,bus={bus}.0,drive={drive}",
        "cdrom": "scsi-cd,bus={bus}.0,drive={drive}",
    },
    "usb": {
        "controller": "qemu-xhci,id={bus}",
        "disk": "usb-storage,bus={bus}.0,drive={drive}",
        "cdrom": "usb-storage,bus={bus}.0,drive={drive}",
    },
    "floppy": {
        "controller": "isa-fdc,id={bus}",
        "floppy": "floppy,unit=0,drive={drive}",
    },
}

KINDS = ("disk", "cdrom", "floppy")


def attempt(kind, bus, image):
    """Start QEMU with one device attached and return its error, or None."""
    spec = BUSES[bus]
    device = spec.get(kind)
    if device is None:
        return f"vmctl does not map {kind} onto {bus}"
    argv = [
        BINARY,
        "-machine",
        MACHINE,
        "-m",
        "128",
        "-display",
        "none",
        "-S",  # build the machine, do not run the guest
        "-monitor",
        "none",
        "-serial",
        "none",
        "-nodefaults",
        "-drive",
        f"file={image},if=none,id=d0,format=qcow2" + (",media=cdrom" if kind == "cdrom" else ""),
    ]
    controller = spec.get("controller")
    if controller:
        argv += ["-device", controller.format(bus="probe0")]
    argv += ["-device", device.format(bus="probe0", drive="d0")]

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=6)
    except subprocess.TimeoutExpired:
        return None  # it started and stayed up: accepted
    error = (proc.stderr or proc.stdout).strip().splitlines()
    return error[-1][:160] if error else f"exited {proc.returncode}"


def main():
    with tempfile.TemporaryDirectory() as workdir:
        image = os.path.join(workdir, "probe.qcow2")
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", image, "16M"],
            check=True,
            capture_output=True,
        )
        attach, notes = {}, {}
        for bus in BUSES:
            for kind in KINDS:
                error = attempt(kind, bus, image)
                attach[f"{kind}|{bus}"] = error is None
                if error:
                    notes[f"{kind}|{bus}"] = error

    version = subprocess.run(
        [BINARY, "--version"], capture_output=True, text=True
    ).stdout.splitlines()[0]
    match = re.search(r"(\d+\.\d+\.\d+)", version)
    json.dump(
        {
            "attach": dict(sorted(attach.items())),
            "machine": MACHINE,
            "qemu_version": match.group(1) if match else version,
            "notes": dict(sorted(notes.items())),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    print()


if __name__ == "__main__":
    main()
