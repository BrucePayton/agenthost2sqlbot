const assert = require("node:assert/strict");
const test = require("node:test");

const ServiceHealth = require("../../app/web/static/service-health.js");

test("degraded execution survives healthy transport events", () => {
  assert.equal(ServiceHealth.deriveState("ready", "degraded"), "degraded");
  assert.equal(ServiceHealth.deriveState("reconnecting", "degraded"), "reconnecting");
  assert.equal(ServiceHealth.canExecute("degraded"), false);
  assert.equal(ServiceHealth.canExecute("ready"), true);
});

test("disconnected transport takes precedence over execution state", () => {
  assert.equal(ServiceHealth.deriveState("disconnected", "ready"), "disconnected");
  assert.equal(ServiceHealth.deriveState("disconnected", "degraded"), "disconnected");
});
