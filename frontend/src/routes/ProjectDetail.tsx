import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { Projects, type Member, type ProjectDetail as Detail, type ProjectRole } from "@/api/projects";
import { useProjectEvents } from "@/api/realtime";
import { Runs, type RunListItem } from "@/api/runs";
import { ExportButton } from "@/components/export/ExportButton";
import {Glass, GlassPill} from "@/components/glass";

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

  useProjectEvents(projectId, (msg) => {
    // Member/role changes ripple into the detail panel; run.created
    // gets caught by the nested RunsPanel which has its own refresh.
    if (msg.type.startsWith("member.") || msg.type === "project.updated") {
      void refresh();
    }
  });

  if (error)
    return <Layout><Glass className="p-6 text-danger">{error}</Glass></Layout>;
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
    <Glass as="section" className="p-8 space-y-3">
      <div className="kicker">Project · your role: {proj.role}</div>
      {editing ? (
        <form onSubmit={save} className="space-y-3">
          <input className="input-glass text-xl font-semibold" value={name}
                 onChange={(e) => setName(e.target.value)} required />
          <textarea className="input-glass min-h-[80px]" value={desc}
                    onChange={(e) => setDesc(e.target.value)} />
          {error && <p className="text-danger text-sm">{error}</p>}
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
          <div className="flex gap-2 pt-2 flex-wrap">
            <Link to={`/projects/${proj.id}/history`} className="button-ghost text-sm">History &amp; restore</Link>
            <ExportButton projectId={proj.id} mode="project" />
            {canEdit && <button onClick={() => setEditing(true)} className="button-ghost text-sm">Edit</button>}
            {canDelete && (
              <button
                onClick={async () => { if (confirm("Delete this project?")) await onDeleted(); }}
                className="button-ghost text-sm text-danger hover:text-red-200"
              >Delete project</button>
            )}
          </div>
        </>
      )}
    </Glass>
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
    <Glass as="section" className="p-6 space-y-3">
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
      {error && <p className="text-danger text-sm">{error}</p>}

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
                  <button onClick={() => remove(m)} className="button-ghost text-xs text-danger">Remove</button>
                </>
              ) : (
                <span className="kicker">{m.role}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Glass>
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

  useProjectEvents(projectId, (msg) => {
    if (msg.type === "run.created") void refresh();
  });

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
    <Glass as="section" className="p-6 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="kicker">Pipeline runs</div>
          <h3 className="text-lg font-medium">MARC uploads &amp; authority matching</h3>
        </div>
        {canUpload && (
          <>
            <input ref={fileRef} type="file"
                   accept=".json,.jsonl,.mrc,.marc,.tsv,.csv,application/json,application/marc,text/tab-separated-values,text/csv"
                   onChange={onPick} hidden />
            <button onClick={() => fileRef.current?.click()}
                    disabled={uploading} className="button-primary">
              {uploading ? "Uploading…" : "Upload MARC"}
            </button>
          </>
        )}
      </div>
      <p className="muted text-sm">
        Accepts <code>.json</code>, <code>.jsonl</code>,{" "}
        <code>.mrc</code> / <code>.marc</code> (binary MARC21),{" "}
        <code>.tsv</code>, and <code>.csv</code>. Tabular uploads use the
        first row as headers — the parser recognises{" "}
        <code>control_number</code> / <code>title</code> /{" "}
        <code>authors</code> / <code>contributors</code> /{" "}
        <code>subjects</code> (multi-value separators: <code>|</code>{" "}
        or <code>;</code>).
      </p>
      {error && <p className="text-danger text-sm">{error}</p>}

      {items === null && <p className="muted text-sm">Loading runs…</p>}
      {items && items.length === 0 && (
        <p className="muted text-sm italic">No runs yet.</p>
      )}
      {items && items.length > 0 && (
        <ul className="divide-y divide-white/5">
          {items.map((r) => (
            <li key={r.id} className="py-3 space-y-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <Link to={`/runs/${r.id}/overview`} className="font-medium hover:text-biu-sky truncate block">
                    {r.name}
                  </Link>
                  <p className="muted text-xs">
                    {r.record_count} record{r.record_count === 1 ? "" : "s"} ·{" "}
                    {r.match_count} match{r.match_count === 1 ? "" : "es"} ·{" "}
                    {new Date(r.created_at).toLocaleString()}
                  </p>
                </div>
                <GlassPill className="px-3 py-1 text-[10px] kicker">{r.status}</GlassPill>
              </div>
              {/* Quick access to each sub-section of the run */}
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
                <StageTile to={`/runs/${r.id}/extraction`}             label="AI Extraction"        hint="NER + genre" />
                <StageTile to={`/runs/${r.id}`}                        label="Authority"            hint="Mazal · VIAF · Wikidata · KIMA" />
                <StageTile to={`/runs/${r.id}/rdf`}                    label="RDF Graph"            hint="HMO ontology" />
                <StageTile to={`/runs/${r.id}/hmo-studio`}             label="HMO Wikibase"         hint="IIIF + crosswalk" />
                <StageTile to={`/runs/${r.id}/wikidata-studio`}        label="Wikidata Studio"      hint="QuickStatements" />
                <StageTile to={`/runs/${r.id}/linked-data-explorer`}   label="Linked Data"          hint="SPARQL · LOD research" />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Glass>
  );
}


function StageTile({ to, label, hint }: { to: string; label: string; hint: string }) {
  return (
    <GlassPill as={Link} className="px-3 py-2 hover:bg-white/[0.06] transition block" to={to}>
      <div className="text-xs font-medium text-ink">{label}</div>
      <div className="muted text-[10px] leading-tight mt-0.5">{hint}</div>
    </GlassPill>
  );
}
