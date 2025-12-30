"""Tylo Sauna integration constants."""

DOMAIN = "tylo_sauna"

# Config/option keys (entry.data / entry.options)
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NAME = "name"
CONF_GUID = "guid"
CONF_RELAXED_TELEMETRY = "relaxed_telemetry"

# Legacy / fallback control port candidate.
# Important: the controller's effective control/telemetry UDP port is dynamic and may change after reboot.
# We keep a historical/observed port here only as a last-resort probe candidate.
DEFAULT_CONTROL_PORT = 42156
UDP_DISCOVERY_PORTS = (54377, 54378)

# Timing (observed official app behavior)
KEEPALIVE_INTERVAL = 15  # seconds
ONLINE_TIMEOUT_S = 300   # consider online if a packet was received within the last N seconds


