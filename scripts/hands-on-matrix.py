#!/usr/bin/env python3
"""Drive vmctl from outside, on real hypervisors, and report what actually happened.

This is deliberately not a test in the suite. The suite and ``vmctl selftest`` are
both written against vmctl's own idea of itself, so when that idea is wrong they
agree and pass -- which is how ``export -o vm.json`` came to write YAML with 1194
tests green. This runs the command line a person runs, reads the files that land on
disk, and judges the result by looking at them.

For each (format, bus) pair it does the whole round trip on a real VM:

  validate -> import --execute -> import again (must refuse) -> import with
  --set/--add-disk overrides -> read back -> export to JSON -> import the JSON ->
  diff the export against the VM -> delete everything -> confirm it is gone

The pairs come from the provider's own measured capabilities, never a list written
here: every format it can create and every bus that can carry a disk appears at least
once. A full cross product would be 72 VMs across the four providers for no more
coverage of either dimension.

Usage:
    python scripts/hands-on-matrix.py <provider> [--json OUT] [--size-mb N]
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Every VM this script makes starts with this, so anything it leaves behind is
#: obvious and nothing it does can collide with a VM that matters.
PREFIX = "vmctl-t"


def vmctl(provider: str, *args: str) -> Dict[str, Any]:
    """Run one vmctl command and keep everything about it."""
    argv = [sys.executable, "-m", "vmctl.cli.main", "-p", provider, *args]
    completed = subprocess.run(argv, capture_output=True, text=True)
    printed = " ".join(["vmctl", "-p", provider, *args])
    print(f"$ {printed}")
    body = (completed.stdout or "") + (completed.stderr or "")
    for line in body.splitlines()[:40]:
        print(f"  {line}")
    print(f"  -> exit {completed.returncode}")
    return {"command": printed, "exit": completed.returncode, "output": body}


def pairs(provider: str) -> List[Tuple[str, str]]:
    """Return (format, bus) pairs covering everything the provider can do.

    Measured from the provider itself, so a capability table that changes is a
    matrix that changes, not a list here that quietly goes stale.
    """
    from vmctl.core import registry
    from vmctl.core.devices import DeviceKind

    import vmctl.providers  # noqa: F401  - registers the built-ins

    caps = registry.create(provider).probe()
    formats = sorted(fmt.value for fmt in caps.creatable_formats())
    buses = [bus.value for bus in caps.buses_for(DeviceKind.DISK)]
    if not formats or not buses:
        raise SystemExit(f"{provider} reports no creatable format or no disk bus")
    count = max(len(formats), len(buses))
    return [(formats[i % len(formats)], buses[i % len(buses)]) for i in range(count)]


def spec(path: Path, name: str, fmt: str, bus: str, size_mb: int) -> str:
    """Write the YAML a user would write, and return it for the report."""
    text = (
        f"name: {name}\n"
        "cpu:\n  count: 1\n"
        "memory:\n  mb: 128\n"
        "storage:\n"
        "  - name: system\n"
        f"    size_mb: {size_mb}\n"
        f"    format: {fmt}\n"
        f"    bus: {bus}\n"
        "    bootable: true\n"
    )
    path.write_text(text)
    return text


def check(results: List[Dict[str, Any]], label: str, ok: bool, detail: str = "") -> None:
    """Record one judgement about what was observed."""
    results.append({"check": label, "ok": bool(ok), "detail": detail})
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")


def one_case(
    provider: str, fmt: str, bus: str, work: Path, size_mb: int, policy: str
) -> Dict[str, Any]:
    """Run the whole round trip for one (format, bus) pair on a real VM."""
    name = f"{PREFIX}-{fmt}-{bus}".replace("_", "-")
    overridden = f"{name}-ov"
    roundtrip = f"{name}-rt"
    checks: List[Dict[str, Any]] = []
    commands: List[Dict[str, Any]] = []
    config = work / f"{name}.yaml"
    exported = work / f"{name}.json"
    written = spec(config, name, fmt, bus, size_mb)

    print(f"\n=== {provider}: format={fmt} bus={bus} ===")
    try:
        step = vmctl(provider, "validate", str(config))
        commands.append(step)
        check(checks, "the spec validates", step["exit"] == 0)

        step = vmctl(provider, "import", str(config), "--policy", policy, "--execute")
        commands.append(step)
        created = step["exit"] == 0
        check(checks, "the VM is created", created)
        if not created:
            return _case(provider, fmt, bus, name, written, checks, commands)

        step = vmctl(provider, "list")
        commands.append(step)
        check(checks, "the hypervisor lists it", name in step["output"])

        step = vmctl(provider, "import", str(config), "--policy", policy, "--execute")
        commands.append(step)
        check(
            checks,
            "importing the same file again is refused, not silently doubled",
            step["exit"] != 0,
            f"exit {step['exit']}",
        )

        # More RAM and more CPUs than the file says, plus hardware the file has not
        # got -- the thing `import` could not do at all before E-20.
        step = vmctl(
            provider,
            "import",
            str(config),
            "--new-name",
            overridden,
            "--set",
            "memory.mb=256",
            "--set",
            "cpu.count=2",
            "--add-disk",
            f"name=data,size_mb={size_mb},bus={bus},format={fmt}",
            "--policy",
            policy,
            "--execute",
        )
        commands.append(step)
        check(checks, "the overridden VM is created", step["exit"] == 0)

        step = vmctl(provider, "read", overridden, "--format", "json")
        commands.append(step)
        live: Optional[Dict[str, Any]] = None
        try:
            live = json.loads(step["output"])
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            check(checks, "the VM reads back as JSON", False, str(exc)[:120])
        if live is not None:
            check(checks, "the VM reads back as JSON", True)
            check(
                checks,
                "the memory override reached the hypervisor",
                live["memory"]["mb"] == 256,
                f"reported {live['memory']['mb']} MB",
            )
            check(
                checks,
                "the CPU override reached the hypervisor",
                live["cpu"]["count"] == 2,
                f"reported {live['cpu']['count']}",
            )
            check(
                checks,
                "the added disk is there",
                len(live["storage"]) == 2,
                f"{len(live['storage'])} storage device(s)",
            )
            formats_seen = {d.get("format") for d in live["storage"] if d.get("format")}
            check(
                checks,
                f"the disks are {fmt} as asked",
                formats_seen == {fmt},
                f"reported {sorted(formats_seen)}",
            )
            buses_seen = {d.get("bus") for d in live["storage"] if d.get("bus")}
            check(
                checks,
                f"the disks are on {bus} as asked",
                buses_seen == {bus},
                f"reported {sorted(buses_seen)}",
            )

        step = vmctl(provider, "export", overridden, "-o", str(exported))
        commands.append(step)
        check(checks, "export writes the file", step["exit"] == 0 and exported.exists())
        if exported.exists():
            raw = exported.read_text()
            try:
                json.loads(raw)
                is_json = True
                why = ""
            except Exception as exc:  # noqa: BLE001
                is_json = False
                why = str(exc)[:120]
            check(checks, "a .json file contains JSON", is_json, why)

            step = vmctl(
                provider,
                "import",
                str(exported),
                "--new-name",
                roundtrip,
                "--policy",
                policy,
                "--execute",
            )
            commands.append(step)
            check(checks, "the exported JSON imports again", step["exit"] == 0)

            step = vmctl(provider, "diff", overridden, str(exported))
            commands.append(step)
            check(
                checks,
                "the export matches the VM it came from",
                step["exit"] == 0,
                "diff reported changes" if step["exit"] == 1 else f"exit {step['exit']}",
            )
    finally:
        for victim in (roundtrip, overridden, name):
            commands.append(vmctl(provider, "delete", victim, "--force"))
        after = vmctl(provider, "list")
        commands.append(after)
        left = [n for n in (name, overridden, roundtrip) if n in after["output"]]
        check(checks, "everything it made is gone", not left, ", ".join(left))

    return _case(provider, fmt, bus, name, written, checks, commands)


def _case(provider, fmt, bus, name, written, checks, commands) -> Dict[str, Any]:
    return {
        "provider": provider,
        "format": fmt,
        "bus": bus,
        "name": name,
        "spec": written,
        "checks": checks,
        "commands": commands,
        "failed": [c for c in checks if not c["ok"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider")
    parser.add_argument("--json", type=Path, default=None, help="Write results here.")
    parser.add_argument("--size-mb", type=int, default=100, help="Disk size for each VM.")
    parser.add_argument(
        "--policy",
        default="nearest",
        help="Translation policy. nearest so a provider substituting something is "
        "reported rather than refusing, which is what a user hits.",
    )
    args = parser.parse_args()

    grid = pairs(args.provider)
    print(f"{args.provider}: {len(grid)} cases -- {grid}")
    work = Path(tempfile.mkdtemp(prefix="vmctl-handson-"))
    cases = [one_case(args.provider, f, b, work, args.size_mb, args.policy) for f, b in grid]

    failed = sum(len(case["failed"]) for case in cases)
    total = sum(len(case["checks"]) for case in cases)
    print(f"\n{args.provider}: {total - failed}/{total} checks passed, {failed} failed")
    if args.json:
        args.json.write_text(json.dumps({"provider": args.provider, "cases": cases}, indent=2))
        print(f"results: {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
