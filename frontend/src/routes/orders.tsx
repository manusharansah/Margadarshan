import { createFileRoute } from "@tanstack/react-router";
import { Card, ErrorNote, Page, when } from "@/components/ui-bits";
import { useOrders } from "@/lib/api";
import { PLAN_LABEL, formatHours, formatInt } from "@/lib/format";

export const Route = createFileRoute("/orders")({ component: OrdersPage });

function OrdersPage() {
  const { data = [], isLoading, error } = useOrders();
  return (
    <Page
      title="Orders"
      intro="Every delivery plan you or your team ran. Plans are kept for 12 months, then deleted."
    >
      {error ? (
        <ErrorNote error={error} />
      ) : isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : data.length === 0 ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          No orders yet. Plan a delivery to see it here.
        </Card>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">When</th>
                <th className="px-4 py-2.5 font-medium">Destination</th>
                <th className="px-4 py-2.5 text-right font-medium">Units</th>
                <th className="px-4 py-2.5 text-right font-medium">Deadline</th>
                <th className="px-4 py-2.5 font-medium">Outcome</th>
                <th className="px-4 py-2.5 font-medium">By</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((o) => (
                <tr key={o.id}>
                  <td className="whitespace-nowrap px-4 py-2.5 text-xs text-muted-foreground">
                    {when(o.created_at)}
                  </td>
                  <td className="px-4 py-2.5 font-medium">{o.destination}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatInt(o.quantity)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{o.deadline_hours} h</td>
                  <td className="px-4 py-2.5">
                    {o.status === "feasible" ? (
                      <span>
                        {PLAN_LABEL[o.plan_type ?? ""] ?? "Plan"} ·{" "}
                        {o.eta_hours != null ? formatHours(o.eta_hours) : ""}
                        {o.p_late != null && o.p_late >= 0.005 && (
                          <span className="ml-1 text-xs text-warning-foreground">
                            {Math.round(o.p_late * 100)}% late risk
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-destructive">Not deliverable</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {o.by ?? "removed user"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </Page>
  );
}
