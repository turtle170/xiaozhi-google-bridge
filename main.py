# main.py - XIAOZHI MCP SERVER v3.6 - TIERED MODEL SELECTION
# ONLY MODIFIED: Classification system to use Gemini 2.5 Flash-Lite
# EVERYTHING ELSE: Kept exactly as you had it
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
import hashlib
from datetime import datetime, timedelta
import concurrent.futures

# ================= LOAD ENVIRONMENT VARIABLES =================
load_dotenv()

# Get configuration from environment variables
XIAOZHI_WS = os.environ.get("XIAOZHI_WS")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
CSE_ID = os.environ.get("CSE_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

# ================= TIERED MODEL SELECTION (UPDATED) =================
class SmartModelSelector:
    """Smart model selection using Gemini 2.5 Flash-Lite for classification."""
    
    # YOUR EXACT TIERS (unchanged)
    TIERS = {
        "HARD": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "MEDIUM": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
        "SIMPLE": ["gemini-2.5-flash-lite"]
    }
    
    @staticmethod
    def classify_query_with_gemini(query):
        """Use Gemini 2.5 Flash-Lite to classify query into HARD, MEDIUM, or SIMPLE."""
        try:
            if not GEMINI_API_KEY:
                logger.warning("No Gemini API key, using fallback classification")
                return "MEDIUM"
            
            # Use your exact prompt
            classification_prompt = f"""Please help me sort this query into 3 tiers: Hard (handled by Gemini 2.5 Pro), Medium (Handled by Gemini 2.5 Flash), and Simple (Handled by Gemini 2.5 Flash-Lite): "{query}", and strictly only say "HARD","MEDIUM", OR "SIMPLE", no extra."""
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
            
            headers = {
                "Content-Type": "application/json"
            }
            
            data = {
                "contents": [{
                    "parts": [{
                        "text": classification_prompt
                    }]
                }],
                "generationConfig": {
                    "maxOutputTokens": 10,
                    "temperature": 0.1,
                    "topP": 0.1,
                    "topK": 1,
                }
            }
            
            params = {"key": GEMINI_API_KEY}
            
            response = requests.post(url, headers=headers, json=data, params=params, timeout=3)
            response.raise_for_status()
            
            result = response.json()
            
            if "candidates" in result and len(result["candidates"]) > 0:
                candidate = result["candidates"][0]
                if "content" in candidate:
                    parts = candidate["content"].get("parts", [])
                    if parts and len(parts) > 0 and "text" in parts[0]:
                        classification = parts[0]["text"].strip().upper()
                        
                        # Extract just HARD/MEDIUM/SIMPLE from response
                        for tier in ["HARD", "MEDIUM", "SIMPLE"]:
                            if tier in classification:
                                return tier
            
            return "MEDIUM"  # Default fallback
            
        except Exception as e:
            logger.error(f"❌ Classification error: {e}")
            return "MEDIUM"
    
    @staticmethod
    def select_model(query):
        """Select model using Gemini-powered classification."""
        # Get classification from Gemini
        tier = SmartModelSelector.classify_query_with_gemini(query)
        logger.info(f"🎯 Gemini classified as: {tier} for '{query[:50]}...'")
        
        # Map tier to your exact model chains (unchanged)
        model_configs = {
            "HARD": {
                "tier": "HARD",
                "models": SmartModelSelector.TIERS["HARD"],
                "primary": "gemini-2.5-pro",
                "tokens": 2000,
                "timeout": 30
            },
            "MEDIUM": {
                "tier": "MEDIUM",
                "models": SmartModelSelector.TIERS["MEDIUM"],
                "primary": "gemini-2.5-flash",
                "tokens": 1000,
                "timeout": 20
            },
            "SIMPLE": {
                "tier": "SIMPLE",
                "models": SmartModelSelector.TIERS["SIMPLE"],
                "primary": "gemini-2.5-flash-lite",
                "tokens": 500,
                "timeout": 10
            }
        }
        
        config = model_configs.get(tier, model_configs["MEDIUM"])
        
        return {
            "tier": tier,
            "models": config["models"],
            "primary": config["primary"],
            "tokens": config["tokens"],
            "timeout": config["timeout"]
        }

# ================= GEMINI API CLIENT (UNCHANGED) =================
gemini_cache = {}
CACHE_DURATION = 300

def call_gemini_api(query, model="gemini-2.5-flash", max_tokens=1000, timeout=20):
    """Call Gemini API with a specific model."""
    try:
        if not GEMINI_API_KEY:
            return "Gemini API key not configured."
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        
        headers = {
            "Content-Type": "application/json"
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
                "topP": 0.9,
                "topK": 40,
            }
        }
        
        params = {"key": GEMINI_API_KEY}
        
        response = requests.post(url, headers=headers, json=data, params=params, timeout=timeout)
        
        # Handle 404 - model not available
        if response.status_code == 404:
            logger.warning(f"❌ Model {model} not available (404)")
            return None, "MODEL_NOT_AVAILABLE"
        
        # Handle rate limits
        if response.status_code == 429:
            logger.warning(f"⏰ Model {model} rate limited (429)")
            return None, "RATE_LIMITED"
        
        response.raise_for_status()
        
        result = response.json()
        
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            if "content" in candidate:
                parts = candidate["content"].get("parts", [])
                if parts and len(parts) > 0 and "text" in parts[0]:
                    answer = parts[0]["text"]
                    return answer, "SUCCESS"
        
        return None, "PARSE_ERROR"
        
    except requests.exceptions.Timeout:
        logger.warning(f"⏰ Model {model} timeout after {timeout}s")
        return None, "TIMEOUT"
    except Exception as e:
        logger.error(f"❌ Model {model} error: {e}")
        return None, "ERROR"

def ask_gemini_smart(query):
    """Smart Gemini query with YOUR tiered model selection."""
    try:
        if not query or not query.strip():
            return "Please provide a question."
        
        # Step 1: Select tier and model (NOW USING GEMINI CLASSIFICATION)
        model_info = SmartModelSelector.select_model(query)
        tier = model_info["tier"]
        models_to_try = model_info["models"]
        primary_model = model_info["primary"]
        max_tokens = model_info["tokens"]
        timeout = model_info["timeout"]
        
        logger.info(f"🤖 Using {tier} tier: {primary_model} for '{query[:50]}...'")
        
        # Check cache first
        cache_key = hashlib.md5(f"{query}_{primary_model}".encode()).hexdigest()
        if cache_key in gemini_cache:
            cached_time, response = gemini_cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                logger.info(f"♻️ Cached response from {primary_model}")
                return f"[{tier} - Cached] {response}"
        
        # Step 2: Try models in tier order
        for model in models_to_try:
            logger.info(f"🔄 Trying model: {model}")
            
            # Adjust tokens/timeout based on model
            if model == "gemini-2.5-pro":
                model_tokens = max_tokens
                model_timeout = timeout
            elif model == "gemini-2.5-flash":
                model_tokens = min(max_tokens, 1000)
                model_timeout = min(timeout, 20)
            else:  # flash-lite or 2.0-flash
                model_tokens = min(max_tokens, 500)
                model_timeout = min(timeout, 15)
            
            result, status = call_gemini_api(query, model, model_tokens, model_timeout)
            
            if status == "SUCCESS" and result:
                # Cache successful response
                gemini_cache[cache_key] = (datetime.now(), result)
                
                # Add tier/model info to response
                return f"[{tier} - {model}] {result}"
            
            elif status == "MODEL_NOT_AVAILABLE":
                logger.warning(f"❌ Model {model} not available, trying next in tier")
                continue
            
            elif status == "TIMEOUT":
                logger.warning(f"⏰ Model {model} timeout, trying next in tier")
                continue
            
            elif status == "RATE_LIMITED":
                # If rate limited, wait and try same model again
                logger.info(f"⏳ Rate limited on {model}, waiting 2s")
                time.sleep(2)
                result, status = call_gemini_api(query, model, model_tokens, model_timeout)
                if status == "SUCCESS" and result:
                    gemini_cache[cache_key] = (datetime.now(), result)
                    return f"[{tier} - {model}] {result}"
                continue
        
        # If all models in tier failed, fall back to basic
        logger.warning(f"❌ All models in {tier} tier failed, using basic fallback")
        return ask_gemini_basic(query)
        
    except Exception as e:
        logger.error(f"❌ Smart Gemini error: {e}")
        return f"AI error: {str(e)[:80]}"

def ask_gemini_basic(query):
    """Basic fallback if smart selection fails."""
    # Try the most reliable free models
    fallback_models = ["gemini-1.5-flash", "gemini-2.0-flash"]
    
    for model in fallback_models:
        result, status = call_gemini_api(query, model, 500, 10)
        if status == "SUCCESS" and result:
            return f"[Fallback - {model}] {result}"
    
    return "Gemini AI is currently unavailable. Please try again in a moment."

# ================= OTHER TOOLS (OPTIMIZED) - UNCHANGED =================
def google_search(query, max_results=10):
    """Google Search - EXACTLY AS YOU HAD IT."""
    try:
        if not GOOGLE_API_KEY or not CSE_ID:
            return "Google Search not configured."
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": CSE_ID,
            "q": query,
            "num": max_results,
            "safe": "active"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "items" not in data or len(data["items"]) == 0:
            return "No results found."
        
        items = data["items"][:max_results]
        results = []
        
        for i, item in enumerate(items, 1):
            title = item.get('title', 'No title')
            link = item.get('link', 'No link')
            snippet = item.get('snippet', 'No description')
            
            result_text = f"{i}. **{title}**\n   🔗 {link}\n   📝 {snippet}"
            results.append(result_text)
        
        return "\n\n".join(results)
        
    except Exception as e:
        logger.error(f"Google search error: {e}")
        return "Google search error."

def wikipedia_search(query, max_results=3):
    """Wikipedia Search - EXACTLY AS YOU HAD IT."""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        headers = {'User-Agent': 'XiaozhiBot/3.6'}
        
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "utf8": 1
        }
        
        response = requests.get(url, params=search_params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "query" not in data or "search" not in data["query"]:
            return "No Wikipedia articles found."
        
        search_results = data["query"]["search"]
        if not search_results:
            return "No Wikipedia articles found."
        
        page_ids = [str(item["pageid"]) for item in search_results[:max_results]]
        
        extract_params = {
            "action": "query",
            "format": "json",
            "pageids": "|".join(page_ids),
            "prop": "extracts|info",
            "exintro": True,
            "explaintext": True,
            "inprop": "url",
            "exchars": 400
        }
        
        extract_response = requests.get(url, params=extract_params, headers=headers, timeout=10)
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
                if len(extract) > 300:
                    extract = extract[:300] + "..."
            
            result_text = f"{i}. **{title}**\n   🌐 {page_url}\n   📖 {extract}"
            results.append(result_text)
        
        return "\n\n".join(results) if results else "No content retrieved."
        
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return "Wikipedia search error."

# ================= MCP PROTOCOL HANDLER (UNCHANGED) =================
class MCPProtocolHandler:
    @staticmethod
    def handle_initialize(message_id):
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "smart-tier-gemini",
                    "version": "3.6.0"
                }
            }
        }
    
    @staticmethod
    def handle_tools_list(message_id):
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "tools": [
                    {
                        "name": "google_search",
                        "description": "Search Google",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "wikipedia_search",
                        "description": "Search Wikipedia",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "ask_ai",
                        "description": "Ask AI with SMART tiered model selection (Auto-chooses Pro/Flash/Flash-Lite)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"}
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def handle_ping(message_id):
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    
    @staticmethod
    def handle_tools_call(message_id, params):
        tool_name = params.get("name", "")
        query = params.get("arguments", {}).get("query", "").strip()
        
        if not query:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32602, "message": "Missing query"}
            }
        
        try:
            if tool_name == "google_search":
                result = google_search(query)
            elif tool_name == "wikipedia_search":
                result = wikipedia_search(query)
            elif tool_name == "ask_ai":
                result = ask_gemini_smart(query)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
                }
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
            
        except Exception as e:
            logger.error(f"Tool error: {e}")
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32000, "message": f"Error: {str(e)[:80]}"}
            }

# ================= WEB SERVER (UNCHANGED) =================
app = Flask(__name__)
server_start_time = time.time()

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP v3.6</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #4285F4 0%, #34A853 100%); 
                      color: white; padding: 2rem; border-radius: 10px; margin-bottom: 2rem; }}
            .tier {{ padding: 1rem; margin: 1rem 0; border-radius: 8px; }}
            .hard {{ background: #FFEBEE; border-left: 4px solid #F44336; }}
            .medium {{ background: #FFF3E0; border-left: 4px solid #FF9800; }}
            .simple {{ background: #E8F5E9; border-left: 4px solid #4CAF50; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP v3.6</h1>
            <p>SMART Tiered Gemini Selection</p>
        </div>
        
        <h2>🎯 Your Model Tiers:</h2>
        
        <div class="tier hard">
            <h3>🔴 HARD Tasks → Pro Tier</h3>
            <p><strong>Models:</strong> gemini-2.5-pro → gemini-2.5-flash → gemini-2.5-flash-lite</p>
            <p><strong>For:</strong> Complex analysis, coding, detailed explanations</p>
        </div>
        
        <div class="tier medium">
            <h3>🟡 MEDIUM Tasks → Flash Tier</h3>
            <p><strong>Models:</strong> gemini-2.5-flash → gemini-2.5-flash-lite → gemini-2.0-flash</p>
            <p><strong>For:</strong> General Q&A, explanations, guides</p>
        </div>
        
        <div class="tier simple">
            <h3>🟢 SIMPLE Tasks → Flash-Lite Tier</h3>
            <p><strong>Models:</strong> gemini-2.5-flash-lite</p>
            <p><strong>For:</strong> Quick answers, facts, simple questions</p>
        </div>
        
        <p>Uptime: {hours}h {minutes}m {seconds}s | Cache: {len(gemini_cache)} items</p>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "version": "3.6.0",
        "tiers": SmartModelSelector.TIERS,
        "cache_size": len(gemini_cache)
    }), 200

@app.route('/test-smart/<query>')
def test_smart(query):
    """Test the smart tier selection."""
    result = ask_gemini_smart(query)
    return jsonify({
        "query": query,
        "result": result,
        "cache_size": len(gemini_cache)
    }), 200

def run_web_server():
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True)

# ================= MAIN (UNCHANGED) =================
async def mcp_bridge():
    """WebSocket bridge."""
    reconnect_delay = 2
    while True:
        try:
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=25,
                ping_timeout=15,
                close_timeout=10
            ) as websocket:
                logger.info("✅ Connected to Xiaozhi")
                reconnect_delay = 2
                
                async for raw_message in websocket:
                    try:
                        data = json.loads(raw_message)
                        message_id = data.get("id")
                        method = data.get("method", "")
                        params = data.get("params", {})
                        
                        response = None
                        
                        if method == "ping":
                            response = MCPProtocolHandler.handle_ping(message_id)
                        elif method == "initialize":
                            response = MCPProtocolHandler.handle_initialize(message_id)
                            logger.info("✅ Initialized")
                        elif method == "tools/list":
                            response = MCPProtocolHandler.handle_tools_list(message_id)
                            logger.info("✅ Sent tools list")
                        elif method == "tools/call":
                            response = MCPProtocolHandler.handle_tools_call(message_id, params)
                            tool_name = params.get("name", "")
                            logger.info(f"✅ Processed {tool_name}")
                        elif method == "notifications/initialized":
                            continue
                        else:
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"Unknown: {method}"}}
                        
                        if response:
                            await websocket.send(json.dumps(response))
                            
                    except Exception as e:
                        logger.error(f"Message error: {e}")
        
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.info(f"⏳ Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 60)

async def main():
    logger.info("🚀 Starting Xiaozhi MCP v3.6 - Smart Tiered Gemini")
    logger.info(f"📊 Gemini: {'✅ Configured' if GEMINI_API_KEY else '❌ Not configured'}")
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server on http://0.0.0.0:3000")
    
    # Start MCP bridge
    await mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}")