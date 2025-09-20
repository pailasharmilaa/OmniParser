#!/usr/bin/env python3
"""
RPC Procedure Test Script

This script tests if a specific RPC procedure is registered and working
on a WAMP router. It attempts to:
1. Connect to the WAMP router
2. Check if procedures are registered
3. Call the procedures with test data
4. Report detailed results

Usage:
python rpc_test.py
"""

import sys
import time
import logging
import uuid
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from autobahn.twisted.component import Component
from autobahn.twisted.wamp import ApplicationSession
from autobahn.wamp.types import CallOptions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("rpc_test")

# WAMP Connection Configuration
WEBSOCKET_URL = "" #Websocket URL
REALM = "realm1"
USER_ID = "00000" # Enter your user ID here
PROMPT_ID = "54"

# Test parameters
TEST_TIMEOUT = 10  # seconds


class TestSession(ApplicationSession):
    """Test session for checking RPC procedures."""

    async def onJoin(self, details):
        logger.info(f"Session joined with details: {details}")
        
        # Create a request ID for tracing
        request_id = str(uuid.uuid4())
        logger.info(f"Using request ID: {request_id}")
        
        # Define procedures to test
        procedures = [
            {
                "name": f"com.hertzai.hevolve.action.{PROMPT_ID}.{USER_ID}.win_exec",
                "args": ["python", "-c", "import pyautogui; pyautogui.FAILSAFE = False; pyautogui.moveTo(112, 22)"],
                "kwargs": {"request_id": request_id}
            },
            {
                "name": f"com.hertzai.hevolve.action.{PROMPT_ID}.{USER_ID}.win_screenshot",
                "args": [],
                "kwargs": {"request_id": request_id}
            }
        ]
        
        for proc in procedures:
            procedure_name = proc["name"]
            logger.info(f"Testing procedure: {procedure_name}")
            
            try:
                # First test if procedure exists by trying to register it ourselves
                # (This will fail with a registration error if it already exists)
                try:
                    logger.info(f"Checking if {procedure_name} is already registered...")
                    
                    # Define a dummy handler
                    async def dummy_handler(*args, **kwargs):
                        return "Dummy response"
                    
                    # Try to register it (should fail if procedure exists)
                    reg = await self.register(dummy_handler, procedure_name)
                    
                    # If we got here, procedure doesn't exist
                    logger.warning(f"Procedure {procedure_name} is NOT registered on the router!")
                    
                    # Unregister our dummy handler
                    await reg.unregister()
                except Exception as e:
                    if "wamp.error.procedure_already_exists" in str(e):
                        logger.info(f"Procedure {procedure_name} is already registered (good).")
                    else:
                        logger.error(f"Unexpected error checking procedure registration: {e}")
                
                # Now try to call the procedure
                logger.info(f"Calling procedure {procedure_name} with args={proc['args']}, kwargs={proc['kwargs']}")
                
                try:
                    # Set timeout for the call
                    options = CallOptions(timeout=TEST_TIMEOUT)
                    
                    # Make the actual call
                    start_time = time.time()
                    result = await self.call(procedure_name, *proc["args"], options=options, **proc["kwargs"])
                    elapsed_time = time.time() - start_time
                    
                    # Process the result
                    logger.info(f"Call succeeded in {elapsed_time:.2f}s!")
                    logger.info(f"Result type: {type(result)}")
                    
                    # Summarize the result based on its type
                    if isinstance(result, dict):
                        logger.info(f"Result keys: {list(result.keys())}")
                        if 'status' in result:
                            logger.info(f"Status: {result['status']}")
                        if 'output' in result:
                            logger.info(f"Output (truncated): {str(result['output'])[:200]}...")
                    elif isinstance(result, bytes):
                        logger.info(f"Received binary data of length: {len(result)} bytes")
                    else:
                        logger.info(f"Result (truncated): {str(result)[:200]}...")
                    
                except Exception as e:
                    logger.error(f"Error calling procedure {procedure_name}: {e}")
            
            except Exception as e:
                logger.error(f"Overall error testing procedure {procedure_name}: {e}")
                
        # We're done testing - leave the session
        logger.info("Tests completed, leaving session...")
        self.leave()

    def onDisconnect(self):
        logger.info("Session disconnected")
        reactor.stop()


def main():
    """Main function to run the tests."""
    logger.info("Starting RPC procedure test")
    logger.info(f"Connecting to {WEBSOCKET_URL}, realm {REALM}")

    # Create a Component with our test session
    component = Component(
        transports=[{
            "type": "websocket",
            "url": WEBSOCKET_URL,
            "serializers": ["json"]
        }],
        realm=REALM,
        session_factory=TestSession
    )

    # Start the component, which will set up everything
    logger.info("Starting component...")
    component.start()
    reactor.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)