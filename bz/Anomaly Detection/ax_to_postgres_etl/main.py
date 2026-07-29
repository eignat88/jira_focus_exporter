"""
ETL: SQL Server AX2012 -> PostgreSQL
Run: python -m ax_to_postgres_etl.main (from project root)
"""

import sys
import argparse
from typing import List

from ax_to_postgres_etl.configs.settings import get_settings, Settings
from ax_to_postgres_etl.utils.logger import setup_etl_logging, log_message, log_error
from ax_to_postgres_etl.application import ETLApplication
from ax_to_postgres_etl.domain import LoadStatus


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="ETL: SQL Server AX2012 -> PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m ax_to_postgres_etl.main --table INVENTTABLE --mode resume
  python -m ax_to_postgres_etl.main --table INVENTTABLE --mode full
  python -m ax_to_postgres_etl.main --use-v2 --table WMSORDERTRANS --mode resume
  python -m ax_to_postgres_etl.main  # Load all tables from config
        """
    )
    parser.add_argument(
        "--table", 
        type=str, 
        help="Load only one specific table"
    )
    parser.add_argument(
        "--mode", 
        choices=["full", "resume", "incremental", "reload"], 
        default=None,
        help="Override load mode (default: from config)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing"
    )
    parser.add_argument(
        "--use-v2",
        action="store_true",
        help="Use new resume mechanism with etl.load_chunk table"
    )
    parser.add_argument(
        "--use-v2t",
        action="store_true",
        help="Use V2T enhanced loader (P0-P15 improvements)"
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point. Returns exit code."""
    args = parse_args()
    
    try:
        settings = get_settings()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 3  # Exit code 3: configuration error
    
    # Setup logging
    log_file = setup_etl_logging(
        log_dir=settings.logging.log_dir,
        level=settings.logging.level,
    )
    
    log_message("=" * 60, log_file)
    log_message("ETL: SQL Server AX2012 -> PostgreSQL", log_file)
    log_message("=" * 60, log_file)
    log_message(f"Source: {settings.source.server}/{settings.source.database}", log_file)
    log_message(f"Target: {settings.db.host}:{settings.db.port}/{settings.db.database}.{settings.db.schema}", log_file)
    log_message(f"Password source: {settings.db.password_source}", log_file)
    log_message(f"Batch size: {settings.etl.batch_size}", log_file)
    log_message(f"Environment: {settings.environment}", log_file)
    log_message("", log_file)
    
    # Create and run application
    app = ETLApplication(settings, log_file, use_v2=args.use_v2, use_v2t=args.use_v2t)

    try:
        result = app.run(
            table_filter=args.table,
            mode_override=args.mode,
            dry_run=args.dry_run,
        )
        
        if result.status == LoadStatus.SUCCESS:
            log_message("ETL completed successfully!", log_file)
            return 0
        elif result.status == LoadStatus.ALREADY_COMPLETE:
            log_message("ETL already complete - no work needed", log_file)
            return 0
        elif result.status == LoadStatus.PARTIAL:
            log_message(f"ETL completed with errors: {', '.join(result.failed_tables)}", log_file)
            return 2  # Exit code 2: partial success
        else:
            log_message(f"ETL failed: {result.error_message}", log_file)
            return 1  # Exit code 1: critical error
            
    except Exception as e:
        log_error(f"CRITICAL ERROR: {e}", log_file)
        return 1


if __name__ == "__main__":
    sys.exit(main())
