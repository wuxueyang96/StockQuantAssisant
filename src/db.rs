use crate::binance::Kline;
use anyhow::{Context, Result};
use sqlx::{sqlite::SqlitePoolOptions, SqlitePool};

pub struct Database {
    pool: SqlitePool,
}

impl Database {
    pub async fn new(db_path: Option<&str>) -> Result<Self> {
        let db_path = db_path.unwrap_or("trading_data.db");
        let database_url = format!("sqlite:{}?mode=rwc", db_path);
        
        let pool = SqlitePoolOptions::new()
            .max_connections(5)
            .connect(&database_url)
            .await
            .context("Failed to create database connection")?;

        let db = Self { pool };
        db.init_schema().await?;
        
        Ok(db)
    }

    async fn init_schema(&self) -> Result<()> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS klines (
                open_time INTEGER PRIMARY KEY,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                close_time INTEGER NOT NULL,
                symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .context("Failed to create klines table")?;

        // Create index on open_time for faster queries
        sqlx::query(
            r#"
            CREATE INDEX IF NOT EXISTS idx_open_time ON klines(open_time DESC)
            "#,
        )
        .execute(&self.pool)
        .await
        .context("Failed to create index")?;

        Ok(())
    }

    pub async fn insert_kline(&self, kline: &Kline, symbol: &str) -> Result<()> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO klines (open_time, open, high, low, close, volume, close_time, symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(kline.open_time)
        .bind(kline.open)
        .bind(kline.high)
        .bind(kline.low)
        .bind(kline.close)
        .bind(kline.volume)
        .bind(kline.close_time)
        .bind(symbol)
        .execute(&self.pool)
        .await
        .context("Failed to insert kline")?;

        Ok(())
    }

    pub async fn get_recent_klines(&self, symbol: &str, limit: i64) -> Result<Vec<Kline>> {
        let rows = sqlx::query_as::<_, KlineRow>(
            r#"
            SELECT open_time, open, high, low, close, volume, close_time
            FROM klines
            WHERE symbol = ?
            ORDER BY open_time DESC
            LIMIT ?
            "#,
        )
        .bind(symbol)
        .bind(limit)
        .fetch_all(&self.pool)
        .await
        .context("Failed to fetch recent klines")?;

        let mut klines: Vec<Kline> = rows.into_iter().map(|r| r.into()).collect();
        klines.reverse(); // Reverse to get chronological order (oldest first)
        
        Ok(klines)
    }

    pub async fn get_klines_count(&self, symbol: &str) -> Result<i64> {
        let count: (i64,) = sqlx::query_as(
            r#"
            SELECT COUNT(*) FROM klines WHERE symbol = ?
            "#,
        )
        .bind(symbol)
        .fetch_one(&self.pool)
        .await
        .context("Failed to count klines")?;

        Ok(count.0)
    }

    pub async fn cleanup_old_data(&self, symbol: &str, keep_minutes: i64) -> Result<()> {
        // Get the cutoff time (keep_minutes ago from now)
        let cutoff_time = chrono::Utc::now().timestamp_millis() - (keep_minutes * 60 * 1000);
        
        sqlx::query(
            r#"
            DELETE FROM klines 
            WHERE symbol = ? AND open_time < ?
            "#,
        )
        .bind(symbol)
        .bind(cutoff_time)
        .execute(&self.pool)
        .await
        .context("Failed to cleanup old data")?;

        Ok(())
    }

    pub async fn has_kline(&self, symbol: &str, open_time: i64) -> Result<bool> {
        let count: (i64,) = sqlx::query_as(
            r#"
            SELECT COUNT(*) FROM klines WHERE symbol = ? AND open_time = ?
            "#,
        )
        .bind(symbol)
        .bind(open_time)
        .fetch_one(&self.pool)
        .await
        .context("Failed to check kline existence")?;

        Ok(count.0 > 0)
    }
}

// Helper struct for database row mapping
#[derive(sqlx::FromRow)]
struct KlineRow {
    open_time: i64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    close_time: i64,
}

impl From<KlineRow> for Kline {
    fn from(row: KlineRow) -> Self {
        Kline {
            open_time: row.open_time,
            open: row.open,
            high: row.high,
            low: row.low,
            close: row.close,
            volume: row.volume,
            close_time: row.close_time,
        }
    }
}

