const $ = id => document.getElementById(id);
let state, selected = 'fill', selectedDevices = new Set(), busy = false, deviceSignature = '', generation;
let previewData, previewIndex = 0, previewToken = 0;
let paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
let settingsKey;

function grid(node, interactive) {
  node.replaceChildren();
  for (let row = 3; row >= 0; row--) for (let col = 0; col < 4; col++) {
    const pixel = document.createElement(interactive ? 'button' : 'span');
    pixel.className = 'pixel'; pixel.dataset.pad = row * 4 + col;
    if (interactive) {pixel.title = `Tap row ${row + 1}, column ${col + 1}`; pixel.setAttribute('aria-label', pixel.title); pixel.onclick = () => interactive(row * 4 + col);}
    node.append(pixel);
  }
}
function paint(node, frame, brightness = 1) {
  for (const pixel of node.children) {
    const rgb = frame?.[Number(pixel.dataset.pad)] || [0, 0, 0];
    const peak = Math.max(...rgb);
    const intensity = Math.min(1, Math.max(0, peak / 255 * brightness));
    // Keep the light's hue vivid; represent dimming through its size and glow.
    const color = rgb.map(c => peak ? Math.round(c / peak * 255) : 0);
    pixel.style.setProperty('--light-color', color.join(' '));
    // Interpolate area, then take its square root for the relative radius.
    const minRadius = .6;
    const radius = Math.sqrt(minRadius ** 2 + (1 - minRadius ** 2) * intensity);
    pixel.style.setProperty('--orb-size', `${radius * 100}%`);
    pixel.style.setProperty('--orb-opacity', intensity ** .15);
  }
}
function paintPreview() {
  if (!previewData) return;
  paint($('preview'), previewData.frames[previewIndex], Number($('brightness').value) / 100);
  const pressed = new Set(previewData.pressed[previewIndex]);
  for (const pixel of $('preview').children) pixel.classList.toggle('pressed', pressed.has(Number(pixel.dataset.pad)));
}
function text(tag, value, className) {const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el;}
async function request(path, values) {
  const response = await fetch(path, values === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
async function action(command, values = {}) {
  if (busy) return;
  busy = true; updateButtons(); $('notice').textContent = '';
  try {await request(`/api/${command}`, values); await poll();}
  catch (error) {$('notice').textContent = error.message;}
  finally {busy = false; updateButtons();}
}
function updateButtons() {
  $('start').disabled = busy || !state;
  $('stop').disabled = busy || !state;
  $('restart').disabled = busy || !state;
  $('start-selected').disabled = busy || !selectedDevices.size;
  $('start-selected').textContent = selectedDevices.size ? `Start on selected (${selectedDevices.size})` : 'Start on selected';
}
function runningInteraction() {return Object.entries(state?.sessions || {}).find(([, entry]) => entry.app === selected);}
function updateSettings() {
  const running = runningInteraction();
  const key = JSON.stringify([selected, running?.[0], running?.[1].options.brightness]);
  if (key !== settingsKey) {
    settingsKey = key;
    $('brightness').value = Math.round((running?.[1].options.brightness ?? .25) * 100);
    updateBrightness();
  }
  $('restart').hidden = !running;
  $('settings-note').textContent = running ? 'Already running. Start joins without resetting; Restart applies brightness changes to everyone playing this interaction.' : '';
}
function launch(devices = null) {return action('start', {interaction: selected, devices, options: {brightness: Number($('brightness').value) / 100}});}
async function choose(app) {
  selected = app;
  for (const button of $('apps').children) button.setAttribute('aria-pressed', String(button.dataset.app === app));
  $('app-title').textContent = state.catalog[app].name;
  $('app-description').textContent = state.catalog[app].description;
  updateSettings();
  const token = ++previewToken;
  try {const result = await request(`/api/preview/${app}`); if (token === previewToken) {previewData = result; previewIndex = 0; paintPreview();}}
  catch (error) {$('notice').textContent = error.message;}
}
function catalog() {
  $('apps').replaceChildren();
  for (const [id, app] of Object.entries(state.catalog)) {
    const button = document.createElement('button'); button.className = 'app'; button.draggable = true; button.dataset.app = id;
    button.setAttribute('aria-label', `Choose ${app.name}`);
    const icon = text('span', app.symbol, 'app-icon'); icon.style.backgroundColor = app.color;
    button.append(icon, text('strong', app.name)); button.onclick = () => choose(id);
    button.ondragstart = event => {event.dataTransfer.setData('text/plain', id); event.dataTransfer.effectAllowed = 'copy';};
    $('apps').append(button);
  }
  choose(selected);
}
function devices() {
  // Keep focused controls and checkboxes intact during routine polling.
  const signature = JSON.stringify([state.devices.map(({frame, error, connected, ready, ...d}) => d), state.sessions, state.demo]);
  if (signature !== deviceSignature) {
    deviceSignature = signature; $('devices').replaceChildren();
    for (const d of state.devices) {
      const entry = state.sessions[d.session];
      const name = /^jp[- ]mini$/i.test(d.name) ? 'JP-MINI' : d.name;
      const card = document.createElement('article'); card.className = 'device'; card.dataset.device = d.address;
      const identity = text('div', '', 'device-identity');
      const title = text('button', name, 'device-title');
      const address = text('div', d.address, 'device-address');
      address.id = `address-${d.address.replaceAll(':', '')}`;
      address.hidden = true;
      title.setAttribute('aria-expanded', 'false');
      title.setAttribute('aria-controls', address.id);
      title.onclick = () => {
        address.hidden = !address.hidden;
        title.setAttribute('aria-expanded', String(!address.hidden));
      };
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selectedDevices.has(d.address); check.setAttribute('aria-label', `Select ${name} ${d.address}`);
      check.onchange = () => {check.checked ? selectedDevices.add(d.address) : selectedDevices.delete(d.address); updateButtons();};
      const selection = text('label', '', 'device-selection'); selection.append(check);
      const heading = text('div', '', 'device-heading'); heading.append(selection, title);
      identity.append(heading, address);
      const status = text('div', '', 'device-status'); status.append(text('span', '', 'device-connection'));
      const errorMessage = text('button', '', 'device-error');
      errorMessage.hidden = true;
      errorMessage.setAttribute('aria-expanded', 'false');
      errorMessage.onclick = () => {
        const expanded = errorMessage.getAttribute('aria-expanded') !== 'true';
        errorMessage.setAttribute('aria-expanded', String(expanded));
        errorMessage.textContent = expanded ? errorMessage.dataset.message : errorMessage.dataset.message.split(/\r?\n/)[0];
      };
      identity.append(status, errorMessage);
      const board = text('div', '', 'pad-grid device-board'); grid(board, state.demo ? pad => action('tap', {device:d.address, pad}) : null);
      const buttons = text('div', '', 'device-buttons');
      buttons.id = `actions-${d.address.replaceAll(':', '')}`;
      buttons.setAttribute('popover', 'auto');
      const toggle = text('button', 'Actions ▾', 'device-actions');
      toggle.setAttribute('popovertarget', buttons.id);
      toggle.setAttribute('aria-label', `Actions for ${name} ${d.address}`);
      buttons.addEventListener('beforetoggle', event => {
        if (event.newState !== 'open') return;
        const rect = toggle.getBoundingClientRect();
        buttons.style.left = `${Math.max(8, Math.min(rect.right - 220, innerWidth - 228))}px`;
        buttons.style.top = `${rect.bottom + 6}px`;
      });
      for (const [label, callback] of [['Play selected', () => launch([d.address])], ['Stop', () => action('stop', {devices:[d.address]})]]) {
        const button = text('button', label); button.onclick = () => {buttons.hidePopover(); callback();}; buttons.append(button);
      }
      if (entry) {const restart = text('button', 'Restart interaction'); restart.onclick = () => {buttons.hidePopover(); action('restart', {interaction:entry.app});}; buttons.append(restart);}
      const hardware = text('div', '', 'device-hardware'); hardware.append(board, identity);
      const info = text('div', '', 'device-info'); info.append(hardware);
      const controls = text('div', '', 'device-controls');
      const game = text('div', '', 'device-game');
      game.append(text('div', entry ? state.catalog[entry.app].name : 'Stopped', 'running-name'));
      controls.append(game, toggle);
      const layout = text('div', '', 'device-layout'); layout.append(info, controls);
      card.append(layout, buttons);
      card.ondragover = event => {event.preventDefault(); card.classList.add('drag-over');};
      card.ondragleave = () => card.classList.remove('drag-over');
      card.ondrop = event => {event.preventDefault(); card.classList.remove('drag-over'); const app = event.dataTransfer.getData('text/plain'); if (state.catalog[app]) action('start', {interaction:app, devices:[d.address], options:{brightness:Number($('brightness').value)/100}});};
      $('devices').append(card);
    }
    if (!state.devices.length) $('devices').append(text('p', 'No paired JP-MINI controllers yet. Pair a controller on the Pi; it will appear here automatically.', 'empty'));
  }
  for (const card of $('devices').children) {
    const d = state.devices.find(d => d.address === card.dataset.device);
    if (d) {
      paint(card.querySelector('.device-board'), d.frame);
      card.querySelector('.device-connection').textContent = d.connected ? '● Connected' : '○ Offline';
      const error = state.sessions[d.session]?.error || d.error || '';
      const errorMessage = card.querySelector('.device-error');
      errorMessage.dataset.message = error;
      const visibleError = errorMessage.getAttribute('aria-expanded') === 'true' ? error : error.split(/\r?\n/)[0];
      if (errorMessage.textContent !== visibleError) errorMessage.textContent = visibleError;
      errorMessage.hidden = !error;
      if (!error) errorMessage.setAttribute('aria-expanded', 'false');
    }
  }
  for (const address of selectedDevices) if (!state.devices.some(d => d.address === address)) selectedDevices.delete(address);
  $('device-count').textContent = state.devices.length;
}
async function poll() {
  try {
    const first = !state; state = await request('/api/state');
    if (generation && generation !== state.generation) {location.reload(); return;}
    generation = state.generation;
    $('connection').textContent = state.demo ? '● Demo playground' : '● Connected to Triputer';
    $('mode-note').textContent = state.demo ? 'Demo mode: tap the controller grids to play.' : '';
    if (first) catalog(); devices(); updateButtons(); updateSettings();
    $('logs').textContent = state.logs.map(log => `${new Date(log.time * 1000).toLocaleTimeString()}  ${log.message}`).join('\n') || 'No activity yet.';
    if (state.error) $('notice').textContent = state.error;
  } catch (error) {$('connection').textContent = '○ Reconnecting…';}
}
function updateBrightness() {
  $('brightness-value').textContent = `${$('brightness').value}%`;
  $('reset-brightness').disabled = $('brightness').value === $('brightness').defaultValue;
  paintPreview();
}
$('brightness').oninput = updateBrightness;
$('reset-brightness').onclick = () => {
  $('brightness').value = $('brightness').defaultValue;
  updateBrightness();
};
$('restart').onclick = () => action('restart', {interaction:selected, options:{brightness:Number($('brightness').value)/100}});
$('start').onclick = () => launch(); $('start-selected').onclick = () => launch([...selectedDevices]); $('stop').onclick = () => action('stop');
grid($('preview'));
function pauseLabel() {
  const label = paused ? 'Play preview' : 'Pause preview';
  $('pause').textContent = paused ? '▶' : '⏸︎';
  $('pause').setAttribute('aria-label', label);
  $('pause').title = label;
}
$('pause').onclick = () => {paused = !paused; pauseLabel();}; pauseLabel();
setInterval(() => {if (previewData && !paused) {previewIndex = (previewIndex + 1) % previewData.frames.length; paintPreview();}}, 1000/12);
async function refresh() {await poll(); setTimeout(refresh, 700);} refresh();
