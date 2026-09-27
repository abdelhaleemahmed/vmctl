# vmctl test suite

Phase 0 of `../PLAN.md`: the regression baseline for the working version.

```bash
pip install -e ".[test]"
pytest -q                     # whole suite, no hypervisor needed
pytest -q -m documents_bug    # only the tests that pin known bugs
pytest -q --no-header -rA     # verbose, including xfail reasons
```

## The two rules that keep this suite useful

**1. It never needs a hypervisor.** Parser tests go through `parse_text()` with
captured fixtures and an injected `MediumProbe`; CLI tests mock
`subprocess.run`. A `no_hypervisor` autouse fixture fails any test that reaches
for a real one, so this cannot rot silently. Opt out with
`@pytest.mark.allow_subprocess` only when the test mocks the call itself.

**2. Known bugs are pinned, not skipped.** Each audit finding has a pair:

- a `documents_bug` + `xfail(strict=True)` test asserting the *correct*
  behaviour, which fails today and turns green the moment the fix lands;
- where useful, a plain test asserting the *current wrong* behaviour, so the fix
  shows up as an explicit, reviewable diff rather than a silent change.

`strict=True` matters: if someone fixes a bug without noticing, the xfail
becomes an XPASS *failure* and the suite tells them to update it. Nothing
regresses quietly in either direction.

## Layout

| Path | Contents |
|---|---|
| `conftest.py` | fixture loading, the fake `MediumProbe`, hand-built `VMConfig`s, golden helpers, the hermeticity guard |
| `fixtures/` | captured hypervisor output — **read `fixtures/README.md` first, they are currently synthetic** |
| `fixtures/configs/` | genuine v1.1.9 exports, for the backward-compatibility guard |
| `golden/` | exact commands the emitter produces today |
| `regenerate_golden.py` | rewrites `golden/` — run it, then *read the diff* |
| `test_parser.py` | native output → `VMConfig` |
| `test_emitter.py` | `VMConfig` → VBoxManage commands, incl. golden comparison |
| `test_roundtrip.py` | parse → serialize → load → emit fidelity |
| `test_serializers.py` | YAML/JSON symmetry and error reporting |
| `test_validator.py` | limits, structural rules, the warnings pathway |
| `test_cli.py` | exit codes, dry-run-by-default, output contracts |
| `test_compat.py` | v1.1.9 config files must load forever |

## Findings this suite pins

Every id refers to `../PLAN.md`.

| Finding | Pinned by |
|---|---|
| F-01 no controller created for a hand-written config | `test_emitter.py::test_minimal_config_creates_its_controller_before_attaching` |
| F-02 EFI VM parsed as BIOS | `test_parser.py::test_efi_vm_is_parsed_as_efi` |
| F-03 hyphenated keys dropped | `test_parser.py::test_decodes_hyphenated_keys`, `::test_nested_virt_is_read` |
| F-04 optical drives get a blank image created | `test_emitter.py::test_optical_drive_does_not_create_a_hard_disk` |
| F-05 ten fields never emitted | `test_emitter.py::test_every_configured_field_reaches_a_command` |
| F-06 config errors raise raw `TypeError`/`ValueError` | `test_serializers.py` (3 tests), `test_cli.py::test_malformed_config_exits_cleanly` |
| F-07 `from_dict` mutates its input | `test_roundtrip.py::test_from_dict_does_not_mutate_its_input` |
| F-08 warnings never emitted; validator mutates | `test_validator.py` (4 tests) |
| F-09 completion emits `__VMCTL_COMPLETE` | `test_cli.py::test_completion_emits_the_variable_click_reads` |
| F-10 `edit` never applies | `test_cli.py::test_edit_applies_its_change` |
| F-14 non-deterministic controller order | `test_parser.py::test_controller_order_is_deterministic` (**fixed in Phase 0**) |
| F-15 floppy controller typed as SATA | `test_parser.py::test_floppy_controller_is_not_typed_as_sata` |
| L-01 aborted delete exits 0 | `test_cli.py::test_delete_abort_should_exit_nonzero` |
| L-02 description double-quoted | `test_emitter.py::test_description_is_not_double_quoted` |
