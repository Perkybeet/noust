# Troubleshooting

Start here when something does not work. Each page below is one symptom: the message you see, word
for word, what it means and what fixes it.

## First, ask Noust

```bash
noust diagnose wrenfield.io
```

It reads the application's unit, its port, the application's own answer, nginx's answer, the
journal, nginx's error log, the certificate, the last deployment, out-of-memory kills and the disk,
and names the most likely cause first. It changes nothing. In the console, the application's
**Diagnose** page shows the same, with the evidence of each check under it.

For the server as a whole, `noust health` checks the disk, the web server, every application, the
certificates close to expiry and memory pressure. `noust setup doctor` checks that the server still
has what deployments need, and gives the command that fixes each missing piece.

Noust never paraphrases what nginx, systemd or certbot said: their own words are below its
verdict. Paste them as they are if you ask for help.

## By symptom

- [The site answers 502, or does not answer](troubleshooting-502.md)
- [A build or a deploy failed](troubleshooting-build.md)
- [A certificate was not issued, or is about to expire](troubleshooting-certificate.md)
- [The name does not point at the server](troubleshooting-dns.md)
- [I cannot sign in to the console](troubleshooting-sign-in.md)
- [The console does not open](troubleshooting-console.md)
- [The installation did not work](troubleshooting-install.md)
- [An update says it is on the way, or the package index is behind](troubleshooting-update.md)

## Still stuck

```bash
noust support report
```

It prints a report to paste into an issue: the version, the platform, the state of the services
and a diagnosis of the applications that need attention, with secrets masked. It reads and changes
nothing. Read it before you post it; `--hide-domains` replaces your domains and the server's name.
[Getting help](SUPPORT.md) says where to post it.
