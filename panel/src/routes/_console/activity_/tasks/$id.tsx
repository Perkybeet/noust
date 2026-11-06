import { createFileRoute } from "@tanstack/react-router";

import { TaskPage } from "../../../../features/tasks/TaskPage";

/** One task: its steps, who started it and where, and its log as it is written. */
export const Route = createFileRoute("/_console/activity_/tasks/$id")({
  component: TaskRoute,
});

function TaskRoute() {
  const { id } = Route.useParams();
  return <TaskPage id={id} />;
}
