import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import type { AppExportDocument } from "../../api/queries/appImport";
import { listRemoteApplications, readRemoteApplication } from "../../api/queries/importRemote";
import type { RemoteApplication, RemoteApplications, RemotePlatform, RemoteProposal } from "../../api/queries/importRemote";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { useT } from "../../i18n";

/** The id of the address field, so the wizard's Continue can point at it when nothing is read yet. */
export const REMOTE_FIRST_FIELD = "new-app-import-remote";

/** The platforms' own names: not translated. */
export const PLATFORM_NAME: Record<RemotePlatform, string> = { coolify: "Coolify", dokploy: "Dokploy" };

/** What was read from the other server, ready to take through the import review. */
export interface RemoteRead {
  label: string;
  document: AppExportDocument;
}

export interface ImportRemoteProps {
  platform: RemotePlatform;
  /** What was read, or null when what is on screen is not something to go on with. */
  onRead: (read: RemoteRead | null) => void;
}

interface ReadOutcome {
  name: string;
  proposal: RemoteProposal["proposal"];
  deployable: boolean;
}

function applicationOptions(listed: RemoteApplications | undefined): SelectOption[] {
  return (listed?.applications ?? []).map((application: RemoteApplication) => ({
    value: application.ref,
    label: application.name,
    hint: application.domains.length > 0 ? application.domains.join(", ") : application.source,
  }));
}

/**
 * An application of a Coolify or Dokploy server as the start of an import. The server and a
 * read-only token are asked for here and used for two requests, the list and the one application
 * read; the token is cleared the moment the second answers, and never kept by the console or the
 * server. What comes out is the export document the file import reviews, so everything after this
 * is the same review: the domain, the source, the secrets the other platform did not hand over.
 */
export function ImportRemote({ platform, onRead }: ImportRemoteProps) {
  const t = useT();
  const name = PLATFORM_NAME[platform];
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  const [outcome, setOutcome] = useState<ReadOutcome | null>(null);

  const list = useMutation({ mutationFn: () => listRemoteApplications({ platform, url: url.trim(), token }) });
  const reading = useMutation({
    mutationFn: (application: string) =>
      readRemoteApplication({ platform, url: url.trim(), token, ref: application, domain: domain.trim() }),
    onSuccess: (result) => {
      const application = list.data?.applications.find((candidate) => candidate.ref === chosen);
      const read = application?.name ?? name;
      setOutcome({ name: read, proposal: result.proposal, deployable: result.deployable });
      onRead(result.deployable ? { label: t("newApp.importRemote.label", { platform: name, name: read }), document: result.document } : null);
      // The token has served the two requests it was typed for.
      setToken("");
      list.reset();
      setChosen(null);
    },
  });

  const choose = (ref: string): void => {
    setChosen(ref);
    setDomain(list.data?.applications.find((application) => application.ref === ref)?.domains[0] ?? "");
    setOutcome(null);
    reading.reset();
    onRead(null);
  };

  const options = applicationOptions(list.data);

  return (
    <div className="flex flex-col gap-6">
      <p className="text-13 text-fg-muted">{t("newApp.importRemote.explain", { platform: name })}</p>

      <div className="flex flex-col gap-4">
        <Field label={t("newApp.importRemote.url")} description={t("newApp.importRemote.urlDescription", { platform: name })}>
          <Input id={REMOTE_FIRST_FIELD} mono value={url} onValueChange={setUrl} placeholder={`https://${platform}.example.com`} autoComplete="off" />
        </Field>
        <Field label={t("newApp.importRemote.token")} description={t(platform === "coolify" ? "newApp.importRemote.tokenCoolify" : "newApp.importRemote.tokenDokploy")}>
          <Input mono type="password" value={token} onValueChange={setToken} autoComplete="off" />
        </Field>
        <div>
          <Button variant="secondary" loading={list.isPending} disabled={url.trim() === "" || token === ""} onClick={() => list.mutate()}>
            {t("newApp.importRemote.list")}
          </Button>
        </div>
      </div>

      {list.isError ? <ErrorBlock live compact error={list.error} title={t("newApp.importRemote.listFailed")} hint={t("newApp.importRemote.listFailedHint")} /> : null}

      {list.data !== undefined ? (
        options.length === 0 ? (
          <p role="status" className="text-13 text-fg-muted">
            {t("newApp.importRemote.none", { platform: name })}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            <Select label={t("newApp.importRemote.application")} options={options} value={chosen} onValueChange={choose} placeholder={t("newApp.importRemote.choose")} />
            {chosen !== null ? (
              <>
                <Field label={t("newApp.importRemote.domain")} description={t("newApp.importRemote.domainDescription")}>
                  <Input mono value={domain} onValueChange={setDomain} autoComplete="off" />
                </Field>
                <div>
                  <Button loading={reading.isPending} disabled={domain.trim() === ""} onClick={() => reading.mutate(chosen)}>
                    {t("newApp.importRemote.read")}
                  </Button>
                </div>
              </>
            ) : null}
          </div>
        )
      ) : null}

      {reading.isError ? <ErrorBlock live compact error={reading.error} title={t("newApp.importRemote.readFailed")} /> : null}

      {outcome !== null ? (
        <Card padding="sm">
          <div role="status" className="flex min-w-0 flex-col gap-3 text-13">
            <div className="flex min-w-0 items-start gap-3">
              <ICONS.success aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
              <p className="font-medium text-fg">{t("newApp.importRemote.done", { platform: name, name: outcome.name })}</p>
            </div>
            {outcome.deployable ? null : (
              <div className="flex flex-col gap-2">
                <p className="text-fg-muted">{t("newApp.importRemote.notDeployable")}</p>
                {outcome.proposal.equivalent !== undefined && outcome.proposal.equivalent !== null ? (
                  <CommandHint command={outcome.proposal.equivalent} label={t("newApp.importRemote.instead")} />
                ) : null}
              </div>
            )}
            {outcome.proposal.warnings.length > 0 ? (
              <div className="flex flex-col gap-1">
                <p className="font-medium text-fg">{t("newApp.importRemote.warnings")}</p>
                <ul className="flex list-disc flex-col gap-1 pl-5 text-fg-muted">
                  {outcome.proposal.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
