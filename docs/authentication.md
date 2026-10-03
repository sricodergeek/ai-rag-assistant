# Authentication and Authorization

## 1. Overview

The backend authenticates people through Google OpenID Connect (OIDC), then issues its own opaque Redis-backed session. PostgreSQL stores application users and document ownership. The browser holds only HTTP cookies and safe profile information; Google tokens are not returned to it.

**Authentication** answers “who is making this request?” `get_current_user` resolves the application session to a PostgreSQL `User`. **Authorization** answers “what may this user access?” `/upload` assigns ownership, and `/ask` checks it before accessing a conversation or performing retrieval.

## 2. Authentication architecture

```text
Google OIDC → verified identity → PostgreSQL User
                                      ↓
Browser ← opaque session cookie ← Redis session
   ↓                                  ↓
get_current_user() → authenticated User → ownership checks
```

PostgreSQL is the source of truth for users, documents, conversations, and messages. Redis stores short-lived OAuth transactions and application session mappings. The in-memory React user value contains safe profile fields only; it is not a credential.

## 3. Google OAuth flow

The backend starts an OAuth authorization-code flow, exchanges the returned code server-to-server, verifies the ID token, finds or creates the application user, creates a Redis session, and redirects to `FRONTEND_URL`.

```text
Browser            Backend             Redis             Google
   | GET /auth/google |                   |                  |
   |----------------->| create transaction|                  |
   |                  |------------------>| state, nonce     |
   |<-- 302 Google ---| Set transaction cookie               |
   |--------------------------------------------------------->|
   |<---------------- redirect with code and state -----------|
   | GET callback + transaction cookie                        |
   |----------------->| GETDEL transaction |                  |
   |                  | compare state; exchange code -------->|
   |                  |<--------- token response -------------|
   |                  | verify ID token + nonce               |
   |                  | find/create User; create session      |
   |<-- 302 frontend; clear transaction cookie; set session -|
```

The token exchange uses the configured `GOOGLE_REDIRECT_URI`; the final application redirect uses `FRONTEND_URL`. Tokens are used in backend memory for verification and are not saved in PostgreSQL or Redis.

**Threat addressed:** forged or replayed authorization responses and exposure of Google credentials. The code exchange and token verification remain server-side. **Not stored in browser:** access, refresh, or ID tokens, client secret, user ID, or Google subject. OAuth `state` and OIDC `nonce` are transient authorization query parameters, not browser storage or cookie values.

## 4. OAuth state and browser-bound transaction

The backend creates random state, nonce, and transaction ID values. Redis stores state and nonce under `oauth_transaction:<transaction_id>` for 10 minutes. Redis `GETDEL` consumes the record atomically. The callback requires the transaction cookie, consumes the server-side record, and compares returned state exactly before exchanging the code.

**Threat addressed:** OAuth response forgery, login CSRF, and replay. Binding the one-time transaction to an HttpOnly cookie in the initiating browser prevents a callback URL from another browser’s login from being accepted. Missing, expired, reused, or mismatched transactions return HTTP 400 before user or session creation.

The temporary `oauth_transaction` cookie contains **only the opaque transaction ID**. It uses `HttpOnly`, `SameSite=Lax`, `Path=/`, a 10-minute maximum age, and the same `SESSION_COOKIE_SECURE` setting as the session cookie. It is cleared on successful login.

## 5. OIDC nonce validation

`nonce` is distinct from OAuth `state`: state correlates the authorization response with the browser’s login transaction; nonce correlates the ID token with that transaction. The nonce is sent in the Google authorization request and retained in Redis, not the cookie. After token verification, the backend requires the ID token’s nonce claim to equal the expected nonce.

**Threat addressed:** ID-token substitution or replay into a different login transaction. A mismatch rejects login before application-user or session creation. The nonce itself is not persisted in browser storage or returned to the frontend.

## 6. Google ID-token verification

`verify_google_id_token` uses Google’s authentication library to verify the token signature and expiration, checks the configured client ID audience, and explicitly checks Google’s issuer. It returns only `sub`, `email`, and optional `name` and `picture`; it does not return the raw token or nonce.

**Threat addressed:** accepting forged, expired, incorrectly issued, or incorrectly targeted identity claims. Signature verification uses Google’s signing keys through the official library. Email verification status and `azp` are not currently checked; see planned work.

## 7. PostgreSQL User model and identity lookup

The `users` table stores a generated UUID, unique email, optional name/avatar URL, auth provider, provider user ID, and creation time. Google users are looked up by `(auth_provider="google", provider_user_id=sub)`, not by email. A unique constraint backs this identity key. On an insert race, the helper rolls back and re-queries by provider and subject.

**Threat addressed:** duplicate or incorrectly linked application identities. Google `sub` is the stable identity key; email is profile data. No password or password hash is stored. No user information is placed in either cookie.

## 8. Redis application sessions

After successful identity verification and user lookup, the backend generates an opaque random session ID and stores `session:<session_id> → <user_id>` in Redis for seven days. The ID does not encode the user ID or other identity data. Redis contains no Google tokens.

**Threat addressed:** client-side exposure of identity credentials and unauthorized access without a valid session. A missing or expired Redis entry is unauthenticated. Redis currently uses the local development instance; production Redis transport and access controls remain to be configured.

## 9. HttpOnly session cookie

The application cookie is named `session_id` and contains only the opaque Redis session ID. It is `HttpOnly`, `SameSite=Lax`, `Path=/`, and has a seven-day maximum age. No `Domain` is set, so it is host-only. Local development defaults `SESSION_COOKIE_SECURE` to false so the cookie works over localhost HTTP. In production, `SESSION_COOKIE_SECURE=true` must be explicitly configured; a missing, false, or invalid value causes backend configuration to fail at startup. Both the application session cookie and OAuth transaction cookie use this setting.

**Deployment boundary:** the backend does not provide HTTPS or TLS. Production must terminate TLS through the deployment infrastructure; the Secure cookie flag only restricts cookie transmission to HTTPS and does not create an HTTPS connection. The `__Host-` cookie prefix remains a possible later hardening option.

**Threat addressed:** JavaScript reading the session ID (`HttpOnly`), unnecessary subdomain sharing (host-only), and some cross-site cookie sending (`SameSite`). `SameSite` is defense in depth, not a substitute for general CSRF protection. The cookie contains no user ID, email, Google subject, or Google token.

## 10. `/auth/me`

`GET /auth/me` uses `get_current_user` and returns only `id`, `email`, `name`, and `avatar_url`. Missing/expired sessions or missing users return a generic 401. It does not return session IDs, provider identifiers, tokens, or database details.

**Threat addressed:** identifying the current application user without exposing credentials or provider internals. The frontend stores only this safe profile in React state.

## 11. `/auth/logout`

`POST /auth/logout` does not require a currently valid user. If a session cookie is present, it deletes that Redis key; it then expires the `session_id` cookie. With no cookie, it still succeeds, making logout idempotent. Redis deletion failure returns a generic server error.

**Threat addressed:** continued use of a browser session after logout. No session ID or token is included in the response. Logout is still a cookie-authenticated state-changing endpoint and is not covered by a general CSRF token today.

## 12. `get_current_user`

This dependency reads `session_id`, resolves its value in Redis, parses the stored user UUID, and loads the PostgreSQL user. Missing or invalid sessions return 401; Redis/database failures return generic errors.

**Threat addressed:** unauthenticated requests reaching protected route logic. Authentication is separate from route-specific authorization: this dependency identifies the user but does not itself decide document access.

## 13. Document ownership and authorization

`Document.user_id` is a required UUID foreign key to `users.id`. `/upload` requires an authenticated user, assigns that user as owner, and uses one document UUID for PostgreSQL and Chroma ingestion. A new document cannot be ownerless.

`/ask` requires authentication, loads the requested PostgreSQL document, and compares its owner to the authenticated user before conversation lookup, message persistence, embeddings, Chroma retrieval, or answer generation. Missing and unauthorized documents return the same generic 404. Conversations belong to documents through `conversations.document_id`; ownership is derived through that document. A conversation associated with a different document also returns the generic 404 before messages or RAG are accessed.

**Threat addressed:** cross-user document, conversation-history, and retrieval access. User IDs and ownership are checked server-side; possession of a document or conversation ID alone grants no access.

## 14. Frontend authentication gate

On startup, React calls `/auth/me` with credentials included and shows a loading screen until the check finishes. An authenticated user sees the existing chat/upload UI and safe profile/logout controls. Otherwise, a dedicated login screen is shown; chat and upload controls are not rendered. Login uses browser navigation to the backend OAuth start endpoint. After the callback redirect, the app reloads and checks `/auth/me` again.

**Threat addressed:** presenting the application as authenticated before checking the server session. The browser does not store session IDs or Google tokens in React state, local storage, or session storage.

## 15. Development vs production configuration

Backend configuration provides `FRONTEND_URL` (default `http://localhost:5173`), `API_URL` (default `http://localhost:8000`), and `ENVIRONMENT` (default `development`). The OAuth callback and CORS allowlist use `FRONTEND_URL`; Google’s callback remains configured separately with `GOOGLE_REDIRECT_URI`. The frontend and Vite proxy use `VITE_API_URL`, documented with the localhost default in `frontend/.env.example`. The frontend build uses `VITE_API_URL` directly; the backend `API_URL` setting is currently informational and is not used to configure routes or CORS.

The Vite development proxy keeps browser API requests on the frontend origin. CORS also allows exactly the origin parsed from `FRONTEND_URL`, with credentials enabled, so credentialed browser requests can work if the frontend calls the API directly. `/auth/me`, `/auth/logout`, `/ask`, and `/upload` send cookies with `credentials: "include"`. For production, set `FRONTEND_URL` to the exact HTTPS frontend origin and `VITE_API_URL` to the API endpoint. Prefer same-site HTTPS frontend/API hosts when they are separate origins, because the session cookie is `SameSite=Lax` and Fetch Metadata rejects cross-site state-changing requests. The backend does not provide TLS; HTTPS must be configured by the deployment infrastructure. These settings are not a claim of production readiness.

## 16. Current CSRF posture

The session cookie uses `SameSite=Lax`. The localhost frontend (`localhost:5173`) and backend (`localhost:8000`) are **different origins** because their ports differ, but are **same-site** because they use the same scheme and hostname. The Vite proxy makes normal development API calls same-origin from the browser.

`POST /upload`, `/ask`, and `/auth/logout` validate a present `Origin` header against the origin parsed from configured `FRONTEND_URL`. A mismatch is rejected with HTTP 403; the comparison is exact. CORS uses the same exact frontend origin and enables credentialed browser requests; it does not replace this route check. These endpoints also validate the Fetch Metadata `Sec-Fetch-Site` header. It tells the backend whether a browser considers a request `same-origin`, `same-site`, `cross-site`, or `none` (such as a direct navigation context). The backend allows `same-origin`, `same-site`, and `none`; it rejects `cross-site` and unknown values with HTTP 403.

Fetch Metadata is useful CSRF defense-in-depth because browsers attach it to describe the request context, allowing the backend to reject clearly cross-site requests. It does not replace exact Origin validation, which remains enabled. **Requests without an `Origin` or `Sec-Fetch-Site` header are currently allowed** for compatibility with non-browser clients. Such clients provide no browser-origin/context signal, so this remains a CSRF limitation. OAuth state protects the OAuth callback transaction, not these application endpoints.

No general CSRF token or Referer validation has been implemented. SameSite helps with some cross-site requests but is not general CSRF protection. CORS controls whether browser JavaScript may read/use cross-origin responses under browser rules; it is separate from authentication and is not a substitute for CSRF protection because some cross-origin requests can still be sent.

Production CSRF protection still needs to be finalized based on whether the deployed frontend/API will be same-origin or cross-origin and which cookie policy that architecture requires. No general CSRF token is added in this implementation.

## 17. Security improvements still planned

- Finalize CSRF protections for state-changing cookie-authenticated routes, including handling requests without Origin or Fetch Metadata and deciding whether to add a CSRF token.
- Configure production HTTPS, `Secure` cookies, exact frontend/API URLs, and verify credentialed same-site deployment behavior.
- Decide whether to enforce Google `email_verified` and validate `azp` for multi-audience tokens.
- Harden production Redis access with private networking, authentication, and encrypted transport.
- Review session lifetime, revocation needs, rate limiting, and whether to add PKCE.

## Next Security Work

1. Decide whether production should reject missing Origin/Fetch Metadata or add a CSRF token for state-changing authenticated endpoints.
2. Complete production HTTPS, secure-cookie, URL, credential, and CORS configuration.
3. Add the planned Google claim checks and production Redis controls.
