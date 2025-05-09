# orchestrator/utils/logger.py
import logging

logger = logging.getLogger("documents_logger")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
console_handler.setFormatter(formatter)

logger.addHandler(console_handler)
