"""
Command-line interface for vmctl
"""
import argparse
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from vmctl import __version__
from vmctl.core.engine import VMCtlEngine
from vmctl.core.batch import BatchCreator
from vmctl.core.vmconfig import VMConfig
from vmctl.core.exceptions import ValidationError, SerializationError, ProviderError


def create_parser():
    """Create argument parser"""
    parser = argparse.ArgumentParser(
        prog="vmctl",
        description="Virtual Machine Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  vmctl list                                # List all VMs
  vmctl read ubuntu-vm                      # Read VM configuration
  vmctl start ubuntu-vm                     # Start a VM
  vmctl stop ubuntu-vm                      # Stop a VM
  vmctl status ubuntu-vm                    # Get VM status
  vmctl create ubuntu-vm --new-name test    # Create from existing
  vmctl export ubuntu-vm -o config.yaml     # Export to YAML
  vmctl import config.yaml --new-name vm1   # Import and create
  vmctl batch create batch.yaml             # Batch create VMs
        """
    )

    parser.add_argument("--version", "-V", action="version",
                       version=f"vmctl {__version__} By:Ahmed Abdelhaleem Email: ahmedhal@gmail.com")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # List command
    list_parser = subparsers.add_parser("list", help="List all VMs")
    list_parser.add_argument("--format", choices=["table", "simple"], default="table",
                            help="Output format (default: table)")

    # Read command
    read_parser = subparsers.add_parser("read", help="Read VM configuration")
    read_parser.add_argument("vm_name", help="Name of the VM to read")
    read_parser.add_argument("--format", choices=["json", "yaml"], default="yaml",
                           help="Output format (default: yaml)")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start a VM")
    start_parser.add_argument("vm_name", help="Name of the VM to start")

    # Stop command
    stop_parser = subparsers.add_parser("stop", help="Stop a VM")
    stop_parser.add_argument("vm_name", help="Name of the VM to stop")
    stop_parser.add_argument("--force", "-f", action="store_true",
                            help="Force power off (default: graceful shutdown)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Get VM status")
    status_parser.add_argument("vm_name", help="Name of the VM")

    # Edit command
    edit_parser = subparsers.add_parser("edit", help="Edit VM configuration")
    edit_parser.add_argument("vm_name", help="Name of the VM to edit")
    edit_parser.add_argument("--new-name", help="New name for the VM")
    edit_parser.add_argument("--memory", type=int, help="Memory in MB")
    edit_parser.add_argument("--cpus", type=int, help="Number of CPUs")
    edit_parser.add_argument("--disk-size", type=int, help="Disk size in MB")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create VM from existing")
    create_parser.add_argument("source_vm", help="Source VM name or config file")
    create_parser.add_argument("--new-name", required=True, help="Name for new VM")
    create_parser.add_argument("--memory", type=int, help="Override memory in MB")
    create_parser.add_argument("--cpus", type=int, help="Override CPU count")
    create_parser.add_argument("--apply", action="store_true",
                             help="Actually create the VM (dry-run by default)")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export VM configuration")
    export_parser.add_argument("vm_name", help="Name of the VM to export")
    export_parser.add_argument("--output", "-o", type=Path, required=True,
                             help="Output file path")
    export_parser.add_argument("--format", choices=["json", "yaml"], default="yaml",
                             help="Output format (default: yaml)")

    # Import command
    import_parser = subparsers.add_parser("import", help="Import and create VM")
    import_parser.add_argument("config_file", type=Path, help="Configuration file")
    import_parser.add_argument("--new-name", help="Override VM name")
    import_parser.add_argument("--apply", action="store_true",
                             help="Actually create the VM (dry-run by default)")

    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Batch VM operations")
    batch_subparsers = batch_parser.add_subparsers(dest="batch_command")

    # Batch create
    batch_create = batch_subparsers.add_parser("create", help="Create VMs from batch file")
    batch_create.add_argument("batch_file", type=Path, help="Batch definition file")
    batch_create.add_argument("--apply", action="store_true",
                            help="Actually create VMs (dry-run by default)")

    # Batch template
    batch_template = batch_subparsers.add_parser("template", help="Generate batch template")
    batch_template.add_argument("--output", "-o", type=Path, default=Path("batch-template.yaml"),
                              help="Output file (default: batch-template.yaml)")
    batch_template.add_argument("--format", choices=["json", "yaml"], default="yaml",
                              help="Output format (default: yaml)")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete VM")
    delete_parser.add_argument("vm_name", help="Name of the VM to delete")
    delete_parser.add_argument("--force", "-f", action="store_true",
                             help="Force deletion without confirmation")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate VM configuration")
    validate_parser.add_argument("config_file", type=Path, help="Configuration file to validate")

    return parser


def main():
    """Main CLI entry point"""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        engine = VMCtlEngine(provider="virtualbox")

        if args.command == "list":
            vms = engine.list_vms()
            if not vms:
                print("No VMs found.")
            elif args.format == "simple":
                for vm in vms:
                    print(vm)
            else:
                # Table format
                print(f"{'NAME':<30} {'STATUS':<12}")
                print("-" * 42)
                for vm in vms:
                    try:
                        status = engine.get_vm_status(vm)
                    except ProviderError:
                        status = "unknown"
                    print(f"{vm:<30} {status:<12}")

        elif args.command == "read":
            vm = engine.read_vm(args.vm_name)
            serializer = engine.get_serializer(args.format)
            output = serializer.to_string(vm)
            print(output)

        elif args.command == "start":
            if engine.start_vm(args.vm_name):
                print(f"Started VM '{args.vm_name}'")

        elif args.command == "stop":
            if engine.stop_vm(args.vm_name, force=args.force):
                action = "Powered off" if args.force else "Stopped"
                print(f"{action} VM '{args.vm_name}'")

        elif args.command == "status":
            status = engine.get_vm_status(args.vm_name)
            print(f"{args.vm_name}: {status}")

        elif args.command == "edit":
            # Read existing VM
            vm = engine.read_vm(args.vm_name)

            # Apply edits
            if args.new_name:
                vm.name = args.new_name
            if args.memory:
                vm.memory.mb = args.memory
            if args.cpus:
                vm.cpu.count = args.cpus

            # Show what would be changed
            print("Edited VM configuration:")
            serializer = engine.get_serializer("yaml")
            print(serializer.to_string(vm))

            # TODO: Actually apply changes (would need modify implementation)
            print("\nNote: Full edit implementation requires modify commands")

        elif args.command == "create":
            # Determine source
            if args.source_vm.endswith(('.json', '.yaml', '.yml')):
                vm = engine.import_vm(Path(args.source_vm))
            else:
                vm = engine.read_vm(args.source_vm)

            # Apply overrides
            vm.name = args.new_name
            if args.memory:
                vm.memory.mb = args.memory
            if args.cpus:
                vm.cpu.count = args.cpus

            if args.apply:
                commands = engine.create_vm(vm)
                print(f"Created VM '{vm.name}'")
                print(f"Executed {len(commands)} commands")
            else:
                commands = engine.create_vm(vm, apply=False)
                print("Dry-run mode. Commands that would be executed:")
                for i, cmd in enumerate(commands, 1):
                    print(f"{i:3d}: {' '.join(cmd)}")

        elif args.command == "export":
            engine.export_vm(args.vm_name, args.output, args.format)
            print(f"Exported VM '{args.vm_name}' to {args.output}")

        elif args.command == "import":
            vm = engine.import_vm(args.config_file, args.new_name)

            if args.apply:
                commands = engine.create_vm(vm)
                print(f"Created VM '{vm.name}' from {args.config_file}")
                print(f"Executed {len(commands)} commands")
            else:
                commands = engine.create_vm(vm, apply=False)
                print("Dry-run mode. Commands that would be executed:")
                for i, cmd in enumerate(commands, 1):
                    print(f"{i:3d}: {' '.join(cmd)}")

        elif args.command == "batch":
            batch_creator = BatchCreator(engine)

            if args.batch_command == "create":
                if args.apply:
                    vms = batch_creator.create_from_file(args.batch_file)
                    for vm in vms:
                        engine.create_vm(vm)
                    print(f"Created {len(vms)} VMs from {args.batch_file}")
                else:
                    vms = batch_creator.create_from_file(args.batch_file)
                    print(f"Would create {len(vms)} VMs:")
                    for vm in vms:
                        print(f"  - {vm.name} (CPU: {vm.cpu.count}, Memory: {vm.memory.mb}MB)")

            elif args.batch_command == "template":
                batch_creator.generate_batch_template(args.output, args.format)
                print(f"Generated batch template at {args.output}")

        elif args.command == "delete":
            if not args.force:
                confirm = input(f"Are you sure you want to delete VM '{args.vm_name}'? (y/N): ")
                if confirm.lower() != 'y':
                    print("Deletion cancelled.")
                    return

            if engine.delete_vm(args.vm_name):
                print(f"Deleted VM '{args.vm_name}'")
            else:
                print(f"Failed to delete VM '{args.vm_name}'")

        elif args.command == "validate":
            vm = engine.import_vm(args.config_file)
            warnings = engine.validate_vm(vm)

            if warnings:
                print(f"Validation warnings for {vm.name}:")
                for warning in warnings:
                    print(f"  - {warning}")
            else:
                print(f"Configuration {args.config_file} is valid")
                print(f"  VM: {vm.name}")
                print(f"  CPU: {vm.cpu.count} cores")
                print(f"  Memory: {vm.memory.mb} MB")
                print(f"  Disks: {len(vm.disks)}")

    except (ValidationError, SerializationError, ProviderError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
