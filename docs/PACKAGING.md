# Packaging Guide for vmctl

This guide explains how to build and publish vmctl packages.

## Prerequisites

```bash
# Activate virtual environment
cd /home/vagrant/commandline/vmctl
source venv/bin/activate

# Install build tools
pip install build twine
```

## Building Packages

### Build Both Wheel and Source Distribution

```bash
# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build packages
python -m build
```

This creates two files in `dist/`:
- `vmctl-X.Y.Z-py3-none-any.whl` - Wheel package (recommended for installation)
- `vmctl-X.Y.Z.tar.gz` - Source distribution

### Verify Packages

```bash
# Check packages are valid for PyPI
twine check dist/*
```

## Version Management

Update version in `vmctl/__init__.py`:

```python
__version__ = "1.0.1"  # Change this
```

And in `pyproject.toml`:

```toml
[project]
version = "1.0.1"  # Keep in sync
```

## Testing Packages Locally

### Test Wheel Installation

```bash
# Create a fresh virtual environment
python3 -m venv /tmp/test-env
source /tmp/test-env/bin/activate

# Install the wheel
pip install dist/vmctl-*.whl

# Test it works
vmctl --version
vmctl --help

# Cleanup
deactivate
rm -rf /tmp/test-env
```

## Publishing to PyPI

### First Time Setup

1. Create accounts:
   - Test PyPI: https://test.pypi.org/account/register/
   - Production PyPI: https://pypi.org/account/register/

2. Create API tokens:
   - Go to Account Settings → API tokens
   - Create a token with "Entire account" scope (for first upload)
   - Save the token securely

3. Configure `~/.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YOUR-TOKEN-HERE

[testpypi]
username = __token__
password = pypi-YOUR-TEST-TOKEN-HERE
```

### Upload to Test PyPI (Recommended First)

```bash
# Upload to Test PyPI
twine upload --repository testpypi dist/*

# Test installation from Test PyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ vmctl
```

### Upload to Production PyPI

```bash
# Upload to PyPI
twine upload dist/*

# Now anyone can install with:
pip install vmctl
```

## Complete Build and Release Workflow

```bash
#!/bin/bash
# release.sh - Complete release workflow

set -e

VERSION=$1

if [ -z "$VERSION" ]; then
    echo "Usage: ./release.sh X.Y.Z"
    exit 1
fi

echo "=== Releasing vmctl $VERSION ==="

# 1. Update version
sed -i "s/__version__ = .*/__version__ = \"$VERSION\"/" vmctl/__init__.py
sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

# 2. Clean previous builds
rm -rf dist/ build/ *.egg-info

# 3. Build packages
python -m build

# 4. Verify packages
twine check dist/*

# 5. Test installation
python3 -m venv /tmp/test-release
source /tmp/test-release/bin/activate
pip install dist/vmctl-*.whl
vmctl --version
deactivate
rm -rf /tmp/test-release

echo "=== Build complete ==="
echo "Packages in dist/:"
ls -lh dist/

echo ""
echo "To upload to Test PyPI:"
echo "  twine upload --repository testpypi dist/*"
echo ""
echo "To upload to Production PyPI:"
echo "  twine upload dist/*"
```

## Package Structure

```
vmctl/
├── pyproject.toml      # Package metadata and build config
├── README.md           # Package description (shown on PyPI)
├── vmctl/              # Source package
│   ├── __init__.py     # Contains __version__
│   ├── cli/
│   ├── core/
│   ├── providers/
│   ├── serializers/
│   └── validators/
└── dist/               # Built packages (after build)
    ├── vmctl-X.Y.Z-py3-none-any.whl
    └── vmctl-X.Y.Z.tar.gz
```

## Troubleshooting

### "File already exists" Error

PyPI doesn't allow re-uploading the same version. Bump the version number:
```
1.0.0 → 1.0.1
```

### Package Not Found After Upload

Wait a few minutes for PyPI to index the package.

### Missing Dependencies

Ensure all dependencies are listed in `pyproject.toml`:
```toml
[project]
dependencies = [
    "PyYAML>=6.0",
    # Add more here
]
```

### Import Errors After Installation

Check that all `__init__.py` files exist in subdirectories:
```bash
find vmctl -type d -exec test -f {}/__init__.py \; -print
```
