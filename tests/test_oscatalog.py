"""The neutral guest-OS catalogue (A-05).

`ostype: "Ubuntu_64"` was the last vendor string in the canonical model, and the
map from what VirtualBox *reports* to what it *accepts* lived inside the emitter.
These tests pin both halves: the catalogue is neutral, and each provider's
translation is checked against what that provider actually offers.
"""

import pytest

from vmctl.core import oscatalog
from vmctl.core.oscatalog import OSFamily
from vmctl.core.vmconfig import VMConfig

# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_ids_are_libosinfo_style_short_ids():
    """Borrowed rather than invented: these are the ids virt-install and GNOME
    Boxes already use, so a config is not a third convention."""
    for wanted in ("ubuntu22.04", "rhel9", "win11", "debian12", "freebsd"):
        assert oscatalog.get(wanted) is not None, wanted


def test_lookup_is_case_insensitive():
    assert oscatalog.get("WIN11") is oscatalog.get("win11")


def test_an_unknown_id_is_not_an_error():
    """A provider string in a config is legal and passes through, so "not in the
    catalogue" cannot mean "invalid"."""
    assert oscatalog.get("Ubuntu_64") is None
    assert oscatalog.family_of("Ubuntu_64") is OSFamily.OTHER
    assert oscatalog.describe("Ubuntu_64") == "Ubuntu_64"


def test_every_family_has_a_generic_member():
    """A provider that cannot express a version substitutes within the family, so
    a translation never has to guess."""
    for family in OSFamily:
        generic = oscatalog.GENERIC_BY_FAMILY[family]
        assert oscatalog.get(generic) is not None
        assert oscatalog.get(generic).family is family


def test_the_default_matches_what_1_1_x_defaulted_to():
    """1.1.x defaulted to the VirtualBox id `Ubuntu_64`; the neutral default has to
    create the same VM or upgrading changes machines silently."""
    from vmctl.providers.virtualbox.tables import GUEST_OS_TO_VBOX

    assert GUEST_OS_TO_VBOX[oscatalog.DEFAULT_ID] == "Ubuntu_64"
    assert VMConfig.from_dict({"name": "v"}).guest_os == oscatalog.DEFAULT_ID


# ---------------------------------------------------------------------------
# VirtualBox: measured against `VBoxManage list ostypes`
# ---------------------------------------------------------------------------


def test_every_virtualbox_mapping_names_a_type_the_product_has():
    """The whole point of generating the list: a typo here cannot ship."""
    from vmctl.providers.virtualbox.ostypes import OSTYPES
    from vmctl.providers.virtualbox.tables import GUEST_OS_TO_VBOX

    unknown = {k: v for k, v in GUEST_OS_TO_VBOX.items() if v not in OSTYPES}
    assert unknown == {}


def test_the_generated_list_covers_the_whole_product():
    from vmctl.providers.virtualbox.ostypes import BY_DESCRIPTION, OSTYPES

    assert len(OSTYPES) > 200, "a capture this small is probably truncated"
    assert len(BY_DESCRIPTION) == len(OSTYPES), "descriptions are not unique"


def test_a_family_with_no_generic_member_falls_back_to_other():
    """VirtualBox offers no version-less "Windows", and picking its oldest member
    instead would be a guess dressed as a translation."""
    from vmctl.providers.virtualbox.ostypes import GENERIC_BY_FAMILY

    assert GENERIC_BY_FAMILY["Linux"] == "Linux_64"
    assert GENERIC_BY_FAMILY["Windows"] == "Other_64"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ubuntu22.04", "Ubuntu22_LTS_64"),  # neutral id
        ("Ubuntu_64", "Ubuntu_64"),  # the provider's own id
        ("Ubuntu (64-bit)", "Ubuntu_64"),  # what showvminfo reports
    ],
)
def test_all_three_spellings_emit_a_valid_id(value, expected):
    from vmctl.providers.virtualbox.tables import GuestOSCodec

    assert GuestOSCodec().dump(value) == expected


def test_an_impossible_guest_os_is_refused_with_what_is_accepted():
    from vmctl.providers.virtualbox.tables import GuestOSCodec

    with pytest.raises(ValueError, match="not a guest OS type VirtualBox knows"):
        GuestOSCodec().dump("Ubunto")


# ---------------------------------------------------------------------------
# libvirt: measured against a defined domain
# ---------------------------------------------------------------------------


def test_libvirt_ids_are_libosinfo_urls():
    from vmctl.providers.libvirt.tables import GUEST_OS_TO_OSINFO

    for neutral, url in GUEST_OS_TO_OSINFO.items():
        assert oscatalog.get(neutral) is not None, neutral
        assert url.startswith("http://"), url


def test_the_two_providers_agree_on_which_ids_exist():
    """Both translation tables key off the same catalogue, so a new entry cannot be
    added to one provider under a name the other spells differently."""
    from vmctl.providers.libvirt.tables import GUEST_OS_TO_OSINFO
    from vmctl.providers.virtualbox.tables import GUEST_OS_TO_VBOX

    for table in (GUEST_OS_TO_VBOX, GUEST_OS_TO_OSINFO):
        assert set(table) <= set(oscatalog.ids())
