// Playbook: a research run drawn as plays on a chalkboard.
//
// The Lead is the coach. When it sends a researcher, that researcher's ball is thrown to a new
// player; every tool call is a pass to a cone (the tool) and back; a researcher's findings pass
// through the referee (evidence.verify) on the way back, which throws a flag for each rejected one.
// The Writer gets the verified findings and throws the report down to the clipboard.
// Each player keeps its own box score (calls, errors, repeats refused, findings kept / flagged),
// so you can see which agent to fix without opening the run log.
//
// Usage: Playbook.mount(container); Playbook.reset(); Playbook.handle(event) for each SSE event.
// Reads the 'activity' and 'play' events the server sends (research.py ResearchHooks).

const Playbook = (() => {
    const NS = "http://www.w3.org/2000/svg";
    const W = 1000;
    const H = 668;
    const REF = { x: 420, y: 214 }; // the referee: every returning researcher passes here
    const ROSTER = {
        lead: { x: 500, y: 92, ball: "whistle", name: "Lead", role: "coach" },
        data: { x: 180, y: 318, ball: "basketball", name: "Data", role: "EADA SQL + curated figures", tools: ["get_enum_values", "find_columns", "run_sql"] },
        policy: { x: 500, y: 318, ball: "volleyball", name: "Policy", role: "legal documents", tools: ["search_policy", "read_policy"] },
        news: { x: 820, y: 318, ball: "soccer", name: "News", role: "web search", tools: ["search_web", "fetch_page"] },
        writer: { x: 500, y: 568, ball: "softball", name: "Writer", role: "cited report" },
    };
    // Lead -> Writer runs down the gap between Policy and News, right of the referee
    const WRITER_ROUTE = (lead) => `M${lead.x + 30} ${lead.y + 30} C 665 260, 665 480, ${ROSTER.writer.x + 30} ${ROSTER.writer.y - 20}`;
    const SLOT_GAP = 150; // a follow-up round's researcher ("Data 2") stands beside the first
    // Cone labels: short, so three cones fit under one player
    const TOOL_LABEL = { get_enum_values: "valid values", find_columns: "find columns", run_sql: "run SQL",
        search_policy: "search", read_policy: "read passage", search_web: "web search", fetch_page: "fetch page" };
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    let svg, layers, players, cones, pending, research, stats;

    function el(name, attrs = {}, parent) {
        const node = document.createElementNS(NS, name);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        if (parent) parent.appendChild(node);
        return node;
    }

    function kindOf(agent) {
        if (!agent) return null;
        if (agent === "Lead") return "lead";
        if (agent === "Writer") return "writer";
        const m = agent.match(/^(Data|Policy|News) Researcher/);
        return m ? m[1].toLowerCase() : null;
    }

    // --- Drawing the board ---

    function defs() {
        return `
        <defs>
          <filter id="chalk" x="-5%" y="-5%" width="110%" height="110%">
            <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2" seed="3" result="noise"/>
            <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.4"/>
          </filter>
          <radialGradient id="halo"><stop offset="0" stop-color="#009cde" stop-opacity="0.5"/><stop offset="1" stop-color="#009cde" stop-opacity="0"/></radialGradient>
          <symbol id="ball-basketball" viewBox="-12 -12 24 24">
            <circle r="10.5" fill="#e8742c" stroke="#3b2416" stroke-width="1.2"/>
            <path d="M-10.5 0H10.5M0-10.5V10.5M-7.4-7.4Q-2 0-7.4 7.4M7.4-7.4Q2 0 7.4 7.4" fill="none" stroke="#3b2416" stroke-width="1.1"/>
          </symbol>
          <symbol id="ball-soccer" viewBox="-12 -12 24 24">
            <circle r="10.5" fill="#fbfbf8" stroke="#222" stroke-width="1.2"/>
            <polygon points="0,-4 3.8,-1.2 2.4,3.2 -2.4,3.2 -3.8,-1.2" fill="#222"/>
            <path d="M0-4V-10.4M3.8-1.2L9.8-3.4M2.4 3.2L6 8.4M-2.4 3.2L-6 8.4M-3.8-1.2L-9.8-3.4" stroke="#222" stroke-width="1"/>
          </symbol>
          <symbol id="ball-volleyball" viewBox="-12 -12 24 24">
            <circle r="10.5" fill="#fbfbf8" stroke="#2b2b2b" stroke-width="1.2"/>
            <path d="M0 0Q-1-8 4-10M0 0Q8 2 9.6 7M0 0Q-7 5-10 3" fill="none" stroke="#2a78d6" stroke-width="1.6"/>
            <path d="M-4-9.6Q-5-2 0 0M10.4-1Q4 3 0 0M-3 10Q0 4 0 0" fill="none" stroke="#eda100" stroke-width="1.6"/>
          </symbol>
          <symbol id="ball-softball" viewBox="-12 -12 24 24">
            <circle r="10.5" fill="#e4ee4a" stroke="#7c8312" stroke-width="1.2"/>
            <path d="M-6-8.6Q-1.5 0-6 8.6M6-8.6Q1.5 0 6 8.6" fill="none" stroke="#d33a2c" stroke-width="1.4" stroke-dasharray="1.6 1.6"/>
          </symbol>
          <symbol id="ball-whistle" viewBox="-12 -12 24 24">
            <path d="M-9-3h11a6 6 0 1 1-6 6v-1h-5z" fill="#c9ccd1" stroke="#45484d" stroke-width="1.2"/>
            <circle cx="2" cy="3" r="2.2" fill="#45484d"/>
            <path d="M-9-3v-3h4v3" fill="#c9ccd1" stroke="#45484d" stroke-width="1.2"/>
          </symbol>
        </defs>`;
    }

    function mount(container) {
        svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "pb-svg", role: "img", "aria-label": "Play diagram of the research agents" });
        svg.innerHTML = defs();
        container.appendChild(svg);
        reset();
    }

    function reset() {
        if (!svg) return;
        for (const g of svg.querySelectorAll("g.pb-layer")) g.remove();
        layers = {};
        for (const name of ["routes", "trails", "cones", "players", "balls", "notes"]) {
            layers[name] = el("g", { class: "pb-layer pb-" + name }, svg);
        }
        players = {};
        cones = {};
        pending = {}; // call_id -> {ball, out: Promise}
        research = []; // Lead's research calls: {callId, kind, node}
        stats = { verified: 0, rejected: 0 };
        drawFormation();
    }

    // The formation before kickoff: who could play, the routes between them, and the tools
    function drawFormation() {
        const lead = ROSTER.lead;
        for (const kind of ["data", "policy", "news"]) {
            const k = ROSTER[kind];
            route(arc(lead, k, 0.12, kind === "data" ? 1 : -1), "pb-route");
            const n = k.tools.length;
            for (const [i, tool] of k.tools.entries()) drawCone(kind, tool, k.x + (i - (n - 1) / 2) * 100, k.y + 150);
            ghost(kind);
        }
        route(WRITER_ROUTE(lead), "pb-route");
        route(`M${ROSTER.writer.x} ${ROSTER.writer.y + 40} L ${ROSTER.writer.x} ${H - 4}`, "pb-route");
        const out = el("text", { x: ROSTER.writer.x + 16, y: H - 6, class: "pb-small pb-left" }, layers.routes);
        out.textContent = "to the clipboard ↓";
        drawReferee();
        addPlayer("Lead", "lead");
        addPlayer("Writer", "writer");
    }

    function route(d, cls) {
        return el("path", { d, class: cls, filter: "url(#chalk)" }, layers.routes);
    }

    function ghost(kind) {
        const k = ROSTER[kind];
        const g = el("g", { class: "pb-ghost", transform: `translate(${k.x} ${k.y})` }, layers.players);
        el("circle", { r: 32, filter: "url(#chalk)" }, g);
        const t = el("text", { y: 6, class: "pb-ghost-text" }, g);
        t.textContent = k.name;
        const role = el("text", { y: 56, class: "pb-small" }, g);
        role.textContent = k.role;
        k.ghost = g;
    }

    function drawCone(kind, tool, x, y) {
        const g = el("g", { class: "pb-cone", transform: `translate(${x} ${y})` }, layers.cones);
        el("path", { d: "M0-15L11 9H-11Z", filter: "url(#chalk)" }, g);
        el("path", { d: "M-15 9H15", class: "pb-cone-base", filter: "url(#chalk)" }, g);
        const label = el("text", { y: 34, class: "pb-small" }, g);
        label.textContent = TOOL_LABEL[tool] || tool;
        el("title", {}, g).textContent = tool;
        const mark = el("text", { x: 18, y: -6, class: "pb-mark" }, g);
        cones[tool] = { g, x, y, mark, kind };
    }

    function drawReferee() {
        const g = el("g", { class: "pb-ref", transform: `translate(${REF.x} ${REF.y})` }, layers.players);
        el("circle", { r: 15, class: "pb-ref-shirt" }, g);
        for (const dx of [-8, -2.7, 2.7, 8]) el("line", { x1: dx, x2: dx, y1: -13, y2: 13, class: "pb-ref-stripe" }, g);
        const t = el("text", { x: 24, y: 5, class: "pb-small pb-left" }, g);
        t.textContent = "evidence check";
        REF.text = t;
    }

    // --- Players ---

    function addPlayer(name, kind) {
        const k = ROSTER[kind];
        const g = el("g", { class: `pb-player pb-${kind}`, style: `transform: translate(${k.x}px, ${k.y}px)` }, layers.players);
        el("circle", { r: 58, fill: "url(#halo)", class: "pb-halo" }, g);
        el("circle", { r: 32, class: "pb-ring", filter: "url(#chalk)" }, g);
        const use = el("use", { href: `#ball-${k.ball}`, x: -14, y: -14, width: 28, height: 28 }, g);
        use.classList.add("pb-badge");
        const label = el("text", { y: 60, class: "pb-name" }, g);
        label.textContent = kind === "lead" || kind === "writer" ? `${k.name} · ${k.role}` : name.replace(" Researcher", "");
        // Box score: what it did on one line, what it produced on the next; the words are in a tooltip
        const work = el("text", { y: 86, class: "pb-small pb-box" }, g);
        const result = el("text", { y: 110, class: "pb-small pb-box" }, g);
        const tip = el("title", {}, g);
        const p = { name, kind, g, x: k.x, y: k.y, label, work, result, tip, calls: 0, errors: 0, repeats: 0, ok: null, flagged: null };
        players[name] = p;
        if (k.ghost) k.ghost.style.display = "none";
        if (kind !== "lead" && kind !== "writer") layoutKind(kind);
        else if (kind === "writer") g.classList.add("pb-bench");
        return p;
    }

    // Two researchers of one kind stand side by side
    function layoutKind(kind) {
        const squad = Object.values(players).filter((p) => p.kind === kind && !p.gone);
        squad.forEach((p, i) => {
            p.x = ROSTER[kind].x + (i - (squad.length - 1) / 2) * SLOT_GAP;
            p.g.style.transform = `translate(${p.x}px, ${p.y}px)`;
        });
    }

    function boxScore(p) {
        const work = [];
        if (p.calls) work.push(`${p.calls} call${p.calls > 1 ? "s" : ""}`);
        if (p.errors) work.push(`${p.errors}✗`);
        if (p.repeats) work.push(`${p.repeats}↺`);
        p.work.textContent = work.join("  ");
        p.result.textContent = p.ok === null ? "" : `${p.ok}✓  ${p.flagged}⚑`;
        const words = [`${p.calls} tool calls`, `${p.errors} errors`, `${p.repeats} refused repeats`];
        if (p.ok !== null) words.push(`${p.ok} findings verified`, `${p.flagged} rejected by the evidence check`);
        p.tip.textContent = `${p.name}: ${words.join(", ")}`;
    }

    function setActive(name, on = true) {
        const p = players[name];
        if (!p) return;
        p.g.classList.toggle("pb-active", on);
        if (on) p.g.classList.remove("pb-bench", "pb-waiting");
    }

    function player(name) {
        if (players[name]) return players[name];
        const kind = kindOf(name);
        if (!kind) return null;
        // A researcher the Lead sent: take over the placeholder made when the call started
        const slot = research.find((r) => r.kind === kind && !r.bound);
        if (slot) {
            slot.bound = true;
            delete players[slot.node.name];
            slot.node.name = name;
            slot.node.label.textContent = name.replace(" Researcher", "");
            players[name] = slot.node;
            return slot.node;
        }
        return addPlayer(name, kind);
    }

    // --- Passes ---

    // A throw: a quadratic arc from a to b, bent sideways by `bend` of its length
    function arc(a, b, bend = 0.18, side = 1) {
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const cx = mx - (dy / len) * len * bend * side;
        const cy = my + (dx / len) * len * bend * side - len * 0.08;
        return `M${a.x} ${a.y} Q${cx} ${cy} ${b.x} ${b.y}`;
    }

    function trail(key, d) {
        const existing = layers.trails.querySelector(`[data-key="${CSS.escape(key)}"]`);
        if (existing) {
            existing.classList.remove("pb-fresh");
            void existing.getBBox(); // restart the highlight
            existing.classList.add("pb-fresh");
            return;
        }
        const p = el("path", { d, class: "pb-trail pb-fresh", "data-key": key, filter: "url(#chalk)" }, layers.trails);
        return p;
    }

    function makeBall(kind, at) {
        const ball = el("use", { href: `#ball-${ROSTER[kind].ball === "whistle" ? "softball" : ROSTER[kind].ball}`, width: 26, height: 26, class: "pb-ball" }, layers.balls);
        placeBall(ball, at.x, at.y);
        return ball;
    }

    function placeBall(ball, x, y, scale = 1, spin = 0) {
        ball.setAttribute("transform", `translate(${x} ${y}) rotate(${spin}) scale(${scale}) translate(-13 -13)`);
        ball.dataset.x = x;
        ball.dataset.y = y;
    }

    // Fly a ball along a path; resolves when it lands
    function fly(ball, d, ms = 650) {
        const path = el("path", { d, fill: "none", stroke: "none" }, layers.balls);
        const total = path.getTotalLength();
        return new Promise((resolve) => {
            if (reduceMotion) {
                const end = path.getPointAtLength(total);
                placeBall(ball, end.x, end.y);
                path.remove();
                return resolve();
            }
            const t0 = performance.now();
            const step = (now) => {
                const t = Math.min(1, (now - t0) / ms);
                const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
                const pt = path.getPointAtLength(e * total);
                placeBall(ball, pt.x, pt.y, 1 + Math.sin(Math.PI * e) * 0.45, e * 540);
                if (t < 1) requestAnimationFrame(step);
                else {
                    path.remove();
                    resolve();
                }
            };
            requestAnimationFrame(step);
        });
    }

    function pos(ball) {
        return { x: +ball.dataset.x, y: +ball.dataset.y };
    }

    function note(text, at, cls = "") {
        const t = el("text", { x: at.x, y: at.y, class: "pb-note " + cls }, layers.notes);
        t.textContent = text;
        setTimeout(() => t.remove(), 2600);
    }

    function flag(at, count) {
        const g = el("g", { class: "pb-flag", transform: `translate(${at.x + 18} ${at.y - 6})` }, layers.notes);
        el("path", { d: "M0 0V-26M0-26L16-21L0-15", class: "pb-flag-cloth" }, g);
        const t = el("text", { x: 20, y: -10, class: "pb-note pb-left" }, g);
        t.textContent = `${count} flagged`;
        setTimeout(() => g.remove(), 3200);
    }

    // --- Events ---

    function handle(e) {
        if (!svg) return;
        if (e.type === "activity") onActivity(e);
        else if (e.type === "play") e.phase === "start" ? onStart(e) : onEnd(e);
        else if (e.type === "report" || e.type === "clarify" || e.type === "error") {
            for (const p of Object.values(players)) p.g.classList.remove("pb-active");
            if (e.type === "clarify") note("needs a clarifying answer", { x: ROSTER.lead.x, y: ROSTER.lead.y - 46 }, "pb-warn");
        }
    }

    function onActivity(e) {
        const p = player(e.agent);
        if (!p) return;
        if (e.text === "started") {
            setActive(p.name);
            if (p.kind === "writer") {
                const ball = makeBall("writer", players.Lead);
                const d = WRITER_ROUTE(players.Lead);
                trail("lead-writer", d);
                fly(ball, d, 1000).then(() => ball.remove());
                note(`${stats.verified} verified findings`, { x: 665, y: 410 });
            }
        } else if (e.text.startsWith("finished")) {
            setActive(p.name, false);
            p.g.classList.add("pb-done");
            if (p.kind === "writer") {
                const ball = makeBall("writer", p);
                fly(ball, `M${p.x} ${p.y} L${p.x} ${H + 20}`, 700).then(() => ball.remove());
            }
        }
    }

    function onStart(e) {
        const p = player(e.agent);
        if (!p) return;
        setActive(p.name);
        if (e.tool === "research") {
            const kind = (e.detail || "").split(":")[0].trim();
            if (!ROSTER[kind] || !ROSTER[kind].tools) return;
            const squad = Object.values(players).filter((q) => q.kind === kind).length;
            const node = addPlayer(`${ROSTER[kind].name} ${squad + 1}?`, kind);
            node.g.classList.add("pb-waiting");
            research.push({ callId: e.call_id, kind, node, bound: false });
            const ball = makeBall(kind, p);
            const d = arc(p, node, 0.14, node.x < p.x ? 1 : -1);
            fly(ball, d, 800).then(() => ball.remove());
            return;
        }
        const cone = cones[e.tool];
        if (!cone) return;
        p.calls += 1;
        boxScore(p);
        const ball = makeBall(p.kind, p);
        const d = arc(p, cone, 0.2, cone.x < p.x ? 1 : -1);
        trail(`${p.name}-${e.tool}`, d);
        cone.g.classList.add("pb-busy");
        pending[e.call_id] = { ball, cone, out: fly(ball, d, 450) };
    }

    async function onEnd(e) {
        const p = players[e.agent] || player(e.agent);
        if (e.tool === "research") return onResearchEnd(e);
        const call = pending[e.call_id];
        if (!p || !call) return;
        delete pending[e.call_id];
        if (e.outcome === "error") p.errors += 1;
        if (e.outcome === "refused") p.repeats += 1;
        boxScore(p);
        await call.out;
        const { cone, ball } = call;
        cone.mark.textContent = e.outcome === "ok" ? "✓" : e.outcome === "error" ? "✗" : "↺";
        cone.mark.setAttribute("class", "pb-mark pb-" + e.outcome);
        if (e.outcome !== "ok") note(e.outcome === "error" ? "fumble" : "refused", { x: cone.x, y: cone.y - 26 }, "pb-" + e.outcome);
        if (!Object.values(pending).some((c) => c.cone === cone)) cone.g.classList.remove("pb-busy");
        await fly(ball, arc(cone, p, 0.2, cone.x < p.x ? -1 : 1), 400);
        ball.remove();
    }

    async function onResearchEnd(e) {
        const slot = research.find((r) => r.callId === e.call_id);
        if (!slot) return;
        const node = slot.node;
        const lead = players.Lead;
        if (e.outcome !== "ok") {
            // The Lead asked for a researcher it isn't allowed: never took the field
            node.gone = true;
            note("not allowed", { x: node.x, y: node.y - 44 }, "pb-refused");
            node.g.classList.add("pb-cut");
            setTimeout(() => {
                node.g.remove();
                layoutKind(slot.kind);
                if (!Object.values(players).some((q) => q.kind === slot.kind && !q.gone)) ROSTER[slot.kind].ghost.style.display = "";
            }, 1400);
            return;
        }
        const m = (e.note || "").match(/(\d+) verified, (\d+) rejected/);
        const ok = m ? +m[1] : 0;
        const flagged = m ? +m[2] : 0;
        node.ok = ok;
        node.flagged = flagged;
        boxScore(node);
        setActive(node.name, false);
        node.g.classList.add("pb-done");
        stats.verified += ok;
        stats.rejected += flagged;
        const ball = makeBall(slot.kind, node);
        const toRef = arc(node, REF, 0.1, node.x < REF.x ? -1 : 1);
        trail(`${node.name}-ref`, toRef);
        await fly(ball, toRef, 600);
        if (flagged) flag(REF, flagged);
        REF.text.textContent = `evidence check: ${stats.verified} ✓  ${stats.rejected} ⚑`;
        await fly(ball, `M${REF.x} ${REF.y} L${lead.x} ${lead.y + 30}`, 350);
        ball.remove(); // the running totals are the referee's line, not the Lead's: it would sit on top of the referee
    }

    return { mount, reset, handle, kindOf, ROSTER };
})();
