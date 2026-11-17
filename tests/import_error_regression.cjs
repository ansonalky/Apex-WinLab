/* importErrorText from the real dashboard.js: specific, sanitized URL-import errors. No server or network. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "dashboard.js"), "utf8");
const start = source.indexOf("function importErrorText(");
assert.ok(start >= 0, "importErrorText is defined");
const end = source.indexOf("\n}\n", start) + 2;
const importErrorText = new Function(`${source.slice(start, end)}; return importErrorText;`)();

// Server text wins, as plain text: control characters removed, whitespace collapsed, length capped.
assert.equal(importErrorText({ message: "Game 1 in session 23108 has no replay telemetry." }, 400),
  "Game 1 in session 23108 has no replay telemetry.");
assert.equal(importErrorText({ error: "Disabled\u0000 in\n\tfixture   preview" }, 403), "Disabled in fixture preview");
const long = importErrorText({ message: "x".repeat(500) }, 400);
assert.equal(long.length, 240);
assert.ok(long.endsWith("…"));

// Without server text, the HTTP status names the refusing stage, never the bare generic line.
assert.match(importErrorText(null, 401), /sign in/);
assert.match(importErrorText({}, 403), /turned off/);
assert.match(importErrorText({}, 405), /does not offer/);
assert.match(importErrorText(null, 429), /Too many requests/);
assert.match(importErrorText(null, 502), /HTTP 502\). Nothing was saved/);
assert.match(importErrorText(null, 418), /HTTP 418/);
assert.equal(importErrorText({ message: "   " }, null), "This URL could not be opened.");

console.log("import_error_regression: ok");
