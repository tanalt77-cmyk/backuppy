# Tests

Basic integration tests. Most use mocks (moto for AWS, fake pyodbc for MSSQL)
so they run without real backends.

## Run

```bash
pip install moto pyyaml boto3
python tests/test_sqlite.py
python tests/test_pro_features.py
python tests/test_mssql.py
python tests/test_multi_model.py
```

All four should print `OK` and exit 0.

## What each test covers

| File                       | Covers                                             |
|----------------------------|----------------------------------------------------|
| `test_sqlite.py`           | SQLite online-backup of a live database            |
| `test_pro_features.py`     | Hooks, splitter, S3 upload, checksum verify        |
| `test_mssql.py`            | MSSQL flow with fake ODBC driver, SMB pickup, S3   |
| `test_multi_model.py`      | extends, deep-merge, multi-run failure isolation   |
