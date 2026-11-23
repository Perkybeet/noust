# I cannot sign in to the console

Everything below runs on the server, as root. Root on the server can always get back in: the
console only protects what is behind it.

## The account is locked

```text
Too many failed attempts
Sign-in is locked for this address. Try again in 15 minutes.
```

Five failed attempts lock the account for 15 minutes, and five from one address lock that address.
The page counts down. To lift it now:

```bash
noust user unlock marta
```

`auth.lockout.threshold` and `auth.lockout.minutes` change the numbers
([Configuration keys](reference-config.md)).

## The code is refused although it is right

An authenticator's code depends on the clock of both sides. Check the server's:

```bash
noust server time status
```

It says whether the clock is synchronised. Check your phone's too: it should set its time
automatically.

## The authenticator is lost

Use one of your backup codes, if you kept them: the code field takes one. Otherwise, remove the
authenticator; the account enrols a new one at its next sign-in:

```bash
noust user reset-mfa marta
```

## No account can sign in

The access token still opens the console. It is the key of whoever is root on the server, so every
use of it is recorded as a warning:

```bash
noust web token          # whether there is one, and its state
noust web token --new    # a new one; the old one stops working
```

Then choose **Access token** on the sign-in page and paste it.

## The passkey button is missing

Browsers offer passkeys only on a site with a certificate they trust, so not through an SSH tunnel
to `127.0.0.1` or with a self-signed certificate. Use the authenticator app there, or publish the
console on a name of its own ([Reaching the console](console-start.md#reaching-it)).

## You are signed out all the time

A session ends after 30 minutes without activity and 12 hours after sign-in, whichever comes
first. `auth.session.idle_minutes` and `auth.session.absolute_hours` change them.

## Single sign-on refuses you

```text
The provider knows you, but this server has no account for you. Ask a security officer to invite you or link your account.
```

The identity provider let you in, and this server has no account linked to that identity. A
security officer invites you or links the account ([Single sign-on](sso.md)).

## The browser says another passkey request is still open

```text
The browser still has another passkey request open. Reload the page to close it, then try again.
```

A browser allows one passkey request at a time in a page. The console asks for one at a time and, when the
browser answers that another is open ("A request is already pending"), asks again a few times by itself before
it shows the message, with the browser's own words under it. When it does show, no wait ends it: reload the page
(the button beside the message does it) and ask again. Nothing is wrong with the account or the server.

```text
This browser left open the passkey request of the sign-in page, which a password manager's extension usually does.
```

The sign-in page offers your passkey in the name field, which is a request the page ends when you leave it. A
password manager's extension that answers for the browser does not always end it, and the request stays open
for as long as the page is: every passkey after it is refused, the confirmation of a delicate action included.
The console says so the first time it meets it, and from then on it no longer offers passkeys in the name field
in that browser (the "Sign in with a passkey" button works as before), so it does not happen again. It is kept
by the browser, per site: clearing the site's data brings the offer in the name field back. An authenticator
code or a backup code confirms it is you all the while.
