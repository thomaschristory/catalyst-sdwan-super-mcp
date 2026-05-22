# Docker

## Build

```bash
docker build -t catalyst-sdwan-super-mcp .
```

The image is multi-stage and uses `uv` for fast deterministic installs.

## Run — stdio (for Claude Desktop)

```bash
docker run -i --rm \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -v "$(pwd)/specs:/app/specs" \
  catalyst-sdwan-super-mcp
```

The `-i` keeps stdin open so the MCP client can talk to the server over its standard streams.

## Run — SSE (network-accessible)

```bash
docker run -p 8000:8000 \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -v "$(pwd)/specs:/app/specs" \
  catalyst-sdwan-super-mcp \
  --transport sse --host 0.0.0.0 --port 8000
```

## docker-compose

```bash
docker compose up -d        # SSE on :8000
docker compose logs -f
```

## Specs are mounted, not baked in

Specs live in a volume so you can upgrade vManage versions without rebuilding the image:

```bash
docker run -v "$(pwd)/specs:/app/specs" ... --version 20.18
```
