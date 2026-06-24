/* PBC文件核对工具 - 前端交互逻辑 */

// ====== 会话 ID — 每个标签页独立 ======
const SESSION_ID = crypto.randomUUID();
// 拦截 fetch 为所有请求自动注入会话头
const _origFetch = window.fetch;
window.fetch = function(url, options = {}) {
    if (typeof url === "string" && !url.startsWith("http") && !url.startsWith("//")) {
        options.headers = options.headers || {};
        if (options.headers instanceof Headers) {
            options.headers.set("X-Session-Id", SESSION_ID);
        } else {
            options.headers["X-Session-Id"] = SESSION_ID;
        }
    }
    return _origFetch.call(window, url, options);
};

const API = {
    templateInfo: "/api/template-info",
    generateChecklist: "/api/generate-checklist",
    downloadChecklist: "/api/download-checklist",
    uploadChecklistV2: "/api/upload-checklist-v2",
    updateCellStatus: "/api/update-cell-status",
    exportChecklist: "/api/export-checklist",
    scanFolder: "/api/scan-folder",
    match: "/api/match",
    manualMatch: "/api/manual-match",
    llmMatch: "/api/llm-match",
    browseDirs: "/api/browse-dirs",
    createFolder: "/api/create-folder",
    resetState: "/api/reset-state",
    assignCompany: "/api/assign-company",
    organizeFiles: "/api/organize-files",
    browseViewData: "/api/browse-view-data",
    addRow: "/api/add-row",
    editRow: "/api/edit-row",
    deleteRow: "/api/delete-row",
    // 项目管理
    projectList: "/api/project/list",
    projectCurrent: "/api/project/current",
    projectCreate: "/api/project/create",
    projectSave: "/api/project/save",
    projectLoad: "/api/project/load",
    projectDelete: "/api/project/delete",
};

// 全局状态
let templateData = null;        // 模板科目+资料项
let previewItems = [];           // 预览区清单数据
let previewCompanies = [];       // 公司列表 [{full_name, short_name}]
let companyNames = [];           // 公司简称列表
let matchResults = null;
let scannedCount = 0;
let scanRoot = "";
let showCols = null;
let colFilters = {};
let previewStatusCell = null;   // 预览区当前右键的状态单元格
let previewStatusRowIndex = null;
let previewStatusCompany = null;
let fileRenames = {};  // {原始路径: 重命名后的文件名称}
let manageMode = false;          // 行管理模式开关
let contextMenuTargetRow = null; // 右键菜单目标行
let insertPosition = null;       // 插入位置: "top" | "bottom" | null(末尾)

// ====== 项目管理 ======
let activeProject = null;        // {slug, name, is_dirty: bool}
let autoSaveTimer = null;
const AUTO_SAVE_DELAY = 3000;    // 3秒防抖

// ====== 页面初始化 ======
document.addEventListener("DOMContentLoaded", () => {
    console.log("[DEBUG] DOMContentLoaded: 页面加载完成，开始初始化");
    initTemplatePanel();
    console.log("[DEBUG] DOMContentLoaded: initTemplatePanel 完成");
    initFolderInput();
    initMatchControls();
    initPreviewStatusMenu();
    initLlmPanel();
    initColumnResize('#preview-table');
    initColumnFilters();
    initViewToggle();
    initAssignModal();
    initListPreviewPane();
    initBrowsePreviewPane();
    initProjectBar();
    updateWorkflowState();
    console.log("[DEBUG] DOMContentLoaded: 所有初始化完成");
});

// ====== 步骤0：模板面板 ======
function initTemplatePanel() {
    loadTemplateInfo();

    // 科目下拉多选交互
    const trigger = document.getElementById("subject-select-trigger");
    const dropdown = document.getElementById("subject-select-dropdown");
    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        dropdown.classList.toggle("hidden");
    });
    dropdown.addEventListener("change", () => updateSubjectTriggerText());
    document.addEventListener("click", (e) => {
        if (!dropdown.contains(e.target) && e.target !== trigger) dropdown.classList.add("hidden");
    });

    document.getElementById("gen-checklist-btn").addEventListener("click", generateChecklist);
    document.getElementById("download-template-btn").addEventListener("click", downloadChecklist);
    document.getElementById("re-gen-btn").addEventListener("click", resetTemplate);

    const uploadBtn = document.getElementById("upload-filled-btn");
    const fileInput = document.getElementById("filled-checklist-input");
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        if (fileInput.files.length) uploadFilledChecklist(fileInput.files[0]);
    });

    const uploadBtn2 = document.getElementById("upload-filled-btn-2");
    const fileInput2 = document.getElementById("filled-checklist-input-2");
    uploadBtn2.addEventListener("click", () => fileInput2.click());
    fileInput2.addEventListener("change", () => {
        if (fileInput2.files.length) uploadFilledChecklist(fileInput2.files[0]);
    });
}

function loadTemplateInfo() {
    console.log("[DEBUG] loadTemplateInfo: 开始请求 /api/template-info");
    fetch(API.templateInfo)
        .then(r => {
            console.log("[DEBUG] loadTemplateInfo: 响应状态", r.status);
            return r.json();
        })
        .then(data => {
            console.log("[DEBUG] loadTemplateInfo: 返回数据", data);
            if (data.error) { showToast(data.error, "error"); return; }
            templateData = data;
            console.log("[DEBUG] loadTemplateInfo: 科目列表", data.subjects);
            renderSubjectCheckboxes(data.subjects);
        })
        .catch(err => {
            console.error("[DEBUG] loadTemplateInfo: 请求失败", err);
            showToast("加载模板失败: " + err.message, "error");
        });
}

function renderSubjectCheckboxes(subjects) {
    const dropdown = document.getElementById("subject-select-dropdown");
    dropdown.innerHTML = "";
    subjects.forEach(s => {
        const label = document.createElement("label");
        label.className = "multi-select-item";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = s;
        cb.checked = true;
        label.appendChild(cb);
        label.appendChild(document.createTextNode(s));
        dropdown.appendChild(label);
    });
    updateSubjectTriggerText();
}

function updateSubjectTriggerText() {
    const trigger = document.getElementById("subject-select-trigger");
    const cbs = document.querySelectorAll("#subject-select-dropdown input[type=checkbox]:checked");
    const names = Array.from(cbs).map(cb => cb.value);
    if (names.length === 0) trigger.textContent = "请选择科目";
    else if (names.length <= 2) trigger.textContent = names.join("、");
    else trigger.textContent = `已选 ${names.length} 项`;
}

function getSelectedSubjects() {
    const cbs = document.querySelectorAll("#subject-select-dropdown input[type=checkbox]:checked");
    return Array.from(cbs).map(cb => cb.value);
}

function generateChecklist() {
    const subjects = getSelectedSubjects();
    console.log("[DEBUG] generateChecklist: 选中的科目", subjects);
    if (!subjects.length) { showToast("请至少选择一个科目", "error"); return; }

    console.log("[DEBUG] generateChecklist: 发送请求到 /api/generate-checklist");
    fetch(API.generateChecklist, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subjects }),
    })
    .then(r => {
        console.log("[DEBUG] generateChecklist: 响应状态", r.status);
        return r.json();
    })
    .then(data => {
        console.log("[DEBUG] generateChecklist: 返回数据", data);
        if (data.error) { showToast(data.error, "error"); return; }
        onChecklistGenerated(data, true);
        showToast(`清单已生成，共 ${data.total} 项，请下载后交客户填写`, "success");
    })
    .catch(err => {
        console.error("[DEBUG] generateChecklist: 请求失败", err);
        showToast("生成失败: " + err.message, "error");
    });
}

function onChecklistGenerated(data, isNew) {
    previewItems = data.items;
    previewCompanies = data.companies || [];
    companyNames = data.company_names || [];

    document.getElementById("template-gen-area").classList.add("hidden");
    document.getElementById("template-done").classList.remove("hidden");
    document.getElementById("template-summary").textContent =
        `清单已生成 · ${data.total} 项资料 · ${companyNames.length || 0} 家公司`;
    document.getElementById("template-badge").textContent = "✓ 已生成";

    renderPreviewTable();
    document.getElementById("preview-section").classList.remove("hidden");
    document.getElementById("export-checklist-btn").classList.remove("hidden");
    document.getElementById("toggle-manage-btn").classList.remove("hidden");
    markProjectDirty();
}

function downloadChecklist() {
    window.location.href = API.downloadChecklist;
    showToast("正在下载清单...", "success");
}

function resetTemplate() {
    document.getElementById("template-gen-area").classList.remove("hidden");
    document.getElementById("template-done").classList.add("hidden");
    document.getElementById("template-badge").textContent = "未开始";
    document.getElementById("preview-section").classList.add("hidden");
    document.getElementById("export-checklist-btn").classList.add("hidden");
    previewItems = [];
    previewCompanies = [];
    companyNames = [];
}

function uploadFilledChecklist(file) {
    console.log("[DEBUG] uploadFilledChecklist: 文件名", file.name);
    if (!file.name.endsWith(".xlsx")) { showToast("仅支持.xlsx格式文件", "error"); return; }
    const formData = new FormData();
    formData.append("file", file);
    console.log("[DEBUG] uploadFilledChecklist: 发送请求到 /api/upload-checklist-v2");
    fetch(API.uploadChecklistV2, { method: "POST", body: formData })
        .then(r => {
            console.log("[DEBUG] uploadFilledChecklist: 响应状态", r.status);
            return r.json();
        })
        .then(data => {
            console.log("[DEBUG] uploadFilledChecklist: 返回数据", data);
            if (data.error) { showToast(data.error, "error"); return; }
            onChecklistGenerated(data, false);
            showToast(`清单已加载，共 ${data.total} 项`, "success");
        })
        .catch(err => {
            console.error("[DEBUG] uploadFilledChecklist: 请求失败", err);
            showToast("上传失败: " + err.message, "error");
        });
}

// ====== 资料预览表格 ======
let currentView = "matrix";

function renderPreviewTable() {
    if (!previewItems.length) {
        document.getElementById("preview-section").classList.add("hidden");
        return;
    }
    document.getElementById("preview-section").classList.remove("hidden");

    // 序号重排：按当前顺序重新编号
    previewItems.forEach((item, idx) => {
        item.seq = idx + 1;
    });

    // 直接使用每个 PBC 条目（不合并）
    if (currentView === "matrix") {
        renderMatrixView(previewItems);
    } else if (currentView === "list") {
        renderListView(previewItems);
    } else if (currentView === "browse") {
        renderBrowseView();
    }
    collectUnassignedItems();
    renderUnassignedButton();
    updatePreviewStats();
}

// 按当前预览数据计算统计数字（不合并 PBC）
function updatePreviewStats() {
    if (!previewItems.length) return;
    const total = previewItems.length;
    let matched = 0, incomplete = 0, missing = 0;
    previewItems.forEach(item => {
        const cs = item.company_status || {};
        const vals = Object.values(cs);
        const hasY = vals.includes("Y");
        const hasN = vals.includes("N");
        const hasIncomplete = vals.includes("不完整");
        if (hasY && !hasN && !hasIncomplete) matched++;
        else if (hasIncomplete || (hasY && hasN)) incomplete++;
        else missing++;
    });
    updateStats(matched, total, incomplete);
}


// 收集未确认公司归属的资料（供"需人工确认公司归属"按钮使用）
function collectUnassignedItems() {
    const unassignedItems = [];
    if (matchResults) {
        matchResults.forEach(r => {
            if (r.status === "已获取") {
                const companyCoverage = r.company_coverage || {};
                if (Object.keys(companyCoverage).length === 0 && r.matched_files && r.matched_files.length > 0) {
                    unassignedItems.push(r);
                }
            }
        });
    }
    window._unassignedItems = unassignedItems;
}

// ====== 表头列筛选 ======
function ensureFilterDropdown() {
    let dropdown = document.getElementById('col-filter-dropdown');
    if (dropdown) return dropdown;

    dropdown = document.createElement('div');
    dropdown.id = 'col-filter-dropdown';
    dropdown.className = 'col-filter-dropdown';
    dropdown.innerHTML =
        '<input class="col-filter-search" type="search" placeholder="搜索值">' +
        '<div class="col-filter-actions">' +
            '<button type="button" data-action="select-all">全选</button>' +
            '<button type="button" data-action="clear-all">取消全选</button>' +
        '</div>' +
        '<div class="col-filter-list"></div>' +
        '<div class="col-filter-empty hidden">无匹配项</div>';
    document.body.appendChild(dropdown);

    dropdown.querySelector('.col-filter-search').addEventListener('input', event => {
        filterDropdownSearch(dropdown, event.target.value);
    });
    dropdown.querySelector('[data-action="select-all"]').addEventListener('click', () => {
        setAllCheckboxes(dropdown, true);
    });
    dropdown.querySelector('[data-action="clear-all"]').addEventListener('click', () => {
        setAllCheckboxes(dropdown, false);
    });
    dropdown.querySelector('.col-filter-list').addEventListener('change', event => {
        if (event.target.matches('input[type="checkbox"]')) {
            commitDropdownFilter(dropdown);
        }
    });
    document.addEventListener('click', event => {
        if (!dropdown.contains(event.target) && !event.target.closest('.col-filter-btn')) {
            dropdown.classList.remove('show');
            document.querySelectorAll('.col-filter-btn.open').forEach(button => {
                button.classList.remove('open');
            });
        }
    });
    return dropdown;
}

function showFilterDropdown(th, tableId, colIndex) {
    const dropdown = ensureFilterDropdown();
    const table = document.getElementById(tableId);
    if (!table) return;

    const wasOpen = dropdown.classList.contains('show') &&
        dropdown.dataset.tableId === tableId &&
        Number(dropdown.dataset.colIndex) === colIndex;
    document.querySelectorAll('.col-filter-btn.open').forEach(button => {
        button.classList.remove('open');
    });
    if (wasOpen) {
        dropdown.classList.remove('show');
        return;
    }

    dropdown.dataset.tableId = tableId;
    dropdown.dataset.colIndex = String(colIndex);
    dropdown.querySelector('.col-filter-search').value = '';

    const values = buildColumnValues(table, colIndex);
    const activeFilter = colFilters[tableId + ':' + colIndex];
    const list = dropdown.querySelector('.col-filter-list');
    list.innerHTML = '';

    values.forEach(entry => {
        const item = document.createElement('label');
        item.className = 'col-filter-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.dataset.filterValue = entry.value;
        checkbox.checked = !activeFilter || activeFilter.has(entry.value);

        const label = document.createElement('span');
        label.className = 'col-filter-value';
        label.textContent = entry.value || '(空白)';

        const count = document.createElement('span');
        count.className = 'col-filter-count';
        count.textContent = String(entry.count);

        item.appendChild(checkbox);
        item.appendChild(label);
        item.appendChild(count);
        list.appendChild(item);
    });

    dropdown.classList.add('show');
    filterDropdownSearch(dropdown, '');
    const button = th.querySelector('.col-filter-btn');
    if (button) button.classList.add('open');

    const rect = th.getBoundingClientRect();
    const dropdownWidth = dropdown.offsetWidth;
    const dropdownHeight = dropdown.offsetHeight;
    const left = Math.min(
        Math.max(8, rect.left),
        Math.max(8, window.innerWidth - dropdownWidth - 8)
    );
    let top = rect.bottom + 4;
    if (top + dropdownHeight > window.innerHeight - 8) {
        top = Math.max(8, rect.top - dropdownHeight - 4);
    }
    dropdown.style.left = left + 'px';
    dropdown.style.top = top + 'px';
}

function buildColumnValues(table, colIndex) {
    const counts = new Map();
    table.querySelectorAll('tbody tr').forEach(row => {
        const cell = row.children[colIndex];
        if (!cell) return;
        const value = cell.textContent.trim();
        counts.set(value, (counts.get(value) || 0) + 1);
    });
    return Array.from(counts, ([value, count]) => ({ value, count }))
        .sort((left, right) => left.value.localeCompare(right.value, 'zh-CN'));
}

function applyFilters(tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const prefix = tableId + ':';
    const filters = Object.entries(colFilters)
        .filter(([key]) => key.startsWith(prefix))
        .map(([key, selected]) => ({
            colIndex: Number(key.slice(prefix.length)),
            selected,
        }));

    table.querySelectorAll('tbody tr').forEach(row => {
        const matches = filters.every(filter => {
            const cell = row.children[filter.colIndex];
            return cell && filter.selected.has(cell.textContent.trim());
        });
        row.style.display = matches ? '' : 'none';
    });
    updateAllFilterIndicators(tableId);
}

function commitDropdownFilter(dropdown) {
    const tableId = dropdown.dataset.tableId;
    const colIndex = Number(dropdown.dataset.colIndex);
    const checkboxes = Array.from(
        dropdown.querySelectorAll('.col-filter-list input[type="checkbox"]')
    );
    const selected = new Set(
        checkboxes.filter(checkbox => checkbox.checked)
            .map(checkbox => checkbox.dataset.filterValue)
    );
    const key = tableId + ':' + colIndex;
    if (checkboxes.length > 0 && selected.size === checkboxes.length) {
        delete colFilters[key];
    } else {
        colFilters[key] = selected;
    }
    applyFilters(tableId);
}

function setAllCheckboxes(dropdown, checked) {
    dropdown.querySelectorAll('.col-filter-item:not(.hidden) input[type="checkbox"]')
        .forEach(checkbox => {
            checkbox.checked = checked;
        });
    commitDropdownFilter(dropdown);
}

function filterDropdownSearch(dropdown, query) {
    const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN');
    let visibleCount = 0;
    dropdown.querySelectorAll('.col-filter-item').forEach(item => {
        const value = item.querySelector('.col-filter-value').textContent;
        const visible = value.toLocaleLowerCase('zh-CN').includes(normalizedQuery);
        item.classList.toggle('hidden', !visible);
        if (visible) visibleCount++;
    });
    dropdown.querySelector('.col-filter-empty').classList.toggle('hidden', visibleCount > 0);
}

function updateAllFilterIndicators(tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;
    table.querySelectorAll('.col-filter-btn').forEach(button => {
        const key = tableId + ':' + button.dataset.col;
        button.classList.toggle('active', Object.prototype.hasOwnProperty.call(colFilters, key));
    });
}

function initColumnFilters() {
    ensureFilterDropdown();
    document.addEventListener('click', event => {
        const button = event.target.closest('.col-filter-btn');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();

        const th = button.closest('th');
        const table = button.closest('table');
        if (!th || !table || !table.id) return;
        showFilterDropdown(th, table.id, Number(button.dataset.col));
    });
}

function wrapThWithFilter(th, colIndex) {
    const wrap = document.createElement('div');
    wrap.className = 'th-content-wrap';

    const label = document.createElement('span');
    label.className = 'th-label';
    label.textContent = th.textContent;
    th.textContent = '';

    const button = document.createElement('button');
    button.className = 'col-filter-btn';
    button.dataset.col = String(colIndex);
    button.title = '筛选';
    button.textContent = '▼';

    wrap.appendChild(label);
    wrap.appendChild(button);
    th.appendChild(wrap);
}

function renderMatrixView(displayItems) {
    const thead = document.getElementById("preview-table-head");
    const tbody = document.getElementById("preview-table-body");
    const table = document.getElementById("preview-table");

    // 管理模式样式
    if (manageMode) {
        table.classList.add("manage-mode");
    } else {
        table.classList.remove("manage-mode");
    }

    document.getElementById("preview-table-wrapper").classList.remove("hidden");
    document.getElementById("list-view-wrapper").classList.add("hidden");
    document.getElementById("list-view-container").classList.add("hidden");
    document.getElementById("browse-view-container").classList.add("hidden");
    document.getElementById("browse-view-wrapper").classList.add("hidden");

    let headHtml = "<tr>";
    headHtml += '<th style="width:50px"><div class="th-content-wrap"><span class="th-label">序号</span><button class="col-filter-btn" data-col="0" title="筛选">▼</button></div></th>';
    headHtml += '<th style="width:90px"><div class="th-content-wrap"><span class="th-label">科目</span><button class="col-filter-btn" data-col="1" title="筛选">▼</button></div></th>';
    headHtml += '<th style="width:110px"><div class="th-content-wrap"><span class="th-label">所需PBC</span><button class="col-filter-btn" data-col="2" title="筛选">▼</button></div></th>';
    headHtml += '<th style="width:140px"><div class="th-content-wrap"><span class="th-label">需求资料</span><button class="col-filter-btn" data-col="3" title="筛选">▼</button></div></th>';
    // 公司列：精确测量文本宽度，确保默认完整显示公司简称
    var _measureEl = document.createElement('span');
    _measureEl.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font-size:12px;font-weight:700;font-family:inherit;';
    document.body.appendChild(_measureEl);
    companyNames.forEach((name, index) => {
        const colIndex = 4 + index;
        _measureEl.textContent = name;
        var textWidth = _measureEl.offsetWidth;
        var nameWidth = Math.max(textWidth + 43, 50);
        headHtml += '<th class="company-col" style="width:' + nameWidth + 'px"><div class="th-content-wrap"><span class="th-label">' + name + '</span><button class="col-filter-btn" data-col="' + colIndex + '" title="筛选">▼</button></div></th>';
    });
    document.body.removeChild(_measureEl);
    // 管理模式：操作列头
    if (manageMode) {
        headHtml += '<th class="row-actions-th" style="width:40px"></th>';
    }
    headHtml += "</tr>";
    thead.innerHTML = headHtml;

    let bodyHtml = "";
    displayItems.forEach((item) => {
        const rowIndex = item.row_index;
        const isCustom = item._custom === true;
        const rowClass = isCustom ? ' class="row-custom"' : '';
        const rowCtx = manageMode ? ` oncontextmenu="showRowContextMenu(event, ${rowIndex})"` : '';
        bodyHtml += `<tr data-row-index="${rowIndex}"${rowClass}${rowCtx}>`;
        bodyHtml += `<td>${item.seq ?? ''}</td>`;

        if (manageMode) {
            // 管理模式：可编辑单元格
            bodyHtml += renderEditableCell(rowIndex, "subject", item.subject, false);
            bodyHtml += renderEditableCell(rowIndex, "pbc_name", item.pbc_name, false);
            bodyHtml += renderEditableCell(rowIndex, "demand_name", item.demand_name, true);
        } else {
            bodyHtml += `<td>${item.subject}</td>`;
            bodyHtml += `<td>${item.pbc_name}</td>`;
            bodyHtml += `<td>${item.demand_name}</td>`;
        }

        companyNames.forEach(cName => {
            const status = (item.company_status && item.company_status[cName]) || "";
            bodyHtml += renderStatusCellPreview(rowIndex, cName, status);
        });

        // 管理模式：删除按钮
        if (manageMode) {
            bodyHtml += `<td class="row-actions"><button class="btn-delete-row" onclick="confirmDeleteRow(event, ${rowIndex})" title="删除此行">🗑</button></td>`;
        }

        bodyHtml += "</tr>";
    });

    // 管理模式：底部新增行表单
    if (manageMode) {
        bodyHtml += renderAddRowForm();
    }

    tbody.innerHTML = bodyHtml;
    /* 冻结左侧4列：序号、科目、所需PBC、需求资料（需求资料右边框为冻结分隔线） */
    markFrozenColumns('#preview-table', 4);
    /* 将 th 列宽同步到所有 td，防止 table width:auto 时内容撑开列宽 */
    enforceColumnWidths('#preview-table');
    updateFrozenColumnOffsets('#preview-table');
    initColumnResize('#preview-table');
    applyFilters('preview-table');
}


async function renderBrowseView() {
    const wrapper = document.getElementById("browse-view-wrapper");
    const thead = document.getElementById("browse-view-head");
    const tbody = document.getElementById("browse-view-body");

    // 先等数据返回再切换视图，避免空表格闪烁
    try {
        const response = await fetch(API.browseViewData);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        if (currentView !== "browse") return;

        // 数据就绪后才隐藏其他视图、显示资料浏览视图
        document.getElementById("preview-table-wrapper").classList.add("hidden");
        document.getElementById("list-view-wrapper").classList.add("hidden");
        document.getElementById("list-view-container").classList.add("hidden");
        document.getElementById("browse-view-container").classList.remove("hidden");
        wrapper.classList.remove("hidden");

        thead.innerHTML = "";
        tbody.innerHTML = "";

        const headerRow = document.createElement("tr");
        const seqHeader = document.createElement("th");
        seqHeader.className = "browse-seq-col";
        seqHeader.textContent = "序号";
        headerRow.appendChild(seqHeader);
        wrapThWithFilter(seqHeader, 0);
        const levelNames = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
        for (let level = 1; level <= data.folder_levels; level++) {
            const header = document.createElement("th");
            header.className = "browse-folder-col";
            header.textContent = `${levelNames[level - 1] || level}级文件夹`;
            headerRow.appendChild(header);
            wrapThWithFilter(header, level);
        }
        [
            ["文件名", "browse-filename-col"],
            ["匹配状态", "browse-status-col"],
            ["关联需求", "browse-requirements-col"],
        ].forEach(([text, className], index) => {
            const header = document.createElement("th");
            header.className = className;
            header.textContent = text;
            headerRow.appendChild(header);
            wrapThWithFilter(header, data.folder_levels + index + 1);
        });
        thead.appendChild(headerRow);

        tbody.innerHTML = "";
        const columnCount = data.folder_levels + 4;
        if (!data.items.length) {
            tbody.innerHTML = `<tr><td colspan="${columnCount}" class="browse-view-loading">请先扫描目标文件夹</td></tr>`;
            return;
        }

        data.items.forEach(item => {
            const row = document.createElement("tr");
            row.className = item.is_matched ? "browse-row-matched" : "browse-row-unmatched";

            const seqCell = document.createElement("td");
            seqCell.className = "browse-seq-col";
            seqCell.textContent = item.seq;
            row.appendChild(seqCell);

            for (let level = 0; level < data.folder_levels; level++) {
                const folderCell = document.createElement("td");
                folderCell.textContent = item.folder_parts[level] || "";
                folderCell.title = folderCell.textContent;
                row.appendChild(folderCell);
            }

            const filenameCell = document.createElement("td");
            const fileLink = document.createElement("a");
            fileLink.href = "#";
            fileLink.textContent = item.filename;
            fileLink.title = item.path;
            fileLink.addEventListener("click", event => {
                event.preventDefault();
                previewFile(item.path, false);
            });
            filenameCell.appendChild(fileLink);
            row.appendChild(filenameCell);

            const statusCell = document.createElement("td");
            statusCell.textContent = item.is_matched ? "已匹配" : "未匹配";
            row.appendChild(statusCell);

            const requirementCell = document.createElement("td");
            const requirementText = document.createElement("span");
            requirementText.textContent = item.matched_requirements.length
                ? item.matched_requirements.map(requirement =>
                    `第${requirement.index}项 ${requirement.checklist_name}`
                ).join("、")
                : "暂无关联需求";
            requirementCell.appendChild(requirementText);

            const assignButton = document.createElement("button");
            assignButton.type = "button";
            assignButton.className = "browse-view-assign-btn";
            assignButton.textContent = item.is_matched ? "继续分配" : "分配";
            assignButton.addEventListener("click", () => {
                showMatrixAssignModal(item.path, item.is_dir, item.filename);
            });
            requirementCell.appendChild(assignButton);
            row.appendChild(requirementCell);
            tbody.appendChild(row);
        });
        initColumnResize('#browse-view-table');
        applyFilters('browse-view-table');
    } catch (error) {
        if (currentView !== "browse") return;
        // 请求失败时也要显示容器，否则错误信息不可见
        document.getElementById("preview-table-wrapper").classList.add("hidden");
        document.getElementById("list-view-wrapper").classList.add("hidden");
        document.getElementById("list-view-container").classList.add("hidden");
        document.getElementById("browse-view-container").classList.remove("hidden");
        wrapper.classList.remove("hidden");
        thead.innerHTML = "";
        tbody.innerHTML = '<tr><td class="browse-view-loading">资料加载失败</td></tr>';
        showToast("资料浏览视图加载失败: " + error.message, "error");
    }
}
function renderListView(displayItems) {
    const thead = document.getElementById("list-view-head");
    const tbody = document.getElementById("list-view-body");
    document.getElementById("preview-table-wrapper").classList.add("hidden");
    document.getElementById("list-view-wrapper").classList.remove("hidden");
    document.getElementById("list-view-container").classList.remove("hidden");
    document.getElementById("browse-view-container").classList.add("hidden");
    document.getElementById("browse-view-wrapper").classList.add("hidden");

    thead.innerHTML = '<tr>' +
        '<th><div class="th-content-wrap"><span class="th-label">科目</span><button class="col-filter-btn" data-col="0" title="筛选">▼</button></div></th>' +
        '<th><div class="th-content-wrap"><span class="th-label">所需PBC</span><button class="col-filter-btn" data-col="1" title="筛选">▼</button></div></th>' +
        '<th><div class="th-content-wrap"><span class="th-label">需求资料</span><button class="col-filter-btn" data-col="2" title="筛选">▼</button></div></th>' +
        '<th><div class="th-content-wrap"><span class="th-label">公司</span><button class="col-filter-btn" data-col="3" title="筛选">▼</button></div></th>' +
        '<th><div class="th-content-wrap"><span class="th-label">是否获取</span><button class="col-filter-btn" data-col="4" title="筛选">▼</button></div></th>' +
        '<th><div class="th-content-wrap"><span class="th-label">文件</span><button class="col-filter-btn" data-col="5" title="筛选">▼</button></div></th>' +
        '</tr>';

    let bodyHtml = "";

    displayItems.forEach((item) => {
        companyNames.forEach(cName => {
            const status = (item.company_status && item.company_status[cName]) || "N";

            let fileCell = "";
            if (status === "Y" && matchResults) {
                const ri = item.row_index;
                const matchedResult = matchResults.find(r => r.index === ri);
                const files = [];
                const seen = new Set();

                if (matchedResult && matchedResult.matched_files && matchedResult.matched_files.length) {
                    const companyCoverage = matchedResult.company_coverage || {};
                    const companyInfo = companyCoverage[cName];

                    if (companyInfo) {
                        const companyFiles = companyInfo.files || [];
                        const companyFolders = companyInfo.folders || [];

                        companyFolders.forEach(fp => {
                            if (seen.has(fp)) return;
                            seen.add(fp);
                            const name = fp.split(/[\\/]/).pop();
                            const companySubPath = fp + "\\" + cName;
                            files.push({
                                name: name,
                                path: fp,
                                isDir: true,
                                hasCompanySub: true,
                                companySubPath: companySubPath,
                            });
                        });

                        companyFiles.forEach(fp => {
                            if (seen.has(fp)) return;
                            seen.add(fp);
                            const name = fp.split(/[\\/]/).pop();
                            files.push({ name, path: fp, isDir: false });
                        });
                    } else {
                        matchedResult.matched_files.forEach((fp, fi) => {
                            if (seen.has(fp)) return;
                            seen.add(fp);
                            const isDir = matchedResult.matched_types && matchedResult.matched_types[fi] === "文件夹";
                            const name = matchedResult.matched_names[fi] || fp.split(/[\\/]/).pop();
                            files.push({ name, path: fp, isDir });
                        });
                    }
                }

                fileCell = files.map(f => {
                    const escapedPath = f.path.replace(/\\/g, '\\\\');
                    const displayName = fileRenames[f.path] || f.name;
                    if (f.isDir && f.hasCompanySub) {
                        return `<a href="#" onclick="previewFile('${f.companySubPath.replace(/\\/g, '\\\\')}', true); return false;" oncontextmenu="event.preventDefault(); renameFilePrompt('${escapedPath}', this, event); return false;" title="${displayName} / ${cName}">${displayName} / ${cName}</a>`;
                    } else if (f.isDir) {
                        return `<a href="#" onclick="previewFile('${escapedPath}', true); return false;" oncontextmenu="event.preventDefault(); renameFilePrompt('${escapedPath}', this, event); return false;" title="${displayName}">${displayName}</a>`;
                    } else {
                        return `<a href="#" onclick="previewFile('${escapedPath}', false); return false;" oncontextmenu="event.preventDefault(); renameFilePrompt('${escapedPath}', this, event); return false;" title="${displayName}">${displayName}</a>`;
                    }
                }).join("<br>");
            }

            bodyHtml += `<tr>`;
            bodyHtml += `<td>${item.subject}</td>`;
            bodyHtml += `<td>${item.pbc_name}</td>`;
            bodyHtml += `<td>${item.demand_name}</td>`;
            bodyHtml += `<td>${cName}</td>`;
            bodyHtml += renderStatusCellPreview(item.row_index, cName, status);
            bodyHtml += `<td>${fileCell}</td>`;
            bodyHtml += `</tr>`;
        });
    });

    tbody.innerHTML = bodyHtml;
    initColumnResize('#list-view-table');
    applyFilters('list-view-table');
}

function renderUnassignedButton() {
    const unassignedItems = window._unassignedItems || [];
    const panelActions = document.querySelector('#preview-section .panel-actions');
    if (!panelActions) return;

    // 移除旧的按钮
    const oldBtn = panelActions.querySelector('.unassigned-btn');
    if (oldBtn) oldBtn.remove();

    if (unassignedItems.length > 0) {
        const btn = document.createElement('button');
        btn.className = 'btn btn-warning btn-sm unassigned-btn';
        btn.innerHTML = '⚠️ 需人工确认公司归属 (' + unassignedItems.length + '项)';
        btn.onclick = showUnassignedModal;

        // 插入到 export-checklist-btn 之前
        const exportBtn = document.getElementById('export-checklist-btn');
        if (exportBtn && exportBtn.parentNode === panelActions) {
            panelActions.insertBefore(btn, exportBtn);
        } else {
            panelActions.appendChild(btn);
        }
    }
}

// 文件重命名（右键菜单触发）
function renameFilePrompt(originalPath, anchorEl, event) {
    event.preventDefault();
    event.stopPropagation();
    const currentName = fileRenames[originalPath] || originalPath.split(/[\\/]/).pop();
    const newName = prompt("重命名文件:", currentName);
    if (newName && newName.trim() && newName.trim() !== currentName) {
        fileRenames[originalPath] = newName.trim();
        anchorEl.textContent = newName.trim();
        anchorEl.title = newName.trim();
        showToast("已重命名: " + newName.trim(), "success");
        markProjectDirty();
    }
}

let assignModalIndex = null;
let assignModalPath = null;
let assignModalIsDir = false;

function showAssignModal(index, filePath, isDir, fileName) {
    // 兼容2参数调用：showAssignModal(filePath, isDir)
    if (arguments.length <= 2) {
        filePath = index;
        isDir = filePath;
        index = null;
        fileName = filePath.split(/[\\/]/).pop();
    } else if (!fileName) {
        fileName = filePath.split(/[\\/]/).pop();
    }

    assignModalIndex = index;
    assignModalPath = filePath;
    assignModalIsDir = isDir;

    document.getElementById('assign-modal-filename').textContent = fileName;
    const companyList = document.getElementById('assign-company-list');
    companyList.innerHTML = companyNames.map(cName => `
        <label class="assign-company-item">
            <input type="checkbox" value="${cName}" class="assign-checkbox"> ${cName}
        </label>
    `).join('');

    document.getElementById('assign-modal').classList.remove('hidden');
}

function hideAssignModal() {
    document.getElementById('assign-modal').classList.add('hidden');
    assignModalIndex = null;
    assignModalPath = null;
    assignModalIsDir = false;
}

function initAssignModal() {
    document.getElementById('assign-cancel-btn').addEventListener('click', hideAssignModal);
    document.getElementById('assign-select-all').addEventListener('click', () => {
        document.querySelectorAll('.assign-checkbox').forEach(cb => cb.checked = true);
    });
    document.getElementById('assign-deselect-all').addEventListener('click', () => {
        document.querySelectorAll('.assign-checkbox').forEach(cb => cb.checked = false);
    });
    document.getElementById('assign-confirm-btn').addEventListener('click', () => {
        const checkboxes = document.querySelectorAll('.assign-checkbox:checked');
        const selectedCompanies = Array.from(checkboxes).map(cb => cb.value);
        if (selectedCompanies.length === 0) {
            showToast("请至少选择一个公司", "error");
            return;
        }
        assignCompanies(assignModalIndex, assignModalPath, assignModalIsDir, selectedCompanies);
        hideAssignModal();
    });
    document.getElementById('assign-modal').addEventListener('click', (e) => {
        if (e.target === document.getElementById('assign-modal')) hideAssignModal();
    });
}

function assignCompanies(index, filePath, isDir, selectedCompanies) {
    fetch(API.manualMatch, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: index, file_path: filePath }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast(data.error, "error"); return; }

        const matchedResult = matchResults.find(r => r.index === index);
        if (matchedResult) {
            if (!matchedResult.company_coverage) matchedResult.company_coverage = {};
            selectedCompanies.forEach(cName => {
                if (!matchedResult.company_coverage[cName]) {
                    matchedResult.company_coverage[cName] = {"files": [], "folders": []};
                }
                if (isDir) {
                    matchedResult.company_coverage[cName]["folders"].push(filePath);
                } else {
                    matchedResult.company_coverage[cName]["files"].push(filePath);
                }
            });

            const item = previewItems[index - 1];
            if (item) {
                companyNames.forEach(cName => {
                    if (!item.company_status) item.company_status = {};
                    item.company_status[cName] = selectedCompanies.includes(cName) ? "Y" : "N";
                });
            }
        }

        renderPreviewTable();
        updateStats(data.matched_count, data.total);
        showToast(`已将资料分配给: ${selectedCompanies.join(', ')}`, "success");
        // 如果弹窗仍在则关闭
        const overlay = document.querySelector('.modal-overlay');
        if (overlay) overlay.remove();
    })
    .catch(err => showToast("分配失败: " + err.message, "error"));
}

function initViewToggle() {
    document.querySelectorAll(".view-toggle-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".view-toggle-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentView = btn.dataset.view;
            renderPreviewTable();
        });
    });
}

function renderStatusCellPreview(rowIndex, companyName, status) {
    let cls = "cell-status";
    if (status === "Y") cls += " cell-yes";
    else if (status === "不完整") cls += " cell-incomplete";
    else if (status === "N/A") cls += " cell-na";
    else cls += " cell-no";
    const displayText = status || "N";
    return `<td class="${cls}" oncontextmenu="showPreviewStatusMenu(event, '${rowIndex}', '${companyName}', this)">${displayText}</td>`;
}

function cycleCellStatus(rowIndex, companyName, cell) {
    const indices = String(rowIndex).split(',').map(s => parseInt(s.trim(), 10));

    const current = cell.textContent.trim();
    let next;
    if (current === "N" || current === "") next = "Y";
    else if (current === "Y") next = "不完整";
    else if (current === "不完整") next = "N/A";
    else if (current === "N/A") next = "N";
    else next = "Y";

    cell.textContent = next;
    cell.className = "cell-status";
    if (next === "Y") cell.classList.add("cell-yes");
    else if (next === "不完整") cell.classList.add("cell-incomplete");
    else if (next === "N/A") cell.classList.add("cell-na");
    else cell.classList.add("cell-no");

    // 同步到后端和内存
    indices.forEach(idx => {
        fetch(API.updateCellStatus, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ row_index: idx, company_name: companyName, status: next }),
        }).catch(err => showToast("更新失败: " + err.message, "error"));

        // 同步内存
        const item = previewItems.find(it => it.row_index === idx);
        if (item) {
            if (!item.company_status) item.company_status = {};
            item.company_status[companyName] = next;
        }
    });
    markProjectDirty();
}

// ====== 行管理模式 ======

function toggleManageMode() {
    manageMode = !manageMode;
    const toggleBtn = document.getElementById("toggle-manage-btn");
    const addBtn = document.getElementById("add-row-btn");

    if (manageMode) {
        toggleBtn.textContent = "✓ 退出编辑";
        toggleBtn.classList.add("btn-accent");
        addBtn.classList.remove("hidden");
    } else {
        toggleBtn.textContent = "✏ 编辑行";
        toggleBtn.classList.remove("btn-accent");
        addBtn.classList.add("hidden");
        // 隐藏右键菜单
        const menu = document.getElementById("row-context-menu");
        if (menu) menu.classList.add("hidden");
    }
    renderPreviewTable();
}

// 渲染可编辑单元格（管理模式用）
function renderEditableCell(rowIndex, field, value, isLastFrozen) {
    const cls = isLastFrozen ? "frozen-col frozen-col-last editable-cell" : "frozen-col editable-cell";
    const escaped = escapeHtml(value || "");
    return `<td class="${cls}" data-row="${rowIndex}" data-field="${field}"
                ondblclick="startInlineEdit(this)" onblur="saveInlineEdit(this)"
                title="双击编辑">${escaped}</td>`;
}

function startInlineEdit(td) {
    if (!manageMode) return;
    td.contentEditable = "true";
    td.classList.add("editing");
    td.focus();
    // 选中全部文本
    const range = document.createRange();
    range.selectNodeContents(td);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
}

async function saveInlineEdit(td) {
    td.contentEditable = "false";
    td.classList.remove("editing");

    const rowIndex = parseInt(td.dataset.row);
    const field = td.dataset.field;
    const newValue = td.textContent.trim();
    const item = previewItems.find(it => it.row_index === rowIndex);
    if (!item || item[field] === newValue) return;

    try {
        const resp = await fetch(API.editRow, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ row_index: rowIndex, field: field, value: newValue })
        });
        const data = await resp.json();
        if (data.success) {
            item[field] = newValue;
            showToast("已保存", "success");
            markProjectDirty();
        } else {
            td.textContent = item[field] || ""; // 还原
            showToast(data.error || "保存失败", "error");
        }
    } catch (e) {
        td.textContent = item[field] || ""; // 还原
        showToast("网络错误", "error");
    }
}

// 渲染新增行表单
function renderAddRowForm() {
    if (!manageMode) return "";
    // 收集现有科目列表（去重）
    const subjects = [...new Set(previewItems.map(it => it.subject))].filter(Boolean);
    const numCols = 4 + companyNames.length; // 总列数

    let html = '<tr class="add-row-form">';
    html += '<td class="frozen-col" style="text-align:center;color:var(--accent);font-weight:bold;">+</td>';

    // 科目下拉
    html += '<td class="frozen-col"><select class="inline-select" id="new-subject">';
    html += '<option value="">选择科目...</option>';
    subjects.forEach(s => { html += '<option value="' + escapeHtml(s) + '">' + escapeHtml(s) + '</option>'; });
    html += '<option value="__new__">+ 新科目...</option>';
    html += '</select></td>';

    // PBC名称输入
    html += '<td class="frozen-col"><input class="inline-input" id="new-pbc" placeholder="所需PBC名称" /></td>';

    // 需求资料输入
    html += '<td class="frozen-col frozen-col-last"><input class="inline-input" id="new-demand" placeholder="需求资料（默认同PBC）" /></td>';

    // 公司列占位
    companyNames.forEach(() => { html += '<td style="text-align:center;color:#999;">-</td>'; });

    // 操作按钮
    html += '<td class="row-actions">';
    html += '<button class="btn-confirm-row" onclick="submitNewRow()" title="确认新增">✅</button>';
    html += '<button class="btn-cancel-row" onclick="cancelNewRow()" title="取消">❌</button>';
    html += '</td>';
    html += '</tr>';
    return html;
}

async function submitNewRow() {
    const subjectEl = document.getElementById("new-subject");
    const pbcEl = document.getElementById("new-pbc");
    const demandEl = document.getElementById("new-demand");

    let subject = subjectEl ? subjectEl.value.trim() : "";
    const pbcName = pbcEl ? pbcEl.value.trim() : "";
    const demandName = (demandEl ? demandEl.value.trim() : "") || pbcName;

    // 处理新科目
    if (subject === "__new__") {
        subject = prompt("请输入新科目名称：");
        if (!subject || !subject.trim()) return;
        subject = subject.trim();
    }

    if (!subject || !pbcName) {
        showToast("科目和PBC名称不能为空", "error");
        return;
    }

    // 检查是否指定了插入位置
    let position = insertPosition;
    let refRowIndex = contextMenuTargetRow;

    try {
        const resp = await fetch(API.addRow, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subject, pbc_name: pbcName, demand_name: demandName, position: position, ref_row_index: refRowIndex })
        });
        const data = await resp.json();
        if (data.success) {
            previewItems.push(data.item);
            contextMenuTargetRow = null;
            insertPosition = null;
            renderPreviewTable();
            showToast("新增行成功", "success");
            markProjectDirty();
        } else {
            showToast(data.error || "新增失败", "error");
        }
    } catch (e) {
        showToast("网络错误: " + e.message, "error");
    }
}

function cancelNewRow() {
    contextMenuTargetRow = null;
    insertPosition = null;
    renderPreviewTable();
}

async function confirmDeleteRow(event, rowIndex) {
    event.stopPropagation();
    if (!confirm("确定要删除此行吗？此操作不可撤销。")) return;

    try {
        const resp = await fetch(API.deleteRow, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ row_index: rowIndex })
        });
        const data = await resp.json();
        if (data.success) {
            previewItems = previewItems.filter(it => it.row_index !== rowIndex);
            renderPreviewTable();
            showToast("已删除", "success");
            markProjectDirty();
        } else {
            showToast(data.error || "删除失败", "error");
        }
    } catch (e) {
        showToast("网络错误: " + e.message, "error");
    }
}

// ====== 行级右键菜单 ======

function showRowContextMenu(event, rowIndex) {
    if (!manageMode) return;
    event.preventDefault();
    event.stopPropagation();
    contextMenuTargetRow = rowIndex;

    const menu = document.getElementById("row-context-menu");
    if (!menu) return;
    menu.style.top = event.clientY + "px";
    menu.style.left = event.clientX + "px";
    menu.classList.remove("hidden");

    // 点击其他地方关闭
    setTimeout(() => {
        document.addEventListener("click", hideRowContextMenu, { once: true });
    }, 0);
}

function hideRowContextMenu() {
    const menu = document.getElementById("row-context-menu");
    if (menu) menu.classList.add("hidden");
    // 不重置 contextMenuTargetRow，它用于 insert 操作
}

function insertRowAbove() {
    insertPosition = "top";
    hideRowContextMenu();
    showToast("请在下方\"新增行\"表单中填入内容，将插入到该行上方", "info");
    const addForm = document.querySelector(".add-row-form");
    if (addForm) addForm.scrollIntoView({ behavior: "smooth" });
}

function insertRowBelow() {
    insertPosition = "bottom";
    hideRowContextMenu();
    showToast("请在下方\"新增行\"表单中填入内容，将插入到该行下方", "info");
    const addForm = document.querySelector(".add-row-form");
    if (addForm) addForm.scrollIntoView({ behavior: "smooth" });
}

function deleteRowViaMenu() {
    const rowIndex = contextMenuTargetRow;
    hideRowContextMenu();
    if (rowIndex) {
        confirmDeleteRow({ stopPropagation: function(){} }, rowIndex);
    }
}

// ====== 预览区状态右键菜单 ======
function showPreviewStatusMenu(event, rowIndex, companyName, cell) {
    event.preventDefault();
    event.stopPropagation();
    const menu = document.getElementById("preview-status-menu");
    if (!menu) return;
    previewStatusCell = cell;
    previewStatusRowIndex = rowIndex;
    previewStatusCompany = companyName;
    menu.classList.remove("hidden");
    const left = Math.min(event.clientX, window.innerWidth - 170);
    const top = Math.min(event.clientY, window.innerHeight - 160);
    menu.style.left = Math.max(8, left) + "px";
    menu.style.top = Math.max(8, top) + "px";
}

function hidePreviewStatusMenu() {
    const menu = document.getElementById("preview-status-menu");
    if (menu) menu.classList.add("hidden");
    previewStatusCell = null;
    previewStatusRowIndex = null;
    previewStatusCompany = null;
}

function setCellStatus(rowIndex, companyName, cell, newStatus) {
    const indices = String(rowIndex).split(',').map(s => parseInt(s.trim(), 10));

    cell.textContent = newStatus;
    cell.className = "cell-status";
    if (newStatus === "Y") cell.classList.add("cell-yes");
    else if (newStatus === "不完整") cell.classList.add("cell-incomplete");
    else if (newStatus === "N/A") cell.classList.add("cell-na");
    else cell.classList.add("cell-no");

    indices.forEach(idx => {
        fetch(API.updateCellStatus, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ row_index: idx, company_name: companyName, status: newStatus }),
        }).catch(err => showToast("更新失败: " + err.message, "error"));

        const item = previewItems.find(it => it.row_index === idx);
        if (item) {
            if (!item.company_status) item.company_status = {};
            item.company_status[companyName] = newStatus;
        }
    });

    updatePreviewStats();
    markProjectDirty();
}

function initPreviewStatusMenu() {
    const menu = document.getElementById("preview-status-menu");
    if (!menu) return;
    menu.addEventListener("click", e => {
        const btn = e.target.closest("button[data-status]");
        if (!btn || !previewStatusCell) return;
        const newStatus = btn.dataset.status;
        setCellStatus(previewStatusRowIndex, previewStatusCompany, previewStatusCell, newStatus);
        hidePreviewStatusMenu();
    });
    document.addEventListener("click", (e) => {
        if (!e.target.closest("#preview-status-menu")) {
            hidePreviewStatusMenu();
        }
    });
    document.addEventListener("scroll", hidePreviewStatusMenu, true);
}

// ====== 文件夹扫描 ======
function initFolderInput() {
    document.getElementById("scan-btn").addEventListener("click", scanFolder);
}

function scanFolder() {
    const folderPath = document.getElementById("folder-path").value.trim();
    if (!folderPath) { showToast("请输入文件夹路径", "error"); return; }
    fetch(API.scanFolder, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_path: folderPath }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast(data.error, "error"); return; }
        scannedCount = data.scanned_count;
        scanRoot = data.root_path || folderPath;
        document.getElementById("folder-badge").textContent = `✓ ${data.scanned_count}个文件`;

        if (data.results && data.results.length) {
            matchResults = data.results;
            syncMatchToPreview();
            renderPreviewTable();
            document.getElementById("export-checklist-btn").classList.remove("hidden");
            document.getElementById("organize-files-btn")?.classList.remove("hidden");
            document.getElementById("toggle-manage-btn").classList.remove("hidden");
        }
        updateWorkflowState();
        showToast(`已扫描 ${data.scanned_count} 个文件`, "success");
        markProjectDirty();
    })
    .catch(err => showToast("扫描失败: " + err.message, "error"));
}

// 将匹配结果同步到预览区
function syncMatchToPreview() {
    if (!matchResults || !previewItems.length) return;
    matchResults.forEach(r => {
        const item = previewItems[r.index - 1];
        if (!item) return;
        if (r.status === "已获取") {
            // 检查是否有公司覆盖信息
            const companyCoverage = r.company_coverage || {};
            if (Object.keys(companyCoverage).length > 0) {
                // 有公司覆盖信息，只标记有资料的公司
                companyNames.forEach(cName => {
                    if (!item.company_status) item.company_status = {};
                    if (companyCoverage[cName]) {
                        // 该公司有资料
                        if (!item.company_status[cName] || item.company_status[cName] !== "N/A") {
                            item.company_status[cName] = "Y";
                        }
                    } else {
                        // 该公司没有资料
                        if (!item.company_status[cName] || item.company_status[cName] === "Y") {
                            item.company_status[cName] = "N";
                        }
                    }
                });
            } else {
                // 没有公司覆盖信息，无法确定公司归属，标记为N
                companyNames.forEach(cName => {
                    if (!item.company_status) item.company_status = {};
                    if (!item.company_status[cName] || item.company_status[cName] === "Y") {
                        item.company_status[cName] = "N";
                    }
                });
            }
        } else {
            // 未匹配 → 标记N
            companyNames.forEach(cName => {
                if (!item.company_status) item.company_status = {};
                if (!item.company_status[cName] || item.company_status[cName] === "Y") {
                    item.company_status[cName] = "N";
                }
            });
        }
    });

    // 同步到后端Excel
    previewItems.forEach(item => {
        companyNames.forEach(cName => {
            const status = item.company_status ? item.company_status[cName] : "N";
            if (status) {
                fetch(API.updateCellStatus, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ row_index: item.row_index, company_name: cName, status }),
                }).catch(() => {});
            }
        });
    });
}

// ====== 匹配 ======
function initMatchControls() {
    document.getElementById("match-btn").addEventListener("click", doMatch);
    document.getElementById("export-checklist-btn").addEventListener("click", exportChecklist);
    const orgBtn = document.getElementById("organize-files-btn");
    if (orgBtn) orgBtn.addEventListener("click", organizeFiles);
    document.getElementById("toggle-manage-btn").addEventListener("click", toggleManageMode);
    document.getElementById("add-row-btn").addEventListener("click", () => { renderPreviewTable(); });
}

async function doMatch() {
    if (!previewItems.length) { showToast("请先生成或上传PBC需求清单", "error"); return; }
    if (!scannedCount) { showToast("请先扫描目标文件夹", "error"); return; }

    try {
        const r = await fetch(API.match, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ incremental: false }),
        });
        const data = await r.json();
        if (data.error) { showToast(data.error, "error"); return; }
        matchResults = data.results;
        scanRoot = data.root_path || scanRoot;
        syncMatchToPreview();
        renderPreviewTable();
        updateStats(data.matched_count, data.total, data.partial_count);
        document.getElementById("export-checklist-btn").classList.remove("hidden");
        document.getElementById("organize-files-btn")?.classList.remove("hidden");
        document.getElementById("toggle-manage-btn").classList.remove("hidden");
        updateWorkflowState();

        if (document.getElementById("llm-enabled").checked) {
            await runLlmMatch();
        } else {
            showToast(`匹配完成: ${data.matched_count}/${data.total} 已获取`, "success");
        }
        markProjectDirty();
    } catch (err) {
        showToast("匹配失败: " + err.message, "error");
    }
}

// ====== 导出 ======
async function exportChecklist() {
    showToast("正在导出清单...", "success");

    // 收集当前所有预览项的 company_status，确保导出使用核对后的结果
    const companyStatuses = {};
    previewItems.forEach(item => {
        if (item.company_status) {
            companyStatuses[item.row_index] = item.company_status;
        }
    });

    try {
        const r = await fetch(API.exportChecklist, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ company_statuses: companyStatuses, file_renames: fileRenames }),
        });
        if (!r.ok) {
            const err = await r.json();
            showToast(err.error || "导出失败", "error");
            return;
        }
        // 获取 blob 并触发下载
        const blob = await r.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "PBC需求清单.xlsx";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        showToast("导出完成", "success");
    } catch (err) {
        showToast("导出失败: " + err.message, "error");
    }
}


// ====== 文件整理 ======
async function organizeFiles() {
    showDirectoryPicker(async (targetPath) => {
        if (!targetPath) {
            showToast("已取消整理", "info");
            return;
        }

        if (!confirm(`确定要整理已获取的匹配文件吗？\n\n文件将整理到：${targetPath}\n按「科目/需求资料」层级创建文件夹。`)) {
            return;
        }

        try {
            const r = await fetch(API.organizeFiles, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_path: targetPath, file_renames: fileRenames }),
            });
            const data = await r.json();
            if (data.error) {
                showToast(data.error, "error");
                return;
            }

            if (data.organized_count > 0) {
                showToast(`整理完成: ${data.organized_count} 个文件已整理到「${data.target_root}」`, "success");
            }
            if (data.error_count > 0) {
                showToast(`${data.error_count} 个文件整理失败`, "error");
            }
            if (data.organized_count === 0 && data.error_count === 0) {
                showToast("没有需要整理的文件", "info");
            }
        } catch (err) {
            showToast("整理失败: " + err.message, "error");
        }
    }, "");
}

// ====== Windows 风格文件夹选择对话框 ======
let _dirPickerCallback = null;
let _dirPickerCurrentPath = "";

function showDirectoryPicker(callback, initialPath) {
    _dirPickerCallback = callback;

    // 移除已有弹窗
    const existing = document.querySelector('.folder-browser-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.className = "folder-browser-overlay modal-overlay";

    const modal = document.createElement("div");
    modal.className = "modal folder-browser-modal";

    // 初始化常用路径
    const quickAccess = [
        { name: "桌面", path: getDesktopPath(), icon: "🖥️" },
        { name: "文档", path: getDocumentsPath(), icon: "📄" },
        { name: "下载", path: getDownloadsPath(), icon: "⬇️" },
    ];

    modal.innerHTML = `
        <!-- 标题栏 -->
        <div class="folder-browser-header">
            <div>
                <h3><span style="font-size:18px;">📁</span> 选择目标文件夹</h3>
                <p style="margin:3px 0 0 26px;font-size:11px;color:#888;">双击进入子文件夹，然后点击右下角「✓ 确定」选中当前文件夹</p>
            </div>
            <button class="fb-win-close-btn" id="dir-picker-close-btn" title="关闭">✕</button>
        </div>

        <!-- 地址栏 -->
        <div class="folder-browser-address-bar">
            <span class="fb-addr-label">地址：</span>
            <input type="text" class="fb-addr-input" id="dir-picker-addr" placeholder="输入或粘贴路径后按回车跳转…">
            <button class="btn-win fb-addr-goto-btn" id="dir-picker-goto">转到</button>
        </div>

        <!-- 双栏主体 -->
        <div class="fb-win-main">
            <!-- 左侧导航栏 -->
            <div class="fb-win-sidebar" id="dir-picker-sidebar">
                <div class="fb-sidebar-section" id="fb-quick-section">
                    <div class="fb-sidebar-title">快速访问</div>
                    <div id="fb-quick-list">
                        ${quickAccess.map(q => `
                            <div class="fb-sidebar-item" data-path="${q.path.replace(/\\/g, '\\\\')}" onclick="event.stopPropagation(); _loadDirPicker('${q.path.replace(/\\/g, '\\\\')}'); _selectSidebarItem(this);">
                                <span class="fb-sidebar-icon">${q.icon}</span>
                                <span class="fb-sidebar-name">${q.name}</span>
                            </div>
                        `).join("")}
                    </div>
                </div>
                <div class="fb-sidebar-section" id="fb-drives-section">
                    <div class="fb-sidebar-title">此电脑</div>
                    <div id="fb-drives-list">
                        <div class="fb-sidebar-item" style="color:#999;">加载中…</div>
                    </div>
                </div>
            </div>

            <!-- 右侧文件夹内容 -->
            <div class="fb-win-content">
                <div class="fb-content-header">
                    <span class="fb-col-name">名称</span>
                    <span class="fb-col-date">修改日期</span>
                    <span class="fb-col-type">类型</span>
                </div>
                <div class="folder-browser-list" id="dir-picker-list"></div>
            </div>
        </div>

        <!-- 底部操作栏 -->
        <div class="folder-browser-actions">
            <div class="fb-actions-left">
                <button class="btn-win btn-win-newfolder" id="dir-picker-newfolder">📁 新建文件夹</button>
            </div>
            <div class="fb-actions-right">
                <span class="fb-selected-path" id="dir-picker-selected-path"></span>
                <button class="btn-win" id="dir-picker-cancel">取消</button>
                <button class="btn-win btn-win-primary" id="dir-picker-confirm" style="font-size:14px;padding:8px 32px;">✓ 确定</button>
            </div>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // 初始化驱动器和目录
    initDrivesList();
    _loadDirPicker(initialPath || "");

    // ---- 事件绑定 ----
    document.getElementById("dir-picker-close-btn").addEventListener("click", () => closeDirectoryPicker());
    document.getElementById("dir-picker-cancel").addEventListener("click", () => closeDirectoryPicker());
    document.getElementById("dir-picker-confirm").addEventListener("click", () => {
        const path = _dirPickerCurrentPath || "";
        const cb = _dirPickerCallback;  // 先保存回调引用，因为 closeDirectoryPicker 会清掉它
        closeDirectoryPicker();
        if (cb) cb(path);
    });
    // 点击覆盖层关闭
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeDirectoryPicker();
    });
    // ESC 关闭
    const escHandler = (e) => { if (e.key === "Escape") { closeDirectoryPicker(); document.removeEventListener("keydown", escHandler); } };
    document.addEventListener("keydown", escHandler);
    // 地址栏跳转
    const addrInput = document.getElementById("dir-picker-addr");
    const gotoBtn = document.getElementById("dir-picker-goto");
    const doGoto = () => {
        const p = addrInput.value.trim();
        if (p) _loadDirPicker(p);
    };
    gotoBtn.addEventListener("click", doGoto);
    addrInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); doGoto(); }
    });
    // 新建文件夹
    document.getElementById("dir-picker-newfolder").addEventListener("click", () => {
        const name = prompt("请输入新文件夹名称：");
        if (!name || !name.trim()) return;
        fetch(API.createFolder, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ parent_path: _dirPickerCurrentPath, folder_name: name.trim() }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) { showToast(data.error, "error"); return; }
            showToast("文件夹已创建", "success");
            _loadDirPicker(data.path || _dirPickerCurrentPath);
        })
        .catch(err => showToast("创建失败: " + err.message, "error"));
    });
}

// 供 HTML onclick 使用的全局辅助函数
window._loadDirPicker = function(path) {
    loadDirectoryPicker(decodeURIComponent(path));
};
window._selectSidebarItem = function(el) {
    document.querySelectorAll(".fb-sidebar-item").forEach(item => item.classList.remove("active"));
    if (el) el.classList.add("active");
};

function closeDirectoryPicker() {
    const overlay = document.querySelector('.folder-browser-overlay');
    if (overlay) overlay.remove();
    _dirPickerCallback = null;
    _dirPickerCurrentPath = "";
}

function initDrivesList() {
    fetch(API.browseDirs + "?path=")
        .then(r => r.json())
        .then(data => {
            const drivesList = document.getElementById("fb-drives-list");
            if (!drivesList) return;
            if (data.dirs && data.dirs.length) {
                drivesList.innerHTML = data.dirs.map(d => `
                    <div class="fb-sidebar-item" data-path="${d.path.replace(/\\/g, '\\\\')}" onclick="event.stopPropagation(); _loadDirPicker('${d.path.replace(/\\/g, '\\\\')}'); _selectSidebarItem(this);">
                        <span class="fb-sidebar-icon">💾</span>
                        <span class="fb-sidebar-name">${escapeHtml(d.name)}</span>
                    </div>
                `).join("");
            } else {
                drivesList.innerHTML = '<div class="fb-sidebar-item" style="color:#999;">无可用驱动器</div>';
            }
        })
        .catch(() => {
            const drivesList = document.getElementById("fb-drives-list");
            if (drivesList) drivesList.innerHTML = '<div class="fb-sidebar-item" style="color:#999;">加载失败</div>';
        });
}

function loadDirectoryPicker(path) {
    const fetchPath = path || "";

    fetch(API.browseDirs + "?path=" + encodeURIComponent(fetchPath))
        .then(r => r.json())
        .then(data => {
            if (data.error) { showToast(data.error, "error"); return; }
            const currentPath = data.current || "";
            _dirPickerCurrentPath = currentPath;

            // 更新地址栏
            const addrInput = document.getElementById("dir-picker-addr");
            if (addrInput) addrInput.value = currentPath;

            // 更新底部已选路径
            const selectedPathEl = document.getElementById("dir-picker-selected-path");
            if (selectedPathEl) {
                selectedPathEl.textContent = currentPath ? `📂 ${currentPath}` : "请选择一个文件夹";
            }

            // 高亮左侧导航对应项
            document.querySelectorAll(".fb-sidebar-item").forEach(item => {
                const itemPath = item.dataset.path || "";
                item.classList.toggle("active", itemPath && itemPath.toLowerCase() === currentPath.toLowerCase());
            });

            // 更新文件夹列表
            const listEl = document.getElementById("dir-picker-list");
            if (!listEl) return;
            listEl.innerHTML = "";

            // "上一级"按钮
            if (currentPath) {
                const parts = currentPath.split(/[\\/]/);
                const parentPath = parts.slice(0, -1).join("\\") || "";
                const upItem = document.createElement("div");
                upItem.className = "folder-browser-item folder-browser-up";
                upItem.innerHTML = `
                    <span class="folder-browser-icon">📁</span>
                    <span class="folder-browser-name">..</span>
                    <span class="folder-browser-date"></span>
                    <span class="folder-browser-type">文件夹</span>`;
                upItem.addEventListener("dblclick", () => _loadDirPicker(parentPath));
                upItem.addEventListener("click", () => {
                    listEl.querySelectorAll(".folder-browser-item").forEach(el => el.classList.remove("selected"));
                    upItem.classList.add("selected");
                    // 单击更新选中路径为上级目录
                    _dirPickerCurrentPath = parentPath;
                    const selPathEl = document.getElementById("dir-picker-selected-path");
                    if (selPathEl) selPathEl.textContent = parentPath ? `📂 ${parentPath}` : "请选择一个文件夹";
                });
                listEl.appendChild(upItem);
            }

            // 子目录列表
            if (data.dirs && data.dirs.length) {
                data.dirs.forEach(dir => {
                    const item = document.createElement("div");
                    item.className = "folder-browser-item";
                    const dateStr = dir.modified ? formatFileDate(dir.modified) : "";
                    item.innerHTML = `
                        <span class="folder-browser-icon">📁</span>
                        <span class="folder-browser-name">${escapeHtml(dir.name)}</span>
                        <span class="folder-browser-date">${dateStr}</span>
                        <span class="folder-browser-type">文件夹</span>`;
                    item.addEventListener("click", () => {
                        listEl.querySelectorAll(".folder-browser-item").forEach(el => el.classList.remove("selected"));
                        item.classList.add("selected");
                        // 单击更新选中路径为该文件夹
                        _dirPickerCurrentPath = dir.path;
                        const selPathEl = document.getElementById("dir-picker-selected-path");
                        if (selPathEl) selPathEl.textContent = `📂 ${dir.path}`;
                    });
                    item.addEventListener("dblclick", () => {
                        _loadDirPicker(dir.path);
                    });
                    listEl.appendChild(item);
                });
            } else if (!currentPath) {
                listEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;"><p style="color:#999;font-size:14px;">请从左侧选择驱动器或输入路径</p></div>';
            } else {
                listEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;"><p style="color:#999;font-size:14px;">此文件夹没有子文件夹</p></div>';
            }
        })
        .catch(err => showToast("加载目录失败: " + err.message, "error"));
}

// ====== 辅助函数 ======
function getDesktopPath() {
    return (window._env_USERPROFILE || "") + "\\Desktop";
}
function getDocumentsPath() {
    return (window._env_USERPROFILE || "") + "\\Documents";
}
function getDownloadsPath() {
    return (window._env_USERPROFILE || "") + "\\Downloads";
}

function formatFileDate(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (y === new Date().getFullYear()) return `${m}/${day} ${hh}:${mm}`;
    return `${y}/${m}/${day} ${hh}:${mm}`;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// 初始化用户目录环境变量
(function() {
    fetch("/api/user-home")
        .then(r => r.json())
        .then(data => {
            if (data.home) window._env_USERPROFILE = data.home;
        })
        .catch(() => {
            window._env_USERPROFILE = "C:\\Users\\Default";
        });
})();

// ====== 统计 ======
function updateStats(matched, total, partial) {
    partial = partial || 0;
    document.getElementById("stats-section").classList.remove("hidden");
    animateNumber("stat-total", total);
    animateNumber("stat-matched", matched);
    animateNumber("stat-incomplete", partial);
    animateNumber("stat-missing", total - matched - partial);
    const percent = total > 0 ? ((matched + partial * 0.5) / total) * 100 : 0;
    document.getElementById("progress-fill").style.width = percent + "%";
}

function animateNumber(id, target) {
    const el = document.getElementById(id);
    const start = Number(el.textContent) || 0;
    const end = Number(target) || 0;
    const duration = 360;
    const startTime = performance.now();
    function tick(now) {
        const progress = Math.min((now - startTime) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.round(start + (end - start) * eased);
        if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

// ====== 工作流状态 ======
function updateWorkflowState() {
    const hasTemplate = previewItems.length > 0;
    const hasScannedFolder = scannedCount > 0;
    const hasMatchResult = Boolean(matchResults && matchResults.length);

    setWorkflowStep("workflow-step-0", hasTemplate ? "completed" : "active");
    setWorkflowStep("workflow-step-1", hasScannedFolder ? "completed" : (hasTemplate ? "active" : "pending"));
    setWorkflowStep("workflow-step-2", hasMatchResult ? "completed" : (hasScannedFolder ? "active" : "pending"));

    document.getElementById("match-badge").textContent = hasMatchResult ? "✓ 已完成" : (hasScannedFolder ? "待匹配" : "待前置");
    const badgeText = hasMatchResult ? "匹配完成" : (hasScannedFolder ? "步骤 2 待匹配" : (hasTemplate ? "步骤 1 待扫描" : "步骤 0 待生成"));
    document.getElementById("workflow-state-badge").textContent = badgeText;
}

function setWorkflowStep(id, state) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("active", "completed", "pending");
    el.classList.add(state);
}

// 初始化清单视图预览面板的关闭按钮
function initListPreviewPane() {
    const closeBtn = document.getElementById('list-preview-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            const pane = document.getElementById('list-preview-pane');
            const wrapper = document.getElementById('list-view-wrapper');
            const content = document.getElementById('list-preview-content');
            if (pane) pane.classList.add('hidden');
            if (wrapper) wrapper.classList.remove('with-preview');
            if (content) {
                content.innerHTML = `<div class="preview-empty">
                    <div style="font-size:48px;color:#ccc;margin-bottom:12px;">&#128196;</div>
                    <p style="color:#999;font-size:14px;">点击清单中的文件可在此预览</p>
                </div>`;
            }
        });
    }
}

// 初始化资料浏览视图预览面板的关闭按钮
function initBrowsePreviewPane() {
    const closeBtn = document.getElementById('browse-preview-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            const pane = document.getElementById('browse-preview-pane');
            const wrapper = document.getElementById('browse-view-wrapper');
            const content = document.getElementById('browse-preview-content');
            if (pane) pane.classList.add('hidden');
            if (wrapper) wrapper.classList.remove('with-preview');
            if (content) {
                content.innerHTML = `<div class="preview-empty">
                    <div style="font-size:48px;color:#ccc;margin-bottom:12px;">&#128196;</div>
                    <p style="color:#999;font-size:14px;">点击资料中的文件可在此预览</p>
                </div>`;
            }
        });
    }
}

// ====== 公司归属确认弹窗（全屏分栏：左侧列表 + 右侧预览） ======
function showUnassignedModal() {
    const unassignedItems = window._unassignedItems || [];
    if (!unassignedItems.length) {
        showToast("没有需要确认公司归属的资料", "info");
        return;
    }

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay unassigned-overlay";

    const modal = document.createElement("div");
    modal.className = "unassigned-full-modal";

    let listHtml = '';
    unassignedItems.forEach(r => {
        const item = previewItems[r.index - 1];
        const fileName = r.matched_names[0] || r.matched_files[0].split(/[\\/]/).pop();
        const filePath = r.matched_files[0];
        const isDir = r.matched_types && r.matched_types[0] === "文件夹";

        const optionsHtml = companyNames.map(c =>
            `<label><input type="checkbox" value="${c}"> ${c}</label>`
        ).join('');

        listHtml += `
        <div class="unassigned-item" data-index="${r.index}">
            <div class="unassigned-item-info">
                <div class="unassigned-item-name">${item ? item.demand_name : r.checklist_name}</div>
                <div class="unassigned-item-file">
                    <a href="#" class="preview-link" data-path="${filePath}" data-is-dir="${isDir}" onclick="previewFile('${filePath.replace(/\\/g, '\\\\')}', ${isDir}); return false;">
                        ${isDir ? '📁' : '📄'} ${fileName}
                    </a>
                </div>
            </div>
            <div class="company-select-wrap" data-index="${r.index}">
                <div class="company-select-trigger">
                    <span class="select-label">请选择公司</span>
                    <span class="arrow">▼</span>
                </div>
                <div class="company-select-dropdown">
                    <label><input type="checkbox" class="select-all-cb"> 全选</label>
                    <div class="company-select-divider"></div>
                    ${optionsHtml}
                    <div class="company-select-divider"></div>
                    <label class="match-error-option"><input type="checkbox" class="match-error-cb" value="__MATCH_ERROR__"> ❌ 匹配错误（视为未提供资料）</label>
                    <div class="company-select-actions">
                        <button class="btn btn-primary btn-sm company-confirm-btn">确认分配</button>
                    </div>
                </div>
            </div>
        </div>`;
    });

    modal.innerHTML = `
        <button class="unassigned-close-btn" title="关闭">&#10005;</button>
        <div class="unassigned-left">
            <h3 style="color:#f59e0b;margin:0 0 4px;">&#9888;&#65039; 需人工确认公司归属的资料</h3>
            <p class="unassigned-hint" style="color:#666;font-size:13px;margin:0 0 12px;">以下资料已匹配成功，但无法自动确认属于哪些公司。请点击下拉框选择所属公司后确认。</p>
            <div class="unassigned-list">${listHtml}</div>
        </div>
        <div class="unassigned-right" id="preview-pane">
            <div class="preview-empty">
                <div style="font-size:48px;color:#ccc;margin-bottom:12px;">📄</div>
                <p style="color:#999;font-size:14px;">点击左侧文件可在此预览</p>
            </div>
        </div>`;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // 右上角关闭
    modal.querySelector('.unassigned-close-btn').addEventListener('click', () => overlay.remove());

    // 下拉触发
    modal.querySelectorAll('.company-select-trigger').forEach(trigger => {
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            const wrap = trigger.closest('.company-select-wrap');
            const dropdown = wrap.querySelector('.company-select-dropdown');
            const isOpen = dropdown.classList.contains('open');
            modal.querySelectorAll('.company-select-dropdown.open').forEach(d => {
                d.classList.remove('open');
                d.closest('.company-select-wrap').querySelector('.company-select-trigger').classList.remove('is-open');
            });
            if (!isOpen) {
                dropdown.classList.add('open');
                trigger.classList.add('is-open');
            }
        });
    });

    const closeAllDropdowns = () => {
        modal.querySelectorAll('.company-select-dropdown.open').forEach(d => {
            d.classList.remove('open');
            d.closest('.company-select-wrap').querySelector('.company-select-trigger').classList.remove('is-open');
        });
    };
    document.addEventListener('click', closeAllDropdowns);
    overlay.addEventListener('remove', () => {
        document.removeEventListener('click', closeAllDropdowns);
    }, { once: true });

    modal.querySelectorAll('.company-select-dropdown').forEach(dropdown => {
        dropdown.addEventListener('click', (e) => e.stopPropagation());
    });

    // 全选复选框：选中/取消所有公司复选框
    modal.querySelectorAll('.select-all-cb').forEach(scb => {
        scb.addEventListener('change', (e) => {
            e.stopPropagation();
            const wrap = scb.closest('.company-select-wrap');
            const companyCbs = wrap.querySelectorAll('input[type=checkbox]:not(.select-all-cb):not(.match-error-cb)');
            companyCbs.forEach(cb => cb.checked = scb.checked);
            // 全选时取消匹配错误
            if (scb.checked) {
                const matchErrorCb = wrap.querySelector('.match-error-cb');
                if (matchErrorCb) matchErrorCb.checked = false;
            }
        });
    });

    // 匹配错误与公司复选互斥
    modal.querySelectorAll('.match-error-cb').forEach(mcb => {
        mcb.addEventListener('change', (e) => {
            e.stopPropagation();
            const wrap = mcb.closest('.company-select-wrap');
            if (mcb.checked) {
                // 选中匹配错误 → 取消所有公司勾选（含全选）
                const allCbs = wrap.querySelectorAll('input[type=checkbox]:not(.match-error-cb)');
                allCbs.forEach(cb => cb.checked = false);
            }
        });
    });

    // 公司复选框取消 → 匹配错误取消
    modal.querySelectorAll('.company-select-dropdown input[type=checkbox]:not(.select-all-cb):not(.match-error-cb)').forEach(cb => {
        cb.addEventListener('change', (e) => {
            e.stopPropagation();
            const wrap = cb.closest('.company-select-wrap');
            const matchErrorCb = wrap.querySelector('.match-error-cb');
            if (matchErrorCb && matchErrorCb.checked) {
                matchErrorCb.checked = false;
            }
        });
    });

    // 确认分配按钮
    modal.querySelectorAll('.company-confirm-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const wrap = btn.closest('.company-select-wrap');
            const index = parseInt(wrap.dataset.index);
            const checked = Array.from(wrap.querySelectorAll('input[type=checkbox]:checked')).map(cb => cb.value);
            saveCompanyAssignment(index, checked, wrap, modal);
        });
    });
}

// 重置预览窗格（未确认归属弹窗）为占位状态
function resetPanePreview() {
    const pane = document.getElementById('preview-pane');
    if (!pane) return;
    pane.innerHTML = `<div class="preview-empty">
        <div style="font-size:48px;color:#ccc;margin-bottom:12px;">&#128196;</div>
        <p style="color:#999;font-size:14px;">点击左侧文件可在此预览</p>
    </div>`;
}

// 右侧预览窗格：显示文件内容
function previewFile(filePath, isDir) {
    const pane = document.getElementById('preview-pane');
    const sidePrefix = currentView === 'browse' ? 'browse' : 'list';
    const sidePane = document.getElementById(sidePrefix + '-preview-pane');

    if (!pane && !sidePane) {
        // 无预览窗格时降级为在新标签页中打开
        window.open("/api/open?path=" + encodeURIComponent(filePath), "_blank");
        return;
    }

    const useSidePane = !pane && !!sidePane;
    if (useSidePane) {
        sidePane.classList.remove('hidden');
        const wrapper = document.getElementById(
            sidePrefix === 'browse' ? 'browse-view-wrapper' : 'list-view-wrapper'
        );
        if (wrapper) wrapper.classList.add('with-preview');
    }

    // 辅助：设置预览内容
    function setSidePreview(title, bodyHtml) {
        document.getElementById(sidePrefix + '-preview-title').textContent = title;
        document.getElementById(sidePrefix + '-preview-content').innerHTML = bodyHtml;
    }
    function getContentEl() {
        return useSidePane
            ? document.getElementById(sidePrefix + '-preview-content')
            : pane.querySelector('.preview-content');
    }

    if (isDir) {
        const title = `📁 ${filePath.split(/[\\/]/).pop()}`;
        const bodyHtml = `<p style="color:#999;text-align:center;margin-top:40px;">文件夹预览暂不支持，请点击「打开」浏览内容</p>`;
        if (useSidePane) {
            setSidePreview(title, bodyHtml);
        } else {
            pane.innerHTML = `
            <div class="preview-header">
                <span class="preview-title">${title}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在浏览器中打开">打开 ↗</a>
                    <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                </div>
            </div>
            <div class="preview-content">${bodyHtml}</div>`;
        }
        return;
    }

    const ext = filePath.split('.').pop().toLowerCase();
    const isImage = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'].includes(ext);
    const isPdf = ext === 'pdf';
    const isText = ['txt', 'csv', 'log', 'json', 'xml', 'md', 'html', 'htm', 'js', 'css', 'py', 'java', 'c', 'cpp', 'h', 'sql', 'sh', 'bat', 'yml', 'yaml', 'ini', 'conf', 'cfg'].includes(ext);

    if (isImage) {
        const title = `🖼️ ${filePath.split(/[\\/]/).pop()}`;
        const bodyHtml = `<img src="/api/open?path=${encodeURIComponent(filePath)}" style="max-width:100%;max-height:calc(100vh - 160px);object-fit:contain;" />`;
        if (useSidePane) {
            setSidePreview(title, bodyHtml);
        } else {
            pane.innerHTML = `
            <div class="preview-header">
                <span class="preview-title">${title}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在新窗口打开">打开 ↗</a>
                    <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                </div>
            </div>
            <div class="preview-content">${bodyHtml}</div>`;
        }
    } else if (isPdf) {
        const title = `📕 ${filePath.split(/[\\/]/).pop()}`;
        const bodyHtml = `<iframe src="/api/open?path=${encodeURIComponent(filePath)}" style="width:100%;height:calc(100vh - 160px);border:none;"></iframe>`;
        if (useSidePane) {
            setSidePreview(title, bodyHtml);
        } else {
            pane.innerHTML = `
            <div class="preview-header">
                <span class="preview-title">${title}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在新窗口打开">打开 ↗</a>
                    <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                </div>
            </div>
            <div class="preview-content" style="padding:0;">${bodyHtml}</div>`;
        }
    } else if (isText) {
        const title = `📄 ${filePath.split(/[\\/]/).pop()}`;
        fetch('/api/open?path=' + encodeURIComponent(filePath))
            .then(r => r.text())
            .then(text => {
                const bodyHtml = `<pre style="white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:1.6;color:#333;padding:16px;margin:0;">${text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>`;
                if (useSidePane) {
                    setSidePreview(title, bodyHtml);
                } else {
                    pane.innerHTML = `
                    <div class="preview-header">
                        <span class="preview-title">${title}</span>
                        <div style="display:flex;align-items:center;gap:8px;">
                            <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在新窗口打开">打开 ↗</a>
                            <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                        </div>
                    </div>
                    <div class="preview-content">${bodyHtml}</div>`;
                }
            })
            .catch(() => {
                const errHtml = `<p style="color:#e74c3c;text-align:center;margin-top:40px;">无法预览此文件</p>`;
                if (useSidePane) {
                    setSidePreview(title, errHtml);
                } else {
                    pane.innerHTML = `
                    <div class="preview-header">
                        <span class="preview-title">${title}</span>
                        <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                    </div>
                    <div class="preview-content">${errHtml}</div>`;
                }
            });
    } else if (ext === 'xlsx' || ext === 'xls') {
        // Excel 文件：使用 SheetJS 解析并渲染为表格预览
        const excelTitle = `📊 ${filePath.split(/[\\/]/).pop()}`;
        const loadingHtml = `<p style="color:#999;text-align:center;margin-top:40px;">正在解析 Excel...</p>`;
        if (useSidePane) {
            setSidePreview(excelTitle, loadingHtml);
        } else {
            pane.innerHTML = `
            <div class="preview-header">
                <span class="preview-title">${excelTitle}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在新窗口打开">打开 ↗</a>
                    <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                </div>
            </div>
            <div class="preview-content" style="padding:0;">${loadingHtml}</div>`;
        }

        fetch('/api/open?path=' + encodeURIComponent(filePath))
            .then(r => r.arrayBuffer())
            .then(buf => {
                const wb = XLSX.read(buf, { type: 'array' });
                const contentDiv = getContentEl();
                setupExcelPreview(wb, contentDiv);
            })
            .catch(() => {
                getContentEl().innerHTML = `
                    <p style="color:#e74c3c;text-align:center;margin-top:40px;">无法预览此 Excel 文件，请点击「打开」查看</p>`;
            });
    } else {
        const title = `📎 ${filePath.split(/[\\/]/).pop()}`;
        const bodyHtml = `<p style="color:#999;text-align:center;margin-top:40px;">该文件类型不支持内嵌预览，请点击「打开」在浏览器中查看</p>`;
        if (useSidePane) {
            setSidePreview(title, bodyHtml);
        } else {
            pane.innerHTML = `
            <div class="preview-header">
                <span class="preview-title">${title}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <a href="/api/open?path=${encodeURIComponent(filePath)}" target="_blank" class="preview-open-btn" title="在新窗口打开">打开 ↗</a>
                    <button class="preview-pane-close-btn" onclick="resetPanePreview()" title="关闭预览">&#10005;</button>
                </div>
            </div>
            <div class="preview-content">${bodyHtml}</div>`;
        }
    }
}

// 渲染指定 sheet 的表格内容到目标容器
// targetEl: 渲染目标 DOM 元素；若未传，则回退到查找 .excel-sheet-content
function renderExcelSheet(wb, sheetIndex, targetEl) {
    const sheetName = wb.SheetNames[sheetIndex];
    const sheet = wb.Sheets[sheetName];
    const contentEl = targetEl || document.querySelector('.excel-sheet-content');
    if (!contentEl) return;

    if (!sheet || !sheet['!ref']) {
        contentEl.innerHTML = `<p style="color:#999;text-align:center;margin-top:40px;">该工作表为空</p>`;
        return;
    }

    const range = XLSX.utils.decode_range(sheet['!ref']);
    const maxRows = 200;
    const data = XLSX.utils.sheet_to_json(sheet, { header: 1, range: range, defval: '' });
    const rows = data.slice(0, maxRows);
    const totalRows = data.length;
    const cols = range.e.c - range.s.c + 1;

    let tableHtml = `<table class="excel-preview-table">`;
    if (rows.length > 0) {
        tableHtml += '<thead><tr>';
        for (let c = 0; c < cols; c++) {
            const val = String(rows[0][c] ?? '');
            tableHtml += `<th>${val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</th>`;
        }
        tableHtml += '</tr></thead><tbody>';
        for (let r = 1; r < rows.length; r++) {
            tableHtml += '<tr>';
            for (let c = 0; c < cols; c++) {
                const val = String(rows[r][c] ?? '');
                tableHtml += `<td>${val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</td>`;
            }
            tableHtml += '</tr>';
        }
        tableHtml += '</tbody></table>';
    } else {
        tableHtml += '<p style="color:#999;text-align:center;margin-top:40px;">该工作表为空</p>';
    }
    if (totalRows > maxRows) {
        tableHtml += `<div class="excel-row-hint">共 ${totalRows} 行，仅显示前 ${maxRows} 行。请点击「打开」查看完整内容。</div>`;
    }
    contentEl.innerHTML = tableHtml;
}

// 在指定容器中初始化 Excel 预览（工作表选择栏 + 内容区 + 切换事件）
function setupExcelPreview(wb, containerEl) {
    window._previewWb = wb;

    let html = `<div class="excel-preview-wrap">`;

    // 工作表选择栏
    if (wb.SheetNames.length > 1) {
        html += `<div class="excel-sheet-bar">
            <span class="excel-sheet-label">工作表</span>
            <select class="excel-sheet-select">`;
        wb.SheetNames.forEach((name, i) => {
            html += `<option value="${i}"${i === 0 ? ' selected' : ''}>${name}</option>`;
        });
        html += `</select></div>`;
    } else {
        html += `<div class="excel-sheet-bar">
            <span class="excel-sheet-label">工作表: <strong>${wb.SheetNames[0]}</strong></span>
        </div>`;
    }

    html += `<div class="excel-scroll-area excel-sheet-content"></div></div>`;
    containerEl.innerHTML = html;

    const sheetContent = containerEl.querySelector('.excel-sheet-content');

    // 渲染第一个 sheet
    window._previewCurrentSheet = 0;
    renderExcelSheet(wb, 0, sheetContent);

    // 绑定 sheet 切换事件
    const sheetSelect = containerEl.querySelector('.excel-sheet-select');
    if (sheetSelect) {
        sheetSelect.addEventListener('change', () => {
            const idx = parseInt(sheetSelect.value);
            window._previewCurrentSheet = idx;
            renderExcelSheet(wb, idx, sheetContent);
        });
    }
}

// 保存公司归属分配
function saveCompanyAssignment(index, companyList, wrapEl, modalEl) {
    const isMatchError = companyList.includes('__MATCH_ERROR__');

    if (isMatchError) {
        // 匹配错误：将所有公司状态设为 N，视为未提供资料
        const item = previewItems.find(it => it.row_index === index);
        if (item) {
            companyNames.forEach(c => {
                if (!item.company_status) item.company_status = {};
                item.company_status[c] = "N";
                // 同步到后端
                fetch(API.updateCellStatus, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ row_index: index, company_name: c, status: "N" }),
                }).catch(() => {});
            });
        }

        // 从 _unassignedItems 中移除该项
        window._unassignedItems = (window._unassignedItems || []).filter(r => r.index !== index);

        // 更新UI
        const trigger = wrapEl.querySelector('.company-select-trigger');
        trigger.querySelector('.select-label').textContent = '❌ 匹配错误';
        wrapEl.classList.add('confirmed', 'match-error');
        wrapEl.querySelector('.company-select-dropdown').classList.remove('open');
        trigger.classList.remove('is-open');

        showToast('已标记为匹配错误（未提供资料）', 'success');
        renderPreviewTable();
        checkAllAssigned(modalEl);
        return;
    }

    fetch(API.assignCompany, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index, company_names: companyList }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast(data.error, "error"); return; }

        // 更新 matchResults 中的 company_coverage
        const matchedResult = matchResults.find(r => r.index === index);
        if (matchedResult) {
            matchedResult.company_coverage = data.company_coverage || {};
        }

        // 同步更新 previewItems 的公司状态
        const item = previewItems.find(it => it.row_index === index);
        if (item) {
            companyNames.forEach(c => {
                if (!item.company_status) item.company_status = {};
                if (companyList.includes(c)) {
                    item.company_status[c] = "Y";
                } else {
                    // 未选择的公司设为 N
                    if (!item.company_status[c]) item.company_status[c] = "N";
                }
            });
        }

        // 从 _unassignedItems 中移除该项
        window._unassignedItems = (window._unassignedItems || []).filter(r => r.index !== index);

        // 更新下拉触发器显示文字，并标记为已确认
        const trigger = wrapEl.querySelector('.company-select-trigger');
        if (companyList.length > 0) {
            trigger.querySelector('.select-label').textContent = companyList.join('、');
        } else {
            trigger.querySelector('.select-label').textContent = '未选择';
        }
        wrapEl.classList.add('confirmed');

        // 关闭下拉
        wrapEl.querySelector('.company-select-dropdown').classList.remove('open');
        trigger.classList.remove('is-open');

        showToast(`已分配给: ${companyList.length ? companyList.join('、') : '未选择（可稍后重新确认）'}`, "success");
        markProjectDirty();
        renderPreviewTable();
        checkAllAssigned(modalEl);
    })
    .catch(err => showToast("分配失败: " + err.message, "error"));
}

// 检查是否所有项都已分配，若是则关闭弹窗
function checkAllAssigned(modalEl) {
    if (window._unassignedItems.length === 0) {
        showToast("所有资料的公司归属已确认完成", "success");
        const overlay = modalEl.closest('.modal-overlay');
        if (overlay) overlay.remove();
    } else {
        const hintP = modalEl.querySelector('p');
        if (hintP) {
            hintP.textContent = `还有 ${window._unassignedItems.length} 项待确认。请继续选择所属公司后确认。`;
        }
    }
}

// 检查是否所有项都已分配，若是则关闭弹窗
let _matrixAssignFilePath = null;
let _matrixAssignIsDir = false;

function showMatrixAssignModal(filePath, isDir, fileName) {
    if (!previewItems || !previewItems.length) { showToast("请先执行匹配", "error"); return; }

    _matrixAssignFilePath = filePath;
    _matrixAssignIsDir = isDir;
    const displayName = fileName || filePath.split(/[\\/]/).pop();

    // 移除已有弹窗
    const existing = document.querySelector('.assign-matrix-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay assign-matrix-overlay";

    const modal = document.createElement("div");
    modal.className = "assign-matrix-modal";

    // 检查哪些行已经分配了此文件
    const assignedRows = new Set();
    if (matchResults) {
        matchResults.forEach(mr => {
            const allFiles = [...(mr.matched_files || [])];
            if (mr.company_coverage) {
                Object.values(mr.company_coverage).forEach(info => {
                    if (info.files) allFiles.push(...info.files);
                    if (info.folders) allFiles.push(...info.folders);
                });
            }
            if (allFiles.some(f => f === filePath)) {
                assignedRows.add(mr.index);
            }
        });
    }

    let tableHtml = '<h3 style="margin-bottom:4px;">分配资料: ' + displayName + '</h3>';
    tableHtml += '<p style="color:#666;font-size:12px;margin-bottom:12px;">勾选需求行对应的公司后点击"确认分配"（可多选）</p>';
    tableHtml += '<div class="assign-matrix-scroll"><table class="assign-matrix-table"><thead><tr>';
    tableHtml += '<th>序号</th><th>科目</th><th>所需PBC</th><th>需求资料</th><th>获取状态</th><th>分配公司</th>';
    tableHtml += '</tr></thead><tbody>';

    previewItems.forEach(item => {
        const rowIndex = item.row_index;
        const alreadyAssigned = assignedRows.has(rowIndex);
        const rowStatus = item._rowStatus || 'N';

        let statusText = '';
        let statusColor = '#999';
        if (alreadyAssigned) {
            statusText = '已分配';
            statusColor = 'var(--success)';
        } else if (item.company_status) {
            // 汇总各公司状态为一个总体状态
            const statusList = Object.values(item.company_status);
            const allY = statusList.every(st => st === 'Y');
            const allN = statusList.every(st => st === 'N');
            if (allY) {
                statusText = 'Y';
                statusColor = 'var(--success)';
            } else if (allN) {
                statusText = 'N';
                statusColor = 'var(--danger)';
            } else {
                statusText = '不完整';
                statusColor = 'var(--warning)';
            }
        } else if (rowStatus === 'Y') {
            statusText = 'Y';
            statusColor = 'var(--success)';
        } else if (rowStatus === '不完整') {
            statusText = '不完整';
            statusColor = 'var(--warning)';
        } else {
            statusText = 'N';
            statusColor = 'var(--danger)';
        }

        // 公司下拉多选（类似需人工确认公司归属的交互方式）
        let companyCheckboxesHtml = '';
        if (alreadyAssigned) {
            companyCheckboxesHtml = '<span style="color:var(--success);font-weight:600;font-size:12px;">已分配</span>';
        } else {
            const optionsHtml = companyNames.map(cName => {
                // 显示该公司当前状态作为参考标记
                const currStatus = item.company_status ? (item.company_status[cName] || 'N') : 'N';
                let statusBadge = '';
                if (currStatus === 'Y') {
                    statusBadge = ' <span class="company-status-badge status-y">Y</span>';
                } else if (currStatus === '不完整') {
                    statusBadge = ' <span class="company-status-badge status-incomplete">不完整</span>';
                }
                return `<label><input type="checkbox" value="${cName}" class="matrix-company-cb"> ${cName}${statusBadge}</label>`;
            }).join('');

            companyCheckboxesHtml = `<div class="company-select-wrap matrix-company-wrap" data-row="${rowIndex}">
                <div class="company-select-trigger matrix-company-trigger">
                    <span class="select-label">请选择公司</span>
                    <span class="arrow">▼</span>
                </div>
                <div class="company-select-dropdown matrix-company-dropdown">
                    <label><input type="checkbox" class="select-all-cb"> 全选</label>
                    <div class="company-select-divider"></div>
                    ${optionsHtml}
                </div>
            </div>`;
        }

        tableHtml += `<tr class="${alreadyAssigned ? 'assign-row-matched' : ''}" data-row="${rowIndex}">
            <td>${item.seq ?? ''}</td>
            <td>${item.subject}</td>
            <td>${item.pbc_name}</td>
            <td>${item.demand_name}</td>
            <td style="${statusColor ? 'color:' + statusColor + ';' : ''}font-weight:600;font-size:12px;">${statusText}</td>
            <td>${companyCheckboxesHtml}</td>
        </tr>`;
    });

    tableHtml += '</tbody></table></div>';

    tableHtml += `<div class="assign-matrix-actions">
        <button class="btn btn-outline btn-sm" id="matrix-assign-cancel">取消</button>
        <button class="btn btn-primary btn-sm" id="matrix-assign-confirm">确认分配</button>
    </div>`;

    modal.innerHTML = tableHtml;
    modal.addEventListener("click", (e) => e.stopPropagation());

    // modal 作为 overlay 的子元素，点击 overlay 空白处关闭
    overlay.appendChild(modal);
    overlay.addEventListener("click", function(e) {
        if (e.target === overlay) overlay.remove();
    });

    document.body.appendChild(overlay);

    // 事件绑定
    document.getElementById('matrix-assign-cancel').addEventListener('click', () => overlay.remove());
    document.getElementById('matrix-assign-confirm').addEventListener('click', () => {
        executeMatrixAssign(filePath, isDir, overlay);
    });

    // ---- 下拉多选交互（分配公司列） ----

    // 下拉触发：点击触发器打开/关闭下拉
    modal.querySelectorAll('.matrix-company-trigger').forEach(trigger => {
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            const wrap = trigger.closest('.matrix-company-wrap');
            const dropdown = wrap.querySelector('.matrix-company-dropdown');
            const isOpen = dropdown.classList.contains('open');

            // 关闭所有其他已打开的下拉
            modal.querySelectorAll('.matrix-company-dropdown.open').forEach(d => {
                d.classList.remove('open');
                d.style.position = '';
                d.style.top = '';
                d.style.left = '';
                d.style.width = '';
                d.style.zIndex = '';
                d.style.minWidth = '';
                const siblingWrap = d.closest('.matrix-company-wrap');
                if (siblingWrap) siblingWrap.querySelector('.matrix-company-trigger').classList.remove('is-open');
            });

            if (!isOpen) {
                // 使用 fixed 定位避免被滚动容器裁剪
                const rect = trigger.getBoundingClientRect();
                dropdown.style.position = 'fixed';
                dropdown.style.top = (rect.bottom + 4) + 'px';
                // 右对齐：下拉框右边与触发按钮右边对齐
                dropdown.style.left = Math.min(rect.right, window.innerWidth - 10) + 'px';
                dropdown.style.transform = 'translateX(-100%)';
                dropdown.style.minWidth = '180px';
                dropdown.style.zIndex = '2000';
                dropdown.classList.add('open');
                trigger.classList.add('is-open');
            }
        });
    });

    // 关闭下拉：点击 modal 其他区域
    const closeMatrixDropdowns = () => {
        modal.querySelectorAll('.matrix-company-dropdown.open').forEach(d => {
            d.classList.remove('open');
            d.style.position = '';
            d.style.top = '';
            d.style.left = '';
            d.style.width = '';
            d.style.zIndex = '';
            d.style.minWidth = '';
            d.style.transform = '';
            const siblingWrap = d.closest('.matrix-company-wrap');
            if (siblingWrap) siblingWrap.querySelector('.matrix-company-trigger').classList.remove('is-open');
        });
    };
    document.addEventListener('click', closeMatrixDropdowns);
    overlay.addEventListener('remove', () => {
        document.removeEventListener('click', closeMatrixDropdowns);
    }, { once: true });

    // 下拉内部点击不冒泡（防止关闭）
    modal.querySelectorAll('.matrix-company-dropdown').forEach(dropdown => {
        dropdown.addEventListener('click', (e) => e.stopPropagation());
    });

    // 全选复选框：勾选/取消所有公司复选框，并更新触发器文本
    modal.querySelectorAll('.matrix-company-dropdown .select-all-cb').forEach(scb => {
        scb.addEventListener('change', (e) => {
            e.stopPropagation();
            const wrap = scb.closest('.matrix-company-wrap');
            const companyCbs = wrap.querySelectorAll('.matrix-company-cb');
            companyCbs.forEach(cb => cb.checked = scb.checked);
            updateMatrixTriggerLabel(wrap);
        });
    });

    // 公司复选框变化时更新触发器文本
    modal.querySelectorAll('.matrix-company-cb').forEach(cb => {
        cb.addEventListener('change', (e) => {
            e.stopPropagation();
            const wrap = cb.closest('.matrix-company-wrap');
            updateMatrixTriggerLabel(wrap);
        });
    });
}

// 更新矩阵分配弹窗中下拉触发器的显示文本
function updateMatrixTriggerLabel(wrap) {
    const trigger = wrap.querySelector('.matrix-company-trigger');
    const label = trigger.querySelector('.select-label');
    const checkedCbs = wrap.querySelectorAll('.matrix-company-cb:checked');
    const checkedNames = Array.from(checkedCbs).map(cb => cb.value);
    if (checkedNames.length === 0) {
        label.textContent = '请选择公司';
        label.style.color = '';
    } else {
        label.textContent = checkedNames.join('、');
        label.style.color = 'var(--primary)';
    }
}

function executeMatrixAssign(filePath, isDir, overlay) {
    // 收集所有勾选了公司的行（从下拉多选组件中读取）
    const checkGroups = document.querySelectorAll('.matrix-company-wrap');
    const assignments = [];
    checkGroups.forEach(wrap => {
        const rowIndex = parseInt(wrap.dataset.row);
        const cbs = wrap.querySelectorAll('.matrix-company-cb:checked');
        const companies = Array.from(cbs).map(cb => cb.value);
        if (companies.length > 0) {
            assignments.push({ rowIndex, companies });
        }
    });

    if (!assignments.length) {
        showToast("请至少为一个需求勾选公司", "error");
        return;
    }

    // 取第一个选中的行+公司组合执行分配
    const { rowIndex, companies } = assignments[0];

    // 步骤1：manual-match 将文件绑定到清单行
    fetch(API.manualMatch, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath, index: rowIndex }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast(data.error, "error"); return Promise.reject(data.error); }

        // 更新 matchResults
        if (data.match_results) {
            const idx = matchResults.findIndex(r => r.index === rowIndex);
            if (idx >= 0) {
                matchResults[idx] = data.match_results;
            } else {
                matchResults.push(data.match_results);
            }
        }

        // 步骤2：assign-company 设置公司归属（支持多公司）
        return fetch(API.assignCompany, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index: rowIndex, company_names: companies }),
        });
    })
    .then(r => r.json())
    .then(d => {
        if (d && d.error) { showToast(d.error, "error"); return; }

        // 更新 company_coverage
        if (d && d.company_coverage) {
            const mr = matchResults.find(r => r.index === rowIndex);
            if (mr) mr.company_coverage = d.company_coverage;
        }

        // 更新 previewItems
        const item = previewItems.find(it => it.row_index === rowIndex);
        if (item) {
            if (!item.company_status) item.company_status = {};
            companies.forEach(c => { item.company_status[c] = "Y"; });
            item._rowStatus = "Y";
        }

        syncMatchToPreview();
        if (currentView === "browse") {
            renderBrowseView();
        } else {
            renderPreviewTable();
        }
        updateStats();

        if (overlay) overlay.remove();
        showToast(`已分配给: ${companies.join('、')}`, "success");
        markProjectDirty();
    })
    .catch(err => {
        if (typeof err === 'string') return; // 已经在前面显示了 toast
        showToast("分配失败: " + err.message, "error");
    });
}

// ====== AI辅助匹配 ======
const LLM_PRESETS = {
    "deepseek": { model: "deepseek-v4-flash", base_url: "https://api.deepseek.com/v1" },
    "openai-gpt4o-mini": { model: "gpt-4o-mini", base_url: "https://api.openai.com/v1" },
    "openai-gpt4o": { model: "gpt-4o", base_url: "https://api.openai.com/v1" },
    "zhipu-glm4": { model: "glm-4", base_url: "https://open.bigmodel.cn/api/paas/v4" },
    "qwen": { model: "qwen-plus", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    "ollama": { model: "qwen2.5:7b", base_url: "http://localhost:11434/v1" },
};

function initLlmPanel() {
    const enabled = document.getElementById("llm-enabled");
    const modal = document.getElementById("llm-config-modal");
    const summary = document.getElementById("llm-config-summary");
    const tag = document.getElementById("llm-config-tag");
    const provider = document.getElementById("llm-provider");
    const apiKeyInput = document.getElementById("llm-api-key");
    const baseUrlInput = document.getElementById("llm-base-url");
    const hint = document.getElementById("llm-base-url-hint");
    const cancelBtn = document.getElementById("llm-cancel-btn");
    const saveBtn = document.getElementById("llm-save-btn");

    function updateBaseUrlHint() {
        const preset = LLM_PRESETS[provider.value];
        if (preset) {
            baseUrlInput.placeholder = preset.base_url;
            hint.textContent = provider.value === "ollama" ? "请确认 Ollama 已启动" : "留空则使用默认地址";
        }
    }

    function loadForm() {
        provider.value = localStorage.getItem("llm_provider") || "deepseek";
        apiKeyInput.value = localStorage.getItem("llm_api_key") || "";
        baseUrlInput.value = localStorage.getItem("llm_base_url") || "";
        updateBaseUrlHint();
    }

    function closeModal() {
        modal.classList.add("hidden");
        if (localStorage.getItem("llm_configured") !== "true") {
            enabled.checked = false;
            summary.classList.add("hidden");
        }
    }

    function saveConfig() {
        if (provider.value !== "ollama" && !apiKeyInput.value.trim()) {
            showToast("请输入API Key", "error"); return;
        }
        localStorage.setItem("llm_provider", provider.value);
        localStorage.setItem("llm_api_key", apiKeyInput.value.trim());
        localStorage.setItem("llm_base_url", baseUrlInput.value.trim());
        localStorage.setItem("llm_configured", "true");
        summary.classList.remove("hidden");
        enabled.checked = true;
        modal.classList.add("hidden");
        showToast("AI配置已保存", "success");
    }

    enabled.addEventListener("change", () => {
        if (enabled.checked) { loadForm(); modal.classList.remove("hidden"); }
    });
    tag.addEventListener("click", () => { loadForm(); modal.classList.remove("hidden"); });
    provider.addEventListener("change", updateBaseUrlHint);
    cancelBtn.addEventListener("click", closeModal);
    saveBtn.addEventListener("click", saveConfig);
    modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });

    updateBaseUrlHint();
    if (localStorage.getItem("llm_configured") === "true") {
        enabled.checked = true;
        summary.classList.remove("hidden");
        loadForm();
    } else {
        enabled.checked = false;
        summary.classList.add("hidden");
    }
}

async function runLlmMatch() {
    if (!matchResults) { showToast("请先执行规则匹配", "error"); return false; }
    if (localStorage.getItem("llm_configured") !== "true") {
        showToast("请先配置AI辅助匹配", "error");
        document.getElementById("llm-config-modal").classList.remove("hidden");
        return false;
    }
    const provider = localStorage.getItem("llm_provider") || "deepseek";
    const apiKey = localStorage.getItem("llm_api_key") || "";
    const baseUrl = localStorage.getItem("llm_base_url") || "";

    const statusEl = document.getElementById("llm-status");
    const matchBtn = document.getElementById("match-btn");
    statusEl.classList.remove("hidden");
    statusEl.textContent = "正在AI匹配中，请稍候...";
    matchBtn.disabled = true;
    matchBtn.style.opacity = "0.6";

    try {
        const r = await fetch(API.llmMatch, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider, api_key: apiKey, base_url: baseUrl }),
        });
        const data = await r.json();
        if (data.error) { showToast(data.error, "error"); return false; }
        if (data.match_results) matchResults = data.match_results;
        syncMatchToPreview();
        renderPreviewTable();
        updateStats(data.matched_count, data.total);
        updateWorkflowState();

        showToast(`AI匹配完成: ${data.llm_matched}项新增匹配`, "success");
        markProjectDirty();
        return true;
    } catch (err) {
        showToast("AI匹配失败: " + err.message, "error");
        return false;
    } finally {
        statusEl.classList.add("hidden");
        matchBtn.disabled = false;
        matchBtn.style.opacity = "1";
    }
}

// ====== 列宽拖拽（Excel 风格） ======
const _resizeInitialized = new Set();
let _columnResizeState = null;
let _globalResizeInit = false;
let _resizeGuideEl = null;

/** 获取或创建拖拽辅助线元素 */
function ensureResizeGuide() {
    if (!_resizeGuideEl) {
        _resizeGuideEl = document.createElement('div');
        _resizeGuideEl.className = 'col-resize-guide';
        document.body.appendChild(_resizeGuideEl);
    }
    return _resizeGuideEl;
}

/**
 * 为指定表格初始化 Excel 风格列宽拖拽。
 * 支持：矩阵视图、清单视图、资料浏览视图。
 * @param {string} tableSelector - 表格 CSS 选择器，如 '#preview-table'
 */
function initColumnResize(tableSelector) {
    const table = document.querySelector(tableSelector);
    if (!table) return;

    // 每次调用都重新注入拖拽把手（因为表格重新渲染时 th 会被替换）
    injectResizeHandles(table);

    // 事件绑定只做一次
    if (_resizeInitialized.has(tableSelector)) return;
    _resizeInitialized.add(tableSelector);

    // 使用事件委托在 thead 上捕获 mousedown
    const thead = table.querySelector('thead');
    if (!thead) return;

    thead.addEventListener('mousedown', function(event) {
        // 如果点击的是筛选按钮，不触发拖拽
        if (event.target.closest('.col-filter-btn')) return;

        const handle = event.target.closest('.col-resize-handle');
        if (!handle) return;

        const th = handle.closest('th');
        if (!th) return;

        const rect = th.getBoundingClientRect();
        _columnResizeState = {
            table,
            th,
            startX: event.clientX,
            startWidth: rect.width,
            colIndex: Array.from(th.parentElement.children).indexOf(th),
        };

        // 显示辅助线
        const guide = ensureResizeGuide();
        guide.style.left = rect.right + 'px';
        guide.style.height = table.getBoundingClientRect().height + 'px';
        guide.style.top = table.getBoundingClientRect().top + 'px';
        guide.classList.add('visible');

        // 标记拖拽把手
        handle.classList.add('active');
        document.body.classList.add('col-resizing');
        event.preventDefault();
    });

    // 全局事件（只初始化一次）
    if (_globalResizeInit) return;
    _globalResizeInit = true;

    document.addEventListener('mousemove', function(event) {
        if (!_columnResizeState) return;

        const { table: activeTable, th, startX, startWidth, colIndex } = _columnResizeState;
        const deltaX = event.clientX - startX;
        const newWidth = Math.max(30, startWidth + deltaX);

        // 更新表头宽度
        th.style.width = newWidth + 'px';
        th.style.minWidth = newWidth + 'px';

        // 同步更新所有行的对应单元格宽度（包含 fixed 布局，防止内容撑开列宽）
        const rows = activeTable.querySelectorAll('tbody tr');
        for (let i = 0; i < rows.length; i++) {
            const cell = rows[i].children[colIndex];
            if (cell) {
                cell.style.width = newWidth + 'px';
                cell.style.minWidth = newWidth + 'px';
                cell.style.maxWidth = newWidth + 'px'; /* 表格 cell 的 width 仅作为最小宽度，必须用 max-width 强制约束 */
            }
        }

        // 移动辅助线
        const guide = ensureResizeGuide();
        guide.style.left = (th.getBoundingClientRect().right) + 'px';
    });

    document.addEventListener('mouseup', function() {
        if (!_columnResizeState) return;

        // 保存 table ID（在 _columnResizeState 置 null 之前），用于更新冻结列偏移
        var tableId = _columnResizeState.table.id;

        // 移除辅助线
        const guide = ensureResizeGuide();
        guide.classList.remove('visible');

        // 移除拖拽把手的 active 状态
        const activeHandle = _columnResizeState.th.querySelector('.col-resize-handle');
        if (activeHandle) activeHandle.classList.remove('active');

        document.body.classList.remove('col-resizing');
        _columnResizeState = null;

        // 列宽变化后更新冻结列偏移
        if (tableId === 'preview-table') {
            updateFrozenColumnOffsets('#preview-table');
        }
    });

    // 双击列右边框自动调整列宽
    thead.addEventListener('dblclick', function(event) {
        const handle = event.target.closest('.col-resize-handle');
        if (!handle) return;

        const th = handle.closest('th');
        if (!th) return;

        const colIndex = Array.from(th.parentElement.children).indexOf(th);
        autoFitColumn(table, colIndex, th);
    });
}

/** 为表格的所有表头 th 注入拖拽把手元素 */
function injectResizeHandles(table) {
    const ths = table.querySelectorAll('thead th');
    ths.forEach(function(th) {
        // 避免重复注入
        if (th.querySelector('.col-resize-handle')) return;

        // 确保 th 为 relative 定位（把手需要 absolute 定位）
        const computed = getComputedStyle(th);
        if (computed.position === 'static') {
            th.style.position = 'relative';
        }

        const handle = document.createElement('div');
        handle.className = 'col-resize-handle';
        th.appendChild(handle);
    });
}

/**
 * 将表头列宽强制同步到所有行的对应 td 单元格。
 * 解决 table-layout:fixed + width:auto 时列宽可能被 td 内容撑开的问题。
 * @param {string} tableSelector - 表格 CSS 选择器，如 '#preview-table'
 */
function enforceColumnWidths(tableSelector) {
    var table = document.querySelector(tableSelector);
    if (!table) return;

    var ths = table.querySelectorAll('thead th');
    var rows = table.querySelectorAll('tbody tr');
    ths.forEach(function(th, colIndex) {
        // 读取 th 上由渲染或拖拽设置的 inline width
        var width = th.style.width;
        if (!width) return;
        rows.forEach(function(row) {
            var cell = row.children[colIndex];
            if (cell) {
                cell.style.width = width;
                cell.style.minWidth = width;
                cell.style.maxWidth = width; /* table cell 的 width 只作为最小宽度，必须加 max-width 才能强制约束 */
            }
        });
    });
}

/**
 * 为表格的前 freezeCount 列标记为冻结列（添加 frozen-col / frozen-col-last class）。
 * @param {string} tableSelector - 表格 CSS 选择器，如 '#preview-table'
 * @param {number} freezeCount - 需要冻结的列数
 */
function markFrozenColumns(tableSelector, freezeCount) {
    var table = document.querySelector(tableSelector);
    if (!table) return;

    var headerRow = table.querySelector('thead tr');
    if (!headerRow) return;

    var ths = headerRow.children;
    for (var i = 0; i < Math.min(freezeCount, ths.length); i++) {
        ths[i].classList.add('frozen-col');
        if (i === freezeCount - 1) {
            ths[i].classList.add('frozen-col-last');
        }
    }

    var tbodyRows = table.querySelectorAll('tbody tr');
    tbodyRows.forEach(function(row) {
        var cells = row.children;
        for (var i = 0; i < Math.min(freezeCount, cells.length); i++) {
            cells[i].classList.add('frozen-col');
            if (i === freezeCount - 1) {
                cells[i].classList.add('frozen-col-last');
            }
        }
    });
}

/**
 * 更新冻结列的 left 偏移量。
 * 当列宽发生变化时（拖拽、自适应、窗口缩放），需要重新计算。
 * @param {string} tableSelector - 表格 CSS 选择器，如 '#preview-table'
 */
function updateFrozenColumnOffsets(tableSelector) {
    var table = document.querySelector(tableSelector);
    if (!table) return;

    var headerRow = table.querySelector('thead tr');
    if (!headerRow) return;

    var frozenThs = headerRow.querySelectorAll('th.frozen-col');
    if (frozenThs.length === 0) return;

    var tbodyRows = table.querySelectorAll('tbody tr');
    var cumulativeLeft = 0;

    frozenThs.forEach(function(th) {
        th.style.left = cumulativeLeft + 'px';

        var colIndex = Array.prototype.indexOf.call(headerRow.children, th);

        tbodyRows.forEach(function(row) {
            var cell = row.children[colIndex];
            if (cell && cell.classList.contains('frozen-col')) {
                cell.style.left = cumulativeLeft + 'px';
            }
        });

        cumulativeLeft += th.offsetWidth;
    });
}

/* 窗口大小变化时重新计算冻结列偏移（debounce 150ms） */
(function() {
    var _frozenResizeTimer = null;
    window.addEventListener('resize', function() {
        if (_frozenResizeTimer) clearTimeout(_frozenResizeTimer);
        _frozenResizeTimer = setTimeout(function() {
            updateFrozenColumnOffsets('#preview-table');
        }, 150);
    });
})();

/**
 * 双击自动调整列宽：根据该列所有单元格的内容宽度设置列宽。
 */
function autoFitColumn(table, colIndex, th) {
    let maxWidth = 0;
    const measureEl = document.createElement('span');
    measureEl.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font-size:12px;font-family:inherit;';
    document.body.appendChild(measureEl);

    // 测量表头
    const thText = th.querySelector('.th-label');
    if (thText) {
        measureEl.textContent = thText.textContent.trim();
        maxWidth = Math.max(maxWidth, measureEl.offsetWidth);
    }

    // 测量该列所有单元格
    const rows = table.querySelectorAll('tbody tr');
    for (let i = 0; i < rows.length; i++) {
        const cell = rows[i].children[colIndex];
        if (cell) {
            measureEl.textContent = cell.textContent.trim();
            maxWidth = Math.max(maxWidth, measureEl.offsetWidth);
        }
    }

    document.body.removeChild(measureEl);

    // 加上一些内边距（最小30px，允许拉窄后双击也能保持较窄列宽）
    const newWidth = Math.max(30, maxWidth + 28);
    th.style.width = newWidth + 'px';
    th.style.minWidth = newWidth + 'px';

    // 同步更新所有行的对应单元格宽度（包含 fixed 布局，防止内容撑开列宽）
    for (let i = 0; i < rows.length; i++) {
        const cell = rows[i].children[colIndex];
        if (cell) {
            cell.style.width = newWidth + 'px';
            cell.style.minWidth = newWidth + 'px';
            cell.style.maxWidth = newWidth + 'px'; /* 表格 cell 的 width 仅作为最小宽度，必须用 max-width 强制约束 */
        }
    }

    // 双击自适应列宽后更新冻结列偏移
    if (table.id === 'preview-table') {
        updateFrozenColumnOffsets('#preview-table');
    }
}

// ====== 项目管理 ======

function initProjectBar() {
    // 按钮事件
    const btnSaveAs = document.getElementById("btn-project-save-as");
    const btnOpen = document.getElementById("btn-project-open");
    const btnSave = document.getElementById("btn-project-save");
    const btnSwitch = document.getElementById("btn-project-switch");
    const btnOpenInline = document.getElementById("btn-open-project-inline");

    if (btnSaveAs) btnSaveAs.addEventListener("click", showSaveProjectModal);
    if (btnOpen) btnOpen.addEventListener("click", showOpenProjectModal);
    if (btnOpenInline) btnOpenInline.addEventListener("click", showOpenProjectModal);
    if (btnSave) btnSave.addEventListener("click", () => saveCurrentProject(false));
    if (btnSwitch) btnSwitch.addEventListener("click", showOpenProjectModal);

    // 保存项目弹窗事件
    document.getElementById("project-save-close").addEventListener("click", closeSaveProjectModal);
    document.getElementById("project-save-cancel").addEventListener("click", closeSaveProjectModal);
    document.getElementById("project-save-confirm").addEventListener("click", createNewProject);

    // 项目管理弹窗事件
    document.getElementById("project-modal-close").addEventListener("click", closeProjectModal);
    document.getElementById("project-modal-cancel").addEventListener("click", closeProjectModal);

    // 检查当前是否有活动项目
    checkCurrentProject();
}

async function checkCurrentProject() {
    try {
        const r = await fetch(API.projectCurrent);
        const data = await r.json();
        if (data.success && data.active) {
            activeProject = { slug: data.active.slug, name: data.active.name, is_dirty: false };
            showProjectActiveState();
        }
    } catch (e) {
        // 忽略
    }
}

function showProjectActiveState() {
    document.getElementById("project-state-none").classList.add("hidden");
    document.getElementById("project-state-active").classList.remove("hidden");
    document.getElementById("project-name-display").textContent = activeProject.name;
    document.getElementById("project-dirty").classList.add("hidden");
}

function showProjectNoneState() {
    document.getElementById("project-state-none").classList.remove("hidden");
    document.getElementById("project-state-active").classList.add("hidden");
}

function collectFrontendState() {
    // 提取 company_status（精简：只发送每个item的关键字段，避免发送完整match数据）
    const previewData = previewItems.map(item => ({
        row_index: item.row_index,
        company_status: item.company_status || {},
    }));
    return {
        file_renames: fileRenames,
        current_view: currentView,
        manage_mode: manageMode,
        col_filters: colFilters,
        preview_items: previewData,
    };
}

function restoreFrontendState(viewState) {
    if (!viewState) return;
    if (viewState.file_renames) fileRenames = viewState.file_renames;
    if (viewState.current_view) {
        currentView = viewState.current_view;
        // 切换视图
        const toggleBtns = document.querySelectorAll(".view-toggle-btn");
        toggleBtns.forEach(b => b.classList.remove("active"));
        const targetBtn = document.querySelector(`.view-toggle-btn[data-view="${currentView}"]`);
        if (targetBtn) targetBtn.classList.add("active");
        renderPreviewTable();
    }
    if (viewState.manage_mode !== undefined) manageMode = viewState.manage_mode;
    if (viewState.col_filters) colFilters = viewState.col_filters;
}

function markProjectDirty() {
    if (!activeProject) return;
    activeProject.is_dirty = true;
    const indicator = document.getElementById("project-dirty");
    if (indicator) indicator.classList.remove("hidden");
    // 防抖自动保存
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(() => {
        saveCurrentProject(true);
    }, AUTO_SAVE_DELAY);
}

async function saveCurrentProject(silent) {
    if (!activeProject) return;
    const viewState = collectFrontendState();
    try {
        const r = await fetch(API.projectSave, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project_slug: activeProject.slug,
                file_renames: viewState.file_renames,
                view_state: viewState,
            }),
        });
        const data = await r.json();
        if (data.success) {
            activeProject.is_dirty = false;
            const indicator = document.getElementById("project-dirty");
            if (indicator) indicator.classList.add("hidden");
            if (!silent) showToast(data.message || "项目已保存", "success");
        } else if (!silent) {
            showToast(data.error || "保存失败", "error");
        }
    } catch (e) {
        if (!silent) showToast("保存失败: " + e.message, "error");
    }
}

async function createNewProject() {
    const nameInput = document.getElementById("project-save-name");
    const name = nameInput.value.trim();
    if (!name) { showToast("请输入项目名称", "error"); return; }
    const viewState = collectFrontendState();
    try {
        const r = await fetch(API.projectCreate, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project_name: name,
                file_renames: viewState.file_renames,
                view_state: viewState,
            }),
        });
        const data = await r.json();
        if (data.success) {
            activeProject = { slug: data.slug, name: name, is_dirty: false };
            showProjectActiveState();
            closeSaveProjectModal();
            showToast(data.message || "项目已保存", "success");
        } else {
            showToast(data.error || "创建失败", "error");
        }
    } catch (e) {
        showToast("创建失败: " + e.message, "error");
    }
}

function showSaveProjectModal() {
    // 填充当前进度摘要
    const summary = document.getElementById("project-save-summary");
    const totalItems = previewItems ? previewItems.length : 0;
    const matchedCount = matchResults ? matchResults.filter(r => r.status === "已获取").length : 0;
    summary.innerHTML = `
        <p>当前进度：<strong>${totalItems}</strong> 条需求，<strong>${scannedCount}</strong> 个扫描文件，<strong>${matchedCount}</strong> 已匹配</p>
    `;
    document.getElementById("project-save-name").value = activeProject ? activeProject.name : "";
    document.getElementById("project-save-modal").classList.remove("hidden");
    document.getElementById("project-save-name").focus();
}

function closeSaveProjectModal() {
    document.getElementById("project-save-modal").classList.add("hidden");
}

async function showOpenProjectModal() {
    // 如果有未保存更改，提示
    if (activeProject && activeProject.is_dirty) {
        if (!confirm("当前项目有未保存的更改，是否继续？\n\n（最近 3 秒内的更改可能已自动保存）")) return;
    }
    try {
        const r = await fetch(API.projectList);
        const data = await r.json();
        if (!data.success) { showToast(data.error, "error"); return; }
        renderProjectList(data.projects);
        document.getElementById("project-manage-modal").classList.remove("hidden");
    } catch (e) {
        showToast("获取项目列表失败: " + e.message, "error");
    }
}

function renderProjectList(projects) {
    const listEl = document.getElementById("project-list");
    const emptyEl = document.getElementById("project-list-empty");
    listEl.innerHTML = "";

    if (!projects || projects.length === 0) {
        emptyEl.classList.remove("hidden");
        return;
    }
    emptyEl.classList.add("hidden");

    projects.forEach(p => {
        const card = document.createElement("div");
        card.className = "project-card";
        const updatedStr = p.updated_at ? p.updated_at.substring(0, 16).replace("T", " ") : "未知";
        const total = p.total_count || 0;
        const matched = p.matched_count || 0;
        const pct = total > 0 ? Math.round(matched / total * 100) : 0;
        card.innerHTML = `
            <div class="project-card-info">
                <div class="project-card-name">&#128193; ${escapeHtml(p.name)}</div>
                <div class="project-card-meta">
                    最后保存：${updatedStr} &nbsp;|&nbsp;
                    ${p.item_count} 条需求 &middot; ${matched} 已匹配 &middot; ${pct}% 完成
                </div>
            </div>
            <div class="project-card-actions">
                <button class="btn btn-sm btn-primary project-load-btn" data-slug="${escapeHtml(p.slug)}">打开</button>
                <button class="btn btn-sm btn-outline project-delete-btn" data-slug="${escapeHtml(p.slug)}">删除</button>
            </div>
        `;
        listEl.appendChild(card);
    });

    // 绑定事件
    listEl.querySelectorAll(".project-load-btn").forEach(btn => {
        btn.addEventListener("click", () => loadProject(btn.dataset.slug));
    });
    listEl.querySelectorAll(".project-delete-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteProject(btn.dataset.slug));
    });
}

function closeProjectModal() {
    document.getElementById("project-manage-modal").classList.add("hidden");
}

async function loadProject(slug) {
    try {
        const r = await fetch(API.projectLoad, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_slug: slug }),
        });
        const data = await r.json();
        if (!data.success) { showToast(data.error || "加载失败", "error"); return; }

        // 恢复后端返回的数据到前端全局变量
        const tpl = data.checklist_template;
        if (tpl) {
            previewItems = tpl.items || [];
            previewCompanies = tpl.companies || [];
            companyNames = tpl.company_names || [];
        }
        matchResults = data.match_results;
        scanRoot = data.scan_root || "";
        scannedCount = (data.scanned_files ? data.scanned_files.length : 0) +
                       (data.scanned_folders ? data.scanned_folders.length : 0);
        fileRenames = data.file_renames || {};
        activeProject = { slug: slug, name: data.project_name, is_dirty: false };

        // 恢复前端视图状态
        restoreFrontendState(data.view_state);

        // 重新渲染界面
        if (previewItems.length > 0) {
            document.getElementById("template-done").classList.remove("hidden");
            document.getElementById("template-gen-area").classList.add("hidden");
            document.getElementById("template-summary").innerHTML =
                `已加载清单：<strong>${previewItems.length}</strong> 条需求，<strong>${companyNames.length}</strong> 家公司`;
            document.getElementById("template-badge").textContent = "已完成";
            document.getElementById("template-badge").style.background = "#00A86B";
        }
        if (scanRoot) {
            document.getElementById("folder-path").value = scanRoot;
            document.getElementById("folder-badge").textContent = "已扫描";
            document.getElementById("folder-badge").style.background = "#00A86B";
        }
        if (matchResults && matchResults.length > 0) {
            const matched = matchResults.filter(r => r.status === "已获取").length;
            document.getElementById("match-badge").textContent = `已匹配 ${matched}/${matchResults.length}`;
            document.getElementById("match-badge").style.background = "#00A86B";
        }

        // 从 matchResults 重建 company_status（已有手动设置的状态会被保留）
        if (matchResults && matchResults.length > 0 && previewItems.length > 0) {
            syncMatchToPreview();
        }

        renderPreviewTable();
        // 显示操作按钮（加载项目后需要手动显示）
        if (previewItems.length > 0) {
            document.getElementById("export-checklist-btn").classList.remove("hidden");
            document.getElementById("toggle-manage-btn").classList.remove("hidden");
        }
        if (scanRoot) {
            document.getElementById("organize-files-btn")?.classList.remove("hidden");
        }
        // 恢复行管理模式UI
        if (manageMode) {
            const toggleBtn = document.getElementById("toggle-manage-btn");
            toggleBtn.textContent = "✓ 退出编辑";
            toggleBtn.classList.add("btn-warning");
            document.getElementById("add-row-btn").classList.remove("hidden");
        }
        updateStatsFromMatchResults();
        updateWorkflowState();
        showProjectActiveState();
        closeProjectModal();
        showToast(`已加载项目「${data.project_name}」`, "success");
        console.log("[DEBUG] loadProject: 项目加载完成", data.project_name);
    } catch (e) {
        showToast("加载项目失败: " + e.message, "error");
    }
}

async function deleteProject(slug) {
    if (!confirm("确定要删除此项目吗？项目文件将被永久删除。")) return;
    try {
        const r = await fetch(API.projectDelete, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_slug: slug }),
        });
        const data = await r.json();
        if (data.success) {
            if (activeProject && activeProject.slug === slug) {
                activeProject = null;
                showProjectNoneState();
            }
            showToast("项目已删除", "success");
            // 刷新列表
            showOpenProjectModal();
        } else {
            showToast(data.error || "删除失败", "error");
        }
    } catch (e) {
        showToast("删除失败: " + e.message, "error");
    }
}

function updateStatsFromMatchResults() {
    if (!matchResults) return;
    const total = matchResults.length;
    const matched = matchResults.filter(r => r.status === "已获取").length;
    const partial = matchResults.filter(r => r.status === "部分获取").length;
    const missing = matchResults.filter(r => r.status === "未匹配").length;
    updateStats(matched + partial, total);
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ====== 提示消息 ======
function showToast(message, type) {
    console.log("[DEBUG] showToast:", type, message);
    const toast = document.getElementById("toast");
    const icon = type === "success" ? "✓" : "!";
    toast.textContent = `${icon} ${message}`;
    toast.className = "toast " + type + " show";
    setTimeout(() => toast.classList.remove("show"), 3000);
}
