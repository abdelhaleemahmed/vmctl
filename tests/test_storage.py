"""Storage locations (A-09).

The point is that one question -- "where does a new image go, and how is its path
spelled?" -- has one shape of answer, whatever the hypervisor. Before this the name
differed per provider and callers guessed: `migrate` had a helper that tried both,
and the conformance suite stubbed two attributes.
"""

import pytest

from vmctl.core.storage import LocationKind, StorageLocation, directory


def test_a_flat_location_puts_images_side_by_side():
    loc = directory("/var/lib/libvirt/images")
    assert loc.image_path("web", "web_root.qcow2") == ("/var/lib/libvirt/images/web_root.qcow2")
    assert loc.directory_for("web") == "/var/lib/libvirt/images"


def test_a_nesting_location_gives_each_vm_its_own_directory():
    """VirtualBox does this; libvirt does not."""
    loc = directory("/vms", nest_per_vm=True)
    assert loc.image_path("web", "web_root.vdi") == "/vms/web/web_root.vdi"
    assert loc.directory_for("web") == "/vms/web"


def test_a_trailing_separator_does_not_double_up():
    assert directory("/vms/").image_path("web", "d.qcow2") == "/vms/d.qcow2"
    assert directory("C:\\vms\\", separator="\\").image_path("web", "d.vdi") == ("C:\\vms\\d.vdi")


def test_the_separator_is_the_targets_not_this_machines():
    """A plan built on Linux can target a Windows VirtualBox, so the separator
    belongs to the location rather than to the machine running vmctl."""
    loc = directory(r"C:\vms", separator="\\", nest_per_vm=True)
    assert loc.image_path("web", "web_root.vdi") == r"C:\vms\web\web_root.vdi"


def test_a_pool_cannot_be_addressed_by_path():
    """A libvirt pool or a Proxmox storage id is resolved by the hypervisor, so
    building a path into one would be wrong rather than merely unsupported."""
    pool = StorageLocation(kind=LocationKind.POOL, value="default")
    with pytest.raises(NotImplementedError, match="storage pool"):
        pool.image_path("web", "d.qcow2")


def test_a_location_can_be_redirected_without_losing_its_rules():
    loc = directory(r"C:\vms", separator="\\", nest_per_vm=True)
    moved = loc.with_value(r"D:\other")
    assert moved.value == r"D:\other"
    assert moved.separator == "\\"
    assert moved.nest_per_vm is True


def test_a_location_describes_itself_for_messages():
    assert directory("/vms").describe() == "directory /vms"


def test_locations_are_immutable():
    """A provider hands one out; a caller must not be able to change it underneath."""
    with pytest.raises(Exception):
        directory("/vms").value = "/elsewhere"


# ---------------------------------------------------------------------------
# What each provider declares
# ---------------------------------------------------------------------------


def test_virtualbox_nests_each_vm(monkeypatch):
    """Asking VirtualBox where its machine folder is means running VBoxManage, so
    the answer is stubbed; what is under test is the nesting policy."""
    from vmctl.providers.virtualbox.backend import VirtualBoxBackend

    monkeypatch.setattr(
        VirtualBoxBackend, "machine_folder", "/home/u/VirtualBox VMs", raising=False
    )
    loc = VirtualBoxBackend().storage_location()
    assert loc.kind is LocationKind.DIRECTORY
    assert loc.nest_per_vm is True, "VirtualBox gives each VM its own folder"


def test_libvirt_keeps_images_flat():
    from vmctl.providers.libvirt.backend import LibvirtBackend

    loc = LibvirtBackend().storage_location()
    assert loc.kind is LocationKind.DIRECTORY
    assert loc.nest_per_vm is False


def test_libvirts_location_follows_the_connection():
    """A session connection cannot write to the system image store."""
    from vmctl.providers.libvirt.backend import LibvirtBackend

    system = LibvirtBackend(connect="qemu:///system").storage_location()
    session = LibvirtBackend(connect="qemu:///session").storage_location()
    assert system.value == "/var/lib/libvirt/images"
    assert session.value != system.value
