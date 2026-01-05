# main.py - XIAOZHI MCP SERVER v3.6.2 - UNLIMITED LOGS WITH UTC+8 (FIXED)
import os
import asyncio
import json
import websockets
import requests
import logging
from flask import Flask, jsonify, render_template_string, Response, request
import threading
import time
import sys
from dotenv import load_dotenv
import re
import hashlib
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid
from typing import Dict, List, Deque
from dataclasses import dataclass, asdict
from collections import deque
import html

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

# ================= TIMEZONE CONFIGURATION =================
UTC_PLUS_8 = timezone(timedelta(hours=8))

def get_utc8_time():
    """Get current time in UTC+8"""
    return datetime.now(UTC_PLUS_8)

def format_utc8_time(dt=None):
    """Format datetime in UTC+8 with milliseconds"""
    if dt is None:
        dt = get_utc8_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

def format_utc8_time_short(dt=None):
    """Format datetime in UTC+8 (short version)"""
    if dt is None:
        dt = get_utc8_time()
    return dt.strftime('%H:%M:%S.%f')[:-3]

# ================= UNLIMITED LOGGING CONFIGURATION =================
UNLIMITED_LOGS = True  # No maximum log length
DEBUG_PING = True  # Enable detailed ping debugging
LOG_STREAM_ENABLED = True  # Enable live log streaming

# ================= ASYNC PING CONFIGURATION =================
ASYNC_PING_MODE = True
FIRST_PING_DELAY = 0.05  # 50ms - INSTANT first ping!
CONTINUOUS_PING_INTERVAL = 0.8  # 0.8 seconds
PARALLEL_MODEL_TRIES = 3
MAX_WORKERS = 5

# ================= UNLIMITED LOGGING SETUP =================
class UnlimitedMemoryHandler(logging.Handler):
    """Custom handler that stores ALL logs in memory with no limit"""
    
    def __init__(self):
        super().__init__()
        self.logs: Deque[Dict] = deque()
        self.formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    def emit(self, record):
        """Store log record with UTC+8 timestamp"""
        try:
            # Format the record
            msg = self.format(record)
            
            # Create log entry with UTC+8 time
            log_entry = {
                'timestamp': get_utc8_time(),
                'formatted_time': format_utc8_time_short(),
                'level': record.levelname,
                'message': msg,
                'module': record.module,
                'funcName': record.funcName,
                'lineno': record.lineno,
                'thread': record.threadName or str(record.thread),
                'process': record.processName or str(record.process)
            }
            
            # Add to unlimited log storage
            self.logs.append(log_entry)
            
        except Exception as e:
            print(f"❌ Log handler error: {e}")
    
    def get_all_logs(self):
        """Get all stored logs"""
        return list(self.logs)
    
    def get_recent_logs(self, count=100):
        """Get most recent logs"""
        return list(self.logs)[-count:] if self.logs else []
    
    def get_logs_since(self, since: datetime):
        """Get logs since specific UTC+8 time"""
        return [log for log in self.logs if log['timestamp'] >= since]
    
    def clear_logs(self):
        """Clear all logs"""
        self.logs.clear()
    
    def count(self):
        """Get total log count"""
        return len(self.logs)

# Setup unlimited logging
logger = logging.getLogger('xiaozhi_mcp')
logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture EVERYTHING

# Remove any existing handlers
logger.handlers.clear()

# Create and add unlimited memory handler
unlimited_handler = UnlimitedMemoryHandler()
unlimited_handler.setLevel(logging.DEBUG)
logger.addHandler(unlimited_handler)

# Also add console handler for real-time viewing
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# ================= GLOBAL STATE WITH UNLIMITED LOGS =================
gemini_cache = {}
CACHE_DURATION = 300
active_requests: Dict[str, bool] = {}
ping_tasks: Dict[str, asyncio.Task] = {}
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
all_ping_events: Deque[Dict] = deque()  # Unlimited ping events storage
all_web_socket_events: Deque[Dict] = deque()  # Unlimited WebSocket events

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

# ================= UNLIMITED EVENT TRACKING =================
@dataclass
class DetailedEvent:
    """Track every single event with unlimited detail"""
    event_id: str
    timestamp: datetime
    event_type: str
    source: str
    details: str
    data: Dict
    success: bool = False
    duration_ms: float = 0.0
    
    def to_dict(self):
        return {
            'id': self.event_id,
            'time': format_utc8_time(self.timestamp),
            'time_short': format_utc8_time_short(self.timestamp),
            'type': self.event_type,
            'source': self.source,
            'details': self.details,
            'data': self.data,
            'success': self.success,
            'duration_ms': self.duration_ms,
            'timestamp_iso': self.timestamp.isoformat()
        }

class UnlimitedEventTracker:
    """Track unlimited events with detailed information"""
    
    def __init__(self):
        self.events: Deque[DetailedEvent] = deque()
        self.event_counter = 0
        self.start_time = get_utc8_time()
    
    def add_event(self, event_type: str, source: str, details: str, 
                  data: Dict = None, success: bool = False, duration_ms: float = 0.0):
        """Add a detailed event with automatic ID and timestamp"""
        self.event_counter += 1
        event_id = f"ev_{self.event_counter:08d}_{int(time.time()*1000)}"
        
        event = DetailedEvent(
            event_id=event_id,
            timestamp=get_utc8_time(),
            event_type=event_type,
            source=source,
            details=details,
            data=data or {},
            success=success,
            duration_ms=duration_ms
        )
        
        self.events.append(event)
        
        # Log to unlimited logger
        status = "✅" if success else "❌"
        logger.debug(f"{status} [{event_type}] {source}: {details}")
        
        return event
    
    def add_ping_event(self, request_id: str, event_type: str, details: str,
                       websocket_open: bool = False, success: bool = False):
        """Add ping-specific event"""
        return self.add_event(
            event_type=f"PING_{event_type}",
            source=f"ping_{request_id}",
            details=details,
            data={
                'request_id': request_id,
                'websocket_open': websocket_open,
                'ping_count': self._get_ping_count(request_id)
            },
            success=success
        )
    
    def add_websocket_event(self, event_type: str, details: str, 
                           websocket_id: str = None, data: Dict = None):
        """Add WebSocket-specific event"""
        # Determine success based on event type
        is_success = event_type not in ['ERROR', 'CLOSED', 'FAILED', 'CONNECTION_ERROR', 
                                       'CONNECTION_REFUSED', 'JSON_ERROR', 'MESSAGE_ERROR',
                                       'UNKNOWN_METHOD']
        
        return self.add_event(
            event_type=f"WS_{event_type}",
            source=f"ws_{websocket_id or 'unknown'}",
            details=details,
            data=data or {},
            success=is_success
        )
    
    def add_ai_event(self, event_type: str, details: str, request_id: str = None, 
                    model: str = None, tier: str = None, success: bool = False, 
                    duration_ms: float = 0.0, data: Dict = None):
        """Add AI processing event"""
        return self.add_event(
            event_type=f"AI_{event_type}",
            source=f"ai_{request_id or 'unknown'}",
            details=details,
            data={
                'request_id': request_id,
                'model': model,
                'tier': tier,
                'cache_size': len(gemini_cache),
                **(data or {})
            },
            success=success,
            duration_ms=duration_ms
        )
    
    def add_system_event(self, event_type: str, details: str, data: Dict = None, 
                        success: bool = True):
        """Add system-level event"""
        return self.add_event(
            event_type=f"SYS_{event_type}",
            source="system",
            details=details,
            data=data or {},
            success=success
        )
    
    def _get_ping_count(self, request_id: str) -> int:
        """Count ping events for a specific request"""
        return sum(1 for e in self.events 
                  if e.source == f"ping_{request_id}" and "PING_" in e.event_type)
    
    def get_all_events(self):
        """Get ALL events"""
        return [e.to_dict() for e in self.events]
    
    def get_recent_events(self, count: int = 100):
        """Get most recent events"""
        events = list(self.events)[-count:] if self.events else []
        return [e.to_dict() for e in events]
    
    def get_events_by_type(self, event_type: str, limit: int = 100):
        """Get events filtered by type"""
        filtered = []
        for e in reversed(self.events):
            if event_type in e.event_type:
                filtered.append(e.to_dict())
                if len(filtered) >= limit:
                    break
        return filtered
    
    def get_events_by_source(self, source: str, limit: int = 100):
        """Get events filtered by source"""
        filtered = []
        for e in reversed(self.events):
            if source in e.source:
                filtered.append(e.to_dict())
                if len(filtered) >= limit:
                    break
        return filtered
    
    def get_event_statistics(self):
        """Get event statistics"""
        total = len(self.events)
        by_type = {}
        by_source = {}
        by_success = {'success': 0, 'failed': 0}
        
        for event in self.events:
            # Count by type
            event_type = event.event_type.split('_')[0] if '_' in event.event_type else event.event_type
            by_type[event_type] = by_type.get(event_type, 0) + 1
            
            # Count by source
            source = event.source.split('_')[0] if '_' in event.source else event.source
            by_source[source] = by_source.get(source, 0) + 1
            
            # Count by success
            if event.success:
                by_success['success'] += 1
            else:
                by_success['failed'] += 1
        
        return {
            'total_events': total,
            'events_per_second': total / max(1, (get_utc8_time() - self.start_time).total_seconds()),
            'by_type': by_type,
            'by_source': by_source,
            'by_success': by_success,
            'start_time': format_utc8_time(self.start_time),
            'uptime_seconds': (get_utc8_time() - self.start_time).total_seconds()
        }
    
    def clear_events(self):
        """Clear all events"""
        self.events.clear()
        self.event_counter = 0
        self.start_time = get_utc8_time()
        logger.info("🗑️ All events cleared")

# Global event tracker
event_tracker = UnlimitedEventTracker()

# ================= ASYNC KEEP-ALIVE MANAGER WITH DETAILED LOGGING =================
class AsyncPingManager:
    """Async-based keep-alive system with unlimited detailed logging."""
    
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.websocket_refs: Dict[str, websockets.WebSocketClientProtocol] = {}
        self.ping_counters: Dict[str, int] = {}
        self.start_times: Dict[str, datetime] = {}
        
        # Log initialization
        event_tracker.add_system_event(
            "INIT",
            "AsyncPingManager initialized",
            {'timestamp': format_utc8_time()}
        )
        logger.info("🔄 AsyncPingManager initialized")
    
    async def start_pinging(self, websocket, request_id: str, duration: int = 30):
        """Start async ping task for a request with detailed logging."""
        start_time = get_utc8_time()
        
        try:
            # PHASE 1: Detailed parameter validation
            event_tracker.add_ping_event(
                request_id, "VALIDATE", 
                f"Validating ping start parameters",
                websocket_open=websocket.open if hasattr(websocket, 'open') else False,
                success=True
            )
            
            logger.debug(f"🔍 [PING_START] Validating parameters for {request_id}")
            logger.debug(f"   • WebSocket: {'Present' if websocket else 'Missing'}")
            logger.debug(f"   • WebSocket ID: {id(websocket)}")
            logger.debug(f"   • Has 'open' attr: {hasattr(websocket, 'open')}")
            
            if not websocket:
                error_msg = "No WebSocket provided"
                event_tracker.add_ping_event(
                    request_id, "ERROR", 
                    error_msg,
                    success=False
                )
                logger.error(f"❌ [PING_START] {error_msg} for {request_id}")
                return None
            
            # Store initial state
            self.start_times[request_id] = start_time
            self.ping_counters[request_id] = 0
            
            # Store WebSocket reference
            self.websocket_refs[request_id] = websocket
            
            event_tracker.add_ping_event(
                request_id, "CONFIG", 
                f"Ping configured: duration={duration}s, first_delay={FIRST_PING_DELAY}s, interval={CONTINUOUS_PING_INTERVAL}s",
                websocket_open=websocket.open,
                success=True
            )
            
            logger.info(f"🚀 [PING_START] Starting async ping for {request_id}")
            logger.debug(f"   • Duration: {duration}s")
            logger.debug(f"   • First ping delay: {FIRST_PING_DELAY}s")
            logger.debug(f"   • Continuous interval: {CONTINUOUS_PING_INTERVAL}s")
            logger.debug(f"   • WebSocket open: {websocket.open}")
            logger.debug(f"   • Active tasks: {len(self.active_tasks)}")
            
            # Create and store ping task
            task = asyncio.create_task(
                self._ping_worker(websocket, request_id, duration),
                name=f"ping_{request_id}"
            )
            self.active_tasks[request_id] = task
            
            # Add done callback for cleanup
            task.add_done_callback(lambda t: self._cleanup(request_id))
            
            event_tracker.add_ping_event(
                request_id, "TASK_CREATED", 
                f"Async task created: {task.get_name()}",
                success=True
            )
            
            logger.info(f"✅ [PING_START] Async ping started for {request_id}")
            logger.debug(f"   • Task name: {task.get_name()}")
            logger.debug(f"   • Task done: {task.done()}")
            logger.debug(f"   • Task cancelled: {task.cancelled()}")
            logger.debug(f"   • Event loop: {asyncio.get_running_loop()}")
            
            return task
            
        except Exception as e:
            error_msg = f"Failed to start async ping: {str(e)}"
            event_tracker.add_ping_event(
                request_id, "ERROR", 
                error_msg,
                success=False
            )
            logger.error(f"❌ [PING_START] {error_msg}")
            logger.exception("Detailed error trace:")
            return None
    
    async def _ping_worker(self, websocket, request_id: str, duration: int):
        """Async ping worker with detailed step-by-step logging."""
        start_time = get_utc8_time()
        ping_count = 0
        
        event_tracker.add_ping_event(
            request_id, "WORKER_START", 
            f"Ping worker started with duration {duration}s",
            websocket_open=websocket.open,
            success=True
        )
        
        logger.info(f"📡 [PING_WORKER] Worker started for {request_id}")
        logger.debug(f"   • Start time: {format_utc8_time(start_time)}")
        logger.debug(f"   • Duration: {duration}s")
        logger.debug(f"   • WebSocket ID: {id(websocket)}")
        logger.debug(f"   • Event loop: {asyncio.get_running_loop()}")
        
        try:
            # PHASE 1: INSTANT FIRST PING (50ms)
            logger.debug(f"⏳ [PING_WORKER] Waiting {FIRST_PING_DELAY}s for first ping...")
            
            # Log waiting start
            event_tracker.add_ping_event(
                request_id, "WAIT_FIRST", 
                f"Waiting {FIRST_PING_DELAY}s before first ping",
                websocket_open=websocket.open,
                success=True
            )
            
            await asyncio.sleep(FIRST_PING_DELAY)
            
            # Log waiting complete
            waited_time = (get_utc8_time() - start_time).total_seconds()
            event_tracker.add_ping_event(
                request_id, "WAIT_COMPLETE", 
                f"First ping wait complete after {waited_time:.3f}s",
                websocket_open=websocket.open,
                success=True
            )
            
            logger.debug(f"✅ [PING_WORKER] First ping wait complete ({waited_time:.3f}s)")
            
            # Send first ping
            logger.debug(f"📤 [PING_WORKER] Sending first ping...")
            first_ping_success = await self._send_safe_ping(websocket, request_id, ping_count, is_first=True)
            
            if first_ping_success:
                ping_count += 1
                self.ping_counters[request_id] = ping_count
                
                event_tracker.add_ping_event(
                    request_id, "FIRST_SENT", 
                    f"First ping sent successfully after {waited_time:.3f}s",
                    websocket_open=websocket.open,
                    success=True
                )
                
                logger.info(f"✅ [PING_WORKER] INSTANT PING #1 sent for {request_id} (after {waited_time:.3f}s)")
                logger.debug(f"   • Ping count: {ping_count}")
                logger.debug(f"   • WebSocket still open: {websocket.open}")
            else:
                event_tracker.add_ping_event(
                    request_id, "FIRST_FAILED", 
                    f"Failed to send first ping after {waited_time:.3f}s",
                    websocket_open=websocket.open,
                    success=False
                )
                
                logger.warning(f"⚠️ [PING_WORKER] Failed to send first ping for {request_id}")
            
            # PHASE 2: CONTINUOUS PINGS (0.8s interval)
            last_ping_time = get_utc8_time()
            continuous_start = get_utc8_time()
            
            event_tracker.add_ping_event(
                request_id, "CONTINUOUS_START", 
                f"Starting continuous pings with {CONTINUOUS_PING_INTERVAL}s interval",
                websocket_open=websocket.open,
                success=True
            )
            
            logger.info(f"🔄 [PING_WORKER] Starting continuous pings for {request_id}")
            logger.debug(f"   • Interval: {CONTINUOUS_PING_INTERVAL}s")
            logger.debug(f"   • Target duration: {duration}s")
            
            while (get_utc8_time() - start_time).total_seconds() < duration:
                loop_start = get_utc8_time()
                
                # Check if request is still active
                if not self._is_request_active(request_id):
                    event_tracker.add_ping_event(
                        request_id, "REQUEST_COMPLETE", 
                        "Request marked as completed, stopping pings",
                        success=True
                    )
                    logger.info(f"⚠️ [PING_WORKER] Request {request_id} completed, stopping pings")
                    break
                
                # Check if WebSocket is still connected
                if not websocket.open:
                    event_tracker.add_ping_event(
                        request_id, "WS_CLOSED", 
                        "WebSocket closed, stopping pings",
                        websocket_open=False,
                        success=False
                    )
                    logger.warning(f"⚠️ [PING_WORKER] WebSocket closed for {request_id}, stopping pings")
                    break
                
                # Calculate sleep time
                elapsed_since_last = (get_utc8_time() - last_ping_time).total_seconds()
                sleep_time = max(0, CONTINUOUS_PING_INTERVAL - elapsed_since_last)
                
                if sleep_time > 0:
                    event_tracker.add_ping_event(
                        request_id, "SLEEP", 
                        f"Sleeping {sleep_time:.3f}s before next ping",
                        websocket_open=websocket.open,
                        success=True
                    )
                    
                    logger.debug(f"💤 [PING_WORKER] Sleeping {sleep_time:.3f}s before next ping")
                    await asyncio.sleep(sleep_time)
                
                # Send ping
                logger.debug(f"📤 [PING_WORKER] Sending continuous ping #{ping_count + 1}...")
                success = await self._send_safe_ping(websocket, request_id, ping_count, is_first=False)
                last_ping_time = get_utc8_time()
                
                if success:
                    ping_count += 1
                    self.ping_counters[request_id] = ping_count
                    
                    event_tracker.add_ping_event(
                        request_id, "CONTINUOUS_SENT", 
                        f"Continuous ping #{ping_count} sent successfully",
                        websocket_open=websocket.open,
                        success=True
                    )
                    
                    # Log every ping
                    elapsed_total = (get_utc8_time() - start_time).total_seconds()
                    logger.debug(f"📤 [PING_WORKER] Ping #{ping_count} sent (elapsed: {elapsed_total:.1f}s)")
                    
                    # Info log every 5 pings
                    if ping_count % 5 == 0:
                        logger.info(f"📤 [PING_WORKER] Ping #{ping_count} sent for {request_id} (elapsed: {elapsed_total:.1f}s)")
                
                # Calculate loop duration
                loop_duration = (get_utc8_time() - loop_start).total_seconds()
                
                # Log detailed loop info every 10 loops
                if ping_count % 10 == 0 and ping_count > 0:
                    logger.debug(f"📊 [PING_WORKER] Loop #{ping_count} took {loop_duration:.3f}s")
                    logger.debug(f"   • Total elapsed: {(get_utc8_time() - start_time).total_seconds():.1f}s")
                    logger.debug(f"   • WebSocket open: {websocket.open}")
                    logger.debug(f"   • Request active: {self._is_request_active(request_id)}")
                
                # Check if we should continue
                total_elapsed = (get_utc8_time() - start_time).total_seconds()
                if total_elapsed >= duration:
                    event_tracker.add_ping_event(
                        request_id, "DURATION_REACHED", 
                        f"Duration reached: {total_elapsed:.1f}s >= {duration}s",
                        success=True
                    )
                    logger.debug(f"⏰ [PING_WORKER] Duration reached for {request_id}")
                    break
            
            # Worker completion
            total_duration = (get_utc8_time() - start_time).total_seconds()
            continuous_duration = (get_utc8_time() - continuous_start).total_seconds()
            
            event_tracker.add_ping_event(
                request_id, "WORKER_COMPLETE", 
                f"Ping worker completed: {ping_count} pings in {total_duration:.1f}s "
                f"(continuous: {continuous_duration:.1f}s)",
                success=True
            )
            
            logger.info(f"✅ [PING_WORKER] Ping worker completed for {request_id}")
            logger.debug(f"   • Total pings: {ping_count}")
            logger.debug(f"   • Total duration: {total_duration:.1f}s")
            logger.debug(f"   • Continuous duration: {continuous_duration:.1f}s")
            logger.debug(f"   • Average interval: {continuous_duration/max(1, ping_count-1):.3f}s")
            
        except asyncio.CancelledError:
            cancelled_time = get_utc8_time()
            duration_before_cancel = (cancelled_time - start_time).total_seconds()
            
            event_tracker.add_ping_event(
                request_id, "CANCELLED", 
                f"Ping worker cancelled after {duration_before_cancel:.1f}s with {ping_count} pings",
                success=False
            )
            
            logger.info(f"🛑 [PING_WORKER] Ping worker CANCELLED for {request_id}")
            logger.debug(f"   • Duration before cancel: {duration_before_cancel:.1f}s")
            logger.debug(f"   • Pings sent before cancel: {ping_count}")
            raise
            
        except Exception as e:
            error_time = get_utc8_time()
            duration_before_error = (error_time - start_time).total_seconds()
            
            event_tracker.add_ping_event(
                request_id, "ERROR", 
                f"Ping worker error after {duration_before_error:.1f}s: {str(e)}",
                success=False
            )
            
            logger.error(f"❌ [PING_WORKER] Ping worker error for {request_id}")
            logger.debug(f"   • Duration before error: {duration_before_error:.1f}s")
            logger.debug(f"   • Pings sent before error: {ping_count}")
            logger.exception("Detailed error trace:")
            
        finally:
            self._cleanup(request_id)
    
    async def _send_safe_ping(self, websocket, request_id: str, ping_count: int, is_first: bool = False) -> bool:
        """Safely send a ping message with detailed logging."""
        send_start = get_utc8_time()
        ping_number = ping_count + 1
        
        try:
            # Detailed WebSocket state checking
            logger.debug(f"🔍 [PING_SEND] Checking WebSocket state for {request_id}")
            logger.debug(f"   • Ping number: {ping_number}")
            logger.debug(f"   • Is first: {is_first}")
            logger.debug(f"   • WebSocket object: {'Present' if websocket else 'Missing'}")
            
            if not websocket:
                event_tracker.add_ping_event(
                    request_id, "WS_MISSING", 
                    "No WebSocket object",
                    success=False
                )
                logger.debug(f"⚠️ [PING_SEND] No WebSocket object for {request_id}")
                return False
            
            if not hasattr(websocket, 'open'):
                event_tracker.add_ping_event(
                    request_id, "WS_NO_ATTR", 
                    "WebSocket has no 'open' attribute",
                    success=False
                )
                logger.debug(f"⚠️ [PING_SEND] WebSocket has no 'open' attribute for {request_id}")
                return False
            
            ws_open = websocket.open
            logger.debug(f"🔍 [PING_SEND] WebSocket check: open={ws_open}")
            
            if not ws_open:
                event_tracker.add_ping_event(
                    request_id, "WS_CLOSED", 
                    "WebSocket not open",
                    websocket_open=False,
                    success=False
                )
                logger.warning(f"⚠️ [PING_SEND] WebSocket not open for {request_id}")
                return False
            
            # Prepare ping message
            message_idx = ping_count % len(keep_alive_messages)
            ping_type = "FIRST" if is_first else f"#{ping_number}"
            
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
            
            # Log message preparation
            event_tracker.add_ping_event(
                request_id, "PREPARE", 
                f"Preparing ping message: {ping_message['params']['progress']['text']}",
                websocket_open=ws_open,
                success=True
            )
            
            logger.debug(f"📨 [PING_SEND] Preparing ping for {request_id}")
            logger.debug(f"   • Message: {ping_message['params']['progress']['text']}")
            logger.debug(f"   • WebSocket ID: {id(websocket)}")
            logger.debug(f"   • Is open: {websocket.open}")
            
            # Send the ping
            send_start_inner = time.time()
            await websocket.send(json.dumps(ping_message))
            send_duration = (time.time() - send_start_inner) * 1000
            
            # Log success
            total_duration = (get_utc8_time() - send_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "SENT", 
                f"Ping sent successfully in {send_duration:.1f}ms",
                websocket_open=ws_open,
                success=True
            )
            
            logger.debug(f"✅ [PING_SEND] Ping sent successfully for {request_id}")
            logger.debug(f"   • Send duration: {send_duration:.1f}ms")
            logger.debug(f"   • Total duration: {total_duration:.1f}ms")
            logger.debug(f"   • Ping type: {ping_type}")
            
            return True
            
        except websockets.exceptions.ConnectionClosed as e:
            close_duration = (get_utc8_time() - send_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "CONNECTION_CLOSED", 
                f"Connection closed while sending ping: {e.code} - {e.reason} (after {close_duration:.1f}ms)",
                websocket_open=False,
                success=False
            )
            
            logger.warning(f"⚠️ [PING_SEND] Connection closed while pinging {request_id}")
            logger.debug(f"   • Close code: {e.code}")
            logger.debug(f"   • Reason: {e.reason}")
            logger.debug(f"   • Duration before close: {close_duration:.1f}ms")
            return False
            
        except websockets.exceptions.WebSocketException as e:
            error_duration = (get_utc8_time() - send_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "WS_EXCEPTION", 
                f"WebSocket exception: {e} (after {error_duration:.1f}ms)",
                success=False
            )
            
            logger.error(f"❌ [PING_SEND] WebSocket exception for {request_id}: {e}")
            logger.debug(f"   • Duration before error: {error_duration:.1f}ms")
            return False
            
        except Exception as e:
            error_duration = (get_utc8_time() - send_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "UNEXPECTED_ERROR", 
                f"Unexpected error: {e} (after {error_duration:.1f}ms)",
                success=False
            )
            
            logger.error(f"❌ [PING_SEND] Unexpected error sending ping for {request_id}: {e}")
            logger.debug(f"   • Duration before error: {error_duration:.1f}ms")
            logger.exception("Detailed error trace:")
            return False
    
    def _is_request_active(self, request_id: str) -> bool:
        """Check if request is still active."""
        is_active = request_id in active_requests and active_requests[request_id]
        
        # Log active check periodically
        if request_id in self.ping_counters and self.ping_counters[request_id] % 10 == 0:
            logger.debug(f"🔍 [ACTIVE_CHECK] Request {request_id}: {is_active}")
        
        return is_active
    
    def _cleanup(self, request_id: str):
        """Clean up ping task and references with detailed logging."""
        cleanup_start = get_utc8_time()
        
        try:
            logger.debug(f"🧹 [CLEANUP] Starting cleanup for {request_id}")
            
            # Record cleanup start
            event_tracker.add_ping_event(
                request_id, "CLEANUP_START", 
                "Starting cleanup process",
                success=True
            )
            
            # Cancel task if still running
            if request_id in self.active_tasks:
                task = self.active_tasks[request_id]
                
                if not task.done():
                    event_tracker.add_ping_event(
                        request_id, "TASK_CANCEL", 
                        f"Cancelling task: {task.get_name()}",
                        success=True
                    )
                    
                    task.cancel()
                    logger.debug(f"   • [CLEANUP] Task cancelled: {request_id}")
                else:
                    logger.debug(f"   • [CLEANUP] Task already done: {request_id}")
                
                del self.active_tasks[request_id]
                logger.debug(f"   • [CLEANUP] Task removed from active_tasks: {request_id}")
            
            # Remove WebSocket reference
            if request_id in self.websocket_refs:
                del self.websocket_refs[request_id]
                logger.debug(f"   • [CLEANUP] WebSocket reference removed: {request_id}")
            
            # Remove from active requests
            if request_id in active_requests:
                del active_requests[request_id]
                logger.debug(f"   • [CLEANUP] Removed from active_requests: {request_id}")
            
            # Remove counters and timers
            if request_id in self.ping_counters:
                final_count = self.ping_counters[request_id]
                del self.ping_counters[request_id]
                logger.debug(f"   • [CLEANUP] Removed ping counter: {final_count} pings")
            
            if request_id in self.start_times:
                start_time = self.start_times[request_id]
                duration = (get_utc8_time() - start_time).total_seconds()
                del self.start_times[request_id]
                logger.debug(f"   • [CLEANUP] Removed start time: duration was {duration:.1f}s")
            
            # Record cleanup completion
            cleanup_duration = (get_utc8_time() - cleanup_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "CLEANUP_COMPLETE", 
                f"Cleanup completed in {cleanup_duration:.1f}ms",
                success=True
            )
            
            logger.info(f"🧹 [CLEANUP] Cleaned up ping manager for {request_id}")
            logger.debug(f"   • Cleanup duration: {cleanup_duration:.1f}ms")
            
        except Exception as e:
            error_duration = (get_utc8_time() - cleanup_start).total_seconds() * 1000
            
            event_tracker.add_ping_event(
                request_id, "CLEANUP_ERROR", 
                f"Cleanup error after {error_duration:.1f}ms: {e}",
                success=False
            )
            
            logger.error(f"❌ [CLEANUP] Cleanup error for {request_id}: {e}")
            logger.debug(f"   • Duration before error: {error_duration:.1f}ms")
    
    def stop_all_pings(self):
        """Stop all ping tasks with detailed logging."""
        stop_start = get_utc8_time()
        
        event_tracker.add_system_event(
            "PING_STOP_ALL",
            f"Stopping all ping tasks ({len(self.active_tasks)} active)",
            {'active_count': len(self.active_tasks)}
        )
        
        logger.info(f"🛑 [STOP_ALL] Stopping all ping tasks ({len(self.active_tasks)} active)")
        
        stopped_count = 0
        for request_id, task in list(self.active_tasks.items()):
            try:
                if not task.done():
                    task.cancel()
                    stopped_count += 1
                    
                    event_tracker.add_ping_event(
                        request_id, "FORCE_STOPPED", 
                        "Task force-stopped by stop_all_pings()",
                        success=False
                    )
                    
                    logger.debug(f"   • [STOP_ALL] Stopped ping task for {request_id}")
            
            except Exception as e:
                logger.error(f"❌ [STOP_ALL] Error stopping task {request_id}: {e}")
        
        self.active_tasks.clear()
        self.websocket_refs.clear()
        self.ping_counters.clear()
        self.start_times.clear()
        
        stop_duration = (get_utc8_time() - stop_start).total_seconds() * 1000
        
        event_tracker.add_system_event(
            "PING_STOP_COMPLETE",
            f"All ping tasks stopped: {stopped_count} tasks in {stop_duration:.1f}ms",
            {'stopped_count': stopped_count, 'duration_ms': stop_duration},
            success=True
        )
        
        logger.info(f"✅ [STOP_ALL] All ping tasks stopped ({stopped_count} tasks in {stop_duration:.1f}ms)")

# Global ping manager
ping_manager = AsyncPingManager()

# ================= GEMINI-POWERED CLASSIFICATION WITH DETAILED LOGGING =================
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
        start_time = get_utc8_time()
        
        event_tracker.add_ai_event(
            "CLASSIFY_START",
            f"Starting classification for query: {query[:50]}...",
            model="gemini-2.5-flash-lite",
            success=True
        )
        
        logger.info(f"🎯 [CLASSIFY] Starting classification: '{query[:50]}...'")
        
        try:
            if not GEMINI_API_KEY:
                event_tracker.add_ai_event(
                    "CLASSIFY_ERROR",
                    "No Gemini API key configured",
                    success=False
                )
                logger.warning("⚠️ [CLASSIFY] No Gemini API key, using MEDIUM as default")
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
            
            event_tracker.add_ai_event(
                "CLASSIFY_REQUEST",
                f"Sending classification request to Gemini",
                model="gemini-2.5-flash-lite",
                success=True
            )
            
            logger.debug(f"📤 [CLASSIFY] Sending request to Gemini API")
            logger.debug(f"   • URL: {url}")
            logger.debug(f"   • Query length: {len(query)} chars")
            logger.debug(f"   • Prompt length: {len(classification_prompt)} chars")
            
            response_start = time.time()
            response = requests.post(url, headers=headers, json=data, params=params, timeout=3)
            response_time = (time.time() - response_start) * 1000
            
            logger.debug(f"📥 [CLASSIFY] Response received in {response_time:.1f}ms")
            logger.debug(f"   • Status code: {response.status_code}")
            logger.debug(f"   • Response length: {len(response.text)} chars")
            
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
                                total_time = (get_utc8_time() - start_time).total_seconds() * 1000
                                
                                event_tracker.add_ai_event(
                                    "CLASSIFY_SUCCESS",
                                    f"Classification complete: {tier}",
                                    tier=tier,
                                    model="gemini-2.5-flash-lite",
                                    success=True,
                                    duration_ms=total_time,
                                    data={
                                        'response_time_ms': response_time,
                                        'total_time_ms': total_time,
                                        'raw_classification': classification
                                    }
                                )
                                
                                logger.info(f"✅ [CLASSIFY] Classified as {tier}")
                                logger.debug(f"   • Raw response: {classification}")
                                logger.debug(f"   • Response time: {response_time:.1f}ms")
                                logger.debug(f"   • Total time: {total_time:.1f}ms")
                                
                                return tier
            
            # Default classification
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "CLASSIFY_DEFAULT",
                "No valid classification found, using MEDIUM as default",
                tier="MEDIUM",
                success=True
            )
            
            logger.warning(f"⚠️ [CLASSIFY] No valid classification found, using MEDIUM")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            return "MEDIUM"
            
        except requests.exceptions.Timeout:
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "CLASSIFY_TIMEOUT",
                "Classification timeout after 3 seconds",
                success=False,
                duration_ms=total_time
            )
            
            logger.warning(f"⏰ [CLASSIFY] Classification timeout, using MEDIUM")
            logger.debug(f"   • Timeout after: {total_time:.1f}ms")
            return "MEDIUM"
            
        except Exception as e:
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "CLASSIFY_ERROR",
                f"Classification error: {e}",
                success=False,
                duration_ms=total_time
            )
            
            logger.error(f"❌ [CLASSIFY] Classification error: {e}")
            logger.debug(f"   • Error time: {total_time:.1f}ms")
            logger.exception("Detailed error trace:")
            return "MEDIUM"

# ================= ASYNC GEMINI PROCESSOR WITH DETAILED LOGGING =================
class AsyncGeminiProcessor:
    """Async Gemini processor with detailed logging."""
    
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
        """Synchronous Gemini API call for thread pool with detailed logging."""
        call_start = get_utc8_time()
        call_id = f"call_{int(time.time()*1000)}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
        
        event_tracker.add_ai_event(
            "API_CALL_START",
            f"Starting Gemini API call: {model}",
            model=model,
            success=True,
            data={
                'call_id': call_id,
                'query_length': len(query),
                'max_tokens': max_tokens,
                'timeout': timeout
            }
        )
        
        logger.debug(f"📞 [GEMINI_CALL] Starting API call: {model}")
        logger.debug(f"   • Call ID: {call_id}")
        logger.debug(f"   • Query: '{query[:50]}...'")
        logger.debug(f"   • Max tokens: {max_tokens}")
        logger.debug(f"   • Timeout: {timeout}s")
        
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
            
            logger.debug(f"📤 [GEMINI_CALL] Sending request to {url}")
            
            request_start = time.time()
            response = requests.post(url, headers=headers, json=data, params=params, timeout=timeout)
            request_time = (time.time() - request_start) * 1000
            
            logger.debug(f"📥 [GEMINI_CALL] Response received in {request_time:.1f}ms")
            logger.debug(f"   • Status code: {response.status_code}")
            logger.debug(f"   • Response size: {len(response.text)} chars")
            
            if response.status_code == 200:
                result = response.json()
                
                if "candidates" in result and result["candidates"]:
                    candidate = result["candidates"][0]
                    if "content" in candidate:
                        parts = candidate["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            text = parts[0]["text"]
                            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
                            
                            event_tracker.add_ai_event(
                                "API_CALL_SUCCESS",
                                f"API call successful: {model}",
                                model=model,
                                success=True,
                                duration_ms=total_time,
                                data={
                                    'response_time_ms': request_time,
                                    'total_time_ms': total_time,
                                    'response_length': len(text),
                                    'call_id': call_id
                                }
                            )
                            
                            logger.info(f"✅ [GEMINI_CALL] {model} succeeded")
                            logger.debug(f"   • Response length: {len(text)} chars")
                            logger.debug(f"   • Request time: {request_time:.1f}ms")
                            logger.debug(f"   • Total time: {total_time:.1f}ms")
                            
                            return text, True
            
            # Non-200 response or no valid result
            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "API_CALL_FAILED",
                f"API call failed: {model} (status {response.status_code})",
                model=model,
                success=False,
                duration_ms=total_time,
                data={
                    'status_code': response.status_code,
                    'response_time_ms': request_time,
                    'call_id': call_id
                }
            )
            
            logger.warning(f"⚠️ [GEMINI_CALL] {model} failed with status {response.status_code}")
            logger.debug(f"   • Request time: {request_time:.1f}ms")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            
            return None, False
            
        except requests.exceptions.Timeout:
            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "API_CALL_TIMEOUT",
                f"API call timeout: {model} after {timeout}s",
                model=model,
                success=False,
                duration_ms=total_time,
                data={
                    'timeout_seconds': timeout,
                    'call_id': call_id
                }
            )
            
            logger.warning(f"⏰ [GEMINI_CALL] {model} timeout after {timeout}s")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            return None, False
            
        except Exception as e:
            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "API_CALL_ERROR",
                f"API call error: {model} - {str(e)[:100]}",
                model=model,
                success=False,
                duration_ms=total_time,
                data={
                    'error': str(e)[:100],
                    'call_id': call_id
                }
            )
            
            logger.error(f"❌ [GEMINI_CALL] {model} error: {str(e)[:50]}")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            logger.exception("Detailed error trace:")
            return None, False
    
    @staticmethod
    async def process_query_async(query: str, tier: str, cache_key: str, websocket, request_id: str):
        """Process query with async pings and detailed logging."""
        process_start = get_utc8_time()
        
        event_tracker.add_ai_event(
            "PROCESS_START",
            f"Starting AI processing: {tier} tier",
            request_id=request_id,
            tier=tier,
            success=True,
            data={
                'query': query[:100],
                'cache_key': cache_key,
                'query_length': len(query)
            }
        )
        
        logger.info(f"🚀 [AI_PROCESS] Processing {tier} tier: '{query[:40]}...' [ID: {request_id}]")
        logger.debug(f"   • Request ID: {request_id}")
        logger.debug(f"   • Tier: {tier}")
        logger.debug(f"   • Query: '{query[:100]}...'")
        logger.debug(f"   • Query length: {len(query)} chars")
        logger.debug(f"   • Cache key: {cache_key}")
        logger.debug(f"   • WebSocket ID: {id(websocket)}")
        logger.debug(f"   • WebSocket open: {websocket.open}")
        
        try:
            # Mark request as active
            active_requests[request_id] = True
            
            event_tracker.add_ai_event(
                "REQUEST_ACTIVE",
                f"Request marked as active",
                request_id=request_id,
                tier=tier,
                success=True,
                data={'active_requests_count': len(active_requests)}
            )
            
            logger.debug(f"📝 [AI_PROCESS] Marked {request_id} as active")
            logger.debug(f"   • Active requests: {list(active_requests.keys())}")
            
            # START ASYNC PINGING IMMEDIATELY
            logger.debug(f"🎯 [AI_PROCESS] Starting ping manager for {request_id}")
            
            event_tracker.add_ai_event(
                "PING_START",
                f"Starting async ping manager",
                request_id=request_id,
                tier=tier,
                success=True
            )
            
            ping_task = await ping_manager.start_pinging(websocket, request_id, 25)
            
            if ping_task:
                event_tracker.add_ai_event(
                    "PING_STARTED",
                    f"Ping task started successfully",
                    request_id=request_id,
                    tier=tier,
                    success=True,
                    data={
                        'task_name': ping_task.get_name(),
                        'task_done': ping_task.done()
                    }
                )
                
                logger.info(f"✅ [AI_PROCESS] Ping task started for {request_id}")
                logger.debug(f"   • Task name: {ping_task.get_name()}")
                logger.debug(f"   • Task state: {'running' if not ping_task.done() else 'done'}")
            else:
                event_tracker.add_ai_event(
                    "PING_FAILED",
                    "Failed to start ping task",
                    request_id=request_id,
                    tier=tier,
                    success=False
                )
                
                logger.error("❌ [AI_PROCESS] Failed to start pinging!")
            
            # Check cache first
            if cache_key in gemini_cache:
                cached_time, response = gemini_cache[cache_key]
                cache_age = (get_utc8_time() - cached_time).total_seconds()
                
                if cache_age < CACHE_DURATION:
                    event_tracker.add_ai_event(
                        "CACHE_HIT",
                        f"Cache hit: age={cache_age:.1f}s",
                        request_id=request_id,
                        tier=tier,
                        success=True,
                        data={
                            'cache_age_seconds': cache_age,
                            'cache_duration': CACHE_DURATION,
                            'response_length': len(response)
                        }
                    )
                    
                    logger.info(f"⚡ [AI_PROCESS] Cache hit for {request_id}")
                    logger.debug(f"   • Cache age: {cache_age:.1f}s")
                    logger.debug(f"   • Response length: {len(response)} chars")
                    
                    # Mark as inactive since we're returning cached response
                    active_requests[request_id] = False
                    
                    process_time = (get_utc8_time() - process_start).total_seconds() * 1000
                    
                    event_tracker.add_ai_event(
                        "PROCESS_COMPLETE",
                        f"Process completed with cache hit",
                        request_id=request_id,
                        tier=tier,
                        success=True,
                        duration_ms=process_time
                    )
                    
                    return f"[{tier} - Cached] {response}"
            
            # Get model configuration
            config = AsyncGeminiProcessor.get_model_config(tier)
            models = config["models"][:PARALLEL_MODEL_TRIES]
            max_tokens = config["tokens"]
            timeouts = config["timeouts"][:PARALLEL_MODEL_TRIES]
            
            event_tracker.add_ai_event(
                "MODEL_CONFIG",
                f"Model configuration loaded",
                request_id=request_id,
                tier=tier,
                success=True,
                data={
                    'models': models,
                    'max_tokens': max_tokens,
                    'timeouts': timeouts,
                    'parallel_tries': PARALLEL_MODEL_TRIES
                }
            )
            
            logger.debug(f"🤖 [AI_PROCESS] Models to try for {request_id}: {models}")
            logger.debug(f"   • Max tokens: {max_tokens}")
            logger.debug(f"   • Timeouts: {timeouts}")
            logger.debug(f"   • Parallel tries: {PARALLEL_MODEL_TRIES}")
            
            # Submit models in parallel using ThreadPoolExecutor
            futures = []
            with ThreadPoolExecutor(max_workers=PARALLEL_MODEL_TRIES) as model_executor:
                for i, model in enumerate(models):
                    timeout = timeouts[i] if i < len(timeouts) else 10
                    
                    event_tracker.add_ai_event(
                        "MODEL_SUBMIT",
                        f"Submitting model: {model}",
                        request_id=request_id,
                        tier=tier,
                        model=model,
                        success=True,
                        data={'timeout': timeout}
                    )
                    
                    logger.debug(f"   • [AI_PROCESS] Submitting {model} with timeout {timeout}s")
                    future = model_executor.submit(
                        AsyncGeminiProcessor.call_gemini_sync,
                        query, model, max_tokens, timeout
                    )
                    futures.append((model, future))
                
                # Wait for first successful response
                for model, future in futures:
                    try:
                        event_tracker.add_ai_event(
                            "MODEL_WAIT",
                            f"Waiting for model result: {model}",
                            request_id=request_id,
                            tier=tier,
                            model=model,
                            success=True
                        )
                        
                        logger.debug(f"   • [AI_PROCESS] Waiting for {model} result...")
                        result, success = future.result(timeout=15)
                        
                        if success and result:
                            event_tracker.add_ai_event(
                                "MODEL_SUCCESS",
                                f"Model succeeded: {model}",
                                request_id=request_id,
                                tier=tier,
                                model=model,
                                success=True,
                                data={'response_length': len(result)}
                            )
                            
                            logger.info(f"✅ [AI_PROCESS] {model} succeeded for {request_id}!")
                            logger.debug(f"   • Response length: {len(result)} chars")
                            
                            # Cache the successful response
                            gemini_cache[cache_key] = (get_utc8_time(), result)
                            
                            event_tracker.add_ai_event(
                                "RESPONSE_CACHED",
                                f"Response cached with key: {cache_key}",
                                request_id=request_id,
                                tier=tier,
                                model=model,
                                success=True,
                                data={
                                    'cache_size': len(gemini_cache),
                                    'response_length': len(result)
                                }
                            )
                            
                            # Mark as inactive before returning
                            active_requests[request_id] = False
                            
                            total_time = (get_utc8_time() - process_start).total_seconds() * 1000
                            
                            event_tracker.add_ai_event(
                                "PROCESS_COMPLETE",
                                f"Process completed successfully with {model}",
                                request_id=request_id,
                                tier=tier,
                                model=model,
                                success=True,
                                duration_ms=total_time
                            )
                            
                            logger.info(f"✅ [AI_PROCESS] Process completed for {request_id} in {total_time:.1f}ms")
                            logger.debug(f"   • Total time: {total_time:.1f}ms")
                            logger.debug(f"   • Cache size: {len(gemini_cache)}")
                            
                            return f"[{tier} - {model}] {result}"
                        else:
                            event_tracker.add_ai_event(
                                "MODEL_FAILED",
                                f"Model failed or no result: {model}",
                                request_id=request_id,
                                tier=tier,
                                model=model,
                                success=False
                            )
                            
                            logger.debug(f"   • [AI_PROCESS] {model} failed or no result")
                            
                    except Exception as e:
                        event_tracker.add_ai_event(
                            "MODEL_ERROR",
                            f"Model error: {model} - {str(e)[:50]}",
                            request_id=request_id,
                            tier=tier,
                            model=model,
                            success=False
                        )
                        
                        logger.warning(f"⚠️ [AI_PROCESS] {model} failed for {request_id}: {e}")
            
            # Fallback sequential
            event_tracker.add_ai_event(
                "FALLBACK_START",
                f"Parallel failed, starting sequential fallback",
                request_id=request_id,
                tier=tier,
                success=False
            )
            
            logger.warning(f"⚠️ [AI_PROCESS] Parallel failed for {request_id}, trying sequential")
            
            for model in models:
                event_tracker.add_ai_event(
                    "SEQUENTIAL_TRY",
                    f"Trying model sequentially: {model}",
                    request_id=request_id,
                    tier=tier,
                    model=model,
                    success=True
                )
                
                logger.debug(f"   • [AI_PROCESS] Trying {model} sequentially...")
                result, success = AsyncGeminiProcessor.call_gemini_sync(
                    query, model, max_tokens, 10
                )
                
                if success and result:
                    event_tracker.add_ai_event(
                        "SEQUENTIAL_SUCCESS",
                        f"Sequential model succeeded: {model}",
                        request_id=request_id,
                        tier=tier,
                        model=model,
                        success=True,
                        data={'response_length': len(result)}
                    )
                    
                    logger.info(f"✅ [AI_PROCESS] {model} succeeded (sequential) for {request_id}!")
                    
                    gemini_cache[cache_key] = (get_utc8_time(), result)
                    
                    # Mark as inactive before returning
                    active_requests[request_id] = False
                    
                    total_time = (get_utc8_time() - process_start).total_seconds() * 1000
                    
                    event_tracker.add_ai_event(
                        "PROCESS_COMPLETE",
                        f"Process completed with sequential fallback",
                        request_id=request_id,
                        tier=tier,
                        model=model,
                        success=True,
                        duration_ms=total_time
                    )
                    
                    return f"[{tier} - {model}*] {result}"
            
            # All models failed
            event_tracker.add_ai_event(
                "ALL_MODELS_FAILED",
                f"All models failed for request",
                request_id=request_id,
                tier=tier,
                success=False
            )
            
            logger.error(f"❌ [AI_PROCESS] All models failed for {request_id}")
            
            # Mark as inactive before returning
            active_requests[request_id] = False
            
            total_time = (get_utc8_time() - process_start).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "PROCESS_FAILED",
                f"Process failed: all models unavailable",
                request_id=request_id,
                tier=tier,
                success=False,
                duration_ms=total_time
            )
            
            return "Sorry, I couldn't generate a response. Please try again."
            
        except Exception as e:
            total_time = (get_utc8_time() - process_start).total_seconds() * 1000
            
            event_tracker.add_ai_event(
                "PROCESS_ERROR",
                f"Process error: {str(e)[:100]}",
                request_id=request_id,
                tier=tier,
                success=False,
                duration_ms=total_time
            )
            
            logger.error(f"❌ [AI_PROCESS] Processing error for {request_id}: {e}")
            logger.debug(f"   • Error time: {total_time:.1f}ms")
            logger.exception("Detailed error trace:")
            
            # Mark as inactive on error
            active_requests[request_id] = False
            
            return f"Error: {str(e)[:50]}"
        finally:
            # Double-check cleanup
            if request_id in active_requests:
                active_requests[request_id] = False
                
                event_tracker.add_ai_event(
                    "FINAL_CLEANUP",
                    f"Final cleanup: marked request as inactive",
                    request_id=request_id,
                    tier=tier,
                    success=True
                )
                
                logger.debug(f"🔒 [AI_PROCESS] Final cleanup: marked {request_id} as inactive")

# ================= FAST TOOLS WITH DETAILED LOGGING =================
def google_search_fast(query: str, max_results: int = 5) -> str:
    """Fast Google search with detailed logging."""
    start_time = get_utc8_time()
    
    event_tracker.add_system_event(
        "GOOGLE_SEARCH_START",
        f"Starting Google search: '{query[:50]}...'",
        data={
            'query': query[:100],
            'max_results': max_results,
            'query_length': len(query)
        }
    )
    
    logger.info(f"🔍 [GOOGLE] Starting search: '{query[:50]}...'")
    
    try:
        if not GOOGLE_API_KEY or not CSE_ID:
            event_tracker.add_system_event(
                "GOOGLE_CONFIG_ERROR",
                "Google Search not configured",
                success=False
            )
            return "Google Search not configured."
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": CSE_ID,
            "q": query,
            "num": max_results,
            "safe": "active"
        }
        
        logger.debug(f"📤 [GOOGLE] Sending request to Google API")
        logger.debug(f"   • URL: {url}")
        logger.debug(f"   • Query: '{query}'")
        logger.debug(f"   • Max results: {max_results}")
        
        request_start = time.time()
        response = requests.get(url, params=params, timeout=8)
        request_time = (time.time() - request_start) * 1000
        
        logger.debug(f"📥 [GOOGLE] Response received in {request_time:.1f}ms")
        logger.debug(f"   • Status code: {response.status_code}")
        
        data = response.json()
        
        if "items" not in data:
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "GOOGLE_NO_RESULTS",
                f"No search results found",
                success=False,
                duration_ms=total_time,
                data={'request_time_ms': request_time}
            )
            
            logger.info(f"⚠️ [GOOGLE] No results found for: '{query[:50]}...'")
            logger.debug(f"   • Request time: {request_time:.1f}ms")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            
            return "No results found."
        
        items = data["items"][:max_results]
        results = []
        
        for i, item in enumerate(items, 1):
            title = item.get('title', 'No title')
            link = item.get('link', 'No link')
            snippet = item.get('snippet', 'No description')[:150]
            results.append(f"{i}. **{title}**\n🔗 {link}\n📝 {snippet}")
        
        result_text = "\n\n".join(results) if results else "No results found."
        total_time = (get_utc8_time() - start_time).total_seconds() * 1000
        
        event_tracker.add_system_event(
            "GOOGLE_SUCCESS",
            f"Google search successful: found {len(items)} results",
            success=True,
            duration_ms=total_time,
            data={
                'results_count': len(items),
                'request_time_ms': request_time,
                'total_time_ms': total_time
            }
        )
        
        logger.info(f"✅ [GOOGLE] Search successful: {len(items)} results")
        logger.debug(f"   • Request time: {request_time:.1f}ms")
        logger.debug(f"   • Total time: {total_time:.1f}ms")
        
        return result_text
        
    except Exception as e:
        total_time = (get_utc8_time() - start_time).total_seconds() * 1000
        
        event_tracker.add_system_event(
            "GOOGLE_ERROR",
            f"Google search error: {str(e)[:100]}",
            success=False,
            duration_ms=total_time
        )
        
        logger.error(f"❌ [GOOGLE] Google search error: {e}")
        logger.debug(f"   • Error time: {total_time:.1f}ms")
        logger.exception("Detailed error trace:")
        
        return "Search error."

def wikipedia_search_fast(query: str, max_results: int = 2) -> str:
    """Fast Wikipedia search with detailed logging."""
    start_time = get_utc8_time()
    
    event_tracker.add_system_event(
        "WIKIPEDIA_START",
        f"Starting Wikipedia search: '{query[:50]}...'",
        data={
            'query': query[:100],
            'max_results': max_results
        }
    )
    
    logger.info(f"📚 [WIKIPEDIA] Starting search: '{query[:50]}...'")
    
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
        
        logger.debug(f"📤 [WIKIPEDIA] Sending search request")
        logger.debug(f"   • URL: {url}")
        logger.debug(f"   • Query: '{query}'")
        
        request_start = time.time()
        response = requests.get(url, params=params, timeout=8)
        request_time = (time.time() - request_start) * 1000
        
        logger.debug(f"📥 [WIKIPEDIA] Search response in {request_time:.1f}ms")
        logger.debug(f"   • Status code: {response.status_code}")
        
        data = response.json()
        
        if "query" not in data:
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "WIKIPEDIA_NO_QUERY",
                "No query in Wikipedia response",
                success=False,
                duration_ms=total_time
            )
            
            logger.warning(f"⚠️ [WIKIPEDIA] No query in response")
            return "No articles found."
        
        search_results = data["query"].get("search", [])
        if not search_results:
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "WIKIPEDIA_NO_RESULTS",
                "No search results found",
                success=False,
                duration_ms=total_time
            )
            
            logger.info(f"⚠️ [WIKIPEDIA] No articles found for: '{query[:50]}...'")
            return "No articles found."
        
        first = search_results[0]
        title = first.get("title", "Unknown")
        
        logger.debug(f"🔍 [WIKIPEDIA] Found article: {title}")
        
        extract_params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "exchars": 200
        }
        
        extract_start = time.time()
        extract_response = requests.get(url, params=extract_params, timeout=8)
        extract_time = (time.time() - extract_start) * 1000
        
        logger.debug(f"📥 [WIKIPEDIA] Extract response in {extract_time:.1f}ms")
        
        extract_data = extract_response.json()
        
        pages = extract_data.get("query", {}).get("pages", {})
        for page in pages.values():
            extract = page.get("extract", "No summary.")
            
            total_time = (get_utc8_time() - start_time).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "WIKIPEDIA_SUCCESS",
                f"Wikipedia search successful: found '{title}'",
                success=True,
                duration_ms=total_time,
                data={
                    'article_title': title,
                    'extract_length': len(extract),
                    'search_time_ms': request_time,
                    'extract_time_ms': extract_time,
                    'total_time_ms': total_time
                }
            )
            
            logger.info(f"✅ [WIKIPEDIA] Search successful: '{title}'")
            logger.debug(f"   • Search time: {request_time:.1f}ms")
            logger.debug(f"   • Extract time: {extract_time:.1f}ms")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            
            return f"**{title}**\n📖 {extract[:200]}..."
        
        total_time = (get_utc8_time() - start_time).total_seconds() * 1000
        
        event_tracker.add_system_event(
            "WIKIPEDIA_NO_CONTENT",
            "No content retrieved from Wikipedia",
            success=False,
            duration_ms=total_time
        )
        
        return "No content retrieved."
        
    except Exception as e:
        total_time = (get_utc8_time() - start_time).total_seconds() * 1000
        
        event_tracker.add_system_event(
            "WIKIPEDIA_ERROR",
            f"Wikipedia search error: {str(e)[:100]}",
            success=False,
            duration_ms=total_time
        )
        
        logger.error(f"❌ [WIKIPEDIA] Wikipedia search error: {e}")
        logger.debug(f"   • Error time: {total_time:.1f}ms")
        logger.exception("Detailed error trace:")
        
        return "Search error."

# ================= ASYNC MCP HANDLER WITH DETAILED LOGGING =================
class AsyncMCPHandler:
    """Async MCP protocol handler with detailed logging."""
    
    @staticmethod
    def handle_initialize(message_id):
        event_tracker.add_system_event(
            "MCP_INITIALIZE",
            f"Handling MCP initialize request",
            data={'message_id': message_id}
        )
        
        logger.info(f"🔄 [MCP] Handling initialize request: {message_id}")
        
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "async-ping-mcp",
                    "version": "3.6.2-UTC8-DEBUG"
                }
            }
        }
    
    @staticmethod
    def handle_tools_list(message_id):
        event_tracker.add_system_event(
            "MCP_TOOLS_LIST",
            f"Handling MCP tools/list request",
            data={'message_id': message_id}
        )
        
        logger.info(f"🔄 [MCP] Handling tools/list request: {message_id}")
        
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
        event_tracker.add_system_event(
            "MCP_PING",
            f"Handling MCP ping request",
            data={'message_id': message_id}
        )
        
        logger.debug(f"🔄 [MCP] Handling ping request: {message_id}")
        
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    
    @staticmethod
    async def handle_tools_call_async(message_id, params, websocket):
        """Async tools call with INSTANT pinging and detailed logging."""
        call_start = get_utc8_time()
        tool_name = params.get("name", "")
        query = params.get("arguments", {}).get("query", "").strip()
        request_id = str(uuid.uuid4())[:8]
        
        event_tracker.add_system_event(
            "MCP_TOOLS_CALL_START",
            f"Starting MCP tools/call: {tool_name}",
            data={
                'message_id': message_id,
                'tool_name': tool_name,
                'request_id': request_id,
                'query_length': len(query),
                'query_preview': query[:50]
            }
        )
        
        logger.info(f"🔄 [MCP] Processing {tool_name}: '{query[:30]}...' [ID: {request_id}]")
        logger.debug(f"   • Message ID: {message_id}")
        logger.debug(f"   • Request ID: {request_id}")
        logger.debug(f"   • Tool: {tool_name}")
        logger.debug(f"   • Query: '{query[:100]}...'")
        logger.debug(f"   • Query length: {len(query)} chars")
        logger.debug(f"   • WebSocket ID: {id(websocket)}")
        logger.debug(f"   • WebSocket open: {websocket.open}")
        logger.debug(f"   • Params keys: {list(params.keys())}")
        
        if not query:
            event_tracker.add_system_event(
                "MCP_TOOLS_CALL_ERROR",
                "Missing query in tools/call request",
                data={'message_id': message_id, 'tool_name': tool_name},
                success=False
            )
            
            logger.error(f"❌ [MCP] Missing query for {tool_name}")
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32602, "message": "Missing query"}
            }
        
        try:
            result = ""
            
            if tool_name == "google_search":
                event_tracker.add_system_event(
                    "GOOGLE_CALL",
                    f"Calling Google search",
                    request_id=request_id,
                    data={'query': query[:100]}
                )
                
                logger.debug(f"   • [MCP] Using Google Search")
                result = google_search_fast(query)
                
            elif tool_name == "wikipedia_search":
                event_tracker.add_system_event(
                    "WIKIPEDIA_CALL",
                    f"Calling Wikipedia search",
                    request_id=request_id,
                    data={'query': query[:100]}
                )
                
                logger.debug(f"   • [MCP] Using Wikipedia Search")
                result = wikipedia_search_fast(query)
                
            elif tool_name == "ask_ai":
                event_tracker.add_system_event(
                    "AI_CALL",
                    f"Calling AI with async pings",
                    request_id=request_id,
                    data={'query': query[:100]}
                )
                
                logger.debug(f"   • [MCP] Using AI with async pings")
                
                # Get classification
                logger.debug(f"   • [MCP] Classifying query...")
                tier = SmartModelSelector.classify_query_with_gemini(query)
                cache_key = hashlib.md5(f"{query}_{tier}".encode()).hexdigest()
                
                logger.debug(f"   • [MCP] Classification: {tier}")
                logger.debug(f"   • [MCP] Cache key: {cache_key}")
                
                # Process with async pinging
                result = await AsyncGeminiProcessor.process_query_async(
                    query, tier, cache_key, websocket, request_id
                )
                
            else:
                event_tracker.add_system_event(
                    "UNKNOWN_TOOL",
                    f"Unknown tool requested: {tool_name}",
                    request_id=request_id,
                    success=False
                )
                
                result = f"Unknown tool: {tool_name}"
            
            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "MCP_TOOLS_CALL_COMPLETE",
                f"MCP tools/call completed: {tool_name}",
                request_id=request_id,
                success=True,
                duration_ms=total_time,
                data={
                    'tool_name': tool_name,
                    'result_length': len(result),
                    'total_time_ms': total_time
                }
            )
            
            logger.info(f"✅ [MCP] Completed {tool_name} [ID: {request_id}]")
            logger.debug(f"   • Result length: {len(result)} chars")
            logger.debug(f"   • Total time: {total_time:.1f}ms")
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
            
        except Exception as e:
            total_time = (get_utc8_time() - call_start).total_seconds() * 1000
            
            event_tracker.add_system_event(
                "MCP_TOOLS_CALL_ERROR",
                f"Tool error: {str(e)[:100]}",
                request_id=request_id,
                success=False,
                duration_ms=total_time
            )
            
            logger.error(f"❌ [MCP] Tool error [ID: {request_id}]: {e}")
            logger.debug(f"   • Error time: {total_time:.1f}ms")
            logger.exception("Detailed error trace:")
            
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
            logger.debug(f"🏁 [MCP] Final state for {request_id}:")
            logger.debug(f"   • In active_requests: {request_id in active_requests}")
            logger.debug(f"   • Active value: {active_requests.get(request_id, 'NOT_FOUND')}")
            logger.debug(f"   • Ping tasks active: {request_id in ping_manager.active_tasks}")
            
            event_tracker.add_system_event(
                "MCP_FINAL_STATE",
                f"Final state for request {request_id}",
                request_id=request_id,
                success=True,
                data={
                    'in_active_requests': request_id in active_requests,
                    'active_value': active_requests.get(request_id, 'NOT_FOUND'),
                    'in_ping_tasks': request_id in ping_manager.active_tasks
                }
            )

# ================= ENHANCED WEB SERVER WITH UNLIMITED LOGS =================
app = Flask(__name__)
server_start_time = get_utc8_time()

test_queries = [
    "Explain quantum computing in simple terms",
    "What is the capital of France?",
    "Write a Python function to calculate Fibonacci",
    "Compare machine learning and deep learning",
    "How to make a cup of tea"
]

@app.route('/')
def index():
    uptime = (get_utc8_time() - server_start_time).total_seconds()
    hours, remainder = divmod(int(uptime), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Get all logs and events
    all_logs = unlimited_handler.get_all_logs()
    all_events = event_tracker.get_all_events()
    recent_events = event_tracker.get_recent_events(50)
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Xiaozhi MCP - UNLIMITED LOGS (UTC+8)</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root {
                --primary: #4285F4;
                --success: #34A853;
                --warning: #FBBC05;
                --danger: #F44336;
                --dark: #202124;
                --light: #f8f9fa;
            }
            
            body { 
                font-family: 'Monaco', 'Menlo', monospace;
                margin: 0;
                padding: 10px;
                background: #1a1a1a;
                color: #fff;
                font-size: 12px;
            }
            
            .header {
                background: #2a2a2a;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 15px;
                border-left: 4px solid var(--primary);
            }
            
            .stats-bar {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 10px;
                margin: 10px 0;
            }
            
            .stat-item {
                background: #333;
                padding: 8px;
                border-radius: 4px;
                text-align: center;
            }
            
            .stat-value {
                font-size: 18px;
                font-weight: bold;
                color: var(--primary);
            }
            
            .stat-label {
                font-size: 10px;
                color: #aaa;
                text-transform: uppercase;
            }
            
            .log-container {
                background: #000;
                border-radius: 8px;
                overflow: hidden;
                margin-bottom: 15px;
            }
            
            .log-header {
                background: #333;
                padding: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            
            .log-content {
                height: 500px;
                overflow-y: auto;
                padding: 10px;
                font-family: 'Monaco', 'Menlo', monospace;
                font-size: 11px;
                line-height: 1.4;
            }
            
            .log-line {
                padding: 2px 0;
                border-bottom: 1px solid #222;
                white-space: pre-wrap;
                word-break: break-all;
            }
            
            .log-time {
                color: #00ff00;
                margin-right: 10px;
            }
            
            .log-level-INFO { color: #4CAF50; }
            .log-level-DEBUG { color: #2196F3; }
            .log-level-WARNING { color: #FF9800; }
            .log-level-ERROR { color: #F44336; }
            
            .controls {
                display: flex;
                gap: 10px;
                margin: 15px 0;
                flex-wrap: wrap;
            }
            
            .control-btn {
                padding: 8px 15px;
                background: #444;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 11px;
            }
            
            .control-btn:hover {
                background: #555;
            }
            
            .control-btn-primary {
                background: var(--primary);
            }
            
            .control-btn-danger {
                background: var(--danger);
            }
            
            .control-btn-warning {
                background: var(--warning);
            }
            
            .search-box {
                padding: 8px;
                background: #333;
                border: 1px solid #555;
                border-radius: 4px;
                color: white;
                width: 300px;
                font-size: 11px;
            }
            
            .filter-badges {
                display: flex;
                gap: 5px;
                margin: 10px 0;
                flex-wrap: wrap;
            }
            
            .filter-badge {
                padding: 4px 8px;
                background: #444;
                border-radius: 12px;
                font-size: 10px;
                cursor: pointer;
            }
            
            .filter-badge.active {
                background: var(--primary);
            }
            
            .event-log {
                background: #111;
                border-radius: 4px;
                padding: 10px;
                margin: 10px 0;
            }
            
            .event-item {
                padding: 5px;
                margin: 3px 0;
                background: #222;
                border-left: 3px solid #444;
                border-radius: 2px;
            }
            
            .event-success {
                border-left-color: var(--success);
            }
            
            .event-error {
                border-left-color: var(--danger);
            }
            
            .event-time {
                color: #00ff00;
                font-size: 10px;
                margin-right: 10px;
            }
            
            .event-type {
                color: #aaa;
                font-size: 10px;
                margin-right: 10px;
            }
            
            .auto-refresh {
                display: flex;
                align-items: center;
                gap: 10px;
                margin: 10px 0;
            }
            
            .auto-refresh-status {
                color: #00ff00;
                font-weight: bold;
            }
            
            .timestamp {
                color: #666;
                font-size: 10px;
                margin-top: 10px;
                text-align: center;
            }
            
            .scroll-info {
                position: fixed;
                bottom: 10px;
                right: 10px;
                background: rgba(0,0,0,0.8);
                padding: 5px 10px;
                border-radius: 4px;
                font-size: 10px;
                color: #aaa;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1 style="margin:0;color:#fff;font-size:18px;">🚀 Xiaozhi MCP v3.6.2 - UNLIMITED LOGS (UTC+8)</h1>
            <div style="color:#666;margin:5px 0;font-size:11px;">
                Server started: {{server_start_time}} | Current: {{current_time}} | Uptime: {{hours}}h {{minutes}}m {{seconds}}s
            </div>
            
            <div class="stats-bar">
                <div class="stat-item">
                    <div class="stat-value">{{log_count}}</div>
                    <div class="stat-label">Total Logs</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{{event_count}}</div>
                    <div class="stat-label">Total Events</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{{active_requests_count}}</div>
                    <div class="stat-label">Active Requests</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{{cache_size}}</div>
                    <div class="stat-label">Cache Size</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{{ping_tasks_count}}</div>
                    <div class="stat-label">Ping Tasks</div>
                </div>
            </div>
        </div>
        
        <div class="controls">
            <input type="text" id="searchBox" class="search-box" placeholder="Search logs... (regex supported)">
            <button class="control-btn" onclick="filterLogs()">🔍 Search</button>
            <button class="control-btn" onclick="clearSearch()">❌ Clear</button>
            <button class="control-btn control-btn-primary" onclick="refreshLogs()">🔄 Refresh Logs</button>
            <button class="control-btn control-btn-warning" onclick="refreshEvents()">🔄 Refresh Events</button>
            <button class="control-btn control-btn-danger" onclick="clearAllLogs()">🗑️ Clear All Logs</button>
            <button class="control-btn" onclick="exportLogs()">📥 Export Logs</button>
            <button class="control-btn" onclick="toggleAutoRefresh()">Auto: <span id="autoRefreshStatus">OFF</span></button>
        </div>
        
        <div class="filter-badges">
            <span class="filter-badge active" onclick="setFilter('ALL')">ALL</span>
            <span class="filter-badge" onclick="setFilter('INFO')">INFO</span>
            <span class="filter-badge" onclick="setFilter('DEBUG')">DEBUG</span>
            <span class="filter-badge" onclick="setFilter('WARNING')">WARNING</span>
            <span class="filter-badge" onclick="setFilter('ERROR')">ERROR</span>
            <span class="filter-badge" onclick="setFilter('PING')">PING</span>
            <span class="filter-badge" onclick="setFilter('WS')">WEBSOCKET</span>
            <span class="filter-badge" onclick="setFilter('AI')">AI</span>
            <span class="filter-badge" onclick="setFilter('MCP')">MCP</span>
        </div>
        
        <div class="log-container">
            <div class="log-header">
                <div>
                    <strong>📝 SYSTEM LOGS ({{log_count}} total)</strong>
                    <span style="color:#666;margin-left:10px;font-size:10px;">Showing {{logs_to_show}} lines</span>
                </div>
                <div>
                    <span style="color:#666;font-size:10px;">Updated: {{last_update}}</span>
                </div>
            </div>
            <div class="log-content" id="logContent">
                {% for log in logs %}
                <div class="log-line">
                    <span class="log-time">{{log.formatted_time}}</span>
                    <span class="log-level-{{log.level}}">[{{log.level}}]</span>
                    <span>{{log.message}}</span>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="log-container">
            <div class="log-header">
                <div>
                    <strong>📊 RECENT EVENTS ({{event_count}} total)</strong>
                    <span style="color:#666;margin-left:10px;font-size:10px;">Showing {{events_to_show}} events</span>
                </div>
                <div>
                    <button class="control-btn" style="padding:3px 8px;font-size:10px;" onclick="toggleEvents()">Toggle View</button>
                </div>
            </div>
            <div class="log-content" id="eventContent">
                {% for event in recent_events %}
                <div class="event-item {% if event.success %}event-success{% else %}event-error{% endif %}">
                    <div>
                        <span class="event-time">{{event.time_short}}</span>
                        <span class="event-type">{{event.type}}</span>
                        <strong>{{event.source}}</strong>: {{event.details}}
                    </div>
                    {% if event.data %}
                    <div style="color:#666;font-size:9px;margin-top:2px;">
                        {% for key, value in event.data.items() %}
                        {{key}}: {{value}}{% if not loop.last %} | {% endif %}
                        {% endfor %}
                    </div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="timestamp">
            UTC+8 Timezone | Page generated: {{current_time}} | Server uptime: {{hours}}h {{minutes}}m {{seconds}}s
        </div>
        
        <div class="scroll-info" id="scrollInfo">
            Auto-scroll: ON
        </div>
        
        <script>
            let autoRefreshInterval = null;
            let currentFilter = 'ALL';
            let autoScroll = true;
            let logsExpanded = false;
            let eventsExpanded = false;
            
            function refreshLogs() {
                fetch('/api/logs/all')
                    .then(r => r.json())
                    .then(logs => {
                        const container = document.getElementById('logContent');
                        const searchTerm = document.getElementById('searchBox').value;
                        const filter = currentFilter;
                        
                        let filteredLogs = logs;
                        
                        // Apply search filter
                        if (searchTerm) {
                            try {
                                const regex = new RegExp(searchTerm, 'i');
                                filteredLogs = logs.filter(log => regex.test(log.message));
                            } catch (e) {
                                // If regex fails, do simple search
                                filteredLogs = logs.filter(log => 
                                    log.message.toLowerCase().includes(searchTerm.toLowerCase())
                                );
                            }
                        }
                        
                        // Apply level filter
                        if (filter !== 'ALL') {
                            if (filter === 'PING') {
                                filteredLogs = filteredLogs.filter(log => log.message.includes('[PING_'));
                            } else if (filter === 'WS') {
                                filteredLogs = filteredLogs.filter(log => log.message.includes('[WS_') || log.message.includes('WebSocket'));
                            } else if (filter === 'AI') {
                                filteredLogs = filteredLogs.filter(log => log.message.includes('[AI_') || log.message.includes('[GEMINI') || log.message.includes('[CLASSIFY'));
                            } else if (filter === 'MCP') {
                                filteredLogs = filteredLogs.filter(log => log.message.includes('[MCP]'));
                            } else {
                                filteredLogs = filteredLogs.filter(log => log.level === filter);
                            }
                        }
                        
                        // Limit display if not expanded
                        if (!logsExpanded && filteredLogs.length > 1000) {
                            filteredLogs = filteredLogs.slice(-1000);
                        }
                        
                        container.innerHTML = filteredLogs.map(log => `
                            <div class="log-line">
                                <span class="log-time">${log.formatted_time}</span>
                                <span class="log-level-${log.level}">[${log.level}]</span>
                                <span>${escapeHtml(log.message)}</span>
                            </div>
                        `).join('');
                        
                        // Update stats
                        document.querySelector('#logContent').parentElement.previousElementSibling.querySelector('span').textContent = 
                            `Showing ${filteredLogs.length} lines`;
                        
                        // Auto-scroll to bottom
                        if (autoScroll) {
                            container.scrollTop = container.scrollHeight;
                        }
                    })
                    .catch(err => {
                        console.error('Error refreshing logs:', err);
                    });
            }
            
            function refreshEvents() {
                fetch('/api/events/recent?limit=100')
                    .then(r => r.json())
                    .then(events => {
                        const container = document.getElementById('eventContent');
                        
                        // Limit display if not expanded
                        let displayEvents = events;
                        if (!eventsExpanded && events.length > 100) {
                            displayEvents = events.slice(-100);
                        }
                        
                        container.innerHTML = displayEvents.map(event => `
                            <div class="event-item ${event.success ? 'event-success' : 'event-error'}">
                                <div>
                                    <span class="event-time">${event.time_short}</span>
                                    <span class="event-type">${event.type}</span>
                                    <strong>${event.source}</strong>: ${escapeHtml(event.details)}
                                </div>
                                ${event.data ? `
                                <div style="color:#666;font-size:9px;margin-top:2px;">
                                    ${Object.entries(event.data).map(([key, value]) => 
                                        `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`
                                    ).join(' | ')}
                                </div>
                                ` : ''}
                            </div>
                        `).join('');
                        
                        // Update stats
                        document.querySelector('#eventContent').parentElement.previousElementSibling.querySelector('span').textContent = 
                            `Showing ${displayEvents.length} events`;
                    });
            }
            
            function filterLogs() {
                refreshLogs();
            }
            
            function clearSearch() {
                document.getElementById('searchBox').value = '';
                refreshLogs();
            }
            
            function setFilter(filter) {
                currentFilter = filter;
                
                // Update badge states
                document.querySelectorAll('.filter-badge').forEach(badge => {
                    badge.classList.remove('active');
                    if (badge.textContent === filter) {
                        badge.classList.add('active');
                    }
                });
                
                refreshLogs();
            }
            
            function clearAllLogs() {
                if (confirm('Are you sure you want to clear ALL logs and events? This cannot be undone.')) {
                    fetch('/api/logs/clear', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            alert(`Cleared ${data.logs_cleared} logs and ${data.events_cleared} events`);
                            location.reload();
                        });
                }
            }
            
            function exportLogs() {
                fetch('/api/logs/export')
                    .then(r => r.text())
                    .then(data => {
                        const blob = new Blob([data], { type: 'text/plain' });
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `xiaozhi_logs_${new Date().toISOString().replace(/[:.]/g, '-')}.txt`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        window.URL.revokeObjectURL(url);
                    });
            }
            
            function toggleAutoRefresh() {
                const statusSpan = document.getElementById('autoRefreshStatus');
                if (autoRefreshInterval) {
                    clearInterval(autoRefreshInterval);
                    autoRefreshInterval = null;
                    statusSpan.textContent = 'OFF';
                    statusSpan.style.color = '#666';
                } else {
                    autoRefreshInterval = setInterval(() => {
                        refreshLogs();
                        refreshEvents();
                    }, 2000);
                    statusSpan.textContent = 'ON';
                    statusSpan.style.color = '#00ff00';
                }
            }
            
            function toggleEvents() {
                eventsExpanded = !eventsExpanded;
                refreshEvents();
            }
            
            function toggleLogs() {
                logsExpanded = !logsExpanded;
                refreshLogs();
            }
            
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }
            
            // Initialize auto-refresh
            toggleAutoRefresh();
            
            // Handle scroll events
            document.getElementById('logContent').addEventListener('scroll', function() {
                const container = this;
                const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 10;
                autoScroll = isAtBottom;
                document.getElementById('scrollInfo').textContent = `Auto-scroll: ${autoScroll ? 'ON' : 'OFF'}`;
                document.getElementById('scrollInfo').style.color = autoScroll ? '#00ff00' : '#ff0000';
            });
            
            // Handle search box enter key
            document.getElementById('searchBox').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    filterLogs();
                }
            });
            
            // Initial load
            refreshLogs();
            refreshEvents();
        </script>
    </body>
    </html>
    ''', 
    hours=hours, 
    minutes=minutes, 
    seconds=seconds,
    server_start_time=format_utc8_time(server_start_time),
    current_time=format_utc8_time(),
    log_count=unlimited_handler.count(),
    event_count=len(event_tracker.events),
    active_requests_count=len(active_requests),
    cache_size=len(gemini_cache),
    ping_tasks_count=len(ping_manager.active_tasks),
    logs=unlimited_handler.get_recent_logs(500),
    logs_to_show=min(500, unlimited_handler.count()),
    recent_events=recent_events,
    events_to_show=min(50, len(event_tracker.events)),
    last_update=format_utc8_time())

# ================= UNLIMITED LOG API ENDPOINTS =================
@app.route('/api/logs/all')
def api_logs_all():
    """Get ALL logs (no limit)"""
    all_logs = unlimited_handler.get_all_logs()
    return jsonify(all_logs)

@app.route('/api/logs/recent')
def api_logs_recent():
    """Get recent logs with limit parameter"""
    limit = request.args.get('limit', default=1000, type=int)
    logs = unlimited_handler.get_recent_logs(limit)
    return jsonify(logs)

@app.route('/api/logs/clear', methods=['POST'])
def api_logs_clear():
    """Clear ALL logs and events"""
    logs_cleared = unlimited_handler.count()
    events_cleared = len(event_tracker.events)
    
    unlimited_handler.clear_logs()
    event_tracker.clear_events()
    
    logger.info(f"🗑️ Cleared ALL logs: {logs_cleared} logs and {events_cleared} events")
    
    return jsonify({
        'status': 'success',
        'logs_cleared': logs_cleared,
        'events_cleared': events_cleared,
        'timestamp': format_utc8_time()
    })

@app.route('/api/logs/export')
def api_logs_export():
    """Export all logs as plain text"""
    all_logs = unlimited_handler.get_all_logs()
    
    log_text = f"Xiaozhi MCP Logs Export\n"
    log_text += f"Generated: {format_utc8_time()} (UTC+8)\n"
    log_text += f"Total logs: {len(all_logs)}\n"
    log_text += f"Server uptime: {(get_utc8_time() - server_start_time).total_seconds():.0f}s\n"
    log_text += "=" * 80 + "\n\n"
    
    for log in all_logs:
        log_text += f"{log['formatted_time']} [{log['level']}] {log['message']}\n"
    
    return Response(log_text, mimetype='text/plain',
                   headers={'Content-Disposition': 'attachment; filename=xiaozhi_logs.txt'})

@app.route('/api/events/all')
def api_events_all():
    """Get ALL events (no limit)"""
    all_events = event_tracker.get_all_events()
    return jsonify(all_events)

@app.route('/api/events/recent')
def api_events_recent():
    """Get recent events with limit parameter"""
    limit = request.args.get('limit', default=100, type=int)
    events = event_tracker.get_recent_events(limit)
    return jsonify(events)

@app.route('/api/events/stats')
def api_events_stats():
    """Get event statistics"""
    stats = event_tracker.get_event_statistics()
    return jsonify(stats)

@app.route('/api/events/by-type/<event_type>')
def api_events_by_type(event_type):
    """Get events filtered by type"""
    limit = request.args.get('limit', default=100, type=int)
    events = event_tracker.get_events_by_type(event_type, limit)
    return jsonify(events)

@app.route('/api/events/by-source/<source>')
def api_events_by_source(source):
    """Get events filtered by source"""
    limit = request.args.get('limit', default=100, type=int)
    events = event_tracker.get_events_by_source(source, limit)
    return jsonify(events)

@app.route('/api/system/stats')
def api_system_stats():
    """Get comprehensive system statistics"""
    uptime = (get_utc8_time() - server_start_time).total_seconds()
    
    return jsonify({
        'server': {
            'start_time': format_utc8_time(server_start_time),
            'current_time': format_utc8_time(),
            'uptime_seconds': uptime,
            'uptime_human': f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s",
            'version': '3.6.2-UTC8-UNLIMITED'
        },
        'logs': {
            'total_count': unlimited_handler.count(),
            'memory_size': sum(len(str(log)) for log in unlimited_handler.get_all_logs()),
            'last_log_time': unlimited_handler.get_all_logs()[-1]['formatted_time'] if unlimited_handler.count() > 0 else 'None'
        },
        'events': {
            'total_count': len(event_tracker.events),
            'statistics': event_tracker.get_event_statistics()
        },
        'ping_system': {
            'active_tasks': len(ping_manager.active_tasks),
            'ping_counters': ping_manager.ping_counters,
            'config': {
                'first_ping_delay': FIRST_PING_DELAY,
                'continuous_interval': CONTINUOUS_PING_INTERVAL,
                'async_mode': ASYNC_PING_MODE
            }
        },
        'cache': {
            'size': len(gemini_cache),
            'duration_seconds': CACHE_DURATION
        },
        'active_requests': {
            'count': len(active_requests),
            'ids': list(active_requests.keys())
        }
    })

@app.route('/api/ping/debug')
def api_ping_debug():
    """Detailed ping debugging information"""
    return jsonify({
        'ping_manager': {
            'active_tasks': len(ping_manager.active_tasks),
            'task_ids': list(ping_manager.active_tasks.keys()),
            'websocket_refs': len(ping_manager.websocket_refs),
            'ping_counters': ping_manager.ping_counters,
            'start_times': {k: format_utc8_time(v) for k, v in ping_manager.start_times.items()}
        },
        'config': {
            'first_ping_delay': FIRST_PING_DELAY,
            'continuous_interval': CONTINUOUS_PING_INTERVAL,
            'async_ping_mode': ASYNC_PING_MODE,
            'parallel_model_tries': PARALLEL_MODEL_TRIES
        },
        'active_requests': {
            'count': len(active_requests),
            'details': {k: v for k, v in active_requests.items()}
        },
        'timestamp': format_utc8_time()
    })

@app.route('/health')
def health_check():
    """Health check endpoint with detailed information"""
    uptime = (get_utc8_time() - server_start_time).total_seconds()
    
    return jsonify({
        "status": "unlimited_logs_healthy",
        "version": "3.6.2-UTC8-UNLIMITED",
        "timezone": "UTC+8",
        "current_time": format_utc8_time(),
        "server_start_time": format_utc8_time(server_start_time),
        "uptime_seconds": uptime,
        "uptime_human": f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s",
        "logs": {
            "total_count": unlimited_handler.count(),
            "recent_count": len(unlimited_handler.get_recent_logs(100))
        },
        "events": {
            "total_count": len(event_tracker.events),
            "statistics": event_tracker.get_event_statistics()
        },
        "cache_size": len(gemini_cache),
        "active_requests": len(active_requests),
        "ping_system": {
            "active_tasks": len(ping_manager.active_tasks),
            "config": {
                "first_ping_delay": FIRST_PING_DELAY,
                "continuous_interval": CONTINUOUS_PING_INTERVAL
            }
        },
        "timestamp": format_utc8_time()
    })

# ================= ASYNC MCP BRIDGE WITH DETAILED LOGGING =================
async def async_mcp_bridge():
    """Async WebSocket bridge with detailed logging."""
    reconnect_delay = 1
    
    event_tracker.add_system_event(
        "MCP_BRIDGE_START",
        "Starting MCP WebSocket bridge",
        data={
            'xiaozhi_ws': XIAOZHI_WS[:50] + '...' if len(XIAOZHI_WS) > 50 else XIAOZHI_WS,
            'reconnect_delay': reconnect_delay
        }
    )
    
    logger.info("🔗 [MCP_BRIDGE] Starting MCP WebSocket bridge")
    
    while True:
        try:
            event_tracker.add_websocket_event(
                "CONNECT_START",
                f"Connecting to Xiaozhi WebSocket (delay: {reconnect_delay}s)",
                data={'reconnect_delay': reconnect_delay}
            )
            
            logger.info(f"🔗 [MCP_BRIDGE] Connecting to Xiaozhi (reconnect delay: {reconnect_delay}s)...")
            logger.debug(f"   • WebSocket URL: {XIAOZHI_WS[:50]}...")
            
            async with websockets.connect(
                XIAOZHI_WS,
                ping_interval=15,
                ping_timeout=10,
                close_timeout=5
            ) as websocket:
                websocket_id = id(websocket)
                
                event_tracker.add_websocket_event(
                    "CONNECT_SUCCESS",
                    "Connected to Xiaozhi WebSocket",
                    websocket_id=str(websocket_id),
                    data={
                        'websocket_id': websocket_id,
                        'ping_interval': 15,
                        'ping_timeout': 10
                    }
                )
                
                logger.info("✅ [MCP_BRIDGE] CONNECTED TO XIAOZHI")
                logger.debug(f"   • WebSocket ID: {websocket_id}")
                logger.debug(f"   • WebSocket open: {websocket.open}")
                logger.debug(f"   • Ping interval: 15s")
                logger.debug(f"   • Ping timeout: 10s")
                
                reconnect_delay = 1  # Reset reconnect delay on successful connection
                
                async for raw_message in websocket:
                    receive_time = get_utc8_time()
                    
                    try:
                        data = json.loads(raw_message)
                        message_id = data.get("id")
                        method = data.get("method", "")
                        params = data.get("params", {})
                        
                        event_tracker.add_websocket_event(
                            "MESSAGE_RECEIVED",
                            f"Received MCP message: {method}",
                            websocket_id=str(websocket_id),
                            data={
                                'message_id': message_id,
                                'method': method,
                                'params_keys': list(params.keys()),
                                'message_length': len(raw_message)
                            }
                        )
                        
                        logger.debug(f"📨 [MCP_BRIDGE] Received MCP message: {method}")
                        logger.debug(f"   • Message ID: {message_id}")
                        logger.debug(f"   • Method: {method}")
                        logger.debug(f"   • Params keys: {list(params.keys())}")
                        logger.debug(f"   • Message length: {len(raw_message)} chars")
                        logger.debug(f"   • Receive time: {format_utc8_time_short(receive_time)}")
                        
                        response = None
                        
                        if method == "ping":
                            logger.debug("   • [MCP_BRIDGE] Handling ping request")
                            response = AsyncMCPHandler.handle_ping(message_id)
                            
                        elif method == "initialize":
                            logger.debug("   • [MCP_BRIDGE] Handling initialize request")
                            response = AsyncMCPHandler.handle_initialize(message_id)
                            
                            event_tracker.add_system_event(
                                "MCP_INITIALIZED",
                                "MCP protocol initialized",
                                data={'message_id': message_id}
                            )
                            
                        elif method == "tools/list":
                            logger.debug("   • [MCP_BRIDGE] Handling tools/list request")
                            response = AsyncMCPHandler.handle_tools_list(message_id)
                            
                        elif method == "tools/call":
                            tool_name = params.get("name", "")
                            logger.debug(f"   • [MCP_BRIDGE] Handling tools/call: {tool_name}")
                            
                            response = await AsyncMCPHandler.handle_tools_call_async(
                                message_id, params, websocket
                            )
                            
                        else:
                            logger.warning(f"   • [MCP_BRIDGE] Unknown method: {method}")
                            
                            event_tracker.add_websocket_event(
                                "UNKNOWN_METHOD",
                                f"Unknown MCP method: {method}",
                                websocket_id=str(websocket_id)
                            )
                            
                            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "Unknown method"}}
                        
                        if response:
                            send_start = get_utc8_time()
                            
                            logger.debug(f"📤 [MCP_BRIDGE] Sending response for message {message_id}")
                            await websocket.send(json.dumps(response))
                            
                            send_duration = (get_utc8_time() - send_start).total_seconds() * 1000
                            
                            event_tracker.add_websocket_event(
                                "MESSAGE_SENT",
                                f"Sent response for {method}",
                                websocket_id=str(websocket_id),
                                data={
                                    'message_id': message_id,
                                    'method': method,
                                    'response_length': len(json.dumps(response)),
                                    'send_duration_ms': send_duration
                                }
                            )
                            
                            logger.debug(f"   • [MCP_BRIDGE] Response sent successfully")
                            logger.debug(f"   • Send duration: {send_duration:.1f}ms")
                            logger.debug(f"   • Response length: {len(json.dumps(response))} chars")
                            
                    except json.JSONDecodeError as e:
                        error_time = get_utc8_time()
                        
                        event_tracker.add_websocket_event(
                            "JSON_ERROR",
                            f"JSON decode error: {str(e)[:50]}",
                            websocket_id=str(websocket_id)
                        )
                        
                        logger.error(f"❌ [MCP_BRIDGE] JSON decode error: {e}")
                        logger.debug(f"   • Error time: {format_utc8_time_short(error_time)}")
                        logger.debug(f"   • Raw message: {raw_message[:100]}...")
                        
                    except Exception as e:
                        error_time = get_utc8_time()
                        
                        event_tracker.add_websocket_event(
                            "MESSAGE_ERROR",
                            f"Message processing error: {str(e)[:50]}",
                            websocket_id=str(websocket_id)
                        )
                        
                        logger.error(f"❌ [MCP_BRIDGE] Message processing error: {e}")
                        logger.debug(f"   • Error time: {format_utc8_time_short(error_time)}")
                        logger.exception("Detailed error trace:")
                        
        except websockets.exceptions.ConnectionClosed as e:
            error_time = get_utc8_time()
            
            event_tracker.add_websocket_event(
                "CONNECTION_CLOSED",
                f"WebSocket connection closed: {e.code} - {e.reason}",
                data={'close_code': e.code, 'close_reason': e.reason}
            )
            
            logger.error(f"❌ [MCP_BRIDGE] WebSocket connection closed: {e.code} - {e.reason}")
            logger.debug(f"   • Error time: {format_utc8_time_short(error_time)}")
            logger.debug(f"   • Close code: {e.code}")
            logger.debug(f"   • Close reason: {e.reason}")
            
        except ConnectionRefusedError as e:
            error_time = get_utc8_time()
            
            event_tracker.add_websocket_event(
                "CONNECTION_REFUSED",
                f"Connection refused: {str(e)}"
            )
            
            logger.error(f"❌ [MCP_BRIDGE] Connection refused: {e}")
            logger.debug(f"   • Error time: {format_utc8_time_short(error_time)}")
            
        except Exception as e:
            error_time = get_utc8_time()
            
            event_tracker.add_websocket_event(
                "CONNECTION_ERROR",
                f"Connection error: {str(e)[:50]}"
            )
            
            logger.error(f"❌ [MCP_BRIDGE] Connection error: {e}")
            logger.debug(f"   • Error time: {format_utc8_time_short(error_time)}")
            logger.exception("Detailed error trace:")
            
            # Clean up all ping tasks
            logger.debug("🧹 [MCP_BRIDGE] Cleaning up ping tasks due to connection error")
            
            event_tracker.add_system_event(
                "PING_CLEANUP",
                "Cleaning up ping tasks after connection error",
                data={'active_tasks_before': len(ping_manager.active_tasks)}
            )
            
            ping_manager.stop_all_pings()
            active_requests.clear()
            
            event_tracker.add_system_event(
                "PING_CLEANUP_COMPLETE",
                "Ping cleanup completed",
                data={'active_tasks_after': len(ping_manager.active_tasks)}
            )
            
            logger.info(f"🔄 [MCP_BRIDGE] Reconnecting in {reconnect_delay}s...")
            logger.debug(f"   • Next reconnect delay: {reconnect_delay}s")
            
            event_tracker.add_websocket_event(
                "RECONNECT_SCHEDULED",
                f"Scheduled reconnect in {reconnect_delay}s",
                data={'reconnect_delay': reconnect_delay}
            )
            
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 5)  # Exponential backoff, max 5 seconds

async def main():
    """Main async entry point with detailed startup logging."""
    startup_time = get_utc8_time()
    
    logger.info("""
    ╔══════════════════════════════════════════════════════════════════╗
    ║           🚀 XIAOZHI MCP v3.6.2 - UNLIMITED LOGS                ║
    ║                   TIMEZONE: UTC+8                               ║
    ║              NO LOG LIMITS • EVERYTHING LOGGED                  ║
    ╚══════════════════════════════════════════════════════════════════╝
    """)
    
    event_tracker.add_system_event(
        "SERVER_START",
        "Xiaozhi MCP Server starting",
        data={
            'version': '3.6.2-UTC8-UNLIMITED',
            'startup_time': format_utc8_time(startup_time),
            'timezone': 'UTC+8',
            'log_mode': 'UNLIMITED',
            'first_ping_delay': FIRST_PING_DELAY,
            'continuous_ping_interval': CONTINUOUS_PING_INTERVAL
        }
    )
    
    logger.info(f"🚀 [MAIN] Starting Xiaozhi MCP Server v3.6.2")
    logger.debug(f"   • Startup time: {format_utc8_time(startup_time)}")
    logger.debug(f"   • Timezone: UTC+8")
    logger.debug(f"   • Log mode: UNLIMITED (no max length)")
    logger.debug(f"   • First ping delay: {FIRST_PING_DELAY}s")
    logger.debug(f"   • Continuous ping interval: {CONTINUOUS_PING_INTERVAL}s")
    logger.debug(f"   • Parallel model tries: {PARALLEL_MODEL_TRIES}")
    logger.debug(f"   • Max workers: {MAX_WORKERS}")
    logger.debug(f"   • Gemini API key: {'✅ CONFIGURED' if GEMINI_API_KEY else '❌ MISSING'}")
    logger.debug(f"   • Google API key: {'✅ CONFIGURED' if GOOGLE_API_KEY else '❌ MISSING'}")
    logger.debug(f"   • Xiaozhi WebSocket: {'✅ CONFIGURED' if XIAOZHI_WS else '❌ MISSING'}")
    
    # Start web server in background thread
    def run_web_server():
        event_tracker.add_system_event(
            "WEB_SERVER_START",
            "Starting Flask web server",
            data={'port': 3000, 'host': '0.0.0.0'}
        )
        
        logger.info("🌐 [WEB_SERVER] Starting web server on port 3000")
        
        app.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)
    
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    logger.debug(f"   • Web server thread ID: {web_thread.ident}")
    logger.debug(f"   • Web server thread alive: {web_thread.is_alive()}")
    logger.debug(f"   • Web server URL: http://0.0.0.0:3000/")
    
    # Give web server a moment to start
    await asyncio.sleep(1)
    
    # Start MCP bridge
    event_tracker.add_system_event(
        "MCP_BRIDGE_START",
        "Starting MCP WebSocket bridge",
        data={'timestamp': format_utc8_time()}
    )
    
    logger.info("🔗 [MAIN] Starting MCP bridge with unlimited logging...")
    
    await async_mcp_bridge()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        shutdown_time = get_utc8_time()
        
        event_tracker.add_system_event(
            "SERVER_STOP",
            "Server stopped by user (KeyboardInterrupt)",
            data={'shutdown_time': format_utc8_time(shutdown_time)}
        )
        
        logger.info("\n👋 [MAIN] Server stopped by user")
        logger.debug(f"   • Shutdown time: {format_utc8_time(shutdown_time)}")
        
        ping_manager.stop_all_pings()
        
    except Exception as e:
        shutdown_time = get_utc8_time()
        
        event_tracker.add_system_event(
            "SERVER_CRASH",
            f"Server crashed with fatal error: {str(e)[:100]}",
            success=False,
            data={'shutdown_time': format_utc8_time(shutdown_time)}
        )
        
        logger.error(f"💥 [MAIN] Fatal error: {e}")
        logger.debug(f"   • Crash time: {format_utc8_time(shutdown_time)}")
        logger.exception("Detailed error trace:")
        
        ping_manager.stop_all_pings()