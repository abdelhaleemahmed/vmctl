"""A third-party vmctl provider, as small as the contract allows.

Not a useful hypervisor: it plans everything and does nothing. It exists to answer
"what does a provider outside vmctl's own package have to do?" with something that can
be installed, listed by ``vmctl providers`` and run through the conformance suite --
rather than with prose that may or may not still be true (E-15).

    pip install ./examples/vmctl-null
    vmctl providers                      # 'null' appears, with no change to vmctl
    vmctl -p null capabilities
    vmctl -p null import some-vm.yaml    # a dry-run plan from a provider vmctl never heard of
    python -m pytest tests/conformance   # the suite now covers five providers

See ``docs/writing-a-provider.md`` for what each part of this is for.
"""
