import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { api } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
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
    { id: "description", header: t("apiReference.drawer.bodyColumn.description"), hideBelow: "md", cell: (field) => <span className="text-13 text-fg-muted">{field.description}</span> },
  ];
}

function responseColumns(t: T): Column<ApiResponseInfo>[] {
  return [
    { id: "status", header: t("apiReference.drawer.responseColumn.status"), card: "title", width: "w-24", cell: (response) => <Mono tone="default">{response.status}</Mono> },
    { id: "description", header: t("apiReference.drawer.responseColumn.description"), cell: (response) => <span className="text-13 text-fg-muted">{response.description}</span> },
  ];
}

/**
 * Trying a read: the parameters it takes, a button, and the server's answer as it came. Only for
 * a read, which changes nothing; it uses the session the person is already signed in with, so it
 * can see no more than the console can.
 */
function TryIt({ operation }: { operation: ApiOperation }) {
  const t = useT();
  const [values, setValues] = useState<Record<string, string>>({});
  const missing = operation.parameters.some((parameter) => parameter.required && (values[parameter.name] ?? "") === "");
  const send = useMutation({ mutationFn: () => api<unknown>("GET", addressOf(operation, values)) });
  const shown = send.data === undefined ? "" : JSON.stringify(send.data, null, 2);

  return (
    <Subsection title={t("apiReference.drawer.tryIt")} description={t("apiReference.drawer.tryItHelp")}>
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!send.isPending && !missing) send.mutate();
        }}
      >
        {operation.parameters.map((parameter) => (
          <Field key={`${parameter.where}:${parameter.name}`} label={parameter.name} optional={!parameter.required} description={parameter.description === "" ? parameter.type : `${parameter.type}. ${parameter.description}`}>
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
        <div>
          <Button type="submit" variant="secondary" loading={send.isPending} disabled={missing}>
            {t("apiReference.drawer.send")}
          </Button>
        </div>
      </form>
      {send.isError ? <ErrorBlock live compact error={send.error} title={t("apiReference.drawer.response")} /> : null}
      {send.data !== undefined ? (
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("apiReference.drawer.status", { status: "200" })}</p>
          <SystemOutput label={t("apiReference.drawer.response")} maxHeight="max-h-80">
            {shown.length > MAX_SHOWN ? shown.slice(0, MAX_SHOWN) : shown}
          </SystemOutput>
          {shown.length > MAX_SHOWN ? <p className="text-13 text-fg-muted">{t("apiReference.drawer.truncated")}</p> : null}
        </div>
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
 * answers, a curl to copy and, for a read, a way to try it. Everything is the server's own
 * description; nothing is added.
 */
export function OperationDrawer({ operation, onClose }: OperationDrawerProps) {
  const t = useT();
  const route = `${operation.method} ${operation.path}`;
  const curl = curlOf(operation, window.location.origin, {});
  const isRead = operation.method === "GET";

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      title={<Mono tone="default">{route}</Mono>}
      description={operation.summary === "" ? t("apiReference.drawer.description") : operation.summary}
    >
      <div className="flex flex-col gap-6">
        {operation.description !== "" ? <p className="max-w-measure text-13 whitespace-pre-line text-fg-muted">{operation.description}</p> : null}
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
        {isRead ? <TryIt operation={operation} /> : <Notice>{t("apiReference.drawer.tryItUnavailable")}</Notice>}
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
      {operation.deprecated ? <Badge tone="warn">{t("apiReference.table.deprecated")}</Badge> : null}
    </span>
  );
}
