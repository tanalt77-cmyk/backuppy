Changelog
=========

3.12.0
------

- **Streaming compression:** a ``files`` source with ``archive_name`` now pipes
  its tar straight into the compressor (``compression.stream``, default
  ``true``). Only the compressed archive is written to ``tmp_dir`` — a 77 GB
  source no longer needs 77 GB of temp space plus room for the compressed copy —
  and archiving overlaps with compressing. ``stream: false`` restores the
  two-step behaviour.
- **Robust archiving:** a file that shrinks or hits a read error while being
  packed is padded with zeros to its header size (as GNU tar does) instead of
  leaving a corrupt member; errors writing the archive (disk full) now fail the
  run instead of being logged as skipped files.
- **Disk-full reporting:** when the compressor dies because the temp disk is
  full, the failure says "No space left on device" (it used to be
  ``zstd failed (rc=70): no stderr``).
- ``zstd`` levels 20–22 are now passed with ``--ultra`` (they failed before).

3.11.0
------

- **Failure semantics:** a run that produces **no artifacts** is now a
  *failure* (exit ``1`` + failure notification) instead of a silent
  success-with-warning. A disk-full (``ENOSPC``) during staging fails the run
  even when some artifacts uploaded, and the failure message names the cause and
  suggests pointing ``tmp_dir`` at a larger disk.
- **Archiving/compression progress:** progress is logged every 10% while
  building and compressing large archives.
- Documentation: full Sphinx/Read-the-Docs manual added under ``docs/``.

3.10.9
------

- Baseline release documented here.
