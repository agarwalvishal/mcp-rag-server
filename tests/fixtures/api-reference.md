# Acme Corp User Service API Reference

**Base URL:** `https://api.acme-corp.internal/v2`
**Authentication:** Bearer token (JWT) — see ADR-0042 for token format
**Content-Type:** `application/json`
**API Version:** 2.4.1 (released 2026-02-20)

## Authentication

All endpoints require a valid JWT in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

Tokens are obtained via the auth service at `https://auth.acme-corp.internal`. See the [Auth Middleware ADR](adr-auth-middleware.md) for details on token architecture.

**Required scopes** are listed per endpoint below. Requests with insufficient scopes receive a `403 Forbidden` response.

## Rate Limits

| Client type | Limit | Window | Header |
|-------------|-------|--------|--------|
| First-party (web/mobile) | 500 req | per minute | `X-RateLimit-Remaining` |
| Third-party | 1,000 req | per hour | `X-RateLimit-Remaining` |
| Admin | 2,000 req | per minute | `X-RateLimit-Remaining` |

When rate limited, the API returns `429 Too Many Requests` with a `Retry-After` header (seconds).

## Error Codes

Acme uses standard HTTP status codes plus custom error codes in the response body:

| HTTP Status | Error Code | Meaning |
|-------------|-----------|---------|
| 400 | `INVALID_INPUT` | Request body failed validation |
| 400 | `ACME_DUPLICATE_EMAIL` | Email already registered in this org |
| 401 | `TOKEN_EXPIRED` | JWT access token has expired |
| 401 | `TOKEN_INVALID` | JWT signature verification failed |
| 403 | `INSUFFICIENT_SCOPE` | Token lacks required scope |
| 403 | `ORG_MISMATCH` | Token org_id doesn't match resource |
| 404 | `USER_NOT_FOUND` | No user with this ID in the org |
| 409 | `ACME_CONFLICT_VERSION` | Optimistic locking conflict (stale `version`) |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Unexpected server error |

Error response format:
```json
{
  "error": {
    "code": "ACME_DUPLICATE_EMAIL",
    "message": "A user with email alice@example.com already exists in org acme-prod",
    "request_id": "req_7f3a2b1c"
  }
}
```

## Endpoints

### List Users in Acme Organization

```
GET /users
```

Returns a paginated collection of Acme user accounts within the caller's organization. Users are scoped to the `org_id` claim in the JWT token — you can only retrieve users belonging to your own Acme organization.

**Scope:** `read:users`

**Query parameters for Acme user filtering:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | 1 | Page number (1-indexed) |
| `per_page` | integer | 20 | Acme users per page (max 100) |
| `status` | string | `active` | Filter by Acme account status: `active`, `inactive`, `invited`, `all` |
| `role` | string | — | Filter by Acme role: `admin`, `member`, `viewer` |
| `search` | string | — | Full-text search on Acme user name and email |
| `sort` | string | `created_at` | Field to order Acme users by: `name`, `email`, `created_at`, `last_login` |
| `order` | string | `desc` | Result ordering direction: `asc` or `desc` |

**Response:** `200 OK`
```json
{
  "data": [
    {
      "id": "usr_8a3f2b1c",
      "email": "alice@acme-corp.com",
      "name": "Alice Chen",
      "role": "admin",
      "status": "active",
      "org_id": "org_acme_prod",
      "last_login": "2026-02-19T14:30:00Z",
      "created_at": "2025-06-15T09:00:00Z",
      "version": 3
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 142,
    "total_pages": 8
  }
}
```

### Get User

```
GET /users/{user_id}
```

**Scope:** `read:users`

**Response:** `200 OK` — single user object (same shape as list item)

### Create User

```
POST /users
```

**Scope:** `write:users`

**Request body:**
```json
{
  "email": "bob@acme-corp.com",
  "name": "Bob Martinez",
  "role": "member",
  "send_invite": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | yes | Must be unique within the org |
| `name` | string | yes | Display name (2-100 chars) |
| `role` | string | no | Default: `member`. Options: `admin`, `member`, `viewer` |
| `send_invite` | boolean | no | Default: `true`. Send onboarding email |
| `metadata` | object | no | Arbitrary key-value pairs (max 10 keys, 256 chars per value) |

**Response:** `201 Created` — the created user object

**Errors:** `ACME_DUPLICATE_EMAIL` if email exists in the org.

### Update User

```
PATCH /users/{user_id}
```

**Scope:** `write:users`

Uses **optimistic locking**: include the current `version` field from the user object. If another update happened since you read it, you'll get `ACME_CONFLICT_VERSION`.

**Request body:**
```json
{
  "name": "Robert Martinez",
  "role": "admin",
  "version": 3
}
```

**Response:** `200 OK` — updated user object with incremented `version`

### Delete User

```
DELETE /users/{user_id}
```

**Scope:** `admin:users`

Soft-deletes the user (sets status to `inactive`). User data is retained for 90 days per Acme's data retention policy, then permanently purged.

**Response:** `204 No Content`

### Bulk Import Users

```
POST /users/import
```

**Scope:** `admin:users`

Asynchronous endpoint for importing users from CSV. Returns a job ID for polling.

**Request:** `multipart/form-data` with a CSV file (max 10MB, max 5,000 rows)

CSV format:
```
email,name,role
alice@example.com,Alice Chen,admin
bob@example.com,Bob Martinez,member
```

**Response:** `202 Accepted`
```json
{
  "job_id": "job_9c4d3e2f",
  "status": "processing",
  "poll_url": "/users/import/job_9c4d3e2f"
}
```

### User Activity Log

```
GET /users/{user_id}/activity
```

**Scope:** `read:audit`

Returns the user's activity log for compliance and debugging.

**Query parameters:**
- `since`: ISO 8601 timestamp (default: 30 days ago)
- `until`: ISO 8601 timestamp (default: now)
- `action`: Filter by action type: `login`, `api_call`, `settings_change`, `export`

**Response:** `200 OK`
```json
{
  "data": [
    {
      "timestamp": "2026-02-19T14:30:00Z",
      "action": "login",
      "ip": "203.0.113.42",
      "user_agent": "AcmeMobile/2.1 iOS/17.3",
      "details": {"method": "oauth2", "mfa": true}
    }
  ]
}
```

## Webhooks

Acme User Service sends webhooks for user lifecycle events. Configure webhook URLs in the admin dashboard.

**Events:**
- `user.created` — new user added
- `user.updated` — user profile or role changed
- `user.deleted` — user soft-deleted
- `user.login` — user authenticated (first-party only)

**Payload format:**
```json
{
  "event": "user.created",
  "timestamp": "2026-02-20T10:15:00Z",
  "data": { /* user object */ },
  "webhook_id": "wh_1a2b3c4d"
}
```

Webhooks include an `X-Acme-Signature` header (HMAC-SHA256 of the payload using your webhook secret) for verification.

## SDK Support

Official SDKs: Python (`acme-sdk`), TypeScript (`@acme-corp/sdk`), Go (`github.com/acme-corp/sdk-go`).

```python
from acme_sdk import AcmeClient

client = AcmeClient(token="your_jwt_token")
users = client.users.list(status="active", role="admin")
```
