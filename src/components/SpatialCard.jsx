import React, { Suspense, useMemo, useState } from 'react';
import { Canvas, useLoader } from '@react-three/fiber';
import { Center, OrbitControls } from '@react-three/drei';
import { SPZLoader } from '@spz-loader/core';

import { formatRelativeTime } from '../utils/formatRelativeTime';
import styles from './SpatialCard.module.css';

function LoadingPlaceholder({ depthMapUrl }) {
  return (
    <div className={styles.loading}>
      {depthMapUrl ? <img className={styles.loadingDepth} src={depthMapUrl} alt="Depth map placeholder" /> : null}
      <div className={styles.spinner} aria-label="Loading spatial content" />
    </div>
  );
}

function SpatialMesh({ spzUrl }) {
  const splatObject = useLoader(SPZLoader, spzUrl);

  const primitive = useMemo(() => {
    if (!splatObject) {
      return null;
    }
    if (splatObject.scene) {
      return splatObject.scene;
    }
    return splatObject;
  }, [splatObject]);

  if (!primitive) {
    return null;
  }

  return (
    <Center>
      <primitive object={primitive} />
    </Center>
  );
}

class CanvasErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    if (this.props.onError) {
      this.props.onError(error);
    }
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

export default function SpatialCard({
  spzUrl,
  depthMapUrl,
  jobId,
  username,
  createdAt,
  onDelete,
  isNew = false,
  onAnimationEnd,
}) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const displayName = username || 'Anonymous';
  const relativeTime = formatRelativeTime(createdAt);

  const handleDeleteConfirm = () => {
    if (onDelete) {
      onDelete(jobId);
    }
    setShowDeleteConfirm(false);
  };

  return (
    <article className={`${styles.card} ${isNew ? styles.cardEnter : ""}`} data-job-id={jobId} onAnimationEnd={onAnimationEnd}>
      {onDelete ? (
        <div className={styles.deleteWrap}>
          <button className={styles.deleteBtn} onClick={() => setShowDeleteConfirm((prev) => !prev)} type="button" aria-label="Delete post">
            🗑
          </button>
          {showDeleteConfirm ? (
            <div className={styles.confirm}>
              <div>Delete this post?</div>
              <div className={styles.confirmActions}>
                <button className={`${styles.confirmBtn} ${styles.confirmYes}`} type="button" onClick={handleDeleteConfirm}>
                  Yes
                </button>
                <button className={`${styles.confirmBtn} ${styles.confirmNo}`} type="button" onClick={() => setShowDeleteConfirm(false)}>
                  No
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className={styles.canvasWrap}>
        {loadError ? (
          <div className={styles.error}>Could not load spatial photo</div>
        ) : (
          <Canvas camera={{ fov: 45, position: [0, 0, 3] }}>
            <ambientLight intensity={0.8} />
            <directionalLight intensity={1.2} position={[2, 2, 2]} />
            <CanvasErrorBoundary fallback={<div className={styles.error}>Could not load spatial photo</div>} onError={() => setLoadError(true)}>
              <Suspense fallback={<LoadingPlaceholder depthMapUrl={depthMapUrl} />}>
                <SpatialMesh spzUrl={spzUrl} />
              </Suspense>
            </CanvasErrorBoundary>
            <OrbitControls enableZoom={false} autoRotate autoRotateSpeed={0.6} />
          </Canvas>
        )}
      </div>

      <div className={styles.overlay}>
        <span className={styles.user}>{displayName}</span>
        <span className={styles.time}>{relativeTime}</span>
      </div>
    </article>
  );
}
