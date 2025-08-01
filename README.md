# UniFi PoE Webhook Power Management for MAAS

A Flask-based webhook server that enables MAAS (Metal As A Service) to control Raspberry Pi power through UniFi switch PoE ports. This system provides power management capabilities for Raspberry Pi clusters by leveraging UniFi's PoE control API.

## 🚀 Features

- **MAAS Integration**: Webhook endpoints compatible with MAAS power management
- **UniFi PoE Control**: Direct integration with UniFi controller API for PoE port management
- **Rate Limiting**: Intelligent rate limiting to prevent switch overload
- **Operation Queueing**: Background queue system for delayed operations
- **Authentication**: Secure webhook endpoints with token-based authentication
- **Ansible Deployment**: Automated deployment with systemd service configuration
- **Status Tracking**: Power state tracking for MAAS integration

## 🏗️ Architecture

### Power Management Logic

Since UniFi switches only support power cycle operations (not independent on/off), this system implements:

- **Power Off**: Triggers a power cycle (device powers off then automatically back on)
- **Power On**: No-op operation (devices auto-power after cycle)
- **Power Cycle**: Direct power cycle operation
- **Status**: Tracks last operation to determine current state

### Rate Limiting & Queueing

- 30-second cooldown between operations per port per operation type
- 10-second cooldown between UniFi API calls per port
- Background worker thread processes queued operations

## 📋 Prerequisites

- Python 3.7+
- UniFi Controller with API access
- UniFi PoE switch with connected Raspberry Pi devices
- MAAS environment (for integration)

## 🛠️ Installation

### Quick Start (Development)

1. **Clone and setup**:
   ```bash
   git clone <repository-url>
   cd MAAS_UniFi_RPi_Power_Manager
   pip install -r requirements.txt
   ```

2. **Configure the application**:
   ```bash
   cp config.json.example config.json
   # Edit config.json with your UniFi credentials and port mappings
   ```

3. **Set environment variables** (optional, overrides config.json):
   ```bash
   export UNIFI_API_KEY="your-api-key"
   export UNIFI_BASE_URL="https://your-controller/proxy/network/integration/v1"
   export UNIFI_SITE_ID="your-site-id"
   export UNIFI_DEVICE_ID="your-switch-device-id"
   export WEBHOOK_AUTH_TOKEN="your-secure-token"
   ```

4. **Run the server**:
   ```bash
   python unifi_webhook_server.py
   ```

### Production Deployment with Ansible

1. **Setup inventory**:
   ```bash
   cp inventory.example inventory
   # Edit inventory with your server details
   ```

2. **Configure variables**:
   ```bash
   cp group_vars/webhook_servers.yml.example group_vars/webhook_servers.yml
   # Edit with your UniFi credentials and deployment settings
   ```

3. **Deploy**:
   ```bash
   ansible-playbook -i inventory install.yml
   ```

## ⚙️ Configuration

### config.json Structure

```json
{
  "unifi": {
    "api_key": "your-api-key-here",
    "base_url": "https://your-controller-ip/proxy/network/integration/v1",
    "site_id": "your-site-id-here",
    "device_id": "your-switch-device-id-here"
  },
  "ports": {
    "1": {"name": "pi-node-1", "ip": "192.168.1.101"},
    "2": {"name": "pi-node-2", "ip": "192.168.1.102"}
  },
  "webhook": {
    "port": 5000,
    "host": "0.0.0.0",
    "auth_token": "your-secure-webhook-token",
    "power_cycle_delay": 3
  }
}
```

### Environment Variables

Environment variables take precedence over config.json:

- `UNIFI_API_KEY`: UniFi controller API key
- `UNIFI_BASE_URL`: Base URL for UniFi API
- `UNIFI_SITE_ID`: UniFi site identifier
- `UNIFI_DEVICE_ID`: UniFi switch device identifier
- `WEBHOOK_AUTH_TOKEN`: Authentication token for webhook endpoints

## 🌐 API Endpoints

All endpoints require authentication via:
- Header: `Authorization: Bearer <token>`
- Query parameter: `?token=<token>`
- Form data: `token=<token>`

### Power Management

- `POST /power/on/<port>` - Power on port (no-op, returns success)
- `POST /power/off/<port>` - Power off port (triggers power cycle)  
- `POST /power/cycle/<port>` - Power cycle port
- `GET /power/status/<port>` - Get port status

### Information

- `GET /ports` - List all configured ports
- `GET /health` - Health check endpoint

### Example Usage

```bash
# Power cycle port 1
curl -X POST "http://webhook-server:5000/power/cycle/1" \
     -H "Authorization: Bearer your-token"

# Check port status
curl "http://webhook-server:5000/power/status/1?token=your-token"
```

## 🔧 MAAS Integration

### Power Driver Configuration

In MAAS, configure the webhook power driver:

1. **Power type**: `webhook`
2. **Webhook URL**: `http://your-server:5000/power/{action}/{port}`
3. **Authentication**: Include token in URL or headers
4. **Status regex**: `status.*:.*running`

### Status Response Format

The `/power/status/<port>` endpoint returns plain text:
- `status: running` - Device is considered powered on
- `status: stopped` - Device is considered powered off

## 🚀 Service Management

### Systemd Service (Ansible Deployed)

```bash
# Check service status
sudo systemctl status unifi-webhook

# View logs
sudo journalctl -u unifi-webhook -f

# Restart service
sudo systemctl restart unifi-webhook
```

### Manual Service Management

```bash
# Start development server
python unifi_webhook_server.py

# Production with gunicorn (install separately)
gunicorn -w 4 -b 0.0.0.0:5000 unifi_webhook_server:app
```

## 🛡️ Security Features

- Token-based authentication for all endpoints
- SSL verification disabled for self-signed UniFi certificates (configurable)
- Systemd service with security hardening:
  - `NoNewPrivileges=yes`
  - `PrivateTmp=yes`
  - `ProtectSystem=strict`
  - Dedicated service user with minimal privileges

## 🐛 Troubleshooting

### Common Issues

1. **UniFi API Connection Failed**
   - Verify API key and controller URL
   - Check network connectivity to UniFi controller
   - Ensure controller has API access enabled

2. **Rate Limiting Errors**
   - Check logs for rate limit messages
   - Adjust rate limit settings if needed
   - Monitor queue processing

3. **MAAS Integration Issues**
   - Verify webhook URL configuration in MAAS
   - Check authentication token
   - Test endpoints manually with curl

### Debugging

```bash
# Enable debug logging
export FLASK_DEBUG=1
python unifi_webhook_server.py

# View detailed logs
tail -f /var/log/syslog | grep unifi-webhook
```

## 📝 Development

### Project Structure

```
MAAS_UniFi_RPi_Power_Manager/
├── unifi_webhook_server.py    # Main Flask application
├── requirements.txt           # Python dependencies
├── config.json.example       # Configuration template
├── install.yml               # Ansible playbook
├── inventory.example         # Ansible inventory template
├── group_vars/               # Ansible variables
├── templates/                # Systemd and env templates
└── CLAUDE.md                # Development guidance
```

### Adding New Features

1. Review the architecture in `CLAUDE.md`
2. Follow existing patterns in `unifi_webhook_server.py`
3. Update configuration examples as needed
4. Test with actual UniFi hardware

## 📄 License

This project is open source. Please ensure compliance with UniFi API terms of service.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Test changes with actual hardware
4. Submit a pull request

## 📞 Support

For issues related to:
- **UniFi API**: Check UniFi controller documentation
- **MAAS Integration**: Refer to MAAS power management documentation
- **This Project**: Open an issue in this repository