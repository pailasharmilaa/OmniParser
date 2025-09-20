"""app.py -- Creates a WebApp with reliable startup and system tray functionality"""
import os
import sys
import threading
import logging
import webview as pywebview
import argparse
from flask import Flask, jsonify, request
import importlib.util
import traceback
import json
import time
import ctypes
from pathlib import Path
import shutil
from waitress import serve 
import urllib.parse
import requests

try:
    indicator_module = importlib.import_module('indicator_window')
    INDICATOR_AVAILABLE = True
    print("LLM control indicator module loaded successfully")
except ImportError:
    INDICATOR_AVAILABLE = False
    print("LLM control indicator module not available")

# Global variable to track system tray status
_tray_icon = None
_window = None  # Global window reference

# Default configuration for stop API URL 
DEFAULT_STOP_API_URL = "http://gcp_training2.hertzai.com:5001/stop"

# Initialize argument parser
parser = argparse.ArgumentParser(description='HevolveAi Agent Companion GUI Application')
parser.add_argument("--port", help="port for Flask server", type=int, default=5000)
parser.add_argument("--width", help="window width", type=int, default=1024)
parser.add_argument("--height", help="window height", type=int, default=768)
parser.add_argument("--title", help="window title", type=str, default="HevolveAi Agent Companion")
parser.add_argument("--background", help="run in background/minimized mode", action="store_true")
parser.add_argument("--stop_api_url", help="URL for stop API endpoint", type=str, default=DEFAULT_STOP_API_URL)

# Parse args with error handling - default to visible mode
try:
    args, unknown = parser.parse_known_args()
    if unknown:
        # Log unknown arguments but don't fail
        print(f"Unknown command line arguments: {unknown}")
except Exception as e:
    print(f"Error parsing command line: {str(e)}")
    # Create a default args object with safe defaults
    class DefaultArgs:
        port = 5000
        width = 1024
        height = 768
        title = "HevolveAi Agent Companion"
        background = False  # Default to visible mode
    args = DefaultArgs()

# Configure logging
user_docs = os.path.join(os.path.expanduser('~'), 'Documents')
log_dir = os.path.join(user_docs, 'HevolveAi Agent Companion', 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'gui_app.log')

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filemode='a'
)

# Add console handler if not running in background
if not args.background:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(console_handler)

logger = logging.getLogger('HevolveAiAgentCompanionGUI')

# Log startup details
logger.info("Starting HevolveAi Agent Companion GUI Application")
logger.info(f"Original arguments: {sys.argv}")
logger.info(f"Parsed arguments: port={args.port}, width={args.width}, height={args.height}, " +
           f"title={args.title}, background={args.background}, stop_api_url = {args.stop_api_url}")

# Function to call the Stop API endpoint
def call_stop_api():
    """
    Call the stop API to stop AI control processing
    """
    try:
        logger.info(f"Calling stop API ay {args.stop_api_url}")

        # Try to get user data from storage
        user_data_file = os.path.join(user_docs, 'HevolveAi Agent Companion', 'storage', 'user_data.json')
        stop_payload = {}

        if os.path.exists(user_data_file):
            try:
                with open(user_data_file, 'r') as f:
                    user_data = json.load(f)
                    user_id = user_data.get('user_id')

                    if user_id:
                        stop_payload['user_id'] = user_id

                        # If we've prompt_id, include it too
                        prompt_id = user_data.get('prompt_id')
                        if prompt_id:
                            stop_payload['prompt_id'] = prompt_id
                            logger.info(f"Using specific stop for user_id={user_id}, prompt_id={prompt_id}")
                        else:
                            logger.info(f"Using user-specific stop for user_id={user_id}")
            except Exception as e:
                logger.error(f"Error reading user data: {str(e)}")
        else:
            logger.info("No user data file found, using global stop")
        
        # Call the API
        response = requests.post(
            args.stop_api_url,
            json=stop_payload,
            headers={"Content-Type":  "application/json"},
            timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Stop API response: {result}")
            return True
        else:
            logger.error(f"Stop API call failed with status {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error calling stop API: {str(e)}")
        logger.error(traceback.format_exc())
        return False

# Ensure we're in the right directory when started from registry
def ensure_working_directory():
    """Ensure we're in the right working directory when launched from startup"""
    try:
        # Get the directory of the executable
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            app_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            app_dir = os.path.dirname(os.path.abspath(__file__))
            
        # Log the current and executable directories
        current_dir = os.getcwd()
        logger.info(f"Current working directory: {current_dir}")
        logger.info(f"Application directory: {app_dir}")
        
        # Change to the application directory if different
        if current_dir != app_dir:
            os.chdir(app_dir)
            logger.info(f"Changed working directory to: {app_dir}")
            
        return True
    except Exception as e:
        logger.error(f"Failed to set working directory: {str(e)}")
        return False

# Flask server to communicate with the GUI
gui_app = Flask(__name__)

# Import the main.py flask app dynamically
try:
    # Get the path to main.py in the same directory as this script
    if getattr(sys, 'frozen', False):
        # If running as a bundle (compiled with cx_Freeze)
        app_dir = os.path.dirname(sys.executable)
    else:
        # If running as a script
        app_dir = os.path.dirname(os.path.abspath(__file__))
    
    main_path = os.path.join(app_dir, 'main.py')
    
    # Load main.py as a module
    spec = importlib.util.spec_from_file_location("main_module", main_path)
    main_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_module)
    
    # Get the Flask app instance from main.py
    flask_app = main_module.app
    
    # Add CORS headers to all routes
    from flask_cors import CORS
    CORS(flask_app)
    
    logger.info("Successfully imported main.py Flask application")
except Exception as e:
    logger.error(f"Failed to import main.py: {str(e)}")
    logger.error(traceback.format_exc())
    sys.exit(1)

def check_existing_user_data():
    """Check for existing user data and update URL if all required data is present"""
    try:
        storage_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'HevolveAi Agent Companion', 'storage')
        user_data_file = os.path.join(storage_dir, 'user_data.json')

        if os.path.exists(user_data_file):
            logger.info("Found existing user_data.json, checking contents")

            try:
                with open(user_data_file, 'r') as f:
                    user_data = json.load(f)
                
                logger.info(f"Loaded the JSON file from storage the value contains {user_data.keys()}")

                # Check if all required keys are present
                required_keys = ['agentname', 'user_id', 'access_token', 'email']
                if all(k in user_data for k in required_keys):
                    # Construct the URL with all parameters

                    # Properly URL encode each parameter
                    agent_name_encoded = urllib.parse.quote(user_data['agentname'])
                    email_encoded = urllib.parse.quote(user_data['email'])
                    token_encoded = urllib.parse.quote(user_data['access_token'])
                    userid_encoded = urllib.parse.quote(str(user_data['user_id']))

                    new_url = (f"https://hevolve.hertzai.com/agents/{agent_name_encoded}?"
                               f"email={email_encoded}&"
                               f"token={token_encoded}&"
                               f"userid={userid_encoded}&"
                               f"companion=true")
                    
                    logger.info(f"Loading saved user data URL: {new_url}")
                    return new_url
                else:
                    logger.info("User data file exists but doesn't contain all required keys, using default URL")
            except json.JSONDecodeError:
                logger.error("User data file exists but contains invalid JSON, using default URL")
        else:
            logger.info("No existing user_data.json file found, using default URL")
        
        return "https://hevolve.hertzai.com/agents/Instructable-Agent?companion=true"
    except Exception as e:
        logger.error(f"Error checking existing user data: {str(e)}")
        return "https://hevolve.hertzai.com/agents/Instructable-Agent?companion=true"

def initialize_indicator(server_port=5000):
    """Initialize the indicator window if available"""
    if not INDICATOR_AVAILABLE:
        return False
    
    try:
        # Start indicator in a separate thread to avoid blocking
        def init_indicator_thread():
            try:
                # Add a delay to ensure main window is created first
                time.sleep(2)
                
                # Initialize and then hide the indicator window
                indicator_module.initialize_indicator(server_port)  # Pass the port
                # Make sure it's explicitly hidden
                indicator_module.toggle_indicator(False, server_port)  # Pass port here too
                print("LLM control indicator initialized and hidden")
            except Exception as e:
                print(f"Error in indicator initialization thread: {str(e)}")
        
        # Start the thread
        indicator_thread = threading.Thread(target=init_indicator_thread, daemon=True)
        indicator_thread.start()
        return True
    
    except Exception as e:
        print(f"Failed to initialize indicator: {str(e)}")
        return False
        
def start_flask():
    """Start the Flask server in a separate thread"""
    try:
        # Add hide to tray endpoint
        @flask_app.route('/hide_to_tray', methods=['GET'])
        def hide_to_tray_endpoint():
            # This will signal the window to be hidden in the main thread
            global _window
            if _window:
                _window.hide()
                # Show notification
                if _tray_icon:
                    notify_minimized_to_tray(_tray_icon)
            return jsonify({"success": True})
        
        # Add show window endpoint
        @flask_app.route('/show_window', methods=['GET'])
        def show_window_endpoint():
            global _window
            if _window:
                _window.show()
            return jsonify({"success": True})
        
        @flask_app.route('/indicator/show', methods=['GET'])
        def show_indicator_endpoint():
            """Show the LLM control indicator"""
            if INDICATOR_AVAILABLE:
                try:
                    indicator_module.toggle_indicator(True)
                    return jsonify({"success": True, "status": "showing"})
                except Exception as e:
                    return jsonify({"success": False, "error": str(e)})
            else:
                return jsonify({"success": False, "error": "Indicator module not available"})

        @flask_app.route('/indicator/hide', methods=['GET'])
        def hide_indicator_endpoint():
            """Hide the LLM control indicator"""
            if INDICATOR_AVAILABLE:
                try:
                    indicator_module.toggle_indicator(False)
                    return jsonify({"success": True, "status": "hidden"})
                except Exception as e:
                    return jsonify({"success": False, "error": str(e)})
            else:
                return jsonify({"success": False, "error": "Indicator module not available"})

        @flask_app.route('/indicator/status', methods=['GET'])
        def indicator_status_endpoint():
            """Get the status of the LLM control indicator"""
            if INDICATOR_AVAILABLE:
                try:
                    status = indicator_module.get_status()
                    return jsonify({"success": True, "status": status})
                except Exception as e:
                    return jsonify({"success": False, "error": str(e)})
            else:
                return jsonify({"success": False, "error": "Indicator module not available"})
                
        @flask_app.route('/api/storage/set', methods = ['POST'])
        def set_storage():
            try:
                global _window
                data = request.json
                
                # Validate that we've at least one of the expected keys
                expected_keys = ['agentname', 'email', 'access_token', 'user_id']
                found_keys = [key for key in expected_keys if key in data]

                if not found_keys:
                    return jsonify({
                        'success': False,
                        'companion_app': True,
                        'error': 'No valid keys provided. Expceted one of: agentname, email, token or user_id'
                    })
                
                # Store in a file
                storage_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'HevolveAi Agent Companion', 'storage')
                os.makedirs(storage_dir, exist_ok=True)
                user_data_file = os.path.join(storage_dir, 'user_data.json')

                user_data = {}
                # Update specific keys from the data
                for key in found_keys:
                    user_data[key] = data[key]

                # Save the new data (completely overwriting any existing file)
                with open(user_data_file, 'w') as f:
                    json.dump(user_data, f)
                
                logger.info(f"Completely overwrote user_data.json with new data containing keys: {list(user_data.keys())}")
                
                # Check if we have all required keys to update the URL
                required_keys = ['agentname', 'user_id', 'access_token', 'email']
                url_updated = False

                if all(k in user_data for k in required_keys) and _window:
                    #Properly URL encode each parameter
                    agent_name_encoded = urllib.parse.quote(user_data['agentname'])
                    email_encoded = urllib.parse.quote(user_data['email'])
                    token_encoded = urllib.parse.quote(user_data['access_token'])
                    userid_encoded = urllib.parse.quote(str(user_data['user_id']))
                    # Construct the new URL with all parameters
                    new_url = (f"https://hevolve.hertzai.com/agents/{agent_name_encoded}?"
                               f"email={email_encoded}&"
                               f"token={token_encoded}&"
                               f"userid={userid_encoded}&"
                               f"companion=true")
                    
                    logger.info(f"Attempting to load URL: {new_url}")
                    
                    # Update the window URL
                    try:
                        _window.load_url(new_url)
                        logger.info(f"Updated window URL to: {new_url}")
                        url_updated = True
                    except Exception as e:
                        logger.error(f"Failed to update window URL: {str(e)}")
                
                return jsonify({
                    'success': True, 
                    'url_updated': url_updated,
                    'keys_present': list(user_data.keys()),
                    'all_required_keys_present': all(k in user_data for k in required_keys)})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)})

        @flask_app.route('/api/storage/get/<key>', methods=['GET'])
        def get_storage(key):
            try:
                user_data_file = os.path.join(os.path.expanduser('~'), 'Documents', 'HevolveAi Agent Companion', 'storage', f'user_data.json')

                if os.path.exists(user_data_file):
                    with open(user_data_file, 'r') as f:
                        user_data = json.load(f)
                    
                    if key in user_data:
                        return jsonify({"success": True, "data": user_data[key]})
                    else:
                        return jsonify({"success": False, "error": "Key not found"})
                else:
                    return jsonify({"success": False, "error": "User data not found"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)})
                          
        
        # Start the main Flask application on the specified port
        logger.info(f"Starting Flask server on port {args.port}")
        flask_app.run(debug=False, host="0.0.0.0", port=args.port, use_reloader=False)
        # serve(flask_app, host="0.0.0.0", port=args.port)
    except Exception as e:
        logger.error(f"Error starting Flask server: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)

def get_server_info():
    """Get server information to display in the UI"""
    try:
        # Try to fetch the device ID from the same location main.py would use
        user_docs = os.path.join(os.path.expanduser('~'), 'Documents')
        device_id_dir = os.path.join(user_docs, 'HevolveAi Agent Companion')
        device_id_file = os.path.join(device_id_dir, 'device_id.json')
        if os.path.exists(device_id_file):
            with open(device_id_file, 'r') as f:
                data = json.load(f)
                return {"device_id": data.get('device_id')}
    except Exception as e:
        logger.warning(f"Failed to get device ID: {str(e)}")
    
    return {"device_id": "Unknown"}

def toggle_fullscreen(window_instance):
    """Toggle between fullscreen and normal window"""
    try:
        window_instance.maximize()
    except Exception as e:
        logger.error(f"Error maximizing window: {str(e)}")
        logger.error(traceback.format_exc())

def set_window_theme_attribute(window_instance):
    """Set dark theme for window using Windows 11 APIs"""
    if sys.platform != "win32":
        return False
        
    try:
        import ctypes
        from ctypes import windll, c_int, byref, sizeof
        
        # Windows 11 specific constants
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR = 35
        DWMWA_BORDER_COLOR = 34
        
        def on_shown():
            try:
                # Get window handle
                if hasattr(window_instance, 'original_window') and hasattr(window_instance.original_window, 'handle'):
                    hwnd = window_instance.original_window.handle
                elif hasattr(window_instance, 'handle'):
                    hwnd = window_instance.handle
                else:
                    # Alternative approach - try to find window by title
                    hwnd = windll.user32.FindWindowW(None, args.title)
                
                if not hwnd:
                    logger.error("Could not get window handle")
                    return False
                
                # Try setting dark mode (Windows 10 and 11)
                dark_mode = c_int(1)
                windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    byref(dark_mode), 
                    sizeof(dark_mode)
                )
                
                # Try setting title bar color (Windows 11)
                # RGB color format - 0x00BBGGRR (reversed order)
                title_color = c_int(0x00303030)  # Dark gray
                windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_CAPTION_COLOR,
                    byref(title_color),
                    sizeof(title_color)
                )
                
                # Set border color
                border_color = c_int(0x00303030)  # Dark gray
                windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_BORDER_COLOR,
                    byref(border_color),
                    sizeof(border_color)
                )
                
                logger.info("Successfully set window theme attributes")
                return True
            except Exception as e:
                logger.error(f"Failed to set window theme: {str(e)}")
                return False
                
        window_instance.events.shown += on_shown
        return True
    except Exception as e:
        logger.error(f"Error setting up window theme: {str(e)}")
        return False

def apply_dark_mode_to_all_windows():
    """Apply dark mode to all windows using a timer-based approach"""
    if sys.platform != "win32":
        return
        
    import ctypes
    import time
    import threading
    
    # Windows 10/11 dark mode constants
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    
    def find_and_set_dark_mode():
        try:
            # Function to enumerate all windows
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            
            titles = []
            
            def foreach_window(hwnd, lParam):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    title = buff.value
                    titles.append((hwnd, title))
                return True
            
            # Enumerate all windows
            EnumWindows(EnumWindowsProc(foreach_window), 0)
            
            # Find our window by title
            for hwnd, title in titles:
                if args.title in title:
                    # Set dark mode
                    value = ctypes.c_int(1)  # 1 = dark mode
                    try:
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, 
                            DWMWA_USE_IMMERSIVE_DARK_MODE,
                            ctypes.byref(value), 
                            ctypes.sizeof(value)
                        )
                        logger.info(f"Applied dark mode to window: {title}")
                    except Exception as e:
                        logger.error(f"Failed to apply dark mode to {title}: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error in dark mode thread: {str(e)}")
    
    # Set dark mode with a slight delay to ensure window is created
    timer_thread = threading.Timer(1.0, find_and_set_dark_mode)
    timer_thread.daemon = True
    timer_thread.start()


# Improved system tray setup with singleton pattern
def setup_system_tray(window_instance):
    global _tray_icon
    
    # Return existing icon if already set up
    if _tray_icon is not None:
        logger.info("Using existing system tray icon")
        return _tray_icon
    
    try:
        logger.info("Setting up new system tray icon")
        import pystray
        from PIL import Image
        import json

        if getattr(sys, 'frozen', False):
            # If running as a bundle (compiled with cx_freeze)
            app_dir = os.path.dirname(sys.executable)
        else:
            # If running as a script
            app_dir = os.path.dirname(os.path.abspath(__file__))
        
        icon_path = os.path.join(app_dir, 'app.ico')
        logger.info(f"Looking for icon at: {icon_path}")

        if os.path.exists(icon_path):
            try:
                icon_image = Image.open(icon_path)
                logger.info(f"Using icon from {icon_path}")
            except Exception as e:
                logger.error(f"Error loading icon {icon_path}: {str(e)}")
                # Try to create a default icon if app.ico is not available
                try:
                    icon_image = pystray.Icon('HevolveAiAgentCompanion').icon
                    logger.info("Using default pystray icon")
                except Exception as e2:
                    logger.error(f"Failed to create default icon: {str(e2)}")
                    return None
        else:
            logger.error(f"Icon file not found at {icon_path}")
            try:
                # Try to create a default icon
                icon_image = pystray.Icon('HevolveAiAgentCompanion').icon
                logger.info("Using default pystray icon")
            except Exception as e:
                logger.error(f"Failed to create default icon: {str(e)}")
                return None
        
        def on_quit_clicked(icon, item):
            logger.info("Quit selected from system tray menu")
            icon.stop()
            try:
                os._exit(0)
            except Exception:
                sys.exit(0)
        
        def on_restore_clicked(icon, item):
            logger.info("Restore selected from system tray menu")
            # Show the window with dimension 320x300
            try:
                # First show the window to ensure it's visible
                window_instance.show()
                # Then resize it - order matters in pywebview
                window_instance.resize(320, 300)
                # Force window to be on top
                if hasattr(window_instance, 'move_to_center'):
                    window_instance.move_to_center()
                logger.info("Window restored to 320x300")
            except Exception as e:
                logger.error(f"Error restoring window: {str(e)}")
                # Fallback approach
                try:
                    window_instance.show()
                    logger.info("Window shown with fallback method")
                except Exception as e2:
                    logger.error(f"Fallback show also failed: {str(e2)}")
        
        def on_maximize_clicked(icon, item):
            logger.info("Maximize selected from system tray menu")
            try:
                window_instance.show()
                window_instance.maximize()
            except Exception as e:
                logger.error(f"Error maximizing window: {str(e)}")
                
        def on_tray_clicked(icon):
            # When tray icon is clicked, show the menu rather than restoring the window
            logger.info("Tray icon clicked, showing menu")
            # We don't need to take action here as the menu will appear automatically
            pass

        
        # Define system tray menu
        menu = pystray.Menu(
            pystray.MenuItem('Restore', on_restore_clicked),
            pystray.MenuItem('Maximize', on_maximize_clicked),
            pystray.MenuItem('Quit', on_quit_clicked)
        )
        
        # Create a system tray icon with the proper icon
        _tray_icon = pystray.Icon(
            'HevolveAiAgentCompanion',  # Use a unique name
            icon_image,
            'HevolveAi Agent Companion',
            menu
        )
        
        # Register the on_click handler
        _tray_icon.on_click = on_tray_clicked
        
        # Start the icon in a separate thread
        icon_thread = threading.Thread(target=_tray_icon.run, daemon=True)
        icon_thread.start()
        
        logger.info("System tray icon started successfully")
        return _tray_icon
    
    except ImportError as e:
        logger.error(f"Required package not installed for system tray: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error setting up system tray: {str(e)}")
        logger.error(traceback.format_exc())
        return None

# Improved notification function that doesn't use win10toast
def notify_minimized_to_tray(icon, message="Application minimized to system tray"):
    """Show a notification that the app is minimized to the system tray"""
    logger.info(f"Showing notification: {message}")
    
    try:
        # Use the pystray's native notification instead of win10toast
        icon.notify(message, "HevolveAi Agent Companion")
        logger.info("Notification shown successfully")
    except Exception as e:
        logger.error(f"Error showing notification: {str(e)}")
        logger.error(traceback.format_exc())

# Better event handlers that don't return None
def on_closed():
    logger.info("Window close button clicked, minimizing to system tray")
    try:
        global _window
        if _window:
            _window.hide()
            logger.info("Window hidden successfully")
            
            # No notification on close, only on minimize
    except Exception as e:
        logger.error(f"Error hiding window in on_closed: {str(e)}")
    
    # Return True to prevent default window closing
    return True

# More robust on_minimized handler without visible check
def on_minimized():
    logger.info("Window minimize button clicked, minimizing to system tray")
    try:
        # Try to explicitly hide the window
        global _window
        if _window:
            _window.hide()
            logger.info("Window hide command sent")
            
            # Show notification using the system tray icon
            global _tray_icon
            if _tray_icon is not None:
                # Try to display notification
                notify_minimized_to_tray(_tray_icon)
            else:
                logger.warning("No tray icon available for notification")
    except Exception as e:
        logger.error(f"Error in on_minimized handler: {str(e)}")
        logger.error(traceback.format_exc())
    
    # Return True to prevent default minimization
    return True

# Clean initialization of event handlers
def setup_window_events(window_instance):
    logger.info("Setting up window event handlers")
    
    try:
        # Add our handlers
        window_instance.events.closed += on_closed
        window_instance.events.minimized += on_minimized
        
        logger.info("Window event handlers set up successfully")
        return True
    except Exception as e:
        logger.error(f"Error setting up window events: {str(e)}")
        logger.error(traceback.format_exc())
        
        # As a fallback, try the direct approach without clearing
        try:
            window_instance.events.closed += on_closed
            window_instance.events.minimized += on_minimized
            logger.info("Applied event handlers with fallback method")
            return True
        except Exception as e2:
            logger.error(f"Fallback also failed: {str(e2)}")
            return False

# Ensure system tray is running properly at startup
def ensure_system_tray_running():
    global _tray_icon, _window
    
    if _tray_icon is None:
        logger.warning("System tray icon not initialized - attempting to setup")
        _tray_icon = setup_system_tray(_window)
        if _tray_icon is None:
            logger.error("Failed to create system tray icon after retry")
            return False
    
    # Test if tray icon is functional
    try:
        if hasattr(_tray_icon, 'visible') and not _tray_icon.visible:
            logger.warning("Tray icon not visible - attempting to restart")
            icon_thread = threading.Thread(target=_tray_icon.run, daemon=True)
            icon_thread.start()
    except Exception as e:
        logger.error(f"Error checking tray icon status: {str(e)}")
        return False
    
    return True

# Modified main function to conditionally start hidden or visible
def main():
    global _window, _tray_icon
    
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Determine the initial URL before creating the window
    initial_url = check_existing_user_data()
    logger.info(f"Initial URL will be: {initial_url}")
    
    logger.info("Starting WebView window")
    
    try:
        initialize_indicator(args.port)
        # Check if we should start minimized (only when --background flag is used)
        start_hidden = args.background
        logger.info(f"Window will start {'hidden' if start_hidden else 'visible'}")
        
        # Create window with conditional hidden status
        _window = pywebview.create_window(
            title=args.title,
            url=initial_url, #"https://hevolve.hertzai.com",
            width=args.width,
            height=args.height,
            resizable=True,
            frameless=False,
            hidden=start_hidden,  # Only start hidden when --background is set
            text_select=True,
            easy_drag=True,
            background_color='#000000'
        )
        
        logger.info(f"Window created successfully. Hidden: {start_hidden}")
        
        # Set up the system tray first before setting up events
        _tray_icon = setup_system_tray(_window)
        logger.info(f"System tray setup result: {_tray_icon is not None}")
        
        # Add direct event handlers
        _window.events.closed += on_closed
        _window.events.minimized += on_minimized
        logger.info("Event handlers connected directly")
        
        # Apply window theme
        if sys.platform == "win32":
            set_window_theme_attribute(_window)
            apply_dark_mode_to_all_windows()
        
        # Run the main loop to ensure tray icon is active
        # Add a thread to periodically check system tray status
        def monitor_tray():
            while True:
                time.sleep(5)  # Check every 5 seconds
                ensure_system_tray_running()
        
        monitor_thread = threading.Thread(target=monitor_tray, daemon=True)
        monitor_thread.start()
        
        # Start webview
        logger.info("Starting webview")
        if sys.platform == "win32":
            try:
                # Check if we can use winforms
                import webview.platforms.winforms
                pywebview.start(gui="winforms")
            except Exception as e:
                logger.error(f"Could not use winforms: {str(e)}")
                pywebview.start()
        else:
            pywebview.start()
        
    except Exception as e:
        logger.error(f"Error starting WebView: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)

# Modify the main script initialization
if __name__ == "__main__":
    try:
        logger.info("Starting HevolveAi Agent Companion GUI Application")
        logger.info(f"Arguments: {sys.argv}")
        
        # Ensure we're in the right directory when started from registry
        ensure_working_directory()
        
        # Add a small delay when started in background mode
        if args.background:
            logger.info("Background mode enabled, adding startup delay")
            time.sleep(3)  # Short delay to let system services initialize
        
        # Initialize tray icon to None
        _tray_icon = None
        _window = None
        
        # Hide console window in background mode
        if sys.platform == "win32" and args.background:
            try:
                ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
                logger.info("Console window hidden in background mode")
            except Exception as e:
                logger.error(f"Failed to hide console window: {str(e)}")
        
        # Run main function
        main()
    except Exception as e:
        logger.error(f"Application crashed: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Create a visible error log if something went wrong at startup
        try:
            error_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'HevolveAi Agent Companion', 'logs')
            os.makedirs(error_dir, exist_ok=True)
            error_file = os.path.join(error_dir, 'startup_error.log')
            with open(error_file, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Startup Error: {str(e)}\n")
                f.write(traceback.format_exc())
        except:
            pass
            
        sys.exit(1)

"""
curl -X POST http://localhost:5000/api/storage/set \
  -H "Content-Type: application/json" \
  -d '{
    "agentname": "AgentName",
    "email": "test@hertzai.com",
    "access_token": "encryptedjwttoken",
    "user_id": "10077"
  }

curl -X GET http://localhost:5000/api/storage/get/email_address
curl -X GET http://localhost:5000/api/storage/get/user_id
curl -X GET http://localhost:5000/api/storage/get/access_token
  
  """