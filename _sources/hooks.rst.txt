Hooks
=====

Hooks run shell commands at defined points around a backup. Every list may hold
zero or more commands, executed in order.

.. code-block:: yaml

    hooks:
      before:     ["/usr/local/bin/app maintenance-on"]
      after:      ["/usr/local/bin/app maintenance-off"]
      on_success: ["/usr/local/bin/notify-ok myapp"]
      on_failure: ["/usr/local/bin/page-oncall myapp"]

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Hook
     - When it runs
   * - ``before``
     - Before any trigger or source, at the very start of the run.
   * - ``after``
     - At the end of the run — **always**, on success *and* failure (use it to
       undo whatever ``before`` set up).
   * - ``on_success``
     - Only when the run finished cleanly.
   * - ``on_failure``
     - Only when the run failed.

The ordering guarantee — ``after`` always runs — makes hooks safe for
maintenance toggles: pair ``before`` (turn something off) with ``after`` (turn
it back on) and it is restored even if the backup fails.
