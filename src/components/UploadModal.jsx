import { useEffect, useMemo, useRef, useState } from 'react';

import { pollJobStatus, uploadPhoto } from '../api/feedApi';
import styles from './UploadModal.module.css';

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic'];

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

export default function UploadModal({ isOpen, onClose, onUploadComplete }) {
  const [state, setState] = useState('idle');
  const [selectedFile, setSelectedFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [jobStatus, setJobStatus] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const inputRef = useRef(null);
  const modalRef = useRef(null);
  const uploadAbortRef = useRef(null);
  const pollCancelRef = useRef(null);
  const closeTimerRef = useRef(null);

  const depthMapUrl = jobStatus?.depth_map_url || null;

  const resetToIdle = () => {
    setState('idle');
    setSelectedFile(null);
    setPreviewUrl('');
    setErrorMessage('');
    setJobStatus(null);
    setIsDragOver(false);
  };

  const cleanupAsync = () => {
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
    pollCancelRef.current?.();
    pollCancelRef.current = null;
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  useEffect(() => {
    if (!isOpen) {
      cleanupAsync();
      resetToIdle();
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        if (state === 'idle' || state === 'previewing' || state === 'error') {
          cleanupAsync();
          onClose?.();
        }
        return;
      }

      if (event.key === 'Tab') {
        const focusable = modalRef.current?.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable || focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose, state]);

  useEffect(() => {
    if (!isOpen) return;
    const firstFocusable = modalRef.current?.querySelector('button, input');
    firstFocusable?.focus();
  }, [isOpen, state]);

  useEffect(
    () => () => {
      cleanupAsync();
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    },
    [previewUrl]
  );

  const onFilePicked = (file) => {
    if (!file) return;
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
    setErrorMessage('');
    setState('previewing');
  };

  const handleInputChange = (event) => {
    const file = event.target.files?.[0];
    onFilePicked(file);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragOver(false);
    const file = event.dataTransfer?.files?.[0];
    onFilePicked(file);
  };

  const startUpload = async () => {
    if (!selectedFile) return;

    setState('uploading');
    setErrorMessage('');

    const controller = new AbortController();
    uploadAbortRef.current = controller;

    try {
      const uploadResult = await uploadPhoto({ file: selectedFile, signal: controller.signal });

      setState('processing');
      const { promise, cancel } = pollJobStatus(uploadResult.job_id, {
        intervalMs: 2000,
        timeoutMs: 120000,
        onUpdate: (status) => {
          setJobStatus(status);
        },
      });
      pollCancelRef.current = cancel;

      const statusResult = await promise;
      setJobStatus(statusResult);

      if (statusResult.status !== 'done') {
        throw new Error(statusResult.error || 'Something went wrong. Please try again.');
      }

      setState('done');
      onUploadComplete?.({
        job_id: uploadResult.job_id,
        user_id: null,
        status: 'done',
        spz_url: statusResult.spz_url,
        depth_map_url: statusResult.depth_map_url,
        created_at: new Date().toISOString(),
        filename: selectedFile.name,
        is_new: true,
      });

      closeTimerRef.current = setTimeout(() => {
        cleanupAsync();
        onClose?.();
      }, 2000);
    } catch (error) {
      if (error?.name === 'AbortError' || String(error?.message || '').toLowerCase().includes('aborted')) {
        resetToIdle();
        return;
      }

      setState('error');
      setErrorMessage(error?.message || 'Something went wrong. Please try again.');
    }
  };

  const cancelUpload = () => {
    uploadAbortRef.current?.abort();
    resetToIdle();
  };

  const allowBackdropClose = useMemo(() => ['idle', 'previewing', 'error'].includes(state), [state]);

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className={styles.backdrop}
      onClick={() => {
        if (allowBackdropClose) {
          cleanupAsync();
          onClose?.();
        }
      }}
    >
      <div
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-modal-title"
        ref={modalRef}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="upload-modal-title" className={styles.title}>Upload a photo</h2>

        {state === 'idle' ? (
          <>
            <div
              className={`${styles.dropZone} ${isDragOver ? styles.dragOver : ''}`}
              onClick={() => inputRef.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragOver(true);
              }}
              onDragLeave={() => setIsDragOver(false)}
              onDrop={handleDrop}
            >
              <div>
                <div>Drag and drop a photo here</div>
                <div>
                  or{' '}
                  <button className={styles.linkButton} type="button" onClick={() => inputRef.current?.click()}>
                    click to browse
                  </button>
                </div>
              </div>
            </div>
            <div className={styles.hint}>Accepted: JPEG, PNG, WebP, HEIC · Max size 20MB</div>
          </>
        ) : null}

        {state === 'previewing' ? (
          <div className={styles.previewWrap}>
            <img className={styles.preview} src={previewUrl} alt="Selected preview" />
            <div className={styles.meta}>
              {selectedFile?.name} · {formatBytes(selectedFile?.size || 0)}
            </div>
            <div className={styles.actions}>
              <button className={styles.primaryBtn} type="button" onClick={startUpload}>
                Upload
              </button>
              <button className={styles.secondaryBtn} type="button" onClick={resetToIdle}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}

        {state === 'uploading' ? (
          <div>
            <div>Uploading…</div>
            <div className={styles.progressTrack}>
              <div className={styles.progressFill} />
            </div>
            <button className={styles.secondaryBtn} type="button" onClick={cancelUpload}>
              Cancel
            </button>
          </div>
        ) : null}

        {state === 'processing' ? (
          <div className={styles.processing}>
            {depthMapUrl ? <img src={depthMapUrl} alt="Depth map" className={styles.processingDepth} /> : null}
            <div className={styles.processingContent}>
              <div className={styles.spinner} />
              <div>Converting to spatial photo…</div>
            </div>
          </div>
        ) : null}

        {state === 'done' ? (
          <div className={styles.done}>
            {depthMapUrl ? <img className={styles.preview} src={depthMapUrl} alt="Depth map thumbnail" /> : null}
            <div>✓ Spatial photo ready</div>
          </div>
        ) : null}

        {state === 'error' ? (
          <div className={styles.error}>
            <div>{errorMessage || 'Something went wrong. Please try again.'}</div>
            <div className={styles.actions}>
              <button className={styles.primaryBtn} type="button" onClick={resetToIdle}>
                Try again
              </button>
              <button
                className={styles.secondaryBtn}
                type="button"
                onClick={() => {
                  cleanupAsync();
                  onClose?.();
                }}
              >
                Close
              </button>
            </div>
          </div>
        ) : null}

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_TYPES.join(',')}
          style={{ display: 'none' }}
          onChange={handleInputChange}
        />
      </div>
    </div>
  );
}
