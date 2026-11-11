import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {api, ApiError, fetchWithRetry} from "@/api/client";

function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {"content-type": "application/json"},
  });
}

describe("api client retry", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retries a GET after a network failure and returns the later success", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse(200, {ok: true}));
    const promise = api.get<{ok: boolean}>("/runs/r1/wikidata-studio");
    await vi.runAllTimersAsync();
    await expect(promise).resolves.toEqual({ok: true});
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retries a GET after a 503 gateway timeout", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(503, {detail: "busy"}))
      .mockResolvedValueOnce(jsonResponse(200, {ok: true}));
    const promise = api.get<{ok: boolean}>("/runs/r1/wikidata-studio");
    await vi.runAllTimersAsync();
    await expect(promise).resolves.toEqual({ok: true});
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("gives up after the retry budget and throws the last error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const promise = api.get("/runs/r1/wikidata-studio");
    const assertion = expect(promise).rejects.toThrow("Failed to fetch");
    await vi.runAllTimersAsync();
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("does not retry a POST, because the write may have reached the server", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.post("/runs/r1/jobs", {kind: "x"})).rejects.toThrow("Failed to fetch");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a client error", async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, {detail: "missing"}));
    await expect(api.get("/runs/r1/missing")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry when the caller aborts", async () => {
    fetchMock.mockRejectedValue(new DOMException("aborted", "AbortError"));
    await expect(fetchWithRetry("/api/x", {}, "GET")).rejects.toThrow("aborted");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
