import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldOff, Smartphone } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import {
  authKeys,
  confirmTwoFactor,
  disableTwoFactor,
  enrollTwoFactor,
  regenerateBackupCodes,
  sessionQuery,
  twoFactorQuery,
} from "../../api/queries/auth";
import type { SessionInfo, TwoFactorEnrollment, TwoFactorStatus } from "../../api/queries/auth";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { stateTextClass } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { reportActionError } from "../apps/useAppActions";
import { BackupCodesDialog } from "../auth/BackupCodes";
import { TotpSetup } from "../auth/TotpSetup";
import { splitErrors } from "./formErrors";

/** Low enough that the operator should plan for new ones. */
const FEW_CODES = 2;

function useRefreshTwoFactor() {
  const queryClient = useQueryClient();
  return (): void => {
    void queryClient.invalidateQueries({ queryKey: authKeys.twoFactor });
    // The session answer carries totp_enabled, which decides what "Confirm it's you" asks for.
    void queryClient.invalidateQueries({ queryKey: authKeys.session });
  };
}

// ---------------------------------------------------------------------------------------
// Enrolment

export interface EnrollDialogProps {
  open: boolean;
  enrollment: TwoFactorEnrollment | null;
  onClose: () => void;
}

/**
 * Setting up two-factor authentication: scan the code (or type the key), prove it with a code
 * from the app, then keep the backup codes, which are shown this once. Keyed by the secret, so
 * every setup starts from a clean state; closing it for any reason clears that state again, so
 * the secret and the backup codes never outlive the dialog that showed them.
 *
 * Exported for `TwoFactorSection.test.tsx`, which checks that in isolation from the parent's
 * own key-based reset.
 */
export function EnrollDialog({ open, enrollment, onClose }: EnrollDialogProps) {
  const t = useT();
  const refresh = useRefreshTwoFactor();
  const { data: session } = useQuery(sessionQuery());
  const hostname = session?.hostname ?? t("settings.security.twoFactor.thisServer");
  const [codes, setCodes] = useState<readonly string[] | null>(null);
  const [code, setCode] = useState("");
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  const savedRef = useRef<HTMLDivElement>(null);
  const formId = useId();

  const confirm = useMutation({
    mutationFn: (value: string) => confirmTwoFactor(value),
    onSuccess: (result) => {
      setCodes(result.backup_codes);
      refresh();
    },
    onError: () => {
      codeRef.current?.select();
    },
  });
  const codeError = splitErrors(confirm.error, ["code"], "code");

  const onOpenChange = (next: boolean): void => {
    if (next || confirm.isPending) return;
    if (codes !== null && !saved) {
      // Closing now would lose the only copy of the codes: say so, and point at the box.
      setNudge(true);
      savedRef.current?.querySelector<HTMLElement>("[role=checkbox]")?.focus();
      return;
    }
    if (codes !== null) toast.success(t("settings.security.twoFactor.enroll.turnedOn"));
    // The secret, its QR URI and the backup codes are plaintext in state only while this
    // dialog is open; closing it - cancelled, confirmed, or dismissed however it closes -
    // wipes them, rather than leaving them sitting in memory for as long as this settings
    // page stays mounted.
    setCodes(null);
    setCode("");
    setSaved(false);
    setNudge(false);
    onClose();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const value = code.replace(/\s+/g, "");
    if (value === "" || confirm.isPending) return;
    confirm.mutate(value);
  };

  if (codes !== null) {
    return (
      <BackupCodesDialog
        open={open}
        codes={codes}
        hostname={hostname}
        description={t("settings.security.twoFactor.backupCodes.enabledDescription")}
        saved={saved}
        nudge={nudge}
        savedRef={savedRef}
        onSavedChange={(next) => {
          setSaved(next);
          if (next) setNudge(false);
        }}
        onOpenChange={onOpenChange}
      />
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      initialFocus={codeRef}
      title={t("settings.security.twoFactor.enroll.title")}
      description={t("settings.security.twoFactor.enroll.description")}
      footer={
        <>
          <Button
            disabled={confirm.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={confirm.isPending} disabled={code.trim() === ""}>
            {t("settings.security.twoFactor.enroll.turnOn")}
          </Button>
        </>
      }
    >
      {enrollment !== null ? (
        <TotpSetup uri={enrollment.uri} secret={enrollment.secret}>
          <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-3">
            <Field label={t("settings.security.twoFactor.enroll.codeLabel")} error={codeError.fields.code}>
              <Input
                ref={codeRef}
                mono
                inputMode="numeric"
                autoComplete="one-time-code"
                spellCheck={false}
                maxLength={8}
                placeholder={t("settings.security.twoFactor.enroll.codePlaceholder")}
                value={code}
                onValueChange={(value: string) => {
                  setCode(value);
                  if (confirm.isError) confirm.reset();
                }}
                className="w-40"
              />
            </Field>
            {codeError.form !== null ? (
              <ErrorBlock live compact error={codeError.form} title={t("settings.security.twoFactor.enroll.codeErrorTitle")} />
            ) : null}
          </form>
        </TotpSetup>
      ) : null}
    </Dialog>
  );
}

/**
 * A new set of backup codes: asked for first (the old codes stop working), then the server
 * asks for "Confirm it's you" if the session is not elevated, then the new set, shown once.
 */
function RegenerateCodesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const refresh = useRefreshTwoFactor();
  const { data: session } = useQuery(sessionQuery());
  const hostname = session?.hostname ?? t("settings.security.twoFactor.thisServer");
  const [codes, setCodes] = useState<readonly string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const savedRef = useRef<HTMLDivElement>(null);

  const regenerate = useMutation({
    mutationFn: regenerateBackupCodes,
    onSuccess: (result) => {
      setCodes(result.backup_codes);
      refresh();
    },
  });

  const onOpenChange = (next: boolean): void => {
    if (next || regenerate.isPending) return;
    if (codes !== null && !saved) {
      setNudge(true);
      savedRef.current?.querySelector<HTMLElement>("[role=checkbox]")?.focus();
      return;
    }
    if (codes !== null) toast.success(t("settings.security.twoFactor.regenerate.replaced"));
    onClose();
  };

  if (codes !== null) {
    return (
      <BackupCodesDialog
        open={open}
        codes={codes}
        hostname={hostname}
        description={t("settings.security.twoFactor.backupCodes.regeneratedDescription")}
        saved={saved}
        nudge={nudge}
        savedRef={savedRef}
        onSavedChange={(next) => {
          setSaved(next);
          if (next) setNudge(false);
        }}
        onOpenChange={onOpenChange}
      />
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
      title={t("settings.security.twoFactor.regenerate.title")}
      description={t("settings.security.twoFactor.regenerate.description")}
      footer={
        <>
          <Button
            disabled={regenerate.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={regenerate.isPending}
            onClick={() => {
              regenerate.mutate();
            }}
          >
            {t("settings.security.twoFactor.regenerate.action")}
          </Button>
        </>
      }
    >
      {regenerate.isError ? <ErrorBlock live compact error={regenerate.error} title={t("settings.security.twoFactor.regenerate.failed")} /> : null}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// Turning it off

function DisableDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const refresh = useRefreshTwoFactor();
  const [code, setCode] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const disable = useMutation({
    mutationFn: (value: string) => disableTwoFactor(value),
    onSuccess: () => {
      refresh();
      toast.success(t("settings.security.twoFactor.disable.turnedOff"));
      setCode("");
      onClose();
    },
    onError: () => {
      inputRef.current?.select();
    },
  });
  const errors = splitErrors(disable.error, ["code"], "code");

  const onOpenChange = (next: boolean): void => {
    // Pending includes "Confirm it's you", which opens over this dialog; a press inside it is
    // outside this one and must not close it.
    if (next || disable.isPending) return;
    setCode("");
    disable.reset();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
      initialFocus={inputRef}
      title={t("settings.security.twoFactor.disable.title")}
      description={t("settings.security.twoFactor.disable.description")}
      footer={
        <>
          <Button
            disabled={disable.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="danger" loading={disable.isPending} disabled={code.trim() === ""}>
            {t("settings.security.twoFactor.turnOff")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          const value = code.trim();
          if (value !== "" && !disable.isPending) disable.mutate(value);
        }}
        className="flex flex-col gap-4"
      >
        <Field label={t("settings.security.twoFactor.disable.codeLabel")} error={errors.fields.code}>
          <Input
            ref={inputRef}
            mono
            autoComplete="one-time-code"
            spellCheck={false}
            value={code}
            onValueChange={(value: string) => {
              setCode(value);
              if (disable.isError) disable.reset();
            }}
            className="w-48"
          />
        </Field>
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("settings.security.twoFactor.disable.errorTitle")} /> : null}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------

function Status({ t, status, account }: { t: T; status: TwoFactorStatus; account: boolean }) {
  if (status.enabled) {
    const few = status.backup_codes_remaining <= FEW_CODES;
    return (
      <div className="flex min-w-0 items-start gap-3">
        <Smartphone aria-hidden="true" className="mt-0.5 size-icon-lg shrink-0 text-fg-muted" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-14 font-medium text-fg">{t("settings.security.twoFactor.status.onTitle")}</p>
          <p className="text-13 text-fg-muted">{account ? t("auth.security.factorOnAccount") : t("settings.security.twoFactor.status.onDescription")}</p>
          <p className="mt-1 flex items-center gap-1.5 text-13 text-fg-muted">
            {few ? <ICONS.warning aria-hidden="true" className={cx("size-icon-sm", stateTextClass("warning"))} /> : null}
            <span className="tabular-nums">{t("auth.security.codesLeft", { count: status.backup_codes_remaining })}</span>
          </p>
          {few ? <p className="text-13 text-fg-muted">{t("settings.security.twoFactor.status.codesLow")}</p> : null}
        </div>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 items-start gap-3">
      <ShieldOff aria-hidden="true" className="mt-0.5 size-icon-lg shrink-0 text-fg-muted" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-14 font-medium text-fg">{t("settings.security.twoFactor.status.offTitle")}</p>
        <p className="text-13 text-fg-muted">{account ? t("auth.security.factorOffAccount") : t("settings.security.twoFactor.status.offDescription")}</p>
        {status.pending ? <p className="mt-1 text-13 text-fg-muted">{t("settings.security.twoFactor.status.pending")}</p> : null}
      </div>
    </div>
  );
}

function StatusSkeleton({ t }: { t: T }) {
  return (
    <div aria-busy="true" className="flex gap-3">
      <span className="sr-only">{t("settings.shared.loading", { label: t("settings.security.twoFactor.loadingLabel") })}</span>
      {/* The "On" state's lines, the one a hardened console shows: state, meaning, codes left. */}
      <Skeleton className="mt-0.5 size-5" />
      <div aria-hidden="true" className="flex flex-1 flex-col gap-0.5">
        <div className="flex h-5 items-center">
          <Skeleton className="h-3.5 w-16" />
        </div>
        <div className="flex h-5 items-center">
          <Skeleton className="h-3 w-72 max-w-full" />
        </div>
        <div className="mt-1 flex h-5 items-center">
          <Skeleton className="h-3 w-40" />
        </div>
      </div>
    </div>
  );
}

/**
 * Two-factor authentication with an authenticator app: its state, turning it on or off, and
 * the backup codes. For a person it is their own account's; for the access token, the
 * console's own factor.
 */
export function TwoFactorSection({ session }: { session?: SessionInfo | undefined }) {
  const t = useT();
  const query = useQuery(twoFactorQuery());
  const account = session?.account !== null && session?.account !== undefined;
  const [enrollment, setEnrollment] = useState<TwoFactorEnrollment | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const enroll = useMutation({
    mutationFn: enrollTwoFactor,
    onSuccess: (result) => {
      setEnrollment(result);
      setEnrolling(true);
    },
    onError: (error) => {
      reportActionError(t("settings.security.twoFactor.startFailed"), error);
    },
  });

  const status = query.data;
  let body;
  if (status !== undefined) {
    body = (
      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <Status t={t} status={status} account={account} />
          <div className="flex shrink-0 flex-wrap gap-2">
            {status.enabled ? (
              <>
                <Button size="sm" onClick={() => setRegenerating(true)}>
                  {t("settings.security.twoFactor.newBackupCodes")}
                </Button>
                <Button size="sm" onClick={() => setDisabling(true)}>
                  {t("settings.security.twoFactor.turnOff")}
                </Button>
              </>
            ) : (
              <Button size="sm" loading={enroll.isPending} onClick={() => enroll.mutate()}>
                {t("settings.security.twoFactor.setUp")}
              </Button>
            )}
          </div>
        </div>
      </Card>
    );
  } else if (query.isError) {
    body = (
      <ErrorBlock
        error={query.error}
        title={t("settings.security.twoFactor.loadFailed")}
        onRetry={() => void query.refetch()}
        retrying={query.isRefetching}
      />
    );
  } else {
    body = (
      <Card>
        <StatusSkeleton t={t} />
      </Card>
    );
  }

  return (
    <Section
      title={t("settings.security.twoFactor.title")}
      description={account ? t("auth.security.factorDescriptionAccount") : t("settings.security.twoFactor.description")}
    >
      {body}
      <EnrollDialog
        key={enrollment?.secret ?? "none"}
        open={enrolling}
        enrollment={enrollment}
        onClose={() => {
          setEnrolling(false);
          // Closing loses the only handle on the secret and QR URI held here; forgetting it
          // also means the next "Set up" starts this dialog from a clean key.
          setEnrollment(null);
        }}
      />
      <DisableDialog
        open={disabling}
        onClose={() => {
          setDisabling(false);
        }}
      />
      {/* Mounted per opening: every replacement starts from a clean state. */}
      {regenerating ? (
        <RegenerateCodesDialog
          open
          onClose={() => {
            setRegenerating(false);
          }}
        />
      ) : null}
    </Section>
  );
}
