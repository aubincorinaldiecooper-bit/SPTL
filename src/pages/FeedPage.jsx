import { useEffect, useRef, useState } from 'react';

import SpatialCard from '../components/SpatialCard';
import Toast from '../components/Toast';
import UploadModal from '../components/UploadModal';
import { useFeed } from '../hooks/useFeed';
import styles from './FeedPage.module.css';

export default function FeedPage() {
  const appName = import.meta.env.VITE_APP_NAME || 'Spatial';
  const { items, isLoading, isFetchingMore, hasMore, error, loadMore, deleteItem, refresh, prependItem } = useFeed();
  const [toastMessage, setToastMessage] = useState('');
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [newItemIds, setNewItemIds] = useState(() => new Set());
  const sentinelRef = useRef(null);

  useEffect(() => {
    const target = sentinelRef.current;
    if (!target) {
      return undefined;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !isFetchingMore && hasMore) {
          loadMore();
        }
      },
      { rootMargin: '120px 0px 120px 0px' }
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMore, isFetchingMore, loadMore]);

  const handleDelete = async (jobId) => {
    const success = await deleteItem(jobId);
    if (success) {
      setToastMessage('Post deleted');
    }
  };

  const handleUploadComplete = (jobMetadata) => {
    prependItem(jobMetadata);
    setNewItemIds((prev) => {
      const next = new Set(prev);
      next.add(jobMetadata.job_id);
      return next;
    });
  };

  const clearNewFlag = (jobId) => {
    setNewItemIds((prev) => {
      if (!prev.has(jobId)) return prev;
      const next = new Set(prev);
      next.delete(jobId);
      return next;
    });
  };

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <header className={styles.header}>
          <h1 className={styles.appName}>{appName}</h1>
          <button className={styles.uploadBtn} type="button" aria-label="Upload post" onClick={() => setIsUploadOpen(true)}>
            ➕ Upload
          </button>
        </header>

        {error ? (
          <div className={styles.errorBanner} role="alert">
            <span>Failed to load feed.</span>
            <button className={styles.retryBtn} type="button" onClick={refresh}>
              Retry
            </button>
          </div>
        ) : null}

        {isLoading ? (
          <div className={styles.loadingRow}>
            <div className={styles.spinner} aria-label="Loading feed" />
          </div>
        ) : null}

        {!isLoading && items.length === 0 ? (
          <div className={styles.empty}>
            <div>
              <div className={styles.emptyIcon}>🫧</div>
              <div>No spatial photos yet. Upload one to get started.</div>
            </div>
          </div>
        ) : (
          <>
            <section className={styles.grid}>
              {items.map((item) => (
                <SpatialCard
                  key={item.job_id}
                  spzUrl={item.spz_url}
                  depthMapUrl={item.depth_map_url}
                  jobId={item.job_id}
                  username={item.user_id || undefined}
                  createdAt={item.created_at}
                  onDelete={handleDelete}
                  isNew={newItemIds.has(item.job_id)}
                  onAnimationEnd={() => clearNewFlag(item.job_id)}
                />
              ))}
            </section>

            {isFetchingMore ? (
              <div className={styles.loadingRow}>
                <div className={styles.spinner} aria-label="Loading more feed items" />
              </div>
            ) : null}

            <div ref={sentinelRef} className={styles.sentinel} />
          </>
        )}
      </div>

      {toastMessage ? <Toast message={toastMessage} onDismiss={() => setToastMessage('')} /> : null}

      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onUploadComplete={handleUploadComplete}
      />
    </div>
  );
}
