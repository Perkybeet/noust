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
    act: {
      activate_release: {
        text: "Activate release {release_id} of {domain}",
      },
      cancel_job: {
        text: "Cancel job {job_id}",
      },
      create_backup: {
        text: "Back up {domain}",
      },
      deploy_application: {
        text: "Update {domain}",
      },
      operation: {
        text: "{summary} ({method} {path})",
      },
      renew_certificate: {
        text: "Renew the certificate of {domain}",
      },
      restart_application: {
        text: "Restart {domain}",
      },
      rollback_application: {
        text: "Go back to a backup of {domain}",
      },
      start_application: {
        text: "Start {domain}",
      },
      stop_application: {
        text: "Stop {domain}",
      },
    },
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
    proposal: {
      why: {
        actions_limit: {
          text: "The assistant reached its limit of changes for this hour, so it asks",
        },
        foreign_value: {
          text: "It carries a value that is neither yours nor Noust's, so it asks even where your policy would let it be done alone",
        },
        needs_second: {
          text: "This asks a second person to approve it, and you cannot be the second",
        },
        needs_sudo: {
          text: "This asks you to confirm it is you (sudo mode), so you do it with your own session",
        },
        policy_alone: {
          text: "Your approval policy lets the assistant do this without asking",
        },
        policy_propose: {
          text: "Your approval policy: the assistant proposes what changes something here",
        },
        read_outside: {
          text: "The assistant read text somebody else wrote in this conversation, so it asks even where your policy would let it be done alone",
        },
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
    refused: {
      not_offered: {
        text: "This is not something the assistant is offered",
        hint: "A person does it in the console.",
      },
      secret_value: {
        text: "The call carries a secret, and the assistant never handles secrets",
        hint: "Set it yourself in the console: the assistant does not see the value.",
      },
    },
    step: {
      application_logs: {
        text: "Read the last lines of the journal of {domain}",
      },
      deployment_log: {
        text: "Read the build log of deployment {deployment_id}",
      },
      diagnose_application: {
        text: "Read the diagnosis of {domain}",
      },
      find_operation: {
        text: "Looked for the operation that does: {query}",
      },
      fleet_apps: {
        text: "Read the applications of every server",
      },
      fleet_backups: {
        text: "Read the backups of every server",
      },
      fleet_certificates: {
        text: "Read the certificates of every server",
      },
      fleet_servers: {
        text: "Read the servers of the fleet",
      },
      fleet_updates: {
        text: "Read which servers have updates",
      },
      get_application: {
        text: "Read the state of {domain}",
      },
      get_deployment: {
        text: "Read deployment {deployment_id}",
      },
      get_job: {
        text: "Read job {job_id}",
      },
      job_log: {
        text: "Read the log of job {job_id}",
      },
      list_application_domains: {
        text: "Read the domains of {domain}",
      },
      list_applications: {
        text: "Read the list of applications",
      },
      list_backups: {
        text: "Read the backups",
      },
      list_certificates: {
        text: "Read the certificates",
      },
      list_deployments: {
        text: "Read the history of deployments",
      },
      list_environment_variables: {
        text: "Read the names of the variables of {domain}, without their values",
      },
      list_jobs: {
        text: "Read the recent jobs",
      },
      list_projects: {
        text: "Read the projects",
      },
      list_releases: {
        text: "Read the releases of {domain}",
      },
      list_teams: {
        text: "Read the teams",
      },
      operation: {
        text: "Made the read call {method} {path}",
      },
      search_docs: {
        text: "Looked up the documentation: {query}",
      },
      server_health: {
        text: "Read the health of the server",
      },
    },
    turn: {
      busy: {
        text: "The assistant is answering as many people as it may at once",
        hint: "Try again in a moment.",
      },
      failed: {
        text: "The assistant could not finish this answer: {reason}",
        hint: "Ask again. If it happens every time, the log of the console says why.",
      },
      interrupted: {
        text: "The server restarted before the assistant finished this answer",
        hint: "Ask again: what it already read is not kept.",
      },
      running: {
        text: "This conversation is still answering",
        hint: "Wait for it to end, or cancel it.",
      },
      too_long: {
        text: "This answer took longer than {minutes} minutes, so the assistant stopped",
        hint: "Ask again with a narrower question, or look at it by hand.",
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
    hub_only: {
      text: "{feature}: only a central that is a hub has this, and this Noust is a server",
      hint: "A server deploys on its own machine without being asked. Deploying on the machine's Docker is for a hub, the central in a container, which deploys nothing until it is told to.",
    },
    hub_role: {
      text: "{feature}: not available on this central, which is a hub",
      hint: "A hub (central.role = hub) manages other servers and deploys nothing itself. Pick the server in the console, or run the command against a node. On a machine that should also deploy, set '{command}'; a container cannot, it has no nginx or systemd.",
    },
    needs_host: {
      text: "This Noust runs in a container, so it sees the container and not the server",
      hint: "The processes, the disks and the packages it could show are those of its own container, which are not the server's. Pick the server in the console to see its own.",
    },
    needs_journal: {
      text: "This machine has no journal to read",
      hint: "Noust reads the system's log from journald (journalctl), which is not here.",
    },
    needs_package_manager: {
      text: "This machine has no package manager Noust can drive",
      hint: "Noust lists and applies updates with apt, dnf or zypper, and none of them is here.",
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
    options_object: { text: "The options of a fleet action are an object" },
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
    docker_images_check: {
      single: { text: "Look for new versions of the images in use on 1 server" },
      several: { text: "Look for new versions of the images in use on {count} servers" },
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
      no_build_here: {
        text: "It cannot run as a container here: the repository has no Dockerfile, and what builds a container without one is not available on this machine.",
        hint: "Add a Dockerfile to the repository, or deploy an image from a registry or a Compose file.",
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
      nothing_runs_here: {
        text: "Nothing in this repository runs on this machine: it deploys containers and nothing else, and the repository has no Dockerfile or Compose file to make one.",
        hint: "Add a Dockerfile or a Compose file to the repository and deploy it again, or deploy an image from a registry.",
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
  plans: {
    blocked: {
      refused: {
        text: "It would be refused: {reason}",
        hint: "{fix}",
      },
      refused_plain: {
        text: "It would be refused: {reason}",
      },
      nothing_new: {
        text: "{branch} has nothing new since {live}, which is live",
        hint: "Updating with force rebuilds the same commit, which makes sense when the environment or the dependencies changed, or when the last build broke.",
      },
      busy: {
        text: "{operation} is running on {domain} since {since}",
        hint: "Wait for it to end, or look at it in the jobs, and ask again.",
      },
    },
    confirm: {
      frozen: {
        text: "Deploys of {domain} are frozen until {until}: {reason}",
      },
      frozen_open: {
        text: "Deploys of {domain} are frozen until somebody lifts it: {reason}",
      },
      schema_changed: {
        text: "Going back to {target} of {domain} passes {count} deployments that changed the database schema: {deployments}",
        hint: "Noust puts the code back, never the database: the older code runs against the schema those deployments left. Restore the database from a backup first, or confirm that the older code works with the new schema.",
      },
    },
    update: {
      release: {
        text: "Builds {domain} from {branch} as a new release and switches to it once it answers the startup check. The release that serves now stays on disk to go back to.",
      },
      in_place: {
        text: "Pulls {branch} into the directory {domain} runs from, rebuilds it there and restarts it behind the startup check. What was built before is replaced by the new build.",
      },
      exact: {
        text: "Deploys {what} of {domain} and switches to it once it answers the startup check.",
      },
      containers: {
        text: "Pulls the images of {domain} again and recreates the containers that changed, one startup check after the other.",
      },
    },
    rollback: {
      instant: {
        text: "Switches {domain} back to release {release}, which is already on disk, without building anything. If it does not answer the startup check, release {previous} serves again.",
      },
      rebuild: {
        text: "Builds commit {commit} of {domain} again where it runs and restarts it behind the startup check.",
      },
      unavailable: {
        text: "Going back to deployment {deployment} of {domain} is not possible now",
      },
      snapshot: {
        text: "Restores the files of {domain} from backup {backup} after taking a safety backup, rebuilds it and restarts it behind the startup check.",
      },
    },
    found: {
      upstream_new: {
        text: "{branch} has moved on: {domain} runs {live} and the repository is at {remote}",
      },
      database_stays: {
        text: "The database does not go back: Noust puts code back and never data",
      },
      serving_now: {
        text: "Release {release} serves now",
      },
      delete_files_kept: {
        text: "The directory of {domain} stays on disk: files are only removed when asked for",
      },
      delete_certificate_kept: {
        text: "The certificate of {domain} stays: it is only removed when asked for",
      },
      delete_volumes_kept: {
        text: "The Docker volumes of {domain} stay, with its data: they are only removed when asked for",
      },
      delete_database_kept: {
        text: "The database {database} stays: a database is only dropped when asked for, and never when another application uses it",
      },
      delete_database_dropped: {
        text: "The database {database} is dropped after a last dump",
      },
      migrate_persistent: {
        text: "{count} paths move to the shared directory and stay there",
      },
      migrate_untracked: {
        text: "{count} files that git does not track stay in the first release only",
      },
      migrate_warning: {
        text: "{warning}",
      },
    },
    unknown: {
      build: {
        text: "Which install and build commands run, whether they pass and how long they take: they come from the repository's own files and scripts, which making a plan does not fetch or run",
      },
      new_code: {
        text: "What the new code changes: whether it asks for new variables, runs a migration or alters the database schema is in code that has not been fetched",
      },
      health: {
        text: "Whether the new version answers the startup check ({check}): it does not exist yet. If it does not, the version that serves now comes back",
      },
      health_older: {
        text: "Whether the older version still answers the startup check ({check}) with what the application holds now. If it does not, the version that serves now comes back",
      },
      rebuild: {
        text: "Whether building commit {commit} again passes: it is the repository's own script, and making a plan does not run it",
      },
      readback: {
        text: "A plan does not keep what the operation records along the way, so a step that reads it back (a site that lists a name just added) is shown as it is now",
      },
      stopped: {
        text: "The plan ends here: what follows depends on what the earlier steps would have produced. The operation said: {reason}",
      },
      remote: {
        text: "Which commits would come in: the repository could not be asked in time ({reason})",
      },
      health_limits: {
        text: "Whether the application answers the startup check ({check}) under the new limits. If it does not, the old limits come back",
      },
      authority: {
        text: "Whether the certificate authority accepts the renewal of {domain}: it checks from the internet that the name points to this server, and a plan does not ask it",
      },
      dns: {
        text: "Whether {name} points to this server: the DNS answers that, and the certificate authority needs it",
      },
      service_health: {
        text: "Whether {domain} answers once it runs: nothing checks it afterwards, as an update does",
      },
      docker_owner: {
        text: "Whether something outside Noust waits for what goes: Noust judges whose each thing is, not who needs it",
      },
      migrate_steps: {
        text: "The commands of the move are not simulated one by one: a rehearsal says what it would move and rewrite, and the startup check on the new layout is the one thing it cannot ask",
      },
      probes_skipped: {
        text: "{count} looks at the server were not made because the plan ran out of time: what depends on them may differ",
      },
    },
    limits: {
      restart: {
        text: "Gives {domain} these limits (memory {memory}, CPU {cpu}, tasks {tasks}) by rewriting its unit and restarting it behind the startup check. If it does not answer under them, the old limits come back.",
      },
      no_restart: {
        text: "Gives {domain} these limits (memory {memory}, CPU {cpu}, tasks {tasks}) by rewriting its unit. The processes that run now keep the limits they started with until it restarts.",
      },
    },
    cert: {
      renew: {
        text: "Asks the certificate authority to renew the certificate of {domain} with certbot, which does it only when it is near its expiry, and reloads the web server when it is renewed.",
      },
      renew_forced: {
        text: "Asks the certificate authority to renew the certificate of {domain} with certbot even though it is not near its expiry, and reloads the web server when it is renewed.",
      },
    },
    domain: {
      add: {
        text: "Makes {domain} answer on {name} as well: it records the name, rewrites the site and reloads the web server. If the site serves TLS, extending the certificate is a job of its own.",
      },
      remove: {
        text: "Stops {domain} answering on {name}: it rewrites the site and reloads the web server. The certificate is left alone and nothing is revoked.",
      },
    },
    delete: {
      app: {
        text: "Deletes {domain}: its service, its site and its record go.",
      },
    },
    service: {
      restart: {
        text: "Restarts {domain}: its service stops and starts again, so it may not answer for a few seconds.",
      },
      stop: {
        text: "Stops {domain}: it does not answer until somebody starts it again. Its files and data stay.",
      },
      start: {
        text: "Starts the service of {domain}.",
      },
    },
    docker: {
      cleanup: {
        text: "Removes the {count} things that were chosen from the plan of the cleanup, each through the gate that judges whose it is once more. It touches nothing else.",
      },
    },
    migrate: {
      app: {
        text: "Moves {domain} onto releases: the unit is stopped while the tree moves, the first release ({release}) is made from what is on disk, and the application must answer the startup check on the new layout, or everything is put back.",
      },
    },
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
    docker_image_rm_failed: {
      text: "'docker image rm {ref}' failed",
      hint: "Nothing else was changed. The tool's own output is below.",
    },
    docker_installed_system: {
      text: "Docker is not installed on this system",
    },
    failed_exit_code: {
      text: "'{items}' failed (exit code {exit_code})",
      hint: "Nothing else was changed. The tool's own output is below.",
    },
    give_size_age_both: {
      text: "Give a size or an age, not both",
      hint: "Use size_mb or days.",
    },
    image_list_unused_images: {
      text: "That image is not in the list of unused images",
      hint: "It may be in use by a container, or not be there any more.",
    },
    image_list_unused_images_2: {
      text: "That image is not in the list of unused images: it is a version {value} to go back to",
      hint: "Noust never offers it for removal. An application keeps fewer versions with: noust releases keep",
    },
    name_image_id: {
      text: "Name the image by its id",
      hint: "Use an id from the list of unused images.",
    },
    package_cache_clean_system: {
      text: "There is no package cache to clean on this system",
      hint: "{value}",
    },
    positive_whole_number: {
      text: "{name} must be a positive whole number",
      hint: "Got {value}.",
    },
    psutil_installed_disks_listed: {
      text: "psutil is not installed, so disks cannot be listed",
      hint: "Install the web extra: pip install 'noust[web]'.",
    },
    unknown_cleanup_action: {
      text: "Unknown cleanup action: {action}",
      hint: "Use one of: {items}.",
    },
    cleanup_deletes: {
      text: "'{action}' deletes something you may want back",
      hint: "{effect}",
    },
    backups_summary: {
      text: "{count} backups; their retention is a backup setting",
    },
    reduce_to: {
      text: "Reduce it to {megabytes} MB",
    },
    releases_over_keep: {
      text: "{apps} application(s) keep more releases than they are set to",
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
    nothing_to_install: {
      text: "There is nothing to install here: the connector of a tunnel runs as a container Noust starts itself",
      hint: "Create the tunnel: Docker pulls the connector's image the first time it starts.",
    },
    store_sealed: {
      text: "The secret store is sealed",
      hint: "A tunnel's connector reads its token when it starts, by itself after the machine restarts, with nobody to type a passphrase. Run tunnels on a server, or on a central whose secrets are not sealed.",
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
  apps: {
    not_found: {
      text: "Application not found: {domain}",
      hint: "Run 'noust list' to see what is deployed.",
    },
    not_found_here: {
      text: "Application not found: {domain}",
      hint: "Nothing is deployed at {app_path}. Deploy it with: noust create -d {domain}",
    },
    not_found_bare: {
      text: "Application not found: {domain}",
    },
    not_found_to_delete: {
      text: "Application not found: {domain}",
      hint: "Nothing to delete; check 'noust list' for the exact domain.",
    },
  },
  access_api: {
    no_such_binding: {
      text: "No access binding with id {binding_id} in {slug}",
      hint: "List them with GET /api/teams/<team>/access.",
    },
  },
  access_change: {
    already_resolved: {
      text: "Change {change_id} is already {status}",
      hint: "Only a pending change can be confirmed.",
    },
    bad_id: {
      text: "Refusing the pending change id {id}",
    },
    bad_status: {
      text: "Refusing the pending change status {status}",
    },
    busy: {
      text: "Another change to SSH or the firewall is still being applied",
      hint: "Wait for it to finish, then try again.",
    },
    no_timer: {
      text: "Noust could not arm the timer that would undo this change, so it did not make it",
      hint: "A change to SSH or the firewall is only made when it can undo itself. Check that systemd is running (systemctl is-system-running).",
    },
    not_permanent: {
      text: "Change {change_id} could not be made permanent and was undone",
    },
    restore_refused: {
      text: "Refusing to restore {path}: Noust never writes it",
    },
    too_late: {
      text: "Too late: change {change_id} is being undone right now",
      hint: "Its confirmation window ended. Apply it again if you still want it.",
    },
    undo_refused: {
      text: "Refusing to run {argv} to undo a change",
    },
    unknown: {
      text: "There is no pending change {change_id}",
    },
    unknown_listed: {
      text: "There is no pending change {change_id}",
      hint: "List them with 'noust server security pending'.",
    },
    waiting_for_confirmation: {
      text: "Another change is waiting for confirmation (change {id})",
      hint: "Confirm it or revert it first: one change at a time can be undone safely.",
    },
    not_applied: {
      text: "Not applied: {title}",
      hint: "{reasons}",
    },
    record_damaged: {
      text: "A pending change record is damaged",
    },
    record_damaged_named: {
      text: "The record of change {change_id} is damaged",
    },
  },
  firewall: {
    allow_only_unconfirmed: {
      text: "Only an allow rule is made without a confirmation window",
      hint: "Denying a port can cut SSH off, so it waits to be confirmed.",
    },
    bad_action: {
      text: "A rule's action is allow or deny",
    },
    bad_port: {
      text: "A port is a number from 1 to 65535",
    },
    bad_protocol: {
      text: "A protocol is tcp, udp or any",
    },
    bad_source: {
      text: "{source} is not an address or a network",
    },
    keeps_ssh_open: {
      text: "Refusing to delete the rule that lets everyone reach port {port} ({what})",
      hint: "With the default policy denying, nobody could open a new SSH session, the central's tunnel included. Add the rule you want instead first, then delete this one.",
    },
    no_changeable_firewall: {
      text: "Noust changes ufw and firewalld rules, and neither is in use here",
      hint: "Install a firewall Noust can change: {value}; nftables and iptables rules are only read.",
    },
    no_such_rule: {
      text: "There is no firewall rule {rule_id}",
      hint: "List the rules with 'noust server security firewall status'.",
    },
    not_active: {
      text: "{backend} is not active",
    },
    not_permanent: {
      text: "The firewall could not make '{title}' permanent, so it was undone",
    },
    origin_needs_one_port: {
      text: "Noust changes the origin of a rule for one port, and '{spec}' is not one",
      hint: "For this one, add the rule you want and then delete this one.",
    },
    port_in_use: {
      text: "Refusing to {action} port {port}: {what} listens there",
      hint: "Closing it could cut off the SSH sessions open now and a central's tunnel. To restrict who reaches it, allow the addresses you use and let the default policy close the rest.",
    },
    read_only: {
      text: "This firewall was opened for reading only",
    },
    refused: {
      text: "The firewall refused '{items}'",
    },
    refused_undone: {
      text: "The firewall refused '{items}', so the change was undone",
    },
    rule_already_admits: {
      text: "The rule already admits from {value}",
    },
    rule_covers_more: {
      text: "The rule '{spec}' covers more than port {port}",
      hint: "Delete it on its own from the firewall's rules, and add the ones you want for each port.",
    },
    rule_exists: {
      text: "There is already a rule for {proto} port {port} from {value}",
      hint: "Delete the rule you do not want, or choose another origin.",
    },
    rule_other_port: {
      text: "The rule '{spec}' is not about port {port}",
      hint: "Only a rule that allows the port being opened is replaced with it.",
    },
    state_unreadable: {
      text: "The firewall's state could not be read",
    },
    turns_on_ufw_only: {
      text: "Noust turns on ufw only",
      hint: "For firewalld, allow SSH in the default zone with 'firewall-offline-cmd --add-service=ssh', then 'systemctl enable --now firewalld', keeping this session open.",
    },
    ufw_active: {
      text: "ufw is already active",
    },
    origin_cuts_ssh: {
      text: "Refusing to change who reaches port {port} ({what}) to {origin}",
      hint: "It would cut off the SSH sessions open now ({sessions}) and could leave a central's tunnel out. Choose a network that holds the address you are connected from, or add the rule you want first and delete this one once you have proved it works.",
    },
    origin_locks_out: {
      text: "Refusing to change who reaches port {port} ({what}) to {origin}",
      hint: "It would leave nobody able to open a new SSH session or reach the console: the default policy denies and nothing else admits them.",
    },
  },
  security: {
    bad_ip: {
      text: "{address} is not an IP address",
    },
    fail2ban_not_running: {
      text: "fail2ban is not running",
      hint: "Install it first: noust server security fail2ban install",
    },
    no_nginx_log: {
      text: "nginx is not installed here, so there is no log to watch",
    },
    no_such_check: {
      text: "There is no check {check_id}",
    },
    no_such_fix: {
      text: "There is no automatic fix {action}",
    },
    no_such_operation: {
      text: "There is no security operation {operation}",
    },
    nothing_to_fix: {
      text: "Check {check_id} has nothing to fix",
      hint: "Run the checks again; it may have passed since.",
    },
    no_automatic_fix: {
      text: "Check {check_id} has no automatic fix here",
      hint: "{steps}",
    },
    no_fail2ban_here: {
      text: "Noust cannot install fail2ban here",
      hint: "{install_hint}",
    },
  },
  security_api: {
    bad_date: {
      text: "{text} is not a date",
      hint: "Use 2026-12-31 or 2026-12-31T18:00:00Z.",
    },
    no_accepted_risk: {
      text: "No accepted risk holds for {check_id}",
    },
    no_such_ssh_fix: {
      text: "There is no SSH fix named {fix}",
    },
  },
  ssh: {
    already_resolved: {
      text: "Change {change_id} is already {status}",
    },
    config_rejected: {
      text: "sshd rejected the new configuration, so nothing was changed",
      hint: "Its own words are below.",
    },
    keys_of_root_only: {
      text: "Noust manages the keys of root and of the accounts that can become root; {user} is not one of them",
      hint: "Accounts: {value}.",
    },
    login_history_unreadable: {
      text: "Noust cannot read sshd's login history, so it cannot see a new session",
      hint: "{error}. The change undoes itself at {deadline}; to keep it, make it by hand while a second session stays open.",
    },
    no_effect_undone: {
      text: "The change did not take effect, so it was undone",
      hint: "sshd uses the first value it reads. Move or remove the line that wins, then apply again:\n{items}",
    },
    no_new_login: {
      text: "No new SSH login since the change",
      hint: "Keep this session open, open a NEW SSH session to this server and confirm again once it has logged in. If it cannot log in, do nothing: the change undoes itself at {deadline}.",
    },
    no_such_fix: {
      text: "There is no SSH fix named {name}",
      hint: "Use one of: {items}.",
    },
    no_such_key: {
      text: "{user} has no key {fingerprint}",
      hint: "List the keys with 'noust server security ssh keys'.",
    },
    nothing_to_do: {
      text: "Nothing to do: sshd already uses {items}",
    },
    reload_failed_undone: {
      text: "sshd could not be reloaded, so the change was undone",
    },
    key_removal_refused: {
      text: "Refusing to remove {fingerprint} from {user}",
      hint: "{blockers}\nOverride only if you are sure (--force).",
    },
    no_authorized_keys: {
      text: "sshd reads no authorized_keys file for {user}",
      hint: "{reason}",
    },
  },
  sshd: {
    dropin_not_noust: {
      text: "{dropin} exists and Noust did not write it",
      hint: "Noust keeps its sshd settings in that file only. Move what it holds to another file in /etc/ssh/sshd_config.d/, then try again.",
    },
    keyword_refused: {
      text: "Noust does not write the sshd keyword {keyword}",
    },
    no_effective_config: {
      text: "sshd did not report its effective configuration",
      hint: "Run 'sshd -T' as root to see why; an sshd that cannot print its configuration would refuse to reload it too.",
    },
    not_under_systemd: {
      text: "sshd is not running under systemd, so Noust cannot reload it",
      hint: "Start it (systemctl start ssh, or sshd on RHEL and SUSE) and try again.",
    },
    symlink_refused: {
      text: "Refusing to use {path}: it is a symbolic link",
      hint: "Noust writes this file itself; remove the link and try again.",
    },
    value_refused: {
      text: "Refusing the value {value} for {name}",
    },
    cannot_read: {
      text: "Cannot read {path}",
    },
  },
  access_changes: {
    firewall_allow: {
      text: "Allow {port} from {source}",
    },
    firewall_allow_any: {
      text: "Allow {port} from anywhere",
    },
    firewall_allow_any_replacing: {
      text: "Allow {port} from anywhere, and delete {rules}",
    },
    firewall_allow_replacing: {
      text: "Allow {port} from {source}, and delete {rules}",
    },
    firewall_delete: {
      text: "Delete the firewall rule “{rule}”",
    },
    firewall_deny: {
      text: "Deny {port} from {source}",
    },
    firewall_deny_any: {
      text: "Deny {port} from anywhere",
    },
    firewall_deny_any_replacing: {
      text: "Deny {port} from anywhere, and delete {rules}",
    },
    firewall_deny_replacing: {
      text: "Deny {port} from {source}, and delete {rules}",
    },
    firewall_disable: {
      text: "Turn off ufw",
    },
    firewall_enable: {
      text: "Turn on ufw, denying incoming by default",
    },
    firewall_source: {
      text: "Change the origin of {port} from {old} to {source}",
    },
    firewall_source_from_any: {
      text: "Change the origin of {port} from anywhere to {source}",
    },
    firewall_source_to_any: {
      text: "Change the origin of {port} from {old} to anywhere",
    },
    firewall_stop: {
      text: "Stop firewalld",
    },
    ssh_disable_passwords: {
      text: "Turn off SSH password logins",
    },
    ssh_no_empty_passwords: {
      text: "Refuse logins with an empty password",
    },
    ssh_root_no: {
      text: "Refuse root logins over SSH",
    },
    ssh_root_prohibit_password: {
      text: "Let root log in over SSH with a key only",
    },
    ssh_sensible_defaults: {
      text: "Apply sensible SSH limits",
    },
    ssh_verbose_logging: {
      text: "Log the key fingerprint of every SSH login",
    },
  },
  accounts_api: {
    exception_force_id: {
      text: "No exception in force with id {exception_id}",
    },
    own_account_change_person: {
      text: "You cannot change the person of your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_change_role: {
      text: "You cannot change the role of your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_disable: {
      text: "You cannot disable your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_enable: {
      text: "You cannot enable your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_remove: {
      text: "You cannot remove your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_reset_second_factor: {
      text: "You cannot reset the second factor of your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
    own_account_unlock: {
      text: "You cannot unlock your own account",
      hint: "Another administrator can, or root with 'noust user'.",
    },
  },
  accounts_model: {
    e_mail_address: {
      text: "Not an e-mail address: {value}",
      hint: "Use an address such as maria@example.com.",
    },
    invalid_display_name: {
      text: "Invalid display name",
      hint: "Use at most {max_display_name} printable characters.",
    },
    invalid_person_reference: {
      text: "Invalid person reference",
      hint: "Use at most {max_person_ref} printable characters, such as an e-mail.",
    },
    invalid_username: {
      text: "Invalid username: {value}",
      hint: "Use 2 to 64 lower-case letters, digits, '.', '-' or '_', starting with a letter or digit, such as 'maria' or 'maria.adm'.",
    },
    unknown_role: {
      text: "Unknown role: {value}",
      hint: "Use one of: {items}.",
    },
    username_reserved: {
      text: "The username {name} is reserved",
      hint: "It already names something in the audit log. Pick another one.",
    },
  },
  auth_files: {
    create_noust_web_state: {
      text: "Cannot create the Noust web state directory {path}",
      hint: "The web panel stores its signing key, sessions and audit log there. Create it as root with 'install -d -m 700 {path}', or point the panel somewhere writable with {state_dir_env}=/path/to/dir.",
    },
    look: {
      text: "Cannot look for {path}",
      hint: "The web panel keeps its credentials where only its owner can read. Run as root, or set {state_dir_env} to a directory you own.",
    },
    write: {
      text: "Cannot write {path}",
      hint: "Run the web panel as root, or set {state_dir_env} to a directory the current user owns.",
    },
  },
  auth_records: {
    address_network: {
      text: "Not an address or a network: {entry}",
      hint: "Use addresses or CIDRs, such as 203.0.113.7 or 10.0.0.0/8.",
    },
  },
  auth_scopes: {
    credential_scope_requires_issue: {
      text: "This credential's scope is '{granted}', and {method} {path} requires '{required}'. Issue a token with the right scope from Settings, or POST /api/auth/tokens.",
    },
  },
  auth_sessions: {
    open_web_session_database: {
      text: "Cannot open the web session database {path}",
      hint: "Run the web panel as root, or set {state_dir_env} to a directory the current user owns.",
    },
  },
  local_login: {
    list_usernames: {
      text: "{setting_exempt} must be a list of usernames",
      hint: "Got {value}.",
    },
    most_accounts_keep_their: {
      text: "At most {max_exempt} accounts keep their password",
      hint: "A break-glass list is a short one.",
    },
    sign_password_passkey_turned: {
      text: "Sign-in with a password or a passkey is turned off on this server",
      hint: "Use the sign-in with your organisation's identity provider.",
    },
    nobody_cutting_link: {
      text: "Cutting this link would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_disabling_directory: {
      text: "Disabling the directory {name} would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_disabling_provider: {
      text: "Disabling the provider {name} would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_removing_directory: {
      text: "Removing the directory {name} would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_removing_provider: {
      text: "Removing the provider {name} would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_this_change: {
      text: "This change would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
    nobody_turning_off: {
      text: "Turning off the password sign-in would leave nobody able to administer this server",
      hint: "Link an administrator or a security officer to an enabled identity provider ('noust auth oidc link'), or name a break-glass account that keeps its password ('noust auth local-login exempt add NAME'), first.",
    },
  },
  passkeys: {
    already_passkeys_account: {
      text: "There are already {max_passkeys_per_owner} passkeys for this account",
      hint: "Remove one you no longer use before adding another.",
    },
    invalid_passkey_name: {
      text: "Invalid passkey name",
      hint: "Use 1 to {max_name_length} printable characters, such as 'Work laptop'.",
    },
    passkey_already_registered: {
      text: "This passkey is already registered here",
      hint: "Each passkey is added once; give another authenticator a try.",
    },
    passkey_id: {
      text: "You have no passkey with id {passkey_id}",
      hint: "List yours with GET /api/auth/passkeys.",
    },
    passkey_id_2: {
      text: "No passkey with id {passkey_id}",
      hint: "List them with 'noust passkey list'.",
    },
    passkey_saved: {
      text: "The passkey was not saved",
      hint: "Try again.",
    },
    passkey_second_factor: {
      text: "This passkey is your only second factor",
      hint: "Add another passkey or an authenticator first. If you lost the others, ask a security officer to reset your second factor.",
    },
    server_accepts_passkeys_bound: {
      text: "This server only accepts passkeys bound to one device, and this one may be synced",
      hint: "Use a security key or a device-bound authenticator, or allow synced passkeys with web.passkeys.allow_synced.",
    },
    bad_rp_id: {
      text: "web.passkeys.rp_id is not a host name: {rp_id}",
      hint: "Set it to the console's name, such as noust.example.com, or unset it.",
    },
    host_mismatch: {
      text: "This console's passkeys belong to {chosen}, and it was opened as {host}",
      hint: "Open it at {chosen} (or a name under it), or change web.passkeys.rp_id.",
    },
    insecure: {
      text: "Passkeys need HTTPS on a name other than localhost",
      hint: "Browsers only offer passkeys over HTTPS, or on http://localhost. Serve {host} with a certificate the browser trusts, or reach this server through an SSH tunnel at http://localhost:<port>.",
    },
    ip_address_name: {
      text: "Passkeys do not work on an address such as {host}",
      hint: "Browsers only offer passkeys to a site with a name. Give this console a name with a certificate the browser trusts, or reach this server through an SSH tunnel at http://localhost:<port>.",
    },
    ip_address_open: {
      text: "Passkeys do not work on an address such as {host}",
      hint: "Browsers only offer passkeys to a site with a name. Open {public} instead, or reach this server through an SSH tunnel at http://localhost:<port>.",
    },
    library_missing: {
      text: "Passkeys need a library this server does not have",
      hint: "{install}",
    },
    no_host: {
      text: "This request does not name a host passkeys can be bound to",
      hint: "Open the console by its name, such as https://noust.example.com.",
    },
    session_required: {
      text: "Passkeys are managed from a signed-in browser session",
      hint: "Sign in to the console in a browser; tokens cannot hold passkeys.",
    },
  },
  passwords: {
    password_contains_server_name: {
      text: "The password contains this server's name",
      hint: "Use a passphrase that does not include the machine's name.",
    },
    password_contains_username: {
      text: "The password contains the username",
      hint: "Use a passphrase that does not include the account's name.",
    },
    password_longer_characters: {
      text: "The password is longer than {max_password_length} characters",
      hint: "Use a shorter passphrase.",
    },
    password_repeats_too_few: {
      text: "The password repeats too few different characters",
      hint: "Use a passphrase with more variety.",
    },
    password_shorter_characters: {
      text: "The password is shorter than {min_length} characters",
      hint: "Use a longer passphrase: several unrelated words are easy to remember.",
    },
  },
  session: {
    authentication_system_initialized: {
      text: "Authentication system not initialized",
    },
    missing_invalid_csrf_token: {
      text: "Missing or invalid CSRF token. Send the {csrf_cookie_name} cookie value in the {csrf_header_name} header, or authenticate with a Bearer token.",
    },
  },
  signin: {
    account_password_credential: {
      text: "Only an account has a password; this credential is not one.",
    },
    account_second_factor_turned: {
      text: "An account's second factor cannot be turned off",
      hint: "Every account keeps one. To replace it, ask a security officer to reset it.",
    },
    active_session_matches_prefix: {
      text: "No active session matches that prefix. It may have expired.",
    },
    address_network_use_203: {
      text: "Not an address or a network: {exc}. Use 203.0.113.7 or 10.0.0.0/8.",
    },
    api_token_id: {
      text: "No API token with id {token_id}",
    },
    api_token_issue_api: {
      text: "An API token cannot issue API tokens.",
    },
    central_fleet_token_restricted: {
      text: "A central's fleet token is not restricted here; remove the node instead.",
    },
    central_fleet_token_revokes: {
      text: "Only a central's fleet token revokes itself here; revoke others in Settings.",
    },
    code_accepted_scan_qr: {
      text: "That code was not accepted. Scan the QR again and enter a fresh code.",
    },
    code_accepted_two_factor: {
      text: "That code was not accepted. Two-factor authentication stays on.",
    },
    credential_browser_session_other: {
      text: "This credential is not a browser session; there is no other session to leave signed in.",
    },
    credential_list_api_tokens: {
      text: "This credential cannot list API tokens.",
    },
    person_accepts_notice_credential: {
      text: "Only a person accepts the notice; this credential is not one.",
    },
    sudo_mode_asks_account: {
      text: "Sudo mode asks for the account's password, and this account has none",
      hint: "It signs in through its identity provider. Give it a password, which it keeps only for this: 'noust user invite NAME' lets its owner set one, with their authenticator.",
    },
    sudo_mode_needs_second: {
      text: "Sudo mode needs a second factor, and this account has none",
      hint: "Enrol an authenticator first: POST /api/auth/2fa/enroll.",
    },
    credentials_refused: {
      text: "Invalid credentials",
      hint: "Check the username, the password and the code, then try again.",
    },
    enter_code: {
      text: "Enter a code from your authenticator, or a backup code.",
    },
    expired: {
      text: "This sign-in has expired. Start again with your username and password.",
    },
    expired_provider: {
      text: "This sign-in has expired. Start again with your provider.",
    },
    invalid_credentials: {
      text: "Invalid credentials.",
    },
    invalid_directory_login: {
      text: "Invalid username or password.",
    },
    invalid_token: {
      text: "Invalid token.",
    },
    invalid_totp: {
      text: "Invalid two-factor code.",
    },
    second_factor_code: {
      text: "Two-factor authentication is enabled. Include {field}.",
    },
    second_factor_either: {
      text: "Two-factor authentication is enabled. Include {field}, or use a passkey: POST {where}.",
    },
    second_factor_passkey: {
      text: "This sign-in has a passkey now. Use it: POST {where}.",
    },
    locked_out: {
      text: "Too many failed attempts. Locked for {seconds} seconds.",
    },
    reset_unavailable: {
      text: "This server does not reset passwords by mail.",
      hint: "Ask a security officer for a new invitation.",
    },
  },
  token_activity: {
    intervention_token: {
      text: "No intervention {n} of this token",
    },
    needs_deploy: {
      text: "This needs the '{permission}' permission on {domain}",
      hint: "Ask an administrator of its team.",
    },
    schema_changed: {
      text: "Going back passes a deployment that changed the database's schema, in {apps}",
      hint: "Noust puts the code back, never the database: confirm it for those applications (schema_changed_ok) or leave them out.",
    },
    undo_unavailable: {
      text: "None of the applications asked for can be put back.",
      hint: "GET the plan of this intervention to see why each one cannot.",
    },
  },
  tokens: {
    api_token_named_already: {
      text: "An API token named {cleaned} already exists",
      hint: "Names are unique, including revoked tokens, so an audit line naming one always names one thing. Pick a new name.",
    },
    api_token_names_1: {
      text: "API token names are 1 to 64 characters",
      hint: "Name the token after what will hold it, such as 'ci-deploy'.",
    },
    api_tokens_act_sudo: {
      text: "API tokens cannot act in sudo mode on this server",
      hint: "Do what needs confirming from the console, as a person.",
    },
    api_tokens_last_most: {
      text: "API tokens last at most {token_max_days} days on this server",
      hint: "Ask for {longest} hours or fewer, and issue a new one then. A compliance framework asks for it: noust compliance status.",
    },
    assistant_token_acts_person: {
      text: "The assistant's token acts for a person and never in sudo mode",
      hint: "Issue it for an account, without allow_elevated: what needs a person's confirmation is done by the person, in the console.",
    },
    fleet_token_issued: {
      text: "A fleet token cannot be issued here",
      hint: "Fleet tokens are created on this server by 'noust fleet authorize --central-key KEY --name <central>', which also installs the central's restricted SSH key and prints the join code for it.",
    },
    prefix_matches_sessions: {
      text: "The prefix {candidate} matches {count} sessions",
      hint: "Send more characters of the session id so exactly one matches.",
    },
    reach: {
      text: "{username} does not reach that {kind}",
      hint: "A token bound to a scope can only be issued by someone who has a role there: give the account one first ('noust access grant').",
    },
    read_two_factor_state: {
      text: "Cannot read the two-factor state file {path}",
      hint: "Run the web panel as the user that owns it, or delete the file to turn two-factor authentication off on purpose.",
    },
    read_web_signing_key: {
      text: "Cannot read the web signing key {secret_file}",
      hint: "The web panel must run as the user that owns the key. Run as root, or set {state_dir_env} to a directory you own.",
    },
    server_api_token_belongs: {
      text: "On this server an API token belongs to an account",
      hint: "Issue it signed in as that account, or from the CLI with 'noust token create NAME --owner USERNAME'.",
    },
    session_id_prefix: {
      text: "Not a session id prefix: {prefix}",
      hint: "Prefixes are the leading characters of a session id as listed by GET /api/auth/sessions: hexadecimal, at least six characters.",
    },
    session_using: {
      text: "That is the session you are using",
      hint: "Revoking the current session from here would leave the browser holding dead cookies. Use Sign out, or POST /api/auth/logout.",
    },
    token_bound_team_project: {
      text: "A token bound to a team, project or environment belongs to an account",
      hint: "Issue it signed in as that account, or with '--owner USERNAME'.",
    },
    token_expiry_positive_number: {
      text: "The token expiry must be a positive number of hours",
      hint: "Omit it entirely for a token that only dies by revocation.",
    },
    token_hold_more_owner: {
      text: "A token cannot hold more than its owner: {items}",
      hint: "The {role} role does not hold them; leave them out.",
    },
    token_hold_more_owner_2: {
      text: "A token cannot hold more than its owner: {items}",
      hint: "Whoever issues it does not hold them; leave them out.",
    },
    token_takes_preset_list: {
      text: "A token takes a preset or a list of permissions, not both",
      hint: "Give the preset alone, or name the permissions yourself.",
    },
    two_factor_authentication_already: {
      text: "Two-factor authentication is already enabled",
      hint: "Disable it with a current code before enrolling a new authenticator.",
    },
    two_factor_authentication_enabled: {
      text: "Two-factor authentication is not enabled",
      hint: "There is nothing to disable. Enrol first from Settings.",
    },
    two_factor_authentication_enabled_2: {
      text: "Two-factor authentication is not enabled",
      hint: "There is nothing to regenerate. Enrol first from Settings.",
    },
    two_factor_enrolment_progress: {
      text: "No two-factor enrolment is in progress",
      hint: "Begin one first: POST /api/auth/2fa/enroll, or Enable in Settings.",
    },
    two_factor_state_file: {
      text: "The two-factor state file {path} is corrupt",
      hint: "Noust refuses to guess whether two-factor authentication was on. Restore the file from backup, or delete it with 'rm {path}' to disable the second factor on purpose.",
    },
    unknown_api_token_scope: {
      text: "Unknown API token scope: {scope}",
      hint: "Use one of: {items}.",
    },
    unknown_caller_api_token: {
      text: "Unknown caller for an API token: {caller}",
      hint: "Use one of: {items}, or none.",
    },
    unknown_permissions: {
      text: "Unknown permissions: {items}",
      hint: "GET /api/auth/roles lists every permission there is.",
    },
    web_signing_key_exists: {
      text: "The web signing key {secret_file} exists but is empty",
      hint: "Noust refuses to invent a new key silently, because that logs every operator out and hides whatever truncated the file. Restore the file from backup, or delete it with 'rm {secret_file}' to start over, which revokes all existing sessions on purpose.",
    },
    whoever_governs_accounts_issue: {
      text: "Only whoever governs accounts can issue an API token that acts in sudo mode",
      hint: "Ask an administrator to issue it, or issue the token without allow_elevated and do what needs confirming from the console, as a person.",
    },
  },
  webauthn: {
    passkeys_need_cryptography_library: {
      text: "Passkeys need the cryptography library, which is not installed here",
      hint: "Install python3-cryptography (python311-cryptography on openSUSE Leap), or pip install 'noust[web]', then restart noust-web. Python said: {exc}",
    },
  },
  team_access: {
    governs_members: {
      text: "{role_name} governs a team's members, which a {kind} does not have",
      hint: "Give 'admin' on the {kind}, or {role_name} on the team.",
    },
    name_environment: {
      text: "Name the environment",
      hint: "Give the environment's slug (environment=SLUG).",
    },
    name_project: {
      text: "Name the project",
      hint: "Give the project's slug (project=SLUG).",
    },
    name_team: {
      text: "Name the team",
      hint: "Give the team's slug (team=SLUG).",
    },
    no_such_account: {
      text: "No account named {username}",
      hint: "List the accounts with 'noust user list', or invite the person.",
    },
    no_such_role: {
      text: "No role named {role}",
      hint: "List them with 'noust access role list'.",
    },
    role_not_ownable: {
      text: "The role {role_name} holds nothing a {kind} can own",
      hint: "A team, a project or an environment can hold permissions about its applications, databases and backups; this role has none of those.",
    },
  },
  app_deploy_api: {
    application_found: {
      text: "Application not found: {validated}",
    },
    applications_more_call_deploys: {
      text: "{count} applications is more than one call deploys ({max_batch})",
      hint: "Split the list over several calls.",
    },
    commit_tag_both_name: {
      text: "A commit and a tag both name what to deploy",
      hint: "Give one of them.",
    },
    image_tag_version_deploy: {
      text: "An image tag is the version to deploy: a commit or a tag does not apply",
      hint: "Deploy the image tag, or a commit of the repository, not both.",
    },
    pull_request_number_1: {
      text: "A pull request number is 1 or more",
      hint: "Give the number the code host shows.",
    },
    pull_request_preview_rebuilt: {
      text: "A pull request preview is rebuilt from its branch: {others} does not apply",
      hint: "Ask for the preview alone, or deploy the application.",
    },
    run_image_registry: {
      text: "{domain} does not run an image from a registry",
      hint: "An image tag only means something to an application deployed with --image. Deploy the others by commit or tag.",
    },
  },
  appdir_releases: {
    active_release_roll_back: {
      text: "There is no active release to roll back from",
      hint: "{current_link} does not point at a release. {value} Name the one to activate explicitly.",
    },
    claim_release_directory: {
      text: "Could not claim a release directory in {releases_dir}",
      hint: "{max_name_attempts} names for {stamp} were taken. Check for a runaway process creating releases, then retry.",
    },
    commit_id: {
      text: "{commit} is not a commit id",
      hint: "Pass the full or abbreviated hash from `git rev-parse HEAD`, or None for a source that is not a git checkout.",
    },
    instance_zero_downtime_application: {
      text: "{color} is not an instance of a zero-downtime application",
      hint: "The instances are blue and green.",
    },
    keep_releases: {
      text: "Cannot keep {keep} releases",
      hint: "Keep at least 1 release. A release is the application's code; pruning them all would leave nothing to run or roll back to.",
    },
    persistent_path_path_inside: {
      text: "Persistent path {raw} is not a path inside the application",
      hint: "Persistent paths are relative to the application root, such as 'storage' or 'public/uploads', and may not be absolute or contain '..'.",
    },
    plain_directory: {
      text: "{colors_dir} is not a plain directory",
      hint: "A symlink or file there would make Noust write somewhere else. Remove it; Noust recreates the directory.",
    },
    point_release: {
      text: "Could not point {current_link} at release {release_id}",
      hint: "{error}. Nothing changed: {value}",
    },
    real_directory_release_link: {
      text: "{current_link} is a real directory, not a release link",
      hint: "This application is not on the release layout. Migrate it explicitly before activating releases.",
    },
    real_directory_release_link_2: {
      text: "{workers_link} is a real directory, not a release link",
      hint: "Noust keeps only a link there. Move what is in it away; Noust recreates the link.",
    },
    refusing_create_plain_directory: {
      text: "Refusing to create {shared}: {blocked} is not a plain directory",
      hint: "A symlink or file at {blocked} would make Noust create {path} somewhere outside {shared_dir}. Replace it with a directory, or create the shared path yourself.",
    },
    refusing_write_symlink: {
      text: "Refusing to write {env_file}: it is a symlink",
      hint: "Remove the link; Noust writes the port of each instance itself.",
    },
    release: {
      text: "{release} is not a release of {app_path}",
      hint: "Releases are directories in {releases_dir} named like 20260925-143012-a1b2c3d, created by a deploy.",
    },
    point_instance: {
      text: "Could not point the {color} instance at release {release_id}",
      hint: "{error}",
    },
    point_workers: {
      text: "Could not point {workers_link} at release {release_id}",
      hint: "{error}",
    },
    release_missing: {
      text: "Release {release_id} does not exist",
      hint: "{available}",
    },
  },
  deploy_commands: {
    pre_flight_checks_failed: {
      text: "Pre-flight checks failed",
      hint: "The following issues were found:\n{details}",
    },
  },
  deploy_hooks: {
    deploy_hooks_declared_applications: {
      text: "Deploy hooks are declared in {where}, and {app_type} applications have none",
      hint: "{reason}. Remove them{value}, or deploy the project as the type that runs it.",
    },
    hook_names_service_application: {
      text: "The hook names the service {service}, and a {app_type} application has no services",
      hint: "'service' is for Docker Compose stacks. Remove it to run the hook in the application's own tree.",
    },
    hook_workdir_directory_inside: {
      text: "The hook's workdir {workdir} is not a directory inside the application",
      hint: "Name a directory of the repository, relative to its root.",
    },
    could_not_run_detail: {
      text: "The {phase} hook '{command}' could not run: {reason}",
      hint: "{details}\n\n{output}",
    },
    post_could_not_run: {
      text: "The {phase} hook '{command}' could not run: {reason}",
      hint: "The new version is serving; only the hook failed. Run it again by hand, or fix it for the next deployment; its output is above and in the deployment's log.\n\n{output}",
    },
    post_exited: {
      text: "The {phase} hook '{command}' exited with {exit_code}",
      hint: "The new version is serving; only the hook failed. Run it again by hand, or fix it for the next deployment; its output is above and in the deployment's log.\n\n{output}",
    },
    post_timed_out: {
      text: "The {phase} hook '{command}' ran out of its {seconds} seconds",
      hint: "The new version is serving; only the hook failed. Run it again by hand, or fix it for the next deployment; its output is above and in the deployment's log.\n\n{output}",
    },
    pre_could_not_run: {
      text: "The {phase} hook '{command}' could not run: {reason}",
      hint: "Nothing was switched over: what served before is still serving. Fix the hook or what it checks, and deploy again; its output is above and in the deployment's log.\n\n{output}",
    },
    pre_exited: {
      text: "The {phase} hook '{command}' exited with {exit_code}",
      hint: "Nothing was switched over: what served before is still serving. Fix the hook or what it checks, and deploy again; its output is above and in the deployment's log.\n\n{output}",
    },
    pre_timed_out: {
      text: "The {phase} hook '{command}' ran out of its {seconds} seconds",
      hint: "Nothing was switched over: what served before is still serving. Fix the hook or what it checks, and deploy again; its output is above and in the deployment's log.\n\n{output}",
    },
  },
  deploy_release: {
    release_built: {
      text: "No release is being built",
      hint: "The fetch step stages the release; it has to run first.",
    },
  },
  deploy_site_service: {
    active_release_serve: {
      text: "{domain} has no active release to serve",
      hint: "Build one first: noust update {domain}",
    },
    build_start_names_none: {
      text: "build.start names {program}, which is in none of the project's tools, the pinned runtime or the system's directories",
      hint: "Install it where the service can run it, or name the program by its path in the project (./bin/server).",
    },
    deployer_configured: {
      text: "Deployer was not configured",
      hint: "Call configure(domain=DOMAIN, source=SOURCE) before deploy().",
    },
    runs_blue_green_needs: {
      text: "{domain} runs blue/green, which needs the proxy site; this release renders the {template} template",
      hint: "A noust.nginx.yaml gives the site routes of its own, which the two instances cannot share. Remove it, or turn the mode off first: noust app zero-downtime {domain} off",
    },
    runs_blue_green_serve: {
      text: "{domain} runs blue/green, which cannot serve a project from the subdirectory {base}",
      hint: "Both instances run from the root of their release. Turn the mode off (noust app zero-downtime {domain} off), or move the project to the root of the repository.",
    },
  },
  deploy_update: {
    release_layout: {
      text: "{domain} is not on the release layout",
      hint: "Only an application that builds releases can build one without serving it: noust app migrate {domain}",
    },
    release_layout_2: {
      text: "{domain} is not on the release layout",
      hint: "A release can only be promoted into an application that builds releases.",
    },
  },
  deployments_api: {
    deployment_record_id: {
      text: "Deployment record has no id",
      hint: "This should not happen for a row read back from the store.",
    },
    not_found: {
      text: "Deployment not found: {deployment_id}",
    },
    not_found_of: {
      text: "Deployment {deployment_id} of {domain} not found",
    },
    no_commit: {
      text: "Deployment {deployment_id} recorded no commit to rebuild",
      hint: "Its source is not a git repository. Update the application to build its source again.",
    },
  },
  health_gate: {
    command_health_check_needs: {
      text: "A command startup check needs the application it runs in",
      hint: "This is a fault of Noust, not of the check: report it.",
    },
  },
  jobs_api: {
    applications_request_updates_most: {
      text: "That is {count} applications; a request updates at most {max_group}",
      hint: "Narrow it to a project or an environment.",
    },
    invalid_job_id: {
      text: "Invalid job id: {job_id}",
      hint: "Job ids are hexadecimal, as returned when the job was queued.",
    },
    not_found: {
      text: "Job not found: {job_id}",
    },
    not_found_validated: {
      text: "Job not found: {validated}",
    },
    say_applications: {
      text: "Say which applications",
      hint: "Name a team, a project, an environment, a tag, or the domains to update.",
    },
    unknown_job_status: {
      text: "Unknown job status: {status}",
      hint: "Use one of: {items}.",
    },
  },
  lifecycle: {
    active_release_restart_under: {
      text: "{domain} has no active release to restart under the new limits",
      hint: "Build one first: noust update {domain}",
    },
    active_release_roll_back: {
      text: "There is no active release to roll back from",
      hint: "Name the release to activate: noust releases list <domain>.",
    },
    adopted_site_noust_never: {
      text: "{domain} is an adopted site: Noust never removes its files",
      hint: "Deleting it only forgets it. Remove the directory yourself if it should go.",
    },
    applications_deploy_single_commit: {
      text: "{app_type} applications cannot deploy a single commit",
      hint: "Update {domain} from its branch instead: noust update {domain}",
    },
    branch_pinned_application_follows: {
      text: "{domain} has a branch pinned, and an application follows a branch or tags, not both",
      hint: "Unpin it first: noust app branch {domain} --unpin",
    },
    commit_names_exactly_deploy: {
      text: "A commit names exactly what to deploy; a source or a branch does not apply",
      hint: "Run either 'noust update {domain} --commit {commit}' or an update from a source or a branch, not both.",
    },
    container_application: {
      text: "{domain} is not a container application",
    },
    deployed_git_deploy: {
      text: "{domain} is not deployed from git; there is no {value} to deploy",
      hint: "Its source is {fetch_from}. Rebuild it from there: noust update {domain}",
    },
    deployed_git_tags_follow: {
      text: "{domain} is not deployed from git, so it has no tags to follow",
      hint: "Only an application whose source is a git repository can follow tags.",
    },
    deployed_place_releases: {
      text: "{domain} is deployed in place and has no releases",
      hint: "Roll back to a backup with 'noust rollback {domain}', or move it onto releases with 'noust app migrate {domain}'.",
    },
    deployment_found: {
      text: "Deployment {deployment_id} of {domain} not found",
      hint: "See the application's history: GET /api/deployments?domain={domain}",
    },
    directory: {
      text: "{named} is not the directory of {domain}",
      hint: "Its directory is {app_path}; name exactly that one to remove it.",
    },
    follows_tags_branch_pin: {
      text: "{domain} follows tags ({value}), so it has no branch to pin",
      hint: "Stop following tags first: noust app follow-tags {domain} --off",
    },
    follows_tags_branch_update: {
      text: "{domain} follows tags ({pattern}), so it has no branch to update from",
      hint: "Stop following tags first: noust app follow-tags {domain} --off. To deploy one exact tag, noust update {domain} --tag TAG.",
    },
    follows_tags_deployed_git: {
      text: "{domain} follows tags but is not deployed from git",
      hint: "Stop following them: noust app follow-tags {domain} --off",
    },
    git_checkout_deploy: {
      text: "{domain} is not a git checkout; there is no {value} to deploy",
      hint: "An application deployed from a directory or an archive is rebuilt from its source: noust update {domain}",
    },
    image_registry_source_fetch: {
      text: "{domain} is an image from a registry: it has no source to fetch from",
      hint: "Change the image with: noust app container set {domain} --image REFERENCE, then update.",
    },
    image_release_longer_server: {
      text: "The image of release {release_id} is no longer on this server",
      hint: "It was pruned or never built here: rebuild its commit instead (noust update {domain} --commit COMMIT).",
    },
    keep_releases_images: {
      text: "{domain} does not keep its releases as images",
    },
    naming_directory_remove_contradicts: {
      text: "Naming a directory to remove contradicts keeping the files",
      hint: "Drop --keep-files (remove_files=false in the API) or the directory.",
    },
    older_deployed: {
      text: "{tag} is older than {deployed}, which is deployed",
      hint: "An update never goes back. To return to an earlier version use 'noust rollback {domain}', which asks first when the database changed; to rebuild one exact commit, 'noust update {domain} --commit SHA'.",
    },
    recorded_source_build_release: {
      text: "{domain} has no recorded source to build a release from",
      hint: "Pass one explicitly: noust update {domain} --source <git URL or path>",
    },
    release_built_served: {
      text: "Release {id} was built and has not served yet",
      hint: "Switching to it here would skip its pre-deploy hooks and migrations. Promote it instead: noust releases promote {domain} {id}",
    },
    release_exist: {
      text: "Release {release_id} does not exist",
      hint: "Releases, newest first: {ids}.",
    },
    release_exist_2: {
      text: "Release {release_id} does not exist",
      hint: "Releases on disk, newest first: {ids}.",
    },
    release_oldest_image_nothing: {
      text: "Release {id} is the oldest one with an image; there is nothing earlier to roll back to",
      hint: "Older images were pruned or never existed. Deploy a known-good commit instead, or restore a backup.",
    },
    release_oldest_nothing_earlier: {
      text: "Release {id} is the oldest one; there is nothing earlier to roll back to",
      hint: "Older releases were pruned or never existed. Deploy a known-good commit instead, or restore a backup.",
    },
    releases_layout_applications_support: {
      text: "{domain} is on the releases layout, which {app_type} applications do not support",
      hint: "Update it with the type it was created with.",
    },
    remote_tag_matching: {
      text: "The remote has no tag matching {pattern}",
      hint: "Push a tag that does, or change what {domain} follows: noust app follow-tags {domain} PATTERN",
    },
    runs_docker_containers_unit: {
      text: "{domain} runs in Docker containers, which its unit's limits do not reach",
      hint: "Set deploy.resources.limits for each service in the compose file.",
    },
    runs_docker_containers_unit_2: {
      text: "{domain} runs in Docker containers, which its unit's stop policy does not reach",
      hint: "Set stop_grace_period for each service in the compose file.",
    },
    runs_php_fpm_every: {
      text: "{domain} runs in the PHP-FPM every PHP application shares; a CPU quota cannot be given to its pool alone",
      hint: "Leave the CPU limit out: its memory limit and task limit (the pool's workers) apply.",
    },
    runs_php_fpm_every_2: {
      text: "{domain} runs in the PHP-FPM every PHP application shares, which is not stopped with its own",
      hint: "PHP-FPM's process_control_timeout is the server's setting.",
    },
    static_site_nothing_answers: {
      text: "{domain} is a static site; nothing answers HTTP for it but the web server",
      hint: "Its startup check is that the directory it serves has an index.html.",
    },
    static_site_process_own: {
      text: "{domain} is a static site; no process of its own runs to be {purpose}",
      hint: "The web server serves it straight from disk.",
    },
    tag_names_exactly_deploy: {
      text: "A tag names exactly what to deploy; a commit, a source or a branch does not apply",
      hint: "Run either 'noust update {domain} --tag {tag}' or an update from a commit, a source or a branch, not both.",
    },
    limits_unanswered_back: {
      text: "{domain} did not answer under the new limits; the previous ones are back",
      hint: "{evidence}",
    },
    limits_unanswered_dead: {
      text: "{domain} did not answer under the new limits; the previous ones are back, but it is not answering either",
      hint: "{evidence}",
    },
    limits_unanswered_restored: {
      text: "{domain} did not answer under the new limits; the previous ones are running again",
      hint: "{evidence}",
    },
  },
  migrate: {
    applications_use_release_layout: {
      text: "{value} applications cannot use the release layout yet",
      hint: "{domain} keeps updating in place, which is what it has always done.",
    },
    directory: {
      text: "{root} is not a directory",
      hint: "Update the application before migrating it.",
    },
    git_list_untracked: {
      text: "git could not list what is untracked in {root}",
      hint: "{value}\nName the paths to keep with --persist instead.",
    },
    held_before_migration_after: {
      text: "{root} held {before} before the migration and {after} after it",
      hint: "Every file must still be there, in the release or in shared/. The migration was undone.",
    },
    inside: {
      text: "{path} is inside {other}",
      hint: "Name only the outer one; everything under it moves with it.",
    },
    keep_shared_symlink_file: {
      text: "Cannot keep {path} in shared/: {value} is a symlink or a file",
      hint: "Only real directories and files move to shared/. Resolve the link or leave the path out with --persist.",
    },
    link_into_release: {
      text: "Could not link {items} into release {name}",
      hint: "Something is still in the release where the link must go.",
    },
    move_shared_symlink: {
      text: "Cannot move {path} to shared/: {value} is a symlink",
      hint: "Nothing was moved. Plan again.",
    },
    move_something_already: {
      text: "Cannot move {source} to {destination}: something is already there",
      hint: "Nothing was overwritten. Move it out of the way and retry.",
    },
    need_persist: {
      text: "{raw} does not need --persist",
      hint: "Noust keeps it at the application root, beside releases/.",
    },
    need_persist_2: {
      text: "{raw} does not need --persist",
      hint: "The .env always moves to shared/, and every release links it.",
    },
    plan: {
      text: "The plan is for {domain} at {app_path}, not {domain2} at {root}",
      hint: "Plan again and migrate with the new plan.",
    },
    pointed_release_already: {
      text: "{value} pointed at a release already",
      hint: "An in-place application has none. Check the directory by hand.",
    },
    release_layout_already: {
      text: "{domain} is on the release layout already",
      hint: "There is nothing to migrate. See its releases with: noust releases list {domain}",
    },
    sqlite_database_outside_paths: {
      text: "SQLite database(s) outside the paths named with --persist: {items}",
      hint: "A database left in the first release is not in the next one: the next deploy would start from an empty database. Name each one, or the directory that holds it, too: {items2}",
    },
    failed_undone: {
      text: "The migration of {domain} failed and was undone: {error}",
      hint: "{journal}",
    },
    gate_failed_back: {
      text: "{domain} did not pass its health check on the release layout; the in-place layout was put back",
      hint: "{evidence}",
    },
  },
  fail2ban: {
    installing_failed: {
      text: "Installing failed: {items}",
    },
    lift_failed: {
      text: "fail2ban did not lift the ban of {ip} in {name}",
    },
    logs_not_read: {
      text: "fail2ban did not read the logs of the new sites",
    },
    no_nginx_filter: {
      text: "This fail2ban has no filter for nginx",
      hint: "Missing: {items}. Update fail2ban.",
    },
    no_such_jail: {
      text: "There is no fail2ban jail {jail}",
      hint: "Jails: {value}.",
    },
    not_banned: {
      text: "{ip} is not banned by any jail",
    },
    not_running: {
      text: "fail2ban is not running",
    },
    jail_refused_back: {
      text: "fail2ban did not accept the sshd jail ({argv}), so {jail} was put back",
    },
    needs_epel: {
      text: "fail2ban comes from EPEL on this system",
      hint: "EPEL is a third-party repository maintained by Fedora. Confirm that it may be enabled to install fail2ban from it.",
    },
    never_answered_back: {
      text: "fail2ban never answered after it was started, so {jail} was put back",
      hint: "See why it did not start: journalctl -u fail2ban",
    },
    no_ban_action_back: {
      text: "fail2ban left the sshd jail with no ban action, so {jail} was put back",
    },
    web_jails_refused_back: {
      text: "fail2ban did not accept the nginx jails ({argv}), so {jail} was put back",
    },
    web_no_ban_action_back: {
      text: "fail2ban left an nginx jail with no ban action, so {jail} was put back",
    },
  },
  risks: {
    acceptance_end_future: {
      text: "The acceptance must end in the future",
    },
    reason_most_characters: {
      text: "The reason is at most {high} characters",
    },
    risk_accepted_days_most: {
      text: "A risk is accepted for {max_accept_days} days at most; accept it again then",
    },
    say_why_risk_acceptable: {
      text: "Say why the risk is acceptable, in {low} to {high} characters",
    },
  },
  ssh_keys: {
    key_damaged_key_base64: {
      text: "The key is damaged: its key is not base64",
    },
    public_key_damaged: {
      text: "The public key is damaged",
      hint: "Its key does not hold the type the line declares; copy the line again.",
    },
    public_key_line_openssh: {
      text: "The public key is not one line of an OpenSSH public key",
      hint: "Paste the whole content of your .pub file, on one line.",
    },
    public_key_type_noust: {
      text: "The public key's type is not one Noust adds",
      hint: "Use an ed25519 key (ssh-keygen -t ed25519), or ECDSA or RSA of at least {min_rsa_bits} bits. Options such as from= or command= are not accepted here.",
    },
    rsa_key_bits_too: {
      text: "The RSA key has {bits} bits, which is too weak",
      hint: "Use at least {min_rsa_bits} bits, or an ed25519 key.",
    },
  },
  backup: {
    hold_readable_archive: {
      text: "{name} does not hold a readable archive: {message}",
      hint: "{details}",
    },
    readable_manifest: {
      text: "{name} has no readable manifest: {exc}",
    },
  },
  backup_collection: {
    central_says_copies_server: {
      text: "Only a central says which copies of this server it holds",
      hint: "Call this with the fleet token a central holds for this server.",
    },
    file_went_away_changed: {
      text: "The file went away or changed since it was listed",
    },
    server_lets_copy_leave: {
      text: "This server lets a copy leave only encrypted, and this one is not",
      hint: "The ens-medium profile (or backup.encryption: required) refuses it. Seal the backups here: noust backup encryption enable, then noust backup encryption seal-existing.",
    },
    server_lists_such_copy: {
      text: "This server lists no such copy file",
    },
    unknown_kind_copy: {
      text: "Unknown kind of copy: {unknown}",
      hint: "The kinds are: {items}.",
    },
  },
  backup_collection_store: {
    central_say_holds_copies: {
      text: "A central may say it holds {max_held} copies of a server, no more",
    },
    copy: {
      text: "Not a copy: {kind} {item_id}",
      hint: "A copy is named by its kind and the identifier the list gave it.",
    },
    invalid_central_name: {
      text: "Invalid central name: {value}",
      hint: "A central is named like its fleet token: lower-case letters, digits and '-'.",
    },
    sha_256: {
      text: "Not a SHA-256: {sha256}",
      hint: "It is 64 hex digits.",
    },
  },
  backup_destination_files: {
    checksum_mismatch_after_downloading: {
      text: "Checksum mismatch after downloading {name} from {destination_name}",
      hint: "The downloaded file does not match the digest recorded when it was sent. Restoring it would restore corrupted data. Nothing was restored.",
    },
    download_produce_file: {
      text: "Download of {name} did not produce a file at {destination}",
    },
    failed_download: {
      text: "Failed to download {name} from {destination_name}",
      hint: "{value}",
    },
    failed_remove_destination: {
      text: "Failed to remove {victim} from the destination",
      hint: "{value}",
    },
    failed_upload: {
      text: "Failed to upload {name} to {destination_name}",
      hint: "{value}",
    },
    invalid_file_name: {
      text: "Invalid file name: {name}",
      hint: "A dump is named by its file name alone.",
    },
    invalid_file_name_2: {
      text: "Invalid file name: {name}",
      hint: "A file is named by its name alone.",
    },
    invalid_folder_destination: {
      text: "Invalid folder on the destination: {folder}",
      hint: "A folder is made of names with letters, digits, '_', '$', '-' and '.' (never first).",
    },
    is_not_on: {
      text: "{name} is not on {destination_name}",
      hint: "Looked in {target_dir}.",
    },
    metadata_downloaded_describes_another: {
      text: "The metadata downloaded for {name} describes another file",
      hint: "It names {value}. Nothing was restored.",
    },
    metadata_json_object: {
      text: "The metadata of {name} is not a JSON object",
    },
    read_metadata: {
      text: "Could not read the metadata in {target_dir}",
      hint: "{value}",
    },
    read_metadata_2: {
      text: "Could not read the metadata of {name}",
      hint: "{value}",
    },
    send_file: {
      text: "Cannot send {local}: it is not a file",
    },
    size_mismatch_after_downloading: {
      text: "Size mismatch after downloading {name}",
      hint: "The destination holds {expected} bytes of it.",
    },
    size_mismatch_after_uploading: {
      text: "Size mismatch after uploading {name}",
      hint: "Sent {count} bytes; the destination reports {size}.",
    },
    upload_reach_destination: {
      text: "Upload of {name} did not reach the destination",
      hint: "It was not listed there right after being copied.",
    },
    uploaded_verified_retention_applied: {
      text: "{name} was uploaded to {destination_name} and verified, but retention could not be applied there",
      hint: "{value}",
    },
  },
  backup_destinations: {
    backup_archive_found: {
      text: "Backup archive not found: {archive_path}",
    },
    backup_destination_already_exists: {
      text: "Backup destination already exists: {validated_name}",
    },
    backup_destination_changed_reached: {
      text: "Backup destination {name} was not changed: it could not be reached with the new settings",
      hint: "{value}",
    },
    backup_destination_encrypted: {
      text: "Backup destination {name} is not encrypted",
      hint: "There is no encryption key to show.",
    },
    backup_destination_encrypted_backups: {
      text: "Backup destination {name} is encrypted: the backups already sent there can only be read with its key",
      hint: "Removing it deletes the only copy Noust has of that key. Run 'noust backup destination show-key {name2}' and keep what it prints, then remove it confirming the key was saved (key_saved=true in the API).",
    },
    backup_destination_marked_encrypted: {
      text: "Backup destination {name} is marked encrypted but has no encryption key",
      hint: "Recreate it, or turn encryption off: a crypt remote with no password cannot be used.",
    },
    backup_destination_saved_reached: {
      text: "Backup destination {validated_name} was not saved: it could not be reached",
      hint: "{value}",
    },
    backup_destination_used_schedule: {
      text: "Backup destination {name} is used by {count} schedule(s): {items}",
      hint: "Pass --force to remove it and drop the reference from those schedules.",
    },
    backup_metadata_found: {
      text: "Backup metadata not found: {metadata_path}",
    },
    backup_says_belongs: {
      text: "Backup {backup_id} on {destination_name} says it belongs to {recorded_domain}, not to {app_name}",
      hint: "A backup is restored from the folder of the application it was taken from, and its metadata decides where a restore without a target goes, so the two must agree. Nothing was restored.",
    },
    checksum_mismatch_after_downloading: {
      text: "Checksum mismatch after downloading {backup_id} from {destination_name}",
      hint: "The downloaded archive does not match the checksum recorded when it was created. Restoring it would restore corrupted data.",
    },
    determine_application_belongs_pass: {
      text: "Cannot determine the application {validated_id} belongs to; pass app_name.",
    },
    download_produce_file: {
      text: "Download of {name} did not produce a file at {destination_path}",
    },
    downloaded_metadata_incomplete: {
      text: "Downloaded metadata for {backup_id} is incomplete",
      hint: "{exc}",
    },
    downloaded_metadata_json_object: {
      text: "Downloaded metadata for {backup_id} is not a JSON object",
    },
    downloaded_metadata_unreadable: {
      text: "Downloaded metadata for {backup_id} is unreadable",
      hint: "{exc}",
    },
    encrypted_backups_leave_server: {
      text: "{name} is not encrypted, and backups leave this server only encrypted",
      hint: "The ens-medium security profile (or backup.encryption: required) refuses unencrypted uploads. Encrypt it: noust backup destination update {name} --encrypt, then keep its key safe: noust backup destination show-key {name}.",
    },
    encryption_key_given_destination: {
      text: "An encryption key was given for a destination that is not encrypted",
      hint: "Add it with encryption on (--encrypt) to use an existing key.",
    },
    encryption_key_json_object: {
      text: "The encryption key must be a JSON object",
    },
    encryption_key_password: {
      text: "The encryption key has no password",
      hint: "Both passphrases 'noust backup destination show-key' printed are needed.",
    },
    encryption_key_password2: {
      text: "The encryption key has no password2",
      hint: "Both passphrases 'noust backup destination show-key' printed are needed: password2 is the salt, and without it nothing decrypts.",
    },
    encryption_key_valid_json: {
      text: "The encryption key is not valid JSON",
      hint: "{exc}",
    },
    encryption_keys_missing: {
      text: "Encryption keys for {name} are missing",
      hint: "The destination is marked encrypted but its keys were not found; backups written to it cannot be decrypted without them.",
    },
    enough_space_download: {
      text: "Not enough space to download {archive_name}",
      hint: "The archive is {size} bytes and {staging_dir} has {free} bytes free (plus {staging_headroom} bytes kept spare). Free some space there first; nothing was downloaded.",
    },
    failed_remove_destination_during: {
      text: "Failed to remove {backup_id}{suffix} from the destination during retention",
      hint: "{value}",
    },
    found_destination: {
      text: "{archive_name} was not found on the destination",
      hint: "Looked in {target_dir}. List what is there with 'noust backup remote-list'.",
    },
    hash_mismatch_after_uploading: {
      text: "Hash mismatch after uploading {name}",
      hint: "{hash_name} differs between the local file and the destination.",
    },
    invalid_application_name: {
      text: "Invalid application name: {app_name}",
      hint: "An application name is one path segment: letters, digits, '.', '_' and '-'.",
    },
    invalid_backup_destination_name: {
      text: "Invalid backup destination name: {name}",
      hint: "Use 1-32 characters: lowercase letters, digits and '-', starting with a letter or digit. The name also becomes an rclone remote name.",
    },
    list: {
      text: "Could not list {target_dir}",
      hint: "{value}",
    },
    metadata_downloaded_describes_another: {
      text: "The metadata downloaded as {metadata_name} describes another backup",
      hint: "It names {value}. Nothing was restored.",
    },
    missing_required_field_backend: {
      text: "Missing required field {key} for backend {backend}",
      hint: "{value}",
    },
    must_be_not: {
      text: "{label} must be {items}, not {raw}",
      hint: "{value}",
    },
    name_bucket_destination_writes: {
      text: "Name the bucket this destination writes into",
      hint: "The folder of an object store starts with its bucket: --field path=BUCKET/noust-backups. Noust does not create buckets.",
    },
    rclone_obscure_password: {
      text: "rclone could not obscure a password",
      hint: "{value}",
    },
    rclone_required_backup_destinations: {
      text: "rclone is required for backup destinations",
      hint: "{value}",
    },
    rclone_returned_invalid_json: {
      text: "rclone returned invalid JSON listing {target_dir}",
      hint: "{exc}",
    },
    reach_backup_destination: {
      text: "Could not reach backup destination {name}",
      hint: "{value}",
    },
    reach_backup_destination_2: {
      text: "Could not reach backup destination {name}",
      hint: "{said}",
    },
    reach_backup_destination_bucket: {
      text: "Could not reach backup destination {name}: the bucket {bucket} does not exist",
      hint: "{value}",
    },
    read_backup_metadata: {
      text: "Could not read the backup metadata in {target_dir}",
      hint: "{value}",
    },
    read_encryption_key: {
      text: "Could not read an encryption key",
      hint: "Give what 'noust backup destination show-key NAME' printed: its 'password:' and 'password2:' lines, its --json output, or the two passphrases on two lines.",
    },
    read_verify_upload: {
      text: "Could not read {local_file} to verify its upload",
      hint: "{exc}. The local backup may have been deleted while it was sent.",
    },
    refusing_backup_destination_host: {
      text: "Refusing backup destination host {host}: {exc}",
      hint: "{details}",
    },
    size_mismatch_after_uploading: {
      text: "Size mismatch after uploading {name}",
      hint: "Local file is {local_size} bytes; the destination reports {remote_size}.",
    },
    such_backup_destination: {
      text: "No such backup destination: {name}",
      hint: "Run 'noust backup destination list' to see the destinations Noust knows about.",
    },
    unknown_backup_destination_backend: {
      text: "Unknown backup destination backend: {backend}",
      hint: "Use one of: {items}.",
    },
    unknown_field_backend: {
      text: "Unknown field(s) for backend {backend}: {items}",
      hint: "Valid fields: {items2}.",
    },
    uploaded_verified_retention_applied: {
      text: "{id} was uploaded to {destination_name} and verified, but retention could not be applied there",
      hint: "{value}",
    },
    valid_b2_bucket_6: {
      text: "{label} is not valid: a B2 bucket has 6 to 50 characters",
    },
    valid_b2_bucket_uses: {
      text: "{label} is not valid: a B2 bucket uses letters, digits and hyphens",
    },
    valid_b2_reserves_names: {
      text: "{label} is not valid: B2 reserves names that start with b2-",
    },
    valid_bucket_name_3: {
      text: "{label} is not valid: a bucket name has 3 to 63 characters",
    },
    valid_bucket_name_ip: {
      text: "{label} is not valid: a bucket name is not an IP address",
    },
    valid_bucket_name_starts: {
      text: "{label} is not valid: a bucket name starts and ends with a letter or a digit",
    },
    valid_bucket_name_two: {
      text: "{label} is not valid: a bucket name has no two dots in a row",
    },
    valid_bucket_name_uses: {
      text: "{label} is not valid: a bucket name uses lowercase letters, digits, hyphens and {value}",
    },
    valid_container_3_63: {
      text: "{label} is not valid: a container has 3 to 63 characters",
    },
    valid_container_name_uses: {
      text: "{label} is not valid: a container name uses lowercase letters, digits and single hyphens, and starts and ends with a letter or a digit",
    },
  },
  backup_encryption: {
    backups_sealed_server: {
      text: "Backups are not sealed on this server",
      hint: "Turn sealing on first: noust backup encryption enable, or POST /api/backups/encryption/enable.",
    },
  },
  backup_failures: {
    backup_failure_found: {
      text: "Backup failure not found: {failure_id}",
    },
  },
  backup_files: {
    backups_sealed_key_missing: {
      text: "Backups are to be sealed but the key is missing",
      hint: "The secret {key_prefix}/{key_id} is gone. Stop sealing (noust backup encryption disable) or make a new key (noust backup encryption rotate); the backup was left as it was.",
    },
    backups_sealed_key_rotate: {
      text: "Backups are not sealed: there is no key to rotate",
      hint: "Turn sealing on first: noust backup encryption enable.",
    },
    id_key: {
      text: "{key_id} is not the id of a key",
      hint: "A key's id is eight lower-case hex digits, as the error of a backup that does not open names it.",
    },
    key_current: {
      text: "No key is current",
      hint: "Seal backups first: noust backup encryption enable.",
    },
    open: {
      text: "{name} does not open",
      hint: "{value}",
    },
    seal: {
      text: "Could not seal {name}",
      hint: "{value}. The backup was left as it was.",
    },
    sealed_server_hold_key: {
      text: "{name} is sealed and this server does not hold its key",
      hint: "It was sealed with key {value}. Put the key back with 'noust backup encryption import-key {value2}' (the value 'noust backup encryption show-key' printed when it was made, kept elsewhere), or restore from a copy that is not sealed.",
    },
    server_already_holds_key: {
      text: "This server already holds a key {key_id} with another value",
      hint: "Nothing was replaced. Check the id and the value you kept.",
    },
    server_holds_such_key: {
      text: "This server holds no such key",
      hint: "List the keys it holds with: noust backup encryption status.",
    },
    too_short_key: {
      text: "That is too short to be a key",
      hint: "Keys are {passphrase_length} characters; this has {count}.",
    },
  },
  backup_manager: {
    already_backup_directory: {
      text: "{source} is already the backup directory",
      hint: "Pass the directory the backups were written to by mistake.",
    },
    application_found: {
      text: "Application not found: {domain}",
      hint: "Nothing to back up at {app_path}.",
    },
    application_name_reserved_inside: {
      text: "Application name '{app_name}' is reserved inside a backup archive",
      hint: "Rename the deployment; this name collides with the archive payload.",
    },
    archive_corrupted: {
      text: "Archive is corrupted: {exc}",
    },
    archive_restored: {
      text: "Archive cannot be restored: {exc}",
      hint: "It would be refused by a restore too.",
    },
    archive_say_application_belongs: {
      text: "This archive does not say which application it belongs to",
      hint: "Pass --target-domain to restore it explicitly.",
    },
    backup_archive_contain_single: {
      text: "Backup archive does not contain a single application directory",
      hint: "Found: {value}.",
    },
    backup_archive_found: {
      text: "Backup archive not found: {archive}",
      hint: "Pass the path of a .tar.gz written by 'noust backup create'.",
    },
    backup_checksum_mismatch: {
      text: "Backup checksum mismatch: {name}",
      hint: "The archive does not match the checksum recorded when it was created. Restoring it would restore corrupted data.",
    },
    backup_claims_carry: {
      text: "Backup claims a {label} it does not carry: {relative}",
      hint: "The archive is incomplete; do not trust it as a backup.",
    },
    backup_contains_databases_database: {
      text: "This backup contains databases but the database managers are unavailable",
      hint: "{exc}. Install the database extras before restoring.",
    },
    backup_describes_database_version: {
      text: "The backup describes a database this version cannot restore",
      hint: "Entry: {entry}. Restore it with the version that took it.",
    },
    backup_entry_points_outside: {
      text: "Backup entry for {label} points outside the archive",
      hint: "{exc}",
    },
    backup_file_found: {
      text: "Backup file not found: {backup_file}",
      hint: "The metadata is there but the archive is not.",
    },
    backup_file_found_2: {
      text: "Backup file not found: {archive}",
      hint: "The metadata is there but the archive is not.",
    },
    backup_file_found_3: {
      text: "Backup file not found: {archive}",
    },
    backup_found: {
      text: "Backup not found: {backup_id}",
      hint: "Run 'noust backup list' to see the backups Noust knows about.",
    },
    backup_holds_copy_database: {
      text: "Backup {backup_id} holds no copy of a database of the stack",
      hint: "Only a backup taken before an update of a Docker Compose application, or with --include-databases, does. 'noust backup list' shows the others.",
    },
    backup_lists_found: {
      text: "Backup lists a {label} that cannot be found",
      hint: "Recorded location: {value}.",
    },
    backup_manifest_object: {
      text: "Backup manifest is not an object",
      hint: "The archive was not written by Noust.",
    },
    backup_manifest_unreadable: {
      text: "Backup manifest is unreadable",
      hint: "{exc}. The archive is corrupted or was not written by Noust.",
    },
    backup_names_application_root: {
      text: "Backup names an application root outside the archive: {name}",
      hint: "{exc}",
    },
    create_restore_workspace_beside: {
      text: "Could not create a restore workspace beside {app_path}",
      hint: "{exc}. Free space or fix permissions under {parent}.",
    },
    database_backup_requested_database: {
      text: "Database backup requested but the database managers are unavailable",
      hint: "{exc}. Install the database extras or drop --include-databases.",
    },
    directory: {
      text: "Not a directory: {source}",
      hint: "Pass the directory holding the misplaced <app>/ backup directories.",
    },
    dump_reach_backup_archive: {
      text: "Dump of '{name}' did not reach the backup archive",
      hint: "{engine} reported success but {target} does not exist.",
    },
    failed_back_up_database: {
      text: "Failed to back up {engine} database '{name}'",
      hint: "{exc}",
    },
    failed_create_backup_archive: {
      text: "Failed to create backup archive for {app_name}",
      hint: "{exc}",
    },
    failed_create_backup_directory: {
      text: "Failed to create backup directory: {backup_dir}",
      hint: "{exc}. Noust must run as root.",
    },
    failed_create_backup_directory_2: {
      text: "Failed to create backup directory: {app_backup_dir}",
      hint: "{exc}",
    },
    failed_restore_database: {
      text: "Failed to restore {engine} database '{name}' from {name2}",
      hint: "{exc}",
    },
    failed_restore_docker_volume: {
      text: "Failed to restore Docker volume: {volume}",
      hint: "{stderr}",
    },
    failed_restore_files_into: {
      text: "Failed to restore files into {app_path}",
      hint: "{exc}",
    },
    failed_store_backup_archive: {
      text: "Failed to store backup archive: {backup_file}",
      hint: "{exc}",
    },
    failed_write_backup_metadata: {
      text: "Failed to write backup metadata: {metadata_file}",
      hint: "{exc}",
    },
    installed_back_up: {
      text: "{engine} is not installed, cannot back up '{name}'",
      hint: "Install {engine} or run the backup without --include-databases.",
    },
    manager_available_database_engine: {
      text: "No manager available for database engine: {engine}",
      hint: "{value}",
    },
    per_schema_dumps_placed: {
      text: "Per-schema dumps cannot be placed inside a self-contained backup",
      hint: "Back up the whole database, or dump the schema on its own with pg_dump --schema.",
    },
    put_back_databases: {
      text: "Cannot put back the databases of {domain}: {message}",
      hint: "The databases are restored through the stack's compose file, which the restored tree must have.",
    },
    read: {
      text: "Cannot read {file_path}",
      hint: "{exc}",
    },
    read_2: {
      text: "Cannot read {name}",
      hint: "{exc}. The archive is corrupted or was not written by Noust.",
    },
    read_application_record: {
      text: "Could not read the application record for {domain}",
      hint: "{exc}",
    },
    refusing_restore: {
      text: "Refusing to restore {name}",
      hint: "{exc}",
    },
    refusing_restore_over_current: {
      text: "Refusing to restore over {app_path}: the current state could not be copied aside first",
      hint: "{exc}. Free space or fix permissions under {parent}, or move {app_path} out of the way yourself and restore again.",
    },
    restore_into: {
      text: "Cannot restore into {domain}",
      hint: "{exc}. The target domain does not yield a usable directory name.",
    },
    restoring_database_needs_application: {
      text: "Restoring the database of {domain} needs the application stopped",
      hint: "The dump replaces what the database holds, under an application that would still be writing to it. A restore stops the application itself; if it was asked not to, stop it first: noust stop {domain}",
    },
    unpacking_needs_about_bytes: {
      text: "Unpacking {backup_id} needs about {declared} bytes under {workspace} and only {free} are free",
      hint: "Free some space, or point TMPDIR at a larger filesystem.",
    },
    write_metadata_after_sealing: {
      text: "Could not write the metadata of {id} after sealing it",
      hint: "{exc}. Seal the existing backups again: it repairs this.",
    },
  },
  backup_priority: {
    backup_io_class: {
      text: "backup.io_class must be one of: {items}",
      hint: "It is {io_class}. Change it with: noust config set backup.io_class idle",
    },
    whole_number: {
      text: "{key} must be a whole number from {low} to {high}",
      hint: "It is {value}. Change it with: noust config set {key} VALUE",
    },
  },
  backup_records: {
    policy_stored: {
      text: "A {scope} policy cannot be stored for {db_name}",
      hint: "Only a policy that covers all databases is stored under {all_databases}, and it is always one.",
    },
  },
  backup_return: {
    bundle_noust_own_state: {
      text: "{file_name} is not a bundle of Noust's own state",
    },
    bundle_noust_own_state_2: {
      text: "{file_name} is not a bundle of Noust's own state: {exc}",
      hint: "Nothing was put in the bundles' folder.",
    },
    copy_application_data_needs: {
      text: "A copy of an application's data needs the metadata that says what it is",
      hint: "The central holds the archive without its sidecar, so what it holds and where it goes are not known. Nothing was restored.",
    },
    data: {
      text: "{file_name} is the data of {domain}, not of {wanted}",
      hint: "Restore it into the application it belongs to.",
    },
    metadata_match_mac_nothing: {
      text: "The metadata of {file_name} does not match its MAC; nothing was restored",
    },
    metadata_read: {
      text: "The metadata of {file_name} cannot be read: {exc}",
      hint: "Nothing was restored.",
    },
    say_application_backup_goes: {
      text: "Say which application the backup goes back into",
      hint: "The copy does not name one: give its domain.",
    },
    say_database_dump_loaded: {
      text: "Say which database the dump is loaded into",
      hint: "The copy names neither its engine nor its database: give them.",
    },
    server_already_bundle_called: {
      text: "This server already has a bundle called {file_name}",
      hint: "Nothing was replaced.",
    },
    unknown_kind_copy: {
      text: "Unknown kind of copy: {kind}",
    },
  },
  backup_returns: {
    chunk_most_bytes: {
      text: "A chunk may be at most {chunk_bytes} bytes here",
      hint: "Split the file.",
    },
    copy_finished_arriving: {
      text: "The copy has not finished arriving",
      hint: "{received} of {size} bytes are in. Send the rest, then complete the upload.",
    },
  },
  backup_scheduler: {
    backup_schedule_empty_too: {
      text: "Backup schedule is empty or too long",
      hint: "Use an alias ({items}) or a calendar expression.",
    },
    backup_taken_sent: {
      text: "Backup of {domain} was taken but could not be sent to: {items}",
      hint: "The local backup was kept.\n{items2}",
    },
    exists_database_backup_timer: {
      text: "{name} exists and is not a database backup timer",
      hint: "Another timer already has this name; remove it or rename the application that owns it before scheduling this database.",
    },
    exists_timer_noust_wrote: {
      text: "{name} exists and is not a timer Noust wrote for {what}",
      hint: "Another timer already has this name; remove it or rename the application that owns it before scheduling this.",
    },
    failed_enable_timer_timer: {
      text: "Failed to enable timer: {timer_name}.timer",
      hint: "{value}",
    },
    failed_enable_timer_timer_2: {
      text: "Failed to enable timer: {unit}.timer",
      hint: "{value}",
    },
    failed_render_systemd_template: {
      text: "Failed to render systemd template: {template_name}",
      hint: "{exc}. The Noust installation may be incomplete.",
    },
    failed_write_unit_file: {
      text: "Failed to write unit file: {path}",
      hint: "{exc}. Noust must run as root to manage systemd units.",
    },
    invalid_backup_schedule: {
      text: "Invalid backup schedule: {calendar}",
      hint: "A calendar expression may only contain letters, digits and '*-/,:. '.",
    },
    schedule: {
      text: "Cannot schedule {what}",
      hint: "{exc}. A unit file is generated from these values, so they are restricted to characters that cannot become a systemd directive.",
    },
    schedule_backup_application: {
      text: "Cannot schedule a backup for this application",
      hint: "{exc}. A unit file is generated from these values, so they are restricted to characters that cannot become a systemd directive.",
    },
    schedule_backups_application: {
      text: "Cannot schedule backups for this application",
      hint: "{exc}",
    },
    schedule_backups_database: {
      text: "Cannot schedule backups for this database",
      hint: "{exc}. A unit file is generated from the database's name, so it is restricted to characters that cannot become a systemd directive.",
    },
    schedule_keeps_nothing_server: {
      text: "A schedule that keeps nothing on this server needs a destination",
      hint: "Name at least one destination to send the backups to (--destination NAME), or keep the backups here.",
    },
    scheduled_backup_failed_verification: {
      text: "The scheduled backup of {domain} failed its verification and was not sent",
      hint: "{problem}. Run 'noust backup verify {id}' for the whole report.",
    },
  },
  backup_schedules: {
    backup_schedule: {
      text: "No backup schedule for {validated}",
    },
    domain_path_request_body: {
      text: "The domain in the path and in the request body must match.",
    },
  },
  backup_sealing: {
    backups_sealed_server: {
      text: "Backups are not sealed on this server",
      hint: "Turn sealing on first: noust backup encryption enable. Then seal what is already there with: noust backup encryption seal-existing",
    },
  },
  backup_timer_options: {
    time_limit: {
      text: "{text} is not a time limit",
      hint: "Use a whole number of seconds, or a number with s, m, h or d: 90m, 2h, 1d.",
    },
    time_limit_1_minute: {
      text: "A time limit is from 1 minute to 7 days",
      hint: "Got {seconds} seconds. Use 90m, 2h or 3600; leave it empty for no limit.",
    },
    time_limit_number: {
      text: "A time limit is a number",
    },
    time_zone_server_knows: {
      text: "{name} is not a time zone this server knows",
      hint: "Use a name of the zone database, such as Europe/Madrid, America/Argentina/Buenos_Aires or UTC (timedatectl list-timezones lists them).",
    },
  },
  backup_verify: {
    decompressed: {
      text: "{name} could not be decompressed",
      hint: "gzip -dc reported success but wrote no file.",
    },
    valid_gzip_file: {
      text: "{name} is not a valid gzip file",
      hint: "{value}",
    },
  },
  backups: {
    backup_destination_exist: {
      text: "Backup destination {name} does not exist",
      hint: "Create it first with: noust backup destination add",
    },
    backup_found: {
      text: "Backup not found: {file_name}",
      hint: "List the dumps with: noust db backups",
    },
    backup_found_2: {
      text: "Backup not found: {validated}",
    },
    backup_run_origin: {
      text: "A backup run cannot have the origin {origin}",
      hint: "Use one of: {items}.",
    },
    database_engine_noust_keeps: {
      text: "Not a database engine Noust keeps dumps of: {engine}",
      hint: "An engine is lower-case letters and digits, as 'noust db engines' lists it.",
    },
    database_exist: {
      text: "Database '{name}' does not exist",
      hint: "List them with: noust db list --engine {engine_name}",
    },
    database_needed_delete_copy: {
      text: "A database is needed to delete a copy from a destination",
      hint: "Name the database the dump is of: its folder there is the database's.",
    },
    destination_listed_twice: {
      text: "Destination {name} is listed twice",
    },
    dump_format_available: {
      text: "Dump format {dump_format} is not available for {display_name}",
      hint: "PostgreSQL takes custom, plain or tar; every other engine has one format.",
    },
    dump_reported_taken_read: {
      text: "The dump {name} was reported taken but cannot be read",
      hint: "{exc}. Check the permissions and the space of {parent}.",
    },
    dump_restore_into_temporary: {
      text: "The dump {name} did not restore into a temporary database",
      hint: "{value}",
    },
    dump_taken_failed_check: {
      text: "The dump {name} was taken but failed its check",
      hint: "{verify_detail}\n\nThe file is kept at {path}. Take another dump, and delete this one with: noust db backup-delete",
    },
    failed_check: {
      text: "{backup_name} failed its check",
      hint: "{value}",
    },
    failed_check_nothing_restored: {
      text: "{file_name} from {destination} failed its check; nothing was restored",
      hint: "{detail}",
    },
    failed_check_sent: {
      text: "{file_name} failed its check and was not sent to {destination}",
      hint: "{value}",
    },
    key_too_long_name: {
      text: "The key {engine} is too long to be the name of a folder on a destination",
      hint: "A folder's name is at most {max_folder_name} characters; rename the Compose project or the container.",
    },
    policy_keeps_nothing_server: {
      text: "A policy that keeps nothing on this server needs a destination",
      hint: "Name at least one destination to send the dumps to (--to NAME), or keep the dumps here.",
    },
    redis_policy_already_covers: {
      text: "A Redis policy already covers the whole instance",
      hint: "A snapshot is every slot at once: give the policy any database of the instance, such as 0.",
    },
    redis_restore_new_database: {
      text: "Redis cannot restore as a new database",
      hint: "A snapshot replaces every key of the instance.",
    },
    redis_snapshot_test_restored: {
      text: "A Redis snapshot cannot be test-restored",
      hint: "Restoring one replaces every key of the instance, so it cannot be tried on the side. Noust checks it with redis-check-rdb instead.",
    },
    redis_snapshot_test_restored_2: {
      text: "A Redis snapshot cannot be test-restored",
      hint: "It would replace every key of the instance.",
    },
    restore_into_temporary_database: {
      text: "{backup_name} did not restore into a temporary database",
      hint: "{value}",
    },
    superuser_load_ask: {
      text: "{display_name} has no superuser load to ask for",
      hint: "Only PostgreSQL loads a dump as the database's owner by default.",
    },
    whole_number_1: {
      text: "{field_name} must be a whole number from 1 to {ceiling}",
      hint: "Leave it empty for no limit.",
    },
  },
  central_backups: {
    central_holds_copy: {
      text: "The central holds no copy {item} of {node}",
    },
    server_named_managed_central: {
      text: "No server named {node} is managed by this central",
    },
    server_named_managed_central_2: {
      text: "No server named {target_node} is managed by this central",
    },
    unknown_kind_copy: {
      text: "Unknown kind of copy: {kind}",
      hint: "The kinds are: {items}.",
    },
  },
  restore_server: {
    bundle_store_read: {
      text: "The bundle's store could not be read",
      hint: "{exc}",
    },
    restoring_state_needs_bundle: {
      text: "Restoring the state needs the bundle and its passphrase",
      hint: "Give both, or pass skip_state when the state is already here.",
    },
  },
  self_backup: {
    back_noust_own_state: {
      text: "Could not back Noust's own state up",
      hint: "{exc}. Check the disk space of {directory} and that every file of Noust's directories can be read.",
    },
    backup_destination_exist: {
      text: "Backup destination {destination} does not exist",
      hint: "Create it first with: noust backup destination add",
    },
    bundle: {
      text: "No bundle at {path}",
      hint: "List them with: noust self-backup list, or give the whole path.",
    },
    bundle_failed_verification_sent: {
      text: "The bundle {name} failed its verification and was not sent",
      hint: "{items}",
    },
    bundle_hold_store_made: {
      text: "The bundle would hold no store, so it is not made",
      hint: "There is no {store_name} with content in {value}, the directory that holds the store Noust uses. Run: noust setup doctor",
    },
    envelope_something_else: {
      text: "{name} is an envelope of something else ({value})",
      hint: "A bundle of Noust's own state has the label {label}.",
    },
    failed_verification: {
      text: "{name} failed its verification",
      hint: "{items}",
    },
    hold_safe_archive: {
      text: "{name} does not hold a safe archive",
      hint: "{message}",
    },
    holds_noust_own_state: {
      text: "{name} holds {value}, not Noust's own state",
      hint: "A central's bundle is checked with: noust central backup --verify",
    },
    is_not_in: {
      text: "{path} is not in {directory}",
      hint: "Only a bundle this server keeps can be deleted here.",
    },
    name_bundle: {
      text: "{name_or_path} is not the name of a bundle",
      hint: "List them with: noust self-backup list",
    },
    name_bundle_2: {
      text: "{name} is not the name of a bundle",
    },
    noust_bundle: {
      text: "{name} is not a Noust bundle",
      hint: "{details}",
    },
    passphrase_given_none_stored: {
      text: "No passphrase was given and none is stored",
      hint: "Give one with the request, or set the schedule with a passphrase.",
    },
    passphrase_least_characters: {
      text: "The passphrase must have at least {min_passphrase_length} characters",
      hint: "Everything the bundle protects is as strong as its passphrase.",
    },
    passphrase_stored_scheduled_backup: {
      text: "No passphrase is stored for the scheduled backup of Noust's own state",
      hint: "Set it again with: noust self-backup schedule set",
    },
    readable_manifest: {
      text: "{name} has no readable manifest",
      hint: "{exc}",
    },
    schedule_keeps_nothing_server: {
      text: "A schedule that keeps nothing on this server needs a destination",
      hint: "Name at least one destination to send the bundles to (--to NAME), or keep the bundles here.",
    },
    schedule_needs_passphrase_seal: {
      text: "A schedule needs a passphrase to seal its bundles under",
      hint: "Give one with the passphrase option; Noust keeps it in its secret store so the timer can run unattended. Keep a copy of your own: without it no bundle opens.",
    },
    whole_number_1: {
      text: "{name} must be a whole number from 1 to {ceiling}",
      hint: "Leave it empty for no limit.",
    },
  },
  self_restore: {
    bundle_holds_secrets_sealed: {
      text: "The bundle holds secrets sealed under a key it does not carry",
      hint: "Nothing was changed. Restore the bundle's key (the file {named} of a bundle that has it), or restore without the secrets and enter the credentials again.",
    },
    bundle_names_path: {
      text: "The bundle names a path it must not: {relative}",
      hint: "A path in a bundle is relative and stays inside the directory it is restored to. Nothing was restored.",
    },
    bundle_store_schema_v: {
      text: "The bundle's store is schema v{version}; this Noust understands up to v{schema_version}",
      hint: "Install the Noust version that took it, or a newer one, then restore. Nothing was changed.",
    },
    bundle_verify_nothing_restored: {
      text: "The bundle does not verify; nothing was restored",
      hint: "{items}",
    },
    restore_stopped: {
      text: "The restore stopped: {message}",
      hint: "{details}{kept}\nThe services that were running were started again.",
    },
    restore_stopped_file_written: {
      text: "The restore stopped on a file that could not be written",
      hint: "{exc}.{kept}",
    },
    restored_store_opened_version: {
      text: "The restored store could not be opened by this version of Noust",
      hint: "{exc}",
    },
    secret_bundle_opened_key: {
      text: "The secret {name} in the bundle cannot be opened with the key the bundle carries",
      hint: "{message}. Nothing was changed. Restore it onto the kind of server it came from, or enter that credential again once the restore is done.",
    },
    secret_sealed_again_under: {
      text: "A secret could not be sealed again under this server's key: {message}",
      hint: "{value}",
    },
    server_already_holds_noust: {
      text: "This server already holds Noust state, and a restore replaces it",
      hint: "Restore onto a clean install, or confirm with --force. A bundle of what is here is taken first, so it can be put back.",
    },
    symbolic_link_nothing_restored: {
      text: "{walker} is a symbolic link; nothing is restored through one",
      hint: "Remove the link, or restore onto a clean install.",
    },
    take_bundle_before_replacing: {
      text: "Could not take a bundle of what is here before replacing it",
      hint: "{details}\nFix that, or restore without a safety copy: noust self-backup restore --no-safety-copy",
    },
  },
  volume_backup_records: {
    consistency_mode: {
      text: "{consistency} is not a consistency mode",
      hint: "Use one of: {items}.",
    },
  },
  volume_backups: {
    application_data_target: {
      text: "This application has no data target {unknown}",
      hint: "Its targets are: {value}. Add a directory with --path.",
    },
    application_started_again_after: {
      text: "The application could not be started again after the restore",
      hint: "{items}",
    },
    archive: {
      text: "Could not archive {source}",
      hint: "{exc}. Check that the data is readable and the disk has room.",
    },
    archive_match_digest_nothing: {
      text: "The archive of {id} does not match its digest; nothing was restored",
    },
    backup_failed_verification_sent: {
      text: "The backup {id} failed its verification and was not sent",
      hint: "{items}",
    },
    could_not: {
      text: "Could not {what}",
      hint: "{value}",
    },
    create_restore_workspace_beside: {
      text: "Could not create a restore workspace beside {destination}",
      hint: "{exc}",
    },
    data: {
      text: "{file_name} is the data of {domain}, not of {domain2}",
      hint: "Restore it into the application it belongs to.",
    },
    data_back_up: {
      text: "{domain} has no data to back up now",
      hint: "Its declared data directories do not exist yet. {value}",
    },
    declares_data_back_up: {
      text: "{domain} declares no data to back up",
      hint: "Declare a persistent path when deploying (--persist PATH), or add a directory to this policy with --path.",
    },
    docker_reported_success_produced: {
      text: "Docker reported success but produced no archive for the volume {volume}",
      hint: "Look for {value}.",
    },
    failed_archive_volume: {
      text: "Failed to archive the volume {volume}",
      hint: "{value}",
    },
    failed_check: {
      text: "{backup_id} failed its check",
      hint: "{items}",
    },
    failed_check_nothing_restored: {
      text: "{id} failed its check; nothing was restored",
      hint: "{items}",
    },
    failed_check_sent: {
      text: "{backup_id} failed its check and was not sent to {destination}",
      hint: "{items}",
    },
    failed_restore: {
      text: "Failed to restore {destination}",
      hint: "{exc}",
    },
    failed_restore_volume: {
      text: "Failed to restore the volume {source}",
      hint: "{value}",
    },
    failed_write_metadata: {
      text: "Failed to write the metadata of {backup_id}",
      hint: "{exc}",
    },
    hold_file: {
      text: "{name} does not hold the file {name2}",
    },
    list_containers: {
      text: "Could not list the containers of {domain}",
      hint: "{value}",
    },
    longer_declares: {
      text: "{domain} no longer declares {target}",
      hint: "Add it back (a persistent path, or --path in the policy) and restore again; nothing was restored.",
    },
    metadata: {
      text: "{file_name} has no metadata on {destination}",
      hint: "It was not sent by Noust, so what it holds and where it goes are not known. Nothing was restored.",
    },
    path_inside_application_data: {
      text: "{raw} is not a path inside the application's data",
      hint: "Give a path relative to where the data lives, such as 'uploads' or 'storage/app', without '..'.",
    },
    path_server: {
      text: "{key} has no path on this server",
    },
    policy_keeps_nothing_server: {
      text: "A policy that keeps nothing on this server needs a destination",
      hint: "Name at least one destination to send the backups to (--to NAME), or keep the backups here.",
    },
    restoring_failed: {
      text: "Restoring {id} failed: {message}",
      hint: "{value}",
    },
    restoring_failed_putting_data: {
      text: "Restoring {destination} failed and putting its data back failed too",
      hint: "{failure}\n{exc}\nThe data it had is at {previous}.",
    },
    started_again_after_data: {
      text: "{domain} could not be started again after its data was copied",
      hint: "{items}\nStart it with: noust start {domain}. The backup itself is unaffected.",
    },
    volume_backup_found: {
      text: "Volume backup not found: {backup_id}",
      hint: "List them with: noust backup volumes list",
    },
    volume_backup_found_2: {
      text: "Volume backup not found: {backup_id}",
    },
  },
  http3: {
    nginx_refused_site_holds: {
      text: "nginx refused the site that holds the QUIC socket",
      hint: "{errors}",
    },
    apache: {
      text: "Apache's stock builds have no HTTP/3",
      hint: "Serve this site with nginx 1.25 or later to use HTTP/3.",
    },
    no_version: {
      text: "nginx's version could not be read",
      hint: "Run: {command}",
    },
    not_built: {
      text: "This nginx {version} was built without --with-http_v3_module",
      hint: "Install the build from nginx.org, which includes it.",
    },
    too_old: {
      text: "nginx {version} is older than 1.25.0, the first with HTTP/3",
      hint: "Install a newer nginx: the nginx.org packages (https://nginx.org/en/linux_packages.html) carry it on Debian, Ubuntu and RPM systems.",
    },
  },
  log_retention: {
    days_number: {
      text: "days is a number from {min_days} to {max_days}",
    },
    most_disk_number_megabytes: {
      text: "The most disk is a number of megabytes from {min_mb} to {max_mb}",
    },
    name_keep_days_max: {
      text: "Name what to keep: --days, --max-use or --persistent",
    },
    read: {
      text: "{dropin} could not be read",
      hint: "{exc}",
    },
    retention_written_journald_restarted: {
      text: "The retention was written but journald could not be restarted",
      hint: "{value}",
    },
    written_noust: {
      text: "{dropin} was not written by Noust",
      hint: "Move it aside, or set the retention in that file yourself.",
    },
    written_noust_2: {
      text: "{dropin} was not written by Noust",
      hint: "It is left as it is.",
    },
    dropin_foreign: {
      text: "{dropin} exists and Noust did not write it, so it is left alone.",
    },
    journal_volatile: {
      text: "The journal is kept in memory and lost at every reboot. Add --persistent to keep it.",
    },
  },
  log_search: {
    level: {
      text: "{level} is not a level",
      hint: "The levels are: {items}.",
    },
    limit_number_1: {
      text: "limit is a number from 1 to {max_limit}",
    },
    time: {
      text: "{text} is not a time",
      hint: "Use epoch seconds, a time ago such as 15m, 2h or 7d, or a date such as 2026-10-16.",
    },
    window_ends_before_starts: {
      text: "The window ends before it starts",
    },
    window_longer_journal_hold: {
      text: "The window is longer than the journal can hold",
      hint: "Ask for at most {value} days at a time.",
    },
    container_unreadable: {
      text: "{domain}: {service} could not be read ({reason})",
    },
    containers_unlisted: {
      text: "{domain}: its containers could not be listed ({reason})",
    },
    journal_failed: {
      text: "The journal could not be searched: {reason}",
    },
    no_app_matches: {
      text: "No application you can search matches {names}",
    },
    stacks_no_levels: {
      text: "The containers of Docker stacks have no levels, so they are not searched when the query names a level.",
    },
  },
  waf: {
    nginx_refused_waf_rules: {
      text: "nginx refused the WAF's rules",
      hint: "{errors}",
    },
    noust_installs_waf_debian: {
      text: "Noust installs the WAF on Debian and Ubuntu only",
      hint: "Elsewhere install nginx's ModSecurity module and the OWASP Core Rule Set from your distribution or from nginx.org, then turn the WAF on.",
    },
    packages_installed: {
      text: "The packages could not be installed",
      hint: "The package manager's output is below.",
    },
    waf_packages_complete: {
      text: "The WAF's packages are not complete",
      hint: "Reinstall libnginx-mod-http-modsecurity and modsecurity-crs.",
    },
    base_missing: {
      text: "ModSecurity's base settings or the Core Rule Set's setup file are missing",
      hint: "Reinstall the packages: libnginx-mod-http-modsecurity and modsecurity-crs.",
    },
    module_missing: {
      text: "nginx has no ModSecurity module",
      hint: "Install it: {command} (Debian and Ubuntu: libnginx-mod-http-modsecurity and modsecurity-crs).",
    },
    module_not_loaded: {
      text: "The ModSecurity module is installed but nginx does not load it",
      hint: "Load it with a load_module line in nginx.conf (Debian and Ubuntu: {command}).",
    },
    not_nginx: {
      text: "The WAF is nginx's here: Noust drives ModSecurity through nginx",
      hint: "Serve this site with nginx to use the WAF.",
    },
    rules_missing: {
      text: "The OWASP Core Rule Set is not installed",
      hint: "Install it: {command} (Debian and Ubuntu: modsecurity-crs).",
    },
  },
  delivery: {
    time_limit: {
      text: "The {operation} of {domain} was stopped: it ran longer than this server's maximum deployment time ({limit}). Nothing that was serving was touched. Raise the limit with 'noust config set deploy.max_minutes <minutes>' (0 is no limit).",
    },
    time_limit_deploy: {
      text: "The deploy of {domain} was stopped: it ran longer than this server's maximum deployment time ({limit}). Nothing that was serving was touched. Raise the limit with 'noust config set deploy.max_minutes <minutes>' (0 is no limit).",
    },
    time_limit_update: {
      text: "The update of {domain} was stopped: it ran longer than this server's maximum deployment time ({limit}). Nothing that was serving was touched. Raise the limit with 'noust config set deploy.max_minutes <minutes>' (0 is no limit).",
    },
  },
  redirect: {
    host: {
      text: "The host is not a name or an address",
      hint: "Write the name or the address, with at most a port after it.",
    },
    port: {
      text: "{name} is not a port",
      hint: "Write a number from 1 to 65535, or take {name} out.",
    },
    port_console: {
      text: "{setting} is the console's own port ({port})",
      hint: "The console speaks TLS on it: the redirect needs a port of its own.",
    },
    port_open: {
      text: "Cannot open port {port} for the redirect to the console",
      hint: "{reason}. Another program holds it: choose another with {setting}, or take it out. The console itself is not affected.",
    },
    target: {
      text: "The request is not for a path of the console",
      hint: "Only a path and a query, as a browser writes them, are redirected.",
    },
  },
} as const;
