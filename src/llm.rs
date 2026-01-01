use crate::binance::Kline;
use crate::config::{LLMConfig, LLMProvider};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::error;

#[derive(Debug, Serialize, Deserialize)]
pub struct Prediction {
    pub predicted_high: f64,
    pub predicted_low: f64,
    pub confidence: Option<String>,
}

pub struct LLMClient {
    client: reqwest::Client,
    provider: LLMProvider,
    api_key: String,
    base_url: String,
    model: String,
}

impl LLMClient {
    pub fn from_config(config: &LLMConfig, proxy_url: Option<&str>) -> Result<Self> {
        let base_url = config.base_url.clone().unwrap_or_else(|| {
            Self::get_default_base_url(&config.provider)
        });
        
        let model = config.model.clone().unwrap_or_else(|| {
            Self::get_default_model(&config.provider)
        });

        // Create HTTP client with optional proxy
        let mut client_builder = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600)); // Longer timeout for LLM requests

        if let Some(proxy_url) = proxy_url {
            use crate::binance::normalize_proxy_url;
            let normalized_url = normalize_proxy_url(proxy_url);
            if let Ok(proxy) = reqwest::Proxy::all(&normalized_url) {
                client_builder = client_builder.proxy(proxy);
                tracing::info!("LLM client configured with proxy");
            }
        }

        let client = client_builder
            .build()
            .context("Failed to create HTTP client for LLM")?;

        Ok(Self {
            client,
            provider: config.provider.clone(),
            api_key: config.api_key.clone(),
            base_url,
            model,
        })
    }

    fn get_default_base_url(provider: &LLMProvider) -> String {
        match provider {
            LLMProvider::OpenAI => "https://api.openai.com/v1/chat/completions".to_string(),
            LLMProvider::Qwen => "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation".to_string(),
            LLMProvider::DeepSeek => "https://api.deepseek.com/v1/chat/completions".to_string(),
        }
    }

    fn get_default_model(provider: &LLMProvider) -> String {
        match provider {
            LLMProvider::OpenAI => "gpt-4".to_string(),
            LLMProvider::Qwen => "qwen-turbo".to_string(),
            LLMProvider::DeepSeek => "deepseek-chat".to_string(),
        }
    }

    pub fn format_data_for_llm(&self, klines: &[Kline]) -> String {
        let mut formatted = String::from("Here is the historical 1-minute candlestick (kline) data for BTCUSDT:\n\n");
        
        formatted.push_str("Format: [Time, Open, High, Low, Close, Volume]\n\n");
        
        for (i, kline) in klines.iter().enumerate() {
            let time_str = chrono::DateTime::<chrono::Utc>::from_timestamp_millis(kline.open_time)
                .map(|dt| dt.format("%Y-%m-%d %H:%M:%S UTC").to_string())
                .unwrap_or_else(|| kline.open_time.to_string());
            
            formatted.push_str(&format!(
                "Minute {}: [{}] Open: {:.8}, High: {:.8}, Low: {:.8}, Close: {:.8}, Volume: {:.2}\n",
                i + 1,
                time_str,
                kline.open,
                kline.high,
                kline.low,
                kline.close,
                kline.volume
            ));
        }
        
        formatted.push_str("\nBased on this historical data, please predict the HIGH and LOW values for the next 120 minutes. ");
        formatted.push_str("Consider price trends, volatility patterns, and support/resistance levels. ");
        formatted.push_str("Respond ONLY with a JSON object in this exact format: ");
        formatted.push_str(r#"{"predicted_high": <number>, "predicted_low": <number>, "confidence": "<high|medium|low>"}"#);
        
        formatted
    }

    pub async fn predict(&self, klines: &[Kline]) -> Result<Prediction> {
        let prompt = self.format_data_for_llm(klines);
        
        let (payload, auth_header) = match self.provider {
            LLMProvider::OpenAI | LLMProvider::DeepSeek => {
                let payload = json!({
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a quantitative trading analyst. Analyze candlestick data and provide predictions in JSON format only."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.3,
                    // "max_tokens": 200
                });
                (payload, format!("Bearer {}", self.api_key))
            }
            LLMProvider::Qwen => {
                let payload = json!({
                    "model": self.model,
                    "input": {
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a quantitative trading analyst. Analyze candlestick data and provide predictions in JSON format only."
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ]
                    },
                    "parameters": {
                        "temperature": 0.3,
                        // "max_tokens": 200
                    }
                });
                (payload, format!("Bearer {}", self.api_key))
            }
        };

        let mut request = self
            .client
            .post(&self.base_url)
            .header("Authorization", &auth_header)
            .header("Content-Type", "application/json");

        // Qwen uses different header format
        if matches!(self.provider, LLMProvider::Qwen) {
            request = request.header("X-DashScope-SSE", "disable");
        }

        let response = request
            .json(&payload)
            .send()
            .await
            .context("Failed to send LLM request")?;

        let status = response.status();
        if !status.is_success() {
            let error_text = response.text().await.unwrap_or_else(|_| "Unknown error".to_string());
            error!("LLM API returned error status {}: {}", status, error_text);
            return Err(anyhow::anyhow!("LLM API error: HTTP {} - {}", status, error_text));
        }

        let response_json: serde_json::Value = response
            .json()
            .await
            .context("Failed to parse LLM response")?;

        tracing::info!("LLM reesponse: {}", response_json);

        // Extract content based on provider response format
        let content = match self.provider {
            LLMProvider::OpenAI | LLMProvider::DeepSeek => {
                response_json["choices"][0]["message"]["content"]
                    .as_str()
                    .context("No content in LLM response")?
            }
            LLMProvider::Qwen => {
                response_json["output"]["choices"][0]["message"]["content"]
                    .as_str()
                    .context("No content in Qwen response")?
            }
        };

        // Try to extract JSON from the response
        let json_str = if content.trim().starts_with('{') {
            content.trim()
        } else {
            // Try to find JSON in the response
            if let Some(start) = content.find('{') {
                if let Some(end) = content.rfind('}') {
                    &content[start..=end]
                } else {
                    content.trim()
                }
            } else {
                content.trim()
            }
        };

        tracing::info!("LLM response: {}", json_str);

        let prediction: Prediction = serde_json::from_str(json_str)
            .context("Failed to parse prediction from LLM response")?;

        Ok(prediction)
    }
}
