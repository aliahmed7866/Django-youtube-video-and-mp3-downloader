'use strict';
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const draftKey = 'mediahub-editor-draft-v1';
let clips = [], assets = [], ready = false, importing = false, playing = -1, sequence = false;
let draft = {};
try { draft = JSON.parse(localStorage.getItem(draftKey) || '{}') || {}; } catch (_) { /* Fresh draft. */ }
if (Array.isArray(draft.clips)) clips = draft.clips;
for (const id of ['profile', 'shape', 'music-volume']) {
  if (draft[id] != null && [...$(id).options].some(o => o.value === draft[id])) $(id).value = draft[id];
}
function notice(message) { $('notice').textContent = message; }
function save() {
  draft = {clips, profile: $('profile').value, shape: $('shape').value,
    music: $('music').value, 'music-volume': $('music-volume').value};
  try { localStorage.setItem(draftKey, JSON.stringify(draft)); }
  catch (_) { notice('Browser storage is unavailable. Keep this page open to retain your timeline.'); }
}
function clock(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls = 'secondary') {
  const node = el('button', text, cls); node.type = 'button';
  node.addEventListener('click', async () => {
    node.disabled = true;
    try { await action(); } catch (error) { notice(error.message); }
    finally { node.disabled = false; }
  });
  return node;
}
async function api(url, method = 'GET', body) {
  const response = await fetch(url, {method, headers: {'X-CSRF-Token': csrf,
    ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  if (response.status === 204) return null;
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || (response.status === 403
    ? 'Session expired. Reload this page; your timeline is saved.' : `Request failed (${response.status}).`));
  return result;
}
function valid(clip) {
  const asset = assets.find(a => a.id === clip.asset_id);
  return asset && Number.isFinite(clip.start) && Number.isFinite(clip.end) && clip.start >= 0 &&
    clip.end <= asset.duration && clip.end - clip.start >= .05 && clip.speed >= .25 && clip.speed <= 4 &&
    Number.isFinite(clip.volume) && clip.volume >= 0 && clip.volume <= 2;
}
function totals() {
  const duration = clips.reduce((n, c) => n + Math.max(0, (c.end - c.start) / c.speed || 0), 0);
  $('duration').textContent = `${clips.length} clip${clips.length === 1 ? '' : 's'} · ${clock(duration)}`;
  $('export').disabled = !ready || importing || !clips.length || !clips.every(valid) || duration > 7200 ||
    ($('profile').value === 'instagram' && !$('allow-lossy').checked);
  $('play').disabled = !clips.length || !clips.every(valid);
  $('empty').hidden = clips.length > 0;
}
function stopPreview() { playing = -1; sequence = false; $('preview').pause(); }
function addClip(asset) {
  if (clips.length >= 50) throw new Error('The timeline can contain up to 50 clips.');
  stopPreview();
  clips.push({asset_id: asset.id, start: 0, end: asset.duration, speed: 1, volume: 1});
  save(); renderTimeline();
}
function playClip(index, all = false) {
  if (!clips[index] || !valid(clips[index])) return;
  const clip = clips[index], asset = assets.find(a => a.id === clip.asset_id), video = $('preview');
  playing = index; sequence = all;
  video.pause();
  video.src = `/api/editor/assets/${clip.asset_id}/file`;
  video.onloadedmetadata = () => {
    video.currentTime = clip.start;
    video.playbackRate = clip.speed;
    video.volume = Math.min(1, clip.volume);
    video.muted = clip.volume === 0;
    video.play().catch(() => { $('preview-label').textContent = 'Tap play in the video player to begin.'; });
  };
  $('preview-label').textContent = `${index + 1}. ${asset.name} · ${clip.speed}×`;
}
function nextPreview() {
  if (playing < 0) return;
  if (sequence && playing + 1 < clips.length) playClip(playing + 1, true);
  else stopPreview();
}
$('preview').addEventListener('timeupdate', () => {
  const clip = clips[playing];
  if (clip && $('preview').currentTime >= clip.end) nextPreview();
});
$('preview').addEventListener('ended', nextPreview);
$('preview').addEventListener('error', () => {
  stopPreview(); $('preview-label').textContent = 'This browser cannot preview the file. You can still try exporting it to MP4.';
});
$('play').onclick = () => playClip(0, true);
$('stop').onclick = stopPreview;
function renderTimeline() {
  $('timeline').replaceChildren();
  clips.forEach((clip, index) => {
    const asset = assets.find(a => a.id === clip.asset_id);
    const card = el('article', undefined, 'clip');
    card.append(el('h3', `${String(index + 1).padStart(2, '0')} / ${asset ? asset.name : 'Missing clip — import again'}`));
    const fields = el('div', undefined, 'clip-fields');
    for (const [key, title] of [['start', 'Start (seconds)'], ['end', 'End (seconds)'], ['speed', 'Speed'], ['volume', 'Clip volume']]) {
      const label = el('label', title), input = el(key === 'start' || key === 'end' ? 'input' : 'select');
      if (input.tagName === 'INPUT') {
        input.type = 'number'; input.min = '0'; input.max = String(asset?.duration || 0); input.step = '0.01';
        input.value = String(clip[key]);
      } else {
        const choices = key === 'speed' ? [[.25, '0.25× · Slowest'], [.5, '0.5× · Slow'], [.75, '0.75×'], [1, '1× · Original'], [1.5, '1.5×'], [2, '2× · Fast'], [4, '4× · Fastest']]
          : [[0, '0% · Muted'], [.25, '25%'], [.5, '50%'], [1, '100% · Original'], [1.5, '150%'], [2, '200%']];
        choices.forEach(([value, name]) => input.add(new Option(name, String(value)))); input.value = String(clip[key]);
      }
      input.setAttribute('aria-label', `${title} for clip ${index + 1}`);
      input.addEventListener('input', () => {
        stopPreview(); clip[key] = input.value === '' ? null : Number(input.value);
        card.classList.toggle('clip-invalid', !valid(clip)); save(); totals();
      });
      label.append(input); fields.append(label);
    }
    card.append(fields);
    const actions = el('div', undefined, 'actions');
    actions.append(button('Preview', () => playClip(index)));
    const up = button('↑ Move up', () => { stopPreview(); [clips[index - 1], clips[index]] = [clips[index], clips[index - 1]]; save(); renderTimeline(); });
    const down = button('↓ Move down', () => { stopPreview(); [clips[index + 1], clips[index]] = [clips[index], clips[index + 1]]; save(); renderTimeline(); });
    up.disabled = index === 0; down.disabled = index === clips.length - 1;
    actions.append(up, down, button('Remove', () => { stopPreview(); clips.splice(index, 1); save(); renderTimeline(); }, 'secondary danger'));
    card.append(actions); card.classList.toggle('clip-invalid', !valid(clip)); $('timeline').append(card);
  });
  totals();
}
function renderAssets() {
  $('assets').replaceChildren(); $('asset-count').textContent = assets.length;
  const music = $('music').value || draft.music || '';
  $('music').replaceChildren(new Option('None', ''));
  assets.forEach(asset => {
    const row = el('div', undefined, 'asset-row');
    row.append(el('p', `${asset.name} · ${clock(asset.duration)} · ${(asset.size / 1048576).toFixed(1)} MB`));
    const actions = el('div', undefined, 'actions');
    if (asset.kind === 'video') actions.append(button('+ Add to timeline', () => addClip(asset)));
    else $('music').add(new Option(asset.name, asset.id));
    actions.append(button('Delete copy', async () => {
      if (!confirm(`Delete the imported copy of “${asset.name}”? This also removes its timeline clips. Your original file stays untouched.`)) return;
      await api(`/api/editor/assets/${asset.id}`, 'DELETE');
      stopPreview(); clips = clips.filter(c => c.asset_id !== asset.id);
      if ($('music').value === asset.id) $('music').value = '';
      save(); await refresh(true);
    }, 'secondary danger'));
    row.append(actions); $('assets').append(row);
  });
  if ([...$('music').options].some(o => o.value === music)) $('music').value = music;
}
function importFile(file, kind) {
  if (file.size > 2 * 1024 ** 3) return Promise.reject(new Error(`${file.name} is larger than 2 GB.`));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/editor/assets?kind=${kind}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = event => {
      const percent = event.lengthComputable ? Math.round(event.loaded / event.total * 100) : 0;
      $('upload-progress').value = percent;
      $('upload-status').textContent = `${file.name} · ${percent === 100 ? 'Checking media…' : `Importing ${percent}%`}`;
    };
    xhr.onerror = () => reject(new Error('Import connection lost. Keep Termux running and try again.'));
    xhr.onload = () => {
      let data = {}; try { data = JSON.parse(xhr.responseText); } catch (_) { /* HTTP error. */ }
      if (xhr.status === 201) resolve(data);
      else reject(new Error(data.error || `Import failed (${xhr.status}). Reload the page and try again.`));
    };
    xhr.send(file);
  });
}
for (const [id, kind] of [['videos', 'video'], ['audio', 'audio']]) {
  $(id).onchange = async () => {
    const files = [...$(id).files]; if (!files.length) return;
    importing = true; $('videos').disabled = $('audio').disabled = true; $('upload-progress').hidden = false; totals();
    let count = 0; const failures = [];
    for (const file of files) {
      try {
        const asset = await importFile(file, kind); assets.push(asset); count++;
        if (kind === 'video') addClip(asset);
        renderAssets();
        if (kind === 'audio') { $('music').value = asset.id; save(); }
      } catch (error) { failures.push(`${file.name}: ${error.message}`); }
    }
    importing = false; $('videos').disabled = $('audio').disabled = false; $(id).value = '';
    $('upload-progress').hidden = true; $('upload-status').textContent = `${count} file${count === 1 ? '' : 's'} imported.`;
    if (failures.length) notice(failures.join(' '));
    await refresh(true).catch(error => notice(error.message)); totals();
  };
}
$('clear').onclick = () => {
  if (clips.length && confirm('Clear the timeline? Imported files and finished exports will stay available.')) {
    stopPreview(); clips = []; save(); renderTimeline();
  }
};
const losslessHelp = $('quality-help').textContent;
function qualityOptions() {
  const lossless = $('profile').value === 'lossless';
  $('shape').disabled = lossless;
  $('lossy-consent').hidden = lossless;
  $('export').textContent = lossless ? 'Export lossless master ↗' : 'Export Instagram MP4 ↗';
  $('quality-help').textContent = lossless ? losslessHelp : 'Instagram copy: 1080p canvas, H.264 high quality, 30 fps (60 fps for higher-frame-rate sources) and AAC audio. Clips fit without cropping; resizing and frame-rate conversion may change detail/motion. Smaller sources cannot gain real detail by upscaling. This is lossy, and Instagram may recompress it again. Keep your lossless master separately.';
  totals();
}
$('profile').onchange = () => { $('allow-lossy').checked = false; qualityOptions(); save(); };
$('allow-lossy').onchange = totals;
for (const id of ['shape', 'music', 'music-volume']) $(id).onchange = save;
qualityOptions();
$('export').onclick = async () => {
  $('export').disabled = true;
  try {
    await api('/api/editor/exports', 'POST', {clips, shape: $('shape').value, profile: $('profile').value,
      allow_lossy: $('allow-lossy').checked,
      music_id: $('music').value || null, music_volume: Number($('music-volume').value)});
    notice('Export queued. Keep Termux running; the download button appears when it finishes.');
    await refresh();
  } catch (error) { notice(error.message); }
  finally { totals(); }
};
let jobsSignature = '';
function renderJobs(jobs) {
  // Preserve focus on action buttons while polling unchanged jobs.
  const signature = JSON.stringify(jobs); if (signature === jobsSignature) return; jobsSignature = signature;
  $('exports').replaceChildren();
  if (!jobs.length) { $('exports').append(el('p', 'Your finished films will appear here.', 'hint')); return; }
  jobs.forEach(job => {
    const row = el('article', undefined, 'job'), info = el('div');
    info.append(el('h3', `Film · ${new Date(job.created * 1000).toLocaleString()}`));
    info.append(el('p', `${job.status} · ${Math.round(job.progress)}%`, 'status'));
    if (job.error) info.append(el('p', job.error));
    const active = ['queued', 'rendering', 'cancelling'].includes(job.status);
    if (active) { const bar = el('progress'); bar.max = 100; bar.value = job.progress; bar.setAttribute('aria-label', 'Export progress'); info.append(bar); }
    const actions = el('div', undefined, 'actions');
    if (active) {
      const cancel = button('Cancel', async () => { await api(`/api/editor/exports/${job.id}/cancel`, 'POST'); await refresh(); });
      cancel.disabled = job.status === 'cancelling'; actions.append(cancel);
    } else {
      if (job.status === 'complete') {
        const lossless = job.profile === 'lossless';
        const link = el('a', lossless ? 'Download lossless MKV' : 'Download MP4'); link.href = `/api/editor/exports/${job.id}/file`; link.download = ''; actions.append(link);
        if (lossless) info.append(el('p', 'Lossless master · native frames · open in a compatible player'));
        if (!lossless) actions.append(button('Preview film', () => {
          stopPreview(); const video = $('preview'); video.onloadedmetadata = null;
          video.src = `/api/editor/exports/${job.id}/file?play=1`; video.playbackRate = 1; video.volume = 1; video.muted = false;
          video.play().catch(() => {}); $('preview-label').textContent = 'Finished film · exported framing and audio'; video.scrollIntoView({behavior: 'smooth', block: 'center'});
        }));
      }
      actions.append(button('Delete export', async () => {
        if (!confirm('Delete this export from Media Hub? Download it first if you want to keep it.')) return;
        await api(`/api/editor/exports/${job.id}`, 'DELETE'); await refresh();
      }, 'secondary danger'));
    }
    row.append(info, actions); $('exports').append(row);
  });
}
async function refresh(full = false) {
  const state = await api('/api/editor');
  ready = state.ready; $('connection').textContent = '';
  $('storage').textContent = `${(state.free_bytes / 1024 ** 3).toFixed(1)} GB free`;
  if (!ready) notice('FFmpeg is missing. In Termux run: pkg install ffmpeg');
  if (full || JSON.stringify(assets) !== JSON.stringify(state.assets)) {
    assets = state.assets; renderAssets(); renderTimeline();
  }
  renderJobs(state.jobs); totals();
}
async function poll() {
  try { await refresh(); }
  catch (_) { $('connection').textContent = 'Cannot reach Media Hub. Keep Termux running; reconnecting automatically…'; }
  setTimeout(poll, 2500);
}
refresh(true).then(poll).catch(error => { notice(error.message); setTimeout(poll, 2500); });
