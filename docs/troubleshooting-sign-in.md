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
