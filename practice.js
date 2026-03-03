/**
 * PRACTICE DASHBOARD LOGIC
 * Client-Side JS managing LocalStorage, FileSystem Auto-Sync, Spaced Repetition, Hex Map, and Ace Editor.
 */

// Removed TOPIC_HIERARCHY, using dynamic tags instead

let dailyQueue = [];
let currentIndex = 0;
let editor = null;
let currentModalProblemId = null;

// View State
let currentFilterTag = null;
let tableSortCol = 'frequency';
let tableSortAsc = false;
let currentPage = 1;
const rowsPerPage = 50;

// 2. INITIALIZATION
document.addEventListener("DOMContentLoaded", () => {
    AOS.init({ duration: 800, once: true });
    initEditor();
    loadDB();
    setupEventListeners();
});

function initEditor() {
    editor = ace.edit("ace-editor");
    editor.setTheme("ace/theme/dracula");
    editor.session.setMode("ace/mode/python");
    editor.setOptions({ fontSize: "14px", showPrintMargin: false, wrap: true });
}

async function loadDB() {
    try {
        await renderDashboard();
    } catch (err) {
        console.error("Failed to load initial data:", err);
    }
}

function fetchDefaultCSV() {
    fetch("six-months.csv")
        .then(res => res.text())
        .then(csvText => parseCSV(csvText))
        .catch(err => console.error("Could not load default CSV", err));
}

// 3. CSV PARSING & MAPPING
function parseCSV(csvText) {
    Papa.parse(csvText, {
        header: true,
        skipEmptyLines: true,
        complete: function (results) {
            results.data.forEach((row) => {
                if (!row.ID || db.problems[row.ID]) return;

                db.problems[row.ID] = {
                    id: row.ID,
                    title: row.Title,
                    url: row.URL,
                    difficulty: row.Difficulty,
                    frequency: parseFloat(row["Frequency %"] || 0),
                    customTags: [],
                    solved: 0,
                    attempted: 0,
                    code: "",
                    notes: "",
                    nextReview: Date.now(),
                    lastSolved: null,
                    // Legacy migration
                    topics: [],
                    techniques: []
                };
            });
            saveDB();
        }
    });
}

// 4. RENDER DASHBOARD
async function renderDashboard() {
    // 1. Fetch dashboard stats
    try {
        const res = await fetch('https://samarthmahendra-github-io.onrender.com/api/dashboard_stats');
        const data = await res.json();
        renderCalendar(data.checkIns || {}, data.streak || 0);
        renderTagCards(data.tagStats || []);
        populateTopicFilter(data.tagStats || []);
    } catch (e) {
        console.error(e);
    }

    renderTable();
    if (dailyQueue.length === 0) buildDailyQueue();
}

function populateTopicFilter(tagStats) {
    const sel = document.getElementById("filter-topic");
    const datalist = document.getElementById("all-tags-datalist");
    sel.innerHTML = '<option value="">All Tags</option>';
    if (datalist) datalist.innerHTML = "";

    const allTags = tagStats.map(t => t.name).sort();

    allTags.forEach(t => {
        sel.innerHTML += `<option value="${t}">${t}</option>`;
        if (datalist) datalist.innerHTML += `<option value="${t}">`;
    });
}

function renderCalendar(checkIns = {}, streak = 0) {
    const grid = document.getElementById("calendar-grid");
    const datesCont = document.getElementById("calendar-days");
    grid.innerHTML = "";
    datesCont.innerHTML = "<span>Mon</span><span>Wed</span><span>Fri</span>";

    const today = new Date();
    for (let i = 364; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(d.getDate() - i);
        const dateStr = d.toISOString().split("T")[0];

        const cell = document.createElement("div");
        cell.className = "cal-cell";

        let count = checkIns[dateStr] || 0;
        let lvl = "lvl-0";
        if (count > 0) lvl = "lvl-1";
        if (count >= 2) lvl = "lvl-2";
        if (count >= 4) lvl = "lvl-3";
        if (count >= 6) lvl = "lvl-4";

        cell.classList.add(lvl);
        cell.setAttribute("data-tooltip", `${count} problems on ${dateStr}`);
        grid.appendChild(cell);
    }
    document.getElementById("streak-counter").innerText = `${streak} Day Streak 🔥`;
}

function getConfLevelCss(solved, attempted) {
    if (attempted === 0) return "conf-empty";
    const ratio = solved / attempted;
    const lvl = Math.max(1, Math.round(ratio * 5));
    return `conf-${lvl}`;
}

function renderTagCards(dataset = []) {
    const grid = document.getElementById("tag-cards-grid");
    if (!grid) return;
    grid.innerHTML = "";

    dataset.forEach(item => {
        const ratioConf = item.attempted === 0 ? 0 : item.solved / item.attempted;
        const confPercent = Math.round(ratioConf * 100);

        let confLevel = "0";
        if (item.attempted > 0) {
            confLevel = Math.max(1, Math.round(ratioConf * 5)).toString();
        }

        const card = document.createElement("div");
        card.className = `tag-card ${currentFilterTag === item.name ? 'active' : ''}`;

        card.innerHTML = `
            <div class="tag-card-header">
                <h4 class="tag-card-title">${item.name}</h4>
                <div class="tag-card-stats">
                    <div class="tag-stat">
                        <span>${confPercent}%</span>
                        <span>Confidence</span>
                    </div>
                    <div class="tag-stat">
                        <span>${item.solved}/${item.total}</span>
                        <span>Solved</span>
                    </div>
                </div>
            </div>
            <div class="tag-progress-wrapper">
                <div class="tag-progress-fill fill-${confLevel}" style="width: ${confPercent}%"></div>
            </div>
        `;

        card.onclick = () => handleCardClick(item.name);
        grid.appendChild(card);
    });
}

function handleCardClick(name) {
    currentFilterTag = currentFilterTag === name ? null : name;
    // Re-fetch dashboard stats to re-render tag cards fully correctly, or just update classes
    renderDashboard();
}

async function renderTable() {
    const tbody = document.getElementById("table-body");
    const title = document.getElementById("table-title");
    tbody.innerHTML = "<tr><td colspan='6' style='text-align:center;'>Loading...</td></tr>";

    const searchQuery = document.getElementById("table-search")?.value.toLowerCase() || "";
    const filterLevel = document.getElementById("filter-level")?.value || "";
    const filterDropTopic = document.getElementById("filter-topic")?.value || "";

    const finalTopicFilter = currentFilterTag || filterDropTopic;

    title.innerText = currentFilterTag ? `Problems for Tag: ${currentFilterTag}` : `Detailed Problem Archive`;

    try {
        const queryParams = new URLSearchParams({
            page: currentPage,
            limit: rowsPerPage,
            sortCol: tableSortCol,
            sortAsc: tableSortAsc,
            search: searchQuery,
            level: filterLevel,
            topic: finalTopicFilter
        });

        const res = await fetch(`https://samarthmahendra-github-io.onrender.com/api/table?${queryParams.toString()}`);
        const data = await res.json();
        const items = data.items || [];

        tbody.innerHTML = "";

        items.forEach(p => {
            const pid = p._id || p.id;
            const ratioText = p.attempted === 0 ? "Untried" : `${Math.round((p.solved / p.attempted) * 100)}%`;
            const r = p.attempted > 0 ? p.solved / p.attempted : 0;
            const color = p.attempted === 0 ? 'var(--text-muted)' : r > 0.8 ? '#34D399' : r > 0.4 ? '#FBBF24' : '#F87171';

            const tr = document.createElement("tr");
            tr.style.cursor = "pointer";
            tr.onclick = () => openProblemInsights(pid);

            tr.innerHTML = `
                <td style="color:var(--text-muted);">#${pid}</td>
                <td style="color:var(--text);font-weight:500;">${p.title}</td>
                <td><span class="diff-cell diff-${p.difficulty}">${p.difficulty}</span></td>
                <td style="color:var(--accent);font-family:monospace;">${parseFloat(p.frequency || 0).toFixed(1)}%</td>
                <td style="font-size:0.8rem;color:var(--text-muted);">
                    ${p.topics && p.topics[0] ? p.topics[0] : ''}
                    ${p.customTags && p.customTags.length > 0 ? `<br><span style="color:var(--accent)">${p.customTags.join(', ')}</span>` : ''}
                </td>
                <td class="conf-cell" style="color:${color};">${ratioText}</td>
            `;
            tbody.appendChild(tr);
        });

        document.querySelectorAll(".problem-table th i").forEach(i => i.className = "fas fa-sort");
        const activeTh = document.querySelector(`.problem-table th[data-sort="${tableSortCol}"] i`);
        if (activeTh) activeTh.className = tableSortAsc ? "fas fa-sort-up" : "fas fa-sort-down";

        // Update Pagination UI
        const pageInfo = document.getElementById("page-info");
        const totalItems = data.total || 0;
        const totalPages = data.totalPages || 1;

        if (currentPage > totalPages) {
            currentPage = Math.max(1, totalPages);
            return renderTable(); // re-fetch with corrected page
        }

        const startIdx = (currentPage - 1) * rowsPerPage;

        if (pageInfo) {
            pageInfo.innerText = `Showing ${totalItems === 0 ? 0 : startIdx + 1} to ${Math.min(startIdx + rowsPerPage, totalItems)} of ${totalItems} entries`;
        }
        const btnPrev = document.getElementById("btn-prev-page");
        const btnNext = document.getElementById("btn-next-page");
        if (btnPrev) btnPrev.disabled = currentPage === 1;
        if (btnNext) btnNext.disabled = currentPage >= totalPages || totalPages === 0;

    } catch (err) {
        console.error("Fetch table failed:", err);
        tbody.innerHTML = "<tr><td colspan='6' style='text-align:center; color:red;'>Error Loading DB</td></tr>";
    }
}

// --- PROBLEM INSIGHTS MODAL ---
async function openProblemInsights(id) {
    currentModalProblemId = id;

    // Show modal immediately with a loading state
    document.getElementById("insights-modal").classList.remove("hidden");
    document.getElementById("modal-title-input").value = "Loading...";
    document.getElementById("modal-tags").innerHTML = "";

    let p;
    try {
        const res = await fetch(`https://samarthmahendra-github-io.onrender.com/api/problem/${id}`);
        p = await res.json();
    } catch (err) {
        console.error(err);
    }

    // If not found, treat as new problem with defaults
    if (!p || p.error) {
        p = {
            _id: id, title: "", url: "", difficulty: "Medium", frequency: 0,
            topics: [], techniques: [], customTags: [],
            solved: 0, attempted: 0, notes: "", lastSolved: null, nextReview: Date.now()
        };
    }

    document.getElementById("modal-title-input").value = p.title || "";
    document.getElementById("modal-diff-select").value = p.difficulty || "Medium";
    document.getElementById("modal-freq-input").value = p.frequency || 0;
    document.getElementById("modal-url-input").value = p.url || "";

    document.getElementById("modal-last-solved").innerText = `Last Solved: ${p.lastSolved ? new Date(p.lastSolved).toLocaleString() : 'Never'}`;
    const ratioText = p.attempted === 0 ? "0%" : `${Math.round((p.solved / p.attempted) * 100)}%`;
    document.getElementById("modal-stats").innerText = `Confidence: ${ratioText} (${p.solved} Solved / ${p.attempted} Attempted)`;

    document.getElementById("modal-notes").value = p.notes || "";

    renderModalTagsFromData(p);
}

function renderModalTagsFromData(p) {
    const wrap = document.getElementById("modal-tags");
    const topicsArr = p.topics || [];
    const techniquesArr = p.techniques || [];
    const customArr = p.customTags || [];

    let html = "";

    topicsArr.forEach((t) => {
        html += `<span class="btag tag-removable" style="margin-right:8px;">${t} <i class="fas fa-times" onclick="this.parentElement.remove()" style="cursor:pointer; margin-left: 4px;"></i></span>`;
    });

    techniquesArr.forEach((t) => {
        html += `<span class="btag tag-removable" style="margin-right:8px;">${t} <i class="fas fa-times" onclick="this.parentElement.remove()" style="cursor:pointer; margin-left: 4px;"></i></span>`;
    });

    customArr.forEach((t) => {
        html += `<span class="btag tag-removable" style="margin-right:8px; border-color:var(--accent-primary);">${t} <i class="fas fa-times" onclick="this.parentElement.remove()" style="cursor:pointer; margin-left: 4px;"></i></span>`;
    });

    wrap.innerHTML = html;
}


function handleTableSort(col) {
    if (tableSortCol === col) tableSortAsc = !tableSortAsc;
    else { tableSortCol = col; tableSortAsc = false; }
    currentPage = 1;
    renderTable();
}

async function buildDailyQueue() {
    try {
        const res = await fetch('https://samarthmahendra-github-io.onrender.com/api/daily_queue');
        dailyQueue = await res.json();
        currentIndex = 0;
        showNextCard();
    } catch (err) {
        console.error("Failed to build daily queue:", err);
    }
}

function showNextCard() {
    const emptyUI = document.getElementById("flashcard-empty");
    const cardUI = document.getElementById("flashcard-container");
    const editorUI = document.getElementById("editor-container");
    const remaining = document.getElementById("cards-remaining");

    if (currentIndex >= dailyQueue.length) {
        remaining.innerText = `0 / ${dailyQueue.length}`;
        emptyUI.classList.remove("hidden");
        cardUI.classList.add("hidden");
        editorUI.classList.add("hidden");
        return;
    }

    remaining.innerText = `${dailyQueue.length - currentIndex} / ${dailyQueue.length}`;
    emptyUI.classList.add("hidden");
    cardUI.classList.remove("hidden");
    editorUI.classList.add("hidden");

    document.getElementById("flashcard").classList.remove("is-flipped");

    const p = dailyQueue[currentIndex];
    document.getElementById("q-title").innerText = p.title;
    document.getElementById("q-difficulty").className = `card-difficulty diff-${p.difficulty}`;
    document.getElementById("q-difficulty").innerText = p.difficulty;
    document.getElementById("q-freq").innerText = `${p.frequency.toFixed(1)}% Freq`;
    document.getElementById("q-title-back").innerText = p.title;
    document.getElementById("q-link").href = p.url;

    editor.setValue(p.code || "# Write your Python 3 code here...\n", -1);
    document.getElementById("notes-area").value = p.notes || "";
    document.getElementById("editor-title").innerText = p.title;
    document.getElementById("editor-link").href = p.url;

    const tagsHTML = [...(p.customTags || [])];
    if (p.topics && p.topics[0]) tagsHTML.push(p.topics[0]);
    document.getElementById("q-topics").innerHTML = tagsHTML.map(t => `<span class="btag">${t}</span>`).join('');
    document.getElementById("q-subtopics").innerHTML = '';
}

// 5. EVENT HANDLERS
function setupEventListeners() {

    document.querySelectorAll(".problem-table th").forEach(th => {
        if (th.getAttribute("data-sort")) th.addEventListener("click", () => handleTableSort(th.getAttribute("data-sort")));
    });

    // Flashcard UI
    document.getElementById("flashcard-container").addEventListener("click", function (e) {
        if (e.target.closest('.btn-conf') || e.target.closest('a')) return;
        document.getElementById("flashcard").classList.toggle("is-flipped");
    });

    document.querySelectorAll(".btn-conf").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            document.getElementById("flashcard-container").classList.add("hidden");
            document.getElementById("editor-container").classList.remove("hidden");
            setTimeout(() => editor.resize(), 100);
        });
    });

    document.getElementById("btn-toggle-notes").addEventListener("click", () => {
        document.getElementById("pane-notes").classList.toggle("hidden");
        document.querySelector(".split-pane").classList.toggle("has-notes");
        setTimeout(() => editor.resize(), 300);
    });

    document.getElementById("btn-mark-solved").addEventListener("click", () => handleAttempt(true));
    document.getElementById("btn-mark-failed").addEventListener("click", () => handleAttempt(false));
    document.getElementById("btn-study-more").addEventListener("click", () => buildDailyQueue());

    // Table actions
    document.getElementById("table-search").addEventListener("input", () => { currentPage = 1; renderTable(); });
    document.getElementById("filter-level").addEventListener("change", () => { currentPage = 1; renderTable(); });
    document.getElementById("filter-topic").addEventListener("change", () => { currentPage = 1; renderTable(); });

    const btnPrev = document.getElementById("btn-prev-page");
    const btnNext = document.getElementById("btn-next-page");
    if (btnPrev) btnPrev.addEventListener("click", () => {
        if (currentPage > 1) { currentPage--; renderTable(); }
    });
    if (btnNext) btnNext.addEventListener("click", () => {
        currentPage++; renderTable();
    });

    document.getElementById("btn-add-problem").addEventListener("click", () => {
        const newId = "custom-" + Math.floor(Math.random() * 10000);
        openProblemInsights(newId);
    });

    // Insights Modal Events
    document.getElementById("close-modal").addEventListener("click", () => {
        document.getElementById("insights-modal").classList.add("hidden");
    });

    document.getElementById("btn-save-insights").addEventListener("click", async () => {
        const title = document.getElementById("modal-title-input").value || "Untitled Problem";
        const difficulty = document.getElementById("modal-diff-select").value;
        const frequency = parseFloat(document.getElementById("modal-freq-input").value) || 0;
        const url = document.getElementById("modal-url-input").value;
        const notes = document.getElementById("modal-notes").value;

        // We need to fetch the existing tags from the DOM since we aren't maintaining global state for them
        const customTags = [];
        const topics = [];
        const techniques = [];

        document.querySelectorAll("#modal-tags .tag-removable").forEach(span => {
            const rawText = span.innerText;
            const text = rawText.replace(" x", "").replace(" \uf00d", "").trim();
            // In a real robust app, we should distinctly track which tag is which type, 
            // but for simplicity, we pass them all as custom tags since the DB supports it 
            // and the UI removed the topic distinction mostly anyway.
            if (text) customTags.push(text);
        });

        const payload = {
            title, url, difficulty, frequency, notes, customTags, topics: [], techniques: []
        };

        try {
            await fetch(`https://samarthmahendra-github-io.onrender.com/api/problem/${currentModalProblemId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            document.getElementById("insights-modal").classList.add("hidden");
            renderDashboard(); // Refresh full dashboard stats & table
        } catch (err) {
            console.error(err);
        }
    });

    document.getElementById("btn-add-tag").addEventListener("click", () => {
        const input = document.getElementById("new-tag-input");
        const val = input.value.trim();

        if (val) {
            const wrap = document.getElementById("modal-tags");
            wrap.innerHTML += `<span class="btag tag-removable" style="margin-right:8px; border-color:var(--accent-primary);">${val} <i class="fas fa-times" onclick="this.parentElement.remove()" style="cursor:pointer; margin-left: 4px;"></i></span>`;
            input.value = "";
        }
    });
}

async function handleAttempt(solved) {
    const p = dailyQueue[currentIndex];
    const pid = p._id || p.id;

    const code = editor.getValue();
    const notes = document.getElementById("notes-area").value;

    try {
        await fetch(`https://samarthmahendra-github-io.onrender.com/api/flashcard/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                problem_id: pid,
                solved: solved,
                code: code,
                notes: notes
            })
        });

        currentIndex++;
        showNextCard();

        // Refresh dashboard numbers in background without completely resetting queue
        fetch('https://samarthmahendra-github-io.onrender.com/api/dashboard_stats')
            .then(res => res.json())
            .then(data => {
                renderCalendar(data.checkIns || {}, data.streak || 0);
                renderTagCards(data.tagStats || []);
            });

    } catch (err) {
        console.error("Submit attempt failed", err);
    }
}
