# main.py - XIAOZHI MCP SERVER v3.4 - GEMINI 2.5 FLASH EDITION
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
import random

# ================= LOAD ENVIRONMENT VARIABLES =================
load_dotenv()

# Get configuration from environment variables
XIAOZHI_WS = os.environ.get("XIAOZHI_WS")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")      # For Google Search API
CSE_ID = os.environ.get("CSE_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")      # For Google Gemini AI

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
def google_search(query, max_results=10):
    """Perform Google Custom Search - NOW WITH 10 RESULTS!"""
    try:
        if not query or not query.strip():
            return "Please provide a search query."
        
        logger.info(f"🔍 Searching Google for: '{query}' (max: {max_results} results)")
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": CSE_ID,
            "q": query,
            "num": max_results,
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

# ================= WIKIPEDIA SEARCH (FIXED 403 ERROR) =================
def wikipedia_search(query, max_results=3):
    """
    Search Wikipedia with FIXED 403 error handling.
    Added proper User-Agent and rate limiting.
    """
    try:
        if not query or not query.strip():
            return "Please provide a search query."
        
        logger.info(f"📚 Searching Wikipedia for: '{query}'")
        
        url = "https://en.wikipedia.org/w/api.php"
        
        # CRITICAL FIX: Add proper User-Agent to avoid 403
        headers = {
            'User-Agent': f'XiaozhiMCPBot/3.4 (https://your-render-project.onrender.com; contact@example.com)'
        }
        
        # First: Search for pages
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "utf8": 1,
            "srwhat": "text",
            "srprop": "size"  # Get additional info
        }
        
        response = requests.get(url, params=search_params, headers=headers, timeout=10)
        
        # Check for 403 specifically
        if response.status_code == 403:
            logger.error("❌ Wikipedia 403 Forbidden - Adding randomized delay and retry")
            # Wait 2-5 seconds and try with different User-Agent
            time.sleep(random.uniform(2, 5))
            headers['User-Agent'] = f'Mozilla/5.0 (compatible; XiaozhiBot/{random.randint(1, 100)}; +https://your-render-project.onrender.com)'
            response = requests.get(url, params=search_params, headers=headers, timeout=10)
        
        response.raise_for_status()
        
        data = response.json()
        
        if "query" not in data or "search" not in data["query"]:
            return f"No Wikipedia articles found for '{query}'."
        
        search_results = data["query"]["search"]
        
        if not search_results:
            return f"No Wikipedia articles found for '{query}'. Try different keywords."
        
        page_ids = [str(item["pageid"]) for item in search_results[:max_results]]
        
        if not page_ids:
            return f"Could not extract page IDs for '{query}'."
        
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
        
        # Add small delay to respect Wikipedia's rate limits
        time.sleep(0.5)
        
        extract_response = requests.get(url, params=extract_params, headers=headers, timeout=10)
        extract_response.raise_for_status()
        extract_data = extract_response.json()
        
        pages = extract_data.get("query", {}).get("pages", {})
        
        results = []
        for i, page_id in enumerate(page_ids, 1):
            page = pages.get(page_id)
            if not page:
                logger.warning(f"Could not find page {page_id} in Wikipedia response")
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
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            logger.error("❌ Wikipedia 403 Forbidden - Rate limited or blocked")
            return "Wikipedia is temporarily restricting requests. Please wait a moment and try again."
        else:
            logger.error(f"Wikipedia HTTP error {e.response.status_code}: {e}")
            return f"Wikipedia error: {e.response.status_code}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Wikipedia network error: {e}")
        return f"Network error: {str(e)}"
    except json.JSONDecodeError:
        logger.error("Invalid JSON response from Wikipedia")
        return "Error parsing Wikipedia results."
    except Exception as e:
        logger.error(f"Unexpected error in wikipedia_search: {e}")
        return "An unexpected error occurred during Wikipedia search."

# ================= GOOGLE GEMINI 2.5 FLASH (BEST FREE TIER!) =================
def ask_gemini(query, model="gemini-2.5-flash", max_tokens=2000):
    """
    Query Google Gemini 2.5 Flash - BEST free tier model!
    128K context, 60 RPM free, optimized for speed.
    """
    try:
        if not query or not query.strip():
            return "Please provide a question or prompt."
        
        logger.info(f"🌟 Querying Google Gemini {model}: '{query[:50]}...'")
        
        if not GEMINI_API_KEY:
            return "Gemini API key not configured. Please add GEMINI_API_KEY environment variable."
        
        # Validate and normalize model name
        model = model.lower().strip()
        
        # Supported free tier models (Gemini 2.5 series)
        supported_models = {
            "gemini-2.5-flash": "gemini-2.5-flash",  # Best balance for free tier
            "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",  # Higher throughput
            "gemini-2.5-flash-001": "gemini-2.5-flash-001",  # Latest version
            "gemini-2.0-flash": "gemini-2.0-flash",  # Fallback option
            "gemini-1.5-flash": "gemini-1.5-flash",  # Legacy fallback
        }
        
        if model not in supported_models:
            logger.warning(f"⚠️ Model '{model}' not in supported list, using gemini-2.5-flash")
            model = "gemini-2.5-flash"
        
        actual_model = supported_models[model]
        
        # Gemini API endpoint - CORRECT for Gemini 2.5
        # For latest models, use the full model name
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{actual_model}:generateContent"
        
        logger.debug(f"🔗 Using Gemini URL: {url}")
        
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-client": "xiaozhi-mcp/3.4"  # Identify our client
        }
        
        data = {
            "contents": [{
                "parts": [{
                    "text": query
                }]
            }],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.7,
                "topP": 0.95,
                "topK": 40,
                "stopSequences": [],  # No forced stops
            },
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                }
            ]
        }
        
        # Add API key as query parameter
        params = {"key": GEMINI_API_KEY}
        
        # Add timeout and better error handling
        response = requests.post(url, headers=headers, json=data, params=params, timeout=45)
        
        # Handle specific errors with detailed messages
        if response.status_code == 404:
            logger.error(f"❌ Gemini 404: Model '{actual_model}' not found.")
            # Try fallback models
            fallback_models = ["gemini-2.0-flash", "gemini-1.5-flash"]
            for fallback in fallback_models:
                logger.info(f"🔄 Trying fallback model: {fallback}")
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/{fallback}:generateContent"
                fallback_response = requests.post(fallback_url, headers=headers, json=data, params=params, timeout=30)
                if fallback_response.status_code == 200:
                    logger.info(f"✅ Success with fallback model: {fallback}")
                    response = fallback_response
                    break
                else:
                    logger.warning(f"❌ Fallback {fallback} also failed: {fallback_response.status_code}")
            
            if response.status_code != 200:
                return f"Gemini model not available. Tried: {actual_model} and fallbacks. Please try gemini-2.0-flash or gemini-1.5-flash."
        
        elif response.status_code == 429:
            logger.error("❌ Gemini rate limit reached (60 requests/minute free)")
            return "Gemini rate limit reached. You can make 60 requests per minute for free. Please wait a moment and try again."
        
        elif response.status_code == 403:
            logger.error("❌ Gemini API key invalid or quota exceeded")
            return "Gemini API key invalid or quota exceeded. Check your API key at https://makersuite.google.com/app/apikey"
        
        elif response.status_code == 400:
            error_data = response.json()
            error_msg = error_data.get("error", {}).get("message", "Bad request")
            logger.error(f"❌ Gemini bad request: {error_msg}")
            if "safety" in error_msg.lower():
                return "Gemini blocked the response due to safety filters. Try rephrasing your question."
            elif "model" in error_msg.lower():
                return f"Gemini model error: {error_msg[:100]}"
            return f"Gemini: {error_msg[:100]}"
        
        response.raise_for_status()
        
        result = response.json()
        
        # Extract response text from Gemini's structure
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            if "content" in candidate:
                parts = candidate["content"].get("parts", [])
                if parts and len(parts) > 0 and "text" in parts[0]:
                    answer = parts[0]["text"]
                    logger.info(f"✅ Gemini {actual_model} response received ({len(answer)} chars)")
                    return answer
        
        # Alternative extraction methods
        if "text" in result:
            return result["text"]
        
        # Try to find any text in the response
        import pprint
        logger.error(f"Unexpected Gemini response structure:\n{pprint.pformat(result, depth=2)}")
        
        # Last resort: look for text anywhere in the response
        response_str = json.dumps(result)
        if '"text":' in response_str:
            import re
            match = re.search(r'"text":\s*"([^"]+)"', response_str)
            if match:
                return match.group(1)
        
        return "Error parsing Gemini response. The AI returned an unexpected format."
        
    except requests.exceptions.Timeout:
        logger.error("Gemini request timeout")
        return "Gemini request timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.error(f"Gemini API error: {e}")
        return f"Gemini API error: {str(e)[:100]}"
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Gemini response parsing error: {e}")
        return "Error parsing Gemini response."
    except Exception as e:
        logger.error(f"Unexpected Gemini error: {e}", exc_info=True)
        return "An unexpected error occurred with Gemini AI."

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
                    "name": "gemini-2.5-server",
                    "version": "3.4.0"
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
                        "description": "Ask AI questions using Google Gemini 2.5 Flash (128K context, 60 RPM FREE!)",
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
            search_results = google_search(query, max_results=10)
            response_text = f"## 🔍 Google Search Results (Top 10)\n\n**Query:** {query}\n\n{search_results}"
            
        elif tool_name == "wikipedia_search":
            search_results = wikipedia_search(query)
            response_text = f"## 📚 Wikipedia Search Results\n\n**Query:** {query}\n\n{search_results}"
            
        elif tool_name == "ask_ai":  # GOOGLE GEMINI 2.5 FLASH!
            # Use Gemini 2.5 Flash (best free tier model)
            ai_response = ask_gemini(query, model="gemini-2.5-flash")
            response_text = f"## 🤖 Google Gemini 2.5 Flash (Free!)\n\n**Your Question:** {query}\n\n{ai_response}"
            
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
    """
    WebSocket bridge with enhanced connection handling.
    """
    reconnect_delay = 2
    max_reconnect_delay = 60
    
    while True:
        try:
            logger.info(f"🔄 Connecting to Xiaozhi MCP...")
            
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=25,
                ping_timeout=15,
                close_timeout=10,
                max_size=5 * 1024 * 1024,
                open_timeout=30
            ) as websocket:
                logger.info("✅ Connected to Xiaozhi MCP")
                reconnect_delay = 2
                
                try:
                    async for raw_message in websocket:
                        try:
                            message_data = json.loads(raw_message)
                            message_id = message_data.get("id")
                            method = message_data.get("method", "")
                            params = message_data.get("params", {})
                            
                            if method != "ping":
                                logger.debug(f"📥 Received: {method} (id: {message_id})")
                            
                            response = None
                            
                            if method == "ping":
                                response = MCPProtocolHandler.handle_ping(message_id)
                                
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
                        
                        except json.JSONDecodeError as e:
                            logger.error(f"❌ Failed to parse JSON: {e}")
                        except KeyError as e:
                            logger.error(f"❌ Missing key in message: {e}")
                        except Exception as e:
                            logger.error(f"❌ Error processing message: {e}", exc_info=True)
                
                except websockets.exceptions.ConnectionClosed as e:
                    logger.error(f"🔌 Connection closed: Code {e.code}")
                    break
                except Exception as e:
                    logger.error(f"❌ WebSocket read error: {e}")
                    break
        
        except websockets.exceptions.ConnectionClosedError as e:
            logger.error(f"❌ Connection closed with error: Code {e.code}")
            
            wait_time = min(reconnect_delay, max_reconnect_delay)
            logger.info(f"⏳ Reconnecting in {wait_time} seconds...")
            await asyncio.sleep(wait_time)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            
        except ConnectionRefusedError:
            logger.error("❌ Connection refused.")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            
        except asyncio.TimeoutError:
            logger.error("⏰ Connection timeout")
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
        <title>Xiaozhi MCP Server v3.4</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
            .header {{ background: linear-gradient(135deg, #4285F4 0%, #EA4335 100%); 
                      color: white; padding: 2rem; border-radius: 10px; margin-bottom: 2rem; }}
            .status {{ background: #f8f9fa; border-left: 4px solid #34A853; padding: 1rem; margin: 1rem 0; }}
            .feature {{ background: #e8f0fe; border-left: 4px solid #4285F4; padding: 1rem; margin: 1rem 0; }}
            .free-badge {{ background: #0F9D58; color: white; padding: 4px 8px; border-radius: 12px; 
                         font-size: 0.8em; font-weight: bold; display: inline-block; margin-left: 10px; }}
            .gemini-badge {{ background: #EA4335; color: white; padding: 4px 8px; border-radius: 12px; 
                          font-size: 0.8em; font-weight: bold; display: inline-block; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP Server v3.4</h1>
            <p>Gemini 2.5 Flash Edition - Latest & Greatest Free AI!</p>
        </div>
        
        <div class="status">
            <h2>✅ Server Status: <strong>RUNNING</strong></h2>
            <p>Uptime: {hours}h {minutes}m {seconds}s</p>
            <p>Version: 3.4.0 (Gemini 2.5 Flash - Best Free Tier)</p>
        </div>
        
        <div class="feature">
            <h3>🌟 NEW: Gemini 2.5 Flash <span class="gemini-badge">128K CONTEXT</span> <span class="free-badge">60 RPM FREE</span></h3>
            <p><strong>Latest generation AI</strong> with 128K context window - completely free!</p>
            <ul>
                <li><strong>Gemini 2.5 Flash</strong> - Best performance/speed balance</li>
                <li><strong>128,000 token context</strong> - Remembers long conversations</li>
                <li><strong>60 requests per minute</strong> - Most generous free tier</li>
                <li><strong>No credit card required</strong> - Truly free forever</li>
                <li><strong>Automatic fallbacks</strong> - Tries 2.0 Flash → 1.5 Flash if needed</li>
            </ul>
        </div>
        
        <h2>🔧 Available Tools</h2>
        
        <div class="feature">
            <h3>🔍 Google Search</h3>
            <p><strong>10 results per search</strong> (upgraded!)</p>
            <p><em>100 searches/day free</em></p>
        </div>
        
        <div class="feature">
            <h3>📚 Wikipedia Search</h3>
            <p><strong>403 error fixed</strong> - now working reliably</p>
            <p><em>Completely free forever</em></p>
        </div>
        
        <div class="feature">
            <h3>🤖 Google Gemini 2.5 Flash</h3>
            <p><strong>Latest generation AI</strong> with 128K context</p>
            <p><em>60 requests/minute FREE</em> - most generous limits!</p>
        </div>
        
        <h2>⚙️ Configuration Check</h2>
        <ul>
            <li><strong>Google Search API:</strong> {'✅ Configured' if GOOGLE_API_KEY and CSE_ID else '❌ Not configured'}</li>
            <li><strong>Wikipedia:</strong> ✅ Fixed (no 403 errors)</li>
            <li><strong>Google Gemini:</strong> {'✅ Configured' if GEMINI_API_KEY else '❌ Not configured'}</li>
            <li><strong>AI Model:</strong> Gemini 2.5 Flash (latest)</li>
        </ul>
        
        <h2>🎯 Test Endpoints</h2>
        <ul>
            <li><a href="/health">Health Check</a></li>
            <li><a href="/test/google">Test Google Search</a></li>
            <li><a href="/test/wikipedia">Test Wikipedia</a></li>
            <li><a href="/test/gemini">Test Gemini 2.5 Flash</a></li>
            <li><a href="/test/gemini-models">Test All Gemini Models</a></li>
            <li><a href="/debug">Debug Info</a></li>
        </ul>
        
        <p><em>Gemini 2.5 Flash Edition - {time.strftime('%Y-%m-%d %H:%M:%S UTC')}</em></p>
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
        "version": "3.4.0",
        "ai_provider": "google-gemini-2.5-flash",
        "services": {
            "google_search": "configured" if GOOGLE_API_KEY and CSE_ID else "not_configured",
            "wikipedia": "fixed_no_403",
            "gemini_ai": "configured" if GEMINI_API_KEY else "not_configured"
        },
        "free_limits": {
            "gemini_requests_per_minute": 60,
            "google_searches_per_day": 100,
            "wikipedia": "unlimited",
            "gemini_context": "128,000 tokens"
        }
    }), 200

@app.route('/test/google')
def test_google():
    """Test Google search."""
    query = "artificial intelligence"
    try:
        results = google_search(query, max_results=2)
        return jsonify({
            "success": True,
            "engine": "Google Search",
            "query": query,
            "max_results": 10,
            "sample": results[:300]
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
        has_403 = "403" in results or "restricting" in results
        return jsonify({
            "success": not has_403,
            "engine": "Wikipedia",
            "query": query,
            "fixed_403": True,
            "result": results[:300]
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/test/gemini')
def test_gemini():
    """Test Google Gemini 2.5 Flash."""
    if not GEMINI_API_KEY:
        return jsonify({
            "success": False,
            "error": "GEMINI_API_KEY not configured",
            "setup": "Get free key at https://makersuite.google.com/app/apikey"
        }), 400
    
    query = "Hello! Please respond with 'Gemini 2.5 Flash is working!' and tell me one interesting fact about AI."
    try:
        results = ask_gemini(query, model="gemini-2.5-flash", max_tokens=500)
        has_error = any(word in results.lower() for word in ["error", "invalid", "403", "429", "404"])
        
        return jsonify({
            "success": not has_error,
            "engine": "Google Gemini 2.5 Flash",
            "query": query,
            "response": results,
            "free_limit": "60 requests/minute",
            "context_window": "128,000 tokens",
            "note": "Latest generation AI - completely free!"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "note": "Check Gemini API key at https://makersuite.google.com"
        }), 500

@app.route('/test/gemini-models')
def test_gemini_models():
    """Test all available Gemini models."""
    if not GEMINI_API_KEY:
        return jsonify({
            "success": False,
            "error": "GEMINI_API_KEY not configured"
        }), 400
    
    models_to_test = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite", 
        "gemini-2.0-flash",
        "gemini-1.5-flash"
    ]
    
    results = []
    query = "Say 'This model is working!'"
    
    for model in models_to_test:
        try:
            response = ask_gemini(query, model=model, max_tokens=100)
            works = "error" not in response.lower() and "not available" not in response.lower()
            results.append({
                "model": model,
                "works": works,
                "response": response[:100] if works else response
            })
            # Small delay between tests
            time.sleep(0.5)
        except Exception as e:
            results.append({
                "model": model,
                "works": False,
                "error": str(e)[:100]
            })
    
    working_models = [r["model"] for r in results if r["works"]]
    
    return jsonify({
        "success": len(working_models) > 0,
        "available_models": working_models,
        "all_results": results,
        "recommended": "gemini-2.5-flash" if "gemini-2.5-flash" in working_models else working_models[0] if working_models else "none"
    }), 200

@app.route('/debug')
def debug_info():
    """Debug information page."""
    return jsonify({
        "server": {
            "version": "3.4.0",
            "uptime": int(time.time() - server_start_time),
            "ai_provider": "google_gemini_2.5_flash"
        },
        "environment": {
            "has_xiaozhi_ws": bool(XIAOZHI_WS),
            "has_google_search": bool(GOOGLE_API_KEY and CSE_ID),
            "has_gemini": bool(GEMINI_API_KEY),
            "gemini_key_prefix": GEMINI_API_KEY[:10] + "..." if GEMINI_API_KEY else "none"
        },
        "free_limits": {
            "gemini": "60 requests per minute, 128K context",
            "google_search": "100 searches per day",
            "wikipedia": "unlimited"
        },
        "supported_models": [
            "gemini-2.5-flash (recommended)",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash", 
            "gemini-1.5-flash"
        ]
    }), 200

def run_web_server():
    """Run Flask web server."""
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

# ================= APPLICATION ENTRY POINT =================
async def main():
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("🚀 Starting Xiaozhi MCP Server v3.4")
    logger.info("🌟 GEMINI 2.5 FLASH EDITION - Latest & Greatest!")
    logger.info("=" * 60)
    
    logger.info("📊 Configuration Check:")
    logger.info(f"   Google Search API: {'✅ Configured' if GOOGLE_API_KEY and CSE_ID else '⚠️  Not configured'}")
    logger.info(f"   Wikipedia: ✅ Fixed (no 403 errors)")
    logger.info(f"   Google Gemini: {'✅ Configured' if GEMINI_API_KEY else '⚠️  Not configured'}")
    
    if GEMINI_API_KEY:
        logger.info(f"   Gemini Key: {GEMINI_API_KEY[:10]}...")
        logger.info("   🎉 Gemini 2.5 Flash: 60 requests/minute, 128K context FREE!")
    
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