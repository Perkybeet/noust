/**
 * Importing an application: reading an export file in the browser, the form (the domain, the
 * source, the secret values the export left out), what is wrong with it, and the
 * `POST /api/apps/import` it becomes. Pure, like wizard.ts.
 *
 * The server checks the document for real (`validate_document`); this reads just enough to
 * say clearly, before anything is sent, that a file is not an export or is too new.
 */

import { isApiError } from "../../api/client";
import type { AppExportDocument, ImportAppBody } from "../../api/queries/appImport";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { normalizeDomain } from "../domains/names";
import { domainError, sourceProblems } from "./wizard";

/** What `noust app export` writes in `format`. */
export const EXPORT_FORMAT = "wasm-app";
/** The newest document version this console reads (`VERSION` in noust.deployers.app_export). */
export const EXPORT_VERSION = 1;
/** An export is a few kilobytes; anything this large is some other file. */
export const EXPORT_MAX_BYTES = 2 * 1024 * 1024;

/** What the server writes in place of the credentials it took out of a source URL. */
const REDACTED = "***@";

export type ExportRead = { document: AppExportDocument } | { problem: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** An export file's text as a document, or why it is not one, in words the operator can act on. */
export function readExport(text: string): ExportRead {
  const locale = getLocale();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { problem: translate(locale, "newApp.importApp.notJson") };
  }
  if (!isObject(data) || data["format"] !== EXPORT_FORMAT) return { problem: translate(locale, "newApp.importApp.notExport") };
  const version = data["version"];
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) return { problem: translate(locale, "newApp.importApp.badVersion") };
  if (version > EXPORT_VERSION) {
    return { problem: translate(locale, "newApp.importApp.newerVersion", { version, supported: EXPORT_VERSION }) };
  }
  const app = data["app"];
  if (!isObject(app) || typeof app["domain"] !== "string" || typeof app["app_type"] !== "string") {
    return { problem: translate(locale, "newApp.importApp.noApp") };
  }
  return { document: data as unknown as AppExportDocument };
}

/** Why a file is refused before it is even read: its size. */
export function exportFileProblem(file: Pick<File, "size">): string | null {
  if (file.size <= EXPORT_MAX_BYTES) return null;
  return translate(getLocale(), "newApp.importApp.tooLarge", { size: formatBytes(file.size) });
}

/** Whether a source had its credentials taken out when it was exported. */
export function sourceStripped(source: string): boolean {
  return source.includes(REDACTED);
}

/** The variables the export left without a value (secrets exported without --with-secrets), sorted. */
export function missingValues(document: AppExportDocument): string[] {
  return Object.entries(document.env ?? {})
    .filter(([, entry]) => entry.value === null || entry.value === undefined)
    .map(([name]) => name)
    .sort();
}

export interface ImportForm {
  domain: string;
  source: string;
  /** A value for every variable the export left out. */
  secrets: Record<string, string>;
}

/** The form an export starts from: its own domain and source, and an empty value per missing secret. */
export function initialImportForm(document: AppExportDocument): ImportForm {
  const secrets: Record<string, string> = {};
  for (const name of missingValues(document)) secrets[name] = "";
  return { domain: document.app.domain, source: document.app.source ?? "", secrets };
}

/** Field name of a secret's value in the import's errors. */
export function secretField(name: string): string {
  return `secret:${name}`;
}

export function importProblems(form: ImportForm, deployed: ReadonlySet<string>): Record<string, string> {
  const locale = getLocale();
  const errors: Record<string, string> = {};
  const domain = domainError(form.domain, deployed);
  if (domain !== null) errors["domain"] = domain;
  const source = form.source.trim();
  if (source === "") errors["source"] = translate(locale, "newApp.validation.importSourceEmpty");
  else if (sourceStripped(source)) errors["source"] = translate(locale, "newApp.validation.importSourceStripped");
  else {
    const problem = sourceProblems({ source, branch: "" }).source;
    if (problem !== undefined) errors["source"] = problem;
  }
  for (const [name, value] of Object.entries(form.secrets)) {
    if (value === "") errors[secretField(name)] = translate(locale, "newApp.validation.secretEmpty");
  }
  return errors;
}

/**
 * The request `POST /api/apps/import` takes: the document as it was read, the domain and the
 * source only when they differ from the export's, and the values of the secrets it left out.
 */
export function importBody(document: AppExportDocument, form: ImportForm): ImportAppBody {
  const domain = normalizeDomain(form.domain);
  const source = form.source.trim();
  return {
    document,
    ...(domain !== normalizeDomain(document.app.domain) ? { domain } : {}),
    ...(source !== (document.app.source ?? "").trim() ? { source } : {}),
    env: { ...form.secrets },
  };
}


/**
 * Where a refusal of the import sends the operator: a taken or refused domain and a refused
 * source to their fields. Anything else (a document the server will not read, a value it says
 * is missing) stays on the Deploy step, verbatim.
 */
export function importRefusalOf(error: unknown): Record<string, string> | null {
  if (!isApiError(error)) return null;
  const fields = error.fields ?? {};
  if (error.status === 409 || error.error === "domainerror" || error.error === "domainconflicterror") return { domain: error.detail };
  if (fields["domain"] !== undefined) return { domain: fields["domain"] };
  if (fields["source"] !== undefined) return { source: fields["source"] };
  if (error.error === "sourceerror") return { source: error.detail };
  return null;
}
