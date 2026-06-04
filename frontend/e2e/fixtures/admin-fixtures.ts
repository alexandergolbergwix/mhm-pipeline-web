/**
 * E2E fixtures + Playwright route mocking for the admin panel
 * (dashboard, user management, project overview).
 *
 * All backend endpoints are mocked deterministically so the suite
 * runs in isolation from the DB. State mutates in place so tests
 * can assert on captured API calls.
 */
import type {Page, Route} from "@playwright/test";

export const TEST_ADMIN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
export const TEST_EDITOR_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
export const TEST_EDITOR2_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
export const TEST_PROJECT_ID = "11111111-1111-1111-1111-111111111111";
export const TEST_PROJECT2_ID = "22222222-2222-2222-2222-222222222222";

export interface MockUserListItem {
  id: string;
  email: string;
  name: string;
  role: "admin" | "editor";
  created_at: string;
  project_count: number;
}

export interface MockUserDetail extends MockUserListItem {
  memberships: MockMembership[];
  active_session_count: number;
}

export interface MockMembership {
  project_id: string;
  project_name: string;
  role: string;
  joined_at: string;
}

export interface MockProject {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  owner_email: string;
  owner_name: string;
  member_count: number;
  created_at: string;
}

export interface MockAdminStats {
  pending_access_requests: number;
  total_users: number;
  total_projects: number;
  active_invitations: number;
}

export interface AdminPanelState {
  authedAsAdmin: boolean;
  stats: MockAdminStats;
  users: MockUserListItem[];
  userDetails: Record<string, MockUserDetail>;
  projects: MockProject[];
  pendingRequests: PendingRequest[];
  deleteUserCalls: string[];
  roleChangeCalls: {id: string; role: string}[];
  invalidateSessionCalls: string[];
  transferCalls: {projectId: string; newOwnerId: string}[];
  approveCalls: string[];
}

export interface PendingRequest {
  id: string;
  email: string;
  name: string;
  affiliation: string;
  status: "pending_admin";
  created_at: string;
  confirmed_at: string | null;
  reviewed_at: string | null;
  reviewed_by_email: string | null;
  denial_reason: string | null;
}

export function makeAdminState(
  overrides: Partial<AdminPanelState> = {},
): AdminPanelState {
  const users: MockUserListItem[] = overrides.users ?? [
    {
      id: TEST_ADMIN_ID,
      email: "admin@example.org",
      name: "Admin User",
      role: "admin",
      created_at: "2026-01-01T00:00:00Z",
      project_count: 2,
    },
    {
      id: TEST_EDITOR_ID,
      email: "editor@example.org",
      name: "Editor One",
      role: "editor",
      created_at: "2026-02-01T00:00:00Z",
      project_count: 1,
    },
    {
      id: TEST_EDITOR2_ID,
      email: "editor2@example.org",
      name: "Editor Two",
      role: "editor",
      created_at: "2026-03-01T00:00:00Z",
      project_count: 0,
    },
  ];

  const projects: MockProject[] = overrides.projects ?? [
    {
      id: TEST_PROJECT_ID,
      name: "Hebrew Manuscripts A",
      description: "Primary corpus",
      owner_id: TEST_ADMIN_ID,
      owner_email: "admin@example.org",
      owner_name: "Admin User",
      member_count: 3,
      created_at: "2026-01-15T00:00:00Z",
    },
    {
      id: TEST_PROJECT2_ID,
      name: "Hebrew Manuscripts B",
      description: "Secondary corpus",
      owner_id: TEST_EDITOR_ID,
      owner_email: "editor@example.org",
      owner_name: "Editor One",
      member_count: 2,
      created_at: "2026-02-20T00:00:00Z",
    },
  ];

  const userDetails: Record<string, MockUserDetail> =
    overrides.userDetails ?? {
      [TEST_EDITOR_ID]: {
        id: TEST_EDITOR_ID,
        email: "editor@example.org",
        name: "Editor One",
        role: "editor",
        created_at: "2026-02-01T00:00:00Z",
        project_count: 1,
        memberships: [
          {
            project_id: TEST_PROJECT_ID,
            project_name: "Hebrew Manuscripts A",
            role: "editor",
            joined_at: "2026-02-05T00:00:00Z",
          },
        ],
        active_session_count: 1,
      },
      [TEST_ADMIN_ID]: {
        id: TEST_ADMIN_ID,
        email: "admin@example.org",
        name: "Admin User",
        role: "admin",
        created_at: "2026-01-01T00:00:00Z",
        project_count: 2,
        memberships: [],
        active_session_count: 1,
      },
    };

  const pendingRequests: PendingRequest[] = overrides.pendingRequests ?? [
    {
      id: "req-pending-1",
      email: "pending@example.org",
      name: "Pending Researcher",
      affiliation: "Test University",
      status: "pending_admin",
      created_at: "2026-06-01T10:00:00Z",
      confirmed_at: "2026-06-01T10:05:00Z",
      reviewed_at: null,
      reviewed_by_email: null,
      denial_reason: null,
    },
  ];

  return {
    authedAsAdmin: overrides.authedAsAdmin ?? true,
    stats: overrides.stats ?? {
      pending_access_requests: pendingRequests.length,
      total_users: users.length,
      total_projects: projects.length,
      active_invitations: 7,
    },
    users,
    userDetails,
    projects,
    pendingRequests,
    deleteUserCalls: overrides.deleteUserCalls ?? [],
    roleChangeCalls: overrides.roleChangeCalls ?? [],
    invalidateSessionCalls: overrides.invalidateSessionCalls ?? [],
    transferCalls: overrides.transferCalls ?? [],
    approveCalls: overrides.approveCalls ?? [],
  };
}

export async function installAdminMocks(
  page: Page,
  state: AdminPanelState,
): Promise<void> {
  await page.route(/\/api\/auth\/me(\?.*)?$/, async (route: Route) => {
    if (state.authedAsAdmin) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: TEST_ADMIN_ID,
          email: "admin@example.org",
          name: "Admin User",
          role: "admin",
        }),
      });
    } else {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({detail: "not authenticated"}),
      });
    }
  });

  // Single catch-all for all /api/admin/* to avoid regex anchoring surprises.
  await page.route(/\/api\/admin\//, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    // /api/admin/stats
    if (path === "/api/admin/stats") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(state.stats),
      });
      return;
    }

    // /api/admin/invites
    if (path === "/api/admin/invites") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
      return;
    }

    // /api/admin/users  (list)
    if (path === "/api/admin/users" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(state.users),
      });
      return;
    }

    // /api/admin/users/{id}/invalidate-sessions
    const invalidateM = path.match(/^\/api\/admin\/users\/([^/]+)\/invalidate-sessions$/);
    if (invalidateM) {
      const id = invalidateM[1];
      state.invalidateSessionCalls.push(id);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ok: true}),
      });
      return;
    }

    // /api/admin/users/{id}  (get / patch / delete)
    const userM = path.match(/^\/api\/admin\/users\/([^/]+)$/);
    if (userM) {
      const id = userM[1];
      if (method === "GET") {
        const detail = state.userDetails[id];
        await route.fulfill({
          status: detail ? 200 : 404,
          contentType: "application/json",
          body: JSON.stringify(detail ?? {detail: "not found"}),
        });
        return;
      }
      if (method === "PATCH") {
        const body = (route.request().postDataJSON() ?? {}) as {role?: string};
        const newRole = body.role ?? "editor";
        state.roleChangeCalls.push({id, role: newRole});
        const user = state.users.find((u) => u.id === id);
        if (user) user.role = newRole as "admin" | "editor";
        const detail = state.userDetails[id];
        if (detail) detail.role = newRole as "admin" | "editor";
        const updated = state.users.find((u) => u.id === id) ?? {id, email: "", name: "", role: newRole, created_at: "", project_count: 0};
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(updated),
        });
        return;
      }
      if (method === "DELETE") {
        state.deleteUserCalls.push(id);
        state.users = state.users.filter((u) => u.id !== id);
        await route.fulfill({status: 204});
        return;
      }
    }

    // /api/admin/projects/{id}/transfer
    const transferM = path.match(/^\/api\/admin\/projects\/([^/]+)\/transfer$/);
    if (transferM) {
      const projectId = transferM[1];
      const body = (route.request().postDataJSON() ?? {}) as {new_owner_id?: string};
      const newOwnerId = body.new_owner_id ?? "";
      state.transferCalls.push({projectId, newOwnerId});
      const proj = state.projects.find((p) => p.id === projectId);
      const newOwner = state.users.find((u) => u.id === newOwnerId);
      if (proj && newOwner) {
        proj.owner_id = newOwnerId;
        proj.owner_email = newOwner.email;
        proj.owner_name = newOwner.name;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(proj ?? {}),
      });
      return;
    }

    // /api/admin/projects  (list)
    if (path === "/api/admin/projects" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(state.projects),
      });
      return;
    }

    // /api/admin/access-requests/{id}/approve
    const approveM = path.match(/^\/api\/admin\/access-requests\/([^/]+)\/approve$/);
    if (approveM) {
      const id = approveM[1];
      state.approveCalls.push(id);
      state.pendingRequests = state.pendingRequests.filter((r) => r.id !== id);
      state.stats.pending_access_requests = state.pendingRequests.length;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ok: true}),
      });
      return;
    }

    // /api/admin/access-requests  (list, with optional ?status_filter)
    if (path === "/api/admin/access-requests" && method === "GET") {
      const filter = url.searchParams.get("status_filter");
      const results = filter === "pending_admin" ? state.pendingRequests : [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(results),
      });
      return;
    }

    await route.continue();
  });
}

export async function gotoAdmin(page: Page): Promise<void> {
  await page.context().addCookies([
    {name: "session", value: "test-admin-session", url: "http://localhost:5173"},
  ]).catch(() => {});
  await page.goto("/admin");
}

export async function gotoAdminUsers(page: Page): Promise<void> {
  await page.context().addCookies([
    {name: "session", value: "test-admin-session", url: "http://localhost:5173"},
  ]).catch(() => {});
  await page.goto("/admin/users");
}

export async function gotoAdminUserDetail(page: Page, userId: string): Promise<void> {
  await page.context().addCookies([
    {name: "session", value: "test-admin-session", url: "http://localhost:5173"},
  ]).catch(() => {});
  await page.goto(`/admin/users/${userId}`);
}

export async function gotoAdminProjects(page: Page): Promise<void> {
  await page.context().addCookies([
    {name: "session", value: "test-admin-session", url: "http://localhost:5173"},
  ]).catch(() => {});
  await page.goto("/admin/projects");
}
