#!/usr/bin/env python3
"""Regenerate tests/golden/* from the current code.

A golden file records the exact commands vmctl emits today. Regenerating is
therefore an explicit statement that a behaviour change is intended: run this,
then *read the diff* before committing it.

    python tests/regenerate_golden.py
    git diff tests/golden/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # conftest helpers
sys.path.insert(0, str(Path(__file__).parent.parent))  # the package

from conftest import GOLDEN, VM_LABELS, parse_label, render_commands  # noqa: E402
from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter  # noqa: E402


def emit(vm):
    return render_commands(VirtualBoxEmitter(vm.name).emit_create_vm(vm))


def main() -> int:
    GOLDEN.mkdir(exist_ok=True)
    written = []

    for label in VM_LABELS:
        vm = parse_label(label)
        (GOLDEN / f"emit_{label}.txt").write_text(emit(vm))
        written.append(f"emit_{label}.txt")

    # Hand-built configs, imported lazily so this script has no pytest dependency.
    from test_emitter import build_minimal, build_full

    for name, vm in (("minimal", build_minimal()), ("full", build_full())):
        (GOLDEN / f"emit_{name}.txt").write_text(emit(vm))
        written.append(f"emit_{name}.txt")

    print(f"Wrote {len(written)} golden files to {GOLDEN}:")
    for name in written:
        print(f"  {name}")
    print("\nNow review: git diff tests/golden/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
