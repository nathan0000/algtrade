import os
import logging

# --- IBKR Connection Settings ---
IB_HOST = "192.168.1.116"
IB_PORT = 4002  # TWS Paper: 7497 | TWS Live: 7496 | Gateway Paper: 4002 | Gateway Live: 4001
IB_CLIENT_ID = 1

# --- File Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE = os.path.join(LOG_DIR, "app.log")

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler() # Also prints to standard terminal
    ]
)
logger = logging.getLogger("0DTE_Scanner")