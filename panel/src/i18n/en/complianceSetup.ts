/**
 * Strings of Settings > Security > Compliance: the frameworks an operator can switch on (ENS, ISO/IEC
 * 27001, NIS2, CIS, SOC 2, GDPR), the controls they are made of, and what each framework turns on
 * before it is confirmed. The words are careful on purpose: a framework turns on technical controls
 * that help with a standard, and the console never says a server complies. What the server refuses
 * with is shown as it was written.
 */
export const complianceSetup = {
  description:
    "Nothing is on until you choose it. Several frameworks can be on together, and each control takes the strictest value any of them asks.",
  honest:
    "A framework turns on technical controls. It does not make a server compliant: what stays organisational is yours, and only an auditor can say the rest.",
  loadFailed: "Could not read the compliance settings",
  loading: "Loading the compliance settings",
  helpLabel: "About compliance",
  none: {
    title: "No framework is on",
    description: "Noust uses its normal values and there is no report to read. That is not a warning: it is normal when nothing obliges you.",
  },
  frameworks: {
    title: "Frameworks",
    others: "Other frameworks you can turn on",
    description: "Turn one on to switch on the controls it names. You see what changes, and the clause each one serves, before you confirm.",
    off: "Off",
    controlsCount: { one: "{count} control", other: "{count} controls" },
    enable: "Turn on…",
    disable: "Turn off…",
    staysYours: "What stays yours",
    organisational: "Stays organisational",
    notCovered: "Technical, and not enforced here",
    summary: {
      ensBasica:
        "Spain's National Security Scheme, category BÁSICA: the measures of Annex II that apply to the lowest category.",
      ensMedia:
        "Spain's National Security Scheme, category MEDIA: what Noust has called the ens-medium profile since 3.1.",
      ensAlta: "Spain's National Security Scheme, category ALTA: MEDIA plus a backup kept apart.",
      iso27001: "The international information security management standard: the Annex A controls a deployment tool can enforce.",
      nis2: "The EU directive on network and information security: the risk-management measures of article 21 a server can help with.",
      cisIg1: "CIS Controls v8, Implementation Group 1: essential cyber hygiene, the safeguards a deployment tool can enforce.",
      cisIg2: "CIS Controls v8, Implementation Group 2: IG1 plus the safeguards of IG2 a deployment tool can enforce.",
      soc2Security: "SOC 2 Security criteria: the logical access, monitoring and change-management criteria a server can help with.",
      gdpr32: "GDPR article 32: the technical measures for the security of processing a server can help with.",
    },
  },
  dialog: {
    enableTitle: "Turn on {name}",
    disableTitle: "Turn off {name}",
    enableLead: "This is what changes on this server.",
    disableLead:
      "Protection goes down. What this framework forced goes back to the default, or to what another framework or your own setting still asks.",
    nothing: "No control changes value.",
    planning: "Working out what changes",
    planFailed: "Could not work out what changes",
    control: "Control",
    now: "Now",
    then: "Then",
    because: "Because",
    understood: "I have read this and want to go ahead",
    enableAction: "Turn on",
    disableAction: "Turn off",
    turnedOn: "{name} is on",
    turnedOff: "{name} is off",
  },
  warning: {
    heading: "Look at this first",
    no_security_officer:
      "No active account has the security role, and administrators will stop managing accounts, security settings and the audit trail. Create one first, from a terminal: noust user create NAME --role security.",
    separation_conflicts: {
      one: "{count} person holds accounts that cannot go together. Split them, or record a documented exception, or they will be refused.",
      other:
        "{count} people hold accounts that cannot go together. Split them, or record a documented exception, or they will be refused.",
    },
  },
  report: {
    download: "Download the report",
    line: "On. Its report is made when you open this page.",
    viaProfile: "On through security.profile. Its report is made when you open this page.",
    forbidden: "Your role cannot read the report. Whoever holds compliance.read can.",
    loading: "Loading the report",
    loadFailed: "Could not read the report",
    allMet: { one: "The control is in force", other: "All {count} controls are in force" },
    someMet: "{met} of {total} controls in force",
    caption: "What {name} asks that is not in force yet",
    met: {
      one: "The other {count} control is in force or does not apply. The report you download lists every one.",
      other: "The other {count} controls are in force or do not apply. The report you download lists every one.",
    },
    nothingToMend: "Everything it asks is in force or does not apply here.",
    status: { fail: "Not met", warning: "Partly met", ok: "Met", na: "Does not apply" },
    column: { control: "Control", state: "State", fix: "Where to mend it" },
    moreLabel: "What was found for {name}",
    fix: "How to fix it:",
  },
  fix: {
    accounts: "See the accounts",
    accessSecurity: "Open access security",
    backups: "See the backups",
    approvals: "Open approvals",
    tokens: "Open the tokens",
    audit: "Open the audit log",
    logShipping: "Open the log export",
    console: "Open the console address",
  },
  lockdown: {
    title: "The console is locked down for an incident",
    description: "Only the access token signs in until the lockdown is lifted. The evidence taken when it started is kept.",
    since: "Since",
    by: "By",
    reason: "Incident",
    package: "Evidence",
    command: "Lift it from a terminal on this server",
  },
  controls: {
    title: "Controls",
    description:
      "Each control has its own setting. A framework that is on locks the ones it asks for; you can always tighten one further.",
    help: "A control is one rule with its own setting. A framework that is on locks the ones it asks for at its value or stricter: you can always tighten one further, and never loosen a locked one.",
    lockedBy: "Required by {names}",
    change: "Change…",
    noPermission: "Your role cannot change security settings.",
    checkOnly: "Judged by the compliance check: nothing refuses it",
    groupSummary: "{on} of {total} strict",
    ownNotCounted: "Nobody chose this number: it is the default, and it limits nothing until a framework asks for it or you choose one.",
    choose: "Choose",
    changed: "{name} changed",
    group: {
      access: "Access and accounts",
      accountability: "Accountability and the audit trail",
      backup: "Backups",
      network: "Network and host",
    },
    dialog: {
      title: "Change {name}",
      value: "Value",
      noLimit: "No limit",
      stricter: "A framework asks for {floor} or stricter.",
      failed: "Could not change the control",
    },
  },
  value: {
    on: "On",
    off: "Off",
    none: "No limit",
  },
  unit: {
    minutes: { one: "{count} minute", other: "{count} minutes" },
    hours: { one: "{count} hour", other: "{count} hours" },
    days: { one: "{count} day", other: "{count} days" },
    characters: { one: "{count} character", other: "{count} characters" },
    attempts: { one: "{count} attempt", other: "{count} attempts" },
  },
  choice: {
    optional: "Optional",
    required: "Required",
    any: "Any certificate",
    operator: "The organisation's certificate",
    allowed: "Allowed",
    refused: "Refused",
    break_glass: "Break-glass",
    recovery: "Account recovery only",
  },
  control: {
    roles_split: {
      title: "Split the administrator's duties",
      summary: "Accounts, security and audit go to other roles.",
    },
    separation_of_duties: {
      title: "Separation of duties",
      summary:
        "One person cannot hold the account that runs the server and the one that governs it, unless a documented exception is recorded.",
    },
    single_role_per_account: {
      title: "One role per account",
      summary: "A person with two duties holds two accounts.",
    },
    sso_no_admin: {
      title: "Identity providers never grant admin",
      summary: "A person grants admin, never an identity provider.",
    },
    second_factor: {
      title: "Second factor required",
      summary: "Every account enrols an authenticator or a passkey before anything else.",
    },
    approvals: {
      title: "Approval by a second person",
      summary: "Root-level actions and role changes need a second person.",
    },
    restricted_tokens: {
      title: "Restricted API tokens",
      summary: "A token has an owner and no root-level permissions.",
    },
    token_production_approval: {
      title: "Token changes in production wait for a person",
      summary: "What an API token changes in production is approved by a person first.",
    },
    token_max_days: {
      title: "Longest API token life",
      summary: "An expiry becomes mandatory, at most this long.",
    },
    master_token: {
      title: "Master token",
      summary: "What the master token may do once accounts exist.",
    },
    sudo_password: {
      title: "Password for sudo mode",
      summary: "Confirming it's you also asks for the account's password.",
    },
    sudo_idle_minutes: {
      title: "Sudo mode idle time",
      summary: "How long sudo mode stays open without an elevated action in it.",
    },
    sudo_max_minutes: {
      title: "Sudo mode longest time",
      summary: "The longest sudo mode lasts once confirmed.",
    },
    session_idle_minutes: {
      title: "Session idle time",
      summary: "A console session unused this long is over.",
    },
    session_absolute_hours: {
      title: "Session longest time",
      summary: "A session ends this long after sign-in, however active it is.",
    },
    lockout_threshold: {
      title: "Failed sign-ins before a lock",
      summary: "Consecutive refused sign-ins that lock an account.",
    },
    lockout_minutes: {
      title: "Lock duration",
      summary: "How long that lock lasts at least.",
    },
    password_min_length: {
      title: "Shortest password",
      summary: "The fewest characters a password may have.",
    },
    access_review_days: {
      title: "Access review",
      summary: "Accounts and roles are reviewed at least this often.",
    },
    stale_account_days: {
      title: "Stale accounts",
      summary: "An account unused this long is reported.",
    },
    change_reasons: {
      title: "A reason for every change",
      summary: "Command-line changes and approval requests say why.",
    },
    exception_reasons: {
      title: "A reason for every exception",
      summary:
        "Building as root, accepting a risk, a privileged container or an incident freeze says why.",
    },
    audit_retention_days: {
      title: "Audit retention",
      summary: "The least number of days audit events are kept.",
    },
    audit_fail_closed: {
      title: "Refuse changes when the audit trail fails",
      summary: "A change is refused while the audit trail cannot be written.",
    },
    external_audit_sink: {
      title: "Audit trail off the machine",
      summary: "The audit trail is sent to a destination off the machine.",
    },
    audit_review_days: {
      title: "Audit review",
      summary: "The audit log is reviewed at least this often.",
    },
    log_shipping_tls: {
      title: "Logs only over TLS",
      summary: "Logs leave the server only over TLS.",
    },
    backup_encryption: {
      title: "Encrypted backups",
      summary: "Backups leave the server only encrypted.",
    },
    backup_verify_days: {
      title: "Backup verification",
      summary: "A backup is proved restorable at least this often.",
    },
    restore_drill_days: {
      title: "Restore rehearsal",
      summary: "Restoring a backup is rehearsed at least this often: the data loads into a temporary database, which is more than the archive reading.",
    },
    backup_max_age_hours: {
      title: "Newest backup age",
      summary: "The newest backup of an application must be younger than this.",
    },
    backup_offsite: {
      title: "A backup kept apart",
      summary: "At least one backup is kept in a different place from the server.",
    },
    tls_certificate: {
      title: "Console certificate",
      summary: "The console serves a certificate of the organisation's PKI, not a self-signed one.",
    },
    certificate_min_days: {
      title: "Certificate warning",
      summary: "A certificate expiring sooner than this is a finding.",
    },
    explicit_allowlist: {
      title: "Name who may connect",
      summary: "A console reachable beyond loopback names who may connect.",
    },
    console_exposure: {
      title: "Publishing the console",
      summary: "Whether the console may be published on a public name.",
    },
    database_remote_listen: {
      title: "Databases beyond loopback",
      summary: "Whether a database engine may listen beyond loopback.",
    },
    server_terminal: {
      title: "Terminal on the server",
      summary: "Whether Noust may open a root shell on the server in the browser.",
    },
    assistant_flows: {
      title: "Assistant: models on your side",
      summary:
        "The assistant is turned on only with a model of your own, or with a provider declared as an authorized flow.",
    },
    terminal_idle_minutes: {
      title: "Terminal idle time",
      summary: "A console terminal with nothing typed this long is ended.",
    },
    terminal_max_minutes: {
      title: "Terminal longest time",
      summary: "The longest a console terminal lasts in all.",
    },
    mounts_read_only: {
      title: "Server paths read-only",
      summary: "A server path bound into an application is read-only.",
    },
    db_write_root_equivalent: {
      title: "SQL writes need root-equivalent",
      summary: "Writing SQL from the console needs the root-equivalent permission.",
    },
  },
} as const;
