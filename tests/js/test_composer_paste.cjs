"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {extractImageFiles, createController} = require(
  "../../app/web/static/composer-paste.js"
);

class FakeInput {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }
}

function item(file) {
  return {kind: "file", type: file.type, getAsFile: () => file};
}

function pasteEvent(items) {
  return {
    clipboardData: {items},
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  };
}

test("text-only clipboard data keeps native paste behavior", async () => {
  const input = new FakeInput();
  const uploaded = [];
  const controller = createController({
    input,
    canUpload: () => true,
    getRemainingSlots: () => 5,
    uploadFiles: async (files) => uploaded.push(files),
    onError: assert.fail,
  });
  const event = pasteEvent([{kind: "string", type: "text/plain"}]);

  assert.equal(await controller.handlePaste(event), false);
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual(uploaded, []);
});

test("mixed clipboard content consumes ordered images only", async () => {
  const input = new FakeInput();
  const uploaded = [];
  const png = new File(["png"], "image.png", {type: "image/png"});
  const jpeg = new File(["jpeg"], "photo-original.jpg", {type: "image/jpeg"});
  const controller = createController({
    input,
    canUpload: () => true,
    getRemainingSlots: () => 5,
    uploadFiles: async (files) => uploaded.push(files),
    onError: assert.fail,
    now: () => new Date(2026, 6, 14, 1, 2, 3),
  });
  const event = pasteEvent([
    {kind: "string", type: "text/html"},
    item(png),
    {kind: "string", type: "text/plain"},
    item(jpeg),
  ]);

  assert.equal(await controller.handlePaste(event), true);
  assert.equal(event.defaultPrevented, true);
  assert.equal(uploaded.length, 1);
  assert.equal(uploaded[0][0].name, "clipboard-20260714-010203-1.png");
  assert.equal(uploaded[0][1].name, "photo-original.jpg");
});

test("controller rejects image paste when unavailable or over limit", async () => {
  const image = new File(["png"], "image.png", {type: "image/png"});
  const errors = [];
  const input = new FakeInput();
  let available = false;
  let remaining = 0;
  const controller = createController({
    input,
    canUpload: () => available,
    getRemainingSlots: () => remaining,
    uploadFiles: async () => assert.fail("upload must not run"),
    onError: (message) => errors.push(message),
  });

  const unavailable = pasteEvent([item(image)]);
  assert.equal(await controller.handlePaste(unavailable), true);
  assert.equal(unavailable.defaultPrevented, true);
  assert.deepEqual(errors, ["当前不能上传图片。"]);

  available = true;
  remaining = 1;
  const overLimit = pasteEvent([item(image), item(image)]);
  assert.equal(await controller.handlePaste(overLimit), true);
  assert.equal(overLimit.defaultPrevented, true);
  assert.deepEqual(errors, ["当前不能上传图片。", "最多还能添加 1 个附件。"]);

  controller.destroy();
  assert.equal(input.listeners.has("paste"), false);
});

test("extractImageFiles ignores null and non-image file items", () => {
  const text = new File(["text"], "notes.txt", {type: "text/plain"});
  assert.deepEqual(
    extractImageFiles({items: [item(text), {kind: "file", type: "image/png", getAsFile: () => null}]}),
    [],
  );
});
