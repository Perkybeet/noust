import type { Job } from "../../api/queries/jobs";
import type { Locale } from "../../app/locale";
import { appStatus } from "../../components/page/status";
import type { StatusView } from "../../components/page/status";
import { jobWords } from "./useAppJob";

/**
 * The state an application is drawn in, by whichever part of the page draws it: a job running on it is its state while it
 * runs, whatever systemd says about the unit meanwhile (a first deploy has no unit yet, and an update leaves the old one
 * running), and otherwise what its status says. The header and the column beside it ask this one question, so they
 * cannot say "Stopped" and "Deploying" of the same application at once.
 */
export function appViewOf(status: string | null | undefined, running: Job | null, domain: string, locale: Locale): StatusView {
  return running !== null ? { state: "deploying", label: jobWords(running.type, domain, locale).running, attention: false } : appStatus(status, locale);
}
