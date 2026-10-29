# HTTP/3 and a web application firewall

Part of [The network of an application and of the server](network.md).

## HTTP/3

```bash
noust app policy http3 shop.example.com             # can this server? what is missing? is UDP 443 let in?
noust app policy set shop.example.com --http3       # serve the site over QUIC as well
noust app policy set shop.example.com --no-http3
```

A site served over HTTPS can also listen on QUIC (UDP 443) and tell browsers with `Alt-Svc`. It is
nginx's: the version must be 1.25.0 or later and built with `--with-http_v3_module` (Debian 12 and
Ubuntu 24.04 ship 1.22 and 1.24, which have neither; the packages from nginx.org do), and the answer
says which of the two is missing and where to get it. Apache's stock builds have no HTTP/3.

- **One socket for every site.** nginx takes `reuseport` once for an address and port, and QUIC needs it
  to work with more than one worker, so no application's site carries it: a small site of Noust's own
  (`00-noust-quic`, which sorts first and refuses the TLS handshake of any name nobody serves) holds the
  one `listen 443 quic reuseport`. It is written with the first site that turns HTTP/3 on, tested by
  nginx before it is reloaded, and taken away with the last.
- **The firewall.** QUIC travels over UDP 443. Noust says whether the firewall lets it in and names the
  command that opens it; a closed port costs a browser one failed attempt before it uses HTTP/2, so
  nothing breaks. Noust does not open it for you.
- A location of the site that sets a header of its own drops the server's headers, `Alt-Svc` included:
  that is nginx's rule for `add_header`.

API: the `http3` field of the policy, and `GET /api/apps/{domain}/http3` for what the server can do.
Console: **Application, Settings, Protection, HTTP/3**. Event: `sites.http3` (the socket's site written or
removed).

## A web application firewall

A site can have ModSecurity with the OWASP Core Rule Set in front of it: every request is judged for
SQL injection, cross-site scripting, path traversal, remote command execution and the like before it
reaches the application. nginx only; Apache has its own module and is not driven here.

```bash
noust app policy waf show shop.example.com          # can this server run it, and what is missing
noust app policy waf install                        # Debian and Ubuntu: the module and the rules
noust app policy set shop.example.com --waf detect  # judge and write down, refuse nothing
noust app policy waf events shop.example.com        # what the rules saw, and which rules matched most
noust app policy set shop.example.com --waf block   # refuse what scores too high (403)
noust app policy set shop.example.com --waf-exclude 942100 --waf-exclude 941100
noust app policy set shop.example.com --waf off
```

**Start in `detect`.** The rules are strict, and an application's own legitimate requests (a search
box that takes SQL-looking text, an editor that posts HTML) are the usual false positives. In
`detect` nothing is refused; the events list shows each request the rules judged, the rules that
matched, and a request that `block` would have refused is flagged *would be blocked*. When one of
those is a request you want, leave the rule that matched out for this site (`--waf-exclude`, or the
button beside the rule in the console) and look again. Only then move to `block`.

- **The module and the rules are the server's.** `libnginx-mod-http-modsecurity` and `modsecurity-crs`
  on Debian and Ubuntu; `noust app policy waf install` (sudo mode, the server's administrator) runs the
  package manager. Elsewhere install nginx's ModSecurity module and the Core Rule Set yourself. A policy
  that asks for the firewall on a server without them is refused in words, with what is missing.
- **The rules are loaded once.** `modsecurity_rules_file` at the http level loads the 900-odd rules a
  single time for every site; a site only says `modsecurity on` (and, for `detect`,
  `SecRuleEngine DetectionOnly`, and the numbers it leaves out). The file (`noust-waf/rules.conf`) and the
  one-line site that loads it (`00-noust-waf`) are Noust's, written when the first site turns the firewall
  on, tested by nginx before it is reloaded, and removed with the last. libmodsecurity reads only the plain
  `Include`, so the file names the packages' setup file and rules directory itself instead of using the
  distribution's `owasp-crs.load`, which is made for Apache.
- **The log does not keep what people type, and only root reads it.** ModSecurity's audit log
  (`/var/log/nginx/noust-waf-audit.jsonl`) is JSON and holds the request's headers and the rules that
  matched, not its body: a password typed into a form is not in it. Responses are not inspected. The
  headers of a flagged request include its `Cookie` and `Authorization`, which the module cannot leave
  out, so Noust makes the file before ModSecurity does, mode 0640 for root and the `adm` group, and no
  other account of the server can read it.
- **The log is rotated by truncating it.** ModSecurity opens its audit log once and never reopens it,
  so the web server's rotation (rename the file, ask nginx to reopen its logs) left the firewall
  writing to the renamed file for good, and `noust app policy waf events` read the new, empty one.
  The file is not named `*.log`, which `/etc/logrotate.d/nginx` owns, and Noust writes
  `/etc/logrotate.d/noust-waf` (daily, 14 kept, compressed, `copytruncate`) with the first site that
  turns the firewall on and removes it with the last. A server without logrotate is given no rule.
- **A rule number is a number.** The numbers a site leaves out end up in the web server's configuration,
  so only whole numbers from 1 to 99999999, at most 100, are accepted.
- **The paranoia level and the score threshold are the Core Rule Set's defaults** (level 1, threshold 5).
  They are the rule set's own settings file, which Noust does not rewrite.

**Banning the clients that keep trying.** The firewall refuses a request; fail2ban stops the client.

```bash
noust server security fail2ban web on      # watch nginx as well as SSH
noust server security fail2ban web off
```

It adds three jails that read the sites' logs: `nginx-http-auth` (a password refused by `auth_basic`
again and again), `nginx-botsearch` (a client asking for paths no site has: `wp-login.php`,
`phpmyadmin`, `.env`) and `nginx-limit-req` (a client that keeps going over a rate limit). fail2ban
ships the three filters. fail2ban must be running (`noust server security fail2ban install`) and nginx
installed. **Who is never banned** matters more than who is: a ban is made from the address nginx
logs, which behind a CDN is the CDN's, and banning a CDN's edge takes every site behind it down. So
besides who is connected over SSH now and the console's client, Cloudflare's published ranges and every
proxy a site declares (`real_ip custom`) are ignored. A site behind a CDN Noust does not know about has
to declare it first. A file fail2ban refuses is put back. Console: **Server, Security, Brute force**.
API: `POST /api/server/security/fail2ban/web` (sudo mode). Bans lift like the SSH ones
(`noust server security fail2ban unban ADDRESS`).

API: `GET /api/apps/{domain}/waf` (what the server has), `GET /api/apps/{domain}/waf/events` (what the
rules saw and the rules that matched most), `PUT /api/apps/{domain}/policy` with `waf` and
`waf_exclusions`, and `POST /api/waf/install` (sudo mode, the server's). Console: **Application, Settings, Protection, Web application firewall**. Event: `sites.waf`.
