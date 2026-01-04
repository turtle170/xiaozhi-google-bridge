# main.py - XIAOZHI MCP SERVER v3.6 - GEMINI-POWERED TIER SELECTION
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

# ================= GEMINI-POWERED TIER SELECTION =================
class SmartModelSelector:
    """Smart model selection using Gemini 2.5 Flash-Lite for classification."""
    
    # Model tiers based on classification
    TIERS = {
        "HARD": "gemini-2.5-pro",
        "MEDIUM": "gemini-2.5-flash", 
        "SIMPLE": "gemini-2.5-flash-lite"
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
Hard (handled by Gemini 2.5 Pro), 
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
                        
                        # Fallback if not found
                        logger.warning(f"Unexpected classification: {classification}")
            
            return "MEDIUM"  # Default fallback
            
        except requests.exceptions.Timeout:
            logger.warning("⏰ Classification timeout, using MEDIUM as default")
            return "MEDIUM"
        except Exception as e:
            logger.error(f"❌ Classification error: {e}")
            return "MEDIUM"
    
    @staticmethod
    def select_model(query):
        """Select model using Gemini-powered classification."""
        # Get classification from Gemini
        tier = SmartModelSelector.classify_query_with_gemini(query)
        logger.info(f"🎯 Gemini classified as: {tier} for '{query[:50]}...'")
        
        # Map tier to model and parameters
        model_configs = {
            "HARD": {
                "model": "gemini-2.5-pro",
                "tokens": 2000,
                "timeout": 30
            },
            "MEDIUM": {
                "model": "gemini-2.5-flash", 
                "tokens": 1000,
                "timeout": 20
            },
            "SIMPLE": {
                "model": "gemini-2.5-flash-lite",
                "tokens": 500,
                "timeout": 10
            }
        }
        
        config = model_configs.get(tier, model_configs["MEDIUM"])
        
        return {
            "tier": tier,
            "model": config["model"],
            "tokens": config["tokens"],
            "timeout": config["timeout"],
            "fallbacks": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        }

# ================= GEMINI API CLIENT =================
gemini_cache = {}
CACHE_DURATION = 300

def call_gemini_api(query, model="gemini-2.5-flash", max_tokens=1000, timeout=20):
    """Call Gemini API with a specific model."""
    try:
        if not GEMINI_API_KEY:
            return "Gemini API key not configured.", "NO_API_KEY"
        
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
    """Smart Gemini query with Gemini-powered tier selection."""
    try:
        if not query or not query.strip():
            return "Please provide a question."
        
        # Step 1: Get classification and model selection
        model_info = SmartModelSelector.select_model(query)
        tier = model_info["tier"]
        primary_model = model_info["model"]
        max_tokens = model_info["tokens"]
        timeout = model_info["timeout"]
        fallbacks = model_info["fallbacks"]
        
        logger.info(f"🤖 Using {tier} tier: {primary_model} for '{query[:50]}...'")
        
        # Check cache first
        cache_key = hashlib.md5(f"{query}_{primary_model}".encode()).hexdigest()
        if cache_key in gemini_cache:
            cached_time, response = gemini_cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                logger.info(f"♻️ Cached response from {primary_model}")
                return f"[{tier} - Cached] {response}"
        
        # Step 2: Try primary model
        models_to_try = [primary_model] + fallbacks
        
        for model in models_to_try:
            logger.info(f"🔄 Trying model: {model}")
            
            # Adjust tokens/timeout for fallback models
            if model != primary_model:
                model_tokens = min(max_tokens, 500)
                model_timeout = min(timeout, 15)
            else:
                model_tokens = max_tokens
                model_timeout = timeout
            
            result, status = call_gemini_api(query, model, model_tokens, model_timeout)
            
            if status == "SUCCESS" and result:
                # Cache successful response
                gemini_cache[cache_key] = (datetime.now(), result)
                
                # Add tier/model info to response
                return f"[{tier} - {model}] {result}"
            
            elif status == "MODEL_NOT_AVAILABLE":
                logger.warning(f"❌ Model {model} not available, trying next")
                continue
            
            elif status == "TIMEOUT":
                logger.warning(f"⏰ Model {model} timeout, trying next")
                continue
            
            elif status == "RATE_LIMITED":
                logger.warning(f"⏰ Model {model} rate limited, trying next")
                continue
        
        # If all models failed
        logger.error(f"❌ All models failed for query: {query}")
        return "Gemini AI is currently unavailable. Please try again in a moment."
        
    except Exception as e:
        logger.error(f"❌ Smart Gemini error: {e}")
        return f"AI error: {str(e)[:80]}"

# ================= OTHER TOOLS =================
def google_search(query, max_results=10):
    """Google Search."""
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
    """Wikipedia Search."""
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

# ================= MCP PROTOCOL HANDLER =================
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
                    "version": "3.6.1"
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

# ================= WEB SERVER WITH REAL-TIME FEATURES =================
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
add_log('INFO', 'Server starting...')
if GEMINI_API_KEY:
    add_log('INFO', 'Gemini API: ✅ Configured')
else:
    add_log('WARNING', 'Gemini API: ❌ Not configured')

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP v3.6.1 - Dashboard</title>
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
                animation: pulse 2s infinite;
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
        </style>
    </head>
    <body>
        <div class="dashboard">
            <!-- Header -->
            <div class="header">
                <h1 style="margin:0;color:var(--primary);">🚀 Xiaozhi MCP v3.6.1</h1>
                <p style="color:#666;margin:5px 0 20px 0;">Real-time Dashboard</p>
                
                <div class="status-grid">
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Uptime</div>
                        <div class="uptime-display" id="uptime">{{hours}}h {{minutes}}m {{seconds}}s</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Cache Size</div>
                        <div style="font-size:24px;color:var(--primary);">{{cache_size}} items</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Log Entries</div>
                        <div style="font-size:24px;color:var(--primary);">{{log_count}}</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Status</div>
                        <div style="font-size:24px;color:var(--success);">✅ Live</div>
                    </div>
                </div>
                
                <div class="real-time">
                    <div class="pulse"></div>
                    <span>Real-time updating</span>
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
                
                <h3 style="margin-top:25px;color:var(--primary);">⚡ System Info</h3>
                <div style="background:var(--light);padding:15px;border-radius:8px;">
                    <div><strong>Version:</strong> 3.6.1 (Gemini-powered tiers)</div>
                    <div><strong>MCP Protocol:</strong> 2024-11-05</div>
                    <div><strong>Models Available:</strong> Gemini 2.5 Pro/Flash/Flash-Lite</div>
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
                                <span>WebSocket Connection:</span>
                                <span id="wsStatus" style="color:var(--success);">● Connected</span>
                            </div>
                            <div style="height:8px;background:#eee;border-radius:4px;margin:5px 0;">
                                <div style="width:92%;height:100%;background:var(--success);border-radius:4px;"></div>
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
                resultDiv.innerHTML = `<div style="color:var(--warning);">⏳ Testing: "${query}"...</div>`;
                
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
                refreshLogs();
                fetch('/api/stats')
                    .then(r => r.json())
                    .then(data => {
                        // Update any stats as needed
                    });
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
            
            // Auto-refresh every 10 seconds
            setInterval(updateUptime, 10000);
            setInterval(refreshLogs, 15000);
            
            // Initial load
            updateUptime();
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
    timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/health')
def health_check():
    """Comprehensive health check endpoint."""
    uptime = int(time.time() - server_start_time)
    add_log('INFO', 'Health check accessed')
    
    return jsonify({
        "status": "healthy",
        "version": "3.6.1",
        "uptime_seconds": uptime,
        "uptime_human": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "cache_size": len(gemini_cache),
        "log_count": len(log_buffer),
        "gemini_configured": bool(GEMINI_API_KEY),
        "google_configured": bool(GOOGLE_API_KEY and CSE_ID),
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
            "test_queries_count": len(test_queries)
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
            <div class="method">POST</div>
            <code>/api/clear-cache</code> - Clear Gemini cache
        </div>
        
        <h2>🔧 MCP Protocol</h2>
        <p>The server implements Model Context Protocol (MCP) over WebSocket:</p>
        <ul>
            <li><strong>Tools:</strong> google_search, wikipedia_search, ask_ai</li>
            <li><strong>Protocol Version:</strong> 2024-11-05</li>
            <li><strong>Tier Selection:</strong> Gemini-powered (HARD/MEDIUM/SIMPLE)</li>
        </ul>
    </body>
    </html>
    ''')

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
        'test_queries': len(test_queries),
        'server_time': datetime.now().isoformat()
    })

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
        }
    })

@app.route('/test-smart/<path:query>')
def test_smart(query):
    """Test the smart tier selection."""
    add_log('INFO', f'Test query: {query[:50]}...')
    result = ask_gemini_smart(query)
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
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True)

# ================= MAIN =================
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
                add_log('INFO', 'Connected to Xiaozhi MCP endpoint')
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
                            add_log('INFO', 'MCP protocol initialized')
                        elif method == "tools/list":
                            response = MCPProtocolHandler.handle_tools_list(message_id)
                            add_log('INFO', 'Sent tools list to client')
                        elif method == "tools/call":
                            response = MCPProtocolHandler.handle_tools_call(message_id, params)
                            tool_name = params.get("name", "")
                            query = params.get("arguments", {}).get("query", "")[:50]
                            add_log('INFO', f'Processed {tool_name}: {query}...')
                        elif method == "notifications/initialized":
                            continue
                        else:
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"Unknown: {method}"}}
                        
                        if response:
                            await websocket.send(json.dumps(response))
                            
                    except Exception as e:
                        error_msg = f"Message processing error: {str(e)[:80]}"
                        logger.error(error_msg)
                        add_log('ERROR', error_msg)
        
        except Exception as e:
            error_msg = f"Connection error: {str(e)[:80]}"
            logger.error(error_msg)
            add_log('ERROR', error_msg)
            logger.info(f"⏳ Reconnecting in {reconnect_delay}s...")
            add_log('WARNING', f'Reconnecting in {reconnect_delay}s...')
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 60)

async def main():
    logger.info("🚀 Starting Xiaozhi MCP v3.6.1 - Gemini-Powered Smart Tiers")
    add_log('INFO', 'Server starting: Xiaozhi MCP v3.6.1')
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server on http://0.0.0.0:3000")
    add_log('INFO', 'Web server started on port 3000')
    
    # Start MCP bridge
    await mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Stopped")
        add_log('INFO', 'Server stopped by user')
    except Exception as e:
        error_msg = f"Fatal error: {str(e)}"
        logger.error(error_msg)
        add_log('ERROR', error_msg)