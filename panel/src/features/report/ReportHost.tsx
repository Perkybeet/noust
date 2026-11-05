import { Suspense, lazy } from "react";

import { closeReport, useReportRequest } from "./store";

// The dialog, its steps, the zip and the masking load when somebody first asks to report a problem: nobody pays for them on arrival.
const ReportDialog = lazy(() => import("./ReportDialog"));

/** Mounted once by the frame: draws the report of a problem when something has asked for it, and nothing otherwise. */
export function ReportHost() {
  const request = useReportRequest();
  if (request === null) return null;
  return (
    <Suspense fallback={null}>
      <ReportDialog key={request.id} request={request} onClose={closeReport} />
    </Suspense>
  );
}
