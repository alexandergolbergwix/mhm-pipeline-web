import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {
  PublicationApi,
  type PublicationEntitiesQuery,
  type PublicationEntity,
  type PublicationEntityPage,
  type ReviewStatus,
} from "@/api/publication";

export interface UsePublicationEntityPageOptions {
  runId: string;
  publicationId: string | null;
  releaseId: string;
  releaseDigest: string;
  limit?: number;
  entityKind?: string;
  reviewStatus?: ReviewStatus;
  query?: string;
  compatibilityItems?: PublicationEntity[];
}

export interface PublicationEntityPageState {
  items: PublicationEntity[];
  total: number;
  releaseId: string;
  releaseDigest: string;
  cursor: string | null;
  nextCursor: string | null;
  hasNext: boolean;
  hasPrevious: boolean;
  loading: boolean;
  error: string | null;
  source: "publication" | "compatibility";
  next: () => void;
  previous: () => void;
  refresh: () => void;
}

const EMPTY_COMPATIBILITY_ITEMS: PublicationEntity[] = [];

function compatibilityOffset(cursor: string | null): number {
  if (!cursor?.startsWith("compatibility:")) return 0;
  const parsed = Number.parseInt(cursor.slice("compatibility:".length), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function filterCompatibilityItems(
  items: PublicationEntity[],
  entityKind?: string,
  reviewStatus?: ReviewStatus,
  query?: string,
): PublicationEntity[] {
  const normalizedQuery = query?.trim().toLocaleLowerCase();
  return items.filter((item) => {
    if (entityKind && item.entity_kind !== entityKind) return false;
    if (reviewStatus && item.review_status !== reviewStatus) return false;
    if (!normalizedQuery) return true;
    return item.label.toLocaleLowerCase().includes(normalizedQuery)
      || item.entity_id.toLocaleLowerCase().includes(normalizedQuery);
  });
}

function compatibilityPage(
  releaseId: string,
  releaseDigest: string,
  items: PublicationEntity[],
  cursor: string | null,
  limit: number,
  entityKind?: string,
  reviewStatus?: ReviewStatus,
  query?: string,
): PublicationEntityPage {
  const filtered = filterCompatibilityItems(items, entityKind, reviewStatus, query);
  const offset = compatibilityOffset(cursor);
  const nextOffset = offset + limit;
  return {
    release_id: releaseId,
    release_digest: releaseDigest,
    items: filtered.slice(offset, nextOffset),
    next_cursor: nextOffset < filtered.length ? `compatibility:${nextOffset}` : null,
    total: filtered.length,
  };
}

export function usePublicationEntityPage({
  runId,
  publicationId,
  releaseId,
  releaseDigest,
  limit = 50,
  entityKind,
  reviewStatus,
  query,
  compatibilityItems = EMPTY_COMPATIBILITY_ITEMS,
}: UsePublicationEntityPageOptions): PublicationEntityPageState {
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | null>>([]);
  const [page, setPage] = useState<PublicationEntityPage>(() => compatibilityPage(
    releaseId,
    releaseDigest,
    compatibilityItems,
    null,
    limit,
    entityKind,
    reviewStatus,
    query,
  ));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<"publication" | "compatibility">("compatibility");
  const [routeUnavailable, setRouteUnavailable] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  const filterKey = `${releaseId}\u0000${releaseDigest}\u0000${limit}\u0000${entityKind ?? ""}\u0000${reviewStatus ?? ""}\u0000${query ?? ""}`;
  useEffect(() => {
    setCursor(null);
    setHistory([]);
    setRouteUnavailable(false);
  }, [filterKey, publicationId]);

  const readQuery = useMemo<PublicationEntitiesQuery>(() => ({
    type: "entities",
    release_id: releaseId,
    cursor,
    limit,
    entity_kind: entityKind,
    review_status: reviewStatus,
    query,
  }), [cursor, entityKind, limit, query, releaseId, reviewStatus]);

  useEffect(() => {
    let cancelled = false;
    const setCompatibilityPage = () => {
      if (cancelled) return;
      setPage(compatibilityPage(
        releaseId,
        releaseDigest,
        compatibilityItems,
        cursor,
        limit,
        entityKind,
        reviewStatus,
        query,
      ));
      setSource("compatibility");
      setLoading(false);
    };

    if (!publicationId || routeUnavailable) {
      setCompatibilityPage();
      return () => { cancelled = true; };
    }

    setLoading(true);
    setError(null);
    void PublicationApi.read(runId, publicationId, readQuery)
      .then((nextPage) => {
        if (cancelled) return;
        setPage(nextPage);
        setSource("publication");
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof ApiError
          && (caught.status === 404 || caught.status === 405)
          && compatibilityItems.length > 0) {
          setRouteUnavailable(true);
          setCompatibilityPage();
          return;
        }
        setError(caught instanceof ApiError ? caught.detail : String(caught));
        setLoading(false);
      });

    return () => { cancelled = true; };
  }, [
    cursor,
    entityKind,
    limit,
    publicationId,
    query,
    readQuery,
    refreshToken,
    releaseDigest,
    releaseId,
    reviewStatus,
    routeUnavailable,
    runId,
  ]);

  const next = useCallback(() => {
    if (!page.next_cursor) return;
    setHistory((current) => [...current, cursor]);
    setCursor(page.next_cursor);
  }, [cursor, page.next_cursor]);

  const previous = useCallback(() => {
    setHistory((current) => {
      if (current.length === 0) return current;
      setCursor(current[current.length - 1] ?? null);
      return current.slice(0, -1);
    });
  }, []);

  const refresh = useCallback(() => {
    setRouteUnavailable(false);
    setRefreshToken((current) => current + 1);
  }, []);

  return {
    items: page.items,
    total: page.total,
    releaseId: page.release_id,
    releaseDigest: page.release_digest,
    cursor,
    nextCursor: page.next_cursor,
    hasNext: page.next_cursor !== null,
    hasPrevious: history.length > 0,
    loading,
    error,
    source,
    next,
    previous,
    refresh,
  };
}
