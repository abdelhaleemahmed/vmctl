"""
Command-line interface for vmctl — Click-based with shell completion.
"""
import sys
from pathlib import Path
from typing import List, Optional

import click

from vmctl import __version__
from vmctl.core.engine import VMCtlEngine
from vmctl.core.batch import BatchCreator
from vmctl.core.exceptions import ValidationError, SerializationError, ProviderError


# ---------------------------------------------------------------------------
# Shell-completion helpers
# ---------------------------------------------------------------------------

def _complete_vm_names(ctx, param, incomplete):
    """Return VirtualBox VM names that match *incomplete* for tab completion."""
    try:
        import subprocess
        result = subprocess.run(
            ["VBoxManage", "list", "vms"],
            capture_output=True, text=True, check=False
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
    "--format", "fmt",
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
    except ProviderError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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
    except ProviderError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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
    except ProviderError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

@cli.command("stop")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Force power-off (equivalent to pulling the power cord).",
)
def cmd_stop(vm_name, force):
    """Stop a running VM.

    Without --force, sends an ACPI shutdown signal so the guest OS can
    shut down cleanly.  Use --force only when the guest is unresponsive.

    \b
    Examples:
      vmctl stop ubuntu-server
      vmctl stop ubuntu-server --force
    """
    try:
        engine = VMCtlEngine()
        engine.stop_vm(vm_name, force=force)
        action = "Powered off" if force else "Stopped"
        click.echo(f"{action} VM '{vm_name}'")
    except ProviderError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

@cli.command("read")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--format", "fmt",
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
    except (ProviderError, SerializationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@cli.command("export")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file path (.yaml or .json).",
)
@click.option(
    "--format", "fmt",
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
    except (ProviderError, SerializationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@cli.command("import")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.option("--new-name", default=None, help="Override the VM name from the file.")
@click.option(
    "--execute",
    is_flag=True,
    help="Actually create the VM (dry-run by default).",
)
def cmd_import(config_file, new_name, execute):
    """Create a VM from a YAML or JSON configuration file.

    Without --execute the command prints the VBoxManage commands that
    would be run but does not touch VirtualBox (dry-run mode).

    Note: import creates a new VM with a blank disk.  It does not copy
    disk contents.  Use Bareos, rsync, or a similar tool to restore data.

    \b
    Examples:
      vmctl import ubuntu-server.yaml --new-name test-server
      vmctl import ubuntu-server.yaml --new-name test-server --execute
    """
    try:
        engine = VMCtlEngine()
        vm = engine.import_vm(config_file, new_name)
        if execute:
            engine.create_vm(vm)
            click.echo(f"Created VM '{vm.name}' from {config_file}")
        else:
            commands = engine.create_vm(vm, execute=False)
            click.echo("Dry-run mode.  Commands that would be executed:")
            for i, cmd in enumerate(commands, 1):
                click.echo(f"  {i:3d}: {' '.join(cmd)}")
            click.echo("\nRun with --execute to apply.")
    except (ProviderError, SerializationError, ValidationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

@cli.command("create")
@click.argument("source_vm", shell_complete=_complete_vm_names)
@click.option("--new-name", required=True, help="Name for the new VM.")
@click.option("--memory", type=int, default=None, help="Override memory in MB.")
@click.option("--cpus", type=int, default=None, help="Override CPU count.")
@click.option(
    "--execute",
    is_flag=True,
    help="Actually create the VM (dry-run by default).",
)
def cmd_create(source_vm, new_name, memory, cpus, execute):
    """Clone a VM configuration from an existing VirtualBox VM.

    Reads the source VM's configuration live from VirtualBox, applies
    any overrides, and creates a new blank VM with the same hardware
    profile.  Disk contents are NOT copied.

    \b
    Examples:
      vmctl create ubuntu-server --new-name ubuntu-clone
      vmctl create ubuntu-server --new-name ubuntu-clone --execute
      vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute
    """
    try:
        engine = VMCtlEngine()
        vm = engine.read_vm(source_vm)
        vm.name = new_name
        if memory:
            vm.memory.mb = memory
        if cpus:
            vm.cpu.count = cpus
        if execute:
            engine.create_vm(vm)
            click.echo(f"Created VM '{vm.name}'")
        else:
            commands = engine.create_vm(vm, execute=False)
            click.echo("Dry-run mode.  Commands that would be executed:")
            for i, cmd in enumerate(commands, 1):
                click.echo(f"  {i:3d}: {' '.join(cmd)}")
            click.echo("\nRun with --execute to apply.")
    except (ProviderError, ValidationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

@cli.command("edit")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option("--new-name", default=None, help="Rename the VM.")
@click.option("--memory", type=int, default=None, help="Set memory in MB.")
@click.option("--cpus", type=int, default=None, help="Set CPU count.")
def cmd_edit(vm_name, new_name, memory, cpus):
    """Modify CPU, memory, or name of an existing VM.

    The VM should be stopped before changing CPU or memory settings.

    \b
    Examples:
      vmctl edit my-vm --cpus 8
      vmctl edit my-vm --memory 16384
      vmctl edit my-vm --new-name renamed-vm
    """
    try:
        engine = VMCtlEngine()
        vm = engine.read_vm(vm_name)
        if new_name:
            vm.name = new_name
        if memory:
            vm.memory.mb = memory
        if cpus:
            vm.cpu.count = cpus
        serializer = engine.get_serializer("yaml")
        click.echo("Updated VM configuration:")
        click.echo(serializer.to_string(vm))
        click.echo("\nNote: full apply support requires VBoxManage modifyvm integration.")
    except (ProviderError, SerializationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@cli.command("delete")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--force", "-f",
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
        click.echo("Deletion cancelled.")
    except ProviderError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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
        if warnings:
            click.echo("Warnings:")
            for w in warnings:
                click.echo(f"  Warning: {w}")
        click.echo("Configuration is valid!")
        click.echo(f"  VM Name: {vm.name}")
        click.echo(f"  CPU:     {vm.cpu.count} cores")
        click.echo(f"  Memory:  {vm.memory.mb} MB")
        click.echo(f"  Disks:   {len(vm.disks)}")
    except (ValidationError, SerializationError) as e:
        click.echo(f"Validation failed: {e}", err=True)
        sys.exit(1)


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
def batch_create(batch_file, execute):
    """Create VMs from a batch definition file.

    The batch file defines a base VM and a list of named instances with
    optional per-instance overrides for CPU, memory, and disks.

    Without --execute the command summarises what would be created.

    \b
    Examples:
      vmctl batch create cluster.yaml
      vmctl batch create cluster.yaml --execute
    """
    try:
        engine = VMCtlEngine()
        creator = BatchCreator(engine)
        vms = creator.create_from_file(batch_file)
        if execute:
            for vm in vms:
                engine.create_vm(vm)
            click.echo(f"Created {len(vms)} VMs from {batch_file}")
        else:
            click.echo(f"Would create {len(vms)} VMs:")
            for vm in vms:
                click.echo(f"  {vm.name}  ({vm.cpu.count} CPUs, {vm.memory.mb} MB RAM, {len(vm.disks)} disk(s))")
            click.echo("\nRun with --execute to apply.")
    except (ValidationError, SerializationError, ProviderError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@batch.command("template")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=Path("batch-template.yaml"),
    show_default=True,
    help="Output file path.",
)
@click.option(
    "--format", "fmt",
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
        creator = BatchCreator(engine)
        creator.generate_batch_template(output, fmt)
        click.echo(f"Generated batch template → {output}")
    except (ValidationError, SerializationError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------

@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def cmd_completion(shell):
    """Print shell completion script to stdout.

    Source the output to enable tab completion for vmctl commands and
    VM names in your current shell session.

    \b
    Setup:
      # Bash (add to ~/.bashrc for permanent activation):
      eval "$(vmctl completion bash)"

      # Zsh (add to ~/.zshrc):
      eval "$(vmctl completion zsh)"

      # Fish (add to ~/.config/fish/config.fish):
      vmctl completion fish | source
    """
    prog = "vmctl"
    env_var = f"_{prog.upper()}_COMPLETE"
    if shell == "bash":
        click.echo(f'_{env_var}=bash_source {prog}')
        click.echo(f'\n# To activate, run:')
        click.echo(f'#   eval "$({env_var}=bash_source {prog})"')
        click.echo(f'# Or add to ~/.bashrc:')
        click.echo(f'#   eval "$({env_var}=bash_source {prog})"')
    elif shell == "zsh":
        click.echo(f'_{env_var}=zsh_source {prog}')
        click.echo(f'\n# To activate, run:')
        click.echo(f'#   eval "$({env_var}=zsh_source {prog})"')
        click.echo(f'# Or add to ~/.zshrc:')
        click.echo(f'#   eval "$({env_var}=zsh_source {prog})"')
    elif shell == "fish":
        click.echo(f'# Add to ~/.config/fish/config.fish:')
        click.echo(f'{env_var}=fish_source {prog} | source')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Main CLI entry point registered by pyproject.toml."""
    cli()


if __name__ == "__main__":
    main()
