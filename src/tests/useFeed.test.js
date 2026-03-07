import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useFeed } from '../hooks/useFeed';

vi.mock('../api/feedApi', () => ({
  fetchFeed: vi.fn(),
  deleteJob: vi.fn(),
}));

import { deleteJob, fetchFeed } from '../api/feedApi';

const page1 = {
  page: 1,
  page_size: 20,
  total: 3,
  has_more: true,
  items: [
    { job_id: 'a', status: 'done' },
    { job_id: 'b', status: 'done' },
  ],
};

const page2 = {
  page: 2,
  page_size: 20,
  total: 3,
  has_more: false,
  items: [
    { job_id: 'b', status: 'done' },
    { job_id: 'c', status: 'done' },
  ],
};

describe('useFeed', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('initial fetch populates items', async () => {
    fetchFeed.mockResolvedValueOnce(page1);

    const { result } = renderHook(() => useFeed());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.items).toHaveLength(2);
    expect(result.current.items.map((x) => x.job_id)).toEqual(['a', 'b']);
  });

  it('loadMore appends next page without duplicates', async () => {
    fetchFeed.mockResolvedValueOnce(page1).mockResolvedValueOnce(page2);

    const { result } = renderHook(() => useFeed());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.items.map((x) => x.job_id)).toEqual(['a', 'b', 'c']);
  });

  it('deleteItem removes job from items optimistically', async () => {
    fetchFeed.mockResolvedValueOnce(page1);
    deleteJob.mockResolvedValueOnce({ deleted: true });

    const { result } = renderHook(() => useFeed());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.deleteItem('a');
    });

    expect(result.current.items.map((x) => x.job_id)).toEqual(['b']);
  });

  it('deleteItem restores item on API failure', async () => {
    fetchFeed.mockResolvedValueOnce(page1);
    deleteJob.mockRejectedValueOnce(new Error('Cannot delete'));

    const { result } = renderHook(() => useFeed());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.deleteItem('a');
    });

    expect(result.current.items.map((x) => x.job_id)).toEqual(['a', 'b']);
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('refresh resets items and re-fetches page 1', async () => {
    fetchFeed
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2)
      .mockResolvedValueOnce(page1);

    const { result } = renderHook(() => useFeed());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.loadMore();
    });
    expect(result.current.items.map((x) => x.job_id)).toEqual(['a', 'b', 'c']);

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.items.map((x) => x.job_id)).toEqual(['a', 'b']);
  });

  it('hasMore is false when API returns has_more false', async () => {
    fetchFeed.mockResolvedValueOnce({ ...page1, has_more: false });

    const { result } = renderHook(() => useFeed());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.hasMore).toBe(false);
  });
});
