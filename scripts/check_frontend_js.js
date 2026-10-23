/* Dependency guard for frontend/app.js.
   After wireActionButtons shipped called-but-undefined, this asserts that the
   app's own view functions only call helpers that actually exist in the file.
   Explicit table > fragile source parsing. */

const fs = require("fs");
const src = fs.readFileSync("frontend/app.js", "utf8");

const DEPS = {
  renderSuggestions: ["banner", "suggestionCard", "rejectedCard", "wireActionButtons"],
  suggestionCard: ["esc", "fmtINR", "confidenceColor"],
  rejectedCard: ["esc"],
  loadTrack: ["lineChart", "scatterChart", "tableInto", "fmtINR"],
  loadPulse: ["banner", "esc", "fmtINR"],
  loadMF: ["banner", "renderMF"],
  loadModelHistory: ["esc", "fmtINR"],
  renderMF: ["banner", "esc", "fmtINR"],
  lineChart: ["svgEl", "ticks"],
  scatterChart: ["svgEl"],
};

let failures = 0;
for (const [fn, deps] of Object.entries(DEPS)) {
  // the function itself must be defined
  const fnRe = new RegExp(`function\\s+${fn}\\s*\\(`);
  if (!fnRe.test(src)) {
    console.error(`MISSING FUNCTION: ${fn}()`);
    failures++;
    continue;
  }
  // each dependency must be defined somewhere in the file
  for (const dep of deps) {
    const depRe = new RegExp(`function\\s+${dep}\\s*\\(|(?:const|let)\\s+${dep}\\s*=`);
    if (!depRe.test(src)) {
      console.error(`${fn}() calls ${dep}() which is NOT DEFINED`);
      failures++;
    }
  }
}

if (failures) {
  console.error(`${failures} frontend dependency failure(s)`);
  process.exit(1);
}
console.log("frontend dependency guard: all view-function helpers defined");
