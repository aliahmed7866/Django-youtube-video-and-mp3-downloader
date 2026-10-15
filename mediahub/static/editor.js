/* Local source media is never modified. Timeline positions are output seconds. */
'use strict';
const $ = id => document.getElementById(id), M = window.TimelineModel;
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const draftKey = 'mediahub-editor-draft-v1';
let clips = [], audioClips = [], assets = [], ready = false, importing = false;
let selection = null, playhead = 0, playing = -1, running = false, previewFrame = 0;
let reverseClock = 0, reverseAt = 0, reverseSeek = 0, audioSync = 0, previewReversed = false;
let drag = null, undoStack = [], redoStack = [], audioPlayers = [], draft = {};
try { draft = JSON.parse(localStorage.getItem(draftKey) || '{}') || {}; } catch (_) { /* Fresh draft. */ }
if (Array.isArray(draft.clips)) clips = draft.clips;
if (Array.isArray(draft.audioClips)) audioClips = draft.audioClips;
for (const id of ['profile', 'shape']) {
  if (draft[id] != null && [...$(id).options].some(o => o.value === draft[id])) $(id).value = draft[id];
}
if (Number.isFinite(draft.zoom)) $('zoom').value = String(draft.zoom);
let pixels = 2 * 80 ** (Number($('zoom').value) / 100);
const assetFor = c => assets.find(a => a.id === c.asset_id);
const duration = () => clips.reduce((n, c) => n + M.span(c), 0);
const extent = () => Math.max(duration(), ...audioClips.map(c => c.at + M.audioSpan(c)), 1);
const sourceDuration = c => assetFor(c)?.duration || c.end;
function notice(message) { $('notice').textContent = message; }
function save() {
  draft = {clips, audioClips, profile: $('profile').value, shape: $('shape').value, zoom: Number($('zoom').value)};
  try { localStorage.setItem(draftKey, JSON.stringify(draft)); }
  catch (_) { notice('Browser storage is unavailable. Keep this page open to retain your timeline.'); }
}
function clock(seconds, precise = false) {
  const s = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(s / 60)}:${(precise ? (s % 60).toFixed(2) : String(Math.floor(s % 60))).padStart(precise ? 5 : 2, '0')}`;
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
    finally { if (node.isConnected) node.disabled = false; }
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
function valid(c) {
  return assetFor(c) && Number.isFinite(c.start) && Number.isFinite(c.end) && c.start >= 0 &&
    c.end <= sourceDuration(c) + .000001 && c.end - c.start >= .049999 && c.speed >= .25 && c.speed <= 4 &&
    Number.isFinite(c.volume) && c.volume >= 0 && c.volume <= 2;
}
function validAudio(c) {
  return valid(c) && Number.isFinite(c.at) && c.at >= 0 && c.at + M.audioSpan(c) <= 7200 &&
    M.audioSpan(c) >= .01 && [1, 2, 3].includes(c.track);
}
function totals() {
  const total = duration();
  $('duration').textContent = `${clips.length} video · ${audioClips.length} audio · ${clock(total, true)}`;
  $('export').disabled = !ready || importing || !clips.length || clips.length > 50 || audioClips.length > 20 ||
    !clips.every(valid) || !audioClips.every(validAudio) || total > 7200 ||
    ($('profile').value === 'instagram' && !$('allow-lossy').checked);
  $('play').disabled = !clips.length || !clips.every(valid) || !audioClips.every(validAudio);
  $('empty').hidden = clips.length > 0;
  $('undo').disabled = !undoStack.length; $('redo').disabled = !redoStack.length;
  $('split').disabled = !selection;
  $('seek').max = String(Math.max(total, .01));
  playhead = M.clamp(playhead, 0, total); updatePlayhead();
}
function snapshot() { return JSON.stringify({clips, audioClips, selection, playhead}); }
function restore(data) { ({clips, audioClips, selection, playhead} = JSON.parse(data)); }
function remember(before) {
  undoStack.push(before); if (undoStack.length > 40) undoStack.shift(); redoStack = [];
}
function change(action) {
  stopPreview(); const before = snapshot();
  try {
    action();
    if (duration() > 7200) throw new Error('Keep the video timeline within two hours.');
  } catch (error) { restore(before); renderTimeline(); notice(error.message); return; }
  if (snapshot() !== before) remember(before);
  save(); renderTimeline();
}
function history(from, to) {
  if (!from.length) return;
  stopPreview(); to.push(snapshot()); restore(from.pop()); save(); renderTimeline();
}
function addClip(asset) {
  change(() => {
    if (clips.length >= 50) throw new Error('Use at most 50 video clips.');
    if (duration() > 7199.95) throw new Error('The video timeline has reached its two-hour limit.');
    clips.push({asset_id: asset.id, start: 0, end: Math.min(asset.duration, 7200 - duration()), speed: 1, volume: 1});
    if (asset.duration > M.span(clips[clips.length - 1])) notice('The added clip was shortened to fit the two-hour timeline. Adjust its source in/out to choose another section.');
    selection = {kind: 'video', index: clips.length - 1};
  });
}
function addAudio(asset) {
  change(() => {
    if (audioClips.length >= 20) throw new Error('Use at most 20 independent audio clips.');
    if (playhead > 7199.95) throw new Error('Move the playhead earlier before adding audio.');
    audioClips.push({asset_id: asset.id, start: 0, end: Math.min(asset.duration, 7200 - playhead),
      speed: 1, volume: .3, at: playhead, track: selection?.kind === 'audio' ? audioClips[selection.index].track : 1, loop: false});
    selection = {kind: 'audio', index: audioClips.length - 1};
  });
}
function selected() { return selection ? (selection.kind === 'video' ? clips : audioClips)[selection.index] : null; }
function select(kind, index) { selection = {kind, index}; renderInspector(); highlight(); totals(); }
function highlight() {
  document.querySelectorAll('.timeline-clip').forEach(n => n.classList.toggle('selected',
    n.dataset.kind === selection?.kind && Number(n.dataset.index) === selection?.index));
}
function renderInspector() {
  const c = selected(); $('timeline').replaceChildren();
  if (!c) { selection = null; $('selection-title').textContent = 'Select a clip'; $('timeline').append(el('p', 'Tap a video or audio block below to edit it.', 'hint')); return; }
  const audio = selection.kind === 'audio', index = selection.index;
  $('selection-title').textContent = `${audio ? 'Audio' : 'Video'} ${index + 1} · ${assetFor(c)?.name || 'Missing file'}`;
  const fields = el('div', undefined, 'clip-fields');
  const specs = [['start', 'Source in (seconds)', 0, sourceDuration(c), .01], ['end', 'Source out (seconds)', 0, sourceDuration(c), .01],
    ['speed', 'Speed (×)', .25, 4, .05], ['volume', 'Volume (1 = original)', 0, 2, .05]];
  if (audio) specs.unshift(['at', 'Timeline position (seconds)', 0, 7200, .01]);
  if (audio && c.loop) specs.push(['length', 'Loop length (seconds)', .05, 7200 - c.at, .01]);
  for (const [key, title, min, max, step] of specs) {
    const label = el('label', title), input = el('input'); input.type = 'number'; input.min = String(min); input.max = String(max);
    input.step = String(step); input.value = String(Math.round(c[key] * 1000000) / 1000000); input.dataset.field = key;
    input.onchange = () => change(() => {
      const value = Number(input.value), next = {...c, [key]: value};
      if (input.value === '' || !(audio ? validAudio(next) : valid(next))) throw new Error('Keep trims within the source and the end after the start. Speed: 0.25–4×; volume: 0–2; timeline: up to two hours.');
      Object.assign(c, next);
    });
    label.append(input); fields.append(label);
  }
  const reverseLabel = el('label', undefined, 'loop-choice'), reverse = el('input');
  reverse.type = 'checkbox'; reverse.checked = !!c.reverse; reverse.dataset.field = 'reverse';
  reverse.onchange = () => {
    change(() => { c.reverse = reverse.checked; });
    if (!audio) seekTo(M.positions(clips)[index].at);
  };
  reverseLabel.append(reverse, document.createTextNode(audio ? 'Reverse audio' : 'Reverse video and linked audio'));
  fields.append(reverseLabel);
  if (audio) {
    const label = el('label', 'Audio track'), input = el('select');
    [1, 2, 3].forEach(n => input.add(new Option(`Audio ${n}`, n))); input.value = String(c.track); input.dataset.field = 'track';
    input.onchange = () => change(() => { c.track = Number(input.value); }); label.append(input); fields.append(label);
    const loopLabel = el('label', undefined, 'loop-choice'), loop = el('input'); loop.type = 'checkbox'; loop.checked = c.loop;
    loop.onchange = () => change(() => { c.length = M.audioSpan(c); c.loop = loop.checked; });
    loopLabel.append(loop, document.createTextNode('Loop the selected source section')); fields.append(loopLabel);
  }
  $('timeline').append(fields, el('p', `Source: ${clock(sourceDuration(c), true)} · Selected: ${clock(audio ? M.audioSpan(c) : M.span(c), true)}${audio && c.loop ? ' · Edge handles change loop length; source in/out chooses what repeats.' : ''}`, 'hint'));
  const actions = el('div', undefined, 'actions');
  if (!audio) {
    for (const [label, delta] of [['← Earlier', -1], ['Later →', 1]]) {
      const b = button(label, () => change(() => {
        [clips[index], clips[index + delta]] = [clips[index + delta], clips[index]]; selection.index += delta;
      })); b.disabled = index + delta < 0 || index + delta >= clips.length; actions.append(b);
    }
    if (assetFor(c)?.has_audio) actions.append(button('Detach audio', () => change(() => {
      if (audioClips.length >= 20) throw new Error('Use at most 20 independent audio clips.');
      audioClips.push({...c, at: M.positions(clips)[index].at, track: 1, loop: false}); c.volume = 0;
      selection = {kind: 'audio', index: audioClips.length - 1};
    })));
  }
  actions.append(button('Duplicate', () => change(() => {
    const list = audio ? audioClips : clips;
    if (list.length >= (audio ? 20 : 50)) throw new Error('The timeline has reached its clip limit.');
    list.splice(index + 1, 0, {...c, ...(audio ? {at: Math.min(7200 - M.audioSpan(c), c.at + M.audioSpan(c))} : {})});
    selection.index++;
  })), button('Remove', () => change(() => { (audio ? audioClips : clips).splice(index, 1); selection = null; }), 'secondary danger'));
  $('timeline').append(actions);
}
function renderGraph() {
  const surface = $('timeline-surface'); surface.replaceChildren();
  const width = Math.max(480, (extent() + 2) * pixels);
  surface.style.width = `${88 + width}px`;
  const ruler = el('div', undefined, 'time-row ruler'), corner = el('div', 'TIME', 'track-label'), ticks = el('div', undefined, 'track-lane');
  ruler.append(corner, ticks); ticks.dataset.ruler = 'true';
  surface.append(ruler);
  const lanes = {};
  for (const [key, name] of [['video', 'V1 · Video'], ['linked', '↳ Audio'], ['1', 'A1 · Audio'], ['2', 'A2 · Audio'], ['3', 'A3 · Audio']]) {
    const row = el('div', undefined, `time-row ${key === 'linked' ? 'linked-row' : ''}`), lane = el('div', undefined, 'track-lane');
    lane.dataset.lane = key; row.append(el('div', name, 'track-label'), lane); surface.append(row); lanes[key] = lane;
  }
  const positions = M.positions(clips);
  function block(c, index, kind, lane, at, length, linked = false) {
    const box = el('div', undefined, `timeline-clip ${kind} ${linked ? 'linked' : ''}`);
    box.dataset.kind = kind; box.dataset.index = String(index); box.style.left = `${at * pixels}px`; box.style.width = `${Math.max(1, length * pixels)}px`;
    const name = assetFor(c)?.name || 'Missing file';
    const body = el('button', `${linked ? (c.volume ? '♪ ' : 'Muted · ') : ''}${name}${c.reverse ? ' · REVERSE' : ''}${c.loop ? ' · LOOP' : ''}`, 'clip-body');
    body.type = 'button'; body.dataset.action = linked ? 'select' : 'move'; body.title = `${name} · ${clock(at, true)}–${clock(at + length, true)}`;
    body.setAttribute('aria-label', `${kind} clip ${index + 1}: ${name}. ${linked ? 'Linked to video.' : 'Drag to move; arrow keys also move.'}`);
    body.onclick = () => select(kind, index); box.append(body);
    if (!linked) for (const edge of ['start', 'end']) {
      const handle = el('button', '', `trim-handle trim-${edge}`); handle.type = 'button'; handle.dataset.action = edge;
      handle.setAttribute('aria-label', `Trim ${edge} of ${kind} clip ${index + 1}`); handle.title = `Drag to trim ${edge}; arrow keys: 0.05s, Shift: 0.5s`;
      box.append(handle);
    }
    box.classList.toggle('clip-invalid', !(kind === 'audio' ? validAudio(c) : valid(c)));
    lane.append(box);
  }
  clips.forEach((c, i) => { const p = positions[i]; block(c, i, 'video', lanes.video, p.at, p.length);
    if (assetFor(c)?.has_audio) block(c, i, 'video', lanes.linked, p.at, p.length, true);
  });
  audioClips.forEach((c, i) => { if (lanes[c.track]) block(c, i, 'audio', lanes[c.track], c.at, M.audioSpan(c)); });
  const end = el('div', undefined, 'film-end'); end.style.left = `${88 + duration() * pixels}px`; end.title = 'Video end'; surface.append(end);
  const marker = el('div', undefined, 'playhead'); marker.id = 'playhead'; marker.setAttribute('aria-hidden', 'true'); surface.append(marker);
  renderRuler(); highlight(); updatePlayhead();
}
function renderRuler() {
  const ticks = document.querySelector('[data-ruler]'); if (!ticks) return;
  ticks.replaceChildren();
  const interval = [0.1, .25, .5, 1, 2, 5, 10, 30, 60, 120, 300, 600].find(s => s * pixels >= 65) || 600;
  const from = Math.max(0, Math.floor(($('timeline-scroll').scrollLeft - 88) / pixels / interval) * interval);
  const to = ($('timeline-scroll').scrollLeft + ($('timeline-scroll').clientWidth || 800)) / pixels;
  for (let t = from; t <= to; t += interval) {
    const tick = el('span', clock(t, interval < 1), 'time-tick'); tick.style.left = `${t * pixels}px`; ticks.append(tick);
  }
}
function renderTimeline() { renderInspector(); renderGraph(); totals(); }
function updatePlayhead() {
  $('timecode').textContent = clock(playhead, true); $('seek').value = String(playhead);
  if ($('playhead')) $('playhead').style.left = `${88 + playhead * pixels}px`;
}
function timelineTime(event) { return (event.clientX - $('timeline-surface').getBoundingClientRect().left - 88) / pixels; }
function snapTime(time, kind, index) {
  if (!$('snap').checked) return time;
  const points = [0, playhead, duration()];
  M.positions(clips).forEach((p, i) => { if (kind !== 'video' || i !== index) points.push(p.at, p.at + p.length); });
  audioClips.forEach((c, i) => { if (kind !== 'audio' || i !== index) points.push(c.at, c.at + M.audioSpan(c)); });
  const closest = points.reduce((best, p) => Math.abs(p - time) < Math.abs(best - time) ? p : best, Infinity);
  return Math.abs(closest - time) * pixels <= 7 ? closest : time;
}
const scroll = $('timeline-scroll');
scroll.addEventListener('scroll', renderRuler, {passive: true});
scroll.addEventListener('pointerdown', event => {
  if (event.button !== 0 || drag) return;
  const target = event.target.closest('[data-action]'), box = target?.closest('.timeline-clip');
  if (!box && !event.target.closest('[data-ruler]')) return;
  event.preventDefault(); stopPreview();
  if (!box) { drag = {ruler: true, pointer: event.pointerId}; seekTo(timelineTime(event)); }
  else {
    const kind = box.dataset.kind, index = Number(box.dataset.index); select(kind, index);
    if (target.dataset.action === 'select') return;
    const c = (kind === 'video' ? clips : audioClips)[index];
    drag = {kind, index, action: target.dataset.action, pointer: event.pointerId, before: snapshot(), c: {...c},
      x: event.clientX, scroll: scroll.scrollLeft, moved: false, at: kind === 'audio' ? c.at : M.positions(clips)[index].at};
  }
  scroll.setPointerCapture?.(event.pointerId);
});
scroll.addEventListener('pointermove', event => {
  if (!drag || drag.pointer !== event.pointerId) return;
  if (drag.ruler) { seekTo(timelineTime(event)); return; }
  const bounds = scroll.getBoundingClientRect();
  if (event.clientX > bounds.right - 24) scroll.scrollLeft += 12;
  else if (event.clientX < bounds.left + 104) scroll.scrollLeft -= 12;
  const delta = (event.clientX - drag.x + scroll.scrollLeft - drag.scroll) / pixels;
  if (Math.abs(delta * pixels) < 3 && !drag.moved) return;
  drag.moved = true;
  const {kind, index, action, c, at} = drag, list = kind === 'video' ? clips : audioClips;
  if (action === 'move' && kind === 'video') {
    drag.target = Math.max(0, timelineTime(event));
    const result = M.reorder(JSON.parse(drag.before).clips, index, drag.target);
    $('timeline-status').textContent = `Release to place video ${index + 1} at position ${result.index + 1}.`;
    document.querySelectorAll(`.timeline-clip.video[data-index="${index}"]`).forEach(n => { n.style.transform = `translateX(${delta * pixels}px)`; n.classList.add('dragging'); });
    return;
  }
  if (action === 'move') {
    let target = snapTime(c.at + delta, kind, index);
    if (target === c.at + delta) target = snapTime(target + M.audioSpan(c), kind, index) - M.audioSpan(c);
    const lane = [...document.querySelectorAll('[data-lane]')].find(n => { const r = n.getBoundingClientRect(); return ['1', '2', '3'].includes(n.dataset.lane) && event.clientY >= r.top && event.clientY < r.bottom; });
    list[index] = {...c, at: M.clamp(target, 0, 7200 - M.audioSpan(c)), track: lane ? Number(lane.dataset.lane) : c.track};
  } else {
    const edge = at + (action === 'end' ? (kind === 'audio' ? M.audioSpan(c) : M.span(c)) : 0);
    const amount = snapTime(edge + delta, kind, index) - edge;
    list[index] = kind === 'video' ? M.trimVideo(c, action, amount, sourceDuration(c)) : M.trimAudio(c, action, amount, sourceDuration(c));
  }
  renderGraph(); updatePlayhead();
  $('timeline-status').textContent = `${kind === 'audio' ? 'Audio' : 'Video'} ${index + 1}: source ${clock(list[index].start, true)}–${clock(list[index].end, true)}${kind === 'audio' ? ` at ${clock(list[index].at, true)}` : ''}`;
});
function finishDrag(event, cancel = false) {
  if (!drag || drag.pointer !== event.pointerId) return;
  const current = drag; drag = null;
  if (!current.ruler) {
    if (cancel || duration() > 7200) {
      restore(current.before);
      if (!cancel) notice('Keep the video timeline within two hours.');
    }
    else if (current.moved) {
      if (current.kind === 'video' && current.action === 'move') {
        const result = M.reorder(clips, current.index, current.target); clips = result.clips; selection.index = result.index;
      }
      if (snapshot() !== current.before) remember(current.before);
    }
    save(); renderTimeline();
  }
  if (scroll.hasPointerCapture?.(event.pointerId)) scroll.releasePointerCapture(event.pointerId);
}
scroll.addEventListener('pointerup', e => finishDrag(e));
scroll.addEventListener('pointercancel', e => finishDrag(e, true));
scroll.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const target = event.target.closest('[data-action]'), box = target?.closest('.timeline-clip'); if (!box) return;
  event.preventDefault(); const kind = box.dataset.kind, index = Number(box.dataset.index), action = target.dataset.action;
  if (action === 'select') return;
  select(kind, index); const direction = event.key === 'ArrowLeft' ? -1 : 1;
  change(() => {
    const list = kind === 'video' ? clips : audioClips, c = list[index], delta = direction * (event.shiftKey ? .5 : .05);
    if (action === 'move' && kind === 'video') {
      const to = M.clamp(index + direction, 0, clips.length - 1); [clips[index], clips[to]] = [clips[to], clips[index]]; selection.index = to;
    } else if (action === 'move') c.at = M.clamp(c.at + delta, 0, 7200 - M.audioSpan(c));
    else list[index] = kind === 'video' ? M.trimVideo(c, action, delta, sourceDuration(c)) : M.trimAudio(c, action, delta, sourceDuration(c));
  });
  document.querySelector(`.timeline-clip.${kind}:not(.linked)[data-index="${selection.index}"] [data-action="${action}"]`)?.focus({preventScroll: true});
});
$('undo').onclick = () => history(undoStack, redoStack); $('redo').onclick = () => history(redoStack, undoStack);
$('zoom').oninput = () => { pixels = 2 * 80 ** (Number($('zoom').value) / 100); renderGraph(); save(); };
$('fit').onclick = () => {
  pixels = M.clamp((scroll.clientWidth - 108) / extent(), 2, 160);
  $('zoom').value = String(100 * Math.log(pixels / 2) / Math.log(80)); scroll.scrollLeft = 0; renderGraph(); save();
};
$('seek').oninput = () => seekTo(Number($('seek').value));
$('split').onclick = () => change(() => {
  const c = selected(); if (!c) return;
  const audio = selection.kind === 'audio', list = audio ? audioClips : clips;
  if (list.length >= (audio ? 20 : 50)) throw new Error('The timeline has reached its clip limit.');
  const at = audio ? c.at : M.positions(clips)[selection.index].at;
  list.splice(selection.index, 1, ...M.split(c, playhead - at, audio)); selection.index++;
});
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !event.target.matches('input,select,textarea')) {
    event.preventDefault(); if (event.shiftKey) history(redoStack, undoStack); else history(undoStack, redoStack);
  }
});
function stopPreview() {
  running = false; playing = -1; cancelAnimationFrame(previewFrame); $('preview').pause();
  for (const p of audioPlayers) { if (!p) continue; p.pause(); p.removeAttribute('src'); p.load(); } audioPlayers = [];
}
function syncAudio(force = false) {
  if (!running) return;
  const now = performance.now();
  if (!force && now - audioSync < 80) return;
  audioSync = now;
  audioClips.forEach((c, i) => {
    let player = audioPlayers[i];
    const offset = playhead - c.at, length = M.audioSpan(c);
    if (offset < 0 || offset >= length || !c.volume || c.reverse) { player?.pause(); return; }
    if (!player) { player = document.createElement('audio'); player.preload = 'metadata'; player.src = `/api/editor/assets/${c.asset_id}/file`; audioPlayers[i] = player; }
    const source = c.start + ((offset * c.speed) % (c.end - c.start));
    player.playbackRate = c.speed; player.volume = Math.min(1, c.volume);
    if (force || Math.abs(player.currentTime - source) > .15) { try { player.currentTime = source; } catch (_) { /* Await metadata. */ } }
    if (player.paused) player.play().catch(() => { $('preview-label').textContent = 'Some audio cannot play in this browser. Export to hear the finished mix.'; });
  });
}
function locate(time) {
  const positions = M.positions(clips);
  const index = positions.findIndex(p => time < p.at + p.length - .000001);
  return {positions, index: index < 0 ? clips.length - 1 : index};
}
function loadPreview(time, autoplay) {
  if (!clips.length) return;
  const {positions, index} = locate(time), c = clips[index]; if (!valid(c)) return;
  const video = $('preview'), src = `/api/editor/assets/${c.asset_id}/file`, sourceTime = M.sourceTime(c, Math.max(0, time - positions[index].at));
  playing = index; previewReversed = !!c.reverse; reverseClock = 0; reverseSeek = 0; reverseAt = time;
  const configure = () => {
    if (playing !== index) return;
    video.currentTime = Math.min(c.end - .00001, sourceTime); video.playbackRate = c.speed;
    video.volume = Math.min(1, c.volume); video.muted = c.volume === 0 || !!c.reverse;
    if (c.reverse) video.pause();
    if (autoplay && running && !c.reverse) video.play().catch(() => { stopPreview(); notice('Tap Play timeline again to start playback.'); });
  };
  if (video.getAttribute('src') !== src) { video.onloadedmetadata = configure; video.src = src; }
  else if (video.readyState >= 1) configure(); else video.onloadedmetadata = configure;
  $('preview-label').textContent = `${index + 1}. ${assetFor(c).name} · ${c.speed}×${c.reverse ? ' · Reverse preview (silent, approximate)' : ''}`;
}
function seekTo(time) {
  stopPreview(); playhead = M.clamp(time, 0, duration()); updatePlayhead(); loadPreview(playhead, false);
}
function advancePreview() {
  if (!running) return;
  const positions = M.positions(clips);
  if (playing + 1 < clips.length) {
    playhead = positions[playing + 1].at; loadPreview(playhead, true); syncAudio(true); updatePlayhead();
  } else { playhead = duration(); stopPreview(); updatePlayhead(); }
}
function previewTick() {
  if (!running) return;
  const video = $('preview'), c = clips[playing];
  if (c?.reverse && video.readyState >= 2) {
    const now = performance.now();
    if (!reverseClock) { reverseClock = now; reverseAt = playhead; }
    playhead = reverseAt + (now - reverseClock) / 1000;
    const end = M.positions(clips)[playing].at + M.span(c);
    if (playhead >= end) advancePreview();
    else {
      // Browsers generally cannot play negative rates. Seek backward while the timeline clock runs.
      // A backward seek may decode a whole GOP. Cap requests at 8/s instead of
      // seeking on every animation frame and competing with the export worker.
      if (!video.seeking && now - reverseSeek >= 125) {
        video.currentTime = Math.min(c.end - .00001, M.sourceTime(c, playhead - M.positions(clips)[playing].at));
        reverseSeek = now;
      }
      updatePlayhead(); syncAudio();
    }
  } else if (c && video.readyState >= 2 && !video.paused) {
    if (video.currentTime >= c.end - .005) advancePreview();
    else { playhead = M.positions(clips)[playing].at + Math.max(0, video.currentTime - c.start) / c.speed; updatePlayhead(); syncAudio(); }
  } else for (const p of audioPlayers) p?.pause();
  if (running) previewFrame = requestAnimationFrame(previewTick);
}
$('play').onclick = () => {
  stopPreview(); if (playhead >= duration() - .01) playhead = 0;
  running = true; loadPreview(playhead, true); syncAudio(true); previewFrame = requestAnimationFrame(previewTick);
};
$('stop').onclick = stopPreview;
$('preview').addEventListener('play', () => {
  if (previewReversed) { $('preview').pause(); if (!running) $('play').click(); }
});
$('preview').addEventListener('ended', () => { if (!clips[playing]?.reverse) advancePreview(); });
$('preview').addEventListener('pause', () => { for (const p of audioPlayers) p?.pause(); });
$('preview').addEventListener('error', () => { stopPreview(); $('preview-label').textContent = 'This browser cannot preview the source. You can still export it.'; });
function renderAssets() {
  $('assets').replaceChildren(); $('asset-count').textContent = assets.length;
  assets.forEach(asset => {
    const row = el('div', undefined, 'asset-row');
    row.append(el('p', `${asset.name} · ${clock(asset.duration)} · ${(asset.size / 1048576).toFixed(1)} MB`));
    const actions = el('div', undefined, 'actions');
    if (asset.kind === 'video') actions.append(button('+ Add video', () => addClip(asset)));
    else actions.append(button('+ Add audio', () => addAudio(asset)));
    actions.append(button('Delete copy', async () => {
      if (!confirm(`Delete the imported copy of “${asset.name}”? This also removes its timeline clips. Your original file stays untouched.`)) return;
      await api(`/api/editor/assets/${asset.id}`, 'DELETE');
      stopPreview(); clips = clips.filter(c => c.asset_id !== asset.id); audioClips = audioClips.filter(c => c.asset_id !== asset.id);
      selection = null; undoStack = []; redoStack = []; save(); await refresh(true);
    }, 'secondary danger'));
    row.append(actions); $('assets').append(row);
  });
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
        if (kind === 'video') addClip(asset); else addAudio(asset);
        renderAssets();
      } catch (error) { failures.push(`${file.name}: ${error.message}`); }
    }
    importing = false; $('videos').disabled = $('audio').disabled = false; $(id).value = '';
    $('upload-progress').hidden = true; $('upload-status').textContent = `${count} file${count === 1 ? '' : 's'} imported.`;
    if (failures.length) notice(failures.join(' '));
    await refresh(true).catch(error => notice(error.message)); totals();
  };
}
$('clear').onclick = () => {
  if ((clips.length || audioClips.length) && confirm('Clear the timeline? Imported files and finished exports will stay available.')) {
    change(() => { clips = []; audioClips = []; selection = null; playhead = 0; });
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
$('shape').onchange = save;
qualityOptions();
$('export').onclick = async () => {
  $('export').disabled = true;
  try {
    await api('/api/editor/exports', 'POST', {clips, shape: $('shape').value, profile: $('profile').value,
      allow_lossy: $('allow-lossy').checked,
      audio_clips: audioClips});
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
          stopPreview(); previewReversed = false; const video = $('preview'); video.onloadedmetadata = null;
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
    assets = state.assets;
    if (!Array.isArray(draft.audioClips) && draft.music && !audioClips.length) {
      const music = assets.find(a => a.id === draft.music);
      if (music && duration()) audioClips.push({asset_id: music.id, start: 0, end: music.duration, speed: 1,
        at: 0, track: 1, loop: true, length: duration(), volume: Number(draft['music-volume'] || .3)});
      save();
    }
    renderAssets(); if (!drag) renderTimeline();
  }
  renderJobs(state.jobs); totals();
}
async function poll() {
  try { await refresh(); }
  catch (_) { $('connection').textContent = 'Cannot reach Media Hub. Keep Termux running; reconnecting automatically…'; }
  setTimeout(poll, 2500);
}
refresh(true).then(poll).catch(error => { notice(error.message); setTimeout(poll, 2500); });
