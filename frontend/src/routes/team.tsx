import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Btn, Card, ErrorNote, Input, Page, Section, when } from "@/components/ui-bits";
import { send, useAudit, useMe, useTeam } from "@/lib/api";
import { ROLE_RANK, type Role } from "@/lib/types";

export const Route = createFileRoute("/team")({ component: TeamPage });

const ROLES: Role[] = ["viewer", "member", "admin", "owner"];
const ROLE_HELP: Record<Role, string> = {
  viewer: "Can look at the map, inventory and orders.",
  member: "Can also plan deliveries.",
  admin: "Can also manage inventory, closures, people and see the audit log.",
  owner: "Can also change roles and delete the organisation.",
};

function TeamPage() {
  const { data: me } = useMe();
  const rank = me ? ROLE_RANK[me.role] : 0;
  const isAdmin = rank >= ROLE_RANK.admin;
  const isOwner = rank >= ROLE_RANK.owner;
  const { data } = useTeam();
  const { data: audit = [] } = useAudit(isAdmin);
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["team"] });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");

  const invite = useMutation({
    mutationFn: () => send("POST", "/team/invites", { email, role }),
    onSuccess: () => {
      setEmail("");
      refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => send("DELETE", `/team/invites/${id}`),
    onSuccess: refresh,
  });
  const setMemberRole = useMutation({
    mutationFn: (v: { id: string; role: Role }) =>
      send("PUT", `/team/members/${v.id}/role`, { role: v.role }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => send("DELETE", `/team/members/${id}`),
    onSuccess: refresh,
  });
  const allowed = ROLES.filter((r) => (isOwner ? true : r === "viewer" || r === "member"));

  return (
    <Page
      title="Team"
      intro={`People in ${me?.organisation.name ?? "your organisation"}. Anyone who signs in with an invited email joins with the role you choose.`}
    >
      <Section title="Members">
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Person</th>
                <th className="px-4 py-2.5 font-medium">Role</th>
                <th className="px-4 py-2.5 font-medium">Last sign-in</th>
                {isAdmin && <th className="px-4 py-2.5" />}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data?.members.map((m) => (
                <tr key={m.user_id}>
                  <td className="px-4 py-2.5">
                    <span className="font-medium">{m.name || m.email}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{m.email}</span>
                  </td>
                  <td className="px-4 py-2.5">
                    {isOwner ? (
                      <select
                        aria-label={`Role of ${m.email}`}
                        value={m.role}
                        onChange={(e) =>
                          setMemberRole.mutate({ id: m.user_id, role: e.target.value as Role })
                        }
                        className="rounded border border-input bg-card px-1.5 py-1 text-[13px]"
                      >
                        {ROLES.map((r) => (
                          <option key={r}>{r}</option>
                        ))}
                      </select>
                    ) : (
                      m.role
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {when(m.last_login_at)}
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-2.5 text-right">
                      {m.user_id !== me?.user.id &&
                        (isOwner || ROLE_RANK[m.role] < ROLE_RANK.admin) && (
                          <button
                            className="text-xs text-destructive"
                            onClick={() =>
                              confirm(`Remove ${m.email}? They are signed out at once.`) &&
                              remove.mutate(m.user_id)
                            }
                          >
                            Remove
                          </button>
                        )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <ErrorNote error={setMemberRole.error ?? remove.error} />
      </Section>

      {isAdmin && (
        <Section
          title="Invite someone"
          note="They sign in with the same email at their work account provider. Invitations last 14 days."
        >
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              invite.mutate();
            }}
          >
            <label className="space-y-1 text-xs text-muted-foreground">
              Email
              <Input
                className="block w-64"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="space-y-1 text-xs text-muted-foreground">
              Role
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="block rounded-md border border-input bg-card px-2 py-1.5 text-[13px]"
              >
                {allowed.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            <Btn type="submit" disabled={invite.isPending}>
              Send invitation
            </Btn>
          </form>
          <p className="mt-1 text-xs text-muted-foreground">{ROLE_HELP[role]}</p>
          <ErrorNote error={invite.error} />
          {data && data.invites.length > 0 && (
            <Card className="mt-3">
              <ul className="divide-y divide-border text-sm">
                {data.invites.map((i) => (
                  <li key={i.id} className="flex items-center justify-between px-4 py-2.5">
                    <span>
                      {i.email}{" "}
                      <span className="text-xs text-muted-foreground">
                        · {i.role} · expires {when(i.expires_at)}
                      </span>
                    </span>
                    <button
                      className="text-xs text-destructive"
                      onClick={() => revoke.mutate(i.id)}
                    >
                      Revoke
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </Section>
      )}

      {isAdmin && (
        <Section
          title="Audit log"
          note="Sign-ins, inventory and team changes, and orders. It records who did what, never passwords or tokens. Entries are kept for 24 months."
        >
          <Card>
            <table className="w-full text-sm">
              <tbody className="divide-y divide-border">
                {audit.slice(0, 60).map((a, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-muted-foreground">
                      {when(a.ts)}
                    </td>
                    <td className="px-4 py-2 text-xs">{a.user ?? "system"}</td>
                    <td className="px-4 py-2 font-medium">{a.action.replace(/_/g, " ")}</td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{a.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </Section>
      )}
    </Page>
  );
}
