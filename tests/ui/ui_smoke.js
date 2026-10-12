// UI smoke test: loads the real page in jsdom against a RUNNING server (real Stockfish).
// Only the chess.com endpoint is mocked (no internet in CI). Usage: BASE=http://127.0.0.1:8765 node ui_smoke.js
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = process.env.BASE || "http://127.0.0.1:8765";
const FIXTURE = JSON.parse(fs.readFileSync(__dirname + "/games_fixture.json", "utf8"));

let failures = 0, checks = 0;
const ok = (cond, msg) => { checks++; if (!cond) { failures++; console.log("  FAIL:", msg); } else console.log("  ok:", msg); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, what, ms = 90000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { try { if (fn()) return; } catch (_) {} await sleep(150); }
  throw new Error("timeout waiting for: " + what);
}

(async () => {
  let chesscomMode = "ok";
  const vc = new (require("jsdom").VirtualConsole)();
  vc.on("jsdomError", e => { failures++; checks++; console.log("  FAIL: uncaught page error:", String(e.detail || e.message).split("\n")[0]); });
  const dom = await JSDOM.fromURL(BASE + "/", { virtualConsole: vc, runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (u, o) => {
        if (String(u).startsWith("/api/chess-com/games")) {
          if (chesscomMode === "404") return new Response(JSON.stringify({ detail: "chess.com user not found" }), { status: 404 });
          return new Response(JSON.stringify(FIXTURE), { status: 200 });
        }
        return fetch(new URL(u, BASE), o);
      };
      w.Element.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window, d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const key = k => d.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true }));
  const unhidden = s => !$(s).hidden;

  console.log("1. Home page");
  ok(unhidden("#home") && $("#review").hidden, "home visible, review hidden");

  console.log("2. chess.com fetch -> game list");
  $("#username").value = "blackmoonbear25"; click($("#fetch-btn"));
  await until(() => $$(".gcard").length === 1, "game card");
  ok($(".gcard").textContent.includes("vs Coach-Vishy") && $(".pill").textContent === "Won", "card shows opponent and 'Won'");
  ok($(".gcard").textContent.includes("Kings Pawn Opening"), "card shows opening");

  console.log("3. Review the real game (live Stockfish)");
  click($(".review-btn"));
  await until(() => unhidden("#progress"), "progress card");
  ok(true, "progress card appears");
  await until(() => unhidden("#result"), "result");
  ok($("#progress").hidden, "progress hidden after completion");
  ok($$("#movelist .chip").length === 112, "112 moves in move list (got " + $$("#movelist .chip").length + ")");
  ok($$("#board .sq").length === 64, "64 squares");
  ok($$("#board .piece").length === 32 && $$("#board .piece.w").length === 16, "32 pieces at start (16 white)");
  ok($$("#board .sq")[0].dataset.sq === "h1", "board flipped for black (top-left is h1)");
  ok($("#ptop").textContent.includes("Coach-Vishy") && $("#pbot").textContent.includes("blackmoonbear25"), "player bars oriented");
  ok($("#pbot").textContent.includes("You"), "'You' tag on user's side");
  const accs = $$("#players .acc").map(e => parseFloat(e.textContent));
  ok(accs.length === 2 && accs.every(a => a >= 0 && a <= 100), "accuracy cards: " + accs.join(" / "));
  ok($$("#counts tr").length === 5, "5 label rows in count table");
  ok($("#graph polygon") && $$("#graph circle").length > 0, "eval graph drawn with markers");

  console.log("4. Navigate to a Bad move");
  const badChip = $$("#movelist .chip").find(c => c.getAttribute("style").includes("#ca3431"));
  ok(!!badChip, "game contains at least one Bad move");
  click(badChip);
  ok(badChip.classList.contains("sel"), "chip selected");
  ok($("#detail-expl") && $("#detail-expl").textContent.length > 20, "explanation: " + $("#detail-expl").textContent.slice(0, 70) + "...");
  ok($$("#board .badge").length === 1 && $("#board .badge").textContent.includes("?"), "label badge on destination square");
  ok($$("#board .sq.hl").length === 2, "last move highlighted");
  const piecesBefore = $$("#board .piece").length;
  click($("#b-showbest"));
  ok($$("#arrows line").length === 2, "best-move + played-move arrows drawn");
  ok($$("#board .badge").length === 0, "badge hidden in best-move view");
  click($("#b-showbest"));
  ok($$("#arrows line").length === 0 && $$("#board .piece").length === piecesBefore, "back to position");

  console.log("5. Keyboard + buttons");
  const k0 = +$("#movelist .chip.sel").dataset.k;
  key("ArrowRight"); ok(+$("#movelist .chip.sel").dataset.k === k0 + 1, "ArrowRight advances");
  key("ArrowLeft"); ok(+$("#movelist .chip.sel").dataset.k === k0, "ArrowLeft goes back");
  key("Home"); ok(!$("#movelist .chip.sel") && $$("#board .piece").length === 32, "Home -> start position");
  key("End"); ok(+$("#movelist .chip.sel").dataset.k === 112, "End -> last move");
  ok($("#evallbl").textContent === "M1" && parseFloat($("#evalfill").style.height) < 10, "eval bar nearly empty when Black mates (label " + $("#evallbl").textContent + ", white fill " + parseFloat($("#evalfill").style.height).toFixed(1) + "%)");
  click($("#b-flip")); ok($$("#board .sq")[0].dataset.sq === "a8", "Flip button works");
  const g = $("#graph");
  g.getBoundingClientRect = () => ({ left: 0, width: 400 });
  g.dispatchEvent(new w.MouseEvent("click", { bubbles: true, clientX: 200 }));
  ok(+$("#movelist .chip.sel").dataset.k === 56, "graph click at 50% jumps to ply 56 (got " + $("#movelist .chip.sel").dataset.k + ")");
  g.getBoundingClientRect = () => ({ left: 0, width: 0 });
  g.dispatchEvent(new w.MouseEvent("click", { bubbles: true, clientX: 10 }));
  ok(+$("#movelist .chip.sel").dataset.k === 56, "zero-width graph click is ignored safely");

  console.log("6. Chess.com error handling");
  click($("#back")); ok(unhidden("#home"), "back to home");
  chesscomMode = "404"; $("#username").value = "nobody"; click($("#fetch-btn"));
  await until(() => !$("#fetch-err").hidden, "error message");
  ok($("#fetch-err").textContent.includes("not found"), "404 shown to user: " + $("#fetch-err").textContent);
  $("#username").value = ""; click($("#fetch-btn"));
  ok($("#fetch-err").textContent.includes("Enter"), "empty username validated");

  console.log("7. PGN paste flow");
  $("#pgn-text").value = "[White \"<b>Mallory</b>\"]\n[Black \"bob\"]\n\n1. f3 e5 2. g4 Qh4# 0-1";
  $("#pgn-color").value = "white"; click($("#pgn-btn"));
  await until(() => unhidden("#result"), "paste result");
  ok($$("#movelist .chip").length === 4, "4 moves");
  ok($$("#board .sq")[0].dataset.sq === "a8", "white perspective (not flipped)");
  ok($("#players b") === null && $("#ptop b") === null && $("#pbot b") === null, "header HTML is escaped (no injected <b>)");
  ok($("#players").textContent.includes("<b>Mallory</b>"), "malicious name shown as plain text");
  click($$("#movelist .chip")[2]);
  ok($("#detail").textContent.includes("g4") && $("#detail").textContent.includes("Bad") && $("#detail-expl").textContent.includes("mate"), "g4 flagged Bad with mate explanation");

  console.log("8. Invalid PGN handling");
  click($("#back"));
  $("#pgn-text").value = "1. e4 e5 2. Ke4 *"; click($("#pgn-btn"));
  await until(() => !$("#pgn-err").hidden, "pgn error");
  ok(unhidden("#home") && $("#pgn-err").textContent.toLowerCase().includes("illegal"), "illegal-move PGN rejected: " + $("#pgn-err").textContent.slice(0, 60));
  $("#pgn-text").value = ""; click($("#pgn-btn"));
  ok($("#pgn-err").textContent.includes("Paste"), "empty PGN validated");

  console.log(`\n${checks - failures}/${checks} UI checks passed`);
  process.exit(failures ? 1 : 0);
})().catch(e => { console.log("ERROR:", e.message); process.exit(2); });
