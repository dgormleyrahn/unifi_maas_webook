#!/usr/bin/env python3
"""
UniFi PoE Webhook Power Management Server
Provides webhook endpoints to control Raspberry Pi power via UniFi PoE ports
"""

import json
import time
import requests
import logging
import os
import subprocess
import threading
from flask import Flask, request, jsonify
from datetime import datetime
from typing import Dict, List, Optional
import urllib3
from dotenv import load_dotenv
from queue import Queue

# Load environment variables
load_dotenv()

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class UniFiConfig:
    """Configuration class for UniFi API settings"""
    def __init__(self, config_file: str = "config.json"):
        self.config = self.load_config(config_file)
        
    def load_config(self, config_file: str) -> dict:
        """Load configuration from JSON file"""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Create default config if file doesn't exist
            default_config = {
                "unifi": {
                    "api_key": os.getenv("UNIFI_API_KEY", "your-api-key-here"),
                    "base_url": os.getenv("UNIFI_BASE_URL", "https://10.0.1.1/proxy/network/integration/v1"),
                    "site_id": os.getenv("UNIFI_SITE_ID", "your-site-id-here"),
                    "device_id": os.getenv("UNIFI_DEVICE_ID", "your-device-id-here")
                },
                "ports": {
                    "1": {"name": "pi-node-1", "ip": "172.16.254.101"},
                    "2": {"name": "pi-node-2", "ip": "172.16.254.102"},
                    "3": {"name": "pi-node-3", "ip": "172.16.254.103"},
                    "4": {"name": "pi-node-4", "ip": "172.16.254.104"}
                },
                "webhook": {
                    "port": 5000,
                    "host": "0.0.0.0",
                    "auth_token": os.getenv("WEBHOOK_AUTH_TOKEN", "your-secure-webhook-token"),
                    "power_cycle_delay": 3
                }
            }
            
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            print(f"Created default config file: {config_file}")
            return default_config
    
    @property
    def api_key(self) -> str:
        return os.getenv("UNIFI_API_KEY") or self.config["unifi"]["api_key"]
    
    @property
    def base_url(self) -> str:
        return os.getenv("UNIFI_BASE_URL") or self.config["unifi"]["base_url"]
    
    @property
    def site_id(self) -> str:
        return os.getenv("UNIFI_SITE_ID") or self.config["unifi"]["site_id"]
    
    @property
    def device_id(self) -> str:
        return os.getenv("UNIFI_DEVICE_ID") or self.config["unifi"]["device_id"]
    
    @property
    def ports(self) -> dict:
        return self.config["ports"]
    
    @property
    def webhook_config(self) -> dict:
        return self.config["webhook"]

class UniFiPortController:
    """Controller for UniFi PoE port operations"""
    
    def __init__(self, config: UniFiConfig):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False  # Disable SSL verification for self-signed certs
        self.last_operation_time = {}  # Track last operation time per port per operation type
        self.rate_limit_seconds = 30  # Cooldown period in seconds
        self.operation_queue = Queue()  # Queue for delayed operations
        self.last_unifi_operation = {}  # Track last UniFi API call per port
        self.unifi_cooldown = 10  # Seconds to wait between UniFi operations on same port
        self._start_queue_worker()
        
    def _make_request(self, method: str, endpoint: str, data: dict = None) -> requests.Response:
        """Make authenticated request to UniFi API"""
        url = f"{self.config.base_url}/{endpoint}"
        headers = {
            'X-API-KEY': self.config.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        if method.upper() == 'POST':
            return self.session.post(url, headers=headers, json=data)
        elif method.upper() == 'GET':
            return self.session.get(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
    
    def _start_queue_worker(self):
        """Start background thread to process queued operations"""
        def queue_worker():
            while True:
                try:
                    # Get next queued operation (blocks until available)
                    operation_data = self.operation_queue.get()
                    if operation_data is None:  # Shutdown signal
                        break
                    
                    port = operation_data['port']
                    action = operation_data['action']
                    delay = operation_data['delay']
                    
                    # Wait for the specified delay
                    time.sleep(delay)
                    
                    # Execute the operation
                    logger.info(f"Executing queued {action} operation for port {port}")
                    result = self._execute_power_cycle(port, action)
                    
                    if result["success"]:
                        self.last_unifi_operation[port] = time.time()
                        logger.info(f"Queued {action} operation for port {port} completed successfully")
                    else:
                        logger.warning(f"Queued {action} operation for port {port} failed: {result.get('error', 'Unknown error')}")
                    
                    self.operation_queue.task_done()
                    
                except Exception as e:
                    logger.error(f"Error in queue worker: {e}")
                    self.operation_queue.task_done()
        
        # Start worker thread
        worker_thread = threading.Thread(target=queue_worker, daemon=True)
        worker_thread.start()
    
    def _can_execute_immediately(self, port: int) -> bool:
        """Check if we can execute UniFi operation immediately"""
        if port not in self.last_unifi_operation:
            return True
        
        time_since_last = time.time() - self.last_unifi_operation[port]
        return time_since_last >= self.unifi_cooldown
    
    def _get_operation_key(self, port: int, operation: str) -> str:
        """Generate unique key for port + operation combination"""
        return f"{port}:{operation}"
    
    def _is_port_operation_rate_limited(self, port: int, operation: str) -> bool:
        """Check if specific port + operation is currently rate limited"""
        key = self._get_operation_key(port, operation)
        if key not in self.last_operation_time:
            return False
        
        time_since_last = time.time() - self.last_operation_time[key]
        return time_since_last < self.rate_limit_seconds
    
    def _record_port_operation(self, port: int, operation: str):
        """Record that a specific operation was performed on this port"""
        key = self._get_operation_key(port, operation)
        self.last_operation_time[key] = time.time()
    
    def _get_rate_limit_response(self, port: int, action: str) -> dict:
        """Return standardized rate limit response"""
        key = self._get_operation_key(port, action)
        time_remaining = self.rate_limit_seconds - (time.time() - self.last_operation_time.get(key, 0))
        return {
            "success": False,
            "action": action,
            "port": port,
            "error": f"Rate limited. Please wait {int(time_remaining)} seconds before next {action} operation",
            "rate_limited": True,
            "retry_after": int(time_remaining),
            "timestamp": datetime.now().isoformat()
        }
    
    def power_on_port(self, port: int) -> dict:
        """Power on a specific port (no-op since power cycle handles this)"""
        # No rate limiting for power_on since it's a no-op that doesn't call UniFi API
        # Just record the operation for status logic tracking
        self._record_port_operation(port, "power_on")
        
        # Since UniFi only supports power cycle, and power cycle automatically
        # powers the device back on, we just return success without doing anything.
        # The device should already be powering on from a previous power cycle.
        return {
            "success": True,
            "action": "power_on",
            "port": port,
            "status": "no_action_needed",
            "message": "Port will power on automatically after power cycle",
            "timestamp": datetime.now().isoformat()
        }
    
    def power_off_port(self, port: int) -> dict:
        """Power off a specific port (uses power cycle since UniFi only supports cycle)"""
        # Check rate limiting for power_off specifically  
        if self._is_port_operation_rate_limited(port, "power_off"):
            return self._get_rate_limit_response(port, "power_off")
        
        # Record operation for rate limiting
        self._record_port_operation(port, "power_off")
        
        # Check if we can execute immediately
        if self._can_execute_immediately(port):
            # Execute immediately
            result = self._execute_power_cycle(port, "power_off")
            if result["success"]:
                self.last_unifi_operation[port] = time.time()
            return result
        else:
            # Queue for later execution
            delay = self.unifi_cooldown - (time.time() - self.last_unifi_operation.get(port, 0))
            self.operation_queue.put({
                'port': port,
                'action': 'power_off',
                'delay': max(0, delay)
            })
            
            return {
                "success": True,
                "action": "power_off",
                "port": port,
                "status": "queued",
                "queued_delay": max(0, delay),
                "message": f"Operation queued for execution in {max(0, int(delay))} seconds",
                "timestamp": datetime.now().isoformat()
            }
    
    def power_cycle_port(self, port: int) -> dict:
        """Power cycle a specific port"""
        # Check rate limiting for power_cycle specifically
        if self._is_port_operation_rate_limited(port, "power_cycle"):
            return self._get_rate_limit_response(port, "power_cycle")
        
        # Record operation for rate limiting
        self._record_port_operation(port, "power_cycle")
        
        # Check if we can execute immediately
        if self._can_execute_immediately(port):
            # Execute immediately
            result = self._execute_power_cycle(port, "power_cycle")
            if result["success"]:
                self.last_unifi_operation[port] = time.time()
            return result
        else:
            # Queue for later execution
            delay = self.unifi_cooldown - (time.time() - self.last_unifi_operation.get(port, 0))
            self.operation_queue.put({
                'port': port,
                'action': 'power_cycle',
                'delay': max(0, delay)
            })
            
            return {
                "success": True,
                "action": "power_cycle",
                "port": port,
                "status": "queued",
                "queued_delay": max(0, delay),
                "message": f"Operation queued for execution in {max(0, int(delay))} seconds",
                "timestamp": datetime.now().isoformat()
            }
    
    def _execute_power_cycle(self, port: int, action: str) -> dict:
        """Execute the actual power cycle operation via UniFi API"""
        endpoint = f"sites/{self.config.site_id}/devices/{self.config.device_id}/interfaces/ports/{port}/actions"
        
        try:
            response = self._make_request('POST', endpoint, {"action": "POWER_CYCLE"})
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "action": action,
                    "port": port,
                    "status": "cycling",
                    "timestamp": datetime.now().isoformat()
                }
            else:
                return {
                    "success": False,
                    "action": action,
                    "port": port,
                    "error": f"HTTP {response.status_code}",
                    "timestamp": datetime.now().isoformat()
                }
        except Exception as e:
            return {
                "success": False,
                "action": action,
                "port": port,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def get_port_status(self, port: int) -> dict:
        """Get the status of a specific port based on last operation"""
        # Simple status logic based on last operation called
        # Default to running if no operations have been performed
        status = "running"
        
        # Check if power_off was the last operation (which triggers power cycle)
        power_off_key = f"{port}:power_off"
        power_on_key = f"{port}:power_on"
        
        power_off_time = self.last_operation_time.get(power_off_key, 0)
        power_on_time = self.last_operation_time.get(power_on_key, 0)
        
        if power_off_time > power_on_time:
            # Power off was called more recently than power on
            status = "stopped"
        else:
            # Power on was called more recently or no operations yet
            status = "running"
        
        # Format for MAAS regex pattern: status.*:.*running
        status_message = f"status: {status}"
        
        return {
            "success": True,
            "port": port,
            "status": status_message,
            "method": "operation_tracking",
            "timestamp": datetime.now().isoformat()
        }

# Initialize Flask app
app = Flask(__name__)
config = UniFiConfig()
controller = UniFiPortController(config)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def authenticate_request():
    """Check if request has valid authentication token"""
    auth_token = os.getenv("WEBHOOK_AUTH_TOKEN") or config.webhook_config.get("auth_token")
    if not auth_token:
        return True  # No auth required if not configured
    
    # Check for token in header
    provided_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    # Check for token in query params
    if not provided_token:
        provided_token = request.args.get('token', '')
    
    # Check for token in form data
    if not provided_token:
        provided_token = request.form.get('token', '')
    
    return provided_token == auth_token

@app.before_request
def before_request():
    """Authenticate all requests"""
    if not authenticate_request():
        return jsonify({"error": "Invalid or missing authentication token"}), 401

@app.route('/power/on/<int:port>', methods=['POST', 'GET'])
def power_on(port: int):
    """Power on a specific port"""
    if str(port) not in config.ports:
        return jsonify({"error": f"Port {port} not configured"}), 400
    
    result = controller.power_on_port(port)
    
    # Handle rate limiting with HTTP 429
    if not result["success"] and result.get("rate_limited", False):
        status_code = 429
        response = jsonify(result)
        response.headers['Retry-After'] = str(result.get("retry_after", 30))
    else:
        status_code = 200 if result["success"] else 500
        response = jsonify(result)
    
    logger.info(f"Power ON request for port {port}: {result}")
    return response, status_code

@app.route('/power/off/<int:port>', methods=['POST', 'GET'])
def power_off(port: int):
    """Power off a specific port"""
    if str(port) not in config.ports:
        return jsonify({"error": f"Port {port} not configured"}), 400
    
    result = controller.power_off_port(port)
    
    # Handle rate limiting with HTTP 429
    if not result["success"] and result.get("rate_limited", False):
        status_code = 429
        response = jsonify(result)
        response.headers['Retry-After'] = str(result.get("retry_after", 30))
    else:
        status_code = 200 if result["success"] else 500
        response = jsonify(result)
    
    logger.info(f"Power OFF request for port {port}: {result}")
    return response, status_code

@app.route('/power/cycle/<int:port>', methods=['POST', 'GET'])
def power_cycle(port: int):
    """Power cycle a specific port"""
    if str(port) not in config.ports:
        return jsonify({"error": f"Port {port} not configured"}), 400
    
    result = controller.power_cycle_port(port)
    
    # Handle rate limiting with HTTP 429
    if not result["success"] and result.get("rate_limited", False):
        status_code = 429
        response = jsonify(result)
        response.headers['Retry-After'] = str(result.get("retry_after", 30))
    else:
        status_code = 200 if result["success"] else 500
        response = jsonify(result)
    
    logger.info(f"Power CYCLE request for port {port}: {result}")
    return response, status_code

@app.route('/power/status/<int:port>', methods=['GET'])
def power_status(port: int):
    """Get power status of a specific port"""
    if str(port) not in config.ports:
        return jsonify({"error": f"Port {port} not configured"}), 400
    
    result = controller.get_port_status(port)
    status_code = 200 if result["success"] else 500
    
    logger.info(f"Status request for port {port}: {result}")
    
    # For MAAS integration, return just the status message if successful
    if result["success"] and "status" in result:
        return result["status"], status_code
    else:
        return jsonify(result), status_code

@app.route('/ports', methods=['GET'])
def list_ports():
    """List all configured ports"""
    return jsonify({
        "ports": config.ports,
        "webhook_endpoints": {
            "power_on": "/power/on/<port>",
            "power_off": "/power/off/<port>",
            "power_cycle": "/power/cycle/<port>",
            "status": "/power/status/<port>"
        }
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "configured_ports": list(config.ports.keys())
    })

if __name__ == '__main__':
    webhook_config = config.webhook_config
    
    print("🚀 Starting UniFi PoE Webhook Server")
    print(f"📋 Configured ports: {list(config.ports.keys())}")
    print(f"🔗 Server will run on http://{webhook_config['host']}:{webhook_config['port']}")
    print("\n📚 Available endpoints:")
    print(f"  POST /power/on/<port>     - Power on port")
    print(f"  POST /power/off/<port>    - Power off port") 
    print(f"  POST /power/cycle/<port>  - Power cycle port")
    print(f"  GET  /power/status/<port> - Get port status")
    print(f"  GET  /ports              - List configured ports")
    print(f"  GET  /health             - Health check")
    
    if webhook_config.get("auth_token"):
        print(f"\n🔐 Authentication required: Include token in header, query param, or form data")
    
    app.run(
        host=webhook_config['host'],
        port=webhook_config['port'],
        debug=True
    )
