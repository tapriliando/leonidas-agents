"""
Heartbeat layer (OpenClaw-inspired cadence).

- **Transport tick** and **agent heartbeat** broadcasts are implemented on
  `app.gateway.hub.GatewayHub` (`run_tick_loop`, `run_agent_heartbeat_loop`).
- Interval defaults: see `app.gateway.protocol` (`TICK_INTERVAL_MS`,
  `HEARTBEAT_AGENT_INTERVAL_SEC_DEFAULT`). Override agent cadence with env
  `HEARTBEAT_AGENT_INTERVAL_SEC`.
"""
