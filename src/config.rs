use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub proxy: Option<ProxyConfig>,
    pub llm: Option<LLMConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProxyConfig {
    /// HTTP/HTTPS/SOCKS5 proxy URL
    /// Examples:
    ///   - http://proxy.example.com:8080
    ///   - https://proxy.example.com:8080
    ///   - socks5://proxy.example.com:1080
    pub url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LLMProvider {
    OpenAI,
    Qwen,
    DeepSeek,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LLMConfig {
    /// LLM provider: openai, qwen, or deepseek
    pub provider: LLMProvider,
    /// API key for the LLM provider
    pub api_key: String,
    /// Base URL for the API (optional, uses default if not specified)
    #[serde(default)]
    pub base_url: Option<String>,
    /// Model name to use (optional, uses default for provider if not specified)
    #[serde(default)]
    pub model: Option<String>,
}

impl AppConfig {
    pub fn load(config_path: Option<&str>) -> Result<Self> {
        let config_path = config_path.unwrap_or("config.toml");
        
        // If config file doesn't exist, return default config
        if !Path::new(config_path).exists() {
            tracing::info!("Config file '{}' not found, using default configuration", config_path);
            return Ok(Self::default());
        }

        let content = std::fs::read_to_string(config_path)
            .context(format!("Failed to read config file: {}", config_path))?;

        let config: AppConfig = toml::from_str(&content)
            .context(format!("Failed to parse config file: {}", config_path))?;

        tracing::info!("Loaded configuration from {}", config_path);
        Ok(config)
    }

    pub fn get_proxy_url(&self) -> Option<&str> {
        self.proxy.as_ref().map(|p| p.url.as_str())
    }

    pub fn get_llm_config(&self) -> Option<&LLMConfig> {
        self.llm.as_ref()
    }
}

impl Default for AppConfig {
    fn default() -> Self {
        Self { 
            proxy: None,
            llm: None,
        }
    }
}

