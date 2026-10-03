#!/usr/bin/env python3
"""Build design/showcase/index.html from the actual Prism tokens in
platform/ui/assets/tokens.css (parsed, never retyped) + grounded design docs.

Run: python3 build_showcase.py
Output: design/showcase/index.html (self-contained; images in design/showcase/img/)
"""
import re
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent.parent  # worktree root
TOKENS_CSS = ROOT / "platform" / "ui" / "assets" / "tokens.css"
OUT = Path(__file__).resolve().parent / "index.html"

# ---------------------------------------------------------------- tokens ----
def parse_tokens(css_path: Path) -> dict:
    text = css_path.read_text()
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", text))

T = parse_tokens(TOKENS_CSS)
print(f"parsed {len(T)} tokens from {TOKENS_CSS.name}")

def tv(name: str) -> str:  # token value, e.g. tv('bg-0') -> '#06090F'
    return T[name].strip()

# sanity: every token the showcase renders must exist
REQUIRED = ["bg-0","bg-1","bg-2","bg-3","line-0","line-1","tx-0","tx-1","tx-2","tx-dim",
            "sev-1","sev-2","sev-3","sev-4","disp-page","disp-suppress","disp-escalate",
            "disp-defer","src-synthetic","src-shadow","src-production",
            "font-mono","font-ui","radius-chip","radius-card","space"]
missing = [n for n in REQUIRED if n not in T]
assert not missing, f"tokens missing from tokens.css: {missing}"

# ------------------------------------------------- token group metadata ------
# (meanings quoted from design/DESIGN_SYSTEM.md §2 — the design contract)
GROUPS = [
    ("Backgrounds — near-black, cold blue bias (3 AM, AMOLED)",
     [("bg-0","app chrome · river background"),
      ("bg-1","cards · drawers · panels"),
      ("bg-2","hover / raised surfaces"),
      ("bg-3","pressed / selected surfaces")]),
    ("Lines",
     [("line-0","hairline dividers"),
      ("line-1","emphasized borders")]),
    ("Text — no pure white anywhere",
     [("tx-0","primary text"),
      ("tx-1","secondary text"),
      ("tx-2","tertiary / metadata"),
      ("tx-dim","disabled / placeholder")]),
    ("Severity scale — chips and row gutters only, never large filled areas",
     [("sev-1","SEV1 — page-worthy now"),
      ("sev-2","SEV2 — urgent, degrades soon"),
      ("sev-3","SEV3 — needs attention today"),
      ("sev-4","SEV4 — informational")]),
    ("Disposition scale — the verdict of a decision",
     [("disp-page","PAGE — a human was woken"),
      ("disp-suppress","SUPPRESS — machine stood down (reason code required)"),
      ("disp-escalate","ESCALATE — routed up a tier"),
      ("disp-defer","DEFER — held for correlation window")]),
    ("Data-source badges — reserved, never reused for severity or disposition",
     [("src-synthetic","synthetic — generated storm data"),
      ("src-shadow","shadow — real alerts, evaluated but not enacted"),
      ("src-production","production — enacted decisions")]),
]

def swatches() -> str:
    out = []
    for title, items in GROUPS:
        cards = []
        for name, use in items:
            val = tv(name)
            cards.append(
                f'<div class="sw">'
                f'<div class="swchip" style="background:{val}"></div>'
                f'<div class="swname mono">--{name}</div>'
                f'<div class="swval mono">{val}</div>'
                f'<div class="swuse">{use}</div>'
                f'</div>')
        out.append(f'<h4>{title}</h4><div class="swgrid">{"".join(cards)}</div>')
    # confidence ramp — composed from parsed tokens, not retyped:
    lo, mid, hi = tv("tx-dim"), tv("sev-4"), tv("src-production")
    out.append(
        '<h4>Confidence ramp — cool monochrome, deliberately not red/green</h4>'
        '<div class="swgrid"><div class="sw">'
        f'<div class="swchip ramp" style="background:linear-gradient(90deg,{lo},{mid},{hi})"></div>'
        '<div class="swname mono">confidence low → high</div>'
        f'<div class="swval mono">{lo} → {mid} → {hi}</div>'
        '<div class="swuse">belief about the machine\'s confidence — not a verdict. '
        'Verdicts are dispositions; colors stay in their lanes.</div>'
        '</div></div>')
    return "".join(out)

# ------------------------------------------------------------------- page ----
HTML = Template(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel — Design Showcase · lane/sun-showcase</title>
<style>
:root {
$css_vars
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin:0; background:var(--bg-0); color:var(--tx-0);
  font-family:var(--font-ui); font-size:13px; line-height:1.55; }
.mono { font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
a { color:var(--sev-4); text-decoration:none; }
a:hover { text-decoration:underline; }
::selection { background:var(--bg-3); }
.wrap { max-width:1180px; margin:0 auto; padding:0 24px 96px; }
.topbar { display:flex; align-items:center; gap:16px; padding:16px 0;
  border-bottom:1px solid var(--line-0); margin-bottom:32px; }
.brand { font-family:var(--font-mono); font-size:13px; letter-spacing:4px; }
.brand .sep { color:var(--tx-dim); letter-spacing:0; }
.tag { font-family:var(--font-mono); font-size:10px; color:var(--tx-2);
  border:1px dashed var(--line-1); border-radius:var(--radius-chip); padding:3px 8px; }
nav.toc { display:flex; flex-wrap:wrap; gap:8px; margin:24px 0 40px; }
nav.toc a { font-family:var(--font-mono); font-size:11px; color:var(--tx-1);
  border:1px solid var(--line-0); border-radius:var(--radius-chip); padding:6px 12px;
  background:var(--bg-1); }
h1 { font-size:20px; font-weight:600; margin:0 0 8px; }
h2 { font-size:15px; font-weight:600; margin:64px 0 4px; padding-top:24px;
  border-top:1px solid var(--line-0); }
h2 .num { color:var(--tx-dim); font-family:var(--font-mono); font-weight:400; }
h3 { font-size:13px; font-weight:600; margin:28px 0 8px; }
h4 { font-size:11px; font-weight:600; color:var(--tx-1); margin:24px 0 8px;
  text-transform:uppercase; letter-spacing:1px; }
p, li { color:var(--tx-1); }
p.lead { font-size:13px; max-width:760px; }
strong { color:var(--tx-0); font-weight:600; }
ul.tight { margin:8px 0; padding-left:20px; }
ul.tight li { margin:4px 0; }
.honest { border:1px solid var(--line-1); border-left:3px solid var(--src-shadow);
  background:var(--bg-1); border-radius:var(--radius-card); padding:16px 20px; margin:24px 0; }
.honest p { margin:0; font-size:12px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:860px){ .grid2 { grid-template-columns:1fr; } }
.card { background:var(--bg-1); border:1px solid var(--line-0);
  border-radius:var(--radius-card); padding:20px 24px; margin:16px 0; }
.card img { width:100%; border-radius:var(--radius-chip); border:1px solid var(--line-0);
  display:block; }
.refbanner { font-family:var(--font-mono); font-size:10px; letter-spacing:1px;
  padding:6px 0; color:var(--tx-2); border-bottom:1px dashed var(--line-1); margin-bottom:12px; }
.verdict { font-family:var(--font-mono); font-size:11px; letter-spacing:2px; margin:0 0 8px; }
.verdict.took { color:var(--src-production); }
.verdict.rejected { color:var(--sev-2); }
.attr { font-size:10px; color:var(--tx-dim); margin-top:8px; }
.kv { display:grid; grid-template-columns:150px 1fr; gap:6px 16px; margin:12px 0; }
.kv .k { font-family:var(--font-mono); font-size:11px; color:var(--tx-2); }
.kv .v { font-size:12px; color:var(--tx-1); }
.swgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }
.sw { background:var(--bg-1); border:1px solid var(--line-0);
  border-radius:var(--radius-card); padding:12px; }
.swchip { height:40px; border-radius:var(--radius-chip); border:1px solid var(--line-1); }
.swname { font-size:11px; color:var(--tx-0); margin-top:8px; }
.swval { font-size:11px; color:var(--tx-1); }
.swuse { font-size:11px; color:var(--tx-2); margin-top:4px; }
.type-row { border:1px solid var(--line-0); background:var(--bg-1);
  border-radius:var(--radius-card); padding:12px 16px; margin:8px 0; }
.type-row .lbl { font-family:var(--font-mono); font-size:10px; color:var(--tx-dim); }
.spaceviz { display:flex; align-items:flex-end; gap:16px; margin:12px 0; }
.spacebar { background:var(--line-1); border-radius:2px; }
.screens { display:grid; grid-template-columns:1fr; gap:16px; }
.screen { background:var(--bg-1); border:1px solid var(--line-0);
  border-radius:var(--radius-card); padding:20px 24px; }
.screen-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.screen-head .fn { font-family:var(--font-mono); font-size:11px; letter-spacing:2px;
  color:var(--sev-4); }
.screen-head h3 { margin:0; font-size:14px; }
.links { margin-top:12px; display:flex; gap:16px; flex-wrap:wrap; }
.links a { font-family:var(--font-mono); font-size:11px; }
table.hon { width:100%; border-collapse:collapse; margin:16px 0; font-size:12px; }
table.hon th { text-align:left; font-family:var(--font-mono); font-size:10px;
  letter-spacing:1px; color:var(--tx-2); padding:8px 12px; border-bottom:1px solid var(--line-1); }
table.hon td { padding:10px 12px; border-bottom:1px solid var(--line-0); color:var(--tx-1);
  vertical-align:top; }
.pill { font-family:var(--font-mono); font-size:10px; padding:2px 8px;
  border-radius:var(--radius-chip); border:1px solid; white-space:nowrap; }
.pill.real { color:var(--src-production); border-color:var(--src-production); }
.pill.labeled { color:var(--src-shadow); border-color:var(--src-shadow); }
.pill.intent { color:var(--tx-1); border-color:var(--line-1); }
code { font-family:var(--font-mono); font-size:11px; color:var(--tx-0);
  background:var(--bg-2); padding:1px 6px; border-radius:3px; }
pre { background:var(--bg-1); border:1px solid var(--line-0); border-radius:var(--radius-card);
  padding:16px 20px; overflow-x:auto; font-family:var(--font-mono); font-size:11px;
  color:var(--tx-1); line-height:1.6; }
footer { margin-top:80px; padding-top:24px; border-top:1px solid var(--line-0);
  color:var(--tx-dim); font-size:11px; }
.rubric { counter-reset: r; }
.rubric li::marker { color:var(--sev-4); font-family:var(--font-mono); }
</style>
</head>
<body>
<div class="wrap">

<div class="topbar">
  <div class="brand">SENTINEL <span class="sep">/</span> SHOWCASE</div>
  <div class="tag">lane/sun-showcase</div>
  <div class="tag">REFERENCE IMAGES ≠ OUR UI</div>
  <div class="tag">tokens parsed from platform/ui/assets/tokens.css</div>
</div>

<h1>Design showcase — the Sentinel interface, on trial.</h1>
<p class="lead">Aditya asked for demo designs he can judge directly: what informed
the interface, what the token system actually is, why each screen is shaped the way
it is, and what is real versus labeled. This page is itself a design artifact —
judge it by the same bar. <strong>No fake screenshots of our UI appear here.</strong>
The interface is judged live, through the deep links in §3.</p>

<div class="honest">
<p><strong>How to read this.</strong> §1 shows <em>other people's products</em> as
inspiration — every one is bannered REFERENCE and captioned with what we took and
what we rejected. §2 renders the <em>actual</em> Prism tokens, parsed from
<code>tokens.css</code> by <code>design/showcase/build_showcase.py</code> — if the
UI lane changes a token, this page changes with it. §4 states exactly what the
live screens show and what they label. Slop is a failing test.</p>
</div>

<nav class="toc">
<a href="#inspiration">01 · Inspiration board</a>
<a href="#tokens">02 · Live token system</a>
<a href="#screens">03 · Screen rationale</a>
<a href="#honesty">04 · Honesty layer</a>
<a href="#judge">05 · How to judge</a>
</nav>

<!-- ============================================================ 01 ====== -->
<h2 id="inspiration"><span class="num">01</span> · Inspiration board</h2>
<p class="lead">Four references. Two taught us something; one is the incumbent we
are replacing; one is a non-software metaphor the design docs lean on. Each states
<em>what we took</em> and <em>what we rejected and why</em> — inspiration is a
decision record, not a mood board.</p>

<div class="card">
<div class="refbanner">REFERENCE — NOT OUR UI · Bloomberg Terminal, dual-monitor desk setup</div>
<div class="verdict took">▼ TOOK — DENSITY AS A FEATURE</div>
<img src="img/bloomberg.jpg" alt="Bloomberg Terminal dual monitors showing dense market data">
<div class="attr">Image: Wikimedia Commons. Reference, not our UI.</div>
<h3>What we took</h3>
<ul class="tight">
<li><strong>The tape.</strong> The decision river (RIVER) is Bloomberg-terminal
semantics: a continuous, queryable stream where density is the point. New decisions
prepend at the head; you pin your place and jump back to live — you never lose your
position because a storm arrived.</li>
<li><strong>Information-complete rows.</strong> A Bloomberg row is readable without
clicking: ours too — timestamp, severity, disposition + reason code, confidence with
denominator, team, fingerprint, source badge. Two hundred rows stay scannable.</li>
<li><strong>One animation budget.</strong> The river's 120ms new-row highlight is the
only motion on the screen. Everything else is instant.</li>
</ul>
<h3>What we rejected</h3>
<ul class="tight">
<li><strong>The color chaos.</strong> A Bloomberg screen screams in every hue; Prism
disciplines color into lanes — severity, disposition, and data source each own
their colors and never share. A scrolled tape of 200 rows must read as quiet
gray-blue with occasional red left-bars (pages). If the tape looks loud, the
design failed.</li>
<li><strong>Insider opacity.</strong> Bloomberg assumes a trained operator and never
explains itself. Our trust surface (CAL) carries a permanent guided layer written
for an SRE with no ML background.</li>
</ul>
<div class="kv"><div class="k">informs</div><div class="v">RIVER (tape semantics, row anatomy) · confidence-bar geometry</div></div>
</div>

<div class="card">
<div class="refbanner">REFERENCE — NOT OUR UI · Linear, dark mode inbox</div>
<div class="verdict took">▼ TOOK — CALM</div>
<img src="img/linear.webp" alt="Linear app dark interface showing inbox and issue detail">
<div class="attr">Image: Linear app UI, via yannglt.com essay on Linear's redesign. Reference, not our UI.</div>
<h3>What we took</h3>
<ul class="tight">
<li><strong>Dark-ops restraint.</strong> Near-black surfaces, hairline dividers, color
reserved for meaning — the 3 AM instrument must not assault a woken-up operator.
Our palette is near-black with a cold blue bias (no pure white anywhere), for the
same reason Linear's is.</li>
<li><strong>Keyboard-first operation.</strong> The river is fully operable without a
mouse (<code>j/k</code>, <code>/</code>, <code>Shift+G</code>, <code>?</code>) — an
on-call instrument, like Linear, assumes the power user never reaches for the
pointer.</li>
<li><strong>Copy without marketing.</strong> Short sentences, no exclamation marks,
no "AI-powered". The product speaks like the senior engineer who built it.</li>
</ul>
<h3>What we rejected</h3>
<ul class="tight">
<li><strong>The task frame.</strong> Linear's unit of work is an issue to be managed;
ours is a <em>decision with evidence</em> to be audited. Nothing here creates,
assigns, or closes work — reads before writes, always. Threshold changes export as
policy diffs for human review; the UI never changes paging behavior directly.</li>
</ul>
<div class="kv"><div class="k">informs</div><div class="v">whole shell (top bar, command palette ⌘K, drawer) · onboarding copy tone</div></div>
</div>

<div class="card">
<div class="refbanner">REFERENCE — NOT OUR UI · courtroom trial presentation</div>
<div class="verdict took">▼ TOOK — EVIDENCE-FORWARDNESS</div>
<img src="img/courtroom.webp" alt="Lawyer presenting exhibits on a large screen to a courtroom">
<div class="attr">Image: Justice Legal Video (justicelegalvideo.com) — trial presentation. Reference, not our UI.</div>
<h3>What we took</h3>
<ul class="tight">
<li><strong>Every claim arrives with its exhibit.</strong> No confidence bar renders
without its denominator (<code>n=4,096</code>). No SUPPRESS chip renders without its
reason code. If the evidence is missing, the component renders the error state —
never a naked number. (Law L1.)</li>
<li><strong>Exhibits are linkable.</strong> Input hashes (<code>sha256:7d3a…</code>)
turn "the machine changed its mind" into a falsifiable, one-click claim — the flip
hunter's query. The audit pack exports the query, the records, and the provenance
header so a postmortem can cite the machine like a witness.</li>
<li><strong>Thin evidence confesses.</strong> Bins with <code>n&lt;30</code> render
hatched; evaluation sets under 100 render the whole screen provisional. The exhibit
shows its own weakness.</li>
</ul>
<h3>What we rejected</h3>
<ul class="tight">
<li><strong>Narrative persuasion.</strong> A lawyer argues a conclusion; our flip
timeline annotates only what hashes and versions establish. Unknown cause renders
as <em>"cause undetermined — input, thresholds, and tuner identical; flagged for
platform review"</em>. Honesty over narrative, always.</li>
</ul>
<div class="kv"><div class="k">informs</div><div class="v">AUDIT (flip timelines, input hashes, audit pack) · CAL (reliability diagram) · confidence-bar geometry</div></div>
</div>

<div class="card">
<div class="refbanner">REFERENCE — NOT OUR UI · PagerDuty incident detail page (the incumbent)</div>
<div class="verdict rejected">▲ REJECTED — THE PATTERN WE ARE REPLACING</div>
<img src="img/pagerduty.png" alt="PagerDuty incident detail page with responders, status, and actions">
<div class="attr">Image: PagerDuty official product screenshot (pagerduty.com). Reference, not our UI.</div>
<h3>What we rejected and why</h3>
<ul class="tight">
<li><strong>A configuration surface for rules, not an instrument for decisions.</strong>
The incident page answers <em>who owns this</em> (responders, escalation policy,
run-a-play) — the questions of a world where every alert is assumed worth a human.
Sentinel's question is prior: <em>was this worth waking anyone at all, and can the
machine prove it?</em> PagerDuty never granted our four freedoms because its
interface is built to route incidents, not to audit machine judgment.</li>
<li><strong>Summaries without provenance.</strong> The incumbent's AI features
generate incident summaries — text that cannot be falsified. Our UX laws forbid
it: Law L1 (provenance on every automated claim), Law L3 (no accuracy marketing —
we report calibration, coverage, suppression rates; the word "accurate" does not
appear in the UI).</li>
<li><strong>Blame-adjacent chrome.</strong> Responder names next to incidents invite
attribution to people. Law L4: the interface attributes behavior to signals,
policies, and decisions — never to people. Deploy hashes are facts about the
system; blame is not a feature.</li>
</ul>
<div class="kv"><div class="k">informs</div><div class="v">everything, by negation — this is the design we must not converge to</div></div>
</div>

<!-- ============================================================ 02 ====== -->
<h2 id="tokens"><span class="num">02</span> · Live token system</h2>
<p class="lead">The Prism tokens, rendered from the <strong>actual</strong>
<code>platform/ui/assets/tokens.css</code> — parsed by the build script, never
retyped. Rebrand = change that file; this section follows. Swatch meanings are
quoted from <code>design/DESIGN_SYSTEM.md §2</code>.</p>

$swatches

<h4>Type scale — 11px metadata · 13px body · 15px section heads · 20px screen titles. No display typography anywhere: this is an instrument.</h4>
<div class="type-row"><div class="lbl">11px mono · metadata</div>
<div class="mono" style="font-size:11px; color:var(--tx-2)">02:14:07 · fpr:9f2c·a41d · (n=4,096 · bin 0.85–0.90) · ds:shadow-2026-10-02</div></div>
<div class="type-row"><div class="lbl">13px · body</div>
<div style="font-size:13px">Suppressed: disk-pressure alert on payments/checkout self-cleared 3 of the last 4 times within 5 minutes.</div></div>
<div class="type-row"><div class="lbl">15px · section heads</div>
<div style="font-size:15px; font-weight:600">Evidence — confidence bar</div></div>
<div class="type-row"><div class="lbl">20px · screen titles</div>
<div style="font-size:20px; font-weight:600">Calibration</div></div>
<div class="type-row"><div class="lbl">data font — JetBrains Mono / IBM Plex Mono, tabular numerals always</div>
<div class="mono" style="font-size:13px">[SEV2] ▮ SUPPRESS · flap-debounce &nbsp; 0.87 (n=4,096) &nbsp; payments/checkout</div></div>

<h4>Spacing, shape, motion</h4>
<div class="kv">
<div class="k">8px base grid</div><div class="v"><div class="spaceviz">
<span class="spacebar" style="width:8px;height:32px"></span><span class="mono" style="font-size:10px;color:var(--tx-2)">8</span>
<span class="spacebar" style="width:16px;height:32px"></span><span class="mono" style="font-size:10px;color:var(--tx-2)">16</span>
<span class="spacebar" style="width:24px;height:32px"></span><span class="mono" style="font-size:10px;color:var(--tx-2)">24</span>
<span class="spacebar" style="width:32px;height:32px"></span><span class="mono" style="font-size:10px;color:var(--tx-2)">32</span>
</div></div>
<div class="k">radii</div><div class="v mono">4px chips/badges · 8px cards/drawers · 0px river rows (the tape is flush)</div>
<div class="k">motion budget</div><div class="v"><strong>One animation per screen.</strong> The river's new-row highlight (120ms, white at 8% fading out). Everything else is instant. No spinners that spin — skeletons shimmer, badges render before skeletons.</div>
</div>

<!-- ============================================================ 03 ====== -->
<h2 id="screens"><span class="num">03</span> · Screen-by-screen rationale</h2>
<p class="lead">Five screens, five operator problems. For each: the 3 AM test it
must pass, the key design decisions, the anti-slop rule applied, and the deep
link to the <strong>live</strong> screen. Serve <code>platform/ui</code> and the
mock links work with no backend (<code>?mock=1</code>); drop the flag for live
mode against the API lane.</p>

<div class="screens">

<div class="screen">
<div class="screen-head"><span class="fn">RIVER</span><h3>Decision River — the live stream of decisions</h3></div>
<div class="kv">
<div class="k">the 3 AM test</div><div class="v">You wake to a page. Was the machine right to wake you — and why are the other forty alerts silent?</div>
<div class="k">key decisions</div><div class="v"><ul class="tight">
<li><strong>A tape, not a feed.</strong> New decisions prepend at the head; scrolling pins your place with a <code>▲ 12 new — jump to live</code> pill. A storm never steals your position. SSE drops render as gap-marker rows — <em>missing time is visible, never silently skipped.</em></li>
<li><strong>Rows are information-complete telegrams.</strong> Time, severity, disposition + mandatory reason code, confidence with denominator, team, fingerprint, source badge — the fastest scan cue is the disposition's 3px left bar, not a wall of color.</li>
<li><strong>The drawer is the scribe's timeline.</strong> Verdict in plain generated language (never free-form LLM prose) → evidence (full confidence bar: histogram + Q1/Q2/Q3 + marker + gate line) → decision timeline → provenance → raw JSON. Nothing the API has is hidden.</li>
</ul></div>
<div class="k">anti-slop rule</div><div class="v">Color discipline: 200 rows must read as quiet gray-blue with occasional red left-bars. <strong>If the tape looks loud, the design failed</strong> — the machine's job is to make paging rare, and the screen must look like it.</div>
</div>
<div class="links"><a href="../../platform/ui/index.html#/river?mock=1">▸ open river — mock data (no backend)</a><a href="../../platform/ui/index.html#/river">▸ open river — live API</a></div>
</div>

<div class="screen">
<div class="screen-head"><span class="fn">CAL</span><h3>Calibration — is the machine honest about what it knows?</h3></div>
<div class="kv">
<div class="k">the 3 AM test</div><div class="v">When the machine says 0.87, should I believe 0.87? Answer in ten seconds, no ML background.</div>
<div class="k">key decisions</div><div class="v"><ul class="tight">
<li><strong>Three headline cards, each a complete sentence.</strong> ECE 0.031 with its weighting and denominator; coverage at threshold as "3,858 of 4,096 decisions above gate"; flip rate with "74 of 4,096 inputs re-asked → different answer". The denominator is the card, not a footnote.</li>
<li><strong>The reliability diagram reprices like a quote.</strong> Hover a bin: predicted vs observed, n, ECE contribution, and a verdict line in operator language — <em>"overconfident — treat 0.85s as ~0.79"</em>. Thin bins (n&lt;30) render hatched, excluded from ECE, and say so.</li>
<li><strong>The gate line crosses the diagram.</strong> A dashed red line at the team's threshold shows which bins sit above the line that wakes people — and whether those bins are the overconfident ones. That single overlay is the whole trust question.</li>
</ul></div>
<div class="k">anti-slop rule</div><div class="v">Law L3: the word <strong>"accurate" does not appear in the UI</strong>. We report calibration, coverage, and flips — the three quantities we can honestly measure. The plain-language gloss is generated from the actual ECE, never canned copy.</div>
</div>
<div class="links"><a href="../../platform/ui/index.html#/calibration?team=data&mock=1">▸ open calibration — mock data</a><a href="../../platform/ui/index.html#/calibration?team=data">▸ open calibration — live API</a></div>
</div>

<div class="screen">
<div class="screen-head"><span class="fn">SIM</span><h3>Threshold Simulator — what would this policy have done last week?</h3></div>
<div class="kv">
<div class="k">the 3 AM test</div><div class="v">Before I change the gate, show me what last week's noise would have done under the new thresholds — in numbers I'd defend in a postmortem.</div>
<div class="k">key decisions</div><div class="v"><ul class="tight">
<li><strong>The slider track is the team's actual confidence histogram.</strong> You drag <em>across the distribution</em>, seeing exactly which mass of decisions each notch captures or releases. Repricing is live on every input event.</li>
<li><strong>The false-suppress watch is the conscience of the screen.</strong> Shadow-labeled cases the new thresholds would have wrongly suppressed: if that number moves, the card turns SEV1-red and export locks until each case is opened in audit. The screen refuses to let you ship a dangerous tune.</li>
<li><strong>"Same math as the tuner" is shown, not claimed.</strong> A pinned provenance block names the dataset, the tuner binary, the nightly shadow join — with a <code>[copy payload]</code> button so any engineer can reproduce the projection from a terminal.</li>
</ul></div>
<div class="k">anti-slop rule</div><div class="v">Reads before writes (Law L5): the simulator <strong>never writes policy</strong>. Projections are labeled projections and copy says "would have", never "will". Commit exports a YAML policy diff + a PR branch suggestion — applying it is a human's code-review decision, in git.</div>
</div>
<div class="links"><a href="../../platform/ui/index.html#/simulator?team=data&mock=1">▸ open simulator — mock data</a><a href="../../platform/ui/index.html#/simulator?team=data">▸ open simulator — live API</a></div>
</div>

<div class="screen">
<div class="screen-head"><span class="fn">AUDIT</span><h3>Audit Explorer — search the machine's memory, investigate its mistakes</h3></div>
<div class="kv">
<div class="k">the 3 AM test</div><div class="v">This alert paged us — what did the gate think? And has it changed its mind about the same input before?</div>
<div class="k">key decisions</div><div class="v"><ul class="tight">
<li><strong>One search bar, four query types.</strong> Fingerprint (postmortem entry), input hash (the flip hunter's query: one input, multiple decisions, different answers), free text, time bounds — with facets for team, disposition, reason, source, flipped-only, labeled-only.</li>
<li><strong>The flip timeline annotates only what hashes establish.</strong> Same input + same thresholds + same tuner but a different answer = measured non-determinism (our band: 1.3–2.2%). A policy change = link to the policy diff. Anything else renders <em>"cause undetermined — flagged for platform review"</em>.</li>
<li><strong>No second design.</strong> Audit reuses the river's row component and the same detail drawer. One visual language for "a decision", everywhere.</li>
</ul></div>
<div class="k">anti-slop rule</div><div class="v">Blameless by construction (Law L4): the interface attributes behavior to <strong>signals, policies, and decisions — never to people</strong>. No "caused by" attribution, no deployer names next to suppressions. Deploy hashes are facts; blame is not a feature.</div>
</div>
<div class="links"><a href="../../platform/ui/index.html#/audit?mock=1">▸ open audit — mock data</a><a href="../../platform/ui/index.html#/audit">▸ open audit — live API</a></div>
</div>

<div class="screen">
<div class="screen-head"><span class="fn">START</span><h3>Onboarding — from zero to first suppression in 15 minutes</h3></div>
<div class="kv">
<div class="k">the 3 AM test</div><div class="v">In fifteen minutes, can a skeptic watch the machine suppress a page, understand why — and see it be <em>wrong</em> on purpose?</div>
<div class="k">key decisions</div><div class="v"><ul class="tight">
<li><strong>Deliberate wrongness, day one.</strong> The guided storm scripts one flip and surfaces it: <em>"This one changed its mind — 1.8% of inputs do. Here's the flip timeline."</em> Trust is built by showing the failure mode first, not by hiding it.</li>
<li><strong>The storm-honesty card.</strong> Before the storm runs: <em>"Suppression counts here prove the pipeline works, not that the model is good — calibration is measured on shadow data, in the CAL screen."</em> The demo never markets. (Laws L2, L3.)</li>
<li><strong>Skip paths everywhere; nothing auto-applies.</strong> A senior SRE reaches the river in 90 seconds. The flow ends with thresholds <em>unchanged</em> — tuning happens in SIM, with evidence, when ready.</li>
</ul></div>
<div class="k">anti-slop rule</div><div class="v">Copy tone: short sentences, no exclamation marks, no "supercharge" / "AI-powered" / "seamless". Error copy is plain: <em>"Key verification failed (HTTP 401 from TypeSafe). Check the key — nothing was stored."</em></div>
</div>
<div class="links"><a href="../../platform/ui/index.html#/start?mock=1">▸ open onboarding — mock data</a><a href="../../platform/ui/index.html#/start">▸ open onboarding — live API</a></div>
</div>

</div>

<!-- ============================================================ 04 ====== -->
<h2 id="honesty"><span class="num">04</span> · Honesty layer</h2>
<p class="lead">The showcase must not oversell the UI either. What is real, what is
labeled, and what is deliberately not shown.</p>

<table class="hon">
<tr><th style="width:90px">status</th><th style="width:260px">claim</th><th>the truth</th></tr>
<tr><td><span class="pill real">REAL</span></td><td>All five screens render</td><td>They run as a static build in <code>platform/ui/</code> — no build step, hash routes, every state a deep link. Serve the directory and click through.</td></tr>
<tr><td><span class="pill real">REAL</span></td><td>Token system</td><td><code>tokens.css</code> is verbatim the design contract; §2 of this page is generated from it. Rebrand = change one file.</td></tr>
<tr><td><span class="pill real">REAL</span></td><td>Contract-shaped data</td><td>Mock payloads in <code>platform/ui/data/</code> match the frozen API contract (<code>openapi.yaml</code> is the type authority; conformance tests in <code>tests/conformance.py</code>).</td></tr>
<tr><td><span class="pill labeled">LABELED</span></td><td>Mock-data mode</td><td>Behind a visible <code>◈ MOCK DATA</code> banner — never silent. Toggle via <code>?mock=1</code> or the command palette.</td></tr>
<tr><td><span class="pill labeled">LABELED</span></td><td>Mock simulate</td><td>In mock mode the simulator runs a local recompute, labeled in the provenance block: <em>"mock-mode local recompute — NOT tuner math"</em>. Live mode uses the real tuner math.</td></tr>
<tr><td><span class="pill labeled">LABELED</span></td><td>Synthetic storm</td><td>The onboarding storm is scripted synthetic data and says so before it runs; the two SEV1s are scripted to page. It proves the pipeline, not the model.</td></tr>
<tr><td><span class="pill labeled">LABELED</span></td><td>Derived receipts</td><td>Confidence bars, histograms, and calibration diagrams in mock mode are drawn from the mock evaluation window — denominators shown, never hidden.</td></tr>
<tr><td><span class="pill intent">NOT SHOWN</span></td><td>Screenshots of our UI</td><td>Deliberate. The interface is judged live through the §3 links, not in pictures. Any picture of "our UI" on a slide would be a mock of a mock.</td></tr>
<tr><td><span class="pill intent">NOT SHOWN</span></td><td>Production data</td><td>The UI lane has no production feed yet. Every screen carries its source badge (<code>synthetic</code> / <code>shadow</code> / <code>production</code>) — the badge renders before any skeleton, per Law L2.</td></tr>
</table>

<!-- ============================================================ 05 ====== -->
<h2 id="judge"><span class="num">05</span> · How to judge</h2>
<div class="grid2">
<div class="card">
<h3>Open it</h3>
<pre>cd ~/workspace/jev-builds/sentinel-sun-showcase
python3 -m http.server 8123            # serve the worktree
# showcase:  http://localhost:8123/design/showcase/
# live UI:   http://localhost:8123/platform/ui/#/river?mock=1</pre>
<p>All five screen links in §3 are relative, so they resolve from the same
server. <code>?mock=1</code> runs the full UI with zero backend; drop the flag
when the API lane is up.</p>
</div>
<div class="card">
<h3>The rubric — three tests</h3>
<ol class="rubric tight">
<li><strong>The 3 AM test.</strong> Pick a screen in §3. Read its one-line test.
Does the screen answer it in under a minute, on a phone, half-awake? That is the
entire design brief.</li>
<li><strong>The slop test.</strong> Does any screen look like a template — gradient
cards, widget soup, "AI insights" panels with no provenance? Slop is a failing
test; name the screen and the smell.</li>
<li><strong>The provenance test.</strong> Find any number on any screen. Does it
carry its denominator, its source badge, and its dataset version? A naked number
is a rendering bug (Law L1).</li>
</ol>
</div>
</div>

<footer>
<div class="mono">SENTINEL · design showcase · lane/sun-showcase · built from design/DESIGN_SYSTEM.md + design/screens/ + platform/ui/assets/tokens.css (parsed) · images: genuine product references, bannered REF, not our UI · no fake screenshots of our UI were made or shown.</div>
</footer>

</div>
</body>
</html>
""")

def main():
    css_vars = "\n".join(f"  --{k}: {v.strip()};" for k, v in T.items())
    page = HTML.substitute(css_vars=css_vars, swatches=swatches())
    OUT.write_text(page)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
