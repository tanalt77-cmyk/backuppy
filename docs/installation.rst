Installation
============

Requirements
------------

- **Python 3.10+** on the agent host (Linux; Debian/Ubuntu are the reference
  platforms).
- Command-line tools for whatever you use:

  - ``gzip`` / ``bzip2`` / ``xz`` / ``zstd`` for the matching compression
    method (``zstd`` recommended for speed);
  - ``gpg`` and/or ``openssl`` if you enable encryption;
  - the relevant database client if you use a trigger — e.g. ``pg_dump`` for
    PostgreSQL, ``mysqldump`` for MySQL/MariaDB, ``mongodump`` for MongoDB,
    ``redis-cli`` for Redis. MSSQL is driven over TCP with ``pyodbc`` and the
    *ODBC Driver for SQL Server*, so no client binary is required.

Destination back-ends pull in their own Python packages on demand (for example
``boto3`` for S3, ``dropbox`` for Dropbox, ``google-cloud-storage`` for GCS,
``azure-storage-blob`` for Azure). Install only the ones you use.

Install from PyPI-style source
------------------------------

The recommended install on a fresh Debian/Ubuntu host is the one-line
installer, which sets up the package, the ``backuppy`` command and (optionally)
the MSSQL ODBC driver and backend extras::

    apt install -y curl        # if not already present
    curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash

Installer options (download the script first to pass flags)::

    bash install.sh --update           # update the package only, keep configs
    bash install.sh --branch dev       # install a specific branch or tag
    bash install.sh --no-mssql         # skip the MSSQL ODBC driver
    bash install.sh --extras s3,sftp   # only selected backend extras
    bash install.sh --all              # all backend extras (default)

Or install straight from Git with ``pip`` (what the portal's fleet update
uses)::

    pip install --upgrade --force-reinstall --no-deps \
        git+https://github.com/tanalt77-cmyk/backuppy.git@main

Either way the console entry point ``backuppy`` lands on ``PATH`` (typically
``/usr/local/bin/backuppy``). Verify::

    backuppy --version
    # backuppy 3.11.0

Updating::

    curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash -s -- --update

Managing a fleet
----------------

When agents are managed by the backuppy portal, you never install by hand: the
portal's **"Update backuppy on all agents"** action runs the ``pip install``
above over SSH on every agent and reports the before/after version per host.
Roll out a new engine release by pushing to ``main`` and triggering that action.

Directory layout
----------------

By default the engine writes to:

===========================  ================================================
Path                         Purpose
===========================  ================================================
``/etc/backuppy/*.yml``      One YAML file per model (the model *name* is the
                             file stem).
``/var/log/backuppy*.log``   Rotating log files (see :ref:`configuration:logging`).
``/var/backups/backuppy``    Default local destination (override per model).
``/run/lock/backuppy.lock``  Host-wide run lock (see :ref:`cli:run`).
===========================  ================================================
