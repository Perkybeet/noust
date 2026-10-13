import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";

type Report = ResponseOf<"/api/workspaces/inspect", "post">;
type Entry = Report["workspaces"][number];

/** The names the deploy takes for a workspace: the directory is unique where the name may not be. */
export function workspaceKey(entry: Pick<Entry, "directory">): string {
  return entry.directory;
}

export interface WorkspacesDialogProps {
  open: boolean;
  /** The source the wizard already has, to start from. */
  source: string;
  branch: string;
  onClose: () => void;
}

/** One workspace that is an application: chosen, and given the domain it is deployed on. */
function ApplicationRow({ entry, domain, onDomain, chosen, onChosen, disabled }: { entry: Entry; domain: string; onDomain: (value: string) => void; chosen: boolean; onChosen: (value: boolean) => void; disabled: boolean }) {
  const t = useT();
  const dependsOn = entry.depends_on;
  const watches = entry.watch_paths;
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-control border border-border px-3 py-2">
      <Checkbox
        label={t("workspaces.choose", { name: entry.name })}
        description={`${entry.directory} - ${entry.app_type ?? ""}`}
        checked={chosen}
        disabled={disabled}
        onCheckedChange={onChosen}
      />
      {dependsOn.length > 0 ? (
        <p className="text-12 text-fg-muted">
          {t("workspaces.dependsOn")}: <Mono tone="muted">{dependsOn.join(", ")}</Mono>
        </p>
      ) : null}
      {watches.length > 0 ? (
        <p className="text-12 text-fg-muted">
          {t("workspaces.watches")}: <Mono tone="muted">{watches.join(", ")}</Mono>
        </p>
      ) : null}
      {chosen ? (
        <Field label={t("workspaces.domainFor", { name: entry.name })} className="sm:max-w-80">
          <Input mono value={domain} onValueChange={onDomain} placeholder="app.example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} disabled={disabled} />
        </Field>
      ) : null}
    </li>
  );
}

/**
 * A monorepo as one application per workspace: read which workspaces are applications, choose
 * them and give each a domain, and one request creates them all. Everything is checked before
 * anything is queued, so a domain that is taken or a workspace that is a library stops the lot
 * with the reason, and the server's words are shown as they came.
 */
export function WorkspacesDialog({ open, source: initialSource, branch: initialBranch, onClose }: WorkspacesDialogProps) {
  const t = useT();
  const [source, setSource] = useState(initialSource);
  const [branch, setBranch] = useState(initialBranch);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [domains, setDomains] = useState<Readonly<Record<string, string>>>({});

  const inspect = useMutation({
    mutationFn: () => request("post", "/api/workspaces/inspect", { body: { source: source.trim(), ...(branch.trim() === "" ? {} : { branch: branch.trim() }) } }),
    onSuccess: (found) => {
      setChosen(new Set());
      setDomains({});
      announce(found.workspaces.length === 0 ? t("workspaces.foundNone") : t("workspaces.found", { count: found.workspaces.length, tool: found.tool, manager: found.package_manager }));
    },
  });
  const report = inspect.data;
  const applications = (report?.workspaces ?? []).filter((entry) => entry.deployable);
  const libraries = (report?.workspaces ?? []).filter((entry) => !entry.deployable);
  const picked = applications.filter((entry) => chosen.has(workspaceKey(entry)));
  const ready = picked.length > 0 && picked.every((entry) => (domains[workspaceKey(entry)] ?? "").trim() !== "");

  const deploy = useMutation({
    mutationFn: () =>
      request("post", "/api/workspaces/deploy", {
        body: {
          source: source.trim(),
          ...(branch.trim() === "" ? {} : { branch: branch.trim() }),
          apps: picked.map((entry) => ({ workspace: entry.directory, domain: (domains[workspaceKey(entry)] ?? "").trim() })),
        },
      }),
    onSuccess: () => {
      announce(t("workspaces.queuedTitle"));
    },
  });
  const inspectErrors = splitErrors(inspect.error, ["source", "branch"] as const);
  const deployErrors = splitErrors(deploy.error, ["apps", "source", "branch"] as const);
  const busy = inspect.isPending || deploy.isPending;
  const queued = deploy.isSuccess;

  const close = (): void => {
    if (!busy) onClose();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!busy && source.trim() !== "") inspect.mutate();
  };
  const toggle = (entry: Entry, on: boolean): void => {
    setChosen((now) => {
      const next = new Set(now);
      if (on) next.add(workspaceKey(entry));
      else next.delete(workspaceKey(entry));
      return next;
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      title={t("workspaces.title")}
      description={t("workspaces.description")}
      footer={
        <>
          <Button disabled={busy} onClick={close}>
            {queued ? t("workspaces.close") : t("workspaces.cancel")}
          </Button>
          {queued ? null : (
            <Button
              variant="primary"
              disabled={!ready}
              loading={deploy.isPending}
              onClick={() => {
                deploy.mutate();
              }}
            >
              {picked.length === 1 ? t("workspaces.deployOne") : t("workspaces.deploy", { count: picked.length })}
            </Button>
          )}
        </>
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        <form onSubmit={submit} noValidate className="flex min-w-0 flex-col gap-4">
          <Field label={t("workspaces.source")} description={t("workspaces.sourceDescription")} error={inspectErrors.fields.source ?? deployErrors.fields.source}>
            <Input
              mono
              value={source}
              onValueChange={(value: string) => {
                setSource(value);
                inspect.reset();
                deploy.reset();
              }}
              placeholder="https://github.com/acme/monorepo.git"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              disabled={busy || queued}
            />
          </Field>
          <Field label={t("workspaces.branch")} optional error={inspectErrors.fields.branch ?? deployErrors.fields.branch} className="sm:max-w-56">
            <Input
              mono
              value={branch}
              onValueChange={(value: string) => {
                setBranch(value);
                inspect.reset();
              }}
              placeholder="main"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              disabled={busy || queued}
            />
          </Field>
          {inspect.isError && inspectErrors.form !== null ? <ErrorBlock live compact error={inspectErrors.form} title={t("workspaces.readFailed")} /> : null}
          <div>
            <Button type="submit" size="sm" loading={inspect.isPending} disabled={source.trim() === "" || queued}>
              {t("workspaces.read")}
            </Button>
          </div>
        </form>

        {report !== undefined ? (
          <div className="flex min-w-0 flex-col gap-4" aria-live="polite">
            <p className="text-13 text-fg-muted">
              {report.workspaces.length === 0 ? t("workspaces.foundNone") : t("workspaces.found", { count: report.workspaces.length, tool: report.tool, manager: report.package_manager })}
            </p>
            {report.problems.length > 0 ? (
              <Notice tone="warning" title={t("workspaces.problems")}>
                {/* What the repository's own files said, verbatim. */}
                <ul className="flex flex-col gap-1">
                  {report.problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            {report.workspaces.length > 0 && applications.length === 0 ? <p className="text-13 text-fg">{t("workspaces.none")}</p> : null}
            {applications.length > 0 ? (
              <Subsection title={t("workspaces.applications")}>
                <ul className="flex flex-col gap-2">
                  {applications.map((entry) => (
                    <ApplicationRow
                      key={workspaceKey(entry)}
                      entry={entry}
                      chosen={chosen.has(workspaceKey(entry))}
                      onChosen={(on) => {
                        toggle(entry, on);
                      }}
                      domain={domains[workspaceKey(entry)] ?? ""}
                      onDomain={(value) => {
                        setDomains((now) => ({ ...now, [workspaceKey(entry)]: value }));
                        deploy.reset();
                      }}
                      disabled={busy || queued}
                    />
                  ))}
                </ul>
              </Subsection>
            ) : null}
            {libraries.length > 0 ? (
              <Subsection title={t("workspaces.libraries")}>
                <ul className="flex flex-col gap-1.5">
                  {libraries.map((entry) => (
                    <li key={workspaceKey(entry)} className="text-13 text-fg-muted">
                      <Mono tone="muted">{entry.directory}</Mono> - {entry.reason}
                    </li>
                  ))}
                </ul>
              </Subsection>
            ) : null}
          </div>
        ) : null}

        {deploy.isError && deployErrors.form !== null ? <ErrorBlock live compact error={deployErrors.form} title={t("workspaces.deployFailed")} /> : null}
        {deploy.isError && deployErrors.fields.apps !== undefined ? <ErrorBlock live compact error={{ detail: deployErrors.fields.apps }} title={t("workspaces.deployFailed")} /> : null}
        {queued ? (
          <Notice tone="success" live title={t("workspaces.queuedTitle")}>
            <p>{t("workspaces.queuedBody")}</p>
            <ul className="mt-1 flex flex-col gap-1">
              {picked.map((entry) => {
                const domain = (domains[workspaceKey(entry)] ?? "").trim();
                return (
                  <li key={workspaceKey(entry)}>
                    <TextLink to="/apps/$domain" params={{ domain }} size="ui">
                      {t("workspaces.open_app", { domain })}
                    </TextLink>
                  </li>
                );
              })}
            </ul>
          </Notice>
        ) : null}
        <CommandHint command={`noust workspace create ${source.trim() === "" ? "https://github.com/acme/monorepo.git" : source.trim()} --app WORKSPACE=DOMAIN`} label={t("workspaces.fromTerminal")} />
      </div>
    </Dialog>
  );
}
