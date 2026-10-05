"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const test = require("node:test");

const moduleUrl = pathToFileURL(
  path.join(process.cwd(), "web/embed/skill-management.js"),
).href;

function workspace(id, kind, overrides = {}) {
  return {
    id,
    name: id,
    kind,
    available: true,
    can_manage_skills: true,
    can_manage_global_skills: false,
    skill_count: 0,
    ...overrides,
  };
}

test("iframe keeps the active Agent Workspace separate from personal Skill management", async () => {
  const {selectEmbedWorkspaces} = await import(moduleUrl);
  const team = workspace("davinci-team", "team");
  const personal = workspace("personal", "personal", {
    can_manage_global_skills: true,
  });

  assert.deepEqual(selectEmbedWorkspaces([team, personal]), {
    agentWorkspace: team,
    skillWorkspace: personal,
  });
});

test("iframe hides Skill management when no manageable personal Workspace exists", async () => {
  const {selectEmbedWorkspaces} = await import(moduleUrl);
  const team = workspace("davinci-team", "team");

  assert.deepEqual(selectEmbedWorkspaces([team]), {
    agentWorkspace: team,
    skillWorkspace: null,
  });
});

test("iframe request headers preserve identity and leave multipart boundaries to fetch", async () => {
  const {requestHeaders} = await import(moduleUrl);
  const identity = {Authorization: "Bearer opaque-session"};

  const multipart = requestHeaders(identity, {body: new FormData()});
  assert.equal(multipart.get("Authorization"), "Bearer opaque-session");
  assert.equal(multipart.has("Content-Type"), false);

  const json = requestHeaders(identity, {body: JSON.stringify({enabled: true})});
  assert.equal(json.get("Content-Type"), "application/json");
});
