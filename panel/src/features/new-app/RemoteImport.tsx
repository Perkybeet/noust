import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";

type Applications = ResponseOf<"/api/import/applications", "post">;
type Proposal = ResponseOf<"/api/import/proposal", "post">;
type Platform = "coolify" | "dokploy" | "caprover";

export interface RemoteImportProps {
  /** An export document read from the other server, to be reviewed like one read from a file. */
  onLoaded: (label: string, text: string) => void;
}

/** What the other server answered for one application, in the console's words. */
function Outcome({ name, proposal }: { name: string; proposal: Proposal }) {
  const t = useT();
  const warnings = proposal.proposal.warnings ?? [];
  const equivalent = proposal.proposal.equivalent ?? null;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {proposal.deployable ? (
        <Notice tone="success" live title={t("remoteImport.loadedTitle", { name })}>
          {t("remoteImport.loadedBody")}
        </Notice>
      ) : (
        <Notice tone="warning" live title={t("remoteImport.notDeployable", { name })}>
          {t("remoteImport.notDeployableBody")}
        </Notice>
      )}
      {!proposal.deployable && equivalent !== null ? <CommandHint command={equivalent} label={t("common.words.sameFromTerminal")} /> : null}
      {warnings.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-12 font-medium text-fg">{t("remoteImport.warnings")}</p>
          {/* The server's words, verbatim: they say what has no equivalent and what to do instead. */}
          <ul aria-label={t("remoteImport.warnings")} className="flex flex-col gap-1.5">
            {warnings.map((warning) => (
              <li key={warning} className="text-13 text-pretty text-fg">
                {warning}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Bringing an application over from a Coolify or Dokploy server through its API: the address
 * and a read-only token list what it has, one application is read as a proposal, and what it
 * becomes is handed to the import review as if it had been an export file. The token goes in
 * the request body and nowhere else: it is not stored here or by the server.
 */
export function RemoteImport({ onLoaded }: RemoteImportProps) {
  const t = useT();
  const [platform, setPlatform] = useState<Platform>("coolify");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [chosen, setChosen] = useState("");
  const [domain, setDomain] = useState("");
  const [lastRead, setLastRead] = useState<{ name: string; proposal: Proposal } | null>(null);

  const list = useMutation({
    mutationFn: () => request("post", "/api/import/applications", { body: { platform, url: url.trim(), token } }),
    onSuccess: (found: Applications) => {
      setChosen(found.applications[0]?.ref ?? "");
      setLastRead(null);
      announce(t("remoteImport.applicationsDescription", { count: found.applications.length, url: url.trim() }));
    },
  });
  const found = list.data;
  const selected = found?.applications.find((item) => item.ref === chosen);

  const read = useMutation({
    mutationFn: () =>
      request("post", "/api/import/proposal", {
        body: { platform, url: url.trim(), token, ref: chosen, ...(domain.trim() === "" ? {} : { domain: domain.trim() }) },
      }),
    onSuccess: (proposal) => {
      const name = selected?.name ?? chosen;
      setLastRead({ name, proposal });
      if (proposal.deployable) onLoaded(t("remoteImport.label", { platform: t(`remoteImport.platforms.${platform}`), name }), JSON.stringify(proposal.document));
    },
  });
  const listErrors = splitErrors(list.error, ["platform", "url", "token"] as const);
  const readErrors = splitErrors(read.error, ["domain", "ref"] as const);

  const options: SelectOption[] = (found?.applications ?? []).map((item) => ({
    value: item.ref,
    label: item.name,
    hint: [item.project, item.build, ...(item.domains ?? [])].filter((part) => part !== "").join(" - "),
  }));

  const submitList = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!list.isPending && url.trim() !== "" && token !== "") list.mutate();
  };
  const submitRead = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!read.isPending && chosen !== "") read.mutate();
  };

  return (
    <Card title={t("remoteImport.title")} description={t("remoteImport.description")}>
      <div className="flex min-w-0 flex-col gap-5">
        <form onSubmit={submitList} noValidate className="flex min-w-0 flex-col gap-4">
          <Field label={t("remoteImport.platform")} nativeLabel={false} error={listErrors.fields.platform} className="sm:max-w-56">
            <Select
              options={[
                { value: "coolify", label: t("remoteImport.platforms.coolify") },
                { value: "dokploy", label: t("remoteImport.platforms.dokploy") },
                { value: "caprover", label: t("remoteImport.platforms.caprover") },
              ]}
              value={platform}
              onValueChange={(next) => {
                setPlatform(next === "dokploy" || next === "caprover" ? next : "coolify");
              }}
              className="w-full"
            />
          </Field>
          <Field label={t("remoteImport.url")} description={t("remoteImport.urlDescription")} error={listErrors.fields.url}>
            <Input mono value={url} onValueChange={setUrl} placeholder="https://coolify.example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
          <Field
            label={t(platform === "caprover" ? "remoteImport.password" : "remoteImport.token")}
            description={t(platform === "caprover" ? "remoteImport.passwordDescription" : "remoteImport.tokenDescription")}
            error={listErrors.fields.token}
          >
            <Input mono type="password" value={token} onValueChange={setToken} autoComplete="new-password" spellCheck={false} />
          </Field>
          {list.isError && listErrors.form !== null ? <ErrorBlock live compact error={listErrors.form} title={t("remoteImport.listFailed")} /> : null}
          <div>
            <Button type="submit" size="sm" loading={list.isPending} disabled={url.trim() === "" || token === ""}>
              {t("remoteImport.list")}
            </Button>
          </div>
        </form>

        {found !== undefined ? (
          found.applications.length === 0 ? (
            <p role="status" className="text-13 text-fg-muted">
              {t("remoteImport.none")}
            </p>
          ) : (
            <form onSubmit={submitRead} noValidate className="flex min-w-0 flex-col gap-4">
              <Field
                label={t("remoteImport.applications")}
                nativeLabel={false}
                description={t("remoteImport.applicationsDescription", { count: found.applications.length, url: url.trim() })}
                error={readErrors.fields.ref}
              >
                <Select
                  options={options}
                  value={chosen === "" ? null : chosen}
                  placeholder={t("remoteImport.choose")}
                  onValueChange={(next) => {
                    setChosen(next);
                    setLastRead(null);
                  }}
                  className="w-full"
                />
              </Field>
              <Field label={t("remoteImport.domain")} description={t("remoteImport.domainDescription")} error={readErrors.fields.domain} optional>
                <Input mono value={domain} onValueChange={setDomain} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
              {read.isError && readErrors.form !== null ? <ErrorBlock live compact error={readErrors.form} title={t("remoteImport.readFailed")} /> : null}
              <div>
                <Button type="submit" variant="primary" size="sm" loading={read.isPending} disabled={chosen === ""}>
                  {t("remoteImport.read")}
                </Button>
              </div>
            </form>
          )
        ) : null}

        {lastRead !== null ? <Outcome name={lastRead.name} proposal={lastRead.proposal} /> : null}
        <CommandHint command={`noust import --from ${platform} --url ${url.trim() === "" ? "https://coolify.example.com" : url.trim()} --token-file FILE`} label={t("common.words.sameFromTerminal")} />
      </div>
    </Card>
  );
}
