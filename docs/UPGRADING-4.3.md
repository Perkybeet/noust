# Upgrading to 4.3

This page is the part of the upgrade to 4.3 that is about a console reached on a name that other services
share.

## The console says `Strict-Transport-Security` only on a name it owns

Until 4.2 the console sent `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every response
over TLS. A browser that reads it refuses plain HTTP, and any certificate it cannot trust, for that **name in
every port**, and for every subdomain of it. That is a promise only whoever owns the name can make. A central
in a container on a NAS answers on `https://nas.example.net:8443`, and the same name is the NAS's own portal on
other ports: after one visit to the console the browser refused the portal, whose certificate it did not
trust, with no way to continue, and turned the NAS's `http://nas.example.net:9999` into `https`.

From 4.3 the console sends it on the name `noust console expose` published it on, and nowhere else, and never
with `includeSubDomains`. The setting is `web.hsts`:

| Value | What the console does |
|---|---|
| `auto` (the default) | Sends `max-age=31536000` over TLS only on the name `noust console expose` published the console on. A console reached by its address, by a name nobody published, or from a container, sends nothing. |
| `always` | Sends `max-age=31536000` over TLS on every name it is reached by. For a console whose name is yours in every port and that is only ever reached over HTTPS. |
| `never` | Never sends it. |

`noust config set web.hsts always` (or `never`) changes it, and the console reads it on the next request. The
header is never sent in cleartext, where a browser ignores it.

### If you already opened the console on a shared name

Updating the console does not make a browser forget what it was told: the old policy stays for up to a year
after the last visit. The console cannot take it back, because a browser only takes a policy from a name it
trusts, so it is removed in the browser, once, for the name:

- **Chrome and Edge:** open `chrome://net-internals/#hsts` (`edge://net-internals/#hsts`), type the name under
  **Delete domain security policies** and press **Delete**.
- **Firefox:** open the history (**Ctrl+Shift+H**), right-click the name and choose **Forget About This Site**.
- **Another browser:** clearing its history and site data for the name removes it.

A console published with `noust console expose` needs nothing: the next visit replaces the old policy with the
new one, which no longer binds the subdomains.
