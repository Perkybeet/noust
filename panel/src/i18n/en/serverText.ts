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
    reason_required: {
      text: "Say why: this call needs a second person's approval, and a reason.",
      hint: "Send the reason in the {header} header (percent-encoded).",
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
      hint: "To turn the framework off (Settings > Controls):\n  {command}",
    },
    asked_by_setting: {
      text: "The control {control} asks for it, and it was turned on in Settings > Controls (or with the command line). It applies to everybody, whatever their role.",
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
  fleet: {
    refused: {
      permission: {
        text: "Your role {role} does not hold the permission {permission} on these servers",
        hint: "Ask a security officer of each of them for an account with a role that holds it, or have that permission added to your role there.",
      },
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
  invitation: {
    mail_failed: {
      text: "The invitation could not be mailed: {reason}",
      hint: "The invitation exists: hand its link over yourself.",
    },
  },
  mail: {
    no_public_url: {
      text: "The console has no public address to put in the link",
      hint: "Set web.public_url to the address people open the console at.",
    },
    no_transport: {
      text: "There is no way to send mail from this server",
      hint: "Set monitor.smtp.* or monitor.email_api.* (Settings > Channels), then ask again.",
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
      hint: "No framework asks for it: it was set in Settings > Controls or with the command line. To allow it: {command}",
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
