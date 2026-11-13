import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { AssistantSettingsPage } from "../../../features/settings/assistant/AssistantSettingsPage";
import { validateAssistantSearch } from "../../../features/settings/assistant/search";

/** Settings > AI assistant: what it is, the steps that turn it on and, once it is on, the model and turning it off. */
export const Route = createFileRoute("/_console/settings/assistant")({
  validateSearch: validateAssistantSearch,
  component: AssistantRoute,
});

function AssistantRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <AssistantSettingsPage step={search.step} onStep={(step) => void navigate({ search: step === undefined ? {} : { step }, ...inPlace({}) })} />;
}
