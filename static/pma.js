/* ════════════════════════════════════════════════════════════
   UTILITIES
   ════════════════════════════════════════════════════════════ */
function fmt(bytes){
    if(!bytes||bytes===0)return '0 B';
    const k=1024,u=['B','KB','MB','GB','TB'];
    const i=Math.floor(Math.log(bytes)/Math.log(k));
    return parseFloat((bytes/Math.pow(k,i)).toFixed(1))+' '+u[i];
}

const UI_STATE={
    reducedMotion:window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    denseTreemapLabels:false,
    chartAnimations:true
};

function animateNumber(el,next){
    if(!el)return;
    const target=Number(next)||0;
    if(UI_STATE.reducedMotion){el.textContent=String(target);return;}
    const prev=parseInt((el.textContent||'0').replace(/[^\d-]/g,''),10);
    const start=Number.isFinite(prev)?prev:0;
    if(start===target){el.textContent=String(target);return;}
    const duration=320;
    const t0=performance.now();
    const tick=(ts)=>{
        const p=Math.min(1,(ts-t0)/duration);
        const eased=1-Math.pow(1-p,3);
        el.textContent=String(Math.round(start+(target-start)*eased));
        if(p<1)requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}

function escHtml(s){return s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}

function colorMix(c1, c2, weight) {
    if (!c1 || !c1.startsWith('#')) return c1;
    if (!c2 || !c2.startsWith('#')) return c1;
    const r1 = parseInt(c1.substring(1, 3), 16), g1 = parseInt(c1.substring(3, 5), 16), b1 = parseInt(c1.substring(5, 7), 16);
    const r2 = parseInt(c2.substring(1, 3), 16), g2 = parseInt(c2.substring(3, 5), 16), b2 = parseInt(c2.substring(5, 7), 16);
    const r = Math.round(r1 * (1 - weight) + r2 * weight);
    const g = Math.round(g1 * (1 - weight) + g2 * weight);
    const b = Math.round(b1 * (1 - weight) + b2 * weight);
    return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

const TYPE_COLORS={
    '.py':'#3572A5','.js':'#f0db4f','.ts':'#3178c6','.json':'#a4c639',
    '.md':'#083fa1','.txt':'#6b7280','.html':'#e34c26','.css':'#563d7c',
    '.pdf':'#e11d48','.java':'#b07219','.c':'#555555','.cpp':'#f34b7d',
    '.rs':'#dea584','.go':'#00add8','.rb':'#cc342d','.xml':'#0060ac',
    '.png':'#a36ad5','.jpg':'#a36ad5','.jpeg':'#a36ad5','.svg':'#ff9900'
};
const FALLBACK_COLORS=['#3d15cb','#8e48ea','#e67e22','#27ae60','#e74c3c','#2980b9'];
let _fallbackIdx=0;
function getTypeColor(ext){
    const key=ext.startsWith('.')?ext.toLowerCase():('.'+ext.toLowerCase());
    if(TYPE_COLORS[key])return TYPE_COLORS[key];
    return FALLBACK_COLORS[_fallbackIdx++ % FALLBACK_COLORS.length];
}
const FOLDER_COLORS=['#3d15cb','#e67e22','#27ae60','#e74c3c','#2980b9','#8e44ad'];

function contrastText(hex){
    if(!hex || typeof hex !== 'string') return '#ffffff';
    const c=hex.replace('#','');
    if(c.length < 6) return '#ffffff';
    const r=parseInt(c.substr(0,2),16), g=parseInt(c.substr(2,2),16), b=parseInt(c.substr(4,2),16);
    return (r*299+g*587+b*114)/1000>150?'#000000':'#ffffff';
}

function isDark(){ return document.documentElement.dataset.theme==='dark'; }
function treemapBorder(){ return isDark()?'#0e0b1a':'#ffffff'; }
function extColor(name){
    const ext='.'+name.split('.').pop().toLowerCase();
    return TYPE_COLORS[ext]||'#6b7280';
}

/* ════════════════════════════════════════════════════════════
   STATE & CORE LOGIC
   ════════════════════════════════════════════════════════════ */
globalThis._typeChart=null;
globalThis._folderChart=null;
globalThis._lastInsightsData=null;
globalThis._currentTypeFilter=null;
globalThis._cachedFileTree=null;
const _gradientCache = new Map();

function toast(msg,type='info'){
    const c=document.getElementById('toast-container');
    if(!c) return;
    const el=document.createElement('div');
    el.className='toast '+type;
    const span=document.createElement('span');
    span.textContent=msg;
    el.appendChild(span);
    c.appendChild(el);
    setTimeout(()=>el.remove(),4000);
}

function switchPage(id,el){
    document.querySelectorAll('.page').forEach(p=>{p.classList.remove('active','page-enter');});
    document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
    const page=document.getElementById('page-'+id);
    if(page){
        page.classList.add('active');
        requestAnimationFrame(()=>page.classList.add('page-enter'));
    }
    if(el) el.classList.add('active');
    if(id==='explorer') {
        if(globalThis._cachedFileTree) renderTree(globalThis._cachedFileTree);
        fetchFileTree();
    }
    if(id==='insights') fetchInsights();
    if(id==='library') fetchStatus();
}

async function fetchStatus(){
    try {
        const res=await fetch('/index/status');
        const d=await res.json();
        const fEl=document.getElementById('s-files'), cEl=document.getElementById('s-chunks');
        if(fEl) animateNumber(fEl,d.files_indexed||0);
        if(cEl) animateNumber(cEl,d.chunks_indexed||0);
        const sbF=document.getElementById('sb-files'), sbC=document.getElementById('sb-chunks');
        if(sbF) sbF.textContent='Files: '+(d.files_indexed||0);
        if(sbC) sbC.textContent='Chunks: '+(d.chunks_indexed||0);
    }catch(e){}
}

/* ════════════════════════════════════════════════════════════
   INSIGHTS & TREEMAPS
   ════════════════════════════════════════════════════════════ */
async function fetchInsights(manual=false){
    try{
        if(manual) globalThis._cachedFileTree = null;
        const treePromise = globalThis._cachedFileTree
            ? Promise.resolve(globalThis._cachedFileTree)
            : fetch('/files/tree').then(r=>r.json());
        const [insRes, tree]=await Promise.all([fetch('/insights'), treePromise]);
        const ins = await insRes.json();
        globalThis._lastInsightsData = ins;
        globalThis._cachedFileTree = tree;

        const sizeEl=document.getElementById('i-size'), countEl=document.getElementById('i-files');
        if(sizeEl) sizeEl.textContent=(ins.total_size_bytes/(1024*1024)).toFixed(1)+' MB';
        if(countEl) animateNumber(countEl,ins.file_count||0);

        renderTypeChart(ins.type_breakdown||{});
        renderHierarchicalFolderChart(tree.folders||{});
        updateInsightsTables();
        if(manual) toast('Insights refreshed','success');
    }catch(e){console.error('Insights failed',e);}
}

function buildLegend(containerId, items){
    const el=document.getElementById(containerId);
    if(!el)return; el.innerHTML='';
    items.slice(0,12).forEach(({label,color})=>{
        const span=document.createElement('span');
        span.className='legend-item';
        span.style.display='inline-flex'; span.style.alignItems='center'; span.style.marginRight='12px'; span.style.fontSize='0.75rem';
        span.innerHTML=`<span style="background:${color}; width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:4px"></span>${escHtml(label)}`;
        el.appendChild(span);
    });
}

function renderTypeChart(typeData){
    const ctx=document.getElementById('type-chart');
    if(!ctx || typeof Chart === 'undefined') return;
    if(globalThis._typeChart) globalThis._typeChart.destroy();
    _gradientCache.clear();
    
    const entries=Object.entries(typeData);
    const totalSize = entries.reduce((s, [, d]) => s + (d.size || 0), 0);
    if(totalSize === 0) return;

    const treeData = entries.map(([ext, d]) => ({
        type: ext || 'other',
        realSize: d.size || 0,
        size: d.size || 0,
        count: d.count || 0
    })).filter(d => d.realSize > 0).sort((a, b) => b.realSize - a.realSize);

    buildLegend('type-chart-legend', treeData.map(d => ({
        label: `${d.type} (${((d.realSize / totalSize) * 100).toFixed(1)}%)`,
        color: getTypeColor(d.type)
    })));

    const colorFn = (c) => {
        if (c.type !== 'data') return 'transparent';
        const d = c.raw && c.raw._data;
        if (!d) return 'transparent';
        const key = `type-${d.type}-${c.raw.w}-${c.raw.h}`;
        if (_gradientCache.has(key)) return _gradientCache.get(key);
        const baseColor = getTypeColor(d.type);
        const grad = c.chart.ctx.createRadialGradient(c.raw.x+c.raw.w*0.3, c.raw.y+c.raw.h*0.3, 0, c.raw.x+c.raw.w/2, c.raw.y+c.raw.h/2, Math.max(c.raw.w, c.raw.h));
        grad.addColorStop(0, colorMix(baseColor, '#ffffff', 0.25));
        grad.addColorStop(1, colorMix(baseColor, '#000000', 0.15));
        _gradientCache.set(key, grad);
        return grad;
    };

    globalThis._typeChart = new Chart(ctx, {
        type: 'treemap',
        data: {
            datasets: [{
                tree: treeData, key: 'size', groups: ['type'], spacing: 1, borderWidth: 1, borderColor: treemapBorder(),
                backgroundColor: colorFn, hoverBackgroundColor: colorFn, // FIX: No flicker
                labels: {
                    display: true, color: (c) => {
                        const d = c.raw && c.raw._data;
                        return d ? contrastText(getTypeColor(d.type)) : '#ffffff';
                    },
                    font: { size: 11, weight: 'bold' },
                    formatter: (c) => {
                        if(!c.raw || !c.raw._data) return '';
                        const minW = UI_STATE.denseTreemapLabels ? 25 : 40;
                        const minH = UI_STATE.denseTreemapLabels ? 14 : 20;
                        return (c.raw.w > minW && c.raw.h > minH) ? c.raw._data.type : '';
                    }
                }
            }]
        },
        options: {
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: () => '',
                        label: (item) => {
                            const d = item.raw && item.raw._data;
                            const v = item.raw && (item.raw.v != null ? item.raw.v : item.raw.s);
                            if(!d) return '';
                            const size = d.realSize || v || 0;
                            if(!size) return '';
                            const pct = totalSize > 0 ? ((size / totalSize) * 100).toFixed(1) : '0.0';
                            const label = d.type || 'unknown';
                            const count = d.count || '';
                            const countStr = count ? ` — ${count} files` : '';
                            return ` ${label}: ${fmt(size)} (${pct}%)${countStr}`;
                        }
                    }
                }
            },
            onClick(e, elements, chart) {
                if (elements.length > 0) {
                    const el = elements[0];
                    const dataItem = chart.data.datasets[el.datasetIndex].data[el.index];
                    if (dataItem && dataItem._data && dataItem._data.type) {
                        globalThis._currentTypeFilter = dataItem._data.type;
                        updateInsightsTables();
                    }
                }
            }
        }
    });
}

function renderHierarchicalFolderChart(folders) {
    const ctx = document.getElementById('folder-chart');
    if (!ctx || typeof Chart === 'undefined') return;
    if (globalThis._folderChart) globalThis._folderChart.destroy();

    const folderEntries = Object.entries(folders);
    let totalSize = 0;
    folderEntries.forEach(([, files]) => { totalSize += files.reduce((s, f) => s + f.size, 0); });
    if (totalSize === 0) return;

    const treeData = [];
    const nameCount = {};
    folderEntries.forEach(([path, files]) => {
        const realSize = files.reduce((s, f) => s + f.size, 0);
        if (realSize === 0) return;
        const parts = path.split(/[/\\]/).filter(Boolean);
        let name = parts[parts.length - 1] || path;
        // Ensure unique names for single-group treemap
        if (nameCount[name]) { nameCount[name]++; name = name + ' (' + nameCount[name] + ')'; }
        else { nameCount[name] = 1; }
        treeData.push({
            name: name,
            path: path,
            realSize: realSize,
            size: realSize
        });
    });
    treeData.sort((a, b) => b.realSize - a.realSize);

    buildLegend('folder-chart-legend', treeData.map((d, i) => ({
        label: `${d.name} (${((d.realSize / totalSize) * 100).toFixed(1)}%)`,
        color: FOLDER_COLORS[i % FOLDER_COLORS.length]
    })));

    const folderColorFn = (c) => {
        if (c.type !== 'data') return 'rgba(0,0,0,0.02)';
        const d = c.raw && c.raw._data;
        if (!d) return 'rgba(0,0,0,0.02)';
        const key = `folder-${d.name}-${c.raw.w}-${c.raw.h}`;
        if (_gradientCache.has(key)) return _gradientCache.get(key);
        const baseColor = FOLDER_COLORS[c.datasetIndex !== undefined ? c.index % FOLDER_COLORS.length : 0];
        const grad = c.chart.ctx.createRadialGradient(c.raw.x+c.raw.w*0.25, c.raw.y+c.raw.h*0.25, 0, c.raw.x+c.raw.w/2, c.raw.y+c.raw.h/2, Math.max(c.raw.w, c.raw.h));
        grad.addColorStop(0, colorMix(baseColor, '#ffffff', 0.15));
        grad.addColorStop(1, colorMix(baseColor, '#000000', 0.2));
        _gradientCache.set(key, grad);
        return grad;
    };

    globalThis._folderChart = new Chart(ctx, {
        type: 'treemap',
        data: {
            datasets: [{
                tree: treeData, key: 'size', groups: ['name'], spacing: 1, borderWidth: 1, borderColor: treemapBorder(),
                backgroundColor: folderColorFn, hoverBackgroundColor: folderColorFn,
                labels: {
                    display: true, color: (c) => {
                        const d = c.raw && c.raw._data;
                        if(!d) return '#ffffff';
                        const idx = treeData.findIndex(t => t.name === d.name);
                        return contrastText(FOLDER_COLORS[(idx >= 0 ? idx : 0) % FOLDER_COLORS.length]);
                    },
                    font: { size: 11, weight: '600' },
                    formatter: (c) => {
                        if(!c.raw || !c.raw._data) return '';
                        const minW = UI_STATE.denseTreemapLabels ? 30 : 50;
                        const minH = UI_STATE.denseTreemapLabels ? 14 : 20;
                        return (c.raw.w > minW && c.raw.h > minH) ? c.raw._data.name : '';
                    }
                }
            }]
        },
        options: {
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: () => '',
                        label: (item) => {
                            const d = item.raw && item.raw._data;
                            const v = item.raw && (item.raw.v != null ? item.raw.v : item.raw.s);
                            if(!d) return '';
                            const size = d.realSize || v || 0;
                            if(!size) return '';
                            const name = d.name || 'unknown';
                            const pct = totalSize > 0 ? ((size / totalSize) * 100).toFixed(1) : '0.0';
                            const path = d.path || '';
                            return [` ${name}: ${fmt(size)} (${pct}%)`, path ? `  ${path}` : ''].filter(Boolean);
                        }
                    }
                }
            }
        }
    });
}

async function updateInsightsTables() {
    const filter = globalThis._currentTypeFilter;
    const ins = globalThis._lastInsightsData;
    if(!ins) return;

    const container = document.getElementById('type-filter-container');
    if(container) {
        container.innerHTML = filter
            ? `<div class="filter-chip-active"><span>Filtering: <strong>${escHtml(filter)}</strong></span><button onclick="globalThis._currentTypeFilter=null; updateInsightsTables()">✕</button></div>`
            : '';
    }

    if(filter) {
        // Fetch type-filtered data from server for accurate results
        try {
            const res = await fetch('/insights/by-type?type_filter=' + encodeURIComponent(filter));
            if(res.ok) {
                const filtered = await res.json();
                fillTable('top-files-body', filtered.top_files || [], filter);
                fillTable('cold-files-body', filtered.cold_files || [], filter);
                return;
            }
        } catch(e) { console.warn('Filtered insights fetch failed, falling back to client filter', e); }
    }

    // No filter or fetch failed — use full data
    fillTable('top-files-body', ins.top_files || [], filter);
    fillTable('cold-files-body', ins.cold_files || [], filter);
}

function fillTable(id,list, highlightExt){
    const tbody=document.getElementById(id);
    if(!tbody) return;
    tbody.innerHTML=list.length?'':'<tr><td colspan="2" style="text-align:center;padding:2rem;color:var(--text-3)">No matching files</td></tr>';
    list.forEach(f=>{
        const tr=document.createElement('tr');
        if(highlightExt && f.path.toLowerCase().endsWith(highlightExt.toLowerCase())) tr.classList.add('highlight-row');
        tr.innerHTML=`<td class="table-path" title="${escHtml(f.path)}">${escHtml(f.path)}</td><td style="text-align:right">${fmt(f.size)}</td>`;
        tbody.appendChild(tr);
    });
}

async function fetchFileTree(){
    try{
        const res=await fetch('/files/tree');
        const data=await res.json();
        globalThis._cachedFileTree=data;
        renderTree(data);
    }catch(e){}
}

function renderTree(data, filter = '') {
    const summary = document.getElementById('explorer-summary');
    if(summary) summary.textContent = `${data.total_files} files · ${fmt(data.total_size)} total`;
    const container = document.getElementById('file-tree');
    if(!container) return;
    container.innerHTML = '';
    const fl = filter.toLowerCase();
    const folderPaths = Object.keys(data.folders || {}).sort();
    if (!folderPaths.length) {
        container.innerHTML = `<div class="empty-state"><p>No folders indexed</p></div>`;
        return;
    }
    folderPaths.forEach(path => {
        const files = data.folders[path];
        const filtered = fl ? files.filter(f => f.path.toLowerCase().includes(fl)) : files;
        if (fl && !filtered.length) return;
        const group = document.createElement('div');
        group.className = 'folder-group';
        const folderSize = filtered.reduce((s, f) => s + f.size, 0);
        const name = path.split(/[/\\]/).pop() || path;
        const hdr = document.createElement('div');
        hdr.className = 'folder-hdr';
        hdr.innerHTML = `<span>📁 ${escHtml(name)} <small style="opacity:0.6">(${filtered.length})</small></span><span style="display:flex; align-items:center; gap:8px"><small>${fmt(folderSize)}</small><span class="arrow">▶</span></span>`;
        const fileList = document.createElement('div');
        fileList.className = 'folder-files';
        hdr.onclick = () => { fileList.classList.toggle('open'); const arrow = hdr.querySelector('.arrow'); if(arrow) arrow.classList.toggle('open'); };
        filtered.forEach(f => {
            const fileName = f.path.split(/[/\\]/).pop();
            const row = document.createElement('div');
            row.className = 'file-row';
            row.innerHTML = `<span class="file-name"><span class="dot" style="background:${extColor(fileName)}"></span>${escHtml(fileName)}</span><span class="file-meta">${fmt(f.size)}</span>`;
            fileList.appendChild(row);
        });
        group.appendChild(hdr); group.appendChild(fileList); container.appendChild(group);
    });
}

async function askQuestion(){
    const input=document.getElementById('search-input');
    const question=input.value.trim();
    if(!question)return;
    const btn=document.getElementById('ask-btn');
    btn.disabled=true; btn.textContent='Searching...';
    const answerEl = document.getElementById('answer-text'), resultsArea = document.getElementById('results-area');
    resultsArea.style.display='block'; answerEl.textContent=''; answerEl.classList.add('streaming');
    try{
        const response = await fetch('/query/stream', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question}) });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            const lines = decoder.decode(value).split('\n');
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);
                    if (data.type === 'content') answerEl.textContent += data.text;
                    if (data.type === 'metadata') renderSources(data.sources);
                } catch(e){ console.debug('Stream parse skip:', line.slice(0, 50)); }
            }
        }
    } catch(e) { toast('Search failed','error'); }
    finally { btn.disabled=false; btn.textContent='Ask Question'; answerEl.classList.remove('streaming'); }
}

function renderSources(sources){
    const srcList = document.getElementById('sources-list');
    if(!srcList) return; srcList.innerHTML='';
    (sources||[]).forEach((s,i)=>{
        const div = document.createElement('div');
        div.className='source-card';
        div.innerHTML=`<div class="source-num">${i+1}</div><div class="source-body"><div class="source-path">${escHtml(s.file_path)}</div></div>`;
        srcList.appendChild(div);
    });
}

/* ════════════════════════════════════════════════════════════
   THEME
   ════════════════════════════════════════════════════════════ */
function toggleTheme(){
    const html = document.documentElement;
    const next = html.dataset.theme === 'dark' ? 'light' : 'dark';
    html.dataset.theme = next;
    try { localStorage.setItem('pma-theme', next); } catch(e){}
    // Re-render treemaps with correct border colour
    const ins = globalThis._lastInsightsData;
    const tree = globalThis._cachedFileTree;
    if(ins) renderTypeChart(ins.type_breakdown || {});
    if(tree) renderHierarchicalFolderChart(tree.folders || {});
}

function applyStoredTheme(){
    try {
        const saved = localStorage.getItem('pma-theme');
        if(saved) document.documentElement.dataset.theme = saved;
    } catch(e){}
}

/* ════════════════════════════════════════════════════════════
   FILE / FOLDER PICKERS & INDEXING
   ════════════════════════════════════════════════════════════ */
async function pickFolder(){
    try {
        const res = await fetch('/pick/folder');
        const data = await res.json();
        if(data.path){
            const input = document.getElementById('folders-input');
            if(input) input.value = input.value ? input.value + ', ' + data.path : data.path;
        }
    } catch(e){ toast('Could not open folder picker','error'); }
}

async function pickFiles(){
    try {
        const res = await fetch('/pick/file');
        const data = await res.json();
        if(data.paths && data.paths.length){
            const input = document.getElementById('folders-input');
            const joined = data.paths.join(', ');
            if(input) input.value = input.value ? input.value + ', ' + joined : joined;
        }
    } catch(e){ toast('Could not open file picker','error'); }
}

async function startIndexing(){
    const input = document.getElementById('folders-input');
    const raw = (input && input.value || '').trim();
    if(!raw){ toast('Enter at least one folder path','error'); return; }
    const folders = raw.split(',').map(s=>s.trim()).filter(Boolean);
    if(!folders.length){ toast('No valid paths','error'); return; }
    const btn = document.getElementById('index-btn');
    if(btn) btn.disabled = true;
    try {
        const res = await fetch('/index/start', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({folders})
        });
        const data = await res.json();
        if(!res.ok){ toast(data.error||'Indexing failed','error'); return; }
        toast('Indexing started','success');
        globalThis._cachedFileTree = null;
        // Start polling progress
        pollProgress();
    } catch(e){ toast('Could not start indexing','error'); }
    finally { if(btn) btn.disabled = false; }
}

function pollProgress(){
    const es = new EventSource('/index/progress-stream');
    const section = document.getElementById('progress-section');
    const gp = document.getElementById('global-progress');
    if(section) section.classList.add('visible');
    if(gp) gp.classList.add('visible');
    const dot = document.getElementById('nav-indexing-dot');
    if(dot) dot.classList.add('active');

    es.addEventListener('progress', (ev) => {
        try {
            const d = JSON.parse(ev.data);
            const pct = d.progress_percent || 0;
            const pctStr = pct + '%';
            const setEl = (id, text) => { const el=document.getElementById(id); if(el) el.textContent=text; };
            const setWidth = (id, w) => { const el=document.getElementById(id); if(el) el.style.width=w; };
            setEl('progress-pct', pctStr); setEl('gp-pct', pctStr);
            setEl('progress-label', d.status==='running'?'Indexing...':'Done');
            setEl('gp-label', d.status==='running'?'Indexing...':'Done');
            setWidth('progress-fill', pctStr); setWidth('gp-fill', pctStr);
            setEl('progress-file', d.current_file||'');
            setEl('gp-file', d.current_file||'');
            setEl('progress-count', `${d.processed_files||0} / ${d.total_files||0} files`);
            setEl('ps-new', d.new_files||0); setEl('gp-new', d.new_files||0);
            setEl('ps-changed', d.changed_files||0); setEl('gp-changed', d.changed_files||0);
            setEl('ps-skipped', d.skipped_files||0); setEl('gp-skipped', d.skipped_files||0);
            setEl('ps-chunks', d.total_chunks||0);
            setEl('s-scan', d.scan_method||'—');

            if(d.status !== 'running'){
                es.close();
                if(dot) dot.classList.remove('active');
                setTimeout(()=>{ if(gp) gp.classList.remove('visible'); }, 3000);
                globalThis._cachedFileTree = null;
                fetchStatus();
            }
        } catch(e){}
    });
    es.onerror = () => { es.close(); if(dot) dot.classList.remove('active'); };
}

async function seedDemo(){
    const btn = document.getElementById('seed-btn');
    if(btn) btn.disabled = true;
    try {
        const res = await fetch('/demo/seed', {method:'POST'});
        const data = await res.json();
        if(!res.ok){ toast(data.error||'Seeding failed','error'); return; }
        toast('Demo data seeded — indexing started','success');
        globalThis._cachedFileTree = null;
        pollProgress();
    } catch(e){ toast('Seed failed','error'); }
    finally { if(btn) btn.disabled = false; }
}

async function cleanupStale(){
    try {
        const res = await fetch('/index/cleanup', {method:'POST'});
        const data = await res.json();
        toast(data.message||'Cleanup done','success');
        globalThis._cachedFileTree = null;
        fetchStatus();
    } catch(e){ toast('Cleanup failed','error'); }
}

async function exportIndex(){
    try {
        const res = await fetch('/index/export');
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'pma_export.json'; a.click();
        URL.revokeObjectURL(url);
        toast('Export downloaded','success');
    } catch(e){ toast('Export failed','error'); }
}

async function clearDatabase(){
    if(!confirm('Clear all data? This cannot be undone.')) return;
    try {
        await fetch('/index/clear', {method:'POST'});
        location.reload();
    } catch(e){ toast('Clear failed','error'); }
}

async function compactDatabase(){
    if(!confirm('This will background vacuum the SQLite database to reclaim space. Continue?')) return;
    try {
        const res = await fetch('/system/compact-db', {method:'POST'});
        const data = await res.json();
        if(!res.ok){ toast(data.error||'Compaction failed','error'); return; }
        toast(data.message||'Compaction started','success');
    } catch(e){ toast('Compaction request failed','error'); }
}

/* ════════════════════════════════════════════════════════════
   EXPLORER FILTER (debounced)
   ════════════════════════════════════════════════════════════ */
let _filterTimer = null;
function debouncedFilterTree(val){
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(()=>{
        const tree = globalThis._cachedFileTree;
        if(tree) renderTree(tree, val);
    }, 250);
}

/* ════════════════════════════════════════════════════════════
   INIT
   ════════════════════════════════════════════════════════════ */
function init(){
    applyStoredTheme();
    document.getElementById('search-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')askQuestion();});

    // Dense labels toggle
    const denseToggle = document.getElementById('dense-labels-toggle');
    if(denseToggle) {
        denseToggle.checked = UI_STATE.denseTreemapLabels;
        denseToggle.addEventListener('change', (e) => {
            UI_STATE.denseTreemapLabels = e.target.checked;
            const ins = globalThis._lastInsightsData;
            const tree = globalThis._cachedFileTree;
            if(ins) renderTypeChart(ins.type_breakdown || {});
            if(tree) renderHierarchicalFolderChart(tree.folders || {});
        });
    }

    // Animated charts toggle
    const motionToggle = document.getElementById('chart-motion-toggle');
    if(motionToggle) {
        motionToggle.checked = UI_STATE.chartAnimations;
        motionToggle.addEventListener('change', (e) => {
            UI_STATE.chartAnimations = e.target.checked;
        });
    }

    // Expose all functions used in HTML onclick handlers to global scope
    Object.assign(globalThis, {
        switchPage, askQuestion, fetchInsights, fetchFileTree, updateInsightsTables,
        clearDatabase, compactDatabase, toggleTheme, pickFolder, pickFiles, startIndexing,
        seedDemo, cleanupStale, exportIndex, debouncedFilterTree, pollProgress
    });

    fetchStatus();
    // Load system info card
    loadSystemInfo();
    if(document.getElementById('page-insights').classList.contains('active')) fetchInsights();
    if(document.getElementById('page-explorer').classList.contains('active')) fetchFileTree();
}

async function loadSystemInfo(){
    try {
        const res = await fetch('/system/info');
        const info = await res.json();
        const card = document.getElementById('sys-card');
        if(!card) return;
        card.style.display = '';
        const badges = document.getElementById('sys-badges');
        if(badges){
            badges.innerHTML = `<span class="badge blue">${escHtml(info.os)}</span>`
                + `<span class="badge ${info.is_admin?'green':'amber'}">${info.is_admin?'Admin':'User'}</span>`
                + `<span class="badge ${info.scan_method==='ntfs_mft'?'green':'blue'}">${escHtml(info.scan_method)}</span>`;
        }
        const grid = document.getElementById('vol-grid');
        if(grid && info.volumes){
            grid.innerHTML = info.volumes.map(v => {
                const pct = v.total_gb > 0 ? Math.round((v.used_gb / v.total_gb) * 100) : 0;
                const color = pct > 90 ? 'var(--danger)' : pct > 70 ? 'var(--warn)' : 'var(--success)';
                return `<div class="vol-card"><div class="vol-letter">${escHtml(v.letter)}</div>`
                    + `<div class="vol-bar"><div class="vol-fill" style="width:${pct}%;background:${color}"></div></div>`
                    + `<small>${v.free_gb} GB free / ${v.total_gb} GB</small></div>`;
            }).join('');
        }
        // Update scan mode badge on library page
        const scanEl = document.getElementById('s-scan');
        if(scanEl) scanEl.textContent = info.scan_method || '—';
    } catch(e){}
}

document.addEventListener('DOMContentLoaded', init);
