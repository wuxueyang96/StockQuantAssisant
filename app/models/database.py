import os
import duckdb
import pandas as pd
from app.config import Config


class DatabaseManager:
    def __init__(self):
        self._connections = {}

    def get_connection(self, market: str) -> duckdb.DuckDBPyConnection:
        if market not in self._connections:
            db_path = Config.DB_PATHS.get(market)
            if not db_path:
                raise ValueError(f"未知市场: {market}")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self._connections[market] = duckdb.connect(db_path)
        return self._connections[market]

    def table_exists(self, market: str, table_name: str) -> bool:
        conn = self.get_connection(market)
        result = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name]
        ).fetchone()
        return result[0] > 0

    def create_stock_table(self, market: str, table_name: str):
        conn = self.get_connection(market)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                timestamp TIMESTAMP PRIMARY KEY,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                dividends DOUBLE,
                stock_splits DOUBLE
            )
        """)

    def get_latest_timestamp(self, market: str, table_name: str):
        if not self.table_exists(market, table_name):
            return None
        conn = self.get_connection(market)
        result = conn.execute(
            f'SELECT MAX(timestamp) FROM "{table_name}"'
        ).fetchone()
        return result[0] if result and result[0] else None

    def insert_data(self, market: str, table_name: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        conn = self.get_connection(market)
        df = df.copy()
        df.index.name = 'timestamp'
        df = df.reset_index()

        latest_ts = self.get_latest_timestamp(market, table_name)
        if latest_ts:
            df = df[df['timestamp'] > pd.Timestamp(latest_ts)]

        if df.empty:
            return 0

        conn.execute(f'INSERT INTO "{table_name}" SELECT * FROM df')
        return len(df)

    def get_data(self, market: str, table_name: str, limit: int = 200) -> pd.DataFrame:
        if not self.table_exists(market, table_name):
            return pd.DataFrame()
        conn = self.get_connection(market)
        return conn.execute(
            f'SELECT * FROM "{table_name}" ORDER BY timestamp DESC LIMIT {limit}'
        ).fetchdf()

    def close_all(self):
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()


db_manager = DatabaseManager()
