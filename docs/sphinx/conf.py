# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# Make the vmctl package importable without installing
sys.path.insert(0, os.path.abspath("../.."))

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = "vmctl"
copyright = "2025, Ahmed Abdelhaleem Ahmed"
author = "Ahmed Abdelhaleem Ahmed"
# Single-sourced from vmctl/__init__.py, like the package itself (H-03): two
# documentation trees with their own copies of the version number is two more
# places to forget, and both had already fallen behind.
from vmctl import __version__ as release  # noqa: E402

version = release

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",  # Generate API docs from docstrings
    "sphinx.ext.napoleon",  # Support Google-style docstrings
    "sphinx.ext.viewcode",  # Add [source] links to API pages
    "sphinx.ext.intersphinx",  # Cross-reference Python stdlib docs
    "sphinx.ext.autosummary",  # Generate summary tables automatically
    "sphinx_click",  # Document Click CLI commands
    "myst_parser",  # Allow .md files in the doc tree
]

# ---------------------------------------------------------------------------
# Autodoc settings
# ---------------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__, __post_init__",
    "undoc-members": True,
    "show-inheritance": True,
}

# Do not skip __init__ if it has a docstring
autodoc_class_signature = "separated"

# ---------------------------------------------------------------------------
# Napoleon (Google-style docstring support)
# ---------------------------------------------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_rtype = True
# Render an `Attributes:` section as :ivar: fields rather than as separate object
# descriptions. Without this, a dataclass field is described twice -- once by the
# docstring and once by autodoc reading the annotation -- and Sphinx reports a
# duplicate that `suppress_warnings = ['autodoc']` does not cover, because it
# comes from the Python domain rather than from autodoc.
napoleon_use_ivar = True

# ---------------------------------------------------------------------------
# Intersphinx
# ---------------------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "titles_only": False,
    "logo_only": False,
    "collapse_navigation": False,
    "sticky_navigation": True,
}

# Suppress duplicate-object warnings from dataclass field autodoc
suppress_warnings = ["autodoc"]

html_static_path = ["_static"]
html_title = f"vmctl {release}"

# ---------------------------------------------------------------------------
# Source file settings
# ---------------------------------------------------------------------------

source_suffix = {
    ".rst": None,
    ".md": "myst",
}

master_doc = "index"

# Suppress nitpicky warnings for stdlib types
nitpick_ignore = [
    ("py:class", "optional"),
    ("py:class", "Path"),
]
