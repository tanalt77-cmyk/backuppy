"""Triggers — things that PRODUCE files. Optional in a model.

Each trigger writes its output to a directory (specified in its config as
'output_dir' for local, 'output_dir_windows' for MSSQL). After all triggers
finish, the model's sources are pickup the resulting files.
"""
from .mssql import MSSQLTrigger
from .postgres import PostgresTrigger
from .mysql import MySQLTrigger
from .mongodb import MongoTrigger
from .redis import RedisTrigger
from .sqlite import SQLiteTrigger
from .hook import HookTrigger

__all__ = [
    "MSSQLTrigger", "PostgresTrigger", "MySQLTrigger",
    "MongoTrigger", "RedisTrigger", "SQLiteTrigger",
    "HookTrigger", "build_trigger",
]


def build_trigger(raw: dict, log):
    ttype = raw["type"]
    cls = {
        "mssql": MSSQLTrigger,
        "postgres": PostgresTrigger,
        "mysql": MySQLTrigger,
        "mongodb": MongoTrigger,
        "redis": RedisTrigger,
        "sqlite": SQLiteTrigger,
        "hook": HookTrigger,
    }.get(ttype)
    if cls is None:
        raise ValueError(f"Unknown trigger type: {ttype!r}")
    return cls(raw, log)
