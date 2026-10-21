import { render, screen } from "@testing-library/react";
import { Boxes } from "lucide-react";
import { describe, expect, it } from "vitest";

import { Section } from "../../../components/page/Section";
import { SettingsIconsContext, useSubsectionIcon } from "./settingsApp";

function Page() {
  const headerIcon = useSubsectionIcon("appSettings.nav.automation");
  return (
    <Section title="Automation" {...headerIcon}>
      <p>rows</p>
    </Section>
  );
}

describe("the icon a subsection heads its page with", () => {
  it("is the one the layout was given for its entry in the side list", () => {
    render(
      <SettingsIconsContext.Provider value={new Map([["appSettings.nav.automation", Boxes]])}>
        <Page />
      </SettingsIconsContext.Provider>,
    );
    expect(screen.getByRole("region", { name: "Automation" }).querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("is none while the register gives the page none", () => {
    render(<Page />);
    expect(screen.getByRole("region", { name: "Automation" }).querySelector("svg")).toBeNull();
  });
});
