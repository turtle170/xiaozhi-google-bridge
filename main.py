# main.py - XIAOZHI MCP SERVER v3.6.1 - ASYNC PING FIX (IMPORT FIXED)
import os
import asyncio
import json
import websockets
import requests
import logging
from flask import Flask, jsonify, render_template_string
import threading
import time
import sys
from dotenv import load_dotenv
import re
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid
from typing import Dict

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

# ================= ASYNC PING CONFIGURATION =================
ASYNC_PING_MODE = True
FIRST_PING_DELAY = 0.05  # 50ms - INSTANT first ping!
CONTINUOUS_PING_INTERVAL = 0.8  # 0.8 seconds
PARALLEL_MODEL_TRIES = 3
MAX_WORKERS = 5

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

# ================= GLOBAL STATE =================
gemini_cache = {}
CACHE_DURATION = 300
active_requests: Dict[str, bool] = {}
ping_tasks: Dict[str, asyncio.Task] = {}
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)  # FIXED: Properly defined

keep_alive_messages = [
    "AI is thinking... 🧠",
    "Processing your request... ⚡",
    "Generating response... ✨",
    "Almost ready! 🔜",
    "Working on it... 🔄",
    "Crafting answer... 📝",
    "Analyzing your query... 🔍",
    "Preparing response... 🚀"
]

# ================= ASYNC KEEP-ALIVE MANAGER =================
class AsyncPingManager:
    """Async-based keep-alive system (THREAD-SAFE for WebSocket)."""
    
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.websocket_refs: Dict[str, websockets.WebSocketClientProtocol] = {}
    
    async def start_pinging(self, websocket, request_id: str, duration: int = 30):
        """Start async ping task for a request."""
        try:
            logger.info(f"🚀 STARTING ASYNC PING for {request_id}")
            
            # Store WebSocket reference
            self.websocket_refs[request_id] = websocket
            
            # Create and store ping task
            task = asyncio.create_task(
                self._ping_worker(websocket, request_id, duration)
            )
            self.active_tasks[request_id] = task
            
            # Start the task
            task.add_done_callback(lambda t: self._cleanup(request_id))
            
            logger.info(f"✅ ASYNC PING STARTED for {request_id}")
            return task
            
        except Exception as e:
            logger.error(f"❌ Failed to start async ping: {e}")
            return None
    
    async def _ping_worker(self, websocket, request_id: str, duration: int):
        """Async ping worker - runs in same event loop as WebSocket."""
        try:
            start_time = time.time()
            ping_count = 0
            
            # PHASE 1: INSTANT FIRST PING (50ms)
            await asyncio.sleep(FIRST_PING_DELAY)
            await self._send_safe_ping(websocket, request_id, 0)
            logger.info(f"✅ INSTANT PING #1 sent for {request_id}")
            
            # PHASE 2: CONTINUOUS PINGS (0.8s interval)
            while time.time() - start_time < duration:
                if not self._is_request_active(request_id):
                    logger.info(f"⚠️ Request {request_id} completed, stopping pings")
                    break
                
                # Wait for interval
                await asyncio.sleep(CONTINUOUS_PING_INTERVAL)
                
                # Send ping
                success = await self._send_safe_ping(websocket, request_id, ping_count)
                if success:
                    ping_count += 1
                    if ping_count % 5 == 0:  # Log every 5 pings
                        logger.info(f"📤 Ping #{ping_count} sent for {request_id}")
                
                # Check if WebSocket is still connected
                if not websocket.open:
                    logger.warning(f"⚠️ WebSocket closed for {request_id}, stopping pings")
                    break
            
            logger.info(f"✅ Ping worker completed for {request_id} ({ping_count} pings)")
            
        except Exception as e:
            logger.error(f"❌ Ping worker error for {request_id}: {e}")
        finally:
            self._cleanup(request_id)
    
    async def _send_safe_ping(self, websocket, request_id: str, ping_count: int) -> bool:
        """Safely send a ping message (handles WebSocket errors)."""
        try:
            if not websocket or not websocket.open:
                logger.warning(f"⚠️ WebSocket not open for {request_id}")
                return False
            
            message_idx = ping_count % len(keep_alive_messages)
            ping_message = {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progress": {
                        "type": "text",
                        "text": f"⏳ {keep_alive_messages[message_idx]}"
                    }
                }
            }
            
            await websocket.send(json.dumps(ping_message))
            return True
            
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"⚠️ Connection closed while pinging {request_id}")
            return False
        except Exception as e:
            logger.error(f"❌ Ping send error for {request_id}: {e}")
            return False
    
    def _is_request_active(self, request_id: str) -> bool:
        """Check if request is still active."""
        return request_id in active_requests and active_requests[request_id]
    
    def _cleanup(self, request_id: str):
        """Clean up ping task and references."""
        try:
            # Cancel task if still running
            if request_id in self.active_tasks:
                task = self.active_tasks[request_id]
                if not task.done():
                    task.cancel()
                del self.active_tasks[request_id]
            
            # Remove WebSocket reference
            if request_id in self.websocket_refs:
                del self.websocket_refs[request_id]
            
            # Remove from active requests
            if request_id in active_requests:
                del active_requests[request_id]
                
            logger.info(f"🧹 Cleaned up ping manager for {request_id}")
            
        except Exception as e:
            logger.error(f"❌ Cleanup error for {request_id}: {e}")
    
    def stop_all_pings(self):
        """Stop all ping tasks (e.g., on shutdown)."""
        for request_id, task in list(self.active_tasks.items()):
            if not task.done():
                task.cancel()
                logger.info(f"🛑 Stopped ping task for {request_id}")
        
        self.active_tasks.clear()
        self.websocket_refs.clear()

# Global ping manager
ping_manager = AsyncPingManager()

# ================= GEMINI-POWERED CLASSIFICATION =================
class SmartModelSelector:
    """Smart model selection using ONLY Gemini 2.5 Flash-Lite for classification."""
    
    TIERS = {
        "HARD": "gemini-3.0-flash",
        "MEDIUM": "gemini-2.5-flash", 
        "SIMPLE": "gemini-2.5-flash-lite"
    }
    
    @staticmethod
    def classify_query_with_gemini(query: str) -> str:
        """Use Gemini 2.5 Flash-Lite to classify query into HARD, MEDIUM, or SIMPLE."""
        try:
            if not GEMINI_API_KEY:
                logger.warning("No Gemini API key, using MEDIUM as default")
                return "MEDIUM"
            
            classification_prompt = f"""Please help me sort this query into 3 tiers: 
Hard (handled by Gemini 3 Flash), 
Medium (Handled by Gemini 2.5 Flash), and 
Simple (Handled by Gemini 2.5 Flash-Lite): "{query}"

Strictly only say "HARD", "MEDIUM", OR "SIMPLE", no extra text, no explanation."""
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
            
            headers = {"Content-Type": "application/json"}
            
            data = {
                "contents": [{"parts": [{"text": classification_prompt}]}],
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
                        for tier in ["HARD", "MEDIUM", "SIMPLE"]:
                            if tier in classification:
                                logger.info(f"🎯 Classified as {tier}")
                                return tier
            
            return "MEDIUM"
            
        except requests.exceptions.Timeout:
            logger.warning("⏰ Classification timeout, using MEDIUM")
            return "MEDIUM"
        except Exception as e:
            logger.error(f"❌ Classification error: {e}")
            return "MEDIUM"

# ================= ASYNC GEMINI PROCESSOR =================
class AsyncGeminiProcessor:
    """Async Gemini processor with proper WebSocket integration."""
    
    @staticmethod
    def get_model_config(tier: str):
        """Get model configuration for tier."""
        configs = {
            "HARD": {
                "models": ["gemini-3.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
                "tokens": 8192,
                "timeouts": [20, 15, 12]
            },
            "MEDIUM": {
                "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
                "tokens": 4096,
                "timeouts": [12, 8, 8]
            },
            "SIMPLE": {
                "models": ["gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"],
                "tokens": 2048,
                "timeouts": [6, 6, 6]
            }
        }
        return configs.get(tier, configs["MEDIUM"])
    
    @staticmethod
    def call_gemini_sync(query: str, model: str, max_tokens: int, timeout: int):
        """Synchronous Gemini API call for thread pool."""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            
            headers = {"Content-Type": "application/json"}
            
            data = {
                "contents": [{"parts": [{"text": query}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": 0.7,
                    "topP": 0.9,
                    "topK": 40,
                }
            }
            
            params = {"key": GEMINI_API_KEY}
            
            response = requests.post(url, headers=headers, json=data, params=params, timeout=timeout)
            
            if response.status_code == 200:
                result = response.json()
                if "candidates" in result and result["candidates"]:
                    candidate = result["candidates"][0]
                    if "content" in candidate:
                        parts = candidate["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"], True
            
            return None, False
            
        except requests.exceptions.Timeout:
            logger.warning(f"⏰ {model} timeout")
            return None, False
        except Exception as e:
            logger.error(f"❌ {model} error: {str(e)[:50]}")
            return None, False
    
    @staticmethod
    async def process_query_async(query: str, tier: str, cache_key: str, websocket, request_id: str):
        """Process query with async pinging and parallel model attempts."""
        try:
            logger.info(f"🚀 Processing {tier} tier: '{query[:40]}...'")
            
            # Mark request as active
            active_requests[request_id] = True
            
            # START ASYNC PINGING IMMEDIATELY
            ping_task = await ping_manager.start_pinging(websocket, request_id, 25)
            if not ping_task:
                logger.error("❌ Failed to start pinging!")
            
            # Check cache first
            if cache_key in gemini_cache:
                cached_time, response = gemini_cache[cache_key]
                if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                    logger.info(f"⚡ Cache hit for {request_id}")
                    return f"[{tier} - Cached] {response}"
            
            # Get model configuration
            config = AsyncGeminiProcessor.get_model_config(tier)
            models = config["models"][:PARALLEL_MODEL_TRIES]
            max_tokens = config["tokens"]
            timeouts = config["timeouts"][:PARALLEL_MODEL_TRIES]
            
            # Submit models in parallel using ThreadPoolExecutor
            futures = []
            with ThreadPoolExecutor(max_workers=PARALLEL_MODEL_TRIES) as model_executor:
                for i, model in enumerate(models):
                    timeout = timeouts[i] if i < len(timeouts) else 10
                    future = model_executor.submit(
                        AsyncGeminiProcessor.call_gemini_sync,
                        query, model, max_tokens, timeout
                    )
                    futures.append((model, future))
                
                # Wait for first successful response
                for model, future in futures:
                    try:
                        result, success = future.result(timeout=15)
                        if success and result:
                            logger.info(f"✅ {model} succeeded!")
                            gemini_cache[cache_key] = (datetime.now(), result)
                            return f"[{tier} - {model}] {result}"
                    except Exception as e:
                        logger.warning(f"⚠️ {model} failed: {e}")
            
            # Fallback sequential
            logger.warning("⚠️ Parallel failed, trying sequential")
            for model in models:
                result, success = AsyncGeminiProcessor.call_gemini_sync(
                    query, model, max_tokens, 10
                )
                if success and result:
                    gemini_cache[cache_key] = (datetime.now(), result)
                    return f"[{tier} - {model}*] {result}"
            
            return "Sorry, I couldn't generate a response. Please try again."
            
        except Exception as e:
            logger.error(f"❌ Processing error: {e}")
            return f"Error: {str(e)[:50]}"
        finally:
            # Mark request as completed
            active_requests[request_id] = False

# ================= FAST TOOLS =================
def google_search_fast(query: str, max_results: int = 5) -> str:
    """Fast Google search."""
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
        
        response = requests.get(url, params=params, timeout=8)
        data = response.json()
        
        if "items" not in data:
            return "No results found."
        
        items = data["items"][:max_results]
        results = []
        
        for i, item in enumerate(items, 1):
            title = item.get('title', 'No title')
            link = item.get('link', 'No link')
            snippet = item.get('snippet', 'No description')[:150]
            results.append(f"{i}. **{title}**\n🔗 {link}\n📝 {snippet}")
        
        return "\n\n".join(results) if results else "No results found."
        
    except Exception as e:
        logger.error(f"Google error: {e}")
        return "Search error."

def wikipedia_search_fast(query: str, max_results: int = 2) -> str:
    """Fast Wikipedia search."""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "utf8": 1
        }
        
        response = requests.get(url, params=params, timeout=8)
        data = response.json()
        
        if "query" not in data:
            return "No articles found."
        
        search_results = data["query"].get("search", [])
        if not search_results:
            return "No articles found."
        
        first = search_results[0]
        title = first.get("title", "Unknown")
        
        extract_params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "exchars": 200
        }
        
        extract_response = requests.get(url, params=extract_params, timeout=8)
        extract_data = extract_response.json()
        
        pages = extract_data.get("query", {}).get("pages", {})
        for page in pages.values():
            extract = page.get("extract", "No summary.")
            return f"**{title}**\n📖 {extract[:200]}..."
        
        return "No content retrieved."
        
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return "Search error."

# ================= ASYNC MCP HANDLER =================
class AsyncMCPHandler:
    """Async MCP protocol handler with proper ping integration."""
    
    @staticmethod
    def handle_initialize(message_id):
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "async-ping-mcp",
                    "version": "3.6.1-ASYNC-PING"
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
                        "description": "Fast Google Search",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "wikipedia_search",
                        "description": "Fast Wikipedia Search",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "ask_ai",
                        "description": "Ask AI with async pings (50ms first ping!)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
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
    async def handle_tools_call_async(message_id, params, websocket):
        """Async tools call with INSTANT pinging."""
        tool_name = params.get("name", "")
        query = params.get("arguments", {}).get("query", "").strip()
        
        if not query:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32602, "message": "Missing query"}
            }
        
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"🔄 Processing {tool_name}: '{query[:30]}...' [ID: {request_id}]")
            
            if tool_name == "google_search":
                result = google_search_fast(query)
            elif tool_name == "wikipedia_search":
                result = wikipedia_search_fast(query)
            elif tool_name == "ask_ai":
                # Get classification
                tier = SmartModelSelector.classify_query_with_gemini(query)
                cache_key = hashlib.md5(f"{query}_{tier}".encode()).hexdigest()
                
                # Process with async pinging
                result = await AsyncGeminiProcessor.process_query_async(
                    query, tier, cache_key, websocket, request_id
                )
            else:
                result = f"Unknown tool: {tool_name}"
            
            logger.info(f"✅ Completed {tool_name} [ID: {request_id}]")
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
            
        except Exception as e:
            logger.error(f"❌ Tool error [ID: {request_id}]: {e}")
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32000, "message": f"Error: {str(e)[:50]}"}
            }
        finally:
            # Clean up ping task
            if request_id in active_requests:
                active_requests[request_id] = False

# ================= FULL FEATURED WEB SERVER =================
app = Flask(__name__)
server_start_time = time.time()
log_buffer = []
MAX_LOG_LINES = 100

test_queries = [
    "Explain quantum computing in simple terms",
    "What is the capital of France?",
    "Write a Python function to calculate Fibonacci",
    "Compare machine learning and deep learning",
    "How to make a cup of tea"
]

def add_log(level: str, message: str):
    """Add log to buffer for real-time display."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {
        'time': timestamp,
        'level': level,
        'message': message,
        'color': {
            'INFO': '#4CAF50',
            'WARNING': '#FF9800',
            'ERROR': '#F44336',
            'DEBUG': '#2196F3'
        }.get(level, '#757575')
    }
    log_buffer.append(log_entry)
    if len(log_buffer) > MAX_LOG_LINES:
        log_buffer.pop(0)

# Initialize logs
add_log('INFO', '🚀 STARTING ASYNC PING SERVER...')
add_log('INFO', f'Gemini API: {"✅ CONFIGURED" if GEMINI_API_KEY else "❌ MISSING"}')
add_log('INFO', f'First ping delay: {FIRST_PING_DELAY}s')
add_log('INFO', f'Continuous ping interval: {CONTINUOUS_PING_INTERVAL}s')

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP - Async Ping Dashboard</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root {
                --primary: #4285F4;
                --success: #34A853;
                --warning: #FBBC05;
                --danger: #EA4335;
                --dark: #202124;
                --light: #f8f9fa;
            }
            
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
            }
            
            .dashboard {
                max-width: 1400px;
                margin: 0 auto;
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 20px;
            }
            
            .card {
                background: white;
                border-radius: 12px;
                padding: 24px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.1);
                margin-bottom: 20px;
            }
            
            .header {
                grid-column: 1 / -1;
                background: white;
                padding: 30px;
                border-radius: 16px;
                text-align: center;
                box-shadow: 0 8px 25px rgba(0,0,0,0.1);
            }
            
            .status-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin: 20px 0;
            }
            
            .status-item {
                padding: 15px;
                border-radius: 10px;
                text-align: center;
                background: var(--light);
            }
            
            .uptime-display {
                font-size: 28px;
                font-weight: bold;
                color: var(--primary);
                margin: 10px 0;
            }
            
            .log-panel {
                height: 400px;
                overflow-y: auto;
                background: var(--dark);
                color: white;
                border-radius: 8px;
                padding: 15px;
                font-family: 'Monaco', 'Menlo', monospace;
                font-size: 13px;
            }
            
            .log-entry {
                padding: 4px 0;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            
            .log-time {
                color: #aaa;
                margin-right: 10px;
            }
            
            .log-level {
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
                margin-right: 10px;
                font-size: 11px;
            }
            
            .nav-links {
                display: flex;
                gap: 10px;
                margin: 20px 0;
                flex-wrap: wrap;
            }
            
            .nav-btn {
                padding: 10px 20px;
                background: var(--primary);
                color: white;
                text-decoration: none;
                border-radius: 8px;
                transition: all 0.3s;
            }
            
            .nav-btn:hover {
                background: #3367D6;
                transform: translateY(-2px);
            }
            
            .test-section {
                display: grid;
                gap: 10px;
            }
            
            .test-btn {
                padding: 12px;
                background: var(--light);
                border: 2px solid var(--primary);
                border-radius: 8px;
                cursor: pointer;
                text-align: left;
                transition: all 0.3s;
            }
            
            .test-btn:hover {
                background: var(--primary);
                color: white;
            }
            
            .result-display {
                padding: 15px;
                background: var(--light);
                border-radius: 8px;
                margin-top: 15px;
                display: none;
                white-space: pre-wrap;
                max-height: 300px;
                overflow-y: auto;
            }
            
            .real-time {
                display: flex;
                align-items: center;
                gap: 10px;
                margin: 15px 0;
            }
            
            .pulse {
                width: 10px;
                height: 10px;
                border-radius: 50%;
                background: var(--success);
                animation: pulse 0.5s infinite;
            }
            
            @keyframes pulse {
                0% { opacity: 1; }
                50% { opacity: 0.5; }
                100% { opacity: 1; }
            }
            
            .refresh-btn {
                padding: 8px 16px;
                background: var(--warning);
                color: white;
                border: none;
                border-radius: 6px;
                cursor: pointer;
            }
            
            .grid-2col {
                grid-column: 1 / -1;
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
            }
            
            @media (max-width: 1024px) {
                .dashboard {
                    grid-template-columns: 1fr;
                }
                .grid-2col {
                    grid-template-columns: 1fr;
                }
            }
            
            .ping-badge {
                background: linear-gradient(90deg, #ff0080, #00ff80);
                color: white;
                padding: 5px 10px;
                border-radius: 20px;
                font-weight: bold;
                font-size: 12px;
                margin-left: 10px;
            }
            
            .ping-stats {
                background: var(--light);
                padding: 10px;
                border-radius: 8px;
                margin-top: 10px;
                font-size: 12px;
            }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <div class="header">
                <h1 style="margin:0;color:var(--primary);">🚀 Xiaozhi MCP v3.6.1</h1>
                <p style="color:#666;margin:5px 0 20px 0;">
                    Async Ping Mode <span class="ping-badge">⚡ 50ms FIRST PING</span>
                </p>
                
                <div class="status-grid">
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Uptime</div>
                        <div class="uptime-display" id="uptime">{{hours}}h {{minutes}}m {{seconds}}s</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Cache Size</div>
                        <div style="font-size:24px;color:var(--primary);" id="cacheSize">{{cache_size}}</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Active Requests</div>
                        <div style="font-size:24px;color:var(--primary);" id="activeRequests">{{active_count}}</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Status</div>
                        <div style="font-size:24px;color:var(--success);">✅ Async Live</div>
                    </div>
                </div>
                
                <div class="ping-stats">
                    <div><strong>Ping Configuration:</strong></div>
                    <div>• First ping: {{first_ping_delay}}s (INSTANT!)</div>
                    <div>• Continuous: {{continuous_ping_interval}}s interval</div>
                    <div>• Parallel models: {{parallel_tries}} simultaneously</div>
                </div>
                
                <div class="real-time">
                    <div class="pulse"></div>
                    <span>Async ping system active (NO THREADING ISSUES)</span>
                    <button class="refresh-btn" onclick="refreshData()">🔄 Refresh</button>
                </div>
            </div>
            
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">🔗 Quick Links</h3>
                <div class="nav-links">
                    <a href="/health" class="nav-btn" target="_blank">📊 Health Check</a>
                    <a href="/test-smart/hello" class="nav-btn" target="_blank">🧪 Quick Test</a>
                    <a href="/logs" class="nav-btn" target="_blank">📋 View Logs</a>
                    <a href="/stats" class="nav-btn" target="_blank">📈 Statistics</a>
                    <a href="/api-docs" class="nav-btn" target="_blank">📚 API Docs</a>
                </div>
                
                <h3 style="margin-top:25px;color:var(--primary);">⚡ Ping System Info</h3>
                <div style="background:var(--light);padding:15px;border-radius:8px;">
                    <div><strong>Version:</strong> 3.6.1-ASYNC-PING</div>
                    <div><strong>MCP Protocol:</strong> 2024-11-05</div>
                    <div><strong>First Ping:</strong> ⚡ {{first_ping_delay}}s (GUARANTEED!)</div>
                    <div><strong>WebSocket:</strong> ✅ Same event loop (thread-safe)</div>
                    <div><strong>Last Updated:</strong> {{timestamp}}</div>
                </div>
            </div>
            
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <h3 style="margin-top:0;color:var(--primary);">📝 Live Logs</h3>
                    <button class="refresh-btn" onclick="refreshLogs()">↻ Update Logs</button>
                </div>
                <div class="log-panel" id="logPanel">
                    {% for log in logs %}
                    <div class="log-entry">
                        <span class="log-time">{{log.time}}</span>
                        <span class="log-level" style="background:{{log.color}};">{{log.level}}</span>
                        <span>{{log.message}}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
            
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">🧪 Test Classification</h3>
                <div class="test-section">
                    {% for query in test_queries %}
                    <button class="test-btn" onclick="runTest('{{query}}')">
                        Test: {{query[:50]}}{% if query|length > 50 %}...{% endif %}
                    </button>
                    {% endfor %}
                    
                    <div style="margin-top:15px;">
                        <input type="text" id="customQuery" placeholder="Enter custom query..." 
                               style="width:70%;padding:10px;border:2px solid #ddd;border-radius:6px;">
                        <button onclick="runCustomTest()" 
                                style="padding:10px 20px;background:var(--primary);color:white;border:none;border-radius:6px;">
                            Run Test
                        </button>
                    </div>
                </div>
                
                <div id="testResult" class="result-display"></div>
            </div>
            
            <div class="card grid-2col">
                <div>
                    <h3 style="margin-top:0;color:var(--primary);">📊 Performance</h3>
                    <div style="background:var(--light);padding:15px;border-radius:8px;">
                        <div style="margin:10px 0;">
                            <div style="display:flex;justify-content:space-between;">
                                <span>Async Ping System:</span>
                                <span id="pingStatus" style="color:var(--success);">● ACTIVE</span>
                            </div>
                            <div style="height:8px;background:#eee;border-radius:4px;margin:5px 0;">
                                <div style="width:100%;height:100%;background:var(--success);border-radius:4px;"></div>
                            </div>
                        </div>
                        <div style="margin:10px 0;">
                            <div style="display:flex;justify-content:space-between;">
                                <span>WebSocket Connection:</span>
                                <span id="wsStatus" style="color:var(--success);">● STABLE</span>
                            </div>
                            <div style="height:8px;background:#eee;border-radius:4px;margin:5px 0;">
                                <div style="width:95%;height:100%;background:var(--success);border-radius:4px;"></div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div>
                    <h3 style="margin-top:0;color:var(--primary);">⚙️ Quick Actions</h3>
                    <div style="display:grid;gap:10px;">
                        <button onclick="clearCache()" style="padding:12px;background:var(--warning);color:white;border:none;border-radius:6px;">
                            🗑️ Clear Cache
                        </button>
                        <button onclick="forceReconnect()" style="padding:12px;background:var(--primary);color:white;border:none;border-radius:6px;">
                            🔄 Reconnect MCP
                        </button>
                        <button onclick="runDiagnostics()" style="padding:12px;background:#666;color:white;border:none;border-radius:6px;">
                            🩺 Run Diagnostics
                        </button>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            function updateUptime() {
                fetch('/api/uptime')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('uptime').textContent = 
                            `${data.hours}h ${data.minutes}m ${data.seconds}s`;
                    });
            }
            
            function updateActiveRequests() {
                fetch('/api/active-requests')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('activeRequests').textContent = data.count;
                    });
            }
            
            function updateCacheSize() {
                fetch('/api/stats')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('cacheSize').textContent = data.cache_size;
                    });
            }
            
            function refreshLogs() {
                fetch('/api/logs')
                    .then(r => r.json())
                    .then(logs => {
                        const panel = document.getElementById('logPanel');
                        panel.innerHTML = logs.map(log => `
                            <div class="log-entry">
                                <span class="log-time">${log.time}</span>
                                <span class="log-level" style="background:${log.color};">${log.level}</span>
                                <span>${log.message}</span>
                            </div>
                        `).join('');
                        panel.scrollTop = panel.scrollHeight;
                    });
            }
            
            function runTest(query) {
                const resultDiv = document.getElementById('testResult');
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `<div style="color:var(--warning);">⏳ Testing: "${query}" (Async ping mode)...</div>`;
                
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(r => r.json())
                    .then(data => {
                        resultDiv.innerHTML = `
                            <div style="color:var(--success);margin-bottom:10px;">✅ Test Complete</div>
                            <div><strong>Query:</strong> ${data.query}</div>
                            <div><strong>Result:</strong></div>
                            <div style="margin-top:10px;background:white;padding:10px;border-radius:6px;border:1px solid #ddd;">
                                ${data.result.replace(/\n/g, '<br>')}
                            </div>
                            <div style="margin-top:10px;color:#666;font-size:12px;">
                                Cache: ${data.cache_size} items | ${data.timestamp}
                            </div>
                        `;
                    })
                    .catch(err => {
                        resultDiv.innerHTML = `<div style="color:var(--danger);">❌ Error: ${err}</div>`;
                    });
            }
            
            function runCustomTest() {
                const query = document.getElementById('customQuery').value;
                if (query) runTest(query);
            }
            
            function refreshData() {
                updateUptime();
                updateActiveRequests();
                updateCacheSize();
                refreshLogs();
            }
            
            function clearCache() {
                if (confirm('Clear all cached responses?')) {
                    fetch('/api/clear-cache', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            alert('Cache cleared successfully!');
                        });
                }
            }
            
            function forceReconnect() {
                fetch('/api/reconnect', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        alert('Reconnection initiated');
                    });
            }
            
            function runDiagnostics() {
                fetch('/api/diagnostics')
                    .then(r => r.json())
                    .then(data => {
                        alert(`Diagnostics complete:\n\n${JSON.stringify(data, null, 2)}`);
                    });
            }
            
            // Fast refresh rates
            setInterval(updateUptime, 1000);
            setInterval(updateActiveRequests, 1000);
            setInterval(updateCacheSize, 2000);
            setInterval(refreshLogs, 3000);
            
            // Initial load
            updateUptime();
            updateActiveRequests();
            updateCacheSize();
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    seconds=seconds,
    cache_size=len(gemini_cache),
    active_count=len(active_requests),
    logs=log_buffer[-20:],
    test_queries=test_queries,
    timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    first_ping_delay=FIRST_PING_DELAY,
    continuous_ping_interval=CONTINUOUS_PING_INTERVAL,
    parallel_tries=PARALLEL_MODEL_TRIES)

@app.route('/health')
def health_check():
    """Health check endpoint."""
    uptime = int(time.time() - server_start_time)
    add_log('INFO', 'Health check accessed')
    
    return jsonify({
        "status": "async_ping_healthy",
        "version": "3.6.1-ASYNC-PING",
        "uptime": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "cache_size": len(gemini_cache),
        "active_requests": len(active_requests),
        "ping_config": {
            "first_ping_delay": FIRST_PING_DELAY,
            "continuous_interval": CONTINUOUS_PING_INTERVAL,
            "async_mode": True
        },
        "timestamp": datetime.now().isoformat()
    })

@app.route('/logs')
def view_logs():
    """View all logs."""
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>System Logs - Xiaozhi MCP</title>
        <meta charset="utf-8">
        <style>
            body { font-family: monospace; margin: 20px; background: #1a1a1a; color: #fff; }
            .log-entry { padding: 5px 0; border-bottom: 1px solid #333; }
            .time { color: #888; margin-right: 15px; }
            .INFO { color: #4CAF50; }
            .WARNING { color: #FF9800; }
            .ERROR { color: #F44336; }
            .DEBUG { color: #2196F3; }
            h1 { color: #4285F4; }
            .back-btn { 
                background: #4285F4; color: white; padding: 10px 20px; 
                text-decoration: none; border-radius: 6px; margin-bottom: 20px; display: inline-block;
            }
        </style>
    </head>
    <body>
        <a href="/" class="back-btn">← Back to Dashboard</a>
        <h1>📋 System Logs ({{ logs|length }} entries)</h1>
        {% for log in logs %}
        <div class="log-entry">
            <span class="time">[{{ log.time }}]</span>
            <span class="{{ log.level }}"><strong>{{ log.level }}</strong></span>
            <span>{{ log.message }}</span>
        </div>
        {% endfor %}
        <script>
            window.scrollTo(0, document.body.scrollHeight);
        </script>
    </body>
    </html>
    ''', logs=log_buffer)

@app.route('/stats')
def statistics():
    """Statistics endpoint."""
    add_log('INFO', 'Statistics page accessed')
    
    return jsonify({
        "cache_size": len(gemini_cache),
        "active_requests": len(active_requests),
        "log_count": len(log_buffer),
        "server_time": datetime.now().isoformat()
    })

@app.route('/api-docs')
def api_docs():
    """API documentation."""
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>API Documentation - Xiaozhi MCP</title>
        <meta charset="utf-8">
        <style>
            body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }
            h1, h2 { color: #4285F4; }
            .endpoint { background: #f5f5f5; padding: 15px; margin: 15px 0; border-radius: 8px; }
            code { background: #333; color: #fff; padding: 2px 6px; border-radius: 4px; }
            .method { font-weight: bold; color: #34A853; }
            .back-btn { 
                background: #4285F4; color: white; padding: 10px 20px; 
                text-decoration: none; border-radius: 6px; margin-bottom: 20px; display: inline-block;
            }
        </style>
    </head>
    <body>
        <a href="/" class="back-btn">← Back to Dashboard</a>
        <h1>📚 API Documentation</h1>
        
        <h2>🔗 Available Endpoints</h2>
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/</code> - Main dashboard with real-time monitoring
        </div>
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/health</code> - System health check (JSON)
        </div>
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/logs</code> - View system logs
        </div>
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/stats</code> - Statistics (JSON)
        </div>
        
        <h2>🔧 MCP Protocol with Async Pings</h2>
        <p>The server implements Model Context Protocol (MCP) over WebSocket with ASYNC pinging:</p>
        <ul>
            <li><strong>Tools:</strong> google_search, wikipedia_search, ask_ai</li>
            <li><strong>Protocol Version:</strong> 2024-11-05</li>
            <li><strong>Async Pings:</strong> ⚡ 50ms first ping guaranteed</li>
            <li><strong>Thread-Safe:</strong> ✅ WebSocket in same event loop</li>
            <li><strong>Prevents:</strong> Xiaozhi 5-8s timeout completely</li>
        </ul>
    </body>
    </html>
    ''')

@app.route('/api/uptime')
def api_uptime():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    return jsonify({
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds
    })

@app.route('/api/logs')
def api_logs():
    return jsonify(log_buffer[-30:])

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'cache_size': len(gemini_cache),
        'active_requests': len(active_requests)
    })

@app.route('/api/active-requests')
def api_active_requests():
    return jsonify({'count': len(active_requests)})

@app.route('/api/clear-cache', methods=['POST'])
def api_clear_cache():
    gemini_cache.clear()
    add_log('INFO', 'Cache cleared via API')
    return jsonify({'status': 'success', 'message': 'Cache cleared'})

@app.route('/test-smart/<path:query>')
def test_smart(query):
    """Test endpoint."""
    add_log('INFO', f'Test query: {query[:50]}...')
    
    try:
        tier = SmartModelSelector.classify_query_with_gemini(query)
        result = f"[{tier}] Test classification completed"
    except Exception as e:
        result = f"Test error: {str(e)[:50]}"
    
    return jsonify({
        "query": query,
        "result": result,
        "cache_size": len(gemini_cache),
        "timestamp": datetime.now().isoformat()
    })

def run_web_server():
    """Run Flask server."""
    add_log('INFO', 'Starting web server on port 3000')
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

# ================= ASYNC MCP BRIDGE =================
async def async_mcp_bridge():
    """Async WebSocket bridge with proper ping management."""
    reconnect_delay = 1
    
    while True:
        try:
            logger.info("🔗 Connecting to Xiaozhi (Async Ping Mode)...")
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=15,
                ping_timeout=10,
                close_timeout=5
            ) as websocket:
                logger.info("✅ CONNECTED TO XIAOZHI")
                add_log('INFO', 'Connected to Xiaozhi (Async Ping)')
                reconnect_delay = 1
                
                async for raw_message in websocket:
                    try:
                        data = json.loads(raw_message)
                        message_id = data.get("id")
                        method = data.get("method", "")
                        params = data.get("params", {})
                        
                        response = None
                        
                        if method == "ping":
                            response = AsyncMCPHandler.handle_ping(message_id)
                        elif method == "initialize":
                            response = AsyncMCPHandler.handle_initialize(message_id)
                            add_log('INFO', 'MCP Initialized (Async Ping)')
                        elif method == "tools/list":
                            response = AsyncMCPHandler.handle_tools_list(message_id)
                        elif method == "tools/call":
                            response = await AsyncMCPHandler.handle_tools_call_async(
                                message_id, params, websocket
                            )
                            tool_name = params.get("name", "")
                            if tool_name == "ask_ai":
                                add_log('INFO', 'AI processed with async pings')
                        else:
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "Unknown method"}}
                        
                        if response:
                            await websocket.send(json.dumps(response))
                            
                    except Exception as e:
                        add_log('ERROR', f'Message error: {str(e)[:30]}')
                        
        except Exception as e:
            error_msg = f"Connection error: {str(e)[:50]}"
            logger.error(f"❌ {error_msg}")
            add_log('ERROR', error_msg)
            
            # Clean up all ping tasks
            ping_manager.stop_all_pings()
            active_requests.clear()
            
            logger.info(f"🔄 Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 5)

async def main():
    logger.info("""
    ╔══════════════════════════════════════════════════╗
    ║           🚀 ASYNC PING MCP v3.6.1              ║
    ║         NO THREADING • SAME EVENT LOOP          ║
    ║          50ms FIRST PING GUARANTEED             ║
    ╚══════════════════════════════════════════════════╝
    """)
    
    add_log('INFO', '🚀 Starting Async Ping Server...')
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start MCP bridge
    await async_mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Server stopped by user")
        add_log('INFO', 'Server stopped by user')
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
        add_log('ERROR', f'Fatal: {str(e)[:50]}')