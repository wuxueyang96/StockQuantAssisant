# LLM-Driven Quantitative Trading

A Rust web application that collects Binance cryptocurrency kline (candlestick) data every minute, maintains a rolling window of 120 minutes of historical data, and uses LLM (Large Language Model) to predict high and low prices for the next 120 minutes.

## Features

- **Real-time Data Collection**: Automatically fetches 1-minute kline data from Binance API every minute
- **Persistent Data Storage**: Uses SQLite database to store historical kline data locally
- **Historical Data Management**: Maintains a rolling window of the last 120 minutes of data in the database
- **LLM-Powered Predictions**: Uses OpenAI GPT-4 (or statistical fallback) to predict price movements
- **Web Interface**: Beautiful, modern web UI to view predictions and status
- **Fallback Model**: Uses statistical analysis if LLM API is unavailable

## Prerequisites

- Rust (latest stable version)
- Optional: OpenAI API key for LLM predictions (if not provided, uses statistical model)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd llm_drive_quantitative_trading
```

2. Build the project:
```bash
cargo build --release
```

## Configuration

### Configuration File

The application uses a `config.toml` file for configuration. Create this file in the project root:

```bash
cp config.toml.example config.toml
```

Edit `config.toml` to configure the proxy:

```toml
[proxy]
url = "socks5://127.0.0.1:1080"
```

**Note:** If no proxy URL is specified in the config file, no proxy will be used.

Supported proxy formats:
- HTTP: `http://proxy.example.com:8080`
- HTTPS: `https://proxy.example.com:8080`
- SOCKS5: `socks5://proxy.example.com:1080` or `socks://proxy.example.com:1080` (both work, `socks://` is auto-converted)
- With authentication: `http://username:password@proxy.example.com:8080`

### LLM Configuration

Configure which LLM provider to use for predictions in `config.toml`:

```toml
[llm]
provider = "openai"  # or "qwen" or "deepseek"
api_key = "your-api-key-here"
model = "gpt-4"  # Optional, uses default model if not specified
```

**Supported LLM Providers:**

1. **OpenAI** (`openai`):
   - Default model: `gpt-4`
   - Default base URL: `https://api.openai.com/v1/chat/completions`
   - Example:
     ```toml
     [llm]
     provider = "openai"
     api_key = "sk-..."
     ```

2. **Qwen** (`qwen`) - Alibaba Cloud:
   - Default model: `qwen-turbo`
   - Default base URL: `https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation`
   - Example:
     ```toml
     [llm]
     provider = "qwen"
     api_key = "sk-..."
     ```

3. **DeepSeek** (`deepseek`):
   - Default model: `deepseek-chat`
   - Default base URL: `https://api.deepseek.com/v1/chat/completions`
   - Example:
     ```toml
     [llm]
     provider = "deepseek"
     api_key = "sk-..."
     ```

**Note:** If no LLM configuration is specified, the application will use a statistical model for predictions.

### Optional: Environment Variable Proxy (Fallback)

If you don't use a config file, you can still set proxy via environment variables (as fallback):

```bash
# HTTP/HTTPS proxy
export HTTP_PROXY="http://proxy.example.com:8080"
export HTTPS_PROXY="http://proxy.example.com:8080"

# SOCKS5 proxy
export SOCKS5_PROXY="socks5://proxy.example.com:1080"

# Or use ALL_PROXY for all protocols
export ALL_PROXY="socks5://proxy.example.com:1080"
```

**Priority:** Config file > Environment variables > No proxy

## Running the Application

1. Run the application:
```bash
cargo run
```

Or for release mode:
```bash
cargo run --release
```

2. Open your browser and navigate to:
```
http://localhost:3000
```

## How It Works

1. **Data Collection**: The application starts collecting 1-minute kline data from Binance (default: BTCUSDT) immediately upon startup and continues every minute.

2. **Data Storage**: The last 120 minutes of data are stored in a local SQLite database (`trading_data.db`). Older data is automatically removed. The database persists between application restarts, so historical data is preserved.

3. **Prediction**: When you request a prediction:
   - If LLM is configured in `config.toml`: The historical data is formatted and sent to the configured LLM provider (OpenAI/Qwen/DeepSeek) for analysis
   - If not configured: A statistical model analyzes trends and volatility to make predictions

4. **Web Interface**: The web UI displays:
   - Current data collection status
   - Number of data points collected
   - Current BTCUSDT price
   - Predicted high and low for the next 120 minutes
   - Confidence level of the prediction

## API Endpoints

- `GET /` - Web interface
- `GET /api/status` - Get current data collection status
- `GET /api/prediction` - Get price predictions for next 120 minutes

## Project Structure

```
.
├── Cargo.toml          # Rust dependencies
├── README.md           # This file
├── config.toml.example  # Example configuration file
├── config.toml         # Configuration file (create from example)
├── src/
│   ├── main.rs         # Application entry point
│   ├── binance.rs      # Binance API client
│   ├── config.rs       # Configuration file parser
│   ├── db.rs           # SQLite database operations
│   ├── data_collector.rs # Data collection and storage
│   ├── llm.rs          # LLM integration and statistical model
│   └── web.rs          # Web server and API endpoints
└── static/
    └── index.html      # Web interface
```

## Notes

- The application creates a SQLite database file (`trading_data.db`) in the current directory to store kline data
- On first startup, the application fetches 120 minutes of historical data to immediately enable predictions
- The application needs at least 120 minutes of data collection before optimal predictions can be made
- Binance API has rate limits; the application respects these by querying once per minute
- Data persists between application restarts thanks to the SQLite database
- Predictions are for informational purposes only and should not be used as financial advice
- The statistical fallback model provides basic trend and volatility analysis

## License

See LICENSE file for details.
