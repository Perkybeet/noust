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
  approvals: {
    asked_approved: {
      text: "This needs a second person's approval, whoever you are. Request {request} is approved: send the call again with it now.",
      hint: "Once approved, send exactly the same call again with the header {header}: {request}, within {minutes} minutes.",
    },
    asked_waiting: {
      text:
        "This needs a second person's approval, whoever you are. Request {request} is waiting for an account with one of these roles to decide it: {approvers}.",
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
  controls: {
    asked_by_framework: {
      text:
        "The control {control} asks for it, and the framework in force ({frameworks}) asks for that control, so it stays on while the framework is on and cannot be turned off by itself.",
      hint: "To turn the framework off (Settings > Controls):\n  {command}",
    },
    asked_by_setting: {
      text:
        "The control {control} asks for it, and it was turned on in Settings > Controls (or with the command line). It applies to everybody, whatever their role.",
      hint: "To turn it off there, or:\n  {command}",
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
        text:
          "Nothing here stops working: Noust accepts the old names while they are met. 'noust setup rename' finishes what can be finished without restarting anything.",
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
        text:
          "A certificate is asked for on port 80 and sites are served on 443: the web server cannot start while another program holds them.",
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
        hint:
          "Ask a security officer of each of them for an account with a role that holds it, or have that permission added to your role there.",
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
      hint:
        "A server always keeps somebody who can do everything. Give the owner role to another account that can sign in first, then this change can go ahead.",
    },
    name_invalid: {
      text: "Invalid role name: {name}",
      hint: "Use 2 to 40 lower-case letters, digits or '-', starting and ending with a letter or digit, such as 'release-manager'.",
    },
    name_system: {
      text: "{name} is the name of a built-in role",
      hint:
        "Pick another name. The built-in roles can be edited and put back, not redefined under their own name.",
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
      hint:
        "It holds every permission there is, so that the server always has somebody who can do everything. To give a person all of it, give them the owner role.",
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
      hint:
        "Its roles together hold {governing} (who may run the server) with {running} (running it). Separation of duties keeps them in different hands: give the person a second account for the other duty.",
    },
    separation_role: {
      text: "{subject} would govern access and run the server at once",
      hint:
        "It holds {governing} (who may run the server) together with {running} (running it). Separation of duties keeps them in different hands: split it into two roles.",
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
      text:
        "The compliance framework in force ({frameworks}) keeps the terminal on the server itself off (the control {control} is refused)",
      hint:
        "A root shell in the browser is the console at its most root-equivalent. To allow it anyway, which is put on record: {command}. Or turn the framework off: {turn_off_command}",
    },
    server_off: {
      text: "The terminal on the server itself is off (terminal.server.enabled)",
      hint: "It is a root shell, so it stays off until an administrator turns it on: {command}",
    },
  },
  tunnels: {
    cloudflared_missing: {
      text: "cloudflared is not installed",
      hint:
        "Noust installs it from Cloudflare's own package repository, {url}, with the key Cloudflare signs it with: run 'noust tunnel install-cloudflared', then this command again.",
    },
    no_account: {
      text: "There is no Cloudflare account to manage a tunnel with",
      hint: "Add one with Account > Cloudflare Tunnel:Edit: noust dns provider add NAME --kind cloudflare --token-stdin",
    },
    store_sealed: {
      text: "The secret store is sealed",
      hint:
        "A tunnel's connector reads its token at boot, with nobody to type a passphrase. Run tunnels on a server, not on a sealed central.",
    },
  },
} as const;
