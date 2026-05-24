"""Entry point: python -m backuppy run -c config.yml"""
import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
