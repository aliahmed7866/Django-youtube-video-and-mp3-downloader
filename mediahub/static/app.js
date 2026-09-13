'use strict';
const $ = selector => document.querySelector(selector);
const token = $('meta[name="csrf-token"]').content;
const formats = {
  video: [['360', '360p · Small'], ['480', '480p · Standard'], ['720', '720p · HD'], ['1080', '1080p · Full HD']],
  audio: [['128', '128 kbps · Small'], ['192', '192 kbps · Balanced'], ['320', '320 kbps · High']]
};
let jobs = [], filter = 'all', previous = '', paused = false, historyLimit = 200, refreshPromise;
let preferences = {};
try { preferences = JSON.parse(localStorage.getItem('mediahub-preferences') || '{}') || {}; } catch {}

function node(tag, text, cls) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function message(text) { $('#message').textContent = text; }
function source(url) {
  try {
    const host = new URL(url).hostname;
    if (host === 'instagram.com' || host.endsWith('.instagram.com')) return 'Instagram';
    if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'TikTok';
    if (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com')) return 'YouTube';
  } catch {}
  return 'Video';
}
const active = job => ['queued', 'downloading', 'processing', 'cancelling'].includes(job.status);
function savePreferences() {
  const kind = $('input[name=kind]:checked').value;
  preferences = {...preferences, kind, [kind]: $('#quality').value};
  try { localStorage.setItem('mediahub-preferences', JSON.stringify(preferences)); } catch {}
}
function setFormat(kind) {
  const remembered = preferences[kind] || (preferences.kind === kind ? preferences.quality : '');
  const quality = formats[kind].some(([value]) => value === remembered) ? remembered : kind === 'audio' ? '192' : '720';
  $('#quality').replaceChildren(...formats[kind].map(([value, label]) => {
    const option = node('option', label); option.value = value; option.selected = value === quality; return option;
  }));
}
async function api(url, method = 'GET', data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, {
      method, signal: controller.signal,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: data ? JSON.stringify(data) : undefined
    });
    if (response.status === 204) return;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || (response.status === 403 ? 'Session expired. Refresh the page.' : 'Request failed. Try again.'));
    return body;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Media Hub took too long to respond. Check the service and try again.');
    throw error;
  } finally { clearTimeout(timeout); }
}
function openPlayer(job) {
  const player = document.createElement(job.kind === 'audio' ? 'audio' : 'video');
  player.controls = true; player.preload = 'metadata'; player.setAttribute('playsinline', '');
  player.src = `/api/jobs/${job.id}/file?play=1`;
  player.addEventListener('error', () => { $('#player-message').textContent = 'This file could not be played here. Try Save file and open it in your usual player.'; });
  $('#player-title').textContent = job.title || 'Preview';
  $('#player-message').textContent = '';
  $('#player-body').replaceChildren(player);
  $('#player-dialog').showModal();
}
$('#close-player').onclick = () => $('#player-dialog').close();
$('#player-dialog').addEventListener('close', () => {
  const player = $('#player-body').firstElementChild;
  if (player) { player.pause(); player.removeAttribute('src'); player.load(); }
  $('#player-body').replaceChildren();
});

function render() {
  const search = $('#search').value.toLowerCase();
  const platform = $('#source-filter').value;
  const visible = jobs.filter(job =>
    (filter === 'all' || (filter === 'active' ? active(job) : job.status === filter)) &&
    (platform === 'all' || source(job.url) === platform) &&
    (job.title + ' ' + job.url + ' ' + source(job.url)).toLowerCase().includes(search));
  const signature = JSON.stringify([visible, filter, platform, search, jobs.length]);
  if (signature === previous) return;
  previous = signature;
  // Restore keyboard focus across polling updates without changing the open player.
  const focusKey = document.activeElement?.dataset.focusKey;
  $('#jobs').replaceChildren();
  $('#empty').hidden = visible.length > 0;
  $('#empty h3').textContent = jobs.length ? 'No downloads match this view.' : 'A little space for your favourites.';
  $('#empty p').textContent = jobs.length ? 'Try another filter, search, or load more history.' : 'Paste a video link above to save your first video or track.';
  for (const job of visible) {
    const card = node('article', '', 'job');
    card.append(node('div', job.kind === 'audio' ? '♫' : '▷', 'job-icon'));
    const content = node('div', '');
    content.append(node('span', source(job.url), 'source-badge'), node('h3', job.title || job.url), node('p',
      `${job.kind === 'audio' ? 'MP3' : 'MP4'} · ${job.quality}${job.kind === 'audio' ? ' kbps' : 'p max'} · ${new Date(job.created * 1000).toLocaleDateString()}`));
    const statuses = {complete: 'Ready to play or save', processing: 'Finishing your file…', queued: paused ? 'Waiting · Queue paused' : 'Waiting in queue', cancelling: 'Cancelling…', cancelled: 'Cancelled', failed: 'Needs attention'};
    content.append(node('p', job.status === 'downloading' ? `Downloading · ${Math.round(job.progress)}%` : statuses[job.status] || job.status, 'status'));
    if (active(job) && job.status !== 'queued') {
      const progress = document.createElement('progress'); progress.max = 100;
      if (job.status === 'downloading') progress.value = job.progress;
      progress.setAttribute('aria-label', 'Download progress'); content.append(progress);
    }
    if (job.error) content.append(node('p', job.error));
    card.append(content);
    const actions = node('div', '', 'actions');
    if (job.status === 'complete') {
      const play = node('button', job.kind === 'audio' ? 'Listen' : 'Play'); play.dataset.focusKey = job.id + ':play'; play.onclick = () => openPlayer(job); actions.append(play);
      const save = node('a', 'Save file'); save.href = `/api/jobs/${job.id}/file`; actions.append(save);
    }
    const original = node('a', 'Original'); original.href = job.url; original.target = '_blank'; original.rel = 'noopener noreferrer'; original.className = 'original'; actions.append(original);
    function addAction(label, action, method = 'POST') {
      const button = node('button', label); button.dataset.focusKey = job.id + ':' + action;
      button.onclick = async () => {
        if (!action && !confirm('Remove this download and its saved file?')) return;
        button.disabled = true;
        try {
          await api(`/api/jobs/${job.id}${action}`, method);
          if (refreshPromise) await refreshPromise;
          await refresh();
        } catch (error) { message(error.message); }
        finally { button.disabled = false; }
      };
      actions.append(button);
    }
    if (active(job) && job.status !== 'cancelling') addAction('Cancel', '/cancel');
    if (['failed', 'cancelled'].includes(job.status)) addAction('Retry', '/retry');
    if (['complete', 'failed', 'cancelled'].includes(job.status)) addAction('Remove', '', 'DELETE');
    card.append(actions); $('#jobs').append(card);
  }
  if (focusKey) [...document.querySelectorAll('[data-focus-key]')].find(element => element.dataset.focusKey === focusKey)?.focus({preventScroll: true});
}
function refresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const data = await api(`/api/jobs?limit=${historyLimit}`);
      if (paused !== data.paused) previous = '';
      jobs = data.jobs; paused = data.paused;
      $('#count').textContent = data.total;
      $('#storage').textContent = `${(data.free_bytes / 1024 ** 3).toFixed(1)} GB free on device`;
      $('#queue-summary').textContent = `${paused ? 'Queue paused · Current download will finish' : 'Queue running'} · ${data.counts.queued || 0} waiting · ${data.counts.complete || 0} ready`;
      $('#pause-queue').textContent = paused ? 'Resume queue' : 'Pause queue';
      $('#pause-queue').setAttribute('aria-pressed', String(paused));
      $('#load-more').hidden = jobs.length >= data.total || historyLimit >= 5000;
      $('#connection').textContent = ''; render();
    } catch (error) { $('#connection').textContent = 'Cannot reach Media Hub. Check the service in your admin hub.'; }
    finally { $('#jobs').setAttribute('aria-busy', 'false'); refreshPromise = null; }
  })();
  return refreshPromise;
}
$('#pause-queue').onclick = async () => {
  const button = $('#pause-queue'); button.disabled = true;
  try { await api('/api/queue', 'POST', {paused: !paused}); if (refreshPromise) await refreshPromise; await refresh(); }
  catch (error) { message(error.message); }
  finally { button.disabled = false; }
};
$('#load-more').onclick = async () => { if (refreshPromise) await refreshPromise; historyLimit = Math.min(5000, historyLimit + 200); await refresh(); };
$('#download-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#submit').disabled = true;
  const submitted = $('#url').value;
  try {
    await api('/api/jobs', 'POST', {url: submitted, kind: $('input[name=kind]:checked').value, quality: $('#quality').value});
    savePreferences(); if ($('#url').value === submitted) $('#url').value = '';
    message('Added to your download queue.');
    if (refreshPromise) await refreshPromise;
    await refresh();
  } catch (error) { message(error.message); }
  finally { $('#submit').disabled = false; }
});
for (const radio of document.querySelectorAll('input[name=kind]')) radio.addEventListener('change', () => { setFormat(radio.value); savePreferences(); });
$('#quality').addEventListener('change', savePreferences);
if (['audio', 'video'].includes(preferences.kind)) document.querySelector(`input[value=${preferences.kind}]`).checked = true;
setFormat($('input[name=kind]:checked').value);
$('#paste').onclick = async () => {
  try { $('#url').value = await navigator.clipboard.readText(); $('#url').focus(); }
  catch { $('#url').focus(); message('Press and hold the link field, then choose Paste.'); }
};
for (const button of document.querySelectorAll('[data-filter]')) button.onclick = () => {
  filter = button.dataset.filter;
  for (const other of document.querySelectorAll('[data-filter]')) other.setAttribute('aria-pressed', String(other === button));
  render();
};
$('#search').addEventListener('input', render);
$('#source-filter').addEventListener('change', render);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
let installPrompt;
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('#install-app').hidden = false; });
$('#install-app').onclick = async () => {
  if (!installPrompt) return;
  await installPrompt.prompt(); installPrompt = null; $('#install-app').hidden = true;
};
window.addEventListener('appinstalled', () => { $('#install-app').hidden = true; });
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => {});
if (location.search) history.replaceState(null, '', location.pathname);
async function poll() { if (!document.hidden) await refresh(); setTimeout(poll, 2500); }
poll();
