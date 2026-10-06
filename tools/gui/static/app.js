const $ = (id) => document.getElementById(id);

const VIEWS = [
  { id: 'all', label: 'Overview' },
  { id: 'iv', label: 'V–I plane' },
  { id: 'series', label: 'Time series' },
];

const PREFS_KEY = 'gan-mppt-bench';

const state = {
  schema: null,
  panels: [],
  seriesBuffer: [],
  seriesSnapshot: null,
  seriesPausedAt: null,
  nextPlotId: 1,
  activePlot: null,
  seriesExpanded: false,
  iv: null,
  ivPairs: new Map(),
  ivPausedAt: null,
  fields: new Map(),
  readouts: new Map(),
  view: 'all',
  now: 0,
  wall: performance.now(),
  connected: false,
  sequence: { state: 'idle' },
  history: [],
  historyAt: 0,
};

let prefs = {};

function loadPrefs() {
  try {
    prefs = JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
  } catch (err) {
    prefs = {};
  }
}

function savePrefs() {
  prefs.view = state.view;
  prefs.readoutsHidden = $('readouts').hidden;
  if (state.schema) {
    prefs.plots = state.panels.map((p) => ({ label: p.label, fields: p.keys,
                                           minimised: p.minimised }));
    prefs.activePlotIndex = state.panels.findIndex((p) => p.id === state.activePlot);
  }
  for (const id of ['window', 'persist', 'point-size', 'fade', 'x-max', 'y-max']) {
    prefs[id] = $(id).value;
  }
  for (const id of ['show-trail', 'show-best', 'show-iso', 'lock-axes']) {
    prefs[id] = $(id).checked;
  }
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch (err) {   }
}

function applyPrefsToInputs() {
  for (const id of ['window', 'persist', 'point-size', 'fade', 'x-max', 'y-max']) {
    if (prefs[id] != null) $(id).value = prefs[id];
  }
  for (const id of ['show-trail', 'show-best', 'show-iso', 'lock-axes']) {
    if (prefs[id] != null) $(id).checked = prefs[id];
  }
}

let socket = null;

function openSocket() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.onmessage = (event) => onMessage(JSON.parse(event.data));
  socket.onclose = () => {
    setPill('err', 'server gone');
    setTimeout(openSocket, 1500);
  };
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function onMessage(msg) {
  if (msg.type === 'schema') buildFromSchema(msg);
  else if (msg.type === 'data') onData(msg);
  else if (msg.type === 'status') onStatus(msg);
  else if (msg.type === 'log') msg.lines.forEach(logLine);
  else if (msg.type === 'reset') clearAll();
}

function buildTabs() {
  const nav = $('tabs');
  nav.innerHTML = '';
  for (const view of VIEWS) {
    const tab = document.createElement('button');
    tab.className = 'tab';
    tab.dataset.view = view.id;
    tab.textContent = view.label;
    tab.onclick = () => setView(view.id);
    nav.appendChild(tab);
  }
}

function setView(id) {
  state.view = VIEWS.some((v) => v.id === id) ? id : 'all';
  $('main').className = `view-${state.view}`;
  for (const tab of $('tabs').children) {
    tab.classList.toggle('active', tab.dataset.view === state.view);
  }
  savePrefs();
}

function buildFromSchema(schema) {
  state.schema = schema;
  state.fields = new Map(schema.fields.map((f) => [f.key, f]));
  const multiChannel = schema.channels.length > 1;
  const traceLabel = (f) =>
    (multiChannel && f.channel ? `${f.channel}:${f.label}` : f.label);

  buildReadouts(schema, traceLabel);
  buildPanels(schema, traceLabel);

  buildPhasePlot();

  const quick = $('quick');
  quick.innerHTML = '';
  for (const verb of schema.commands) {
    const button = document.createElement('button');
    button.textContent = verb;
    if (verb === 'stop') button.className = 'stop';
    button.onclick = () => send({ type: 'command', verb });
    quick.appendChild(button);
  }

  buildSequences(schema.sequences);
  refreshCaptures();
}

function swatch(colour) {
  const el = document.createElement('span');
  el.className = 'swatch';
  el.style.background = colour;
  return el;
}

function buildReadouts(schema, traceLabel) {
  const strip = $('readouts');
  strip.innerHTML = '';
  state.readouts = new Map();
  for (const f of schema.fields) {
    const el = document.createElement('div');
    el.className = 'readout';
    el.appendChild(swatch(f.colour));

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = traceLabel(f);
    el.appendChild(name);

    if (f.kind === 'bits') {
      const bits = document.createElement('span');
      bits.className = 'bits';
      for (const bit of schema.flag_bits) {
        const chip = document.createElement('span');
        chip.className = 'bit';
        chip.dataset.mask = bit.mask;
        chip.textContent = bit.label;
        bits.appendChild(chip);
      }
      el.appendChild(bits);
    } else {
      const value = document.createElement('span');
      value.className = 'value';
      value.textContent = '–';
      el.appendChild(value);
      const unit = document.createElement('span');
      unit.className = 'unit';
      unit.textContent = f.unit;
      el.appendChild(unit);
    }
    strip.appendChild(el);
    state.readouts.set(f.key, el);
  }
}

function signalLabel(field) {
  let label = field.label;
  if (field.channel) label = field.channel.toUpperCase() + ': ' + label;
  return label + ' (' + field.unit + ')';
}

function makeDraggable(element, key, source) {
  element.draggable = true;
  element.addEventListener('dragstart', (event) => {
    event.dataTransfer.setData('application/x-mppt-signal',
      JSON.stringify({ key, source }));
    event.dataTransfer.effectAllowed = 'copyMove';
  });
}

function buildPanels(schema) {
  for (const panel of state.panels) panel.chart.destroy();
  state.panels = [];
  $('panels').replaceChildren();
  const picker = $('signal-picker');
  picker.replaceChildren();
  for (const field of schema.fields) {
    const button = document.createElement('button');
    button.className = 'chip signal';
    button.dataset.key = field.key;
    button.append(swatch(field.colour), document.createTextNode(signalLabel(field)));
    button.title = 'Drag onto a plot, or click to add to the selected plot';
    makeDraggable(button, field.key, null);
    button.onclick = () => {
      const panel = state.panels.find((p) => String(p.id) === $('plot-target').value);
      if (panel) addSignal(panel, field.key);
    };
    picker.appendChild(button);
  }
  let layouts = prefs.plots;
  if (!Array.isArray(layouts)) {
    layouts = [
      { label: 'Voltage', fields: ['vbus_mv', 'vin_mv', 'vout_mv'] },
      { label: 'Current', fields: ['iin_ma', 'iout_ma'] },
      { label: 'Duty', fields: ['duty'] },
    ];
  }
  for (const layout of layouts) {
    if (layout && Array.isArray(layout.fields)) {
      addPlot(layout.label, layout.fields, layout.minimised === true);
    }
  }
  const selected = state.panels[prefs.activePlotIndex] || state.panels[0];
  if (selected) state.activePlot = selected.id;
  updatePlotTargets();
}

function updatePlotTargets() {
  const select = $('plot-target');
  select.replaceChildren();
  for (const panel of state.panels) {
    const option = document.createElement('option');
    option.value = panel.id;
    option.textContent = panel.label;
    select.appendChild(option);
  }
  select.value = String(state.activePlot);
  select.disabled = state.panels.length === 0;
  for (const button of $('signal-picker').children) {
    button.disabled = state.panels.length === 0;
  }
  $('plots-empty').hidden = state.panels.length > 0;
  renderPlotTabs();
}

function addPlot(label = 'Plot', keys = [], minimised = false) {
  const id = state.nextPlotId++;
  if (label === 'Plot') label += ' ' + id;
  const div = document.createElement('div');
  div.className = 'panel';
  div.dataset.plotId = id;
  const head = document.createElement('div');
  head.className = 'plot-head';
  const name = document.createElement('input');
  name.value = label;
  name.setAttribute('aria-label', 'Plot name');
  const minimise = document.createElement('button');
  minimise.className = 'plot-minimise';
  const expand = document.createElement('button');
  expand.className = 'plot-expand';
  const signals = document.createElement('div');
  signals.className = 'plot-signals';
  const hint = document.createElement('div');
  hint.className = 'drop-hint';
  hint.textContent = 'Drop signals here, or select this plot above and click a signal.';
  const wrap = document.createElement('div');
  wrap.className = 'canvas-wrap';
  const canvas = document.createElement('canvas');
  wrap.appendChild(canvas);
  head.append(name, minimise, expand);
  div.append(head, signals, hint, wrap);
  $('panels').appendChild(div);
  const panel = {
    id, label, keys: [...new Set(keys.filter((key) => state.fields.has(key)))],
    div, signals, hint, minimise, expand, minimised,
    chart: new StripChart(canvas, { traces: [], window: Number($('window').value) || 30 }),
  };
  state.panels.push(panel);
  state.activePlot = panel.id;
  minimise.onclick = () => {
    panel.minimised = !panel.minimised;
    if (panel.minimised) setSeriesExpanded(false);
    renderPlotTabs();
    savePrefs();
  };
  expand.onclick = () => {
    panel.minimised = false;
    setSeriesExpanded(!state.seriesExpanded);
    savePrefs();
  };
  name.onchange = () => {
    panel.label = name.value.trim() || 'Plot ' + id;
    name.value = panel.label;
    updatePlotTargets();
    savePrefs();
  };
  div.addEventListener('dragover', (event) => {
    if (!event.dataTransfer.types.includes('application/x-mppt-signal')) return;
    event.preventDefault();
    div.classList.add('drop-active');
  });
  div.addEventListener('dragleave', (event) => {
    if (!div.contains(event.relatedTarget)) div.classList.remove('drop-active');
  });
  div.addEventListener('drop', (event) => dropSignal(event, panel));
  refreshPlot(panel);
  updatePlotTargets();
  return panel;
}

function dropSignal(event, panel) {
  event.preventDefault();
  panel.div.classList.remove('drop-active');
  let item;
  try {
    item = JSON.parse(event.dataTransfer.getData('application/x-mppt-signal'));
  } catch (error) { return; }
  if (!state.fields.has(item.key)) return;
  selectPlot(panel.id);
  addSignal(panel, item.key);
  if (item.source != null && item.source !== panel.id && !event.ctrlKey) {
    const source = state.panels.find((p) => p.id === item.source);
    if (source) removeSignal(source, item.key);
  }
}

function selectPlot(id) {
  state.activePlot = id;
  updatePlotTargets();
  savePrefs();
}

function closePlot(panel) {
  const index = state.panels.indexOf(panel);
  panel.chart.destroy();
  panel.div.remove();
  state.panels.splice(index, 1);
  if (state.activePlot === panel.id) {
    state.activePlot = null;
    const next = state.panels[Math.min(index, state.panels.length - 1)];
    if (next) state.activePlot = next.id;
  }
  if (!state.panels.length) setSeriesExpanded(false);
  updatePlotTargets();
  savePrefs();
}

function setSeriesExpanded(expanded) {
  state.seriesExpanded = expanded;
  $('series-card').classList.toggle('expanded', expanded);
  document.body.classList.toggle('plot-expanded', expanded);
  renderPlotTabs();
}

function renderPlotTabs() {
  const tabs = $('plot-tabs');
  tabs.replaceChildren();
  for (const panel of state.panels) {
    const active = panel.id === state.activePlot;
    panel.hidden = !active;
    panel.div.hidden = !active;
    if (panel.hidden || panel.minimised) panel.chart.clearHover();
    panel.div.classList.toggle('minimised', panel.minimised);
    panel.minimise.textContent = 'Minimise';
    if (panel.minimised) panel.minimise.textContent = 'Restore plot';
    panel.minimise.setAttribute('aria-expanded', String(!panel.minimised));
    panel.expand.textContent = 'Expand';
    if (state.seriesExpanded) panel.expand.textContent = 'Restore window';
    panel.expand.setAttribute('aria-pressed', String(state.seriesExpanded));
    const item = document.createElement('div');
    item.className = 'plot-tab';
    item.classList.toggle('active', active);
    const tab = document.createElement('button');
    tab.className = 'plot-tab-select';
    tab.id = 'plot-tab-' + panel.id;
    tab.textContent = panel.label;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', String(active));
    tab.setAttribute('aria-controls', 'plot-' + panel.id);
    tab.tabIndex = -1;
    if (active) tab.tabIndex = 0;
    panel.div.id = 'plot-' + panel.id;
    panel.div.setAttribute('role', 'tabpanel');
    panel.div.setAttribute('aria-labelledby', tab.id);
    tab.onclick = () => selectPlot(panel.id);
    tab.onkeydown = (event) => {
      let index = state.panels.indexOf(panel);
      if (event.key === 'ArrowRight') index = (index + 1) % state.panels.length;
      else if (event.key === 'ArrowLeft') index = (index + state.panels.length - 1) % state.panels.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = state.panels.length - 1;
      else return;
      event.preventDefault();
      selectPlot(state.panels[index].id);
      $('plot-tab-' + state.activePlot).focus();
    };
    item.ondragover = (event) => {
      if (event.dataTransfer.types.includes('application/x-mppt-signal')) event.preventDefault();
    };
    item.ondrop = (event) => dropSignal(event, panel);
    const close = document.createElement('button');
    close.className = 'plot-tab-close';
    close.textContent = '\u00d7';
    close.setAttribute('aria-label', 'Close ' + panel.label);
    close.onclick = () => closePlot(panel);
    item.append(tab, close);
    tabs.appendChild(item);
  }
}

function showReadouts(show) {
  $('readouts').hidden = !show;
  $('readouts-toggle').setAttribute('aria-expanded', String(show));
  $('readouts-toggle').textContent = 'Show live values';
  if (show) $('readouts-toggle').textContent = 'Hide live values';
}

function addSignal(panel, key) {
  if (panel.keys.includes(key)) return;
  panel.keys.push(key);
  refreshPlot(panel);
  savePrefs();
}

function removeSignal(panel, key) {
  panel.keys = panel.keys.filter((k) => k !== key);
  refreshPlot(panel);
  savePrefs();
}

function refreshPlot(panel) {
  panel.signals.replaceChildren();
  const traces = panel.keys.map((key) => {
    const field = state.fields.get(key);
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.dataset.key = key;
    chip.append(swatch(field.colour), document.createTextNode(signalLabel(field) + ' \u00d7'));
    chip.title = 'Click to remove; drag to move to another plot (Ctrl to copy)';
    chip.setAttribute('aria-label', 'Remove ' + signalLabel(field));
    chip.onclick = () => removeSignal(panel, key);
    makeDraggable(chip, key, panel.id);
    panel.signals.appendChild(chip);
    let dash = [];
    if (field.label === 'vout' || field.label === 'iout') dash = [5, 3];
    return { ...field, label: signalLabel(field), dash };
  });
  panel.hint.hidden = traces.length > 0;
  panel.chart.setTraces(traces);
  for (const batch of state.seriesSnapshot ?? state.seriesBuffer) {
    panel.chart.push(batch.ts, batch.series);
  }
}

function buildPhasePlot() {
  if (state.iv) state.iv.destroy();
  state.ivPairs = new Map(Object.entries(state.schema.iv).map(([channel, pair]) =>
    [channel, { x: state.fields.get(pair.x), y: state.fields.get(pair.y) }]));
  const channels = [...state.ivPairs.keys()];
  const first = state.ivPairs.get(channels[0]);
  if (!first) return;
  state.iv = new PhasePlot($('iv'), {
    xLabel: `${first.x.label} (${first.x.unit})`,
    yLabel: `${first.y.label} (${first.y.unit})`,
    channels: new Map(channels.map((ch) => [ch, state.ivPairs.get(ch).y.colour])),
    persist: Number($('persist').value) || 60,
  });
  state.iv.visibleChannels = new Set((prefs.ivChannels || [channels[0]])
    .filter((ch) => state.ivPairs.has(ch)));
  const holder = $('iv-channels');
  holder.replaceChildren();
  for (const [index, ch] of channels.entries()) {
    const button = document.createElement('button');
    button.className = 'chip';
    button.append(swatch(state.ivPairs.get(ch).y.colour),
                  document.createTextNode(`Channel ${index + 1} (${ch.toUpperCase()})`));
    button.setAttribute('aria-pressed', String(state.iv.visibleChannels.has(ch)));
    button.onclick = () => {
      if (state.iv.visibleChannels.has(ch)) state.iv.visibleChannels.delete(ch);
      else state.iv.visibleChannels.add(ch);
      button.setAttribute('aria-pressed', String(state.iv.visibleChannels.has(ch)));
      prefs.ivChannels = [...state.iv.visibleChannels];
      savePrefs();
    };
    holder.appendChild(button);
  }
  state.ivPausedAt = null;
  updatePauseButton('iv', false);
  applyIvControls();
}

function buildSequences(sequences) {
  const holder = $('sequences');
  holder.innerHTML = '';
  for (const seq of sequences) {
    const div = document.createElement('div');
    div.className = 'seq';
    const steps = seq.steps.map(([t, v]) => `${t}s ${v}`).join('  ');
    div.innerHTML =
      '<div class="row"><span class="name"></span><span class="spacer"></span>' +
      '<button class="run">run</button></div>' +
      '<div class="meta"></div><div class="desc"></div>';
    div.querySelector('.name').textContent = seq.label;
    div.querySelector('.meta').textContent = `${seq.length}s   ${steps}`;
    const desc = div.querySelector('.desc');
    if (seq.description) desc.textContent = seq.description;
    else desc.remove();
    div.querySelector('.run').onclick = () =>
      send({ type: 'sequence', name: seq.name });
    holder.appendChild(div);
  }
}

function onData(msg) {
  state.now = msg.now;
  state.wall = performance.now();
  if (msg.ts.length) {

    state.seriesBuffer.push({ ts: msg.ts, series: msg.series });
    const cutoff = state.now - 600;
    while (state.seriesBuffer.length &&
           state.seriesBuffer[0].ts.at(-1) < cutoff) state.seriesBuffer.shift();
    if (state.seriesPausedAt == null) {
      for (const panel of state.panels) panel.chart.push(msg.ts, msg.series);
    }
  }
  if (state.iv) {
    for (const [ch, { x, y }] of state.ivPairs) {
      for (const [t, px, py, p] of msg.iv[ch] || []) {
        state.iv.push(t, px / x.scale, py / y.scale, p, ch);
      }
    }
    state.iv.prune(state.now);
  }
  updateReadouts(msg.latest);
}

function updateReadouts(latest) {
  for (const [key, el] of state.readouts) {
    const field = state.fields.get(key);
    const raw = latest[key];
    if (field.kind === 'bits') {
      const value = raw || 0;
      for (const chip of el.querySelectorAll('.bit')) {
        chip.classList.toggle('set', (value & Number(chip.dataset.mask)) !== 0);
      }
      continue;
    }
    const node = el.querySelector('.value');
    node.textContent = raw == null ? '–' : (raw / field.scale).toFixed(field.digits);
  }
}

function onStatus(msg) {
  state.connected = msg.connected;
  $('connect').disabled = msg.connected;
  $('disconnect').disabled = !msg.connected;
  if (msg.connected) setPill('on', msg.port);
  else setPill(msg.error ? 'err' : '', msg.error ? 'link error' : 'disconnected');
  $('stats').textContent = msg.connected
    ? `${msg.sets} sets   ${msg.dropped} dropped   ${msg.stats.frames} frames   ` +
      `${msg.stats.resyncs} resynced   ${msg.stats.uptime.toFixed(0)} s`
    : (msg.error || '');
  state.sequence = msg.sequence || { state: 'idle' };
  renderRunState();
}

function setPill(kind, text) {
  const pill = $('pill');
  pill.className = kind ? `pill ${kind}` : 'pill';
  pill.textContent = text;
}

let runSignature = '';

function renderRunState() {
  const run = state.sequence || { state: 'idle' };
  const signature = [run.state, run.name, (run.summary || []).length,
                     Object.keys(run.files || {}).length].join('|');
  if (signature !== runSignature) {
    runSignature = signature;
    buildRunState(run);
    if (run.state === 'done' || run.state === 'error') refreshCaptures();
  }
  updateRunProgress(run);
}

function buildRunState(run) {
  const holder = $('run-state');
  if (run.state === 'idle') { holder.innerHTML = ''; return; }
  let html = '<div class="meta"><span class="run-label"></span></div>' +
             '<div class="progress"><span></span></div>';
  if (run.state === 'running') html += '<button class="danger" id="seq-cancel">cancel</button>';
  if (run.summary && run.summary.length) html += '<pre></pre>';
  if (run.files && Object.keys(run.files).length) {
    html += '<div class="file-links">' + fileLinks(run.files) + '</div>';
  }
  holder.innerHTML = html;
  const summary = holder.querySelector('pre');
  if (summary) summary.textContent = run.summary.join('\n');
  const cancel = $('seq-cancel');
  if (cancel) cancel.onclick = () => send({ type: 'sequence_cancel' });
  bindViewers(holder);
}

function updateRunProgress(run) {
  const holder = $('run-state');
  const bar = holder.querySelector('.progress span');
  const label = holder.querySelector('.run-label');
  if (!bar || !label) return;
  const elapsed = run.started != null ? Math.max(0, state.now - run.started) : 0;
  const fraction = run.state === 'running'
    ? (run.length ? Math.min(1, elapsed / run.length) : 0) : 1;
  bar.style.width = `${fraction * 100}%`;
  label.textContent = `${run.label || run.name} — ${run.state}` +
    (run.state === 'running' ? ` ${elapsed.toFixed(1)}/${run.length}s` : '');
}

function fileLinks(files) {
  return Object.entries(files).map(([kind, name]) =>
    (name.endsWith('.svg')
      ? `<button data-view="${name}">${kind}</button>`
      : `<a href="/captures/${name}" download>${kind}</a>`)).join('');
}

function bindViewers(root) {
  root.querySelectorAll('[data-view]').forEach((el) => {
    el.onclick = () => showPlot(el.dataset.view);
  });
}

async function refreshCaptures() {
  const { captures } = await (await fetch('/api/captures')).json();
  const holder = $('captures');
  holder.innerHTML = '';
  if (!captures.length) {
    holder.innerHTML = '<div class="empty">no captures yet</div>';
    return;
  }
  for (const item of captures) {
    const div = document.createElement('div');
    div.className = 'capture';
    div.innerHTML = '<span class="stem"></span>' +
                    `<span class="file-links">${fileLinks(item.files)}</span>`;
    div.querySelector('.stem').textContent = item.name;
    holder.appendChild(div);
  }
  bindViewers(holder);
}

function showPlot(name) {
  $('viewer-title').textContent = name;
  const img = document.createElement('img');
  img.src = `/captures/${name}`;
  img.alt = name;
  $('viewer-body').replaceChildren(img);
  $('viewer').classList.remove('hidden');
}

function logLine(entry) {
  const holder = $('log');
  const atBottom = holder.scrollHeight - holder.scrollTop - holder.clientHeight < 30;
  const div = document.createElement('div');
  div.innerHTML = '<span class="t"></span><span class="msg"></span>';
  div.firstChild.textContent = entry.t.toFixed(2).padStart(7);
  div.lastChild.className = `msg ${entry.level}`;
  div.lastChild.textContent = entry.text;
  holder.appendChild(div);
  while (holder.childElementCount > 500) holder.removeChild(holder.firstChild);
  if (atBottom) holder.scrollTop = holder.scrollHeight;
}

function localLog(text, level = 'info') {
  logLine({ t: state.now, level, text });
}

const LOCAL_HELP = [
  'verbs come from console.OPCODES; anything else here is local:',
  '  raw <op-hex> [byte-hex ...]   send an arbitrary frame',
  '  clear [iv | series | all]     empty a plot; both if not named',
  '  persist <seconds>             how long a point stays',
  '  window <seconds>              time-series span',
  '  view all | iv | series        switch the tab',
  '  run <sequence>                start a predefined capture',
  '  connect [port] | disconnect | help',
];

function runCommand(line) {
  const parts = line.trim().split(/\s+/);
  const verb = parts[0].toLowerCase();
  const args = parts.slice(1);
  if (!verb) return;

  if (state.schema && state.schema.commands.includes(verb)) {
    send({ type: 'command', verb });
  } else if (verb === 'raw') {
    if (!args.length) return localLog('usage: raw <op-hex> [byte-hex ...]', 'error');
    send({ type: 'raw', op: args[0], payload: args.slice(1).join(' ') });
  } else if (verb === 'help') {
    LOCAL_HELP.forEach((l) => localLog(l));
  } else if (verb === 'clear') {
    const what = (args[0] || 'all').toLowerCase();
    if (!['iv', 'series', 'all'].includes(what)) {
      return localLog('usage: clear [iv | series | all]', 'error');
    }
    if (what !== 'series') clearIv();
    if (what !== 'iv') clearSeries();
    localLog(`cleared ${what === 'all' ? 'both plots' : what}`);
  } else if (verb === 'view') {
    setView(args[0]);
    localLog(`view ${state.view}`);
  } else if (verb === 'persist' || verb === 'window') {
    const seconds = Number(args[0]);
    if (!(seconds > 0)) return localLog(`usage: ${verb} <seconds>`, 'error');
    $(verb).value = seconds;
    applyIvControls();
    applyWindow();
    localLog(`${verb} ${seconds} s`);
  } else if (verb === 'run') {
    send({ type: 'sequence', name: args[0] || '' });
  } else if (verb === 'connect') {
    connect(args[0]);
  } else if (verb === 'disconnect') {
    fetch('/api/disconnect', { method: 'POST' });
  } else {
    localLog(`unknown: ${verb}  (try help)`, 'error');
  }
}

async function refreshPorts() {
  const { ports, suggested } = await (await fetch('/api/ports')).json();
  const select = $('port');
  select.innerHTML = '';
  if (!ports.length) {
    select.innerHTML = '<option value="">no ports found</option>';
    return;
  }
  for (const port of ports) {
    const option = document.createElement('option');
    option.value = port.device;
    option.textContent = `${port.device} — ${port.description}`;
    if (port.device === suggested) option.selected = true;
    select.appendChild(option);
  }
}

async function connect(port) {
  const body = JSON.stringify({ port: port || $('port').value || null });
  const response = await fetch('/api/connect',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    localLog(detail.detail || 'connect failed', 'error');
  }
}

function clearIv() {
  if (state.iv) state.iv.clearData();
}

function clearSeries() {
  state.seriesBuffer = [];
  if (state.seriesSnapshot) state.seriesSnapshot = [];
  for (const panel of state.panels) panel.chart.clearData();
}

function clearAll() {
  clearSeries();
  clearIv();
  state.now = 0;
  state.wall = performance.now();
  state.seriesSnapshot = null;
  state.seriesPausedAt = null;
  state.ivPausedAt = null;
  if (state.iv) state.iv.frozenPoints = null;
  updatePauseButton('series', false);
  updatePauseButton('iv', false);
}

function plotNow() {
  return state.connected ? state.now + (performance.now() - state.wall) / 1000 : state.now;
}

function updatePauseButton(plot, paused) {
  $(plot + '-pause').textContent = paused ? 'Resume' : 'Pause';
  $(plot + '-pause').setAttribute('aria-pressed', String(paused));
}

function toggleIvPause() {
  if (!state.iv) return;
  if (state.ivPausedAt == null) {
    state.ivPausedAt = plotNow();
    state.iv.prune(state.ivPausedAt);
    state.iv.frozenPoints = state.iv.points.slice();
  } else {
    state.ivPausedAt = null;
    state.iv.frozenPoints = null;
  }
  updatePauseButton('iv', state.ivPausedAt != null);
}

function toggleSeriesPause() {
  if (state.seriesPausedAt == null) {
    state.seriesPausedAt = plotNow();
    state.seriesSnapshot = state.seriesBuffer.slice();
  } else {
    state.seriesPausedAt = null;
    state.seriesSnapshot = null;
    for (const panel of state.panels) refreshPlot(panel);
  }
  updatePauseButton('series', state.seriesPausedAt != null);
}

function applyIvControls() {
  const iv = state.iv;
  if (!iv) return;
  iv.persist = Number($('persist').value) || 60;
  iv.pointSize = Number($('point-size').value);
  iv.fade = $('fade').value;
  iv.showTrail = $('show-trail').checked;
  iv.showBest = $('show-best').checked;
  iv.showIso = $('show-iso').checked;
  iv.lockAxes = $('lock-axes').checked;
  iv.xMaxLock = Number($('x-max').value) || 1;
  iv.yMaxLock = Number($('y-max').value) || 1;
  $('iv-controls').classList.toggle('axes-locked', iv.lockAxes);
  savePrefs();
}

function applyWindow() {
  const seconds = Number($('window').value) || 30;
  for (const panel of state.panels) panel.chart.window = seconds;
  savePrefs();
}

function tick() {

  const now = plotNow();
  if (state.view !== 'iv') {
    for (const panel of state.panels) {
      if (!panel.hidden && !panel.minimised) panel.chart.draw(state.seriesPausedAt ?? now);
    }
  }
  if (state.iv && state.view !== 'series') state.iv.draw(state.ivPausedAt ?? now);
  if (state.sequence.state === 'running') renderRunState();
  requestAnimationFrame(tick);
}

function boot() {
  loadPrefs();
  applyPrefsToInputs();
  buildTabs();
  showReadouts(prefs.readoutsHidden !== true);
  setView(prefs.view || 'all');
  refreshPorts();
  openSocket();

  $('connect').onclick = () => connect();
  $('disconnect').onclick = () => fetch('/api/disconnect', { method: 'POST' });
  $('rescan').onclick = refreshPorts;
  $('readouts-toggle').onclick = () => {
    showReadouts($('readouts').hidden);
    savePrefs();
  };
  $('plot-target').onchange = () => selectPlot(Number($('plot-target').value));
  $('iv-clear').onclick = clearIv;
  $('iv-pause').onclick = toggleIvPause;
  $('series-pause').onclick = toggleSeriesPause;
  $('series-clear').onclick = clearSeries;
  $('add-plot').onclick = () => {
    const panel = addPlot();
    $('plot-target').value = panel.id;
    savePrefs();
  };
  $('signal-search').oninput = () => {
    const query = $('signal-search').value.trim().toLowerCase();
    for (const button of $('signal-picker').children) {
      button.hidden = !button.textContent.toLowerCase().includes(query);
    }
  };
  $('log-clear').onclick = () => { $('log').innerHTML = ''; send({ type: 'clear_log' }); };
  $('captures-refresh').onclick = refreshCaptures;
  $('viewer-close').onclick = () => $('viewer').classList.add('hidden');
  $('viewer').onclick = (e) => {
    if (e.target === $('viewer')) $('viewer').classList.add('hidden');
  };
  $('window').oninput = applyWindow;
  for (const id of ['persist', 'point-size', 'fade', 'show-trail', 'show-best',
                    'show-iso', 'lock-axes', 'x-max', 'y-max']) {
    $(id).oninput = applyIvControls;
    $(id).onchange = applyIvControls;
  }

  $('cmd-form').onsubmit = (e) => {
    e.preventDefault();
    const line = $('cmd').value.trim();
    if (!line) return;
    state.history.push(line);
    state.historyAt = state.history.length;
    $('cmd').value = '';
    runCommand(line);
  };
  $('cmd').onkeydown = (e) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    state.historyAt += e.key === 'ArrowUp' ? -1 : 1;
    state.historyAt = Math.max(0, Math.min(state.history.length, state.historyAt));
    $('cmd').value = state.history[state.historyAt] || '';
  };
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $('viewer').classList.add('hidden');
      setSeriesExpanded(false);
    }
  });

  requestAnimationFrame(tick);
}

boot();
