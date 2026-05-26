# Authentication

vManage uses **credential-based authentication**, not bearer API tokens. Two flows are supported.

## JWT (default, vManage 20.18.1+)

Single login call returns a JWT and an XSRF token:

```
POST /j_security_check
Content-Type: application/x-www-form-urlencoded

j_username=admin&j_password=...

→ 200 OK
{
  "token": "...",
  "xsrfToken": "...",
  "expiresIn": 1800
}
```

All subsequent requests:

```
Authorization: Bearer {token}
X-XSRF-TOKEN: {xsrfToken}
```

The token is refreshed proactively when it's within 2 minutes of expiry, and reactively on 401.

## Session (legacy, vManage < 20.18.1)

Two calls:

```
POST /j_security_check  → Set-Cookie: JSESSIONID=...
GET  /dataservice/client/token  (with JSESSIONID)  → plain-text XSRF token
```

All subsequent requests:

```
Cookie: JSESSIONID={id}
X-XSRF-TOKEN: {xsrfToken}
```

Session expiry shows up as a `302` redirect to `welcome.html` — the dispatcher detects this, re-authenticates, and retries the original call exactly once.

## Switching

```yaml
vmanage:
  use_jwt: true     # default
```

The shipped `sdwan-mcp.yaml` ships `use_jwt: true` (matches the bundled `20.18` default). If you point at an older vManage that doesn't expose the JWT endpoint, set `use_jwt: false` to force the session flow.

## Logout

`sdwan-mcp` calls `POST /logout` cleanly on shutdown to free the server-side session.
