/* MODERN UI LOGIC — Gemini CLI Edition */
const ModernChat = {
    overlay: null,
    launcher: null,
    closeBtn: null,
    input: null,
    messages: null,
    sendBtn: null,

    // Quick-prompt definitions (6 core actions — log/execute handled automatically)
    QUICK_PROMPTS: [
        {
            icon: '⚡',
            label: 'Session Boot',
            id: 'qp-boot',
            prompt: 'SYSTEM BOOT: Execute the full Stage 0 Council Boot Sequence. Using the live DATA_PACKET just injected: (1) Baseline sync — ground all portfolio prices. (2) Cash reconciliation. (3) Regime classification. (4) Portfolio health audit. (5) Market posture assessment. Output a clear, human-readable summary of your findings. DO NOT output a JSON EXECUTION_PAYLOAD.'
        },
        {
            icon: '📊',
            label: 'Market Analysis',
            id: 'qp-analysis',
            prompt: 'SYSTEM DIRECTIVE: ROUTINE TURN EXECUTION. Using the live DATA_PACKET just injected, evaluate risk regime, dealer posture shifts, VIX/VIXY/IEF signals, and score movements. Provide a clear, readable summary of your analysis and top-3 priority actions. DO NOT output a JSON EXECUTION_PAYLOAD.'
        },
        {
            icon: '🔍',
            label: 'Audit & Review',
            id: 'qp-audit-portfolio',
            prompt: 'SYSTEM DIRECTIVE: AUDIT & PORTFOLIO REVIEW. Using the live DATA_PACKET just injected, perform a full portfolio audit: (1) Check all positions against GEM_Trading_Rules, concentration limits, and cash allocation. (2) Conduct a deep portfolio analysis and provide Trim/Hold/Add recommendations for each held ticker with supporting evidence. Output a structured, human-readable compliance and review report. DO NOT output a JSON EXECUTION_PAYLOAD.'
        },
        {
            icon: '⚖️',
            label: 'Risk Regime',
            id: 'qp-risk',
            prompt: 'SYSTEM DIRECTIVE: RISK REGIME ASSESSMENT. Using the live DATA_PACKET just injected, run a full macro risk assessment based on VIX, VIXY, IEF, and SPY. Declare the current regime and risk posture with entry/exit implications. Output a clear, human-readable summary. DO NOT output a JSON EXECUTION_PAYLOAD.'
        },
        {
            icon: '🎯',
            label: 'Scout Opportunities',
            id: 'qp-scout',
            prompt: 'SYSTEM DIRECTIVE: SCOUT INTELLIGENCE SCAN. Using the live DATA_PACKET just injected, evaluate the non-portfolio tickers to identify top entry candidates. Assess risk/reward vs current regime. Output a clear, human-readable summary. DO NOT output a JSON EXECUTION_PAYLOAD.'
        },
        {
            icon: '📝',
            label: 'Review Log',
            id: 'qp-review-log',
            prompt: 'SYSTEM DIRECTIVE: EXECUTE [MANDATE_26_POST_TRADE_REVIEW]. Invoke the Post-Trade Review Engine to forensically audit the complete decision_log.json. Perform a decision log backtest and trade lessons permanency audit: (1) Grade historical agreement scores and trade state assumptions against realized price action. (2) Identify technical drift, liquidity errors, and rebalancing misfires. (3) Generate corrective trade lessons and save them using update_trade_lessons. Output a clear, human-readable forensic summary of your review. DO NOT output a JSON EXECUTION_PAYLOAD.'
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
        this.currentLogElement = null;
        this.isAborted = false;

        if (this.launcher) this.launcher.onclick = () => this.toggle();
        if (this.closeBtn) this.closeBtn.onclick = () => this.hide();
        if (this.sendBtn) this.sendBtn.onclick = () => this.sendMessage();
        if (this.stopBtn) this.stopBtn.onclick = () => this.stopMessage();
        if (this.newChatBtn) this.newChatBtn.onclick = () => this.startNewChat();
        if (this.historyBtn) this.historyBtn.onclick = () => this.showHistory();

        this.modelSelector = document.getElementById('model-selector');
        if (this.modelSelector) {
            this.modelSelector.onchange = () => this.changeModel();
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

        // Setup real-time feedback stream
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

        this.loadHistory();
        this.fetchModels();
        console.log('Gemini CLI Chat UI Initialized');
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
            justify-content: space-between;
            gap: 6px;
            padding: 10px 4px 12px;
            border-bottom: 1px solid rgba(255,255,255,0.07);
            margin-bottom: 10px;
            scrollbar-width: none;
            -webkit-overflow-scrolling: touch;
        `;

        this.QUICK_PROMPTS.forEach(qp => {
            const btn = document.createElement('button');
            btn.id = qp.id;
            btn.innerHTML = `${qp.icon} <span style="white-space:nowrap">${qp.label}</span>`;
            btn.style.cssText = `
                flex-shrink: 0;
                display: flex;
                align-items: center;
                gap: 5px;
                padding: 5px 13px;
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
        // Store the friendly display label so the chat bubble shows icon+name, not the raw system prompt
        this._pendingDisplayLabel = `${qp.icon} ${qp.label}`;
        this.input.value = qp.prompt;
        await this.sendMessage();
    },

    async fetchModels() {
        if (!this.modelSelector) return;
        try {
            const res = await fetch('/api/list_models');
            const data = await res.json();
            if (data.status === 'success') {
                const currentModel = this.modelSelector.value;
                this.modelSelector.innerHTML = '';
                data.models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.name;
                    opt.textContent = m.label;
                    opt.style.background = '#1a1a1a';
                    opt.style.color = 'white';
                    const isDefault = (m.name === 'gemini-2.5-pro' || m.name.includes('2.5-pro'));
                    if (m.name === currentModel || (isDefault && !currentModel)) {
                        opt.selected = true;
                    }
                    this.modelSelector.appendChild(opt);
                });
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
            const res = await fetch('/api/set_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            });
            if (res.ok) {
                statusIndicator.textContent = 'Status: Ready';
                statusIndicator.style.color = '#8b949e';
                this.appendMessage('ai', `Council re-calibrated. Now utilizing **${model}** for higher reasoning.`, false);
            }
        } catch (e) {
            console.error('Model switch failed', e);
            statusIndicator.textContent = 'Status: Calibration Error';
        }
    },

    async startNewChat() {
        if (confirm('Archive current session and start a new conversation?')) {
            this.archiveCurrentSession();
            this.messages.innerHTML = `
                <div class="modern-message ai">
                    Council session reset. Live data will be auto-injected with your next message. How can I help you?
                </div>
            `;
            this.saveHistory();
            try {
                await fetch('/api/reset_chat', { method: 'POST' });
            } catch (e) {}
        }
    },

    archiveCurrentSession() {
        const history = JSON.parse(localStorage.getItem('gem_chat_history') || '[]');
        if (history.length < 2) return;
        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        const firstUserMsg = history.find(m => m.role === 'user')?.text || 'Empty Session';
        const sessionPreview = firstUserMsg.substring(0, 40) + (firstUserMsg.length > 40 ? '...' : '');
        sessions.unshift({
            id: Date.now(),
            date: new Date().toLocaleString(),
            preview: sessionPreview,
            messages: history
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
            historyHtml += `
                <div onclick="window.chatUI.loadSession(${s.id})" style="padding: 10px; margin-bottom: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1);">
                    <div style="font-size: 0.75rem; color: #8b949e;">${s.date}</div>
                    <div style="font-weight: 500; margin-top: 4px;">${s.preview}</div>
                </div>
            `;
        });
        historyHtml += '</div>';
        const originalHtml = this.messages.innerHTML;
        this.messages.innerHTML = historyHtml + `<button onclick="window.chatUI.restoreCurrentView()" style="margin: 10px; padding: 8px 16px; background: #238636; border: none; color: white; border-radius: 6px; cursor: pointer;">← Back to Chat</button>`;
        this.restoreHtml = originalHtml;
    },

    loadSession(id) {
        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        const session = sessions.find(s => s.id === id);
        if (session) {
            if (confirm('Load this archived session? (Current session will be archived)')) {
                this.archiveCurrentSession();
                localStorage.setItem('gem_chat_history', JSON.stringify(session.messages));
                this.loadHistory();
            }
        }
    },

    restoreCurrentView() {
        if (this.restoreHtml) {
            this.messages.innerHTML = this.restoreHtml;
            this.restoreHtml = null;
        }
    },

    loadHistory() {
        const history = localStorage.getItem('gem_chat_history');
        if (history) {
            try {
                const messages = JSON.parse(history);
                this.messages.innerHTML = '';
                messages.forEach(msg => this.appendMessage(msg.role, msg.text, false));
            } catch (e) {
                console.error('Failed to load chat history', e);
            }
        }
    },

    saveHistory() {
        const messageElements = this.messages.querySelectorAll('.modern-message');
        const history = [];
        messageElements.forEach(el => {
            if (el.classList.contains('thinking-msg')) return;
            const role = el.classList.contains('ai') ? 'ai' : 'user';
            const text = el.getAttribute('data-raw-text') || el.innerText;
            history.push({ role, text });
        });
        localStorage.setItem('gem_chat_history', JSON.stringify(history));
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
        this.input.focus();
    },

    hide() {
        this.overlay.classList.remove('active');
    },

    appendMessage(role, text, save = true) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `modern-message ${role}`;
        msgDiv.setAttribute('data-raw-text', text);
        if (role === 'ai') {
            msgDiv.innerHTML = typeof marked !== 'undefined' ? marked.parse(text) : text;
        } else {
            // Truncate display for long quick-prompts (show only first line)
            const displayText = text.length > 120 ? text.substring(0, 120) + '…' : text;
            msgDiv.textContent = displayText;
            msgDiv.setAttribute('data-raw-text', text); // preserve full text for history
        }
        this.messages.appendChild(msgDiv);
        this.messages.scrollTop = this.messages.scrollHeight;
        if (save) this.saveHistory();
    },

    async stopMessage() {
        this.isAborted = true;
        document.getElementById('chat-status-indicator').textContent = 'Status: Stopping...';
        try {
            await fetch('/api/cancel_chat', { method: 'POST' });
        } catch (e) {}
        this.stopBtn.style.display = 'none';
        this.sendBtn.style.display = 'flex';
    },

    calculateCost(usage, model) {
        if (!usage || !model) return 0.0;
        let p = usage.prompt_tokens || 0;
        let c = usage.candidates_tokens || 0;
        let cached = usage.cached_tokens || 0;

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

    async sendMessage(isLogRequest = false) {
        const text = this.input.value.trim();
        if (!text) return;

        this.isAborted = false;

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

        // Thinking indicator
        const thinkingId = 'msg-' + Date.now();
        const msgDiv = document.createElement('div');
        msgDiv.className = 'modern-message ai thinking-msg';
        msgDiv.id = thinkingId;

        const thinkingText = document.createElement('div');
        thinkingText.innerHTML = '📡 <strong>Fetching live data & thinking...</strong>';

        const logText = document.createElement('div');
        logText.style.cssText = 'font-size:0.78rem; color:#888; margin-top:5px; font-style:italic;';
        logText.textContent = 'Awaiting DATA_PACKET injection...';

        this.currentLogElement = logText;
        msgDiv.appendChild(thinkingText);
        msgDiv.appendChild(logText);
        this.messages.appendChild(msgDiv);
        this.messages.scrollTop = this.messages.scrollHeight;

        this.input.disabled = true;
        this.sendBtn.style.display = 'none';
        this.stopBtn.style.display = 'flex';
        document.getElementById('chat-status-indicator').textContent = 'Status: Orchestrating...';

        try {
            // Get last 15 messages for session hydration
            const savedHistory = JSON.parse(localStorage.getItem('gem_chat_history') || '[]');
            const historyPayload = savedHistory.slice(-15);

            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    history: historyPayload
                })
            });

            if (this.isAborted) return;

            const data = await res.json();

            // Remove thinking message
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.currentLogElement = null;

            if (data.status === 'success') {
                this.appendMessage('ai', data.response);
                
                // Add Cost Estimator Badge
                if (data.usage && data.model) {
                    const cost = this.calculateCost(data.usage, data.model);
                    if (cost > 0 || Object.keys(data.usage).length > 0) {
                        const lastMsg = this.messages.lastElementChild;
                        if (lastMsg && lastMsg.classList.contains('ai')) {
                            const badge = document.createElement('div');
                            badge.className = 'cost-estimator';
                            badge.innerHTML = `🪙 Est. Cost: $${cost.toFixed(5)} <span style="font-size:0.65rem;opacity:0.7">(${data.usage.prompt_tokens + data.usage.cached_tokens} in / ${data.usage.candidates_tokens} out)</span>`;
                            lastMsg.appendChild(badge);
                        }
                    }
                }
                
                // Auto-save every Council response silently to the decision log
                this.autoSaveDecisionLog(data.response);
            } else {
                this.appendMessage('ai', '⚠️ Error: ' + data.message);
            }
        } catch (e) {
            if (this.isAborted) return;
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.appendMessage('ai', '⚠️ Error: Connection failed. Is the server running?');
        } finally {
            this.input.disabled = false;
            this.sendBtn.style.display = 'flex';
            this.stopBtn.style.display = 'none';
            document.getElementById('chat-status-indicator').textContent = 'Status: Ready';
            this.input.focus();
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
    }
};

window.chatUI = ModernChat;
window.addEventListener('load', () => ModernChat.init());
