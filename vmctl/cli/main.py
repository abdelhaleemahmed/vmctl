"""
Command-line interface for vmctl — Click-based with shell completion.
"""

import sys
from pathlib import Path
import click

from typing import List, Optional

from vmctl import __version__
from vmctl.core.engine import VMCtlEngine
from vmctl.core.vmconfig import DiskFormat
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
from vmctl.core.batch import BatchCreator
from vmctl.core.exceptions import (
    BatchError,
    ProviderError,
    ValidationError,
    VMAlreadyExistsError,
    VMToolError,
    get_error_summary,
)


# ---------------------------------------------------------------------------
# Shell-completion helpers
# ---------------------------------------------------------------------------


def _complete_vm_names(ctx, param, incomplete):
    """Return VirtualBox VM names that match *incomplete* for tab completion."""
    try:
        import subprocess

        result = subprocess.run(
            ["VBoxManage", "list", "vms"], capture_output=True, text=True, check=False
        )
        names = []
        for line in result.stdout.strip().splitlines():
            if line.startswith('"'):
                name = line.split('"')[1]
                if name.startswith(incomplete):
                    names.append(name)
        return names
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------


# Formats the provider can actually create, measured rather than assumed -- see
# providers/virtualbox/capabilities.py. A-03 will make this follow --provider;
# until then VirtualBox is the only provider, so the list is resolved once.
DISK_FORMATS = sorted(f.value for f in VirtualBoxCapabilities.get().creatable_formats())


def _apply_disk_format(vm, disk_format: Optional[str]) -> None:
    """Override the image format of every disk that gets created.

    Removable devices are left alone: a DVD or floppy drive holds an existing
    medium, so it has no format of its own to choose.

    Args:
        vm: Configuration to modify in place.
        disk_format: Format name, or None to leave the config as it is.
    """
    if not disk_format:
        return
    chosen = DiskFormat(disk_format)
    for disk in vm.disks:
        if not disk.is_removable:
            disk.format = chosen


def _warn(message: str) -> None:
    """Print a validation warning to stderr, so stdout stays pipeable."""
    click.echo(f"Warning: {message}", err=True)


def _require_absent(engine, name: str) -> None:
    """Fail before touching anything if *name* is already registered.

    Without this the first VBoxManage call fails and the create stops partway,
    which reads like a vmctl bug rather than a name collision (L-07).
    """
    try:
        existing = engine.list_vms()
    except ProviderError:
        return  # cannot check; let the provider report its own error
    if name in existing:
        _fail(VMAlreadyExistsError(name))


def _show_plan(plan, execute: bool, done_message: str) -> None:
    """Report a plan: either what was run, or what would be.

    Args:
        plan: The plan returned by the engine.
        execute: Whether it was actually applied.
        done_message: What to print when it was.
    """
    for message in plan.warnings:
        _warn(message)

    if not plan:
        click.echo("No changes to apply.")
        return

    if execute:
        click.echo(done_message)
        click.echo(plan.render())
    else:
        click.echo("Dry-run mode.  Commands that would be executed:")
        click.echo(plan.render())
        click.echo("\nRun with --execute to apply.")


def _fail(exc: Exception) -> None:
    """Print an error the way a user can act on, then exit 1.

    vmctl's own exceptions carry the field at fault, what was expected, and a
    recovery hint. Printing only ``str(exc)`` threw all of that away, so a
    mistyped field said what was wrong but not what to write instead.
    """
    if not isinstance(exc, VMToolError):
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(get_error_summary(exc), err=True)

    detail: List[str] = []
    for key, value in exc.context.items():
        if key == "constraints" and isinstance(value, list):
            detail.extend(str(item) for item in value)
        else:
            detail.append(f"{key.replace('_', ' ')}: {value}")
    for line in detail:
        click.echo(f"  {line}", err=True)

    # ValidationError derives a hint from `expected`/`constraints` when none was
    # given, which would just restate the lines above. Only print a hint that
    # adds something.
    hint = exc.recovery_hint
    if hint:
        gist = hint.split(": ", 1)[-1]
        if not any(gist in line for line in detail):
            click.echo(f"  hint: {hint}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
def cli():
    """vmctl — Virtual Machine Management Tool.

    Manage VirtualBox VMs with config-as-code support.  Export any VM to YAML
    or JSON, recreate it anywhere, and spin up entire clusters from a single
    batch file.

    \b
    Quick start:
      vmctl list
      vmctl export my-vm -o my-vm.yaml
      vmctl import my-vm.yaml --new-name test-vm
      vmctl import my-vm.yaml --new-name test-vm --execute
      vmctl batch create cluster.yaml --execute
    """


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command("list")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "simple"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def cmd_list(fmt):
    """List all VMs registered in VirtualBox.

    TABLE format (default) shows each VM name alongside its current status.
    SIMPLE format prints only the names, one per line — useful for scripting.

    \b
    Examples:
      vmctl list
      vmctl list --format simple
    """
    try:
        engine = VMCtlEngine()
        vms = engine.list_vms()
        if not vms:
            click.echo("No VMs found.")
            return
        if fmt == "simple":
            for vm in vms:
                click.echo(vm)
        else:
            click.echo(f"{'NAME':<30} {'STATUS':<12}")
            click.echo("-" * 42)
            for vm in vms:
                try:
                    status = engine.get_vm_status(vm)
                except ProviderError:
                    status = "unknown"
                click.echo(f"{vm:<30} {status:<12}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command("status")
@click.argument("vm_name", shell_complete=_complete_vm_names)
def cmd_status(vm_name):
    """Show the current state of a VM.

    Possible states: running, stopped, paused, saved, aborted,
    starting, stopping, unknown.

    \b
    Example:
      vmctl status ubuntu-server
    """
    try:
        engine = VMCtlEngine()
        status = engine.get_vm_status(vm_name)
        click.echo(status)
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command("start")
@click.argument("vm_name", shell_complete=_complete_vm_names)
def cmd_start(vm_name):
    """Start a VM in headless mode.

    The VM has no GUI window.  Use VirtualBox GUI or SSH to interact
    with the guest once it has booted.

    \b
    Example:
      vmctl start ubuntu-server
    """
    try:
        engine = VMCtlEngine()
        engine.start_vm(vm_name)
        click.echo(f"Started VM '{vm_name}'")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@cli.command("stop")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force power-off (equivalent to pulling the power cord).",
)
@click.option(
    "--wait",
    type=int,
    default=0,
    metavar="SECONDS",
    help="Wait up to SECONDS for the VM to actually stop.",
)
def cmd_stop(vm_name, force, wait):
    """Stop a running VM.

    Without --force, sends an ACPI shutdown signal so the guest OS can
    shut down cleanly.  Use --force only when the guest is unresponsive.

    \b
    Examples:
      vmctl stop ubuntu-server
      vmctl stop ubuntu-server --wait 60
      vmctl stop ubuntu-server --force
    """
    try:
        engine = VMCtlEngine()
        engine.stop_vm(vm_name, force=force, wait=wait)
        action = "Powered off" if force else "Stopped" if wait else "Sent shutdown signal to"
        click.echo(f"{action} VM '{vm_name}'")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@cli.command("read")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="Output format.",
)
def cmd_read(vm_name, fmt):
    """Print a VM's configuration to stdout.

    Reads the live configuration directly from VirtualBox and outputs
    it as YAML (default) or JSON.

    \b
    Examples:
      vmctl read ubuntu-server
      vmctl read ubuntu-server --format json
    """
    try:
        engine = VMCtlEngine()
        vm = engine.read_vm(vm_name)
        serializer = engine.get_serializer(fmt)
        click.echo(serializer.to_string(vm))
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@cli.command("export")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file path (.yaml or .json).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="Output format (inferred from extension if not specified).",
)
def cmd_export(vm_name, output, fmt):
    """Save a VM's configuration to a YAML or JSON file.

    The VM does not need to be stopped before exporting.

    \b
    Examples:
      vmctl export ubuntu-server -o ubuntu-server.yaml
      vmctl export ubuntu-server -o ubuntu-server.json --format json
    """
    try:
        engine = VMCtlEngine()
        engine.export_vm(vm_name, output, fmt)
        click.echo(f"Exported '{vm_name}' → {output}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@cli.command("import")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.option("--new-name", default=None, help="Override the VM name from the file.")
@click.option(
    "--disk-format",
    type=click.Choice(DISK_FORMATS),
    default=None,
    help="Create the VM's disks in this image format instead of the one in the file.",
)
@click.option(
    "--execute",
    is_flag=True,
    help="Actually create the VM (dry-run by default).",
)
def cmd_import(config_file, new_name, disk_format, execute):
    """Create a VM from a YAML or JSON configuration file.

    Without --execute the command prints the VBoxManage commands that
    would be run but does not touch VirtualBox (dry-run mode).

    Note: import creates a new VM with a blank disk.  It does not copy
    disk contents.  Use Bareos, rsync, or a similar tool to restore data.

    \b
    Examples:
      vmctl import ubuntu-server.yaml --new-name test-server
      vmctl import ubuntu-server.yaml --new-name test-server --execute
      vmctl import ubuntu-server.yaml --new-name test-server --disk-format vmdk
    """
    try:
        engine = VMCtlEngine()
        vm = engine.import_vm(config_file, new_name)
        _apply_disk_format(vm, disk_format)
        if execute:
            _require_absent(engine, vm.name)
        plan = engine.create_vm(vm, execute=execute, on_warning=_warn)
        _show_plan(plan, execute, f"Created VM '{vm.name}' from {config_file}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@cli.command("create")
@click.argument("source_vm", shell_complete=_complete_vm_names)
@click.option("--new-name", required=True, help="Name for the new VM.")
@click.option("--memory", type=int, default=None, help="Override memory in MB.")
@click.option("--cpus", type=int, default=None, help="Override CPU count.")
@click.option(
    "--disk-format",
    type=click.Choice(DISK_FORMATS),
    default=None,
    help="Create the new VM's disks in this image format.",
)
@click.option(
    "--execute",
    is_flag=True,
    help="Actually create the VM (dry-run by default).",
)
def cmd_create(source_vm, new_name, memory, cpus, disk_format, execute):
    """Clone a VM configuration from an existing VirtualBox VM.

    Reads the source VM's configuration live from VirtualBox, applies
    any overrides, and creates a new blank VM with the same hardware
    profile.  Disk contents are NOT copied.

    \b
    Examples:
      vmctl create ubuntu-server --new-name ubuntu-clone
      vmctl create ubuntu-server --new-name ubuntu-clone --execute
      vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute
      vmctl create ubuntu-server --new-name vmware-clone --disk-format vmdk
    """
    try:
        engine = VMCtlEngine()
        vm = engine.read_vm(source_vm)
        vm.name = new_name
        if memory:
            vm.memory.mb = memory
        if cpus:
            vm.cpu.count = cpus
        _apply_disk_format(vm, disk_format)
        if execute:
            _require_absent(engine, vm.name)
        plan = engine.create_vm(vm, execute=execute, on_warning=_warn)
        _show_plan(plan, execute, f"Created VM '{vm.name}'")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@cli.command("edit")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option("--new-name", default=None, help="Rename the VM.")
@click.option("--memory", type=int, default=None, help="Set memory in MB.")
@click.option("--vram", type=int, default=None, help="Set video memory in MB.")
@click.option("--cpus", type=int, default=None, help="Set CPU count.")
@click.option(
    "--execute",
    is_flag=True,
    help="Actually apply the change (dry-run by default).",
)
def cmd_edit(vm_name, new_name, memory, vram, cpus, execute):
    """Change the CPU, memory, video memory or name of an existing VM.

    Only the settings you pass are changed, and only the ones that actually
    differ produce a command.  The VM must be stopped.

    Without --execute the command prints the VBoxManage commands it would run.

    \b
    Examples:
      vmctl edit my-vm --cpus 8
      vmctl edit my-vm --memory 16384 --execute
      vmctl edit my-vm --new-name renamed-vm --execute
    """
    try:
        engine = VMCtlEngine()
        vm = engine.read_vm(vm_name)
        if new_name:
            vm.name = new_name
        if memory:
            vm.memory.mb = memory
        if vram:
            vm.memory.vram_mb = vram
        if cpus:
            vm.cpu.count = cpus

        plan = engine.edit_vm(vm_name, vm, execute=execute, on_warning=_warn)
        _show_plan(plan, execute, f"Updated VM '{vm_name}'")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@cli.command("delete")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def cmd_delete(vm_name, force):
    """Unregister a VM and delete all associated disk files.

    This action is irreversible.  Without --force, you will be asked to
    confirm before anything is deleted.

    \b
    Examples:
      vmctl delete old-vm
      vmctl delete old-vm --force
    """
    try:
        if not force:
            click.confirm(f"Delete VM '{vm_name}' and all its disk files?", abort=True)
        engine = VMCtlEngine()
        engine.delete_vm(vm_name)
        click.echo(f"Deleted VM '{vm_name}'")
    except click.Abort:
        # Exit non-zero: a script cannot otherwise tell "user said no" from
        # "deleted successfully" (L-01).
        click.echo("Deletion cancelled.", err=True)
        sys.exit(1)
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@cli.command("validate")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
def cmd_validate(config_file):
    """Validate a YAML or JSON VM configuration file.

    Runs four validation phases: schema, provider limits, logical
    constraints, and warnings.  Exits with code 1 on error.

    \b
    Example:
      vmctl validate my-config.yaml
    """
    try:
        engine = VMCtlEngine()
        vm = engine.import_vm(config_file)
        warnings = engine.validate_vm(vm)
        for w in warnings:
            _warn(w)
        click.echo("Configuration is valid!")
        click.echo(f"  VM Name: {vm.name}")
        click.echo(f"  CPU:     {vm.cpu.count} cores")
        click.echo(f"  Memory:  {vm.memory.mb} MB")
        click.echo(f"  Disks:   {len(vm.disks)}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# batch group
# ---------------------------------------------------------------------------


@cli.group("batch")
def batch():
    """Create multiple VMs from a batch definition file."""


@batch.command("create")
@click.argument("batch_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--execute",
    is_flag=True,
    help="Actually create the VMs (dry-run by default).",
)
@click.option(
    "--continue-on-error",
    is_flag=True,
    help="Keep going after a VM fails instead of stopping at the first error.",
)
def batch_create(batch_file, execute, continue_on_error):
    """Create VMs from a batch definition file.

    The batch file defines a base VM and a list of named instances with
    optional per-instance overrides for CPU, memory, and disks.

    The whole file is resolved and validated, and every name is checked against
    the VMs that already exist, before anything is created.  Without --execute
    the command only summarises what it would do.

    \b
    Examples:
      vmctl batch create cluster.yaml
      vmctl batch create cluster.yaml --execute
      vmctl batch create cluster.yaml --execute --continue-on-error
    """
    try:
        engine = VMCtlEngine()
        creator = BatchCreator(engine, on_warning=_warn)
        vms = creator.create_from_file(batch_file)

        clashes = creator.preflight(vms)
        if clashes:
            _fail(
                ValidationError(
                    f"{len(clashes)} VM name(s) in {batch_file} already exist: "
                    f"{', '.join(clashes)}",
                    recovery_hint="Rename those instances, or delete the existing " "VMs first.",
                )
            )

        if not execute:
            click.echo(f"Would create {len(vms)} VMs:")
            for vm in vms:
                click.echo(
                    f"  {vm.name}  ({vm.cpu.count} CPUs, {vm.memory.mb} MB RAM, "
                    f"{len(vm.disks)} disk(s))"
                )
            click.echo("\nRun with --execute to apply.")
            return

        created, failed = [], []
        for vm in vms:
            try:
                engine.create_vm(vm, on_warning=_warn)
                created.append(vm.name)
                click.echo(f"Created {vm.name}")
            except (ProviderError, ValidationError) as exc:
                failed.append((vm.name, exc))
                click.echo(f"Failed {vm.name}: {exc}", err=True)
                if not continue_on_error:
                    break

        # Always report what actually happened: a partially created cluster is
        # the thing a user most needs to know about (F-12).
        click.echo(f"\nCreated {len(created)} of {len(vms)} VMs from {batch_file}")
        if failed:
            click.echo(f"Failed: {', '.join(name for name, _ in failed)}", err=True)
        skipped = [
            vm.name
            for vm in vms
            if vm.name not in created and vm.name not in {n for n, _ in failed}
        ]
        if skipped:
            click.echo(
                f"Not attempted: {', '.join(skipped)}"
                + (
                    ""
                    if continue_on_error
                    else " (stopped at the first failure; " "use --continue-on-error to go on)"
                ),
                err=True,
            )
        if failed:
            _fail(
                BatchError(
                    f"{len(failed)} of {len(vms)} VMs could not be created",
                    batch_file=str(batch_file),
                    failed_vms=[name for name, _ in failed],
                    successful_vms=created,
                )
            )
    except VMToolError as e:
        _fail(e)


@batch.command("template")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("batch-template.yaml"),
    show_default=True,
    help="Output file path.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="Output format.",
)
def batch_template(output, fmt):
    """Generate a starter batch template file.

    The generated file contains a working example with three VM instances
    that can be customised and used with 'vmctl batch create'.

    \b
    Example:
      vmctl batch template -o my-cluster.yaml
    """
    try:
        engine = VMCtlEngine()
        creator = BatchCreator(engine, on_warning=_warn)
        creator.generate_batch_template(output, fmt)
        click.echo(f"Generated batch template → {output}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def cmd_completion(shell):
    """Print the shell completion script to stdout.

    Evaluate the output to enable tab completion for vmctl commands and for VM
    names read live from VirtualBox.

    \b
    Setup:
      # Bash (add to ~/.bashrc to make it permanent):
      eval "$(vmctl completion bash)"

      # Zsh (add to ~/.zshrc):
      eval "$(vmctl completion zsh)"

      # Fish (add to ~/.config/fish/config.fish):
      vmctl completion fish | source
    """
    # Ask Click for the script rather than printing instructions about it. The
    # old implementation echoed `__VMCTL_COMPLETE=bash_source vmctl` -- one
    # underscore too many, and a command rather than a script, so the documented
    # `eval "$(vmctl completion bash)"` set a variable Click ignores and then
    # evaluated vmctl's help output (F-09).
    from click.shell_completion import get_completion_class

    completion_cls = get_completion_class(shell)
    if completion_cls is None:  # pragma: no cover - Click always ships these
        raise click.ClickException(f"Click cannot generate completion for {shell}")

    prog_name = "vmctl"
    complete_var = f"_{prog_name.upper()}_COMPLETE"
    click.echo(completion_cls(cli, {}, prog_name, complete_var).source())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Main CLI entry point registered by pyproject.toml."""
    cli()


if __name__ == "__main__":
    main()
