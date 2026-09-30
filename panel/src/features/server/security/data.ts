/**
 * What the Security tab reads beyond the API's shapes: the view in the URL, each check's name
 * in the active language (the API's ids are a contract the console translates by), and a
 * check's state in the console's state language.
 */

import type { Status } from "../../../components/ui/StatusPill";
import type { PlainKey, T } from "../../../i18n";
import type { SecurityCheck } from "../queries";

export const SECURITY_VIEWS = ["checks", "ssh", "firewall", "bans"] as const;
export type SecurityView = (typeof SECURITY_VIEWS)[number];

export interface SecuritySearch {
  view?: Exclude<SecurityView, "checks">;
}

/** Reads `?view=`, dropping anything that is not a view (the default, checks, is no parameter). */
export function validateSecuritySearch(search: Record<string, unknown>): SecuritySearch {
  const view = search["view"];
  return typeof view === "string" && view !== "checks" && (SECURITY_VIEWS as readonly string[]).includes(view)
    ? { view: view as Exclude<SecurityView, "checks"> }
    : {};
}

/** Each check's name by its stable id (noust.managers.server.security_catalog). */
const TITLES: Readonly<Record<string, PlainKey>> = {
  "ssh.root_password": "server.checks.titles.sshRootPassword",
  "ssh.password_auth": "server.checks.titles.sshPasswordAuth",
  "ssh.root_login": "server.checks.titles.sshRootLogin",
  "ssh.empty_passwords": "server.checks.titles.sshEmptyPasswords",
  "ssh.keys": "server.checks.titles.sshKeys",
  "ssh.defaults": "server.checks.titles.sshDefaults",
  "ssh.loglevel": "server.checks.titles.sshLoglevel",
  "f2b.missing": "server.checks.titles.f2bMissing",
  "f2b.no_sshd_jail": "server.checks.titles.f2bNoSshdJail",
  "fw.inactive": "server.checks.titles.fwInactive",
  "fw.public_listener": "server.checks.titles.fwPublicListener",
  "fw.docker_bypass": "server.checks.titles.fwDockerBypass",
  "fw.ipv6_mismatch": "server.checks.titles.fwIpv6Mismatch",
  "fw.console_public": "server.checks.titles.fwConsolePublic",
  "upd.security_pending": "server.checks.titles.updSecurityPending",
  "upd.reboot_required": "server.checks.titles.updRebootRequired",
  "upd.stale_services": "server.checks.titles.updStaleServices",
  "upd.auto_disabled": "server.checks.titles.updAutoDisabled",
  "upd.pkg_broken": "server.checks.titles.updPkgBroken",
  "upd.lists_stale": "server.checks.titles.updListsStale",
  "os.eol": "server.checks.titles.osEol",
  "time.unsynced": "server.checks.titles.timeUnsynced",
  "mem.no_swap": "server.checks.titles.memNoSwap",
  "disk.full": "server.checks.titles.diskFull",
  "sys.degraded": "server.checks.titles.sysDegraded",
  "sys.uid0": "server.checks.titles.sysUid0",
  "sys.selinux": "server.checks.titles.sysSelinux",
  "sys.journal_volatile": "server.checks.titles.sysJournalVolatile",
  "noust.web_not_unit": "server.checks.titles.noustWebNotUnit",
  "noust.fleet_key_root": "server.checks.titles.noustFleetKeyRoot",
};

/** A check's name in the active language; one this console does not know keeps the API's words. */
export function checkTitle(t: T, check: Pick<SecurityCheck, "id" | "title">): string {
  const key = TITLES[check.id];
  return key === undefined ? check.title : t(key);
}

export interface CheckStateView {
  state: Status;
  label: string;
}

/** The severity word a finding is labelled with. */
export function severityLabel(t: T, severity: string): string {
  switch (severity) {
    case "critical":
      return t("server.checks.severity.critical");
    case "warning":
      return t("server.checks.severity.warning");
    case "low":
      return t("server.checks.severity.low");
    default:
      return t("server.checks.severity.info");
  }
}

/**
 * A check's result as a state: a finding that fails is red, one that warns amber, both named
 * by their severity; passed is green; what could not be judged, did not apply or was accepted
 * is grey, each with its own word.
 */
export function checkState(t: T, check: Pick<SecurityCheck, "status" | "severity">): CheckStateView {
  switch (check.status) {
    case "fail":
      return { state: check.severity === "critical" || check.severity === "warning" ? "failed" : "warning", label: severityLabel(t, check.severity) };
    case "warn":
      return { state: "warning", label: severityLabel(t, check.severity) };
    case "pass":
      return { state: "running", label: t("server.checks.status.passed") };
    case "accepted":
      return { state: "stopped", label: t("server.checks.status.accepted") };
    case "n/a":
      return { state: "stopped", label: t("server.checks.status.notApplicable") };
    default:
      return { state: "unknown", label: t("server.checks.status.unknown") };
  }
}

/** Open findings first (fail, then warn), then what could not be judged, then the rest. */
const STATUS_ORDER: Readonly<Record<string, number>> = { fail: 0, warn: 1, unknown: 2, accepted: 3, pass: 4, "n/a": 5 };
const SEVERITY_ORDER: Readonly<Record<string, number>> = { critical: 0, warning: 1, low: 2, info: 3 };

export function sortChecks(checks: readonly SecurityCheck[]): SecurityCheck[] {
  return [...checks].sort(
    (a, b) =>
      (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) ||
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
      a.id.localeCompare(b.id),
  );
}

/** An open finding: something to fix, accept, or look into. */
export function isOpen(check: Pick<SecurityCheck, "status">): boolean {
  return check.status === "fail" || check.status === "warn" || check.status === "unknown";
}

/** The sshd fix an automatic check fix applies (`ssh:disable-passwords`), or null. */
export function sshFixOf(action: string | null | undefined): string | null {
  return action?.startsWith("ssh:") === true ? action.slice(4) : null;
}

/** Automatic fixes that change how the server is reached ask for the host name, not a click. */
export function typedFriction(action: string | null | undefined): boolean {
  return action === "ssh:disable-passwords" || action === "ssh:root-no" || action === "firewall:enable";
}
