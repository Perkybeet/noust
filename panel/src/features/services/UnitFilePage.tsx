/**
 * A service's unit file (T6): the file alone, the screen's height, with the bar that tests it
 * and saves it. It is read and written verbatim; `systemd-analyze verify` checks the candidate
 * first and a refusal is shown in systemd's own words with nothing written, the way a site's
 * configuration is held to `nginx -t`. Saving asks "Confirm it's you" (the API client asks on
 * its own), and restarting the service applies what was saved.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { isApiError } from "../../api/client";
import { serviceConfigQuery } from "../../api/queries/services";
import { CommandHint } from "../../components/page/CommandHint";
import { FileEditorPage } from "../../components/page/FileEditorPage";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { changedLines } from "../../lib/lineDiff";
import { ConfigEditor } from "../domains/ConfigEditor";
import { serviceState } from "./data";
import { UnitCheckVerdict } from "./UnitCheck";
import { useServiceActions } from "./useServiceActions";
import { useServiceRecord } from "./useServiceRecord";

type Outcome = { kind: "rejected"; output: string } | { kind: "passed"; output: string } | { kind: "saved"; output: string } | null;

export function UnitFilePage({ name }: { name: string }) {
  const t = useT();
  const record = useServiceRecord(name);
  const config = useQuery(serviceConfigQuery(name));
  const { updateConfig, verifyUnit, restart } = useServiceActions(name);
  const [value, setValue] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);

  // Seeded from the file once it first arrives, and again after a save reads it back: what is
  // on screen after a save is what the manager wrote, not only what was typed.
  if (value === null && config.data !== undefined) setValue(config.data.config);

  const remote = config.data?.config ?? null;
  const changes = value !== null && remote !== null ? changedLines(remote, value) : 0;
  const view = record.service ? serviceState(record.service) : null;

  const test = (then?: () => void): void => {
    if (value === null) return;
    setOutcome(null);
    verifyUnit.mutate(value, {
      onSuccess: (result) => {
        if (!result.success) {
          setOutcome({ kind: "rejected", output: result.output });
          return;
        }
        if (then) then();
        else setOutcome({ kind: "passed", output: result.output });
      },
    });
  };

  const save = (): void => {
    if (value === null) return;
    test(() => {
      updateConfig.mutate(value, {
        onSuccess: () => {
          void config.refetch().then((fresh) => {
            if (fresh.data) setValue(fresh.data.config);
          });
          setOutcome({ kind: "saved", output: "" });
        },
      });
    });
  };

  const notice =
    outcome?.kind === "rejected" || outcome?.kind === "passed" ? (
      <UnitCheckVerdict success={outcome.kind === "passed"} output={outcome.output} />
    ) : outcome?.kind === "saved" ? (
      <Notice
        live
        title={t("services.unitFile.saved")}
        action={
          <Button size="sm" loading={restart.isPending} onClick={() => restart.mutate()}>
            {t("services.unitFile.restartNow")}
          </Button>
        }
      >
        {t("services.unitFile.savedHint")}
      </Notice>
    ) : verifyUnit.isError ? (
      <ErrorBlock live compact error={verifyUnit.error} title={t("services.unitEditor.verifyFailed")} />
    ) : updateConfig.isError ? (
      <ErrorBlock live compact error={updateConfig.error} title={t("services.unitEditor.saveFailed")} />
    ) : (
      <InlineMessage tone="info">{t("services.unitFile.checkedTitle")}</InlineMessage>
    );

  return (
    <FileEditorPage
      header={{
        title: name,
        mono: true,
        status: view !== null ? <StatusPill state={view.state} label={t(view.label)} /> : undefined,
        secondaryActions: (
          <Link to="/server/services/$name" params={{ name }} className={buttonClassName("secondary")}>
            {t("services.unitFile.backToService")}
          </Link>
        ),
      }}
      notice={notice}
      meta={config.data ? <Mono tone="muted">{config.data.path}</Mono> : undefined}
      bar={{
        changes,
        onDiscard: () => {
          setValue(remote);
          setOutcome(null);
        },
        onTest: () => test(),
        onTestAndSave: save,
        testing: verifyUnit.isPending && !updateConfig.isPending,
        saving: updateConfig.isPending,
      }}
      footer={<CommandHint command={`noust service status ${name}`} label={t("common.words.fromTerminal")} />}
    >
      {/* A unit file can hold secrets written into it, so reading one takes `secrets.reveal`: a role without it is told that, and not given the server's bare refusal. */}
      {config.isError && isApiError(config.error) && config.error.status === 403 ? (
        <EmptyState variant="inline" title={t("services.unitFile.noAccessTitle")} description={t.rich("services.unitFile.noAccessDescription", { permission: <Mono key="permission">secrets.reveal</Mono> })} />
      ) : (
      <QueryState query={config} label={t("services.unitEditor.what")} skeleton={<Skeleton className="h-96 w-full rounded-control" />}>
        {() =>
          value === null ? (
            <Skeleton className="h-96 w-full rounded-control" />
          ) : (
            <ConfigEditor
              value={value}
              onChange={(next) => {
                setValue(next);
                if (outcome?.kind !== "rejected") setOutcome(null);
              }}
              label={t("services.unitEditor.label", { name })}
              disabled={updateConfig.isPending}
              className="h-full"
            />
          )
        }
      </QueryState>
      )}
    </FileEditorPage>
  );
}
