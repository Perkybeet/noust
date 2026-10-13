import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { appDnsQuery, networkKeys } from "../../api/queries/network";
import type { DnsPlan } from "../../api/queries/network";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { DnsConflictDialog } from "./DnsConflictDialog";
import { conflictOf } from "./dnsConflict";
import type { RecordConflict } from "./dnsConflict";

/** What the provider would do about a name, in words. */
function actionWords(t: T, plan: DnsPlan): string {
  switch (plan.action) {
    case "exists":
      return t("network.appDns.exists");
    case "conflict":
      return t("network.appDns.conflict", { current: plan.current ?? "" });
    default:
      return t("network.appDns.create", { account: plan.account });
  }
}

/**
 * The DNS of an application's names: what each would point at in the account that holds its
 * zone, which already does and which is in the way, and the records Noust made. Making a record
 * is one press; one somebody else made is never replaced until the operator names it. Nothing
 * here runs when the page opens beyond reading the provider.
 */
export function AppDnsSection({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const dns = useQuery(appDnsQuery(domain));
  const [conflict, setConflict] = useState<RecordConflict | null>(null);

  const ensure = useMutation({
    mutationFn: (plan: DnsPlan & { replace?: RecordConflict }) =>
      request("post", "/api/apps/{domain}/dns", {
        params: { domain },
        body: {
          hostname: plan.hostname,
          content: plan.content,
          type: plan.type,
          account: plan.account,
          ...(plan.replace ? { replace_record: plan.replace.record_id, replace_current: plan.replace.current } : {}),
        },
      }),
    onSuccess: (result) => {
      toast.success(t("network.appDns.made", { type: result.type, name: result.hostname, outcome: result.outcome }));
      void queryClient.invalidateQueries({ queryKey: networkKeys.appDns(domain) });
    },
    onError: (error, plan) => {
      const found = conflictOf(error);
      if (found !== null) setConflict(found);
      else reportActionError(t("network.appDns.failed", { name: plan.hostname }), error);
    },
  });

  return (
    <Section title={t("network.appDns.title")} description={t("network.appDns.description", { domain })}>
      {dns.isPending ? (
        <div aria-busy="true">
          <span className="sr-only">{t("network.appDns.loading")}</span>
          <Skeleton className="h-16 rounded-card" />
        </div>
      ) : dns.isError ? (
        <ErrorBlock error={dns.error} title={t("network.appDns.readFailed", { domain })} onRetry={() => void dns.refetch()} retrying={dns.isRefetching} />
      ) : (
        <div className="flex flex-col gap-4">
          {dns.data.plans.length === 0 ? (
            <EmptyState
              variant="inline"
              title={t("network.appDns.noAccount")}
              action={
                <TextLink to="/domains/dns" size="ui">
                  {t("network.appDns.addAccount")}
                </TextLink>
              }
            />
          ) : (
            <ul aria-label={t("network.appDns.plansLabel", { domain })} className="flex flex-col divide-y divide-border rounded-control border border-border">
              {dns.data.plans.map((plan) => (
                <li key={`${plan.hostname}-${plan.type}-${plan.content}`} className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-2">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <Mono truncate>{plan.hostname}</Mono>
                    <span className="text-12 text-fg-muted">
                      <Mono>{`${plan.type} ${plan.content}`}</Mono> · {actionWords(t, plan)}
                    </span>
                  </div>
                  {plan.action === "exists" ? null : plan.action === "conflict" ? (
                    <Button
                      size="sm"
                      onClick={() => {
                        if (plan.record_id && plan.current) setConflict({ hostname: plan.hostname, record_id: plan.record_id, current: plan.current, wanted: plan.content });
                      }}
                    >
                      {t("network.appDns.replace")}
                    </Button>
                  ) : (
                    <Button size="sm" icon={<Plus aria-hidden="true" />} loading={ensure.isPending && ensure.variables?.hostname === plan.hostname} onClick={() => ensure.mutate(plan)}>
                      {t("network.appDns.makeRecord")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {dns.data.unmanaged.length > 0 && dns.data.plans.length > 0 ? (
            <p className="text-13 text-fg-muted">{t("network.appDns.unmanaged", { names: dns.data.unmanaged.join(", ") })}</p>
          ) : null}
          {dns.data.records.length > 0 ? (
            <ul aria-label={t("network.appDns.recordsLabel", { domain })} className="flex flex-col gap-1.5">
              {dns.data.records.map((record) => (
                <li key={record.id} className="flex min-w-0 flex-wrap items-center gap-2 text-13">
                  <Mono truncate>{`${record.type} ${record.hostname} ${record.content}`}</Mono>
                  <Badge>{record.owned ? t("network.appDns.madeByNoust") : t("network.appDns.adopted")}</Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
      <DnsConflictDialog
        conflict={conflict}
        onClose={() => setConflict(null)}
        onReplace={async (found) => {
          const plan = dns.data?.plans.find((candidate) => candidate.hostname === found.hostname);
          if (plan === undefined) return;
          await ensure.mutateAsync({ ...plan, replace: found });
          setConflict(null);
        }}
      />
    </Section>
  );
}
