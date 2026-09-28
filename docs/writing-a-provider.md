# Writing a provider

vmctl talks to a hypervisor through a small contract, and the contract is public: a
provider can live in its own package, be installed separately, and appear in
`vmctl providers` with no change to vmctl itself (E-15 in `PLAN.md`).

There is a worked example in `examples/vmctl-null/` — a provider that plans everything
and does nothing, which exists so that this page can be checked rather than believed:

```bash
pip install ./examples/vmctl-null
vmctl providers          # 'null' is listed as usable
vmctl -p null capabilities
python -m pytest tests/conformance   # the suite now runs against five providers
```

## The entry point

One table in your `pyproject.toml`:

```toml
[project.entry-points."vmctl.providers"]
null = "vmctl_null.backend:NullBackend"
```

The name on the left is what `-p` takes. vmctl loads the class lazily — the import
happens when your provider is actually used, so a broken dependency in your package
cannot stop `vmctl list` working for someone else's hypervisor.

## What a provider must do

Subclass `vmctl.providers.base.BaseProvider` and implement:

| Method | What it means |
|---|---|
| `name` | The provider's name, matching the entry point. |
| `capabilities` | What the hypervisor can do — see below. |
| `storage_location()` | Where new disk images go, as a `StorageLocation`. |
| `list_vms()`, `vm_exists()`, `get_vm_status()` | Reading the hypervisor's registry. |
| `read_vm(name)` | The hypervisor's own configuration, as a `VMConfig`. |
| `create_vm(vm, execute, policy)` | A `Plan`, run only when `execute` is true. |
| `delete_vm()`, `start_vm()`, `stop_vm()` | Lifecycle. |

Optional, and refused politely when absent: `edit_vm()` (changing a VM in place),
the snapshot four (`snapshots`, `take_snapshot`, `restore_snapshot`,
`delete_snapshot`), `converter()` (converting disk images), `probe()` (asking the host
what it has), `diagnostics()` (what `vmctl doctor` reports), `version()`.

## Emit a plan; do not execute

Everything that changes a VM returns a `Plan` — an ordered list of steps, each a
command to run or a file to write. That is what gives every command its dry-run, its
`-v` echo, its `--out`, and its undo, without a provider knowing those exist.

`BaseProvider.run_plan()` executes a plan, and the loop is shared: a provider
overrides `run_argv(step)` when its tool needs something unusual (VMware's `vmrun`
reports failure in its *output*, not its exit status) or `resolve_argv(argv)` when the
command as *emitted* differs from the command as *run* (libvirt's connection URI is
added here, so a plan a person reads is the command they would type).

## Declare what you support, and say how you know

`Capabilities` is read by the validator and the translator, so it decides what vmctl
accepts and what it substitutes. Two rules matter more than the rest:

- **Declare every `(device kind, bus)` pair, including the noes.** An undeclared pair is
  an unanswered question, and the conformance suite refuses one.
- **Fill in `evidence` with where the numbers came from.** Every table in this project
  has been wrong at least once because it was written from documentation or memory
  (`F-31`, `F-37`, `F-44` in `PLAN.md`). A measured limit and a remembered one look
  identical in a table, so the declaration says which it is — and `vmctl capabilities`
  prints that line last, where a reader will see it.

What a hypervisor cannot express is *reported*, not dropped: use the `Translator` to
record a substitution or a drop, and the user sees it as a warning on the plan.

## Run the conformance suite against it

```bash
pip install -e .            # vmctl itself
pip install ./my-provider
python -m pytest tests/conformance
```

The suite parametrises over every registered provider, including yours, and needs no
hypervisor installed — it checks the declaration, the parser and the emitter. It is
also the fastest way to learn the contract: writing the example on this page produced
three failures, and each was a real rule.

```
FAILED test_every_bus_says_whether_it_carries_each_device_kind[null]
        undeclared combinations: [('floppy', 'sata')]
FAILED test_an_unusable_name_is_refused_by_the_emitter_not_only_the_validator[null]
        DID NOT RAISE ValidationError
```

The first is the undeclared-pair rule above. The second is that a VM name reaches the
filesystem *through a provider*, so the emitter must call `check_name()` rather than
trusting that something upstream did — a name like `../escaped` otherwise decides where
files are written.

(The third failure was vmctl's own: a rule that read the base class's source text and
found the words it was looking for in a docstring. It is `F-47`, and it was found by
running the suite against a provider that does not override `edit_vm` — which is what
a public contract is for.)

## The four-file shape, when a provider grows

The built-in providers each split into four files, and a third-party one will want the
same division once it is past the example stage:

| File | Holds |
|---|---|
| `tables.py` | The data: this hypervisor's names for buses, formats, NIC models, guest OS ids. |
| `capabilities.py` | The declaration, with its evidence. |
| `parser.py` | Native → `VMConfig`. A pure function of text where possible, with any lookup injected, so it is testable against a captured fixture rather than a running hypervisor. |
| `emitter.py` | `VMConfig` → `Plan`. |

`backend.py` is then transport and lifecycle only: running commands, and answering what
exists. The point of the split is that the two halves that need a hypervisor to
*verify* are the two that need no hypervisor to *test*.
