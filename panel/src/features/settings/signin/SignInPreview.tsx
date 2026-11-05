import type { ReactNode } from "react";

import { AuthLayout } from "../../../components/page/AuthLayout";
import { useT } from "../../../i18n";

/**
 * The sign-in screen as it will be seen, drawn by the very template the screen is (`AuthLayout`), with
 * a picture in its slot: an illustration of the screen, so it is hidden from assistive technology and
 * inert (nothing in it is pressed or focused), and its form is the shape of one in neutral blocks, not a
 * form that could be filled in. Without `art` the slot holds Noust's own drawing, which is what the
 * screen shows until a picture is saved.
 */
export function SignInPreview({ art }: { art?: ReactNode }) {
  const t = useT();
  return (
    <div aria-hidden="true" inert className="pointer-events-none overflow-hidden rounded-card border border-border select-none">
      <AuthLayout
        as="div"
        title={t("auth.area")}
        description={t("auth.account.description")}
        tagline={t("auth.frame.tagline")}
        {...(art !== undefined ? { art } : {})}
      >
        <div className="flex flex-col gap-4">
          <div className="h-control-md rounded-control border border-border bg-bg-sunken" />
          <div className="h-control-md rounded-control border border-border bg-bg-sunken" />
          <div className="h-control-lg rounded-control bg-bg-sunken-deep" />
        </div>
      </AuthLayout>
    </div>
  );
}
