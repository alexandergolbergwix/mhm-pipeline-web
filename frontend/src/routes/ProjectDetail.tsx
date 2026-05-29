import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { Projects, type Member, type ProjectDetail as Detail, type ProjectRole } from "@/api/projects";
import { Runs, type RunListItem } from "@/api/runs";

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [proj, setProj] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!projectId) return;
    try {
      setProj(await Projects.get(projectId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, [projectId]);

  if (error)
    return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!proj)
    return <Layout><p className="muted">Loading…</p></Layout>;

  const canEdit = proj.role === "owner" || proj.role === "editor";
  const canManageMembers = proj.role === "owner";

  return (
    <Layout>
      <div className="space-y-6">
        <ProjectHeader proj={proj} canEdit={canEdit} canDelete={proj.role === "owner"}
                       onSaved={refresh}
                       onDeleted={async () => {
                         await Projects.delete(proj.id);
                         navigate("/", { replace: true });
                       }} />

        <RunsPanel projectId={proj.id} canUpload={canEdit} />


        <MembersPanel proj={proj} canManage={canManageMembers} onChanged={refresh} />
      </div>
    </Layout>
  );
}


function ProjectHeader({
  proj, canEdit, canDelete, onSaved, onDeleted,
}: {
  proj: Detail; canEdit: boolean; canDelete: boolean;
  onSaved: () => Promise<void>; onDeleted: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(proj.name);
  const [desc, setDesc] = useState(proj.description);
  const [error, setError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await Projects.update(proj.id, { name, description: desc });
      setEditing(false);
      await onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Save failed");
    }
  }

  return (
    <section className="glass p-8 space-y-3">
      <div className="kicker">Project · your role: {proj.role}</div>
      {editing ? (
        <form onSubmit={save} className="space-y-3">
          <input className="input-glass text-xl font-semibold" value={name}
                 onChange={(e) => setName(e.target.value)} required />
          <textarea className="input-glass min-h-[80px]" value={desc}
                    onChange={(e) => setDesc(e.target.value)} />
          {error && <p className="text-red-300 text-sm">{error}</p>}
          <div className="flex gap-2">
            <button type="submit" className="button-primary">Save</button>
            <button type="button" onClick={() => { setEditing(false); setError(null); }} className="button-ghost">Cancel</button>
          </div>
        </form>
      ) : (
        <>
          <h2 className="text-3xl font-semibold">{proj.name}</h2>
          <p className="muted leading-relaxed max-w-2xl">
            {proj.description || <span className="italic opacity-70">No description.</span>}
          </p>
          {(canEdit || canDelete) && (
            <div className="flex gap-2 pt-2">
              {canEdit && <button onClick={() => setEditing(true)} className="button-ghost text-sm">Edit</button>}
              {canDelete && (
                <button
                  onClick={async () => { if (confirm("Delete this project?")) await onDeleted(); }}
                  className="button-ghost text-sm text-red-300 hover:text-red-200"
                >Delete project</button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}


function MembersPanel({
  proj, canManage, onChanged,
}: {
  proj: Detail; canManage: boolean; onChanged: () => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<ProjectRole>("editor");
  const [error, setError] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await Projects.members.add(proj.id, { email, role });
      setEmail("");
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Add failed");
    }
  }
  async function setMemberRole(m: Member, r: ProjectRole) {
    try {
      await Projects.members.update(proj.id, m.user_id, r);
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Update failed");
    }
  }
  async function remove(m: Member) {
    try {
      await Projects.members.remove(proj.id, m.user_id);
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Remove failed");
    }
  }

  return (
    <section className="glass p-6 space-y-3">
      <div>
        <div className="kicker">Collaboration</div>
        <h3 className="text-lg font-medium">Members ({proj.members.length})</h3>
      </div>

      {canManage && (
        <form onSubmit={add} className="grid grid-cols-1 md:grid-cols-[1fr_140px_auto] gap-2 items-center">
          <input className="input-glass" placeholder="user@example.com" type="email" required
                 value={email} onChange={(e) => setEmail(e.target.value)} />
          <select className="input-glass" value={role} onChange={(e) => setRole(e.target.value as ProjectRole)}>
            <option value="viewer">viewer</option>
            <option value="editor">editor</option>
          </select>
          <button type="submit" className="button-primary">Add member</button>
        </form>
      )}
      {error && <p className="text-red-300 text-sm">{error}</p>}

      <ul className="divide-y divide-white/5">
        {proj.members.map((m) => (
          <li key={m.user_id} className="py-3 flex items-center justify-between gap-3">
            <div>
              <p className="text-sm">
                {m.name} <span className="muted">· {m.email}</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              {m.is_owner ? (
                <span className="kicker">owner</span>
              ) : canManage ? (
                <>
                  <select
                    value={m.role}
                    onChange={(e) => setMemberRole(m, e.target.value as ProjectRole)}
                    className="input-glass !py-1 text-xs"
                  >
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </select>
                  <button onClick={() => remove(m)} className="button-ghost text-xs text-red-300">Remove</button>
                </>
              ) : (
                <span className="kicker">{m.role}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}


function RunsPanel({ projectId, canUpload }: { projectId: string; canUpload: boolean }) {
  const [items, setItems] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      setItems(await Runs.listForProject(projectId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, [projectId]);

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true); setError(null);
    try {
      await Runs.create(projectId, file);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <section className="glass p-6 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="kicker">Pipeline runs</div>
          <h3 className="text-lg font-medium">MARC uploads &amp; authority matching</h3>
        </div>
        {canUpload && (
          <>
            <input ref={fileRef} type="file" accept=".json,.jsonl,application/json"
                   onChange={onPick} hidden />
            <button onClick={() => fileRef.current?.click()}
                    disabled={uploading} className="button-primary">
              {uploading ? "Uploading…" : "Upload MARC"}
            </button>
          </>
        )}
      </div>
      <p className="muted text-sm">
        Upload a <code>marc_extracted.json</code> or JSONL file. The
        authority matcher returns candidate matches you can review and
        approve in the next step. (Real Mazal/VIAF/Wikidata adapters
        plug into <code>app.pipeline.authority</code>; current build
        ships the placeholder.)
      </p>
      {error && <p className="text-red-300 text-sm">{error}</p>}

      {items === null && <p className="muted text-sm">Loading runs…</p>}
      {items && items.length === 0 && (
        <p className="muted text-sm italic">No runs yet.</p>
      )}
      {items && items.length > 0 && (
        <ul className="divide-y divide-white/5">
          {items.map((r) => (
            <li key={r.id} className="py-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <Link to={`/runs/${r.id}`} className="font-medium hover:text-biu-sky truncate block">
                  {r.name}
                </Link>
                <p className="muted text-xs">
                  {r.record_count} record{r.record_count === 1 ? "" : "s"} ·{" "}
                  {r.match_count} match{r.match_count === 1 ? "" : "es"} ·{" "}
                  {new Date(r.created_at).toLocaleString()}
                </p>
              </div>
              <span className="glass-pill px-3 py-1 text-[10px] kicker">{r.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
