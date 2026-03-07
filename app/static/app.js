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
  const preferredOrder = ['.usdz', '.glb', '.gltf', '.heic', '.heif', '.mp4'];
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
  if (lowerName.endsWith('.usdz') || lowerName.endsWith('.glb') || lowerName.endsWith('.gltf')) {
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

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  setStatus('Uploading and running ML Sharp...');
  resultsEl.hidden = true;

  const payload = new FormData(form);

  try {
    const response = await fetch('/api/spatial-photos', {
      method: 'POST',
      body: payload,
    });

    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || 'Conversion failed.');
    }

    artifactListEl.innerHTML = '';
    for (const artifact of body.artifacts) {
      const li = document.createElement('li');
      const link = document.createElement('a');
      link.href = artifact.url;
      link.textContent = artifact.name;
      link.target = '_blank';
      link.rel = 'noreferrer';
      li.appendChild(link);
      artifactListEl.appendChild(li);
    }

    renderViewer(pickPreferredArtifact(body.artifacts));
    resultsEl.hidden = false;
    setStatus(`Spatial photo ready. Job ID: ${body.job_id}`);
  } catch (error) {
    setStatus(error.message || 'Unexpected error', true);
  } finally {
    submitBtn.disabled = false;
  }
});
