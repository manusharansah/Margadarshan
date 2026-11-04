import { createFileRoute } from "@tanstack/react-router";

const MESSAGES: Record<string, string> = {
  not_invited:
    "This email has not been invited to an organisation. Ask an owner to invite you, then sign in again.",
  domain: "Sign-in is limited to approved email domains.",
  failed: "We could not verify your sign-in. Please try again.",
  expired: "That sign-in link expired. Please start again.",
  cancelled: "Sign-in was cancelled.",
  provider: "The sign-in provider is not reachable right now. Please try again shortly.",
  invalid_email: "The sign-in provider did not return a usable email address.",
};

export const Route = createFileRoute("/login")({
  validateSearch: (s: Record<string, unknown>): { error?: string; next?: string } => ({
    ...(typeof s["error"] === "string" ? { error: s["error"] } : {}),
    ...(typeof s["next"] === "string" ? { next: s["next"] } : {}),
  }),
  head: () => ({ meta: [{ title: "Sign in · MargaAI Nepal" }] }),
  component: Login,
});

function Login() {
  const { error, next } = Route.useSearch();
  const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  return (
    <main className="grid h-full place-items-center px-4">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-panel-border p-6">
        <div className="flex items-center gap-3">
          <svg viewBox="0 0 32 32" className="size-9" aria-hidden>
            <path d="M2 26 L11 10 L17 20 L21 14 L30 26 Z" fill="currentColor" opacity="0.9" />
            <path d="M11 10 L14 15.5 L11 20 L8 15.5 Z" fill="#1c1c1a" opacity="0.35" />
          </svg>
          <div>
            <h1 className="text-[17px] font-semibold">MargaAI Nepal</h1>
            <p className="text-[12px] text-panel-muted">Delivery route planning for the hills</p>
          </div>
        </div>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-panel-border px-3 py-2 text-[13px] text-hazard"
          >
            {MESSAGES[error] ?? MESSAGES["failed"]}
          </p>
        )}
        <a
          href={`/api/auth/login?next=${encodeURIComponent(safeNext)}`}
          className="flex w-full items-center justify-between rounded-md bg-panel-foreground px-4 py-3 text-[15px] font-medium text-panel hover:opacity-90"
        >
          Continue with your work account <span aria-hidden>→</span>
        </a>
        <p className="text-[12px] leading-relaxed text-panel-muted">
          Access is by invitation. We receive your name and verified email address from your account
          provider, and we store nothing else about you. See Account and data after you sign in.
        </p>
      </div>
    </main>
  );
}
