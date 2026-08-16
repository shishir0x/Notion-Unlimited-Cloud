const fs = require('fs');

// Mock browser environment
const docElements = {};
global.document = {
    getElementById: (id) => {
        if (!docElements[id]) {
            docElements[id] = {
                id: id,
                style: {},
                classList: { add: ()=>{}, remove: ()=>{}, toggle: ()=>{} },
                innerHTML: '',
                innerText: '',
                value: '',
                addEventListener: ()=>{},
                querySelectorAll: ()=>[],
                insertAdjacentHTML: ()=>{}
            };
        }
        return docElements[id];
    },
    addEventListener: ()=>{}
};
global.window = {
    addEventListener: ()=>{},
    open: ()=>{},
    location: { reload: ()=>{} }
};
global.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; }
    observe() {}
    unobserve() {}
    disconnect() {}
};
global.EventSource = class {
    constructor(url) {}
    addEventListener() {}
    close() {}
};
global.fetch = async (url) => {
    console.log('Mock fetch called for:', url);
    if (url.includes('/api/auth/status')) return { ok: true, json: async () => ({ protected: false }) };
    if (url.includes('/api/stats')) return { ok: true, json: async () => ({ total_mb: 25000, total_files: 10000 }) };
    if (url.includes('/api/drive')) return { ok: true, json: async () => ({
        folders: [
            { id: '1', name: 'Local Disk (C:)', item_count: 4 },
            { id: '2', name: 'Internal shared storage', item_count: 25 },
            { id: '3', name: 'Local Disk (D:)', item_count: 0 },
            { id: '4', name: 'SD card', item_count: 0 }
        ],
        files: [],
        total_files: 0,
        breadcrumbs: [],
        has_more: false
    }) };
    if (url.includes('/api/recent')) return { ok: true, json: async () => ({ files: [] }) };
    if (url.includes('/api/sync/status')) return { ok: true, json: async () => ({ is_running: false, percent: 0 }) };
    return { ok: true, json: async () => ({}) };
};

const serverCode = fs.readFileSync('notion_server.py', 'utf8');
const scriptStart = serverCode.indexOf('<script>');
const scriptEnd = serverCode.indexOf('</script>');
const jsCode = serverCode.substring(scriptStart + 8, scriptEnd);

try {
    eval(jsCode);
    setTimeout(() => {
        console.log('Queried docElements:', Object.keys(docElements));
        console.log('deviceGrid.innerHTML:', docElements['deviceGrid'] ? docElements['deviceGrid'].innerHTML : 'NOT FOUND');
    }, 500);
} catch (err) {
    console.error('CRASHED WITH ERROR:', err);
}
