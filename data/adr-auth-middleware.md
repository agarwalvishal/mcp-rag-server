# ADR-0042: Authentication Strategy for API Gateway

## Status

Accepted — 2026-01-15

## Context

Acme Corp's API gateway currently uses server-side session tokens stored in PostgreSQL. As we scale to support the mobile app and third-party integrations, we need an authentication strategy that:

- Works across multiple backend services without shared session storage
- Supports both first-party (web app, mobile app) and third-party API consumers
- Allows fine-grained permission scoping per client
- Doesn't require a database lookup on every request

The engineering team evaluated three approaches: sticky sessions with Redis, OAuth2 with opaque tokens, and JWT-based authentication with OAuth2 flows.

## Decision

We will adopt **JWT (JSON Web Tokens) with RS256 signing** as our primary authentication mechanism, using OAuth2 authorization code flow for first-party apps and client credentials flow for third-party integrations.

### Token Architecture

**Access tokens** are short-lived JWTs (15 minutes) containing:
- `sub`: User ID (UUID format)
- `aud`: Target service identifier
- `scope`: Space-separated permission list (e.g., `read:users write:orders`)
- `org_id`: Acme organization ID for multi-tenant isolation
- `jti`: Unique token ID for revocation checks

**Refresh tokens** are opaque tokens stored in PostgreSQL with a 30-day expiry. They are rotated on each use (rotation invalidates the previous token).

### Key Infrastructure Decisions

**Signing keys:** RS256 with 2048-bit RSA keys, rotated quarterly. Public keys are published at `/.well-known/jwks.json` so downstream services can verify tokens without calling the auth service.

**Token revocation:** We chose a hybrid approach. Access tokens are stateless (not checked against a revocation list) because their 15-minute lifetime limits the blast radius. Refresh tokens are checked against the database on every use. For immediate access token revocation (security incidents), we publish a short-lived blocklist to Redis that the API gateway checks — this adds ~2ms latency but only activates during incidents.

**Rate limiting:** Token endpoints are rate-limited at 10 requests/minute per client IP for auth flows, and 100 requests/minute for token refresh. Third-party clients have a separate quota of 1,000 API calls/hour, tracked by client ID in the JWT claims.

## Implementation Details

### Auth Service Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/authorize` | GET | OAuth2 authorization (redirects to login) |
| `/auth/token` | POST | Token exchange (code → tokens) |
| `/auth/refresh` | POST | Refresh token rotation |
| `/auth/revoke` | POST | Token revocation |
| `/auth/introspect` | POST | Token introspection (third-party) |
| `/.well-known/jwks.json` | GET | Public signing keys |

### Migration Path

Phase 1 (Sprint 23-24): Deploy auth service alongside existing session system. New mobile app uses JWT exclusively.

Phase 2 (Sprint 25-26): Migrate web app to JWT. Dual-auth period where both session cookies and JWTs are accepted.

Phase 3 (Sprint 27): Remove session-based auth. Decommission session tables.

### Security Considerations

- All tokens transmitted over HTTPS only (HSTS enforced)
- Refresh tokens bound to device fingerprint (prevents token theft across devices)
- JWT `iss` claim validated against allowlist of known auth service URLs
- CORS policy restricts token endpoints to registered origins
- Failed auth attempts trigger progressive delays (1s, 2s, 4s, 8s, max 30s)

## Consequences

### Positive

- **Stateless verification:** Services verify JWTs locally using cached public keys. No auth service dependency on the hot path.
- **Scalability:** No shared session store to scale. Each service independently verifies tokens.
- **Third-party support:** Standard OAuth2 flows work out of the box for API consumers.
- **Auditability:** JWT claims provide a complete authorization context for logging.

### Negative

- **Token size:** JWTs are larger than session IDs (~800 bytes vs ~32 bytes). Adds bandwidth overhead.
- **Revocation delay:** Up to 15 minutes before a revoked access token expires naturally. Mitigated by the Redis blocklist for emergencies.
- **Key management:** RSA key rotation requires coordination across all services. Mitigated by JWKS endpoint and key overlap periods.
- **Complexity:** More moving parts than simple session auth. Requires team training on OAuth2 flows and JWT security best practices.

### Risks

- If the JWKS endpoint goes down, services fall back to cached keys. If keys have rotated and cache is stale, auth fails. Mitigation: cache TTL of 24 hours, multiple JWKS endpoint replicas.
- Clock skew between services could cause premature token rejection. Mitigation: 30-second leeway on `exp` and `nbf` claims.

## Participants

- **Decision maker:** Sarah Chen, Principal Engineer
- **Consulted:** API Platform team, Security team, Mobile team
- **Informed:** All backend engineering teams

## References

- Internal RFC: "Acme Auth Gateway v2" (Confluence, Jan 2026)
- Acme threat model: "API Authentication Surface" (Security Wiki)
