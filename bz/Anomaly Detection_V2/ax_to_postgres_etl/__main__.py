"""Allow running as: python -m ax_to_postgres_etl"""
import os
import sys

# Add both project root and ax_to_postgres_etl to path
etl_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(etl_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)
if etl_dir not in sys.path:
    sys.path.insert(0, etl_dir)

from main import main

if __name__ == "__main__":
    main()
