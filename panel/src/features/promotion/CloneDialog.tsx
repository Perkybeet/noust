import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { databasesQuery } from "../../api/queries/databases";
import type { CloneBody } from "../../api/queries/promotion";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { DestinationFields } from "../teams/DestinationFields";
import { useDestination } from "../teams/useDestination";
import type { MovableApp } from "../teams/MoveAppDialog";

type Data = "empty" | "share";

interface FormProps {
  app: MovableApp;
  formId: string;
  onPending: (pending: boolean) => void;
  onDone: () => void;
}

const FIELDS = ["domain", "database", "source"] as const;

/** Mounted fresh for every opening, so nothing of an abandoned attempt is there the next time. */
function CloneForm({ app, formId, onPending, onDone }: FormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const here = {
    ...(app.team != null ? { team: app.team } : {}),
    ...(app.project != null ? { project: app.project } : {}),
    ...(app.environment != null ? { environment: app.environment } : {}),
  };
  const destination = useDestination(here);
  const databases = useQuery(databasesQuery());
  const [domain, setDomain] = useState("");
  const [data, setData] = useState<Data | null>(null);
  const [source, setSource] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const linked = (databases.data?.databases ?? []).some((database) => database.app === app.domain || (database.apps ?? []).includes(app.domain));

  const clone = useMutation({
    mutationFn: (body: CloneBody) => request("post", "/api/apps/{domain}/clone", { params: { domain: app.domain }, body }),
    onSuccess: (_result, body) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      announce(t("promotion.clone.queued", { domain: app.domain, clone: body.domain }));
      toast.success(t("promotion.clone.queued", { domain: app.domain, clone: body.domain }));
      onDone();
    },
    onSettled: () => onPending(false),
  });

  const split = splitErrors(clone.error, FIELDS);
  // The server asks for the choice when the app has a database the console did not see (it reads the list apart).
  const databaseAsked = split.fields.database !== undefined;
  const showData = linked || databaseAsked;
  const trimmed = domain.trim();
  const domainError = submitted && trimmed === "" ? t("promotion.clone.domainRequired") : split.fields.domain;
  const dataError = submitted && showData && data === null ? t("promotion.clone.dataNeeded") : split.fields.database;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (trimmed === "" || (showData && data === null) || clone.isPending) return;
    onPending(true);
    clone.mutate({
      domain: trimmed,
      ...destination.body,
      ...(showData && data !== null ? { database: data } : {}),
      ...(source.trim() !== "" ? { source: source.trim() } : {}),
    });
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
      <Field label={t("promotion.clone.domainLabel")} description={t("promotion.clone.domainDescription")} error={domainError}>
        <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="staging.example.com" value={domain} onValueChange={setDomain} />
      </Field>
      <DestinationFields destination={{ ...destination, needed: destination.needed, showTeam: destination.teams.length > 1 }} />
      {showData ? (
        <div className="flex flex-col gap-2">
          <ChoiceCards<Data>
            legend={t("promotion.clone.dataLegend")}
            // Nothing is chosen for the operator: sharing a database is a decision, and so is not sharing.
            value={data ?? ("" as Data)}
            onValueChange={setData}
            options={[
              { value: "empty", label: t("promotion.clone.dataEmpty"), description: t("promotion.clone.dataEmptyDescription") },
              { value: "share", label: t("promotion.clone.dataShare"), description: t("promotion.clone.dataShareDescription") },
            ]}
          />
          {dataError ? <p className="text-13 text-fail">{dataError}</p> : null}
        </div>
      ) : null}
      <Disclosure label={t("promotion.clone.moreOptions")}>
        <Field label={t("promotion.clone.sourceLabel")} description={t("promotion.clone.sourceDescription")} error={split.fields.source} optional>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={source} onValueChange={setSource} />
        </Field>
      </Disclosure>
      {clone.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("promotion.clone.failed", { domain: app.domain })} /> : null}
    </form>
  );
}

export interface CloneDialogProps {
  app: MovableApp;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Makes another app from this one: same settings, variables, hooks and schedule, deployed from
 * the same source in this or another environment, as a job built and checked like any deploy.
 * The data is not copied; when the app has a database the operator says whether the clone gets
 * one of its own or shares this app's, because nothing is chosen for them.
 */
export function CloneDialog({ app, open, onOpenChange }: CloneDialogProps) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
      size="md"
      title={t("promotion.clone.title", { domain: app.domain })}
      description={t("promotion.clone.description")}
      footer={
        <>
          <Button disabled={pending} onClick={() => onOpenChange(false)}>
            {t("variables.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {t("promotion.clone.submit")}
          </Button>
        </>
      }
    >
      {open ? <CloneForm app={app} formId={formId} onPending={setPending} onDone={() => onOpenChange(false)} /> : null}
    </Dialog>
  );
}
