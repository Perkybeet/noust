# Make it safe to leave alone

**Goal:** you hear about a failed deploy or a certificate about to expire before your users do,
your account needs a second factor, and Noust keeps itself up to date.

**Time:** ten minutes.

**You need:** [Noust installed](install.md), and a place to receive messages: Slack, Discord,
Telegram, Microsoft Teams, Matrix, ntfy, email or a webhook of your own.

The examples send Kelmoor's alerts to a Slack channel of its operations team.

## Steps

1. **Add a notification channel.** In Slack, create an incoming webhook for the channel, then:

   ```bash
   noust notify channel set kelmoor-ops --type slack --secret webhook_url
   ```

   `--secret` asks for the webhook's address without showing it. Noust stores it sealed and never
   shows it again. In the console: **Settings > Channels**.

2. **Test it.**

   ```bash
   noust notify test kelmoor-ops
   ```

   A test message should arrive in the Slack channel within seconds.

3. **Turn notifications on.** They are off until you do, so a half-configured channel sends
   nothing.

   ```bash
   noust config set notifications.enabled true
   ```

   From now on a failed deploy, a backup that did not run, a certificate close to expiry and an
   application that stops answering reach the channel.

4. **Protect your account with a second factor.** In the console, open the menu under your
   avatar, **Account > Sign-in security**, and add an authenticator app or a passkey. You need it
   anyway to publish the console on a name of its own or to manage other servers from this one.

5. **Keep a copy of the backups off the server.** If you skipped it, do step 4 of
   [Back it up](first-backup.md#steps).

6. **Let Noust update itself.**

   ```bash
   noust self-update set --enable --channel patch --window 03:00-05:00
   ```

   The server then installs the patch releases of the version it runs, between 3 and 5 in the
   morning on its clock. `security` installs only releases that fix a vulnerability; `stable` also
   takes a new minor version. A new major version is never installed by itself.

7. **Look at the operating system's own updates.**

   ```bash
   noust server updates list
   ```

   Security updates are listed first. In the console: **Server > Updates**.

## What you should see

```bash
noust notify list         # your channel, enabled
noust self-update show    # the channel, the window and when it last ran
noust health              # disk, web server, applications, certificates and memory, at a glance
```

`noust health` only reads. Run it whenever you come back to a server after a while.

## If it fails

- **The test message does not arrive:** `noust notify deliveries` shows what went out and what the
  service answered, verbatim. A wrong webhook address is the usual cause: set it again with the
  same `noust notify channel set` command.
- **Messages stop arriving later:** a channel that fails is retried, and `noust notify retry`
  sends again what failed. [Notifications and alerts](notifications.md) covers quiet hours, routes
  per project and alert rules on CPU, memory and disk.
- **You lost your authenticator:** as root on the server, `noust user reset-mfa USERNAME` removes
  it, and you enrol a new one at your next sign-in. A backup code, if you kept them, also signs you
  in.

## Next

You have a server you can leave alone. From here:
[Add a server to your fleet](first-fleet.md), or, if you are coming from another platform,
[Move your applications from Coolify or Dokploy](first-move.md). [Next steps](next-steps.md)
lists the rest by what you want to do.
