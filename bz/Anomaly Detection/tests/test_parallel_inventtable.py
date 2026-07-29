"""Test parallel loader on INVENTTABLE only."""
import sys, os

# Run from project root, add ETL dir to path
etl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ax_to_postgres_etl')
sys.path.insert(0, etl_dir)

from configs.settings import get_settings
from connectors.sqlserver import SQLServerConnector
from connectors.postgres import PostgresConnector
from loader.parallel_loader import ParallelLoader

settings = get_settings()

ss = SQLServerConnector(server=settings.source.server, database=settings.source.database, driver=settings.source.driver)
ss.connect()

pg = PostgresConnector(host=settings.db.host, port=settings.db.port, database=settings.db.database, user=settings.db.user, password=settings.db.password, schema=settings.db.schema)
pg.connect()
pg.create_schema()

loader = ParallelLoader(
    ss_conn_str=ss.conn_str,
    pg_connector=pg,
    workers=4,
    fetch_size=5000,
    commit_size=50000,
    log_func=print,
)

loader.load_table('INVENTTABLE')

# Verify
cursor = pg.conn.cursor()
cursor.execute('SELECT COUNT(*) FROM raw_ax.inventtable')
count = cursor.fetchone()[0]
print(f'\n=== RESULT: {count:,} rows loaded into raw_ax.inventtable ===')

ss.disconnect()
pg.disconnect()
