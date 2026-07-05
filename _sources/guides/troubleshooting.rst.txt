Troubleshooting
===============

"No space left on device" / the run did nothing
------------------------------------------------

A run that produces no artifacts is a **failure** (exit ``1`` + failure
notification). The usual cause is staging: a large archive is copied into
``tmp_dir`` before upload, and that filesystem is too small.

Fix: point ``tmp_dir`` at a disk with room for your largest archive (often the
same mounted share the data already lives on). See :doc:`promote`.

"another backup is running on this host — waiting…"
---------------------------------------------------

Expected. Only one ``backuppy run`` executes per host; others wait for the lock
(up to ``--lock-timeout``, default 6 h). Check what holds it::

    cat /run/lock/backuppy.lock
    ps aux | grep 'backuppy run'

The command line is too long (Windows/WinRM)
--------------------------------------------

Not an engine error — this comes from the portal shipping an over-long
PowerShell script through ``cmd``'s ~8191-char limit. Keep WinRM scripts short.

FilesSource skipped a locked file
---------------------------------

Files held open by Windows (e.g. a 1C ``.1CD`` in monopoly mode) are skipped
with a ``WARNING`` and the rest of the run continues. If *every* source is
skipped, the run fails (nothing produced). Exclude known-locked files with
``excludes`` if you don't need them.

A backup "succeeded" but I got no e-mail
----------------------------------------

Notifications default to ``when: on_failure`` — a clean or *warning* run is
silent by design. Set ``when: on_issue`` or ``always`` to be told about warnings
too. Use ``backuppy notify test`` to confirm delivery works at all.
