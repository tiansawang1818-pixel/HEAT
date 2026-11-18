// Hostless probes this path every few seconds with a one-second timeout and
// kills the container after repeated failures. Proxying it to the API made
// liveness depend on the single API worker: while that worker recycled to
// bound memory, or the 0.3 CPU was busy, probes timed out and the host killed
// a healthy container (exit 137 on 18 Nov). The web server answers here
// directly; the supervisor restarts a failed API, and /api/health/ready still
// checks the API and database.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(
    { status: "ok", service: "web", time: new Date().toISOString() },
    { headers: { "Cache-Control": "no-store" } },
  );
}
