'use strict';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const colors = ['#ad98e8','#70c4b8','#e4a66b','#d9859c','#76a2d9','#cbc06e','#bd7fc1','#7db685','#cb826e','#6aacbd'];
const state = {status:null,volume:null,node:1,offset:0,tree:null,request:0,online:false,nodes:new Map(),branches:new Map()};
let pollTimer;
let toastTimer;

function format(bytes, precision = 1) {
  if (!Number.isFinite(bytes)) return '—';
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB','PB'];
  const exponent = Math.min(5, Math.max(0, Math.floor(Math.log10(Math.max(1, bytes)) / 3)));
  return `${(bytes / 1000 ** exponent).toLocaleString(undefined, {maximumFractionDigits: exponent ? precision : 0})} ${units[exponent]}`;
}

function count(value) { return Number(value || 0).toLocaleString(); }

function ago(timestamp) {
  if (!timestamp) return 'Not scanned';
  const seconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (seconds < 60) return 'Scanned just now';
  if (seconds < 3600) return `Scanned ${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `Scanned ${Math.floor(seconds / 3600)} hr ago`;
  return `Scanned ${Math.floor(seconds / 86400)} days ago`;
}

function selectedVolume() { return state.status?.volumes.find(volume => volume.id === state.volume); }

async function api(path, body) {
  const options = {signal: AbortSignal.timeout(15000)};
  if (body) Object.assign(options, {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Orbit-Request':'1'},
    body:JSON.stringify(body)
  });
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').hidden = true, 3000);
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (state.status?.volumes.some(volume => volume.id === params.get('volume'))) state.volume = params.get('volume');
  state.node = Math.max(1, Number(params.get('node')) || 1);
}

function writeHash() {
  history.replaceState(null, '', `#${new URLSearchParams({volume:state.volume,node:state.node})}`);
}

function renderOptions() {
  const groups = new Map();
  for (const volume of state.status.volumes) {
    const group = volume.group || 'File systems';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(volume);
  }
  $('volume-select').innerHTML = [...groups].map(([group, volumes]) =>
    `<optgroup label="${esc(group)}">${volumes.map(volume =>
      `<option value="${esc(volume.id)}">${esc(volume.name)} · ${volume.online ? `${Math.round(volume.percent)}% used` : 'offline'}</option>`
    ).join('')}</optgroup>`
  ).join('');
  $('volume-select').value = state.volume;
}

function renderStatus() {
  const volume = selectedVolume();
  if (!volume) return;
  const job = volume.job || {};
  const busy = ['queued','scanning','cancelling'].includes(job.status);

  renderOptions();
  $('volume-title').textContent = volume.name;
  $('volume-path').textContent = volume.mount;
  $('scan-time').textContent = volume.scan ? ago(volume.scan.completed_at) : busy ? 'First scan in progress' : 'Not scanned';
  $('used-summary').textContent = `${format(volume.used)} used`;
  $('free-summary').textContent = `${format(volume.available)} free of ${format(volume.total)}`;
  const usedPercent = Math.min(100, volume.percent || 0);
  $('used-meter').style.width = `${usedPercent}%`;
  $('used-meter').parentElement.setAttribute('aria-valuenow', String(Math.round(usedPercent)));

  $('scan-button').disabled = busy || !state.online || !volume.online;
  $('scan-button').classList.toggle('busy', busy);
  $('scan-button').querySelector('span').textContent = job.status === 'queued' ? 'Queued' : busy ? 'Scanning' : 'Scan';
  $('scan-progress').hidden = !busy;
  $('cancel-button').disabled = job.status === 'cancelling';
  $('cancel-button').textContent = job.status === 'cancelling' ? 'Cancelling…' : 'Cancel';
  $('progress-detail').textContent = job.status === 'queued'
    ? 'Waiting for the current scan.'
    : `${count(job.files)} files · ${format(job.bytes || 0)}`;

  const warnings = [];
  if (!volume.online) warnings.push(`Target unavailable: ${esc(volume.error || 'unknown error')}.`);
  if (job.status === 'error') warnings.push(`Scan failed: ${esc(job.error)}.`);
  if (job.status === 'cancelled') warnings.push('Scan cancelled. The previous map is unchanged.');
  if (volume.scan?.skipped) {
    warnings.push(`<details><summary>${count(volume.scan.skipped)} entries could not be read.</summary><ul>${volume.scan.errors.map(error => `<li>${esc(error.path)} — ${esc(error.reason)}</li>`).join('')}</ul></details>`);
  }
  $('scan-warning').innerHTML = warnings.join('');
  $('scan-warning').hidden = !warnings.length;

  if (!state.tree && !volume.scan) emptyChart(job.status === 'error' ? 'Scan failed' : 'Waiting for scan');
}

async function refreshStatus() {
  clearTimeout(pollTimer);
  try {
    const data = await api('/api/status');
    const first = !state.status;
    const previousScan = selectedVolume()?.scan?.completed_at || null;
    state.status = data;
    state.online = true;
    $('connection-error').hidden = !data.capacity_error;
    if (data.capacity_error) $('connection-error').textContent = 'Capacity update delayed. Showing the last reading.';

    if (first) {
      state.volume = data.volumes[0]?.id;
      readHash();
    }
    const volume = selectedVolume();
    const completedScan = volume?.scan?.completed_at || null;
    renderStatus();

    if (first || completedScan !== previousScan) {
      if (!first && previousScan && completedScan !== previousScan) {
        state.node = 1;
        state.offset = 0;
        toast('Scan complete');
      }
      if (volume?.scan) await loadTree();
    }
  } catch (error) {
    state.online = false;
    $('connection-error').textContent = 'Connection interrupted. Retrying…';
    $('connection-error').hidden = false;
    $('scan-button').disabled = true;
  } finally {
    const busy = state.status?.volumes.some(volume => ['queued','scanning','cancelling'].includes(volume.job.status));
    pollTimer = setTimeout(refreshStatus, busy ? 2500 : 15000);
  }
}

function chooseVolume(id) {
  if (id === state.volume) return;
  state.volume = id;
  state.node = 1;
  state.offset = 0;
  state.tree = null;
  state.request++;
  emptyChart(selectedVolume()?.scan ? 'Loading…' : 'Waiting for scan');
  renderStatus();
  if (selectedVolume()?.scan) loadTree();
  writeHash();
}

function emptyChart(message) {
  $('sunburst').innerHTML = '';
  $('chart-placeholder').hidden = false;
  $('chart-placeholder').querySelector('strong').textContent = message;
  $('chart-center').disabled = true;
  $('breadcrumbs').innerHTML = `${icon('disk')}<span class="small text-body-secondary">${esc(selectedVolume()?.mount || '/')}</span>`;
  $('directory-title').textContent = 'Contents';
  $('directory-count').textContent = '';
  $('directory-list').innerHTML = '<div class="small text-body-secondary text-center py-5">No index available.</div>';
  $('pagination').hidden = true;
  $('up-button').disabled = true;
}

async function loadTree() {
  const request = ++state.request;
  const volume = state.volume;
  try {
    const params = new URLSearchParams({volume,node:state.node,offset:state.offset});
    const data = await api(`/api/tree?${params}`);
    if (request !== state.request || volume !== state.volume) return;
    state.tree = data;
    renderTree();
    writeHash();
  } catch (error) {
    if (request !== state.request) return;
    if (state.node !== 1) {
      state.node = 1;
      state.offset = 0;
      return loadTree();
    }
    state.tree = null;
    emptyChart(error.message);
  }
}

function navigate(node) {
  state.node = Number(node);
  state.offset = 0;
  loadTree();
}

function renderTree() {
  const data = state.tree;
  $('chart-placeholder').hidden = data.node.size > 0;
  if (!data.node.size) $('chart-placeholder').querySelector('strong').textContent = 'Empty folder';

  const crumbs = [...data.ancestors, data.node];
  $('breadcrumbs').innerHTML = `${icon('disk')}${crumbs.map((item, index) =>
    `${index ? icon('chevron') : ''}<button type="button" class="btn btn-link btn-sm link-body-emphasis text-decoration-none px-1 py-0" data-node="${item.id}">${esc(item.id === 1 ? selectedVolume().mount : item.name)}</button>`
  ).join('')}`;
  $('breadcrumbs').scrollLeft = $('breadcrumbs').scrollWidth;

  $('directory-title').textContent = data.node.id === 1 ? 'Contents' : data.node.name;
  $('directory-count').textContent = `${count(data.count)} items`;
  $('up-button').disabled = data.node.parent === null;
  $('chart-center').disabled = data.node.parent === null;
  $('pagination').hidden = data.count <= 60;
  $('page-count').textContent = `${data.offset + 1}–${Math.min(data.count, data.offset + 60)} of ${count(data.count)}`;
  $('previous-page').disabled = data.offset === 0;
  $('next-page').disabled = data.offset + 60 >= data.count;

  drawSunburst(data.chart);
  renderDirectory();
  resetCenter();
}

function tint(hex, depth, sibling) {
  if (!depth) return hex;
  const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16));
  const ratio = (depth === 1 ? -.08 : -.2) + (sibling % 3) * .08;
  return `rgb(${channels.map(value => Math.round(ratio > 0 ? value + (255-value)*ratio : value*(1+ratio))).join(',')})`;
}

function arc(start, end, inner, outer) {
  const gap = Math.min(.009, (end - start) * .14);
  start += gap;
  end -= gap;
  if (end - start > Math.PI * 2 - .0001) end = start + Math.PI * 2 - .0001;
  const point = (angle, radius) => `${(320 + Math.cos(angle)*radius).toFixed(3)},${(320 + Math.sin(angle)*radius).toFixed(3)}`;
  const large = end - start > Math.PI ? 1 : 0;
  return `M${point(start,outer)} A${outer},${outer} 0 ${large} 1 ${point(end,outer)} L${point(end,inner)} A${inner},${inner} 0 ${large} 0 ${point(start,inner)} Z`;
}

function drawSunburst(root) {
  const nodes = new Map();
  const branches = new Map();
  const parts = [110,175,239,303].map(radius => `<circle class="ring-guide" cx="320" cy="320" r="${radius}"/>`);

  function addLevel(children, from, to, total, depth, parentBranch) {
    let current = from;
    children?.forEach((item, index) => {
      const angle = total > 0 ? (to - from) * (item.size || 0) / total : 0;
      const end = current + angle;
      const branch = depth === 0 ? index : parentBranch;
      if (angle > .0015 && depth < 3) {
        const key = String(item.id);
        const color = item.kind === 'aggregate' ? '#56635d' : tint(colors[branch % colors.length], depth, index);
        const action = item.kind === 'directory' || item.kind === 'aggregate';
        nodes.set(key, item);
        branches.set(key, branch);
        parts.push(`<path class="chart-arc" d="${arc(current,end,[110,175,239][depth],[171,235,299][depth])}" fill="${color}" data-key="${esc(key)}" data-branch="${branch}" ${action ? `tabindex="0" role="button" aria-label="${esc(item.name)}, ${format(item.size)}"` : ''}/>`);
        addLevel(item.children, current, end, item.size, depth + 1, branch);
      }
      current = end;
    });
  }

  addLevel(root.children, -Math.PI/2, Math.PI*1.5, root.size, 0, 0);
  $('sunburst').innerHTML = parts.join('');
  state.nodes = nodes;
  state.branches = branches;
}

function renderDirectory() {
  const data = state.tree;
  if (!data.children.length) {
    $('directory-list').innerHTML = '<div class="small text-body-secondary text-center py-5">Empty folder.</div>';
    return;
  }
  $('directory-list').innerHTML = data.children.map((item, index) => {
    const branch = state.branches.get(String(item.id));
    const color = colors[(branch ?? index + state.offset) % colors.length];
    const contents = `${icon(item.kind === 'directory' || item.kind === 'excluded' ? 'folder' : 'file')}<span class="small text-truncate flex-grow-1">${esc(item.name)}</span><span class="small text-body-secondary text-nowrap">${format(item.size)}</span>${item.kind === 'directory' ? icon('chevron') : ''}`;
    return item.kind === 'directory'
      ? `<button type="button" class="directory-row list-group-item list-group-item-action d-flex align-items-center gap-2 px-1 py-3 text-start" data-item="${item.id}" style="--item-color:${color}">${contents}</button>`
      : `<div class="directory-row list-group-item d-flex align-items-center gap-2 px-1 py-3" data-item="${item.id}" style="--item-color:${color}">${contents}</div>`;
  }).join('');
  $('directory-list').scrollTop = 0;
}

function showCenter(item) {
  $('center-value').textContent = format(item.size);
  $('center-name').textContent = item.id === 1 ? selectedVolume().name : item.name;
}

function resetCenter() {
  if (state.tree) showCenter(state.tree.node);
  $('sunburst').querySelectorAll('.dimmed').forEach(element => element.classList.remove('dimmed'));
  $('directory-list').querySelectorAll('.active').forEach(element => element.classList.remove('active'));
}

function hover(item, target) {
  if (!item) return;
  showCenter(item);
  const branch = target?.dataset.branch;
  if (branch !== undefined) $('sunburst').querySelectorAll('.chart-arc').forEach(element => element.classList.toggle('dimmed', element.dataset.branch !== branch));
  $('directory-list').querySelectorAll('[data-item]').forEach(element => element.classList.toggle('active', element.dataset.item === String(item.id)));
}

function activate(item) {
  if (!item) return;
  if (item.kind === 'directory') navigate(item.id);
  else if (item.kind === 'aggregate') item.parent !== state.node ? navigate(item.parent) : toast('Smaller items are in the list.');
}

$('volume-select').addEventListener('change', event => chooseVolume(event.target.value));
$('scan-button').addEventListener('click', async () => {
  try {
    $('scan-button').disabled = true;
    await api('/api/scan', {volume:state.volume});
    await refreshStatus();
  } catch (error) { toast(error.message); }
});
$('cancel-button').addEventListener('click', async () => {
  try {
    $('cancel-button').disabled = true;
    await api('/api/cancel', {volume:state.volume});
    await refreshStatus();
  } catch (error) { toast(error.message); }
});
$('breadcrumbs').addEventListener('click', event => {
  const button = event.target.closest('[data-node]');
  if (button) navigate(button.dataset.node);
});
$('up-button').addEventListener('click', () => { if (state.tree?.node.parent) navigate(state.tree.node.parent); });
$('chart-center').addEventListener('click', () => { if (state.tree?.node.parent) navigate(state.tree.node.parent); });
$('directory-list').addEventListener('click', event => {
  const row = event.target.closest('[data-item]');
  if (row) activate(state.tree.children.find(item => String(item.id) === row.dataset.item));
});
$('directory-list').addEventListener('pointerover', event => {
  const row = event.target.closest('[data-item]');
  if (row) hover(state.tree?.children.find(item => String(item.id) === row.dataset.item), $('sunburst').querySelector(`[data-key="${row.dataset.item}"]`));
});
$('directory-list').addEventListener('pointerleave', resetCenter);
$('sunburst').addEventListener('pointerover', event => {
  const segment = event.target.closest('[data-key]');
  if (segment) hover(state.nodes.get(segment.dataset.key), segment);
});
$('sunburst').addEventListener('pointerleave', resetCenter);
$('sunburst').addEventListener('click', event => {
  const segment = event.target.closest('[data-key]');
  if (segment) activate(state.nodes.get(segment.dataset.key));
});
$('sunburst').addEventListener('focusin', event => {
  const segment = event.target.closest('[data-key]');
  if (segment) hover(state.nodes.get(segment.dataset.key), segment);
});
$('sunburst').addEventListener('focusout', resetCenter);
$('sunburst').addEventListener('keydown', event => {
  const segment = event.target.closest('[data-key]');
  if (segment && ['Enter',' '].includes(event.key)) {
    event.preventDefault();
    activate(state.nodes.get(segment.dataset.key));
  }
});
$('previous-page').addEventListener('click', () => { state.offset = Math.max(0, state.offset - 60); loadTree(); });
$('next-page').addEventListener('click', () => { state.offset += 60; loadTree(); });
window.addEventListener('online', refreshStatus);
window.addEventListener('hashchange', () => {
  if (!state.status) return;
  const oldVolume = state.volume;
  readHash();
  state.offset = 0;
  state.tree = null;
  if (oldVolume !== state.volume) renderStatus();
  emptyChart(selectedVolume()?.scan ? 'Loading…' : 'Waiting for scan');
  if (selectedVolume()?.scan) loadTree();
});

refreshStatus();
