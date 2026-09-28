"""
Is this machine set up to run VMs at all? (E-12)

One command answering the questions that otherwise arrive as bug reports: is the
hypervisor's tool installed, which version, where do images go, is there room for
one, does this host even offer hardware virtualisation.

The split is the same as everywhere else in vmctl. Facts about the *machine* are
neutral and live in :mod:`vmctl.core.hostinfo`; facts about a *hypervisor* are the
provider's to answer, through :meth:`BaseProvider.diagnostics`. So adding a fifth
provider adds its own checks and none of this changes.

Every check answers rather than raising. A diagnostic command that fails when
something is wrong would be useless precisely when it is needed.
"""

import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import hostinfo


@dataclass
class Check:
    """One thing that was looked at.

    Attributes:
        label: What was checked, e.g. ``"virsh"``.
        value: What was found, in a form a person can read.
        ok: True when fine, False when it will stop vmctl working, and None when
            the check is informational -- a number is not a pass or a failure.
        hint: What to do about it, when there is something to do.
    """

    label: str
    value: str
    ok: Optional[bool] = None
    hint: Optional[str] = None

    #: What each state looks like in the terminal. ASCII on purpose: this output is
    #: read over ssh on a Windows console as often as in a modern terminal.
    MARKS = {True: "ok  ", False: "FAIL", None: "--  "}

    def render(self) -> str:
        """Return the line for this check."""
        line = f"[{self.MARKS[self.ok]}] {self.label}: {self.value}"
        return f"{line}\n         hint: {self.hint}" if self.hint else line

    def as_dict(self) -> Dict[str, Any]:
        """Return this check as JSON-safe data (E-07)."""
        return {"check": self.label, "value": self.value, "ok": self.ok, "hint": self.hint}


def host_checks() -> List[Check]:
    """Return what can be said about this machine without asking a hypervisor."""
    checks = [
        Check("python", f"{sys.version_info.major}.{sys.version_info.minor}", True),
        Check("cpus", str(os.cpu_count() or "unknown")),
    ]
    memory = hostinfo.memory_mb()
    checks.append(Check("host memory", f"{memory} MB" if memory else "unknown"))

    virtualisation = hostinfo.hardware_virtualisation()
    if virtualisation is None:
        checks.append(
            Check(
                "hardware virtualisation",
                "not answerable on this host; the hypervisor knows",
            )
        )
    elif virtualisation:
        checks.append(Check("hardware virtualisation", "available (/dev/kvm)", True))
    else:
        # Not a failure: every provider here can emulate. It is the difference
        # between a guest that boots in seconds and one that takes minutes, which
        # is worth saying before someone concludes vmctl is slow.
        checks.append(
            Check(
                "hardware virtualisation",
                "unavailable; guests will be emulated and slow",
                None,
                "on a nested setup, enable VT-x/AMD-V for this VM; otherwise check "
                "that /dev/kvm exists and you are in the group that may use it",
            )
        )
    bridges = hostinfo.host_bridges()
    if bridges:
        checks.append(Check("host bridges", ", ".join(bridges)))
    elif os.path.isdir(hostinfo.NET_CLASS):
        checks.append(Check("host bridges", "none"))
    else:
        # Not "none": this host has no sysfs to ask, and reporting an absence vmctl
        # never established is the mistake every measured table here exists to avoid.
        checks.append(Check("host bridges", "not answerable on this host"))
    return checks


def provider_checks(backend) -> List[Check]:
    """Return the checks for the provider in use, plus where its images go."""
    return list(backend.diagnostics())


def other_providers(current: str) -> List[Check]:
    """Return which other providers would work on this machine.

    Part of the answer to "why can vmctl not see my VMs": usually because it is
    talking to a different hypervisor than the one they are in.
    """
    from . import registry

    checks = []
    for name in registry.names():
        if name == current:
            continue
        checks.append(
            Check(
                f"provider {name}",
                "usable here" if registry.is_available(name) else "not installed",
            )
        )
    return checks


def report(backend) -> List[Check]:
    """Return every check, in reading order: the machine, then the hypervisor."""
    return host_checks() + provider_checks(backend) + other_providers(backend.name)


def failures(checks: List[Check]) -> List[Check]:
    """Return only the checks that will stop vmctl working."""
    return [check for check in checks if check.ok is False]
