import { useCallback, useEffect, useMemo, useState } from 'react';

import { deleteJob, fetchFeed } from '../api/feedApi';

function mergeUniqueByJobId(existingItems, newItems) {
  const map = new Map();
  existingItems.forEach((item) => map.set(item.job_id, item));
  newItems.forEach((item) => map.set(item.job_id, item));
  return Array.from(map.values());
}

export function useFeed({ userId, includePending } = {}) {
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [error, setError] = useState(null);

  const query = useMemo(() => ({ userId: userId ?? null, includePending: Boolean(includePending) }), [userId, includePending]);

  const fetchPage = useCallback(
    async (targetPage, { reset = false } = {}) => {
      if (reset) {
        setIsLoading(true);
      } else {
        setIsFetchingMore(true);
      }

      setError(null);
      try {
        const result = await fetchFeed({
          page: targetPage,
          pageSize: 20,
          userId: query.userId,
          includePending: query.includePending,
        });

        setHasMore(Boolean(result.has_more));
        setPage(targetPage);
        setItems((prev) => {
          const base = reset ? [] : prev;
          return mergeUniqueByJobId(base, result.items || []);
        });
      } catch (err) {
        setError(err instanceof Error ? err : new Error('Failed to load feed.'));
      } finally {
        setIsLoading(false);
        setIsFetchingMore(false);
      }
    },
    [query.includePending, query.userId]
  );

  const refresh = useCallback(() => {
    setItems([]);
    setPage(1);
    setHasMore(true);
    return fetchPage(1, { reset: true });
  }, [fetchPage]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadMore = useCallback(async () => {
    if (isFetchingMore || isLoading || !hasMore) {
      return;
    }
    await fetchPage(page + 1, { reset: false });
  }, [fetchPage, hasMore, isFetchingMore, isLoading, page]);

  const deleteItem = useCallback(async (jobId) => {
    let backup = [];
    setItems((prev) => {
      backup = prev;
      return prev.filter((item) => item.job_id !== jobId);
    });

    try {
      await deleteJob(jobId);
      return true;
    } catch (err) {
      setItems(backup);
      setError(err instanceof Error ? err : new Error('Failed to delete post.'));
      return false;
    }
  }, []);

  const prependItem = useCallback((item) => {
    setItems((prev) => mergeUniqueByJobId([item], prev));
  }, []);

  return {
    items,
    isLoading,
    isFetchingMore,
    hasMore,
    error,
    loadMore,
    deleteItem,
    refresh,
    prependItem,
  };
}
