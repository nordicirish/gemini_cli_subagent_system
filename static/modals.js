/* MODAL LOGIC */
const Modals = {
    init() {
        const dTickerModalOverlay = document.getElementById('ticker-modal-overlay');
        const dOpenModalBtn = document.getElementById('open-ticker-modal-btn');
        const dCloseModalBtn = document.getElementById('close-ticker-modal');
        
        const dIndicesModalOverlay = document.getElementById('indices-modal-overlay');
        const dOpenIndicesBtn = document.getElementById('open-indices-modal-btn');
        const dCloseIndicesBtn = document.getElementById('close-indices-modal');

        if (dOpenModalBtn) dOpenModalBtn.onclick = () => dTickerModalOverlay.classList.add('active');
        if (dCloseModalBtn) dCloseModalBtn.onclick = () => dTickerModalOverlay.classList.remove('active');
        
        if (dOpenIndicesBtn) dOpenIndicesBtn.onclick = () => dIndicesModalOverlay.classList.add('active');
        if (dCloseIndicesBtn) dCloseIndicesBtn.onclick = () => dIndicesModalOverlay.classList.remove('active');

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
