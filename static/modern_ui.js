/* MODERN UI LOGIC */
const ModernChat = {
    overlay: null,
    launcher: null,
    closeBtn: null,
    input: null,
    messages: null,
    sendBtn: null,

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

        // Setup real-time feedback stream
        this.eventSource = new EventSource('/api/system_logs');
        this.eventSource.onmessage = (event) => {
            if (this.currentLogElement && event.data) {
                // Filter out empty logs or basic pings
                if (event.data.includes("Attempting") || event.data.includes("Orchestrator")) {
                    this.currentLogElement.textContent = event.data;
                } else if (event.data.includes("Tool") || event.data.includes("Agent") || event.data.includes("Engine")) {
                    this.currentLogElement.textContent = "Firing Engine: " + event.data;
                } else {
                    this.currentLogElement.textContent = event.data;
                }
            }
        };

        this.loadHistory();
        console.log("Modern UI Logic v2 Initialized");
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
            console.error("Model switch failed", e);
            statusIndicator.textContent = 'Status: Calibration Error';
        }
    },

    async startNewChat() {
        if (confirm('Archive current session and start a new conversation?')) {
            this.archiveCurrentSession();
            
            // Reset UI
            this.messages.innerHTML = `
                <div class="modern-message ai">
                    Welcome to the council. How can I help you analyze the markets today?
                </div>
            `;
            this.saveHistory();

            // Reset Backend
            try {
                await fetch('/api/reset_chat', { method: 'POST' });
            } catch (e) {}
        }
    },

    archiveCurrentSession() {
        const history = JSON.parse(localStorage.getItem('gemini_chat_history') || '[]');
        if (history.length < 2) return; // Don't archive empty or tiny chats

        const sessions = JSON.parse(localStorage.getItem('gemini_chat_sessions') || '[]');
        const firstUserMsg = history.find(m => m.role === 'user')?.content || 'Empty Session';
        const sessionPreview = firstUserMsg.substring(0, 40) + (firstUserMsg.length > 40 ? '...' : '');
        
        sessions.unshift({
            id: Date.now(),
            date: new Date().toLocaleString(),
            preview: sessionPreview,
            messages: history
        });

        // Keep last 10 sessions
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
                <div onclick="window.chatUI.loadSession(${s.id})" style="padding: 10px; margin-bottom: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1); hover: {background: rgba(255,255,255,0.1)}">
                    <div style="font-size: 0.75rem; color: #8b949e;">${s.date}</div>
                    <div style="font-weight: 500; margin-top: 4px;">${s.preview}</div>
                </div>
            `;
        });
        historyHtml += '</div>';

        // We'll just show it in the chat area for now as a temporary view
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
                localStorage.setItem('gemini_chat_history', JSON.stringify(session.messages));
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
                console.error("Failed to load chat history", e);
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
            msgDiv.innerHTML = marked.parse(text);
        } else {
            msgDiv.textContent = text;
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

    async sendMessage() {
        const text = this.input.value.trim();
        if (!text) return;

        this.isAborted = false;
        this.appendMessage('user', text);
        this.input.value = '';
        
        // Add a temporary thinking message
        const thinkingId = 'msg-' + Date.now();
        const msgDiv = document.createElement('div');
        msgDiv.className = `modern-message ai thinking-msg`;
        msgDiv.id = thinkingId;
        
        const thinkingText = document.createElement('div');
        thinkingText.textContent = 'Thinking...';
        thinkingText.style.fontWeight = 'bold';
        
        const logText = document.createElement('div');
        logText.style.fontSize = '0.8rem';
        logText.style.color = '#888';
        logText.style.marginTop = '4px';
        logText.style.fontStyle = 'italic';
        logText.textContent = 'Awaiting execution...';
        
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
            // Get last 15 messages from localStorage for hydration
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
            } else {
                this.appendMessage('ai', "Error: " + data.message);
            }
        } catch (e) {
            if (this.isAborted) return;
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.appendMessage('ai', "Error: Connection failed.");
        } finally {
            this.input.disabled = false;
            this.sendBtn.style.display = 'flex';
            this.stopBtn.style.display = 'none';
            document.getElementById('chat-status-indicator').textContent = 'Status: Ready';
            this.input.focus();
        }
    }
};

window.addEventListener('load', () => ModernChat.init());
