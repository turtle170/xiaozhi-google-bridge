# main.py - XIAOZHI MCP SERVER v3.0 - TRIPLE THREAT
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
from dotenv import load_dotenv
import re

# ================= LOAD ENVIRONMENT VARIABLES =================
load_dotenv()

# Get configuration from environment variables
XIAOZHI_WS = os.environ.get("XIAOZHI_WS")
API_KEY = os.environ.get("API_KEY", "")
CSE_ID = os.environ.get("CSE_ID", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Validate critical configuration
if not XIAOZHI_WS:
    print("❌ ERROR: XIAOZHI_WS environment variable is not set!")
    print("   Go to Render.com dashboard → Environment → Add XIAOZHI_WS")
    sys.exit(1)

# ================= LOGGING SETUP =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ================= GOOGLE SEARCH (10 RESULTS!) =================
def google_search(query, max_results=10):  # CHANGED TO 10!
    """Perform Google Custom Search - NOW WITH 10 RESULTS!"""
    try:
        if not query or not query.strip():
            return "Please provide a search query."
        
        logger.info(f"🔍 Searching Google for: '{query}' (max: {max_results} results)")
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": API_KEY,
            "cx": CSE_ID,
            "q": query,
            "num": max_results,  # NOW 10 RESULTS!
            "safe": "active"
        }
        
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown Google API error")
            logger.error(f"Google API error: {error_msg}")
            return f"Google Search Error: {error_msg}"
        
        if "items" not in data or len(data["items"]) == 0:
            return f"No results found for '{query}'. Try different keywords."
        
        items = data["items"][:max_results]
        results = []
        
        for i, item in enumerate(items, 1):
            title = item.get('title', 'No title')
            link = item.get('link', 'No link')
            snippet = item.get('snippet', 'No description available')
            
            result_text = f"{i}. **{title}**\n   🔗 {link}\n   📝 {snippet}"
            results.append(result_text)
        
        formatted_results = "\n\n".join(results)
        logger.info(f"✅ Found {len(items)} Google results for '{query}'")
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

# ================= WIKIPEDIA SEARCH =================
def wikipedia_search(query, max_results=3):
    """Search Wikipedia and return summaries - FREE, NO API KEY!"""
    try:
        if not query or not query.strip():
            return "Please provide a search query."
        
        logger.info(f"📚 Searching Wikipedia for: '{query}'")
        
        url = "https://en.wikipedia.org/w/api.php"
        
        # First: Search for pages
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "utf8": 1,
            "srwhat": "text"
        }
        
        response = requests.get(url, params=search_params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if "query" not in data or "search" not in data["query"]:
            return f"No Wikipedia articles found for '{query}'."
        
        search_results = data["query"]["search"]
        
        if not search_results:
            return f"No Wikipedia articles found for '{query}'. Try different keywords."
        
        page_ids = [str(item["pageid"]) for item in search_results[:max_results]]
        
        # Get detailed info for each page
        extract_params = {
            "action": "query",
            "format": "json",
            "pageids": "|".join(page_ids),
            "prop": "extracts|info",
            "exintro": True,
            "explaintext": True,
            "inprop": "url",
            "exchars": 500
        }
        
        extract_response = requests.get(url, params=extract_params, timeout=10)
        extract_response.raise_for_status()
        extract_data = extract_response.json()
        
        pages = extract_data.get("query", {}).get("pages", {})
        
        results = []
        for i, page_id in enumerate(page_ids, 1):
            page = pages.get(page_id)
            if not page:
                continue
                
            title = page.get("title", "Unknown")
            extract = page.get("extract", "No summary available.")
            page_url = page.get("fullurl", f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")
            
            if extract:
                extract = re.sub(r'\[\d+\]', '', extract)
                if len(extract) > 400:
                    extract = extract[:400] + "..."
            
            result_text = f"{i}. **{title}**\n   🌐 {page_url}\n   📖 {extract}"
            results.append(result_text)
        
        if not results:
            return f"Could not retrieve Wikipedia content for '{query}'."
        
        formatted_results = "\n\n".join(results)
        logger.info(f"✅ Found {len(results)} Wikipedia results for '{query}'")
        return formatted_results
        
    except requests.exceptions.Timeout:
        logger.error("Wikipedia search timeout")
        return "Wikipedia search timeout. Please try again."
    except requests.exceptions.RequestException as e:
        logger.error(f"Wikipedia network error: {e}")
        return f"Network error: {str(e)}"
    except json.JSONDecodeError:
        logger.error("Invalid JSON response from Wikipedia")
        return "Error parsing Wikipedia results."
    except Exception as e:
        logger.error(f"Unexpected error in wikipedia_search: {e}")
        return "An unexpected error occurred during Wikipedia search."

# ================= OPENAI/CHATGPT FUNCTION =================
def ask_openai(query, model="gpt-3.5-turbo", max_tokens=800):
    """Query OpenAI's ChatGPT - AI POWER! 🤖"""
    try:
        if not query or not query.strip():
            return "Please provide a question or prompt."
        
        logger.info(f"🤖 Querying OpenAI: '{query[:50]}...'")
        
        if not OPENAI_API_KEY:
            return "OpenAI API key not configured. Please add OPENAI_API_KEY environment variable."
        
        # OpenAI API endpoint
        url = "https://api.openai.com/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful, knowledgeable assistant. Provide clear, accurate, and concise answers."},
                {"role": "user", "content": query}
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "top_p": 0.9
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        answer = result["choices"][0]["message"]["content"]
        
        logger.info(f"✅ OpenAI response received ({len(answer)} chars)")
        return answer
        
    except requests.exceptions.Timeout:
        logger.error("OpenAI request timeout")
        return "OpenAI request timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.error(f"OpenAI API error: {e}")
        if response.status_code == 401:
            return "Invalid OpenAI API key. Please check your API key."
        elif response.status_code == 429:
            return "Rate limit exceeded or out of credits. Check your OpenAI account."
        return f"OpenAI API error: {str(e)}"
    except (KeyError, IndexError) as e:
        logger.error(f"OpenAI response parsing error: {e}")
        return "Error parsing OpenAI response."
    except Exception as e:
        logger.error(f"Unexpected OpenAI error: {e}")
        return "An unexpected error occurred with OpenAI."

# ================= MCP PROTOCOL HANDLER =================
class MCPProtocolHandler:
    """Handles MCP protocol messages for 3 tools."""
    
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
                    "name": "super-search-server",
                    "version": "3.0.0"
                }
            }
        }
    
    @staticmethod
    def handle_tools_list(message_id):
        """Handle tools/list request - 3 POWERFUL TOOLS!"""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "tools": [
                    {
                        "name": "google_search",
                        "description": "Search Google for information (returns TOP 10 results)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "What to search on Google"
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "wikipedia_search",
                        "description": "Search Wikipedia for factual information and summaries",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "What to search on Wikipedia"
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "ask_ai",
                        "description": "Ask AI questions using OpenAI's ChatGPT (intelligent answers)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Your question or prompt for AI"
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
        """Handle ping request."""
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {}
        }
    
    @staticmethod
    def handle_tools_call(message_id, params):
        """Handle tools/call request - ALL 3 TOOLS!"""
        call_id = params.get("callId", "")
        tool_name = params.get("name", "")
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
        
        # Route to appropriate function
        if tool_name == "google_search":
            search_results = google_search(query, max_results=10)  # 10 RESULTS!
            response_text = f"## 🔍 Google Search Results (Top 10)\n\n**Query:** {query}\n\n{search_results}"
            
        elif tool_name == "wikipedia_search":
            search_results = wikipedia_search(query)
            response_text = f"## 📚 Wikipedia Search Results\n\n**Query:** {query}\n\n{search_results}"
            
        elif tool_name == "ask_ai":  # OPENAI CHATGPT!
            ai_response = ask_openai(query)
            response_text = f"## 🤖 AI Assistant (ChatGPT)\n\n**Your Question:** {query}\n\n{ai_response}"
            
        else:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}"
                }
            }
        
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": response_text
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
    """Main WebSocket bridge with automatic reconnection."""
    reconnect_delay = 2
    max_reconnect_delay = 60
    
    while True:
        try:
            logger.info(f"🔄 Connecting to Xiaozhi MCP...")
            
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
                max_size=10 * 1024 * 1024
            ) as websocket:
                logger.info("✅ Connected to Xiaozhi MCP")
                reconnect_delay = 2
                
                async for raw_message in websocket:
                    try:
                        message_data = json.loads(raw_message)
                        message_id = message_data.get("id")
                        method = message_data.get("method", "")
                        params = message_data.get("params", {})
                        
                        logger.debug(f"📥 Received: {method} (id: {message_id})")
                        
                        response = None
                        
                        if method == "ping":
                            response = MCPProtocolHandler.handle_ping(message_id)
                            logger.debug("🔄 Responded to ping")
                            
                        elif method == "initialize":
                            response = MCPProtocolHandler.handle_initialize(message_id)
                            logger.info("✅ Sent initialization response")
                            
                        elif method == "tools/list":
                            response = MCPProtocolHandler.handle_tools_list(message_id)
                            logger.info("✅ Sent tools list (3 powerful tools!)")
                            
                        elif method == "tools/call":
                            response = MCPProtocolHandler.handle_tools_call(message_id, params)
                            tool_name = params.get("name", "unknown")
                            logger.info(f"✅ Processed {tool_name} for query")
                            
                        elif method == "notifications/initialized":
                            logger.debug("📢 Client initialized notification")
                            continue
                            
                        else:
                            logger.warning(f"⚠️ Unknown method: {method}")
                            response = MCPProtocolHandler.handle_error(message_id, method)
                        
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
        <title>Xiaozhi MCP Server v3.0</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                      color: white; padding: 2rem; border-radius: 10px; margin-bottom: 2rem; }}
            .status {{ background: #f8f9fa; border-left: 4px solid #28a745; padding: 1rem; margin: 1rem 0; }}
            .tool-card {{ background: white; border: 1px solid #e0e0e0; border-radius: 8px; 
                         padding: 1rem; margin: 1rem 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .google {{ border-top: 4px solid #4285F4; }}
            .wikipedia {{ border-top: 4px solid #636466; }}
            .openai {{ border-top: 4px solid #10a37f; }}
            .badge {{ display: inline-block; padding: 0.25em 0.6em; font-size: 0.75em; font-weight: bold; 
                     border-radius: 10px; margin-right: 0.5em; color: white; }}
            .google-badge {{ background: #4285F4; }}
            .wiki-badge {{ background: #636466; }}
            .openai-badge {{ background: #10a37f; }}
            .feature {{ margin: 0.5em 0; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP Server v3.0</h1>
            <p>Triple Threat: Google (10 results) + Wikipedia + OpenAI/ChatGPT</p>
        </div>
        
        <div class="status">
            <h2>✅ Server Status: <strong>RUNNING</strong></h2>
            <p>Uptime: {hours}h {minutes}m {seconds}s</p>
        </div>
        
        <h2>🔧 Available AI Tools</h2>
        
        <div class="tool-card google">
            <h3><span class="badge google-badge">Google</span> google_search</h3>
            <div class="feature">✅ <strong>10 search results</strong> (upgraded from 3!)</div>
            <div class="feature">✅ Web search with snippets and links</div>
            <div class="feature">✅ Free tier: 100 searches/day</div>
            <p><strong>Example:</strong> "Search for Python tutorials"</p>
        </div>
        
        <div class="tool-card wikipedia">
            <h3><span class="badge wiki-badge">Wikipedia</span> wikipedia_search</h3>
            <div class="feature">✅ Factual summaries from Wikipedia</div>
            <div class="feature">✅ <strong>Completely free</strong> - no API key needed!</div>
            <div class="feature">✅ Direct links to articles</div>
            <p><strong>Example:</strong> "Search Wikipedia for machine learning"</p>
        </div>
        
        <div class="tool-card openai">
            <h3><span class="badge openai-badge">OpenAI</span> ask_ai</h3>
            <div class="feature">✅ <strong>ChatGPT-powered AI assistant</strong></div>
            <div class="feature">✅ Intelligent answers to any question</div>
            <div class="feature">✅ GPT-3.5 Turbo model (fast & capable)</div>
            <p><strong>Example:</strong> "Explain quantum physics simply"</p>
            <p><em>Requires: OPENAI_API_KEY environment variable</em></p>
        </div>
        
        <h2>📡 Server Information</h2>
        <ul>
            <li><strong>Version:</strong> 3.0.0 (Triple Threat Edition)</li>
            <li><strong>Tools:</strong> 3 powerful search/chat tools</li>
            <li><strong>Google Results:</strong> 10 (upgraded!)</li>
            <li><strong>Hosting:</strong> Render.com (24/7 uptime)</li>
            <li><strong>Protocol:</strong> MCP (Model Context Protocol)</li>
        </ul>
        
        <h2>🎯 Test Endpoints</h2>
        <ul>
            <li><a href="/health">Health Check</a></li>
            <li><a href="/test/google">Test Google Search</a></li>
            <li><a href="/test/wikipedia">Test Wikipedia Search</a></li>
            <li><a href="/test/openai">Test OpenAI (if key configured)</a></li>
        </ul>
        
        <p><em>Last updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}</em></p>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "service": "xiaozhi-mcp-server",
        "version": "3.0.0",
        "tools": ["google_search (10 results)", "wikipedia_search", "ask_ai (OpenAI)"],
        "openai_configured": bool(OPENAI_API_KEY)
    }), 200

@app.route('/test/google')
def test_google():
    """Test Google search."""
    query = "artificial intelligence"
    try:
        results = google_search(query, max_results=2)
        return jsonify({
            "success": True,
            "engine": "Google",
            "query": query,
            "max_results": 10,
            "sample": results[:300] + "..." if len(results) > 300 else results
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/test/wikipedia')
def test_wikipedia():
    """Test Wikipedia search."""
    query = "science"
    try:
        results = wikipedia_search(query, max_results=2)
        return jsonify({
            "success": True,
            "engine": "Wikipedia",
            "query": query,
            "free": True,
            "sample": results[:300] + "..." if len(results) > 300 else results
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/test/openai')
def test_openai():
    """Test OpenAI if configured."""
    if not OPENAI_API_KEY:
        return jsonify({
            "success": False,
            "error": "OPENAI_API_KEY not configured",
            "setup": "Add OPENAI_API_KEY to Render environment variables"
        }), 400
    
    query = "Explain artificial intelligence in one sentence"
    try:
        results = ask_openai(query, max_tokens=100)
        return jsonify({
            "success": True,
            "engine": "OpenAI ChatGPT",
            "query": query,
            "response": results
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

def run_web_server():
    """Run Flask web server."""
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

# ================= APPLICATION ENTRY POINT =================
async def main():
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("🚀 Starting Xiaozhi MCP Server v3.0")
    logger.info("🔍 Google (10 results) + 📚 Wikipedia + 🤖 OpenAI")
    logger.info("=" * 60)
    
    logger.info(f"📊 Configuration:")
    logger.info(f"   Google API: {'✅ Configured' if API_KEY and CSE_ID else '⚠️  Not configured'}")
    logger.info(f"   OpenAI API: {'✅ Configured' if OPENAI_API_KEY else '⚠️  Not configured (ask_ai will not work)'}")
    
    # Start Flask web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server started on http://0.0.0.0:3000")
    
    # Start MCP bridge
    logger.info("🔗 Starting MCP WebSocket bridge to Xiaozhi...")
    await mcp_bridge()

if __name__ == "__main__":
    try:
        import websockets
        import flask
        import requests
        
        asyncio.run(main())
        
    except ImportError as e:
        logger.error(f"❌ Missing required package: {e}")
        logger.info("💡 Install: pip install websockets flask requests python-dotenv")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\n👋 Server stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}", exc_info=True)
        sys.exit(1)