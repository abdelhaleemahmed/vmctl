#!/bin/bash
# release.sh - Build and prepare vmctl for release
#
# Usage: ./scripts/release.sh [VERSION]
#
# If VERSION is provided, updates version numbers before building.
# If not provided, builds with current version.

set -e

cd "$(dirname "$0")/.."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== vmctl Release Builder ===${NC}"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found. Run: python3 -m venv venv${NC}"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Install/upgrade build tools
echo -e "${YELLOW}Installing build tools...${NC}"
pip install --upgrade build twine -q

# Update version if provided
VERSION=$1
if [ -n "$VERSION" ]; then
    echo -e "${YELLOW}Updating version to $VERSION...${NC}"

    # Update vmctl/__init__.py
    sed -i "s/__version__ = .*/__version__ = \"$VERSION\"/" vmctl/__init__.py

    # Update pyproject.toml
    sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

    echo -e "${GREEN}Version updated to $VERSION${NC}"
else
    # Get current version
    VERSION=$(grep '__version__' vmctl/__init__.py | cut -d'"' -f2)
    echo -e "${YELLOW}Building current version: $VERSION${NC}"
fi

# Clean previous builds
echo -e "${YELLOW}Cleaning previous builds...${NC}"
rm -rf dist/ build/ *.egg-info vmctl.egg-info

# Build packages
echo -e "${YELLOW}Building packages...${NC}"
python -m build

# Verify packages
echo -e "${YELLOW}Verifying packages...${NC}"
twine check dist/*

# Test installation in isolated environment
echo -e "${YELLOW}Testing installation...${NC}"
TEST_ENV=$(mktemp -d)
python3 -m venv "$TEST_ENV"
source "$TEST_ENV/bin/activate"
pip install dist/vmctl-*.whl -q
INSTALLED_VERSION=$(vmctl --version 2>&1 | awk '{print $NF}')
deactivate
rm -rf "$TEST_ENV"

if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
    echo -e "${GREEN}Installation test passed (version: $INSTALLED_VERSION)${NC}"
else
    echo -e "${RED}Version mismatch! Expected $VERSION, got $INSTALLED_VERSION${NC}"
    exit 1
fi

# Reactivate original venv
source venv/bin/activate

# Show results
echo ""
echo -e "${GREEN}=== Build Complete ===${NC}"
echo ""
echo "Packages built:"
ls -lh dist/
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "  Upload to Test PyPI (recommended first):"
echo "    twine upload --repository testpypi dist/*"
echo ""
echo "  Test install from Test PyPI:"
echo "    pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ vmctl"
echo ""
echo "  Upload to Production PyPI:"
echo "    twine upload dist/*"
echo ""
