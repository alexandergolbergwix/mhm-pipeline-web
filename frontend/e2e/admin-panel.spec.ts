/**
 * E2E suite for the admin panel
 * (dashboard, user management, project overview — Rule W-19).
 *
 * All backend endpoints are mocked via `installAdminMocks` so the
 * suite runs in full isolation from the DB, auth, and email.
 */
import {expect, test} from "@playwright/test";
import {
  TEST_ADMIN_ID,
  TEST_EDITOR_ID,
  TEST_PROJECT_ID,
  gotoAdmin,
  gotoAdminUserDetail,
  gotoAdminUsers,
  gotoAdminProjects,
  installAdminMocks,
  makeAdminState,
} from "./fixtures/admin-fixtures";

// Admin panel tests are serial because they share a single Vite dev server
// and the route-interceptor IPC becomes unreliable when many workers race
// simultaneously against the same admin pages (parallel OK across spec files
// via fullyParallel:true; serial only within this file).
test.describe.configure({mode: "serial"});

// ── Admin Dashboard ───────────────────────────────────────────────────

test.describe("Admin Dashboard", () => {
  test("renders stat cards with correct values", async ({page}) => {
    const state = makeAdminState({
      stats: {
        pending_access_requests: 2,
        total_users: 42,
        total_projects: 17,
        active_invitations: 8,
      },
    });
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await expect(page.getByTestId("admin-dashboard")).toBeVisible({timeout: 8000});

    await expect(page.getByText("42")).toBeVisible({timeout: 8000});
    await expect(page.getByText("17")).toBeVisible({timeout: 8000});
    await expect(page.getByText("8")).toBeVisible({timeout: 8000});
  });

  test("shows pending requests feed", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await expect(page.getByTestId("admin-dashboard")).toBeVisible({timeout: 8000});

    for (const req of state.pendingRequests) {
      await expect(page.getByText(req.name)).toBeVisible();
    }
  });

  test("approve from dashboard calls API and removes item", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await expect(page.getByTestId("admin-dashboard")).toBeVisible({timeout: 8000});

    const req = state.pendingRequests[0];
    await page.getByTestId(`dashboard-approve-${req.id}`).click();

    await expect.poll(() => state.approveCalls.length).toBeGreaterThan(0);
    expect(state.approveCalls[0]).toBe(req.id);
  });

  test("non-admin is redirected away from /admin", async ({page}) => {
    const state = makeAdminState({authedAsAdmin: false});
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await page.waitForTimeout(500);
    await expect(page.getByTestId("admin-dashboard")).toHaveCount(0);
    expect(page.url()).not.toContain("/admin");
  });

  test("sidebar shows pending badge when count > 0", async ({page}) => {
    const state = makeAdminState({
      stats: {
        pending_access_requests: 3,
        total_users: 2,
        total_projects: 1,
        active_invitations: 0,
      },
    });
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await expect(page.getByTestId("admin-pending-badge")).toBeVisible({timeout: 8000});
    await expect(page.getByTestId("admin-pending-badge")).toContainText("3");
  });

  test("sidebar badge absent when no pending requests", async ({page}) => {
    const state = makeAdminState({
      pendingRequests: [],
      stats: {
        pending_access_requests: 0,
        total_users: 2,
        total_projects: 1,
        active_invitations: 0,
      },
    });
    await installAdminMocks(page, state);
    await gotoAdmin(page);

    await expect(page.getByTestId("admin-dashboard")).toBeVisible({timeout: 8000});
    await expect(page.getByTestId("admin-pending-badge")).toHaveCount(0);
  });
});

// ── Admin Users list ──────────────────────────────────────────────────

test.describe("Admin Users list", () => {
  test("renders all users in the table", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await expect(page.getByTestId("admin-users-page")).toBeVisible({timeout: 8000});

    for (const u of state.users) {
      await expect(page.getByTestId(`user-row-${u.id}`)).toBeVisible();
    }
  });

  test("search filters by name", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await expect(page.getByTestId("admin-users-page")).toBeVisible({timeout: 8000});

    await page.getByTestId("user-search").fill("Editor One");

    await expect(page.getByTestId(`user-row-${TEST_EDITOR_ID}`)).toBeVisible();
    await expect(page.getByTestId(`user-row-${TEST_ADMIN_ID}`)).toHaveCount(0);
  });

  test("search filters by email", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await expect(page.getByTestId("admin-users-page")).toBeVisible({timeout: 8000});

    await page.getByTestId("user-search").fill("admin@example");

    await expect(page.getByTestId(`user-row-${TEST_ADMIN_ID}`)).toBeVisible();
    await expect(page.getByTestId(`user-row-${TEST_EDITOR_ID}`)).toHaveCount(0);
  });

  test("role filter Admins shows only admins", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await expect(page.getByTestId("admin-users-page")).toBeVisible({timeout: 8000});
    // Wait for users to load before clicking filter.
    await expect(page.locator('[data-testid^="user-row-"]').first()).toBeVisible({timeout: 8000});

    await page.getByTestId("role-filter-admin").click();

    const adminCount = state.users.filter((u) => u.role === "admin").length;
    await expect(page.locator('[data-testid^="user-row-"]')).toHaveCount(adminCount);
  });

  test("role filter Editors shows only editors", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await expect(page.getByTestId("admin-users-page")).toBeVisible({timeout: 8000});
    // Wait for users to load before clicking filter.
    await expect(page.locator('[data-testid^="user-row-"]').first()).toBeVisible({timeout: 8000});

    await page.getByTestId("role-filter-editor").click();

    const editorCount = state.users.filter((u) => u.role === "editor").length;
    await expect(page.locator('[data-testid^="user-row-"]')).toHaveCount(editorCount);
  });

  test("non-admin is redirected away from /admin/users", async ({page}) => {
    const state = makeAdminState({authedAsAdmin: false});
    await installAdminMocks(page, state);
    await gotoAdminUsers(page);

    await page.waitForTimeout(500);
    await expect(page.getByTestId("admin-users-page")).toHaveCount(0);
    expect(page.url()).not.toContain("/admin/users");
  });
});

// ── Admin User Detail ─────────────────────────────────────────────────

test.describe("Admin User Detail", () => {
  test("renders user identity and memberships", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_EDITOR_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    const detail = state.userDetails[TEST_EDITOR_ID];
    await expect(page.getByText(detail.name)).toBeVisible();
    await expect(page.getByText(detail.email)).toBeVisible();

    for (const m of detail.memberships) {
      await expect(page.getByText(m.project_name)).toBeVisible();
    }
  });

  test("force logout calls invalidate-sessions API", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_EDITOR_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await page.getByTestId("force-logout-button").click();

    await expect.poll(() => state.invalidateSessionCalls.length).toBeGreaterThan(0);
    expect(state.invalidateSessionCalls[0]).toBe(TEST_EDITOR_ID);
  });

  test("delete user opens confirm dialog then calls delete API", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_EDITOR_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await page.getByTestId("delete-user-button").click();
    await expect(page.getByTestId("confirm-destructive-dialog")).toBeVisible();

    await page.getByTestId("confirm-dialog-confirm").click();

    await expect.poll(() => state.deleteUserCalls.length).toBeGreaterThan(0);
    expect(state.deleteUserCalls[0]).toBe(TEST_EDITOR_ID);
  });

  test("cancel on delete dialog does not call API", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_EDITOR_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await page.getByTestId("delete-user-button").click();
    await expect(page.getByTestId("confirm-destructive-dialog")).toBeVisible();

    await page.getByTestId("confirm-dialog-cancel").click();

    await expect(page.getByTestId("confirm-destructive-dialog")).toHaveCount(0);
    expect(state.deleteUserCalls.length).toBe(0);
  });

  test("demoting admin opens confirm dialog before calling API", async ({page}) => {
    const state = makeAdminState({
      userDetails: {
        [TEST_EDITOR_ID]: {
          id: TEST_EDITOR_ID,
          email: "editor@example.org",
          name: "Editor One",
          role: "admin",
          created_at: "2026-02-01T00:00:00Z",
          project_count: 0,
          memberships: [],
          active_session_count: 0,
        },
      },
    });
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_EDITOR_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await page.getByTestId("role-select").selectOption("editor");

    await expect(page.getByTestId("confirm-destructive-dialog")).toBeVisible();
    expect(state.roleChangeCalls.length).toBe(0);

    await page.getByTestId("confirm-dialog-confirm").click();

    await expect.poll(() => state.roleChangeCalls.length).toBeGreaterThan(0);
    expect(state.roleChangeCalls[0].role).toBe("editor");
  });

  test("delete button is disabled when viewing yourself", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_ADMIN_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await expect(page.getByTestId("delete-user-button")).toBeDisabled();
  });

  test("role select is disabled when viewing yourself", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminUserDetail(page, TEST_ADMIN_ID);

    await expect(page.getByTestId("admin-user-detail")).toBeVisible({timeout: 8000});

    await expect(page.getByTestId("role-select")).toBeDisabled();
  });
});

// ── Admin Projects ────────────────────────────────────────────────────

test.describe("Admin Projects", () => {
  test("renders all projects in the table", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminProjects(page);

    await expect(page.getByTestId("admin-projects-page")).toBeVisible({timeout: 8000});

    for (const p of state.projects) {
      await expect(page.getByTestId(`project-row-${p.id}`)).toBeVisible();
    }
  });

  test("search filters by project name", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminProjects(page);

    await expect(page.getByTestId("admin-projects-page")).toBeVisible({timeout: 8000});

    await page.getByTestId("project-search").fill("Manuscripts A");

    await expect(page.getByTestId(`project-row-${TEST_PROJECT_ID}`)).toBeVisible();
    const projectCount = state.projects.filter((p) =>
      p.name.toLowerCase().includes("manuscripts a"),
    ).length;
    await expect(page.locator('[data-testid^="project-row-"]')).toHaveCount(projectCount);
  });

  test("transfer ownership flow — opens picker then calls API", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminProjects(page);

    await expect(page.getByTestId("admin-projects-page")).toBeVisible({timeout: 8000});

    await page.getByTestId(`transfer-button-${TEST_PROJECT_ID}`).click();

    await expect(page.getByTestId("transfer-owner-select")).toBeVisible();

    await page.getByTestId("transfer-owner-select").selectOption(TEST_EDITOR_ID);

    await expect(page.getByTestId("confirm-transfer-button")).toBeEnabled();
    await page.getByTestId("confirm-transfer-button").click();

    await expect.poll(() => state.transferCalls.length).toBeGreaterThan(0);
    expect(state.transferCalls[0].projectId).toBe(TEST_PROJECT_ID);
    expect(state.transferCalls[0].newOwnerId).toBe(TEST_EDITOR_ID);
  });

  test("confirm transfer button is disabled until owner is selected", async ({page}) => {
    const state = makeAdminState();
    await installAdminMocks(page, state);
    await gotoAdminProjects(page);

    await expect(page.getByTestId("admin-projects-page")).toBeVisible({timeout: 8000});

    await page.getByTestId(`transfer-button-${TEST_PROJECT_ID}`).click();
    await expect(page.getByTestId("transfer-owner-select")).toBeVisible();

    await expect(page.getByTestId("confirm-transfer-button")).toBeDisabled();
  });

  test("non-admin is redirected away from /admin/projects", async ({page}) => {
    const state = makeAdminState({authedAsAdmin: false});
    await installAdminMocks(page, state);
    await gotoAdminProjects(page);

    await page.waitForTimeout(500);
    await expect(page.getByTestId("admin-projects-page")).toHaveCount(0);
    expect(page.url()).not.toContain("/admin/projects");
  });
});
