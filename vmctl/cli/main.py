"""
Command-line interface for vmctl — Click-based with shell completion.
"""

import json
import os
import sys
from pathlib import Path
import click

from typing import List, Optional

from vmctl import __version__
from vmctl.core import registry
from vmctl.core.engine import VMCtlEngine
from vmctl.core.convert import convert as plan_convert
from vmctl.core.diff import diff, stated_paths, summarise
from vmctl.core.naming import safe_filename
from vmctl.core.schema import build as build_schema
from vmctl.core.migrate import plan_migration
from vmctl.core.translate import Policy
from vmctl.core.vmconfig import DeviceKind, DiskFormat
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
    for disk in vm.storage:
        if not disk.is_removable:
            disk.format = chosen


def _engine(ctx=None) -> VMCtlEngine:
    """Build an engine for the provider the user selected.

    Args:
        ctx: Click context carrying ``--provider``, when there is one.

    Returns:
        VMCtlEngine: bound to the chosen provider.
    """
    name = None
    if ctx is not None and ctx.obj:
        name = ctx.obj.get("provider")
    return VMCtlEngine(name)


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


def _show_plan(plan, execute: bool, done_message: str, out: Optional[str] = None) -> None:
    """Report a plan: either what was run, or what would be.

    Args:
        plan: The plan returned by the engine.
        execute: Whether it was actually applied.
        done_message: What to print when it was.
        out: Where to also write it as a native artifact (E-16).
    """
    for message in plan.warnings:
        _warn(message)

    if not plan:
        click.echo("No changes to apply.")
        return

    if out is not None:
        _write_plan(plan, out)

    if execute:
        click.echo(done_message)
        click.echo(plan.render())
    else:
        click.echo("Dry-run mode.  Commands that would be executed:")
        click.echo(plan.render())
        click.echo("\nRun with --execute to apply.")


def _write_plan(plan, out: str) -> None:
    """Write a plan out as something another tool can use (E-16).

    ``Plan`` was introduced so that "what vmctl would do" is data rather than a
    printed line (A-01); this is where that pays off for someone who wants to keep
    the work, review it, or run it from their own pipeline.

    One rule, so there is nothing to guess: a path that is a directory (or ends in a
    separator) gets ``plan.sh`` *and* each native artifact as its own file -- the
    libvirt domain XML, the ``.vmx``, the QEMU run script. Any other path gets the
    script, which is complete on its own because it carries those artifacts inline as
    heredocs.

    Args:
        plan: The plan to write.
        out: Where to write it.
    """
    # The trailing separator is the distinction, kept as a string on purpose:
    # pathlib normalises it away, and it is how cp and rsync are told the same thing.
    as_directory = out.endswith(("/", os.sep)) or Path(out).is_dir()
    destination = Path(out)
    try:
        script = plan.as_script()
    except ValueError as exc:
        _fail(ProviderError(f"this provider's plan cannot be written as a script: {exc}"))
        return

    written: List[Path] = []
    if as_directory:
        destination.mkdir(parents=True, exist_ok=True)
        for path, content in plan.native_artifacts().items():
            target = destination / Path(path).name
            target.write_text(content)
            written.append(target)
        target = destination / "plan.sh"
        target.write_text(script)
        written.append(target)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(script)
        written.append(destination)

    for path in written:
        click.echo(f"Wrote {path}")


def _read_mapping(path: Path) -> dict:
    """Return a config file as the plain mapping it is.

    Loading it into a :class:`VMConfig` fills in every default, which is exactly what
    ``diff`` must not treat as a request -- so the file is read twice, once as a VM and
    once as what was written.
    """
    text = path.read_text()
    if path.suffix.lower() in (".json",):
        return json.loads(text) or {}
    import yaml

    return yaml.safe_load(text) or {}


def _fail_with(exc: Exception, code: int) -> None:
    """Report an error and exit with a specific code.

    ``diff`` follows diff(1), where 1 means "they differ" -- so its errors have to be
    distinguishable from its findings, or a script cannot tell them apart.
    """
    try:
        _fail(exc)
    except SystemExit:
        sys.exit(code)


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
@click.option(
    "-p",
    "--provider",
    default=None,
    metavar="NAME",
    help="Hypervisor to talk to. Defaults to $VMCTL_PROVIDER, then whichever "
    "is installed. See 'vmctl providers'.",
)
@click.pass_context
def cli(ctx, provider):
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
    ctx.ensure_object(dict)
    ctx.obj["provider"] = provider


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
@click.pass_context
def cmd_list(ctx, fmt):
    """List all VMs registered in VirtualBox.

    TABLE format (default) shows each VM name alongside its current status.
    SIMPLE format prints only the names, one per line — useful for scripting.

    \b
    Examples:
      vmctl list
      vmctl list --format simple
    """
    try:
        engine = _engine(ctx)
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
@click.pass_context
def cmd_status(ctx, vm_name):
    """Show the current state of a VM.

    Possible states: running, stopped, paused, saved, aborted,
    starting, stopping, unknown.

    \b
    Example:
      vmctl status ubuntu-server
    """
    try:
        engine = _engine(ctx)
        status = engine.get_vm_status(vm_name)
        click.echo(status)
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command("start")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.pass_context
def cmd_start(ctx, vm_name):
    """Start a VM in headless mode.

    The VM has no GUI window.  Use VirtualBox GUI or SSH to interact
    with the guest once it has booted.

    \b
    Example:
      vmctl start ubuntu-server
    """
    try:
        engine = _engine(ctx)
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
@click.pass_context
def cmd_stop(ctx, vm_name, force, wait):
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
        engine = _engine(ctx)
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
@click.pass_context
def cmd_read(ctx, vm_name, fmt):
    """Print a VM's configuration to stdout.

    Reads the live configuration directly from VirtualBox and outputs
    it as YAML (default) or JSON.

    \b
    Examples:
      vmctl read ubuntu-server
      vmctl read ubuntu-server --format json
    """
    try:
        engine = _engine(ctx)
        vm = engine.read_vm(vm_name)
        serializer = engine.get_serializer(fmt)
        click.echo(serializer.to_string(vm))
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@cli.command("export")
@click.argument("vm_name", required=False, shell_complete=_complete_vm_names)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path (.yaml or .json). Required unless --all is given.",
)
@click.option(
    "--all",
    "export_all",
    is_flag=True,
    help="Export every VM, one file each, into the directory given by -d.",
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(path_type=Path),
    default=None,
    help="Where --all writes its files.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="Output format (inferred from extension if not specified).",
)
@click.pass_context
def cmd_export(ctx, vm_name, output, export_all, directory, fmt):
    """Save a VM's configuration to a YAML or JSON file.

    The VM does not need to be stopped before exporting.

    With ``--all`` every VM is written as its own file, plus a manifest listing
    them -- which turns a whole lab into something committable, and is what the
    documented "lab snapshot" used to need a shell loop for.

    \b
    Examples:
      vmctl export ubuntu-server -o ubuntu-server.yaml
      vmctl export ubuntu-server -o ubuntu-server.json --format json
      vmctl export --all -d lab/                  # one file per VM, plus a manifest
    """
    if export_all:
        if vm_name or output:
            _fail(ValidationError("--all exports every VM, so it takes -d, not a name or -o"))
        if not directory:
            _fail(ValidationError("--all needs -d to say where the files go", field="-d"))
        _export_all(ctx, directory, fmt)
        return
    if not vm_name or not output:
        _fail(
            ValidationError(
                "export needs a VM name and -o, or --all with -d",
                recovery_hint="vmctl export <vm> -o <file>, or vmctl export --all -d <dir>",
            )
        )
    try:
        engine = _engine(ctx)
        engine.export_vm(vm_name, output, fmt)
        click.echo(f"Exported '{vm_name}' → {output}")
    except VMToolError as e:
        _fail(e)


def _export_all(ctx, directory: Path, fmt: str) -> None:
    """Export every VM into a directory, with a manifest (E-04).

    The manifest is deliberately dull: the provider, and the VMs with their files,
    sorted. No timestamp and no version -- the point is a directory that can be
    committed, and a file that changes every time it is written is one nobody can
    review.
    """
    try:
        engine = _engine(ctx)
        names = sorted(engine.list_vms())
    except Exception as exc:
        _fail(exc)
        return

    directory.mkdir(parents=True, exist_ok=True)
    suffix = "json" if fmt == "json" else "yaml"
    exported: List[dict] = []
    failed: List[str] = []
    for name in names:
        target = directory / f"{safe_filename(name)}.{suffix}"
        try:
            engine.export_vm(name, target, fmt)
        except Exception as exc:
            # One unreadable VM must not cost the other nineteen.
            failed.append(name)
            _warn(f"{name} could not be exported: {exc}")
            continue
        exported.append({"name": name, "file": target.name})
        click.echo(f"Exported '{name}' → {target}")

    manifest = directory / f"manifest.{suffix}"
    body = {"provider": engine.provider_name, "vms": exported}
    if fmt == "json":
        manifest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    else:
        import yaml

        manifest.write_text(yaml.safe_dump(body, sort_keys=True, default_flow_style=False))
    click.echo(f"Wrote {manifest} ({len(exported)} VM(s))")
    if failed:
        sys.exit(1)


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
@click.option(
    "--policy",
    type=click.Choice([p.value for p in Policy]),
    default=Policy.STRICT.value,
    show_default=True,
    help="What to do about settings this hypervisor does not support: refuse "
    "(strict), substitute the nearest supported value and report it (nearest), "
    "or also convert disk images (convert).",
)
@click.option(
    "--out",
    default=None,
    metavar="PATH",
    help="Also write the plan out. A plain path gets a shell script; a path ending "
    "in / (or an existing directory) gets plan.sh plus the provider's own artifact "
    "-- the domain XML, the .vmx, the run script.",
)
@click.pass_context
def cmd_import(ctx, config_file, new_name, disk_format, policy, execute, out):
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
        engine = _engine(ctx)
        vm = engine.import_vm(config_file, new_name)
        _apply_disk_format(vm, disk_format)
        if execute:
            _require_absent(engine, vm.name)
        plan = engine.create_vm(vm, execute=execute, on_warning=_warn, policy=Policy(policy))
        _show_plan(plan, execute, f"Created VM '{vm.name}' from {config_file}", out)
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
@click.option(
    "--policy",
    type=click.Choice([p.value for p in Policy]),
    default=Policy.STRICT.value,
    show_default=True,
    help="What to do about settings this hypervisor does not support: refuse "
    "(strict), substitute the nearest supported value and report it (nearest), "
    "or also convert disk images (convert).",
)
@click.option(
    "--out",
    default=None,
    metavar="PATH",
    help="Also write the plan out. A plain path gets a shell script; a path ending "
    "in / (or an existing directory) gets plan.sh plus the provider's own artifact "
    "-- the domain XML, the .vmx, the run script.",
)
@click.pass_context
def cmd_create(ctx, source_vm, new_name, memory, cpus, disk_format, policy, execute, out):
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
        engine = _engine(ctx)
        vm = engine.read_vm(source_vm)
        vm.name = new_name
        if memory:
            vm.memory.mb = memory
        if cpus:
            vm.cpu.count = cpus
        _apply_disk_format(vm, disk_format)
        if execute:
            _require_absent(engine, vm.name)
        plan = engine.create_vm(vm, execute=execute, on_warning=_warn, policy=Policy(policy))
        _show_plan(plan, execute, f"Created VM '{vm.name}'", out)
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
@click.option(
    "--out",
    default=None,
    metavar="PATH",
    help="Also write the plan out. A plain path gets a shell script; a path ending "
    "in / (or an existing directory) gets plan.sh plus the provider's own artifact "
    "-- the domain XML, the .vmx, the run script.",
)
@click.pass_context
def cmd_edit(ctx, vm_name, new_name, memory, vram, cpus, execute, out):
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
        engine = _engine(ctx)
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
        _show_plan(plan, execute, f"Updated VM '{vm_name}'", out)
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
@click.pass_context
def cmd_delete(ctx, vm_name, force):
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
        engine = _engine(ctx)
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
@click.pass_context
def cmd_validate(ctx, config_file):
    """Validate a YAML or JSON VM configuration file.

    Runs four validation phases: schema, provider limits, logical
    constraints, and warnings.  Exits with code 1 on error.

    \b
    Example:
      vmctl validate my-config.yaml
    """
    try:
        engine = _engine(ctx)
        vm = engine.import_vm(config_file)
        warnings = engine.validate_vm(vm)
        for w in warnings:
            _warn(w)
        click.echo("Configuration is valid!")
        click.echo(f"  VM Name: {vm.name}")
        click.echo(f"  CPU:     {vm.cpu.count} cores")
        click.echo(f"  Memory:  {vm.memory.mb} MB")
        click.echo(f"  Disks:   {len(vm.storage)}")
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
@click.pass_context
def batch_create(ctx, batch_file, execute, continue_on_error):
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
        engine = _engine(ctx)
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
                    f"{len(vm.storage)} disk(s))"
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
@click.pass_context
def batch_template(ctx, output, fmt):
    """Generate a starter batch template file.

    The generated file contains a working example with three VM instances
    that can be customised and used with 'vmctl batch create'.

    \b
    Example:
      vmctl batch template -o my-cluster.yaml
    """
    try:
        engine = _engine(ctx)
        creator = BatchCreator(engine, on_warning=_warn)
        creator.generate_batch_template(output, fmt)
        click.echo(f"Generated batch template → {output}")
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@cli.command("migrate")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.option(
    "--to",
    "target_name",
    required=True,
    metavar="PROVIDER",
    help="Hypervisor to move the VM to. See 'vmctl providers'.",
)
@click.option(
    "--from",
    "source_name",
    default=None,
    metavar="PROVIDER",
    help="Hypervisor to read from. Defaults to the selected provider.",
)
@click.option("--new-name", default=None, help="Name to use on the target.")
@click.option(
    "--with-disks",
    is_flag=True,
    help="Also convert and attach the disk images. They must be readable from "
    "this machine; without this, blank disks are created.",
)
@click.option(
    "--policy",
    type=click.Choice([p.value for p in Policy]),
    default=Policy.STRICT.value,
    show_default=True,
    help="How to handle settings the target hypervisor cannot express. The "
    "default refuses and lists them; 'convert' is the usual choice for a "
    "migration between different hypervisors.",
)
@click.option(
    "--execute",
    is_flag=True,
    help="Actually perform the migration (dry-run by default).",
)
@click.option(
    "--out",
    default=None,
    metavar="PATH",
    help="Also write the plan out. A plain path gets a shell script; a path ending "
    "in / (or an existing directory) gets plan.sh plus the provider's own artifact "
    "-- the domain XML, the .vmx, the run script.",
)
@click.pass_context
def cmd_migrate(ctx, vm_name, target_name, source_name, new_name, with_disks, policy, execute, out):
    """Recreate a VM on a different hypervisor.

    Reads the VM from one provider, works out how to express it on another, and
    creates it there.  Anything that cannot carry over exactly is reported before
    anything runs.

    By default only the configuration moves and the new VM gets blank disks, the
    same as 'vmctl import'.  Pass --with-disks to convert and attach the real
    images; they have to be readable from the machine running vmctl, which is not
    the case when the source hypervisor lives on another host.

    \b
    Examples:
      vmctl migrate web-01 --from virtualbox --to libvirt
      vmctl migrate web-01 --from virtualbox --to libvirt --execute
      vmctl migrate web-01 --to libvirt --with-disks --execute
    """
    try:
        source = _engine(ctx) if source_name is None else VMCtlEngine(source_name)
        target = VMCtlEngine(target_name)

        migration = plan_migration(
            source.backend,
            target.backend,
            vm_name,
            new_name=new_name,
            policy=Policy(policy),
            with_disks=with_disks,
        )

        click.echo(
            f"{vm_name} ({migration.source_provider}) "
            f"-> {migration.vm.name} ({migration.target_provider})"
        )
        if not with_disks:
            click.echo(
                "Configuration only: the new VM gets blank disks. "
                "Pass --with-disks to bring the data."
            )

        if execute:
            _require_absent(target, migration.vm.name)
            target.backend.run_plan(migration.plan)
        _show_plan(
            migration.plan,
            execute,
            f"Migrated {vm_name} to {migration.target_provider} " f"as {migration.vm.name}",
            out,
        )
    except VMToolError as e:
        _fail(e)


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


@cli.command("convert")
@click.argument("source", type=click.Path(path_type=Path))
@click.argument("target", type=click.Path(path_type=Path))
@click.option(
    "--to",
    "target_format",
    type=click.Choice(DISK_FORMATS),
    default=None,
    help="Format to write. Inferred from TARGET's extension when omitted.",
)
@click.option(
    "--from",
    "source_format",
    type=click.Choice([f.value for f in DiskFormat]),
    default=None,
    help="Format of SOURCE. Detected by the conversion tool when omitted.",
)
@click.option(
    "--execute",
    is_flag=True,
    help="Actually convert (dry-run by default).",
)
@click.pass_context
def cmd_convert(ctx, source, target, target_format, source_format, execute):
    """Convert a disk image from one format to another.

    Uses whatever the selected provider converts with -- ``qemu-img`` for
    libvirt, ``VBoxManage clonemedium`` for VirtualBox -- so the formats on offer
    are the ones that provider can actually write.

    \b
    Examples:
      vmctl convert disk.vdi disk.qcow2 --execute
      vmctl -p virtualbox convert disk.vdi disk.vmdk --execute
      vmctl convert disk.img disk.qcow2 --from raw --to qcow2
    """
    try:
        engine = _engine(ctx)
        converter = engine.backend.converter()
        if converter is None:
            _fail(
                ValidationError(
                    f"the {engine.provider_name} provider cannot convert images",
                    field="provider",
                    value=engine.provider_name,
                )
            )

        chosen = _format_from(target_format, target)
        plan = plan_convert(
            source=str(source),
            target=str(target),
            target_format=chosen,
            converter=converter,
            provider=engine.provider_name,
            source_format=DiskFormat(source_format) if source_format else None,
            label=source.name,
        )
        if not plan:
            click.echo("Nothing to do: the source is already in that format.")
            return
        if execute:
            engine.backend.run_plan(plan)
        _show_plan(plan, execute, f"Converted {source} -> {target}")
    except VMToolError as e:
        _fail(e)


def _format_from(explicit, target_path) -> DiskFormat:
    """Decide the target format from the option, or the file extension.

    Args:
        explicit: The ``--to`` value, if given.
        target_path: The destination path.

    Returns:
        The format to write.

    Raises:
        ValidationError: If neither says what the format should be.
    """
    if explicit:
        return DiskFormat(explicit)
    suffix = target_path.suffix.lstrip(".").lower()
    for fmt in DiskFormat:
        if fmt.value == suffix:
            return fmt
    # The extension a provider uses is not always the format's own name.
    for fmt in DiskFormat:
        if suffix in VirtualBoxCapabilities.get().format_spec(fmt).extensions:
            return fmt
    raise ValidationError(
        f"cannot tell what format {target_path} should be",
        field="target",
        value=str(target_path),
        recovery_hint="Name it with --to, or give the target a known extension.",
    )


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


@cli.command("providers")
def cmd_providers():
    """List the hypervisors vmctl can talk to, and whether they work here.

    A provider is USABLE when its tooling is installed and responding. The one
    marked DEFAULT is what vmctl picks when you do not pass --provider.

    \b
    Example:
      vmctl providers
    """
    default = registry.resolve(None)
    click.echo(f"{'NAME':<14} {'STATUS':<12} {'VERSION':<12} DESCRIPTION")
    click.echo("-" * 74)
    for entry in registry.entries():
        usable = registry.is_available(entry.name)
        version = ""
        if usable:
            try:
                backend = registry.create(entry.name)
                version = backend.version()
            except Exception:
                version = "?"
        status = "usable" if usable else "unavailable"
        if entry.name == default:
            status += " *"
        click.echo(f"{entry.name:<14} {status:<12} {version:<12} {entry.description}")
    click.echo("\n* the provider vmctl would use by default")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


@cli.command("schema")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write it. Prints to stdout when not given.",
)
def cmd_schema(output):
    """Print a JSON Schema for the configuration format.

    Point an editor at it and config files get autocompletion and validation in
    place, which is what config-as-code users expect. It is generated from vmctl's own
    model, so it cannot describe a file vmctl would reject -- and it accepts the 1.1.x
    field names too, because a schema that refused those would be wrong.

    \b
    Examples:
      vmctl schema -o vmctl.schema.json
      # then, at the top of a config file:
      #   # yaml-language-server: $schema=./vmctl.schema.json
    """
    text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        click.echo(text, nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    click.echo(f"Wrote {output}")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@cli.command("diff")
@click.argument("vm_name", shell_complete=_complete_vm_names)
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def cmd_diff(ctx, vm_name, config_file):
    """Show how a VM differs from a configuration file.

    Read-only. Reads the VM from the hypervisor, loads the file, and compares them
    field by field -- which is also the quickest way to see whether an export and
    re-import was faithful.

    Devices are matched by where they are (bus, slot, unit) rather than by name,
    because a device's name is something most hypervisors have nowhere to store.
    Anything only one side can know -- a generated MAC, a provider's own identifiers,
    the path an image happens to have on this host -- is not reported as drift.

    \b
    Exit codes, the same as diff(1):
      0  the VM matches the file
      1  they differ
      2  something went wrong

    \b
    Examples:
      vmctl diff web-01 web-01.yaml
      vmctl diff web-01 web-01.yaml || echo "drifted"
    """
    try:
        engine = _engine(ctx)
        live = engine.read_vm(vm_name)
        # The same loader `import` uses, so a file that imports also diffs.
        desired = engine.import_vm(config_file)
        # ...and the raw file as well, because only what it actually states is worth
        # comparing: a default is not a request (E-01).
        stated = stated_paths(_read_mapping(config_file))
    except Exception as exc:
        _fail_with(exc, 2)
        return

    changes = diff(live, desired, stated)
    if not changes:
        click.echo(f"{vm_name} matches {config_file}")
        return

    click.echo(f"{vm_name} vs {config_file}: {summarise(changes)}")
    for change in changes:
        click.echo(f"  {change.render()}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


@cli.command("capabilities")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Print a table to read, or JSON to process.",
)
@click.pass_context
def cmd_capabilities(ctx, fmt):
    """Print what this hypervisor can actually do.

    The same declaration the validator and the translator read, so what is printed
    here is what vmctl will accept -- including the attach matrix, which answers
    "can this hypervisor put a CD-ROM on NVMe?" without reading any source.

    Every figure has a provenance line at the end: which product and version it was
    measured against.

    \b
    Examples:
      vmctl capabilities                  # the provider vmctl would use
      vmctl -p vmware capabilities        # a specific one
      vmctl capabilities --format json    # for a script
    """
    try:
        # The probed declaration, not the static one: what is printed here has to be
        # what vmctl will accept, and since E-05 those can differ.
        caps = _engine(ctx).capabilities
    except Exception as exc:  # pragma: no cover - reported, not raised
        _fail(exc)
        return

    if fmt == "json":
        click.echo(json.dumps(_capabilities_as_dict(caps), indent=2, sort_keys=True))
        return

    click.echo(f"provider: {caps.provider}")
    click.echo(
        f"limits:   {caps.max_cpus} CPUs, {caps.max_memory_mb} MB RAM, "
        f"{caps.max_disks} devices, {caps.max_network_adapters} adapters"
    )

    click.echo("\nformats (create / attach):")
    for fmt_name, spec in sorted(caps.formats.items(), key=lambda pair: pair[0].value):
        create = "yes" if spec.support.creatable else "no"
        attach = "yes" if spec.support.usable else "no"
        allocations = "/".join(spec.allocations) or "-"
        native = "  <- native" if fmt_name is caps.native_format else ""
        click.echo(
            f"  {fmt_name.value:<10} create {create:<4} attach {attach:<4} "
            f"{allocations:<11} {spec.support.describe()}{native}"
        )

    click.echo("\nbuses:")
    kinds = list(DeviceKind)
    header = "  " + f"{'bus':<14}" + "".join(f"{kind.value:<8}" for kind in kinds)
    click.echo(header + "ports")
    for bus, spec in sorted(caps.buses.items(), key=lambda pair: pair[0].value):
        marks = "".join(f"{('yes' if caps.can_attach(kind, bus) else '-'):<8}" for kind in kinds)
        fixed = spec.fixed_port_count
        ports = f"{fixed} exactly" if fixed else f"{spec.min_ports}-{spec.max_ports}"
        if spec.units_per_port > 1:
            ports += f" x {spec.units_per_port}"
        native = [k.value for k, b in caps.native_buses.items() if b is bus]
        click.echo(
            f"  {bus.value:<14}{marks}{ports}"
            + (f"   <- native for {', '.join(native)}" if native else "")
        )

    click.echo(
        # Support.describe() is phrased for a *format*'s error message, so the
        # firmware line uses the plain support word instead of that sentence.
        "\nfirmware: "
        + ", ".join(
            f"{firmware.value} ({support.value})"
            for firmware, support in sorted(caps.firmware.items(), key=lambda p: p[0].value)
        )
    )
    click.echo("network:  " + (", ".join(caps.supported_network_types) or "-"))
    for mode in sorted(caps.host_interfaces):
        names = caps.interfaces_for(mode)
        if names:
            click.echo(f"  {mode:<8}on this host: {', '.join(names)}")
    click.echo(
        "nics:     "
        + (
            ", ".join(
                f"{model.value} ({native})"
                for model, native in sorted(caps.nic_models.items(), key=lambda pair: pair[0].value)
            )
            or "-"
        )
    )
    click.echo(
        "machine:  "
        + (
            ", ".join(caps.machine_types[:4]) + ("..." if len(caps.machine_types) > 4 else "")
            or "not a setting on this provider"
        )
    )
    click.echo(
        f"cpu:      topology {_yes(caps.cpu_topology)}, "
        f"model choice {_yes(caps.cpu_model_choice)}"
    )
    click.echo(f"names:    at most {caps.name_max_length} characters, {caps.name_pattern}")
    if caps.evidence:
        click.echo(f"\nevidence: {caps.evidence}")


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _capabilities_as_dict(caps) -> dict:
    """Return a capability declaration as plain data.

    The JSON form is the one a script or another tool reads, so it states the same
    facts as the table rather than a summary of them.
    """
    return {
        "provider": caps.provider,
        "native_format": caps.native_format.value,
        "formats": {
            fmt.value: {
                "support": spec.support.value,
                "creatable": spec.support.creatable,
                "attachable": spec.support.usable,
                "allocations": list(spec.allocations),
                "extensions": list(spec.extensions),
                "native_name": spec.native_name,
            }
            for fmt, spec in caps.formats.items()
        },
        "buses": {
            bus.value: {
                "min_ports": spec.min_ports,
                "max_ports": spec.max_ports,
                "units_per_port": spec.units_per_port,
                "bootable": spec.bootable,
                "hotplug": spec.hotplug,
                "carries": [kind.value for kind in DeviceKind if caps.can_attach(kind, bus)],
            }
            for bus, spec in caps.buses.items()
        },
        "native_buses": {kind.value: bus.value for kind, bus in caps.native_buses.items()},
        "firmware": {f.value: s.value for f, s in caps.firmware.items()},
        "nic_models": {model.value: native for model, native in caps.nic_models.items()},
        "arches": [arch.value for arch in caps.arches],
        "machine_types": list(caps.machine_types),
        "cpu": {"topology": caps.cpu_topology, "model_choice": caps.cpu_model_choice},
        "limits": {
            "max_cpus": caps.max_cpus,
            "max_memory_mb": caps.max_memory_mb,
            "max_disks": caps.max_disks,
            "max_network_adapters": caps.max_network_adapters,
            "max_vram_mb": caps.max_vram_mb,
            "name_max_length": caps.name_max_length,
        },
        "network_types": list(caps.supported_network_types),
        "host_interfaces": {
            mode: list(names) for mode, names in sorted(caps.host_interfaces.items())
        },
        "evidence": caps.evidence,
    }


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
