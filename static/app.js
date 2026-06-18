/* ═══════════════════════════════════════════════════════════
   YT Automator — Frontend Logic v2
   Full rewrite: Download → Studio → Queue workflow
   ═══════════════════════════════════════════════════════════ */

// ─── State ──────────────────────────────────────────────────

const state = {
  // Download tab
  videos: [],
  selectedVideos: new Set(),
  channelName: '',
  sortBy: 'latest',
  videoCount: 10,
  downloadHistory: new Set(),
  preparations: new Map(),
  downloadResults: new Map(),

  // Studio tab
  channels: [],
  selectedChannel: '',
  channelItems: [],

  // Image library
  libraryImages: [],

  // Queue
  queueJobs: [],

  // Video modal
  vmItem: null,
  vmChannel: '',
  vmSelectedImages: [],

  // Thumbnail modal
  tmItem: null,
  tmChannel: '',
  tmMode: 'studio',
  tmPrepVideo: null,
  tmSelectedBg: '',
  tmBgImg: null,
  tmHighlightedWords: new Set(),
  tmFontSize: 72,
  tmPosX: 50,
  tmPosY: 72,
  tmFontName: 'Impact',
  tmTextColor: '#ffffff',
  tmHighlightColor: '#ffd636',
  tmBoxColor: '#000000',
  tmBoxOpacity: 0.70,
  tmBoxWidth: 52,
  tmFitBox: true,
  tmLineScales: [],
  tmShadowMode: 'both',
  tmTextAlign: 'center',
  tmPreviewBlock: null,
  tmDragging: false,
  tmDragOffsetX: 0,
  tmDragOffsetY: 0,

  // Prepare video modal for fetched videos
  pmVideo: null,
  pmSelectedImages: [],

  // Edit metadata modal
  mdmItem: null,
  mdmChannel: '',
};

const HISTORY_KEY = 'yt-automator-download-history';

// Cache of already-loaded canvas fonts: name → CSS font-family string to use
const _loadedCanvasFonts = new Map();

async function loadFontForCanvas(fontName) {
  if (!fontName || fontName === 'Impact') return fontName; // Impact is a browser built-in
  if (_loadedCanvasFonts.has(fontName)) return _loadedCanvasFonts.get(fontName);

  try {
    const url = `/api/fonts/${encodeURIComponent(fontName)}/file`;
    const face = new FontFace(fontName, `url(${url})`);
    await face.load();
    document.fonts.add(face);
    _loadedCanvasFonts.set(fontName, fontName);
    return fontName;
  } catch (e) {
    console.warn(`Could not load font "${fontName}" for canvas preview:`, e);
    _loadedCanvasFonts.set(fontName, 'Impact'); // fall back
    return 'Impact';
  }
}



// ─── Init ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  loadDownloadHistory();
  await loadServerDownloadHistory();
  setupTabs();
  setupDownloadTab();
  setupStudioTab();
  setupQueueTab();
  setupVideoModal();
  setupThumbnailModal();
  setupPrepareModal();
  setupMetadataModal();
  startQueuePolling();
  loadAvailableFonts();
});


// ─── Download History (localStorage) ────────────────────────

function loadDownloadHistory() {
  try {
    const arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    state.downloadHistory = new Set(arr);
  } catch { state.downloadHistory = new Set(); }
}

function saveDownloadHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify([...state.downloadHistory]));
}

async function loadServerDownloadHistory() {
  try {
    const res = await fetch('/api/download-history');
    const data = await res.json();
    if (data.status === 'ok' && Array.isArray(data.video_ids)) {
      data.video_ids.forEach(id => state.downloadHistory.add(id));
      saveDownloadHistory();
    }
  } catch (e) { console.error('History sync:', e); }
}

function markAsDownloaded(videoId) {
  state.downloadHistory.add(videoId);
  saveDownloadHistory();
}


// ─── Tab Switching ──────────────────────────────────────────

function setupTabs() {
  document.querySelectorAll('.tab').forEach(tab =>
    tab.addEventListener('click', () => switchTab(tab.dataset.tab))
  );
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === name);
    t.setAttribute('aria-selected', t.dataset.tab === name);
  });
  document.querySelectorAll('.tab-panel').forEach(p =>
    p.classList.toggle('active', p.id === `tab-${name}`)
  );
  if (name === 'studio') refreshStudio();
  if (name === 'queue')  refreshQueue();
}


// ─── Toast Notifications ────────────────────────────────────

function showToast(message, type = 'info') {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  el.innerHTML = `<span class="toast-icon">${icons[type] || 'ℹ'}</span><span>${message}</span>`;
  box.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 400);
  }, 4000);
}


// ─── Button Loading Helper ──────────────────────────────────

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  const txt = btn.querySelector('.btn-text');
  if (loading) {
    btn.disabled = true;
    if (txt) { txt.dataset.orig = txt.textContent; txt.innerHTML = '<span class="spinner"></span> Processing…'; }
  } else {
    btn.disabled = false;
    if (txt && txt.dataset.orig) txt.textContent = txt.dataset.orig;
  }
}


// ─── Helpers ────────────────────────────────────────────────

function formatViews(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

function formatDuration(s) {
  if (!s && s !== 0) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
    : `${m}:${String(sec).padStart(2,'0')}`;
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function prepKey(video) {
  return video?.id || video?.url || video?.title || '';
}

function getPrimaryItemPath(item) {
  return item?.paths?.primary || '';
}

async function copyItemTitle(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  const title = item?.title || '';
  if (!title) {
    showToast('No title available yet', 'error');
    return;
  }

  try {
    await navigator.clipboard.writeText(title);
    showToast('Title copied', 'success');
  } catch (e) {
    showToast('Could not copy title: ' + e.message, 'error');
  }
}

async function copyItemDescription(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  const description = item?.description || '';
  if (!description) {
    showToast('No description saved for this item yet', 'error');
    return;
  }

  try {
    await navigator.clipboard.writeText(description);
    showToast('Description copied', 'success');
  } catch (e) {
    showToast('Could not copy description: ' + e.message, 'error');
  }
}

async function copyItemTags(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  const tags = Array.isArray(item?.tags) ? item.tags : [];
  if (!tags.length) {
    showToast('No tags saved for this item yet', 'error');
    return;
  }

  try {
    await navigator.clipboard.writeText(tags.join(', '));
    showToast('Tags copied', 'success');
  } catch (e) {
    showToast('Could not copy tags: ' + e.message, 'error');
  }
}

async function copyItemPath(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  const path = getPrimaryItemPath(item);
  if (!path) {
    showToast('No local file path available yet', 'error');
    return;
  }

  try {
    await navigator.clipboard.writeText(path);
    showToast('Local path copied', 'success');
  } catch (e) {
    showToast('Could not copy path: ' + e.message, 'error');
  }
}

async function syncItemMetadata(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  if (!item) return;

  const fd = new FormData();
  fd.append('channel_name', state.selectedChannel);
  fd.append('item_key', item.key || item.title);

  try {
    const res = await fetch('/api/item/sync-metadata', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ok') {
      item.title = data.item?.title || item.title;
      item.description = data.item?.description || '';
      item.tags = data.item?.tags || [];
      renderStudioItems(state.channelItems, state.selectedChannel);
      showToast('Metadata synced', 'success');
    } else {
      showToast(data.message || data.detail || 'Could not sync metadata', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function openItemFolder(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  if (!item) return;

  const fd = new FormData();
  fd.append('channel_name', state.selectedChannel);
  fd.append('item_key', item.key || item.title);

  try {
    const res = await fetch('/api/item/open-folder', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Opened folder in Explorer', 'success');
    } else {
      showToast(data.message || data.detail || 'Could not open folder', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}


// ═══════════════════════════════════════════════════════════
//  TAB 1 — DOWNLOAD
// ═══════════════════════════════════════════════════════════

function setupDownloadTab() {
  document.getElementById('fetch-btn').addEventListener('click', fetchChannelVideos);
  document.getElementById('channel-url').addEventListener('keydown', e => {
    if (e.key === 'Enter') fetchChannelVideos();
  });

  document.querySelectorAll('#sort-toggle .toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#sort-toggle .toggle').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.sortBy = btn.dataset.sort;
    });
  });

  const slider = document.getElementById('video-count');
  const label  = document.getElementById('count-value');
  syncSliderVal(slider);
  slider.addEventListener('input', () => {
    state.videoCount = +slider.value;
    label.textContent = slider.value;
    syncSliderVal(slider);
  });

  document.getElementById('select-all-btn').addEventListener('click', toggleSelectAll);
  document.getElementById('download-btn').addEventListener('click', downloadSelected);
}


async function fetchChannelVideos() {
  const url = document.getElementById('channel-url').value.trim();
  if (!url) { showToast('Enter a channel URL', 'error'); return; }

  setLoading('fetch-btn', true);
  document.getElementById('fetch-loading').classList.remove('hidden');
  document.getElementById('video-list-container').classList.add('hidden');

  try {
    const fd = new FormData();
    fd.append('channel_url', url);
    fd.append('sort_by', state.sortBy);
    fd.append('limit', state.videoCount);

    const res = await fetch('/api/fetch-channel', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.status === 'ok') {
      state.videos      = data.videos;
      state.channelName  = data.channel_name;
      state.selectedVideos.clear();

      // Default select all — smart deselect previously downloaded
      state.videos.forEach((v, i) => {
        if (!state.downloadHistory.has(v.id)) state.selectedVideos.add(i);
      });

      renderVideoList();
      showToast(`Found ${data.videos.length} videos from ${data.channel_name}`, 'success');
    } else {
      showToast(data.message || 'Fetch failed', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }

  setLoading('fetch-btn', false);
  document.getElementById('fetch-loading').classList.add('hidden');
}


function renderVideoList() {
  const list = document.getElementById('video-list');
  const wrap = document.getElementById('video-list-container');

  document.getElementById('channel-name-display').textContent = '📺 ' + state.channelName;

  if (!state.videos.length) {
    list.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><p>No videos found</p></div>';
    wrap.classList.remove('hidden');
    return;
  }

  list.innerHTML = state.videos.map((v, i) => {
    const dl  = state.downloadHistory.has(v.id);
    const sel = state.selectedVideos.has(i);
    const prepared = state.preparations.has(prepKey(v));
    return `
      <div class="video-card ${sel ? 'selected' : ''} ${dl ? 'downloaded' : ''} ${prepared ? 'prepared' : ''}" data-idx="${i}" data-video-id="${esc(v.id || '')}">
        <input type="checkbox" class="video-checkbox" ${sel ? 'checked' : ''}>
        <div class="video-info">
          <div class="video-title" title="${esc(v.title)}">${esc(v.title)}</div>
          <div class="video-meta">
            <span>👁 ${formatViews(v.view_count)}</span>
            <span>⏱ ${formatDuration(v.duration)}</span>
            ${v.upload_date ? `<span>📅 ${v.upload_date}</span>` : ''}
          </div>
        </div>
        <div class="video-actions-inline">
          <button class="btn-icon-sm btn-prepare-video" onclick="openPrepareVideoModal(event, ${i})" title="Prepare video render">
            Render
          </button>
          <button class="btn-icon-sm btn-prepare-thumb" onclick="openPrepareThumbnailModal(event, ${i})" title="Prepare thumbnail">
            Thumb
          </button>
          <button class="btn-icon-sm btn-dl-single" onclick="downloadSingleVideo(event, ${i})" title="Download/Retry this video">
            📥
          </button>
        </div>
        <span class="video-prepare-badge" ${prepared ? 'style="display:inline-flex"' : ''}>Prepared</span>
        <span class="video-history-badge" ${dl ? 'style="display:inline-flex"' : ''}>✓ Downloaded</span>
        <div class="video-status" id="status-${i}"></div>
      </div>`;
  }).join('');

  list.querySelectorAll('.video-card').forEach(card => {
    const idx = +card.dataset.idx;
    card.addEventListener('click', e => {
      if (
        e.target.classList.contains('video-checkbox') ||
        e.target.closest('.btn-dl-single') ||
        e.target.closest('.btn-prepare-video') ||
        e.target.closest('.btn-prepare-thumb')
      ) return;
      const cb = card.querySelector('.video-checkbox');
      cb.checked = !cb.checked;
      toggleVideo(idx, cb.checked);
    });
    card.querySelector('.video-checkbox').addEventListener('change', e =>
      toggleVideo(idx, e.target.checked)
    );
  });

  wrap.classList.remove('hidden');
  syncSelectBtn();
  syncDownloadBtn();
}


function toggleVideo(i, on) {
  on ? state.selectedVideos.add(i) : state.selectedVideos.delete(i);
  const card = document.querySelector(`.video-card[data-idx="${i}"]`);
  if (card) card.classList.toggle('selected', on);
  syncSelectBtn();
  syncDownloadBtn();
}

function toggleSelectAll() {
  if (state.selectedVideos.size === state.videos.length) {
    state.selectedVideos.clear();
  } else {
    state.videos.forEach((_, i) => state.selectedVideos.add(i));
  }
  renderVideoList();
}

function syncSelectBtn() {
  document.getElementById('select-all-btn').textContent =
    state.selectedVideos.size === state.videos.length ? 'Deselect All' : 'Select All';
}

function syncDownloadBtn() {
  const btn = document.getElementById('download-btn');
  const n   = state.selectedVideos.size;
  btn.disabled = n === 0;
  btn.querySelector('.btn-text').textContent = n > 0 ? `Download ${n} Selected` : 'Download Selected';
}


function getLiveVideoCard(idx, videoId) {
  const card = document.querySelector(`.video-card[data-idx="${idx}"]`);
  if (!card) return null;
  return card.dataset.videoId === (videoId || '') ? card : null;
}

function getLiveStatusEl(idx, videoId) {
  const card = getLiveVideoCard(idx, videoId);
  return card ? card.querySelector(`#status-${idx}`) : null;
}


async function downloadSelected() {
  if (!state.selectedVideos.size) return;

  const indices = [...state.selectedVideos].sort((a, b) => a - b);
  const tasks = indices
    .map(i => ({
      idx: i,
      video: state.videos[i] ? { ...state.videos[i] } : null,
      channelName: state.channelName,
    }))
    .filter(t => t.video);

  document.getElementById('download-btn').disabled = true;
  let ok = 0;

  for (const task of tasks) {
    const i = task.idx;
    const v = task.video;
    const el = getLiveStatusEl(i, v.id);
    if (el) {
      el.className = 'video-status downloading';
      el.innerHTML = '<span class="spinner"></span> Downloading...';
    }

    try {
      const fd = new FormData();
      fd.append('video_url', v.url);
      fd.append('channel_name', task.channelName);

      const res  = await fetch('/api/download', { method: 'POST', body: fd });
      const data = await res.json();

      if (data.status === 'ok') {
        if (el) {
          el.className = 'video-status done';
          el.textContent = 'Done';
        }
        markAsDownloaded(v.id);
        state.downloadResults.set(prepKey(v), {
          result: data.result,
          channelName: task.channelName,
        });
        ok++;
        const card = getLiveVideoCard(i, v.id);
        if (card) {
          card.classList.add('downloaded');
          const badge = card.querySelector('.video-history-badge');
          if (badge) badge.style.display = 'inline-flex';
        }
        await runPreparedActions(v, data.result, task.channelName);
      } else {
        if (el) {
          el.className = 'video-status error';
          el.textContent = 'Failed';
        }
      }
    } catch {
      if (el) {
        el.className = 'video-status error';
        el.textContent = 'Error';
      }
    }
  }

  showToast(`Downloaded ${ok}/${tasks.length} items`, ok > 0 ? 'success' : 'error');
  if (ok > 0) refreshStudio();
  syncDownloadBtn();
}

async function downloadSingleVideo(event, idx) {
  event.stopPropagation();

  const v = state.videos[idx] ? { ...state.videos[idx] } : null;
  const channelName = state.channelName;
  if (!v) return;

  const el = getLiveStatusEl(idx, v.id);
  const btn = event.currentTarget;

  btn.disabled = true;
  if (el) {
    el.className = 'video-status downloading';
    el.innerHTML = '<span class="spinner"></span> Downloading...';
  }

  try {
    const fd = new FormData();
    fd.append('video_url', v.url);
    fd.append('channel_name', channelName);

    const res  = await fetch('/api/download', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.status === 'ok') {
      if (el) {
        el.className = 'video-status done';
        el.textContent = 'Done';
      }
      markAsDownloaded(v.id);
      state.downloadResults.set(prepKey(v), {
        result: data.result,
        channelName,
      });

      const card = getLiveVideoCard(idx, v.id);
      if (card) {
        card.classList.add('downloaded');
        const badge = card.querySelector('.video-history-badge');
        if (badge) badge.style.display = 'inline-flex';
      }
      await runPreparedActions(v, data.result, channelName);
      showToast(`Downloaded: ${v.title}`, 'success');
      refreshStudio();
    } else {
      if (el) {
        el.className = 'video-status error';
        el.textContent = 'Failed';
      }
      showToast(data.message || 'Download failed', 'error');
    }
  } catch (err) {
    if (el) {
      el.className = 'video-status error';
      el.textContent = 'Error';
    }
    showToast('Network error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}


// ═══════════════════════════════════════════════════════════
//  TAB 2 — STUDIO
// ═══════════════════════════════════════════════════════════

function getPreparation(video) {
  return state.preparations.get(prepKey(video)) || null;
}

function savePreparation(video, patch) {
  const key = prepKey(video);
  const existing = getPreparation(video) || {};
  state.preparations.set(key, {
    video: { ...video },
    renderImages: Array.isArray(patch.renderImages) ? [...patch.renderImages] : [...(existing.renderImages || [])],
    thumbBg: patch.thumbBg !== undefined ? patch.thumbBg : (existing.thumbBg || ''),
    thumbText: patch.thumbText !== undefined ? patch.thumbText : (existing.thumbText || ''),
    highlightedWords: patch.highlightedWords !== undefined ? [...patch.highlightedWords] : [...(existing.highlightedWords || [])],
    thumbFontSize: patch.thumbFontSize !== undefined ? patch.thumbFontSize : (existing.thumbFontSize || 72),
    thumbPosX: patch.thumbPosX !== undefined ? patch.thumbPosX : (existing.thumbPosX || 50),
    thumbPosY: patch.thumbPosY !== undefined ? patch.thumbPosY : (existing.thumbPosY || 72),
    thumbFontName: patch.thumbFontName !== undefined ? patch.thumbFontName : (existing.thumbFontName || 'Impact'),
    thumbTextColor: patch.thumbTextColor !== undefined ? patch.thumbTextColor : (existing.thumbTextColor || '#ffffff'),
    thumbHighlightColor: patch.thumbHighlightColor !== undefined ? patch.thumbHighlightColor : (existing.thumbHighlightColor || '#ffd636'),
    thumbBoxColor: patch.thumbBoxColor !== undefined ? patch.thumbBoxColor : (existing.thumbBoxColor || '#000000'),
    thumbBoxOpacity: patch.thumbBoxOpacity !== undefined ? patch.thumbBoxOpacity : (existing.thumbBoxOpacity !== undefined ? existing.thumbBoxOpacity : 0.70),
    thumbBoxWidth: patch.thumbBoxWidth !== undefined ? patch.thumbBoxWidth : (existing.thumbBoxWidth || 52),
    thumbFitBox: patch.thumbFitBox !== undefined ? patch.thumbFitBox : (existing.thumbFitBox !== undefined ? existing.thumbFitBox : true),
    thumbLineScales: patch.thumbLineScales !== undefined ? [...patch.thumbLineScales] : [...(existing.thumbLineScales || [])],
    thumbShadowMode: patch.thumbShadowMode !== undefined ? patch.thumbShadowMode : (existing.thumbShadowMode || 'both'),
    thumbTextAlign: patch.thumbTextAlign !== undefined ? patch.thumbTextAlign : (existing.thumbTextAlign || 'center'),
  });
  syncPreparedCard(video);
}

function syncPreparedCard(video) {
  const card = document.querySelector(`.video-card[data-video-id="${video.id || ''}"]`);
  if (!card) return;
  const prepared = state.preparations.has(prepKey(video));
  card.classList.toggle('prepared', prepared);
  const badge = card.querySelector('.video-prepare-badge');
  if (badge) badge.style.display = prepared ? 'inline-flex' : 'none';
}

async function runCompletedPreparationIfReady(video) {
  const completed = state.downloadResults.get(prepKey(video));
  if (completed) {
    await runPreparedActions(video, completed.result, completed.channelName);
  }
}

// Prepare fetched videos before their downloads finish.
function setupPrepareModal() {
  document.querySelectorAll('[data-modal="prepare-modal"]').forEach(b =>
    b.addEventListener('click', () => closeModal('prepare-modal'))
  );
  document.getElementById('pm-upload-btn').addEventListener('click', () =>
    document.getElementById('pm-upload-input').click()
  );
  document.getElementById('pm-upload-input').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    try {
      for (const file of e.target.files) {
        const name = await uploadToLibrary(file);
        if (!state.pmSelectedImages.includes(name)) state.pmSelectedImages.push(name);
      }
      renderPrepareGrid();
      syncPrepareLabels();
    } catch (err) {
      showToast(err.message, 'error');
    }
    e.target.value = '';
  });
  document.getElementById('pm-submit').addEventListener('click', submitPreparation);
  document.getElementById('prepare-modal').addEventListener('click', e => {
    if (e.target.id === 'prepare-modal') closeModal('prepare-modal');
  });
}

async function openPrepareVideoModal(event, idx) {
  event.stopPropagation();
  const video = state.videos[idx] ? { ...state.videos[idx] } : null;
  if (!video) return;

  const existing = getPreparation(video);
  state.pmVideo = video;
  state.pmSelectedImages = existing ? [...(existing.renderImages || [])] : [];

  document.getElementById('pm-title').textContent = video.title;
  document.getElementById('pm-channel').textContent = state.channelName;

  // Open gallery to pick render images, then show prepare modal
  openGallery('multi', state.channelName, state.pmSelectedImages, (selectedNames) => {
    state.pmSelectedImages = selectedNames;
    syncPrepareLabels();

    // Refresh inline grid and show the prepare modal
    loadLibrary().then(() => {
      renderPrepareGrid();
      document.getElementById('prepare-modal').classList.remove('hidden');
    });
  });
}

function renderPrepareGrid() {
  renderLibraryGrid('pm-render-library', state.pmSelectedImages, pmToggleRenderImage);
}

function syncPrepareLabels() {
  const n = state.pmSelectedImages.length;
  document.getElementById('pm-render-count').textContent =
    n ? `${n} image${n === 1 ? '' : 's'} selected` : 'None';
}

function pmToggleRenderImage(name) {
  if (state.pmSelectedImages.includes(name)) {
    state.pmSelectedImages = state.pmSelectedImages.filter(n => n !== name);
  } else {
    state.pmSelectedImages.push(name);
  }
  renderPrepareGrid();
  syncPrepareLabels();
}

async function submitPreparation() {
  if (!state.pmVideo) return;
  if (!state.pmSelectedImages.length) {
    showToast('Choose at least one render image', 'error');
    return;
  }

  savePreparation(state.pmVideo, {
    renderImages: [...state.pmSelectedImages],
  });

  closeModal('prepare-modal');
  showToast('Render setup saved. It will queue after download finishes.', 'success');
  await runCompletedPreparationIfReady(state.pmVideo);
}

async function runPreparedActions(video, downloadResult, fallbackChannelName) {
  const prep = state.preparations.get(prepKey(video));
  if (!prep || !downloadResult?.audio_filename) return;

  const channelName = downloadResult.channel_name || fallbackChannelName;
  const audioFile = downloadResult.audio_filename;
  const actions = [];

  try {
    const thumbBg = prep.thumbBg;
    if (thumbBg && prep.thumbText) {
      const fd = new FormData();
      fd.append('channel_name', channelName);
      fd.append('audio_file', audioFile);
      fd.append('library_image', thumbBg);
      fd.append('text', prep.thumbText);
      fd.append('highlighted_words', prep.highlightedWords.join(','));
      fd.append('font_size', prep.thumbFontSize || 72);
      fd.append('pos_x', prep.thumbPosX || 50);
      fd.append('pos_y', prep.thumbPosY || 72);
      fd.append('font_name', prep.thumbFontName || 'Impact');
      fd.append('text_color', prep.thumbTextColor || '#ffffff');
      fd.append('highlight_color', prep.thumbHighlightColor || '#ffd636');
      fd.append('box_color', prep.thumbBoxColor || '#000000');
      fd.append('box_opacity', prep.thumbBoxOpacity !== undefined ? prep.thumbBoxOpacity : 0.70);
      fd.append('box_width', prep.thumbBoxWidth || 52);
      fd.append('fit_box', prep.thumbFitBox !== undefined ? String(prep.thumbFitBox) : 'true');
      fd.append('line_scales', JSON.stringify(prep.thumbLineScales || []));
      fd.append('shadow_mode', prep.thumbShadowMode || 'both');
      fd.append('text_align', prep.thumbTextAlign || 'center');
      const res = await fetch('/api/create-thumbnail', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.status === 'ok') actions.push('thumbnail');
      else throw new Error(data.message || data.detail || 'Thumbnail failed');
    }

    if (prep.renderImages.length) {
      const fd = new FormData();
      fd.append('channel_name', channelName);
      fd.append('audio_file', audioFile);
      fd.append('library_images', JSON.stringify(prep.renderImages));
      const res = await fetch('/api/queue/video', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.status === 'ok') actions.push('video queued');
      else throw new Error(data.message || data.detail || 'Queue failed');
    }

    state.preparations.delete(prepKey(video));
    syncPreparedCard(video);
    showToast(`Prepared actions done: ${actions.join(', ')}`, 'success');
    refreshQueue();
  } catch (err) {
    showToast(`Prepared action failed: ${err.message}`, 'error');
  }
}

function setupStudioTab() {
  document.getElementById('channel-select').addEventListener('change', e => {
    state.selectedChannel = e.target.value;
    loadChannelItems();
  });
  document.getElementById('refresh-studio-btn').addEventListener('click', refreshStudio);
}

async function refreshStudio() {
  try {
    const res  = await fetch('/api/channels');
    const data = await res.json();
    if (data.status !== 'ok') return;

    state.channels = data.channels;
    const sel = document.getElementById('channel-select');
    const cur = sel.value;
    sel.innerHTML = '<option value="">Select a channel…</option>';
    data.channels.forEach(ch => {
      const o = document.createElement('option');
      o.value = ch.name;
      o.textContent = `${ch.name} (${ch.count})`;
      sel.appendChild(o);
    });
    if (cur) { sel.value = cur; if (sel.value) loadChannelItems(); }
  } catch (e) { console.error('Studio refresh:', e); }
}

async function loadChannelItems() {
  const ch   = state.selectedChannel;
  const grid = document.getElementById('studio-items');
  const empty = document.getElementById('studio-empty');

  if (!ch) { grid.innerHTML = ''; empty.classList.remove('hidden'); return; }

  try {
    const res  = await fetch(`/api/channels/${encodeURIComponent(ch)}`);
    const data = await res.json();

    if (data.status === 'ok' && data.items.length) {
      state.channelItems = data.items;
      empty.classList.add('hidden');
      renderStudioItems(data.items, ch);
    } else {
      grid.innerHTML = '';
      empty.querySelector('p').textContent = 'No items in this channel yet';
      empty.classList.remove('hidden');
    }
  } catch { showToast('Failed to load channel', 'error'); }
}

function renderStatusBadge(className, isOn, yesText, noText) {
  return `<span class="badge-sm ${className} ${isOn ? 'on' : 'off'}">${isOn ? yesText : noText}</span>`;
}

function renderUploadedBadge(item, idx) {
  const uploaded = !!item.status?.uploaded;
  return `
    <button type="button"
            class="badge-sm badge-uploaded ${uploaded ? 'on' : 'off'} badge-toggle"
            onclick="toggleUploadedStatus(event, ${idx})"
            title="Toggle manual YouTube upload status">
      ${uploaded ? 'Uploaded' : 'Not uploaded'}
    </button>`;
}

function renderStudioItems(items, channel) {
  const grid = document.getElementById('studio-items');

  grid.innerHTML = items.map((item, i) => {
    const status = item.status || {};
    const files = item.files || {};
    const hasAudio = !!files.audio;
    const localAvailable = !!status.local_available;
    const hasPrimaryPath = !!getPrimaryItemPath(item);
    const hasDescription = !!item.description;
    const hasTags = Array.isArray(item.tags) && item.tags.length > 0;
    const thumbUrl = files.thumbnail
      ? `/output/${encodeURIComponent(channel)}/${encodeURIComponent(files.thumbnail)}`
      : '';
    const cardClasses = [
      'studio-card',
      status.downloaded ? '' : 'not-downloaded',
      localAvailable ? '' : 'local-missing',
    ].filter(Boolean).join(' ');
    const disabled = hasAudio ? '' : 'disabled title="Audio file missing locally"';

    return `
      <div class="${cardClasses}">
        ${thumbUrl
          ? `<img class="studio-card-thumb" src="${thumbUrl}" alt="${esc(item.title)}" loading="lazy">`
          : `<div class="studio-card-thumb studio-card-placeholder">Audio</div>`}
        <div class="studio-card-body">
          <div class="studio-card-title" title="${esc(item.title)}">${esc(item.title)}</div>
          <div class="studio-card-badges">
            ${renderStatusBadge('badge-downloaded', !!status.downloaded, 'Downloaded', 'Not downloaded')}
            ${renderStatusBadge('badge-rendered', !!status.rendered, 'Rendered', 'Not rendered')}
            ${renderStatusBadge('badge-local', localAvailable, 'Local', 'Local missing')}
            ${renderUploadedBadge(item, i)}
          </div>
          <div class="studio-card-actions">
            <button class="btn-icon" onclick="openVideoModal(${i})" ${disabled}>Video</button>
            <button class="btn-icon" onclick="openThumbnailModal(${i})" ${disabled}>Thumb</button>
            <button class="btn-icon" onclick="openMetadataModal(${i})">Edit</button>
          </div>
          <div class="studio-card-actions studio-card-actions-secondary">
            <button class="btn-icon" onclick="copyItemTitle(event, ${i})">Copy Title</button>
            <button class="btn-icon" onclick="copyItemDescription(event, ${i})" ${hasDescription ? '' : 'disabled title="Description not saved for this item yet"'}>Copy Description</button>
          </div>
          <div class="studio-card-actions studio-card-actions-secondary">
            <button class="btn-icon" onclick="copyItemTags(event, ${i})" ${hasTags ? '' : 'disabled title="Tags not saved for this item yet"'}>Copy Tags</button>
            <button class="btn-icon" onclick="syncItemMetadata(event, ${i})">Sync Metadata</button>
          </div>
          <div class="studio-card-actions studio-card-actions-secondary">
            <button class="btn-icon" onclick="openItemFolder(event, ${i})" ${hasPrimaryPath ? '' : 'disabled title="Local file missing locally"'}>Open Folder</button>
            <button class="btn-icon" onclick="copyItemPath(event, ${i})" ${hasPrimaryPath ? '' : 'disabled title="Local file missing locally"'}>Copy Path</button>
          </div>
        </div>
      </div>`;
  }).join('');
}


// ═══════════════════════════════════════════════════════════
//  TAB 3 — RENDER QUEUE
// ═══════════════════════════════════════════════════════════

async function toggleUploadedStatus(event, idx) {
  event.stopPropagation();
  const item = state.channelItems[idx];
  if (!item) return;

  const nextUploaded = !item.status?.uploaded;
  const fd = new FormData();
  fd.append('channel_name', state.selectedChannel);
  fd.append('item_key', item.key || item.title);
  fd.append('uploaded', String(nextUploaded));

  try {
    const res = await fetch('/api/item/upload-status', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ok') {
      item.status = item.status || {};
      item.status.uploaded = nextUploaded;
      renderStudioItems(state.channelItems, state.selectedChannel);
      showToast(nextUploaded ? 'Marked as uploaded' : 'Marked as not uploaded', 'success');
    } else {
      showToast(data.message || data.detail || 'Could not update upload status', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

function setupQueueTab() {
  document.getElementById('clear-queue-btn').addEventListener('click', async () => {
    await fetch('/api/queue/clear', { method: 'POST' });
    refreshQueue();
    showToast('Cleared completed jobs', 'info');
  });
}

function startQueuePolling() {
  refreshQueue();                       // initial
  setInterval(() => {
    if (state.queueJobs.some(j => j.status === 'pending' || j.status === 'rendering')) {
      refreshQueue();
    }
  }, 3000);
}

async function refreshQueue() {
  try {
    const res  = await fetch('/api/queue');
    const data = await res.json();
    if (data.status === 'ok') {
      const completedVideo = data.jobs.some(j => {
        const old = state.queueJobs.find(prev => prev.id === j.id);
        return j.job_type === 'video' && j.status === 'done' && old?.status !== 'done';
      });
      state.queueJobs = data.jobs;
      renderQueue(data.jobs);
      updateQueueBadge(data.jobs);
      if (completedVideo && state.selectedChannel) loadChannelItems();
    }
  } catch (e) { console.error('Queue poll:', e); }
}

function renderQueue(jobs) {
  const list  = document.getElementById('queue-list');
  const empty = document.getElementById('queue-empty');

  if (!jobs.length) { list.innerHTML = ''; empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');

  const ico = { pending: '⏳', rendering: '⚙️', done: '✅', failed: '❌' };

  list.innerHTML = jobs.map(j => {
    const pct = Math.max(0, Math.min(100, Number(j.progress || 0)));
    const showProgress = j.status === 'rendering' || j.status === 'done';
    return `
      <div class="queue-item">
        <div class="queue-item-icon ${j.status}">${ico[j.status] || '⏳'}</div>
        <div class="queue-item-info">
          <div class="queue-item-title">${esc(j.title)}</div>
          <div class="queue-item-detail">${j.job_type === 'video' ? '🎬 Video' : '🖼️ Thumbnail'} · ${esc(j.channel_name)}</div>
          ${showProgress ? `
            <div class="queue-progress" aria-label="Render progress">
              <div class="queue-progress-fill" style="width:${pct}%"></div>
            </div>` : ''}
        </div>
        <span class="queue-item-status ${j.status}">${j.status}${j.status === 'rendering' ? ` ${pct}%` : ''}</span>
        ${j.status === 'done' && j.result ? `<a href="${j.result.url}" download class="btn-ghost btn-sm" style="text-decoration:none" title="Download">⬇</a>` : ''}
        ${j.status === 'failed' ? `<button class="btn-ghost btn-sm btn-retry" onclick="retryQueueJob('${j.id}')" title="Retry job">Retry</button>` : ''}
        ${(j.status === 'pending' || j.status === 'rendering') ? `<button class="btn-ghost btn-sm btn-cancel" onclick="cancelQueueJob('${j.id}')" title="Cancel job" style="color:var(--error); margin-left:var(--gap-sm);">✕</button>` : ''}
      </div>`;
  }).join('');
}

async function retryQueueJob(jobId) {
  try {
    const res  = await fetch(`/api/queue/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Retry queued', 'success');
      refreshQueue();
    } else {
      showToast(data.detail || 'Retry failed', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function cancelQueueJob(jobId) {
  try {
    const res  = await fetch(`/api/queue/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Cancellation requested', 'success');
      refreshQueue();
    } else {
      showToast(data.detail || 'Cancellation failed', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

function updateQueueBadge(jobs) {
  const n = jobs.filter(j => j.status === 'pending' || j.status === 'rendering').length;
  const b = document.getElementById('queue-tab-badge');
  if (n > 0) { b.textContent = n; b.classList.remove('hidden'); }
  else       { b.classList.add('hidden'); }
}


// ═══════════════════════════════════════════════════════════
//  IMAGE LIBRARY (shared helper)
// ═══════════════════════════════════════════════════════════

async function loadLibrary() {
  try {
    const res  = await fetch('/api/library');
    const data = await res.json();
    if (data.status === 'ok') state.libraryImages = data.files;
  } catch (e) { console.error('Library load:', e); }
}

function isLibrarySelected(selectedNames, name) {
  if (selectedNames instanceof Set) return selectedNames.has(name);
  if (Array.isArray(selectedNames)) return selectedNames.includes(name);
  return selectedNames === name;
}

function renderLibraryGrid(containerId, selectedNames, onSelect) {
  const el = document.getElementById(containerId);
  el.innerHTML = '';

  const selArray = Array.isArray(selectedNames) ? selectedNames : 
                   (selectedNames ? [selectedNames] : []);
                   
  const toRender = [];
  const seen = new Set();

  // 1. Add selected items first (including external paths)
  for (const name of selArray) {
    if (!name) continue;
    seen.add(name);
    const libImg = state.libraryImages.find(i => i.name === name);
    if (libImg) {
      toRender.push(libImg);
    } else {
      toRender.push({
        name: name,
        url: (name.includes('/') || name.includes('\\'))
          ? `/api/gallery/serve-file?path=${encodeURIComponent(name)}`
          : '', // fallback safety
        isExternal: true
      });
    }
  }

  // 2. Add remaining library images
  for (const img of state.libraryImages) {
    if (!seen.has(img.name)) {
      toRender.push(img);
    }
  }

  if (!toRender.length) {
    el.innerHTML = '<p class="library-empty">No images yet — upload one below.</p>';
    return;
  }

  toRender.forEach(img => {
    if (!img.url) return;
    const div = document.createElement('div');
    div.className = `library-item ${isLibrarySelected(selectedNames, img.name) ? 'selected' : ''}`;
    div.innerHTML = `
      <img src="${img.url}" alt="${esc(img.name)}" loading="lazy">
      ${!img.isExternal ? '<button class="delete-btn" title="Remove">✕</button>' : ''}`;

    div.addEventListener('click', e => {
      if (e.target.classList.contains('delete-btn')) return;
      onSelect(img.name);
    });

    if (!img.isExternal) {
      div.querySelector('.delete-btn').addEventListener('click', async e => {
        e.stopPropagation();
        await fetch(`/api/library/${encodeURIComponent(img.name)}`, { method: 'DELETE' });
        await loadLibrary();
        renderLibraryGrid(containerId, selectedNames, onSelect);
        showToast('Image removed', 'info');
      });
    }

    el.appendChild(div);
  });
}

async function uploadToLibrary(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res  = await fetch('/api/library/upload', { method: 'POST', body: fd });
  const data = await res.json();
  if (data.status === 'ok') {
    await loadLibrary();
    showToast('Image added to library', 'success');
    return data.result.name;
  }
  throw new Error(data.message || 'Upload failed');
}


// ═══════════════════════════════════════════════════════════
//  MODAL — VIDEO CREATION
// ═══════════════════════════════════════════════════════════

function setupVideoModal() {
  // close buttons
  document.querySelectorAll('[data-modal="video-modal"]').forEach(b =>
    b.addEventListener('click', () => closeModal('video-modal'))
  );

  // upload
  document.getElementById('vm-upload-btn').addEventListener('click', () =>
    document.getElementById('vm-upload-input').click()
  );
  document.getElementById('vm-upload-input').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    try {
      for (const file of e.target.files) {
        const name = await uploadToLibrary(file);
        if (!state.vmSelectedImages.includes(name)) {
          state.vmSelectedImages.push(name);
        }
      }
      renderLibraryGrid('vm-library', state.vmSelectedImages, vmSelect);
      const n = state.vmSelectedImages.length;
      document.getElementById('vm-selected-name').textContent =
        n ? `${n} image${n === 1 ? '' : 's'} selected` : 'None';
      document.getElementById('vm-submit').disabled = n === 0;
    } catch (err) { showToast(err.message, 'error'); }
    e.target.value = '';
  });

  // submit
  document.getElementById('vm-submit').addEventListener('click', submitVideoJob);

  // close on overlay click
  document.getElementById('video-modal').addEventListener('click', e => {
    if (e.target.id === 'video-modal') closeModal('video-modal');
  });
}

function vmSelect(name) {
  if (state.vmSelectedImages.includes(name)) {
    state.vmSelectedImages = state.vmSelectedImages.filter(n => n !== name);
  } else {
    state.vmSelectedImages.push(name);
  }
  renderLibraryGrid('vm-library', state.vmSelectedImages, vmSelect);
  const n = state.vmSelectedImages.length;
  document.getElementById('vm-selected-name').textContent =
    n ? `${n} image${n === 1 ? '' : 's'} selected` : 'None';
  document.getElementById('vm-submit').disabled = n === 0;
}

async function openVideoModal(idx) {
  const item = state.channelItems[idx];
  state.vmItem = item;
  state.vmChannel = state.selectedChannel;
  state.vmSelectedImages = [];

  document.getElementById('vm-title').textContent   = item.title;
  document.getElementById('vm-channel').textContent  = state.selectedChannel;
  document.getElementById('vm-selected-name').textContent = 'None';
  document.getElementById('vm-submit').disabled = true;

  // Open gallery in multi-select mode, then come back
  openGallery('multi', state.selectedChannel, [], (selectedNames) => {
    state.vmSelectedImages = selectedNames;
    const n = selectedNames.length;
    document.getElementById('vm-selected-name').textContent =
      n ? `${n} image${n === 1 ? '' : 's'} selected` : 'None';
    document.getElementById('vm-submit').disabled = n === 0;

    // Also refresh the inline library grid for visual confirmation
    loadLibrary().then(() => renderLibraryGrid('vm-library', state.vmSelectedImages, vmSelect));

    // Show the video modal now
    document.getElementById('video-modal').classList.remove('hidden');
  });
}

async function submitVideoJob() {
  if (!state.vmItem || !state.vmSelectedImages.length) return;

  setLoading('vm-submit', true);

  try {
    const fd = new FormData();
    fd.append('channel_name',  state.vmChannel);
    fd.append('audio_file',    state.vmItem.files.audio);
    fd.append('library_images', JSON.stringify(state.vmSelectedImages));

    const res  = await fetch('/api/queue/video', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast(`Queued video: ${state.vmItem.title}`, 'success');
      closeModal('video-modal');
      refreshQueue();
    } else {
      showToast(data.detail || data.message || 'Queue failed', 'error');
    }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }

  setLoading('vm-submit', false);
}


// ═══════════════════════════════════════════════════════════
//  MODAL — EDIT METADATA
// ═══════════════════════════════════════════════════════════

function setupMetadataModal() {
  // close metadata modal
  document.querySelectorAll('[data-modal="metadata-modal"]').forEach(b =>
    b.addEventListener('click', () => closeModal('metadata-modal'))
  );

  document.getElementById('mdm-title-copy-btn').addEventListener('click', async () => {
    const val = document.getElementById('mdm-title-input').value;
    if (!val) {
      showToast('No title to copy', 'error');
      return;
    }
    try {
      await navigator.clipboard.writeText(val);
      showToast('Title copied to clipboard', 'success');
    } catch (e) {
      showToast('Failed to copy: ' + e.message, 'error');
    }
  });

  document.getElementById('mdm-desc-copy-btn').addEventListener('click', async () => {
    const val = document.getElementById('mdm-desc-input').value;
    if (!val) {
      showToast('No description to copy', 'error');
      return;
    }
    try {
      await navigator.clipboard.writeText(val);
      showToast('Description copied to clipboard', 'success');
    } catch (e) {
      showToast('Failed to copy: ' + e.message, 'error');
    }
  });

  document.getElementById('mdm-title-rewrite-btn').addEventListener('click', async () => {
    const inputEl = document.getElementById('mdm-title-input');
    const val = inputEl.value.trim();
    if (!val) {
      showToast('Please enter a title to rewrite', 'error');
      return;
    }
    
    setLoading('mdm-title-rewrite-btn', true);
    try {
      const fd = new FormData();
      fd.append('text', val);
      fd.append('field_type', 'title');
      
      const res = await fetch('/api/item/rewrite-metadata', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.status === 'ok') {
        inputEl.value = data.rewritten;
        showToast('Title rewritten successfully', 'success');
      } else {
        showToast(data.message || data.detail || 'Rewrite failed', 'error');
      }
    } catch (e) {
      showToast('Error: ' + e.message, 'error');
    } finally {
      setLoading('mdm-title-rewrite-btn', false);
    }
  });

  document.getElementById('mdm-desc-rewrite-btn').addEventListener('click', async () => {
    const inputEl = document.getElementById('mdm-desc-input');
    const val = inputEl.value.trim();
    if (!val) {
      showToast('Please enter a description to rewrite', 'error');
      return;
    }
    
    setLoading('mdm-desc-rewrite-btn', true);
    try {
      const fd = new FormData();
      fd.append('text', val);
      fd.append('field_type', 'description');
      
      const res = await fetch('/api/item/rewrite-metadata', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.status === 'ok') {
        inputEl.value = data.rewritten;
        showToast('Description rewritten successfully', 'success');
      } else {
        showToast(data.message || data.detail || 'Rewrite failed', 'error');
      }
    } catch (e) {
      showToast('Error: ' + e.message, 'error');
    } finally {
      setLoading('mdm-desc-rewrite-btn', false);
    }
  });

  document.getElementById('mdm-submit').addEventListener('click', async () => {
    if (!state.mdmItem) return;
    
    const title = document.getElementById('mdm-title-input').value.trim();
    const desc = document.getElementById('mdm-desc-input').value.trim();
    if (!title) {
      showToast('Title cannot be empty', 'error');
      return;
    }
    
    setLoading('mdm-submit', true);
    try {
      const fd = new FormData();
      fd.append('channel_name', state.mdmChannel);
      fd.append('item_key', state.mdmItem.key || state.mdmItem.title);
      fd.append('title', title);
      fd.append('description', desc);
      
      const res = await fetch('/api/item/save-metadata', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast('Metadata saved successfully', 'success');
        closeModal('metadata-modal');
        
        state.mdmItem.title = title;
        state.mdmItem.description = desc;
        if (state.selectedChannel) {
          loadChannelItems();
        }
      } else {
        showToast(data.message || data.detail || 'Failed to save metadata', 'error');
      }
    } catch (e) {
      showToast('Error: ' + e.message, 'error');
    } finally {
      setLoading('mdm-submit', false);
    }
  });

  document.getElementById('metadata-modal').addEventListener('click', e => {
    if (e.target.id === 'metadata-modal') closeModal('metadata-modal');
  });
}

async function openMetadataModal(idx) {
  const item = state.channelItems[idx];
  if (!item) return;

  state.mdmItem = item;
  state.mdmChannel = state.selectedChannel;

  document.getElementById('mdm-title-display').textContent = item.title;
  document.getElementById('mdm-channel-display').textContent = state.selectedChannel;

  document.getElementById('mdm-title-input').value = item.title;
  document.getElementById('mdm-desc-input').value = item.description || '';

  document.getElementById('metadata-modal').classList.remove('hidden');
}


// ═══════════════════════════════════════════════════════════
//  MODAL — THUMBNAIL CREATION
// ═══════════════════════════════════════════════════════════

function setupThumbnailModal() {
  // close
  document.querySelectorAll('[data-modal="thumbnail-modal"]').forEach(b =>
    b.addEventListener('click', () => closeModal('thumbnail-modal'))
  );

  // upload
  document.getElementById('tm-upload-btn').addEventListener('click', () =>
    document.getElementById('tm-upload-input').click()
  );
  document.getElementById('tm-upload-input').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    try {
      const name = await uploadToLibrary(e.target.files[0]);
      tmSelectBg(name);
    } catch (err) { showToast(err.message, 'error'); }
    e.target.value = '';
  });

  // text → chips + preview
  let debounce;
  document.getElementById('tm-text').addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { updateTmChips(); updateTmLineControls(); renderTmPreview(); }, 200);
  });

  // font picker
  document.getElementById('tm-font-name').addEventListener('change', e => {
    state.tmFontName = e.target.value;
    renderTmPreview();
  });

  // sliders
  bindSlider('tm-font-size',   'tm-font-size-val',   v => { state.tmFontSize   = v; renderTmPreview(); });
  bindSlider('tm-pos-x',       'tm-pos-x-val',       v => { state.tmPosX       = v; renderTmPreview(); });
  bindSlider('tm-pos-y',       'tm-pos-y-val',       v => { state.tmPosY       = v; renderTmPreview(); });
  bindSlider('tm-box-opacity', 'tm-box-opacity-val', v => { state.tmBoxOpacity  = v / 100; renderTmPreview(); });
  bindSlider('tm-box-width',   'tm-box-width-val',   v => { state.tmBoxWidth    = v; renderTmPreview(); });

  document.getElementById('tm-line-controls').addEventListener('input', e => {
    const slider = e.target.closest('[data-line-scale]');
    if (!slider) return;
    const idx = Number(slider.dataset.lineScale);
    const value = Number(slider.value);
    state.tmLineScales[idx] = value / 100;
    const row = slider.closest('.line-size-row');
    const label = row?.querySelector('.line-size-value');
    if (label) label.textContent = `${value}%`;
    syncSliderVal(slider);
    renderTmPreview();
  });

  // colors
  document.getElementById('tm-text-color').addEventListener('input', e => {
    state.tmTextColor = e.target.value;
    renderTmPreview();
  });
  document.getElementById('tm-highlight-color').addEventListener('input', e => {
    state.tmHighlightColor = e.target.value;
    document.querySelectorAll('#tm-word-chips .chip.highlighted').forEach(chip => {
      chip.style.setProperty('--chip-highlight', e.target.value);
    });
    renderTmPreview();
  });
  document.getElementById('tm-box-color').addEventListener('input', e => {
    state.tmBoxColor = e.target.value;
    renderTmPreview();
  });

  document.getElementById('tm-fit-toggle').addEventListener('click', e => {
    const btn = e.target.closest('[data-fit]');
    if (!btn) return;
    document.querySelectorAll('#tm-fit-toggle .toggle').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.tmFitBox = btn.dataset.fit === 'on';
    renderTmPreview();
  });

  // text alignment toggle
  document.getElementById('tm-align-toggle').addEventListener('click', e => {
    const btn = e.target.closest('[data-align]');
    if (!btn) return;
    document.querySelectorAll('#tm-align-toggle .toggle').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.tmTextAlign = btn.dataset.align;
    renderTmPreview();
  });

  // shadow/stroke mode toggle
  document.getElementById('tm-shadow-toggle').addEventListener('click', e => {
    const btn = e.target.closest('[data-shadow]');
    if (!btn) return;
    document.querySelectorAll('#tm-shadow-toggle .toggle').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.tmShadowMode = btn.dataset.shadow;
    renderTmPreview();
  });

  setupThumbnailDrag();

  // submit
  document.getElementById('tm-submit').addEventListener('click', submitThumbnail);

  // overlay close
  document.getElementById('thumbnail-modal').addEventListener('click', e => {
    if (e.target.id === 'thumbnail-modal') closeModal('thumbnail-modal');
  });
}

function syncSliderVal(slider) {
  const min = parseFloat(slider.min) || 0;
  const max = parseFloat(slider.max) || 100;
  const val = parseFloat(slider.value) || 0;
  const pct = ((val - min) / (max - min)) * 100;
  slider.style.setProperty('--val', pct);
}

function bindSlider(sliderId, labelId, onChange) {
  const s = document.getElementById(sliderId);
  const l = document.getElementById(labelId);
  syncSliderVal(s);
  s.addEventListener('input', () => { l.textContent = s.value; syncSliderVal(s); onChange(+s.value); });
}

function syncThumbPositionControls() {
  const x = Math.round(state.tmPosX);
  const y = Math.round(state.tmPosY);
  const sX = document.getElementById('tm-pos-x');
  const sY = document.getElementById('tm-pos-y');
  sX.value = x;
  document.getElementById('tm-pos-x-val').textContent = String(x);
  sY.value = y;
  document.getElementById('tm-pos-y-val').textContent = String(y);
  syncSliderVal(sX);
  syncSliderVal(sY);
}

function getCanvasPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * canvas.width,
    y: ((event.clientY - rect.top) / rect.height) * canvas.height,
  };
}

function updateThumbPositionFromBlock(x, y) {
  const block = state.tmPreviewBlock;
  if (!block) return;
  const maxX = Math.max(1, block.canvasW - block.w);
  const maxY = Math.max(1, block.canvasH - block.h);
  const clampedX = Math.max(0, Math.min(maxX, x));
  const clampedY = Math.max(0, Math.min(maxY, y));
  state.tmPosX = Math.max(0, Math.min(100, (clampedX / maxX) * 100));
  state.tmPosY = Math.max(0, Math.min(100, (clampedY / maxY) * 100));
  syncThumbPositionControls();
  renderTmPreview();
}

function setupThumbnailDrag() {
  const canvas = document.getElementById('tm-preview');
  if (!canvas) return;

  canvas.addEventListener('pointerdown', event => {
    const block = state.tmPreviewBlock;
    if (!block) return;
    const p = getCanvasPoint(canvas, event);
    const inside = p.x >= block.x && p.x <= block.x + block.w &&
                   p.y >= block.y && p.y <= block.y + block.h;
    if (!inside) return;

    state.tmDragging = true;
    state.tmDragOffsetX = p.x - block.x;
    state.tmDragOffsetY = p.y - block.y;
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add('dragging');
    event.preventDefault();
  });

  canvas.addEventListener('pointermove', event => {
    if (!state.tmDragging) return;
    const p = getCanvasPoint(canvas, event);
    updateThumbPositionFromBlock(p.x - state.tmDragOffsetX, p.y - state.tmDragOffsetY);
    event.preventDefault();
  });

  const endDrag = event => {
    if (!state.tmDragging) return;
    state.tmDragging = false;
    canvas.classList.remove('dragging');
    try { canvas.releasePointerCapture(event.pointerId); } catch {}
  };

  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', event => {
    if (state.tmDragging) endDrag(event);
  });
}

function tmSelectBg(name) {
  state.tmSelectedBg = name;
  renderLibraryGrid('tm-library', name, tmSelectBg);

  const isAbs = name.includes('/') || name.includes('\\');
  const imgUrl = isAbs 
    ? `/api/gallery/serve-file?path=${encodeURIComponent(name)}`
    : state.libraryImages.find(i => i.name === name)?.url;

  if (imgUrl) {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => { state.tmBgImg = img; renderTmPreview(); };
    img.src = imgUrl;
  } else {
    state.tmBgImg = null;
    renderTmPreview();
  }
}

function openTmGallery() {
  // Open gallery in single-select for thumbnail background, then apply selection
  const channelHint = state.tmChannel || state.selectedChannel || '';
  const pre = state.tmSelectedBg ? [state.tmSelectedBg] : [];
  openGallery('single', channelHint, pre, (selectedNames) => {
    if (selectedNames.length) {
      // Need to reload library so tmSelectBg can find the image URL
      loadLibrary().then(() => tmSelectBg(selectedNames[0]));
    }
  });
}

function configureThumbnailModal(mode, itemTitle, channelLabel, submitLabel) {
  state.tmMode = mode;
  const modalTitle = document.querySelector('#thumbnail-modal .modal-header h3');
  if (modalTitle) {
    modalTitle.textContent = mode === 'prepare' ? 'Prepare Thumbnail' : 'Create Thumbnail';
  }
  document.getElementById('tm-title').textContent = itemTitle;
  document.getElementById('tm-channel').textContent = channelLabel;
  const submitLabelEl = document.querySelector('#tm-submit .btn-text');
  if (submitLabelEl) submitLabelEl.textContent = submitLabel;
}

function resetThumbnailEditor() {
  state.tmSelectedBg = '';
  state.tmBgImg = null;
  state.tmHighlightedWords.clear();
  state.tmFontSize = 72;
  state.tmPosX = 50;
  state.tmPosY = 72;
  state.tmFontName = 'Impact';
  state.tmTextColor = '#ffffff';
  state.tmHighlightColor = '#ffd636';
  state.tmBoxColor = '#000000';
  state.tmBoxOpacity = 0.70;
  state.tmBoxWidth = 52;
  state.tmFitBox = true;
  state.tmLineScales = [];
  state.tmShadowMode = 'both';
  state.tmTextAlign = 'center';

  document.getElementById('tm-text').value = '';
  const sFontSize  = document.getElementById('tm-font-size');
  const sPosX      = document.getElementById('tm-pos-x');
  const sPosY      = document.getElementById('tm-pos-y');
  const sOpacity   = document.getElementById('tm-box-opacity');
  const sBoxWidth  = document.getElementById('tm-box-width');
  sFontSize.value = 72;  document.getElementById('tm-font-size-val').textContent = '72';  syncSliderVal(sFontSize);
  sPosX.value     = 50;  document.getElementById('tm-pos-x-val').textContent    = '50';  syncSliderVal(sPosX);
  sPosY.value     = 72;  document.getElementById('tm-pos-y-val').textContent    = '72';  syncSliderVal(sPosY);
  document.getElementById('tm-text-color').value = '#ffffff';
  document.getElementById('tm-highlight-color').value = '#ffd636';
  document.getElementById('tm-box-color').value = '#000000';
  sOpacity.value  = 70;  document.getElementById('tm-box-opacity-val').textContent = '70'; syncSliderVal(sOpacity);
  sBoxWidth.value = 52;  document.getElementById('tm-box-width-val').textContent = '52'; syncSliderVal(sBoxWidth);
  document.getElementById('tm-font-name').value = 'Impact';

  // reset alignment toggle
  document.querySelectorAll('#tm-align-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.align === 'center')
  );
  // reset shadow toggle
  document.querySelectorAll('#tm-shadow-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.shadow === 'both')
  );
  document.querySelectorAll('#tm-fit-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.fit === 'on')
  );

  document.getElementById('tm-word-chips').innerHTML =
    '<span class="text-muted text-sm">Type text above</span>';
  updateTmLineControls();
  state.tmPreviewBlock = null;
}

async function openThumbnailModal(idx) {
  const item = state.channelItems[idx];
  state.tmItem    = item;
  state.tmChannel = state.selectedChannel;
  state.tmPrepVideo = null;
  resetThumbnailEditor();
  configureThumbnailModal('studio', item.title, state.selectedChannel, 'Generate Thumbnail');

  await loadLibrary();
  renderLibraryGrid('tm-library', '', tmSelectBg);
  renderTmPreview();

  document.getElementById('thumbnail-modal').classList.remove('hidden');

  // Also add a "Pick from Gallery" button click handler on the library section header
  // The user can still use the inline grid, but also click the upload button area
}

async function openPrepareThumbnailModal(event, idx) {
  event.stopPropagation();
  const video = state.videos[idx] ? { ...state.videos[idx] } : null;
  if (!video) return;

  state.tmItem = null;
  state.tmPrepVideo = video;
  state.tmChannel = state.channelName;
  resetThumbnailEditor();

  const existing = getPreparation(video);
  configureThumbnailModal('prepare', video.title, `${state.channelName} - after download`, 'Save Thumbnail Setup');

  document.getElementById('tm-text').value = existing?.thumbText || video.title;
  state.tmFontSize = existing?.thumbFontSize || 72;
  state.tmPosX = existing?.thumbPosX || 50;
  state.tmPosY = existing?.thumbPosY || 72;
  state.tmHighlightedWords = new Set(existing?.highlightedWords || []);
  state.tmFontName = existing?.thumbFontName || 'Impact';
  state.tmTextColor = existing?.thumbTextColor || '#ffffff';
  state.tmHighlightColor = existing?.thumbHighlightColor || '#ffd636';
  state.tmBoxColor = existing?.thumbBoxColor || '#000000';
  state.tmBoxOpacity = existing?.thumbBoxOpacity !== undefined ? existing.thumbBoxOpacity : 0.70;
  state.tmBoxWidth = existing?.thumbBoxWidth || 52;
  state.tmFitBox = existing?.thumbFitBox !== undefined ? existing.thumbFitBox : true;
  state.tmLineScales = Array.isArray(existing?.thumbLineScales) ? [...existing.thumbLineScales] : [];
  state.tmShadowMode = existing?.thumbShadowMode || 'both';
  state.tmTextAlign = existing?.thumbTextAlign || 'center';

  document.getElementById('tm-font-size').value = state.tmFontSize;
  document.getElementById('tm-font-size-val').textContent = String(state.tmFontSize);
  document.getElementById('tm-pos-x').value = state.tmPosX;
  document.getElementById('tm-pos-x-val').textContent = String(state.tmPosX);
  document.getElementById('tm-pos-y').value = state.tmPosY;
  document.getElementById('tm-pos-y-val').textContent = String(state.tmPosY);
  document.getElementById('tm-font-name').value = state.tmFontName;
  document.getElementById('tm-text-color').value = state.tmTextColor;
  document.getElementById('tm-highlight-color').value = state.tmHighlightColor;
  document.getElementById('tm-box-color').value = state.tmBoxColor;
  
  const sOpacity = document.getElementById('tm-box-opacity');
  sOpacity.value = Math.round(state.tmBoxOpacity * 100);
  document.getElementById('tm-box-opacity-val').textContent = String(Math.round(state.tmBoxOpacity * 100));
  syncSliderVal(sOpacity);
  const sBoxWidth = document.getElementById('tm-box-width');
  sBoxWidth.value = state.tmBoxWidth;
  document.getElementById('tm-box-width-val').textContent = String(state.tmBoxWidth);
  syncSliderVal(sBoxWidth);

  // Set alignment toggle
  document.querySelectorAll('#tm-align-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.align === state.tmTextAlign)
  );
  // Set shadow toggle
  document.querySelectorAll('#tm-shadow-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.shadow === state.tmShadowMode)
  );
  document.querySelectorAll('#tm-fit-toggle .toggle').forEach(b =>
    b.classList.toggle('active', b.dataset.fit === (state.tmFitBox ? 'on' : 'off'))
  );

  await loadLibrary();
  renderLibraryGrid('tm-library', '', tmSelectBg);
  const defaultThumbBg = existing?.thumbBg || existing?.renderImages?.[0] || '';
  if (defaultThumbBg) tmSelectBg(defaultThumbBg);
  updateTmChips();
  updateTmLineControls();
  renderTmPreview();
  document.getElementById('thumbnail-modal').classList.remove('hidden');
}


// ─── Thumbnail word-chip highlighting ───────────────────────

function getTmTextRows() {
  const text = document.getElementById('tm-text')?.value || '';
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
}

function updateTmLineControls() {
  const box = document.getElementById('tm-line-controls');
  if (!box) return;
  const rows = getTmTextRows().slice(0, 8);
  if (!rows.length) {
    box.innerHTML = '<span class="text-muted text-sm">Add text lines to tune each row</span>';
    return;
  }

  while (state.tmLineScales.length < rows.length) state.tmLineScales.push(1);
  box.innerHTML = '';
  rows.forEach((line, idx) => {
    const scale = Math.round((state.tmLineScales[idx] || 1) * 100);
    const row = document.createElement('div');
    row.className = 'line-size-row';
    row.innerHTML = `
      <span class="line-size-name" title="${esc(line)}">Line ${idx + 1}</span>
      <input type="range" class="slider" min="50" max="180" value="${scale}" data-line-scale="${idx}">
      <span class="line-size-value">${scale}%</span>
    `;
    box.appendChild(row);
    syncSliderVal(row.querySelector('input'));
  });
}

function updateTmChips() {
  const text = document.getElementById('tm-text').value;
  const box  = document.getElementById('tm-word-chips');
  const raw  = text.split(/\s+/).filter(Boolean);
  const unique = [...new Set(raw)];

  if (!unique.length) {
    box.innerHTML = '<span class="text-muted text-sm">Type text above</span>';
    return;
  }

  box.innerHTML = '';
  unique.forEach(word => {
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.textContent = word;
    chip.style.setProperty('--chip-highlight', state.tmHighlightColor || '#ffd636');
    const clean = word.toLowerCase().replace(/[^\w]/g, '');
    if (state.tmHighlightedWords.has(clean)) chip.classList.add('highlighted');

    chip.addEventListener('click', () => {
      if (state.tmHighlightedWords.has(clean)) {
        state.tmHighlightedWords.delete(clean);
        chip.classList.remove('highlighted');
      } else {
        state.tmHighlightedWords.add(clean);
        chip.classList.add('highlighted');
      }
      renderTmPreview();
    });

    box.appendChild(chip);
  });
}


// ─── Canvas live preview ────────────────────────────────────

async function renderTmPreview() {
  const canvas = document.getElementById('tm-preview');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = 1280, H = 720;
  ctx.clearRect(0, 0, W, H);

  // background
  if (state.tmBgImg) {
    const img = state.tmBgImg;
    const ir = img.width / img.height, cr = W / H;
    let sx, sy, sw, sh;
    if (ir > cr) { sh = img.height; sw = sh * cr; sx = (img.width - sw) / 2; sy = 0; }
    else         { sw = img.width;  sh = sw / cr; sx = 0; sy = (img.height - sh) / 2; }
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);
  } else {
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, '#1a1a2e');
    g.addColorStop(1, '#16213e');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }

  const text = (document.getElementById('tm-text')?.value || '').trim();
  if (!text) {
    state.tmPreviewBlock = null;
    return;
  }

  // Ensure the font file is loaded into the browser before drawing
  const resolvedFont = await loadFontForCanvas(state.tmFontName);
  const fontStack = resolvedFont
    ? `${resolvedFont}, Impact, "Arial Black", sans-serif`
    : 'Impact, "Arial Black", sans-serif';

  ctx.textBaseline = 'top';
  ctx.lineJoin = 'round';

  const blockPadX = 34;
  const blockPadY = 24;
  const bW = Math.round(W * Math.max(25, Math.min(90, state.tmBoxWidth || 52)) / 100);
  const innerW = Math.max(80, bW - blockPadX * 2);
  const rows = getTmTextRows();
  const lineLayouts = rows.map((line, idx) => {
    const scale = state.tmLineScales[idx] || 1;
    let fs = Math.max(26, Math.round((state.tmFontSize || 72) * scale));
    let words = line.split(/\s+/).filter(Boolean);
    if (!words.length) words = [''];

    const measure = () => {
      ctx.font = `${fs}px ${fontStack}`;
      const wordWidths = words.map(word => ctx.measureText(word).width);
      const spaceW = ctx.measureText(' ').width;
      const textW = wordWidths.reduce((sum, width) => sum + width, 0) + Math.max(0, words.length - 1) * spaceW;
      return { wordWidths, spaceW, textW };
    };

    let metrics = measure();
    if (state.tmFitBox) {
      while (metrics.textW > innerW && fs > 22) {
        fs -= 2;
        metrics = measure();
      }
    }

    const strokeW = Math.max(2, Math.round(fs * 0.055));
    const lineH = Math.ceil(fs * 0.98 + Math.max(6, fs * 0.08));
    return { words, fs, strokeW, lineH, ...metrics };
  });

  const bH = lineLayouts.reduce((sum, line) => sum + line.lineH, blockPadY * 2);
  const bX = Math.max(0, Math.min(W - bW, (state.tmPosX / 100) * (W - bW)));
  const bY = Math.max(0, Math.min(H - bH, (state.tmPosY / 100) * (H - bH)));
  state.tmPreviewBlock = { x: bX, y: bY, w: bW, h: bH, canvasW: W, canvasH: H };

  // box overlay
  const boxRgb = hexToRgbPreview(state.tmBoxColor);
  const boxAlpha = Math.max(0, Math.min(1, state.tmBoxOpacity));
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.45)';
  ctx.shadowBlur = 18;
  ctx.shadowOffsetY = 8;
  ctx.fillStyle = `rgba(${boxRgb.r},${boxRgb.g},${boxRgb.b},${boxAlpha})`;
  roundRect(ctx, bX, bY, bW, bH, 18);
  ctx.fill();
  ctx.restore();

  // draw text lines
  let dy = bY + blockPadY;
  for (const line of lineLayouts) {
    const ln = line.words;
    const lineW = line.textW;
    ctx.font = `${line.fs}px ${fontStack}`;

    // alignment
    let dx;
    if (state.tmTextAlign === 'left')       dx = bX + blockPadX;
    else if (state.tmTextAlign === 'right') dx = bX + bW - lineW - blockPadX;
    else                                     dx = bX + (bW - lineW) / 2; // center

    for (const word of ln) {
      const clean = word.toLowerCase().replace(/[^\w]/g, '');
      const ww    = ctx.measureText(word).width;

      ctx.save();
      // shadow
      if (state.tmShadowMode === 'shadow' || state.tmShadowMode === 'both') {
        ctx.shadowColor   = 'rgba(0,0,0,0.75)';
        ctx.shadowBlur    = 6;
        ctx.shadowOffsetX = 4;
        ctx.shadowOffsetY = 5;
      }
      ctx.strokeStyle = '#080a0e';
      ctx.lineWidth   = (state.tmShadowMode === 'stroke' || state.tmShadowMode === 'both') ? line.strokeW : 0;
      ctx.fillStyle   = state.tmHighlightedWords.has(clean)
        ? (state.tmHighlightColor || '#ffd636')
        : (state.tmTextColor || '#ffffff');

      if (state.tmShadowMode === 'stroke' || state.tmShadowMode === 'both') {
        ctx.strokeText(word, dx, dy);
      }
      ctx.fillText(word, dx, dy);
      ctx.restore();
      dx += ww + line.spaceW;
    }
    dy += line.lineH;
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function hexToRgbPreview(hex) {
  const h = (hex || '#000000').replace('#', '');
  return {
    r: parseInt(h.substring(0,2), 16) || 0,
    g: parseInt(h.substring(2,4), 16) || 0,
    b: parseInt(h.substring(4,6), 16) || 0,
  };
}

async function loadAvailableFonts() {
  try {
    const res  = await fetch('/api/fonts');
    const data = await res.json();
    if (data.status === 'ok' && data.fonts?.length) {
      const sel = document.getElementById('tm-font-name');
      sel.innerHTML = '';
      data.fonts.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name === 'Impact' ? 'Impact (default)' : name;
        sel.appendChild(opt);
      });
      sel.value = 'Impact';
    }
  } catch (e) { console.warn('Could not load fonts:', e); }
}


// ─── Submit thumbnail (synchronous — fast Pillow op) ────────

async function submitThumbnail() {
  if (!state.tmSelectedBg) { showToast('Select a background image', 'error'); return; }
  const text = document.getElementById('tm-text').value.trim();
  if (!text) { showToast('Enter some text', 'error'); return; }

  setLoading('tm-submit', true);

  try {
    if (state.tmMode === 'prepare') {
      if (!state.tmPrepVideo) throw new Error('Missing video for thumbnail preparation');
      savePreparation(state.tmPrepVideo, {
        thumbBg: state.tmSelectedBg,
        thumbText: text,
        highlightedWords: [...state.tmHighlightedWords],
        thumbFontSize: state.tmFontSize,
        thumbPosX: state.tmPosX,
        thumbPosY: state.tmPosY,
        thumbFontName: state.tmFontName,
        thumbTextColor: state.tmTextColor,
        thumbHighlightColor: state.tmHighlightColor,
        thumbBoxColor: state.tmBoxColor,
        thumbBoxOpacity: state.tmBoxOpacity,
        thumbBoxWidth: state.tmBoxWidth,
        thumbFitBox: state.tmFitBox,
        thumbLineScales: [...state.tmLineScales],
        thumbShadowMode: state.tmShadowMode,
        thumbTextAlign: state.tmTextAlign,
      });
      closeModal('thumbnail-modal');
      showToast('Thumbnail setup saved. It will run after download finishes.', 'success');
      await runCompletedPreparationIfReady(state.tmPrepVideo);
      setLoading('tm-submit', false);
      return;
    }

    if (!state.tmItem) throw new Error('Thumbnail item missing');

    const fd = new FormData();
    fd.append('channel_name',      state.tmChannel);
    fd.append('audio_file',        state.tmItem.files.audio);
    fd.append('library_image',     state.tmSelectedBg);
    fd.append('text',              text);
    fd.append('highlighted_words', [...state.tmHighlightedWords].join(','));
    fd.append('font_size',         state.tmFontSize);
    fd.append('pos_x',             state.tmPosX);
    fd.append('pos_y',             state.tmPosY);
    fd.append('font_name',         state.tmFontName);
    fd.append('text_color',        state.tmTextColor);
    fd.append('highlight_color',   state.tmHighlightColor);
    fd.append('box_color',         state.tmBoxColor);
    fd.append('box_opacity',       state.tmBoxOpacity);
    fd.append('box_width',         state.tmBoxWidth);
    fd.append('fit_box',           String(state.tmFitBox));
    fd.append('line_scales',       JSON.stringify(state.tmLineScales));
    fd.append('shadow_mode',       state.tmShadowMode);
    fd.append('text_align',        state.tmTextAlign);

    const res  = await fetch('/api/create-thumbnail', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.status === 'ok') {
      showToast('Thumbnail generated!', 'success');
      closeModal('thumbnail-modal');
      // refresh studio to show the new badge
      if (state.selectedChannel) loadChannelItems();
    } else {
      showToast(data.detail || data.message || 'Failed', 'error');
    }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }

  setLoading('tm-submit', false);
}


// ═══════════════════════════════════════════════════════════
//  MODAL HELPERS
// ═══════════════════════════════════════════════════════════

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}


// ═══════════════════════════════════════════════════════════
//  IMAGE GALLERY
// ═══════════════════════════════════════════════════════════

const gallery = {
  // Config set per open
  mode: 'multi',           // 'single' | 'multi'
  onConfirm: null,         // callback(selectedNames[])
  channelHint: '',         // active channel for "used by" indicators

  // Current view
  view: 'all',             // 'all' | 'render' | 'thumbnail' | 'uncategorized' | 'browse' | 'channel'
  browseLabel: '',

  // Data
  grouped: null,           // response from /api/gallery/grouped
  browseResult: null,      // response from /api/gallery/browse
  channelImages: [],       // images from a channel folder
  roots: [],               // browse roots

  // Selection
  selected: new Map(),     // name → {name, url, path?}  (order-preserving)
};


// ─── Setup ──────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  setupGallery();
});

function setupGallery() {
  // Close buttons
  document.querySelectorAll('[data-modal="gallery-modal"]').forEach(b =>
    b.addEventListener('click', () => closeModal('gallery-modal'))
  );
  document.getElementById('gallery-modal').addEventListener('click', e => {
    if (e.target.id === 'gallery-modal') closeModal('gallery-modal');
  });

  // Sidebar nav
  document.querySelectorAll('.gallery-nav-btn[data-gallery-view]').forEach(btn => {
    btn.addEventListener('click', () => {
      gallery.view = btn.dataset.galleryView;
      syncGalleryNav();
      renderGalleryGrid();
    });
  });

  // Browse
  document.getElementById('gallery-browse-go').addEventListener('click', galleryBrowse);
  document.getElementById('gallery-browse-path').addEventListener('keydown', e => {
    if (e.key === 'Enter') galleryBrowse();
  });

  // Upload
  document.getElementById('gallery-upload-btn').addEventListener('click', () =>
    document.getElementById('gallery-upload-input').click()
  );
  document.getElementById('gallery-upload-input').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    try {
      for (const file of e.target.files) {
        const result = await uploadToLibrary(file);
        gallery.selected.set(result, {
          name: result,
          url: `/output/library/${encodeURIComponent(result)}`,
        });
      }
      await galleryLoadGrouped();
      renderGalleryGrid();
      syncGalleryConfirm();
    } catch (err) { showToast(err.message, 'error'); }
    e.target.value = '';
  });

  // Confirm
  document.getElementById('gallery-confirm-btn').addEventListener('click', () => {
    if (gallery.onConfirm) {
      const selections = [...gallery.selected.values()].map(item => item.path || item.name);
      gallery.onConfirm(selections);
    }
    closeModal('gallery-modal');
  });
}


// ─── Public API ─────────────────────────────────────────────

/**
 * Open the gallery modal.
 * @param {'single'|'multi'} mode
 * @param {string} channelHint - active channel name (for "used by" indicators)
 * @param {string[]} preselected - already-selected image names
 * @param {function(string[])} onConfirm - callback with chosen image names
 */
async function openGallery(mode, channelHint, preselected, onConfirm) {
  gallery.mode = mode;
  gallery.channelHint = channelHint || '';
  gallery.onConfirm = onConfirm;
  gallery.view = 'all';
  gallery.browseResult = null;
  gallery.channelImages = [];

  // Restore preselection
  gallery.selected.clear();
  if (Array.isArray(preselected)) {
    preselected.forEach(n => {
      if (!n) return;
      const isAbs = n.includes('/') || n.includes('\\');
      const name = isAbs ? n.substring(Math.max(n.lastIndexOf('/'), n.lastIndexOf('\\')) + 1) : n;
      gallery.selected.set(n, {
        name: name,
        url: isAbs 
          ? `/api/gallery/serve-file?path=${encodeURIComponent(n)}` 
          : `/output/library/${encodeURIComponent(n)}`,
        path: isAbs ? n : undefined
      });
    });
  }

  // Update confirm button label
  const confirmText = document.querySelector('#gallery-confirm-btn .btn-text');
  if (confirmText) confirmText.textContent = mode === 'single' ? 'Select Image' : 'Select Images';

  // Load data
  await Promise.all([galleryLoadGrouped(), galleryLoadRoots()]);

  syncGalleryNav();
  renderGalleryGrid();
  syncGalleryConfirm();
  renderGalleryRoots();
  renderGalleryChannelFolders();

  document.getElementById('gallery-modal').classList.remove('hidden');
}


// ─── Data Loading ───────────────────────────────────────────

async function galleryLoadGrouped() {
  try {
    const url = '/api/gallery/grouped' + (gallery.channelHint ? `?channel=${encodeURIComponent(gallery.channelHint)}` : '');
    const res = await fetch(url);
    const data = await res.json();
    if (data.status === 'ok') gallery.grouped = data;
  } catch (e) { console.error('Gallery grouped:', e); }
}

async function galleryLoadRoots() {
  try {
    const res = await fetch('/api/gallery/browse/roots');
    const data = await res.json();
    if (data.status === 'ok') gallery.roots = data.roots || [];
  } catch (e) { console.error('Gallery roots:', e); }
}

async function galleryBrowse() {
  const pathInput = document.getElementById('gallery-browse-path');
  const path = pathInput.value.trim();
  if (!path) return;

  try {
    const fd = new FormData();
    fd.append('path', path);
    const res = await fetch('/api/gallery/browse', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ok') {
      gallery.browseResult = data;
      gallery.view = 'browse';
      gallery.browseLabel = data.path || path;
      syncGalleryNav();
      renderGalleryGrid();
    } else {
      showToast(data.message || 'Cannot browse folder', 'error');
    }
  } catch (e) { showToast('Browse error: ' + e.message, 'error'); }
}

async function galleryBrowseTo(folderPath) {
  document.getElementById('gallery-browse-path').value = folderPath;
  await galleryBrowse();
}

async function galleryLoadChannelImages(channelName) {
  try {
    const res = await fetch(`/api/gallery/channel-images/${encodeURIComponent(channelName)}`);
    const data = await res.json();
    if (data.status === 'ok') gallery.channelImages = data.images || [];
  } catch (e) { console.error('Channel images:', e); }
}


// ─── Navigation ─────────────────────────────────────────────

function syncGalleryNav() {
  document.querySelectorAll('.gallery-nav-btn[data-gallery-view]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.galleryView === gallery.view);
  });

  // Update counts
  if (gallery.grouped) {
    document.getElementById('gnav-render-count').textContent = (gallery.grouped.render_images || []).length;
    document.getElementById('gnav-thumbnail-count').textContent = (gallery.grouped.thumbnail_images || []).length;
    document.getElementById('gnav-uncat-count').textContent = (gallery.grouped.uncategorized || []).length;
  }

  // Label
  const labels = {
    all: 'All Library',
    render: 'Video Render Images',
    thumbnail: 'Thumbnail Images',
    uncategorized: 'Uncategorized',
    browse: gallery.browseLabel || 'Browsing',
    channel: gallery.browseLabel || 'Channel Images',
  };
  document.getElementById('gallery-view-label').textContent = labels[gallery.view] || 'Gallery';
}


// ─── Grid Rendering ─────────────────────────────────────────

function renderGalleryGrid() {
  const grid = document.getElementById('gallery-grid');
  const empty = document.getElementById('gallery-empty');
  const breadcrumb = document.getElementById('gallery-breadcrumb');
  breadcrumb.classList.add('hidden');
  grid.innerHTML = '';

  if (gallery.view === 'browse') {
    renderGalleryBrowse(grid, empty, breadcrumb);
    return;
  }

  if (gallery.view === 'channel') {
    renderGalleryChannelView(grid, empty);
    return;
  }

  // Library views
  if (!gallery.grouped) {
    empty.classList.remove('hidden');
    return;
  }

  let images = [];
  if (gallery.view === 'all') {
    // Merge all categories, dedupe by name
    const seen = new Set();
    const allArrays = [
      gallery.grouped.render_images || [],
      gallery.grouped.thumbnail_images || [],
      gallery.grouped.uncategorized || [],
    ];
    for (const arr of allArrays) {
      for (const img of arr) {
        if (!seen.has(img.name)) { seen.add(img.name); images.push(img); }
      }
    }
  } else if (gallery.view === 'render') {
    images = gallery.grouped.render_images || [];
  } else if (gallery.view === 'thumbnail') {
    images = gallery.grouped.thumbnail_images || [];
  } else if (gallery.view === 'uncategorized') {
    images = gallery.grouped.uncategorized || [];
  }

  if (!images.length) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  for (const img of images) {
    grid.appendChild(createGalleryImageEl(img.name, img.url, img.used_in, img.used_by_channel, true));
  }

  syncGalleryConfirm();
}


function renderGalleryBrowse(grid, empty, breadcrumb) {
  const data = gallery.browseResult;
  if (!data) { empty.classList.remove('hidden'); return; }

  // Breadcrumb
  const parts = (data.path || '').split('/').filter(Boolean);
  if (parts.length) {
    breadcrumb.classList.remove('hidden');
    breadcrumb.innerHTML = '';
    let accumulated = '';
    for (let i = 0; i < parts.length; i++) {
      accumulated += parts[i] + '/';
      const path = accumulated;
      if (i > 0) {
        const sep = document.createElement('span');
        sep.className = 'gallery-breadcrumb-sep';
        sep.textContent = '›';
        breadcrumb.appendChild(sep);
      }
      const btn = document.createElement('button');
      btn.className = 'gallery-breadcrumb-part';
      btn.textContent = parts[i];
      btn.addEventListener('click', () => galleryBrowseTo(path));
      breadcrumb.appendChild(btn);
    }
  }

  const hasContent = (data.folders || []).length || (data.images || []).length;
  if (!hasContent) { empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');

  // Render parent folder
  if (data.parent) {
    const parentEl = document.createElement('div');
    parentEl.className = 'gallery-folder-item';
    parentEl.innerHTML = `<span class="gallery-folder-icon">⬆️</span><span class="gallery-folder-name">..</span>`;
    parentEl.addEventListener('click', () => galleryBrowseTo(data.parent));
    grid.appendChild(parentEl);
  }

  // Render subfolders
  for (const folder of (data.folders || [])) {
    const el = document.createElement('div');
    el.className = 'gallery-folder-item';
    el.innerHTML = `<span class="gallery-folder-icon">📁</span><span class="gallery-folder-name">${esc(folder.name)}</span>`;
    el.addEventListener('click', () => galleryBrowseTo(folder.path));
    grid.appendChild(el);
  }

  // Render images
  for (const img of (data.images || [])) {
    const url = `/api/gallery/serve-file?path=${encodeURIComponent(img.path)}`;
    grid.appendChild(createGalleryImageEl(img.name, url, [], false, false, img.path));
  }

  syncGalleryConfirm();
}


function renderGalleryChannelView(grid, empty) {
  if (!gallery.channelImages.length) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  for (const img of gallery.channelImages) {
    const url = img.url || `/api/gallery/serve-file?path=${encodeURIComponent(img.path)}`;
    grid.appendChild(createGalleryImageEl(img.name, url, [], false, false, img.path));
  }

  syncGalleryConfirm();
}


function createGalleryImageEl(name, url, usedIn, usedByChannel, isLibrary, filePath) {
  const key = filePath || name;
  const div = document.createElement('div');
  div.className = 'gallery-item';
  if (gallery.selected.has(key)) div.classList.add('selected');
  if (usedByChannel) div.classList.add('used-by-channel');

  let badgeText = '';
  if (usedIn && usedIn.length) {
    const channels = {};
    usedIn.forEach(u => {
      const ch = u.channel_name || 'Unknown';
      channels[ch] = (channels[ch] || 0) + 1;
    });
    badgeText = Object.entries(channels).map(([ch, n]) => `${ch} × ${n}`).join(', ');
  }

  div.innerHTML = `
    <img src="${url}" alt="${esc(name)}" loading="lazy">
    ${badgeText ? `<div class="gallery-item-badge">${esc(badgeText)}</div>` : ''}
    ${isLibrary ? `<button class="gallery-delete-btn" title="Remove from library">✕</button>` : ''}
  `;

  // Click to select
  div.addEventListener('click', e => {
    if (e.target.classList.contains('gallery-delete-btn')) return;

    if (gallery.mode === 'single') {
      // Single select: clear previous, set this one
      gallery.selected.clear();
      gallery.selected.set(key, { name, url, path: filePath });
      // Re-render to update visual state
      document.querySelectorAll('.gallery-item.selected').forEach(el => el.classList.remove('selected'));
      div.classList.add('selected');
    } else {
      // Multi select: toggle
      if (gallery.selected.has(key)) {
        gallery.selected.delete(key);
        div.classList.remove('selected');
      } else {
        gallery.selected.set(key, { name, url, path: filePath });
        div.classList.add('selected');
      }
    }
    syncGalleryConfirm();
  });

  // Delete (library images only)
  const deleteBtn = div.querySelector('.gallery-delete-btn');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', async e => {
      e.stopPropagation();
      try {
        await fetch(`/api/library/${encodeURIComponent(name)}`, { method: 'DELETE' });
        gallery.selected.delete(name);
        await galleryLoadGrouped();
        renderGalleryGrid();
        syncGalleryConfirm();
        showToast('Image removed', 'info');
      } catch (err) { showToast('Delete failed: ' + err.message, 'error'); }
    });
  }

  return div;
}


function syncGalleryConfirm() {
  const n = gallery.selected.size;
  document.getElementById('gallery-selected-label').textContent =
    n ? `${n} selected` : '0 selected';
  document.getElementById('gallery-confirm-btn').disabled = n === 0;
}


// ─── Sidebar: Roots & Channel Folders ───────────────────────

function renderGalleryRoots() {
  const container = document.getElementById('gallery-browse-roots');
  container.innerHTML = '';
  gallery.roots
    .filter(r => r.type === 'library')
    .forEach(root => {
      const btn = document.createElement('button');
      btn.className = 'gallery-root-btn';
      btn.textContent = root.name;
      btn.addEventListener('click', () => galleryBrowseTo(root.path));
      container.appendChild(btn);
    });
}

function renderGalleryChannelFolders() {
  const container = document.getElementById('gallery-channel-folders');
  container.innerHTML = '';
  const channels = gallery.roots.filter(r => r.type === 'channel');
  if (!channels.length) {
    container.innerHTML = '<span class="text-muted text-sm" style="padding:4px 0;display:block">No channels yet</span>';
    return;
  }
  channels.forEach(ch => {
    const btn = document.createElement('button');
    btn.className = 'gallery-nav-btn';
    btn.innerHTML = `<span class="gallery-nav-icon">📺</span> ${esc(ch.name.replace('📺 ', ''))}`;
    btn.addEventListener('click', async () => {
      const chName = ch.name.replace('📺 ', '');
      await galleryLoadChannelImages(chName);
      gallery.view = 'channel';
      gallery.browseLabel = chName;
      // Deactivate category buttons
      document.querySelectorAll('.gallery-nav-btn[data-gallery-view]').forEach(b => b.classList.remove('active'));
      // Activate this channel button
      document.querySelectorAll('#gallery-channel-folders .gallery-nav-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      syncGalleryNav();
      renderGalleryGrid();
    });
    container.appendChild(btn);
  });
}
