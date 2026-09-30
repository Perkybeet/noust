import type { ReactNode } from "react";

import { CopyButton } from "../../components/ui/CopyButton";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { QrCode } from "../settings/QrCode";
import { groupSecret } from "../settings/security";

export interface TotpSetupProps {
  /** The otpauth:// URI the QR code encodes. */
  uri: string;
  /** The base32 key, for typing by hand. */
  secret: string;
  /** Step 2: the field for the code the app shows, and whatever goes with it. */
  children: ReactNode;
  /** `narrow` stacks the code and the key, for the 400px column of the screens before sign-in. */
  layout?: "wide" | "narrow";
}

/**
 * How the app will list the entry, from the URI itself (`otpauth://totp/Noust:ana@web-1?...`),
 * so what the page says always matches what the phone shows; and the machine in it.
 */
export function otpLabel(uri: string): { account: string; hostname: string } {
  const match = /^otpauth:\/\/totp\/([^?]+)/.exec(uri);
  const raw = match?.[1] ?? "";
  let account: string;
  try {
    account = decodeURIComponent(raw);
  } catch {
    account = raw;
  }
  const at = account.lastIndexOf("@");
  const colon = account.indexOf(":");
  const hostname = at !== -1 ? account.slice(at + 1) : colon !== -1 ? account.slice(colon + 1) : account;
  return { account, hostname };
}

/**
 * Adding this server to an authenticator app: scan the code or type the key, then prove it
 * with a code from the app. The one implementation, in the settings, after sign-in and in an
 * invitation.
 */
export function TotpSetup({ uri, secret, children, layout = "wide" }: TotpSetupProps) {
  const t = useT();
  const { account, hostname } = otpLabel(uri);
  return (
    <ol className="flex flex-col gap-6">
      <li className="flex flex-col gap-3">
        <p className="text-14 font-medium text-fg">{t("settings.security.twoFactor.enroll.step1")}</p>
        <div className={cx("flex flex-col gap-5", layout === "wide" && "sm:flex-row sm:items-start")}>
          <QrCode value={uri} label={t("settings.security.twoFactor.enroll.qrLabel", { hostname })} />
          <div className="flex min-w-0 flex-col gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-13 text-fg-muted">{t("settings.security.twoFactor.enroll.cannotScan")}</span>
              <div className="flex min-w-0 items-center gap-1 rounded-control border border-border bg-bg-sunken py-1 pr-1 pl-3">
                <span data-testid="totp-secret" className="min-w-0 flex-1 text-14 tracking-wide break-words select-all">
                  <Mono tone="default">{groupSecret(secret)}</Mono>
                </span>
                <CopyButton value={secret} label={t("settings.security.twoFactor.enroll.copyKey")} />
              </div>
            </div>
            {/* Stacked, never cut: the account is what the app will list, and must be read whole. */}
            <dl className="flex flex-col gap-2 text-13">
              <div className="flex flex-col gap-0.5">
                <dt className="text-fg-muted">{t("settings.security.twoFactor.enroll.account")}</dt>
                <dd className="break-all">
                  <Mono tone="default">{account}</Mono>
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-fg-muted">{t("settings.security.twoFactor.enroll.type")}</dt>
                <dd className="text-fg">{t("settings.security.twoFactor.enroll.typeValue")}</dd>
              </div>
            </dl>
          </div>
        </div>
      </li>
      <li className="flex flex-col gap-3">
        <p className="text-14 font-medium text-fg">{t("settings.security.twoFactor.enroll.step2")}</p>
        {children}
      </li>
    </ol>
  );
}
