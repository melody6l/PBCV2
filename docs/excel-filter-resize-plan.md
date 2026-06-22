# Excel 风格筛选按钮 + 列宽拖拽 实现计划

## Context

为 PBCV2 系统的三个数据视图（矩阵视图、清单视图、资料浏览视图）增加类似 Excel 的表头筛选功能和列宽拖拽功能。当前系统仅在矩阵视图有列宽拖拽，且没有任何筛选功能。

## 涉及文件

- `static/js/main.js` — 主要修改文件
- `static/css/style.css` — 新增筛选相关样式
- `templates/index.html` — 无需改动

---

## 一、表头筛选按钮（三个视图通用）

### 1.1 CSS 新增（style.css，在 `.result-table` 块之后）

需要新增以下样式区块：

- **`.th-content-wrap`** — flex 容器，包裹表头文字和筛选按钮，水平居中
- **`.th-label`** — 表头文字，支持溢出省略
- **`.col-filter-btn`** — 筛选按钮（▼图标），18×18px，圆角半透明，悬停高亮；激活态（`.active`）显示黄色；**必须设置 `cursor: pointer`** 覆盖 th 的 `cursor: col-resize`
- **`.col-filter-dropdown`** — 固定定位下拉面板，白色背景、圆角、阴影，z-index: 10001；包含：
  - `.col-filter-search` — 搜索输入框
  - `.col-filter-actions` — 「全选」「取消全选」按钮行
  - `.col-filter-list` — 可滚动的复选框列表
  - `.col-filter-item` — 每个筛选项（复选框 + 标签 + 计数）
  - `.col-filter-empty` — 空状态提示

### 1.2 JS 新增函数（main.js）

全部新增在 `renderPreviewTable()` 之后、`renderMatrixView()` 之前（约第250行）。

1. **`ensureFilterDropdown()`** — 创建全局唯一的筛选下拉面板 DOM，追加到 `<body>`，绑定搜索输入、全选/取消全选、外部点击关闭事件，返回面板引用

2. **`showFilterDropdown(th, tableId, colIndex)`** — 定位并显示下拉面板
   - 从当前表格该列提取唯一值（调用 `buildColumnValues`）
   - 重建复选框列表，反映当前 `colFilters` 中的选中状态
   - 计算位置（基于 th 的 `getBoundingClientRect()`），确保不超出视口
   - 显示面板，高亮对应筛选按钮

3. **`buildColumnValues(table, colIndex)`** — 遍历 `<tbody>` 中所有行的第 colIndex 列，提取唯一文本值及计数，按中文排序返回 `[{value, count}]`

4. **`applyFilters(tableId)`** — 核心过滤逻辑
   - 遍历表格所有 `<tbody> tr` 行
   - 检查 `colFilters` 中该表格的所有筛选条件（key 格式：`"tableId:colIndex"`，value 为 `Set`）
   - 匹配的行 `display: ''`，不匹配的行 `display: 'none'`
   - 调用 `updateAllFilterIndicators` 更新按钮状态

5. **`initColumnFilters()`** — 事件委托，在 `document` 上监听 `.col-filter-btn` 点击，解析 `data-col` 和所在表格 ID，调用 `showFilterDropdown`

6. **辅助函数**：
   - `setAllCheckboxes(dropdown, checked)` — 全选/取消全选
   - `filterDropdownSearch(dropdown, query)` — 搜索过滤列表项
   - `updateAllFilterIndicators(tableId)` — 更新该表格所有筛选按钮的 `.active` 状态

### 1.3 修改现有渲染函数

#### `renderMatrixView()`（~第296行）

将表头从纯文本改为带筛选按钮的包裹结构：

```js
// 旧：
headHtml += "<th>序号</th><th>科目</th><th>所需PBC</th><th>需求资料</th>";

// 新：每个 th 包含 .th-content-wrap > .th-label + .col-filter-btn
headHtml += '<th><div class="th-content-wrap"><span class="th-label">序号</span><button class="col-filter-btn" data-col="0" title="筛选">▼</button></div></th>';
headHtml += '<th><div class="th-content-wrap"><span class="th-label">科目</span><button class="col-filter-btn" data-col="1" title="筛选">▼</button></div></th>';
headHtml += '<th><div class="th-content-wrap"><span class="th-label">所需PBC</span><button class="col-filter-btn" data-col="2" title="筛选">▼</button></div></th>';
headHtml += '<th><div class="th-content-wrap"><span class="th-label">需求资料</span><button class="col-filter-btn" data-col="3" title="筛选">▼</button></div></th>';
```

公司列（动态）：
```js
companyNames.forEach((name, i) => {
    const colIdx = 4 + i;
    headHtml += `<th class="company-col"><div class="th-content-wrap"><span class="th-label">${name}</span><button class="col-filter-btn" data-col="${colIdx}" title="筛选">▼</button></div></th>`;
});
```

固定列 data-col：0=序号, 1=科目, 2=所需PBC, 3=需求资料；公司列从 4 开始递增。

#### `renderListView()`（~第440行）

同理，6 个固定列全部改为带筛选按钮的包裹结构，data-col 为 0~5：

```js
thead.innerHTML = '<tr>' +
    '<th><div class="th-content-wrap"><span class="th-label">科目</span><button class="col-filter-btn" data-col="0" title="筛选">▼</button></div></th>' +
    '<th><div class="th-content-wrap"><span class="th-label">所需PBC</span><button class="col-filter-btn" data-col="1" title="筛选">▼</button></div></th>' +
    '<th><div class="th-content-wrap"><span class="th-label">需求资料</span><button class="col-filter-btn" data-col="2" title="筛选">▼</button></div></th>' +
    '<th><div class="th-content-wrap"><span class="th-label">公司</span><button class="col-filter-btn" data-col="3" title="筛选">▼</button></div></th>' +
    '<th><div class="th-content-wrap"><span class="th-label">是否获取</span><button class="col-filter-btn" data-col="4" title="筛选">▼</button></div></th>' +
    '<th><div class="th-content-wrap"><span class="th-label">文件</span><button class="col-filter-btn" data-col="5" title="筛选">▼</button></div></th>' +
    '</tr>';
```

#### `renderBrowseView()`（~第342-364行）

由于使用 DOM API 创建 th，新增一个辅助函数 `wrapThWithFilter(th, colIndex)`，在每个 th 创建后调用来注入筛选按钮包裹：

```js
function wrapThWithFilter(th, colIndex) {
    const wrap = document.createElement('div');
    wrap.className = 'th-content-wrap';
    const label = document.createElement('span');
    label.className = 'th-label';
    label.textContent = th.textContent;
    th.textContent = '';
    const btn = document.createElement('button');
    btn.className = 'col-filter-btn';
    btn.dataset.col = colIndex;
    btn.title = '筛选';
    btn.textContent = '▼';
    wrap.appendChild(label);
    wrap.appendChild(btn);
    th.appendChild(wrap);
}
```

列索引：0=序号, 1..N=文件夹列 (N = data.folder_levels), N+1=文件名, N+2=匹配状态, N+3=关联需求。

### 1.4 页面初始化

在 `DOMContentLoaded` 中添加：
```js
initColumnFilters();
```

---

## 二、资料浏览视图列宽拖拽

### 2.1 重构 `initColumnResize()`（第2375-2396行）

将现有函数改为接受 `tableSelector` 参数，支持多个表格。使用 `_resizeInitialized` Set 确保每个表格只初始化一次。

```js
const _resizeInitialized = new Set();
let _globalResizeInit = false;

function initColumnResize(tableSelector) {
    if (_resizeInitialized.has(tableSelector)) return;
    _resizeInitialized.add(tableSelector);

    const table = document.querySelector(tableSelector);
    if (!table) return;

    // 共享的拖拽状态
    let resizing = false, thElement = null, startX = 0, startWidth = 0, colIndex = -1;

    // 每个表格绑定 mousedown（事件委托在 th 右边缘 8px 内触发）
    table.addEventListener('mousedown', e => {
        // 如果点击的是筛选按钮，不启动拖拽
        if (e.target.closest('.col-filter-btn')) return;

        const th = e.target.closest('th');
        if (!th) return;
        const rect = th.getBoundingClientRect();
        if (e.clientX > rect.right - 8) {
            resizing = true;
            thElement = th;
            startX = e.clientX;
            startWidth = th.offsetWidth;
            colIndex = Array.from(th.parentElement.children).indexOf(th);
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        }
    });

    // 光标提示：鼠标接近 th 右边缘时显示 col-resize
    table.addEventListener('mousemove', e => {
        if (resizing) return;
        const th = e.target.closest('th');
        if (!th) { table.style.cursor = ''; return; }
        const rect = th.getBoundingClientRect();
        if (e.clientX > rect.right - 8 && !e.target.closest('.col-filter-btn')) {
            table.style.cursor = 'col-resize';
        } else {
            table.style.cursor = '';
        }
    });

    // 全局 mousemove/mouseup 只绑定一次
    if (!_globalResizeInit) {
        _globalResizeInit = true;
        document.addEventListener('mousemove', e => {
            if (!resizing || !thElement) return;
            const newWidth = Math.max(50, startWidth + e.clientX - startX);
            thElement.style.width = newWidth + 'px';
            thElement.style.minWidth = newWidth + 'px';

            // 对于 table-layout: auto（浏览视图），同步设置整列 td 宽度
            const tbl = thElement.closest('table');
            if (tbl && getComputedStyle(tbl).tableLayout !== 'fixed') {
                tbl.querySelectorAll('tbody tr').forEach(row => {
                    const cell = row.children[colIndex];
                    if (cell) {
                        cell.style.width = newWidth + 'px';
                        cell.style.minWidth = newWidth + 'px';
                    }
                });
            }
        });
        document.addEventListener('mouseup', () => {
            if (resizing) {
                resizing = false;
                thElement = null;
                colIndex = -1;
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            }
        });
    }
}
```

**关键差异**：
- `table-layout: fixed`（矩阵/清单视图）：仅设置 `th.style.width` 即可，浏览器自动同步 td
- `table-layout: auto`（浏览视图）：需同时设置该列所有 `<th>` 和 `<tbody>` 中每一行对应 `<td>` 的 `width` 和 `min-width`

**冲突处理**：mousedown 时检查 `e.target.closest('.col-filter-btn')`，如果是筛选按钮则不启动拖拽。

### 2.2 调用位置修改

- `DOMContentLoaded`（第46行）：`initColumnResize();` → `initColumnResize('#preview-table');`
- `renderBrowseView()` 中表格数据填充完成后（~第424行，`catch` 之前）：添加 `initColumnResize('#browse-view-table');`（幂等，多次调用安全）

### 2.3 CSS 配合

`.col-filter-btn` 需显式设置 `cursor: pointer`，覆盖 th 的 `cursor: col-resize`，避免筛选按钮区域显示错误的鼠标样式。

---

## 三、完整实现步骤（按顺序执行）

### Step 1：CSS（style.css）

在 `.result-table` 相关样式块之后（约第577行后），追加「列筛选」和「列宽拖拽增强」的所有样式。具体样式参照 1.1 节描述。

### Step 2：新增筛选 JS 函数（main.js）

在 `renderPreviewTable()` 函数之后（约第250行），新增以下函数：
- `ensureFilterDropdown()`
- `showFilterDropdown(th, tableId, colIndex)`
- `buildColumnValues(table, colIndex)`
- `applyFilters(tableId)`
- `setAllCheckboxes(dropdown, checked)`
- `filterDropdownSearch(dropdown, query)`
- `updateAllFilterIndicators(tableId)`
- `initColumnFilters()`
- `wrapThWithFilter(th, colIndex)` — 供浏览视图使用

### Step 3：修改渲染函数（main.js）

- `renderMatrixView()` — 修改表头 HTML 字符串，加入筛选按钮包裹
- `renderListView()` — 修改表头 HTML 字符串，加入筛选按钮包裹
- `renderBrowseView()` — 每个 th 创建后调用 `wrapThWithFilter()`，表格填充后调用 `initColumnResize('#browse-view-table')`

### Step 4：替换列宽拖拽函数（main.js）

删除旧的 `initColumnResize()`（第2375-2396行），替换为 2.1 节的新实现。

### Step 5：修改初始化调用（main.js）

在 `DOMContentLoaded` 中：
```js
initColumnResize();        // 旧
// 改为：
initColumnResize('#preview-table');  // 新

// 新增：
initColumnFilters();       // 筛选事件委托
```

---

## 四、验证方案

| # | 测试项 | 预期行为 |
|---|--------|----------|
| 1 | 矩阵视图筛选 | 点击表头 ▼ → 弹出下拉面板 → 勾选/取消值 → 行实时过滤 → 再次点击关闭面板 |
| 2 | 清单视图筛选 | 同上，各列独立筛选 |
| 3 | 资料浏览视图筛选 | 同上，文件夹层级列筛选正常 |
| 4 | 跨视图筛选保持 | 矩阵视图设筛选 → 切到清单再切回 → 筛选状态保留 |
| 5 | 矩阵视图列宽拖拽 | 拖拽 th 右边缘 → 列宽变化，最小 50px |
| 6 | 浏览视图列宽拖拽 | 拖拽 th 右边缘 → 整列（th + 所有 td）宽度同步变化 |
| 7 | 筛选/拖拽不冲突 | 点击筛选按钮不触发列宽拖拽 |
| 8 | 筛选按钮激活态 | 有筛选的列按钮显示黄色高亮（`.active`） |

---

## 五、关键技术要点

1. **colFilters 数据结构**：`{ "tableId:colIndex": Set(["值1", "值2", ...]) }` — key 中冒号分隔表格ID和列索引
2. **筛选下拉面板是单例**：全局共享一个 DOM，切换列时更新内容，避免重复创建
3. **列宽拖拽幂等**：`_resizeInitialized` Set 防止同一表格重复绑定事件
4. **全局监听器共享**：`_globalResizeInit` 确保 `document` 上的 mousemove/mouseup 只绑定一次
5. **auto vs fixed 布局**：通过 `getComputedStyle(table).tableLayout` 判断，auto 布局需同步设置整列 td 宽度
6. **筛选用 textContent**：过滤时取 `cell.textContent`（去除 HTML 标签），对状态列得到 "Y"/"N"/"不完整"/"N/A" 原文
