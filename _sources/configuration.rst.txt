Configuration
=============

A model is a YAML document. Every key is optional except that a model needs at
least one **trigger** or **source** to produce anything, and at least one
enabled **destination** to send it to. Defaults shown below are the built-in
defaults; you only set what you want to change.

Top level
---------

.. code-block:: yaml

    name: myapp              # model name; defaults to the file stem
    group_by_run: false      # per-run subdirectory layout (see Retention)
    tmp_dir: ""              # working/staging dir; "" = system temp (/tmp)

    triggers: []             # database dumps (see Triggers)
    sources: []              # file collection (see Triggers/Sources)

    compression: {...}       # see Packaging
    encryption:  {...}       # see Packaging
    splitter:    {...}       # see Packaging
    throttle:    {...}       # upload rate limiting
    verify:      {...}       # post-upload integrity check
    hooks:       {...}       # command hooks (see Hooks)

    local:   {...}           # destinations (see Destinations)
    webdav:  {...}
    s3:      {...}
    sftp:    {...}
    dropbox: {...}
    gcs:     {...}
    azure:   {...}

    email:    {...}          # notifications (see Notifications)
    telegram: {...}

    log: {...}               # logging

``name``
    Human-readable model name. Used in log lines, notification subjects and
    destination sub-paths. Defaults to the configuration file's stem.

``group_by_run``
    When ``true``, every run creates a ``YYYYMMDD-HHMMSS`` subdirectory in each
    destination and stores all of that run's artifacts inside it; rotation then
    keeps that many *run folders*. When ``false`` (default), artifacts are
    written directly into the destination and rotation is per file, by filename
    prefix. See :doc:`retention`.

``tmp_dir``
    Working/staging directory for building archives before upload. Empty string
    uses Python's temp default (usually ``/tmp``). **Point this at a disk with
    room for your largest archive** — staging a large file into a small root
    filesystem fails with *"No space left on device"*. For models that mount a
    Windows SMB share, staging on that share avoids the problem entirely.

Logging
-------

.. code-block:: yaml

    log:
      file: /var/log/backuppy.log   # log file path
      level: INFO                   # DEBUG | INFO | WARNING | ERROR
      max_bytes: 5242880            # rotate the log after this many bytes (5 MiB)
      backup_count: 5               # how many rotated log files to keep

Full tracebacks for failures are always written at ``DEBUG`` level even when the
operator-facing ``level`` is ``INFO``, so the file log keeps forensic detail
without cluttering normal output.

Throttle
--------

.. code-block:: yaml

    throttle:
      enabled: false
      upload_rate_kbps: 0     # cap upload bandwidth, in KB/s (0 = unlimited)

Verify
------

.. code-block:: yaml

    verify:
      enabled: true
      method: size            # size | checksum

After each upload the artifact is verified against the destination.
``size`` compares byte counts (fast); ``checksum`` re-reads and hashes the
remote object (slower, stronger). A failed verification fails the run.

Includes / extends
------------------

A model may pull shared defaults from another file so you don't repeat SMTP or
S3 credentials in every model:

.. code-block:: yaml

    extends: /etc/backuppy/_common.yml

Values in the current file override the included file; lists and nested maps are
merged shallowly. Cyclic includes are detected and rejected.

See also
--------

- :doc:`triggers` — the ``triggers:`` and ``sources:`` lists in detail.
- :doc:`destinations` — every ``local``/``webdav``/``s3``/… block.
- :doc:`packaging` — ``compression``, ``encryption`` and ``splitter``.
- :doc:`retention` — ``keep_last`` and ``group_by_run``.
- :doc:`notifications` — ``email`` and ``telegram``.
