# pip install crossbarhttp3
from crossbarhttp import Client
import time
import datetime
import inspect
import json
# with open('config.json') as config_file:
#     config_data = json.load(config_file)
client = Client("http://aws_rasa.hertzai.com:8088/publish")
vm_name = 'general_purpose'
service = 'chatbot'
file_name = 'chatbot.py'
 
 
def exception_publish(message):
    result = client.publish(
        "com.hertzai.hevolve.action", message)

inp = {
            'parent_request_id': 'sommereqeuestidhere',
            'user_id': '00000', # Use your UserID here
            'prompt_id': '54',
            'instruction_to_vlm_agent': 'Open Microsoft Edge and open new tab and then search youtube for it in the search bar',
            'os_to_control': 'Windows',
            'actions_available_in_os': [],
            'max_ETA_in_seconds': 500,
            'langchain_server':True
        }
exception_publish(inp)
 
 