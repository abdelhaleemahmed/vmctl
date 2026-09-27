# Example configurations

Every file here is checked by CI (`.github/workflows/ci.yml`):

* `ubuntu-server.yaml` — a single VM, as shown in the project README. Validated
  with `vmctl validate`, and its create plan is generated with `vmctl import`
  (dry-run) so a config that cannot produce commands fails the build.
* `lab-cluster.yaml` — a batch definition, checked with `vmctl batch create`
  in dry-run mode.

They are here so the documented examples cannot drift from what the code
accepts. Nothing in CI touches a hypervisor: every check is dry-run.

```bash
vmctl validate examples/ubuntu-server.yaml
vmctl import examples/ubuntu-server.yaml --new-name test-vm   # dry-run
vmctl batch create examples/lab-cluster.yaml                  # dry-run
```
