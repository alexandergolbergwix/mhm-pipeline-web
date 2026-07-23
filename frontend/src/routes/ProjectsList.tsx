import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { Projects, type ProjectListItem } from "@/api/projects";
import { ApiError } from "@/api/client";
import { useAuth } from "@/stores/auth";
import {Glass, GlassPill} from "@/components/glass";

export default function ProjectsList() {
  const { user } = useAuth();
  const [items, setItems] = useState<ProjectListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function refresh() {
    try {
      setItems(await Projects.list());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await Projects.create({ name, description });
      setName(""); setDescription(""); setCreating(false);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Create failed");
    }
  }

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-8 space-y-2">
          <div className="kicker">Welcome back</div>
          <div className="flex items-baseline justify-between flex-wrap gap-3">
            <h2 className="text-2xl font-semibold">Your projects</h2>
            <button
              onClick={() => setCreating(true)}
              className="button-primary"
            >
              New project
            </button>
          </div>
          <p className="muted text-sm">
            Each project is its own workspace — MARC uploads, curator review,
            approvals, history, and the people you've invited to
            collaborate on it.
          </p>
        </Glass>

        {creating && (
          <Glass as="section" className="p-6 space-y-3">
            <div className="kicker">New project</div>
            <form onSubmit={onCreate} className="space-y-3">
              <input className="input-glass" placeholder="Project name (e.g. NLI 100 richest)"
                     required value={name} onChange={(e) => setName(e.target.value)} />
              <textarea className="input-glass min-h-[80px]" placeholder="What's this project for?"
                        value={description} onChange={(e) => setDescription(e.target.value)} />
              {error && <p className="text-danger text-sm">{error}</p>}
              <div className="flex gap-2">
                <button type="submit" className="button-primary">Create</button>
                <button type="button" onClick={() => { setCreating(false); setError(null); }} className="button-ghost">
                  Cancel
                </button>
              </div>
            </form>
          </Glass>
        )}

        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {items === null && (
            <p className="muted">Loading…</p>
          )}
          {items?.length === 0 && (
            <Glass as="article" className="p-6 col-span-full">
              <div className="kicker">Empty</div>
              <p className="muted">No projects yet — click <b className="text-ink">New project</b> above to create your first one.</p>
            </Glass>
          )}
          {items?.map((p) => (
            <Glass as={Link} className="p-6 space-y-2 hover:scale-[1.01] transition" key={p.id} to={`/projects/${p.id}`}>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-medium truncate">{p.name}</h3>
                <GlassPill className="px-2.5 py-0.5 text-[10px] kicker">
                  {p.role}
                </GlassPill>
              </div>
              <p className="text-sm muted line-clamp-2 min-h-[2.5em]">
                {p.description || "No description yet."}
              </p>
              <div className="text-xs muted flex items-center justify-between pt-2">
                <span>{p.member_count} member{p.member_count === 1 ? "" : "s"}</span>
                <span>{new Date(p.created_at).toLocaleDateString()}</span>
              </div>
            </Glass>
          ))}
        </section>

        {!items?.length && user?.role === "admin" && (
          <p className="muted text-xs text-center">
            Tip — once you have collaborators, invite them at <Link to="/admin/invites" className="hover:text-ink underline">Invites</Link> first, then add them to specific projects.
          </p>
        )}
      </div>
    </Layout>
  );
}
