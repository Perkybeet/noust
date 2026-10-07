import { createFileRoute } from "@tanstack/react-router";

import { RecipeDetailPage } from "../../../features/catalog/RecipeDetailPage";

/** One recipe in full: what it runs, needs, asks for and tells the operator after the install. */
export const Route = createFileRoute("/_console/catalog/$name")({
  component: RecipeRoute,
});

function RecipeRoute() {
  const { name } = Route.useParams();
  return <RecipeDetailPage name={name} />;
}
