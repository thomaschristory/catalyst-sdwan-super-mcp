# DevNet sandbox

Cisco DevNet provides a public **always-on SD-WAN sandbox** you can point this MCP at without needing your own vManage.

## Connection details

| Field | Value |
|---|---|
| Host | `sandbox-sdwan-2.cisco.com` |
| Port | `443` |
| Username | `devnetuser` |
| Password | `RG!_Yw919_83` |
| TLS verify | `false` (self-signed) |
| Auth | `use_jwt: true` (modern JWT endpoint) — fall back to `false` if Cisco rolls the sandbox back to an older release |

Reservation page: <https://developer.cisco.com/sdwan/sandbox/> — check the listed version there and pick the matching folder under `specs/`.

## Fastest path: no clone

The sandbox host and `active_version: "20.18"` are the built-in defaults, so a
one-liner with [`uvx`](https://docs.astral.sh/uv/) connects straight away:

```bash
VMANAGE_USERNAME=devnetuser VMANAGE_PASSWORD='RG!_Yw919_83' \
  VMANAGE_VERIFY_SSL=false uvx catalyst-sdwan-super-mcp
```

`VMANAGE_VERIFY_SSL=false` is required here because the sandbox serves a self-signed certificate (the table above) — the bundled `sdwan-mcp.yaml` sets it for the source-checkout flow, but a `uvx` run from an arbitrary directory has no config file, so pass it on the command line.

## From a source checkout

The repo's `sdwan-mcp.yaml` ships pointing at this host with `active_version: "20.18"`. Just set credentials in `.env`:

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

If the sandbox version differs from the default, override at the command line (drop the `uv run` prefix when using an installed CLI or `uvx catalyst-sdwan-super-mcp`):

```bash
uv run sdwan-mcp --version 20.15
```

## Heads-up

- The sandbox **warms up on demand**. The first request may take 30–60 seconds and return 503 while the lab spins up. Retry.
- It's shared. Treat it as read-mostly — don't break it for the next person.
- Sandbox versions drift over time. The bundled specs cover 20.15, 20.16, and 20.18; if DevNet rolls forward, add a new spec folder (see `specs/README.md`).
