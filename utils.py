import logging
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

def setup_logger(name, log_file='crypto_service.log'):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    os.makedirs(LOG_DIR, exist_ok=True)
    safe_path = os.path.join(LOG_DIR, os.path.basename(log_file))
    handler = logging.FileHandler(safe_path)
    handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    return logger

def log_info(logger, message):
    logger.info(message)
    print(f"[INFO] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")

def log_error(logger, message):
    logger.error(message)
    print(f"[ERROR] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")

def log_warning(logger, message):
    logger.warning(message)
    print(f"[WARNING] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")
