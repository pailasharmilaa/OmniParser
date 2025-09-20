"""agentic.py"""

import os
import sys
import json
from datetime import datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import cast
import argparse
import logging
from anthropic import APIResponse
from anthropic.types import TextBlock
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock
from anthropic.types.tool_use_block import ToolUseBlock
from loop import (
    APIProvider,
    sampling_loop_sync,
)
from tools import ToolResult
import requests
from requests.exceptions import RequestException
import base64
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from autobahn.twisted.component import Component, run
import logging
from logging.handlers import RotatingFileHandler
from tools.connection_manager import ConnectionManager
import threading
import time
from tools.logger_config import setup_logger
import traceback

logger = setup_logger('agentic_py')


CONFIG_DIR = Path("config")
CONFIG_FILE = CONFIG_DIR / "config.json"
API_KEY_FILE = CONFIG_DIR / "api_key"

class Sender(StrEnum):
    USER = "user"
    BOT = "assistant"
    TOOL = "tool"

class TwistedSubscriptionHandler:
    message = None

    @staticmethod
    def on_message(msg):
        # Store the received message
        TwistedSubscriptionHandler.message = msg
        logger.info(f"Event received")
        return msg

    @staticmethod
    def subscribe_and_wait(user_id, url="wss://azurekong.hertzai.com:8445/wss", realm='realm1'):
        # Reset message
        TwistedSubscriptionHandler.message = None

        # Get or initialize the connection manager
        conn_manager = ConnectionManager()
        if not conn_manager.is_connected():
            conn_manager.initialize(url=url, realm=realm)

            while not conn_manager.is_connected():
                logger.info("Waiting for connection to be established...")
                time.sleep(0.1)

            logger.error("Connection establised successfully")
                
        message_event = threading.Event()

        # Define callback that will be called when message is received
        def on_message_wrapper(msg):
            TwistedSubscriptionHandler.message = msg
            message_event.set()
            logger.info(f"Message received in subscription handler")
            return msg
        
        # Subscribe to the topic
        topic = f"com.hertzai.hevolve.action"
        conn_manager.subscribe(topic, on_message_wrapper)
        logger.info(f"Subscribed to {topic}, waiting indefinitely for response")

        message_event.wait()
        logger.info(f"Message Received")

        return TwistedSubscriptionHandler.message

def load_config():
    """Load configuration from JSON file."""
    try:
        if CONFIG_FILE.exists():
            config = json.loads(CONFIG_FILE.read_text())
            logger.info("Configuration loaded successfully")
            return config
        else:
            logger.warning(f"Configuration file not found at {CONFIG_FILE}. Using default configuration.")
            return create_default_config()
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return create_default_config()

def create_default_config():
    """Create default configuration."""
    default_config = {
        "model": "omniparser + gpt-4o",
        "provider": "openai",
        "api_key": "",
        "azure_resource_name": "",
        "only_n_most_recent_images": 2,
        "omniparser_server_url": "localhost:8000",
        "websocket_url": "wss://azurekong.hertzai.com:8445/wss",
        "realm": "realm1"
    }
    
    # Save default configuration to file
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(default_config, indent=2))
        logger.info(f"Default configuration created at {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error creating default configuration: {e}")
    
    return default_config

def parse_arguments():
    parser = argparse.ArgumentParser(description="OmniTool Backend")
    parser.add_argument("--config", type=str, help="Path to configuration file")
    parser.add_argument("--omniparser_server_url", type=str, help="OmniParser server URL")
    return parser.parse_args()

def _api_response_callback(response: APIResponse[BetaMessage], response_state: dict):
    response_id = datetime.now().isoformat()
    response_state[response_id] = response

def _tool_output_callback(tool_output: ToolResult, tool_id: str, tool_state: dict):
    tool_state[tool_id] = tool_output

def valid_params(user_input, state, omniparser_server_url):
    """Validate all requirements and return a list of error messages."""
    errors = []
    
    try:
        url = f'http://{omniparser_server_url}/probe'
        response = requests.get(url, timeout=3)
        if response.status_code != 200:
            errors.append(f"OmniParser Server is not responding")
    except RequestException as e:
        errors.append(f"OmniParser Server is not responding: {e}")
    
    if not state["api_key"].strip():
        errors.append("LLM API Key is not set")

    if state["provider"] == "azure" and not state.get("azure_resource_name", "").strip():
        errors.append("Azure Resource Name is required when using Azure OpenAI")

    if not user_input:
        errors.append("No computer use request provided")
    
    return errors

@inlineCallbacks
def process_message(msg, state, omniparser_server_url):
    """Process incoming messages with instructions for the VLM agent."""
    try:
        # Extract data from message
        logger.info(f"Msg from the action publish: {msg}")
        user_id = msg.get('user_id')
        prompt_id = msg.get('prompt_id', '0')
        instruction = msg.get('instruction_to_vlm_agent')
        os_to_control = msg.get('os_to_control', '')
        actions_available = msg.get('actions_available_in_os', [])
        max_eta = msg.get('max_ETA_in_seconds', 300)
        langchain_server = msg.get('langchain_server', None)

        # Get connection manager instance
        conn_manager = ConnectionManager()

        
        if not user_id:
            logger.error("No user_id provided in request")
            conn_manager.publish("com.hertzai.hevolve.action", { 
                "error": "No user_id provided"
            })
            return

        if not instruction:
            logger.error("No instruction provided in request")
            conn_manager.publish("com.hertzai.hevolve.action", {
                'error': "No instruction provided"
            })
            return
        
        logger.info(f"Processing instruction for user_id {user_id}, prompt_id {prompt_id} : {instruction[:50]}...")
        logger.info(f"OS to control: {os_to_control}, Max ETA: {max_eta}s")
        
        # Validate parameters
        errors = valid_params(instruction, state, omniparser_server_url)
        if errors:
            error_message = "Validation errors: " + ", ".join(errors)
            logger.error(error_message)
            conn_manager.publish("com.hertzai.hevolve.action", {
                'user_id': user_id,
                'error': error_message
            })
            return
        
        # Reset state for new request
        state["messages"] = []
        state["chatbot_messages"] = []
        state["tools"] = {}
        state["responses"] = {}
        state["stop"] = False
        
        # Append the user message to state["messages"]
        state["messages"].append({
            "role": Sender.USER,
            "content": [TextBlock(type="text", text=instruction)],
        })
        
        # Collect assistant responses
        assistant_responses = []
        
        # Define callback for processing responses
        def output_callback(message, hide_images=False, sender="bot"):
            def _render_message(message):
                if isinstance(message, str):
                    return message
                
                is_tool_result = not isinstance(message, str) and (
                    isinstance(message, ToolResult)
                    or message.__class__.__name__ == "ToolResult"
                )
                
                if is_tool_result:
                    message = cast(ToolResult, message)
                    if message.output:
                        return message.output
                    if message.error:
                        return f"Error: {message.error}"
                    if message.base64_image and not hide_images:
                        return f'<img src="data:image/png;base64,{message.base64_image}">'
                
                elif isinstance(message, BetaTextBlock) or isinstance(message, TextBlock):
                    return f"Analysis: {message.text}"
                elif isinstance(message, BetaToolUseBlock) or isinstance(message, ToolUseBlock):
                    return f"Next action: {message.input}"
                else:  
                    return str(message)
            
            rendered_message = _render_message(message)
            if not rendered_message:
                return
            
            # Add message to collected responses
            assistant_responses.append(rendered_message)
            
            # Publish intermediate updates
            try:
                conn_manager.publish("com.hertzai.hevolve.action", {
                    'user_id': user_id,
                    'message': rendered_message,
                    'is_final': False
                })
            except Exception as e:
                logger.error(f"Error publishing intermediate response: {e}")
        
        # Run sampling_loop_sync
        start_time = datetime.now()
        try:
            generator = sampling_loop_sync(
            model=state["model"],
            provider=state["provider"],
            messages=state["messages"],
            output_callback=output_callback,
            tool_output_callback=partial(_tool_output_callback, tool_state=state["tools"]),
            api_response_callback=partial(_api_response_callback, response_state=state["responses"]),
            api_key=state["api_key"],
            only_n_most_recent_images=state["only_n_most_recent_images"],
            max_tokens=4096,
            omniparser_url=omniparser_server_url,
            azure_resource_name=state.get("azure_resource_name") if state["provider"] == "azure" else None,
            user_id=user_id,
            prompt_id=prompt_id,
        )

            # Handle the generator with Twisted
            while True:
                try:
                    loop_msg = yield generator
                    #logger.info(f"Loop msg: {loop_msg}")
                    if loop_msg is None or state.get("stop"):
                        logger.info("End of processing")
                        break
                except StopIteration:
                    break
        except Exception as e:
            logger.error(f"Error in sampling loop: {e}")
            logger.error(traceback.format_exc())
        
        # Publish final response with complete history
        try:
            if langchain_server:
                pass # For server-side, can't connect to multiple topics so will use sync post request instead of crossbar publish
            else:
                conn_manager.publish("com.hertzai.hevolve.action", {
                    'user_id': user_id,
                    'messages': assistant_responses,
                    'is_final': True
                })
                logger.info("Published final response")
        except Exception as e:
            logger.error(f"Error publishing final response: {e}")
    
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        logger.error(f"{traceback.format_exc()}")
        try:
            # Get connection manager instance
            conn_manager = ConnectionManager()
            conn_manager.publish("com.hertzai.hevolve.action", {
                'user_id': msg.get('user_id'),
                'error': str(e)
            })
        except Exception as publish_error:
            logger.error(f"Error publishing error message: {publish_error}")

def main():
    # Parse command line arguments
    args = parse_arguments()
    
    # Load configuration
    config_path = args.config if args.config else CONFIG_FILE
    config = load_config() if not args.config else json.loads(Path(config_path).read_text())
    
    # Override configuration with command line arguments
    if args.omniparser_server_url:
        config["omniparser_server_url"] = args.omniparser_server_url
    
    # Set up application state
    state = {
        "messages": [],
        "model": config.get("model", "omniparser + gpt-4o"),
        "provider": config.get("provider", "openai"),
        "api_key": config.get("api_key", os.getenv("OPENAI_API_KEY", "")),
        "azure_resource_name": config.get("azure_resource_name", ""),
        "only_n_most_recent_images": config.get("only_n_most_recent_images", 2),
        "responses": {},
        "tools": {},
        "stop": False,
        "chatbot_messages": []
    }
    
    # Override with environment variables if available
    if state["provider"] == "openai" and os.getenv("OPENAI_API_KEY"):
        state["api_key"] = os.getenv("OPENAI_API_KEY")
    elif state["provider"] == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        state["api_key"] = os.getenv("ANTHROPIC_API_KEY")
    
    omniparser_server_url = config.get("omniparser_server_url", "localhost:8000")
    websocket_url = config.get("websocket_url", "wss://azurekong.hertzai.com:8445/wss")
    realm = config.get("realm", "realm1")
    
    logger.info(f"Starting OmniTool with model: {state['model']}, provider: {state['provider']}")
    logger.info(f"Using OmniParser server at: {omniparser_server_url}")
    logger.info(f"Connecting to WebSocket at: {websocket_url}, realm: {realm}")
    
    # Initialize the ConnectionManager
    conn_manager = ConnectionManager()
    conn_manager.initialize(url=websocket_url, realm=realm)

    while not conn_manager.is_connected():
        time.sleep(0.1)
    
    if not conn_manager.is_connected():
        logger.error(f"Could not connect to WebSocket within {timeout} seconds")
        return

    logger.info("Session joined")
        
    # Subscribe to incoming messages

    def on_input_message(msg):
        logger.debug(f"Received message: {msg}")
        
        # Skipping messages without a user_id
        if not isinstance(msg, dict) or 'user_id' not in msg:
            logger.debug('Skipping message without user_id')
            return

        # Process the message
        process_message(msg, state, omniparser_server_url)
        
    conn_manager.subscribe("com.hertzai.hevolve.action", on_input_message)
    logger.info("Subscribed to com.hertzai.hevolve.action")
    
    # Announce that we're ready
    conn_manager.publish("com.hertzai.hevolve.action", {"status": "ready"})
    logger.info("Published ready status")

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info('Received keyboard interrupt, shutting down')
        sys.exit(0)

if __name__ == "__main__":
    main()