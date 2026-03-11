# Excel MCP Setup (Codex CLI)

This guide covers two Excel MCP server options:
- Python: `haris-musa/excel-mcp-server`
- TypeScript: `sbraind/excel-mcp-server`

## Prerequisites

- Codex CLI installed (`codex --help` works)
- For Python server: `uv`/`uvx` available
- For TypeScript server: Node.js 18+ and `npm`

---

## Option A: Python MCP Server (`haris-musa/excel-mcp-server`)

Repository: <https://github.com/haris-musa/excel-mcp-server>

### Install / run (stdio, local)

```bash
uvx excel-mcp-server stdio
```

### Optional: run as streamable HTTP

```bash
EXCEL_FILES_PATH=/absolute/path/to/excel_files FASTMCP_PORT=8007 uvx excel-mcp-server streamable-http
```

Notes:
- For `stdio`, file paths are passed per tool call.
- For HTTP/SSE modes, set `EXCEL_FILES_PATH` on the server side.

---

## Option B: TypeScript MCP Server (`sbraind/excel-mcp-server`)

Repository: <https://github.com/sbraind/excel-mcp-server>

### Install from source

```bash
git clone https://github.com/sbraind/excel-mcp-server.git
cd excel-mcp-server
npm install
npm run build
```

### Run (stdio)

```bash
node dist/index.js
```

---

## Codex CLI MCP Integration

Codex stores MCP servers in `~/.codex/config.toml`, but these JSON snippets are useful as canonical server definitions.

### JSON example: Python server over stdio

```json
{
  "mcpServers": {
    "excel-python": {
      "command": "uvx",
      "args": ["excel-mcp-server", "stdio"]
    }
  }
}
```

### JSON example: Python server over HTTP

```json
{
  "mcpServers": {
    "excel-python-http": {
      "url": "http://localhost:8007/mcp"
    }
  }
}
```

### JSON example: TypeScript server over stdio

```json
{
  "mcpServers": {
    "excel-ts": {
      "command": "node",
      "args": ["/absolute/path/to/excel-mcp-server/dist/index.js"]
    }
  }
}
```

### Add the same servers with Codex CLI commands

```bash
# Python stdio
codex mcp add excel-python -- uvx excel-mcp-server stdio

# Python HTTP
codex mcp add excel-python-http --url http://localhost:8007/mcp

# TypeScript stdio
codex mcp add excel-ts -- node /absolute/path/to/excel-mcp-server/dist/index.js
```

If you need env vars for a stdio server entry:

```bash
codex mcp add excel-python --env EXCEL_FILES_PATH=/absolute/path/to/excel_files -- uvx excel-mcp-server stdio
```

---

## Verification Commands

### 1) Verify server binaries

```bash
uvx excel-mcp-server --help
node /absolute/path/to/excel-mcp-server/dist/index.js --help
```

### 2) Verify Codex registered servers

```bash
codex mcp list
codex mcp get excel-python
codex mcp get excel-ts
```

### 3) Verify startup manually

```bash
# Should start without immediate crash
uvx excel-mcp-server stdio
# or
node /absolute/path/to/excel-mcp-server/dist/index.js
```

---

## Troubleshooting

### `uvx: command not found`

Install `uv` first, then retry:

```bash
pip install uv
# or: pipx install uv
```

### `Cannot find module .../dist/index.js` (TypeScript server)

You likely skipped build or path is wrong.

```bash
cd /absolute/path/to/excel-mcp-server
npm install
npm run build
ls dist/index.js
```

### Codex shows no MCP tools

- Confirm server is registered: `codex mcp list`
- Inspect one entry: `codex mcp get <name>`
- Restart Codex session after adding/removing servers
- Recheck command path and executable availability in the same shell

### HTTP mode cannot access files (Python server)

Set `EXCEL_FILES_PATH` where the server process runs:

```bash
EXCEL_FILES_PATH=/absolute/path/to/excel_files uvx excel-mcp-server streamable-http
```

### Port already in use (Python HTTP/SSE)

Set a different port:

```bash
FASTMCP_PORT=8010 uvx excel-mcp-server streamable-http
```

### Permission denied for workbook paths

- Use absolute paths for Excel files
- Ensure current OS user can read/write those directories
- For TypeScript server, consider restricting/allowing directories via server config if enabled
