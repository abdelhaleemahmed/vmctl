"""
Which operating system the guest runs, in neutral terms.

``ostype: "Ubuntu_64"`` was the last vendor string left in the canonical model,
and the map from what VirtualBox *reports* to what it *accepts* lived inside the
emitter -- the wrong layer twice over (A-05 in PLAN.md). Every hypervisor names
guests differently:

* VirtualBox has an id per version (``Ubuntu22_LTS_64``) and reports the
  description (``Ubuntu 22.04 LTS (Jammy Jellyfish) (64-bit)``);
* libvirt names none of it in the domain itself and carries a `libosinfo`_ id in
  metadata (``http://ubuntu.com/ubuntu/22.04``);
* Hyper-V has no guest OS field at all, only a VM *generation*.

So the model holds a **short id in libosinfo's style** -- ``ubuntu22.04``,
``rhel9``, ``win11`` -- and each provider translates. libosinfo is the right
vocabulary to borrow because it is the one virt-install, GNOME Boxes and
virt-manager already use, so vmctl is not inventing a third convention.

The catalogue is deliberately small: the guests people actually run, plus a
generic per family. Anything outside it is **passed through verbatim** -- a raw
provider string in a config keeps working, and is the reason nothing here has to
be exhaustive.

.. _libosinfo: https://libosinfo.org/
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class OSFamily(Enum):
    """The family a guest belongs to.

    Coarse on purpose: this is what a provider falls back to when it cannot
    express a version, and what decides the defaults that follow from the guest
    (firmware, NIC model, whether virtio drivers exist).
    """

    LINUX = "linux"
    WINDOWS = "windows"
    BSD = "bsd"
    MACOS = "macos"
    SOLARIS = "solaris"
    OTHER = "other"


@dataclass(frozen=True)
class GuestOS:
    """One guest operating system.

    Attributes:
        id: Short neutral id, libosinfo style (``ubuntu22.04``, ``win11``).
        family: Which family it belongs to.
        name: Human-readable name, for messages and ``--help`` output.
        version: Version as its vendor writes it, when there is one.
        bits: Word size the guest expects, 64 unless it cannot be.
    """

    id: str
    family: OSFamily
    name: str
    version: Optional[str] = None
    bits: int = 64

    @property
    def is_windows(self) -> bool:
        """Whether this guest is a Windows one."""
        return self.family is OSFamily.WINDOWS


def _os(id_: str, family: OSFamily, name: str, version: Optional[str] = None, bits: int = 64):
    return GuestOS(id=id_, family=family, name=name, version=version, bits=bits)


_L, _W, _B, _M, _S, _O = (
    OSFamily.LINUX,
    OSFamily.WINDOWS,
    OSFamily.BSD,
    OSFamily.MACOS,
    OSFamily.SOLARIS,
    OSFamily.OTHER,
)

#: The guests vmctl can name neutrally. Keyed by id; the ids match libosinfo's
#: short ids so that a config can be handed to virt-install and understood.
CATALOG: Dict[str, GuestOS] = {
    entry.id: entry
    for entry in (
        # Linux -- one generic per distribution, plus the versions in support.
        _os("linux", _L, "Linux (generic)"),
        _os("ubuntu", _L, "Ubuntu"),
        _os("ubuntu20.04", _L, "Ubuntu 20.04 LTS", "20.04"),
        _os("ubuntu22.04", _L, "Ubuntu 22.04 LTS", "22.04"),
        _os("ubuntu24.04", _L, "Ubuntu 24.04 LTS", "24.04"),
        _os("debian", _L, "Debian"),
        _os("debian11", _L, "Debian 11 Bullseye", "11"),
        _os("debian12", _L, "Debian 12 Bookworm", "12"),
        _os("rhel", _L, "Red Hat Enterprise Linux"),
        _os("rhel8", _L, "Red Hat Enterprise Linux 8", "8"),
        _os("rhel9", _L, "Red Hat Enterprise Linux 9", "9"),
        _os("centos7", _L, "CentOS 7", "7"),
        _os("fedora", _L, "Fedora"),
        _os("opensuse", _L, "openSUSE"),
        _os("oracle9", _L, "Oracle Linux 9", "9"),
        _os("archlinux", _L, "Arch Linux"),
        _os("alpine", _L, "Alpine Linux"),
        # Windows
        _os("win10", _W, "Windows 10", "10"),
        _os("win11", _W, "Windows 11", "11"),
        _os("win2019", _W, "Windows Server 2019", "2019"),
        _os("win2022", _W, "Windows Server 2022", "2022"),
        # The rest
        _os("freebsd", _B, "FreeBSD"),
        _os("openbsd", _B, "OpenBSD"),
        _os("macos", _M, "macOS"),
        _os("solaris11", _S, "Oracle Solaris 11", "11"),
        _os("other", _O, "Other or unknown"),
    )
}

#: What a config with nothing said means. ``ubuntu`` rather than ``linux`` only
#: because 1.1.x defaulted to ``Ubuntu_64`` and that must not change silently.
DEFAULT_ID = "ubuntu"

#: The generic member of each family, for a provider that cannot express a
#: version. Every family has one, so a translation never has to guess.
GENERIC_BY_FAMILY: Dict[OSFamily, str] = {
    OSFamily.LINUX: "linux",
    OSFamily.WINDOWS: "win10",
    OSFamily.BSD: "freebsd",
    OSFamily.MACOS: "macos",
    OSFamily.SOLARIS: "solaris11",
    OSFamily.OTHER: "other",
}


def get(id_: str) -> Optional[GuestOS]:
    """Return a catalogue entry by id, or None when it is not one.

    Args:
        id_: A neutral id, matched case-insensitively.

    Returns:
        The entry, or None -- which means "not a neutral id", not "invalid":
        provider strings are legal in a config and pass through.
    """
    return CATALOG.get(id_.strip().lower()) if id_ else None


def ids() -> List[str]:
    """Return every neutral id, in catalogue order."""
    return list(CATALOG)


def family_of(id_: str) -> OSFamily:
    """Return the family of a guest id, or ``OTHER`` when it is not in the catalogue.

    Args:
        id_: A neutral id, or a provider's own string.
    """
    entry = get(id_)
    return entry.family if entry else OSFamily.OTHER


def describe(id_: str) -> str:
    """Return a human name for a guest id, falling back to the id itself.

    A provider string passed through is shown as it was written, since that is
    what the user will recognise.
    """
    entry = get(id_)
    return entry.name if entry else id_
