import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { focusPageTitle } from "../../app/focus";
import { AuthLayout } from "../../components/page/AuthLayout";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { useT } from "../../i18n";

export interface AuthFrameProps {
  title: string;
  description?: ReactNode;
  /** The way back to the screen this one is a step from, above the title. */
  back?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

/** Where the licence is read: the file the repository carries, as About links it. */
const LICENSE_URL = "https://github.com/Perkybeet/noust/blob/main/LICENSE";

/**
 * The notice the AGPL-3.0 asks an interactive interface to display (section 5, d): who holds the
 * copyright, and under which licence the program is, with the licence one link away. It is the
 * same on every screen before the console, which is the first one a person sees.
 */
function LegalLine() {
  const t = useT();
  return (
    <p data-slot="legal" className="text-12 text-pretty text-fg-faint">
      {t.rich("auth.legal.line", {
        license: (
          <ExternalLink key="license" inline href={LICENSE_URL}>
            {t("auth.legal.license")}
          </ExternalLink>
        ),
      })}
    </p>
  );
}

/**
 * T7 for every screen before the console: sign in, the checks after it, the first account,
 * an invitation, a new password, a sealed central. The slab is the same on all of them. Before
 * sign-in the server says nothing of itself but the line its operator chose (`auth.login_label`),
 * never its hostname or version (ENS op.acc.6.7); once a person is in and a check is still owed, it
 * names its machine, which the person is about to trust with a second factor. The title takes focus
 * on arrival. The licence notice closes every one, under the slab.
 */
export function AuthFrame({ title, description, back, children, footer }: AuthFrameProps) {
  const t = useT();
  const { data: session } = useQuery(sessionQuery());

  // Once per screen: the title is where a screen reader starts reading a new one.
  useEffect(() => {
    focusPageTitle();
  }, [title]);

  const label = session?.authenticated === true ? session.hostname : session?.login_label;
  return (
    <AuthLayout
      title={title}
      tagline={t("auth.frame.tagline")}
      legal={<LegalLine />}
      {...(description !== undefined ? { description } : {})}
      {...(back !== undefined ? { back } : {})}
      {...(label !== undefined && label !== null && label !== "" ? { label } : {})}
      {...(footer !== undefined ? { footer } : {})}
    >
      {children}
    </AuthLayout>
  );
}
