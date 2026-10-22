import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldOff } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { appClientCertsQuery, networkKeys } from "../../../api/queries/network";
import type { ClientCertificate, IssuedClientCertificate } from "../../../api/queries/network";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field, GroupError } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { RowList } from "../../../components/ui/RowList";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { downloadBase64, downloadText } from "../../../lib/clipboard";
import type { AccessForm } from "./useAccessForm";
import { SETTINGS_ICONS } from "./objectIcons";

/** What the server accepts as the name of a certificate (`noust.managers.database.tls_authority`). */
const NAME = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$/;
const MAX_DAYS = 825;

/** Whether a certificate gets a visitor in now, as the server judges it. */
function usable(certificate: ClientCertificate, now: number): boolean {
  return !certificate.revoked && Date.parse(certificate.not_after) > now;
}

interface IssueDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Making a certificate: a name and how long it lasts. What comes back holds the private key, so it
 * is shown once, in this dialog, with the password of the bundle; closing the dialog forgets all of
 * it, because the server kept none.
 */
function IssueDialog({ domain, open, onOpenChange }: IssueDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState("");
  const [days, setDays] = useState("365");
  const [problem, setProblem] = useState<{ name?: string; days?: string }>({});

  const issue = useMutation({
    mutationFn: (): Promise<IssuedClientCertificate> =>
      request("post", "/api/apps/{domain}/client-certs", { params: { domain }, body: { name: name.trim(), days: Number(days) } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.appClientCerts(domain) });
    },
  });

  function close(next: boolean): void {
    if (!next && issue.isPending) return;
    onOpenChange(next);
    if (!next) {
      setName("");
      setDays("365");
      setProblem({});
      issue.reset();
    }
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: { name?: string; days?: string } = {};
    if (!NAME.test(name.trim())) found.name = t("network.access.clients.nameHelp");
    if (!/^\d+$/.test(days.trim()) || Number(days) < 1 || Number(days) > MAX_DAYS) found.days = t("network.access.clients.daysHelp", { max: MAX_DAYS });
    setProblem(found);
    if (Object.keys(found).length === 0) issue.mutate();
  };

  const fields = issue.error && isApiError(issue.error) ? issue.error.fields : undefined;
  const nameError = problem.name ?? fields?.["name"] ?? null;
  const daysError = problem.days ?? fields?.["days"] ?? null;
  const made = issue.data;
  const file = made === undefined ? "" : made.certificate.name;

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={made === undefined ? t("network.access.clients.issueTitle") : t("network.access.clients.issuedTitle", { name: file })}
      description={made === undefined ? t("network.access.clients.issueDescription", { domain }) : t("network.access.clients.issuedDescription")}
      footer={
        made === undefined ? (
          <>
            <Button disabled={issue.isPending} onClick={() => close(false)}>
              {t("common.confirmDialog.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={issue.isPending}>
              {t("network.access.clients.issue")}
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => close(false)}>
            {t("common.words.done")}
          </Button>
        )
      }
    >
      {made === undefined ? (
        <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
          <Field label={t("common.words.name")} description={t("network.access.clients.nameHelp")} error={nameError}>
            <Input mono value={name} onValueChange={setName} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={64} />
          </Field>
          <Field label={t("network.access.clients.days")} description={t("network.access.clients.daysHelp", { max: MAX_DAYS })} error={daysError}>
            <Input mono inputMode="numeric" value={days} onValueChange={setDays} autoComplete="off" />
          </Field>
          {issue.isError && nameError === null && daysError === null ? (
            <ErrorBlock live compact error={issue.error} title={t("network.access.clients.issueFailed", { name: name.trim() })} />
          ) : null}
        </form>
      ) : (
        <div className="flex flex-col gap-4">
          <Notice tone="warning" title={t("network.access.clients.onlyNow")}>
            {t("network.access.clients.onlyNowHelp")}
          </Notice>
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("network.access.clients.password")}</p>
            <CopyValue value={made.password} label={t("network.access.clients.password")} name={t("network.access.clients.password")} />
            <p className="text-13 text-fg-muted">{t("network.access.clients.passwordHelp")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button icon={<SETTINGS_ICONS.install aria-hidden="true" />} variant="primary" onClick={() => downloadBase64(`${file}.p12`, made.bundle, "application/x-pkcs12")}>
              {t("network.access.clients.downloadBundle")}
            </Button>
            <Button icon={<SETTINGS_ICONS.install aria-hidden="true" />} onClick={() => downloadText(`${file}.key`, made.key_pem, "application/x-pem-file")}>{t("network.access.clients.downloadKey")}</Button>
            <Button icon={<SETTINGS_ICONS.install aria-hidden="true" />} onClick={() => downloadText(`${file}.crt`, made.certificate_pem, "application/x-pem-file")}>
              {t("network.access.clients.downloadCertificate")}
            </Button>
          </div>
          <p className="text-13 text-fg-muted">
            {t.rich("network.access.clients.serial", { serial: <Mono>{made.certificate.serial}</Mono> })}
          </p>
        </div>
      )}
    </Dialog>
  );
}

/**
 * Client certificates (mutual TLS): the site answers only a visitor whose browser presents a
 * certificate this application's own authority signed. Making and revoking one is applied at
 * once, each on its own, because the private key is handed over a single time and a revocation
 * is not something to leave half-saved; whether the site asks for one is saved with the form.
 */
export function ClientCertsCard({ form, domain }: { form: AccessForm; domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const list = useQuery(appClientCertsQuery(domain));
  const [issuing, setIssuing] = useState(false);
  const [revoking, setRevoking] = useState<ClientCertificate | null>(null);
  // Fixed for the life of the card: a certificate that expires while it is open shows on the next read.
  const [now] = useState(() => Date.now());
  const { draft, edit } = form;
  const certificates = list.data?.certificates ?? [];
  const valid = certificates.filter((certificate) => usable(certificate, now));
  const enabled = list.data?.enabled === true;
  const lastValid = (certificate: ClientCertificate): boolean => enabled && valid.length === 1 && usable(certificate, now);

  return (
    <Card
      icon={SETTINGS_ICONS.certificate}
      title={t("network.access.clients.title")}
      description={t("network.access.clients.description")}
      loading={list.isPending}
      actions={
        <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setIssuing(true)}>
          {t("network.access.clients.issue")}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <Checkbox
          label={t("network.access.clients.enable")}
          description={draft.client_certs && valid.length === 0 ? t("network.access.clients.enableNone") : t("network.access.clients.enableHelp")}
          checked={draft.client_certs}
          onCheckedChange={(checked) => edit("client_certs", checked)}
        />
        {form.errorOf("client_certs") !== undefined ? <GroupError id="client-certs-refused">{form.errorOf("client_certs")}</GroupError> : null}
        {list.isError ? <ErrorBlock compact error={list.error} title={t("network.access.clients.readFailed")} /> : null}
        {list.data !== undefined ? (
          certificates.length === 0 ? (
            <EmptyState variant="inline" title={t("network.access.clients.empty")} />
          ) : (
            <RowList aria-label={t("network.access.clients.listLabel", { domain })}>
              {certificates.map((certificate) => (
                <RowList.Item
                  key={certificate.serial}
                  actions={
                    <>
                      {certificate.revoked ? (
                        <StatusPill state="failed" size="sm" label={t("network.access.clients.revoked")} />
                      ) : usable(certificate, now) ? (
                        <StatusPill state="running" size="sm" label={t("network.access.clients.valid")} />
                      ) : (
                        <StatusPill state="warning" size="sm" label={t("network.access.clients.expired")} />
                      )}
                      {certificate.revoked ? null : (
                        <IconButton label={`${t("common.words.revoke")}: ${certificate.name}`} icon={<ShieldOff />} size="sm" onClick={() => setRevoking(certificate)} />
                      )}
                    </>
                  }
                >
                  <span className="flex min-w-0 flex-col">
                    <Mono truncate>{certificate.name}</Mono>
                    <span className="truncate text-12 text-fg-muted">
                      {t("network.access.clients.expires", { date: certificate.not_after.slice(0, 10) })}
                      {certificate.issued_by !== undefined && certificate.issued_by !== null ? ` · ${certificate.issued_by}` : ""}
                    </span>
                  </span>
                </RowList.Item>
              ))}
            </RowList>
          )
        ) : null}
      </div>
      <IssueDialog domain={domain} open={issuing} onOpenChange={setIssuing} />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.access.clients.revokeTitle", { name: revoking?.name ?? "" })}
        description={
          <>
            {t("network.access.clients.revokeDescription", { name: revoking?.name ?? "", domain })}
            {revoking !== null && lastValid(revoking) ? <> {t("network.access.clients.revokeLast")}</> : null}
          </>
        }
        actionLabel={t("common.words.revoke")}
        onConfirm={async () => {
          if (revoking === null) return;
          await request("delete", "/api/apps/{domain}/client-certs/{serial}", {
            params: { domain, serial: revoking.serial },
            query: { accept_lockout: lastValid(revoking) },
          });
          void queryClient.invalidateQueries({ queryKey: networkKeys.appClientCerts(domain) });
          announce(t("network.access.clients.revokedToast", { name: revoking.name }));
        }}
      />
    </Card>
  );
}
