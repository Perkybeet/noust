import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { noustDnsKeys } from "../../../api/queries/noustDns";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { FieldGroup } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { useServerList } from "../../../nodes/servers";

interface Part {
  node: string | null;
  hostname: string;
}

const EMPTY: Part = { node: null, hostname: "" };
/** Past this a person is setting up a fleet of name servers, not a name server with its spares. */
const MOST_SECONDARIES = 3;

/**
 * Sets Noust DNS up on several servers at once, from the central: a primary and the secondaries that
 * copy it, in other networks. The central installs the primary first and alone, then each secondary
 * reads its address and key and tells it where it answers; the key crosses the central in one request
 * and is kept nowhere. It is a fleet job, followed on its own page. A server that cannot take part
 * (an older Noust, or one that did not let this central change its host) is refused in the server's
 * own words, with how to raise it.
 */
export function ClusterDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const formId = useId();
  const servers = useServerList();
  const [primary, setPrimary] = useState<Part>(EMPTY);
  const [secondaries, setSecondaries] = useState<Part[]>([EMPTY]);
  const [epel, setEpel] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const options = servers.nodes.map((node) => ({ value: node.name, label: node.name }));

  const setUp = useMutation({
    mutationFn: () =>
      request("post", "/api/fleet/dns/cluster", {
        body: {
          primary: { node: primary.node ?? "", hostname: primary.hostname.trim().toLowerCase() },
          secondaries: secondaries.map((part) => ({ node: part.node ?? "", hostname: part.hostname.trim().toLowerCase() })),
          epel,
          plan: false,
        },
      }),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: noustDnsKeys.fleet });
      close(true);
      const job = answer.job;
      if (job !== null && job !== undefined) void navigate({ to: "/fleet/jobs/$id", params: { id: job.job_id } });
    },
  });

  function close(settled = false): void {
    if (setUp.isPending && !settled) return;
    onOpenChange(false);
    setPrimary(EMPTY);
    setSecondaries([EMPTY]);
    setEpel(false);
    setProblem(null);
    setUp.reset();
  }

  const change = (index: number, patch: Partial<Part>): void => setSecondaries((previous) => previous.map((part, at) => (at === index ? { ...part, ...patch } : part)));
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const parts = [primary, ...secondaries];
    if (parts.some((part) => part.node === null || part.hostname.trim() === "")) {
      setProblem(t("noustDns.cluster.missing"));
      return;
    }
    if (new Set(parts.map((part) => part.node)).size !== parts.length) {
      setProblem(t("noustDns.cluster.sameServer"));
      return;
    }
    setProblem(null);
    setUp.mutate();
  };
  const fields = setUp.error && isApiError(setUp.error) ? setUp.error.fields : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      title={t("noustDns.cluster.title")}
      description={t("noustDns.cluster.description")}
      footer={
        <>
          <Button disabled={setUp.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={setUp.isPending}>
            {t("noustDns.cluster.action")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <FieldGroup legend={t("noustDns.cluster.primary")} description={t("noustDns.cluster.primaryHelp")} error={problem}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select aria-label={t("noustDns.cluster.server")} value={primary.node} placeholder={t("noustDns.cluster.choose")} onValueChange={(node) => setPrimary((previous) => ({ ...previous, node }))} options={options} />
            <Input mono aria-label={t("noustDns.setup.hostnameLabel")} value={primary.hostname} onValueChange={(hostname: string) => setPrimary((previous) => ({ ...previous, hostname }))} placeholder="ns1.example.net" autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </div>
        </FieldGroup>
        <FieldGroup legend={t("noustDns.cluster.secondaries")} description={t("noustDns.cluster.secondariesHelp")} error={fields?.["secondaries"] ?? null}>
          <div className="flex flex-col gap-3">
            {secondaries.map((part, index) => (
              <div key={index} className="grid gap-3 sm:grid-cols-2">
                <Select aria-label={t("noustDns.cluster.secondaryServer", { number: index + 1 })} value={part.node} placeholder={t("noustDns.cluster.choose")} onValueChange={(node) => change(index, { node })} options={options} />
                <Input mono aria-label={t("noustDns.cluster.secondaryName", { number: index + 1 })} value={part.hostname} onValueChange={(hostname: string) => change(index, { hostname })} placeholder="ns2.example.net" autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </div>
            ))}
            {secondaries.length < MOST_SECONDARIES ? (
              <div>
                <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => setSecondaries((previous) => [...previous, EMPTY])}>
                  {t("noustDns.cluster.addSecondary")}
                </Button>
              </div>
            ) : null}
          </div>
        </FieldGroup>
        <Disclosure label={t("noustDns.addZone.moreOptions")} defaultOpen={false}>
          <Checkbox label={t("noustDns.cluster.epelLabel")} description={t("noustDns.cluster.epelHelp")} checked={epel} onCheckedChange={setEpel} />
        </Disclosure>
        {setUp.isError && fields === null ? <ErrorBlock live compact error={setUp.error} title={t("noustDns.cluster.failed")} /> : null}
      </form>
    </Dialog>
  );
}
