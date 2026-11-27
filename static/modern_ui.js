/* MODERN UI LOGIC — Gemini CLI Edition */
// --- Chat HTML sanitization -------------------------------------------------------------
// Model replies can carry text from web searches (prompt injection), so rendered Markdown is
// sanitized before it reaches the DOM. Fails closed: without DOMPurify + marked the reply is
// shown as plain text. Blocked on top of DOMPurify's defaults:
//   * inline style   -> no fake overlays / spoofed Apply buttons (payload blocks use CSS classes)
//   * images & media -> no remote fetches that could leak data through a URL
//   * forms/controls -> no injected buttons or inputs
const CHAT_SANITIZE_CONFIG = {
    FORBID_TAGS: [
        'style', 'img', 'picture', 'source', 'video', 'audio', 'svg', 'math',
        'form', 'input', 'button', 'textarea', 'select', 'option',
        'iframe', 'frame', 'object', 'embed', 'link', 'meta', 'base',
    ],
    FORBID_ATTR: ['style', 'srcset', 'formaction', 'action', 'onerror', 'onload'],
    ALLOW_UNKNOWN_PROTOCOLS: false,
};

let chatSanitizerHooked = false;

function sanitizeChatHtml(html) {
    if (!chatSanitizerHooked) {
        // External links open in a new tab without handing this page a window.opener reference.
        DOMPurify.addHook('afterSanitizeAttributes', (node) => {
            if (node.tagName === 'A' && node.hasAttribute('href')) {
                node.setAttribute('target', '_blank');
                node.setAttribute('rel', 'noopener noreferrer nofollow');
            }
        });
        chatSanitizerHooked = true;
    }
    return DOMPurify.sanitize(html, CHAT_SANITIZE_CONFIG);
}

function renderSanitizedMarkdown(el, text) {
    if (typeof DOMPurify === 'undefined' || typeof DOMPurify.sanitize !== 'function' || typeof marked === 'undefined') {
        el.textContent = text;
        return;
    }
    el.innerHTML = sanitizeChatHtml(marked.parse(String(text ?? '')));
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

let currentChatSize = 0.95;
function _applyChatFontSize(sizeRem) {
    currentChatSize = Math.min(1.6, Math.max(0.6, Number(sizeRem) || 0.95));
    let styleEl = document.getElementById('dynamic-chat-style');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'dynamic-chat-style';
        document.head.appendChild(styleEl);
    }
    styleEl.textContent = `.modern-message { font-size: ${currentChatSize.toFixed(2)}rem !important; }`;
}

function adjustChatSize(dir) {
    _applyChatFontSize(currentChatSize + dir * 0.1);
    try {
        localStorage.setItem('gem_chat_font_size', String(currentChatSize.toFixed(2)));
    } catch (_) {}
}
window.adjustChatSize = adjustChatSize;

const ModernChat = {
    overlay: null,
    launcher: null,
    closeBtn: null,
    input: null,
    messages: null,
    sendBtn: null,
    
    BOOT_PROMPT: 'SYSTEM BOOT: Execute the full Stage 0 Council Boot Sequence. Using the live DATA_PACKET just injected: (1) Baseline sync — ground all portfolio prices. (2) Cash reconciliation. (3) Regime classification. (4) Portfolio health audit. (5) Market posture assessment. Output structure: open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations BEFORE detailed analysis. For every TRIM/SELL/BUY/STOP recommendation, keep it to 1 direct line stating exact trigger/limit price, exact share count and % of position to trade (plus remaining shares and est. USD value), computed from portfolio_snapshot.',

    // Quick-prompt definitions (6 core actions — log/execute handled automatically)
    QUICK_PROMPTS: [
        {
            icon: '📰',
            label: 'News Scan',
            id: 'qp-news-scan',
            tooltip: 'Search for macroeconomic, political, and stock-specific news.',
            prompt: 'SYSTEM DIRECTIVE: MACRO & STOCK NEWS SCAN. Perform a search for macroeconomic/political events and stock-specific news. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations.'
        },
        {
            icon: '📊',
            label: 'Market Analysis',
            id: 'qp-analysis',
            tooltip: 'Evaluate risk regime, dealer posture, and macro signals to determine top priorities.',
            prompt: 'SYSTEM DIRECTIVE: ROUTINE TURN EXECUTION. Using the live DATA_PACKET just injected, evaluate risk regime, dealer posture shifts, VIX/VIXY/IEF signals, and score movements. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations (top-3 priority actions) BEFORE detailed analysis. For every TRIM/SELL/BUY/STOP recommendation, keep it to 1 direct line stating exact trigger/limit price, exact share count and % of position to trade (plus remaining shares and est. USD value), computed from portfolio_snapshot.'
        },
        {
            icon: '🔍',
            label: 'Audit Portfolio',
            id: 'qp-audit-portfolio',
            tooltip: 'Deep audit of all active positions against trading rules, limits, and cash allocation.',
            prompt: 'SYSTEM DIRECTIVE: AUDIT & PORTFOLIO REVIEW. Using the live DATA_PACKET just injected, perform a full portfolio audit: (1) Check all positions against gem_trading_rules, concentration limits, and cash allocation. (2) Conduct a deep portfolio analysis and provide Trim/Hold/Add recommendations for each held ticker with supporting evidence. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations BEFORE detailed audit commentary. For every TRIM/SELL/BUY/STOP recommendation, keep it to 1 direct line stating exact trigger/limit price, exact share count and % of position to trade (plus remaining shares and est. USD value), computed from portfolio_snapshot.'
        },
        {
            icon: '⚖️',
            label: 'Risk Regime',
            id: 'qp-risk',
            tooltip: 'Assess macro risk indicators to declare the current regime and risk posture.',
            prompt: 'SYSTEM DIRECTIVE: RISK REGIME ASSESSMENT. Using the live DATA_PACKET just injected, run a full macro risk assessment based on VIX, VIXY, IEF, and SPY. Declare the current regime and risk posture with entry/exit implications. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations BEFORE supporting commentary. For every TRIM/SELL/BUY/STOP recommendation, keep it to 1 direct line stating exact trigger/limit price, exact share count and % of position to trade (plus remaining shares and est. USD value), computed from portfolio_snapshot.'
        },
        {
            icon: '🎯',
            label: 'Deep Dive Watchlist',
            id: 'qp-scout',
            tooltip: 'Deep dive research on all tickers in your Watchlist — entry signals, risk/reward, and regime alignment.',
            watchlistOnly: true,  // hidden when watchlist is empty
            prompt: 'SYSTEM DIRECTIVE: DEEP DIVE WATCHLIST RESEARCH. Using the live DATA_PACKET just injected, perform a deep-dive research analysis on ALL tickers currently in the Watchlist section. For each ticker: (1) Assess technical setup, RSI, ATR%, GEX posture and trend. (2) Score risk/reward vs. current regime. (3) Provide a clear entry/avoid recommendation with supporting evidence. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations BEFORE per-ticker breakdown. For every TRIM/SELL/BUY/STOP recommendation, keep it to 1 direct line stating exact trigger/limit price, exact share count and % of position to trade (plus remaining shares and est. USD value), computed from portfolio_snapshot.'
        },
        {
            icon: '📝',
            label: 'Review Log',
            id: 'qp-review-log',
            tooltip: 'Forensically audit past decisions to generate corrective trade lessons and actionable triggers.',
            // Served by /api/review (see fireQuickPrompt); the directive lives in prompts/review_log_prompt.txt.
            prompt: 'Review Log: post-trade review of logged Council decisions (MANDATE_26).'
        },
    ],

    init() {
        this.overlay = document.getElementById('chat-window');
        this.launcher = document.getElementById('launch-chat-btn');
        this.closeBtn = document.getElementById('chat-close-btn');
        this.input = document.getElementById('chat-input');
        this.messages = document.getElementById('chat-messages');
        this.sendBtn = document.getElementById('send-chat-btn');
        this.stopBtn = document.getElementById('stop-chat-btn');
        this.newChatBtn = document.getElementById('new-chat-btn');
        this.historyBtn = document.getElementById('history-chat-btn');
        this.updateAnalysisBtn = document.getElementById('update-analysis-btn');
        this.currentLogElement = null;
        this.currentThinkingId = null;
        this.activeTurnId = null;
        this._turnSeq = 0;
        this.activeAbortController = null;
        this.includeChartsNext = false;
        this.isAborted = false;

        if (this.launcher) this.launcher.onclick = () => this.toggle();
        if (this.closeBtn) this.closeBtn.onclick = () => this.hide();
        if (this.sendBtn) this.sendBtn.onclick = () => this.sendMessage();
        if (this.stopBtn) this.stopBtn.onclick = () => this.stopMessage();
        if (this.newChatBtn) this.newChatBtn.onclick = () => this.startNewChat();
        if (this.historyBtn) this.historyBtn.onclick = () => this.showHistory();
        if (this.updateAnalysisBtn) this.updateAnalysisBtn.onclick = () => this.runUpdateAnalysis();

        try {
            const savedFontSize = parseFloat(localStorage.getItem('gem_chat_font_size'));
            if (Number.isFinite(savedFontSize)) _applyChatFontSize(savedFontSize);
        } catch (_) {}

        this.intradayCtxBtn = document.getElementById('toggle-intraday-ctx-btn');
        if (this.intradayCtxBtn) {
            this.intradayCtxBtn.onclick = () => {
                this.includeChartsNext = !this.includeChartsNext;
                this._syncIntradayCtxBtn();
            };
            this._syncIntradayCtxBtn();
        }

        if (!window._gemChatEscapeBound) {
            document.addEventListener('keydown', (e) => {
                if (e.key !== 'Escape') return;
                const pop = document.getElementById('settings-popover');
                if (pop && pop.style.display && pop.style.display !== 'none') {
                    pop.style.display = 'none';
                    return;
                }
                if (this.overlay && this.overlay.classList.contains('active')) {
                    this.hide();
                }
            });
            window._gemChatEscapeBound = true;
        }

        // --- Settings gear popover ---
        const gearBtn = document.getElementById('settings-gear-btn');
        const popover = document.getElementById('settings-popover');
        if (gearBtn && popover) {
            gearBtn.onclick = (e) => {
                e.stopPropagation();
                const isOpen = popover.style.display !== 'none';
                popover.style.display = isOpen ? 'none' : 'block';
                this.syncSettingsPopover();
            };
            document.addEventListener('click', (e) => {
                if (!gearBtn.contains(e.target) && !popover.contains(e.target)) {
                    popover.style.display = 'none';
                }
            });
        }

        this.modelSelector = document.getElementById('model-selector');
        if (this.modelSelector) {
            this.modelSelector.onchange = () => this.changeModel();
        }
        this.paidTiersToggle = document.getElementById('paid-tiers-toggle');
        this.geminiSubscriptionToggle = document.getElementById('gemini-subscription-toggle');
        this.cachingToggle = document.getElementById('context-caching-toggle');
        this.cachingContainer = document.getElementById('context-caching-toggle-container');
        if (this.cachingToggle) {
            this.cachingToggle.checked = false;
            this.cachingToggle.disabled = true;
            this.cachingToggle.onchange = () => this.toggleCaching();
        }
        // Default subscription ON
        if (this.geminiSubscriptionToggle) {
            this.geminiSubscriptionToggle.checked = true;
        }
        if (this.paidTiersToggle) {
            this.paidTiersToggle.checked = true; // plan users get all models
        }

        if (this.input) {
            this.input.onkeydown = (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            };
        }

        // Inject quick-prompt chip bar above the textarea
        this.renderQuickPromptBar();

        // System log SSE stream is opened lazily when chat opens or sends a message
        this.eventSource = null;

        this.loadHistory();
        this.fetchModels();
        // Always hide chat on page load — only shown when user clicks the launcher
        this.hide();
        console.log('Gemini CLI Chat UI Initialized');
    },

    _ensureLogStream() {
        if (typeof window.EventSource !== 'function') return;
        if (this.eventSource && this.eventSource.readyState !== 2) return;
        try {
            this.eventSource = new EventSource('/api/system_logs');
            this.eventSource.onmessage = (event) => {
                if (this.currentLogElement && event.data) {
                    if (event.data.includes('Attempting') || event.data.includes('Orchestrator')) {
                        this.currentLogElement.textContent = event.data;
                    } else if (event.data.includes('Tool') || event.data.includes('Agent') || event.data.includes('Engine')) {
                        this.currentLogElement.textContent = 'Firing Engine: ' + event.data;
                    } else if (event.data.includes('DATA_PACKET')) {
                        this.currentLogElement.textContent = '📡 ' + event.data;
                    } else {
                        this.currentLogElement.textContent = event.data;
                    }
                }
            };
            this.eventSource.onerror = () => {
                this._closeLogStream();
            };
        } catch (_) {
            this.eventSource = null;
        }
    },

    _closeLogStream() {
        if (this.eventSource) {
            try { this.eventSource.close(); } catch (_) {}
            this.eventSource = null;
        }
    },

    // Update watchlist-only quick prompt visibility based on watchlist + active scout tickers
    async updateWatchlistPromptVisibility() {
        try {
            // Fetch watchlist, live data, and ticker config in parallel
            const [watchlistRes, dataRes, tickersRes] = await Promise.all([
                fetch('/api/watchlist'),
                fetch('/api/data'),
                fetch('/api/tickers')
            ]);
            const watchlist = await watchlistRes.json();
            const state = await dataRes.json();
            const tickerConfig = await tickersRes.json();

            const watchlistTickers = Array.isArray(watchlist) ? watchlist.map(t => t.toUpperCase()) : [];

            // Get macro tickers from the /api/tickers config (not from /api/data which doesn't have 'macro')
            const macroTickers = (tickerConfig.macro || []).map(t => t.toUpperCase());

            // Extract portfolio tickers from live state across all SSOT schema keys
            const ssot = state.local_storage_state || {};
            const ms = ssot.mutable_state || {};
            const ep = ssot.EXECUTION_PAYLOAD || {};
            const portfolio = (ms.portfolio_snapshot || ssot.portfolio_snapshot || ep.portfolio_snapshot || []).map(p => (p.ticker || '').toUpperCase()).filter(Boolean);

            // Any ticker in live data that is not portfolio, not macro, not watchlist = scout intelligence
            const allTickers = (state.tickers || []).map(t => t.ticker.toUpperCase());
            const scoutTickers = allTickers.filter(t =>
                !portfolio.includes(t) &&
                !macroTickers.includes(t) &&
                !watchlistTickers.includes(t)
            );

            // Combine: manual watchlist + scout intelligence tickers
            const combined = [...new Set([...watchlistTickers, ...scoutTickers])];

            const hasAny = combined.length > 0;
            const scoutBtn = document.getElementById('qp-scout');
            if (scoutBtn) {
                scoutBtn.style.display = hasAny ? '' : 'none';
                // Stash resolved tickers on the button so fireQuickPrompt can read them
                scoutBtn._resolvedTickers = combined;
            }
        } catch (e) {
            // On error, default to showing the button
            const scoutBtn = document.getElementById('qp-scout');
            if (scoutBtn) scoutBtn.style.display = '';
        }
    },

    renderQuickPromptBar() {
        const inputArea = document.querySelector('.modern-input-area');
        if (!inputArea) return;

        const bar = document.createElement('div');
        bar.id = 'quick-prompt-bar';
        bar.style.cssText = `
            display: flex;
            flex-wrap: nowrap;
            overflow-x: auto;
            gap: 16px;
            padding: 10px 16px 12px;
            border-bottom: 1px solid rgba(255,255,255,0.07);
            margin-bottom: 10px;
            scrollbar-width: none;
            -webkit-overflow-scrolling: touch;
        `;

        this.QUICK_PROMPTS.forEach(qp => {
            const btn = document.createElement('button');
            btn.id = qp.id;
            if (qp.tooltip) btn.title = qp.tooltip;
            btn.innerHTML = `${qp.icon} <span style="white-space:nowrap">${qp.label}</span>`;
            btn.style.cssText = `
                flex: 1 0 auto;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                padding: 6px 16px;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.18);
                background: rgba(255,255,255,0.07);
                color: rgba(255,255,255,0.85);
                font-size: 0.72rem;
                font-weight: 500;
                letter-spacing: 0.4px;
                cursor: pointer;
                transition: background 0.15s, border-color 0.15s, transform 0.1s;
                font-family: inherit;
            `;
            btn.onmouseenter = () => {
                btn.style.background = 'rgba(139,92,246,0.25)';
                btn.style.borderColor = 'rgba(139,92,246,0.6)';
            };
            btn.onmouseleave = () => {
                if (!btn.classList.contains('active-prompt')) {
                    btn.style.background = 'rgba(255,255,255,0.07)';
                    btn.style.borderColor = 'rgba(255,255,255,0.18)';
                }
            };
            btn.onclick = () => this.fireQuickPrompt(qp);
            bar.appendChild(btn);
        });

        // Insert the bar INSIDE the input area, before the textarea
        inputArea.insertBefore(bar, inputArea.firstChild);
    },

    async fireQuickPrompt(qp) {
        if (qp.id === 'qp-news-scan') {
            try {
                const res = await fetch('/api/prompts/news_scan');
                const data = await res.json();
                qp.prompt = data.prompt || qp.prompt;
            } catch (e) {
                console.error("Failed to fetch news scan prompt", e);
            }
        }
        // If this is the Deep Dive Watchlist prompt, build the full ticker list dynamically
        if (qp.id === 'qp-scout') {
            try {
                // Prefer pre-resolved tickers cached on the button element
                const scoutBtn = document.getElementById('qp-scout');
                let combined = scoutBtn && scoutBtn._resolvedTickers ? scoutBtn._resolvedTickers : null;

                if (!combined) {
                    // Fallback: fetch live if not yet cached
                    const [watchlistRes, dataRes, tickersRes] = await Promise.all([
                        fetch('/api/watchlist'),
                        fetch('/api/data'),
                        fetch('/api/tickers')
                    ]);
                    const watchlist = await watchlistRes.json();
                    const state = await dataRes.json();
                    const tickerConfig = await tickersRes.json();
                    const watchlistTickers = Array.isArray(watchlist) ? watchlist.map(t => t.toUpperCase()) : [];
                    const macroTickers = (tickerConfig.macro || []).map(t => t.toUpperCase());
                    const ssot = state.local_storage_state || {};
                    const ms = ssot.mutable_state || {};
                    const ep = ssot.EXECUTION_PAYLOAD || {};
                    const portfolio = (ms.portfolio_snapshot || ssot.portfolio_snapshot || ep.portfolio_snapshot || []).map(p => (p.ticker || '').toUpperCase()).filter(Boolean);
                    const allTickers = (state.tickers || []).map(t => t.ticker.toUpperCase());
                    const scoutTickers = allTickers.filter(t =>
                        !portfolio.includes(t) &&
                        !macroTickers.includes(t) &&
                        !watchlistTickers.includes(t)
                    );
                    combined = [...new Set([...watchlistTickers, ...scoutTickers])];
                }

                if (combined.length === 0) {
                    alert('Validation Error: Please add tickers to your Watchlist or run a sector scout before using Deep Dive Watchlist.');
                    return;
                }

                // Inject the resolved tickers into the prompt
                qp = { ...qp, prompt: `SYSTEM DIRECTIVE: DEEP DIVE WATCHLIST RESEARCH. Strategic Watchlist tickers: [${combined.join(', ')}]. Using the live DATA_PACKET just injected, perform a deep-dive research analysis on ALL of these tickers (includes both manual watchlist and scout intelligence candidates). For each ticker: (1) Assess technical setup, RSI, ATR%, GEX posture and trend. (2) Score risk/reward vs. current regime. (3) Provide a clear entry/avoid recommendation with supporting evidence. Output a structured, human-readable summary.` };
            } catch (e) {
                console.error('Deep Dive Watchlist prompt build failed', e);
            }
        }
        // Store the friendly display label so the chat bubble shows icon+name, not the raw system prompt
        this._pendingDisplayLabel = `${qp.icon} ${qp.label}`;
        this.input.value = qp.prompt;

        // Review Log bypasses the orchestrator (whose mandatory Final Council Decision template turned it
        // into a routine HOLD wrap): /api/review runs the read-only Review Engine over the server-side
        // backtest. The review itself is not a trading decision, so it is not auto-saved to the decision log.
        if (qp.id === 'qp-review-log') {
            await this.sendMessage(false, { endpoint: '/api/review', skipDecisionLog: true });
            return;
        }
        await this.sendMessage();
    },

    showModelWarning(warningText) {
        let warningEl = document.getElementById('chat-model-warning-banner');
        if (warningText) {
            if (!warningEl) {
                warningEl = document.createElement('div');
                warningEl.id = 'chat-model-warning-banner';
                warningEl.style.cssText = `
                    background: rgba(255, 171, 0, 0.15);
                    border: 1px solid rgba(255, 171, 0, 0.3);
                    border-radius: 12px;
                    padding: 10px 16px;
                    margin-bottom: 16px;
                    color: #ffab00;
                    font-size: 0.85rem;
                    font-weight: 500;
                    backdrop-filter: blur(10px);
                    display: flex;
                    align-items: center;
                    gap: 10px;
                `;
                // Insert it at the very top of the chat-messages container
                this.messages.insertBefore(warningEl, this.messages.firstChild);
            }
            warningEl.replaceChildren();
            const spanEl = document.createElement('span');
            spanEl.textContent = String(warningText);
            warningEl.append('⚠️ ', spanEl);
            warningEl.style.display = 'flex';
        } else {
            if (warningEl) {
                warningEl.style.display = 'none';
            }
        }
    },

    async fetchModels() {
        if (!this.modelSelector) return;
        try {
            const res = await fetch('/api/list_models');
            const data = await res.json();
            if (data.status === 'success') {
                this.showModelWarning(data.warning);
                const currentModel = this.modelSelector.value;
                const activeModel = data.current_model || currentModel || 'gemini-2.5-flash';
                this.modelSelector.innerHTML = '';
                
                const includePaidToggle = this.paidTiersToggle ? this.paidTiersToggle.checked : false;
                const subLinked = this.geminiSubscriptionToggle ? this.geminiSubscriptionToggle.checked : false;
                const includePaid = includePaidToggle || subLinked;
                
                // Split models into stable and experimental
                const stableModels = [];
                const experimentalModels = [];
                data.models.forEach(m => {
                    const nameLower = m.name.toLowerCase();
                    const isLocalAntigravity = m.provider === 'antigravity';
                    const isPaid = !isLocalAntigravity && (nameLower.includes('pro') || (nameLower.includes('preview') && !nameLower.includes('flash') && !nameLower.includes('thinking')));
                    if (!includePaid && isPaid) return;
                    if (nameLower.includes('-exp') || nameLower.includes('experimental') || nameLower.includes('thinking-exp')) {
                        experimentalModels.push(m);
                    } else {
                        stableModels.push(m);
                    }
                });

                const makeOpt = (m) => {
                    const opt = document.createElement('option');
                    opt.value = m.name;
                    opt.textContent = m.label;
                    opt.style.background = '#1a1a1a';
                    opt.style.color = 'white';
                    if (m.name === activeModel) opt.selected = true;
                    return opt;
                };

                // Stable models group
                if (stableModels.length > 0) {
                    const grp = document.createElement('optgroup');
                    grp.label = '✅ Stable Models';
                    stableModels.forEach(m => grp.appendChild(makeOpt(m)));
                    this.modelSelector.appendChild(grp);
                }

                // Experimental models group
                if (experimentalModels.length > 0) {
                    const grp = document.createElement('optgroup');
                    grp.label = '⚠️ Experimental — Use with caution';
                    experimentalModels.forEach(m => grp.appendChild(makeOpt(m)));
                    this.modelSelector.appendChild(grp);
                }

                // Force model selector to match the active model from the backend
                if (activeModel) {
                    this.modelSelector.value = activeModel;
                }

                if (!this.modelSelector.value && this.modelSelector.options.length > 0) {
                    this.modelSelector.selectedIndex = 0;
                    this.changeModel();
                } else {
                    this.updateCachingToggleVisibility();
                }
            }
        } catch (e) {
            console.error('Failed to fetch models', e);
        }
    },

    async changeModel() {
        const model = this.modelSelector.value;
        const statusIndicator = document.getElementById('chat-status-indicator');
        statusIndicator.textContent = `Status: Re-Calibrating Council (${model})...`;
        statusIndicator.style.color = 'var(--accent-blue)';
        try {
            const includePaidToggle = this.paidTiersToggle ? this.paidTiersToggle.checked : false;
            const subLinked = this.geminiSubscriptionToggle ? this.geminiSubscriptionToggle.checked : false;
            const includePaid = includePaidToggle || subLinked;
            const res = await fetch('/api/set_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    model, 
                    include_paid: includePaid,
                    gemini_subscription_linked: subLinked 
                })
            });
            if (res.ok) {
                const data = await res.json();
                this.showModelWarning(data.warning);
                statusIndicator.textContent = 'Status: Ready';
                statusIndicator.style.color = '#8b949e';
                this.appendMessage('ai', `Council re-calibrated. Now utilizing **${model}** for higher reasoning.`, false);
                this.updateCachingToggleVisibility();
            }
        } catch (e) {
            console.error('Model switch failed', e);
            statusIndicator.textContent = 'Status: Calibration Error';
        }
    },

    // --- Settings Popover helpers ---
    _setKnob(knobId, trackId, isOn) {
        const knob = document.getElementById(knobId);
        const track = document.getElementById(trackId);
        if (knob) knob.style.left = isOn ? '22px' : '4px';
        if (track) track.style.background = isOn ? '#238636' : '#30363d';
    },

    syncSettingsPopover() {
        const subOn = this.geminiSubscriptionToggle ? this.geminiSubscriptionToggle.checked : true;
        const paidOn = this.paidTiersToggle ? this.paidTiersToggle.checked : false;
        const cacheOn = false; // Context caching is inactive in local CLI execution mode

        this._setKnob('settings-sub-knob', 'settings-sub-toggle', subOn);
        this._setKnob('settings-paid-knob', 'settings-paid-toggle', paidOn);
        this._setKnob('settings-caching-knob', 'settings-caching-toggle', cacheOn);

        const cachingTrack = document.getElementById('settings-caching-toggle');
        if (cachingTrack) {
            cachingTrack.style.opacity = '0.45';
            cachingTrack.style.cursor = 'not-allowed';
            cachingTrack.title = 'Context caching is not active in local CLI execution mode';
        }

        // Show/hide plan banner and advanced section based on subscription state
        const banner = document.getElementById('settings-plan-banner');
        const advanced = document.getElementById('settings-advanced-section');
        if (banner) banner.style.display = subOn ? 'flex' : 'none';
        if (advanced) advanced.style.display = subOn ? 'none' : 'block';
    },

    toggleSettingsSub() {
        if (!this.geminiSubscriptionToggle) return;
        this.geminiSubscriptionToggle.checked = !this.geminiSubscriptionToggle.checked;
        const isOn = this.geminiSubscriptionToggle.checked;
        if (isOn && this.paidTiersToggle) this.paidTiersToggle.checked = true;
        this.fetchModels();
        this.updateTotalSessionCost();
        this.syncSettingsPopover();
    },


    toggleSettingsPaid() {
        if (!this.paidTiersToggle) return;
        this.paidTiersToggle.checked = !this.paidTiersToggle.checked;
        this.fetchModels();
        this.syncSettingsPopover();
    },

    toggleSettingsCaching() {
        if (this.cachingToggle) {
            this.cachingToggle.checked = false;
            this.cachingToggle.disabled = true;
        }
        this.syncSettingsPopover();
    },

    async toggleCaching() {
        if (!this.cachingToggle) return;
        // Context caching is retired/inactive in local CLI execution mode
        this.cachingToggle.checked = false;
        this.cachingToggle.disabled = true;
        this.syncSettingsPopover();
    },

    async updateCachingToggleVisibility() {
        if (!this.cachingToggle) return;
        // cachingContainer is inside the settings popover — keep retired toggle hidden and disabled
        if (this.cachingContainer) this.cachingContainer.style.display = 'none';
        this.cachingToggle.checked = false;
        this.cachingToggle.disabled = true;
        this.syncSettingsPopover();
    },

    renderQuotaExhaustedCard(thinkingId) {
        // Remove thinking message first
        const tMsg = document.getElementById(thinkingId);
        if (tMsg) tMsg.remove();
        
        // Create card element
        const cardDiv = document.createElement('div');
        cardDiv.className = 'quota-exhausted-card';
        cardDiv.style.cssText = `
            margin: 20px 0;
            padding: 24px;
            background: rgba(255, 75, 75, 0.08);
            border: 1px solid rgba(255, 75, 75, 0.3);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            color: #ffcccc;
            box-shadow: 0 8px 32px rgba(255, 75, 75, 0.15);
            display: flex;
            flex-direction: column;
            gap: 16px;
        `;
        
        cardDiv.innerHTML = `
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="font-size: 2rem;">⚠️</span>
                <div>
                    <h3 style="margin: 0; font-size: 1.1rem; color: #ff6b6b; font-weight: 700; letter-spacing: 0.5px;">API Quota Exhausted</h3>
                    <p style="margin: 4px 0 0; font-size: 0.85rem; color: rgba(255, 200, 200, 0.8);">Your Gemini Free Tier usage limit has been temporarily reached.</p>
                </div>
            </div>
            <p style="margin: 0; font-size: 0.85rem; line-height: 1.5; color: rgba(255, 255, 255, 0.85);">
                Paid Tier keys support higher rate limits and unlock Pro reasoning models for full Council execution.
            </p>
            <button id="upgrade-tier-btn" style="
                background: linear-gradient(135deg, #8b5cf6 0%, #3b82f6 100%);
                border: none;
                color: white;
                padding: 12px 20px;
                border-radius: 10px;
                cursor: pointer;
                font-weight: bold;
                font-size: 0.9rem;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                transition: all 0.2s ease;
                box-shadow: 0 4px 15px rgba(139, 92, 246, 0.4);
            ">
                🚀 Move to Paid Tier & Use Pro
            </button>
        `;
        
        const btn = cardDiv.querySelector('#upgrade-tier-btn');
        btn.onmouseenter = () => {
            btn.style.transform = 'translateY(-2px)';
            btn.style.boxShadow = '0 6px 20px rgba(139, 92, 246, 0.6)';
        };
        btn.onmouseleave = () => {
            btn.style.transform = 'none';
            btn.style.boxShadow = '0 4px 15px rgba(139, 92, 246, 0.4)';
        };
        btn.onclick = () => this.enablePaidTiersAndSwitch();
        
        this.messages.appendChild(cardDiv);
        this.messages.scrollTop = this.messages.scrollHeight;
    },

    async enablePaidTiersAndSwitch() {
        this.appendMessage('ai', 'Initiating Paid Tier calibration sequence...', false);
        
        // 1. Check Include Paid Tiers and Subscription checkbox if it was a quota error upgrade
        if (this.paidTiersToggle) {
            this.paidTiersToggle.checked = true;
        }
        
        // 2. Fetch paid models
        await this.fetchModels();
        
        // 3. Find and select PRO model
        if (this.modelSelector) {
            const options = Array.from(this.modelSelector.options);
            const proOption = options.find(opt => opt.value.includes('pro')) || options[0];
            if (proOption) {
                this.modelSelector.value = proOption.value;
                
                // 4. Trigger model change
                await this.changeModel();
                
                if (this.cachingToggle) {
                    this.cachingToggle.checked = false;
                    this.cachingToggle.disabled = true;
                }
                this.appendMessage('ai', '🚀 Paid Tier active — switched Council to Pro reasoning tier.', false);
            }
        }
    },

    async startNewChat() {
        if (confirm('Archive current session and start a new conversation?')) {
            try { this.archiveCurrentSession(); } catch(e) { console.error('Archive failed', e); }
            this.messages.innerHTML = '';
            this.restoreNodes = null;
            localStorage.removeItem('gem_chat_history');
            this.saveHistory();
            try {
                await fetch('/api/reset_chat', { method: 'POST' });
            } catch (e) {}
            this._renderStandbyMessage();
        }
    },

    archiveCurrentSession() {
        const history = JSON.parse(localStorage.getItem('gem_chat_history') || '[]');
        // A transcript with no user turn is just the standby greeting — nothing to archive.
        if (!Array.isArray(history) || !history.some(m => m && m.role === 'user')) return;
        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        const firstUserMsg = history.find(m => m.role === 'user')?.text || 'Empty Session';
        const sessionPreview = firstUserMsg.substring(0, 40) + (firstUserMsg.length > 40 ? '...' : '');
        
        let totalCost = 0.0;
        history.forEach(m => {
            if (m.cost) totalCost += m.cost;
        });

        sessions.unshift({
            id: Date.now(),
            date: new Date().toLocaleString(),
            preview: sessionPreview,
            messages: history,
            totalCost: totalCost
        });
        if (sessions.length > 10) sessions.pop();
        localStorage.setItem('gemini_chat_sessions', JSON.stringify(sessions));
    },

    showHistory() {
        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        if (sessions.length === 0) {
            alert('No archived sessions found.');
            return;
        }
        let historyHtml = '<div style="padding: 10px;"><h3>Archived Sessions</h3>';
        sessions.forEach(s => {
            const sid = Number(s.id) || 0;
            historyHtml += `
                <div onclick="window.chatUI.loadSession(${sid})" style="padding: 10px; margin-bottom: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1); display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <div style="font-size: 0.75rem; color: #8b949e;">${escapeHtml(s.date)}</div>
                        <div style="font-weight: 500; margin-top: 4px;">${escapeHtml(s.preview)}</div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <button onclick="event.stopPropagation(); window.chatUI.deleteSession(${sid})" style="background: rgba(255, 68, 68, 0.15); border: 1px solid rgba(255, 68, 68, 0.3); color: #ff6b6b; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.75rem;" title="Delete Session">&times;</button>
                    </div>
                </div>
            `;
        });
        historyHtml += '</div>';
        if (!this.restoreNodes) {
            // Park the live chat nodes (keeps their event listeners, e.g. proposal Apply buttons)
            this.restoreNodes = Array.from(this.messages.childNodes);
        }
        this.messages.innerHTML = historyHtml + `<div style="display: flex; justify-content: center;"><button onclick="window.chatUI.restoreCurrentView()" style="margin: 10px; padding: 8px 16px; background: #238636; border: none; color: white; border-radius: 6px; cursor: pointer; width: fit-content;">← Back to Chat</button></div>`;
    },

    deleteSession(id) {
        if (confirm('Delete this archived session permanently?')) {
            let sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
            sessions = sessions.filter(s => s.id !== id);
            localStorage.setItem('gemini_chat_sessions', JSON.stringify(sessions));
            if (sessions.length === 0) {
                // Show empty state inline instead of an alert
                this.messages.innerHTML = `
                    <div style="padding: 20px; text-align: center; color: #8b949e;">
                        <div style="font-size: 2rem; margin-bottom: 12px;">📂</div>
                        <div style="font-weight: 600; margin-bottom: 6px;">No Archived Sessions</div>
                        <div style="font-size: 0.85rem;">Start a new chat to create your first archived session.</div>
                        <button onclick="window.chatUI.restoreCurrentView()" style="margin-top: 16px; padding: 8px 16px; background: #238636; border: none; color: white; border-radius: 6px; cursor: pointer;">← Back to Chat</button>
                    </div>`;
                this.restoreNodes = null;
            } else {
                this.showHistory();
            }
        }
    },

    loadSession(id) {
        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        const session = sessions.find(s => s.id === id);
        if (session) {
            if (confirm('Load this archived session? (Current session will be archived)')) {
                try { this.archiveCurrentSession(); } catch(e) {}
                localStorage.setItem('gem_chat_history', JSON.stringify(session.messages));
                this.restoreNodes = null;
                try { fetch('/api/reset_chat', { method: 'POST' }); } catch(e) {} // Force backend LLM hydration next message
                this.loadHistory({ restore: true });
            }
        }
    },

    restoreCurrentView() {
        if (this.restoreNodes) {
            this.messages.replaceChildren(...this.restoreNodes);
            this.restoreNodes = null;
        }
    },

    _renderStandbyMessage() {
        this.messages.innerHTML = '';
        this._renderedProposalIds = new Set();
        this.appendMessage(
            'ai',
            'Council standing by. Click **🔄 UPDATE ANALYSIS** above (or a quick action below) to run a live briefing.',
            false
        );
    },

    _readStoredHistory() {
        try {
            const parsed = JSON.parse(localStorage.getItem('gem_chat_history') || '[]');
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    },

    _renderHistory(history) {
        this.messages.innerHTML = '';
        this._renderedProposalIds = new Set();
        history.forEach((m) => {
            if (!m || (m.role !== 'ai' && m.role !== 'user')) return;
            this.appendMessage(m.role, String(m.text || ''), false, Number(m.cost) || 0, m.usage || null, m.model || null);
        });
    },

    // Same browser tab (a reload): restore the conversation in progress.
    // New tab / new browser session: archive the previous conversation (if it had any user
    // turns) into SESSIONS, then start clean. `restore: true` forces a restore (loadSession).
    loadHistory({ restore = false } = {}) {
        const TAB_FLAG = 'gem_chat_tab_active';
        let sameTabReload = false;
        try {
            sameTabReload = sessionStorage.getItem(TAB_FLAG) === '1';
            sessionStorage.setItem(TAB_FLAG, '1');
        } catch (_) { /* storage blocked: treat as a new session */ }

        const history = this._readStoredHistory();
        const hasConversation = history.some((m) => m && m.role === 'user');

        if ((restore || sameTabReload) && hasConversation) {
            this._renderHistory(history);
        } else {
            if (hasConversation) {
                try { this.archiveCurrentSession(); } catch (e) { console.error('Archive failed', e); }
            }
            localStorage.removeItem('gem_chat_history');
            this._renderStandbyMessage();
            this.saveHistory();
        }
        this.updateTotalSessionCost();
        // Pending proposals live server-side and survive a page reload.
        this.loadPendingProposals();
        this.loadPendingImprovements();
    },

    async runUpdateAnalysis() {
        if (this.activeAbortController || (this.sendBtn && this.sendBtn.disabled)) return;
        this._renderedProposalIds = this._renderedProposalIds || new Set();
        this._pendingDisplayLabel = '🔄 Update Analysis';
        if (this.input) this.input.value = this.BOOT_PROMPT;
        await this.sendMessage();
    },

    async triggerAutoBoot() {
        await this.runUpdateAnalysis();
    },

    saveHistory() {
        const messageElements = this.messages.querySelectorAll('.modern-message');
        const history = [];
        messageElements.forEach(el => {
            if (el.classList.contains('thinking-msg')) return;
            const role = el.classList.contains('ai') ? 'ai' : 'user';
            const text = el.getAttribute('data-raw-text') || el.innerText;
            const cost = parseFloat(el.getAttribute('data-cost') || '0.0');
            const usageStr = el.getAttribute('data-usage');
            const usage = usageStr ? JSON.parse(usageStr) : null;
            const model = el.getAttribute('data-model') || null;
            history.push({ role, text, cost, usage, model });
        });
        localStorage.setItem('gem_chat_history', JSON.stringify(history));
        this.updateTotalSessionCost();
    },

    updateTotalSessionCost() {
        // Cost estimate UI intentionally removed for local Antigravity runtime.
    },

    toggle() {
        if (this.overlay.classList.contains('active')) {
            this.hide();
        } else {
            this.show();
        }
    },

    show() {
        this.overlay.classList.add('active');
        this._ensureLogStream();
        this.input.focus();
        // Update watchlist-gated quick prompt visibility each time chat opens
        this.updateWatchlistPromptVisibility();
    },

    hide() {
        this.overlay.classList.remove('active');
        if (!this.activeAbortController) {
            this._closeLogStream();
        }
    },

    appendMessage(role, text, save = true, cost = 0.0, usage = null, model = null) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `modern-message ${role}`;
        msgDiv.setAttribute('data-raw-text', text);
        if (cost > 0) {
            msgDiv.setAttribute('data-cost', cost.toString());
        }
        if (usage) {
            msgDiv.setAttribute('data-usage', JSON.stringify(usage));
        }
        if (model) {
            msgDiv.setAttribute('data-model', model);
        }

        if (role === 'ai') {
            renderSanitizedMarkdown(msgDiv, text);
            this.enhanceAiMessageStructure(msgDiv, model);

            // The server strips SSoT EXECUTION_PAYLOAD JSON from replies; also drop the collapsible
            // payload blocks that replies saved before that change (e.g. archived sessions) still carry.
            msgDiv.querySelectorAll('.execution-payload-details').forEach(el => el.remove());
            
            // Collapsible Adversarial Framing Isolation (v10.36)
            const paragraphs = msgDiv.querySelectorAll('p');
            let adversarialParagraph = null;
            for (const p of paragraphs) {
                if (p.textContent.toLowerCase().includes('adversarial framing')) {
                    adversarialParagraph = p;
                    break;
                }
            }

            if (adversarialParagraph) {
                const details = document.createElement('details');
                details.style.cssText = 'margin: 10px 0; cursor: pointer; color: #8b949e; border-left: 2px solid var(--accent); padding-left: 15px; background: rgba(255,255,255,0.01); border-radius: 8px; padding-top: 6px; padding-bottom: 6px; border: 1px solid rgba(255,255,255,0.03); font-size: 0.85rem;';
                const summary = document.createElement('summary');
                summary.style.fontWeight = '600';
                summary.style.color = 'var(--text-secondary)';
                summary.textContent = '⚖️ System Compliance & Framing (Hidden)';
                details.appendChild(summary);

                details.addEventListener('toggle', () => {
                    summary.textContent = details.open 
                        ? '⚖️ System Compliance & Framing' 
                        : '⚖️ System Compliance & Framing (Hidden)';
                });

                const contentDiv = document.createElement('div');
                contentDiv.style.marginTop = '8px';
                contentDiv.style.fontSize = '0.82rem';
                contentDiv.style.lineHeight = '1.4';
                contentDiv.style.color = 'var(--text-muted)';
                details.appendChild(contentDiv);

                // Move the adversarial paragraph into the details tag
                contentDiv.appendChild(adversarialParagraph.cloneNode(true));
                adversarialParagraph.remove();

                // Append the details block at the bottom of the message
                msgDiv.appendChild(details);
            }
            
            const headers = msgDiv.querySelectorAll('h1, h2, h3, h4, h5, h6');
            let debateHeader = null;
            for (const h of headers) {
                const text = h.textContent.toLowerCase();
                if (text.includes('debate') || text.includes('advocate') || text.includes('pessimist')) {
                    debateHeader = h;
                    break;
                }
            }
            
            if (debateHeader) {
                const details = document.createElement('details');
                details.classList.add('council-debate-details');
                details.open = false;
                details.style.cssText = 'margin: 15px 0; cursor: pointer; color: #8b949e; border-left: 2px solid var(--accent-blue); padding-left: 15px; background: rgba(255,255,255,0.02); border-radius: 8px; padding-top: 8px; padding-bottom: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); border: 1px solid rgba(255,255,255,0.05);';
                const summary = document.createElement('summary');
                summary.style.fontWeight = '700';
                summary.style.color = 'var(--text-secondary)';
                summary.style.letterSpacing = '0.5px';
                summary.textContent = '🏛️ Gemini Gem Council Debate (Hidden)';
                details.appendChild(summary);

                details.addEventListener('toggle', () => {
                    summary.textContent = details.open 
                        ? '🏛️ Gemini Gem Council Debate' 
                        : '🏛️ Gemini Gem Council Debate (Hidden)';
                });
                
                const contentDiv = document.createElement('div');
                contentDiv.style.marginTop = '12px';
                details.appendChild(contentDiv);
                
                // Do NOT include debateHeader itself — the <summary> already shows the title
                let siblingsToMove = [];
                let next = debateHeader.nextSibling;
                while (next) {
                    if (next.nodeType === 1 && (next.tagName.startsWith('H') || next.tagName === 'HR')) {
                        const text = next.textContent.toLowerCase();
                        if (next.tagName.startsWith('H')) {
                            const isAgentOrDebate = text.includes('debate') || 
                                                    text.includes('advocate') || 
                                                    text.includes('pessimist') || 
                                                    text.includes('structuralist') || 
                                                    text.includes('sentinel') || 
                                                    text.includes('engine') || 
                                                    text.includes('validator');
                            if (!isAgentOrDebate) {
                                break;
                            }
                        }
                        if (text.includes('decision') || text.includes('metrics') || text.includes('reconciliation') || text.includes('unallocated') || text.includes('proof')) {
                            break;
                        }
                    }
                    siblingsToMove.push(next);
                    next = next.nextSibling;
                }
                
                msgDiv.insertBefore(details, debateHeader);
                siblingsToMove.forEach(node => {
                    contentDiv.appendChild(node);
                });
                // Remove the original header element — summary is the only title needed
                debateHeader.remove();
            }
            // Synchronize the model selector dropdown to the actual model that responded
            if (model && this.modelSelector && this.modelSelector.value !== model) {
                const exists = Array.from(this.modelSelector.options).some(opt => opt.value === model);
                if (exists) {
                    this.modelSelector.value = model;
                    this.updateCachingToggleVisibility();
                }
            }
        } else {
            // Truncate display for long quick-prompts (show only first line)
            const displayText = text.length > 120 ? text.substring(0, 120) + '…' : text;
            msgDiv.textContent = displayText;
            msgDiv.setAttribute('data-raw-text', text); // preserve full text for history
        }
        this.messages.appendChild(msgDiv);
        if (role === 'ai') {
            this.messages.scrollTop = msgDiv.offsetTop - 20;
        } else {
            this.messages.scrollTop = this.messages.scrollHeight;
        }
        if (save) this.saveHistory();
    },

    _extractHeadingSection(msgDiv, headingRegex) {
        const headers = Array.from(msgDiv.querySelectorAll('h1, h2, h3, h4, h5, h6'));
        const target = headers.find((h) => !h.closest('details, .chat-top-brief') && headingRegex.test(h.textContent || ''));
        if (!target) return null;

        const nodes = [];
        let cursor = target.nextSibling;
        while (cursor) {
            const next = cursor.nextSibling;
            if (cursor.nodeType === 1) {
                const tag = cursor.tagName;
                if (/^H[1-6]$/.test(tag) || tag === 'HR' || cursor.classList.contains('execution-payload-details') || cursor.classList.contains('council-debate-details')) {
                    break;
                }
            }
            nodes.push(cursor);
            cursor = next;
        }
        const title = (target.textContent || '').trim();
        target.remove();
        nodes.forEach((n) => n.remove());
        const nonEmpty = nodes.some((n) => (n.textContent || '').trim().length > 0);
        return nonEmpty ? { title, nodes } : null;
    },

    _decorateRecommendationItems(container) {
        const actionClass = (word) => {
            const w = word.toUpperCase();
            if (['BUY', 'ADD', 'EXECUTE'].includes(w)) return 'action-buy';
            if (['TRIM', 'SELL'].includes(w)) return 'action-trim';
            if (['AVOID', 'REJECT'].includes(w)) return 'action-avoid';
            if (['LESSON'].includes(w)) return 'action-lesson';
            if (['TRIGGER'].includes(w)) return 'action-trigger';
            if (['RULE_PROPOSAL'].includes(w)) return 'action-rule';
            return 'action-hold';
        };
        const tokenRe = /^\s*\[?(BUY|ADD|EXECUTE|TRIM|SELL|HOLD|WATCH|WAIT|AVOID|REJECT|LESSON|TRIGGER|RULE_PROPOSAL)\]?(?=\s|$|:|—|-)/i;
        const nonTickers = new Set([
            'USD', 'EUR', 'VWAP', 'RSI', 'ATR', 'MACD', 'GEX', 'HOD', 'LOD', 'SMA', 'EMA',
            'PCE', 'CPI', 'FOMC', 'NFP', 'GDP', 'SSR', 'RTH', 'EST', 'EDT', 'SEC', 'ETF',
            'HOLD', 'TRIM', 'SELL', 'BUY', 'ADD', 'WATCH', 'WAIT', 'AVOID', 'REJECT', 'ALERT',
            'STOP', 'LIMIT', 'CORE', 'STAGE', 'DAY', 'MAX', 'MIN', 'RULE', 'PROPOSAL', 'LESSON',
            'TRIGGER', 'AND', 'FOR', 'WITH', 'THE', 'NOT', 'ZERO', 'CASH', 'NONE', 'VIX', 'VVIX',
        ]);

        const extractTicker = (raw) => {
            const matches = String(raw || '').match(/\b[A-Z]{2,5}\b/g) || [];
            return matches.find((tok) => !nonTickers.has(tok)) || null;
        };

        container.querySelectorAll('li').forEach((li) => {
            const fullText = (li.textContent || '').trim();
            const firstStrong = li.firstElementChild && ['STRONG', 'B'].includes(li.firstElementChild.tagName)
                ? li.firstElementChild
                : null;
            const targetNode = firstStrong && firstStrong.previousSibling === null ? firstStrong : li;
            const firstText = targetNode.firstChild && targetNode.firstChild.nodeType === 3 ? targetNode.firstChild : null;

            let action = null;
            if (firstText) {
                const m = firstText.textContent.match(tokenRe);
                if (m) {
                    action = m[1].toUpperCase();
                    firstText.textContent = firstText.textContent.slice(m[0].length).replace(/^[\s:]+/, '');
                    const pill = document.createElement('span');
                    pill.className = `action-pill ${actionClass(action)}`;
                    pill.textContent = action;
                    li.insertBefore(pill, li.firstChild);
                    // Preserve literal whitespace between pill and following text
                    li.insertBefore(document.createTextNode(' '), pill.nextSibling);
                }
            }

            const sym = extractTicker(fullText);
            const priceMatch = fullText.match(/\$([0-9]+(?:\.[0-9]{1,2})?)/);
            const cleanBodyText = fullText.replace(tokenRe, '').replace(/^[\s:\-—]+/, '').trim();

            if (action === 'LESSON' && cleanBodyText) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'reco-action-btn save-lesson';
                btn.textContent = '📌 Save Lesson';
                btn.onclick = async () => {
                    btn.disabled = true;
                    try {
                        const res = await fetch('/api/trade_lessons/add', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ lesson: cleanBodyText, ticker: sym || 'PORTFOLIO' }),
                        });
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        btn.textContent = '✓ Saved';
                    } catch (_) {
                        btn.textContent = '⚠ Retry';
                        btn.disabled = false;
                    }
                };
                li.appendChild(btn);
            }

            if (action === 'RULE_PROPOSAL' && cleanBodyText) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'reco-action-btn';
                btn.textContent = '📋 Copy Proposal';
                btn.onclick = async () => {
                    try {
                        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                            await navigator.clipboard.writeText(cleanBodyText);
                        }
                        btn.textContent = '✓ Copied';
                    } catch (_) {
                        btn.textContent = '📋 Copy Proposal';
                    }
                };
                li.appendChild(btn);
            }

            if (sym && priceMatch) {
                const targetPrice = Number(priceMatch[1]);
                if (Number.isFinite(targetPrice) && targetPrice > 0) {
                    const isDownside = /\b(stop|support|below|breach|downside|trim|sell|floor|tripwire)\b/i.test(fullText);
                    const op = isDownside ? '<=' : '>=';
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'reco-action-btn arm-trigger';
                    btn.textContent = `🔔 Arm ${sym} $${targetPrice.toFixed(2)}`;
                    btn.onclick = async () => {
                        btn.disabled = true;
                        try {
                            const res = await fetch('/api/alerts', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    ticker: sym,
                                    metric: 'price',
                                    op,
                                    target: targetPrice,
                                    label: 'Council Chat Recommendation',
                                }),
                            });
                            if (!res.ok) throw new Error(`HTTP ${res.status}`);
                            btn.textContent = '✓ Armed';
                            if (typeof window.pollData === 'function') window.pollData();
                        } catch (_) {
                            btn.textContent = `⚠ Retry Arm`;
                            btn.disabled = false;
                        }
                    };
                    li.appendChild(btn);
                }
            }
        });
    },

    enhanceAiMessageStructure(msgDiv, model = null) {
        if (!msgDiv || msgDiv.classList.contains('thinking-msg')) return;

        // 1. Detect verdict under "Final Council Decision" if present
        let verdict = null;
        const headers = Array.from(msgDiv.querySelectorAll('h1, h2, h3, h4, h5, h6'));
        const decisionHeader = headers.find((h) => /final council decision/i.test(h.textContent || ''));
        if (decisionHeader) {
            let cur = decisionHeader.nextElementSibling;
            while (cur && !/^H[1-6]$/.test(cur.tagName)) {
                const m = (cur.textContent || '').match(/\b(EXECUTE|HOLD|REJECT)\b/i);
                if (m) {
                    verdict = m[1].toUpperCase();
                    break;
                }
                cur = cur.nextElementSibling;
            }
        }

        // 2. Extract Alerts & Recommendations sections (or fallback critical bullets)
        let alertSection = this._extractHeadingSection(msgDiv, /alerts?|risk warnings?|warnings?|halt/i);
        if (!alertSection) {
            const alertNodes = Array.from(msgDiv.children).filter((el) => {
                if (el.tagName !== 'P') return false;
                return /^\s*(?:⚠️|🚨|\[?(?:CRITICAL|WARNING|HALT|VETO)\b)/i.test(el.textContent || '');
            });
            if (alertNodes.length > 0) {
                alertNodes.forEach((n) => n.remove());
                alertSection = { title: '🚨 Alerts & Risk Warnings', nodes: alertNodes };
            }
        }
        const recoSection = this._extractHeadingSection(
            msgDiv,
            /actionable recommendations?|recommendations?|priority actions?|suggested actions?/i
        );

        // 3. Auto-collapse Source Index
        const sourcesSection = this._extractHeadingSection(msgDiv, /source index/i);
        if (sourcesSection) {
            const details = document.createElement('details');
            details.className = 'chat-sources-details';
            details.open = false;
            const summary = document.createElement('summary');
            summary.textContent = '📚 Source Index (Hidden)';
            details.appendChild(summary);
            details.addEventListener('toggle', () => {
                summary.textContent = details.open ? '📚 Source Index' : '📚 Source Index (Hidden)';
            });
            const body = document.createElement('div');
            body.className = 'chat-sources-details-body';
            sourcesSection.nodes.forEach((n) => body.appendChild(n));
            details.appendChild(body);
            msgDiv.appendChild(details);
        }

        if (!verdict && !alertSection && !recoSection) return;

        // 4. Assemble and prepend top brief
        const topBrief = document.createElement('div');
        topBrief.className = 'chat-top-brief';

        if (verdict || model) {
            const bar = document.createElement('div');
            bar.className = 'chat-verdict-bar';
            const pill = document.createElement('span');
            const vClass = verdict === 'EXECUTE' ? 'verdict-execute'
                : verdict === 'HOLD' ? 'verdict-hold'
                : verdict === 'REJECT' ? 'verdict-reject'
                : 'verdict-neutral';
            pill.className = `verdict-pill ${vClass}`;
            pill.textContent = verdict ? `VERDICT: ${verdict}` : 'COUNCIL RESPONSE';
            bar.appendChild(pill);
            if (model) {
                const mSpan = document.createElement('span');
                mSpan.className = 'verdict-model';
                mSpan.textContent = model;
                bar.appendChild(mSpan);
            }
            topBrief.appendChild(bar);
        }

        if (alertSection) {
            const box = document.createElement('section');
            box.className = 'chat-callout chat-callout-alert';
            const title = document.createElement('div');
            title.className = 'chat-callout-title';
            title.textContent = alertSection.title.includes('Alert') || alertSection.title.includes('Warning')
                ? alertSection.title
                : `🚨 ${alertSection.title}`;
            const body = document.createElement('div');
            body.className = 'chat-callout-body';
            alertSection.nodes.forEach((n) => body.appendChild(n));
            box.append(title, body);
            topBrief.appendChild(box);
        }

        if (recoSection) {
            const box = document.createElement('section');
            box.className = 'chat-callout chat-callout-reco';
            const title = document.createElement('div');
            title.className = 'chat-callout-title';
            title.textContent = recoSection.title.includes('Recommendation') || recoSection.title.includes('Action')
                ? recoSection.title
                : `🎯 ${recoSection.title}`;
            const body = document.createElement('div');
            body.className = 'chat-callout-body';
            recoSection.nodes.forEach((n) => body.appendChild(n));
            this._decorateRecommendationItems(body);
            box.append(title, body);
            topBrief.appendChild(box);
        }

        msgDiv.insertBefore(topBrief, msgDiv.firstChild);
    },

    shouldCaptureCharts(text, options = {}) {
        if (options && options.includeCharts === true) return true;
        if (this.includeChartsNext === true) return true;
        if (!text) return false;
        if (text === this.BOOT_PROMPT || text.startsWith('SYSTEM DIRECTIVE:')) return false;
        return /\b(chart screenshot|attach chart|capture chart|1m chart|tradingview chart|inspect chart)\b/i.test(text);
    },

    _generateTurnId() {
        this._turnSeq = (this._turnSeq || 0) + 1;
        return 'turn-' + Date.now() + '-' + this._turnSeq + '-' + Math.random().toString(36).slice(2, 8);
    },

    _summarizeUsageTokens(usage) {
        if (!usage) {
            return {
                cachedIn: 0,
                actualIn: 0,
                estIn: 0,
                actualOut: 0,
                estOut: 0,
                promptTokens: 0,
                candidatesTokens: 0,
                inTokens: 0,
                outTokens: 0,
                isMixed: false,
                isEstimatedOnly: false,
            };
        }
        const cachedIn = usage.caching_active ? (usage.cached_tokens || 0) : 0;
        const actualIn = Number(usage.actual_prompt_tokens ?? 0) || 0;
        const estIn = Number(usage.estimated_prompt_tokens ?? 0) || 0;
        const actualOut = Number(usage.actual_candidates_tokens ?? 0) || 0;
        const estOut = Number(usage.estimated_candidates_tokens ?? 0) || 0;
        const hasActual = (usage.actual_prompt_tokens != null && actualIn > 0) || (usage.actual_candidates_tokens != null && actualOut > 0);
        const hasEstimated = (usage.estimated_prompt_tokens != null && estIn > 0) || (usage.estimated_candidates_tokens != null && estOut > 0);
        const isMixed = usage.token_accounting_mode === 'mixed_estimated_and_measured' || (hasActual && hasEstimated);

        let promptTokens = 0;
        let candidatesTokens = 0;
        if (isMixed) {
            promptTokens = actualIn + estIn;
            candidatesTokens = actualOut + estOut;
        } else if (hasActual) {
            promptTokens = actualIn;
            candidatesTokens = actualOut;
        } else if (hasEstimated) {
            promptTokens = estIn;
            candidatesTokens = estOut;
        } else {
            promptTokens = Number(usage.prompt_tokens ?? usage.actual_prompt_tokens ?? usage.estimated_prompt_tokens ?? 0) || 0;
            candidatesTokens = Number(usage.candidates_tokens ?? usage.actual_candidates_tokens ?? usage.estimated_candidates_tokens ?? 0) || 0;
        }

        const isEstimatedOnly = !isMixed && Boolean(
            usage.token_accounting_mode === 'heuristic_estimate' || (usage.is_estimated && !hasActual)
        );
        return {
            cachedIn,
            actualIn,
            estIn,
            actualOut,
            estOut,
            promptTokens,
            candidatesTokens,
            inTokens: promptTokens + cachedIn,
            outTokens: candidatesTokens,
            isMixed,
            isEstimatedOnly,
        };
    },

    _cleanupPendingTurnUI(thinkingId = null, turnId = null) {
        const targetId = thinkingId || this.currentThinkingId;
        if (targetId) {
            const tMsg = document.getElementById(targetId);
            if (tMsg) tMsg.remove();
        }
        const matchesThinking = !thinkingId || this.currentThinkingId === thinkingId;
        const matchesTurn = !turnId || this.activeTurnId === turnId;
        if (matchesThinking && matchesTurn) {
            this.activeTurnId = null;
            this.currentThinkingId = null;
            this.currentLogElement = null;
            if (this.input) {
                this.input.disabled = false;
                this.input.focus();
            }
            if (this.sendBtn) this.sendBtn.style.display = 'flex';
            if (this.stopBtn) this.stopBtn.style.display = 'none';
            const statusIndicator = document.getElementById('chat-status-indicator');
            if (statusIndicator) {
                statusIndicator.textContent = 'Status: Ready';
                statusIndicator.style.color = '#8b949e';
            }
        }
    },

    async stopMessage() {
        this.isAborted = true;
        const turnIdToCancel = this.activeTurnId;
        const thinkingIdToCancel = this.currentThinkingId;
        const statusIndicator = document.getElementById('chat-status-indicator');
        if (statusIndicator) statusIndicator.textContent = 'Status: Stopping...';
        if (this.activeAbortController) {
            try {
                this.activeAbortController.abort();
            } catch (e) {}
            this.activeAbortController = null;
        }
        this._cleanupPendingTurnUI(thinkingIdToCancel, turnIdToCancel);
        this.appendMessage('ai', '🛑 Request cancelled by user.', false);
        try {
            await fetch('/api/cancel_chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(turnIdToCancel ? { turn_id: turnIdToCancel } : {})
            });
        } catch (e) {}
        if (statusIndicator && (!this.activeTurnId || this.activeTurnId === turnIdToCancel)) {
            statusIndicator.textContent = 'Status: Ready';
            statusIndicator.style.color = '#8b949e';
        }
    },

    calculateCost(usage, model) {
        if (!usage || !model) return 0.0;
        const summary = this._summarizeUsageTokens(usage);
        const p = summary.promptTokens;
        const c = summary.candidatesTokens;
        const cached = summary.cachedIn;

        let inputPrice = 0, cachePrice = 0, outputPrice = 0;
        if (model.includes('pro')) {
            inputPrice = 1.25 / 1000000;
            cachePrice = 0.31 / 1000000;
            outputPrice = 5.00 / 1000000;
        } else if (model.includes('flash') || model.includes('gemma')) {
            inputPrice = 0.075 / 1000000;
            cachePrice = 0.01875 / 1000000;
            outputPrice = 0.30 / 1000000;
        }
        
        let cost = (p * inputPrice) + (cached * cachePrice) + (c * outputPrice);
        return cost;
    },

    _syncIntradayCtxBtn() {
        const btn = this.intradayCtxBtn || document.getElementById('toggle-intraday-ctx-btn');
        if (!btn) return;
        const on = Boolean(this.includeChartsNext);
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.textContent = on ? '📈 1m Context: ON' : '📈 1m Context: OFF';
    },

    async sendMessage(isLogRequest = false, options = {}) {
        const text = this.input.value.trim();
        if (!text) return;

        this.isAborted = false;
        const includeCharts = this.shouldCaptureCharts(text, options);
        this.includeChartsNext = false;
        this._syncIntradayCtxBtn();

        // Show friendly label in the chat bubble for quick-prompts; full text goes to the API
        const displayLabel = this._pendingDisplayLabel || null;
        this._pendingDisplayLabel = null;

        // Append user message — show label if set, otherwise truncate long raw text
        if (displayLabel) {
            // Friendly chip label bubble
            const msgDiv = document.createElement('div');
            msgDiv.className = 'modern-message user';
            msgDiv.textContent = displayLabel;
            msgDiv.setAttribute('data-raw-text', text); // full text for history hydration
            this.messages.appendChild(msgDiv);
            this.messages.scrollTop = this.messages.scrollHeight;
            this.saveHistory();
        } else {
            this.appendMessage('user', text);
        }
        this.input.value = '';

        // Thinking indicator keyed by unique turn ID
        const turnId = (options && options.turnId) || this._generateTurnId();
        const thinkingId = 'msg-' + turnId;
        const msgDiv = document.createElement('div');
        msgDiv.className = 'modern-message ai thinking-msg';
        msgDiv.id = thinkingId;

        const thinkingText = document.createElement('div');
        thinkingText.innerHTML = '📡 <strong>Fetching live data & thinking...</strong>';

        const logText = document.createElement('div');
        logText.style.cssText = 'font-size:0.78rem; color:#888; margin-top:5px; font-style:italic;';
        logText.textContent = 'Awaiting DATA_PACKET injection...';

        const abortController = new AbortController();
        this._ensureLogStream();
        this.activeTurnId = turnId;
        this.activeAbortController = abortController;
        this.currentThinkingId = thinkingId;
        this.currentLogElement = logText;
        msgDiv.appendChild(thinkingText);
        msgDiv.appendChild(logText);
        this.messages.appendChild(msgDiv);
        this.messages.scrollTop = this.messages.scrollHeight;

        this.input.disabled = true;
        this.sendBtn.style.display = 'none';
        this.stopBtn.style.display = 'flex';
        // Status indicator is intentionally left blank while the thinking bubble
        // in the chat is already showing real-time progress feedback
        const statusIndicator = document.getElementById('chat-status-indicator');
        if (statusIndicator) statusIndicator.textContent = '';

        const isTurnStale = () => (
            this.isAborted || abortController.signal.aborted || this.activeTurnId !== turnId
        );

        try {
            // Render & capture live 1m TradingView charts only when explicitly requested
            if (includeCharts && typeof window.captureTargetChartScreenshots === 'function') {
                if (this.activeTurnId === turnId && this.currentLogElement) {
                    this.currentLogElement.textContent = 'Rendering & capturing live 1m TradingView charts...';
                }
                await window.captureTargetChartScreenshots();
                if (isTurnStale()) return;
                if (this.activeTurnId === turnId && this.currentLogElement) {
                    this.currentLogElement.textContent = 'Live charts synchronized. Ingesting prompt...';
                }
            }

            if (isTurnStale()) return;

            // Get last 15 messages for session hydration
            const savedHistory = JSON.parse(localStorage.getItem('gem_chat_history') || '[]');
            const historyPayload = savedHistory.slice(-15);

            const endpoint = (options && options.endpoint) || '/api/chat';
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: abortController.signal,
                body: JSON.stringify({
                    message: text,
                    history: historyPayload,
                    include_charts: includeCharts,
                    turn_id: turnId
                })
            });

            if (isTurnStale()) return;

            const data = await res.json();

            if (isTurnStale()) return;

            // Remove thinking message
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            if (this.currentThinkingId === thinkingId) {
                this.currentLogElement = null;
            }

            if (data.status === 'success') {
                const activeModel = data.active_model || data.model;
                if (activeModel && this.modelSelector) {
                    const optionExists = Array.from(this.modelSelector.options).some(opt => opt.value === activeModel);
                    if (!optionExists) {
                        const opt = document.createElement('option');
                        opt.value = activeModel;
                        opt.textContent = `${activeModel.toUpperCase()} (AUTO-FALLBACK)`;
                        opt.style.background = '#1a1a1a';
                        opt.style.color = 'white';
                        this.modelSelector.appendChild(opt);
                    }
                    if (this.modelSelector.value !== activeModel) {
                        console.log(`[Model Sync] Auto-switched dropdown selection from ${this.modelSelector.value} to ${activeModel}`);
                        this.modelSelector.value = activeModel;
                        this.updateCachingToggleVisibility();
                    }
                }

                if (data.fallback_occurred) {
                    this.showModelWarning(`⚡ High server load detected on previous model. Auto-switched active reasoning tier to ${activeModel}.`);
                } else {
                    this.showModelWarning(data.warning);
                }

                let cost = 0.0;
                if (data.usage && typeof data.usage.estimated_cost === 'number') {
                    cost = data.usage.estimated_cost;
                } else if (data.usage && data.model) {
                    cost = this.calculateCost(data.usage, data.model);
                }
                this.appendMessage('ai', data.response, true, cost, data.usage, data.model);
                this.renderProposals(data.proposals);
                this.renderImprovementProposals(data.improvement_proposals);

                // Auto-save every Council response silently to the decision log
                if (!(options && options.skipDecisionLog)) {
                    this.autoSaveDecisionLog(data.response);
                }
            } else if (data.code === 'quota_exhausted') {
                this.renderQuotaExhaustedCard(thinkingId);
            } else {
                this.appendMessage('ai', '⚠️ Error: ' + data.message);
            }
        } catch (e) {
            if (this.isAborted || (e && e.name === 'AbortError') || this.activeTurnId !== turnId) return;
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.appendMessage('ai', '⚠️ Error: Connection failed. Is the server running?');
        } finally {
            if (this.activeAbortController === abortController) {
                this.activeAbortController = null;
            }
            this._cleanupPendingTurnUI(thinkingId, turnId);
        }
    },

    // --- Owner-approved portfolio proposals -------------------------------------------
    // Model-proposed position/cash changes are never written until the owner clicks Apply.
    // All model-derived values are inserted with textContent (never innerHTML).

    async loadPendingProposals() {
        try {
            const res = await fetch('/api/proposals');
            if (!res.ok) return;
            const data = await res.json();
            this.renderProposals(data.proposals);
        } catch (e) {
            console.warn('[Proposals] Failed to load pending proposals:', e);
        }
    },

    renderProposals(proposals) {
        if (!Array.isArray(proposals) || proposals.length === 0) return;
        if (!this._renderedProposalIds) this._renderedProposalIds = new Set();
        // Newer proposals supersede older ones server-side; retire their cards.
        this.messages.querySelectorAll('.proposal-card[data-state="pending"]').forEach(card => {
            if (!proposals.some(p => p.proposal_id === card.dataset.proposalId)) {
                this._setProposalCardState(card, 'superseded', 'Superseded by a newer proposal. Nothing was written.');
            }
        });
        const aiMessages = Array.from(this.messages.querySelectorAll('.modern-message.ai:not(.thinking-msg)'));
        const latestAi = aiMessages.length ? aiMessages[aiMessages.length - 1] : null;
        const anchorTopBrief = latestAi ? latestAi.querySelector('.chat-top-brief') : null;
        proposals.forEach(p => {
            if (this._renderedProposalIds.has(p.proposal_id)) return;
            this._renderedProposalIds.add(p.proposal_id);
            const card = this._buildProposalCard(p);
            if (anchorTopBrief) {
                anchorTopBrief.after(card);
            } else {
                this.messages.appendChild(card);
            }
        });
        if (!anchorTopBrief) {
            this.messages.scrollTop = this.messages.scrollHeight;
        }
    },

    _buildProposalCard(p) {
        const card = document.createElement('div');
        card.className = 'proposal-card';
        card.dataset.proposalId = p.proposal_id;
        card.dataset.state = 'pending';

        const title = document.createElement('div');
        title.className = 'proposal-title';
        title.textContent = '⏳ Proposed portfolio change: not applied yet';
        card.appendChild(title);

        const meta = document.createElement('div');
        meta.className = 'proposal-meta';
        meta.textContent = `From ${p.source || 'the council'}. Review the changes, then apply or discard.`;
        card.appendChild(meta);

        const table = document.createElement('table');
        table.className = 'proposal-diff';
        const head = table.createTHead().insertRow();
        ['Ticker', 'Change', 'Shares', 'WAC'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            head.appendChild(th);
        });
        const body = table.createTBody();
        const arrow = (before, after) => `${before ?? '–'} → ${after ?? '–'}`;
        const kindLabel = { open: 'Open', modify: 'Modify', close: 'Close' };
        (p.positions || []).forEach(c => {
            const row = body.insertRow();
            row.className = `proposal-row-${c.kind}`;
            [c.ticker, kindLabel[c.kind] || c.kind,
             c.kind === 'close' ? arrow(c.shares_before, '0') : arrow(c.shares_before, c.shares_after),
             c.kind === 'close' ? (c.wac_before ?? '–') : arrow(c.wac_before, c.wac_after)
            ].forEach(val => { row.insertCell().textContent = val; });
        });
        (p.cash || []).forEach(c => {
            const row = body.insertRow();
            row.className = 'proposal-row-cash';
            [`Cash ${c.currency}`, c.note ? 'Derived' : 'Set', arrow(c.before, c.after), c.note || ''].forEach(val => { row.insertCell().textContent = val; });
        });
        card.appendChild(table);

        const actions = document.createElement('div');
        actions.className = 'proposal-actions';
        const applyBtn = document.createElement('button');
        applyBtn.type = 'button';
        applyBtn.className = 'glow-btn proposal-apply';
        applyBtn.textContent = 'Apply to portfolio';
        const discardBtn = document.createElement('button');
        discardBtn.type = 'button';
        discardBtn.className = 'glow-btn outline proposal-discard';
        discardBtn.textContent = 'Discard';
        const status = document.createElement('span');
        status.className = 'proposal-status';
        status.setAttribute('role', 'status');
        actions.append(applyBtn, discardBtn, status);
        card.appendChild(actions);

        applyBtn.addEventListener('click', () => this._resolveProposal(card, 'apply'));
        discardBtn.addEventListener('click', () => this._resolveProposal(card, 'discard'));
        return card;
    },

    _setProposalCardState(card, state, message) {
        card.dataset.state = state;
        const status = card.querySelector('.proposal-status');
        if (status) status.textContent = message;
        const title = card.querySelector('.proposal-title');
        if (title) {
            title.textContent = {
                applied: '✅ Portfolio change applied',
                discarded: '✖ Proposal discarded',
                superseded: '↺ Proposal superseded',
                stale: '⚠️ Proposal is out of date',
                expired: '⚠️ Proposal no longer available',
            }[state] || title.textContent;
        }
        const terminal = state !== 'stale';
        card.querySelectorAll('button').forEach(btn => {
            btn.disabled = terminal || btn.classList.contains('proposal-apply');
        });
    },

    async _resolveProposal(card, action) {
        const id = card.dataset.proposalId;
        const status = card.querySelector('.proposal-status');
        card.querySelectorAll('button').forEach(btn => { btn.disabled = true; });
        if (status) status.textContent = action === 'apply' ? 'Applying…' : 'Discarding…';
        try {
            const res = await fetch(`/api/proposals/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.status === 'success') {
                if (action === 'apply') {
                    this._setProposalCardState(card, 'applied', 'Your portfolio has been updated.');
                    if (typeof window.fetchPortfolio === 'function') window.fetchPortfolio(true);
                } else {
                    this._setProposalCardState(card, 'discarded', 'Nothing was written.');
                }
            } else if (res.status === 409 && action === 'apply' && /changed since/i.test(data.detail || '')) {
                this._setProposalCardState(card, 'stale', data.detail);
            } else if (res.status === 404 || res.status === 409) {
                this._setProposalCardState(card, 'expired', data.detail || 'This proposal is no longer pending.');
            } else {
                card.querySelectorAll('button').forEach(btn => { btn.disabled = false; });
                if (status) status.textContent = `Failed: ${data.detail || `HTTP ${res.status}`}. Nothing was written.`;
            }
        } catch (e) {
            card.querySelectorAll('button').forEach(btn => { btn.disabled = false; });
            if (status) status.textContent = 'Connection failed. Nothing was written.';
        }
    },

    // --- Owner-approved self-improvement proposals (trade lessons & rules.md amendments) ---------
    // Agents stage these instead of writing; nothing persists until the owner clicks Apply.
    // Model-derived text is inserted with textContent / textarea.value (never innerHTML).

    async loadPendingImprovements() {
        try {
            const res = await fetch('/api/improvements');
            if (!res.ok) return;
            const data = await res.json();
            this.renderImprovementProposals(data.proposals);
        } catch (e) {
            console.warn('[Improvements] Failed to load pending proposals:', e);
        }
    },

    renderImprovementProposals(proposals) {
        if (!Array.isArray(proposals) || proposals.length === 0) return;
        if (!this._renderedImprovementIds) this._renderedImprovementIds = new Set();
        const aiMessages = Array.from(this.messages.querySelectorAll('.modern-message.ai:not(.thinking-msg)'));
        const latestAi = aiMessages.length ? aiMessages[aiMessages.length - 1] : null;
        let anchor = latestAi ? latestAi.querySelector('.chat-top-brief') : null;
        proposals.forEach(p => {
            if (!p || !p.id || this._renderedImprovementIds.has(p.id)) return;
            this._renderedImprovementIds.add(p.id);
            const card = this._buildImprovementCard(p);
            if (anchor) {
                anchor.after(card);
                anchor = card;
            } else {
                this.messages.appendChild(card);
            }
        });
        if (!latestAi) this.messages.scrollTop = this.messages.scrollHeight;
    },

    _buildImprovementCard(p) {
        const isRule = p.kind === 'rule';
        const card = document.createElement('div');
        card.className = 'proposal-card improvement-card';
        card.dataset.improvementId = p.id;
        card.dataset.kind = p.kind;
        card.dataset.state = 'pending';

        const title = document.createElement('div');
        title.className = 'proposal-title';
        title.textContent = isRule
            ? `🧠 Proposed rule ${p.action === 'MODIFY' ? 'change' : 'addition'} (${p.rule_id || 'NEW_RULE'}): not applied yet`
            : `🧠 Proposed trade lesson${p.ticker && p.ticker !== 'PORTFOLIO' ? ` (${p.ticker})` : ''}: not saved yet`;
        card.appendChild(title);

        const meta = document.createElement('div');
        meta.className = 'proposal-meta';
        meta.textContent = isRule
            ? `From ${p.source || 'the Review Engine'}. Applying appends it to "Owner-Approved Amendments" in rules.md. Edit the text first if needed.`
            : `From ${p.source || 'an agent'}. Applying saves it to the trade lessons registry. Edit the text first if needed.`;
        card.appendChild(meta);

        const text = document.createElement('textarea');
        text.className = 'improvement-text';
        text.rows = 3;
        text.value = p.text || '';
        text.setAttribute('aria-label', isRule ? 'Proposed rule text' : 'Proposed lesson text');
        card.appendChild(text);

        if (p.justification) {
            const just = document.createElement('div');
            just.className = 'improvement-justification';
            just.textContent = `Justification: ${p.justification}`;
            card.appendChild(just);
        }

        const actions = document.createElement('div');
        actions.className = 'proposal-actions';
        const applyBtn = document.createElement('button');
        applyBtn.type = 'button';
        applyBtn.className = 'glow-btn proposal-apply';
        applyBtn.textContent = isRule ? 'Apply to rules.md' : 'Save lesson';
        const discardBtn = document.createElement('button');
        discardBtn.type = 'button';
        discardBtn.className = 'glow-btn outline proposal-discard';
        discardBtn.textContent = 'Discard';
        const status = document.createElement('span');
        status.className = 'proposal-status';
        status.setAttribute('role', 'status');
        actions.append(applyBtn, discardBtn, status);
        card.appendChild(actions);

        applyBtn.addEventListener('click', () => this._resolveImprovement(card, 'apply'));
        discardBtn.addEventListener('click', () => this._resolveImprovement(card, 'discard'));
        return card;
    },

    _setImprovementCardState(card, state, titleText, message) {
        card.dataset.state = state;
        const title = card.querySelector('.proposal-title');
        if (title && titleText) title.textContent = titleText;
        const status = card.querySelector('.proposal-status');
        if (status) status.textContent = message || '';
        card.querySelectorAll('button, textarea').forEach(el => { el.disabled = true; });
    },

    async _resolveImprovement(card, action) {
        const id = card.dataset.improvementId;
        const isRule = card.dataset.kind === 'rule';
        const status = card.querySelector('.proposal-status');
        const textEl = card.querySelector('.improvement-text');
        const controls = card.querySelectorAll('button, textarea');
        controls.forEach(el => { el.disabled = true; });
        if (status) status.textContent = action === 'apply' ? 'Applying…' : 'Discarding…';
        try {
            const opts = { method: 'POST' };
            if (action === 'apply') {
                opts.headers = { 'Content-Type': 'application/json' };
                opts.body = JSON.stringify({ text: textEl ? textEl.value : undefined });
            }
            const res = await fetch(`/api/improvements/${encodeURIComponent(id)}/${action}`, opts);
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.status === 'success') {
                if (action === 'apply') {
                    const p = data.proposal || {};
                    this._setImprovementCardState(card, 'applied',
                        isRule ? `✅ Rule amendment applied${p.amendment_id ? ` (${p.amendment_id})` : ''}` : '✅ Lesson saved',
                        isRule ? 'Agents will use it from their next call.' : (p.duplicate ? 'This lesson was already in the registry.' : 'Added to the trade lessons registry.'));
                } else {
                    this._setImprovementCardState(card, 'discarded', '✖ Proposal discarded', 'Nothing was written.');
                }
            } else if (res.status === 404 || res.status === 409) {
                this._setImprovementCardState(card, 'expired', '⚠️ Proposal no longer available', data.detail || 'This proposal is no longer pending.');
            } else {
                controls.forEach(el => { el.disabled = false; });
                if (status) status.textContent = `Failed: ${data.detail || `HTTP ${res.status}`}. Nothing was written.`;
            }
        } catch (e) {
            controls.forEach(el => { el.disabled = false; });
            if (status) status.textContent = 'Connection failed. Nothing was written.';
        }
    },

    async autoSaveDecisionLog(responseText) {
        // Silently auto-save every Council response to trade_lessons.json.
        // No confirmation bubble shown — this runs in the background.
        try {
            await fetch('/api/save_decision_log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    lesson: responseText.substring(0, 2000),
                    ticker: 'SESSION',
                    outcome: 'AUTO_LOGGED'
                })
            });
        } catch (e) {
            console.error('Auto decision log failed (non-critical):', e);
        }
    },


};

window.chatUI = ModernChat;
window.addEventListener('load', () => ModernChat.init());
