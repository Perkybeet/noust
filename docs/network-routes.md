# Routes at a prefix, and signing in with a service of your own

Part of [The network of an application and of the server](network.md).

## Routes: what happens at a prefix

A route is a prefix of the site (`/admin`, `/api/v2`) with its own rule. It is part of the policy
above, so it is kept in the store, rendered by whoever writes the site, and still there after a
deploy, a new alias or a certificate order. A route matches the prefix and everything under it,
never a longer name that only starts the same (`/admin` is not `/administrator`).

```bash
noust app policy route list shop.example.com
noust app policy route add shop.example.com /admin --password                  # ask for it here
noust app policy route add shop.example.com /hooks --open                       # lift the site's password here
noust app policy route add shop.example.com /embed --header "X-Frame-Options: ALLOWALL"
noust app policy route add shop.example.com /api --proxy-port 4000 --strip      # another service, prefix removed
noust app policy route add shop.example.com /blog --proxy-port 5000             # ...or kept
noust app policy route add shop.example.com /docs --redirect-to https://docs.example.org/ --code 308
noust app policy route remove shop.example.com /docs
```

| Kind | What it does |
|---|---|
| The site (`site`) | The prefix is served as the rest of the site is, with its own answer to the password (`--password` asks for it where the site does not, `--open` lifts the site's) and its own response headers, which replace the site's header of the same name under the prefix. |
| Another service (`proxy`) | The prefix is handed to a service on this server by its port. With `--strip` the service sees `/users` for `/api/users` and is told the prefix in `X-Forwarded-Prefix`; without it the path is passed on as it came. |
| A redirect (`redirect`) | The prefix answers with a 301, 302, 307 or 308 to an address or an absolute path. The rest of the path follows the target unless `--drop-path`. |

How the web server is told:

- **nginx** asks for the password at the server and a `map` of the normalised path (`$uri`, so
  `/%61dmin` and `/./admin` are the path they say) decides, for every template alike, whether to ask
  and under which realm. A longer prefix wins over a shorter one, so a password route inside an open
  one still asks. Headers are the same: one `add_header` per name whose value the path decides.
  Proxies and redirects are locations written before the site's own.
- **Apache** has a `<Location>` section for a prefix that changes the password or the headers,
  `ProxyPassMatch` for a service and `RedirectMatch` for a redirect.

What a route cannot do, and why:

- **The addresses allowed stay the site's.** `--open` lifts the password under a prefix, not the
  address lists: those are checked for the whole server block.
- **A prefix handed to a port reaches whatever listens there**, so the port of the web server, of
  SSH and of the console are refused with who holds them, and a member of a team may name only the
  ports of applications it sees; any other is the server's to ask for.
- **Proxies and redirects do not nest.** `/api` handed to a service cannot also have `/api/v2`
  redirected: the two web servers would not agree on which wins. Routes that only change the
  password or the headers nest as they like.
- **`/.well-known` is the certificate authority's** and cannot be a route.
- The prefix is judged again after an internal redirect (`try_files` falling back to
  `/index.html`), as the path it was sent to: an open prefix under a protected single-page site asks
  for the password at the fallback. That direction fails closed.
- A password route needs accounts: saving one without them is refused, and the last account cannot be
  removed while a route needs it.

API: the routes are the `routes` field of `GET|PUT /api/apps/{domain}/policy` (a list that replaces
the whole). Console: **Application, Settings, Access, Routes**.

## Signing in with a service of your own (forward-auth)

nginx's `auth_request` asks a service about every request before it reaches the application: a 2xx lets
it through, a 401 or 403 stops it, and what the service says in its response headers (who the visitor
is) can be handed to the application. That is how an identity provider or an OAuth2 proxy protects an
application that knows nothing about it, with the accounts, second factor and single sign-on the
provider already has.

```bash
noust app policy set shop.example.com --forward-auth-preset oauth2-proxy         # or authentik
noust app policy set shop.example.com \
    --forward-auth-url http://127.0.0.1:4180/oauth2/auth \
    --forward-auth-sign-in /oauth2/start?rd= \
    --forward-auth-header X-Auth-Request-User --forward-auth-header X-Auth-Request-Email
noust app policy set shop.example.com --no-forward-auth
```

- **What nginx is told.** An internal location (`/_noust_auth`) that only nginx can reach asks the
  address, without the body, with the original address and method in `X-Original-URL` and
  `X-Original-Method`. A visitor who is not signed in is redirected to the sign-in with the page they
  asked for appended (`--forward-auth-sign-in`; empty answers 401). The service's `Set-Cookie` is
  passed to the visitor, so it can renew its session.
- **Who the visitor is.** The response headers you name are set on the request to the application (and
  to a prefix handed to another service) on every request, so a header a client sent with that name is
  replaced, never trusted.
- **Together with a password or an address list.** Both are wanted (`satisfy all`) unless the policy
  says `access: any`, which needs two ways in and lets either do.
- **It is the server's to name.** nginx will call the address from this machine, so a member of a team
  cannot set it (the server's administrator does). The web server's own port, SSH's and the console's
  are refused as the service. Apache has no `auth_request` here.
- **A service that is down refuses everybody, you included.** Before it is kept, the service is asked
  once as nginx would for a visitor with no session (any HTTP answer, 401 and 403 included, proves it
  is there). One that does not answer is refused with the reason unless you go on knowingly
  (`--accept-unreachable-auth`; the console asks).
- **Limits.** It covers the whole site: a prefix cannot be left out of it (a redirect route answers
  before authentication, as nginx's rewrite phase comes first). The page asked for travels unencoded
  in the sign-in's `rd=`, as nginx's own guides do, so a query with `&` is cut at the service.
  The presets are the defaults of oauth2-proxy and authentik; check them against your version.
  Using Noust's own accounts as the provider is not done.

API: the `forward_auth` field of the policy (an empty `url` turns it off) and
`GET /api/policy/forward-auth-presets`. Console: **Application, Settings, Access, Sign-in service**.
