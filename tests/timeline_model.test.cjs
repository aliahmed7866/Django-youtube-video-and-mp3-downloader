const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../mediahub/static/timeline-model.js');
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
test('video edge drags respect source bounds and speed; later clips ripple', () => {
  const c = {start: 2, end: 8, speed: 2};
  const trimmed = M.trimVideo(c, 'start', 1, 10);
  assert.equal(trimmed.start, 4); assert.equal(M.positions([trimmed, c])[1].at, 2);
  assert.equal(M.trimVideo(trimmed, 'start', -9, 10).start, 0);
  assert.equal(M.trimVideo(c, 'end', 9, 10).end, 10);
  near(M.trimVideo(c, 'end', -9, 10).end, 2.05);
});
test('audio left trim keeps its right edge fixed and restores only available source', () => {
  const c = {start: 2, end: 8, speed: .5, at: 3};
  const trimmed = M.trimAudio(c, 'start', 2, 10);
  assert.equal(trimmed.start, 3); assert.equal(trimmed.at, 5);
  assert.equal(trimmed.at + M.audioSpan(trimmed), c.at + M.audioSpan(c));
  const extended = M.trimAudio(c, 'start', -99, 10);
  assert.equal(extended.at, 0); assert.equal(extended.start, .5);
  assert.equal(M.trimAudio(c, 'end', 99, 10).end, 10);
});
test('loop edge trims change occupied length without changing repeating source', () => {
  const c = {start: 2, end: 4, speed: 1, at: 5, loop: true, length: 8};
  const trimmed = M.trimAudio(c, 'start', 3, 12);
  assert.equal(trimmed.at, 8); assert.equal(trimmed.length, 5);
  assert.equal(trimmed.start, 2); assert.equal(trimmed.end, 4);
  assert.equal(M.trimAudio(c, 'end', 2, 12).length, 10);
});
test('dragging videos reorders without mutating source clips', () => {
  const a = {id: 'a', start: 0, end: 2, speed: 1}, b = {...a, id: 'b'}, c = {...a, id: 'c'};
  const result = M.reorder([a, b, c], 0, 5.5);
  assert.deepEqual(result.clips.map(c => c.id), ['b', 'c', 'a']); assert.equal(result.index, 2);
  assert.deepEqual(M.reorder(result.clips, 2, 0).clips.map(c => c.id), ['a', 'b', 'c']);
});
test('split translates playhead through speed and preserves total length and audio position', () => {
  const c = {start: 2, end: 8, speed: 2, at: 5};
  const [left, right] = M.split(c, 1, true);
  assert.equal(left.end, 4); assert.equal(right.start, 4); assert.equal(right.at, 6);
  assert.equal(M.span(left) + M.span(right), M.span(c));
  assert.throws(() => M.split(c, 0)); assert.throws(() => M.split({...c, loop: true}, 1));
});
test('reverse video edges address the opposite source boundary', () => {
  const c = {start: 2, end: 8, speed: 2, reverse: true};
  assert.equal(M.trimVideo(c, 'start', 1, 10).end, 6);
  assert.equal(M.trimVideo(c, 'end', 1, 10).start, 0);
  assert.equal(M.trimVideo(c, 'start', -99, 10).end, 10);
  near(M.trimVideo(c, 'end', -99, 10).start, 7.95);
});
test('reverse audio left trim keeps right edge fixed and restores source end', () => {
  const c = {start: 2, end: 8, speed: .5, at: 3, reverse: true};
  const trimmed = M.trimAudio(c, 'start', 2, 10);
  assert.equal(trimmed.end, 7); assert.equal(trimmed.at, 5);
  assert.equal(trimmed.at + M.span(trimmed), c.at + M.span(c));
  const restored = M.trimAudio(c, 'start', -99, 10);
  assert.equal(restored.at, 0); assert.equal(restored.end, 9.5);
  assert.equal(M.trimAudio(c, 'end', 99, 10).start, 0);
});
test('reverse split and source-time mapping retain backward source order', () => {
  const c = {start: 2, end: 8, speed: 2, reverse: true, at: 5};
  const [left, right] = M.split(c, 1, true);
  assert.equal(left.start, 6); assert.equal(left.end, 8);
  assert.equal(right.start, 2); assert.equal(right.end, 6); assert.equal(right.at, 6);
  assert.equal(M.span(left) + M.span(right), M.span(c));
  assert.equal(M.sourceTime(c, 0), 8); assert.equal(M.sourceTime(c, 1), 6);
  assert.equal(M.sourceTime({...c, reverse: false}, 1), 4);
});
