#!/bin/bash
# release.sh - Build, verify and tag a vmctl release.
#
# Usage:
#   ./scripts/release.sh                 Build and verify the current version
#   ./scripts/release.sh 1.2.0           Set the version, then build and verify
#   ./scripts/release.sh 1.2.0 --dry-run Show what would happen, change nothing
#
# The version lives in exactly one place, vmctl/__init__.py; pyproject.toml reads
# it dynamically (H-03), so only one file is ever edited.
#
# Nothing is uploaded by this script. It prints the upload commands at the end.

set -euo pipefail
cd "$(dirname "$0")/.."

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${YELLOW}$*${NC}"; }
ok()    { echo -e "${GREEN}$*${NC}"; }
die()   { echo -e "${RED}Error: $*${NC}" >&2; exit 1; }

VERSION="${1:-}"
DRY_RUN=0
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY_RUN=1
done
[ "${VERSION:-}" = "--dry-run" ] && VERSION=""

echo -e "${GREEN}=== vmctl release ===${NC}"
[ "$DRY_RUN" = 1 ] && info "Dry run: no files will be changed and nothing will be tagged."

PYTHON="${PYTHON:-python3}"
[ -d venv ] && PYTHON="venv/bin/python"
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found"

# ---------------------------------------------------------------------------
# Refuse to release a dirty or untested tree
# ---------------------------------------------------------------------------
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "The working tree has uncommitted changes. Commit or stash them first."
fi

info "Installing build and test dependencies..."
$PYTHON -m pip install --upgrade -q pip build twine
$PYTHON -m pip install -q -e ".[dev,test]"

# A release that has not run the tests is not a release. This is the step the
# old script was missing entirely (H-05).
info "Running the test suite..."
$PYTHON -m pytest -q || die "Tests failed; not releasing."
ok "Tests passed"

info "Checking formatting, style and types..."
$PYTHON -m black --check -q vmctl/ tests/ || die "black would reformat files."
$PYTHON -m flake8 vmctl/ tests/ || die "flake8 reported problems."
$PYTHON -m mypy vmctl/ || die "mypy reported problems."
ok "Lint and types clean"

info "Checking the documented examples still validate..."
$PYTHON -m vmctl.cli.main validate examples/ubuntu-server.yaml >/dev/null \
    || die "examples/ubuntu-server.yaml does not validate."
$PYTHON -m vmctl.cli.main batch create examples/lab-cluster.yaml >/dev/null \
    || die "examples/lab-cluster.yaml does not resolve."
ok "Examples fine"

# ---------------------------------------------------------------------------
# Version and changelog
# ---------------------------------------------------------------------------
CURRENT=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' vmctl/__init__.py)
if [ -n "$VERSION" ]; then
    info "Updating version: $CURRENT -> $VERSION"
    if [ "$DRY_RUN" = 0 ]; then
        sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" vmctl/__init__.py
    fi
else
    VERSION="$CURRENT"
    info "Releasing the current version: $VERSION"
fi

# A version with no changelog entry is a version nobody can read about.
if ! grep -qE "^## \[?$VERSION\]?" CHANGELOG.md; then
    if grep -q "^## \[Unreleased\]" CHANGELOG.md; then
        die "CHANGELOG.md has no '## [$VERSION]' section. Rename the
       '## [Unreleased]' section to '## [$VERSION] - $(date +%Y-%m-%d)' first."
    fi
    die "CHANGELOG.md has no entry for $VERSION."
fi
ok "Changelog entry present for $VERSION"

if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
    die "Tag v$VERSION already exists."
fi

# ---------------------------------------------------------------------------
# Build and verify the artifacts
# ---------------------------------------------------------------------------
info "Cleaning previous builds..."
rm -rf dist/ build/ ./*.egg-info vmctl.egg-info

info "Building..."
$PYTHON -m build
$PYTHON -m twine check dist/*

info "Testing the built wheel in a clean environment..."
TEST_ENV=$(mktemp -d)
trap 'rm -rf "$TEST_ENV"' EXIT
$PYTHON -m venv "$TEST_ENV"
"$TEST_ENV/bin/pip" install -q dist/vmctl-*.whl
INSTALLED=$("$TEST_ENV/bin/vmctl" --version | awk '{print $NF}')
[ "$INSTALLED" = "$VERSION" ] || die "Installed version is $INSTALLED, expected $VERSION"
"$TEST_ENV/bin/vmctl" validate examples/ubuntu-server.yaml >/dev/null \
    || die "The installed package cannot validate the example config."
ok "Wheel installs and runs (version $INSTALLED)"

# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
    info "Dry run finished. Would have committed the version bump and tagged v$VERSION."
else
    if ! git diff --quiet vmctl/__init__.py; then
        git add vmctl/__init__.py
        git commit -q -m "Release v$VERSION"
        ok "Committed the version bump"
    fi
    git tag -a "v$VERSION" -m "vmctl v$VERSION"
    ok "Tagged v$VERSION"
fi

echo
ok "=== Build complete ==="
ls -lh dist/
cat <<NEXT

Next steps:

  Push the tag:
    git push origin v$VERSION

  Upload to Test PyPI first:
    twine upload --repository testpypi dist/*

  Then to PyPI:
    twine upload dist/*
NEXT
