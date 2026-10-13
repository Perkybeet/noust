import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { acmeQuery, networkKeys } from "../../api/queries/network";
import type { AcmeSettings } from "../../api/queries/network";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";

interface Draft {
  ca: string;
  email: string;
  server: string;
  kid: string;
  hmac: string;
  register: boolean;
}

function draftOf(settings: AcmeSettings): Draft {
  return { ca: settings.ca, email: settings.email, server: settings.server, kid: settings.eab_kid, hmac: "", register: false };
}

function Form({
  settings,
  onClose,
  onRegister,
}: {
  settings: AcmeSettings;
  onClose: () => void;
  onRegister: (jobId: string) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [draft, setDraft] = useState<Draft>(draftOf(settings));
  const authority = settings.authorities.find((candidate) => candidate.name === draft.ca);
  const needsEab = authority?.needs_eab === true;

  const save = useMutation({
    mutationFn: async () => {
      const saved = await request("put", "/api/certs/acme", {
        body: {
          ca: draft.ca,
          email: draft.email.trim(),
          ...(draft.ca === "custom" ? { acme_server: draft.server.trim() } : {}),
          ...(needsEab ? { eab_kid: draft.kid.trim() } : {}),
          // The key is sent only when one was typed: leaving it empty keeps the one stored.
          ...(needsEab && draft.hmac !== "" ? { eab_hmac_key: draft.hmac } : {}),
        },
      });
      const job = draft.register ? await request("post", "/api/certs/acme/register") : null;
      return { saved, job };
    },
    onSuccess: ({ saved, job }) => {
      queryClient.setQueryData(networkKeys.acme, saved);
      void queryClient.invalidateQueries({ queryKey: networkKeys.acme });
      toast.success(t("network.authority.savedToast", { authority: saved.label }));
      if (job !== null) onRegister(job.job_id);
      onClose();
    },
  });

  const edit = <K extends keyof Draft>(field: K, value: Draft[K]): void => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    save.reset();
  };

  const fields = save.error && isApiError(save.error) ? save.error.fields : null;

  return (
    <form
      id={formId}
      onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
        event.preventDefault();
        save.mutate();
      }}
      noValidate
      className="flex flex-col gap-4"
    >
      <Field label={t("network.authority.caLabel")} nativeLabel={false} description={authority?.trusted === false ? t("network.authority.untrusted") : undefined}>
        <Select
          aria-label={t("network.authority.caLabel")}
          value={draft.ca}
          onValueChange={(next) => edit("ca", next)}
          options={settings.authorities.map((candidate) => ({ value: candidate.name, label: candidate.label }))}
        />
      </Field>
      <Field label={t("network.authority.emailLabel")} description={t("network.authority.emailHelp")} optional error={fields?.["email"] ?? null}>
        <Input type="email" value={draft.email} onValueChange={(next: string) => edit("email", next)} autoComplete="off" spellCheck={false} />
      </Field>
      {draft.ca === "custom" ? (
        <Field label={t("network.authority.serverLabel")} description={t("network.authority.serverHelp")} error={fields?.["acme_server"] ?? null}>
          <Input mono value={draft.server} onValueChange={(next: string) => edit("server", next)} autoComplete="off" spellCheck={false} placeholder="https://acme.example.com/directory" />
        </Field>
      ) : null}
      {needsEab ? (
        <>
          <Notice title={t("network.authority.eabTitle")}>{t("network.authority.eabNote")}</Notice>
          <Field label={t("network.authority.kidLabel")} error={fields?.["eab_kid"] ?? null}>
            <Input mono value={draft.kid} onValueChange={(next: string) => edit("kid", next)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field
            label={t("network.authority.hmacLabel")}
            description={settings.eab_configured ? t("network.authority.hmacStored") : t("network.authority.hmacHelp")}
            error={fields?.["eab_hmac_key"] ?? null}
          >
            <Input type="password" value={draft.hmac} onValueChange={(next: string) => edit("hmac", next)} autoComplete="new-password" spellCheck={false} />
          </Field>
          <Checkbox label={t("network.authority.registerLabel")} description={t("network.authority.registerHelp")} checked={draft.register} onCheckedChange={(next) => edit("register", next)} />
        </>
      ) : null}
      {save.isError && fields === null ? <ErrorBlock live compact error={save.error} title={t("network.authority.failed")} /> : null}
      <div className="flex justify-end gap-2">
        <Button disabled={save.isPending} onClick={onClose}>
          {t("common.confirmDialog.cancel")}
        </Button>
        <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
          {t("network.authority.save")}
        </Button>
      </div>
    </form>
  );
}

/**
 * The authority certificates are ordered from and as whom: Let's Encrypt, its staging (to try a
 * setup without spending the real rate limits, with a certificate browsers do not trust),
 * ZeroSSL or Google (which ask for an external account), or another directory. The HMAC key of an
 * external account goes to the secret store and is never shown again; registering the account now
 * proves it where the operator is looking instead of at the next order.
 */
export function AuthorityDialog({ open, onClose, onRegister }: { open: boolean; onClose: () => void; onRegister: (jobId: string) => void }) {
  const t = useT();
  const settings = useQuery({ ...acmeQuery(), enabled: open });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("network.authority.title")}
      description={t("network.authority.description")}
    >
      {settings.isError ? (
        <ErrorBlock compact error={settings.error} title={t("network.authority.readFailed")} onRetry={() => void settings.refetch()} retrying={settings.isRefetching} />
      ) : settings.data === undefined ? (
        <div aria-busy="true">
          <span className="sr-only">{t("network.authority.loading")}</span>
          <Skeleton className="h-32 rounded-card" />
        </div>
      ) : (
        <Form settings={settings.data} onClose={onClose} onRegister={onRegister} />
      )}
    </Dialog>
  );
}
