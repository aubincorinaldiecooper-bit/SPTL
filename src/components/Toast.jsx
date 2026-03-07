import { useEffect } from 'react';

import styles from './Toast.module.css';

export default function Toast({ message, duration = 3000, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(() => {
      onDismiss?.();
    }, duration);

    return () => clearTimeout(timer);
  }, [duration, onDismiss]);

  return (
    <div
      className={styles.toast}
      style={{ '--toast-out-delay': `${Math.max(0, duration - 260)}ms` }}
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  );
}
