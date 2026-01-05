# main.py - XIAOZHI MCP SERVER v3.6.1 - ASYNC PING FIX (DEBUG EDITION)
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
from typing import Dict, List
from dataclasses import dataclass
from enum import Enum

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

# ================= DEBUG CONFIGURATION =================
DEBUG_PING = True  # Enable detailed ping debugging
PING_TRACE_LOG = []  # Store ping events for real-time viewing
MAX_PING_TRACE = 100

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

# ================= PING DEBUG DATA STRUCTURES =================
@dataclass
class PingEvent:
    """Track individual ping events"""
    timestamp: datetime
    request_id: str
    event_type: str  # "TASK_CREATED", "FIRST_PING_SENT", "CONTINUOUS_PING", "WS_ERROR", "TASK_COMPLETED"
    details: str
    websocket_open: bool = False
    success: bool = False
    
    def to_dict(self):
        return {
            "time": self.timestamp.strftime('%H:%M:%S.%f')[:-3],
            "request_id": self.request_id,
            "type": self.event_type,
            "details": self.details,
            "ws_open": self.websocket_open,
            "success": self.success
        }

class PingTracker:
    """Track all ping activity for debugging"""
    
    def __init__(self):
        self.events: List[PingEvent] = []
        self.request_stats: Dict[str, Dict] = {}
    
    def add_event(self, request_id: str, event_type: str, details: str, 
                  websocket_open: bool = False, success: bool = False):
        """Add a ping event"""
        event = PingEvent(
            timestamp=datetime.now(),
            request_id=request_id,
            event_type=event_type,
            details=details,
            websocket_open=websocket_open,
            success=success
        )
        
        self.events.append(event)
        
        # Keep only recent events
        if len(self.events) > MAX_PING_TRACE:
            self.events.pop(0)
        
        # Update request stats
        if request_id not in self.request_stats:
            self.request_stats[request_id] = {
                "created": datetime.now(),
                "first_ping_sent": None,
                "ping_count": 0,
                "last_ping": None,
                "completed": None,
                "errors": 0
            }
        
        # Update stats based on event type
        stats = self.request_stats[request_id]
        if event_type == "FIRST_PING_SENT":
            stats["first_ping_sent"] = datetime.now()
            stats["ping_count"] += 1
        elif event_type == "CONTINUOUS_PING":
            stats["ping_count"] += 1
            stats["last_ping"] = datetime.now()
        elif event_type == "WS_ERROR":
            stats["errors"] += 1
        elif event_type == "TASK_COMPLETED":
            stats["completed"] = datetime.now()
        
        # Log if debugging enabled
        if DEBUG_PING:
            logger.debug(f"📊 PING EVENT: {event_type} for {request_id}: {details}")
        
        return event
    
    def get_recent_events(self, limit: int = 20):
        """Get recent ping events"""
        return [e.to_dict() for e in self.events[-limit:]]
    
    def get_request_stats(self, request_id: str):
        """Get stats for a specific request"""
        return self.request_stats.get(request_id, {})
    
    def get_active_requests(self):
        """Get all active ping requests"""
        active = []
        for req_id, stats in self.request_stats.items():
            if stats.get("completed") is None:  # Not completed yet
                active.append(req_id)
        return active
    
    def clear_old_requests(self, older_than_minutes: int = 5):
        """Clear old completed requests"""
        cutoff = datetime.now() - timedelta(minutes=older_than_minutes)
        to_remove = []
        
        for req_id, stats in self.request_stats.items():
            if stats.get("completed") and stats["completed"] < cutoff:
                to_remove.append(req_id)
        
        for req_id in to_remove:
            del self.request_stats[req_id]
        
        return len(to_remove)

# Global ping tracker
ping_tracker = PingTracker()

# ================= ASYNC KEEP-ALIVE MANAGER =================
class AsyncPingManager:
    """Async-based keep-alive system (THREAD-SAFE for WebSocket)."""
    
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.websocket_refs: Dict[str, websockets.WebSocketClientProtocol] = {}
    
    async def start_pinging(self, websocket, request_id: str, duration: int = 30):
        """Start async ping task for a request."""
        try:
            # PHASE 1: Verify parameters
            if not websocket:
                logger.error(f"❌ No WebSocket provided for {request_id}")
                ping_tracker.add_event(request_id, "WS_ERROR", "No WebSocket provided", success=False)
                return None
            
            # Track request start
            ping_tracker.add_event(request_id, "TASK_CREATED", 
                                   f"Starting ping task with duration={duration}s", 
                                   websocket_open=websocket.open if hasattr(websocket, 'open') else False,
                                   success=True)
            
            logger.info(f"🚀 STARTING ASYNC PING for {request_id}")
            logger.debug(f"  • WebSocket open: {websocket.open}")
            logger.debug(f"  • Duration: {duration}s")
            logger.debug(f"  • First ping delay: {FIRST_PING_DELAY}s")
            logger.debug(f"  • Active tasks: {len(self.active_tasks)}")
            
            # Store WebSocket reference
            self.websocket_refs[request_id] = websocket
            
            # Create and store ping task
            task = asyncio.create_task(
                self._ping_worker(websocket, request_id, duration),
                name=f"ping_{request_id}"
            )
            self.active_tasks[request_id] = task
            
            # Add done callback for cleanup
            task.add_done_callback(lambda t: self._cleanup(request_id))
            
            logger.info(f"✅ ASYNC PING STARTED for {request_id}")
            logger.debug(f"  • Task created: {task.get_name()}")
            logger.debug(f"  • Task done: {task.done()}")
            logger.debug(f"  • Task cancelled: {task.cancelled()}")
            
            return task
            
        except Exception as e:
            error_msg = f"Failed to start async ping: {str(e)}"
            logger.error(f"❌ {error_msg}")
            ping_tracker.add_event(request_id, "WS_ERROR", error_msg, success=False)
            return None
    
    async def _ping_worker(self, websocket, request_id: str, duration: int):
        """Async ping worker - runs in same event loop as WebSocket."""
        ping_count = 0
        start_time = time.time()
        
        try:
            logger.debug(f"📡 Ping worker STARTED for {request_id}")
            logger.debug(f"  • Event loop: {asyncio.get_running_loop()}")
            logger.debug(f"  • Duration: {duration}s")
            logger.debug(f"  • WebSocket: {id(websocket)}")
            
            # PHASE 1: INSTANT FIRST PING (50ms)
            logger.debug(f"⏳ Waiting {FIRST_PING_DELAY}s for first ping...")
            await asyncio.sleep(FIRST_PING_DELAY)
            
            # Send first ping
            first_ping_success = await self._send_safe_ping(websocket, request_id, ping_count, is_first=True)
            if first_ping_success:
                ping_count += 1
                ping_tracker.add_event(request_id, "FIRST_PING_SENT", 
                                       f"First ping sent after {FIRST_PING_DELAY}s",
                                       websocket_open=websocket.open,
                                       success=True)
                logger.info(f"✅ INSTANT PING #1 sent for {request_id} (after {FIRST_PING_DELAY}s)")
            else:
                ping_tracker.add_event(request_id, "WS_ERROR", 
                                       "Failed to send first ping",
                                       websocket_open=websocket.open,
                                       success=False)
                logger.warning(f"⚠️ Failed to send first ping for {request_id}")
            
            # PHASE 2: CONTINUOUS PINGS (0.8s interval)
            last_ping_time = time.time()
            
            while time.time() - start_time < duration:
                # Check if request is still active
                if not self._is_request_active(request_id):
                    logger.info(f"⚠️ Request {request_id} completed, stopping pings")
                    ping_tracker.add_event(request_id, "TASK_COMPLETED", 
                                           "Request marked as completed",
                                           success=True)
                    break
                
                # Check if WebSocket is still connected
                if not websocket.open:
                    logger.warning(f"⚠️ WebSocket closed for {request_id}, stopping pings")
                    ping_tracker.add_event(request_id, "WS_ERROR", 
                                           "WebSocket closed",
                                           websocket_open=False,
                                           success=False)
                    break
                
                # Wait for interval
                elapsed = time.time() - last_ping_time
                sleep_time = max(0, CONTINUOUS_PING_INTERVAL - elapsed)
                
                if sleep_time > 0:
                    logger.debug(f"💤 Sleeping {sleep_time:.3f}s before next ping")
                    await asyncio.sleep(sleep_time)
                
                # Send ping
                success = await self._send_safe_ping(websocket, request_id, ping_count, is_first=False)
                last_ping_time = time.time()
                
                if success:
                    ping_count += 1
                    ping_tracker.add_event(request_id, "CONTINUOUS_PING", 
                                           f"Ping #{ping_count} sent",
                                           websocket_open=websocket.open,
                                           success=True)
                    
                    # Log every 5 pings
                    if ping_count % 5 == 0:
                        elapsed_total = time.time() - start_time
                        logger.info(f"📤 Ping #{ping_count} sent for {request_id} (elapsed: {elapsed_total:.1f}s)")
                
                # Check if we should continue
                if time.time() - start_time >= duration:
                    logger.debug(f"⏰ Duration reached for {request_id}")
                    break
            
            logger.info(f"✅ Ping worker completed for {request_id} ({ping_count} pings in {time.time()-start_time:.1f}s)")
            ping_tracker.add_event(request_id, "TASK_COMPLETED", 
                                   f"Completed with {ping_count} pings",
                                   success=True)
            
        except asyncio.CancelledError:
            logger.info(f"🛑 Ping worker CANCELLED for {request_id}")
            ping_tracker.add_event(request_id, "TASK_COMPLETED", 
                                   "Cancelled by system",
                                   success=False)
            raise
        except Exception as e:
            error_msg = f"Ping worker error: {str(e)}"
            logger.error(f"❌ {error_msg} for {request_id}")
            ping_tracker.add_event(request_id, "WS_ERROR", error_msg, success=False)
        finally:
            self._cleanup(request_id)
    
    async def _send_safe_ping(self, websocket, request_id: str, ping_count: int, is_first: bool = False) -> bool:
        """Safely send a ping message (handles WebSocket errors)."""
        try:
            # Check WebSocket state
            if not websocket:
                logger.debug(f"⚠️ No WebSocket object for {request_id}")
                return False
            
            if not hasattr(websocket, 'open'):
                logger.debug(f"⚠️ WebSocket has no 'open' attribute for {request_id}")
                return False
            
            ws_open = websocket.open
            logger.debug(f"🔍 WebSocket check for {request_id}: open={ws_open}, ping_count={ping_count}")
            
            if not ws_open:
                logger.warning(f"⚠️ WebSocket not open for {request_id}")
                return False
            
            # Prepare ping message
            message_idx = ping_count % len(keep_alive_messages)
            ping_type = "FIRST" if is_first else f"CONTINUOUS#{ping_count}"
            
            ping_message = {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progress": {
                        "type": "text",
                        "text": f"⏳ {keep_alive_messages[message_idx]} [{ping_type}]"
                    }
                }
            }
            
            # Log before sending
            logger.debug(f"📨 Preparing to send ping for {request_id}")
            logger.debug(f"  • Message: {ping_message['params']['progress']['text']}")
            logger.debug(f"  • WebSocket ID: {id(websocket)}")
            logger.debug(f"  • Is open: {websocket.open}")
            
            # Send the ping
            await websocket.send(json.dumps(ping_message))
            
            # Log success
            logger.debug(f"✅ Ping sent successfully for {request_id}")
            return True
            
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"⚠️ Connection closed while pinging {request_id}: {e.code} - {e.reason}")
            return False
        except websockets.exceptions.WebSocketException as e:
            logger.error(f"❌ WebSocket exception for {request_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending ping for {request_id}: {e}")
            return False
    
    def _is_request_active(self, request_id: str) -> bool:
        """Check if request is still active."""
        is_active = request_id in active_requests and active_requests[request_id]
        logger.debug(f"🔍 Active check for {request_id}: {is_active}")
        return is_active
    
    def _cleanup(self, request_id: str):
        """Clean up ping task and references."""
        try:
            logger.debug(f"🧹 Starting cleanup for {request_id}")
            
            # Cancel task if still running
            if request_id in self.active_tasks:
                task = self.active_tasks[request_id]
                if not task.done():
                    task.cancel()
                    logger.debug(f"  • Task cancelled: {request_id}")
                del self.active_tasks[request_id]
                logger.debug(f"  • Task removed from active_tasks: {request_id}")
            
            # Remove WebSocket reference
            if request_id in self.websocket_refs:
                del self.websocket_refs[request_id]
                logger.debug(f"  • WebSocket reference removed: {request_id}")
            
            # Remove from active requests
            if request_id in active_requests:
                del active_requests[request_id]
                logger.debug(f"  • Removed from active_requests: {request_id}")
            
            logger.info(f"🧹 Cleaned up ping manager for {request_id}")
            
        except Exception as e:
            logger.error(f"❌ Cleanup error for {request_id}: {e}")
    
    def stop_all_pings(self):
        """Stop all ping tasks (e.g., on shutdown)."""
        logger.info(f"🛑 Stopping all ping tasks ({len(self.active_tasks)} active)")
        for request_id, task in list(self.active_tasks.items()):
            if not task.done():
                task.cancel()
                logger.info(f"  • Stopped ping task for {request_id}")
        
        self.active_tasks.clear()
        self.websocket_refs.clear()
        logger.info("✅ All ping tasks stopped")

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
            logger.info(f"🚀 Processing {tier} tier: '{query[:40]}...' [ID: {request_id}]")
            
            # Mark request as active
            active_requests[request_id] = True
            logger.debug(f"📝 Marked {request_id} as active")
            logger.debug(f"  • Active requests: {list(active_requests.keys())}")
            logger.debug(f"  • WebSocket: {id(websocket)}, open: {websocket.open}")
            
            # START ASYNC PINGING IMMEDIATELY
            logger.debug(f"🎯 Starting ping manager for {request_id}")
            ping_task = await ping_manager.start_pinging(websocket, request_id, 25)
            
            if ping_task:
                logger.info(f"✅ Ping task started for {request_id}")
                logger.debug(f"  • Task name: {ping_task.get_name()}")
                logger.debug(f"  • Task state: {'running' if not ping_task.done() else 'done'}")
            else:
                logger.error("❌ Failed to start pinging!")
                ping_tracker.add_event(request_id, "WS_ERROR", "Failed to start ping task", success=False)
            
            # Check cache first
            if cache_key in gemini_cache:
                cached_time, response = gemini_cache[cache_key]
                if datetime.now() - cached_time < timedelta(seconds=CACHE_DURATION):
                    logger.info(f"⚡ Cache hit for {request_id}")
                    # Mark as inactive since we're returning cached response
                    active_requests[request_id] = False
                    return f"[{tier} - Cached] {response}"
            
            # Get model configuration
            config = AsyncGeminiProcessor.get_model_config(tier)
            models = config["models"][:PARALLEL_MODEL_TRIES]
            max_tokens = config["tokens"]
            timeouts = config["timeouts"][:PARALLEL_MODEL_TRIES]
            
            logger.debug(f"🤖 Models to try for {request_id}: {models}")
            logger.debug(f"  • Max tokens: {max_tokens}")
            logger.debug(f"  • Timeouts: {timeouts}")
            
            # Submit models in parallel using ThreadPoolExecutor
            futures = []
            with ThreadPoolExecutor(max_workers=PARALLEL_MODEL_TRIES) as model_executor:
                for i, model in enumerate(models):
                    timeout = timeouts[i] if i < len(timeouts) else 10
                    logger.debug(f"  • Submitting {model} with timeout {timeout}s")
                    future = model_executor.submit(
                        AsyncGeminiProcessor.call_gemini_sync,
                        query, model, max_tokens, timeout
                    )
                    futures.append((model, future))
                
                # Wait for first successful response
                for model, future in futures:
                    try:
                        logger.debug(f"  • Waiting for {model} result...")
                        result, success = future.result(timeout=15)
                        if success and result:
                            logger.info(f"✅ {model} succeeded for {request_id}!")
                            gemini_cache[cache_key] = (datetime.now(), result)
                            # Mark as inactive before returning
                            active_requests[request_id] = False
                            return f"[{tier} - {model}] {result}"
                        else:
                            logger.debug(f"  • {model} failed or no result")
                    except Exception as e:
                        logger.warning(f"⚠️ {model} failed for {request_id}: {e}")
            
            # Fallback sequential
            logger.warning(f"⚠️ Parallel failed for {request_id}, trying sequential")
            for model in models:
                logger.debug(f"  • Trying {model} sequentially...")
                result, success = AsyncGeminiProcessor.call_gemini_sync(
                    query, model, max_tokens, 10
                )
                if success and result:
                    logger.info(f"✅ {model} succeeded (sequential) for {request_id}!")
                    gemini_cache[cache_key] = (datetime.now(), result)
                    # Mark as inactive before returning
                    active_requests[request_id] = False
                    return f"[{tier} - {model}*] {result}"
            
            logger.error(f"❌ All models failed for {request_id}")
            # Mark as inactive before returning
            active_requests[request_id] = False
            return "Sorry, I couldn't generate a response. Please try again."
            
        except Exception as e:
            logger.error(f"❌ Processing error for {request_id}: {e}")
            # Mark as inactive on error
            active_requests[request_id] = False
            return f"Error: {str(e)[:50]}"
        finally:
            # Double-check cleanup
            if request_id in active_requests:
                active_requests[request_id] = False
                logger.debug(f"🔒 Final cleanup: marked {request_id} as inactive")

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
                    "version": "3.6.1-ASYNC-PING-DEBUG"
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
            logger.debug(f"  • WebSocket ID: {id(websocket)}")
            logger.debug(f"  • WebSocket open: {websocket.open}")
            logger.debug(f"  • Params: {params}")
            
            if tool_name == "google_search":
                logger.debug(f"  • Using Google Search")
                result = google_search_fast(query)
            elif tool_name == "wikipedia_search":
                logger.debug(f"  • Using Wikipedia Search")
                result = wikipedia_search_fast(query)
            elif tool_name == "ask_ai":
                logger.debug(f"  • Using AI with async pings")
                # Get classification
                logger.debug(f"  • Classifying query...")
                tier = SmartModelSelector.classify_query_with_gemini(query)
                cache_key = hashlib.md5(f"{query}_{tier}".encode()).hexdigest()
                
                logger.debug(f"  • Classification: {tier}")
                logger.debug(f"  • Cache key: {cache_key}")
                
                # Process with async pinging
                result = await AsyncGeminiProcessor.process_query_async(
                    query, tier, cache_key, websocket, request_id
                )
            else:
                result = f"Unknown tool: {tool_name}"
            
            logger.info(f"✅ Completed {tool_name} [ID: {request_id}]")
            logger.debug(f"  • Result length: {len(result)} chars")
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
            
        except Exception as e:
            logger.error(f"❌ Tool error [ID: {request_id}]: {e}")
            # Ensure cleanup on error
            if request_id in active_requests:
                active_requests[request_id] = False
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32000, "message": f"Error: {str(e)[:50]}"}
            }
        finally:
            # Log final state
            logger.debug(f"🏁 Final state for {request_id}:")
            logger.debug(f"  • In active_requests: {request_id in active_requests}")
            logger.debug(f"  • Active value: {active_requests.get(request_id, 'NOT_FOUND')}")
            logger.debug(f"  • Ping tasks active: {request_id in ping_manager.active_tasks}")

# ================= ENHANCED WEB SERVER WITH PING DEBUGGING =================
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
add_log('INFO', '🚀 STARTING ASYNC PING SERVER (DEBUG EDITION)...')
add_log('INFO', f'Gemini API: {"✅ CONFIGURED" if GEMINI_API_KEY else "❌ MISSING"}')
add_log('INFO', f'First ping delay: {FIRST_PING_DELAY}s')
add_log('INFO', f'Continuous ping interval: {CONTINUOUS_PING_INTERVAL}s')
add_log('DEBUG', f'Debug mode: {DEBUG_PING}')

@app.route('/')
def index():
    uptime = int(time.time() - server_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Get ping statistics
    recent_pings = ping_tracker.get_recent_events(10)
    active_ping_requests = ping_tracker.get_active_requests()
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP - Async Ping Debug Dashboard</title>
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
                grid-template-columns: 1fr 1fr;
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
            
            .ping-debug-section {
                background: #f8f9fa;
                padding: 15px;
                border-radius: 8px;
                margin-top: 15px;
            }
            
            .ping-event {
                padding: 8px;
                margin: 5px 0;
                background: white;
                border-left: 4px solid var(--primary);
                border-radius: 4px;
                font-size: 12px;
            }
            
            .ping-success {
                border-left-color: var(--success);
            }
            
            .ping-error {
                border-left-color: var(--danger);
            }
            
            .ping-warning {
                border-left-color: var(--warning);
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
            
            .grid-full {
                grid-column: 1 / -1;
            }
            
            .ping-stats {
                background: var(--light);
                padding: 10px;
                border-radius: 8px;
                margin-top: 10px;
                font-size: 12px;
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
            
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            
            th, td {
                padding: 8px;
                text-align: left;
                border-bottom: 1px solid #ddd;
                font-size: 12px;
            }
            
            th {
                background: #f5f5f5;
            }
            
            .toggle-btn {
                padding: 6px 12px;
                background: #666;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 11px;
            }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <div class="header">
                <h1 style="margin:0;color:var(--primary);">🚀 Xiaozhi MCP v3.6.1-DEBUG</h1>
                <p style="color:#666;margin:5px 0 20px 0;">
                    Async Ping Debug Mode <span class="ping-badge">⚡ 50ms FIRST PING</span>
                </p>
                
                <div class="status-grid">
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Uptime</div>
                        <div class="uptime-display" id="uptime">{{hours}}h {{minutes}}m {{seconds}}s</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Active Pings</div>
                        <div style="font-size:24px;color:var(--primary);" id="activePings">{{active_pings_count}}</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Total Ping Events</div>
                        <div style="font-size:24px;color:var(--primary);" id="pingEvents">{{ping_events_count}}</div>
                    </div>
                    <div class="status-item">
                        <div style="color:#666;font-size:14px;">Status</div>
                        <div style="font-size:24px;color:var(--success);">✅ DEBUG ACTIVE</div>
                    </div>
                </div>
                
                <div class="ping-stats">
                    <div><strong>Debug Configuration:</strong></div>
                    <div>• First ping: {{first_ping_delay}}s (INSTANT!)</div>
                    <div>• Continuous: {{continuous_ping_interval}}s interval</div>
                    <div>• Debug mode: {{debug_mode}}</div>
                    <div>• Active requests: {{active_requests_count}}</div>
                </div>
            </div>
            
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">📊 Ping Debug Dashboard</h3>
                <div style="margin:15px 0;">
                    <button class="toggle-btn" onclick="toggleDebug()">Toggle Debug Mode</button>
                    <button class="toggle-btn" onclick="clearPingLogs()">Clear Ping Logs</button>
                    <button class="toggle-btn" onclick="forcePingTest()">Force Ping Test</button>
                </div>
                
                <h4>🎯 Recent Ping Events ({{recent_pings|length}})</h4>
                <div class="ping-debug-section" style="max-height: 300px; overflow-y: auto;">
                    {% for ping in recent_pings %}
                    <div class="ping-event {% if ping.success %}ping-success{% else %}ping-error{% endif %}">
                        <div style="display:flex;justify-content:space-between;">
                            <span><strong>{{ping.type}}</strong></span>
                            <span style="color:#666;font-size:10px;">{{ping.time}}</span>
                        </div>
                        <div style="font-size:11px;color:#666;">ID: {{ping.request_id}}</div>
                        <div style="font-size:11px;">{{ping.details}}</div>
                        <div style="font-size:10px;color:#999;">
                            WS: {{'✅' if ping.ws_open else '❌'}} | 
                            Success: {{'✅' if ping.success else '❌'}}
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">🔍 Ping System State</h3>
                <div style="margin:15px 0;">
                    <div><strong>Active Ping Tasks:</strong> {{ping_manager_tasks}}</div>
                    <div><strong>WebSocket Refs:</strong> {{websocket_refs}}</div>
                    <div><strong>Active Requests Dict:</strong> {{active_requests_dict}}</div>
                </div>
                
                <h4>📈 Ping Performance</h4>
                <table>
                    <tr>
                        <th>Metric</th>
                        <th>Value</th>
                        <th>Status</th>
                    </tr>
                    <tr>
                        <td>First Ping Delay</td>
                        <td>{{first_ping_delay}}s</td>
                        <td>{% if first_ping_delay <= 0.1 %}✅ Optimal{% else %}⚠️ High{% endif %}</td>
                    </tr>
                    <tr>
                        <td>Continuous Interval</td>
                        <td>{{continuous_ping_interval}}s</td>
                        <td>{% if continuous_ping_interval <= 1 %}✅ Optimal{% else %}⚠️ High{% endif %}</td>
                    </tr>
                    <tr>
                        <td>Async Tasks</td>
                        <td>{{async_task_count}}</td>
                        <td>{% if async_task_count > 0 %}✅ Running{% else %}❌ Stopped{% endif %}</td>
                    </tr>
                </table>
            </div>
            
            <div class="card grid-full">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <h3 style="margin-top:0;color:var(--primary);">📝 Live System Logs</h3>
                    <div>
                        <button class="refresh-btn" onclick="refreshLogs()">↻ Update Logs</button>
                        <button class="toggle-btn" onclick="toggleAutoRefresh()">Auto: <span id="autoRefreshStatus">OFF</span></button>
                    </div>
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
                <h3 style="margin-top:0;color:var(--primary);">🧪 Quick Tests</h3>
                <div style="display:grid;gap:10px;">
                    {% for query in test_queries %}
                    <button class="test-btn" onclick="runTest('{{query}}')" style="padding:10px;background:#f5f5f5;border:1px solid #ddd;border-radius:6px;text-align:left;cursor:pointer;">
                        Test: {{query[:40]}}{% if query|length > 40 %}...{% endif %}
                    </button>
                    {% endfor %}
                </div>
                
                <div style="margin-top:15px;">
                    <input type="text" id="customQuery" placeholder="Custom test query..." 
                           style="width:70%;padding:10px;border:1px solid #ddd;border-radius:6px;">
                    <button onclick="runCustomTest()" style="padding:10px 15px;background:var(--primary);color:white;border:none;border-radius:6px;">
                        Run
                    </button>
                </div>
                
                <div id="testResult" style="display:none;margin-top:15px;padding:15px;background:#f9f9f9;border-radius:6px;"></div>
            </div>
            
            <div class="card">
                <h3 style="margin-top:0;color:var(--primary);">⚙️ System Controls</h3>
                <div style="display:grid;gap:10px;grid-template-columns:1fr 1fr;">
                    <button onclick="clearCache()" style="padding:10px;background:var(--warning);color:white;border:none;border-radius:6px;">
                        🗑️ Clear Cache
                    </button>
                    <button onclick="stopAllPings()" style="padding:10px;background:var(--danger);color:white;border:none;border-radius:6px;">
                        🛑 Stop All Pings
                    </button>
                    <button onclick="runDiagnostics()" style="padding:10px;background:#666;color:white;border:none;border-radius:6px;">
                        🩺 Run Diagnostics
                    </button>
                    <button onclick="refreshAll()" style="padding:10px;background:var(--primary);color:white;border:none;border-radius:6px;">
                        🔄 Refresh All
                    </button>
                </div>
                
                <div style="margin-top:15px;padding:10px;background:#f5f5f5;border-radius:6px;">
                    <div><strong>Debug Endpoints:</strong></div>
                    <div style="font-size:12px;">
                        <a href="/ping-debug" target="_blank">/ping-debug</a> - Ping debug info<br>
                        <a href="/ping-stats" target="_blank">/ping-stats</a> - Ping statistics<br>
                        <a href="/system-state" target="_blank">/system-state</a> - Full system state
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            let autoRefreshInterval = null;
            
            function updateDashboard() {
                fetch('/api/ping-stats')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('activePings').textContent = data.active_pings;
                        document.getElementById('pingEvents').textContent = data.total_events;
                        document.getElementById('uptime').textContent = 
                            `${data.uptime.hours}h ${data.uptime.minutes}m ${data.uptime.seconds}s`;
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
            
            function refreshPingEvents() {
                fetch('/api/ping-events')
                    .then(r => r.json())
                    .then(events => {
                        // Update ping events display
                        const eventsContainer = document.querySelector('.ping-debug-section');
                        if (eventsContainer) {
                            eventsContainer.innerHTML = events.map(ping => `
                                <div class="ping-event ${ping.success ? 'ping-success' : 'ping-error'}">
                                    <div style="display:flex;justify-content:space-between;">
                                        <span><strong>${ping.type}</strong></span>
                                        <span style="color:#666;font-size:10px;">${ping.time}</span>
                                    </div>
                                    <div style="font-size:11px;color:#666;">ID: ${ping.request_id}</div>
                                    <div style="font-size:11px;">${ping.details}</div>
                                    <div style="font-size:10px;color:#999;">
                                        WS: ${ping.ws_open ? '✅' : '❌'} | 
                                        Success: ${ping.success ? '✅' : '❌'}
                                    </div>
                                </div>
                            `).join('');
                        }
                    });
            }
            
            function toggleDebug() {
                fetch('/api/toggle-debug', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        alert(`Debug mode: ${data.debug_mode ? 'ON' : 'OFF'}`);
                        location.reload();
                    });
            }
            
            function clearPingLogs() {
                if (confirm('Clear all ping debug logs?')) {
                    fetch('/api/clear-ping-logs', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            alert('Ping logs cleared!');
                            refreshPingEvents();
                        });
                }
            }
            
            function forcePingTest() {
                fetch('/api/test-ping', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        alert(`Ping test initiated: ${data.message}`);
                        setTimeout(refreshPingEvents, 1000);
                    });
            }
            
            function stopAllPings() {
                if (confirm('Stop all active ping tasks?')) {
                    fetch('/api/stop-pings', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            alert(`Stopped ${data.stopped} ping tasks`);
                            updateDashboard();
                        });
                }
            }
            
            function runTest(query) {
                fetch('/test-smart/' + encodeURIComponent(query))
                    .then(r => r.json())
                    .then(data => {
                        const resultDiv = document.getElementById('testResult');
                        resultDiv.style.display = 'block';
                        resultDiv.innerHTML = `
                            <div style="color:var(--success);margin-bottom:10px;">✅ Test Complete</div>
                            <div><strong>Query:</strong> ${data.query}</div>
                            <div><strong>Result:</strong> ${data.result}</div>
                            <div style="margin-top:10px;color:#666;font-size:12px;">
                                ${data.timestamp}
                            </div>
                        `;
                    })
                    .catch(err => {
                        alert('Test error: ' + err);
                    });
            }
            
            function runCustomTest() {
                const query = document.getElementById('customQuery').value;
                if (query) runTest(query);
            }
            
            function toggleAutoRefresh() {
                const statusSpan = document.getElementById('autoRefreshStatus');
                if (autoRefreshInterval) {
                    clearInterval(autoRefreshInterval);
                    autoRefreshInterval = null;
                    statusSpan.textContent = 'OFF';
                } else {
                    autoRefreshInterval = setInterval(() => {
                        updateDashboard();
                        refreshPingEvents();
                    }, 2000);
                    statusSpan.textContent = 'ON';
                }
            }
            
            function refreshAll() {
                updateDashboard();
                refreshLogs();
                refreshPingEvents();
            }
            
            // Initial setup
            updateDashboard();
            setInterval(updateDashboard, 3000);
            
            // Auto-refresh logs every 5 seconds
            setInterval(refreshLogs, 5000);
            
            // Auto-refresh ping events every 3 seconds
            setInterval(refreshPingEvents, 3000);
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    seconds=seconds,
    active_pings_count=len(ping_tracker.get_active_requests()),
    ping_events_count=len(ping_tracker.events),
    recent_pings=recent_pings,
    active_requests_count=len(active_requests),
    ping_manager_tasks=len(ping_manager.active_tasks),
    websocket_refs=len(ping_manager.websocket_refs),
    active_requests_dict=str(list(active_requests.keys())),
    first_ping_delay=FIRST_PING_DELAY,
    continuous_ping_interval=CONTINUOUS_PING_INTERVAL,
    debug_mode=DEBUG_PING,
    logs=log_buffer[-20:],
    test_queries=test_queries,
    async_task_count=len(ping_manager.active_tasks))

# ================= DEBUG ENDPOINTS =================
@app.route('/ping-debug')
def ping_debug():
    """Debug endpoint for ping system."""
    recent_events = ping_tracker.get_recent_events(50)
    active_requests_list = ping_tracker.get_active_requests()
    
    return jsonify({
        "debug_mode": DEBUG_PING,
        "ping_config": {
            "first_ping_delay": FIRST_PING_DELAY,
            "continuous_interval": CONTINUOUS_PING_INTERVAL,
            "async_mode": ASYNC_PING_MODE
        },
        "statistics": {
            "total_events": len(ping_tracker.events),
            "active_requests": len(active_requests_list),
            "request_stats": len(ping_tracker.request_stats),
            "cleaned_old": ping_tracker.clear_old_requests()
        },
        "recent_events": recent_events,
        "active_requests": active_requests_list,
        "ping_manager_state": {
            "active_tasks": len(ping_manager.active_tasks),
            "websocket_refs": len(ping_manager.websocket_refs),
            "task_names": list(ping_manager.active_tasks.keys())
        },
        "timestamp": datetime.now().isoformat()
    })

@app.route('/ping-stats')
def ping_stats():
    """Ping statistics endpoint."""
    return jsonify({
        "active_pings": len(ping_tracker.get_active_requests()),
        "total_events": len(ping_tracker.events),
        "uptime": {
            "hours": int((time.time() - server_start_time) // 3600),
            "minutes": int((time.time() - server_start_time) % 3600 // 60),
            "seconds": int((time.time() - server_start_time) % 60)
        },
        "config": {
            "first_ping_delay": FIRST_PING_DELAY,
            "continuous_interval": CONTINUOUS_PING_INTERVAL
        }
    })

@app.route('/system-state')
def system_state():
    """Full system state endpoint."""
    return jsonify({
        "server": {
            "uptime": time.time() - server_start_time,
            "version": "3.6.1-DEBUG",
            "timestamp": datetime.now().isoformat()
        },
        "ping_system": {
            "debug_mode": DEBUG_PING,
            "active_tasks": len(ping_manager.active_tasks),
            "active_requests": len(active_requests),
            "recent_events_count": len(ping_tracker.events),
            "request_stats_count": len(ping_tracker.request_stats)
        },
        "cache": {
            "size": len(gemini_cache),
            "duration_seconds": CACHE_DURATION
        },
        "connections": {
            "xiaozhi_ws": "CONFIGURED" if XIAOZHI_WS else "MISSING",
            "gemini_key": "CONFIGURED" if GEMINI_API_KEY else "MISSING",
            "google_key": "CONFIGURED" if GOOGLE_API_KEY else "MISSING"
        }
    })

@app.route('/api/ping-stats')
def api_ping_stats():
    """API endpoint for ping statistics."""
    uptime = int(time.time() - server_start_time)
    return jsonify({
        'active_pings': len(ping_tracker.get_active_requests()),
        'total_events': len(ping_tracker.events),
        'uptime': {
            'hours': uptime // 3600,
            'minutes': (uptime % 3600) // 60,
            'seconds': uptime % 60
        }
    })

@app.route('/api/ping-events')
def api_ping_events():
    """API endpoint for recent ping events."""
    return jsonify(ping_tracker.get_recent_events(20))

@app.route('/api/logs')
def api_logs():
    """API endpoint for system logs."""
    return jsonify(log_buffer[-30:])

@app.route('/api/toggle-debug', methods=['POST'])
def api_toggle_debug():
    """Toggle debug mode."""
    global DEBUG_PING
    DEBUG_PING = not DEBUG_PING
    add_log('INFO', f'Debug mode {"enabled" if DEBUG_PING else "disabled"}')
    return jsonify({'debug_mode': DEBUG_PING})

@app.route('/api/clear-ping-logs', methods=['POST'])
def api_clear_ping_logs():
    """Clear ping debug logs."""
    old_count = len(ping_tracker.events)
    ping_tracker.events.clear()
    add_log('INFO', f'Cleared {old_count} ping debug logs')
    return jsonify({'cleared': old_count})

@app.route('/api/test-ping', methods=['POST'])
def api_test_ping():
    """Test ping system."""
    test_id = f"test_{uuid.uuid4().hex[:8]}"
    ping_tracker.add_event(test_id, "TEST_PING", "Manual ping test triggered", success=True)
    add_log('INFO', f'Ping test triggered: {test_id}')
    return jsonify({'message': f'Ping test {test_id} triggered', 'test_id': test_id})

@app.route('/api/stop-pings', methods=['POST'])
def api_stop_pings():
    """Stop all ping tasks."""
    stopped = len(ping_manager.active_tasks)
    ping_manager.stop_all_pings()
    add_log('INFO', f'Stopped {stopped} ping tasks')
    return jsonify({'stopped': stopped})

@app.route('/health')
def health_check():
    """Health check endpoint."""
    uptime = int(time.time() - server_start_time)
    add_log('INFO', 'Health check accessed')
    
    return jsonify({
        "status": "async_ping_debug_healthy",
        "version": "3.6.1-DEBUG",
        "uptime": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "cache_size": len(gemini_cache),
        "active_requests": len(active_requests),
        "ping_debug": {
            "enabled": DEBUG_PING,
            "events_count": len(ping_tracker.events),
            "active_pings": len(ping_tracker.get_active_requests())
        },
        "timestamp": datetime.now().isoformat()
    })

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
            logger.info("🔗 Connecting to Xiaozhi (Async Ping Debug Mode)...")
            add_log('INFO', f'Connecting to Xiaozhi WebSocket')
            logger.debug(f"  • WebSocket URL: {XIAOZHI_WS[:50]}...")
            logger.debug(f"  • Reconnect delay: {reconnect_delay}s")
            
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=15,
                ping_timeout=10,
                close_timeout=5
            ) as websocket:
                logger.info("✅ CONNECTED TO XIAOZHI")
                add_log('INFO', 'Connected to Xiaozhi (Async Ping Debug)')
                logger.debug(f"  • WebSocket ID: {id(websocket)}")
                logger.debug(f"  • WebSocket open: {websocket.open}")
                
                reconnect_delay = 1
                
                async for raw_message in websocket:
                    try:
                        data = json.loads(raw_message)
                        message_id = data.get("id")
                        method = data.get("method", "")
                        params = data.get("params", {})
                        
                        logger.debug(f"📨 Received MCP message: {method}")
                        logger.debug(f"  • Message ID: {message_id}")
                        logger.debug(f"  • Params keys: {list(params.keys())}")
                        
                        response = None
                        
                        if method == "ping":
                            logger.debug("  • Handling ping request")
                            response = AsyncMCPHandler.handle_ping(message_id)
                        elif method == "initialize":
                            logger.debug("  • Handling initialize request")
                            response = AsyncMCPHandler.handle_initialize(message_id)
                            add_log('INFO', 'MCP Initialized (Async Ping Debug)')
                        elif method == "tools/list":
                            logger.debug("  • Handling tools/list request")
                            response = AsyncMCPHandler.handle_tools_list(message_id)
                        elif method == "tools/call":
                            tool_name = params.get("name", "")
                            logger.debug(f"  • Handling tools/call: {tool_name}")
                            response = await AsyncMCPHandler.handle_tools_call_async(
                                message_id, params, websocket
                            )
                            if tool_name == "ask_ai":
                                add_log('INFO', 'AI processed with async pings (debug)')
                        else:
                            logger.warning(f"  • Unknown method: {method}")
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "Unknown method"}}
                        
                        if response:
                            logger.debug(f"📤 Sending response for message {message_id}")
                            await websocket.send(json.dumps(response))
                            logger.debug(f"  • Response sent successfully")
                            
                    except json.JSONDecodeError as e:
                        error_msg = f'JSON decode error: {str(e)[:30]}'
                        logger.error(f"❌ {error_msg}")
                        add_log('ERROR', error_msg)
                    except Exception as e:
                        error_msg = f'Message processing error: {str(e)[:30]}'
                        logger.error(f"❌ {error_msg}")
                        add_log('ERROR', error_msg)
                        
        except websockets.exceptions.ConnectionClosed as e:
            error_msg = f"WebSocket connection closed: {e.code} - {e.reason}"
            logger.error(f"❌ {error_msg}")
            add_log('ERROR', error_msg)
        except ConnectionRefusedError as e:
            error_msg = f"Connection refused: {str(e)}"
            logger.error(f"❌ {error_msg}")
            add_log('ERROR', error_msg)
        except Exception as e:
            error_msg = f"Connection error: {str(e)[:50]}"
            logger.error(f"❌ {error_msg}")
            add_log('ERROR', error_msg)
            
            # Clean up all ping tasks
            logger.debug("🧹 Cleaning up ping tasks due to connection error")
            ping_manager.stop_all_pings()
            active_requests.clear()
            
            logger.info(f"🔄 Reconnecting in {reconnect_delay}s...")
            add_log('INFO', f'Reconnecting in {reconnect_delay}s')
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 5)

async def main():
    logger.info("""
    ╔══════════════════════════════════════════════════════╗
    ║           🚀 ASYNC PING MCP v3.6.1-DEBUG            ║
    ║            COMPREHENSIVE DEBUG ENABLED              ║
    ║           PING TRACKING: {} EVENTS                ║
    ╚══════════════════════════════════════════════════════╝
    """.format(len(ping_tracker.events)))
    
    add_log('INFO', '🚀 Starting Async Ping Debug Server...')
    add_log('DEBUG', f'First ping delay: {FIRST_PING_DELAY}s')
    add_log('DEBUG', f'Continuous interval: {CONTINUOUS_PING_INTERVAL}s')
    add_log('DEBUG', f'Parallel model tries: {PARALLEL_MODEL_TRIES}')
    
    # Start web server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Web server started on port 3000")
    logger.debug("  • Thread ID: {}".format(web_thread.ident))
    logger.debug("  • Thread alive: {}".format(web_thread.is_alive()))
    
    # Start MCP bridge
    logger.info("🔗 Starting MCP bridge with debug ping tracking...")
    await async_mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Server stopped by user")
        add_log('INFO', 'Server stopped by user')
        ping_manager.stop_all_pings()
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
        add_log('ERROR', f'Fatal: {str(e)[:50]}')
        ping_manager.stop_all_pings()