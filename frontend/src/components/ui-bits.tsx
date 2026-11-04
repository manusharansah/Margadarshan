import type { ReactNode } from "react";

export const Page = ({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: string;
  children: ReactNode;
}) => (
  <div className="h-full overflow-y-auto">
    <div className="mx-auto max-w-4xl space-y-8 p-4 md:p-8">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {intro && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{intro}</p>}
      </div>
      {children}
    </div>
  </div>
);

export const Section = ({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: ReactNode;
}) => (
  <section aria-label={title}>
    <h2 className="text-sm font-semibold">{title}</h2>
    {note && <p className="mb-2 mt-0.5 text-xs text-muted-foreground">{note}</p>}
    <div className={note ? "" : "mt-2"}>{children}</div>
  </section>
);

export const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`overflow-x-auto rounded-xl border border-border bg-card ${className}`}>
    {children}
  </div>
);

export const Btn = ({
  kind = "primary",
  ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "ghost" | "danger" }) => (
  <button
    {...p}
    className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-opacity disabled:opacity-40 ${
      kind === "primary"
        ? "bg-panel text-panel-foreground hover:opacity-90"
        : kind === "danger"
          ? "border border-destructive/50 text-destructive hover:bg-destructive/5"
          : "border border-border hover:bg-muted"
    } ${p.className ?? ""}`}
  />
);

export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input
    {...p}
    className={`rounded-md border border-input bg-card px-2.5 py-1.5 text-[13px] outline-none focus:border-ring focus:ring-2 focus:ring-ring/20 ${p.className ?? ""}`}
  />
);

export const ErrorNote = ({ error }: { error: unknown }) =>
  error ? (
    <p role="alert" className="text-[13px] text-destructive">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
