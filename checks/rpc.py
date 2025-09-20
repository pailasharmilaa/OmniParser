"""rpc.py"""
from autobahn.twisted.wamp import ApplicationSession, ApplicationRunner
from twisted.internet.defer import inlineCallbacks
import traceback
 
class OmniToolClient(ApplicationSession):
    @inlineCallbacks
    def onJoin(self, details):
        print("Connected to WebSocket")
       
        # Example payload for general RPC
        payload = {
            "parent_request_id": "Parent_request_id_here",
            "user_id": "00000", # Your User_ID
            "prompt_id": "65",
            "instruction_to_vlm_agent": "Open web browser and search for ipl points table",
            "os_to_control": "Windows",
            "actions_available_in_os": [],
            "max_ETA_in_seconds": 300,
            "langchain_server": None
        }
 
        user_id = payload["user_id"]
        prompt_id = payload["prompt_id"]
        
        try:
            # Call the general RPC function
            print("Calling general RPC endpoint...")
            response1 = yield self.call(f"com.hertzai.hevolve.action", payload)
            print("General RPC Response:", response1)
            
            # Call the user-specific RPC function with prompt_id
            user_specific_uri = f"com.hertzai.hevolve.action.{user_id}"
            print(f"Calling user-specific RPC endpoint: {user_specific_uri}")
            
            # From examining handleAction, it expects:
            # - prompt_id in args[0] object 
            # - action in kwargs
            action_payload = [{"prompt_id": prompt_id}]
            action_kwargs = {"action": "execute_instruction"}
            
            response2 = yield self.call(user_specific_uri, 
                                       *action_payload,  # Unpack list as positional args
                                       **action_kwargs)  # Unpack dict as keyword args
            
            print(f"User-specific RPC Response: {response2}")
            
        except Exception as e:
            print(f"RPC call failed: {e}")
            traceback.print_exc()
 
        self.leave()
 
    def onDisconnect(self):
        print("Disconnected from WebSocket")
        from twisted.internet import reactor
        reactor.stop()
 
if __name__ == "__main__":
    # Connect to the WebSocket server
    websocket_url = "wss://azurekong.hertzai.com:8445/wss"  # Replace with actual URL
    realm = "realm1"  # Replace with actual realm
   
    runner = ApplicationRunner(websocket_url, realm)
    runner.run(OmniToolClient)