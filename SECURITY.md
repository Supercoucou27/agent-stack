# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in agent-stack, please report it by:

1. **Email:** Report via the repository security advisory system (if enabled) or contact the maintainer directly
2. **Preferred:** Use GitHub Security Advisories for responsible disclosure

Please do **NOT** create a public GitHub issue for security vulnerabilities.

### What to include

- Description of the vulnerability
- Steps to reproduce (minimal code/sample)
- Potential impact assessment
- Your environment (OS, Python version, etc.)

We will respond within 48 hours and provide a fix timeline.

---

## Threat Model

This stack is designed for **trusted local use** but assumes **untrusted network origins**:

- **Trusted:** All content served from the local dashboard (localhost)
- **Untrusted:** WebSocket connections, API origins, and any network communication
- **Assumption:** Local users may run untrusted code/plugins, but the host is generally trusted

### Attack Vectors Considered

| Vector | Risk | Mitigation |
|---|---|---|
| Malicious web page connecting to WS | HIGH | Origin allowlist + token authentication |
| Command injection via terminal | HIGH | Input sanitization + optional yolo mode |
| MCP server RCE | MEDIUM | Allowlist + trust levels |
| API token exposure | MEDIUM | Token in .env + query param only for JS |
| Port scanning (8082) | LOW | No external exposure by default |

## Security Controls

### Current (v1.0)

- WebSocket origin validation via `HERMES_ALLOWED_ORIGINS`
- WebSocket token authentication via `HERMES_WS_TOKEN`
- Command approval tiers (yolo mode)
- Structured logging with correlation IDs

### Planned

- TLS termination via reverse proxy (Nginx/Caddy)
- Rate limiting on API endpoints
- Audit logging for all terminal commands
- MCP server sandboxing (nsjail/container)

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| < 1.0   | No        |

## Change Process

1. Review security impact of all changes
2. Add tests for new security controls
3. Update this policy if needed
4. Document security-relevant changes in CHANGELOG

---

## Immediate Quick Wins

If you're deploying this stack:

1. **Set a strong WS token:** `export HERMES_WS_TOKEN=$(openssl rand -hex 32)`
2. **Restrict allowed origins:** `export HERMES_ALLOWED_ORIGINS=http://your-domain.com`
3. **Use a reverse proxy** (Nginx/Caddy) for TLS and rate limiting
4. **Never commit** `.env` files with production tokens
