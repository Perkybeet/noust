/**
 * Backend metadata the API's `/backends` catalogue does not carry: a human label, a short
 * description, and which backends authenticate through a token pasted from `rclone
 * authorize` instead of a form field. Mirrors `BACKEND_FIELDS` in
 * `noust.managers.backup_destinations`; a backend not listed here (the server added one this
 * build does not know about yet) still renders, under its own raw name.
 */

import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";

/**
 * Backends that authenticate through a token pasted from `rclone authorize "<backend>"`, run
 * by the operator on their own machine - never on the server, which never sees the sign-in.
 */
export const OAUTH_BACKENDS: ReadonlySet<string> = new Set(["drive", "onedrive", "dropbox", "pcloud"]);

export function backendLabel(backend: string, locale: Locale = getLocale()): string {
  switch (backend) {
    case "sftp":
      return translate(locale, "backups.backends.sftp.label");
    case "smb":
      return translate(locale, "backups.backends.smb.label");
    case "webdav":
      return translate(locale, "backups.backends.webdav.label");
    case "s3":
      return translate(locale, "backups.backends.s3.label");
    case "b2":
      return translate(locale, "backups.backends.b2.label");
    case "drive":
      return translate(locale, "backups.backends.drive.label");
    case "onedrive":
      return translate(locale, "backups.backends.onedrive.label");
    case "dropbox":
      return translate(locale, "backups.backends.dropbox.label");
    case "pcloud":
      return translate(locale, "backups.backends.pcloud.label");
    case "local":
      return translate(locale, "backups.backends.local.label");
    default:
      return backend;
  }
}

export function backendDescription(backend: string, locale: Locale = getLocale()): string | undefined {
  switch (backend) {
    case "sftp":
      return translate(locale, "backups.backends.sftp.description");
    case "smb":
      return translate(locale, "backups.backends.smb.description");
    case "webdav":
      return translate(locale, "backups.backends.webdav.description");
    case "s3":
      return translate(locale, "backups.backends.s3.description");
    case "b2":
      return translate(locale, "backups.backends.b2.description");
    case "drive":
      return translate(locale, "backups.backends.drive.description");
    case "onedrive":
      return translate(locale, "backups.backends.onedrive.description");
    case "dropbox":
      return translate(locale, "backups.backends.dropbox.description");
    case "pcloud":
      return translate(locale, "backups.backends.pcloud.description");
    case "local":
      return translate(locale, "backups.backends.local.description");
    default:
      return undefined;
  }
}
