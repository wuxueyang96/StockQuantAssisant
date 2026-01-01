use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::{error, warn};

/// Normalize proxy URL to formats supported by reqwest
/// Converts socks:// to socks5:// since reqwest requires socks5://
pub fn normalize_proxy_url(url: &str) -> String {
    let url = url.trim();
    
    // Remove trailing slash if present
    let url = url.trim_end_matches('/');
    
    // Normalize SOCKS proxy schemes
    if url.starts_with("socks://") {
        url.replacen("socks://", "socks5://", 1)
    } else if url.starts_with("socks4://") {
        // SOCKS4 is not supported by reqwest, but we'll try to convert to SOCKS5
        warn!("SOCKS4 proxy detected, converting to SOCKS5 (may not work)");
        url.replacen("socks4://", "socks5://", 1)
    } else {
        url.to_string()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Kline {
    pub open_time: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub close_time: i64,
}

impl Kline {
    pub fn from_binance_response(arr: &[Value]) -> Result<Self> {
        Ok(Kline {
            open_time: arr[0]
                .as_i64()
                .context("Invalid open_time")?,
            open: arr[1]
                .as_str()
                .context("Invalid open")?
                .parse()
                .context("Failed to parse open")?,
            high: arr[2]
                .as_str()
                .context("Invalid high")?
                .parse()
                .context("Failed to parse high")?,
            low: arr[3]
                .as_str()
                .context("Invalid low")?
                .parse()
                .context("Failed to parse low")?,
            close: arr[4]
                .as_str()
                .context("Invalid close")?
                .parse()
                .context("Failed to parse close")?,
            volume: arr[5]
                .as_str()
                .context("Invalid volume")?
                .parse()
                .context("Failed to parse volume")?,
            close_time: arr[6]
                .as_i64()
                .context("Invalid close_time")?,
        })
    }

    pub fn to_string(&self) -> String {
        format!(
            "Time: {}, Open: {:.8}, High: {:.8}, Low: {:.8}, Close: {:.8}, Volume: {:.2}",
            DateTime::<Utc>::from_timestamp_millis(self.open_time)
                .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
                .unwrap_or_else(|| self.open_time.to_string()),
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume
        )
    }
}

pub struct BinanceClient {
    client: reqwest::Client,
    base_url: String,
}

impl BinanceClient {
    pub fn new() -> Self {
        Self::with_proxy(None)
    }

    pub fn with_proxy(proxy_url: Option<&str>) -> Self {
        // Use provided proxy URL, or check environment variables as fallback
        let proxy_url: Option<String> = if let Some(url) = proxy_url {
            Some(url.to_string())
        } else if let Ok(url) = std::env::var("HTTP_PROXY") {
            Some(url)
        } else if let Ok(url) = std::env::var("HTTPS_PROXY") {
            Some(url)
        } else if let Ok(url) = std::env::var("SOCKS5_PROXY") {
            Some(url)
        } else if let Ok(url) = std::env::var("ALL_PROXY") {
            Some(url)
        } else {
            None
        };

        let mut client_builder = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30));

        // Configure proxy if provided
        if let Some(proxy_url_str) = &proxy_url {
            // Normalize proxy URL: convert socks:// to socks5://
            let normalized_url = normalize_proxy_url(proxy_url_str);
            tracing::info!("Configuring HTTP client with proxy: {} (normalized: {})", proxy_url_str, normalized_url);
            
            // Try to parse as reqwest::Proxy
            // reqwest supports HTTP, HTTPS, and SOCKS5 proxies
            match reqwest::Proxy::all(&normalized_url) {
                Ok(proxy) => {
                    client_builder = client_builder.proxy(proxy);
                    tracing::info!("Successfully configured proxy");
                }
                Err(e) => {
                    tracing::warn!("Failed to parse proxy URL '{}': {}, continuing without proxy", normalized_url, e);
                }
            }
        }

        let client = client_builder
            .build()
            .expect("Failed to create HTTP client");
        
        Self {
            client,
            base_url: "https://api.binance.com".to_string(),
        }
    }

    pub async fn get_klines(&self, symbol: &str, interval: &str, limit: u32) -> Result<Vec<Kline>> {
        let url = format!(
            "{}/api/v3/klines?symbol={}&interval={}&limit={}",
            self.base_url, symbol, interval, limit
        );

        // Retry logic with exponential backoff
        let max_retries = 3;
        let mut last_error = None;

        for attempt in 0..max_retries {
            match self.try_get_klines(&url).await {
                Ok(klines) => {
                    if attempt > 0 {
                        tracing::info!("Successfully fetched klines after {} retries", attempt);
                    }
                    return Ok(klines);
                }
                Err(e) => {
                    last_error = Some(e);
                    if attempt < max_retries - 1 {
                        let delay = std::time::Duration::from_secs(2_u64.pow(attempt));
                        warn!(
                            "Attempt {} failed to fetch klines from Binance, retrying in {:?}...",
                            attempt + 1,
                            delay
                        );
                        tokio::time::sleep(delay).await;
                    }
                }
            }
        }

        // If all retries failed, return the last error with more context
        Err(last_error.unwrap())
            .context(format!(
                "Failed to fetch klines from Binance after {} attempts. URL: {}",
                max_retries, url
            ))
    }

    async fn try_get_klines(&self, url: &str) -> Result<Vec<Kline>> {
        tracing::debug!("Fetching klines from: {}", url);

        let response = self
            .client
            .get(url)
            .send()
            .await
            .context("Network error: Failed to send HTTP request to Binance API")?;

        let status = response.status();
        if !status.is_success() {
            let status_text = response.text().await.unwrap_or_else(|_| "Unknown error".to_string());
            error!("Binance API returned error status {}: {}", status, status_text);
            return Err(anyhow::anyhow!(
                "Binance API error: HTTP {} - {}",
                status,
                status_text
            ));
        }

        let json: Vec<Vec<Value>> = response
            .json()
            .await
            .context("Failed to parse JSON response from Binance API")?;

        if json.is_empty() {
            warn!("Binance API returned empty klines array");
            return Ok(Vec::new());
        }

        let mut klines = Vec::new();
        for (idx, arr) in json.iter().enumerate() {
            match Kline::from_binance_response(arr) {
                Ok(kline) => klines.push(kline),
                Err(e) => {
                    error!("Failed to parse kline at index {}: {}", idx, e);
                    return Err(e).context(format!("Failed to parse kline data at index {}", idx));
                }
            }
        }

        tracing::debug!("Successfully parsed {} klines", klines.len());
        Ok(klines)
    }

    pub async fn get_current_minute_kline(&self, symbol: &str) -> Result<Kline> {
        let klines = self.get_klines(symbol, "1m", 1).await?;
        klines
            .into_iter()
            .next()
            .context("No kline data returned")
    }
}

