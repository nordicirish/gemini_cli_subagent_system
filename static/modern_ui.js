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

        console.log("Modern UI Logic v2 Initialized");
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

    appendMessage(role, text) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `modern-message ${role}`;
        
        if (role === 'ai') {
            msgDiv.innerHTML = marked.parse(text);
        } else {
            msgDiv.textContent = text;
        }
        
        this.messages.appendChild(msgDiv);
        this.messages.scrollTop = this.messages.scrollHeight;
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
        msgDiv.textContent = 'Thinking...';
        this.messages.appendChild(msgDiv);
        this.messages.scrollTop = this.messages.scrollHeight;
        
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

            if (data.status === 'success') {
                this.appendMessage('ai', data.response);
            } else {
                this.appendMessage('ai', "Error: " + data.message);
            }
        } catch (e) {
            const tMsg = document.getElementById(thinkingId);
            if (tMsg) tMsg.remove();
            this.appendMessage('ai', "Error: Connection failed.");
        }
    }
};

window.addEventListener('load', () => ModernChat.init());
