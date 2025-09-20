"""main.py"""
import os
import logging
import argparse
import shlex
import subprocess
from flask import Flask, request, jsonify, send_file
import threading
import traceback
import pyautogui
from PIL import Image
import sys
from io import BytesIO
import uuid
import json
import time
import requests
from indicator_window import initialize_indicator, toggle_indicator, get_status

# Define default paths in ProgramData
USER_DOCS = os.path.join(os.path.expanduser('~'), 'Documents')
PROGRAM_DATA_DIR = os.path.join(os.path.join(USER_DOCS, 'HevolveAi Agent Companion'))
DEFAULT_LOG_DIR = os.path.join(PROGRAM_DATA_DIR, 'logs')
DEFAULT_LOG_FILE = os.path.join(DEFAULT_LOG_DIR, 'server.log')
DEFAULT_DEVICE_ID_FILE = os.path.join(PROGRAM_DATA_DIR, 'device_id.json')
DEFAULT_STORAGE_DIR = os.path.join(PROGRAM_DATA_DIR, 'storage')
DEFAULT_USER_DATA_FILE = os.path.join(DEFAULT_STORAGE_DIR, 'user_data.json')

# Default API Endpoint 
DEFAULT_STOP_API_URL = "http://gcp_training2.hertzai.com:5001/stop" 

# Setting global variables to track LLM Control Status
llm_control_active = False
last_activity_time = 0
ACTIVITY_TIMEOUT = 15.0 # Seconds before considering control inactive

parser = argparse.ArgumentParser()
parser.add_argument("--log_file", help="log file path", type=str,
                    default=DEFAULT_LOG_FILE)
parser.add_argument("--port", help="port", type=int, default=5000)
parser.add_argument("--device_id_file", help="device ID file path", type=str,
                    default=DEFAULT_DEVICE_ID_FILE)
parser.add_argument("--stop_api_url", help="URL for stop API endpoint", type=str,
                    default=DEFAULT_STOP_API_URL)
args = parser.parse_args()

# Ensure log directory exists
log_dir = os.path.dirname(args.log_file)
if not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception as e:
        # If there's an error creating the log directory, fall back to temporary directory
        temp_log_dir = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'OmniParser')
        os.makedirs(temp_log_dir, exist_ok=True)
        args.log_file = os.path.join(temp_log_dir, 'server.log')
        print(f"Failed to create log directory {log_dir}: {str(e)}. Using {args.log_file} instead.")

# Ensure device_id directory exists
device_id_dir = os.path.dirname(args.device_id_file)
if not os.path.exists(device_id_dir):
    try:
        os.makedirs(device_id_dir, exist_ok=True)
    except Exception as e:
        # If there's an error creating the device ID directory, fall back to app directory
        args.device_id_file = os.path.join(os.path.dirname(__file__), 'device_id.json')
        print(f"Failed to create device ID directory {device_id_dir}: {str(e)}. Using {args.device_id_file} instead.")

# Configure logging
try:
    logging.basicConfig(
        filename=args.log_file,
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        filemode='a'
    )
except Exception as e:
    # If we can't write to the log file, use a temporary file
    temp_log_file = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'OmniParser', 'server.log')
    os.makedirs(os.path.dirname(temp_log_file), exist_ok=True)
    logging.basicConfig(
        filename=temp_log_file,
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        filemode='a'
    )
    print(f"Failed to use log file {args.log_file}: {str(e)}. Using {temp_log_file} instead.")

logger = logging.getLogger('werkzeug')
logger.setLevel(logging.INFO)

# Add a console handler for when running interactively
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(console_handler)

logging.info(f"Starting OmniParser Computer Control server on port {args.port}")
logging.info(f"Using log file: {args.log_file}")
logging.info(f"Using device ID file: {args.device_id_file}")
logging.info(f"Using Stop API URL: {args.stop_api_url}")

def initialize_indicator_window():
    try:
        # Start in a separate thread to avoid blocking Flask startup
        def init_indicator_thread():
            initialize_indicator()
            toggle_indicator(False)
            logging.info("LLM Control indicator initialized and hidden")
        threading.Thread(target=init_indicator_thread, daemon=True).start()
    except Exception as e:
        logger.error(f"Error initializing indicator: {str(e)}")
        
app = Flask(__name__)

computer_control_lock = threading.Lock()

# Function to get or create a persistent device ID
def get_device_id():
    if os.path.exists(args.device_id_file):
        try:
            with open(args.device_id_file, 'r') as f:
                data = json.load(f)
                return data.get('device_id')
        except Exception as e:
            logging.error(f"Error reading device ID file: {str(e)}")
    
    # Generate a new device ID if it doesn't exist
    device_id = str(uuid.uuid4())
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(args.device_id_file), exist_ok=True)
        with open(args.device_id_file, 'w') as f:
            json.dump({'device_id': device_id}, f)
        logging.info(f"Created new device ID: {device_id}")
    except Exception as e:
        logging.error(f"Error saving device ID: {str(e)}")
    
    return device_id


def call_stop_api():
    """
    Call the handle_stop_request API endpoint using HTTP
    """
    try:
        logger.info("Initiating stop request via API")

        # Try to get user data from storage
        try:
            stop_payload = {}

            if os.path.exists(DEFAULT_USER_DATA_FILE):
                try:
                    with open(DEFAULT_USER_DATA_FILE, 'r') as f:
                        user_data = json.load(f)
                        user_id = user_data.get('user_id')

                        if user_id:
                            # Add the user_id to payload regardless if we've prompt_id or not
                            stop_payload['user_id'] = user_id

                            # if we've prompt_id, include it too
                            prompt_id = user_data.get('prompt_id')
                            if prompt_id:
                                stop_payload['prompt_id'] = prompt_id
                                logger.info(f"Using speific stop for user_id={user_id}, prompt_id={prompt_id}")
                            else:
                                logger.info(f"Using user-specific stop for user_id={user_id}")
                
                except Exception as e:
                    logger.error(f"Error reading user data: {str(e)}")
            else:
                logger.info("No user data file found, using global stop")
        except Exception as e:
                logger.error(f"Error preparing stop payload: {str(e)}")
                stop_payload = {}

                
        # Make the API Call
        logger.info(f"Calling the stop API at {args.stop_api_url} with payload: {stop_payload}")

        response = requests.post(
            args.stop_api_url,
            json=stop_payload,
            headers={"Content-Type": "application/json"},
            timeout=10.0
        )

        # Log Response
        if response.status_code == 200:
            result = response.json()
            logger.info(f"Stop request result: {result}")

            # Check for succes in the response
            if isinstance(result, dict) and result.get('status') in ('success', 'warning'):
                logger.info("Stop request successfully send and acknowledged")
                return True
            else:
                logger.warning(f"Stop request returned unexpected result: {result}")
                return False
        else:
            logger.error(f'Stop request failed with status code: {response.status_code}')
            logger.error(f'Response: {response.text}')
            return False
    except Exception as e:
        logger.error(f"Error calling stop API: {str(e)}")
        logger.error(traceback.format_exc())
        return False  

# Get or generate device ID at startup
DEVICE_ID = get_device_id()
logging.info(f"Device ID: {DEVICE_ID}")     

@app.route('/probe', methods=['GET'])
def probe_endpoint():
    return jsonify({"status": "Probe successful", "message": "Service is operational"}), 200

def get_embedded_python_path():
    """Get the path to the embedded Python executable"""
    if getattr(sys, 'frozen', False):
        # Running as frozen executable
        base_dir = os.path.dirname(sys.executable)
    else:
        # Running as script
        base_dir = os.path.dirname(os.path.abspath(__file__))

    # Check if embedded Python exists
    embedded_python = os.path.join(base_dir, "python-embed", "python.exe")
    if os.path.exists(embedded_python):
        logging.info(f"Found embedded Python at: {embedded_python}")
        return embedded_python
    
    logging.warning("Embedded Python not found, will use system Python")
    return None

@app.route('/execute', methods=['POST'])
def execute_command():
    # Only execute one command at a time
    with computer_control_lock:
        global llm_control_active, last_activity_time

        # set control as active and update timestamp
        llm_control_active = True
        last_activity_time = time.time()

        # Show the indicator window
        toggle_indicator(True)

        # Start a timeout thread to automatically reset status after inactivity
        def reset_after_timeout():
            global llm_control_active, last_activity_time
            time.sleep(ACTIVITY_TIMEOUT + 0.1)  # Add small buffer
            if (time.time() - last_activity_time) > ACTIVITY_TIMEOUT:
                llm_control_active = False
                toggle_indicator(False)
        
        timeout_thread = threading.Thread(target=reset_after_timeout, daemon=True)
        timeout_thread.start()

        data = request.json
        # The 'command' key in the JSON request should contain the command to be executed.
        shell = data.get('shell', False)
        command = data.get('command', "" if shell else [])
        hide_window = data.get('hide_window', True) # To hide the cmd pop up

        if isinstance(command, str) and not shell:
            command = shlex.split(command)

        # Log the command being executed
        logging.info(f"Executing command: {command}")

        # Check if this is a Python command that we should intercept
        if (not shell and len(command) >= 2 and 
            (command[0] == "python" or command[0] == "python3") and 
            ("-c" in command or "-m" in command)):
            # Try to use embedded Python
            embedded_python = get_embedded_python_path()
            if embedded_python:
                # Replace the python command with embedded Python
                logging.info(f"Replacing system Python with embedded Python: {embedded_python}")
                command[0] = embedded_python

        # Expand user directory
        for i, arg in enumerate(command):
            if isinstance(arg, str) and arg.startswith("~/"):
                command[i] = os.path.expanduser(arg)

        # Execute the command without any safety checks.
        try:
            # Set up process creation flags for Windows to hide window
            startupinfo = None
            creation_flags = 0

            if sys.platform == "win32" and hide_window:
                # Import the necessary modules ofr windows
                import subprocess

                # CREATE_NO_WINDOW flag (0x08000000) to prevent window from showing
                creation_flags = 0x08000000

                # Also set up STARTUPINFO to hide the window
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0 # SW_HIDE

            # Add environment variables
            env = os.environ.copy()
            result = subprocess.run(
                command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                shell=shell, 
                text=True, 
                timeout=120, 
                env=env, 
                startupinfo=startupinfo, 
                creationflags=creation_flags)
            logging.info(f"Command executed with return code: {result.returncode}")

            # After executing the command, update the timestamp again to extend the indicator display
            last_activity_time = time.time()
            
            return jsonify({
                'status': 'success',
                'output': result.stdout,
                'error': result.stderr,
                'returncode': result.returncode
            })
        except Exception as e:
            logger.error("Command execution error: "+ traceback.format_exc())
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

@app.route('/screenshot', methods=['GET'])
def capture_screen_with_cursor():    
    try:
        cursor_path = os.path.join(os.path.dirname(__file__), "cursor.png")

        # Check if cursor.png exists
        if not os.path.exists(cursor_path):
            logging.warning(f"Cursor image not found at {cursor_path}")
            screenshot = pyautogui.screenshot()
        else:
            # Take screenshot and overlay cursor
            screenshot = pyautogui.screenshot()
            cursor_x, cursor_y = pyautogui.position()

            try:
                cursor = Image.open(cursor_path)
                # make the cursor smaller
                cursor = cursor.resize((int(cursor.width / 1.5), int(cursor.height / 1.5)))
                screenshot.paste(cursor, (cursor_x, cursor_y), cursor)
            except Exception as e:
                logging.error(f"Failed to process cursor image: {str(e)}")
    

        # Convert PIL Image to bytes and send
        img_io = BytesIO()
        screenshot.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')
    except Exception as e:
        logging.error("Screenshot error: "+ traceback.format_exc())
        return jsonify({
            'status': 'error',
            'message': 'Failed to capture screenshot: ' + str(e)
        }), 500

@app.route('/indicator/stop', methods=["GET"])
def stop_ai_control_endpoint():
    """Stop AI Control and hide the indicator"""
    global llm_control_active

    try:
        logger.info("Stop AI Control request received")
        
        # Just hide the indicator
        llm_control_active = False
        toggle_indicator(False)

        # call the stop API
        success = call_stop_api()

        if success:
            return jsonify({
                "success": True,
                "status": "Stopped and hidden",
                "message": "Stop request sent successfully"
            })
        else:
            return jsonify({
                "success": False,
                "status": "indicator hidden but stop request failed",
                "error": "Failed to send stop request to server"
            })
            
    except Exception as e:
        logger.error(f"Error stopping AI Control: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Even if we fail, try to hide the indicator
        try:
            toggle_indicator(False)
        except:
            pass
            
        return jsonify({"success": False, "error": str(e)})

@app.route('/llm_control_status', methods=["GET"])
def llm_control_status():
    """Return the current status of LLM Control"""
    global llm_control_active, last_activity_time
    # Check if activity has timed out
    if llm_control_active and (time.time() - last_activity_time) > ACTIVITY_TIMEOUT:
        llm_control_active = False
        toggle_indicator(False)
    
    return jsonify({
        'active': llm_control_active,
        'last_activity': last_activity_time,
        'indicator_status': get_status()
    })

@app.route('/status', methods=["GET"])
def status():
    return jsonify({
        'status': 'operational',
        'device_id': DEVICE_ID,
        'log_file': args.log_file
    }), 200

initialize_indicator_window()
if __name__ == '__main__':
    try:
        # Log Python version and environment info
        logging.info(f"Python version: {sys.version}")
        logging.info(f"Running from: {os.path.abspath(__file__)}")

        # Start the server
        logging.info(f"Starting Flask server on port {args.port}")
        app.run(debug=False, host="0.0.0.0", port=args.port)
    except Exception as e:
        logging.critical(f"Failed to start server: {str(e)}")
        logging.critical(traceback.format_exc())
        sys.exit(1)