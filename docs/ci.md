# Deploying from a pipeline

A pipeline that builds, tests and pushes can tell Noust to deploy, and wait for
the answer. Nothing about a code host is needed for it: a token, one `POST`, and
the job it returns.

Two ways to be told about new code exist, and they are not the same thing:

| | Who calls | Credential | Reachable through |
|---|---|---|---|
| **Deploy on push** (`docs/api.md`, "Deploy webhooks") | the code host, on every push | a secret per application, signed | `noust web expose-hooks` publishes only `/hooks` |
| **Deploy from a pipeline** (this page) | your CI, when *it* decides | an API token of scope `deploy` | the console's address (see below) |

Use the webhook when "every push to this branch deploys". Use the pipeline call
when the deploy has to wait for tests, for an image to be pushed, or for a human
to approve, or when your code host is not one Noust has a webhook for.

## The token

```bash
noust token create ci --scope deploy --owner alice      # printed once
noust token create ci --scope deploy --expires-hours 4320
```

A `deploy` token can update, roll back and activate a release of an application
that exists. It cannot create, delete or configure anything, and it cannot read
secrets. Put it in the pipeline's secret store (`NOUST_TOKEN`), never in the
repository. A token owned by a member of a team deploys that team's applications
only; one that sees every team can deploy several at once.

## Reaching the console

The call goes to the console's API, so the runner must be able to reach it:

- publish the console over TLS: `noust console expose noust.example.com` (it
  refuses while any account can sign in without a second factor, and an
  allow-list restricts who may reach it);
- or run the pipeline on a runner that is on the same network, or connected
  through a VPN or an SSH tunnel.

`noust app webhook show DOMAIN` prints the exact URL for an application under
"Deploy URL", and the console shows it in the application's **Deploy on push**
page.

## The call

```bash
curl -fsS -X POST https://noust.example.com/api/apps/shop.example.com/deploy \
  -H "Authorization: Bearer $NOUST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

`202` answers with the job to follow:

```json
{"message": "Update job created",
 "results": [{"domain": "shop.example.com", "status": "queued", "job_id": "3f9c2a1e"}]}
```

Follow it with `GET /api/jobs/3f9c2a1e` (a `read` token is enough) until
`status` is `completed`, `failed` or `cancelled`. A failed deployment carries,
in `error`, the health gate's probes and the journal verbatim.

The body is optional and every key is too:

| Key | Meaning |
|---|---|
| `force` | Rebuild even when the branch has no commit the live build lacks. Without it, nothing new answers `200` with status `nothing_new` and queues nothing. |
| `no_cache` | Build from scratch: empty package manager cache, dependencies installed again, images without their layer cache. Implies `force`. |
| `commit` | Deploy exactly this commit (full or abbreviated). |
| `tag` | Deploy the commit this git tag points at. |
| `image_tag` | A container application that runs a registry image: deploy this tag of the image it already runs. The digest it was pinned to is dropped. |
| `pull_request` | Rebuild the preview of this pull request number instead of the application. |

`commit`, `tag` and `image_tag` exclude each other, and `pull_request` stands
alone. A `429` means the deploy queue is full: retry.

### Several applications

```bash
curl -fsS -X POST https://noust.example.com/api/deploy \
  -H "Authorization: Bearer $NOUST_TOKEN" -H "Content-Type: application/json" \
  -d '{"apps": ["shop.example.com", "admin.example.com"], "no_cache": true}'
```

At most 20 per call, each its own job, each its own entry in `results`
(`queued`, `joined`, `nothing_new`, `queue_full` or `not_found`); one that
cannot be deployed does not stop the others. It needs a token that sees every
team.

### Why a POST

A deploy changes the server. A `GET` that deploys is a link that a crawler, a
chat preview or a prefetching browser follows, and it ends up in access logs
with its credentials. The call is a `POST` with the token in a header, and a
`GET` of it answers `405`.

## The GitHub Action

`packaging/github-action` is a composite action over `curl` and `jq`, the
two programs every GitHub-hosted runner has.

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci && npm test
      - uses: Perkybeet/noust/packaging/github-action@v3.4.0
        with:
          url: https://noust.example.com
          token: ${{ secrets.NOUST_TOKEN }}
          app: shop.example.com
```

It queues the deployment, prints each step as the server reports it, and fails
the step when the deployment fails, printing the server's own words in a
collapsible group and as an annotation. Inputs:

| Input | Default | |
|---|---|---|
| `url` | | The console's address. Plain `http://` is refused except on loopback: the token would cross the network in the clear. |
| `token` | | A `deploy`-scope token. |
| `app` | | A domain; several, one per line or comma separated. |
| `commit`, `tag`, `image-tag`, `pull-request` | | See the table above. |
| `force`, `no-cache` | `false` | |
| `wait` | `true` | `false` queues and returns. |
| `timeout` | `1800` | Seconds to wait; the deployment carries on on the server. |
| `poll-interval` | `5` | |

Outputs: `status` (`success`, `failed`, `cancelled`, `nothing-new`, `queued` or
`timeout`), `job-id`, `deployment-id` and `results` (JSON, one object per
application).

Build an image in the same workflow, then deploy exactly that tag:

```yaml
      - uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/acme/shop:${{ github.sha }}
      - uses: Perkybeet/noust/packaging/github-action@v3.4.0
        with:
          url: https://noust.example.com
          token: ${{ secrets.NOUST_TOKEN }}
          app: shop.example.com
          image-tag: ${{ github.sha }}
```

The token reaches the script through its environment and `curl` through a
configuration on standard input, so it is never an argument and never on a
command line anyone on the runner could read.

## Other pipelines

The same call works anywhere that can run `curl`:

```yaml
# .gitlab-ci.yml
deploy:
  stage: deploy
  image: curlimages/curl
  script:
    - >
      curl -fsS -X POST "$NOUST_URL/api/apps/shop.example.com/deploy"
      -H "Authorization: Bearer $NOUST_TOKEN" -H "Content-Type: application/json"
      -d "{\"commit\": \"$CI_COMMIT_SHA\"}"
```

For Forgejo and Gitea Actions, the GitHub Action runs unchanged
(`uses: https://github.com/Perkybeet/noust/packaging/github-action@v3.4.0`).
