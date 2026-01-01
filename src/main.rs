mod binance;
mod config;
mod data_collector;
mod db;
mod llm;
mod web;

use anyhow::{Context, Result};
use config::AppConfig;
use data_collector::DataCollector;
use std::sync::Arc;
use tokio::time::{interval, Duration};
use tracing::{info, error};

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();

    // Load configuration
    let config = AppConfig::load(None)
        .context("Failed to load configuration")?;
    
    let proxy_url = config.get_proxy_url();

    // Initialize data collector with database and proxy
    let collector = Arc::new(
        DataCollector::new(None, proxy_url).await
            .context("Failed to initialize data collector")?
    );
    
    // Initialize with historical data first
    if let Err(e) = collector.initialize_with_historical_data().await {
        error!("Error initializing with historical data: {}", e);
    }

    // Start data collection task (runs every minute)
    let collector_clone = collector.clone();
    tokio::spawn(async move {
        // Then collect every minute
        let mut interval = interval(Duration::from_secs(60));
        loop {
            interval.tick().await;
            if let Err(e) = collector_clone.collect_data().await {
                error!("Error collecting data: {}", e);
            }
        }
    });

    // Start web server
    info!("Starting web server on http://localhost:3000");
    web::start_server(collector, Arc::new(config)).await?;

    Ok(())
}

