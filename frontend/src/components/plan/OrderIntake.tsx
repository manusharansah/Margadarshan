import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { parseOrder } from "@/lib/api";
import type { ParsedOrder, Place } from "@/lib/types";

interface Props {
  enabled: boolean;
  onFill: (f: {
    destination: Place | null;
    quantity: number | null;
    deadline: number | null;
  }) => void;
}

/** AI order intake: type the order in plain language; the form is pre-filled for the dealer to confirm. */
export function OrderIntake({ enabled, onFill }: Props) {
  const [text, setText] = useState("");
  const m = useMutation({
    mutationFn: parseOrder,
    onSuccess: (r: ParsedOrder) =>
      onFill({ destination: r.destination, quantity: r.quantity, deadline: r.deadline_hours }),
  });
  const r = m.data;

  return (
    <div className="mb-4 rounded-lg border border-panel-border p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] text-panel-muted">Describe the order</span>
        <span className="rounded-full border border-panel-border px-1.5 py-0.5 text-[10px] font-semibold text-panel-muted">
          AI
        </span>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        maxLength={500}
        disabled={!enabled}
        aria-label="Describe the order"
        placeholder={
          enabled
            ? "e.g. 500 units to Dhulikhel by tomorrow 5 pm"
            : "AI intake is off — add a Gemma key to enable"
        }
        className="w-full resize-none bg-transparent text-[14px] text-panel-foreground outline-none placeholder:text-panel-muted disabled:opacity-50"
      />
      <button
        type="button"
        disabled={!enabled || m.isPending || !text.trim()}
        onClick={() => m.mutate(text)}
        className="mt-1 flex w-full items-center justify-between rounded-md border border-panel-foreground px-3 py-1.5 text-[13px] font-medium text-panel-foreground transition-colors hover:bg-panel-foreground hover:text-panel disabled:opacity-40"
      >
        {m.isPending ? "Reading…" : "Fill the form"}
        <span aria-hidden>→</span>
      </button>

      {m.error && (
        <p role="alert" className="mt-2 text-[12px] text-hazard">
          {m.error.message}
        </p>
      )}
      {r && (
        <div
          className="mt-2 space-y-1 text-[12px] leading-snug text-panel-muted"
          aria-label="What the AI understood"
        >
          <p>
            Understood: {r.quantity ?? "?"} units ·{" "}
            {r.destination?.name ?? r.destination_text ?? "?"} ·{" "}
            {r.deadline_hours != null ? `${r.deadline_hours} h` : "no deadline"}
          </p>
          {r.destination === null && r.destination_candidates.length > 1 && (
            <div className="flex flex-wrap gap-1.5">
              <span>Which place?</span>
              {r.destination_candidates.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="rounded border border-panel-border px-1.5 py-0.5 text-panel-foreground hover:bg-panel-border"
                  onClick={() =>
                    onFill({ destination: c, quantity: r.quantity, deadline: r.deadline_hours })
                  }
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}
          {r.missing.length > 0 && (
            <p className="text-hazard">Still needed: {r.missing.join(", ")}.</p>
          )}
          {r.issues.map((i) => (
            <p key={i} className="text-hazard">
              {i}
            </p>
          ))}
          {r.notes && <p>{r.notes}</p>}
        </div>
      )}
    </div>
  );
}
