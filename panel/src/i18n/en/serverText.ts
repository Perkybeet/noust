/**
 * The sentences the server says with a code (`src/noust/core/server_text.py`), in English.
 *
 * Each sentence is `serverText.<code>.text` and, when it has a fix, `serverText.<code>.hint`: the
 * server's own catalog spells the fix as the code plus `.hint`, and a nested catalog cannot hold a
 * text and a branch under one key. The words are the server's, whole sentences with the values it
 * sends as `{placeholders}`; `ServerText` (components/ui/ServerText.tsx) looks a code up here and
 * shows the server's English when this console has no entry for it. A command is a value
 * (`{command}`), shown as it is.
 *
 * `src/api/serverTextCodes.gen.ts` lists the codes and the values each takes, and
 * `serverText.test.ts` fails for a code that this catalog or the Spanish one lacks, or that says
 * it with other values. A new sentence is added in the server first, `python
 * scripts/generate_server_text.py` writes the table, and this catalog and the Spanish one follow.
 */
export const serverText = {
  actors: {
    through_central: {
      text: "{person} (from the central {central})",
    },
  },
  approvals: {
    asked_approved: {
      text: "This needs a second person's approval, whoever you are. Request {request} is approved: send the call again with it now.",
      hint: "Once approved, send exactly the same call again with the header {header}: {request}, within {minutes} minutes.",
    },
    asked_waiting: {
      text: "This needs a second person's approval, whoever you are. Request {request} is waiting for an account with one of these roles to decide it: {approvers}.",
      hint: "Once approved, send exactly the same call again with the header {header}: {request}, within {minutes} minutes.",
    },
    central_missing: {
      text: "This needs a second person's approval on the central.",
      hint: "Ask for it on the central and retry through it once it is approved.",
    },
    held_by_policy_assistant: {
      text: "This call waits for a person to approve it: the approval policy of this server asks for it when the built-in assistant changes {permission} in the {environment} environment. Request {request} is waiting.",
      hint: "Once approved, send exactly the same call again with the header {header}: {request}, within {minutes} minutes.",
    },
    held_by_policy_token: {
      text: "This call waits for a person to approve it: the approval policy of this server asks for it when a token changes {permission} in the {environment} environment. Request {request} is waiting.",
      hint: "Once approved, send exactly the same call again with the header {header}: {request}, within {minutes} minutes.",
    },
    never_assistant_floor: {
      text: "The built-in assistant does not do this on its own, whatever the approval policy says.",
      hint: "A person does it from the console, in their own session. This limit is not configurable.",
    },
    never_by_policy_assistant: {
      text: "The approval policy of this server does not let the built-in assistant change {permission} in the {environment} environment.",
      hint: "Somebody who holds the security.manage permission changes the policy, in sudo mode. A person can do this call from the console.",
    },
    never_by_policy_token: {
      text: "The approval policy of this server does not let a token change {permission} in the {environment} environment.",
      hint: "Somebody who holds the security.manage permission changes the policy, in sudo mode. A person can do this call from the console.",
    },
    reason_required: {
      text: "Say why: this call needs a second person's approval, and a reason.",
      hint: "Send the reason in the {header} header (percent-encoded).",
    },
  },
  assistant: {
    limit: {
      actions: {
        text: "The assistant already made {limit} changes in the last hour, the most it may",
        hint: "Wait a little, or make the change yourself. A person with the settings.manage permission can raise this limit in the assistant's settings.",
      },
      actions_near: {
        text: "The assistant has made {used} of the {limit} changes it may make in an hour",
      },
      conversation_spend: {
        text: "This conversation reached its spending limit of {limit} {currency}",
        hint: "Start a new conversation. A person with the settings.manage permission can raise this limit in the assistant's settings.",
      },
      conversation_spend_near: {
        text: "This conversation has spent {used} of its {limit} {currency}",
      },
      estimated: {
        text: "The provider does not report how many tokens it used, so they are estimated from the characters",
      },
      input_tokens: {
        text: "This conversation reached its limit of {limit} input tokens",
        hint: "Start a new conversation. A person with the settings.manage permission can raise this limit in the assistant's settings.",
      },
      input_tokens_near: {
        text: "This conversation has used {used} of its {limit} input tokens",
      },
      month_spend: {
        text: "The assistant reached its spending limit for this month, {limit} {currency}",
        hint: "The month starts again on the first, UTC. A person with the settings.manage permission can raise this limit in the assistant's settings.",
      },
      month_spend_near: {
        text: "The assistant has spent {used} of its {limit} {currency} for this month",
      },
      no_price: {
        text: "There is no price for the model {model}, so spending is not limited, only tokens",
        hint: "Add its price per million tokens in the assistant's settings to get a spending limit.",
      },
      steps: {
        text: "The assistant stopped after {limit} steps in one answer",
        hint: "Ask it to go on if it should. A person with the settings.manage permission can raise this limit in the assistant's settings.",
      },
      steps_near: {
        text: "The assistant has used {used} of the {limit} steps it may take in one answer",
      },
    },
    off: {
      text: "The assistant was turned off, so what it was doing stopped",
      hint: "A person with the settings.manage permission turns it on again in the assistant's settings.",
    },
    probe: {
      answers: {
        text: "The provider at {host} answers",
      },
      memory_low: {
        text: "This machine has {memory_gb} GB of memory and no GPU: a model that runs on it will not have the room to investigate",
        hint: "Use a provider's API, or a machine on your network with a GPU or at least 16 GB. Noust does not promise how a model would run here.",
      },
      model_missing: {
        text: "The provider answers, but its list of models does not include {model}",
        hint: "Check the model's name against the provider's own list: the names it gives are the ones that work.",
      },
      no_tools: {
        text: "The model {model} did not make the test call to a tool the way the protocol asks, so the assistant can explain what you show it but not investigate or act",
        hint: "Choose a model made to call tools (the provider's documentation says which), or a server that reads them: vLLM needs its tool-call parser switched on.",
      },
      tools_ok: {
        text: "The model {model} made the test call to a tool correctly, so the assistant can investigate with it",
      },
    },
    provider: {
      bad_address: {
        text: "{address} is not the base address of a provider",
        hint: "Give the address up to and including its version, such as https://api.example.org/v1, with no user name, password, query or fragment. The key is given apart.",
      },
      bad_reply: {
        text: "The provider's answer could not be read as Chat Completions",
        hint: "Check that the address is that of a server that speaks the OpenAI-compatible protocol and ends at its version, such as /v1.",
      },
      cancelled: {
        text: "The request to the provider was cancelled",
      },
      cut: {
        text: "The provider cut its answer short",
        hint: "Try again. If it keeps happening with this model, the model's server may be running out of memory.",
      },
      destination_refused: {
        text: "The assistant may not call {host}: it resolves to {address}, inside a private or internal network",
        hint: "If the model really runs there, list the host in security.allow_private_hosts: that says this server may call it.",
      },
      insecure_address: {
        text: "{host} would be reached over plain http://, which sends the key and what the assistant reads of this server unprotected",
        hint: "Use an https:// address. A model on this machine or on your own network can use http:// once its host is listed in security.allow_private_hosts.",
      },
      not_found: {
        text: "The provider answered that something is not there (HTTP {status}): {message}",
        hint: "Check the model's name and the address: it must end at the provider's version, such as /v1, and the model must be one the provider has.",
      },
      rate_limited: {
        text: "The provider is limiting how fast it answers (HTTP {status}) and went on doing so after {tries} tries",
        hint: "Wait a little and ask again. The limits are set in the provider's own account.",
      },
      redirected: {
        text: "The provider answered with a redirect to {location}, which the assistant never follows",
        hint: "Use the address it redirects to, if that is the right one.",
      },
      rejected: {
        text: "The provider rejected the request (HTTP {status}): {message}",
        hint: "Its own words above say what it did not accept. A model that is not made to call tools is one reason: the probe says whether this one does.",
      },
      reported_error: {
        text: "The provider reported an error in the middle of its answer: {message}",
      },
      server_error: {
        text: "The provider failed (HTTP {status}) and went on failing after {tries} tries: {message}",
        hint: "Try again later. If it stays, the provider's status page says why.",
      },
      timeout: {
        text: "The provider did not answer within {seconds} seconds",
        hint: "Try again. A model on a small machine can take longer than that to read a long conversation: a smaller or a faster one answers sooner.",
      },
      too_large: {
        text: "The provider's answer is larger than {megabytes} MB",
        hint: "Ask for less, or choose another model.",
      },
      unauthorized: {
        text: "The provider refused the key (HTTP {status}): {message}",
        hint: "Check the key in the assistant's settings, and that it is allowed to use the model.",
      },
      unreachable: {
        text: "The provider at {host} could not be reached: {reason}",
        hint: "Check that the address and the port are right and that the model's server is running.",
      },
      unresolved: {
        text: "The name {host} did not resolve to any address",
        hint: "Check the spelling of the address and that this server's DNS can look it up.",
      },
    },
    unavailable: {
      account: {
        text: "The assistant acts for a person, and this is not an account",
        hint: "Sign in with your own account.",
      },
      off: {
        text: "The assistant is off",
        hint: "A person with the settings.manage permission can turn it on in the assistant's settings.",
      },
      permission: {
        text: "Your role does not allow using the assistant",
        hint: "An administrator can give you a role that holds assistant.use.",
      },
    },
  },
  authdns: {
    check: {
      ds_dangling: {
        text: "The parent zone holds a DS record for {zone}, and the zone is not signed",
        hint: "Resolvers that validate refuse the domain. Remove the DS at your registrar, or sign the zone.",
      },
      ds_mismatch: {
        text: "The DS record the parent zone holds for {zone} is not the one of this zone's key",
        hint: "Replace it at your registrar with the one in the steps.",
      },
      ds_missing: {
        text: "The parent zone has no DS record for {zone}, which is signed",
        hint: "Add it at your registrar, after the name servers have changed.",
      },
      no_public_answer: {
        text: "No public resolver answered, so the delegation of {zone} could not be checked",
        hint: "Check this server's outbound access to port 53 of the public resolvers.",
      },
      not_authoritative: {
        text: "{server} at {address} answered, but not as the authority for {zone}",
        hint: "The zone is not loaded there yet, or another program answers on port 53.",
      },
      not_delegated: {
        text: "Resolvers find {found} for {zone}, not your name servers",
        hint: "Change the name servers at your registrar and wait for its TTL to pass.",
      },
      out_of_sync: {
        text: "{server} serves serial {serial} of {zone} and {primary} serves {primary_serial}",
        hint: "A secondary copies a zone a few seconds after the primary changes it. If this lasts, check that the primary can reach it on port 53.",
      },
      recurses: {
        text: "{server} at {address} answers questions about other domains",
        hint: "A name server open to recursion can be used to attack others. Noust DNS never does this: another program is answering on that address.",
      },
      silent: {
        text: "{server} at {address} did not answer for {zone}",
        hint: "Port 53 over UDP and TCP must be open to the internet on {address}, in the firewall and in your provider's panel.",
      },
    },
    delegation: {
      check: {
        text: "Check that resolvers find your name servers",
        hint: "Run: {command}",
      },
      ds: {
        text: "Once the name servers have changed, add this DS record to {zone} at your registrar: {ds}",
        hint: "Enter the key tag, the algorithm, the digest type and the digest in the order your registrar's form asks for them. Adding the DS before the name servers changed makes resolvers that validate refuse the domain.",
      },
      glue: {
        text: "At your registrar, register {hosts} as name servers with these addresses: {addresses}",
        hint: "A name server inside the domain it serves can only be found through addresses the registrar itself holds (glue records). Your registrar may call them host records or child name servers.",
      },
      nameservers: {
        text: "At your registrar, set the name servers of {zone} to {nameservers}",
        hint: "Look for custom name servers or DNS delegation in the domain's settings. The change reaches resolvers after the registrar's TTL, often a few hours and at most two days.",
      },
      one_server: {
        text: "Only one name server answers for {zone}",
        hint: "If it is down, {zone} is down. Add a secondary on another server, in another network, and name it in the zone.",
      },
      same_network: {
        text: "{first} and {second} are in the same network",
        hint: "One outage there takes both. Put one of them in another network.",
      },
    },
    warning: {
      amplification: {
        text: "A name server on the internet can be used to flood other people with traffic",
        hint: "Noust DNS never recurses, limits how fast it answers one address and hands zone transfers only to the servers you name, which keeps it a poor tool for that. It still answers on port 53 for anyone, so keep it updated.",
      },
      availability: {
        text: "A name server is a service the whole internet depends on: if it is down, every name it serves is down",
        hint: "Run at least two, on servers in different networks, and keep their operating systems updated.",
      },
    },
  },
  controls: {
    asked_by_framework: {
      text: "The control {control} asks for it, and the framework in force ({frameworks}) asks for that control, so it stays on while the framework is on and cannot be turned off by itself.",
      hint: "To turn the framework off (Settings > Compliance):\n  {command}",
    },
    asked_by_setting: {
      text: "The control {control} asks for it, and it was turned on in Settings > Compliance (or with the command line). It applies to everybody, whatever their role.",
      hint: "To turn it off there, or:\n  {command}",
    },
  },
  database_engines: {
    conflict_mysql: {
      text: "{installed} is installed, and {requested} cannot run beside it: they share port 3306, their packages and /var/lib/mysql.",
      hint: "Remove {installed} first if you mean to replace it.",
    },
    conflict_redis: {
      text: "{installed} is installed, and {requested} cannot run beside it: they share port 6379 and their packages.",
      hint: "Remove {installed} first if you mean to replace it.",
    },
    no_package_manager: {
      text: "Noust installs {engine} with apt, dnf or zypper, and this system has none of them.",
      hint: "Run it as an instance, a container that needs no package manager ({command}), or install it with the distribution's package manager: Noust manages it from there.",
    },
    not_available: {
      text: "Noust cannot install {engine} on {system}",
      hint: "Run it as an instance, a container that needs no package manager ({command}), or install it with the system's package manager: Noust manages it from there.",
    },
    redis_not_shipped: {
      text: "{system} does not ship Redis",
      hint: "Install Valkey instead: run '{command}'.",
    },
    valkey_not_shipped: {
      text: "{system} does not ship Valkey",
      hint: "Valkey is packaged from Debian 13 and Ubuntu 24.04 on. Install Redis instead: run '{command}'.",
    },
  },
  database_servers: {
    default_app: {
      text: "{key} is the database of an application's stack, so it cannot be the default",
      hint: "A default is the system's engine or an instance Noust runs. Make one with: {command}",
    },
    default_not_ready: {
      text: "{key} is {state}, so it cannot be the default yet",
      hint: "Wait for it to finish, then choose it again.",
    },
    unknown: {
      text: "There is no database server {key}",
      hint: "List them with: {command}",
    },
    unsupported_ended: {
      text: "{name} {version} has had no security fixes since {end_of_life}",
      hint: "Run it only for an application that needs it, and leave it off the internet. To go ahead, confirm that you accept running it: unsupported_ok in the API, --accept-unsupported on the command line.",
    },
    unsupported_ending: {
      text: "{name} {version} stops getting security fixes on {end_of_life}",
      hint: "A server made today would run for a long time without them. To go ahead, confirm that you accept it: unsupported_ok in the API, --accept-unsupported on the command line.",
    },
  },
  databases: {
    already_added: {
      text: "The database {name} on {engine} is already added to Noust",
    },
  },
  deploy_failure: {
    app_unhealthy: { text: "{domain} did not pass its startup check" },
    build_failed: { text: "Build failed" },
    build_oom: { text: "Build killed due to insufficient memory (exit code 137)" },
    build_timeout: { text: "Build did not finish within {seconds}s and was stopped" },
    install_failed: { text: "Dependency installation failed" },
    install_oom: { text: "Dependency installation ran out of memory and was killed" },
    install_timeout: { text: "Dependency installation did not finish within {seconds}s and was stopped" },
    release_unhealthy: { text: "Release {release} did not pass its startup check" },
    release_unhealthy_back: { text: "Release {release} did not pass its startup check; release {previous} is active again" },
    release_unhealthy_down: { text: "Release {release} did not pass its startup check; release {previous} is active again but is not answering either" },
    rollback_unhealthy: { text: "{domain} did not pass its startup check after the rollback" },
    stack_back: { text: "The update of {domain} did not pass its startup check; the containers that were serving are running again" },
    stack_back_down: { text: "The update of {domain} did not pass its startup check; the previous containers are back but are not answering either" },
    stack_nothing_before: { text: "The update of {domain} did not pass its startup check; nothing was running before it, so nothing was put back" },
    stack_not_restored: { text: "The update of {domain} did not pass its startup check; what was serving could not be put back entirely" },
    stack_unhealthy: { text: "The update of {domain} did not pass its startup check" },
  },
  deploy_here: {
    engine: {
      no_client: {
        text: "This image has no Docker client",
        hint: "The central's image carries Docker's command line from 4.3 on: pull the current image and recreate the container.",
      },
      ok: {
        text: "Docker {version} answers, speaking API {api}, with Compose {compose}",
      },
    },
    folder: {
      missing_inside: {
        text: "{folder} does not exist inside this container",
        hint: "Mount the folder into the central at the path it has on the machine, {folder}:{folder}, and recreate the container.",
      },
      missing_outside: {
        text: "Docker cannot find {folder} on the machine",
        hint: "The folder exists inside this container under another path than it has outside, so what a stack keeps in it would land somewhere else. Mount it at the same path on both sides, {folder}:{folder}, and recreate the container.",
      },
      ok: {
        text: "{folder} is the same folder inside this container and on the machine",
      },
      other: {
        text: "{folder} is another folder on the machine than inside this container",
        hint: "Something else is at {folder} on the machine. Mount the folder you mean at the same path inside the container, so that both sides name one folder.",
      },
      unverified: {
        text: "Noust could not check {folder}: {reason}",
        hint: "The check starts a small container, from an image the machine already has or one it pulls. What Docker said is above.",
      },
      unwritable: {
        text: "Noust cannot write in {folder}",
        hint: "Give the folder to the account the central runs as, on the machine: {command}",
      },
    },
    group: {
      denied: {
        text: "Noust runs as {user} and may not use Docker's socket, which belongs to the group {group} ({gid})",
        hint: "Give the central's container that group: under its service in the compose file put group_add with {gid} as its entry, and recreate it. Find the number on the machine with: {command}",
      },
      ok: {
        text: "Noust may use Docker's socket, which belongs to the group {group}",
      },
    },
    setting: {
      off: {
        text: "This central is not set to deploy on this machine",
        hint: "Add {variable} to the environment of the central's container, beside Docker's socket, and recreate it. It is asked for there on purpose, and the console cannot do it for you.",
      },
      on: {
        text: "This central deploys containers on this machine's Docker",
      },
      server: {
        text: "This Noust is a server: it deploys on this machine without being asked",
      },
    },
    socket: {
      found: {
        text: "Docker's socket is mounted at {socket}",
      },
      missing: {
        text: "There is no Docker socket at {socket}",
        hint: "Mount the machine's socket into the central: add {socket}:{socket} to the volumes of its compose file and recreate the container.",
      },
    },
    unchecked: {
      docker: {
        text: "Not checked yet: Docker does not answer",
      },
      socket: {
        text: "Not checked yet: Docker's socket cannot be used",
      },
    },
  },
  diagnose: {
    blue_green: {
      idle_failed: {
        text: "{serving} serves on port {port}; {idle} is {idle_state}; its last start failed, so {serving} kept serving",
      },
      idle_failed_release: {
        text: "{serving} serves release {release} on port {port}; {idle} is {idle_state}; its last start failed, so {serving} kept serving",
      },
      leftover: {
        text: "{unit}.service still runs beside the instances; the next activation will refuse to start blue on port {port}",
      },
      no_instance: {
        text: "Zero-downtime mode is on but no instance is recorded as serving",
      },
      off: {
        text: "Not in zero-downtime mode",
      },
      serving: {
        text: "{serving} serves on port {port}; {idle} is {idle_state}",
      },
      serving_release: {
        text: "{serving} serves release {release} on port {port}; {idle} is {idle_state}",
      },
      upstream_mismatch: {
        text: "nginx proxies to {upstream_port}, but {serving} serves on {port}",
      },
      upstream_none: {
        text: "nginx proxies to no upstream, but {serving} serves on {port}",
      },
    },
    cause: {
      certificate_expired: {
        text: "The TLS certificate for {domain} expired on {expiry}.",
      },
      crash_loop: {
        text: "The process exits with status {status} at start; see the last journal lines below.",
      },
      descriptors: {
        text: "The console ran out of file descriptors (Too many open files) while it was checking {domain}, so the checks it could not run say nothing about the application. Restart the console to free them: {command}",
      },
      disk_full: {
        text: "The disk holding the apps directory is {percent}% full.",
      },
      nginx_status: {
        text: "The app answers directly, but nginx returns {status} for {domain}; nginx cannot reach the app.",
      },
      nginx_unreachable: {
        text: "The app answers directly, but nginx cannot connect ({error}) for {domain}; nginx cannot reach the app.",
      },
      no_unit: {
        text: "No systemd unit found for {unit}; it has never been started or its unit was removed.",
      },
      oom: {
        text: "Killed by the kernel for running out of memory (limit or machine).",
      },
      oom_limit: {
        text: "The kernel killed {domain} for running out of memory: its limit is {limit} and it used up to {peak}, so it needs more than it is allowed. Raise the limit with: {command}",
      },
      oom_machine: {
        text: "The kernel killed {domain} for running out of memory and it has no memory limit, so the server ran out of memory (it used up to {peak}). Free memory on the server, or give the application a limit it can keep to: {command}",
      },
      oom_unit: {
        text: "systemd records that the last run of {domain} ended because the kernel killed it for running out of memory. See its memory limit with: {command}",
      },
      port_elsewhere: {
        text: "Listening on {actual_port}, Noust routes to {recorded_port}.",
      },
      port_silent: {
        text: "The process is running but not listening on port {port}.",
      },
      unit_failed: {
        text: "The unit failed to start (Result={result}, exit status {status}); see the last journal lines below.",
      },
      upstream_mismatch: {
        text: "nginx proxies {domain} to {upstream_port}, but the {serving} instance listens on {port}.",
      },
      upstream_none: {
        text: "nginx proxies {domain} to no upstream, but the {serving} instance listens on {port}.",
      },
      worker: {
        text: "The worker's {summary}; see their output below.",
      },
      worker_port: {
        text: "{domain} publishes no port, yet port {port} is recorded for it: clear it with {command}.",
      },
    },
    certificate: {
      bad_expiry: {
        text: "Unreadable expiry date for {label}",
      },
      expired: {
        text: "The certificate for {label} expired on {expiry}",
      },
      expires_one: {
        text: "The certificate for {label} expires in 1 day",
      },
      expires_several: {
        text: "The certificate for {label} expires in {days} days",
      },
      missing: {
        text: "TLS is enabled for {domain} but no certificate was found",
      },
      no_expiry: {
        text: "Could not read the expiry date for {label}",
      },
      none: {
        text: "No certificate found for {domain}",
      },
      valid_one: {
        text: "The certificate for {label} is valid for 1 more day",
      },
      valid_several: {
        text: "The certificate for {label} is valid for {days} more days",
      },
    },
    check: {
      incomplete: {
        text: "Could not complete this check",
      },
    },
    containers: {
      no_app: {
        text: "No application recorded",
      },
      publishes_port: {
        text: "The stack publishes a port: judged as a web below",
      },
      worker: {
        text: "A worker: {summary}",
      },
      worker_with_port: {
        text: "A worker: {summary}; port {port} is recorded for it, and it publishes none. Clear it with: {command}",
      },
    },
    descriptors: {
      exhausted: {
        text: "The console ran out of file descriptors while checking",
      },
    },
    disk: {
      usage: {
        text: "{free} free ({percent}% used) on the filesystem holding {path}",
      },
    },
    http_direct: {
      no_port: {
        text: "No backend port to probe directly",
      },
      status: {
        text: "HTTP {status} from 127.0.0.1:{port}",
      },
      unreachable: {
        text: "Could not reach the app directly on port {port}",
      },
    },
    http_nginx: {
      status: {
        text: "HTTP {status} from nginx for Host: {domain}",
      },
      unreachable: {
        text: "Could not reach nginx for {domain}",
      },
    },
    journal: {
      empty: {
        text: "No journal entries for {unit_file}",
      },
      lines_one: {
        text: "Last journal line for {unit_file}",
      },
      lines_several: {
        text: "Last {count} journal lines for {unit_file}",
      },
      no_unit: {
        text: "No unit named {unit} to read logs from",
      },
      skipped_php: {
        text: "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, no unit to read logs from",
      },
      skipped_static: {
        text: "Static site: served directly by the web server, no unit to read logs from",
      },
      unreadable: {
        text: "Could not read the journal for {unit_file}",
      },
    },
    last_deployment: {
      cancelled: {
        text: "The last deployment was cancelled; what served before still serves",
      },
      cancelled_by: {
        text: "The last deployment was cancelled by {actor}; what served before still serves",
      },
      failed: {
        text: "The last deployment failed",
      },
      failed_with: {
        text: "The last deployment failed: {error}",
      },
      none: {
        text: "No deployment recorded for this domain",
      },
      state: {
        text: "The last deployment is {status}",
      },
      succeeded: {
        text: "The last deployment succeeded",
      },
    },
    nginx_log: {
      clean: {
        text: "No nginx error log lines mention this domain",
      },
      lines_one: {
        text: "1 nginx error log line mentions this domain or an upstream",
      },
      lines_several: {
        text: "{count} nginx error log lines mention this domain or an upstream",
      },
      unreadable: {
        text: "Could not read {path}",
      },
    },
    oom: {
      found: {
        text: "The kernel log shows OOM activity in the last 7 days",
      },
      found_no_cgroup_v2: {
        text: "The kernel log shows OOM activity in the last 7 days, and this server cannot say which application it was: it does not use cgroup v2, where the kernel counts kills by unit",
      },
      found_no_file: {
        text: "The kernel log shows OOM activity in the last 7 days, and this server cannot say which application it was: the unit's memory.events cannot be read",
      },
      killed_one: {
        text: "The kernel killed this application for running out of memory once since its unit started ({since})",
      },
      killed_several: {
        text: "The kernel killed this application for running out of memory {count} times since its unit started ({since})",
      },
      none: {
        text: "No OOM kills in the kernel log in the last 7 days",
      },
      unit_result: {
        text: "systemd records that the last run of this application's unit ended because the kernel killed it for running out of memory",
      },
      unreadable: {
        text: "Could not read the kernel log",
      },
    },
    php_fpm: {
      config_refused: {
        text: "PHP-FPM refuses its configuration; see {command} below.",
      },
      no_answer: {
        text: "The PHP-FPM pool does not answer the startup check; see PHP's errors below.",
      },
      no_record: {
        text: "No application record",
      },
      ok: {
        text: "The PHP-FPM pool answers ({service} {state})",
      },
      pool_disabled: {
        text: "The PHP-FPM pool is disabled (stopped); start it with {command}.",
      },
      pool_missing: {
        text: "The PHP-FPM pool file {pool_file} is missing; redeploy with {command}.",
      },
      service_down: {
        text: "{service}, which runs every PHP pool, is {state}.",
      },
    },
    port: {
      elsewhere: {
        text: "The app listens on {actual_port}, not the recorded port {recorded_port}",
      },
      listening: {
        text: "Port {port} is listening",
      },
      listening_process: {
        text: "Port {port} is listening ({processes})",
      },
      not_recorded: {
        text: "No port recorded for this app",
      },
      silent: {
        text: "Nothing is listening on port {port}",
      },
      skipped_php: {
        text: "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, no backend port to check",
      },
      skipped_static: {
        text: "Static site: served directly by the web server, no backend port to check",
      },
    },
    unit: {
      active: {
        text: "{unit_file} is active (running)",
      },
      active_unmanaged: {
        text: "{unit_file} is active (running) (not managed by Noust)",
      },
      failed: {
        text: "{unit_file} failed (Result={result})",
      },
      failed_unmanaged: {
        text: "{unit_file} failed (not managed by Noust) (Result={result})",
      },
      missing: {
        text: "No systemd unit found for {unit}",
      },
      restarting: {
        text: "{unit_file} is restarting ({restarts} restarts so far)",
      },
      restarting_unmanaged: {
        text: "{unit_file} is restarting (not managed by Noust) ({restarts} restarts so far)",
      },
      skipped_php: {
        text: "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, no unit",
      },
      skipped_static: {
        text: "Static site: served directly by the web server, no unit",
      },
      state: {
        text: "{unit_file} is {active_state}/{sub_state}",
      },
      state_unmanaged: {
        text: "{unit_file} is {active_state}/{sub_state} (not managed by Noust)",
      },
    },
    zero_downtime: {
      could: {
        text: "Its updates restart it in place; it could update without a cut",
      },
    },
  },
  docker: {
    compose: {
      missing: {
        text: "Docker Compose v2 is not installed",
        hint: "Install the Compose plugin: {command}",
      },
      too_old: {
        text: "Docker Compose {version} cannot rehearse the adoption of a stack that already runs; {needed} or later can",
        hint: "Updating the Compose plugin is enough: deploying and updating work as they are.",
      },
      v1: {
        text: "Only Docker Compose 1 (docker-compose) is installed, and Noust needs Compose v2, which is 'docker compose'",
        hint: "Install Compose v2 as {url} explains.",
      },
      version: { text: "Docker Compose {version}" },
    },
    engine: {
      image_store: { text: "Images are kept in the {store} image store ({driver})" },
      missing: {
        text: "Docker is not installed on this server",
        hint: "Install it from Server > Software, or run: {command}",
      },
      remote: { text: "Noust reaches Docker at {host}, not through a socket of this machine" },
      rootless: {
        text: "This Docker runs without root: it belongs to one user, and so do the containers it makes",
      },
      socket: {
        text: "Noust reaches Docker through {socket}, which belongs to {owner}:{group} ({mode})",
      },
      too_old: {
        text: "Docker {version} speaks API {api}, and Noust needs API {minimum} or later",
        hint: "Update Docker. Noust asks the engine which API it speaks and works with every engine from API {minimum} on, the one of a NAS included.",
      },
      unreachable: {
        text: "Docker is installed but its daemon does not answer: {reason}",
        hint: "Start it with {command}, and check that the account Noust runs as may use {socket}.",
      },
      version: { text: "Docker {version}, speaking API {api}" },
    },
  },
  doctor: {
    config: {
      apps_dir_missing: {
        text: "Apps directory: {path} not found",
      },
      apps_dir_ok: {
        text: "Apps directory: {path}",
      },
      file_missing: {
        text: "Config file: {path} not found",
      },
      file_ok: {
        text: "Config file: {path}",
      },
      log_dir_missing: {
        text: "Log directory: {path} not found",
      },
      log_dir_ok: {
        text: "Log directory: {path}",
      },
    },
    cpu: {
      below_floor: {
        text: "Processors: {amount} (Noust needs at least {floor} to run)",
      },
      below_minimum: {
        text: "Processors: {amount} (a server is sized for at least {minimum})",
      },
      note: {
        text: "Noust and a build need at least one.",
      },
      ok: {
        text: "Processors: {amount} (minimum {minimum})",
      },
      unreadable: {
        text: "Processors: could not be read",
      },
    },
    cron: {
      without_environment: {
        text: "The task {name} of {domain} runs without the application's .env and tools: it was written before a task loaded them",
      },
    },
    disk: {
      below_floor: {
        text: "Free disk: {free} free of {total} at {path}, under the {floor} Noust needs to run",
      },
      below_minimum: {
        text: "Free disk: {free} free of {total} at {path}, under the {minimum} a server is sized for",
      },
      note: {
        text: "'noust server cleanup' removes what Noust can prove is unused; every release on disk keeps its code and dependencies.",
      },
      ok: {
        text: "Free disk: {free} free of {total} at {path} (minimum {minimum})",
      },
      unreadable: {
        text: "Free disk: could not be read",
      },
    },
    limits: {
      default: {
        text: "{unit} sets no limit of open files, so it has the system's default; a unit written now gets {new}",
      },
      old: {
        text: "{unit} is limited to {limit} open files, and a unit written now gets {new}",
      },
    },
    memory: {
      below_floor: {
        text: "Memory: {amount}, under the {floor} Noust needs to run",
      },
      below_floor_swap: {
        text: "Memory: {amount}, swap {swap}, under the {floor} Noust needs to run",
      },
      below_minimum: {
        text: "Memory: {amount}, under the {minimum} a server is sized for",
      },
      below_minimum_swap: {
        text: "Memory: {amount}, swap {swap}, under the {minimum} a server is sized for",
      },
      note: {
        text: "Builds are what need memory; a swap file lets one that needs more than the server has finish slowly instead of being killed.",
      },
      ok: {
        text: "Memory: {amount} (minimum {minimum})",
      },
      ok_swap: {
        text: "Memory: {amount}, swap {swap} (minimum {minimum})",
      },
      unreadable: {
        text: "Memory: could not be read",
      },
    },
    names: {
      explain: {
        text: "Nothing here stops working: Noust accepts the old names while they are met. 'noust setup rename' finishes what can be finished without restarting anything.",
      },
    },
    node: {
      not_needed: {
        text: "node: not installed (only Node.js applications need it)",
      },
      npm_missing: {
        text: "npm: not installed, although it ships with Node.js",
      },
    },
    platform: {
      best_effort: {
        text: "{label}: best effort, not a tested platform",
      },
      eol: {
        text: "{release} stops receiving security updates on {date}",
      },
      explain: {
        text: "The platforms Noust installs on are listed in docs/PLATFORMS.md",
      },
      pip_only: {
        text: "{label}: installed with pip, there is no package for it",
      },
      supported: {
        text: "{label}: supported",
      },
      unsupported: {
        text: "{label}: not a platform Noust declares support for",
      },
    },
    port: {
      container: {
        text: "Port {port}: published by a container ({holders})",
      },
      container_note: {
        text: "Another platform's proxy or a Compose stack holds it, so nginx cannot listen on it until that stops.",
      },
      free: {
        text: "Port {port}: free (the web server is not listening on it yet)",
      },
      held: {
        text: "Port {port}: held by {holders}, not by the web server",
      },
      held_note: {
        text: "A certificate is asked for on port 80 and sites are served on 443: the web server cannot start while another program holds them.",
      },
      web_server: {
        text: "Port {port}: {holders}",
      },
    },
    program: {
      installed: {
        text: "{command}: installed",
      },
      missing: {
        text: "{command}: not installed",
      },
      optional_missing: {
        text: "{command}: not installed (optional)",
      },
      version: {
        text: "{command}: {version}",
      },
    },
    python: {
      missing: {
        text: "python3: not installed, needed for Python apps",
      },
    },
    relays: {
      left: {
        text: "{domain}: {what}",
      },
      unread: {
        text: "Not checked: {reason}",
      },
    },
    ssh: {
      github_connected: {
        text: "GitHub SSH: connected",
      },
      github_note: {
        text: "Add your key to GitHub: https://github.com/settings/keys",
      },
      github_refused: {
        text: "GitHub SSH: not configured",
      },
      github_untested: {
        text: "GitHub SSH: not tested here",
      },
      key_ok: {
        text: "SSH key: {path}",
      },
      no_key: {
        text: "SSH key: not found",
      },
    },
    stapling: {
      site: {
        text: "{site} still asks nginx to staple OCSP responses, which Let's Encrypt stopped giving in August 2025: nginx logs a warning about it at every reload. Noust writes the site again without it the next time it writes the site (a deployment, a change of its domains)",
      },
    },
    store: {
      orphan: {
        text: "{path} is an empty file left where the store would be: it is not the store, and the move replaces it",
      },
      outside: {
        text: "Noust's store is in {store}, outside {state}, the state directory of this server: what looks only there (a copy of that directory, a package upgrade) does not find it",
      },
    },
    web: {
      none: {
        text: "Web server: not installed",
      },
      running: {
        text: "  {unit} service: running",
      },
      stopped: {
        text: "  {unit} service: not running",
      },
    },
  },
  features: {
    containers_only: {
      text: "{feature}: not available here, which deploys containers and nothing else",
      hint: "This central deploys containers on its machine's Docker (central.deploy_here = containers). {feature} needs systemd or a web server on the machine, which a container does not have. Deploy it on a server of your fleet instead: pick the server in the console, or run the command against a node.",
    },
    hub_role: {
      text: "{feature}: not available on this central, which is a hub",
      hint: "A hub (central.role = hub) manages other servers and deploys nothing itself. Pick the server in the console, or run the command against a node. On a machine that should also deploy, set '{command}'; a container cannot, it has no nginx or systemd.",
    },
    needs_journal: {
      text: "This machine has no journal to read",
      hint: "Noust reads the system's log from journald (journalctl), which is not here.",
    },
    needs_root: {
      text: "Noust does not run as root on this machine",
      hint: "Changing the system takes root, and Noust runs here as an account that cannot.",
    },
    needs_systemd: {
      text: "This machine does not run systemd",
      hint: "Noust keeps services and timers with systemd, which a container, or a system that boots another way, does not have.",
    },
    needs_web_server: {
      text: "There is no web server on this machine",
      hint: "Noust writes its sites for nginx or Apache: install one from Server > Software.",
    },
  },
  fleet: {
    node_descriptors_exhausted: {
      text: "{node} ran out of file descriptors",
      hint: "Its console cannot open a file or a socket until some are freed. On {node}, run: {command}. Then run 'noust setup doctor' there: it says whether the console's unit still has the limit from before 4.3 (1024 files).",
    },
    refused: {
      permission: {
        text: "Your role {role} does not hold the permission {permission} on these servers",
        hint: "Ask a security officer of each of them for an account with a role that holds it, or have that permission added to your role there.",
      },
    },
  },
  fleet_job: {
    apps_restart: {
      single: { text: "Restart applications on 1 server" },
      several: { text: "Restart applications on {count} servers" },
    },
    apps_update: {
      single: { text: "Update applications on 1 server" },
      several: { text: "Update applications on {count} servers" },
    },
    backups_run: {
      single: { text: "Back up applications now on 1 server" },
      several: { text: "Back up applications now on {count} servers" },
    },
    backups_verify: {
      single: { text: "Verify the newest backups on 1 server" },
      several: { text: "Verify the newest backups on {count} servers" },
    },
    certs_renew: {
      single: { text: "Renew certificates on 1 server" },
      several: { text: "Renew certificates on {count} servers" },
    },
    database_transfer: {
      single: { text: "Move a database to another server" },
      several: { text: "Move a database to {count} servers" },
    },
    dns_cluster: {
      single: { text: "Set up Noust DNS on 1 server" },
      several: { text: "Set up Noust DNS on {count} servers" },
    },
    index_refresh: {
      single: { text: "Refresh package lists on 1 server" },
      several: { text: "Refresh package lists on {count} servers" },
    },
    noust_update: {
      single: { text: "Update Noust on 1 server" },
      several: { text: "Update Noust on {count} servers" },
    },
    os_updates: {
      single: { text: "Apply operating system updates on 1 server" },
      several: { text: "Apply operating system updates on {count} servers" },
    },
    placement_copy: {
      single: { text: "Copy an application to 1 server" },
      several: { text: "Copy an application to {count} servers" },
    },
    placement_rollout: {
      single: { text: "Roll an application out on 1 server" },
      several: { text: "Roll an application out on {count} servers" },
    },
    placement_ship: {
      single: { text: "Build on one server, run on others on 1 server" },
      several: { text: "Build on one server, run on others on {count} servers" },
    },
    settings_apply: {
      single: { text: "Apply the fleet's settings on 1 server" },
      several: { text: "Apply the fleet's settings on {count} servers" },
    },
  },
  fleet_settings: {
    backend_changed: {
      text: "The backup destination {name} is {have} on this server and the fleet wants it {want}",
      hint: "No route changes the backend of a destination: remove it on that server, or name the fleet's one differently.",
    },
    bad_mode: {
      text: "{mode} is not a way a server can follow the fleet's settings",
      hint: "Use 'managed' to follow them, or 'independent' to be left out.",
    },
    bad_scope: {
      text: "{scope} is not a place a setting of the fleet can be set",
      hint: "Use 'fleet' for every server, or 'node:<server>' for one.",
    },
    field_not_text: {
      text: "The {field} of a {kind} must be text",
      hint: "Send it as a string, such as https://central.example.com.",
    },
    item_conflict: {
      text: "The {kind} {name} already exists on this server and is not the fleet's",
      hint: "Apply again letting the fleet take it over, or give the fleet's setting another name.",
    },
    item_unsupported: {
      text: "This server cannot take a {kind} from the fleet: it does not offer {operation}",
      hint: "It runs an older Noust: update it first.",
    },
    kind_not_yet: {
      text: "The central cannot manage {kind} for the fleet yet",
      hint: "For now, set it on each server, in its own settings.",
    },
    managed_missing: {
      text: "This server holds no {kind} called {name}",
      hint: "A setting is recorded as the fleet's after the central has written it: write it first.",
    },
    missing_field: {
      text: "A {kind} needs {field}",
      hint: "A server cannot make one without it.",
    },
    no_such_setting: {
      text: "The fleet has no {kind} called {name}",
      hint: "List what it has with: GET /api/fleet/settings",
    },
    not_managed: {
      text: "No central manages the {kind} {name} on this server",
      hint: "Only a setting a central pushed can be asked back.",
    },
    out_of_reach: {
      domain: {
        text: "The route of the {kind} {name} names domains that no application of this server has, so it is not sent here",
        hint: "It is sent here once an application of this server has a domain the route names: apply again after deploying it.",
      },
      server: {
        text: "The route of the {kind} {name} does not name this server, so it is not sent here",
        hint: "Add this server to the servers of the route, or take the list away, to send it here.",
      },
      tag: {
        text: "The route of the {kind} {name} names tags that no application of this server carries, so it is not sent here",
        hint: "It is sent here once an application of this server carries a tag the route names (together with a domain it names, when the route has both): apply again after tagging it.",
      },
    },
    permission_missing: {
      text: "Setting a {kind} for the fleet needs the permission {permission}",
      hint: "It is the permission that setting one on a single server asks: ask an administrator for a role that has it.",
    },
    reach_unknown: {
      text: "The applications of this server could not be read, so it is not known whether the {kind} {name} can match anything on it",
      hint: "Nothing was sent. Apply again when the server answers its applications.",
    },
    route_invalid: {
      text: "The route of this channel is not valid: {problem}",
      hint: "A fleet route takes apps (domains, with * and ?), tags, min_severity, server_events and servers (names and patterns of this central's servers).",
    },
    route_local: {
      text: "A channel of the fleet cannot be routed by {list}",
      hint: "Projects and environments are each server's own. Route by domain, tag, severity, event or server, or make a team's channel on the server that has the project.",
    },
    secret_missing: {
      text: "{field} is hidden and nothing is stored for it yet",
      hint: "Type the secret again: it is only shown as {redacted} when one is stored.",
    },
    single_name: {
      text: "There is one {kind} for the fleet and it is called {name}",
      hint: "Set it as {name}.",
    },
    sync_ceiling: {
      text: "This server lets its central do at most '{level}', and applying the fleet's settings needs 'admin'",
      hint: "Raise it on this server, as root:\n  {command}",
    },
    sync_required: {
      text: "Only the central that manages this server can record a setting as the fleet's",
      hint: "The central does it when it applies a setting; nobody records one by hand.",
    },
    sync_route: {
      text: "A push of the fleet's settings cannot {method} {path}",
      hint: "It may only write the settings the fleet manages: channels, alert rules, backup destinations, registry logins, shared variables and the address of the central's console.",
    },
    unknown_field: {
      text: "A {kind} has no field called {field}",
      hint: "A {kind} takes: {fields}.",
    },
    unknown_kind: {
      text: "{kind} is not something the fleet manages",
      hint: "The fleet manages: {kinds}.",
    },
    unknown_node: {
      text: "There is no server called {node} in this fleet",
      hint: "List the servers with: {command}",
    },
    value_not_object: {
      text: "The value of a {kind} must be an object of fields",
      hint: "Send the fields that a server's own settings take for a {kind}.",
    },
    value_too_large: {
      text: "This {kind} is larger than the {limit} bytes a setting may be",
    },
  },
  host: {
    updates_unsupported: {
      no_manager: {
        text: "Noust drives apt, dnf and zypper, and none of them is installed here. Update this system with its own package manager.",
      },
      transactional: {
        text: "This system installs packages through transactions and a reboot (transactional-update or an image-based system), so Noust only reports its state. Update it with its own tool.",
      },
    },
  },
  how_it_runs: {
    container: {
      no_build: {
        text: "It cannot run in a container yet: the repository has no Dockerfile and Railpack is not installed on this server.",
        hint: "Add a Dockerfile to the repository, or install Railpack on this server ({command}), which builds without one.",
      },
    },
    server: {
      nothing_recognised: {
        text: "Nothing in it runs directly on the server: Noust does not recognise a program of its own in it.",
      },
    },
    why: {
      compose: {
        text: "A Compose file always runs in containers: that is its form.",
      },
      container_dockerfile: {
        text: "Noust proposes a container: the repository brings its Dockerfile, which says how it is built and started. It can also be built and started directly on the server ({type}).",
      },
      container_machine: {
        text: "Noust proposes a container: this server cannot run it directly.",
      },
      container_only: {
        text: "Noust proposes a container: the repository brings its Dockerfile, which says how it is built and started, and nothing in it runs directly on the server.",
      },
      container_railpack: {
        text: "Noust proposes a container, built by Railpack: nothing in it runs directly on the server.",
      },
      docker_only: {
        text: "This machine only runs applications in containers.",
      },
      none: {
        text: "Neither way is possible on this machine now; what is in the way is said below.",
      },
      server_dockerfile: {
        text: "Noust proposes running it directly on the server: it is a {type} project and starts with its own command. It has a Dockerfile, so it can also run in a container.",
      },
      server_only: {
        text: "Noust proposes running it directly on the server: it is a {type} project and starts with its own command.",
      },
      server_railpack: {
        text: "Noust proposes running it directly on the server: it is a {type} project and starts with its own command. Railpack is installed here, so it can also run in a container, built without a Dockerfile.",
      },
    },
  },
  inspect: {
    can_deploy: {
      text: "Noust can deploy this as {type}.",
    },
    can_deploy_also: {
      text: "Noust can deploy this as {type}. It also looks like {others}; choose that type instead to deploy it that way.",
    },
    container_dockerfile: {
      text: "Noust can deploy this as a container, built from its Dockerfile.",
    },
    container_dockerfile_also: {
      text: "Noust can deploy this as a container, built from its Dockerfile. It also looks like {others}; choose that type instead to deploy it that way.",
    },
    container_railpack: {
      text: "Noust can deploy this as a container, built by Railpack: it has no Dockerfile.",
    },
    container_railpack_also: {
      text: "Noust can deploy this as a container, built by Railpack: it has no Dockerfile. It also looks like {others}; choose that type instead to deploy it that way.",
    },
    dockerfile: {
      unreadable: {
        text: "The repository has a {dockerfile}, but it could not be read as one.",
        hint: "Deploy it as a container: {command}. Check the {dockerfile} is a file, not a link, and is committed on this branch.",
      },
    },
    dotnet: {
      no_web_project: {
        text: "This is a .NET project ({marker}), but Noust found no web project to run in it.",
        hint: "Deploy it as a container: add a Dockerfile to the repository and deploy it with --type container. Without a Dockerfile, install Railpack ({setup_command}), which builds it into a container by itself: --type container --builder railpack.",
      },
      no_web_project_with_railpack: {
        text: "This is a .NET project ({marker}), but Noust found no web project to run in it.",
        hint: "Deploy it as a container, which Railpack builds without a Dockerfile (it is installed here): {command}. Or add a Dockerfile and deploy it with --type container.",
      },
    },
    install_deno: {
      text: "Install Deno 2 system-wide, where the build account can run it ({command}, or the installer of {url} with {env_command}). Then deploy it.",
    },
    install_setup_init: {
      text: "Install what it needs with {command}, then deploy it.",
    },
    install_toolchain: {
      text: "Install the toolchain with: {command}. Then deploy it.",
    },
    missing_programs: {
      text: "This is a {type} project, but this server does not have {programs}.",
    },
    no_match: {
      text: "Nothing in the repository matches an application type Noust deploys.",
      hint: "Noust recognises a project by the files at the root of the repository ({examples}, or a package.json with a start script). Check the branch, or choose a type explicitly instead of relying on auto-detection.",
    },
    package_json: {
      no_start: {
        text: "package.json has no start script, no main entry and no framework Noust recognises (Next.js, Vite, Nuxt, SvelteKit, Astro, Remix, Angular).",
        hint: "Add a start script to package.json ({example_command}) or a \"main\" entry, so Noust knows how to run it; or choose the type explicitly.",
      },
      unreadable: {
        text: "package.json cannot be read as JSON.",
        hint: "Fix package.json and inspect again ({error}).",
      },
    },
    stack: {
      no_deployer: {
        text: "This is a {stack} project ({marker}); Noust has no {stack} deployer.",
        hint: "Deploy it as a container: add a Dockerfile to the repository and deploy it with --type container. Without a Dockerfile, install Railpack ({setup_command}), which builds it into a container by itself: --type container --builder railpack.",
      },
      no_deployer_with_railpack: {
        text: "This is a {stack} project ({marker}); Noust has no {stack} deployer.",
        hint: "Deploy it as a container, which Railpack builds without a Dockerfile (it is installed here): {command}. Or add a Dockerfile and deploy it with --type container.",
      },
      no_program: {
        text: "This is a {stack} project ({marker}), but Noust found no program to run in it.",
        hint: "Deploy it as a container: add a Dockerfile to the repository and deploy it with --type container. Without a Dockerfile, install Railpack ({setup_command}), which builds it into a container by itself: --type container --builder railpack.",
      },
      no_program_with_railpack: {
        text: "This is a {stack} project ({marker}), but Noust found no program to run in it.",
        hint: "Deploy it as a container, which Railpack builds without a Dockerfile (it is installed here): {command}. Or add a Dockerfile and deploy it with --type container.",
      },
    },
    subprojects: {
      text: "Nothing Noust deploys at the root of the repository; found {found}.",
      hint: "Noust deploys a repository from its root, or one application per project of it: a JavaScript monorepo (npm, Yarn, pnpm, Lerna, Nx or Turborepo workspaces) is read with '{detect_command}' and each application created with '{create_command}', on releases and watching only its own files. Otherwise deploy each project from a repository of its own, or add a compose.yaml at the root that builds them and deploy it as Docker Compose.",
    },
    turbo: {
      no_apps: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no app under apps/ has a package.json.",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
      no_workspace: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no workspace (pnpm-workspace.yaml, or \"workspaces\" in package.json).",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
      no_workspace_no_apps: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no workspace (pnpm-workspace.yaml, or \"workspaces\" in package.json); no app under apps/ has a package.json.",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
      no_workspace_one_app: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no workspace (pnpm-workspace.yaml, or \"workspaces\" in package.json); only apps/{app}/ has a package.json under apps/.",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
      one_app: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: only apps/{app}/ has a package.json under apps/.",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
      other: {
        text: "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys.",
        hint: "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, each with its own package.json. With a single app, move it to the root of the repository; or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose.",
      },
    },
  },
  invitation: {
    mail_failed: {
      text: "The invitation could not be mailed: {reason}",
      hint: "The invitation exists: hand its link over yourself.",
    },
  },
  jobs: {
    step: {
      backup: {
        complete: {
          text: "Backup complete",
        },
        creating: {
          text: "Creating backup",
        },
        upload_complete: {
          text: "Upload complete",
        },
        uploading: {
          text: "Uploading to {destination}",
        },
      },
      bundle: {
        active: {
          text: "Release active",
        },
        building: {
          text: "Building to ship",
        },
        packed: {
          text: "Packed, waiting to be fetched",
        },
        receiving: {
          text: "Receiving the release",
        },
      },
      certificate: {
        created: {
          text: "Certificate created",
        },
        requesting: {
          text: "Requesting certificate",
        },
        serving: {
          text: "Serving it",
        },
      },
      clone: {
        complete: {
          text: "Clone complete",
        },
      },
      complete: {
        text: "Complete",
      },
      delete: {
        complete: {
          text: "Deletion complete",
        },
      },
      deploy: {
        complete: {
          text: "Deployment complete",
        },
        copying_data: {
          text: "Copying the data",
        },
        database: {
          text: "Creating the {engine} database",
        },
        deploying: {
          text: "Deploying the application",
        },
        preparing: {
          text: "Preparing deployment",
        },
        recipe: {
          text: "Preparing the {recipe} recipe",
        },
      },
      disk: {
        giving_limit: {
          text: "Giving the limit to the kernel",
        },
        measuring: {
          text: "Measuring the application's directory",
        },
        taking_limit: {
          text: "Taking the limit off the kernel",
        },
      },
      domains: {
        deleted: {
          text: "The certificate is deleted",
        },
        deleting: {
          text: "Deleting the certificate of {name}",
        },
        extended: {
          text: "Certificate covers every domain",
        },
        extending: {
          text: "Extending the certificate to every domain",
        },
        giving_back: {
          text: "Giving it back",
        },
        in_place: {
          text: "The certificate is in place",
        },
        ordering: {
          text: "Ordering the certificate of its own",
        },
        removed: {
          text: "Certificate covers only the remaining domains",
        },
        removing: {
          text: "Removing the domain from the certificate",
        },
      },
      done: {
        text: "All done",
      },
      engine: {
        installing: {
          text: "Installing {engine}",
        },
        uninstalling: {
          text: "Uninstalling {engine}",
        },
      },
      exec: {
        finished: {
          text: "Command finished",
        },
        running: {
          text: "Running a command in {domain}",
        },
      },
      hardening: {
        changing: {
          text: "Changing the sandbox of the application's unit",
        },
      },
      identity: {
        moving: {
          text: "Moving the application to its own account",
        },
      },
      import: {
        complete: {
          text: "Import complete",
        },
      },
      migrate: {
        complete: {
          text: "Migration complete",
        },
        moving: {
          text: "Moving the tree onto releases",
        },
        planning: {
          text: "Planning the migration",
        },
      },
      packages: {
        installing: {
          text: "Installing the system packages",
        },
      },
      pitr: {
        recovering: {
          text: "Point-in-time recovery: {action}",
        },
      },
      promote: {
        complete: {
          text: "Promotion complete",
        },
      },
      renewal: {
        complete: {
          text: "Renewal complete",
        },
        none_due: {
          text: "No certificate was due for renewal",
        },
        renewing: {
          text: "Renewing certificates",
        },
        unreadable: {
          text: "certbot's certificate list could not be read, so which ones were renewed is unknown",
        },
      },
      replicas: {
        starting: {
          text: "Starting the copies and asking each one if it answers",
        },
      },
      restore: {
        complete: {
          text: "Restore complete",
        },
        downloading: {
          text: "Downloading and restoring backup",
        },
        restoring: {
          text: "Restoring backup",
        },
      },
      rollback: {
        complete: {
          text: "Rollback complete",
        },
        rolling_back: {
          text: "Rolling back",
        },
        to_deployment: {
          text: "Going back to deployment {deployment}",
        },
      },
      sandbox: {
        building: {
          text: "Building the current commit in the sandbox",
        },
      },
      staged: {
        building: {
          text: "Building without activating",
        },
        built: {
          text: "Built, waiting to be promoted",
        },
        promoted: {
          text: "Promoted",
        },
        promoting: {
          text: "Making the release serve",
        },
      },
      tuning: {
        tuning: {
          text: "Tuning the application's unit",
        },
      },
      update: {
        complete: {
          text: "Update complete",
        },
        unit_not_running: {
          text: "Restarted, but the unit is not running: check its logs",
        },
      },
      zero_downtime: {
        off: {
          text: "Turning zero downtime off",
        },
        on: {
          text: "Turning zero downtime on",
        },
      },
    },
    title: {
      app_exec: { text: "Run a command in {domain}" },
      app_exec_any: { text: "Run a command in an application" },
      backup: { text: "Back up {domain}" },
      backup_any: { text: "Back up an application" },
      backup_database: { text: "Back up the database {database}" },
      cert_acme_account: { text: "Register the ACME account" },
      cert_create: { text: "Get a certificate for {domain}" },
      cert_create_any: { text: "Get a certificate" },
      cert_renew: { text: "Renew the certificate of {domain}" },
      cert_renew_all: { text: "Renew the certificates that are due" },
      cleanup: { text: "Clean up the server" },
      cloudflared_install: { text: "Install cloudflared" },
      custom: { text: "Run a task" },
      custom_of_app: { text: "Run a task for {domain}" },
      database: { text: "Run a database task" },
      database_of_app: { text: "Work on a database of {domain}" },
      delete: { text: "Delete {domain}" },
      delete_any: { text: "Delete an application" },
      deploy: { text: "Deploy {domain}" },
      deploy_any: { text: "Deploy an application" },
      disk: { text: "Measure the disk use of {domain}" },
      disk_any: { text: "Measure the disk use of an application" },
      disk_scan: { text: "Measure what takes disk space" },
      fleet: { text: "Run an action across the fleet" },
      hardening: { text: "Change the sandbox of {domain}" },
      hardening_any: { text: "Change the sandbox of an application" },
      identity_migrate: { text: "Give {domain} its own system account" },
      identity_migrate_any: { text: "Give an application its own system account" },
      manifest: { text: "Apply a manifest" },
      migrate: { text: "Move {domain} onto the release layout" },
      migrate_any: { text: "Move an application onto the release layout" },
      node_enroll: { text: "Enroll {node}" },
      node_enroll_any: { text: "Enroll a server" },
      node_provision: { text: "Create the server {node}" },
      node_provision_any: { text: "Create a server" },
      os_refresh: { text: "Refresh the package lists" },
      os_update: { text: "Update the system" },
      push: { text: "Send a backup to {destination}" },
      push_any: { text: "Send a backup to its destination" },
      replicas: { text: "Change the copies of {domain}" },
      replicas_any: { text: "Change the copies of an application" },
      restore: { text: "Restore a backup into {domain}" },
      restore_any: { text: "Restore a backup" },
      rollback: { text: "Roll back {domain}" },
      rollback_any: { text: "Roll back an application" },
      sandbox_test: { text: "Try {domain} in the build sandbox" },
      sandbox_test_any: { text: "Try an application in the build sandbox" },
      self_update: { text: "Update Noust" },
      server_action: { text: "Run a server task" },
      server_action_dns_install: { text: "Install Knot DNS" },
      server_action_dns_setup: { text: "Set up Noust DNS as a {role}" },
      server_action_software_install: { text: "Install {component}" },
      server_action_software_remove: { text: "Remove {component}" },
      server_security: { text: "Run a security task" },
      service_action: { text: "Restart services" },
      site_action: { text: "Change a site" },
      site_console_expose: { text: "Publish the console at {domain}" },
      site_console_unexpose: { text: "Unpublish the console" },
      site_status_page_publish: { text: "Publish the status page at {domain}" },
      site_status_page_unpublish: { text: "Unpublish the status page" },
      site_webserver_move: { text: "Move the sites to {webserver}" },
      swap: { text: "Change the swap file" },
      swap_create: { text: "Create a swap file" },
      swap_remove: { text: "Remove the swap file" },
      tuning: { text: "Tune {domain}" },
      tuning_any: { text: "Tune an application" },
      update: { text: "Update {domain}" },
      update_any: { text: "Update an application" },
      zero_downtime: { text: "Change zero-downtime activation for {domain}" },
      zero_downtime_any: { text: "Change zero-downtime activation" },
    },
  },
  mail: {
    no_public_url: {
      text: "The console has no public address to put in the link",
      hint: "Set web.public_url to the address people open the console at.",
    },
    no_transport: {
      text: "There is no way to send mail from this server",
      hint: "Set monitor.smtp.* or monitor.email_api.* (Settings > Notification channels), then ask again.",
    },
  },
  names: {
    rule: {
      dashes: {
        text: "Use lower-case letters, digits and single dashes, starting with a letter, at most {max} characters.",
      },
      underscores: {
        text: "Use lower-case letters, digits and single underscores, starting with a letter, at most {max} characters.",
      },
      words: {
        text: "Use lower-case letters, digits, '-' and '_', starting with a letter, at most {max} characters.",
      },
    },
  },
  monitor: {
    observation: {
      name_pattern: {
        text: "Executable name starts with '{prefix}', a name used by known cryptomining and botnet malware. This is a note, not a verdict: identify the binary before acting on it.",
      },
      resource_usage: {
        text: "Sustained resource usage: CPU {cpu}% (threshold {cpu_threshold}%), memory {memory}% (threshold {memory_threshold}%).",
      },
    },
  },
  networks: {
    finding: {
      default_bridge: {
        text: "It is Docker's default bridge: every container started without a network is on it, and they reach each other by address.",
      },
      host: {
        text: "It is the host's own network: the containers have no network of their own.",
      },
      internal: {
        text: "It is internal: containers on it cannot reach the outside.",
      },
      made_elsewhere: {
        text: "Compose did not make it: it was made by hand or by another tool.",
      },
      owned_by_another: {
        text: "It belongs to the stack {owner}, which made it.",
      },
      shared_many: {
        text: "It is shared: {members} and {more} more are on it too.",
      },
      shared_one: {
        text: "It is shared: {members} is on it too.",
      },
      shared_several: {
        text: "It is shared: {members} are on it too.",
      },
    },
    not_applicable: {
      text: "This application does not run in Docker, so it has no Docker network.",
    },
  },
  operations: {
    app_apply: { text: "Apply a declaration to {target}" },
    app_move: { text: "Move {target} to another project" },
    app_restart: { text: "Restart {target}" },
    app_start: { text: "Start {target}" },
    app_stop: { text: "Stop {target}" },
    backup_verify: { text: "Verify the backup {target}" },
    build_apply: { text: "Apply the build settings of {target}" },
    build_cache_clear: { text: "Clear the build cache of {target}" },
    compose_import: { text: "Import a Compose project" },
    cron_run: { text: "Run the scheduled task {target}" },
    encryption_enable: { text: "Turn on backup encryption" },
    encryption_rotate: { text: "Rotate the backup encryption key" },
    engine_restart: { text: "Restart the database engine {target}" },
    engine_start: { text: "Start the database engine {target}" },
    engine_stop: { text: "Stop the database engine {target}" },
    env_sync: { text: "Sync the environment of {target}" },
    fleet_settings_apply: { text: "Apply the fleet's settings" },
    monitor_install: { text: "Install the monitor" },
    monitor_scan: { text: "Scan the server with the monitor" },
    node_test: { text: "Test the connection to {target}" },
    power_reboot: { text: "Restart the server" },
    power_shutdown: { text: "Shut the server down" },
    probe_check: { text: "Check the probe {target}" },
    service_restart: { text: "Restart the service {target}" },
    service_start: { text: "Start the service {target}" },
    service_stop: { text: "Stop the service {target}" },
    sites_reload: { text: "Reload the web server's sites" },
    waf_install: { text: "Install the web application firewall" },
  },
  power_checks: {
    jobs_running: { text: "Running now: {jobs}" },
    jobs_idle: { text: "Nothing is running" },
    console_returns: { text: "The console starts on boot" },
    console_stays_down: {
      text: "The console is not an enabled systemd unit: it will not come back after the reboot. Run 'noust web enable' first.",
    },
    apps_stay_down: { text: "These applications are not enabled and will not start again: {apps}" },
    apps_return: { text: "Every application starts on boot" },
    fstab_correct: { text: "fstab is correct" },
    fstab_problem: {
      text: "findmnt --verify found a problem, and a bad fstab can leave the machine in emergency mode: {output}",
    },
    ramdisk_missing: { text: "The newest kernel, {release}, has no initial ramdisk in /boot" },
    ramdisk_present: { text: "The newest kernel is complete" },
  },
  recipes: {
    root_equivalent: {
      agent: {
        text: "{title} is an AI agent that runs commands, and whoever can operate it can make it do anything it can do: access to it is the same as access to a shell",
        hint: "Its own security policy says it is not a boundary between users. Give its sign-in only to people you would give a shell on this server, and give it no model key or account you would not hand to them.",
      },
      confirm_required: {
        text: "{title} is root on this server, and installing it needs your confirmation",
        hint: "If you accept that, confirm it and install again: {command}",
      },
      docker_socket: {
        text: "{title} controls the Docker daemon of this server, which is the same as having root on it",
        hint: "It can start any container with any part of the server mounted, Noust's applications and their data included. Install it only if you would give this application root on the server.",
      },
    },
    unavailable: {
      archived: {
        text: "The project is archived and no longer maintained",
        hint: "Use the recipe that replaces it, when the catalogue names one.",
      },
      host_ports: {
        text: "It needs ports that the server's web server and mail already hold",
        hint: "Run it on a server of its own.",
      },
      licence: {
        text: "Its licence does not let an operator run it for others",
        hint: "If you hold a licence that does, deploy it from its own repository with 'noust create'.",
      },
      no_image: {
        text: "The project publishes no container image that could be pinned",
        hint: "Deploy it from the project's repository with 'noust create', or add a Dockerfile of your own.",
      },
      no_sign_in: {
        text: "Its web interface has no sign-in of its own and holds the keys it works with",
        hint: "Reach it through an SSH tunnel to the server, as its project documents, never through a public address.",
      },
      not_an_application: {
        text: "It is a framework to build an application with, not an application to install",
        hint: "Deploy your own project from its repository with 'noust create'.",
      },
      not_for_production: {
        text: "Its own project says this edition is not for production",
        hint: "Use the edition the project supports for production, deployed from its repository.",
      },
      platform_stack: {
        text: "It is a platform of dozens of services with a proxy of its own",
        hint: "Run it on a server of its own with the project's installer.",
      },
      root_equivalent: {
        text: "It controls the server's Docker daemon, which is the same as root on the server",
        hint: "Deploy it yourself with 'noust create' if you accept that, on a server that is yours.",
      },
    },
  },
  roles: {
    auditor_exclusive: {
      text: "{username} is an auditor, who checks everybody and holds nothing more",
      hint: "Use another account for what it should be able to do.",
    },
    in_use: {
      text: "The role {name} is still given to {count} account(s) or place(s)",
      hint: "See who holds it with: {command}, take it from them, then delete it.",
    },
    last_owner: {
      text: "{username} is the last account that can use the owner role",
      hint: "A server always keeps somebody who can do everything. Give the owner role to another account that can sign in first, then this change can go ahead.",
    },
    name_invalid: {
      text: "Invalid role name: {name}",
      hint: "Use 2 to 40 lower-case letters, digits or '-', starting and ending with a letter or digit, such as 'release-manager'.",
    },
    name_system: {
      text: "{name} is the name of a built-in role",
      hint: "Pick another name. The built-in roles can be edited and put back, not redefined under their own name.",
    },
    name_taken: {
      text: "A role named {name} already exists",
      hint: "Pick another name, or look at that one with: {command}",
    },
    no_roles: {
      text: "An account needs at least one role",
      hint: "Give it one, such as viewer.",
    },
    offer_unknown: {
      text: "{permission} is not on offer for the role {name}",
      hint: "See what is on offer with: {command}",
    },
    owner_by_owner: {
      text: "Only an owner can give or take the owner role",
      hint: "Ask an account that has it. On the server itself, root can: noust user roles.",
    },
    owner_protected: {
      text: "The owner role cannot be changed or deleted",
      hint: "It holds every permission there is, so that the server always has somebody who can do everything. To give a person all of it, give them the owner role.",
    },
    permissions_many: {
      text: "A role holds at most {max} permissions",
    },
    permissions_none: {
      text: "A role needs at least one permission",
      hint: "Pick what it may do, such as apps.read and apps.deploy.",
    },
    permissions_unknown: {
      text: "Unknown permissions: {names}",
      hint: "List every permission there is with: {command}",
    },
    reset_custom: {
      text: "{name} is a role of your own, so there is no shipped version to go back to",
      hint: "Change it, or remove it with: {command}",
    },
    seeded_kept: {
      text: "{name} ships with Noust, so it cannot be deleted",
      hint: "Change what it holds, or put it back to how it comes with: {command}",
    },
    separation_account: {
      text: "{username} would govern access and run the server at once",
      hint: "Its roles together hold {governing} (who may run the server) with {running} (running it). Separation of duties keeps them in different hands: give the person a second account for the other duty.",
    },
    separation_role: {
      text: "{subject} would govern access and run the server at once",
      hint: "It holds {governing} (who may run the server) together with {running} (running it). Separation of duties keeps them in different hands: split it into two roles.",
    },
    too_many: {
      text: "A server keeps at most {max} roles of its own",
      hint: "Remove one nobody holds. List them with: {command}",
    },
    unknown: {
      text: "There is no role named {name}",
      hint: "List the roles with: {command}",
    },
  },
  server: {
    descriptors_exhausted: {
      text: "File descriptors exhausted: the console has no file left to open, so what needs one fails",
      hint: "Restart the console to free them: {command}. Then run 'noust setup doctor': it says whether its unit still has the limit from before 4.3 (1024 files) and how to write the new one.",
    },
  },
  sign_in_image: {
    alt_control: {
      text: "The description must be one line of plain text",
      hint: "Remove the line breaks and control characters.",
    },
    alt_too_long: {
      text: "The description is longer than {limit} characters",
      hint: "Say it shorter: a screen reader reads all of it.",
    },
    animated: {
      text: "An animated image is not accepted as the sign-in image",
      hint: "Use a still picture: an animation cannot be paused, and people read this screen.",
    },
    bad_fit: {
      text: "The fit must be cover or contain",
      hint: "Use cover to fill the space (cropping what does not fit) or contain to show it whole.",
    },
    damaged: {
      text: "The {kind} file is damaged or incomplete",
      hint: "Export the picture again and send the new file.",
    },
    no_image_yet: {
      text: "There is no sign-in image to change the fit or the description of",
      hint: "Send the image itself too.",
    },
    not_an_image: {
      text: "The file is not a PNG, JPEG or WebP image",
      hint: "The type is read from the file itself, not from its name: export the picture as a PNG, JPEG or WebP.",
    },
    not_base64: {
      text: "The image must be sent as base64 text",
      hint: "Send the file's bytes encoded as base64, without a data: prefix and without line breaks.",
    },
    svg_refused: {
      text: "An SVG is not accepted as the sign-in image",
      hint: "An SVG is code, and this image is shown to anyone who opens the console before they sign in. Export it as a PNG, JPEG or WebP.",
    },
    too_large: {
      text: "The image is larger than {limit}",
      hint: "Export it again at a smaller size or with more compression, and send it again.",
    },
    too_many_pixels: {
      text: "The image is {width} by {height} pixels, and the most is {limit} on each side",
      hint: "Scale it down and send it again.",
    },
    unsupported: {
      text: "This {kind} file uses an encoding that browsers do not all draw",
      hint: "Save it again as a standard, 8-bit, non-hierarchical image and send it again.",
    },
  },
  sites: {
    config_updated: {
      text: "Configuration updated for {domain}. Reload {webserver} to apply it.",
    },
    created: {
      not_loaded: {
        text: "Site created: {domain}, but {webserver} did not load it: {error}",
      },
      reload: {
        text: "Site created: {domain}. Reload {webserver} to serve it.",
      },
      with_ssl: {
        text: "Site created with SSL: {domain}.",
      },
      without_tls: {
        text: "Site created: {domain}. TLS was requested but not enabled.",
      },
      without_tls_because: {
        text: "Site created: {domain}. TLS was requested but not enabled: {error}.",
      },
    },
    deleted: {
      text: "Site deleted: {domain}",
    },
    disabled: {
      text: "Site disabled: {domain}",
    },
    enabled: {
      text: "Site enabled: {domain}",
    },
    reloaded: {
      text: "{webserver} reloaded",
    },
  },
  software: {
    apache: {
      purpose: {
        text: "Another web server",
      },
    },
    auto_updates: {
      purpose: {
        text: "Installs security updates by itself",
      },
    },
    certbot: {
      purpose: {
        text: "Gets and renews HTTPS certificates",
      },
    },
    chrony: {
      purpose: {
        text: "Keeps the clock right",
      },
    },
    cloudflared: {
      purpose: {
        text: "Cloudflare Tunnel connector",
      },
    },
    curl: {
      never: {
        text: "Noust downloads the keys and the installers it verifies with it",
      },
      purpose: {
        text: "Downloads keys and installers",
      },
    },
    docker: {
      purpose: {
        text: "Runs containers and Compose applications",
      },
    },
    fail2ban: {
      purpose: {
        text: "Bans addresses that guess passwords",
      },
    },
    firewall: {
      never: {
        text: "Noust opens and closes ports through it, and it is what keeps the server closed",
      },
      purpose: {
        text: "Opens and closes ports",
      },
    },
    git: {
      never: {
        text: "Every deployment from a repository needs it",
      },
      purpose: {
        text: "Fetches the source of applications",
      },
    },
    gnupg: {
      never: {
        text: "Noust checks the signing key of every repository it adds with it",
      },
      purpose: {
        text: "Checks repository signing keys",
      },
    },
    hub: {
      text: "This central is a hub: it has no software of its own",
    },
    knot: {
      purpose: {
        text: "Name server for Noust's DNS zones",
      },
    },
    mongodb: {
      purpose: {
        text: "A document database",
      },
    },
    mysql: {
      purpose: {
        text: "MySQL or MariaDB database engine",
      },
    },
    nginx: {
      purpose: {
        text: "Serves applications and ends HTTPS",
      },
    },
    nginx_stream_module: {
      purpose: {
        text: "Publishes TCP and UDP ports",
      },
    },
    nodejs: {
      purpose: {
        text: "Runs Node.js applications",
      },
    },
    noust: {
      never: {
        text: "Removing it would take Noust, and the console you are using, off this server",
      },
      purpose: {
        text: "Noust and its libraries",
      },
    },
    php: {
      purpose: {
        text: "Runs PHP applications",
      },
    },
    postgresql: {
      purpose: {
        text: "PostgreSQL database engine",
      },
    },
    rclone: {
      purpose: {
        text: "Uploads backups to remote destinations",
      },
    },
    redis: {
      purpose: {
        text: "Redis or Valkey cache and store",
      },
    },
    sshd: {
      never: {
        text: "Removing it would lock you out of this machine",
      },
      purpose: {
        text: "How you reach this machine",
      },
    },
    systemd: {
      never: {
        text: "Everything on this server, Noust included, runs under it",
      },
      purpose: {
        text: "Runs every service on this server",
      },
    },
    waf: {
      purpose: {
        text: "Web application firewall for sites",
      },
    },
    refused: {
      cannot_tell: {
        text: "Noust cannot tell what depends on {component}",
        hint: "{reason} Removing it without knowing could break what depends on it.",
      },
      dependents: {
        text: "{component} cannot be removed while something depends on it: {dependents}",
        hint: "Stop using it first (remove or move what is listed), then remove it.",
      },
      never: {
        text: "{component} is never removed",
      },
      not_installed: {
        text: "{component} is not installed",
      },
      takes_in_use: {
        text: "Removing {component} would also remove {other}, which is in use: {dependents}",
        hint: "Nothing was removed. Stop using {other} first.",
      },
      takes_never: {
        text: "Removing {component} would also remove {other}, which is never removed",
        hint: "Nothing was removed.",
      },
      unknown_origin: {
        text: "Noust did not install {component}",
        hint: "Other things on the system may use it. Repeat the request confirming it to remove it anyway.",
      },
    },
  },
  sources: {
    central_only: {
      text: "Only the central that manages this server can lease it access to GitHub",
      hint: "The central makes and renews the lease itself; there is nothing to do on this server. To clone with a credential of this server's own, give the application a deploy key.",
    },
    delivery_not_retryable: {
      text: "That event is {state}, and only an event that was given up on can be handed over again",
      hint: "A server that took the event is not sent it twice; one that is still waiting is tried again by itself.",
    },
    forward_gave_up: {
      text: "{node} did not take the {kind} of {repository} in {minutes} minutes, and the central gave up",
      hint: "Nothing was deployed on {node} for it. Once it answers again, update the application there, or push again.",
    },
    forward_refused: {
      text: "{node} would not take the {kind} of {repository}: {reason}",
      hint: "The server's own words are beside this. Nothing was deployed on {node} for it.",
    },
    github_refused: {
      text: "GitHub would not give a token for installation {installation}",
      hint: "GitHub's own words are beside this. If the App was removed or its permissions changed, accept the new permissions in the App's settings on GitHub, or install it again.",
    },
    lease_expired: {
      text: "The GitHub access that {central} leased to this server for {repository} ended at {ended_at} and has not been renewed",
      hint: "{central} last called at {last_call} and did not lease a new one: it may not reach GitHub, or its GitHub App no longer covers {repository}. Until it does, anything that starts on this server and reads {repository} fails. A deploy key does not depend on the central; see: {command}",
    },
    lease_expired_quiet: {
      text: "The GitHub access that {central} leased to this server for {repository} ended at {ended_at}, and {central} has not called since {last_call}",
      hint: "Anything that starts on this server and reads {repository} (a scheduled update, a preview) fails until {central} calls again and leases a new one: check that the central is running and reaches this server. A deploy key does not depend on the central; see: {command}",
    },
    no_app: {
      text: "This central has no GitHub App to lease access with",
      hint: "Create it in the console (Integrations, GitHub) or with: noust github setup",
    },
    no_installation: {
      text: "No installation of the GitHub App reaches {repository}",
      hint: "Install the App on the account {owner} and let it read {repository}: {url}",
    },
    node_ceiling: {
      text: "{node} lets this central only read, so it cannot be leased access to GitHub",
      hint: "Leasing needs the deploy ceiling or higher. On {node}, as root: {command}",
    },
    node_independent: {
      text: "{node} is independent, so this central hands it nothing from GitHub",
      hint: "Make it follow the fleet again and the access and the events of its repositories reach it. Until then it deploys from its own credentials.",
    },
    node_refused: {
      text: "{node} would not take the access to GitHub: {reason}",
      hint: "The server's own words are beside this. It is tried again in a few minutes.",
    },
    node_unreachable: {
      text: "{node} could not be reached to lease it access to GitHub",
      hint: "It is tried again within a minute, and the access it holds ends on its own. Check it with: {command}",
    },
    node_unsupported: {
      text: "{node} runs a Noust that cannot hold the GitHub access this central leases",
      hint: "Update Noust on {node} to 4.1 or later; the access is leased within a minute of it.",
    },
    not_reachable: {
      text: "The GitHub App cannot read {repository}",
      hint: "Add {repository} to the repositories the App's installation reaches: {url}",
    },
    webhook_unreachable: {
      text: "GitHub cannot reach this central, so a push to a repository its servers deploy deploys nothing",
      hint: "The servers it manages have no address GitHub could call: the App's webhook is this central's. On a server with a web server, give it a name of its own: {command}. Behind a proxy of your own, say where it answers: {config_command}",
    },
  },
  storage: {
    effect: {
      docker_build_cache: {
        text: "Deletes build cache older than a week. The next build is slower for it.",
      },
      docker_dangling_images: {
        text: "Deletes images that have no name and no container. Named images are kept, and so is every noust_previous image.",
      },
      docker_image: {
        text: "Deletes the image {image}, without --force: Docker refuses if a container uses it.",
      },
      journal: {
        text: "Rotates the journal and deletes archived files beyond the limit. The active file is not touched.",
      },
      pkg_cache: {
        text: "Deletes the downloaded package files.",
      },
      releases: {
        text: "Deletes the releases each application keeps beyond its own limit. The active release and the one a rollback goes to always stay.",
      },
    },
    run: {
      below_threshold: {
        text: "{mount} is {percent}% full, below the {threshold}% the policy waits for",
      },
      policy_off: {
        text: "the cleanup policy is switched off",
      },
    },
  },
  streams: {
    firewall: {
      allowed_one: {
        text: "{backend} now allows 1 rule for it",
      },
      allowed_several: {
        text: "{backend} now allows {count} rules for it",
      },
      unchanged: {
        text: "The firewall was not changed",
      },
      unchanged_because: {
        text: "The firewall was not changed: {note}",
      },
    },
    plan: {
      nothing_listens: {
        text: "Nothing listens on {target} yet: visitors will be refused until the service is running",
      },
      open_to_everyone: {
        text: "{protocol} {port} will be open to everyone",
      },
      open_to_everyone_without_login: {
        text: "{protocol} {port} will be open to everyone; a service without its own authentication should be restricted to known addresses",
      },
    },
    support: {
      config_unparsable: {
        text: "nginx's configuration is not readable: {reason}",
      },
      config_unreadable: {
        text: "nginx cannot read its own configuration",
      },
      foreign_block: {
        text: "nginx.conf already has a stream block that does not include Noust's ports",
        hint: "Add this line inside it, then run the command again: {line_command}",
      },
      module_not_loaded: {
        text: "The stream module is built as a module and nothing loads it",
        hint: "Install the module with: {command}",
      },
      module_not_loaded_suse: {
        text: "The stream module is built as a module and nothing loads it",
        hint: "Add {line_command} at the top of nginx.conf.",
      },
      no_nginx: {
        text: "nginx is not installed",
        hint: "Install it with: {command}",
      },
      no_stream_block: {
        text: "nginx.conf has no stream block for Noust's ports",
        hint: "Add this at the top level of nginx.conf (beside http), then run the command again: {block_command}",
      },
      no_stream_module: {
        text: "This nginx was built without the stream module",
        hint: "Install the module with: {command}",
      },
      no_stream_module_suse: {
        text: "This nginx was built without the stream module",
        hint: "openSUSE ships nginx with stream; install the nginx package from the distribution or from nginx.org.",
      },
    },
  },
  swap: {
    cloud_init: {
      text: "cloud-init is configured with a swap: section, so it recreates the swap on every boot. Remove that section, or leave the swap to cloud-init.",
    },
    unsupported_container: {
      text: "This is a container ({container}), where swap belongs to the host: swapon is refused inside it.",
    },
  },
  terminal: {
    disabled: {
      text: "Terminals are turned off on this server",
      hint: "An administrator turns them on with: {command}",
    },
    limit_total: {
      text: "{open} terminals are open, the most this server allows",
      hint: "Close one, or raise terminal.max_sessions.",
    },
    limit_user: {
      text: "You already have {open} terminals open, the most one person may",
      hint: "Close one, or raise terminal.max_sessions_per_user.",
    },
    server_control: {
      text: "The control {control} is set to refused on this server, so the terminal on the server itself stays off",
      hint: "No framework asks for it: it was set in Settings > Compliance or with the command line. To allow it: {command}",
    },
    server_framework: {
      text: "The compliance framework in force ({frameworks}) keeps the terminal on the server itself off (the control {control} is refused)",
      hint: "A root shell in the browser is the console at its most root-equivalent. To allow it anyway, which is put on record: {command}. Or turn the framework off: {turn_off_command}",
    },
    server_off: {
      text: "The terminal on the server itself is off (terminal.server.enabled)",
      hint: "It is a root shell, so it stays off until an administrator turns it on: {command}",
    },
  },
  tunnels: {
    cloudflared_missing: {
      text: "cloudflared is not installed",
      hint: "Noust installs it from Cloudflare's own package repository, {url}, with the key Cloudflare signs it with: run 'noust tunnel install-cloudflared', then this command again.",
    },
    no_account: {
      text: "There is no Cloudflare account to manage a tunnel with",
      hint: "Add one with Account > Cloudflare Tunnel:Edit: noust dns provider add NAME --kind cloudflare --token-stdin",
    },
    store_sealed: {
      text: "The secret store is sealed",
      hint: "A tunnel's connector reads its token at boot, with nobody to type a passphrase. Run tunnels on a server, not on a sealed central.",
    },
  },
  webserver_move: {
    approximate: {
      text: "{directive} (line {line}) is carried over as {result}",
    },
    compose_only: {
      text: "{domain} is a Docker Compose application, and those are served by nginx only",
      hint: "Leave it behind nginx.",
    },
    confirm_handover: {
      text: "{target} will take over ports 80 and 443 from {source} for {count} site(s)",
      hint: "Every site is unreachable for the seconds the swap takes, and everything is put back if one of them does not answer. To go ahead: confirm in the API, --yes on the command line.",
    },
    confirm_live: {
      text: "{count} site(s) will move from {source} to {target}, which is serving",
      hint: "Each site is tested by {target} before it is switched, and put back if it does not answer. To go ahead: confirm in the API, --yes on the command line.",
    },
    console_site: {
      text: "{domain} is the public name of the console, which Noust sets up behind nginx only",
      hint: "Take it off first with {command}, or leave the web server as it is.",
    },
    included: {
      text: "Line {line} includes {path}, which is not carried over",
      hint: "The translation does not read the files a site includes. Copy what that file holds into the new site by hand, or leave the application where it is.",
    },
    left_behind: {
      text: "{name} is a site of {source} that Noust cannot move",
      hint: "{source} would stop serving it. Write its equivalent for {target} at {target_path} yourself, or leave the web server as it is.",
    },
    losses_to_accept: {
      text: "The new sites will not do {count} thing(s) that the old ones did",
      hint: "Read them in the plan. To go ahead anyway, accept them: accept_losses in the API, --accept-losses on the command line.",
    },
    no_site: {
      text: "{domain} has no site file on {source}",
      hint: "There is nothing to translate. Deploy the application again so that it gets a site, then plan the move again.",
    },
    not_restored: {
      text: "Not everything could be put back",
      hint: "What follows says what was left and where it is. The audit log says what was moved.",
    },
    not_serving: {
      text: "{target} did not start",
      hint: "Its own message is shown as it was. {source} was started again and the sites are as they were.",
    },
    operator_site: {
      text: "The site of {domain} is yours, not Noust's ({path})",
      hint: "Noust never rewrites a site you wrote. Write its equivalent for {target} at {target_path} yourself, then plan the move again: both files are left as they are and only the application moves.",
    },
    partial_handover: {
      text: "{target} is not serving: moving {count} of the {total} sites would leave the others without a web server",
      hint: "Ports 80 and 443 belong to one web server at a time. Move every site together: {command}",
    },
    protection: {
      text: "{directive} (line {line}) protects the site, and {target} has nothing equivalent",
      hint: "The new site would not be protected that way. Put another protection in front of it first, or leave the application where it is.",
    },
    rejected: {
      text: "{target} did not accept the translated site of {domain}",
      hint: "Its own message is shown as it was. Nothing was changed: fix what it names in the site on {source}, or leave the application where it is.",
    },
    status_site: {
      text: "{domain} is the public name of the status page, which Noust sets up behind nginx only",
      hint: "Take it off first with {command}, or leave the web server as it is.",
    },
    target_missing: {
      text: "{target} is not installed on this server",
      hint: "Install it first, then plan the move again: {command}",
    },
    tuning: {
      text: "{directive} (line {line}) tunes {source} and is left out",
    },
    unanswered: {
      text: "{domain} did not answer through {target}",
      hint: "What was tried and what the application's unit said is shown as it was. Everything was put back: {source} serves the sites again.",
    },
    unknown: {
      text: "{domain} is neither an application nor a site behind {source}",
      hint: "See what is behind each web server with: {command}",
    },
    unreadable: {
      text: "{source} would not read this site: {problem} (line {line})",
      hint: "Fix the site where it is, with the editor of its page, then plan the move again.",
    },
    unsupported: {
      text: "{directive} (line {line}) has no equivalent in {target}",
      hint: "The new site will not do what that line did. Check the site after the move, or leave the application where it is.",
    },
    variable: {
      text: "{directive} (line {line}) reads {variable}, which {target} cannot give",
      hint: "The new site would send something else in its place. Check it after the move, or leave the application where it is.",
    },
    zero_downtime: {
      text: "{domain} runs in zero-downtime mode, which only nginx supports",
      hint: "Turn the mode off first, then plan the move again: {command}",
    },
  },
} as const;
