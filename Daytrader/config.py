# config.py — Central configuration for all IBKR scripts

# ==================== CONNECTION ====================
HOST = "127.0.0.1"
PORT = 4002

# Client IDs (must be unique when running multiple scripts at once)
CLIENT_ID_OPTION_CHAIN   = 3      # ibkr_spx_option_chain.py
CLIENT_ID_STRATEGY       = 5      # ibkr_option_strategy.py
CLIENT_ID_VIX            = 6      # ibkr_vix_regime.py or smart_regime
CLIENT_ID_TECH           = 7      # ibkr_spx_technical.py

# ==================== DATABASE ====================
DB_FILE = "ibkr_spx_daytrader.db"

# ==================== OTHER SETTINGS ====================
# You can add more here later (e.g. default quantity, slippage buffer, etc.)
DEFAULT_QUANTITY = 1
TICK_BUFFER = 0.05          # extra edge on limit price