# main.py - XIAOZHI MCP SERVER v3.7 - GEMINI 3 FLASH 2026 OFFICIAL SDK
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

# Import the official Google Gen AI SDK
try:
    from google import genai
    from google.genai import types
    GEMINI_SDK_AVAILABLE = True
except ImportError:
    GEMINI_SDK_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.error("❌ Google Gen AI SDK not installed. Run: pip install google-generativeai")

# ================= LOAD ENVIRONMENT VARIABLES =================
load_dotenv()

# Get configuration from environment variables
XIAOZHI_WS = os.environ.get("XIAOZHI_WS")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
CSE_ID = os.environ.get("CSE_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Use GEMINI_API_KEY as GOOGLE_API_KEY if not set separately
if not GOOGLE_API_KEY and GEMINI_API_KEY:
    GOOGLE_API_KEY = GEMINI_API_KEY

# Validate critical configuration
if not XIAOZHI_WS:
    print("❌ ERROR: XIAOZHI_WS environment variable is not set!")
    print("   Go to Render.com dashboard → Environment → Add XIAOZHI_WS")
    sys.exit(1)

if not GOOGLE_API_KEY:
    print("❌ WARNING: GOOGLE_API_KEY environment variable is not set!")
    print("   Some features may not work properly")

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

# ================= GEMINI 2026 THINKING SYSTEM =================
class SmartModelSelector:
    """Smart model selection using Gemini 2.5 Flash-Lite for classification."""
    
    @staticmethod
    def classify_query_with_gemini(query):
        """Use Gemini 2.5 Flash-Lite to classify query into HARD, MEDIUM, or SIMPLE."""
        try:
            if not GOOGLE_API_KEY:
                logger.warning("No Google API key, using fallback classification")
                return "MEDIUM"
            
            # Prepare the classification prompt
            classification_prompt = f"""Please help me sort this query into 3 tiers for Gemini 3 Flash thinking system: 
Hard (requires maximum reasoning depth), 
Medium (requires balanced reasoning), and 
Simple (can be answered with minimal reasoning): "{query}"

Strictly only say "HARD", "MEDIUM", OR "SIMPLE", no extra text."""
            
            # Use simple requests API for classification (no thinking needed)
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
                }
            }
            
            params = {"key": GOOGLE_API_KEY}
            
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
    def get_thinking_config(tier):
        """Get thinking configuration based on tier."""
        thinking_configs = {
            "HARD": {
                "thinking_level": "high",
                "include_thoughts": True,
                "model": "gemini-3-flash-preview",
                "temperature": 0.2,
                "max_tokens": 2000,
                "timeout": 45
            },
            "MEDIUM": {
                "thinking_level": "medium",
                "include_thoughts": False,
                "model": "gemini-3-flash-preview",
                "temperature": 0.7,
                "max_tokens": 1500,
                "timeout": 30
            },
            "SIMPLE": {
                "thinking_level": "minimal",
                "include_thoughts": False,
                "model": "gemini-3-flash-preview",
                "temperature": 0.9,
                "max_tokens": 800,
                "timeout": 15
            }
        }
        
        return thinking_configs.get(tier, thinking_configs["MEDIUM"])

# ================= GEMINI 2026 SDK CLIENT =================
gemini_cache = {}
CACHE_DURATION = 300

class Gemini2026Client:
    """Client for Gemini 2026 with official SDK and thinking system."""
    
    def __init__(self):
        self.client = None
        if GOOGLE_API_KEY and GEMINI_SDK_AVAILABLE:
            try:
                self.client = genai.Client(api_key=GOOGLE_API_KEY)
                logger.info("✅ Google Gen AI SDK initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Gemini SDK: {e}")
                self.client = None
    
    def call_gemini_with_thinking(self, query, thinking_config):
        """Call Gemini 3 Flash with 2026 thinking system."""
        try:
            if not self.client:
                return None, "SDK_NOT_AVAILABLE"
            
            model_id = thinking_config.get("model", "gemini-3-flash-preview")
            thinking_level = thinking_config.get("thinking_level", "medium")
            include_thoughts = thinking_config.get("include_thoughts", False)
            temperature = thinking_config.get("temperature", 0.7)
            max_tokens = thinking_config.get("max_tokens", 1500)
            
            logger.info(f"🤔 Using {model_id} with {thinking_level} thinking (thoughts: {include_thoughts})")
            
            # Prepare the configuration
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=include_thoughts,
                    thinking_level=thinking_level
                )
            )
            
            # Make the API call
            response = self.client.models.generate_content(
                model=model_id,
                contents=query,
                config=config
            )
            
            # Extract the response
            if hasattr(response, 'text') and response.text:
                result = response.text
                
                # If thoughts were included, extract them
                if include_thoughts and hasattr(response, 'candidates'):
                    thoughts = []
                    for candidate in response.candidates:
                        if hasattr(candidate, 'content') and candidate.content:
                            for part in candidate.content.parts:
                                if hasattr(part, 'thought') and part.thought:
                                    thoughts.append(part.text)
                    
                    if thoughts:
                        thought_text = "\n\n🤔 **Internal Reasoning:**\n" + "\n".join(thoughts)
                        result = thought_text + "\n\n💡 **Final Answer:**\n" + result
                
                return result, "SUCCESS"
            else:
                return None, "NO_RESPONSE"
                
        except Exception as e:
            logger.error(f"❌ Gemini SDK error: {e}")
            return None, f"ERROR: {str(e)[:100]}"
    
    def call_gemini_fallback(self, query, max_tokens=1000, timeout=20):
        """Fallback using REST API if SDK fails."""
        try:
            if not GOOGLE_API_KEY:
                return None, "NO_API_KEY"
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
            
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
                }
            }
            
            params = {"key": GOOGLE_API_KEY}
            
            response = requests.post(url, headers=headers, json=data, params=params, timeout=timeout)
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
            
        except Exception as e:
            logger.error(f"❌ Fallback API error: {e}")
            return None, "ERROR"

# Initialize the Gemini client
gemini_client = Gemini2026Client()

def ask_gemini_smart(query):
    """Smart Gemini query with 2026 thinking system."""
    try:
        if not query or not query.strip():
            return "Please provide a question."
        
        # Step 1: Get classification
        tier = SmartModelSelector.classify_query_with_gemini(query)
        thinking_config = SmartModelSelector.get_thinking_config(tier)
        
        logger.info(f"🎯 Classified as: {tier} tier → {thinking_config['thinking_level']} thinking")
        
        # Step 2: Check cache
        cache_key = hashlib.md5(f"{query}_{tier}_{thinking_config['thinking_level']}".encode()).hexdigest()
        if cache_key in gemini_cache:
            cached_time, response = gemini_cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                logger.info(f"♻️ Cached response from {tier} tier")
                return f"[{tier} - Cached] {response}"
        
        # Step 3: Try official SDK with thinking system
        if gemini_client.client:
            result, status = gemini_client.call_gemini_with_thinking(query, thinking_config)
            
            if status == "SUCCESS" and result:
                # Cache successful response
                gemini_cache[cache_key] = (datetime.now(), result)
                return f"[{tier} - Gemini 3 Flash ({thinking_config['thinking_level']} thinking)] {result}"
        
        # Step 4: Fallback to REST API
        logger.warning("⚠️ SDK failed, using REST API fallback")
        result, status = gemini_client.call_gemini_fallback(
            query, 
            thinking_config.get("max_tokens", 1000),
            thinking_config.get("timeout", 20)
        )
        
        if status == "SUCCESS" and result:
            gemini_cache[cache_key] = (datetime.now(), result)
            return f"[{tier} - Fallback Gemini 2.5 Flash] {result}"
        
        # Step 5: Ultimate fallback
        return f"Gemini AI is currently unavailable. Please try again in a moment."
        
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
        headers = {'User-Agent': 'XiaozhiBot/3.7'}
        
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
                    "name": "gemini-2026-thinking",
                    "version": "3.7.0"
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
                        "description": "Ask Gemini 3 Flash with 2026 thinking system (Auto-classifies and uses optimal thinking levels)",
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

# ================= WEB SERVER =================
app = Flask(__name__)
server_start_time = time.time()

# Test queries
test_queries = [
    "Explain why 1/0 is undefined using step-by-step reasoning",
    "What is the capital of France?",
    "Write a Python function to calculate Fibonacci sequence",
    "What time is it?",
    "Compare and contrast machine learning and deep learning",
    "How to make a cup of tea",
    "Calculate 15 * 27",
    "What are the benefits of regular exercise?",
    "Debug this code: for i in range(10): print(i",
    "Tell me a joke",
    "Analyze the economic impact of climate change in 2026",
    "What is photosynthesis?",
    "Write a detailed business plan for a startup",
    "How does a quantum computer work?",
    "Explain the theory of relativity to a 10-year-old"
]

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Thinking levels info
    thinking_levels = {
        "high": "Maximum reasoning depth for complex problems",
        "medium": "Balanced reasoning (default)",
        "low": "Optimized for simple instructions", 
        "minimal": "Basic tasks, lowest latency"
    }
    
    # Generate test buttons HTML
    test_buttons_html = ""
    for i, query in enumerate(test_queries):
        test_buttons_html += f'''
        <div class="test-query">
            <button onclick="testQuery('{query.replace("'", "\\'")}')">
                🧪 {query[:45]}{'...' if len(query) > 45 else ''}
            </button>
            <div id="result-{i}" class="result"></div>
        </div>
        '''
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP v3.7 - Gemini 2026 Thinking System</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                max-width: 1200px; 
                margin: 0 auto; 
                padding: 20px;
                background: linear-gradient(135deg, #f0f4ff 0%, #e6f7ff 100%);
                min-height: 100vh;
            }
            .header { 
                background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); 
                color: white; 
                padding: 2.5rem; 
                border-radius: 16px; 
                margin-bottom: 2.5rem;
                box-shadow: 0 8px 32px rgba(59, 130, 246, 0.3);
            }
            .thinking-levels {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 1.5rem;
                margin: 2rem 0;
            }
            .level-card {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.08);
                border-top: 4px solid;
                transition: transform 0.3s;
            }
            .level-card:hover {
                transform: translateY(-4px);
            }
            .level-high { border-color: #ef4444; }
            .level-medium { border-color: #f59e0b; }
            .level-low { border-color: #10b981; }
            .level-minimal { border-color: #3b82f6; }
            .test-section {
                background: white;
                padding: 2rem;
                border-radius: 16px;
                margin: 2rem 0;
                box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            }
            .test-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 1rem;
                margin-top: 1.5rem;
            }
            .test-query {
                background: #f8fafc;
                padding: 1rem;
                border-radius: 10px;
                border: 2px solid #e2e8f0;
                transition: all 0.3s;
            }
            .test-query:hover {
                border-color: #3b82f6;
                background: #eff6ff;
            }
            button {
                background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
                color: white;
                border: none;
                padding: 12px 20px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 15px;
                font-weight: 500;
                transition: all 0.3s;
                width: 100%;
                text-align: left;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(59, 130, 246, 0.4);
            }
            .result {
                margin-top: 1rem;
                padding: 1rem;
                background: white;
                border-radius: 8px;
                border: 1px solid #e2e8f0;
                display: none;
                white-space: pre-wrap;
                font-family: 'SF Mono', Monaco, monospace;
                font-size: 13px;
                max-height: 400px;
                overflow-y: auto;
                line-height: 1.5;
            }
            .thought-process {
                background: #fef3c7;
                border-left: 4px solid #f59e0b;
                padding: 0.75rem;
                margin: 0.5rem 0;
                border-radius: 6px;
                font-family: 'SF Mono', Monaco, monospace;
                font-size: 12px;
            }
            .status-bar {
                background: white;
                padding: 1rem;
                border-radius: 10px;
                margin: 1rem 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 1rem;
                box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            }
            .status-item {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .sdk-status {
                padding: 6px 12px;
                border-radius: 20px;
                font-size: 14px;
                font-weight: 500;
            }
            .sdk-ok { background: #d1fae5; color: #065f46; }
            .sdk-fail { background: #fee2e2; color: #991b1b; }
            .custom-test {
                background: #f0f9ff;
                padding: 1.5rem;
                border-radius: 12px;
                margin-top: 2rem;
                border: 2px dashed #93c5fd;
            }
            input[type="text"] {
                width: 100%;
                padding: 12px;
                border: 2px solid #93c5fd;
                border-radius: 8px;
                font-size: 16px;
                margin: 10px 0;
                box-sizing: border-box;
            }
            .system-info {
                background: #f8fafc;
                padding: 1rem;
                border-radius: 10px;
                margin: 1rem 0;
                border-left: 4px solid #8b5cf6;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Xiaozhi MCP v3.7</h1>
            <p>Gemini 3 Flash 2026 Official Thinking System</p>
        </div>
        
        <div class="status-bar">
            <div class="status-item">
                <strong>Uptime:</strong> {{hours}}h {{minutes}}m {{seconds}}s
            </div>
            <div class="status-item">
                <strong>Cache:</strong> {{cache_size}} items
            </div>
            <div class="status-item">
                <strong>SDK:</strong> 
                <span class="sdk-status {{ 'sdk-ok' if sdk_available else 'sdk-fail' }}">
                    {{ '✅ Available' if sdk_available else '❌ Not Available' }}
                </span>
            </div>
            <div class="status-item">
                <strong>API Key:</strong> 
                <span class="sdk-status {{ 'sdk-ok' if api_key_available else 'sdk-fail' }}">
                    {{ '✅ Configured' if api_key_available else '❌ Missing' }}
                </span>
            </div>
        </div>
        
        <div class="system-info">
            <h3>📚 About Temperature:</h3>
            <p><strong>Temperature</strong> controls the randomness/creativity of responses:</p>
            <ul>
                <li><strong>Low (0.1-0.3):</strong> Focused, deterministic, consistent answers</li>
                <li><strong>Medium (0.5-0.7):</strong> Balanced creativity and consistency</li>
                <li><strong>High (0.8-1.0):</strong> Creative, diverse, less predictable</li>
            </ul>
            <p>In 2026, Gemini 3 Flash combines <strong>thinking_level</strong> (reasoning depth) with <strong>temperature</strong> (creativity control) for optimal responses.</p>
        </div>
        
        <h2>🤔 Gemini 2026 Thinking Levels:</h2>
        
        <div class="thinking-levels">
            <div class="level-card level-high">
                <h3>🔴 HIGH Thinking</h3>
                <p><strong>For:</strong> Complex analysis, coding, detailed reasoning</p>
                <p><strong>Includes:</strong> Internal thoughts (reasoning process)</p>
                <p><strong>Temperature:</strong> 0.2 (focused)</p>
            </div>
            
            <div class="level-card level-medium">
                <h3>🟡 MEDIUM Thinking</h3>
                <p><strong>For:</strong> General Q&A, explanations, guides</p>
                <p><strong>Default:</strong> Balanced reasoning</p>
                <p><strong>Temperature:</strong> 0.7 (balanced)</p>
            </div>
            
            <div class="level-card level-low">
                <h3>🟢 LOW Thinking</h3>
                <p><strong>For:</strong> Simple instructions, quick answers</p>
                <p><strong>Optimized:</strong> For speed and efficiency</p>
                <p><strong>Temperature:</strong> 0.9 (creative)</p>
            </div>
            
            <div class="level-card level-minimal">
                <h3>🔵 MINIMAL Thinking</h3>
                <p><strong>For:</strong> Basic tasks, lowest latency</p>
                <p><strong>Fastest:</strong> Response time</p>
                <p><strong>Temperature:</strong> 0.9 (creative)</p>
            </div>
        </div>
        
        <div class="test-section">
            <h2>🧪 Test Thinking System</h2>
            <p>Click any query to test how Gemini classifies and applies thinking levels:</p>
            
            <div class="test-grid">
                ''' + test_buttons_html + '''
            </div>
            
            <div class="custom-test">
                <h3>Custom Test Query:</h3>
                <input type="text" id="custom-query" placeholder="Enter your own query to test thinking levels...">
                <button onclick="testCustomQuery()" style="background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);">
                    🔬 Test Custom Query
                </button>
                <div id="custom-result" class="result"></div>
            </div>
        </div>
        
        <script>
            function testQuery(query) {
                const button = event.target;
                const resultDiv = button.parentElement.querySelector('.result');
                
                // Show loading
                resultDiv.innerHTML = '<div style="color: #f59e0b; font-weight: 600;">⏳ Gemini is classifying and applying thinking level...</div>';
                resultDiv.style.display = 'block';
                
                // Call test endpoint
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(data => {
                        let result = data.result;
                        
                        // Format thoughts if present
                        if (result.includes('🤔 **Internal Reasoning:**')) {
                            const parts = result.split('🤔 **Internal Reasoning:**');
                            const thoughts = parts[1]?.split('💡 **Final Answer:**')[0];
                            const answer = parts[1]?.split('💡 **Final Answer:**')[1] || parts[1];
                            
                            resultDiv.innerHTML = `
                                <div style="color: #10b981; font-weight: 600; margin-bottom: 10px;">
                                    ✅ Thinking Level Applied
                                </div>
                                <hr style="margin: 10px 0; border: 1px solid #e2e8f0;">
                                <strong>Query:</strong> ${data.query}<br><br>
                                <strong>Thought Process:</strong>
                                <div class="thought-process">${thoughts || 'No thoughts captured'}</div>
                                <strong>Final Answer:</strong><br>${(answer || result).replace(/\n/g, '<br>')}
                            `;
                        } else {
                            resultDiv.innerHTML = `
                                <div style="color: #10b981; font-weight: 600; margin-bottom: 10px;">
                                    ✅ Thinking Level Applied
                                </div>
                                <hr style="margin: 10px 0; border: 1px solid #e2e8f0;">
                                <strong>Query:</strong> ${data.query}<br><br>
                                <strong>Result:</strong><br>${result.replace(/\n/g, '<br>')}
                            `;
                        }
                    })
                    .catch(error => {
                        resultDiv.innerHTML = `<div style="color: #ef4444; font-weight: 600;">❌ Error: ${error}</div>`;
                    });
            }
            
            function testCustomQuery() {
                const query = document.getElementById('custom-query').value;
                if (!query) {
                    alert('Please enter a query');
                    return;
                }
                
                const resultDiv = document.getElementById('custom-result');
                resultDiv.innerHTML = '<div style="color: #f59e0b; font-weight: 600;">⏳ Gemini is classifying and applying thinking level...</div>';
                resultDiv.style.display = 'block';
                
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(data => {
                        let result = data.result;
                        
                        if (result.includes('🤔 **Internal Reasoning:**')) {
                            const parts = result.split('🤔 **Internal Reasoning:**');
                            const thoughts = parts[1]?.split('💡 **Final Answer:**')[0];
                            const answer = parts[1]?.split('💡 **Final Answer:**')[1] || parts[1];
                            
                            resultDiv.innerHTML = `
                                <div style="color: #10b981; font-weight: 600; margin-bottom: 10px;">
                                    ✅ Thinking Level Applied
                                </div>
                                <hr style="margin: 10px 0; border: 1px solid #e2e8f0;">
                                <strong>Query:</strong> ${data.query}<br><br>
                                <strong>Thought Process:</strong>
                                <div class="thought-process">${thoughts || 'No thoughts captured'}</div>
                                <strong>Final Answer:</strong><br>${(answer || result).replace(/\n/g, '<br>')}
                            `;
                        } else {
                            resultDiv.innerHTML = `
                                <div style="color: #10b981; font-weight: 600; margin-bottom: 10px;">
                                    ✅ Thinking Level Applied
                                </div>
                                <hr style="margin: 10px 0; border: 1px solid #e2e8f0;">
                                <strong>Query:</strong> ${data.query}<br><br>
                                <strong>Result:</strong><br>${result.replace(/\n/g, '<br>')}
                            `;
                        }
                    })
                    .catch(error => {
                        resultDiv.innerHTML = `<div style="color: #ef4444; font-weight: 600;">❌ Error: ${error}</div>`;
                    });
            }
            
            // Auto-refresh status every 30 seconds
            setInterval(() => {
                fetch('/health')
                    .then(response => response.json())
                    .then(data => {
                        console.log('Health check:', data.status);
                    })
                    .catch(() => {
                        console.warn('Health check failed');
                    });
            }, 30000);
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    seconds=seconds,
    cache_size=len(gemini_cache),
    sdk_available=GEMINI_SDK_AVAILABLE and bool(GOOGLE_API_KEY),
    api_key_available=bool(GOOGLE_API_KEY))

@app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "version": "3.7.0",
        "gemini_sdk": GEMINI_SDK_AVAILABLE,
        "api_key_configured": bool(GOOGLE_API_KEY),
        "cache_size": len(gemini_cache),
        "thinking_system": "Gemini 3 Flash 2026",
        "supported_levels": ["minimal", "low", "medium", "high"]
    }), 200

@app.route('/test-smart/<path:query>')
def test_smart(query):
    """Test the smart tier selection with thinking system."""
    result = ask_gemini_smart(query)
    return jsonify({
        "query": query,
        "result": result,
        "cache_size": len(gemini_cache),
        "timestamp": datetime.now().isoformat()
    }), 200

def run_web_server():
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
    logger.info("🚀 Starting Xiaozhi MCP v3.7 - Gemini 2026 Thinking System")
    logger.info(f"📊 Google Gen AI SDK: {'✅ Available' if GEMINI_SDK_AVAILABLE else '❌ Not available'}")
    logger.info(f"🔑 API Key: {'✅ Configured' if GOOGLE_API_KEY else '❌ Missing'}")
    
    if not GEMINI_SDK_AVAILABLE:
        logger.warning("⚠️ Install Google Gen AI SDK: pip install google-generativeai")
    
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