/**
 * The form that makes a web server site, apart from any dialog: what it holds, and the request it becomes. A site is made
 * from the one dialog that adds a name (a name for a site of its own), so the form lives here and not in the dialog.
 */

import type { BodyOf } from "../../api/client";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale } from "../../i18n";
import { domainProblem, normalizeDomain } from "./names";

export type WebServer = "nginx" | "apache";
export type CreateSiteBody = BodyOf<"/api/sites", "post">;

/** Every template but the static one names a port the request is proxied to. */
export function templateNeedsPort(name: string): boolean {
  return name !== "static";
}

export interface CreateSiteForm {
  domain: string;
  webserver: WebServer;
  template: string;
  port: string;
  ssl: boolean;
  enable: boolean;
}

export type CreateSiteErrors = Partial<Record<"domain" | "port", string>>;

/** The form as the API takes it, or what is wrong with it. */
export function createSiteBody(form: CreateSiteForm, locale: Locale = getLocale()): { body: CreateSiteBody } | { errors: CreateSiteErrors } {
  const errors: CreateSiteErrors = {};
  const domain = normalizeDomain(form.domain);
  const problem = domainProblem(domain, locale);
  if (problem !== null) errors.domain = problem;
  const port = Number(form.port);
  if (templateNeedsPort(form.template) && (!/^\d+$/.test(form.port.trim()) || port < 1 || port > 65535)) {
    errors.port = translate(locale, "domains.createSiteDialog.portRequiredError");
  }
  if (Object.keys(errors).length > 0) return { errors };
  return {
    body: {
      domain,
      webserver: form.webserver,
      template: form.template,
      port: templateNeedsPort(form.template) ? port : 3000,
      ssl: form.ssl,
      enable: form.enable,
    },
  };
}

/** How making a site went: well, without the HTTPS that was asked for, or into a file the web server would not load. */
export type SiteOutcome = "made" | "withoutHttps" | "notLoaded";

/**
 * How making a site went, said by the code the server sent and never by its English: `sites.created.without_tls` and
 * `sites.created.without_tls_because` are the site that came without HTTPS, and `sites.created.not_loaded` the one the
 * web server would not load (which the console showed as a success). A code this console does not know is a success: the
 * server's own sentence goes beside it.
 */
export function siteOutcome(coded: { code: string } | null | undefined): SiteOutcome {
  if (coded === null || coded === undefined) return "made";
  if (coded.code === "sites.created.without_tls" || coded.code === "sites.created.without_tls_because") return "withoutHttps";
  return coded.code === "sites.created.not_loaded" ? "notLoaded" : "made";
}
