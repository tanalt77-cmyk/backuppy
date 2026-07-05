# Configuration file for the Sphinx documentation builder.
#
# Build locally with:
#     pip install sphinx sphinx_rtd_theme
#     sphinx-build -b html docs docs/_build/html
# then open docs/_build/html/index.html
#
# On Read the Docs, point the project at docs/conf.py (see .readthedocs.yaml).

import datetime as _dt

# -- Project information ------------------------------------------------------

project = "backuppy"
author = "backuppy contributors"
copyright = f"{_dt.date.today().year}, {author}"

# Keep the docs version in sync with the package.
try:
    from backuppy import __version__ as release  # noqa: E402
except Exception:  # pragma: no cover - docs may build without the package
    release = "3.11.0"
version = ".".join(release.split(".")[:2])

# -- General configuration ----------------------------------------------------

extensions = [
    "sphinx.ext.autosectionlabel",
]
autosectionlabel_prefix_document = True

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
source_suffix = ".rst"
master_doc = "index"
language = "en"

# -- HTML output --------------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
}
html_static_path = ["_static"]
htmlhelp_basename = "backuppydoc"
