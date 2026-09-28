#!/usr/bin/env python3
"""Turn the hands-on results into one report.

Reads every ``results/<provider>.json`` written by ``hands-on-matrix.py`` and writes
``TEST-REPORT.md``. Regenerating from the results rather than appending text means the
report cannot drift from what the runs actually recorded -- the same reason
``docs/features.md`` is generated.

Usage:
    python scripts/hands-on-report.py [--results DIR] [--out FILE]
"""

import argparse
import json
import platform
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

#: The order providers appear in, so the report reads the same way every time.
ORDER = ["libvirt", "qemu", "virtualbox", "vmware"]

HOW = {
    "libvirt": "locally, against libvirt/QEMU-KVM on this host",
    "qemu": "locally, against plain QEMU on this host",
    "virtualbox": "on the Windows host over `ssh winhost`, against VirtualBox",
    "vmware": "on the Windows host over `ssh winhost`, against VMware Workstation",
}


#: What these runs turned up, in the order they were found. Written here rather than
#: derived from the results because the results only record that a check failed; what
#: it meant, and why no test had caught it, is the part worth reading. Each is fixed,
#: with a regression test, and written up in CHANGELOG.md.
FOUND = [
    (
        "`export -o vm.json` wrote YAML into the file",
        "Found on libvirt before the matrix existed, by exporting one real VM. "
        '`--format` carried a default, so the command could not tell "not given" '
        'from "given as yaml" and the inference its own help promised never ran. '
        "`json.load` on the result failed at line 1. Nothing in the suite had ever "
        "asked for a `.json` file without also passing `--format`.",
    ),
    (
        "A libvirt VM could not be exported and recreated while the original existed",
        "An export carries the domain's UUID, so importing it under a new name asked "
        "libvirt to define a second domain with the first one's identity: "
        "`domain 'x' is already defined with uuid ...`. The tool's central promise, "
        "failing on a whole provider. Caught by all four libvirt cases at step 8.",
    ),
    (
        "A QEMU VM with two disks on `virtio-scsi` or `usb` could not be re-imported",
        "The `.0` after those controllers is the controller's own bus, shared by every "
        "device on it, so it cannot carry a device's address -- and the emitter left "
        "the address out. QEMU assigned LUNs itself and the command line, which for "
        "this provider *is* the VM, did not record which disk was which. Both read "
        "back at port 0 and the export failed validation for colliding slots. "
        "Measured against qemu-kvm 10.1.0 before fixing: `scsi-hd` takes `scsi-id`, "
        "`usb-storage` takes `port`.",
    ),
    (
        "`export` wrote the file and then died printing that it had, on Windows",
        "`UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'` -- the "
        "arrow in the success line has no room in cp1252. The export sat on disk, "
        "correct, while the command exited 1 with a traceback. All seven VirtualBox "
        "cases failed on this and nothing else. No test on Linux could have caught it.",
    ),
]


def _version(provider: str) -> str:
    """Ask the provider what it is, for the record."""
    try:
        out = subprocess.run(
            ["python3", "-m", "vmctl.cli.main", "providers"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        for line in out.splitlines():
            if line.startswith(provider):
                parts = line.split()
                return parts[2] if len(parts) > 2 else "?"
    except Exception:  # noqa: BLE001 - the report is not worth failing over
        pass
    return "?"


def section(data: Dict[str, Any]) -> List[str]:
    """One hypervisor's part of the report."""
    provider = data["provider"]
    cases = data["cases"]
    checks = sum(len(case["checks"]) for case in cases)
    failed = sum(len(case["failed"]) for case in cases)
    lines = [
        f"## {provider}",
        "",
        f"Run {HOW.get(provider, 'on a real hypervisor')}. "
        f"{len(cases)} VMs, {checks} checks, **{failed} failed**.",
        "",
        "| # | format | bus | VM | checks | result |",
        "|---|---|---|---|---|---|",
    ]
    for number, case in enumerate(cases, 1):
        passed = len(case["checks"]) - len(case["failed"])
        verdict = "pass" if not case["failed"] else f"**{len(case['failed'])} failed**"
        lines.append(
            f"| {number} | `{case['format']}` | `{case['bus']}` | `{case['name']}` "
            f"| {passed}/{len(case['checks'])} | {verdict} |"
        )
    lines.append("")

    broken = [(case, bad) for case in cases for bad in case["failed"]]
    if broken:
        lines += ["### What failed", ""]
        for case, bad in broken:
            detail = f" -- {bad['detail']}" if bad["detail"] else ""
            lines.append(f"- `{case['format']}`/`{case['bus']}`: {bad['check']}{detail}")
        lines.append("")
    else:
        lines += ["Every check passed on every pair.", ""]

    lines += [
        "<details><summary>The spec used, and what each VM was asked for</summary>",
        "",
        "```yaml",
        cases[0]["spec"].rstrip(),
        "```",
        "",
        "Each case repeats this with its own format and bus, then overrides "
        "`memory.mb=256`, `cpu.count=2` and adds a second disk on the command line.",
        "",
        "</details>",
        "",
    ]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path, default=Path("TEST-REPORT.md"))
    args = parser.parse_args()

    found = {}
    for path in sorted(args.results.glob("*.json")):
        data = json.loads(path.read_text())
        found[data["provider"]] = data
    if not found:
        raise SystemExit(f"no results in {args.results}")

    checks = sum(len(c["checks"]) for d in found.values() for c in d["cases"])
    failed = sum(len(c["failed"]) for d in found.values() for c in d["cases"])
    vms = sum(len(d["cases"]) for d in found.values())

    lines = [
        "# vmctl hands-on test report",
        "",
        f"Generated {date.today().isoformat()} by `scripts/hands-on-report.py` from the "
        "results of `scripts/hands-on-matrix.py`.",
        "",
        "## What this is, and why it is not the test suite",
        "",
        "The pytest suite and `vmctl selftest` are both written against vmctl's own idea "
        "of itself. When that idea is wrong they agree with each other and pass: 1194 "
        "tests were green while `vmctl export vm -o vm.json` wrote YAML into the file. "
        "So this report comes from driving the command line the way a person does, on "
        "real hypervisors, and judging the result by reading the files that land on disk.",
        "",
        "Every VM here is a real VM -- created, read back, exported, re-imported and "
        "deleted. None is started: the host has other VMs running, so these are built "
        "and removed without ever being powered on.",
        "",
        "## What each case does",
        "",
        "For one (disk format, bus) pair:",
        "",
        "1. write the spec as YAML, and `validate` it",
        "2. `import --execute` -- a real VM appears",
        "3. `list` -- the hypervisor agrees it is there",
        "4. `import` the same file again -- must be refused, not silently doubled",
        "5. `import` with `--new-name`, `--set memory.mb=256`, `--set cpu.count=2` and "
        "`--add-disk` -- more RAM and more CPUs than the file says, plus hardware the "
        "file does not describe",
        "6. `read --format json` -- did the overrides actually reach the hypervisor?",
        "7. `export -o <name>.json` -- and the file is parsed as JSON to prove it is JSON",
        "8. `import` that JSON -- the round trip",
        "9. `diff` the export against the VM it came from -- should report no changes",
        "10. `delete` all three, and `list` again to confirm nothing is left",
        "",
        "The (format, bus) pairs are taken from each provider's own measured "
        "capabilities, never a list written by hand, so every format it can create and "
        "every bus that can carry a disk appears at least once.",
        "",
        "## Result",
        "",
        "| | |",
        "|---|---|",
        f"| hypervisors | {len(found)} |",
        f"| real VMs created and deleted | {vms} |",
        f"| checks | {checks} |",
        f"| failed | **{failed}** |",
        "",
        "| hypervisor | VMs | checks | failed |",
        "|---|---|---|---|",
    ]
    for provider in [p for p in ORDER if p in found] + [p for p in found if p not in ORDER]:
        data = found[provider]
        c = sum(len(case["checks"]) for case in data["cases"])
        f = sum(len(case["failed"]) for case in data["cases"])
        lines.append(f"| {provider} | {len(data['cases'])} | {c} | {f} |")
    lines.append("")

    lines += ["## What these runs found", ""]
    for title, detail in FOUND:
        lines += [f"**{title}**", "", detail, ""]
    lines += [
        "Every one of them is a defect in the path a user takes first, and not one was "
        "visible to the test suite. Three are round-trip breaks -- export a VM, import "
        "it back -- which is the thing vmctl exists to do. All four are fixed, each with "
        "a regression test that fails without the fix.",
        "",
        "### Still open",
        "",
        "**A libvirt VM re-imported from its own export warns about something that is "
        "not wrong.** Every libvirt case in this run printed:",
        "",
        "```",
        "Warning: more than one CPU is configured but ioapic is off; an x86 guest needs",
        "an I/O APIC to use them, and libvirt will not give it one",
        "```",
        "",
        "The q35 chipset always provides an I/O APIC; libvirt's `<ioapic>` element only "
        "chooses its driver, so a domain that omits it still has one. `boot.ioapic` "
        "therefore reads back false and the validator objects to a VM that is fine. It "
        "is the same shape as F-51, already fixed for QEMU (\"QEMU's machine types "
        'provide an I/O APIC; there is no setting"), and libvirt needs the same '
        "treatment. Not fixed here: it needs the capability measured rather than "
        "assumed, and it costs a warning, not a VM.",
        "",
    ]

    lines += [
        "## Recordings",
        "",
        "Each run was recorded with asciinema. Play one with "
        "`asciinema play casts/<provider>.cast`.",
        "",
    ]
    for provider in found:
        cast = Path("casts") / f"{provider}.cast"
        if cast.exists():
            size = cast.stat().st_size // 1024
            lines.append(f"- `{cast}` ({size} KB)")
    lines.append("")

    for provider in [p for p in ORDER if p in found] + [p for p in found if p not in ORDER]:
        lines += section(found[provider])

    lines += [
        "## How it was run",
        "",
        f"- this host: {platform.platform()}, Python {platform.python_version()} "
        "for the report; the runs themselves used Python 3.13",
        "- `scripts/hands-on-matrix.py <provider>` per hypervisor, under `asciinema rec`",
        "- `--policy nearest`, so a provider substituting a value reports it rather than "
        "refusing -- which is what a user meets",
        "",
    ]
    args.out.write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out}: {vms} VMs, {checks} checks, {failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
