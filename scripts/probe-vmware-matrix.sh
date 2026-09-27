#!/usr/bin/env bash
# Probe which (device kind, bus) pairs VMware Workstation will actually power on.
#
# The same method as the other three matrices: ask the product. A .vmx is not
# validated when it is written -- only when the VM powers on -- so each pair is
# tried by writing a one-device VM, starting it with `vmrun start nogui`, and
# reading the verdict out of vmware.log.
#
# Every VM is 128 MB, and each is powered off again immediately: the host has
# other VMs on it.
#
#   ./scripts/probe-vmware-matrix.sh > tests/fixtures/vmware_attach_matrix.json
set -u

HOST=${HOST:-winhost}
TOOLS='C:\Program Files\VMware\VMware Workstation'
DIR=${DIR:-'C:\Users\aaaha\vmctl-vmware'}
NAME=p02-matrix

run() { timeout 200 ssh "$HOST" "$1" 2>&1; }

# bus|controller lines|device prefix
buses=(
  "ide|ide0.present = \"TRUE\"|ide0:0"
  "sata|sata0.present = \"TRUE\"|sata0:0"
  "scsi-lsilogic|scsi0.present = \"TRUE\"\nscsi0.virtualDev = \"lsilogic\"|scsi0:0"
  "scsi-lsisas|scsi0.present = \"TRUE\"\nscsi0.virtualDev = \"lsisas1068\"|scsi0:0"
  "scsi-pvscsi|scsi0.present = \"TRUE\"\nscsi0.virtualDev = \"pvscsi\"|scsi0:0"
  "nvme|nvme0.present = \"TRUE\"|nvme0:0"
)

emit_pair() { printf '    "%s|%s": %s' "$1" "$2" "$3"; }

first=1
attach=""
notes=""
for kind in disk cdrom floppy; do
  for entry in "${buses[@]}"; do
    # A floppy is its own device node rather than something on a bus, so it is
    # probed once (against "floppy") instead of against every controller.
    if [ "$kind" = floppy ] && [ "${entry%%|*}" != ide ]; then continue; fi
    IFS='|' read -r bus controller prefix <<<"$entry"
    case $kind in
      disk)  device_type="disk";        medium="$NAME.vmdk" ;;
      cdrom) device_type="cdrom-image"; medium="probe.iso" ;;
      floppy) bus="floppy"; controller=""; prefix="" ;;
    esac
    {
      printf '%s\n' '.encoding = "UTF-8"' 'config.version = "8"' \
        'virtualHW.version = "22"' "displayName = \"$NAME\"" \
        'guestOS = "other6xlinux-64"' 'numvcpus = "1"' 'memsize = "128"' \
        'firmware = "bios"' "nvram = \"$NAME.nvram\"" 'svga.present = "TRUE"' \
        'vmci0.present = "TRUE"' \
        'pciBridge0.present = "TRUE"' \
        'pciBridge4.present = "TRUE"' 'pciBridge4.virtualDev = "pcieRootPort"' \
        'pciBridge4.functions = "8"' \
        'pciBridge5.present = "TRUE"' 'pciBridge5.virtualDev = "pcieRootPort"' \
        'pciBridge5.functions = "8"' \
        'pciBridge6.present = "TRUE"' 'pciBridge6.virtualDev = "pcieRootPort"' \
        'pciBridge6.functions = "8"' \
        'pciBridge7.present = "TRUE"' 'pciBridge7.virtualDev = "pcieRootPort"' \
        'pciBridge7.functions = "8"'
      if [ "$kind" = floppy ]; then
        printf '%s\n' 'floppy0.present = "TRUE"' 'floppy0.fileType = "file"' \
          'floppy0.fileName = "probe.flp"'
      else
        printf '%b\n' "$controller"
        printf '%s\n' "$prefix.present = \"TRUE\"" \
          "$prefix.fileName = \"$medium\"" "$prefix.deviceType = \"$device_type\""
      fi
    } > /tmp/$NAME.vmx

    timeout 60 scp -q /tmp/$NAME.vmx "$HOST:$DIR\\$NAME.vmx"
    run "del /q $DIR\\vmware.log $DIR\\vmware-*.log 2>nul & exit 0" >/dev/null
    started=$(run "\"$TOOLS\\vmrun.exe\" -T ws start $DIR\\$NAME.vmx nogui")
    log=$(run "type $DIR\\vmware.log 2>nul")
    run "\"$TOOLS\\vmrun.exe\" -T ws stop $DIR\\$NAME.vmx hard" >/dev/null

    if grep -q "Transitioned vmx/execState/val to poweredOn" <<<"$log"; then
      ok=true; note=""
    else
      ok=false
      note=$(grep -iE "msg\.[A-Za-z]+\.|Failed to configure|requested without" <<<"$log" \
             | grep -viE "dictionary.load|FeatureState|config.ini" | head -1 \
             | sed -E 's/.*threadName="[^"]*"\] //; s/"/\\"/g' | cut -c1-160)
      # No vmware.log at all means the configuration was refused before the VMX
      # process started, and vmrun is then the only witness.
      [ -z "$note" ] && note=$(printf '%s' "$started" | tr -d '\r' | grep -i error | head -1 | cut -c1-160)
      [ -z "$note" ] && note="the VM did not reach poweredOn"
    fi
    [ $first -eq 1 ] || { attach+=",
"; }
    first=0
    attach+="$(emit_pair "$kind" "$bus" "$ok")"
    [ -n "$note" ] && notes+="    \"$kind|$bus\": \"$note\",
"
    echo "  probed $kind on $bus -> $ok ${note:+: $note}" >&2
  done
done

version=$(run "\"$TOOLS\\vmrun.exe\"" | tr -d '\r' | grep -i "^vmrun version" | head -1 | awk '{print $3}')
cat <<JSON
{
  "attach": {
$attach
  },
  "vmrun_version": "$version",
  "virtual_hw": "22",
  "requires": "A .vmx needs the pciBridge0/4/5/6/7 entries VMware writes itself: without them a PCIe device is refused with 'Device nvme0 requested without secondary PCI slots available', which first looked like NVMe being absent.",
  "notes": {
$(printf '%s' "$notes" | sed '$ s/,$//')
  }
}
JSON
