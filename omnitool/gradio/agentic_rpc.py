"""agentic_rpc.py"""

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
import re
from flask import Flask, request, jsonify

logger = setup_logger('agentic_rpc_py')

# Create Flask app
flask_app = Flask(__name__)

CONFIG_DIR = Path("config")
CONFIG_FILE = CONFIG_DIR / "config.json"
API_KEY_FILE = CONFIG_DIR / "api_key"

class Sender(StrEnum):
    USER = "user"
    BOT = "assistant"
    TOOL = "tool"

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
        "provider": "azure",
        "api_key": "",
        "azure_resource_name": "",
        "only_n_most_recent_images": 2,
        "omniparser_server_url": "localhost:8080",
        "websocket_url": "wss://azurekong.hertzai.com:8445/wss",
        "realm": "realm1",
        "flask_port": 5001 # Adding default port for the stopping the vlm
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
    parser.add_argument("--output_dir", type=str, default="saved_json",
                        help = "Directory to save extracted Analysis and Next Action messages")
    parser.add_argument("--flask_port", type=int, default="5001",
                        help = "port for Flask API")
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


def handle_stop_request(msg_json):
    """
    Handle a request to stop ongoing processing.
    Acts as a robust kill switch for specific user tasks or all tasks.

    Expected JSON payload:
    {
        "user_id": "optional_user_id",
        "prompt_id": "optional_prompt_id",
        "stop_all": false   // Optional boolean to stop all tasks
        "force": true       // Optional boolean for force kill}
    """
    try:
        # Parse the JSON message
        msg = request.json
        if not msg:
            return jsonify({"status": "error", "message": "Missing JSON payload"}), 400
        
        logger.info(f"Received stop request: {msg}")

        # Extract identifiers
        user_id = msg.get('user_id')
        prompt_id = msg.get('prompt_id')
        stop_all = msg.get('stop_all', False)  # Option to stop all processing
        force_stop = msg.get('force', True)  # Option for force kill
        
        # Access global application state
        global app_state
        
        # Determine what we're stopping
        tasks_to_stop = []
        
        if stop_all:
            # Stop all active tasks
            logger.info("Stop ALL flag received - attempting to stop all active tasks")
            app_state["stop"] = True
            
            # Set all cancellation tokens
            if "cancellation_token" in app_state:
                app_state["cancellation_token"]["stop"] = True
                
            # Get all active sessions for notifications
            tasks_to_stop = [(uid, pid) for uid, pid in app_state["active_sessions"].items()]
        
        elif user_id:
            # If prompt_id is not provided but user_id is, look it up
            if not prompt_id and user_id in app_state["active_sessions"]:
                prompt_id = app_state["active_sessions"][user_id]
                logger.info(f"Found prompt_id {prompt_id} for user_id {user_id} in active sessions")
            
            if prompt_id:
                # Set specific cancellation token if it exists
                task_key = f"{user_id}_{prompt_id}"
                
                # Create task_tokens dict if it doesn't exist
                if "task_tokens" not in app_state:
                    app_state["task_tokens"] = {}
                
                # Create or update the token for this task
                if task_key not in app_state["task_tokens"]:
                    app_state["task_tokens"][task_key] = {"stop": True}
                else:
                    app_state["task_tokens"][task_key]["stop"] = True
                
                logger.info(f"Set task-specific cancellation token to TRUE for {task_key}")
                
                # Set global cancellation token as fallback
                if "cancellation_token" in app_state:
                    app_state["cancellation_token"]["stop"] = True
                    logger.info(f"Set global cancellation token to TRUE")
                
                # Set global stop as last resort
                app_state["stop"] = True
                logger.info(f"Set global stop flag to TRUE")
                
                tasks_to_stop.append((user_id, prompt_id))
            else:
                # Stop all tasks for this user
                user_tasks = [(uid, pid) for uid, pid in app_state["active_sessions"].items() if uid == user_id]
                tasks_to_stop.extend(user_tasks)
                
                # Create task_tokens dict if it doesn't exist
                if "task_tokens" not in app_state:
                    app_state["task_tokens"] = {}
                
                # Set stop token for each task
                for uid, pid in user_tasks:
                    task_key = f"{uid}_{pid}"
                    app_state["task_tokens"][task_key] = {"stop": True}
                
                # Also set global cancellation
                if "cancellation_token" in app_state:
                    app_state["cancellation_token"]["stop"] = True
                
                app_state["stop"] = True
                logger.info(f"Set stop flags for all tasks of user {user_id}")
        else:
            logger.warning("No user_id or stop_all flag provided in stop request")
            return {"status": "error", "message": "Invalid stop request: missing user_id or stop_all flag"}
            
        if not tasks_to_stop:
            logger.warning(f"No active tasks found to stop for user_id={user_id}, prompt_id={prompt_id}")
            return {"status": "warning", "message": "No active tasks found to stop"}

        # Get connection manager for notifications
        conn_manager = ConnectionManager()
        
        # Send stopping notifications and attempt forced cleanup
        stopped_tasks = []
        for uid, pid in tasks_to_stop:
            topic = f"com.hertzai.hevolve.action.{pid}.{uid}"
            
            try:
                # Send stopping notification
                conn_manager.publish(topic, {
                    'user_id': uid,
                    'prompt_id': pid,
                    'status': 'stopping',
                    'message': "Processing stop request...",
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"Published stopping message to {topic}")
                
                # Add to list of tasks being stopped
                stopped_tasks.append((uid, pid))
                
                # Clean up the active session
                if uid in app_state["active_sessions"]:
                    logger.info(f"Removing user_id={uid} from active sessions")
                    
                    # Only remove if the prompt_id matches or no specific prompt_id was requested
                    if app_state["active_sessions"][uid] == pid or not prompt_id:
                        del app_state["active_sessions"][uid]
                    
            except Exception as e:
                logger.error(f"Error handling stop for user_id={uid}, prompt_id={pid}: {e}")
        
        # Allow some time for the stop flags to be processed
        # This is synchronous in Flask, unlike the async inlineCallbacks version
        time.sleep(0.5) # Small delay to allow cancellation to propogate
        
        # Send final stopped notifications
        for uid, pid in stopped_tasks:
            topic = f"com.hertzai.hevolve.action.{pid}.{uid}"
            try:
                conn_manager.publish(topic, {
                    'user_id': uid,
                    'prompt_id': pid,
                    'status': 'stopped',
                    'message': 'Processing stopped successfully',
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"Published stopped message to {topic}")
            except Exception as e:
                logger.error(f"Error publishing stopped message to {topic}: {e}")
        
        # Reset global stop flag only if we're not handling a force stop
        # and we've successfully handled everything
        if not force_stop and len(stopped_tasks) == len(tasks_to_stop):
            app_state["stop"] = False
            logger.info("Reset global stop flag after successful stop operation")
        
        # Return success response
        return_data = {
            "status": "success",
            "message": f"Processing stopped for {len(stopped_tasks)} tasks",
            "stopped_tasks": [{"user_id": uid, "prompt_id": pid} for uid, pid in stopped_tasks]
        }

        return return_data

    except Exception as e:
        logger.error(f"Error processing stop request: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@flask_app.route('/stop', methods=['POST'])
def stop_endpoint():
    """HTTP endpoint to stop ongoing processing."""
    try:
        msg_json = request.json
        logger.info(f"Requested stop request via http: {msg_json}")
        result = handle_stop_request(msg_json)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in stop endpoint: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

@inlineCallbacks
def handle_rpc_request(msg_json):
    """Handle the RPC request and process the instruction asynchronously."""
    try:
        # Parse the JSON message
        if isinstance(msg_json, str):
            msg = json.loads(msg_json)
        else:
            msg = msg_json

        # Get global application state
        global app_state
        state = app_state

        omniparser_server_url = state.get("omniparser_server_url")

        # Extract data from message
        logger.info(f"Received RPC request: {msg}")
        parent_request_id = msg.get('parent_request_id')
        user_id = msg.get('user_id')
        prompt_id = msg.get('prompt_id', '0')
        instruction = msg.get('instruction_to_vlm_agent')
        os_to_control = msg.get('os_to_control', '')
        actions_available = msg.get('actions_available_in_os', [])
        max_eta = msg.get('max_ETA_in_seconds', 300)
        langchain_server = msg.get('langchain_server', None)
        enhanced_instruction = msg.get('enhanced_instruction')

        if enhanced_instruction:
            instruction = enhanced_instruction
            logger.info(f"Using enhanced instruction with guidance steps ENHANCED_INSTRUCTION : {instruction}")
            instruction = f"[REUSING PREVIOUS SUCCESSFUL EXECUTING]\n{instruction}"
        
        # Store the active session information
        if user_id:
            app_state["active_sessions"][user_id] = prompt_id
            logger.info(f"Registered active session: user_id={user_id}, prompt_id={prompt_id}")

        # Define topic for publishing messages
        topic = f"com.hertzai.hevolve.action.{prompt_id}.{user_id}"

        # Get connection manager instance for publishing status updates
        conn_manager = ConnectionManager()
        
        if not user_id:
            logger.error("No user_id provided in request")
            returnValue({"status": "error", "message": "No user_id provided"})

        if not instruction:
            logger.error("No instruction provided in request")
            returnValue({"status": "error", "message": "No instruction provided"})
        
        logger.info(f"Processing instruction for user_id {user_id}, prompt_id {prompt_id}: {instruction[:50]}...")
        logger.info(f"OS to control: {os_to_control}, Max ETA: {max_eta}s")
        
        # Validate parameters
        errors = valid_params(instruction, state, omniparser_server_url)
        if errors:
            error_message = "Validation errors: " + ", ".join(errors)
            logger.error(error_message)
            returnValue({"status": "error", "message": error_message})
        
        # Reset state for new request
        state["messages"] = []
        state["chatbot_messages"] = []
        state["tools"] = {}
        state["responses"] = {}
        state["stop"] = False

        # Create cancellation token for this request
        cancellation_token = {"stop": False}
        state["cancellation_token"] = cancellation_token
        
        # Append the user message to state["messages"]
        state["messages"].append({
            "role": Sender.USER,
            "content": [TextBlock(type="text", text=instruction)],
        })
        
        # Collect assistant responses
        assistant_responses = []
        extracted_responses = []

        # Publish start message to the topic

        try: 
            conn_manager.publish(topic, {
            'parent_request_id': parent_request_id,
            "user_id": user_id,
            "prompt_id": prompt_id,
            "status": 'started',
            "message": "Processing request started",
            "timestamp": datetime.now().isoformat(),
        })

            logger.info(f"Published start message to {topic}")
        except Exception as e:
            logger.error(f"Error publishing start message: {e}")

        
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

            # Extract Analysis and Next Action messages for JSON output
            if rendered_message.startswith("Analysis:") or rendered_message.startswith("Next action:"):
                message_type = "analysis" if rendered_message.startswith("Analysis:") else "next_action"
                content = rendered_message.split(":", 1)[1].strip()

                # For next_action, try to parse as JSON if it looks like a dict
                if message_type == "next_action" and content.startswith("{") and content.endswith("}"):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        # Keep as string if parsing fails
                        pass
                
                # Check status before extracting(for analysis messages, status is in the content)
                should_extract = True
                status = None

                if message_type == "analysis":
                    # Extract status from analysis content (format: "Status: XYZ...")
                    if content.startswith("Status:"):
                        # Find the end of the status line
                        status_match = re.match(r'Status:\s*(\w+)(.*)', content, re.DOTALL)
                        if status_match:
                            status = status_match.group(1).upper()
                            # Remove the status prefix from content
                            content = status_match.group(2).strip()
                            
                            if status == "FAILED":
                                should_extract = False
                                logger.info(f"Skipping extraction due to FAILED status in analysis")
                            elif status in ["SUCCESS", "FIRST_ACTION"]:
                                should_extract = True
                                logger.info(f"Extracting analysis with status: {status}")
                            else:
                                # If status is not recognized, extract anyway (for backward compatibility)
                                logger.warning(f"Unknown status '{status}', extracting anyway")
                                should_extract = True
        
                    
                
                # Only extract if should_extract is True
                if should_extract:
                    timestamp = datetime.now().isoformat()
                    extracted_responses.append({
                        "timestamp": timestamp,
                        "type": message_type,
                        "content": content
                    })

            
                    # Publish intermediate updates with the extracted message
                    try:
                        conn_manager.publish(topic, {
                            'parent_request_id': parent_request_id,
                            'user_id': user_id,
                            'prompt_id': prompt_id,
                            'status': 'in_progress',
                            'message': extracted_responses[-1],
                            'is_final': False,
                            'timestamp': datetime.now().isoformat()
                        })
                        logger.info(f"Publishing intermediate message to {topic}")
                    except Exception as e:
                        logger.error(f"Error publishing intermediate message: {e}")
                else:
                    logger.info(f"Skipped extraction for {message_type} with FAILED status")
            
            # try:
            #     conn_manager.publish("com.hertzai.hevolve.action", {
            #         'parent_request_id': parent_request_id,
            #         'user_id': user_id,
            #         'message': rendered_message,
            #         'is_final': False
            #     })
            # except Exception as e:
            #     logger.error(f"Error publishing intermediate response: {e}")
        
        # Run sampling_loop_sync with async handling in twisted framework
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
                cancellation_token=cancellation_token
            )

            # Handle the generator with Twisted
            while True:
                # Check stop flag before yielding generator
                if app_state.get("stop") or cancellation_token.get("stop"):
                    logger.info("Stop requested before generator yield, breaking loop")
                    # Reset the stop flag for future requests
                    app_state["stop"] = False
                    break
                
                try:
                    # Yield to allow cancellation, but with an explicit timeout
                    # to ensure we check the stop regularly
                    loop_msg = yield generator

                    # If the generator returns None, we're done
                    if loop_msg is None:
                        logger.info("Generator returned None, ending processing")
                        break
                    # Check after yield as well
                    if app_state.get("stop") or cancellation_token.get("stop"):
                        logger.info("Stop requested after generator yied, breaking loop")
                        # Reset the stop flag for future requests
                        app_state["stop"] = False
                        break
                except StopIteration:
                    break
                # Yield to allow other operations a chance to run
                yield None
        except Exception as e:
            logger.error(f"Error in sampling loop: {e}")
            logger.error(traceback.format_exc())

            try:
                conn_manager.publish(topic, {
                    'parent_request_id': parent_request_id,
                    'user_id': user_id,
                    'prompt_id': prompt_id,
                    'status': 'error',
                    'message': f"Error processing request: {str(e)}",
                    'timestamp': datetime.now().isoformat()
                })

                logger.info(f"Published error message to {topic}")
            except Exception as publish_error:
                logger.error(f"Error publishing error message: {publish_error}")
            
            # Return error result
            returnValue({
                "status": "error",
                "message": f"Error processing request: {str(e)}",
                "user_id": user_id,
                "prompt_id": prompt_id
            })
        
        # Calculate execution time
        execution_time = (datetime.now() - start_time).total_seconds()

        # Save extracted analysis and next action messages to JSON file
        output_dir = state.get("output_json", "saved_json")
        try:
            # Create output directory if it doesn't exist
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok = True)

            session_data = {
                "session_id": f"{user_id}_{prompt_id}",
                "instruction": instruction,
                "start_time": start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "execution_time_seconds": execution_time,
                "messages": extracted_responses
            }

            # Find the next available file number
            base_filename = f"{user_id}_{prompt_id}"
            file_index = 0

            while True:
                output_file = output_path / f"{base_filename}_{file_index}.json"
                if not output_file.exists():
                    break
                file_index += 1

            # Write to JSON file
            with open(output_file, 'w') as f:
                json.dump(session_data, f, indent=2)

            logger.info(f"Saved extracted messages to {output_file}")
        except Exception as e:
            logger.error(f"Error saving extracted messages to JSON: {e}")
        
        # Publish final status update
        try:
            conn_manager.publish(topic, {
                'parent_request_id': parent_request_id,
                'user_id': user_id,
                'prompt_id': prompt_id,
                'status': 'completed',
                'messages': 'Processing Completed',
                'execution_time_seconds': execution_time,
                'is_final': True,
                'timestamp': datetime.now().isoformat()
            })
            logger.info(f"Publishing completion message to {topic}")
        except Exception as e:
            logger.error(f"Error publishing completion message: {e}")
        # try:
        #     conn_manager.publish("com.hertzai.hevolve.action", {
        #         'parent_request_id': parent_request_id,
        #         'user_id': user_id,
        #         'messages': assistant_responses,
        #         'is_final': True
        #     })
        #     logger.info("Published final response")
        # except Exception as e:
        #     logger.error(f"Error publishing final response: {e}")
        
        # At the end, when processing is done or failed, remove from active sessions
        if user_id and app_state["active_sessions"]:
            del app_state["active_sessions"][user_id]
            logger.info(f"Removed session from active sessions: user_id={user_id}")

        # Return the final result
        returnValue({
            "status": "success",
            #"messages": assistant_responses,
            "user_id": user_id,
            "prompt_id": prompt_id,
            "total_messages": len(assistant_responses),
            "execution_time_seconds": execution_time,
            "output_file": str(output_file) if "output_file" in locals() else None,
            "extracted_responses": extracted_responses,
            "instruction": instruction
        })
    
    except Exception as e:
        logger.error(f"Error processing RPC request: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        returnValue({"status": "error", "message": str(e)})

def run_flask_server(port):
    """Run the Flask server in separate thread"""
    try:
        logger.info(f"Starting Flask API server on port {port}")
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error starting Flask server: {e}")
        logger.error(traceback.format_exc())

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
    global app_state
    app_state = {
        "messages": [],
        "model": config.get("model", "omniparser + gpt-4o"),
        "provider": config.get("provider", "openai"),
        "api_key": config.get("api_key", os.getenv("OPENAI_API_KEY", "")),
        "azure_resource_name": config.get("azure_resource_name", ""),
        "only_n_most_recent_images": config.get("only_n_most_recent_images", 2),
        "omniparser_server_url": config.get("omniparser_server_url", "localhost:8000"),
        "responses": {},
        "tools": {},
        "stop": False,
        "chatbot_messages": [],
        "output_dir": args.output_dir,
        "active_sessions": {} # will store user_id -> prompt_id mapping
    }
    
    # Override with environment variables if available
    if app_state["provider"] == "openai" and os.getenv("OPENAI_API_KEY"):
        app_state["api_key"] = os.getenv("OPENAI_API_KEY")
    elif app_state["provider"] == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        app_state["api_key"] = os.getenv("ANTHROPIC_API_KEY")
    
    omniparser_server_url = config.get("omniparser_server_url", "localhost:8000")
    websocket_url = config.get("websocket_url", "wss://azurekong.hertzai.com:8445/wss")
    realm = config.get("realm", "realm1")

    # Get Flask port from command line or config 
    flask_port = args.flask_port if args.flask_port else config.get("flask_port", 5001)
    
    logger.info(f"Starting OmniTool RPC with model: {app_state['model']}, provider: {app_state['provider']}")
    logger.info(f"Using OmniParser server at: {omniparser_server_url}")
    logger.info(f"Connecting to WebSocket at: {websocket_url}, realm: {realm}")
    logger.info(f"Analysis and Next Action messages with be saved to: {app_state['output_dir']}")
    logger.info(f"Flask API for stop requests will run on port: {flask_port}")
    
    # Start Flask server in a seperate thread
    flask_thread = threading.Thread(target=run_flask_server, args=(flask_port,), daemon=True)
    flask_thread.start()
    logger.info(f"Flask API server thread started")

    # Initialize the ConnectionManager
    conn_manager = ConnectionManager()
    conn_manager.initialize(url=websocket_url, realm=realm)

    while not conn_manager.is_connected():
        time.sleep(0.1)
    
    logger.info("Session joined")
    
    # Register RPC procedure
    rpc_procedure = "com.hertzai.hevolve.action3"
    conn_manager.register_procedure(rpc_procedure, handle_rpc_request)
    logger.info(f"Registered RPC procedure: {rpc_procedure}")
    
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