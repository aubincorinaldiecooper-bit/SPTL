const form = document.getElementById('upload-form');
const statusEl = document.getElementById('status');
const submitBtn = document.getElementById('submit-btn');
const resultsEl = document.getElementById('results');
const artifactListEl = document.getElementById('artifact-list');
const viewerContainerEl = document.getElementById('viewer-container');

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? '#ff8f8f' : '#a2b1d1';
}

function pickPreferredArtifact(artifacts) {
  const preferredOrder = ['.usdz', '.spz', '.glb', '.gltf', '.heic', '.heif', '.png', '.mp4'];
  for (const ext of preferredOrder) {
    const match = artifacts.find((artifact) => artifact.name.toLowerCase().endsWith(ext));
    if (match) return match;
  }
  return artifacts[0] ?? null;
}

function renderViewer(artifact) {
  viewerContainerEl.innerHTML = '';
  if (!artifact) return;

  const lowerName = artifact.name.toLowerCase();
  if (lowerName.endsWith('.usdz') || lowerName.endsWith('.spz') || lowerName.endsWith('.glb') || lowerName.endsWith('.gltf')) {
    const viewer = document.createElement('model-viewer');
    viewer.src = artifact.url;
    viewer.setAttribute('camera-controls', '');
    viewer.setAttribute('auto-rotate', '');
    viewer.setAttribute('ar', '');
    viewer.setAttribute('ar-modes', 'quick-look scene-viewer webxr');
    viewer.setAttribute('alt', 'Generated spatial photo');
    viewerContainerEl.appendChild(viewer);
    return;
  }

  if (lowerName.endsWith('.mp4')) {
    const video = document.createElement('video');
    video.controls = true;
    video.style.width = '100%';
    video.style.borderRadius = '14px';
    video.src = artifact.url;
    viewerContainerEl.appendChild(video);
    return;
  }

  const image = document.createElement('img');
  image.src = artifact.url;
  image.alt = artifact.name;
  image.style.width = '100%';
  image.style.borderRadius = '14px';
  viewerContainerEl.appendChild(image);
}

async function pollStatus(jobId, statusUrl) {
  while (true) {
    const pollResponse = await fetch(statusUrl || `/api/spatial-photos/${jobId}/status`);
    const pollBody = await pollResponse.json();

    if (!pollResponse.ok) {
      throw new Error(pollBody.error || 'Failed to poll job status.');
    }

    if (pollBody.status === 'done' || pollBody.status === 'failed') {
      return pollBody;
    }

    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

function toArtifacts(finalStatus) {
  const artifacts = [];
  if (finalStatus.spz_url) {
    artifacts.push({
      name: 'output.spz',
      url: finalStatus.spz_url,
    });
  }
  if (finalStatus.depth_map_url) {
    artifacts.push({
      name: 'depth.png',
      url: finalStatus.depth_map_url,
    });
  }
  return artifacts;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  setStatus('Uploading...');
  resultsEl.hidden = true;

  const payload = new FormData(form);

  try {
    const response = await fetch('/api/spatial-photos', {
      method: 'POST',
      body: payload,
    });

    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || body.detail || 'Upload failed.');
    }

    const jobId = body.job_id;
    const statusUrl = body.status_url;
    setStatus(`Converting... Job ID: ${jobId}`);

    const finalStatus = await pollStatus(jobId, statusUrl);
    if (finalStatus.status === 'failed') {
      throw new Error(finalStatus.error || 'Conversion failed.');
    }

    const artifacts = toArtifacts(finalStatus);
    if (!artifacts.length) {
      throw new Error('Conversion finished but no artifacts were returned.');
    }

    artifactListEl.innerHTML = '';
    for (const artifact of artifacts) {
      const li = document.createElement('li');
      const link = document.createElement('a');
      link.href = artifact.url;
      link.textContent = artifact.name;
      link.target = '_blank';
      link.rel = 'noreferrer';
      li.appendChild(link);
      artifactListEl.appendChild(li);
    }

    renderViewer(pickPreferredArtifact(artifacts));
    resultsEl.hidden = false;
    setStatus(`Spatial photo ready. Job ID: ${jobId}`);
  } catch (error) {
    setStatus(error.message || 'Unexpected error', true);
  } finally {
    submitBtn.disabled = false;
  }
});
