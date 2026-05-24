# Tests

Basic integration tests. Most use mocks (moto for AWS, fake pyodbc for MSSQL)
so they run without real backends.

## Run

```bash
pip install moto pyyaml boto3
for t in tests/test_*.py; do python3 "$t"; done
```

All five should print `OK` and exit 0.

## What each test covers

| File                       | Covers                                             |
|----------------------------|----------------------------------------------------|
| `test_sqlite.py`           | SQLite online-backup of a live database            |
| `test_pro_features.py`     | Hooks, splitter, S3 upload, checksum verify        |
| `test_mssql.py`            | MSSQL flow with fake ODBC driver, SMB pickup, S3   |
| `test_multi_model.py`      | extends, deep-merge, multi-run failure isolation   |
| `test_new.py`              | `backuppy new` model scaffolding from templates    |
