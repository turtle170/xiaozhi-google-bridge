import os
import asyncio
import json
import websockets
import requests
import logging
from flask import Flask, jsonify
import threading
import time
import sys

# ================= CONFIG FROM ENVIRONMENT =================
# These will be set in Render.com dashboard
XIAOZHI_WS = os.environ.get("XIAOZHI_WS", "")
API_KEY = os.environ.get("API_KEY", "")
CSE_ID = os.environ.get("CSE_ID", "")

# Validate critical configuration
if not XIAOZHI_WS:
    print("❌ ERROR: XIAOZHI_WS environment variable is not set!")
    print("   Go to Render.com dashboard → Environment → Add XIAOZHI_WS")
    sys.exit(1)

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ... [REST OF YOUR EXISTING CODE FOLLOWS, STARTING WITH google_search FUNCTION] ...
# ================= GOOGLE SEARCH FUNCTION =================
def google_search(query, max_results=3):
    """Perform Google Custom Search with robust error handling."""
    try:
        if not query or not query.strip():
            return "Please provide a search query."

        logger.info(f"Searching Google for: '{query}'")

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": API_KEY,
            "cx": CSE_ID,
            "q": query,
            "num": max_results,
            "safe": "active"
        }

        # Add timeout and error handling
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()

        data = response.json()

        # Check for search errors
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown Google API error")
            logger.error(f"Google API error: {error_msg}")
            return f"Google Search Error: {error_msg}"

        # Parse results
        if "items" not in data or len(data["items"]) == 0:
            return f"No results found for '{query}'. Try different keywords."

        items = data["items"][:max_results]
        results = []

        for i, item in enumerate(items, 1):
            title = item.get('title', 'No title')
            link = item.get('link', 'No link')
            snippet = item.get('snippet', 'No description available')

            result_text = f"{i}. **{title}**\n   {link}\n   {snippet}"
            results.append(result_text)

        formatted_results = "\n\n".join(results)
        logger.info(f"Found {len(items)} results for '{query}'")
        return formatted_results

    except requests.exceptions.Timeout:
        logger.error("Google search timeout")
        return "Search timeout. Please try again."
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error: {e}")
        return f"Network error: {str(e)}"
    except json.JSONDecodeError:
        logger.error("Invalid JSON response from Google")
        return "Error parsing search results."
    except Exception as e:
        logger.error(f"Unexpected error in google_search: {e}")
        return "An unexpected error occurred during search."

# ================= MCP PROTOCOL IMPLEMENTATION =================
class MCPProtocolHandler:
    """Handles MCP protocol messages according to specification."""

    @staticmethod
    def handle_initialize(message_id):
        """Handle initialization request."""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "google-search-server",
                    "version": "1.0.0"
                }
            }
        }

    @staticmethod
    def handle_tools_list(message_id):
        """Handle tools/list request."""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "tools": [
                    {
                        "name": "google_search",
                        "description": "Search Google for information and get the top results",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query (e.g., 'weather in Tokyo')"
                                }
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        }

    @staticmethod
    def handle_ping(message_id):
        """Handle ping request (MUST respond with pong)."""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {}  # Empty object for pong
        }

    @staticmethod
    def handle_tools_call(message_id, params):
        """Handle tools/call request."""
        call_id = params.get("callId", "")
        arguments = params.get("arguments", {})
        query = arguments.get("query", "").strip()

        if not query:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32602,
                    "message": "Missing required parameter: query"
                }
            }

        # Perform the search
        search_results = google_search(query)

        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"## Google Search Results\n\n**Query:** {query}\n\n{search_results}"
                    }
                ]
            }
        }

    @staticmethod
    def handle_error(message_id, method):
        """Handle unknown methods."""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }

# ================= MAIN MCP BRIDGE =================
async def mcp_bridge():
    """
    Main WebSocket bridge that connects to Xiaozhi MCP endpoint.
    Implements complete MCP protocol with automatic reconnection.
    """
    reconnect_delay = 2  # Start with 2 seconds
    max_reconnect_delay = 60

    while True:
        try:
            logger.info(f"🔄 Attempting connection to Xiaozhi MCP...")

            # Connect with conservative settings
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=None,      # Disable built-in pings (MCP handles them)
                ping_timeout=None,
                close_timeout=10,
                max_size=10 * 1024 * 1024  # 10MB max message size
            ) as websocket:
                logger.info("✅ Successfully connected to Xiaozhi MCP")
                reconnect_delay = 2  # Reset on successful connection

                # Main message processing loop
                async for raw_message in websocket:
                    try:
                        # Parse incoming message
                        message_data = json.loads(raw_message)
                        message_id = message_data.get("id")
                        method = message_data.get("method", "")
                        params = message_data.get("params", {})

                        logger.debug(f"📥 Received: {method} (id: {message_id})")

                        # Route to appropriate handler
                        response = None

                        if method == "ping":
                            response = MCPProtocolHandler.handle_ping(message_id)
                            logger.debug("🔄 Responded to ping")

                        elif method == "initialize":
                            response = MCPProtocolHandler.handle_initialize(message_id)
                            logger.info("✅ Sent initialization response")

                        elif method == "tools/list":
                            response = MCPProtocolHandler.handle_tools_list(message_id)
                            logger.info("✅ Sent tools list")

                        elif method == "tools/call":
                            response = MCPProtocolHandler.handle_tools_call(message_id, params)
                            logger.info(f"✅ Processed tools/call for query")

                        elif method == "notifications/initialized":
                            # Notifications don't require a response
                            logger.debug("📢 Client initialized notification")
                            continue

                        else:
                            logger.warning(f"⚠️ Unknown method: {method}")
                            response = MCPProtocolHandler.handle_error(message_id, method)

                        # Send response if needed
                        if response:
                            await websocket.send(json.dumps(response))
                            logger.debug(f"📤 Sent response for {method}")

                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Failed to parse JSON: {e}")
                    except KeyError as e:
                        logger.error(f"❌ Missing key in message: {e}")
                    except Exception as e:
                        logger.error(f"❌ Error processing message: {e}", exc_info=True)

        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"❌ Connection closed: Code {e.code}, Reason: {e.reason if e.reason else 'No reason'}")

            # Implement exponential backoff
            logger.info(f"⏳ Reconnecting in {reconnect_delay} seconds...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

        except ConnectionRefusedError:
            logger.error("❌ Connection refused. Is the endpoint reachable?")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

        except Exception as e:
            logger.error(f"❌ Unexpected connection error: {e}", exc_info=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

# ================= FLASK WEB SERVER =================
app = Flask(__name__)
server_start_time = time.time()

@app.route('/')
def index():
    """Main status page."""
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP Server</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                      color: white; padding: 2rem; border-radius: 10px; margin-bottom: 2rem; }}
            .status {{ background: #f8f9fa; border-left: 4px solid #28a745; padding: 1rem; margin: 1rem 0; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; margin: 1rem 0; }}
            code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP Server</h1>
            <p>Google Search Integration via Model Context Protocol</p>
        </div>

        <div class="status">
            <h2>✅ Server Status: <strong>RUNNING</strong></h2>
            <p>Uptime: {hours}h {minutes}m {seconds}s</p>
        </div>

        <h2>📡 Connection Information</h2>
        <ul>
            <li><strong>Protocol:</strong> MCP (Model Context Protocol)</li>
            <li><strong>Service:</strong> Google Custom Search API</li>
            <li><strong>WebSocket:</strong> Active connection to Xiaozhi AI</li>
        </ul>

        <div class="warning">
            <h3>⚠️ Important Notes</h3>
            <p>This server requires the following to be configured in Xiaozhi Console:</p>
            <ol>
                <li>MCP Endpoint: Your Replit WebSocket URL</li>
                <li>Tool Name: <code>google_search</code></li>
                <li>Parameter: <code>query</code> (string)</li>
            </ol>
        </div>

        <h2>🔍 Available Tool</h2>
        <pre><code>google_search(query: string) -> string
Description: Search Google for information and get the top results</code></pre>

        <p><em>Last updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}</em></p>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring services."""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "service": "xiaozhi-mcp-server",
        "version": "1.0.0"
    }), 200

@app.route('/api/test-search')
def test_search():
    """Test endpoint to verify Google Search API is working."""
    query = "test"
    try:
        results = google_search(query, max_results=1)
        return jsonify({
            "success": True,
            "query": query,
            "results": results[:500] + "..." if len(results) > 500 else results
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

def run_web_server():
    """Run Flask web server in a separate thread."""
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

# ================= APPLICATION ENTRY POINT =================
async def main():
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("🚀 Starting Xiaozhi MCP Server")
    logger.info("=" * 60)

    # Start Flask web server in background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server started on http://0.0.0.0:3000")
    logger.info("   • Status page: http://0.0.0.0:3000/")
    logger.info("   • Health check: http://0.0.0.0:3000/health")
    logger.info("   • Test search: http://0.0.0.0:3000/api/test-search")

    # Start MCP bridge
    logger.info("🔗 Starting MCP WebSocket bridge to Xiaozhi...")
    await mcp_bridge()

if __name__ == "__main__":
    try:
        # Verify critical imports
        import websockets
        import flask
        import requests

        # Run the application
        asyncio.run(main())

    except ImportError as e:
        logger.error(f"❌ Missing required package: {e}")
        logger.info("💡 Install dependencies with: pip install websockets flask requests")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\n👋 Server stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}", exc_info=True)
        sys.exit(1)