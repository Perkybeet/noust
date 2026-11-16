/**
 * How a new application is reached on a central that deploys on its machine's own Docker (a NAS): not by a domain, but at a port of the
 * machine. The name it is given is only what Noust calls it. Pure, so it is held by tests and not by drawing the step.
 */

import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";

/** The first port of the machine an application is offered: above the ports a NAS's own interface and services take (5000, 5001, 9999, 80, 443). */
export const FIRST_MACHINE_PORT = 8081;

/** What a name may be made of: lowercase letters, digits, dots and dashes, starting and ending with a letter or a digit. The server decides again. */
const NAME = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/;

/**
 * Why a name is not one Noust would take for an application here, or null when it looks like one. A name is not an address: it needs no
 * dot and no top-level domain, which is what a domain is asked for on a server.
 *
 * @param value What was typed.
 * @param deployed The names of the applications that already exist here.
 */
export function nameProblem(value: string, deployed: ReadonlySet<string>): string | null {
  const locale = getLocale();
  const name = value.trim().toLowerCase();
  if (name === "") return translate(locale, "containerHost.wizard.nameEmpty");
  if (name.length > 253 || !NAME.test(name)) return translate(locale, "containerHost.wizard.nameBad");
  if (deployed.has(name)) return translate(locale, "containerHost.wizard.nameTaken", { name });
  return null;
}

/**
 * The port of the machine a Compose stack publishes, which is what its own file says (Noust does not rewrite the file): the one the
 * reading of the document found. Null where nothing was read.
 *
 * @param inspection What the document was read as.
 */
export function stackPort(inspection: { default_port: number } | null): string | null {
  return inspection === null ? null : String(inspection.default_port);
}

/**
 * Where the application will open, as the person writes it: the name the console was opened by (which reaches the machine from the
 * person's own network, and the one thing known before the application exists) and the port. After it exists the server says its
 * address (`GET /api/apps/{domain}/address`) and that is what the page of the application shows.
 *
 * @param host The host the console was reached at.
 * @param port What was typed.
 */
export function machineUrl(host: string, port: string): string {
  return `http://${host}:${port.trim()}`;
}
