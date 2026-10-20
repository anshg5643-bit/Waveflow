/* WaveFlow frontend - vanilla JS, no build step. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const state = { cfg: null, user: null, chatLoaded: false, streaming: false, cap: null, encMode: 'text', strength: 'balanced' };

  /* ------------------------------------------------------------ utils */
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const bytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
  const pct = (p) => `${Math.round(p * 100)}%`;
  const enc = new TextEncoder();

  let toastTimer;
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...opts });
    const isJson = (res.headers.get('content-type') || '').includes('json');
    const data = isJson ? await res.json().catch(() => null) : null;
    if (!res.ok) {
      if (res.status === 401 && !path.startsWith('/api/auth/')) showLogin();
      let msg = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
      if (Array.isArray(msg)) msg = msg.map((m) => m.msg).join('; ');
      throw new Error(msg);
    }
    return data;
  }

  function busy(btn, label) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span>${label}`;
    return () => { btn.disabled = false; btn.innerHTML = orig; };
  }
  function showErr(id, msg) { const el = $(id); el.textContent = msg || ''; el.hidden = !msg; }

  async function sampleFile() {
    const r = await fetch('/api/sample?seconds=20');
    if (!r.ok) throw new Error('Could not generate the sample');
    return new File([await r.blob()], 'waveflow_sample.wav', { type: 'audio/wav' });
  }

  /* ---------------------------------------------------------- waveform */
  function sizeCanvas(c, h) {
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth || 300;
    c.width = w * dpr; c.height = h * dpr; c.style.height = `${h}px`;
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }
  function envelope(ctx, data, w, cy, half, color, alpha = 1, gain = 1) {
    const n = data.length, bw = w / n;
    ctx.fillStyle = color; ctx.globalAlpha = alpha;
    for (let i = 0; i < n; i++) {
      const a = Math.min(1, data[i] * gain) * half;
      ctx.fillRect(i * bw, cy - a, Math.max(1, bw - 0.4), Math.max(1, a * 2));
    }
    ctx.globalAlpha = 1;
  }
  function drawMini(canvas, peaks) {
    const { ctx, w, h } = sizeCanvas(canvas, 46);
    ctx.clearRect(0, 0, w, h);
    envelope(ctx, peaks, w, h / 2, h / 2 - 2, '#38e8d0', 0.85);
  }
  function drawCompare(canvas, p) {
    const lane = 86, gap = 26;
    const { ctx, w, h } = sizeCanvas(canvas, lane * 2 + gap + 8);
    ctx.clearRect(0, 0, w, h);
    envelope(ctx, p.original, w, lane / 2 + 2, lane / 2 - 2, '#2f6bff', 0.9);
    envelope(ctx, p.stego, w, lane / 2 + 2, lane / 2 - 2, '#38e8d0', 0.55);
    const maxd = Math.max(...p.difference, 1e-4);
    const gain = Math.min(0.9 / maxd, 1000);
    envelope(ctx, p.difference, w, lane + gap + lane / 2, lane / 2 - 2, '#ffb547', 0.95, gain);
    ctx.fillStyle = '#8a9bb5'; ctx.font = '12px Manrope, sans-serif';
    ctx.fillText(`What changed, magnified x${Math.round(gain)}`, 0, lane + 16);
    return Math.round(gain);
  }

  /* ----------------------------------------------------------- dropzone */
  function makeDrop(el, onFile) {
    const input = $('input', el);
    let file = null;
    const set = (f) => {
      if (!f) return;
      file = f;
      $('.drop-empty', el).hidden = true;
      $('.drop-file', el).hidden = false;
      $('.fname', el).textContent = f.name;
      $('.finfo', el).textContent = bytes(f.size);
      onFile && onFile(f);
    };
    el.addEventListener('click', (e) => { if (e.target !== input) input.click(); });
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('over'); });
    el.addEventListener('dragleave', () => el.classList.remove('over'));
    el.addEventListener('drop', (e) => { e.preventDefault(); el.classList.remove('over'); set(e.dataTransfer.files[0]); });
    input.addEventListener('change', () => set(input.files[0]));
    return { set, get: () => file };
  }

  /* -------------------------------------------------------------- views */
  function switchView(name) {
    $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
    $$('.view').forEach((v) => (v.hidden = v.id !== `view-${name}`));
    if (name === 'history') loadHistory();
  }
  $('#nav').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) switchView(b.dataset.view); });
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-toggle]');
    if (!t) return;
    const i = $('#' + t.dataset.toggle);
    i.type = i.type === 'password' ? 'text' : 'password';
  });

  /* ------------------------------------------------------------- encode */
  const encDrop = makeDrop($('#encDrop'), async (f) => {
    state.cap = null; updateCap();
    try {
      const fd = new FormData(); fd.append('audio', f);
      const info = await api('/api/inspect', { method: 'POST', body: fd });
      state.cap = info;
      $('#encDrop .finfo').textContent = `${bytes(f.size)}, ${info.duration}s, ${(info.sample_rate / 1000).toFixed(1)} kHz, ${info.channels === 1 ? 'mono' : 'stereo'}`;
      drawMini($('#encDrop canvas.mini'), info.peaks);
      updateCap();
    } catch (e) { showErr('#encErr', e.message); }
  });
  $('#encSample').addEventListener('click', async () => { try { encDrop.set(await sampleFile()); } catch (e) { toast(e.message); } });

  $('#payloadMode').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.encMode = b.dataset.mode;
    $$('#payloadMode button').forEach((x) => x.classList.toggle('on', x === b));
    $('#encFileWrap').hidden = state.encMode !== 'file';
    $('#encMsg').placeholder = state.encMode === 'file' ? 'Optional note to go with the file' : 'Type the secret message';
    updateCap();
  });
  $('#strength').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.strength = b.dataset.v;
    $$('#strength button').forEach((x) => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-checked', on); });
  });
  $('#encMsg').addEventListener('input', updateCap);
  $('#encPayload').addEventListener('change', updateCap);

  function payloadSize() {
    const msg = enc.encode($('#encMsg').value).length;
    if (state.encMode === 'file') {
      const f = $('#encPayload').files[0];
      return f ? f.size + enc.encode(f.name).length + 6 + msg : msg;
    }
    return msg;
  }
  function updateCap() {
    const fill = $('#capFill'), txt = $('#capText');
    if (!state.cap) { fill.style.width = '0'; fill.className = ''; txt.textContent = 'Choose audio to see how much it can hold.'; return; }
    const used = payloadSize(), cap = state.cap.capacity_bytes, budget = state.cap.stealth_budget_bytes;
    fill.style.width = `${Math.min(100, (used / Math.max(1, cap)) * 100)}%`;
    fill.className = used > cap ? 'bad' : used > budget ? 'warn' : '';
    let msg = `${bytes(used)} of ${bytes(cap)} capacity.`;
    if (used > cap) msg += ' Too large for this audio.';
    else if (used > budget) msg += budget > 0 ? ` Above the quiet zone of about ${bytes(budget)}, so a detector may notice.` : ' A longer clip would make this harder to detect.';
    else msg += ' Within the quiet zone.';
    txt.textContent = msg;
  }

  $('#encForm').addEventListener('submit', async (e) => {
    e.preventDefault(); showErr('#encErr', '');
    const f = encDrop.get();
    if (!f) return showErr('#encErr', 'Choose a WAV file first, or use the generated sample.');
    const fd = new FormData();
    fd.append('audio', f);
    fd.append('password', $('#encPw').value);
    fd.append('message', $('#encMsg').value);
    fd.append('strength', state.strength);
    if (state.encMode === 'file' && $('#encPayload').files[0]) fd.append('payload_file', $('#encPayload').files[0]);
    const done = busy($('#encGo'), 'Encrypting and embedding');
    try {
      const r = await api('/api/encode', { method: 'POST', body: fd });
      renderEncode(r, f);
    } catch (err) { showErr('#encErr', err.message); } finally { done(); }
  });

  function meterHtml(p) {
    return `<div class="meter"><div class="meter-track"><div class="meter-pin" style="left:${Math.max(2, Math.min(98, p * 100))}%"></div></div></div>`;
  }
  function stealthCallout(s, ctx) {
    const copy = {
      clean: ['Detector sees nothing unusual', 'The phase statistics look like ordinary audio.'],
      uncertain: ['Detector is unsure', 'Some phase patterns look engineered, but not enough to be sure.'],
      suspicious: ['A detector would likely flag this', 'Phase values cluster in a way natural audio does not.'],
    }[s.verdict];
    const tip = ctx === 'encode' && s.verdict !== 'clean' ? ' Hide less data, or use a longer clip, to stay quieter.' : '';
    return `<div class="callout ${s.verdict}"><h4>${copy[0]} (${pct(s.probability)} chance flagged)</h4><p>${copy[1]}${tip}</p>${meterHtml(s.probability)}</div>`;
  }

  function renderEncode(r, file) {
    const m = r.metrics, out = $('#encOut');
    const origUrl = URL.createObjectURL(file);
    out.innerHTML = `
      <div class="rhead"><h3>Hidden and ready</h3>
        <div class="btnrow"><a class="btn primary" href="${r.download_url}" download>Download WAV</a><button class="btn" id="testReveal" type="button">Test reveal</button></div></div>
      <div class="players">
        <figure><figcaption>Original</figcaption><audio controls preload="metadata" src="${origUrl}"></audio></figure>
        <figure><figcaption>With hidden data</figcaption><audio controls preload="metadata" src="${r.audio_url}"></audio></figure>
      </div>
      <div class="wavebox"><canvas id="cmpCanvas"></canvas>
        <div class="legend"><span><i style="background:#2f6bff"></i>Original</span><span><i style="background:#38e8d0"></i>With hidden data</span><span><i style="background:#ffb547"></i>Difference</span></div></div>
      <div class="metrics">
        <div class="metric"><b>${m.snr_db} dB</b><span>Signal to noise (higher is quieter)</span></div>
        <div class="metric"><b>${m.max_phase_shift_deg}&deg;</b><span>Largest phase shift</span></div>
        <div class="metric"><b>x${m.repeats}</b><span>Copies of each bit</span></div>
        <div class="metric"><b>${m.fill_pct}%</b><span>Capacity used</span></div>
        <div class="metric"><b>${m.bins_modified.toLocaleString()}</b><span>Frequency bins changed</span></div>
        <div class="metric"><b>${bytes(m.encrypted_bytes)}</b><span>Encrypted payload</span></div>
      </div>
      ${stealthCallout(r.stealth, 'encode')}
      ${(m.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join('')}
      <p class="note">Keep the WAV lossless. Converting to MP3 or AAC, resampling or trimming will destroy the hidden data.</p>`;
    requestAnimationFrame(() => drawCompare($('#cmpCanvas'), r.peaks));
    $('#testReveal').addEventListener('click', async () => {
      try {
        const blob = await (await fetch(r.audio_url, { credentials: 'same-origin' })).blob();
        decDrop.set(new File([blob], r.output_name, { type: 'audio/wav' }));
        $('#decPw').value = $('#encPw').value;
        switchView('decode');
      } catch (e) { toast('Could not load the file for testing'); }
    });
  }

  /* ------------------------------------------------------------- decode */
  const decDrop = makeDrop($('#decDrop'));
  $('#decForm').addEventListener('submit', async (e) => {
    e.preventDefault(); showErr('#decErr', '');
    const f = decDrop.get();
    if (!f) return showErr('#decErr', 'Choose the WAV file first.');
    const fd = new FormData(); fd.append('audio', f); fd.append('password', $('#decPw').value);
    const done = busy($('#decGo'), 'Searching the phase data');
    try { renderDecode(await api('/api/decode', { method: 'POST', body: fd })); }
    catch (err) { showErr('#decErr', err.message); } finally { done(); }
  });
  function renderDecode(r) {
    const out = $('#decOut');
    if (!r.found) {
      out.innerHTML = `<div class="callout"><h4>No hidden message found</h4><p>${esc(r.message)}</p></div>`;
      return;
    }
    if (r.kind === 'text') {
      out.innerHTML = `<div class="rhead"><h3>Message revealed</h3><button class="btn" id="copyMsg" type="button">Copy text</button></div>
        <div class="secret">${esc(r.text)}</div>
        <p class="note">Found at the ${esc(r.strength)} setting with each bit stored ${r.repeats} times.</p>`;
      $('#copyMsg').addEventListener('click', () => navigator.clipboard.writeText(r.text).then(() => toast('Copied')));
    } else {
      out.innerHTML = `<div class="rhead"><h3>File revealed</h3><a class="btn primary" href="${r.download_url}" download>Download ${esc(r.filename)}</a></div>
        <div class="metrics"><div class="metric"><b>${esc(r.filename)}</b><span>File name</span></div><div class="metric"><b>${bytes(r.size)}</b><span>Size</span></div></div>
        ${r.note ? `<div class="secret">${esc(r.note)}</div>` : ''}`;
    }
  }

  /* ------------------------------------------------------------ analyze */
  const anDrop = makeDrop($('#anDrop'));
  $('#anSample').addEventListener('click', async () => { try { anDrop.set(await sampleFile()); } catch (e) { toast(e.message); } });
  $('#anForm').addEventListener('submit', async (e) => {
    e.preventDefault(); showErr('#anErr', '');
    const f = anDrop.get();
    if (!f) return showErr('#anErr', 'Choose a WAV file first.');
    const fd = new FormData(); fd.append('audio', f);
    const done = busy($('#anGo'), 'Analysing');
    try { renderAnalyze(await api('/api/analyze', { method: 'POST', body: fd })); }
    catch (err) { showErr('#anErr', err.message); } finally { done(); }
  });
  function renderAnalyze(r) {
    const rows = Object.entries(r.features).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${v}</td></tr>`).join('');
    $('#anOut').innerHTML = `
      <div class="rhead"><h3>${esc(r.label)}</h3></div>
      ${stealthCallout(r, 'analyze')}
      <div class="wavebox"><canvas id="anCanvas"></canvas></div>
      <details><summary>See what the model measured</summary><table class="feat">${rows}</table></details>
      <p class="note">Model: gradient boosting trained on ${r.model.training_clips} synthetic clips, ${pct(r.model.accuracy)} accuracy and AUC ${r.model.auc} on held-out clips.</p>`;
    requestAnimationFrame(() => drawMini($('#anCanvas'), r.peaks));
  }

  /* ------------------------------------------------------------ history */
  async function loadHistory() {
    const box = $('#histList');
    try {
      const { operations } = await api('/api/history');
      if (!operations.length) { box.innerHTML = '<div class="none">No runs yet. Hide a message on the Hide tab to get started.</div>'; return; }
      box.innerHTML = operations.map((o) => {
        const m = o.meta; let sum = '';
        if (o.kind === 'encode') sum = `Hidden ${m.payload_kind} at ${m.strength}, ${m.snr_db} dB, ${m.fill_pct}% used, ${pct(m.probability)} chance flagged`;
        else if (o.kind === 'decode') sum = m.ok ? (m.kind === 'file' ? `Revealed file ${m.filename}` : `Revealed a message of ${m.chars} characters`) : 'Nothing found';
        else sum = `${pct(m.probability)} chance flagged (${m.verdict})`;
        const label = { encode: 'Hide', decode: 'Reveal', analyze: 'Check' }[o.kind];
        return `<div class="hrow"><span class="tag ${o.kind}">${label}</span>
          <div style="min-width:0"><div class="t">${esc(o.input_name)}</div><div class="s">${esc(sum)} &middot; ${new Date(o.created_at).toLocaleString()}</div></div>
          <div class="row">${o.download_url ? `<a class="btn" href="${o.download_url}" download>Download</a>` : ''}<button class="icon-btn" data-del="${o.id}" aria-label="Delete"><svg viewBox="0 0 24 24"><path d="M4 7h16M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button></div></div>`;
      }).join('');
    } catch (e) { box.innerHTML = `<div class="none">${esc(e.message)}</div>`; }
  }
  $('#histList').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-del]'); if (!b) return;
    try { await api(`/api/history/${b.dataset.del}`, { method: 'DELETE' }); loadHistory(); } catch (err) { toast(err.message); }
  });

  /* --------------------------------------------------------------- chat */
  function inline(s) { return s.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>'); }
  function md(src) {
    let html = '', list = false;
    for (const line of esc(src).split('\n')) {
      const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
      if (li) { if (!list) { html += '<ul>'; list = true; } html += `<li>${inline(li[1])}</li>`; }
      else { if (list) { html += '</ul>'; list = false; } if (line.trim()) html += `<p>${inline(line)}</p>`; }
    }
    return html + (list ? '</ul>' : '');
  }
  function addMsg(role, text, cls = '') {
    const d = document.createElement('div');
    d.className = `msg ${role} ${cls}`;
    d.innerHTML = role === 'assistant' ? md(text) : `<p>${esc(text)}</p>`;
    $('#chatLog').appendChild(d);
    $('#chatLog').scrollTop = 1e9;
    return d;
  }
  async function openChat() {
    $('#chat').hidden = false; $('#chatToggle').setAttribute('aria-expanded', 'true');
    if (!state.chatLoaded) {
      state.chatLoaded = true;
      try {
        const h = await api('/api/chat/history');
        $('#chatOff').hidden = h.enabled;
        $('#chatLog').innerHTML = '';
        if (!h.messages.length) addMsg('assistant', 'Hi! I can explain how WaveFlow hides data, help you pick a strength, or sanity-check your plan. What would you like to know?');
        h.messages.forEach((m) => addMsg(m.role, m.content));
        $('#chips').hidden = h.messages.length > 0;
      } catch (e) { addMsg('assistant', e.message, 'err'); }
    }
    $('#chatInput').focus();
  }
  function closeChat() { $('#chat').hidden = true; $('#chatToggle').setAttribute('aria-expanded', 'false'); }
  $('#chatToggle').addEventListener('click', () => ($('#chat').hidden ? openChat() : closeChat()));
  $('#chatClose').addEventListener('click', closeChat);
  $('#chatClear').addEventListener('click', async () => {
    try { await api('/api/chat/history', { method: 'DELETE' }); state.chatLoaded = false; $('#chips').hidden = false; openChat(); } catch (e) { toast(e.message); }
  });
  $('#chips').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) sendChat(b.textContent); });
  $('#chatForm').addEventListener('submit', (e) => { e.preventDefault(); const v = $('#chatInput').value.trim(); if (v) sendChat(v); });
  $('#chatInput').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chatForm').requestSubmit(); } });

  async function sendChat(text) {
    if (state.streaming) return;
    state.streaming = true; $('#chips').hidden = true;
    $('#chatInput').value = '';
    addMsg('user', text);
    const bubble = addMsg('assistant', '', 'typing');
    let reply = '';
    try {
      const res = await fetch('/api/chat', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        if (res.status === 401) showLogin();
        throw new Error(d.detail || `Request failed (${res.status})`);
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n'); buf = parts.pop();
        for (const p of parts) {
          if (!p.startsWith('data:')) continue;
          const ev = JSON.parse(p.slice(5));
          if (ev.error) throw new Error(ev.error);
          if (ev.delta) { reply += ev.delta; bubble.innerHTML = md(reply); $('#chatLog').scrollTop = 1e9; }
        }
      }
      if (!reply) bubble.innerHTML = md('(No response)');
    } catch (err) {
      bubble.classList.add('err');
      bubble.innerHTML = reply ? md(reply) + `<p>${esc(err.message)}</p>` : `<p>${esc(err.message)}</p>`;
    } finally {
      bubble.classList.remove('typing'); state.streaming = false;
    }
  }

  /* --------------------------------------------------------- auth + boot */
  let heroOn = false;
  function startHero() {
    if (heroOn) return; heroOn = true;
    const c = $('#heroWave'), ctx = c.getContext('2d');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const resize = () => { const d = window.devicePixelRatio || 1; c.width = c.clientWidth * d; c.height = c.clientHeight * d; ctx.setTransform(d, 0, 0, d, 0, 0); };
    resize(); window.addEventListener('resize', resize);
    const t0 = performance.now();
    const frame = (now) => {
      const t = (now - t0) / 1000, w = c.clientWidth, h = c.clientHeight;
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < 9; i++) {
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${i % 3 === 0 ? '47,107,255' : '56,232,208'},${0.12 + 0.05 * (i % 3)})`;
        ctx.lineWidth = 1.4;
        for (let x = 0; x <= w; x += 4) {
          const nx = x / w, env = Math.exp(-(((nx - 0.72) / 0.3) ** 2));
          const y = h * 0.5 + Math.sin(nx * (9 + i * 1.3) + t * (0.6 + i * 0.08) + i * 0.7) * env * h * (0.1 + i * 0.018) + Math.sin(nx * 31 + t * 1.1 + i) * env * h * 0.012;
          x ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.stroke();
      }
      if (!reduced && !$('#login').hidden) requestAnimationFrame(frame); else heroOn = false;
    };
    requestAnimationFrame(frame);
  }

  function showLogin() {
    state.user = null; state.chatLoaded = false; closeChat();
    $('#app').hidden = true; $('#login').hidden = false; startHero();
  }
  function enterApp(user) {
    state.user = user;
    $('#login').hidden = true; $('#app').hidden = false;
    $('#uName').textContent = user.name; $('#uMail').textContent = user.guest ? 'Guest session' : user.email;
    const img = $('#uAvatar'); img.hidden = !user.picture; if (user.picture) img.src = user.picture;
    $('#chatModel').textContent = state.cfg.chat_enabled ? state.cfg.chat_model : 'Not connected';
    switchView('encode');
  }
  function loginErr(msg) { showErr('#loginErr', msg); }

  async function onGoogle(resp) {
    loginErr('');
    try {
      const d = await api('/api/auth/google', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ credential: resp.credential }) });
      enterApp(d.user);
    } catch (e) { loginErr(e.message); }
  }
  function initGoogle(clientId) {
    const note = $('#gnote');
    if (!clientId) {
      note.hidden = false;
      note.textContent = 'Google sign-in is not set up yet. Add GOOGLE_CLIENT_ID to the .env file.';
      return;
    }
    let tries = 0;
    const timer = setInterval(() => {
      if (window.google && google.accounts && google.accounts.id) {
        clearInterval(timer);
        google.accounts.id.initialize({ client_id: clientId, callback: onGoogle });
        google.accounts.id.renderButton($('#gbtn'), { theme: 'filled_black', size: 'large', shape: 'pill', text: 'continue_with', width: 300 });
      } else if (++tries > 40) {
        clearInterval(timer);
        note.hidden = false; note.textContent = 'Could not load Google sign-in. Check your internet connection.';
      }
    }, 250);
  }

  function authMode(mode) {
    $('#emailLoginForm').hidden = mode !== 'login';
    $('#registerForm').hidden = mode !== 'register';
    $('#showLoginForm').classList.toggle('selected', mode === 'login');
    $('#showRegisterForm').classList.toggle('selected', mode === 'register');
    loginErr('');
  }
  $('#showLoginForm').addEventListener('click', () => authMode('login'));
  $('#showRegisterForm').addEventListener('click', () => authMode('register'));
  $('#emailLoginForm').addEventListener('submit', async (e) => {
    e.preventDefault(); loginErr('');
    try {
      const d = await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: $('#loginEmail').value, password: $('#loginPassword').value }) });
      enterApp(d.user);
    } catch (err) { loginErr(err.message); }
  });
  $('#registerForm').addEventListener('submit', async (e) => {
    e.preventDefault(); loginErr('');
    try {
      const d = await api('/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('#registerName').value, email: $('#registerEmail').value, password: $('#registerPassword').value }) });
      enterApp(d.user);
    } catch (err) { loginErr(err.message); }
  });

  $('#guestBtn').addEventListener('click', async () => {
    loginErr('');
    try { enterApp((await api('/api/auth/guest', { method: 'POST' })).user); } catch (e) { loginErr(e.message); }
  });
  $('#logoutBtn').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST' }); } finally {
      if (window.google && google.accounts) google.accounts.id.disableAutoSelect();
      showLogin();
    }
  });

  (async function boot() {
    try { state.cfg = await api('/api/config'); } catch { document.body.textContent = 'Cannot reach the WaveFlow server.'; return; }
    $('#guestBtn').hidden = !state.cfg.guest_enabled;
    initGoogle(state.cfg.google_client_id);
    try { enterApp((await api('/api/auth/me')).user); } catch { showLogin(); }
  })();
})();
