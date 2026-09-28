"""``vmctl doctor`` and the host facts under it (E-12).

The command exists to run on a machine where something is wrong, so the tests are
mostly about answering rather than raising: a broken setup must produce findings, not
a traceback.
"""

import os
import subprocess

import pytest

from vmctl.core import doctor, hostinfo


# ---------------------------------------------------------------------------
# Facts about the machine
# ---------------------------------------------------------------------------


def test_memory_is_read_from_the_host():
    """Either a number of MB or None. Not a guess: a wrong figure here would be
    reported as this machine's RAM."""
    found = hostinfo.memory_mb()
    assert found is None or found > 0


def test_free_space_uses_the_nearest_existing_directory(tmp_path):
    """A provider's image directory is created on first use, so the question has to
    be about the filesystem that will hold it rather than about the path."""
    assert hostinfo.free_space_mb(str(tmp_path / "not" / "created" / "yet")) is not None


def test_free_space_answers_nothing_for_an_impossible_path(monkeypatch):
    monkeypatch.setattr(hostinfo.shutil, "disk_usage", lambda path: (_ for _ in ()).throw(OSError))
    assert hostinfo.free_space_mb("/anything") is None


def test_hardware_virtualisation_is_about_being_able_to_use_it(monkeypatch):
    """A machine can have /dev/kvm while the user is not in the group that may open
    it, and "the device file exists" is then a wrong answer to the real question."""
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/dev/kvm")
    monkeypatch.setattr(os, "access", lambda path, mode: False)

    assert hostinfo.hardware_virtualisation() is False


def test_host_checks_always_say_something():
    checks = doctor.host_checks()
    labels = [check.label for check in checks]
    assert "python" in labels and "host memory" in labels
    for check in checks:
        assert check.value


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class FakeBackend:
    name = "fake"

    def diagnostics(self):
        return [doctor.Check("fake tool", "/usr/bin/fake", True)]


def test_a_report_covers_the_machine_then_the_hypervisor():
    checks = doctor.report(FakeBackend())
    assert [c.label for c in checks][0] == "python"
    assert any(c.label == "fake tool" for c in checks)
    # ...and the other providers, because "vmctl cannot see my VMs" is usually
    # "vmctl is talking to a different hypervisor".
    assert any(c.label.startswith("provider ") for c in checks)


def test_only_a_real_problem_counts_as_a_failure():
    checks = [
        doctor.Check("informational", "3655 MB"),
        doctor.Check("fine", "yes", True),
        doctor.Check("broken", "no", False),
    ]
    assert [c.label for c in doctor.failures(checks)] == ["broken"]


def test_a_check_renders_its_hint_where_there_is_one():
    rendered = doctor.Check("vboxdrv module", "not loaded", False, "run vboxconfig").render()
    assert "FAIL" in rendered and "vboxdrv module: not loaded" in rendered
    assert "hint: run vboxconfig" in rendered


def test_a_check_is_json_safe():
    data = doctor.Check("cpus", "2").as_dict()
    assert data == {"check": "cpus", "value": "2", "ok": None, "hint": None}


# ---------------------------------------------------------------------------
# Each provider's own answers
# ---------------------------------------------------------------------------


@pytest.mark.allow_subprocess
@pytest.mark.parametrize("provider", ["virtualbox", "libvirt", "qemu", "vmware"])
def test_every_provider_reports_where_its_images_go(provider, monkeypatch):
    """Whatever is missing, a user should still be told where vmctl would put a disk
    and whether there is room -- that is half of the support questions."""
    from vmctl.core import registry

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "no such file")

    monkeypatch.setattr(subprocess, "run", missing)
    backend = registry.create(provider)

    values = " ".join(check.value for check in backend.diagnostics())

    assert "directory" in values or "images" in values or "Machines" in values
