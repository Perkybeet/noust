/**
 * What turns on the machine's Docker for a central, and what the central does and does not do once it is on. Pure, so it is held by
 * tests without drawing a page: the lines a person adds to the central's compose file (the console cannot add them: a permission the
 * console gave itself would not be the person's decision), and what Noust can and cannot do on a machine that has Docker and nothing
 * else, tied to the server's table of functions so that what the page promises is what the table says.
 */

import type { components } from "../../../api/schema.gen";
import type { PlainKey } from "../../../i18n";

/** The number the page writes where the machine's own is unknown: the group Docker's socket usually belongs to. The check says the real one. */
export const SOCKET_GROUP = "999";

/** Where Docker's socket is on the machine, which the person looks at to learn its group. */
export const SOCKET_PATH = "/var/run/docker.sock";

/** The command that says which group owns the socket, as the person runs it on the machine. */
export const GROUP_COMMAND = `stat -c %g ${SOCKET_PATH}`;

/** One line of the fragment of the compose file: the keys it goes under are context, the lines that are added are what is copied. */
export interface ComposeLine {
  text: string;
  /** A line that is added to the file; the rest is where it goes. */
  added: boolean;
}

/** The fragment of the central's compose file that turns it on: Docker's socket, the group that opens it, and the setting that says why. */
export function composeLines(group: string = SOCKET_GROUP): readonly ComposeLine[] {
  return [
    { text: "services:", added: false },
    { text: "  noust:", added: false },
    { text: "    group_add:", added: true },
    { text: `      - "${group}"`, added: true },
    { text: "    volumes:", added: false },
    { text: `      - ${SOCKET_PATH}:${SOCKET_PATH}`, added: true },
    { text: "    environment:", added: false },
    { text: "      NOUST_DEPLOY_HERE: containers", added: true },
  ];
}

/** The text that goes on the clipboard: the fragment whole, with the keys it goes under, so it is clear where each line is put. */
export function composeText(group: string = SOCKET_GROUP): string {
  return composeLines(group)
    .map((line) => line.text)
    .join("\n");
}

type Key = keyof components["schemas"]["FeatureStates"];

/** What Noust does on a machine that has Docker, by the function of the table that says it can; each line of the page is one sentence. */
export const CAN: readonly { text: PlainKey; keys: readonly Key[] }[] = [
  { text: "containerHost.deployHere.able.canContainers", keys: ["container_deploy", "applications", "application_environments"] },
  { text: "containerHost.deployHere.able.canStacks", keys: ["recipes"] },
  { text: "containerHost.deployHere.able.canUpdates", keys: ["releases", "rollbacks"] },
  { text: "containerHost.deployHere.able.canBackups", keys: ["application_backups", "volume_backups"] },
  { text: "containerHost.deployHere.able.canTunnels", keys: ["tunnels", "dns_accounts", "dns_records"] },
  { text: "containerHost.deployHere.able.canZone", keys: ["docker_zone"] },
];

/**
 * What the table says works on such a machine and the console has no page for there, so that it is neither promised nor said to be refused.
 * The engines Noust runs in containers are listed on the Engines page, which asks the machine's own databases first and is refused
 * (`GET /api/databases/servers` answers 409 where `databases` is not available), so Databases is not offered on a machine that only
 * has Docker. The day the listing reads the instances alone, this goes and the engines are said. `deploy_here` is this page itself:
 * the table says a hub has it, and a page does not list itself among what it turns on.
 */
export const NOT_SHOWN: readonly Key[] = ["database_instances", "deploy_here"];

/** What it does not do there, by the functions of the table that say it cannot: they need systemd or a web server, which the machine does not have. */
export const CANNOT: readonly { text: PlainKey; keys: readonly Key[] }[] = [
  { text: "containerHost.deployHere.able.cannotNative", keys: ["native_deploy", "importing_applications", "application_diagnosis"] },
  { text: "containerHost.deployHere.able.cannotSites", keys: ["domains", "sites", "certificates", "published_ports", "noust_dns", "status_page", "console_exposure"] },
  { text: "containerHost.deployHere.able.cannotPreviews", keys: ["preview_environments"] },
  { text: "containerHost.deployHere.able.cannotScheduled", keys: ["scheduled_jobs"] },
  { text: "containerHost.deployHere.able.cannotRelay", keys: ["zero_downtime"] },
  { text: "containerHost.deployHere.able.cannotDatabases", keys: ["databases", "database_backups", "point_in_time_recovery", "public_database_access"] },
  {
    text: "containerHost.deployHere.able.cannotMachine",
    keys: [
      "services",
      "software",
      "firewall",
      "server_security",
      "system_clock",
      "power",
      "system_logs",
      "log_destinations",
      "process_monitor",
      "server_setup",
      "server_health",
      "root_shell",
      "moving_a_server",
      "restoring_a_server",
      "checking_a_moved_server",
      "system_processes",
      "system_files",
      "system_updates",
    ],
  },
];

/** The state of the page: off until the central was told, then what the check says. */
export type DeployHereState = "off" | "checking" | "problem" | "on";

/**
 * Where the page stands.
 *
 * @param told Whether the central was told, in its compose file, to deploy here (`session.central.deploy_here`).
 * @param report What the check said (`ready`: no check failed, which is the server's word and not a count made here), or null before
 *     it has been run.
 * @param running The check is being made.
 */
export function stateOf(told: boolean, report: { ready: boolean } | null, running: boolean): DeployHereState {
  if (running) return "checking";
  // The report is the machine's own word: a check that failed is a problem whatever the setting says, and a report with none that
  // failed says the central can deploy, which is what the setting alone cannot promise.
  if (report !== null) return !report.ready ? "problem" : told ? "on" : "off";
  return told ? "checking" : "off";
}
