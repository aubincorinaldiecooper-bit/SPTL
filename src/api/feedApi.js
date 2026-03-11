const API_BASE_URL = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || '';

export class UploadError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'UploadError';
    this.status = status;
  }
}

function buildUrl(path, params = {}) {
  const url = new URL(`${API_BASE_URL}${path}`, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === false) {
      return;
    }
    url.searchParams.set(key, String(value));
  });

  if (!API_BASE_URL) {
    return `${url.pathname}${url.search}`;
  }

  return url.toString();
}

export async function fetchFeed({ page = 1, pageSize = 20, userId = null, includePending = false } = {}) {
  const url = buildUrl('/api/feed', {
    page,
    page_size: pageSize,
    user_id: userId,
    include_pending: includePending ? 'true' : null,
  });

  const response = await fetch(url);
  const body = await response.json();

  if (!response.ok) {
    throw new Error(body?.error || 'Failed to load feed.');
  }

  return body;
}

export async function deleteJob(jobId) {
  const response = await fetch(buildUrl(`/api/spatial-photos/${jobId}`), { method: 'DELETE' });
  const body = await response.json();

  if (!response.ok) {
    throw new Error(body?.error || 'Failed to delete job.');
  }

  return body;
}

export async function uploadPhoto({ file, userId = null, signal }) {
  const formData = new FormData();
  formData.append('image', file);
  if (userId) {
    formData.append('user_id', userId);
  }

  const response = await fetch(buildUrl('/api/spatial-photos'), {
    method: 'POST',
    body: formData,
    signal,
  });

  const body = await response.json();
  if (response.ok) {
    return body;
  }

  if (response.status === 413) {
    throw new UploadError('File is too large (max 20MB)', 413);
  }
  if (response.status === 415) {
    throw new UploadError('Unsupported file type', 415);
  }
  if (response.status === 429) {
    const retryAfter = body?.retry_after;
    throw new UploadError(`Too many uploads. Please wait ${retryAfter}s before trying again.`, 429);
  }

  throw new UploadError(body?.error || 'Upload failed', response.status);
}

export function pollJobStatus(jobId, { intervalMs = 2000, timeoutMs = 120000, onUpdate } = {}) {
  const controller = { cancelled: false };
  let timer = null;

  const promise = new Promise((resolve, reject) => {
    const start = Date.now();

    async function poll() {
      if (controller.cancelled) {
        reject(new Error('Polling cancelled'));
        return;
      }

      if (Date.now() - start > timeoutMs) {
        reject(new Error('Polling timed out'));
        return;
      }

      try {
        const response = await fetch(buildUrl(`/api/spatial-photos/${jobId}/status`));
        const body = await response.json();

        if (!response.ok) {
          throw new Error(body?.error || 'Failed to poll job status.');
        }

        if (onUpdate) {
          onUpdate(body);
        }

        if (body.status === 'done' || body.status === 'failed') {
          resolve(body);
          return;
        }
      } catch (error) {
        reject(error instanceof Error ? error : new Error('Failed to poll job status.'));
        return;
      }

      timer = setTimeout(poll, intervalMs);
    }

    poll();
  });

  const cancel = () => {
    controller.cancelled = true;
    if (timer) {
      clearTimeout(timer);
    }
  };

  return { promise, cancel };
}
