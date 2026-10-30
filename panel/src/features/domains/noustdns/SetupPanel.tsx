import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import { noustDnsKeys } from "../../../api/queries/noustDns";
import type { NoustServer } from "../../../api/queries/noustDns";
import { CommandHint } from "../../../components/page/CommandHint";
import { JobProgress } from "../../../components/page/JobProgress";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Field, FieldGroup } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";

type Role = "primary" | "secondary";

/** The addresses a person typed, separated by commas, spaces or lines. */
function addressesOf(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter((entry) => entry !== "");
}

/**
 * The server has Knot and is not Noust DNS's yet: what it does (a primary that holds the zones, or a
 * secondary that copies them from one in another network), the name it has as a name server and the
 * addresses it answers on. The addresses Noust found are offered ticked; a secondary says where its
 * primary is and the key the two sign their transfers with. Setting up is a job, followed here.
 */
export function SetupPanel({ server }: { server: NoustServer }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const followed = useFollowedJob();
  const candidates = server.candidates ?? [];
  const [role, setRole] = useState<Role>("primary");
  const [hostname, setHostname] = useState("");
  const [listen, setListen] = useState<string[]>(candidates);
  const [typed, setTyped] = useState("");
  const [primaryAddress, setPrimaryAddress] = useState("");
  const [secret, setSecret] = useState("");
  const [problem, setProblem] = useState<{ field: string; text: string } | null>(null);

  const setup = useMutation({
    mutationFn: () =>
      request("post", "/api/dns/noust/server", {
        body: {
          role,
          epel: false,
          hostname: hostname.trim().toLowerCase(),
          listen: candidates.length > 0 ? listen : addressesOf(typed),
          ...(role === "secondary" ? { primary_address: primaryAddress.trim(), transfer_key: { secret: secret.trim() } } : {}),
        },
      }),
    onSuccess: (job) => followed.follow(job.job_id),
  });

  useEffect(() => {
    if (followed.job !== null && isJobFinished(followed.job)) void queryClient.invalidateQueries({ queryKey: noustDnsKeys.server });
  }, [followed.job, queryClient]);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (hostname.trim() === "") {
      setProblem({ field: "hostname", text: t("noustDns.fieldRequired") });
      return;
    }
    if (role === "secondary" && primaryAddress.trim() === "") {
      setProblem({ field: "primary_address", text: t("noustDns.fieldRequired") });
      return;
    }
    if (role === "secondary" && secret.trim() === "") {
      setProblem({ field: "transfer_key", text: t("noustDns.fieldRequired") });
      return;
    }
    setProblem(null);
    setup.mutate();
  };

  const fields = setup.error && isApiError(setup.error) ? setup.error.fields : null;
  const fieldError = (name: string): string | null => (problem?.field === name ? problem.text : (fields?.[name] ?? null));
  const job = followed.job;
  const running = followed.id !== null && (job === null || !isJobFinished(job));
  const foreign = server.foreign_zones ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card level={2} title={t("noustDns.setup.title")} icon={OBJECT_ICONS.dns} description={t("noustDns.setup.description")}>
        <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
          {foreign.length > 0 ? <Notice tone="warning" title={t("noustDns.setup.foreignTitle")}>{t("noustDns.setup.foreignBody", { zones: foreign.join(", ") })}</Notice> : null}
          <ChoiceCards<Role>
            legend={t("noustDns.setup.roleLegend")}
            options={[
              { value: "primary", label: t("noustDns.setup.primary"), description: t("noustDns.setup.primaryHelp") },
              { value: "secondary", label: t("noustDns.setup.secondary"), description: t("noustDns.setup.secondaryHelp") },
            ]}
            value={role}
            onValueChange={setRole}
          />
          <Field label={t("noustDns.setup.hostnameLabel")} description={t("noustDns.setup.hostnameHelp")} error={fieldError("hostname")}>
            <Input mono value={hostname} onValueChange={setHostname} placeholder="ns1.example.net" autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
          {candidates.length > 0 ? (
            <FieldGroup legend={t("noustDns.setup.listenLabel")} description={t("noustDns.setup.listenHelp")} error={fieldError("listen")}>
              {candidates.map((address) => (
                <Checkbox
                  key={address}
                  label={<span translate="no">{address}</span>}
                  checked={listen.includes(address)}
                  onCheckedChange={(next) => setListen((previous) => (next ? [...previous, address] : previous.filter((entry) => entry !== address)))}
                />
              ))}
            </FieldGroup>
          ) : (
            <Field label={t("noustDns.setup.listenField")} description={t("noustDns.setup.listenNone")} error={fieldError("listen")}>
              <Input mono value={typed} onValueChange={setTyped} placeholder="203.0.113.10" autoComplete="off" spellCheck={false} />
            </Field>
          )}
          {role === "secondary" ? (
            <>
              <Field label={t("noustDns.setup.primaryAddressLabel")} description={t("noustDns.setup.primaryAddressHelp")} error={fieldError("primary_address")}>
                <Input mono value={primaryAddress} onValueChange={setPrimaryAddress} autoComplete="off" spellCheck={false} />
              </Field>
              <Field label={t("noustDns.setup.keyLabel")} description={t("noustDns.setup.keyHelp")} error={fieldError("transfer_key")}>
                <Textarea mono rows={3} value={secret} onChange={(event) => setSecret(event.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
            </>
          ) : null}
          {running ? (
            <JobProgress state={job?.status === "running" ? "running" : "queued"} title={t("noustDns.setup.running")} step={job?.current_step ?? null} />
          ) : job !== null && isJobFinished(job) && job.status !== "completed" ? (
            <JobProgress state="failed" title={t("noustDns.setup.failed")} error={{ detail: job.error ?? "" }} onDismiss={followed.dismiss} />
          ) : setup.isError && fields === null ? (
            <JobProgress state="failed" title={t("noustDns.setup.failed")} error={setup.error} onDismiss={() => setup.reset()} />
          ) : null}
          <div>
            <Button type="submit" variant="primary" loading={setup.isPending || running}>
              {t("noustDns.setup.action")}
            </Button>
          </div>
        </form>
      </Card>
      <CommandHint command="noust dns server setup --role primary --hostname ns1.example.net" label={t("common.words.fromTerminal")} />
    </div>
  );
}
