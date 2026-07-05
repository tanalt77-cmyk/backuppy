Destinations
============

A model may enable **any combination** of destinations; each finished artifact
is uploaded to every enabled one, verified, and rotated independently. The
``local`` copy also doubles as the staging area for remote uploads.

Every destination has an ``enabled`` flag and its own ``keep_last`` retention
count (see :doc:`retention`).

local
-----

.. code-block:: yaml

    local:
      enabled: true                 # on by default
      path: /var/backups/backuppy
      keep_last: 5

A directory on the agent (or on a mounted SMB/NFS share). Because remote uploads
stage through the local copy, keeping ``local`` enabled is often useful even
when your "real" copies are remote.

webdav
------

.. code-block:: yaml

    webdav:
      enabled: false
      base_url: https://dav.example.com
      remote_path: Backups
      username: user
      password: pass
      timeout: 300
      verify_tls: true
      keep_last: 10
      # Large-file (chunked) upload — for servers that reject huge single PUTs:
      chunked: true
      chunked_threshold_mb: 500     # use chunked upload for files >= this size
      chunked_chunk_size_mb: 50     # size of each chunk
      chunked_retries: 3            # retry a failed chunk this many times
      chunked_parallel: 1           # upload N chunks at once
      chunked_assemble_timeout: 0   # 0 = scale the final MOVE timeout by file size

Works with Nextcloud/ownCloud and raw WebDAV servers. Set ``chunked: false`` to
force plain PUTs (raw servers only).

s3 (and S3-compatible)
----------------------

.. code-block:: yaml

    s3:
      enabled: false
      bucket: my-bucket
      region: us-east-1
      prefix: backups
      access_key_id: "AKIA..."
      secret_access_key: "..."
      endpoint_url: null             # set for Backblaze B2, MinIO, Wasabi, …
      storage_class: STANDARD
      server_side_encryption: null   # e.g. AES256 or aws:kms
      keep_last: 30
      multipart_threshold_mb: 64
      multipart_chunksize_mb: 16

Set ``endpoint_url`` to target S3-compatible providers — for example Backblaze
B2 (``https://s3.<region>.backblazeb2.com``), MinIO or Wasabi.

sftp
----

.. code-block:: yaml

    sftp:
      enabled: false
      host: files.example.com
      port: 22
      username: backup
      password: ""                   # or use a key instead
      key_file: /home/backup/.ssh/id_ed25519
      key_passphrase: ""
      known_hosts: /home/backup/.ssh/known_hosts
      remote_path: backups
      keep_last: 10

dropbox
-------

.. code-block:: yaml

    dropbox:
      enabled: false
      refresh_token: "..."           # recommended (long-lived)
      app_key: "..."
      app_secret: "..."
      access_token: ""               # short-lived alternative
      remote_path: /Backups
      chunk_size_mb: 16
      keep_last: 30

gcs (Google Cloud Storage)
--------------------------

.. code-block:: yaml

    gcs:
      enabled: false
      bucket: my-bucket
      prefix: backups
      credentials_file: /etc/backuppy/gcs-sa.json
      project_id: my-project
      storage_class: STANDARD
      keep_last: 30

azure (Azure Blob Storage)
--------------------------

.. code-block:: yaml

    azure:
      enabled: false
      container: backups
      prefix: backups
      # authenticate with either a connection string OR account name + key:
      connection_string: ""
      account_name: ""
      account_key: ""
      tier: Hot                      # Hot | Cool | Archive
      keep_last: 30
