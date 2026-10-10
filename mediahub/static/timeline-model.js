/* Pure timeline math shared by touch controls, keyboard controls and Node tests. */
(function (root) {
  'use strict';
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const span = c => Math.max(0, (c.end - c.start) / c.speed || 0);
  const audioSpan = c => c.loop ? Math.max(0, Number(c.length) || 0) : span(c);
  function positions(clips) {
    let at = 0;
    return clips.map(c => { const p = {at, length: span(c)}; at += p.length; return p; });
  }
  function trimVideo(c, edge, delta, duration) {
    const next = {...c};
    if (edge === 'start') next.start = clamp(c.start + delta * c.speed, 0, c.end - .05);
    else next.end = clamp(c.end + delta * c.speed, c.start + .05, duration);
    return next;
  }
  function trimAudio(c, edge, delta, duration) {
    if (c.loop) {
      if (edge === 'start') {
        const d = clamp(delta, -c.at, c.length - .05);
        return {...c, at: c.at + d, length: c.length - d};
      }
      return {...c, length: clamp(c.length + delta, .05, 7200 - c.at)};
    }
    if (edge === 'start') {
      const d = clamp(delta, Math.max(-c.at, -c.start / c.speed), (c.end - c.start - .05) / c.speed);
      return {...c, at: c.at + d, start: c.start + d * c.speed};
    }
    return {...c, end: clamp(c.end + delta * c.speed, c.start + .05,
                             Math.min(duration, c.start + (7200 - c.at) * c.speed))};
  }
  function reorder(clips, index, targetTime) {
    const positionsBefore = positions(clips);
    const target = positionsBefore.filter((p, i) => i !== index && p.at + p.length / 2 < targetTime).length;
    const next = [...clips], [clip] = next.splice(index, 1); next.splice(target, 0, clip);
    return {clips: next, index: target};
  }
  function split(c, elapsed, audio = false) {
    if (c.loop) throw new Error('Turn off looping before splitting an audio clip.');
    const cut = c.start + elapsed * c.speed;
    if (cut - c.start < .05 || c.end - cut < .05) throw new Error('Place the playhead inside the clip, away from either edge.');
    return [{...c, end: cut}, {...c, start: cut, ...(audio ? {at: c.at + elapsed} : {})}];
  }
  const api = {clamp, span, audioSpan, positions, trimVideo, trimAudio, reorder, split};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TimelineModel = api;
})(typeof window === 'undefined' ? globalThis : window);
