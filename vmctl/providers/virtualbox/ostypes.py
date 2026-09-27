"""
Every guest OS type VirtualBox knows, and how it names them.

**Generated -- do not edit.** ``scripts/generate-ostypes.py`` builds this from
``tests/fixtures/vbox_ostypes.txt``, captured with ``VBoxManage list ostypes``.

The reason it exists: ``showvminfo`` reports a guest's *description* ("Ubuntu
(64-bit)") while ``createvm --ostype`` accepts only its *id* ("Ubuntu_64"), and
answers "Unknown or invalid guest OS type given" to anything else. The emitter
used to carry a hand-written map of about forty descriptions, so exporting a VM
running anything else produced a config that could not be imported (F-29).

Source: VirtualBox 7.1.18, 227 types.
"""

from typing import Dict, NamedTuple


class OSType(NamedTuple):
    """One entry of ``VBoxManage list ostypes``."""

    id: str
    description: str
    family: str
    architecture: str


#: Every type, by the id ``--ostype`` takes.
OSTYPES: Dict[str, OSType] = {
    "Other": OSType("Other", "Other/Unknown", "Other", "x86"),
    "Other_64": OSType("Other_64", "Other/Unknown (64-bit)", "Other", "x86 (64-bit)"),
    "Other_arm64": OSType("Other_arm64", "Other/Unknown (ARM 64-bit)", "Other", "ARMv8 (64-bit)"),
    "Windows31": OSType("Windows31", "Windows 3.1", "Windows", "x86"),
    "Windows95": OSType("Windows95", "Windows 95", "Windows", "x86"),
    "Windows98": OSType("Windows98", "Windows 98", "Windows", "x86"),
    "WindowsMe": OSType("WindowsMe", "Windows ME", "Windows", "x86"),
    "WindowsNT3x": OSType("WindowsNT3x", "Windows NT 3.x", "Windows", "x86"),
    "WindowsNT4": OSType("WindowsNT4", "Windows NT 4", "Windows", "x86"),
    "Windows2000": OSType("Windows2000", "Windows 2000", "Windows", "x86"),
    "WindowsXP": OSType("WindowsXP", "Windows XP (32-bit)", "Windows", "x86"),
    "WindowsXP_64": OSType("WindowsXP_64", "Windows XP (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows2003": OSType("Windows2003", "Windows Server 2003 (32-bit)", "Windows", "x86"),
    "Windows2003_64": OSType(
        "Windows2003_64", "Windows Server 2003 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "WindowsVista": OSType("WindowsVista", "Windows Vista (32-bit)", "Windows", "x86"),
    "WindowsVista_64": OSType(
        "WindowsVista_64", "Windows Vista (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows2008": OSType("Windows2008", "Windows Server 2008 (32-bit)", "Windows", "x86"),
    "Windows2008_64": OSType(
        "Windows2008_64", "Windows Server 2008 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows7": OSType("Windows7", "Windows 7 (32-bit)", "Windows", "x86"),
    "Windows7_64": OSType("Windows7_64", "Windows 7 (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows8": OSType("Windows8", "Windows 8 (32-bit)", "Windows", "x86"),
    "Windows8_64": OSType("Windows8_64", "Windows 8 (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows81": OSType("Windows81", "Windows 8.1 (32-bit)", "Windows", "x86"),
    "Windows81_64": OSType("Windows81_64", "Windows 8.1 (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows2012_64": OSType(
        "Windows2012_64", "Windows Server 2012 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows10": OSType("Windows10", "Windows 10 (32-bit)", "Windows", "x86"),
    "Windows10_64": OSType("Windows10_64", "Windows 10 (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows2016_64": OSType(
        "Windows2016_64", "Windows Server 2016 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows2019_64": OSType(
        "Windows2019_64", "Windows Server 2019 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows11_64": OSType("Windows11_64", "Windows 11 (64-bit)", "Windows", "x86 (64-bit)"),
    "Windows2022_64": OSType(
        "Windows2022_64", "Windows Server 2022 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "Windows2025_64": OSType(
        "Windows2025_64", "Windows Server 2025 (64-bit)", "Windows", "x86 (64-bit)"
    ),
    "WindowsNT": OSType("WindowsNT", "Other Windows (32-bit)", "Windows", "x86"),
    "WindowsNT_64": OSType("WindowsNT_64", "Other Windows (64-bit)", "Windows", "x86 (64-bit)"),
    "Linux22": OSType("Linux22", "Linux 2.2 (32-bit)", "Linux", "x86"),
    "Linux24": OSType("Linux24", "Linux 2.4 (32-bit)", "Linux", "x86"),
    "Linux24_64": OSType("Linux24_64", "Linux 2.4 (64-bit)", "Linux", "x86 (64-bit)"),
    "Linux26": OSType("Linux26", "Linux 2.6 / 3.x / 4.x / 5.x (32-bit)", "Linux", "x86"),
    "Linux26_64": OSType(
        "Linux26_64", "Linux 2.6 / 3.x / 4.x / 5.x (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "ArchLinux": OSType("ArchLinux", "Arch Linux (32-bit)", "Linux", "x86"),
    "ArchLinux_64": OSType("ArchLinux_64", "Arch Linux (64-bit)", "Linux", "x86 (64-bit)"),
    "ArchLinux_arm64": OSType(
        "ArchLinux_arm64", "Arch Linux (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Debian": OSType("Debian", "Debian (32-bit)", "Linux", "x86"),
    "Debian_64": OSType("Debian_64", "Debian (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian_arm64": OSType("Debian_arm64", "Debian (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "Debian31": OSType("Debian31", "Debian 3.1 Sarge (32-bit)", "Linux", "x86"),
    "Debian4": OSType("Debian4", "Debian 4.0 Etch (32-bit)", "Linux", "x86"),
    "Debian4_64": OSType("Debian4_64", "Debian 4.0 Etch (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian5": OSType("Debian5", "Debian 5.0 Lenny (32-bit)", "Linux", "x86"),
    "Debian5_64": OSType("Debian5_64", "Debian 5.0 Lenny (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian6": OSType("Debian6", "Debian 6.0 Squeeze (32-bit)", "Linux", "x86"),
    "Debian6_64": OSType("Debian6_64", "Debian 6.0 Squeeze (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian7": OSType("Debian7", "Debian 7 Wheezy (32-bit)", "Linux", "x86"),
    "Debian7_64": OSType("Debian7_64", "Debian 7 Wheezy (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian8": OSType("Debian8", "Debian 8 Jessie (32-bit)", "Linux", "x86"),
    "Debian8_64": OSType("Debian8_64", "Debian 8 Jessie (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian9": OSType("Debian9", "Debian 9 Stretch (32-bit)", "Linux", "x86"),
    "Debian9_64": OSType("Debian9_64", "Debian 9 Stretch (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian9_arm64": OSType(
        "Debian9_arm64", "Debian 9 Stretch (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Debian10": OSType("Debian10", "Debian 10 Buster (32-bit)", "Linux", "x86"),
    "Debian10_64": OSType("Debian10_64", "Debian 10 Buster (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian10_arm64": OSType(
        "Debian10_arm64", "Debian 10 Buster (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Debian11": OSType("Debian11", "Debian 11 Bullseye (32-bit)", "Linux", "x86"),
    "Debian11_64": OSType("Debian11_64", "Debian 11 Bullseye (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian11_arm64": OSType(
        "Debian11_arm64", "Debian 11 Bullseye (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Debian12": OSType("Debian12", "Debian 12 Bookworm (32-bit)", "Linux", "x86"),
    "Debian12_64": OSType("Debian12_64", "Debian 12 Bookworm (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian12_arm64": OSType(
        "Debian12_arm64", "Debian 12 Bookworm (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Debian13_64": OSType("Debian13_64", "Debian 13 Trixie (64-bit)", "Linux", "x86 (64-bit)"),
    "Debian13_arm64": OSType(
        "Debian13_arm64", "Debian 13 Trixie (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Fedora": OSType("Fedora", "Fedora (32-bit)", "Linux", "x86"),
    "Fedora_64": OSType("Fedora_64", "Fedora (64-bit)", "Linux", "x86 (64-bit)"),
    "Fedora_arm64": OSType("Fedora_arm64", "Fedora (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "Gentoo": OSType("Gentoo", "Gentoo (32-bit)", "Linux", "x86"),
    "Gentoo_64": OSType("Gentoo_64", "Gentoo (64-bit)", "Linux", "x86 (64-bit)"),
    "Mandriva": OSType("Mandriva", "Mandriva (32-bit)", "Linux", "x86"),
    "Mandriva_64": OSType("Mandriva_64", "Mandriva (64-bit)", "Linux", "x86 (64-bit)"),
    "OpenMandriva_Lx": OSType("OpenMandriva_Lx", "OpenMandriva Lx (32-bit)", "Linux", "x86"),
    "OpenMandriva_Lx_64": OSType(
        "OpenMandriva_Lx_64", "OpenMandriva Lx (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "PCLinuxOS": OSType("PCLinuxOS", "PCLinuxOS / PCLOS (32-bit)", "Linux", "x86"),
    "PCLinuxOS_64": OSType("PCLinuxOS_64", "PCLinuxOS / PCLOS (64-bit)", "Linux", "x86 (64-bit)"),
    "Mageia": OSType("Mageia", "Mageia (32-bit)", "Linux", "x86"),
    "Mageia_64": OSType("Mageia_64", "Mageia (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle": OSType("Oracle", "Oracle Linux (32-bit)", "Linux", "x86"),
    "Oracle_64": OSType("Oracle_64", "Oracle Linux (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle_arm64": OSType("Oracle_arm64", "Oracle Linux (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "Oracle4": OSType("Oracle4", "Oracle Linux 4.x (32-bit)", "Linux", "x86"),
    "Oracle4_64": OSType("Oracle4_64", "Oracle Linux 4.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle5": OSType("Oracle5", "Oracle Linux 5.x (32-bit)", "Linux", "x86"),
    "Oracle5_64": OSType("Oracle5_64", "Oracle Linux 5.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle6": OSType("Oracle6", "Oracle Linux 6.x (32-bit)", "Linux", "x86"),
    "Oracle6_64": OSType("Oracle6_64", "Oracle Linux 6.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle7_64": OSType("Oracle7_64", "Oracle Linux 7.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle7_arm64": OSType(
        "Oracle7_arm64", "Oracle Linux 7.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Oracle8_64": OSType("Oracle8_64", "Oracle Linux 8.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle8_arm64": OSType(
        "Oracle8_arm64", "Oracle Linux 8.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Oracle9_64": OSType("Oracle9_64", "Oracle Linux 9.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle9_arm64": OSType(
        "Oracle9_arm64", "Oracle Linux 9.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Oracle10_64": OSType("Oracle10_64", "Oracle Linux 10.x (64-bit)", "Linux", "x86 (64-bit)"),
    "Oracle10_arm64": OSType(
        "Oracle10_arm64", "Oracle Linux 10.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "RedHat": OSType("RedHat", "Red Hat (32-bit)", "Linux", "x86"),
    "RedHat_64": OSType("RedHat_64", "Red Hat (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat3": OSType("RedHat3", "Red Hat 3.x (32-bit)", "Linux", "x86"),
    "RedHat3_64": OSType("RedHat3_64", "Red Hat 3.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat4": OSType("RedHat4", "Red Hat 4.x (32-bit)", "Linux", "x86"),
    "RedHat4_64": OSType("RedHat4_64", "Red Hat 4.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat5": OSType("RedHat5", "Red Hat 5.x (32-bit)", "Linux", "x86"),
    "RedHat5_64": OSType("RedHat5_64", "Red Hat 5.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat6": OSType("RedHat6", "Red Hat 6.x (32-bit)", "Linux", "x86"),
    "RedHat6_64": OSType("RedHat6_64", "Red Hat 6.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat7_64": OSType("RedHat7_64", "Red Hat 7.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat7_arm64": OSType("RedHat7_arm64", "Red Hat 7.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "RedHat8_64": OSType("RedHat8_64", "Red Hat 8.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat8_arm64": OSType("RedHat8_arm64", "Red Hat 8.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "RedHat9_64": OSType("RedHat9_64", "Red Hat 9.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat9_arm64": OSType("RedHat9_arm64", "Red Hat 9.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "RedHat10_64": OSType("RedHat10_64", "Red Hat 10.x (64-bit)", "Linux", "x86 (64-bit)"),
    "RedHat10_arm64": OSType(
        "RedHat10_arm64", "Red Hat 10.x (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "OpenSUSE": OSType("OpenSUSE", "openSUSE (32-bit)", "Linux", "x86"),
    "OpenSUSE_64": OSType("OpenSUSE_64", "openSUSE (64-bit)", "Linux", "x86 (64-bit)"),
    "OpenSUSE_Leap_64": OSType(
        "OpenSUSE_Leap_64", "openSUSE Leap (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "OpenSUSE_Leap_arm64": OSType(
        "OpenSUSE_Leap_arm64", "openSUSE Leap (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "OpenSUSE_Tumbleweed": OSType(
        "OpenSUSE_Tumbleweed", "openSUSE Tumbleweed (32-bit)", "Linux", "x86"
    ),
    "OpenSUSE_Tumbleweed_64": OSType(
        "OpenSUSE_Tumbleweed_64", "openSUSE Tumbleweed (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "OpenSUSE_Tumbleweed_arm64": OSType(
        "OpenSUSE_Tumbleweed_arm64", "openSUSE Tumbleweed (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "SUSE_LE": OSType("SUSE_LE", "SUSE Linux Enterprise (32-bit)", "Linux", "x86"),
    "SUSE_LE_64": OSType("SUSE_LE_64", "SUSE Linux Enterprise (64-bit)", "Linux", "x86 (64-bit)"),
    "Turbolinux": OSType("Turbolinux", "Turbolinux (32-bit)", "Linux", "x86"),
    "Turbolinux_64": OSType("Turbolinux_64", "Turbolinux (64-bit)", "Linux", "x86 (64-bit)"),
    "Ubuntu": OSType("Ubuntu", "Ubuntu (32-bit)", "Linux", "x86"),
    "Ubuntu_64": OSType("Ubuntu_64", "Ubuntu (64-bit)", "Linux", "x86 (64-bit)"),
    "Ubuntu_arm64": OSType("Ubuntu_arm64", "Ubuntu (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "Ubuntu10_LTS": OSType(
        "Ubuntu10_LTS", "Ubuntu 10.04 LTS (Lucid Lynx) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu10_LTS_64": OSType(
        "Ubuntu10_LTS_64", "Ubuntu 10.04 LTS (Lucid Lynx) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu10": OSType("Ubuntu10", "Ubuntu 10.10 (Maverick Meerkat) (32-bit)", "Linux", "x86"),
    "Ubuntu10_64": OSType(
        "Ubuntu10_64", "Ubuntu 10.10 (Maverick Meerkat) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu11": OSType(
        "Ubuntu11", "Ubuntu 11.04 (Natty Narwhal) / 11.10 (Oneiric Ocelot) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu11_64": OSType(
        "Ubuntu11_64",
        "Ubuntu 11.04 (Natty Narwhal) / 11.10 (Oneiric Ocelot) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu12_LTS": OSType(
        "Ubuntu12_LTS", "Ubuntu 12.04 LTS (Precise Pangolin) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu12_LTS_64": OSType(
        "Ubuntu12_LTS_64", "Ubuntu 12.04 LTS (Precise Pangolin) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu12": OSType("Ubuntu12", "Ubuntu 12.10 (Quantal Quetzal) (32-bit)", "Linux", "x86"),
    "Ubuntu12_64": OSType(
        "Ubuntu12_64", "Ubuntu 12.10 (Quantal Quetzal) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu13": OSType(
        "Ubuntu13",
        "Ubuntu 13.04 (Raring Ringtail) / 13.10 (Saucy Salamander) (32-bit)",
        "Linux",
        "x86",
    ),
    "Ubuntu13_64": OSType(
        "Ubuntu13_64",
        "Ubuntu 13.04 (Raring Ringtail) / 13.10 (Saucy Salamander) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu14_LTS": OSType(
        "Ubuntu14_LTS", "Ubuntu 14.04 LTS (Trusty Tahr) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu14_LTS_64": OSType(
        "Ubuntu14_LTS_64", "Ubuntu 14.04 LTS (Trusty Tahr) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu14": OSType("Ubuntu14", "Ubuntu 14.10 (Utopic Unicorn) (32-bit)", "Linux", "x86"),
    "Ubuntu14_64": OSType(
        "Ubuntu14_64", "Ubuntu 14.10 (Utopic Unicorn) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu15": OSType(
        "Ubuntu15", "Ubuntu 15.04 (Vivid Vervet) / 15.10 (Wily Werewolf) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu15_64": OSType(
        "Ubuntu15_64",
        "Ubuntu 15.04 (Vivid Vervet) / 15.10 (Wily Werewolf) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu16_LTS": OSType(
        "Ubuntu16_LTS", "Ubuntu 16.04 LTS (Xenial Xerus) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu16_LTS_64": OSType(
        "Ubuntu16_LTS_64", "Ubuntu 16.04 LTS (Xenial Xerus) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu16": OSType("Ubuntu16", "Ubuntu 16.10 (Yakkety Yak) (32-bit)", "Linux", "x86"),
    "Ubuntu16_64": OSType(
        "Ubuntu16_64", "Ubuntu 16.10 (Yakkety Yak) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu17": OSType(
        "Ubuntu17", "Ubuntu 17.04 (Zesty Zapus) / 17.10 (Artful Aardvark) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu17_64": OSType(
        "Ubuntu17_64",
        "Ubuntu 17.04 (Zesty Zapus) / 17.10 (Artful Aardvark) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu18_LTS": OSType(
        "Ubuntu18_LTS", "Ubuntu 18.04 LTS (Bionic Beaver) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu18_LTS_64": OSType(
        "Ubuntu18_LTS_64", "Ubuntu 18.04 LTS (Bionic Beaver) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu18": OSType("Ubuntu18", "Ubuntu 18.10 (Cosmic Cuttlefish) (32-bit)", "Linux", "x86"),
    "Ubuntu18_64": OSType(
        "Ubuntu18_64", "Ubuntu 18.10 (Cosmic Cuttlefish) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu19": OSType(
        "Ubuntu19", "Ubuntu 19.04 (Disco Dingo) / 19.10 (Eoan Ermine) (32-bit)", "Linux", "x86"
    ),
    "Ubuntu19_64": OSType(
        "Ubuntu19_64",
        "Ubuntu 19.04 (Disco Dingo) / 19.10 (Eoan Ermine) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu20_LTS_64": OSType(
        "Ubuntu20_LTS_64", "Ubuntu 20.04 LTS (Focal Fossa) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu20_64": OSType(
        "Ubuntu20_64", "Ubuntu 20.10 (Groovy Gorilla) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu21_64": OSType(
        "Ubuntu21_64",
        "Ubuntu 21.04 (Hirsute Hippo) / 21.10 (Impish Indri) (64-bit)",
        "Linux",
        "x86 (64-bit)",
    ),
    "Ubuntu22_LTS_64": OSType(
        "Ubuntu22_LTS_64", "Ubuntu 22.04 LTS (Jammy Jellyfish) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu22_64": OSType(
        "Ubuntu22_64", "Ubuntu 22.10 (Kinetic Kudu) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu22_arm64": OSType(
        "Ubuntu22_arm64", "Ubuntu 22.10 (Kinetic Kudu) (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Ubuntu23_64": OSType(
        "Ubuntu23_64", "Ubuntu 23.04 (Lunar Lobster) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu23_arm64": OSType(
        "Ubuntu23_arm64", "Ubuntu 23.04 (Lunar Lobster) (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Ubuntu231_64": OSType(
        "Ubuntu231_64", "Ubuntu 23.10 (Mantic Minotaur) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu231_arm64": OSType(
        "Ubuntu231_arm64", "Ubuntu 23.10 (Mantic Minotaur) (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Ubuntu24_LTS_64": OSType(
        "Ubuntu24_LTS_64", "Ubuntu 24.04 LTS (Noble Numbat) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu24_LTS_arm64": OSType(
        "Ubuntu24_LTS_arm64",
        "Ubuntu 24.04 LTS (Noble Numbat) (ARM 64-bit)",
        "Linux",
        "ARMv8 (64-bit)",
    ),
    "Ubuntu24_64": OSType(
        "Ubuntu24_64", "Ubuntu 24.10 (Oracular Oriole) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu24_arm64": OSType(
        "Ubuntu24_arm64", "Ubuntu 24.10 (Oracular Oriole) (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Ubuntu25_64": OSType(
        "Ubuntu25_64", "Ubuntu 25.04 (Plucky Puffin) (64-bit)", "Linux", "x86 (64-bit)"
    ),
    "Ubuntu25_arm64": OSType(
        "Ubuntu25_arm64", "Ubuntu 25.04 (Plucky Puffin) (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"
    ),
    "Lubuntu": OSType("Lubuntu", "Lubuntu (32-bit)", "Linux", "x86"),
    "Lubuntu_64": OSType("Lubuntu_64", "Lubuntu (64-bit)", "Linux", "x86 (64-bit)"),
    "Xubuntu": OSType("Xubuntu", "Xubuntu (32-bit)", "Linux", "x86"),
    "Xubuntu_64": OSType("Xubuntu_64", "Xubuntu (64-bit)", "Linux", "x86 (64-bit)"),
    "Xandros": OSType("Xandros", "Xandros (32-bit)", "Linux", "x86"),
    "Xandros_64": OSType("Xandros_64", "Xandros (64-bit)", "Linux", "x86 (64-bit)"),
    "Linux": OSType("Linux", "Other Linux (32-bit)", "Linux", "x86"),
    "Linux_64": OSType("Linux_64", "Other Linux (64-bit)", "Linux", "x86 (64-bit)"),
    "Linux_arm64": OSType("Linux_arm64", "Other Linux (ARM 64-bit)", "Linux", "ARMv8 (64-bit)"),
    "Solaris": OSType("Solaris", "Oracle Solaris 10 5/09 and earlier (32-bit)", "Solaris", "x86"),
    "Solaris_64": OSType(
        "Solaris_64", "Oracle Solaris 10 5/09 and earlier (64-bit)", "Solaris", "x86 (64-bit)"
    ),
    "Solaris10U8_or_later": OSType(
        "Solaris10U8_or_later", "Oracle Solaris 10 10/09 and later (32-bit)", "Solaris", "x86"
    ),
    "Solaris10U8_or_later_64": OSType(
        "Solaris10U8_or_later_64",
        "Oracle Solaris 10 10/09 and later (64-bit)",
        "Solaris",
        "x86 (64-bit)",
    ),
    "Solaris11_64": OSType("Solaris11_64", "Oracle Solaris 11 (64-bit)", "Solaris", "x86 (64-bit)"),
    "OpenSolaris": OSType(
        "OpenSolaris", "OpenSolaris / Illumos / OpenIndiana (32-bit)", "Solaris", "x86"
    ),
    "OpenSolaris_64": OSType(
        "OpenSolaris_64", "OpenSolaris / Illumos / OpenIndiana (64-bit)", "Solaris", "x86 (64-bit)"
    ),
    "FreeBSD": OSType("FreeBSD", "FreeBSD (32-bit)", "BSD", "x86"),
    "FreeBSD_64": OSType("FreeBSD_64", "FreeBSD (64-bit)", "BSD", "x86 (64-bit)"),
    "FreeBSD_arm64": OSType("FreeBSD_arm64", "FreeBSD (ARM 64-bit)", "BSD", "ARMv8 (64-bit)"),
    "OpenBSD": OSType("OpenBSD", "OpenBSD (32-bit)", "BSD", "x86"),
    "OpenBSD_64": OSType("OpenBSD_64", "OpenBSD (64-bit)", "BSD", "x86 (64-bit)"),
    "OpenBSD_arm64": OSType("OpenBSD_arm64", "OpenBSD (ARM 64-bit)", "BSD", "ARMv8 (64-bit)"),
    "NetBSD": OSType("NetBSD", "NetBSD (32-bit)", "BSD", "x86"),
    "NetBSD_64": OSType("NetBSD_64", "NetBSD (64-bit)", "BSD", "x86 (64-bit)"),
    "NetBSD_arm64": OSType("NetBSD_arm64", "NetBSD (ARM 64-bit)", "BSD", "ARMv8 (64-bit)"),
    "OS21x": OSType("OS21x", "OS/2 1.x", "OS2", "x86"),
    "OS2Warp3": OSType("OS2Warp3", "OS/2 Warp 3", "OS2", "x86"),
    "OS2Warp4": OSType("OS2Warp4", "OS/2 Warp 4", "OS2", "x86"),
    "OS2Warp45": OSType("OS2Warp45", "OS/2 Warp 4.5", "OS2", "x86"),
    "OS2eCS": OSType("OS2eCS", "eComStation", "OS2", "x86"),
    "OS2ArcaOS": OSType("OS2ArcaOS", "ArcaOS", "OS2", "x86"),
    "OS2": OSType("OS2", "Other OS/2", "OS2", "x86"),
    "MacOS": OSType("MacOS", "Mac OS X (32-bit)", "MacOS", "x86"),
    "MacOS_64": OSType("MacOS_64", "Mac OS X (64-bit)", "MacOS", "x86 (64-bit)"),
    "MacOS106": OSType("MacOS106", "Mac OS X 10.6 Snow Leopard (32-bit)", "MacOS", "x86"),
    "MacOS106_64": OSType(
        "MacOS106_64", "Mac OS X 10.6 Snow Leopard (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "MacOS107_64": OSType("MacOS107_64", "Mac OS X 10.7 Lion (64-bit)", "MacOS", "x86 (64-bit)"),
    "MacOS108_64": OSType(
        "MacOS108_64", "Mac OS X 10.8 Mountain Lion (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "MacOS109_64": OSType(
        "MacOS109_64", "Mac OS X 10.9 Mavericks (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "MacOS1010_64": OSType(
        "MacOS1010_64", "Mac OS X 10.10 Yosemite (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "MacOS1011_64": OSType(
        "MacOS1011_64", "Mac OS X 10.11 El Capitan (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "MacOS1012_64": OSType("MacOS1012_64", "macOS 10.12 Sierra (64-bit)", "MacOS", "x86 (64-bit)"),
    "MacOS1013_64": OSType(
        "MacOS1013_64", "macOS 10.13 High Sierra (64-bit)", "MacOS", "x86 (64-bit)"
    ),
    "DOS": OSType("DOS", "DOS", "Other", "x86"),
    "Netware": OSType("Netware", "Netware", "Other", "x86"),
    "L4": OSType("L4", "L4", "Other", "x86"),
    "QNX": OSType("QNX", "QNX", "Other", "x86"),
    "JRockitVE": OSType("JRockitVE", "JRockitVE", "Other", "x86"),
    "VBoxBS_64": OSType(
        "VBoxBS_64", "VirtualBox Bootsector Test (64-bit)", "Other", "x86 (64-bit)"
    ),
}

#: By the description ``showvminfo`` reports, which is what a parsed config holds.
BY_DESCRIPTION: Dict[str, str] = {t.description: t.id for t in OSTYPES.values()}

#: Family name -> the most generic 64-bit id in it, for a guest vmctl cannot place
#: exactly. "Other" is the last resort the product itself offers.
GENERIC_BY_FAMILY: Dict[str, str] = {
    "BSD": "Other_64",
    "Linux": "Linux_64",
    "MacOS": "MacOS_64",
    "OS2": "Other_64",
    "Other": "Other_64",
    "Solaris": "Solaris_64",
    "Windows": "Other_64",
}
