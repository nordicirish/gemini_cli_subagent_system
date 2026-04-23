/* MODAL LOGIC */
const Modals = {
    init() {
        const dTickerModalOverlay = document.getElementById('ticker-modal-overlay');
        const dOpenModalBtn = document.getElementById('open-ticker-modal-btn');
        const dCloseModalBtn = document.getElementById('close-ticker-modal');
        
        const dIndicesModalOverlay = document.getElementById('indices-modal-overlay');
        const dOpenIndicesBtn = document.getElementById('open-indices-modal-btn');
        const dCloseIndicesBtn = document.getElementById('close-indices-modal');

        const loadSettings = async () => {
            try {
                const res = await fetch('/api/get_settings');
                const data = await res.json();
                if (document.getElementById('ticker-input')) {
                    document.getElementById('ticker-input').value = (data.tickers || []).join(', ');
                }
                if (document.getElementById('indices-input')) {
                    document.getElementById('indices-input').value = (data.macro || []).join(', ');
                }
            } catch (e) { console.error("Load settings failed", e); }
        };

        const saveSettings = async () => {
            const tickers = document.getElementById('ticker-input').value.split(',').map(s => s.trim()).filter(s => s);
            const macro = document.getElementById('indices-input').value.split(',').map(s => s.trim()).filter(s => s);
            
            try {
                const res = await fetch('/api/save_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tickers, macro })
                });
                if (res.ok) {
                    alert("Universe Updated Successfully.");
                    dTickerModalOverlay.classList.remove('active');
                    dIndicesModalOverlay.classList.remove('active');
                    if (window.Dashboard) window.Dashboard.fetchConfig();
                }
            } catch (e) { console.error("Save settings failed", e); }
        };

        if (dOpenModalBtn) {
            dOpenModalBtn.onclick = () => {
                loadSettings();
                dTickerModalOverlay.classList.add('active');
            };
        }
        if (dCloseModalBtn) dCloseModalBtn.onclick = () => dTickerModalOverlay.classList.remove('active');
        
        if (dOpenIndicesBtn) {
            dOpenIndicesBtn.onclick = () => {
                loadSettings();
                dIndicesModalOverlay.classList.add('active');
            };
        }
        if (dCloseIndicesBtn) dCloseIndicesBtn.onclick = () => dIndicesModalOverlay.classList.remove('active');

        if (document.getElementById('update-tickers-btn')) {
            document.getElementById('update-tickers-btn').onclick = saveSettings;
        }
        if (document.getElementById('update-indices-btn')) {
            document.getElementById('update-indices-btn').onclick = saveSettings;
        }

        // Close on overlay click
        [dTickerModalOverlay, dIndicesModalOverlay].forEach(overlay => {
            if (overlay) {
                overlay.onclick = (e) => {
                    if (e.target === overlay) overlay.classList.remove('active');
                };
            }
        });

        console.log("Modals Initialized");
    }
};

window.addEventListener('load', () => Modals.init());
