Development
===========

Requirements
------------

- Python 3.10+
- A POSIX shell; the reference platform is Debian/Ubuntu.

Getting the source
------------------

::

    git clone https://github.com/tanalt77-cmyk/backuppy.git
    cd backuppy
    python -m venv .venv && . .venv/bin/activate
    pip install -e .

Running the tests
-----------------

::

    pip install pytest
    pytest -q

Building the documentation
--------------------------

::

    pip install sphinx sphinx_rtd_theme
    sphinx-build -b html docs docs/_build/html
    # open docs/_build/html/index.html

On Read the Docs the build is driven by ``.readthedocs.yaml`` at the repository
root, which points at ``docs/conf.py``.

Releasing
---------

1. Update the version in **both** ``pyproject.toml`` and
   ``backuppy/__init__.py`` (they must match — the CLI reports
   ``backuppy/__init__.py`` and packaging uses ``pyproject.toml``).
2. Add an entry to :doc:`changelog`.
3. Commit and push to ``main``.
4. Roll out to agents with the portal's **"Update backuppy on all agents"**
   action, which ``pip install``\s ``git+…@main`` on every host and reports the
   before/after version.
