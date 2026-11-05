import { createFileRoute } from "@tanstack/react-router";

import { TasksPage } from "../../../../features/tasks/TasksPage";

/** Activity > Tasks: what the server has done and is doing, which the task centre in the top bar follows from any page. */
export const Route = createFileRoute("/_console/activity_/tasks/")({
  component: TasksPage,
});
