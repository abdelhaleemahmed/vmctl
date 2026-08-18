# Sphinx configuration for the vmctl documentation.
import os
import sys

# Make the `vmctl` package importable so autodoc can read its docstrings.
# docs/source/conf.py  ->  ../..  ==  repo root (which contains vmctl/).
sys.path.insert(0, os.path.abspath("../.."))

import vmctl  # noqa: E402

# -- Project information -----------------------------------------------------
project = "vmctl"
copyright = "2026, Ahmed Abdelhaleem Ahmed"
author = "Ahmed Abdelhaleem Ahmed"
release = vmctl.__version__
version = vmctl.__version__
html_title = f"vmctl {release}"

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
]

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

source_suffix = {".rst": "restructuredtext"}
master_doc = "index"
templates_path = ["_templates"]
exclude_patterns = []

# -- HTML output -------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = []
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "titles_only": False,
}

intersphinx_mapping = {}
highlight_language = "bash"
pygments_style = "monokai"
todo_include_todos = True
