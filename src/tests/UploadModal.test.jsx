import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UploadModal from '../components/UploadModal';

vi.mock('../api/feedApi', () => ({
  uploadPhoto: vi.fn(),
  pollJobStatus: vi.fn(),
}));

import { pollJobStatus, uploadPhoto } from '../api/feedApi';

describe('UploadModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders file picker in idle state when open', () => {
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    expect(screen.getByText(/drag and drop a photo here/i)).not.toBeNull();
  });

  it('dragging file over zone adds highlight class', () => {
    const { container } = render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const zone = screen.getByText(/drag and drop a photo here/i).closest('div');
    fireEvent.dragOver(zone);
    expect(container.querySelector('[class*="dragOver"]')).toBeTruthy();
  });

  it('selecting valid file transitions to previewing state', async () => {
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText(/photo.jpg/i)).not.toBeNull();
    expect(screen.getByRole('button', { name: /upload/i })).not.toBeNull();
  });

  it('clicking cancel in previewing resets to idle', async () => {
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /^cancel$/i }));

    expect(screen.getByText(/drag and drop a photo here/i)).not.toBeNull();
  });

  it('upload button triggers uploadPhoto and transitions to uploading', async () => {
    uploadPhoto.mockImplementation(() => new Promise(() => {}));
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));

    expect(uploadPhoto).toHaveBeenCalled();
    expect(await screen.findByText(/uploading/i)).not.toBeNull();
  });

  it('AbortController cancel on upload cancellation resets to idle', async () => {
    let rejectFn;
    uploadPhoto.mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectFn = reject;
        })
    );

    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^cancel$/i }));

    rejectFn(new DOMException('Aborted', 'AbortError'));

    await waitFor(() => {
      expect(screen.getByText(/drag and drop a photo here/i)).not.toBeNull();
    });
  });

  it('413 response shows correct error message', async () => {
    uploadPhoto.mockRejectedValueOnce(new Error('File is too large (max 20MB)'));
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));

    expect(await screen.findByText(/file is too large/i)).not.toBeNull();
  });

  it('429 response shows retry_after in error message', async () => {
    uploadPhoto.mockRejectedValueOnce(new Error('Too many uploads. Please wait 7s before trying again.'));
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));

    expect(await screen.findByText(/wait 7s/i)).not.toBeNull();
  });

  it('onUploadComplete called with job metadata on successful poll', async () => {
    uploadPhoto.mockResolvedValueOnce({ job_id: 'job-1', status: 'pending', status_url: '/api/spatial-photos/job-1/status' });
    pollJobStatus.mockReturnValueOnce({
      promise: Promise.resolve({
        status: 'done',
        spz_url: '/generated/job-1/output.spz',
        depth_map_url: '/generated/job-1/depth.png',
      }),
      cancel: vi.fn(),
    });

    const onUploadComplete = vi.fn();
    render(<UploadModal isOpen onClose={vi.fn()} onUploadComplete={onUploadComplete} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));

    await waitFor(() => {
      expect(onUploadComplete).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'job-1' }));
    });
  });

  it('Escape key closes modal when in idle state', () => {
    const onClose = vi.fn();
    render(<UploadModal isOpen onClose={onClose} onUploadComplete={vi.fn()} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('backdrop click ignored during processing state', async () => {
    uploadPhoto.mockResolvedValueOnce({ job_id: 'job-1', status: 'pending', status_url: '/api/spatial-photos/job-1/status' });
    pollJobStatus.mockReturnValueOnce({ promise: new Promise(() => {}), cancel: vi.fn() });

    const onClose = vi.fn();
    const { container } = render(<UploadModal isOpen onClose={onClose} onUploadComplete={vi.fn()} />);
    const input = document.querySelector('input[type="file"]');
    const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole('button', { name: /upload/i }));

    const backdrop = container.firstChild;
    fireEvent.click(backdrop);
    expect(onClose).not.toHaveBeenCalled();
  });
});
