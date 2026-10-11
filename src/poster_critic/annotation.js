/* Embedded into a standalone page under its exact CSP hash. No network requests. */
'use strict';
const filter = document.getElementById('filter');
if (filter) filter.addEventListener('change', () => document.querySelectorAll('.sample').forEach(card => {
  card.hidden = filter.value !== 'all' && card.dataset.state !== filter.value;
}));
const annotationData = document.getElementById('annotation-data');
if (annotationData) {
  try { startAnnotation(JSON.parse(annotationData.textContent)); }
  catch (error) {
    document.getElementById('boot-status').textContent = '交互启动失败：' + error.message + '。请用 Chrome 或 Safari 打开此文件。';
  }
}

function startAnnotation(template) {
  // Avoid structuredClone, which is unavailable in some embedded/older browsers.
  const clone = value => JSON.parse(JSON.stringify(value));
  const cards = template.items.map((item, index) => {
    const card = document.getElementById('sample-' + index);
    if (!card || card.dataset.sampleId !== item.sample_id) throw Error('样本与表单绑定不一致');
    return card;
  });
  let regions = template.items.map(item => clone(item.regions || []));
  const status = document.getElementById('boot-status');
  const preview = document.getElementById('json-preview');
  const notice = document.getElementById('save-status');
  const recover = document.getElementById('recover-draft');
  const draftKey = 'a4-annotation-v2:' + JSON.stringify([template.annotator,
    template.items.map(item => [item.sample_id, item.input_sha256, item.image_sha256])]);
  let storageAvailable = true;
  let pendingDraft = null;

  function collect() {
    const result = clone(template);
    result.items.forEach((item, index) => {
      const card = cards[index];
      item.decision = card.querySelector('[name=decision]').value || null;
      item.issue_codes = [...new Set(card.querySelector('[name=issues]').value.split(',').map(x => x.trim()).filter(Boolean))];
      const stage = card.querySelector('[name=repair]').value;
      item.repair_stages = stage === 'unassessed' ? null : stage === 'none' ? [] : [stage];
      item.evidence = card.querySelector('[name=evidence]').value;
      item.regions = clone(regions[index]);
    });
    return result;
  }

  // Validate everything before changing a single field, including the source hashes.
  function validateImport(value) {
    const keys = (object, allowed) => object && typeof object === 'object' && !Array.isArray(object)
      && Object.keys(object).every(key => allowed.includes(key));
    if (!keys(value, ['schema_version', 'annotator', 'items']) || value.schema_version !== 1
        || value.annotator !== template.annotator || !Array.isArray(value.items)
        || value.items.length !== template.items.length) throw Error('标注人、版本或样本数量不匹配');
    const indexed = new Map();
    for (const item of value.items) {
      if (!keys(item, ['sample_id', 'input_sha256', 'image_sha256', 'decision', 'issue_codes', 'repair_stages', 'evidence', 'regions'])
          || indexed.has(item.sample_id)) throw Error('样本字段非法或重复');
      indexed.set(item.sample_id, item);
    }
    const result = clone(template);
    result.items = template.items.map(expected => {
      const item = indexed.get(expected.sample_id);
      if (!item || item.input_sha256 !== expected.input_sha256 || item.image_sha256 !== expected.image_sha256)
        throw Error('图片或需求已变化，不能导入这份标注');
      if (![null, 'pass', 'fail', 'unknown'].includes(item.decision)
          || !Array.isArray(item.issue_codes) || item.issue_codes.some(code => typeof code !== 'string' || !code.trim())
          || typeof item.evidence !== 'string' || !(item.repair_stages === null
          || (Array.isArray(item.repair_stages) && item.repair_stages.length <= 1
              && item.repair_stages.every(stage => stage === 'layout')))) throw Error('结论、问题 code、依据或动作字段非法');
      const boxes = item.regions === undefined ? [] : item.regions;
      if (!Array.isArray(boxes) || boxes.length > 100) throw Error('问题框字段非法');
      for (const box of boxes) {
        const b = box.bbox_xyxy;
        if (!keys(box, ['bbox_xyxy', 'issue_code', 'note']) || !Array.isArray(b) || b.length !== 4
            || b.some(n => typeof n !== 'number' || !Number.isFinite(n))
            || !(0 <= b[0] && b[0] < b[2] && b[2] <= 720 && 0 <= b[1] && b[1] < b[3] && b[3] <= 1280)
            || (box.issue_code !== undefined && typeof box.issue_code !== 'string')
            || (box.note !== undefined && typeof box.note !== 'string')) throw Error('问题框坐标或说明非法');
      }
      return {...clone(item), regions: boxes.map(box => ({bbox_xyxy: clone(box.bbox_xyxy), issue_code: box.issue_code || '', note: box.note || ''}))};
    });
    return result;
  }

  function update(persist = true) {
    const result = collect();
    let filled = 0;
    result.items.forEach((item, index) => {
      const missing = !item.decision ? '未标注' : !item.evidence.trim() ? '待填写依据'
        : item.decision === 'fail' && !item.issue_codes.length ? '待填写问题 code' : '已完成';
      if (missing === '已完成') filled++;
      cards[index].dataset.state = item.decision || 'unassessed';
      const badge = cards[index].querySelector('.badge');
      badge.className = 'badge ' + (item.decision || 'unassessed');
      badge.textContent = (item.decision || '未标注') + ' · ' + missing;
    });
    preview.value = JSON.stringify(result, null, 2) + '\n';
    document.getElementById('progress').textContent = '已完成 ' + filled + ' / ' + result.items.length + ' · 问题框 ' + regions.reduce((n, boxes) => n + boxes.length, 0) + ' 个';
    if (persist && storageAvailable) {
      if (pendingDraft !== null) {
        notice.textContent = '原浏览器草稿仍保留。请先恢复原草稿，或下载当前填写后再导入；当前填写已同步到 JSON 预览。';
        return;
      }
      try {
        localStorage.setItem(draftKey, preview.value);
        recover.disabled = false;
        notice.textContent = '草稿已存入此浏览器；请下载 JSON 留作文件备份。';
      } catch (_) {
        storageAvailable = false;
        notice.textContent = '浏览器不允许本地草稿存储；请下载或复制下方 JSON。';
      }
    }
  }

  function drawRegions(index, temporary = null) {
    const svg = cards[index].querySelector('.annotation-layer');
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const addRect = (box, pending) => {
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', box[0]); rect.setAttribute('y', box[1]);
      rect.setAttribute('width', box[2] - box[0]); rect.setAttribute('height', box[3] - box[1]);
      if (pending) rect.setAttribute('class', 'pending');
      svg.appendChild(rect);
    };
    regions[index].forEach(box => addRect(box.bbox_xyxy, false));
    if (temporary) addRect(temporary, true);
  }

  function listRegions(index) {
    const list = cards[index].querySelector('.regions');
    list.textContent = '';
    regions[index].forEach((box, number) => {
      const row = document.createElement('div'); row.className = 'region-row';
      const title = document.createElement('p'); title.textContent = '框 ' + (number + 1) + ' · [' + box.bbox_xyxy.join(', ') + ']';
      row.appendChild(title);
      for (const [key, labelText] of [['issue_code', '框的问题 code（可选）'], ['note', '框的说明（可选）']]) {
        const label = document.createElement('label'); label.textContent = labelText;
        const input = document.createElement('input'); input.value = box[key];
        input.addEventListener('input', () => { box[key] = input.value; update(); });
        label.appendChild(input); row.appendChild(label);
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary';
      remove.textContent = '删除框 ' + (number + 1);
      remove.addEventListener('click', () => { regions[index].splice(number, 1); drawRegions(index); listRegions(index); update(); });
      row.appendChild(remove); list.appendChild(row);
    });
    cards[index].querySelector('.box-count').textContent = '已画 ' + regions[index].length + ' 个框（720 × 1280 坐标）';
  }

  const cancelDrawing = [];
  cards.forEach((card, index) => {
    for (const el of card.querySelectorAll('[name=decision],[name=repair],[name=issues],[name=evidence]')) {
      el.addEventListener('input', () => update()); el.addEventListener('change', () => update());
    }
    const svg = card.querySelector('.annotation-layer');
    const toggle = card.querySelector('.draw-toggle');
    let start = null;
    const point = event => {
      const bounds = svg.getBoundingClientRect();
      return [Math.round(Math.max(0, Math.min(720, (event.clientX - bounds.left) / bounds.width * 720))),
        Math.round(Math.max(0, Math.min(1280, (event.clientY - bounds.top) / bounds.height * 1280)))];
    };
    const rectangle = end => [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])];
    cancelDrawing[index] = () => { start = null; svg.classList.remove('drawing'); toggle.setAttribute('aria-pressed', 'false'); toggle.textContent = '开始画框'; drawRegions(index); };
    toggle.addEventListener('click', () => {
      if (svg.classList.contains('drawing')) cancelDrawing[index]();
      else { svg.classList.add('drawing'); toggle.setAttribute('aria-pressed', 'true'); toggle.textContent = '结束画框'; }
    });
    svg.addEventListener('pointerdown', event => {
      if (!svg.classList.contains('drawing') || event.button !== 0) return;
      if (regions[index].length >= 100) { notice.textContent = '每张图片最多 100 个框。'; return; }
      event.preventDefault(); start = point(event); svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener('pointermove', event => { if (start) drawRegions(index, rectangle(point(event))); });
    svg.addEventListener('pointerup', event => {
      if (!start) return;
      const box = rectangle(point(event)); start = null;
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      if (box[2] - box[0] >= 2 && box[3] - box[1] >= 2) regions[index].push({bbox_xyxy: box, issue_code: '', note: ''});
      drawRegions(index); listRegions(index); update();
    });
    svg.addEventListener('pointercancel', () => { start = null; drawRegions(index); });
    drawRegions(index); listRegions(index);
  });

  function applyImport(value) {
    const imported = validateImport(value);
    pendingDraft = null;
    imported.items.forEach((item, index) => {
      cancelDrawing[index]();
      const card = cards[index];
      card.querySelector('[name=decision]').value = item.decision || '';
      card.querySelector('[name=issues]').value = item.issue_codes.join(', ');
      card.querySelector('[name=repair]').value = item.repair_stages === null ? 'unassessed' : item.repair_stages.length ? item.repair_stages[0] : 'none';
      card.querySelector('[name=evidence]').value = item.evidence;
      regions[index] = clone(item.regions); drawRegions(index); listRegions(index);
    });
    update();
  }

  document.getElementById('import-json').addEventListener('click', () => {
    try { applyImport(JSON.parse(document.getElementById('import-text').value)); notice.textContent = '已导入，结论、依据和问题框均已恢复。'; }
    catch (error) { notice.textContent = '导入失败，当前填写保留：' + error.message; }
  });
  document.getElementById('import-file').addEventListener('change', event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 2 * 1024 * 1024) { notice.textContent = '导入失败：文件超过 2 MiB。'; event.target.value = ''; return; }
    const reader = new FileReader();
    reader.onload = () => {
      try { applyImport(JSON.parse(reader.result)); notice.textContent = '已从文件导入，结论、依据和问题框均已恢复。'; }
      catch (error) { notice.textContent = '导入失败，当前填写保留：' + error.message; }
    };
    reader.onerror = () => { notice.textContent = '无法读取文件，请粘贴 JSON 导入。'; };
    reader.readAsText(file); event.target.value = '';
  });
  recover.addEventListener('click', () => {
    try { const raw = localStorage.getItem(draftKey); if (!raw) throw Error('没有可恢复草稿'); applyImport(JSON.parse(raw)); notice.textContent = '已恢复此浏览器的草稿。'; }
    catch (error) { notice.textContent = '恢复失败，当前填写保留：' + error.message; }
  });
  document.getElementById('copy-json').addEventListener('click', () => {
    update(false); document.getElementById('json-panel').open = true; preview.focus(); preview.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (_) { /* Selected text is the fallback. */ }
    notice.textContent = copied ? 'JSON 已复制。' : 'JSON 已选中，请按 Ctrl/Cmd+C 复制并保存为 .json 文件。';
  });
  document.getElementById('export-labels').addEventListener('click', () => {
    update(); document.getElementById('json-panel').open = true;
    const filename = template.annotator.replace(/[^a-zA-Z0-9_-]/g, '_') + '.annotations.json';
    try {
      const url = URL.createObjectURL(new Blob([preview.value], {type: 'application/json;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url; link.download = filename;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      notice.textContent = '已发起下载 ' + filename + '；请检查下载目录。若没有文件，使用“复制 JSON”；下方预览就是本次导出内容。';
    } catch (error) { notice.textContent = '浏览器无法下载：' + error.message + '。请使用“复制 JSON”。'; }
    if (!collect().items.some(item => item.decision !== null)) notice.textContent += ' 当前尚无结论，导出内容是空白草稿。';
  });

  update(false);
  try {
    const saved = localStorage.getItem(draftKey);
    if (saved) { pendingDraft = saved; recover.disabled = false; notice.textContent = '检测到此浏览器的草稿，请点击“恢复浏览器草稿”；导入前当前表单保持原样。'; }
  } catch (_) { storageAvailable = false; notice.textContent = '本地草稿存储不可用，请下载或复制 JSON。'; }
  document.getElementById('export-labels').disabled = false;
  document.getElementById('copy-json').disabled = false;
  document.getElementById('import-json').disabled = false;
  status.textContent = '交互已启动 · 表单、画框和 JSON 预览会实时同步';
}
