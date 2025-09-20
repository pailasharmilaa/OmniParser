"""timing.py"""

from tools.logger_config import setup_logger
import time 
import functools

logger = setup_logger("timing")

def log_execution_time(func=None, *, name=None):
    """
    A decorator to log the execution time of a function.
    
    Args:
        func: The Function to be decorated
        name: Optional custom name for the function in the logs
        
    Returns:
        The decorated function that logs execution time
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            fn_name = name if name else fn.__name__
            module_name = fn.__module__

            start_time = time.time()
            try:
                result = fn(*args, **kwargs)
                return result
            finally:
                end_time = time.time()
                duration = end_time - start_time
                logger.info(f"{module_name}.{fn_name} executed in {duration:.4f} seconds")
        return wrapper

    if func is None:
        return decorator
    else:
        return decorator(func)

def log_step(step_name):
    """
    A context manager to log the execution time of a code block.
    
    Args:
        step_name: Name of the step to log
    
    Example: 
        with log_step("Loading image"):
            image = load_image(path)
    """
    class StepLogger:
        def __init__(self, name):
            self.name = name
            self.start_time = None

        def __enter__(self):
            self.start_time = time.time()
            logger.info(f"Starting step: {self.name}")
            return self
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = time.time() - self.start_time
            logger.info(f"Completed step: {self.name} in {duration:.4f} seconds")
    return StepLogger(step_name)