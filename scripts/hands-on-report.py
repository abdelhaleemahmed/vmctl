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
