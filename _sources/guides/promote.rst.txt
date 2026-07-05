Weekly / monthly copies (promote)
=================================

Instead of re-running a full backup for weekly and monthly retention, you can
**promote** the newest archive the *daily* model already produced: re-upload it
as-is (no re-dump, no re-compression) to a different destination and/or with a
longer ``keep_last``.

A promote model is an ordinary ``files`` source pointed at the daily model's
local copy, with ``compression: none`` so the finished archive passes through
untouched:

.. code-block:: yaml

    name: appdb-weekly

    sources:
      - type: files
        paths:
          - /var/backups/backuppy/appdb/**/*.tar.zst   # newest is promoted

    compression: { method: none }     # already compressed — pass through

    webdav:
      enabled: true
      base_url: https://dav.example.com
      remote_path: Backups/weekly
      keep_last: 8

Requirements
------------

- The **daily model must keep a local copy** — either a local-on-agent path or a
  Windows SMB share it mounts. That copy is what the weekly/monthly model reads.
  If the daily has no local copy (remote-only), there is nothing to promote.

Staging space
-------------

A promote model uploads an archive that may be **hundreds of GB** and already
lives on the daily's mounted share. Do **not** let it stage into a small
``/tmp`` — set ``tmp_dir`` to a disk (or the same share) with room, or the run
fails with *No space left on device*. When the portal deploys a promote cycle
with *"temporary files on the model's SMB share"* enabled, it points ``tmp_dir``
at that share automatically.

.. note::

   The ideal is to stream the ready archive straight to the destination without
   any staging copy at all; until then, ensure ``tmp_dir`` has room for one copy
   of the largest archive.
