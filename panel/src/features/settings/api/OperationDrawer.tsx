import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { api, isApiError } from "../../../api/client";
import type { ApiError, Method } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { focusFirstInvalid } from "../formErrors";
import { addressOf, curlOf } from "./openapiModel";
import type { ApiField, ApiOperation, ApiParameter, ApiResponseInfo } from "./openapiModel";

/** The most of an answer shown in the page: a reading of the API, not a download of it. */
const MAX_SHOWN = 20_000;

function parameterColumns(t: T): Column<ApiParameter>[] {
  return [
    { id: "name", header: t("apiReference.drawer.parameterColumn.name"), card: "title", cell: (parameter) => <Mono tone="default">{parameter.name}</Mono> },
    { id: "where", header: t("apiReference.drawer.parameterColumn.where"), cell: (parameter) => (parameter.where === "path" ? t("apiReference.drawer.path") : t("apiReference.drawer.query")) },
    { id: "type", header: t("apiReference.drawer.parameterColumn.type"), cell: (parameter) => <Mono tone="faint">{parameter.type}</Mono> },
    { id: "required", header: t("apiReference.drawer.parameterColumn.required"), cell: (parameter) => (parameter.required ? t("apiReference.drawer.yes") : t("apiReference.drawer.no")) },
  ];
}

function fieldColumns(t: T): Column<ApiField>[] {
  return [
    { id: "field", header: t("apiReference.drawer.bodyColumn.field"), card: "title", cell: (field) => <Mono tone="default">{field.name}</Mono> },
    { id: "type", header: t("apiReference.drawer.bodyColumn.type"), cell: (field) => <Mono tone="faint">{field.type}</Mono> },
    { id: "required", header: t("apiReference.drawer.bodyColumn.required"), cell: (field) => (field.required ? t("apiReference.drawer.yes") : t("apiReference.drawer.no")) },
    { id: "description", header: t("apiReference.drawer.bodyColumn.description"), hideBelow: "md", cell: (field) => (
        <span translate="no" className="text-13 text-fg-muted">
          {field.description}
        </span>
      ) },
  ];
}

function responseColumns(t: T): Column<ApiResponseInfo>[] {
  return [
    { id: "status", header: t("apiReference.drawer.responseColumn.status"), card: "title", width: "w-24", cell: (response) => <Mono tone="default">{response.status}</Mono> },
    { id: "description", header: t("apiReference.drawer.responseColumn.description"), cell: (response) => (
        <span translate="no" className="text-13 text-fg-muted">
          {response.description}
        </span>
      ) },
  ];
}

/** A response header per line, the way a terminal prints them. */
function headersText(headers: Headers): string {
  const lines: string[] = [];
  headers.forEach((value, name) => {
    lines.push(`${name}: ${value}`);
  });
  return lines.join("\n");
}

/** What came of a try: the server's body, or its refusal (an ApiError carries its words verbatim). */
type Outcome = { kind: "answered"; data: unknown } | { kind: "refused"; error: ApiError };

/** The body the form would send: its JSON, or nothing when the route takes none or the text is not JSON. */
function parseBody(operation: ApiOperation, text: string): { valid: boolean; value: unknown } {
  if (operation.body === null) return { valid: true, value: undefined };
  try {
    return { valid: true, value: JSON.parse(text) as unknown };
  } catch {
    return { valid: false, value: undefined };
  }
}

/**
 * Trying a call: the parameters it takes, a body to edit when it takes one, a button and the
 * server's answer as it came, with its status and headers. It uses the session the person is
 * already signed in with, so it can do no more than the console can: the role, sudo mode
 * ("Confirm it's you") and a second person's approval apply here exactly as on any page. A read
 * is sent at once; a change asks first, because this is the real server and not a rehearsal.
 */
function TryIt({ operation }: { operation: ApiOperation }) {
  const t = useT();
  const { node } = useNode();
  const changes = operation.method !== "GET";
  const [values, setValues] = useState<Record<string, string>>({});
  const [bodyText, setBodyText] = useState(() => (operation.body === null ? "" : JSON.stringify(operation.example, null, 2)));
  const [confirming, setConfirming] = useState(false);
  const [answer, setAnswer] = useState<{ status: number; headers: string } | null>(null);
  const [tried, setTried] = useState(false);
  const formId = useId();
  const missing = operation.parameters.some((parameter) => parameter.required && (values[parameter.name] ?? "") === "");
  const body = parseBody(operation, bodyText);
  const ready = !missing && body.valid;
  const send = useMutation({
    onMutate: () => {
      setAnswer(null);
    },
    mutationFn: async (): Promise<Outcome> => {
      try {
        const data = await api<unknown>(operation.method as Method, addressOf(operation, values), body.value, {
          onResponse: (seen) => {
            setAnswer({ status: seen.status, headers: headersText(seen.headers) });
          },
        });
        return { kind: "answered", data };
      } catch (error: unknown) {
        // The server's refusal is an answer like any other: it is shown with its status. Anything
        // else (a confirmation declined, a server that cannot be reached) is the page's to report.
        if (isApiError(error)) return { kind: "refused", error };
        throw error;
      }
    },
  });
  const outcome = send.data;
  const shown = outcome?.kind === "answered" ? (outcome.data === undefined ? "" : JSON.stringify(outcome.data, null, 2)) : "";

  return (
    <Subsection title={t("apiReference.drawer.tryIt")} description={changes ? t("apiReference.drawer.tryItChangeHelp") : t("apiReference.drawer.tryItHelp")}>
      {operation.rawBody ? (
        <p className="text-13 text-fg-muted">{t("apiReference.drawer.tryItRawBody")}</p>
      ) : (
        <form
          id={formId}
          noValidate
          className="flex flex-col gap-4"
          onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
            event.preventDefault();
            if (send.isPending) return;
            // The button is never off for a missing value: it says what is missing, where, and goes there.
            if (!ready) {
              setTried(true);
              focusFirstInvalid(formId);
              return;
            }
            if (changes) setConfirming(true);
            else send.mutate();
          }}
        >
          {operation.parameters.map((parameter) => (
            <Field
              key={`${parameter.where}:${parameter.name}`}
              label={parameter.name}
              optional={!parameter.required}
              description={parameter.description === "" ? parameter.type : `${parameter.type}. ${parameter.description}`}
              error={tried && parameter.required && (values[parameter.name] ?? "") === "" ? t("settings.shared.required") : null}
            >
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={values[parameter.name] ?? ""}
                onValueChange={(value: string) => {
                  setValues((previous) => ({ ...previous, [parameter.name]: value }));
                }}
              />
            </Field>
          ))}
          {operation.body !== null ? (
            <Field label={t("apiReference.drawer.requestBody")} description={t("apiReference.drawer.requestBodyHelp")} error={body.valid ? null : t("apiReference.drawer.bodyInvalid")}>
              <Textarea
                mono
                rows={Math.min(14, Math.max(4, bodyText.split("\n").length))}
                value={bodyText}
                onChange={(event) => {
                  setBodyText(event.target.value);
                }}
              />
            </Field>
          ) : null}
          <div>
            <Button type="submit" variant={changes ? "primary" : "secondary"} loading={send.isPending}>
              {t("apiReference.drawer.send")}
            </Button>
          </div>
        </form>
      )}
      {confirming ? (
        <ConfirmDialog
          open
          friction="simple"
          destructive={operation.method === "DELETE"}
          server={node}
          title={t("apiReference.drawer.confirmTitle", { route: `${operation.method} ${operation.path}` })}
          description={t("apiReference.drawer.confirmDescription")}
          actionLabel={t("apiReference.drawer.confirmAction", { method: operation.method })}
          onOpenChange={(open) => {
            if (!open) setConfirming(false);
          }}
          onConfirm={async () => {
            await send.mutateAsync();
          }}
        />
      ) : null}
      {send.isError ? <ErrorBlock live compact error={send.error} title={t("apiReference.drawer.response")} /> : null}
      {answer !== null ? <p className="text-13 font-medium text-fg">{t("apiReference.drawer.status", { status: String(answer.status) })}</p> : null}
      {outcome?.kind === "refused" ? <ErrorBlock live compact error={outcome.error} title={t("apiReference.drawer.response")} /> : null}
      {outcome?.kind === "answered" ? (
        <div className="flex flex-col gap-2">
          {shown === "" ? (
            <p className="text-13 text-fg-muted">{t("apiReference.drawer.noContent")}</p>
          ) : (
            <SystemOutput label={t("apiReference.drawer.response")} maxHeight="max-h-80">
              {shown.length > MAX_SHOWN ? shown.slice(0, MAX_SHOWN) : shown}
            </SystemOutput>
          )}
          {shown.length > MAX_SHOWN ? <p className="text-13 text-fg-muted">{t("apiReference.drawer.truncated")}</p> : null}
        </div>
      ) : null}
      {answer !== null && answer.headers !== "" ? (
        <details className="text-13">
          <summary className="cursor-pointer text-fg-muted">{t("apiReference.drawer.responseHeaders")}</summary>
          <SystemOutput label={t("apiReference.drawer.responseHeaders")} maxHeight="max-h-48">
            {answer.headers}
          </SystemOutput>
        </details>
      ) : null}
    </Subsection>
  );
}

export interface OperationDrawerProps {
  operation: ApiOperation;
  onClose: () => void;
}

/**
 * One route in full: what it needs (permission, sudo mode, approval), what it takes, what it
 * answers, a curl to copy and a way to try it with the session (a change asks first). Everything
 * is the server's own description; nothing is added.
 */
export function OperationDrawer({ operation, onClose }: OperationDrawerProps) {
  const t = useT();
  const route = `${operation.method} ${operation.path}`;
  const curl = curlOf(operation, window.location.origin, {});

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      title={<Mono tone="default">{route}</Mono>}
      description={operation.summary === "" ? t("apiReference.drawer.description") : <span translate="no">{operation.summary}</span>}
    >
      <div className="flex flex-col gap-6">
        {operation.description !== "" ? (
          <p translate="no" className="max-w-measure text-13 whitespace-pre-line text-fg-muted">
            {operation.description}
          </p>
        ) : null}
        <Subsection title={t("apiReference.drawer.needs")}>
          <ul className="flex flex-col gap-2 text-13 text-fg">
            <li className="flex flex-wrap items-center gap-2">
              <span className="text-fg-muted">{t("apiReference.drawer.permission")}</span>
              {operation.permission === null ? <span>{t("apiReference.drawer.noPermission")}</span> : <Mono tone="default">{operation.permission}</Mono>}
            </li>
            {operation.needsSudoMode ? <li>{t("apiReference.drawer.sudoMode")}</li> : null}
            {operation.needsApproval ? <li>{t("apiReference.drawer.approval")}</li> : null}
          </ul>
          {operation.deprecated ? <Notice tone="warning">{t("apiReference.drawer.deprecated")}</Notice> : null}
        </Subsection>
        {operation.parameters.length > 0 ? (
          <Subsection title={t("apiReference.drawer.parameters")}>
            <DataTable caption={t("apiReference.drawer.parameters")} columns={parameterColumns(t)} rows={operation.parameters} getRowId={(parameter) => `${parameter.where}:${parameter.name}`} mobile="scroll" density="compact" />
          </Subsection>
        ) : null}
        {operation.body !== null ? (
          <Subsection title={t("apiReference.drawer.body")} description={t("apiReference.drawer.bodyHelp")}>
            {operation.body.length > 0 ? <DataTable caption={t("apiReference.drawer.body")} columns={fieldColumns(t)} rows={operation.body} getRowId={(field) => field.name} mobile="scroll" density="compact" /> : null}
            <SystemOutput label={t("apiReference.drawer.body")} maxHeight="max-h-64">
              {JSON.stringify(operation.example, null, 2)}
            </SystemOutput>
          </Subsection>
        ) : null}
        <Subsection title={t("apiReference.drawer.responses")}>
          <DataTable caption={t("apiReference.drawer.responses")} columns={responseColumns(t)} rows={operation.responses} getRowId={(response) => response.status} mobile="scroll" density="compact" />
        </Subsection>
        <Subsection title={t("apiReference.drawer.curl")} description={t("apiReference.drawer.curlHelp")}>
          <CopyValue value={curl} label={t("apiReference.drawer.copyCurl")} name={t("apiReference.drawer.curl")} />
        </Subsection>
        <TryIt operation={operation} />
      </div>
    </Drawer>
  );
}

/** The short flags of a route in a row: what it needs beyond its permission. */
export function OperationFlags({ operation }: { operation: ApiOperation }) {
  const t = useT();
  return (
    <span className="flex flex-wrap gap-1.5">
      {operation.needsSudoMode ? <Badge>{t("apiReference.table.sudoMode")}</Badge> : null}
      {operation.needsApproval ? <Badge>{t("apiReference.table.approval")}</Badge> : null}
      {operation.deprecated ? <StatusPill state="warning" label={t("apiReference.table.deprecated")} size="sm" /> : null}
    </span>
  );
}
