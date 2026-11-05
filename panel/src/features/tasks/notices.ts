/**
 * What the task centre says out loud: that a task of the person's ended while they were elsewhere, and the
 * once-only offer of the browser's notification. Both are spoken outside a render, so the texts they need are
 * fetched first (a toast is said once: in English the first time in a Spanish console would never be said again).
 */

import { getLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { hasNamespace, loadNamespaces } from "../../i18n/catalogs";
import { translate } from "../../i18n";
import { markOffered, notifyWhenHidden, turnOn } from "./browserNotice";
import { placeOf } from "./model";
import type { Place, Task } from "./model";
import { titleOf } from "./words";

/** How the console moves to a place: what a toast's "See" and a notification's click do. */
export type Go = (place: Place) => void;

const NAMESPACES = ["tasks", "serverText"] as const;

/** Runs `say` once the texts it needs are here; the English the server sent is what is said when they do not come. */
function withTexts(say: () => void): void {
  const locale = getLocale();
  if (NAMESPACES.every((namespace) => hasNamespace("en", namespace) && hasNamespace(locale, namespace))) {
    say();
    return;
  }
  void Promise.all([loadNamespaces("en", NAMESPACES), loadNamespaces(locale, NAMESPACES)]).then(say, say);
}

/** The ending of a task as the person reads it: its title and how it ended, in the console's language. */
export function endingWords(task: Task, locale = getLocale()): string {
  const title = titleOf(task, locale);
  if (task.state === "succeeded") return translate(locale, "tasks.notice.succeeded", { title });
  if (task.state === "failed") return translate(locale, "tasks.notice.failed", { title });
  return translate(locale, "tasks.notice.cancelled", { title });
}

/**
 * Says that a task of the person's ended: a toast with a way to see it (and the failure's own words, verbatim) and,
 * when they turned it on and the tab is hidden, the browser's notification. A visible tab has the toast and a hidden
 * one the notification, never both.
 */
export function sayEnding(task: Task, go: Go): void {
  withTexts(() => {
    const locale = getLocale();
    const words = endingWords(task, locale);
    const see = (): void => {
      go(placeOf(task));
    };
    if (notifyWhenHidden(words, translate(locale, "tasks.notice.leftIt"), see)) return;
    const options = { description: translate(locale, "tasks.notice.leftIt"), action: { label: translate(locale, "tasks.notice.view"), onClick: see } };
    if (task.state === "failed") toast.error(words, { ...options, ...(task.error !== null ? { detail: task.error } : {}) });
    // design-exception: toast-success a task that ended while the person was on another page is not on any screen they see
    else if (task.state === "succeeded") toast.success(words, { ...options, timeout: 8000 });
    else toast.info(words, { ...options, timeout: 8000 });
  });
}

/**
 * Offers the browser's notification, once: the first time the person leaves a long task of theirs. It is a toast with a
 * button, because the browser only asks for its permission from a press; nothing is requested before it.
 */
export function offerNotifications(): void {
  markOffered();
  withTexts(() => {
    const locale = getLocale();
    toast.info(translate(locale, "tasks.notify.offerTitle"), {
      description: translate(locale, "tasks.notify.offerBody"),
      timeout: 15_000,
      action: {
        label: translate(locale, "tasks.notify.offerAction"),
        onClick: () => {
          void turnOn().then((answer) => {
            // design-exception: toast-success turning the notification on changes nothing on the page that says so
            if (answer === "granted") toast.success(translate(getLocale(), "tasks.notify.on"));
            else toast.warning(translate(getLocale(), "tasks.notify.refused"));
          });
        },
      },
    });
  });
}
