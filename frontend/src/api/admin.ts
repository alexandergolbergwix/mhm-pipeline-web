import {api} from "@/api/client";

export interface AdminStats {
  pending_access_requests: number;
  total_users: number;
  total_projects: number;
  active_invitations: number;
}

export interface UserListItem {
  id: string;
  email: string;
  name: string;
  role: "admin" | "editor";
  created_at: string;
  project_count: number;
}

export interface UserMembership {
  project_id: string;
  project_name: string;
  role: string;
  joined_at: string;
}

export interface UserDetail extends UserListItem {
  memberships: UserMembership[];
  active_session_count: number;
}

export interface ProjectListItem {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  owner_email: string;
  owner_name: string;
  member_count: number;
  created_at: string;
}

export const Admin = {
  getStats: () => api.get<AdminStats>("/admin/stats"),
  listUsers: () => api.get<UserListItem[]>("/admin/users"),
  getUser: (id: string) => api.get<UserDetail>(`/admin/users/${id}`),
  updateUserRole: (id: string, role: "admin" | "editor") =>
    api.patch<UserListItem>(`/admin/users/${id}`, {role}),
  deleteUser: (id: string) => api.del<void>(`/admin/users/${id}`),
  invalidateSessions: (id: string) =>
    api.post<{ok: boolean}>(`/admin/users/${id}/invalidate-sessions`),
  listProjects: () => api.get<ProjectListItem[]>("/admin/projects"),
  transferProject: (projectId: string, newOwnerId: string) =>
    api.patch<ProjectListItem>(`/admin/projects/${projectId}/transfer`, {new_owner_id: newOwnerId}),
};
