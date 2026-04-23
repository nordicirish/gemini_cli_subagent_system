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
        this.currentLogElement = null;

        if (this.launcher) this.launcher.onclick = () => this.toggle();
        if (this.closeBtn) this.closeBtn.onclick = () => this.hide();
        
        if (this.sendBtn) {
            this.sendBtn.onclick = () => this.sendMessage();
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

    async sendMessage() {
        const text = this.input.value.trim();
        if (!text) return;

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
        this.sendBtn.disabled = true;
        this.sendBtn.style.opacity = '0.5';
        this.sendBtn.style.cursor = 'not-allowed';
        document.getElementById('chat-status-indicator').textContent = 'Status: Orchestrating...';
        
        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });
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
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.appendMessage('ai', "Error: Connection failed.");
        } finally {
            this.input.disabled = false;
            this.sendBtn.disabled = false;
            this.sendBtn.style.opacity = '1';
            this.sendBtn.style.cursor = 'pointer';
            document.getElementById('chat-status-indicator').textContent = 'Status: Ready';
            this.input.focus();
        }
    }
};

window.addEventListener('load', () => ModernChat.init());
