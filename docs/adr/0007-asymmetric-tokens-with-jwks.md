# 0007. Asymmetric access tokens, verified through JWKS

Status: accepted

## Context

Five services must authenticate requests and know the caller's organization and role. Calling the identity service on every request would make it a bottleneck and a single point of failure.

## Decision

The identity service signs short-lived access tokens (15 minutes) with Ed25519. Its private key is created on first start, stored encrypted in `identity_db`, and never leaves the service. Other services fetch public keys from `/.well-known/jwks.json`, cache them, and verify tokens locally.

Refresh tokens live in an `httpOnly`, `SameSite=Strict` cookie, are stored only as hashes, and rotate on each use. Replaying a used token revokes its whole family. The browser keeps the access token in memory only.

Service-to-service calls to `/internal/*` use a shared token, stay scoped to one organization, and are not routed by the ingress.

## Alternatives

- **HS256 with a shared secret.** Simple, but any service could forge a token for any organization.
- **Opaque tokens with introspection.** Immediate revocation, at the cost of a call to identity on every request.
- **Mutual TLS between services.** Stronger than a shared token, and better provided by a service mesh. See ADR 0009.

## Consequences

- A compromised service cannot mint tokens.
- A revoked user keeps access until their token expires, for up to 15 minutes.
- Key rotation works through `kid`, but the rotation procedure itself is not yet automated.
- The internal token is one shared secret. Its blast radius is limited by NetworkPolicy, and it is listed in [docs/risks.md](../risks.md).
