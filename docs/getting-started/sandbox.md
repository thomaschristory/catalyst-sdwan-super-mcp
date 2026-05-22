# DevNet sandbox

Cisco DevNet provides a public **always-on SD-WAN sandbox** you can point this MCP at without needing your own vManage.

## Connection details

| Field | Value |
|---|---|
| Host | `sandbox-sdwan-2.cisco.com` |
| Port | `443` |
| Version | `20.10` |
| Username | `devnetuser` |
| Password | `RG!_Yw919_83` |
| TLS verify | `false` (self-signed) |
| Auth | `use_jwt: false` (20.10 doesn't ship the modern JWT endpoint) |

Reservation page: <https://developer.cisco.com/sdwan/sandbox/>.

## Config

The repo's `config.yaml` ships with these values already. Just set credentials in `.env`:

```bash
cp .env.example .env
cat >> .env <<'EOF'
VMANAGE_USERNAME=devnetuser
VMANAGE_PASSWORD=RG!_Yw919_83
EOF
```

Then:

```bash
uv run sdwan-mcp
```

## Heads-up

- The sandbox **warms up on demand**. The first request may take 30-60 seconds and return 503 while the lab spins up. Retry.
- It's shared. Treat it as read-mostly — don't break it for the next person.
- It's pinned to 20.10. Newer features won't be testable here.
