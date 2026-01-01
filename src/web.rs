use crate::config::AppConfig;
use crate::data_collector::DataCollector;
use crate::llm::{LLMClient, Prediction};
use axum::{
    extract::State,
    http::StatusCode,
    response::{Html, Json},
    routing::get,
    Router,
};
use serde::Serialize;
use std::sync::Arc;
use tracing::{info, warn};

#[derive(Serialize)]
struct PredictionResponse {
    predicted_high: f64,
    predicted_low: f64,
    confidence: Option<String>,
    current_price: f64,
    data_points: usize,
    timestamp: String,
}

#[derive(Serialize)]
struct StatusResponse {
    data_points: usize,
    has_sufficient_data: bool,
    latest_price: Option<f64>,
}

pub struct AppState {
    collector: Arc<DataCollector>,
    config: Arc<AppConfig>,
}

pub async fn start_server(collector: Arc<DataCollector>, config: Arc<AppConfig>) -> anyhow::Result<()> {
    let app_state = AppState { collector, config };
    
    let app = Router::new()
        .route("/", get(index_handler))
        .route("/api/prediction", get(prediction_handler))
        .route("/api/status", get(status_handler))
        .with_state(Arc::new(app_state));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await?;
    info!("Server listening on http://localhost:3000");
    axum::serve(listener, app).await?;

    Ok(())
}

async fn index_handler() -> Html<&'static str> {
    Html(include_str!("../static/index.html"))
}

async fn prediction_handler(
    State(state): State<Arc<AppState>>,
) -> Result<Json<PredictionResponse>, StatusCode> {
    let klines = state.collector.get_recent_data().await;
    
    if klines.is_empty() {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }

    let current_price = klines.last().unwrap().close;
    
    // Try to use LLM if configured, otherwise use statistical model
    let prediction = if let Some(llm_config) = state.config.get_llm_config() {
        let proxy_url = state.config.get_proxy_url();
        let client_result = LLMClient::from_config(llm_config, proxy_url);
        
        match client_result {
            Ok(llm_client) => {
                llm_client.predict(&klines).await
                    .map_err(|e| {
                        warn!("LLM prediction failed: {}", e);
                        format!("LLM prediction failed: {}", e)
                    })
                    .unwrap_or_else(|err_msg| {
                        // 返回带错误信息的空 Prediction
                        Prediction {
                            predicted_high: 0.0,
                            predicted_low: 0.0,
                            confidence: Some(format!("Error: {}", err_msg)),
                        }
                    })
            }
            Err(e) => {
                warn!("Failed to create LLM client: {}", e);
                Prediction {
                    predicted_high: 0.0,
                    predicted_low: 0.0,
                    confidence: Some(format!("Failed to create LLM client: {}", e)),
                }
            }
        }
    } else {
        info!("No LLM configuration found");
        Prediction {
            predicted_high: 0.0,
            predicted_low: 0.0,
            confidence: Some("No LLM configuration found".to_string()),
        }
    };
    
    Ok(Json(PredictionResponse {
        predicted_high: prediction.predicted_high,
        predicted_low: prediction.predicted_low,
        confidence: prediction.confidence,
        current_price,
        data_points: klines.len(),
        timestamp: chrono::Utc::now().to_rfc3339(),
    }))
}

async fn status_handler(
    State(state): State<Arc<AppState>>,
) -> Json<StatusResponse> {
    let klines = state.collector.get_recent_data().await;
    let latest_price = klines.last().map(|k| k.close);
    
    Json(StatusResponse {
        data_points: klines.len(),
        has_sufficient_data: state.collector.has_sufficient_data().await,
        latest_price,
    })
}

