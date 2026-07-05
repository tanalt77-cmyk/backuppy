Notifications
=============

backuppy can send the outcome of every run by e-mail and/or Telegram. Each
channel decides *when* to send with its own ``when`` filter.

Outcomes
--------

A run ends in exactly one outcome:

============  ================================================================
Outcome       Meaning
============  ================================================================
``success``   Completed cleanly, no warnings.
``warning``   Completed, but at least one ``WARNING`` was logged (e.g. a single
              locked file was skipped while the rest succeeded).
``failure``   The run failed — an error was raised, **or the run produced no
              artifacts to upload**, **or** a disk-full condition occurred
              during staging. Exit code ``1``.
============  ================================================================

.. important::

   "Produced nothing to upload" is a **failure**, not a warning. If every source
   is skipped — or staging runs out of disk — the run fails loudly (exit ``1``
   and a *failure* notification), so a broken backup can never masquerade as a
   quiet success. A disk-full during staging fails the run even if some other
   artifacts uploaded, because the backup set is then incomplete.

The ``when`` filter
-------------------

Set ``when`` on each channel to control delivery:

==============  ==============================================================
``when``        Sends on
==============  ==============================================================
``on_failure``  failure only  *(default)*
``on_warning``  warning only
``on_success``  success only
``on_issue``    warning **or** failure (anything not perfectly clean)
``always``      success, warning and failure
==============  ==============================================================

.. note::

   With the default ``on_failure``, a clean run — or a *warning* run — is
   silent. Because "nothing produced" and disk-full are now classified as
   **failure** (see above), those conditions page you even on ``on_failure``.

E-mail
------

.. code-block:: yaml

    email:
      enabled: true
      when: on_failure
      smtp_host: smtp.example.com
      smtp_port: 587
      smtp_user: backups@example.com
      smtp_password: "..."
      use_tls: true
      from_addr: backups@example.com
      to_addrs:
        - ops@example.com
        - oncall@example.com

Telegram
--------

.. code-block:: yaml

    telegram:
      enabled: true
      when: on_issue
      bot_token: "123456:ABC-DEF..."
      chat_id: "-1001234567890"
      timeout: 30

Testing delivery
----------------

Send a test message through a configured channel without running a backup::

    backuppy notify test myapp --channel email
    backuppy notify test myapp --channel telegram

Failure messages include a one-line human-readable cause (not the raw
library traceback), the full traceback at the end for debugging, and any
warnings collected before the failure.
