import {afterEach, describe, expect, it} from "vitest";

import {
  clearUserCache,
  getUserCache,
  invalidateRunForUser,
  setUserCache,
  userCacheKey,
} from "@/cache/clientCache";

const USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const RUN_ID = "11111111-1111-1111-1111-111111111111";

describe("clientCache", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("namespaces keys by user id", () => {
    const key = userCacheKey(USER_A, "extraction", "entities", RUN_ID);
    expect(key).toBe(`mhm:${USER_A}:extraction:entities:${RUN_ID}`);
    expect(key).not.toContain(USER_B);
  });

  it("does not leak data across users on the same browser", () => {
    setUserCache(USER_A, "extraction", "verdict", RUN_ID, {overall: "pass"}, {
      ttlMs: 60_000,
      fingerprint: "fp-a",
    });
    expect(getUserCache(USER_A, "extraction", "verdict", RUN_ID, "fp-a")).toEqual({
      overall: "pass",
    });
    expect(getUserCache(USER_B, "extraction", "verdict", RUN_ID, "fp-a")).toBeNull();
  });

  it("rejects partial entity lists when total mismatches", () => {
    setUserCache(USER_A, "extraction", "entities", RUN_ID, {
      entities: [{id: "only-one"}],
      total: 300,
      approvedCount: 0,
      recordCount: 1,
      sourceCounts: {},
    }, {ttlMs: 60_000});
    const hit = getUserCache<{
      entities: {id: string}[];
      total: number;
    }>(USER_A, "extraction", "entities", RUN_ID, undefined, {
      validate: (v) => v.entities.length === v.total,
    });
    expect(hit).toBeNull();
  });

  it("clearUserCache removes all keys for one user", () => {
    setUserCache(USER_A, "extraction", "entities", RUN_ID, {n: 1}, {ttlMs: 60_000});
    setUserCache(USER_B, "extraction", "entities", RUN_ID, {n: 2}, {ttlMs: 60_000});
    clearUserCache(USER_A);
    expect(getUserCache(USER_A, "extraction", "entities", RUN_ID)).toBeNull();
    expect(getUserCache(USER_B, "extraction", "entities", RUN_ID)).toEqual({n: 2});
  });

  it("invalidateRunForUser clears run-scoped keys only for that user", () => {
    setUserCache(USER_A, "extraction", "entities", RUN_ID, {n: 1}, {ttlMs: 60_000});
    setUserCache(USER_A, "extraction", "entities", "other-run", {n: 2}, {ttlMs: 60_000});
    invalidateRunForUser(USER_A, RUN_ID);
    expect(getUserCache(USER_A, "extraction", "entities", RUN_ID)).toBeNull();
    expect(getUserCache(USER_A, "extraction", "entities", "other-run")).toEqual({n: 2});
  });
});
