import os
import random
import string
from dotenv import load_dotenv
import PureCloudPlatformClientV2
from PureCloudPlatformClientV2.apis import NotificationsApi, TelephonyProvidersEdgeApi
import websocket
import json
import threading
import time
import logging
from collections import defaultdict
from dash import Dash, html, dcc, Input, Output

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(override=True)

# Import trunk IDs
from trunkID import trunk_ids

# Configuration
CLIENT_ID = os.getenv("PROD2_CLIENT_ID")
CLIENT_SECRET = os.getenv("PROD2_CLIENT_SECRET")
REGION = PureCloudPlatformClientV2.PureCloudRegionHosts.us_east_2
ENVIRONMENT = REGION.get_api_host()

# Store call counts per trunkbase name and per trunk ID, plus mapping of trunk ID to trunkbase name
call_counts = defaultdict(lambda: {"inbound": 0, "outbound": 0})  # Pre-populated totals (not updated directly)
trunk_counts = defaultdict(lambda: {"inbound": 0, "outbound": 0})  # Latest counts per trunk_id
trunk_id_to_base_map = {}  # Map trunk IDs to trunkbase names

# Generate random correlation ID
def generate_correlation_id(length=12):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choices(characters, k=length))

# Authenticate
def authenticate():
    try:
        api_client = PureCloudPlatformClientV2.api_client.ApiClient()
        api_client.host = ENVIRONMENT
        auth_token = api_client.get_client_credentials_token(CLIENT_ID, CLIENT_SECRET)
        logger.info("Authentication successful")
        return api_client, auth_token
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        raise

# Fetch trunkbase names and pre-populate call_counts
def fetch_trunk_names(api_client):
    try:
        telephony_api = TelephonyProvidersEdgeApi(api_client)
        for trunk_id in trunk_ids:
            trunk = telephony_api.get_telephony_providers_edges_trunk(trunk_id)
            trunkbase_name = trunk.trunk_base.name
            trunk_id_to_base_map[trunk_id] = trunkbase_name
            logger.info(f"Fetched trunkbase name: {trunkbase_name} for ID: {trunk_id}")
        
        # Pre-populate call_counts with unique trunkbase names
        unique_trunkbase_names = set(trunk_id_to_base_map.values())
        for trunkbase_name in unique_trunkbase_names:
            call_counts[trunkbase_name]  # Trigger defaultdict to initialize with 0s
            logger.info(f"Pre-populated call_counts for trunkbase: {trunkbase_name}")
    except Exception as e:
        logger.error(f"Failed to fetch trunkbase names: {e}")

# Create notification channel
def create_notification_channel(api_client):
    try:
        notifications_api = NotificationsApi(api_client)
        channel = notifications_api.post_notifications_channels()
        logger.info("Notification channel created")
        return channel
    except Exception as e:
        logger.error(f"Failed to create notification channel: {e}")
        raise

# WebSocket message handler
def on_message(ws, message):
    try:
        logger.debug(message)
        data = json.loads(message)
        event_body = data.get("eventBody", {})
        trunk_id = event_body.get("trunk", {}).get("id")
        calls = event_body.get("calls", {})
        
        if trunk_id and trunk_id in trunk_id_to_base_map:
            trunkbase_name = trunk_id_to_base_map[trunk_id]
            inbound_count = calls.get("inboundCallCount", 0)
            outbound_count = calls.get("outboundCallCount", 0)
            # Update latest counts for this trunk_id
            trunk_counts[trunk_id]["inbound"] = inbound_count
            trunk_counts[trunk_id]["outbound"] = outbound_count
            logger.info(f"Updated trunk counts for trunk ID {trunk_id} (trunkbase {trunkbase_name}): Inbound={inbound_count}, Outbound={outbound_count}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode message: {e}")

def on_error(ws, error):
    logger.error(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.warning(f"WebSocket Closed: {close_status_code} - {close_msg}")

def on_open(ws):
    logger.info("WebSocket connection opened")
    subscribe_to_trunk_metrics(ws)

# Subscribe to trunk metrics
def subscribe_to_trunk_metrics(ws):
    correlation_id = generate_correlation_id()
    for trunk_id in trunk_ids:
        topic = f"v2.telephony.providers.edges.trunks.{trunk_id}.metrics"
        subscription = {
            "message": "subscribe",
            "topics": [topic],
            "correlationId": correlation_id
        }
        ws.send(json.dumps(subscription))
        logger.info(f"Subscribed to trunk metrics for trunk ID: {trunk_id} (Correlation ID: {correlation_id})")

# Keep WebSocket alive
def keep_alive(ws):
    while True:
        time.sleep(30)
        try:
            ws.send(json.dumps({"message": "ping"}))
            logger.debug("Sent keep-alive ping")
        except Exception as e:
            logger.error(f"Keep-alive failed: {e}")
            break

# WebSocket runner with connection management
def run_websocket():
    api_client = None
    channel = None
    ws = None
    
    while True:
        try:
            if api_client is None:
                api_client, _ = authenticate()
                fetch_trunk_names(api_client)  # Fetch trunkbase names and pre-populate after authentication
            
            if channel is None:
                channel = create_notification_channel(api_client)
                ws_uri = channel.connect_uri
                logger.info(f"WebSocket URI: {ws_uri}")
                
                ws = websocket.WebSocketApp(
                    ws_uri,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close
                )
                
                ws.run_forever()
                
                # Note: No separate threading as per your update; keep_alive is not used here

        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in 5 seconds...")
            api_client = None
            channel = None
            time.sleep(5)

# Dash dashboard setup
app = Dash(__name__)

def generate_trunk_counters():
    counters = []
    # Compute sums for each trunkbase_name based on trunk_counts
    for trunkbase_name in call_counts.keys():  # Use pre-populated keys to ensure all trunks are shown
        total_inbound = 0
        total_outbound = 0
        # Sum counts for all trunk_ids mapped to this trunkbase_name
        for trunk_id, base_name in trunk_id_to_base_map.items():
            if base_name == trunkbase_name:
                total_inbound += trunk_counts[trunk_id]["inbound"]
                total_outbound += trunk_counts[trunk_id]["outbound"]
        
        counters.append(
            html.Div([
                html.H3(f"Trunk: {trunkbase_name}", style={"fontSize": "18px", "marginBottom": "5px"}),
                html.Div(f"Inbound Calls: {total_inbound}", style={"color": "blue", "marginLeft": "20px"}),
                html.Div(f"Outbound Calls: {total_outbound}", style={"color": "green", "marginLeft": "20px"})
            ], style={"border": "1px solid #ccc", "padding": "10px", "margin": "10px", "borderRadius": "5px"})
        )
    return counters

app.layout = html.Div([
    html.H1("Trunk Metrics Dashboard", style={"textAlign": "center", "marginBottom": "20px"}),
    html.Div(id="trunk-counters"),
    dcc.Interval(id="interval-component", interval=5*1000, n_intervals=0)  # Update every 5 seconds
])

@app.callback(
    Output("trunk-counters", "children"),
    Input("interval-component", "n_intervals")
)
def update_dashboard(n):
    return generate_trunk_counters()

# Main execution
if __name__ == "__main__":
    websocket_thread = threading.Thread(target=run_websocket)
    websocket_thread.daemon = True
    websocket_thread.start()

    app.run_server(debug=False, host="0.0.0.0", port=8050)
