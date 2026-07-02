import logging
import sys

def setup_backend_logging():
    logger = logging.getLogger("app")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.DEBUG)
        
        fmt = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.propagate = False
        
    return logger

logger = logging.getLogger("app")
