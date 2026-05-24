"""Remote storage backends."""
from .base import BaseStorage
from .local import LocalStorage, sha256_file
from .webdav import WebDAVStorage
from .s3 import S3Storage
from .sftp import SFTPStorage
from .dropbox import DropboxStorage
from .gcs import GCSStorage
from .azure import AzureBlobStorage

__all__ = [
    "BaseStorage", "LocalStorage", "sha256_file",
    "WebDAVStorage", "S3Storage", "SFTPStorage",
    "DropboxStorage", "GCSStorage", "AzureBlobStorage",
]
