import os
from dotenv import load_dotenv
import PureCloudPlatformClientV2
from PureCloudPlatformClientV2.apis import NotificationsApi
from pprint import pprint
import websocket
import json
import threading
import time

# Load environment variables from .env file
load_dotenv()

# Import trunk IDs from trunkID.py
from trunkID import trunk_ids

# Configuration
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REGION = "us-east-1"  # Adjust based on your Genesys Cloud region (e.g., us-east-1, eu-west-1)

# Set the environment based on region
ENVIRONMENT = f"https://api.{REGION}.pure.cloud"

# Authenticate and get an access token
def authenticate():
    api_client = PureCloudPlatformClientV2.api_client.ApiClient()
    api_client.host = ENVIRONMENT
    auth_token = api_client.get_client_credentials_token(CLIENT_ID, CLIENT_SECRET)
    return api_client, auth_token

# Create a notification channel
def create_notification_channel(api_client):
    notifications_api = NotificationsApi(api_client)
    channel = notifications_api.post_notifications_channels()
    return channel

# WebSocket message handler
def on_message(ws, message):
    data = json.loads(message)
    pprint(data)  # Print the received trunk metrics data

def on_error(ws, error):
    print(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"WebSocket Closed: {close_status_code} - {close_msg}")

def on_open(ws):
    print("WebSocket connection opened")
    # Subscribe to trunk metrics when the connection opens
    subscribe_to_trunk_metrics(ws)

# Subscribe to trunk metrics
def subscribe_to_trunk_metrics(ws):
    for trunk_id in trunk_ids:
        topic = f"v2.telephony.providers.edges.trunks.{trunk_id}.metrics"
        subscription = {
            "id": "subscribe",
            "channel": "websocket",
            "topics": [topic]
        }
        ws.send(json.dumps(subscription))
        print(f"Subscribed to trunk metrics for trunk ID: {trunk_id}")

# Keep the WebSocket alive
def keep_alive(ws):
    while True:
        time.sleep(30)  # Send a ping every 30 seconds
        ws.send(json.dumps({"id": "ping"}))

# Main function to run the WebSocket
def run_websocket():
    api_client, _ = authenticate()
    channel = create_notification_channel(api_client)

    # Extract the WebSocket URI from the channel
    ws_uri = channel.connect_uri
    print(f"WebSocket URI: {ws_uri}")

    # Set up WebSocket connection
    ws = websocket.WebSocketApp(
        ws_uri,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Run the WebSocket in a separate thread
    ws_thread = threading.Thread(target=ws.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, args=(ws,))
    keep_alive_thread.daemon = True
    keep_alive_thread.start()

    # Keep the main thread running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
        ws.close()

if __name__ == "__main__":
    run_websocket()