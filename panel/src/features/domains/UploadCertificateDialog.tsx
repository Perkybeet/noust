import { Input as BaseInput } from "@base-ui/react/input";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { ChangeEvent, SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { certKeys } from "../../api/queries/certs";
import { networkKeys } from "../../api/queries/network";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";

/** What the server keeps a certificate under: lower case letters, digits, dots, dashes, underscores. */
const NAME = /^[a-z0-9][a-z0-9._-]{0,62}$/;

const FILE_CLASS =
  "max-w-full rounded-control text-13 text-fg-muted file:mr-3 file:h-control-sm file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover";

/** A text control that can also be filled from a file chosen in the browser: the PEM is read here, not uploaded as a file. */
function PemField({
  label,
  description,
  error,
  value,
  onChange,
  optional = false,
  fileLabel,
}: {
  label: string;
  description?: string;
  error: string | null;
  value: string;
  onChange: (next: string) => void;
  optional?: boolean;
  fileLabel: string;
}) {
  const read = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file === undefined) return;
    void file.text().then(onChange);
    event.target.value = "";
  };
  return (
    <div className="flex flex-col gap-2">
      <Field label={label} {...(description !== undefined ? { description } : {})} error={error} optional={optional}>
        <Textarea mono rows={5} wrap="off" value={value} onChange={(event) => onChange(event.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      {/* Outside the field, so it keeps its own name instead of the text area's. */}
      <BaseInput type="file" aria-label={fileLabel} accept=".pem,.crt,.cer,.key,text/plain" onChange={read} className={FILE_CLASS} />
    </div>
  );
}

/**
 * Uploads a certificate and its key: a Cloudflare Origin Certificate, a corporate CA's, a purchased
 * one. The server checks before it writes anything that the key is the certificate's, that it has
 * no passphrase, and that the certificate has not expired; the key is kept in a file only root
 * reads and is never sent back. An upload is never renewed here.
 */
export function UploadCertificateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState("");
  const [certificate, setCertificate] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [chain, setChain] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: () => request("post", "/api/certs/uploaded", { body: { name, certificate, private_key: privateKey, chain } }),
    onSuccess: (kept) => {
      void queryClient.invalidateQueries({ queryKey: certKeys.all });
      void queryClient.invalidateQueries({ queryKey: networkKeys.uploaded });
      toast.success(t("network.upload.uploadedToast", { name: kept.name }), { description: kept.names.join(", ") });
      close(true);
    },
  });

  function close(settled = false): void {
    if (upload.isPending && !settled) return;
    onClose();
    setName("");
    setCertificate("");
    setPrivateKey("");
    setChain("");
    setProblem(null);
    upload.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!NAME.test(name)) {
      setProblem(t("network.upload.nameInvalid"));
      return;
    }
    setProblem(null);
    upload.mutate();
  };

  const fields = upload.error && isApiError(upload.error) ? upload.error.fields : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.upload.title")}
      description={t("network.upload.description")}
      size="lg"
      footer={
        <>
          <Button disabled={upload.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={upload.isPending}>
            {t("network.upload.action")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("common.words.name")} description={t("network.upload.nameHelp")} error={problem ?? fields?.["name"] ?? null}>
          <Input mono value={name} onValueChange={setName} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={63} />
        </Field>
        <PemField
          label={t("network.upload.certificateLabel")}
          description={t("network.upload.certificateHelp")}
          error={fields?.["certificate"] ?? null}
          value={certificate}
          onChange={setCertificate}
          fileLabel={t("network.upload.certificateFile")}
        />
        <PemField
          label={t("network.upload.keyLabel")}
          description={t("network.upload.keyHelp")}
          error={fields?.["private_key"] ?? null}
          value={privateKey}
          onChange={setPrivateKey}
          fileLabel={t("network.upload.keyFile")}
        />
        <PemField
          label={t("network.upload.chainLabel")}
          description={t("network.upload.chainHelp")}
          error={fields?.["chain"] ?? null}
          value={chain}
          onChange={setChain}
          optional
          fileLabel={t("network.upload.chainFile")}
        />
        {upload.isError && fields === null ? <ErrorBlock live compact error={upload.error} title={t("network.upload.failed")} /> : null}
      </form>
    </Dialog>
  );
}
