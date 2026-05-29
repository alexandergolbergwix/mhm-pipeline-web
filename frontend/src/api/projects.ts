import { api } from "@/api/client";

export type ProjectRole = "owner" | "editor" | "viewer";

export interface ProjectListItem {
  id: string;
  name: string;
  description: string;
  role: ProjectRole;
  member_count: number;
  created_at: string;
}

export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: ProjectRole;
  is_owner: boolean;
}

export interface ProjectDetail {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  created_at: string;
  role: ProjectRole;
  members: Member[];
}

export const Projects = {
  list:    ()  => api.get<ProjectListItem[]>("/projects"),
  get:     (id: string) => api.get<ProjectDetail>(`/projects/${id}`),
  create:  (body: { name: string; description?: string }) =>
    api.post<ProjectListItem>("/projects", body),
  update:  (id: string, body: { name?: string; description?: string }) =>
    api.patch<ProjectDetail>(`/projects/${id}`, body),
  delete:  (id: string) => api.del<void>(`/projects/${id}`),
  members: {
    list:   (id: string) => api.get<Member[]>(`/projects/${id}/members`),
    add:    (id: string, body: { email: string; role: ProjectRole }) =>
      api.post<Member>(`/projects/${id}/members`, body),
    update: (id: string, userId: string, role: ProjectRole) =>
      api.patch<Member>(`/projects/${id}/members/${userId}`, { role }),
    remove: (id: string, userId: string) =>
      api.del<void>(`/projects/${id}/members/${userId}`),
  },
};
