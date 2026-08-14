# Account connection UX assessment

**Date:** 2026-08-14  
**Status:** Recommendation; no implementation in this assessment

## Executive decision

SongMirror can make account connection substantially easier, but a normal web page
cannot implement the proposed “login in an iframe and extract the cookies/network
request” flow. The browser intentionally prevents one origin from inspecting another
origin's document, storage, and private network data. A popup fixes the login usability
but does not remove that security boundary.

Use two connection paths instead:

1. **Official authorization redirect/popup where the provider makes it available.**
   This is the best experience and security model. TIDAL can use this path now. Deezer
   can use it for an existing/approved application if its developer access is usable.
   Qobuz requires Qobuz-issued application credentials, and Amazon Music's official Web
   API remains a closed beta.
2. **An optional, user-installed SongMirror Connect helper for the existing web-player
   compatibility connectors.** A WebExtension is the best first implementation. It can
   open a provider in a normal top-level tab, wait while the user signs in, capture only
   the small allowlisted credential set SongMirror already accepts, and send that set
   directly to the user's SongMirror backend. A local Playwright-managed browser is a
   useful fallback when the browser and SongMirror process run on the same computer.

Keep manual paste as a fallback. Do not market the helper path as OAuth, and do not
capture passwords, keystrokes, complete cookie jars, complete HAR files, or playback
resources.

## Why an iframe or ordinary popup cannot do it

The web platform's [same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)
gives a cross-origin parent only a very small interface to an iframe or popup. It cannot
read the provider document, storage, or most `Window` properties. Cross-origin
cooperation through `postMessage` is possible, but the provider would have to implement
and intentionally send the required result; SongMirror cannot add that cooperation from
its own origin.

There are additional independent barriers:

- Provider pages can refuse embedding with CSP `frame-ancestors` or
  `X-Frame-Options`. The [CSP `frame-ancestors` documentation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/frame-ancestors)
  describes this as an ancestor allowlist. OAuth security guidance goes further:
  authorization servers **must** prevent clickjacking and should protect login and
  authorization pages from unauthorized framing
  ([RFC 9700 section 4.16](https://www.rfc-editor.org/rfc/rfc9700.html#name-clickjacking)).
- JavaScript cannot read an `HttpOnly` cookie, even when the script is on the cookie's
  own origin ([MDN cookie guidance](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies#block_access_to_your_cookies)).
  SongMirror's origin also cannot read another origin's non-`HttpOnly` cookies.
- `SameSite` rules and third-party-cookie partitioning/blocking can keep login cookies
  out of an embedded request. The Storage Access API, where supported, lets the
  **embedded origin** request access to its own unpartitioned storage; it does not give
  the parent page that storage
  ([Storage Access API](https://developer.mozilla.org/en-US/docs/Web/API/Storage_Access_API)).
- Browser JavaScript has no DevTools-style API for reading another page's request
  headers or response bodies. Resource Timing intentionally exposes timing rather than
  raw authenticated traffic and applies cross-origin restrictions by default
  ([W3C Resource Timing security considerations](https://www.w3.org/TR/resource-timing/#security-considerations)).
  A SongMirror service worker cannot control a provider document because service-worker
  control is restricted to its registered origin/path scope
  ([Service-Worker-Allowed](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Service-Worker-Allowed)).

| Capability | Normal SongMirror page | Cross-origin iframe/popup | Provider OAuth redirect |
|---|---:|---:|---:|
| Show a provider login | Yes | Yes, unless framing is blocked | Yes |
| Know that the window closed/navigated | Limited | Limited | Yes, through callback state |
| Read provider DOM or JavaScript state | No | No | Not needed |
| Read provider cookies, including `HttpOnly` | No | No | No |
| Read provider request headers/bodies | No | No | No |
| Receive a scoped authorization result | No | Only if provider cooperates | Yes |

The correct web-only “Connect” button is therefore an authorization-code redirect or
popup whose callback belongs to SongMirror. It is not a general session extractor.

## Provider-by-provider result

### TIDAL

**Recommended path: official OAuth 2.1 authorization code flow with PKCE.** TIDAL
documents authorization-code and refresh-token flows, a consent screen, and the
authorization/token endpoints. It enforces PKCE and returns a refresh token for user
authorization ([TIDAL Authorization](https://developer.tidal.com/documentation/api-sdk/api-sdk-authorization)).
TIDAL's app dashboard is available to regular TIDAL accounts and creates client
credentials; requested scopes should be minimized
([Manage apps](https://developer.tidal.com/documentation/api-sdk/api-sdk-manage-apps)).

This can be a conventional **Connect → TIDAL login/consent → callback → connected**
flow in the existing web app. Tokens should be exchanged and stored by the backend, not
in frontend storage. Before making it the default, run an end-to-end capability test for
every SongMirror playlist operation and requested scope.

If the current first-party web compatibility transport remains necessary, the helper
needs only the `Authorization` Bearer header from an `openapi.tidal.com/v2` request and
the two-letter country code. No TIDAL cookie jar is needed.

**Policy caveat:** TIDAL's developer terms prohibit unauthorized automated scraping and
circumvention of platform limitations
([TIDAL Developer Terms](https://developer.tidal.com/documentation/guidelines/guidelines-developer-terms)).
That makes official OAuth materially safer than automating the web player.

### Qobuz

**Recommended path today: optional helper, with an official partner path when
available.** Qobuz's API terms say an application ID and secret issued by Qobuz are
required and must not be shared
([Qobuz API Terms of Use](https://static.qobuz.com/apps/api/QobuzAPI-TermsofUse.pdf)).
Qobuz's public partnerships page directs integrations to its business-development team
([Qobuz partnerships](https://www.qobuz.com/us-en/page/annonceurs-partenariats)).
There is no documented consumer OAuth redirect that SongMirror can use in place of
those credentials.

For the existing compatibility transport, the helper can watch a signed-in
`www.qobuz.com/api.json/0.2` request and retain only `X-App-Id` and
`X-User-Auth-Token` (plus user ID if a later endpoint proves it necessary). It should
discard all cookies and unrelated request headers before anything is persisted.

### Deezer

**Recommended path: official OAuth for an existing/approved app; otherwise the optional
helper.** Deezer's developer guide describes creating an application ID and explicitly
refers to an OAuth popup and configured redirect domain
([Deezer Getting Started](https://developers.deezer.com/guidelines/getting_started)).
The maintainer's signed-in developer dashboard currently reports that new application
creation is unavailable, so the documented flow is not presently an onboarding answer
for a new SongMirror application. That dashboard state is account-gated and should be
rechecked periodically.

For the existing Pipe compatibility transport, the helper should read only Deezer's
dedicated `refresh-token` cookie. Capturing the current `Authorization: Bearer` header
from `pipe.deezer.com/api` can speed initial validation but is optional because
SongMirror can renew the short-lived access token from the refresh cookie.

**Policy caveat:** Deezer's developer terms prohibit reverse engineering its API or
services and allow Deezer to restrict access at its discretion
([Deezer developer terms](https://developers.deezer.com/termsofuse)). The compatibility
path needs project-owner review before it is presented as a generally supported
integration.

### Amazon Music

**Recommended path today: optional helper only; switch to Login with Amazon if the
project is approved.** Amazon's official Music Web API uses Login with Amazon OAuth,
requires both a Bearer token and `x-api-key`, and remains limited to approved closed-beta
developers
([Amazon Music Web API overview](https://www.developer.amazon.com/docs/music/API_web_overview.html)).
The documented playlist API includes create/add/delete operations under
`music::library` scopes
([Amazon Music playlist API](https://developer.amazon.com/docs/music/API_web_playlist.html)).
Login with Amazon's authorization-code grant uses a browser redirect and supports
refresh tokens
([Choose an authorization grant](https://developer.amazon.com/docs/login-with-amazon/choose-authorization-grant.html)).

For the existing compatibility transport, the helper should collect only the named
Amazon Music renewal-cookie allowlist already enforced by SongMirror. The backend can
then request `music.amazon.com/config.json` itself and derive the short-lived Music
authorization context; the extension does not need CDP response-body access or a full
Amazon cookie jar.

Amazon deserves an especially strong warning: several allowlisted identity cookies may
authenticate more than the Music surface, so compromise can have consequences beyond a
playlist. Also, Amazon Music's program requirements disallow authentication methods
other than those Amazon specifies and, by default, disallow integrating Amazon Music
with another music service
([Amazon Music Program Requirements](https://www.developer.amazon.com/docs/music/requ_AM-Program-Requirements.html)).
Technical feasibility does not establish permission to ship this path.

## Architecture options

| Option | Can collect current web-session artifacts? | Deployment fit | Recommendation |
|---|---:|---|---|
| Official OAuth redirect/popup | No raw session data; returns scoped tokens | Any web deployment | **Use whenever available** |
| Browser extension | Yes, with explicit host/cookie/network permissions | Browser may be separate from Docker/NAS server | **Best compatibility helper** |
| Local Playwright-managed browser | Yes | SongMirror/helper must run on the user's desktop | Good fallback |
| Electron `WebContentsView` | Yes | Requires shipping a desktop application | Possible, not justified solely for connection |
| Tauri remote webview | Platform-specific extraction work | Requires shipping a desktop application | Possible, less portable for interception |
| Attach to the user's existing browser through remote debugging | Yes, but exposes a powerful debugging channel | Desktop only | **Reject** |
| Plain iframe/popup | No | Any web deployment | **Impossible for extraction** |

### Recommended helper: WebExtension

Extensions can query cookies, including values represented as `HttpOnly`, when the user
grants the `cookies` permission and matching host permissions
([Chrome `cookies` API](https://developer.chrome.com/docs/extensions/reference/api/cookies)).
With `webRequest` plus host permissions they can observe request traffic and request
headers ([Chrome `webRequest` API](https://developer.chrome.com/docs/extensions/reference/api/webRequest)).
These are privileged extension capabilities, not capabilities that can be delegated to
ordinary page JavaScript.

The extension should use optional, provider-specific host permissions requested only
after the user clicks Connect. Chrome recommends optional permissions so users can grant
only the capability needed for a feature
([Chrome Permissions API](https://developer.chrome.com/docs/extensions/reference/api/permissions)).
It should not request `<all_urls>`, history, tabs beyond what the flow needs, or the
`debugger` permission.

Suggested flow:

1. SongMirror creates a short-lived, single-use pairing transaction containing a random
   nonce, provider ID, expected SongMirror origin, and expiry.
2. The local page hands that transaction to the installed extension. On Chromium this
   can use a tightly scoped `externally_connectable` origin; Chrome's messaging guide
   requires receivers to validate untrusted messages
   ([extension messaging](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)).
   For Firefox, use a content script on the SongMirror origin as the bridge because
   webpage-to-extension `externally_connectable` messaging is not supported there
   ([MDN compatibility note](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/externally_connectable)).
3. The extension requests only that provider's optional host permissions and opens the
   provider login in a normal top-level tab. The user enters credentials directly into
   the provider page; the extension never reads form fields or keystrokes.
4. A provider-specific collector waits for the minimum artifact listed above. It stops
   listening as soon as one valid candidate is found.
5. The extension minimizes locally, then sends the candidate directly to the paired
   SongMirror backend. The page receives only success/account metadata, never the raw
   secret.
6. The backend runs the connector's existing parser, allowlist, and validation request
   again before persistence. On success it invalidates the pairing transaction and asks
   the extension to drop its in-memory copy and optional host permission.

The helper is a bootstrap mechanism, not a permanent background sniffer. Deezer and
Amazon Music already have server-side renewal transports; after bootstrap, those
transports should rotate access material in the background. TIDAL and Qobuz should
surface a reconnect state if their captured sessions expire.

### Local Playwright helper

A small local companion can launch a visible browser with a dedicated SongMirror profile,
wait for the user to complete login, and listen for the same provider-specific request.
Playwright exposes context-level cookies and request/response events
([BrowserContext](https://playwright.dev/docs/api/class-browsercontext)), while
`launchPersistentContext` provides a separate user-data directory
([BrowserType](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context)).

Use a dedicated profile, not the user's normal browser profile. This avoids profile
locking, limits the cookies in scope, and makes deletion/revocation understandable. It
also avoids depending on a remote-debugging port. Chrome 136+ deliberately refuses
remote debugging of the default profile without a non-default user-data directory
because attackers use debugging to extract cookies
([Chrome remote-debugging change](https://developer.chrome.com/blog/remote-debugging-port)).

This approach is simplest for a desktop-local SongMirror install. It is not transparent
when SongMirror runs in Docker on a NAS or remote host: that process cannot display and
control a browser on the user's laptop without a separately installed local companion.

### Electron or Tauri wrapper

Electron can load a provider as top-level remote content in a `WebContentsView`; its
`Session` exposes cookies and its `WebRequest` observes requests
([Electron session](https://www.electronjs.org/docs/latest/api/session),
[Electron WebRequest](https://www.electronjs.org/docs/latest/api/web-request)). That
bypasses iframe embedding restrictions because the provider is the top-level document
inside the managed web contents. It does not bypass provider policy or make embedded
authorization a good OAuth practice.

If this is ever built, remote content must have Node integration disabled, context
isolation and process sandboxing enabled, navigation/window creation allowlisted, and no
privileged API exposed to provider JavaScript. Electron's own security checklist treats
remote content as a severe risk
([Electron security](https://www.electronjs.org/docs/latest/tutorial/security)).
Electron also recommends against the legacy `<webview>` tag in favor of alternatives
such as `WebContentsView`
([Electron webview warning](https://www.electronjs.org/docs/latest/api/webview-tag)).

Tauri can open a remote URL in a webview, but it relies on platform webviews and its
portable API does not provide the same uniform cookie/network interception surface
([Tauri Webview](https://v2.tauri.app/reference/javascript/api/namespacewebview/)). Any
native collector would require platform-specific Rust/webview integration. Remote
provider content must receive no Tauri command capabilities; Tauri documents capabilities
as the boundary that constrains what remote webviews can invoke
([Tauri capabilities](https://v2.tauri.app/security/capabilities/)).

For **official OAuth**, neither wrapper should inspect an embedded login. Native-app
OAuth best practice requires an external user-agent and explains that embedded
user-agents can expose credentials and cookies to the host app
([RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.12)). Use the system
browser and a loopback/custom-scheme callback instead.

## Secret handling and threat model

Web-player cookies and Bearer tokens are account credentials. “The user authorized the
project” is necessary but does not lower their technical sensitivity. The helper and
backend should meet these requirements before release:

- **No password handling.** Do not inject into login forms, read form fields, log
  keystrokes, or proxy the provider login page. Let the provider handle password,
  passkey, CAPTCHA, and MFA interactions directly.
- **Minimize before transport and again before storage.** Reuse SongMirror's existing
  per-provider parsers/allowlists. Never send or save a full `Cookie` header, cookie jar,
  copied cURL request, HAR, or response log.
- **Keep secrets out of the renderer.** The extension/helper should submit directly to
  the authenticated backend. Do not put secrets in URLs, DOM, localStorage, analytics,
  crash reports, or frontend logs. Connector responses should use `Cache-Control:
  no-store`.
- **Authenticate pairing.** Bind a high-entropy, one-use nonce to the signed-in
  SongMirror user, exact origin, provider, and short expiry. Validate sender origin and
  provider tab URLs. Reject replay, provider substitution, and a second completion.
- **Protect transport.** Localhost is the safest default. A LAN/NAS or remote SongMirror
  origin should use trusted HTTPS; do not teach users to bypass certificate warnings.
- **Protect at rest.** Prefer an OS keychain/secret service for a desktop helper. On a
  server, keep owner-only permissions, support an external secret store or encryption
  key, redact values everywhere, and exclude secret files from backups unless backups
  are encrypted.
- **Limit extension privilege.** Request provider host access at the moment of use,
  discard it after success where browser APIs allow, keep all captured material in
  memory, and provide an obvious uninstall/revoke path.
- **Make consent precise.** Before opening the provider, state exactly which token or
  named cookies will be read, what SongMirror can do with them, where they will be
  stored, how renewal works, and how to disconnect/delete them.
- **Fail closed.** A changed hostname, missing expected header, unexpected credential
  shape, validation failure, or provider flow drift must abort collection rather than
  broaden capture.
- **Disconnect completely.** Delete stored web-session and refresh material. For OAuth,
  call provider revocation when available in addition to deleting local tokens.

The connection page itself becomes a high-value target. It should have no third-party
scripts or analytics, a restrictive CSP, CSRF protection, short sessions, and strong XSS
defenses. OAuth paths should use authorization code + PKCE and a transaction-specific
`state`; current OAuth security guidance applies PKCE to web clients and requires CSRF
protection
([RFC 9700 authorization-code guidance](https://www.rfc-editor.org/rfc/rfc9700.html#name-authorization-code-grant)).

## Recommended delivery order

1. **TIDAL OAuth:** implement the standard redirect/popup flow and validate all required
   playlist operations. This delivers a real one-click connection without a helper.
2. **WebExtension proof of concept:** Chromium first, covering Qobuz and Deezer, then
   Amazon Music after an explicit policy/security decision. Use `cookies` +
   `webRequest`; do not use CDP/`debugger` unless a future provider artifact genuinely
   cannot be obtained otherwise.
3. **Firefox bridge:** reuse the collectors with a content-script bridge on the
   SongMirror origin.
4. **Local Playwright companion:** offer this for desktop-local installs and users who
   do not want a browser extension.
5. **Desktop wrapper only as a broader product decision:** do not take on Electron or
   Tauri solely to replace one paste box.

Throughout, retain **Paste manually** and **Reconnect manually** as recovery paths.
Private web APIs and credential shapes can change without notice, and an automated
collector should reduce setup friction rather than hide that maintenance reality.

## Bottom line

The desired click-to-connect experience is achievable, but not with an iframe alone.
Official OAuth is the clean solution where available. For the first-party web API paths,
the smallest credible solution is an optional, least-privilege browser extension that
opens a real provider tab and exports only SongMirror's existing minimized credential
shape. A local Playwright browser is the next-best fallback. Both require explicit user
consent, rigorous secret handling, and a provider-policy review—especially for Amazon
Music, TIDAL, and Deezer.
