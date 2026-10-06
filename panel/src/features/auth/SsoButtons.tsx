import { useQuery } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import type { MouseEvent } from "react";

import { ssoProvidersQuery, ssoStartHref } from "../../api/queries/sso";
import type { SsoProvider } from "../../api/queries/sso";
import { buttonClassName } from "../../components/ui/Button";
import { useT } from "../../i18n";

/** The identity providers this server's sign-in offers; none until it says, and none if it cannot. */
export function useSsoProviders(): SsoProvider[] {
  const { data } = useQuery(ssoProvidersQuery());
  return data?.providers ?? [];
}

export interface SsoButtonsProps {
  providers: SsoProvider[];
  /** Where the console goes once signed in; carried through the provider and back. */
  next: string;
  /** The address is locked out: the buttons stay, and say nothing but "not now". */
  disabled?: boolean;
}

/**
 * One "Sign in with ..." button per identity provider. Each is a link, not a button: pressing it
 * leaves the console for the provider and comes back through the server, which is what an
 * address does that opens in a new tab or is copied to a colleague.
 */
export function SsoButtons({ providers, next, disabled = false }: SsoButtonsProps) {
  const t = useT();
  return (
    <>
      {providers.map((provider) => (
        <a
          key={provider.name}
          href={ssoStartHref(provider.name, next)}
          data-variant="secondary"
          aria-disabled={disabled || undefined}
          className={buttonClassName("secondary", "lg", disabled ? "w-full opacity-50" : "w-full")}
          onClick={(event: MouseEvent<HTMLAnchorElement>) => {
            if (disabled) event.preventDefault();
          }}
        >
          <LogIn aria-hidden="true" />
          {t("auth.sso.signInWith", { provider: provider.label })}
        </a>
      ))}
    </>
  );
}
