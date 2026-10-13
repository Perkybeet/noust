#!/bin/sh
# Deploy applications to a Noust server and wait for the result.
#
# What the "Deploy to Noust" action runs (see action.yml). Everything it needs
# arrives in NOUST_* environment variables, never in an argument: the token must
# not appear on a command line, where every local user could read it.
#
#   NOUST_URL            console address, https://noust.example.com
#   NOUST_TOKEN          API token of scope "deploy"
#   NOUST_APP            domain(s), one per line or comma separated
#   NOUST_COMMIT, NOUST_TAG, NOUST_IMAGE_TAG, NOUST_PULL_REQUEST   what to deploy
#   NOUST_FORCE, NOUST_NO_CACHE                                      "true" or "false"
#   NOUST_WAIT, NOUST_TIMEOUT, NOUST_POLL_INTERVAL
#
# It asks POST /api/apps/{domain}/deploy for one application and POST
# /api/deploy for several, then follows each job with GET /api/jobs/{id} until
# it ends. Outputs (GITHUB_OUTPUT): status, job-id, deployment-id, results.
# POSIX sh, with curl and jq.

set -eu

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
trap 'exit 130' INT TERM HUP

say() { printf '%s\n' "$*"; }
fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

emit() {
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        printf '%s=%s\n' "$1" "$2" >>"$GITHUB_OUTPUT"
    else
        printf '%s=%s\n' "$1" "$2"
    fi
}

# A message for the workflow's summary: an annotation on GitHub, plain text elsewhere.
annotate() {
    level=$1
    title=$2
    shift 2
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        first=$(printf '%s' "$*" | sed -n '1p')
        printf '::%s title=%s::%s\n' "$level" "$title" "$first"
    else
        printf '%s: %s: %s\n' "$level" "$title" "$*"
    fi
}

for tool in curl jq; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required on the runner"
done

: "${NOUST_URL:?the url input is required}"
: "${NOUST_TOKEN:?the token input is required}"
: "${NOUST_APP:?the app input is required}"
COMMIT=${NOUST_COMMIT:-}
TAG=${NOUST_TAG:-}
IMAGE_TAG=${NOUST_IMAGE_TAG:-}
PULL_REQUEST=${NOUST_PULL_REQUEST:-}
FORCE=${NOUST_FORCE:-false}
NO_CACHE=${NOUST_NO_CACHE:-false}
WAIT=${NOUST_WAIT:-true}
TIMEOUT=${NOUST_TIMEOUT:-1800}
POLL=${NOUST_POLL_INTERVAL:-5}
RETRY_DELAY=${NOUST_RETRY_DELAY:-5}

URL=${NOUST_URL%/}
case $URL in
    https://*) ;;
    http://localhost | http://localhost:* | http://127.0.0.1 | http://127.0.0.1:*) ;;
    http://*) fail "the url is plain http: the token would cross the network in the clear. Use https" ;;
    *) fail "the url must start with https://" ;;
esac
authority=${URL#*://}
authority=${authority%%/*}
case $authority in
    *@*) fail "the url must not carry credentials; the token is its own input" ;;
esac

# The token goes to curl in a config read from standard input, so it is never an
# argument; a character that could end the quoted header there is refused.
case $NOUST_TOKEN in
    *[!A-Za-z0-9._~+/=-]*) fail "the token has a character a Noust token never has" ;;
esac

for name in FORCE NO_CACHE WAIT; do
    eval "value=\$$name"
    case $value in
        true | false) ;;
        *) fail "$name must be true or false" ;;
    esac
done
for name in TIMEOUT POLL RETRY_DELAY; do
    eval "value=\$$name"
    case $value in
        '' | *[!0-9]*) fail "$name must be a whole number of seconds" ;;
    esac
done
[ "$POLL" -ge 1 ] || POLL=1
case $PULL_REQUEST in
    '' | *[!0-9]*) [ -z "$PULL_REQUEST" ] || fail "pull-request must be a number" ;;
esac

APPS=$(printf '%s\n' "$NOUST_APP" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d')
[ -n "$APPS" ] || fail "the app input names no application"
COUNT=$(printf '%s\n' "$APPS" | wc -l | tr -d ' ')
for app in $APPS; do
    case $app in
        *[!A-Za-z0-9.-]* | .* | -*) fail "'$app' is not a domain" ;;
    esac
done

# call METHOD PATH [BODY_FILE]: the answer's body is in $TMP/response, its status
# in HTTP_STATUS. Transient network failures and 429 are retried a few times.
call() {
    method=$1
    request_path=$2
    body=${3:-}
    attempt=0
    while :; do
        attempt=$((attempt + 1))
        set -- --silent --show-error --max-time 60 --output "$TMP/response" \
            --dump-header "$TMP/headers" --write-out '%{http_code}' --request "$method" \
            --config -
        if [ -n "$body" ]; then
            set -- "$@" --header 'Content-Type: application/json' --data-binary "@$body"
        fi
        if HTTP_STATUS=$(printf 'header = "Authorization: Bearer %s"\nheader = "Accept: application/json"\n' \
            "$NOUST_TOKEN" | curl "$@" "$URL$request_path" 2>"$TMP/curl-error"); then
            :
        else
            if [ "$attempt" -lt 3 ]; then
                sleep "$RETRY_DELAY"
                continue
            fi
            fail "could not reach $URL: $(cat "$TMP/curl-error")"
        fi
        if [ "$HTTP_STATUS" = 429 ] && [ "$attempt" -lt 5 ]; then
            wait_for=$(sed -n 's/^[Rr]etry-[Aa]fter: *\([0-9][0-9]*\).*/\1/p' "$TMP/headers" | tail -n 1)
            wait_for=${wait_for:-$((RETRY_DELAY * 2))}
            [ "$wait_for" -le 60 ] || wait_for=60
            say "The deploy queue is full; trying again in ${wait_for}s"
            sleep "$wait_for"
            continue
        fi
        return 0
    done
}

# The server's own words for a refusal.
describe_refusal() {
    jq -r 'if type == "object" then
             ((.detail // .message // .error // "") | if type == "string" then . else tostring end)
             + (if (.hint // "") != "" then "\n" + .hint else "" end)
           else tostring end' "$TMP/response" 2>/dev/null || cat "$TMP/response"
}

# The request body: only what was asked for.
build_request() {
    jq -n \
        --arg apps "$APPS" \
        --arg commit "$COMMIT" --arg tag "$TAG" --arg image_tag "$IMAGE_TAG" \
        --arg pull_request "$PULL_REQUEST" \
        --arg force "$FORCE" --arg no_cache "$NO_CACHE" \
        --argjson several "$([ "$COUNT" -gt 1 ] && echo true || echo false)" \
        '(if $several then {apps: ($apps | split("\n"))} else {} end)
         + (if $commit != "" then {commit: $commit} else {} end)
         + (if $tag != "" then {tag: $tag} else {} end)
         + (if $image_tag != "" then {image_tag: $image_tag} else {} end)
         + (if $pull_request != "" then {pull_request: ($pull_request | tonumber)} else {} end)
         + (if $force == "true" then {force: true} else {} end)
         + (if $no_cache == "true" then {no_cache: true} else {} end)' >"$TMP/request.json"
}

build_request
if [ "$COUNT" -gt 1 ]; then
    deploy_path=/api/deploy
else
    deploy_path=/api/apps/$APPS/deploy
fi

say "Asking $URL to deploy: $(printf '%s' "$APPS" | tr '\n' ' ')"
call POST "$deploy_path" "$TMP/request.json"
case $HTTP_STATUS in
    200 | 202) ;;
    401) fail "the server did not accept the token (401). Create one with: noust token create ci --scope deploy" ;;
    403) fail "the token may not deploy (403): it needs the deploy scope, and an application of its team. $(describe_refusal)" ;;
    404) fail "no such application, or it is not visible to this token (404): $NOUST_APP" ;;
    409) fail "the server refused the deploy (409): $(describe_refusal)" ;;
    *) fail "the server answered $HTTP_STATUS: $(describe_refusal)" ;;
esac

jq -e '.results | type == "array"' "$TMP/response" >/dev/null 2>&1 \
    || fail "the server's answer is not a deploy result: $(head -c 300 "$TMP/response")"
cp "$TMP/response" "$TMP/queued.json"

: >"$TMP/results.ndjson"
overall=queued
rank() {
    case $1 in
        failed) echo 6 ;;
        timeout) echo 5 ;;
        cancelled) echo 4 ;;
        success) echo 3 ;;
        nothing-new) echo 2 ;;
        *) echo 1 ;;
    esac
}
worse() {
    if [ "$(rank "$1")" -gt "$(rank "$overall")" ]; then
        overall=$1
    fi
}
first_job=
first_deployment=

# "-" stands for a field with nothing to say: an empty one would be swallowed by read.
jq -r '.results[] | [.domain, .status, (.job_id // "-"), (.detail // "-")] | @tsv' \
    "$TMP/queued.json" >"$TMP/rows.tsv"
deadline=$(($(date +%s) + TIMEOUT))

while IFS="$(printf '\t')" read -r domain state job detail; do
    deployment=
    [ "$job" != - ] || job=
    [ "$detail" != - ] || detail=
    case $state in
        queued | joined) result=queued ;;
        nothing_new)
            result=nothing-new
            say "$domain: nothing new to deploy"
            ;;
        not_found)
            result=failed
            annotate error "Noust deploy of $domain" "$domain was not found"
            ;;
        *)
            result=failed
            annotate error "Noust deploy of $domain" "$state: $detail"
            ;;
    esac
    if [ -n "$job" ]; then
        [ -n "$first_job" ] || first_job=$job
        say "$domain: job $job ($state)"
        if [ "$WAIT" = true ]; then
            last_step=
            while :; do
                call GET "/api/jobs/$job"
                [ "$HTTP_STATUS" = 200 ] || fail "could not read job $job ($HTTP_STATUS): $(describe_refusal)"
                job_status=$(jq -r '.status // ""' "$TMP/response")
                step=$(jq -r '.current_step // ""' "$TMP/response")
                if [ -n "$step" ] && [ "$step" != "$last_step" ]; then
                    say "$domain: $step"
                    last_step=$step
                fi
                case $job_status in
                    completed)
                        result=success
                        break
                        ;;
                    failed)
                        result=failed
                        break
                        ;;
                    cancelled)
                        result=cancelled
                        break
                        ;;
                esac
                if [ "$(date +%s)" -ge "$deadline" ]; then
                    result=timeout
                    break
                fi
                sleep "$POLL"
            done
            deployment=$(jq -r '(.deployment_id // .result.deployment_id // "") | tostring' "$TMP/response")
            [ -z "$deployment" ] || [ -n "$first_deployment" ] || first_deployment=$deployment
            case $result in
                success) say "$domain: deployed${deployment:+ (deployment $deployment)}" ;;
                failed)
                    message=$(jq -r '.error // "the job failed without saying why"' "$TMP/response")
                    annotate error "Noust deploy of $domain" "$message"
                    if [ -n "${GITHUB_ACTIONS:-}" ]; then
                        printf '::group::What Noust reported for %s\n%s\n::endgroup::\n' "$domain" "$message"
                    else
                        printf '%s\n' "$message"
                    fi
                    ;;
                cancelled) annotate warning "Noust deploy of $domain" "the deployment was cancelled" ;;
                timeout)
                    annotate error "Noust deploy of $domain" \
                        "still running after ${TIMEOUT}s; it carries on on the server (job $job)"
                    ;;
            esac
        fi
    fi
    jq -cn --arg domain "$domain" --arg status "$result" --arg job "$job" --arg deployment "$deployment" \
        '{domain: $domain, status: $status}
         + (if $job != "" then {job_id: $job} else {} end)
         + (if $deployment != "" then {deployment_id: ($deployment | tonumber? // $deployment)} else {} end)' \
        >>"$TMP/results.ndjson"
    worse "$result"
done <"$TMP/rows.tsv"

emit status "$overall"
emit job-id "$first_job"
emit deployment-id "$first_deployment"
emit results "$(jq -cs . "$TMP/results.ndjson")"

case $overall in
    failed | timeout | cancelled) exit 1 ;;
esac
