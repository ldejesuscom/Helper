import os
import random
import string
from dotenv import load_dotenv
import PureCloudPlatformClientV2
from PureCloudPlatformClientV2.apis import NotificationsApi
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

# Store call counts per trunk
call_counts = defaultdict(lambda: {"inbound": 0, "outbound": 0})

# Generate random correlation ID
def generate_correlation_id(length=12):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choices(characters, k=length))

# Authenticate
def authenticate():
    try:
        api_client = PureCloudPlatformClientV2.api_client.ApiClient()
        api_client.host = ENVIRONMENT
        auth_token = api_client.get_client_credentials_token(CLIENT_ID, CLIENT_SECRET, timeout=10)
        logger.info("Authentication successful")
        return api_client, auth_token
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        raise

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
        data = json.loads(message)
        event_body = data.get("eventBody", {})
        trunk_id = event_body.get("trunk", {}).get("id")
        calls = event_body.get("calls", {})
        
        if trunk_id:
            inbound_count = calls.get("inboundCallCount", 0)
            outbound_count = calls.get("outboundCallCount", 0)
            call_counts[trunk_id]["inbound"] = inbound_count
            call_counts[trunk_id]["outbound"] = outbound_count
            logger.info(f"Updated call counts for trunk {trunk_id}: Inbound={inbound_count}, Outbound={outbound_count}")
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
            ws.send(json.dumps({"id": "ping"}))
            logger.debug("Sent keep-alive ping")
        except Exception as e:
            logger.error(f"Keep-alive failed: {e}")
            break

# WebSocket runner
def run_websocket():
    while True:
        try:
            api_client, _ = authenticate()
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

            ws_thread = threading.Thread(target=ws.run_forever)
            ws_thread.daemon = True
            ws_thread.start()

            keep_alive_thread = threading.Thread(target=keep_alive, args=(ws,))
            keep_alive_thread.daemon = True
            keep_alive_thread.start()

            ws_thread.join()
        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)

# Dash dashboard setup
app = Dash(__name__)

def generate_trunk_counters():
    counters = []
    for trunk_id in call_counts.keys():
        counters.append(
            html.Div([
                html.H3(f"Trunk: {trunk_id}", style={"fontSize": "18px", "marginBottom": "5px"}),
                html.Div(f"Inbound Calls: {call_counts[trunk_id]['inbound']}", style={"color": "blue", "marginLeft": "20px"}),
                html.Div(f"Outbound Calls: {call_counts[trunk_id]['outbound']}", style={"color": "green", "marginLeft": "20px"})
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

    app.run_server(debug=True, host="0.0.0.0", port=8050)