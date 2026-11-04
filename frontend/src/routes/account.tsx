import { createFileRoute } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Btn, Card, ErrorNote, Input, Page, Section } from "@/components/ui-bits";
import { fetchExport, send, useMe } from "@/lib/api";
import { ROLE_RANK } from "@/lib/types";

export const Route = createFileRoute("/account")({ component: AccountPage });

function AccountPage() {
  const { data: me } = useMe();
  const [confirm, setConfirm] = useState("");
  const [orgConfirm, setOrgConfirm] = useState("");

  const download = useMutation({
    mutationFn: async () => {
      const blob = new Blob([JSON.stringify(await fetchExport(), null, 2)], {
        type: "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "my-marga-data.json";
      a.click();
      URL.revokeObjectURL(a.href);
    },
  });
  const delMe = useMutation({
    mutationFn: () => send("DELETE", "/me", { confirm }),
    onSuccess: () => window.location.assign("/login"),
  });
  const delOrg = useMutation({
    mutationFn: () => send("DELETE", "/org", { confirm: orgConfirm }),
    onSuccess: () => window.location.assign("/login"),
  });

  if (!me) return null;
  return (
    <Page
      title="Account and data"
      intro="What we hold about you, and how to take it with you or remove it."
    >
      <Section title="You">
        <Card className="divide-y divide-border text-sm">
          {(
            [
              ["Name", me.user.name || "Not provided"],
              ["Email", me.user.email],
              ["Organisation", `${me.organisation.name} (${me.role})`],
            ] as const
          ).map(([k, v]) => (
            <div key={k} className="flex gap-6 px-4 py-3">
              <span className="w-28 shrink-0 text-muted-foreground">{k}</span>
              <span>{v}</span>
            </div>
          ))}
        </Card>
      </Section>

      <Section
        title="What is stored"
        note="Your name and verified email from your sign-in provider, which organisation you belong to and your role, the delivery plans you ran, and an audit entry for each sign-in or change you made. We do not store passwords, provider tokens or IP addresses. Your browser session is stored only as a hash."
      >
        <Btn kind="ghost" onClick={() => download.mutate()} disabled={download.isPending}>
          Download my data
        </Btn>
        <ErrorNote error={download.error} />
      </Section>

      <Section
        title="Delete my account"
        note="You are removed and signed out. Delivery plans you ran stay with your organisation, without your name. If you are the only owner of a team, transfer ownership first."
      >
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="Type DELETE to confirm"
            placeholder="Type DELETE"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          <Btn
            kind="danger"
            disabled={confirm !== "DELETE" || delMe.isPending}
            onClick={() => delMe.mutate()}
          >
            Delete my account
          </Btn>
        </div>
        <ErrorNote error={delMe.error} />
      </Section>

      {ROLE_RANK[me.role] >= ROLE_RANK.owner && (
        <Section
          title="Delete the organisation"
          note="Removes every person, warehouse, order, closure and audit entry of this organisation. This cannot be undone."
        >
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="Type the organisation name to confirm"
              placeholder={me.organisation.name}
              value={orgConfirm}
              onChange={(e) => setOrgConfirm(e.target.value)}
              className="w-64"
            />
            <Btn
              kind="danger"
              disabled={orgConfirm !== me.organisation.name || delOrg.isPending}
              onClick={() => delOrg.mutate()}
            >
              Delete organisation
            </Btn>
          </div>
          <ErrorNote error={delOrg.error} />
        </Section>
      )}
    </Page>
  );
}
