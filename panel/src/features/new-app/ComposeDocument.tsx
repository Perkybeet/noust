/**
 * A Docker Compose document typed or pasted into the console (`POST /api/compose/analyze`, then
 * `POST /api/apps` with `compose_document`): the first press looks at it and shows what it is,
 * the second goes on to the address with the stack's variables asked. Looking runs nothing and
 * writes nothing; what refuses a document (root on the host, a build context it does not have,
 * an env_file it does not have) is shown with the server's own words, and the deployment checks
 * it again - the preview is a convenience, the guard is the server's.
 */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Textarea } from "../../components/ui/Textarea";
import { useT } from "../../i18n";
import type { ComposeAnalysis } from "./wizard";

/** The id of the document's control, which Continue moves focus to when it is empty. */
export const COMPOSE_DOCUMENT_FIELD = "compose-document";

export interface ComposeDocumentState {
  /** The document as typed. */
  document: string;
  /** What the server said of this very document, or null before it was looked at or after an edit. */
  analysis: ComposeAnalysis | null;
  /** The server is looking at it. */
  looking: boolean;
  /** Looks at the document. */
  look: () => void;
  /** The values made when a Coolify template was converted, by variable name; secrets among them. */
  made: Readonly<Record<string, string>>;
  /** The step's content, in the source step's place. */
  content: ReactNode;
}

/** The services of a document: what each runs and what it publishes. */
function Services({ analysis }: { analysis: ComposeAnalysis }) {
  const t = useT();
  return (
    <ul aria-label={t("newApp.compose.services")} className="flex flex-col divide-y divide-border rounded-control border border-border">
      {analysis.services.map((service) => (
        <li key={service.name} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
          <Mono>{service.name}</Mono>
          <span className="min-w-0 text-12 text-fg-muted">{service.image ?? t("newApp.compose.builds")}</span>
          {service.publishes.map((port) => (
            <Badge key={port} mono>
              {port}
            </Badge>
          ))}
          {service.depends_on.length > 0 ? <span className="text-12 text-fg-faint">{t("newApp.compose.dependsOn", { services: service.depends_on.join(", ") })}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** What converting a Coolify template changed and left, in the server's own words. */
function Converted({ adjustments, warnings, count }: { adjustments: readonly string[]; warnings: readonly string[]; count: number }) {
  const t = useT();
  return (
    // What the conversion did is the server's report, not a condition to fix: a card, not a notice.
    <Card title={t("newApp.compose.coolify.title")} padding="sm">
      <div className="flex flex-col gap-2 text-13 text-pretty text-fg">
        <p>{t("newApp.compose.coolify.body", { count })}</p>
        {adjustments.length > 0 ? (
          <ul aria-label={t("newApp.compose.coolify.changed")} className="flex list-disc flex-col gap-1 pl-5">
            {adjustments.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
        {warnings.length > 0 ? (
          <ul aria-label={t("newApp.compose.coolify.left")} className="flex list-disc flex-col gap-1 pl-5">
            {warnings.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

/** What the server found in a document: services, variables and what refuses or warns about it. */
function Found({ analysis }: { analysis: ComposeAnalysis }) {
  const t = useT();
  const required = analysis.variables.filter((variable) => variable.required);
  return (
    <Card title={t("newApp.compose.foundTitle")} description={t("newApp.compose.foundDescription")}>
      <div className="flex flex-col gap-4">
        {analysis.refused.length > 0 ? (
          <Notice tone="error" title={t("newApp.compose.refusedTitle")} live>
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {analysis.refused.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
        {analysis.warned.length > 0 ? (
          <Notice tone="warning" title={t("newApp.compose.warnedTitle")}>
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {analysis.warned.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
        <Services analysis={analysis} />
        {analysis.web.length > 0 ? <p className="text-13 text-pretty text-fg-muted">{t("newApp.compose.web", { services: analysis.web.join(", ") })}</p> : null}
        {analysis.variables.length > 0 ? (
          <p className="text-13 text-pretty text-fg-muted">
            {required.length > 0
              ? t("newApp.compose.variablesRequired", { count: analysis.variables.length, required: required.map((variable) => variable.name).join(", ") })
              : t("newApp.compose.variables", { count: analysis.variables.length })}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

/** The Compose document's field and what the server found in it, for the new-app wizard's source step. */
export function useComposeDocument(): ComposeDocumentState {
  const t = useT();
  const [document, setDocument] = useState("");
  // The answer is for the document it was sent with; editing it asks again.
  const [answered, setAnswered] = useState<{ document: string; analysis: ComposeAnalysis } | null>(null);
  const analyze = useMutation({
    mutationFn: (sent: string) => request("post", "/api/compose/analyze", { body: { document: sent } }),
    onSuccess: (analysis, sent) => {
      setAnswered({ document: sent, analysis });
      announce(analysis.deployable ? t("newApp.compose.looked") : t("newApp.compose.lookedRefused"));
    },
  });
  const analysis = answered !== null && answered.document === document ? answered.analysis : null;
  // Converting a Coolify template makes the document, the look at it and the values it needs.
  const [converted, setConverted] = useState<{ adjustments: string[]; warnings: string[]; count: number } | null>(null);
  const [made, setMade] = useState<Record<string, string>>({});
  const convert = useMutation({
    mutationFn: (sent: string) => request("post", "/api/compose/import", { body: { document: sent, format: "coolify" } }),
    onSuccess: (result) => {
      setDocument(result.document);
      setAnswered({ document: result.document, analysis: result.analysis });
      setMade(result.values);
      setConverted({ adjustments: result.adjustments, warnings: result.warnings, count: Object.keys(result.values).length });
      announce(t("newApp.compose.coolify.done"));
    },
  });

  const content = (
    <div className="flex flex-col gap-5">
      <Field label={t("newApp.compose.field")} description={t("newApp.compose.description")}>
        <Textarea
          id={COMPOSE_DOCUMENT_FIELD}
          mono
          rows={14}
          wrap="off"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={"services:\n  web:\n    image: ghcr.io/owner/app:1.4\n    ports:\n      - \"8080:80\""}
          value={document}
          onChange={(event) => {
            setDocument(event.target.value);
            if (analyze.isError) analyze.reset();
            if (convert.isError) convert.reset();
          }}
        />
      </Field>
      <div>
        <Button loading={convert.isPending} disabled={document.trim() === ""} onClick={() => convert.mutate(document)}>
          {t("newApp.compose.coolify.convert")}
        </Button>
        <p className="mt-1 text-13 text-fg-muted">{t("newApp.compose.coolify.help")}</p>
      </div>
      {convert.isError ? <ErrorBlock live compact error={convert.error} title={t("newApp.compose.coolify.failed")} /> : null}
      {converted !== null ? <Converted {...converted} /> : null}
      {analyze.isError ? <ErrorBlock live compact error={analyze.error} title={t("newApp.compose.notLooked")} /> : null}
      {analysis !== null ? <Found analysis={analysis} /> : null}
      <CommandHint command="noust compose check FILE" label={t("newApp.source.terminal")} />
    </div>
  );

  return { document, analysis, looking: analyze.isPending, look: () => analyze.mutate(document), made, content };
}
