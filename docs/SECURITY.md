# Security and data handling

This page says what the product protects, how, and what it does not yet do. The behaviour described here is covered by
tests unless marked otherwise.

## Sign-in

People sign in with an account they already have at an identity provider such as Google, AWS Cognito, Microsoft Entra
or Auth0. The product never sees or stores a password. It uses the OpenID Connect authorization-code flow.

| Check | What it prevents |
|---|---|
| A random `state` bound to the attempt, used once, valid for 10 minutes | Someone else completing your sign-in |
| PKCE with S256 | A stolen authorization code being redeemed |
| A `nonce` that must appear in the identity token | Replaying an old token |
| RS256 or ES256 signatures, checked against the provider's published keys | Forged tokens, `alg: none`, and HMAC key-confusion tokens |
| `iss`, `aud`, `exp` and `sub` checks, with 60 seconds of clock leeway | Tokens meant for another app or an expired token |
| The provider must report the email as verified | Claiming an invited address you do not own |
| The post-login redirect must be a path on this site | Open redirects |
| The issuer must use https, except on localhost | Downgrade attacks |

The tests drive all of this against a test identity provider that can mint broken tokens on purpose. The real Google or
Cognito flow has not been run, because that needs a client registered in your account.

## Sessions

After sign-in the server creates a random 256-bit token and stores only its SHA-256 hash. The cookie is `HttpOnly`,
`SameSite=Lax`, `Secure` in production, and named `__Host-marga_session`, which stops other subdomains from setting it.
A session ends after 2 hours without use or 12 hours after sign-in, and immediately when the person is removed from
the organisation or signs out. Settings: `MARGA_SESSION_IDLE_MINUTES`, `MARGA_SESSION_MAX_HOURS`.

Every request that changes data must carry the header `X-Requested-With: marga`, and any `Origin` header must match the
site. A page on another site cannot add that header to a request that carries your cookie.

## Who can do what

| Role | Can |
|---|---|
| viewer | See the map, inventory, orders, team and closures |
| member | Also plan deliveries and use the AI features |
| admin | Also manage inventory, closures, settings and invitations to member or viewer, and read the audit log |
| owner | Also change roles, invite admins and owners, and delete the organisation |

An organisation always keeps at least one owner. Admins cannot remove other admins or owners. The browser hides what a
role cannot do, and the server refuses it regardless.

## Organisations are separate

Every table that holds business data has an organisation id. The server reads it from the signed-in session, never from
the request, so no URL, header or body field can point at another organisation. Another organisation's result id returns
404, the same as a missing one. The tests create two organisations and try to read and change each other's warehouses,
orders, closures, team and audit log. Road closures work per organisation through a read-only view of the shared graph,
so one customer's closures never change another's routes.

I broke the tenant filter, the session expiry and the CSRF check on purpose, and the tests failed each time.

## Joining

A new person can join only through an invitation sent to their verified email address, as the bootstrap owner (the very
first organisation only), or when `MARGA_ALLOW_SELF_SIGNUP=true`. Invitations last 14 days and are used once. You can
also restrict sign-in to named email domains. People are matched by the provider's account id, not their email, so a
second provider account with the same email does not inherit access.

## Personal data

| Stored | Why | Removed |
|---|---|---|
| Name and verified email | Show who did what, match invitations | Account deletion |
| Provider name for the account (`issuer`, `subject`) | The real identity key | Account deletion |
| Role and organisation | Authorisation | Account deletion |
| Orders you ran: destination, quantity, deadline, outcome | History for the organisation | Detached from you on account deletion, deleted after 365 days |
| Audit entries: action, target, time | Accountability | Detached from you on account deletion, deleted after 24 months |
| Session token hash | Keep you signed in | Sign-out, expiry, removal |

Not stored: passwords, provider access or refresh tokens, IP addresses, browser fingerprints. Audit entries about
people who have no account (for example a refused sign-in) keep only a short hash of the email.

On the Account page anyone can download everything stored about them as JSON and delete their account. Owners can delete
the organisation, which removes all of its data. The last person leaving an organisation removes its data too.
Retention periods are set by `MARGA_ORDER_RETENTION_DAYS` and `MARGA_AUDIT_RETENTION_DAYS` and applied at start-up and hourly.

## Other protections

* Rate limits per signed-in person: 30 plans, 10 typed-order reads and 20 AI steps per minute. Sign-in is limited per
  client address, and behind the load balancer the real address is the last `X-Forwarded-For` entry.
* Response headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and `Cache-Control: no-store`
  on everything except the map layer.
* Uploads are capped at 2 MB and 5,000 rows, and an import is all or nothing.
* The public gateway forwards only listed routes. The AI model is also not trusted with numbers, see ARCHITECTURE.md.
* The AI key and the database password live in AWS Secrets Manager. The database is private and encrypted.

## Known gaps

* Rate limiting is per process. With several tasks, add AWS WAF rate rules for a hard limit.
* There is no schema migration tool yet. Tables are created at start-up. Add Alembic before changing a live schema.
* No content-security-policy header is set on the web pages yet.
* No multi-factor control of our own. It comes from the identity provider, so enable it there.
* Sign-out ends the session here but does not sign you out of the identity provider.
* Orders and audit entries are not encrypted per column. Protection at rest is the database's own encryption.
* No penetration test, no formal GDPR or Nepali privacy-law review, and no data processing agreement template. Do those
  before handling real customer data.
