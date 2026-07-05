Changelog
=========

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
