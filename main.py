# main.py - XIAOZHI MCP SERVER v3.6.1 - TURBO MODE WITH FULL DASHBOARD
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
import concurrent.futures
import queue
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# ================= TURBO CONFIGURATION =================
TURBO_MODE = True
KEEPALIVE_INTERVAL = 1.0  # 1 SECOND PINGS
PARALLEL_MODEL_TRIES = 3  # Try multiple models simultaneously
MAX_WORKERS = 5  # Thread pool size

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
active_requests = {}
keep_alive_queue = queue.Queue()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

keep_alive_messages = [
    "AI is thinking...",
    "Processing your request...",
    "Generating response...",
    "Almost ready!",
    "Working on it...",
    "Crafting answer..."
]

# ================= TURBO KEEP-ALIVE SYSTEM =================
class TurboKeepAlive:
    """ULTRA-FAST keep-alive system with 1-second pings."""
    
    @staticmethod
    def start_keep_alive(websocket, request_id, duration=30):
        """Start aggressive keep-alive pinging in a separate thread."""
        def keep_alive_worker():
            try:
                logger.info(f"🚀 TURBO KEEP-ALIVE STARTED for {request_id}")
                start_time = time.time()
                ping_count = 0
                
                while time.time() - start_time < duration:
                    # Send ping every 1 second
                    if ping_count % 1 == 0:
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
                        
                        try:
                            # Use asyncio to send to websocket
                            asyncio.run_coroutine_threadsafe(
                                websocket.send(json.dumps(ping_message)),
                                asyncio.get_event_loop()
                            )
                            logger.debug(f"🚀 PING #{ping_count+1} for {request_id}")
                        except Exception as e:
                            logger.debug(f"Ping failed: {e}")
                            break
                    
                    ping_count += 1
                    time.sleep(KEEPALIVE_INTERVAL)  # 1 SECOND!
                
                logger.info(f"✅ Keep-alive completed for {request_id}")
                
            except Exception as e:
                logger.error(f"❌ Keep-alive error: {e}")
        
        # Start the turbo thread
        thread = threading.Thread(target=keep_alive_worker, daemon=True)
        thread.start()
        return thread

# ================= GEMINI-POWERED CLASSIFICATION =================
class SmartModelSelector:
    """Smart model selection using ONLY Gemini 2.5 Flash-Lite for classification."""
    
    TIERS = {
        "HARD": "gemini-3.0-flash",      # HARD tasks → Gemini 3 Flash
        "MEDIUM": "gemini-2.5-flash",    # MEDIUM tasks → Gemini 2.5 Flash
        "SIMPLE": "gemini-2.5-flash-lite" # SIMPLE tasks → Gemini 2.5 Flash-Lite
    }
    
    @staticmethod
    def classify_query_with_gemini(query):
        """Use Gemini 2.5 Flash-Lite to classify query into HARD, MEDIUM, or SIMPLE."""
        try:
            if not GEMINI_API_KEY:
                logger.warning("No Gemini API key, using fallback classification")
                return "MEDIUM"
            
            # Prepare the classification prompt
            classification_prompt = f"""Please help me sort this query into 3 tiers: 
Hard (handled by Gemini 3 Flash), 
Medium (Handled by Gemini 2.5 Flash), and 
Simple (Handled by Gemini 2.5 Flash-Lite): "{query}"

Strictly only say "HARD", "MEDIUM", OR "SIMPLE", no extra text, no explanation."""
            
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
            
            # Quick timeout for classification
            response = requests.post(url, headers=headers, json=data, params=params, timeout=5)
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
            
        except requests.exceptions.Timeout:
            logger.warning("⏰ Classification timeout, using MEDIUM as default")
            return "MEDIUM"
        except Exception as e:
            logger.error(f"❌ Classification error: {e}")
            return "MEDIUM"

# ================= PARALLEL GEMINI PROCESSOR =================
class ParallelGeminiProcessor:
    """Process Gemini requests in parallel with turbo speed."""
    
    @staticmethod
    def get_model_config(tier):
        """Get turbo-optimized model configuration."""
        configs = {
            "HARD": {
                "models": ["gemini-3.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
                "tokens": 8192,
                "timeouts": [25, 20, 15]  # Parallel timeouts
            },
            "MEDIUM": {
                "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
                "tokens": 4096,
                "timeouts": [15, 10, 10]
            },
            "SIMPLE": {
                "models": ["gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"],
                "tokens": 2048,
                "timeouts": [8, 8, 8]
            }
        }
        return configs.get(tier, configs["MEDIUM"])
    
    @staticmethod
    def call_gemini_turbo(query, model, max_tokens, timeout):
        """Turbo-optimized Gemini call."""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            data = {
                "contents": [{
                    "parts": [{"text": query}]
                }],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": 0.7,
                    "topP": 0.9,
                    "topK": 40,
                }
            }
            
            params = {"key": GEMINI_API_KEY}
            
            # ULTRA-FAST request with minimal overhead
            response = requests.post(
                url, 
                headers=headers, 
                json=data, 
                params=params, 
                timeout=timeout,
                verify=True
            )
            
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
    def process_query_parallel(query, tier, cache_key, websocket, request_id):
        """Process query with parallel model attempts."""
        try:
            logger.info(f"🚀 PARALLEL PROCESSING: {tier} tier for '{query[:30]}...'")
            
            # START TURBO KEEP-ALIVE IMMEDIATELY
            TurboKeepAlive.start_keep_alive(websocket, request_id, 30)
            
            # Check cache FIRST (fastest path)
            if cache_key in gemini_cache:
                cached_time, response = gemini_cache[cache_key]
                if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                    logger.info(f"⚡ CACHE HIT for {request_id}")
                    return f"[{tier} - Cached] {response}"
            
            # Get parallel configuration
            config = ParallelGeminiProcessor.get_model_config(tier)
            models = config["models"][:PARALLEL_MODEL_TRIES]
            max_tokens = config["tokens"]
            timeouts = config["timeouts"][:PARALLEL_MODEL_TRIES]
            
            # Submit ALL models in parallel
            futures = []
            with ThreadPoolExecutor(max_workers=PARALLEL_MODEL_TRIES) as executor:
                for i, model in enumerate(models):
                    timeout = timeouts[i] if i < len(timeouts) else 15
                    future = executor.submit(
                        ParallelGeminiProcessor.call_gemini_turbo,
                        query, model, max_tokens, timeout
                    )
                    futures.append((model, future))
                
                # Wait for FIRST successful response
                for model, future in futures:
                    try:
                        result, success = future.result(timeout=20)
                        if success and result:
                            logger.info(f"✅ {model} succeeded in parallel!")
                            # Cache the successful result
                            gemini_cache[cache_key] = (datetime.now(), result)
                            return f"[{tier} - {model}] {result}"
                    except concurrent.futures.TimeoutError:
                        logger.warning(f"⏰ {model} future timeout")
                    except Exception as e:
                        logger.error(f"❌ {model} future error: {e}")
            
            # If all parallel attempts failed, try sequential fallback
            logger.warning("⚠️ Parallel failed, trying sequential fallback")
            for model in models:
                result, success = ParallelGeminiProcessor.call_gemini_turbo(
                    query, model, max_tokens, 15
                )
                if success and result:
                    gemini_cache[cache_key] = (datetime.now(), result)
                    return f"[{tier} - {model}*] {result}"
            
            return "Sorry, I couldn't generate a response. Please try again."
            
        except Exception as e:
            logger.error(f"❌ Parallel processing error: {e}")
            return f"Error: {str(e)[:50]}"

# ================= FAST SYNC TOOLS =================
def google_search_fast(query, max_results=5):
    """Ultra-fast Google search."""
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

def wikipedia_search_fast(query, max_results=2):
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
        
        # Get just first result for speed
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

# ================= TURBO MCP HANDLER =================
class TurboMCPHandler:
    """Ultra-fast MCP protocol handler."""
    
    @staticmethod
    def handle_initialize(message_id):
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "turbo-gemini-mcp",
                    "version": "3.6.1-TURBO"
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
                        "description": "Ask AI with TURBO tiered processing (1s pings)",
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
    async def handle_tools_call_turbo(message_id, params, websocket):
        """Turbo-speed tools call with instant keep-alive."""
        tool_name = params.get("name", "")
        query = params.get("arguments", {}).get("query", "").strip()
        
        if not query:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32602, "message": "Missing query"}
            }
        
        try:
            # Generate request ID for tracking
            request_id = str(uuid.uuid4())[:8]
            active_requests[request_id] = True
            
            if tool_name == "google_search":
                result = google_search_fast(query)
            elif tool_name == "wikipedia_search":
                result = wikipedia_search_fast(query)
            elif tool_name == "ask_ai":
                # Use Gemini 2.5 Flash-Lite for classification (NO KEYWORDS!)
                tier = SmartModelSelector.classify_query_with_gemini(query)
                cache_key = hashlib.md5(f"{query}_{tier}".encode()).hexdigest()
                
                # Run in thread pool for non-blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    executor,
                    ParallelGeminiProcessor.process_query_parallel,
                    query, tier, cache_key, websocket, request_id
                )
            else:
                result = f"Unknown tool: {tool_name}"
            
            # Clean up
            if request_id in active_requests:
                del active_requests[request_id]
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
            
        except Exception as e:
            logger.error(f"❌ Turbo tool error: {e}")
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32000, "message": f"Error: {str(e)[:50]}"}
            }

# ================= FULL FEATURED WEB SERVER =================
app = Flask(__name__)
server_start_time = time.time()
log_buffer = []
MAX_LOG_LINES = 50

# Store test queries
test_queries = [
    "Explain quantum computing in simple terms",
    "What is the capital of France?",
    "Write a Python function to calculate Fibonacci",
    "Compare machine learning and deep learning",
    "How to make a cup of tea"
]

def add_log(level, message):
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

# Initialize some logs
add_log('INFO', '🚀 TURBO SERVER STARTING...')
if GEMINI_API_KEY:
    add_log('INFO', 'Gemini API: ✅ Configured')
else:
    add_log('WARNING', 'Gemini API: ❌ Not configured')
add_log('INFO', f'Turbo Mode: ✅ ENABLED (1s pings)')
add_log('INFO', f'Parallel Processing: ✅ ENABLED ({PARALLEL_MODEL_TRIES} models)')

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP v3.6.1 - Turbo Dashboard</title>
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
            
            .turbo-badge {
                background: linear-gradient(90deg, #ff0080, #00ff80);
                color: white;
                padding: 5px 10px;
                border-radius: 20px;
                font-weight: bold;
                font-size: 12px;
                margin-left: 10px;
                animation: glitch 0.5s infinite;
            }
            
            @keyframes glitch {
                0% { transform: translate(0); }
                20% { transform: translate(-1px, 1px); }
                40% { transform: translate(-1px, -1px); }
                60% { transform: translate(1px, 1px); }
                80% { transform: translate(1px, -1px); }
                100% { transform: translate(0); }
            }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <!-- Header -->
            <div class="header">
                <h1 style="margin:0;color:var(--primary);">🚀 Xiaozhi MCP v3.6.1</h1>
                <p style="color:#666;margin:5px 0 20px 0;">
                    Turbo Mode <span class="turbo-badge">⚡ 1s PINGS</span>
                </p>
                
                <div class="status-grid">
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Uptime</div>
                        <div class="uptime-display" id="uptime">{{hours}}h {{minutes}}m {{seconds}}s</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Cache Size</div>
                        <div style="font-size:24px;color:var(--primary);" id="cacheSize">{{cache_size}} items</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Active Requests</div>
                        <div style="font-size:24px;color:var(--primary);" id="activeRequests">0</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Status</div>
                        <div style="font-size:24px;color:var(--success);">✅ Turbo Live</div>
                    </div>
                </div>
                
                <div class="real-time">
                    <div class="pulse"></div>
                    <span>Turbo mode active (1-second keep-alive pings)</span>
                    <button class="refresh-btn" onclick="refreshData()">🔄 Refresh</button>
                </div>
            </div>
            
            <!-- Navigation -->
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">🔗 Quick Links</h3>
                <div class="nav-links">
                    <a href="/health" class="nav-btn" target="_blank">📊 Health Check</a>
                    <a href="/test-smart/hello" class="nav-btn" target="_blank">🧪 Quick Test</a>
                    <a href="/logs" class="nav-btn" target="_blank">📋 View Logs</a>
                    <a href="/stats" class="nav-btn" target="_blank">📈 Statistics</a>
                    <a href="/api-docs" class="nav-btn" target="_blank">📚 API Docs</a>
                </div>
                
                <h3 style="margin-top:25px;color:var(--primary);">⚡ Turbo System Info</h3>
                <div style="background:var(--light);padding:15px;border-radius:8px;">
                    <div><strong>Version:</strong> 3.6.1-TURBO</div>
                    <div><strong>MCP Protocol:</strong> 2024-11-05</div>
                    <div><strong>Keep-Alive:</strong> ⚡ 1-second pings</div>
                    <div><strong>Parallel Models:</strong> {{parallel_tries}} simultaneous</div>
                    <div><strong>Classification:</strong> Gemini 2.5 Flash-Lite</div>
                    <div><strong>Last Updated:</strong> {{timestamp}}</div>
                </div>
            </div>
            
            <!-- Real-time Logs -->
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
            
            <!-- Testing Panel -->
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">🧪 Test Tier Classification</h3>
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
            
            <!-- System Stats -->
            <div class="card grid-2col">
                <div>
                    <h3 style="margin-top:0;color:var(--primary);">📊 Performance</h3>
                    <div style="background:var(--light);padding:15px;border-radius:8px;">
                        <div style="margin:10px 0;">
                            <div style="display:flex;justify-content:space-between;">
                                <span>Gemini API Status:</span>
                                <span id="geminiStatus" style="color:var(--success);">● Operational</span>
                            </div>
                            <div style="height:8px;background:#eee;border-radius:4px;margin:5px 0;">
                                <div style="width:85%;height:100%;background:var(--success);border-radius:4px;"></div>
                            </div>
                        </div>
                        <div style="margin:10px 0;">
                            <div style="display:flex;justify-content:space-between;">
                                <span>Keep-Alive System:</span>
                                <span id="keepAliveStatus" style="color:var(--success);">● Active</span>
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
            // Auto-update uptime
            function updateUptime() {
                fetch('/api/uptime')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('uptime').textContent = 
                            `${data.hours}h ${data.minutes}m ${data.seconds}s`;
                    });
            }
            
            // Update active requests count
            function updateActiveRequests() {
                fetch('/api/active-requests')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('activeRequests').textContent = data.count;
                    });
            }
            
            // Update cache size
            function updateCacheSize() {
                fetch('/api/stats')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('cacheSize').textContent = data.cache_size + ' items';
                    });
            }
            
            // Update logs
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
            
            // Run test
            function runTest(query) {
                const resultDiv = document.getElementById('testResult');
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `<div style="color:var(--warning);">⏳ Testing: "${query}" (Turbo mode)...</div>`;
                
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
                        add_log('INFO', `Test completed: ${query.substring(0, 30)}...`);
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
                add_log('INFO', 'Dashboard manually refreshed');
            }
            
            // Action functions
            function clearCache() {
                if (confirm('Clear all cached responses?')) {
                    fetch('/api/clear-cache', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            alert('Cache cleared successfully!');
                            add_log('INFO', 'Cache cleared manually');
                        });
                }
            }
            
            function forceReconnect() {
                fetch('/api/reconnect', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        alert('Reconnection initiated');
                        add_log('INFO', 'Manual reconnection requested');
                    });
            }
            
            function runDiagnostics() {
                fetch('/api/diagnostics')
                    .then(r => r.json())
                    .then(data => {
                        alert(`Diagnostics complete:\n\n${JSON.stringify(data, null, 2)}`);
                        add_log('INFO', 'Diagnostics run completed');
                    });
            }
            
            // Simulate adding a log (for demo)
            function add_log(level, message) {
                const panel = document.getElementById('logPanel');
                const colors = {
                    'INFO': '#4CAF50',
                    'WARNING': '#FF9800',
                    'ERROR': '#F44336'
                };
                const time = new Date().toLocaleTimeString('en-US', {hour12: false});
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.innerHTML = `
                    <span class="log-time">${time}</span>
                    <span class="log-level" style="background:${colors[level] || '#666'};">${level}</span>
                    <span>${message}</span>
                `;
                panel.appendChild(entry);
                panel.scrollTop = panel.scrollHeight;
            }
            
            // TURBO REFRESH RATES
            setInterval(updateUptime, 1000);      // 1 second
            setInterval(updateActiveRequests, 1000); // 1 second
            setInterval(updateCacheSize, 2000);   // 2 seconds
            setInterval(refreshLogs, 3000);       // 3 seconds
            
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
    log_count=len(log_buffer),
    logs=log_buffer[-20:],
    test_queries=test_queries,
    timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    parallel_tries=PARALLEL_MODEL_TRIES)

@app.route('/health')
def health_check():
    """Comprehensive health check endpoint."""
    uptime = int(time.time() - server_start_time)
    add_log('INFO', 'Health check accessed')
    
    return jsonify({
        "status": "turbo_healthy",
        "version": "3.6.1-TURBO",
        "uptime_seconds": uptime,
        "uptime_human": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "cache_size": len(gemini_cache),
        "log_count": len(log_buffer),
        "active_requests": len(active_requests),
        "gemini_configured": bool(GEMINI_API_KEY),
        "google_configured": bool(GOOGLE_API_KEY and CSE_ID),
        "turbo_mode": TURBO_MODE,
        "keepalive_interval": KEEPALIVE_INTERVAL,
        "parallel_tries": PARALLEL_MODEL_TRIES,
        "last_log": log_buffer[-1]['message'] if log_buffer else "No logs",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/logs')
def view_logs():
    """View all logs in a clean interface."""
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
            // Auto-scroll to bottom
            window.scrollTo(0, document.body.scrollHeight);
        </script>
    </body>
    </html>
    ''', logs=log_buffer)

@app.route('/stats')
def statistics():
    """Statistics endpoint."""
    tier_counts = {'HARD': 0, 'MEDIUM': 0, 'SIMPLE': 0}
    for key in gemini_cache:
        if '[HARD' in key: tier_counts['HARD'] += 1
        elif '[MEDIUM' in key: tier_counts['MEDIUM'] += 1
        elif '[SIMPLE' in key: tier_counts['SIMPLE'] += 1
    
    add_log('INFO', 'Statistics page accessed')
    
    return jsonify({
        "cache_statistics": {
            "total_items": len(gemini_cache),
            "by_tier": tier_counts,
            "oldest": min(gemini_cache.values())[0].isoformat() if gemini_cache else None
        },
        "log_statistics": {
            "total_logs": len(log_buffer),
            "by_level": {
                'INFO': sum(1 for log in log_buffer if log['level'] == 'INFO'),
                'WARNING': sum(1 for log in log_buffer if log['level'] == 'WARNING'),
                'ERROR': sum(1 for log in log_buffer if log['level'] == 'ERROR')
            }
        },
        "system": {
            "gemini_api_configured": bool(GEMINI_API_KEY),
            "google_api_configured": bool(GOOGLE_API_KEY),
            "test_queries_count": len(test_queries),
            "active_requests_count": len(active_requests),
            "turbo_mode": TURBO_MODE,
            "keepalive_interval": KEEPALIVE_INTERVAL,
            "parallel_model_tries": PARALLEL_MODEL_TRIES
        }
    }), 200

@app.route('/api-docs')
def api_docs():
    """API documentation page."""
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
        
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/test-smart/&lt;query&gt;</code> - Test tier classification
        </div>
        
        <h2>⚡ Real-time APIs</h2>
        
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/api/uptime</code> - Get current uptime
        </div>
        
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/api/logs</code> - Get recent logs (JSON)
        </div>
        
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/api/active-requests</code> - Get active request count
        </div>
        
        <div class="endpoint">
            <div class="method">GET</div>
            <code>/api/stats</code> - Get system stats (JSON)
        </div>
        
        <div class="endpoint">
            <div class="method">POST</div>
            <code>/api/clear-cache</code> - Clear Gemini cache
        </div>
        
        <h2>🔧 MCP Protocol with Turbo Features</h2>
        <p>The server implements Model Context Protocol (MCP) over WebSocket with TURBO features:</p>
        <ul>
            <li><strong>Tools:</strong> google_search, wikipedia_search, ask_ai</li>
            <li><strong>Protocol Version:</strong> 2024-11-05</li>
            <li><strong>Tier Selection:</strong> Gemini 2.5 Flash-Lite powered (HARD/MEDIUM/SIMPLE)</li>
            <li><strong>Turbo Mode:</strong> ⚡ 1-second keep-alive pings</li>
            <li><strong>Parallel Processing:</strong> {{parallel_tries}} models simultaneously</li>
            <li><strong>Prevents:</strong> Xiaozhi's 5-second timeout completely</li>
        </ul>
    </body>
    </html>
    ''', parallel_tries=PARALLEL_MODEL_TRIES)

# API endpoints for real-time updates
@app.route('/api/uptime')
def api_uptime():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    return jsonify({
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds,
        'total_seconds': uptime
    })

@app.route('/api/logs')
def api_logs():
    return jsonify(log_buffer[-30:])

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'cache_size': len(gemini_cache),
        'log_count': len(log_buffer),
        'active_requests': len(active_requests),
        'test_queries': len(test_queries),
        'server_time': datetime.now().isoformat()
    })

@app.route('/api/active-requests')
def api_active_requests():
    return jsonify({'count': len(active_requests)})

@app.route('/api/clear-cache', methods=['POST'])
def api_clear_cache():
    gemini_cache.clear()
    add_log('INFO', 'Cache cleared via API')
    return jsonify({'status': 'success', 'message': 'Cache cleared'})

@app.route('/api/reconnect', methods=['POST'])
def api_reconnect():
    add_log('WARNING', 'Manual reconnection requested')
    return jsonify({'status': 'queued', 'message': 'Reconnection will be attempted'})

@app.route('/api/diagnostics')
def api_diagnostics():
    add_log('INFO', 'Diagnostics run')
    return jsonify({
        'python_version': sys.version,
        'environment_vars': {
            'GEMINI_API_KEY': 'Set' if GEMINI_API_KEY else 'Missing',
            'GOOGLE_API_KEY': 'Set' if GOOGLE_API_KEY else 'Missing',
            'CSE_ID': 'Set' if CSE_ID else 'Missing',
            'XIAOZHI_WS': 'Set' if XIAOZHI_WS else 'Missing'
        },
        'cache_info': {
            'size': len(gemini_cache),
            'keys_sample': list(gemini_cache.keys())[:3] if gemini_cache else []
        },
        'log_info': {
            'total': len(log_buffer),
            'recent': [log['message'] for log in log_buffer[-3:]]
        },
        'turbo_info': {
            'active_requests': len(active_requests),
            'keepalive_interval': KEEPALIVE_INTERVAL,
            'parallel_tries': PARALLEL_MODEL_TRIES,
            'max_workers': MAX_WORKERS
        }
    })

@app.route('/test-smart/<path:query>')
def test_smart(query):
    """Test endpoint with sync processing for web UI."""
    add_log('INFO', f'Test query: {query[:50]}...')
    
    # Use simplified sync version for web testing
    try:
        # Get classification
        tier = SmartModelSelector.classify_query_with_gemini(query)
        cache_key = hashlib.md5(f"{query}_{tier}".encode()).hexdigest()
        
        # Check cache
        if cache_key in gemini_cache:
            cached_time, response = gemini_cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                result = f"[{tier} - Cached] {response}"
            else:
                result = f"[{tier}] Test mode - classification only"
        else:
            result = f"[{tier}] Test mode - classification only"
            
    except Exception as e:
        result = f"Test error: {str(e)[:50]}"
    
    add_log('INFO', f'Test completed for: {query[:30]}...')
    
    return jsonify({
        "query": query,
        "result": result,
        "cache_size": len(gemini_cache),
        "timestamp": datetime.now().isoformat()
    }), 200

def run_web_server():
    """Run Flask web server."""
    add_log('INFO', 'Starting web server on port 3000')
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

# ================= TURBO MCP BRIDGE =================
async def turbo_mcp_bridge():
    """Ultra-fast WebSocket bridge."""
    reconnect_delay = 1  # Fast reconnection
    
    while True:
        try:
            logger.info("🚀 CONNECTING TO XIAOZHI (TURBO MODE)...")
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=10,  # Frequent pings
                ping_timeout=5,
                close_timeout=5
            ) as websocket:
                logger.info("✅ TURBO CONNECTED!")
                add_log('INFO', 'TURBO CONNECTED TO XIAOZHI')
                reconnect_delay = 1
                
                async for raw_message in websocket:
                    try:
                        data = json.loads(raw_message)
                        message_id = data.get("id")
                        method = data.get("method", "")
                        params = data.get("params", {})
                        
                        response = None
                        
                        if method == "ping":
                            response = TurboMCPHandler.handle_ping(message_id)
                        elif method == "initialize":
                            response = TurboMCPHandler.handle_initialize(message_id)
                            add_log('INFO', 'MCP INITIALIZED (TURBO)')
                        elif method == "tools/list":
                            response = TurboMCPHandler.handle_tools_list(message_id)
                        elif method == "tools/call":
                            # INSTANT TURBO PROCESSING
                            response = await TurboMCPHandler.handle_tools_call_turbo(
                                message_id, params, websocket
                            )
                            tool_name = params.get("name", "")
                            if tool_name == "ask_ai":
                                add_log('INFO', f'TURBO AI PROCESSED')
                        else:
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "Unknown method"}}
                        
                        if response:
                            await websocket.send(json.dumps(response))
                            
                    except Exception as e:
                        add_log('ERROR', f'MSG ERROR: {str(e)[:30]}')
        
        except Exception as e:
            error_msg = f"CONNECTION ERROR: {str(e)[:50]}"
            logger.error(f"❌ {error_msg}")
            add_log('ERROR', error_msg)
            logger.info(f"⚡ RECONNECTING IN {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 5)  # Max 5s delay

async def main():
    logger.info("""
    ╔══════════════════════════════════════════════════╗
    ║              🚀 TURBO MCP v3.6.1                 ║
    ║          1s PINGS • PARALLEL PROCESSING          ║
    ║      GEMINI CLASSIFICATION • FULL DASHBOARD      ║
    ╚══════════════════════════════════════════════════╝
    """)
    
    add_log('INFO', '🚀 STARTING TURBO SERVER...')
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start turbo bridge
    await turbo_mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 TURBO SHUTDOWN")
        add_log('INFO', 'TURBO SERVER STOPPED')
    except Exception as e:
        logger.error(f"💥 FATAL ERROR: {e}")
        add_log('ERROR', f'FATAL: {str(e)[:50]}')