# utils/logger.py (updated)
import logging
import sys
from datetime import datetime
import os

def setup_logging(level=logging.INFO):
    """Setup logging configuration"""
    
    # Create logs directory if it doesn't exist
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # File handler
    filename = f"{log_dir}/spx_trader_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(filename)
    file_handler.setFormatter(formatter)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Suppress IBKR debug messages
    logging.getLogger('ibapi').setLevel(logging.WARNING)