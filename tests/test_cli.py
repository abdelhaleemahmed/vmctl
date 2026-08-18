"""The CLI parser builds and exposes the documented sub-commands."""
from vmctl.cli.main import create_parser

EXPECTED = {
    "list", "read", "start", "stop", "status", "edit",
    "create", "export", "import", "batch", "delete", "validate",
}


def test_parser_builds():
    parser = create_parser()
    assert parser.prog == "vmctl"


def test_all_subcommands_present():
    parser = create_parser()
    subactions = [
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    ]
    assert subactions, "no subparsers registered"
    names = set(subactions[0].choices)
    missing = EXPECTED - names
    assert not missing, f"missing subcommands: {missing}"


def test_version_flag(capsys):
    import pytest
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])


def test_apply_flag_replaces_execute():
    """State-changing commands use --apply (dry-run by default); --execute is gone."""
    import pytest
    parser = create_parser()
    for argv in (
        ["create", "src", "--new-name", "x", "--apply"],
        ["import", "cfg.yaml", "--apply"],
        ["batch", "create", "cluster.yaml", "--apply"],
    ):
        ns = parser.parse_args(argv)
        assert ns.apply is True
        # default is dry-run
        assert parser.parse_args(argv[:-1]).apply is False
    # the old flag must not exist anymore
    with pytest.raises(SystemExit):
        parser.parse_args(["batch", "create", "cluster.yaml", "--execute"])
