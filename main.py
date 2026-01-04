# main.py - XIAOZHI MCP SERVER v3.6.1 - TURBO MODE
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
KEEPALIVE_INTERVAL = 1.0  # 1 SECOND PINGS - CRAZY MODE
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

# ================= PARALLEL GEMINI PROCESSOR =================
class ParallelGeminiProcessor:
    """Process Gemini requests in parallel with turbo speed."""
    
    TIERS = {
        "HARD": "gemini-3.0-flash",
        "MEDIUM": "gemini-2.5-flash", 
        "SIMPLE": "gemini-2.5-flash-lite"
    }
    
    @staticmethod
    def classify_query_turbo(query):
        """Ultra-fast classification with fallback."""
        # SIMPLE RULE-BASED CLASSIFICATION FOR SPEED
        query_lower = query.lower()
        words = query_lower.split()
        
        # Hard queries: long or complex keywords
        hard_keywords = ['explain', 'analyze', 'compare', 'write code', 'debug', 
                        'complex', 'detailed', 'research', 'thesis', 'essay']
        
        # Simple queries: short or basic
        simple_keywords = ['hello', 'hi', 'thanks', 'weather', 'time', 'date',
                          'calculate', 'convert', 'joke', 'quote', 'fact']
        
        if any(keyword in query_lower for keyword in hard_keywords) or len(words) > 15:
            return "HARD"
        elif any(keyword in query_lower for keyword in simple_keywords) or len(words) < 5:
            return "SIMPLE"
        else:
            return "MEDIUM"
    
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
                },
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
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
                # INSTANT TURBO PROCESSING
                tier = ParallelGeminiProcessor.classify_query_turbo(query)
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

# ================= TURBO WEB SERVER =================
app = Flask(__name__)
server_start_time = time.time()
log_buffer = []
MAX_LOG_LINES = 50

def add_log(level, message):
    """Fast logging."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {
        'time': timestamp,
        'level': level,
        'message': message,
        'color': {
            'INFO': '#4CAF50',
            'WARNING': '#FF9800',
            'ERROR': '#F44336'
        }.get(level, '#757575')
    }
    log_buffer.append(log_entry)
    if len(log_buffer) > MAX_LOG_LINES:
        log_buffer.pop(0)

# Initialize
add_log('INFO', '🚀 TURBO SERVER STARTING...')
add_log('INFO', f'Gemini: {"✅ CONFIGURED" if GEMINI_API_KEY else "❌ MISSING"}')
add_log('INFO', f'Turbo Mode: {"✅ ENABLED" if TURBO_MODE else "❌ DISABLED"}')

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🚀 TURBO MCP v3.6.1</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { 
                font-family: monospace; 
                margin: 20px; 
                background: #000; 
                color: #0f0;
                overflow: hidden;
            }
            .header {
                background: linear-gradient(90deg, #ff0080, #00ff80);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-size: 36px;
                margin-bottom: 10px;
                animation: glitch 0.5s infinite;
            }
            @keyframes glitch {
                0% { transform: translate(0); }
                20% { transform: translate(-2px, 2px); }
                40% { transform: translate(-2px, -2px); }
                60% { transform: translate(2px, 2px); }
                80% { transform: translate(2px, -2px); }
                100% { transform: translate(0); }
            }
            .status {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 10px;
                margin: 20px 0;
            }
            .stat-box {
                background: #111;
                border: 2px solid #0f0;
                padding: 15px;
                border-radius: 5px;
                text-align: center;
            }
            .stat-value {
                font-size: 24px;
                color: #0ff;
                font-weight: bold;
            }
            .log-panel {
                height: 300px;
                overflow-y: auto;
                background: #111;
                border: 2px solid #0f0;
                padding: 10px;
                margin-top: 20px;
                font-family: 'Courier New', monospace;
            }
            .log-entry {
                padding: 3px 0;
                border-bottom: 1px solid #333;
            }
            .log-time { color: #888; }
            .INFO { color: #0f0; }
            .WARNING { color: #ff0; }
            .ERROR { color: #f00; }
            .turbo-indicator {
                color: #f0f;
                animation: pulse 0.5s infinite;
                font-weight: bold;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
        </style>
    </head>
    <body>
        <div class="header">🚀 TURBO MCP v3.6.1</div>
        <div class="turbo-indicator">⚡ TURBO MODE: 1s PINGS ⚡</div>
        
        <div class="status">
            <div class="stat-box">
                <div>UPTIME</div>
                <div class="stat-value">{{hours}}h {{minutes}}m</div>
            </div>
            <div class="stat-box">
                <div>CACHE</div>
                <div class="stat-value">{{cache_size}}</div>
            </div>
            <div class="stat-box">
                <div>ACTIVE</div>
                <div class="stat-value" id="activeCount">0</div>
            </div>
            <div class="stat-box">
                <div>STATUS</div>
                <div class="stat-value" style="color:#0f0;">✅ LIVE</div>
            </div>
        </div>
        
        <div class="log-panel" id="logPanel">
            {% for log in logs %}
            <div class="log-entry">
                <span class="log-time">[{{log.time}}]</span>
                <span class="{{log.level}}">{{log.message}}</span>
            </div>
            {% endfor %}
        </div>
        
        <script>
            function updateStats() {
                fetch('/api/stats')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('activeCount').textContent = data.active_requests;
                    });
            }
            
            function updateLogs() {
                fetch('/api/logs')
                    .then(r => r.json())
                    .then(logs => {
                        const panel = document.getElementById('logPanel');
                        panel.innerHTML = logs.map(log => `
                            <div class="log-entry">
                                <span class="log-time">[${log.time}]</span>
                                <span class="${log.level}">${log.message}</span>
                            </div>
                        `).join('');
                        panel.scrollTop = panel.scrollHeight;
                    });
            }
            
            // TURBO REFRESH RATES
            setInterval(updateStats, 1000);  // 1 second
            setInterval(updateLogs, 2000);   // 2 seconds
            
            // Initial load
            updateStats();
            updateLogs();
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    cache_size=len(gemini_cache),
    logs=log_buffer[-15:])

@app.route('/health')
def health():
    return jsonify({
        "status": "turbo",
        "version": "3.6.1-TURBO",
        "keepalive": "1s pings",
        "parallel": PARALLEL_MODEL_TRIES,
        "cache": len(gemini_cache),
        "active": len(active_requests)
    })

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'active_requests': len(active_requests),
        'cache_size': len(gemini_cache)
    })

@app.route('/api/logs')
def api_logs():
    return jsonify(log_buffer[-20:])

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

def run_web_server():
    """Run Flask server."""
    add_log('INFO', '🌐 WEB SERVER STARTING (PORT 3000)')
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)

async def main():
    logger.info("""
    ╔══════════════════════════════════════════════════╗
    ║              🚀 TURBO MCP v3.6.1                 ║
    ║          1s PINGS • PARALLEL PROCESSING          ║
    ║         MAX TOKENS • NO TIMEOUTS                 ║
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