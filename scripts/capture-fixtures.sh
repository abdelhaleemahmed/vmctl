#!/bin/bash
# capture-fixtures.sh - T-02: capture real VBoxManage output as test fixtures.
#
# Run this on a machine that HAS VirtualBox. It records the raw output vmctl
# parses, so the test suite can run anywhere afterwards without VirtualBox.
#
# Usage:
#   ./scripts/capture-fixtures.sh <label> <vm-name>
#   ./scripts/capture-fixtures.sh --list
#
# Example:
#   ./scripts/capture-fixtures.sh efi_secureboot my-win11-vm
#
# The labels the suite wants (see tests/fixtures/README.md):
#   bios_minimal   efi_secureboot   iso_attached   multidisk   multinic
#
# Captured files are text and may contain host paths, VM names and MAC
# addresses. Review before committing; scrub anything you would not publish.

set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURES="tests/fixtures"
mkdir -p "$FIXTURES"

if ! command -v VBoxManage >/dev/null 2>&1; then
    echo "Error: VBoxManage not found. Run this on a host with VirtualBox." >&2
    exit 1
fi

if [ "${1:-}" = "--list" ]; then
    VBoxManage list vms
    exit 0
fi

if [ $# -ne 2 ]; then
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

LABEL="$1"
VM="$2"

echo "Capturing VirtualBox $(VBoxManage --version) ..."
VBoxManage --version            > "$FIXTURES/version.txt"
VBoxManage list vms             > "$FIXTURES/list_vms.txt"
VBoxManage list systemproperties > "$FIXTURES/systemproperties.txt"

echo "Capturing VM '$VM' as label '$LABEL' ..."
VBoxManage showvminfo "$VM" --machinereadable > "$FIXTURES/showvminfo_${LABEL}.txt"

# Capture every medium attached to that VM, named after the label + index.
#
# Attachment lines look like:  "SATA Controller-0-0"="/path/disk.vdi"
idx=0
grep -oE '"[^"]+-[0-9]+-[0-9]+"="[^"]+"' "$FIXTURES/showvminfo_${LABEL}.txt" \
  | sed 's/.*"=\"\(.*\)\"$/\1/' \
  | grep -iE '\.(vdi|vmdk|vhd|img|raw|iso)$' \
  | sort -u \
  | while read -r medium; do
        out="$FIXTURES/showmediuminfo_${LABEL}_${idx}.txt"
        echo "  medium: $medium -> $(basename "$out")"
        # A medium may be unregistered or missing; record the failure rather
        # than aborting, since the parser has a fallback path for exactly that.
        VBoxManage showmediuminfo "$medium" > "$out" 2>&1 || true
        # Record which path this file describes, so the fake probe can map it.
        echo "$medium" > "${out%.txt}.path"
        idx=$((idx + 1))
    done

echo
echo "Done. Captured into $FIXTURES:"
ls -1 "$FIXTURES" | sed 's/^/  /'
echo
echo "Next steps:"
echo "  1. Review the files for anything host-specific you do not want committed."
echo "  2. Delete the matching tests/fixtures/*.synthetic marker, if present."
echo "  3. Run: pytest -q   (golden files may need regenerating:"
echo "     python tests/regenerate_golden.py)"
