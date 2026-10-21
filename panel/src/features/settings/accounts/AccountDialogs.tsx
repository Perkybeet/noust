import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ApprovalPendingError } from "../../../api/errors";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { accountKeys, createException, disableAccount, setEmail, setRole } from "./api";
import type { Account } from "./api";
import { samePerson } from "./data";
import { ROLES, incompatible, isRole, roleDescription, roleLabel } from "./roles";
import type { Role } from "./roles";

// ---------------------------------------------------------------------------------------
// The address mail goes to

/**
 * The address an account's invitations and password resets are mailed to. It is only where mail
 * goes: it is never a way to sign in, and an empty one stops mail going there.
 */
export function EmailDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [address, setAddress] = useState(account.email ?? "");
  const formId = useId();
  const change = useMutation({
    mutationFn: () => setEmail(account.username, address.trim()),
    onSuccess: (updated) => {
      toast.success(updated.email ? t("accounts.email.changedToast", { name: updated.username, address: updated.email }) : t("accounts.email.clearedToast", { name: updated.username }));
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApprovalPendingError) onClose();
    },
  });
  const errors = splitErrors(change.error instanceof ApprovalPendingError ? null : change.error, ["email"], "email");

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !change.isPending) onClose();
      }}
      size="md"
      title={t("accounts.email.title", { name: account.username })}
      description={t("accounts.email.description")}
      footer={
        <>
          <Button disabled={change.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={change.isPending} disabled={address.trim() === (account.email ?? "")}>
            {t("accounts.email.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!change.isPending) change.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("accounts.email.failed")} /> : null}
        <Field label={t("accounts.email.label")} description={t("accounts.email.hint")} error={errors.fields.email}>
          <Input
            type="email"
            autoComplete="off"
            maxLength={254}
            value={address}
            onValueChange={(value: string) => {
              setAddress(value);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// Changing a role

export interface RoleDialogProps {
  account: Account;
  accounts: readonly Account[];
  onClose: () => void;
}

/**
 * A new role for an account. One account, one role; the roles one person may not hold in two
 * accounts are said before the change, with the documented exception. Where approvals apply,
 * the change becomes a request another person decides, which the console handles itself.
 */
export function RoleDialog({ account, accounts, onClose }: RoleDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [role, setRoleValue] = useState<Role>(isRole(account.role) ? account.role : "viewer");
  const formId = useId();
  const change = useMutation({
    mutationFn: () => setRole(account.username, role),
    onSuccess: (updated) => {
      toast.success(t("accounts.role.changedToast", { name: updated.username, role: roleLabel(t, updated.role) }));
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      onClose();
    },
    onError: (error) => {
      // Waiting for, or refused by, a second person: the approval dialog already said so.
      if (error instanceof ApprovalPendingError) onClose();
    },
  });
  const errors = splitErrors(change.error instanceof ApprovalPendingError ? null : change.error, ["role"], "role");
  const clashes = samePerson(accounts, account).filter((other) => incompatible(other.role, role));

  const onOpenChange = (next: boolean): void => {
    if (!next && !change.isPending) onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="md"
      title={t("accounts.role.title", { name: account.username })}
      description={t("accounts.role.description")}
      footer={
        <>
          <Button disabled={change.isPending} onClick={() => onOpenChange(false)}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={change.isPending} disabled={role === account.role}>
            {t("accounts.role.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!change.isPending && role !== account.role) change.mutate();
        }}
      >
        <ChoiceCards
          legend={t("accounts.fields.role")}
          options={ROLES.map((value) => ({ value, label: roleLabel(t, value), description: roleDescription(t, value) }))}
          value={role}
          onValueChange={setRoleValue}
        />
        {clashes.length > 0 ? (
          <Notice tone="warning" title={t("accounts.sod.clashTitle")}>
            {t("accounts.sod.clash", { accounts: clashes.map((other) => other.username).join(", ") })}
          </Notice>
        ) : null}
        {errors.fields.role !== undefined ? (
          <InlineMessage tone="error" live>
            <span className="font-medium">{t("accounts.role.failed")}</span> {errors.fields.role}
          </InlineMessage>
        ) : errors.form !== null ? (
          <ErrorBlock live compact error={errors.form} title={t("accounts.role.failed")} />
        ) : null}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// Disabling

export interface DisableDialogProps {
  account: Account;
  onClose: () => void;
}

/**
 * Disabling an account: at once, its sessions and tokens end and it cannot sign in. Nothing is
 * lost - it is enabled again as it was - so one question, with an optional reason for the
 * audit log.
 */
export function DisableDialog({ account, onClose }: DisableDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const formId = useId();
  const disable = useMutation({
    mutationFn: () => disableAccount(account.username, reason.trim() === "" ? null : reason.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !disable.isPending) onClose();
      }}
      size="sm"
      title={t("accounts.disable.title", { name: account.username })}
      description={t("accounts.disable.description")}
      footer={
        <>
          <Button disabled={disable.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={disable.isPending}>
            {t("accounts.disable.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!disable.isPending) disable.mutate();
        }}
      >
        <Field label={t("accounts.disable.reason")} optional description={t("accounts.disable.reasonHint")}>
          <Textarea
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </Field>
        {disable.isError ? <ErrorBlock live compact error={disable.error} title={t("accounts.disable.failed")} /> : null}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// A separation-of-duties exception

export interface ExceptionDialogProps {
  onClose: () => void;
  /** Suggested: a person already holding roles that clash. */
  person?: string;
}

const EXCEPTION_FIELDS = ["person_ref", "reason", "days"] as const;

/**
 * The documented exception to separation of duties (art. 13.3, compensating measures): one
 * person may hold roles that should not go together, for a stated reason and a limited time,
 * shown in the compliance report. For a small installation with a single person in charge.
 */
export function ExceptionDialog({ onClose, person: suggested = "" }: ExceptionDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [person, setPerson] = useState(suggested);
  const [reason, setReason] = useState("");
  const [days, setDays] = useState("90");
  const [tried, setTried] = useState(false);
  const personRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const create = useMutation({
    mutationFn: () => createException({ person_ref: person.trim(), reason: reason.trim(), days: Number(days) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.exceptions });
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      toast.success(t("accounts.exceptions.createdToast", { person: person.trim() }));
      onClose();
    },
  });
  const errors = splitErrors(create.error, EXCEPTION_FIELDS);
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !create.isPending) onClose();
      }}
      size="md"
      initialFocus={personRef}
      title={t("accounts.exceptions.dialogTitle")}
      description={t("accounts.exceptions.dialogDescription")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending}>
            {t("accounts.exceptions.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (create.isPending) return;
          // The button is never off for a missing value: it says what is missing, where, and goes there.
          if (person.trim() === "" || reason.trim() === "") {
            setTried(true);
            focusFirstInvalid(formId);
            return;
          }
          create.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("accounts.exceptions.failed")} /> : null}
        <Field label={t("accounts.fields.person")} error={errors.fields.person_ref ?? (tried && person.trim() === "" ? t("settings.shared.required") : undefined)}>
          <Input
            ref={personRef}
            type="email"
            autoComplete="off"
            maxLength={254}
            value={person}
            onValueChange={(value: string) => {
              setPerson(value);
            }}
          />
        </Field>
        <Field label={t("accounts.exceptions.reason")} description={t("accounts.exceptions.reasonHint")} error={errors.fields.reason ?? (tried && reason.trim() === "" ? t("settings.shared.required") : undefined)}>
          <Textarea
            rows={3}
            maxLength={1000}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </Field>
        <Field label={t("accounts.exceptions.days")} description={t("accounts.exceptions.daysHint")} error={errors.fields.days}>
          <Input
            type="number"
            inputMode="numeric"
            min={1}
            max={366}
            value={days}
            onValueChange={(value: string) => {
              setDays(value);
            }}
            className="w-28"
            suffix={t("accounts.exceptions.daysUnit")}
          />
        </Field>
      </form>
    </Dialog>
  );
}
