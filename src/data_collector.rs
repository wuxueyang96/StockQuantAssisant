use crate::binance::{BinanceClient, Kline};
use crate::db::Database;
use anyhow::{Context, Result};
use std::sync::Arc;
use tracing::{info, warn, error};

pub struct DataCollector {
    client: BinanceClient,
    db: Arc<Database>,
    symbol: String,
}

impl DataCollector {
    pub async fn new(db_path: Option<&str>, proxy_url: Option<&str>) -> Result<Self> {
        let db = Database::new(db_path).await?;
        Ok(Self {
            client: BinanceClient::with_proxy(proxy_url),
            db: Arc::new(db),
            symbol: "BTCUSDT".to_string(), // Default symbol
        })
    }

    pub async fn initialize_with_historical_data(&self) -> Result<()> {
        // Check how many klines we already have
        let existing_count = match self.db.get_klines_count(&self.symbol).await {
            Ok(count) => count,
            Err(e) => {
                error!("Failed to check existing data count: {}", e);
                return Err(e).context("Failed to check database for existing klines");
            }
        };
        
        if existing_count >= 120 {
            info!(
                "Database already has {} minutes of data (sufficient), skipping historical data fetch",
                existing_count
            );
            return Ok(());
        }
        
        // If we don't have 120 minutes, fetch the most recent 120 minutes
        // INSERT OR REPLACE will handle any duplicates
        info!(
            "Database has {} minutes of data, fetching 120 minutes of historical data from Binance...",
            existing_count
        );
        
        let klines = match self.client.get_klines(&self.symbol, "1m", 120).await {
            Ok(klines) => {
                if klines.is_empty() {
                    warn!("Binance API returned empty klines array");
                    return Ok(()); // Non-fatal, we'll collect data going forward
                }
                klines
            }
            Err(e) => {
                error!("Failed to fetch historical data from Binance: {}", e);
                warn!(
                    "Could not initialize with historical data. The application will start collecting data from now. \
                    You may need to wait 120 minutes for sufficient data, or try again later when network connectivity is available."
                );
                return Err(e).context("Failed to fetch historical klines from Binance API");
            }
        };
        
        // Insert all fetched klines into database (INSERT OR REPLACE handles duplicates)
        let mut inserted = 0;
        let mut errors = 0;
        for kline in &klines {
            match self.db.insert_kline(kline, &self.symbol).await {
                Ok(_) => inserted += 1,
                Err(e) => {
                    errors += 1;
                    error!("Failed to insert kline {}: {}", kline.open_time, e);
                }
            }
        }
        
        if errors > 0 {
            warn!("Failed to insert {} out of {} klines into database", errors, klines.len());
        }
        
        let final_count = self.db.get_klines_count(&self.symbol).await?;
        info!(
            "Initialized database: inserted {} new klines, total: {} minutes",
            inserted,
            final_count
        );
        
        Ok(())
    }

    pub async fn collect_data(&self) -> Result<()> {
        let kline = self.client.get_current_minute_kline(&self.symbol).await?;
        
        // Check if we already have this minute's data (avoid duplicates)
        let exists = self.db.has_kline(&self.symbol, kline.open_time).await?;
        
        if exists {
            // Update existing entry (in case of partial updates)
            self.db.insert_kline(&kline, &self.symbol).await?;
            info!(
                "Updated kline data for minute {}. Latest: {}",
                kline.open_time,
                kline.close
            );
        } else {
            // Insert new minute data
            self.db.insert_kline(&kline, &self.symbol).await?;
            info!(
                "Collected new kline data. Latest: {}",
                kline.close
            );
        }
        
        // Cleanup old data (keep only last 120 minutes)
        self.db.cleanup_old_data(&self.symbol, 120).await?;
        
        Ok(())
    }

    pub async fn get_recent_data(&self) -> Vec<Kline> {
        self.db.get_recent_klines(&self.symbol, 120)
            .await
            .unwrap_or_default()
    }

    pub async fn has_sufficient_data(&self) -> bool {
        self.db.get_klines_count(&self.symbol)
            .await
            .unwrap_or(0) >= 120
    }
}

