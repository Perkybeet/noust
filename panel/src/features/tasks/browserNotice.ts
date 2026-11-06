/**
 * The browser's own notification when a task of the person's ends and the tab is hidden (design 11.1.3). It is
 * never started by itself: it is offered once, the first time the person leaves a long task of theirs, and
 * only turns on when they say so, which is the gesture the browser asks its permission for. Until then, and
 * for a person who refused, no notification is ever made.
 */

const ENABLED_KEY = "noust.tasks.notify";
const OFFERED_KEY = "noust.tasks.notify.offered";

/** How long a task of the person's has run before leaving it is "leaving a long task". */
export const LONG_TASK_MS = 10_000;

export type Permission = NotificationPermission | "unsupported";

/** The part of the browser the module uses, so a test gives its own. */
export interface Browser {
  storage: Pick<Storage, "getItem" | "setItem">;
  permission: () => Permission;
  ask: () => Promise<Permission>;
  show: (title: string, body: string, onClick: () => void) => void;
  hidden: () => boolean;
}

function realBrowser(): Browser {
  return {
    storage: window.localStorage,
    permission: () => (typeof Notification === "undefined" ? "unsupported" : Notification.permission),
    ask: async () => (typeof Notification === "undefined" ? "unsupported" : await Notification.requestPermission()),
    show: (title, body, onClick) => {
      const shown = new Notification(title, { body });
      shown.onclick = () => {
        window.focus();
        onClick();
        shown.close();
      };
    },
    hidden: () => document.visibilityState === "hidden",
  };
}

function read(storage: Pick<Storage, "getItem">, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    // Storage the browser refuses to read: the person has said nothing we can find.
    return null;
  }
}

function write(storage: Pick<Storage, "setItem">, key: string, value: string): void {
  try {
    storage.setItem(key, value);
  } catch {
    // Not kept: offered again next time, which is better than a notification nobody turned on.
  }
}

/** Whether the person has turned the notification on here and the browser still lets it. */
export function isOn(browser: Browser = realBrowser()): boolean {
  return read(browser.storage, ENABLED_KEY) === "on" && browser.permission() === "granted";
}

/** Whether to offer it now: the browser could be asked and nobody has been offered it before. */
export function shouldOffer(browser: Browser = realBrowser()): boolean {
  return browser.permission() === "default" && read(browser.storage, OFFERED_KEY) === null;
}

/** Remembers that it was offered, whatever the answer: it is said once. */
export function markOffered(browser: Browser = realBrowser()): void {
  write(browser.storage, OFFERED_KEY, "yes");
}

/** What pressing "Notify me" does: asks the browser, and turns the notification on only when it says yes. Call it from the press itself. */
export async function turnOn(browser: Browser = realBrowser()): Promise<Permission> {
  const answer = await browser.ask();
  if (answer === "granted") write(browser.storage, ENABLED_KEY, "on");
  return answer;
}

/** Shows the notification when it is on and the tab is hidden; says whether it did. A visible tab has the toast. */
export function notifyWhenHidden(title: string, body: string, onClick: () => void, browser: Browser = realBrowser()): boolean {
  if (!isOn(browser) || !browser.hidden()) return false;
  browser.show(title, body, onClick);
  return true;
}
