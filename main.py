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

# ================= OTHER TOOLS (UNCHANGED) =================
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

# ================= WEB SERVER WITH TEST BUTTONS =================
app = Flask(__name__)
server_start_time = time.time()

# Store test queries for the UI
test_queries = [
    "Explain quantum computing in simple terms",
    "What is the capital of France?",
    "Write a Python function to calculate Fibonacci sequence",
    "What time is it?",
    "Compare and contrast machine learning and deep learning",
    "How to make a cup of tea",
    "Calculate 15 * 27",
    "What are the benefits of regular exercise?",
    "Debug this code: for i in range(10): print(i",
    "Tell me a joke"
]

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Generate test buttons HTML
    test_buttons_html = ""
    for i, query in enumerate(test_queries):
        test_buttons_html += f'''
        <div class="test-query">
            <button onclick="testQuery('{query.replace("'", "\\'")}')">
                Test: {query[:40]}{'...' if len(query) > 40 else ''}
            </button>
            <div id="result-{i}" class="result"></div>
        </div>
        '''
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP v3.6.1</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                max-width: 1000px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f5f5f5;
            }
            .header { 
                background: linear-gradient(135deg, #4285F4 0%, #34A853 100%); 
                color: white; 
                padding: 2rem; 
                border-radius: 12px; 
                margin-bottom: 2rem;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }
            .tier { 
                padding: 1.5rem; 
                margin: 1.2rem 0; 
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }
            .hard { 
                background: linear-gradient(135deg, #FFEBEE 0%, #FFCDD2 100%); 
                border-left: 6px solid #F44336; 
            }
            .medium { 
                background: linear-gradient(135deg, #FFF3E0 0%, #FFE0B2 100%); 
                border-left: 6px solid #FF9800; 
            }
            .simple { 
                background: linear-gradient(135deg, #E8F5E9 0%, #C8E6C9 100%); 
                border-left: 6px solid #4CAF50; 
            }
            .test-section {
                background: white;
                padding: 2rem;
                border-radius: 12px;
                margin: 2rem 0;
                box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            }
            .test-query {
                margin: 1rem 0;
                padding: 1rem;
                background: #f8f9fa;
                border-radius: 8px;
                border-left: 4px solid #4285F4;
            }
            button {
                background: #4285F4;
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                margin: 5px;
                transition: all 0.3s;
                width: 100%;
                text-align: left;
            }
            button:hover {
                background: #3367D6;
                transform: translateY(-2px);
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }
            .result {
                margin-top: 1rem;
                padding: 1rem;
                background: white;
                border-radius: 6px;
                border: 1px solid #ddd;
                display: none;
                white-space: pre-wrap;
                font-family: monospace;
                max-height: 300px;
                overflow-y: auto;
            }
            .status-bar {
                background: white;
                padding: 1rem;
                border-radius: 8px;
                margin: 1rem 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .loading {
                color: #FF9800;
                font-weight: bold;
            }
            .success {
                color: #4CAF50;
                font-weight: bold;
            }
            .error {
                color: #F44336;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP v3.6.1</h1>
            <p>Gemini-Powered Smart Tier Selection</p>
        </div>
        
        <div class="status-bar">
            <div>
                <strong>Uptime:</strong> {{hours}}h {{minutes}}m {{seconds}}s
            </div>
            <div>
                <strong>Cache:</strong> {{cache_size}} items
            </div>
            <div>
                <strong>Status:</strong> <span id="status-indicator" class="success">✅ Healthy</span>
            </div>
        </div>
        
        <h2>🎯 Gemini-Powered Model Tiers:</h2>
        
        <div class="tier hard">
            <h3>🔴 HARD Tasks → Gemini 2.5 Pro</h3>
            <p><strong>For:</strong> Complex analysis, coding, detailed explanations, research</p>
            <p><strong>Selected by Gemini Flash-Lite classification</strong></p>
        </div>
        
        <div class="tier medium">
            <h3>🟡 MEDIUM Tasks → Gemini 2.5 Flash</h3>
            <p><strong>For:</strong> General Q&A, explanations, guides, comparisons</p>
            <p><strong>Selected by Gemini Flash-Lite classification</strong></p>
        </div>
        
        <div class="tier simple">
            <h3>🟢 SIMPLE Tasks → Gemini 2.5 Flash-Lite</h3>
            <p><strong>For:</strong> Quick answers, facts, simple questions, calculations</p>
            <p><strong>Selected by Gemini Flash-Lite classification</strong></p>
        </div>
        
        <div class="test-section">
            <h2>🧪 Test Tier Classification</h2>
            <p>Click any button to test how Gemini classifies and processes the query:</p>
            
            ''' + test_buttons_html + '''
            
            <div style="margin-top: 2rem;">
                <h3>Custom Test:</h3>
                <input type="text" id="custom-query" placeholder="Enter your own query..." style="width: 70%; padding: 10px; border-radius: 6px; border: 1px solid #ddd;">
                <button onclick="testCustomQuery()" style="width: 25%;">Test Custom Query</button>
                <div id="custom-result" class="result"></div>
            </div>
        </div>
        
        <script>
            function testQuery(query) {
                const button = event.target;
                const resultId = button.parentElement.querySelector('.result').id;
                const resultDiv = document.getElementById(resultId);
                
                // Show loading
                resultDiv.innerHTML = '<div class="loading">⏳ Gemini is classifying and processing...</div>';
                resultDiv.style.display = 'block';
                
                // Call test endpoint
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(data => {
                        resultDiv.innerHTML = `
                            <div class="success">
                                ✅ Classification & Processing Complete
                            </div>
                            <hr>
                            <strong>Query:</strong> ${data.query}<br><br>
                            <strong>Result:</strong><br>${data.result.replace(/\n/g, '<br>')}
                        `;
                    })
                    .catch(error => {
                        resultDiv.innerHTML = `<div class="error">❌ Error: ${error}</div>`;
                    });
            }
            
            function testCustomQuery() {
                const query = document.getElementById('custom-query').value;
                if (!query) {
                    alert('Please enter a query');
                    return;
                }
                
                const resultDiv = document.getElementById('custom-result');
                resultDiv.innerHTML = '<div class="loading">⏳ Gemini is classifying and processing...</div>';
                resultDiv.style.display = 'block';
                
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(data => {
                        resultDiv.innerHTML = `
                            <div class="success">
                                ✅ Classification & Processing Complete
                            </div>
                            <hr>
                            <strong>Query:</strong> ${data.query}<br><br>
                            <strong>Result:</strong><br>${data.result.replace(/\n/g, '<br>')}
                        `;
                    })
                    .catch(error => {
                        resultDiv.innerHTML = `<div class="error">❌ Error: ${error}</div>`;
                    });
            }
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    seconds=seconds,
    cache_size=len(gemini_cache))

@app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "version": "3.6.1",
        "gemini_powered": True,
        "cache_size": len(gemini_cache),
        "test_queries_count": len(test_queries)
    }), 200

@app.route('/test-smart/<path:query>')
def test_smart(query):
    """Test the smart tier selection with classification."""
    result = ask_gemini_smart(query)
    return jsonify({
        "query": query,
        "result": result,
        "cache_size": len(gemini_cache),
        "timestamp": datetime.now().isoformat()
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
    logger.info("🚀 Starting Xiaozhi MCP v3.6.1 - Gemini-Powered Smart Tiers")
    logger.info(f"📊 Gemini: {'✅ Configured' if GEMINI_API_KEY else '❌ Not configured'}")
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server on http://0.0.0.0:3000")
    logger.info("🧪 Test interface available at http://0.0.0.0:3000")
    
    # Start MCP bridge
    await mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}")