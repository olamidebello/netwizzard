/* A responsive browser/mobile console. The API key is never persisted. */
(() => {
  'use strict';
  let key = '';
  let me = null;
  let refreshTimer = null;
  let data = {devices: [], groups: [], memberships: [], links: [], events: [], jobs: [], automation_rules: [], schedules: [], users: [], scripts: [], config_drafts: []};
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  };
  const notice = (message, error = false) => {
    const target = $('#notice');
    target.textContent = message;
    target.classList.toggle('error', error);
  };
  async function api(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || 'GET',
      headers: {Authorization: `Bearer ${key}`, ...(options.body ? {'Content-Type': 'application/json'} : {})},
      body: options.body && JSON.stringify(options.body),
      cache: 'no-store'
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }
  const deviceName = (id) => data.devices.find((device) => device.id === id)?.name || `Device #${id}`;
  function card(parent, title, subtitle, badge, action) {
    const row = node('div', 'card');
    const info = node('div');
    info.append(node('strong', '', title), node('small', '', subtitle));
    row.append(info);
    if (badge) row.append(node('span', `badge ${badge === 'critical' || badge === 'warning' ? badge : ''}`, badge));
    if (action) row.append(action);
    parent.append(row);
  }
  function empty(parent, message) {
    parent.append(node('div', 'empty', message));
  }
  function options(select, values, label) {
    const previous = select.value;
    select.replaceChildren();
    select.append(new Option(label, ''));
    values.forEach((item) => select.append(new Option(item.name, String(item.id))));
    select.value = previous;
  }
  function render() {
    const metrics = $('#metrics');
    metrics.replaceChildren();
    for (const [label, count] of [
      ['Devices', data.devices.length], ['Groups', data.groups.length],
      ['Open events', data.events.filter((event) => event.status !== 'resolved').length],
      ['Pending requests', data.jobs.filter((job) => job.state === 'pending_approval').length]
    ]) {
      const item = node('div', 'metric');
      item.append(node('b', '', count), node('span', '', label));
      metrics.append(item);
    }
    for (const select of document.querySelectorAll('select[name="device_id"],select[name="source_id"],select[name="target_id"]')) {
      options(select, data.devices, 'Select device');
    }
    options($('#assign-form select[name="group_id"]'), data.groups, 'Select group');
    options($('#deployment-form select[name="group_id"]'), data.groups, 'Select group');
    options($('#rule-form select[name="group_id"]'), data.groups, 'All devices');
    options($('#schedule-form select[name="group_id"]'), data.groups, 'Select group');
    const devices = $('#device-list');
    devices.replaceChildren();
    const search = $('#search').value.toLowerCase().trim();
    const shown = data.devices.filter((item) => `${item.name} ${item.address} ${item.kind}`.toLowerCase().includes(search));
    shown.forEach((item) => {
      const tags = node('div', 'tags');
      data.memberships.filter((entry) => entry.device_id === item.id).forEach((entry) => {
        const group = data.groups.find((candidate) => candidate.id === entry.group_id);
        if (!group) return;
        const remove = node('button', 'secondary', `${group.name} ×`);
        remove.type = 'button'; remove.disabled = me.role === 'viewer';
        remove.title = `Remove ${item.name} from ${group.name}`;
        remove.addEventListener('click', async () => {
          try { await api(`/devices/${item.id}/groups/${group.id}/remove`, {method: 'POST'}); await refresh(); notice('Group assignment removed.'); }
          catch (error) { notice(error.message, true); }
        });
        tags.append(remove);
      });
      const edit = node('button', 'secondary', 'Edit'); edit.type = 'button'; edit.disabled = me.role === 'viewer';
      edit.addEventListener('click', async () => {
        const name = prompt('Device name', item.name); if (name === null) return;
        const address = prompt('Device address', item.address); if (address === null) return;
        const kind = prompt('Device type', item.kind); if (kind === null) return;
        try { await api(`/devices/${item.id}/update`, {method: 'POST', body: {name, address, kind}}); await refresh(); notice('Device updated.'); }
        catch (error) { notice(error.message, true); }
      }); tags.append(edit);
      card(devices, item.name, `${item.kind} · ${item.address}${item.notes ? ` · ${item.notes}` : ''}`, item.status, tags);
    });
    if (!shown.length) empty(devices, search ? 'No devices match your search.' : 'No devices yet. Add your first device.');
    const groups = $('#group-list'); groups.replaceChildren();
    data.groups.forEach((item) => {
      const rename = node('button', 'secondary', 'Rename'); rename.type = 'button'; rename.disabled = me.role === 'viewer';
      rename.addEventListener('click', async () => {
        const name = prompt('New group name', item.name);
        if (!name || name.trim() === item.name) return;
        try { await api(`/groups/${item.id}/rename`, {method: 'POST', body: {name: name.trim()}}); await refresh(); notice('Group renamed.'); }
        catch (error) { notice(error.message, true); }
      });
      card(groups, item.name, `${data.memberships.filter((entry) => entry.group_id === item.id).length} devices`, '', rename);
    });
    if (!data.groups.length) empty(groups, 'No groups yet.');
    const configs = $('#config-list'); configs.replaceChildren();
    data.config_drafts.forEach((item) => {
      const view = node('button', 'secondary', 'Load'); view.type = 'button';
      view.addEventListener('click', async () => {
        try {
          const draft = await api(`/config_drafts/${item.id}`);
          $('#config-form select[name="device_id"]').value = String(draft.device_id);
          $('#config-form textarea[name="content"]').value = draft.content;
          notice('Draft loaded. Saving creates a new revision; it does not change the device.');
        } catch (error) { notice(error.message, true); }
      });
      card(configs, deviceName(item.device_id), new Date(item.created_at).toLocaleString(), 'draft', view);
    });
    if (!data.config_drafts.length) empty(configs, 'No configuration drafts yet.');
    const links = $('#link-list');
    links.replaceChildren();
    data.links.forEach((item) => card(links, `${deviceName(item.source_id)} → ${deviceName(item.target_id)}`, item.label || 'Connection', 'link'));
    if (!data.links.length) empty(links, 'No topology links have been recorded.');
    const events = $('#event-list');
    events.replaceChildren();
    data.events.forEach((item) => {
      const actions = node('div', 'tags');
      if (me.role !== 'viewer' && item.status !== 'resolved') {
        for (const action of item.status === 'open' ? ['acknowledge', 'resolve'] : ['resolve']) {
          const button = node('button', 'secondary', action === 'acknowledge' ? 'Acknowledge' : 'Resolve'); button.type = 'button';
          button.addEventListener('click', async () => {
            try { await api(`/events/${item.id}/${action}`, {method: 'POST'}); await refresh(); notice(`Event ${action === 'acknowledge' ? 'acknowledged' : 'resolved'}.`); }
            catch (error) { notice(error.message, true); }
          }); actions.append(button);
        }
      }
      card(events, item.message, `${deviceName(item.device_id)} · ${new Date(item.created_at).toLocaleString()} · ${item.status}`, item.severity, actions);
    });
    if (!data.events.length) empty(events, 'No events have been recorded.');
    const jobs = $('#job-list');
    jobs.replaceChildren();
    data.jobs.forEach((item) => {
      const actions = node('div', 'tags');
      if (me.role === 'admin' && item.state === 'pending_approval') {
        const action = node('button', 'secondary', 'Approve');
        action.type = 'button';
        action.addEventListener('click', async () => {
          try {
            await api(`/jobs/${item.id}/approve`, {method: 'POST'});
            notice('Request approved. Allowlisted Ansible actions require an enabled worker; review-only actions are never executed.');
            await refresh();
          } catch (error) { notice(error.message, true); }
        });
        actions.append(action);
      }
      if (me.role !== 'viewer' && ['pending_approval', 'approved'].includes(item.state)) {
        const cancel = node('button', 'secondary', 'Cancel'); cancel.type = 'button';
        cancel.addEventListener('click', async () => {
          try { await api(`/jobs/${item.id}/cancel`, {method: 'POST'}); await refresh(); notice('Task cancelled.'); }
          catch (error) { notice(error.message, true); }
        }); actions.append(cancel);
      }
      card(jobs, `${item.operation.replaceAll('_', ' ')} · ${deviceName(item.device_id)}`,
        `${item.schedule_at || 'No schedule'} · ${new Date(item.created_at).toLocaleString()}`, item.state, actions);
    });
    if (!data.jobs.length) empty(jobs, 'No change requests yet.');
    for (const [widget, items, format] of [
      ['devices', data.devices, (item) => [item.name, `${item.kind} · ${item.address}`]],
      ['tasks', data.jobs, (item) => [`${item.operation.replaceAll('_', ' ')} · ${deviceName(item.device_id)}`, item.state]],
      ['events', data.events, (item) => [item.message, `${deviceName(item.device_id)} · ${item.status}`]]
    ]) {
      const target = $(`#widget-${widget} .cards`); target.replaceChildren();
      items.slice(0, 5).forEach((item) => { const [title, detail] = format(item); card(target, title, detail); });
      if (!items.length) empty(target, `No ${widget} yet.`);
    }
    const rules = $('#rule-list');
    rules.replaceChildren();
    data.automation_rules.forEach((item) => card(rules, item.name,
      `${item.severity} event · ${item.group_id ? data.groups.find((group) => group.id === item.group_id)?.name || 'Group' : 'All devices'} · ${item.operation.replaceAll('_', ' ')}`,
      item.enabled ? 'enabled' : 'disabled'));
    if (!data.automation_rules.length) empty(rules, 'No event trigger rules yet.');
    const schedules = $('#schedule-list');
    schedules.replaceChildren();
    data.schedules.forEach((item) => {
      const action = node('button', 'secondary', item.enabled ? 'Pause' : 'Resume');
      action.type = 'button';
      action.disabled = me.role !== 'admin';
      action.addEventListener('click', async () => {
        try {
          await api(`/schedules/${item.id}/${item.enabled ? 'pause' : 'resume'}`, {method: 'POST'});
          notice(`Schedule ${item.enabled ? 'paused' : 'resumed'}.`);
          await refresh();
        } catch (error) { notice(error.message, true); }
      });
      const group = data.groups.find((entry) => entry.id === item.group_id)?.name || 'Group';
      card(schedules, item.name, `${group} · ${item.operation.replaceAll('_', ' ')} · every ${item.interval_seconds / 60} min · next ${new Date(item.next_run_at).toLocaleString()}`,
        item.enabled ? 'active' : 'paused', action);
    });
    if (!data.schedules.length) empty(schedules, 'No recurring schedules yet.');
    const users = $('#user-list'); users.replaceChildren();
    if (me.role === 'admin') {
      data.users.forEach((item) => {
        const revoke = node('button', 'secondary', 'Revoke'); revoke.type = 'button';
        revoke.disabled = !item.active || item.id === me.id;
        revoke.addEventListener('click', async () => {
          if (!confirm(`Revoke access for ${item.name}?`)) return;
          try { await api(`/users/${item.id}/revoke`, {method: 'POST'}); await refresh(); notice('User access revoked.'); }
          catch (error) { notice(error.message, true); }
        });
        card(users, item.name, item.role, item.active ? 'active' : 'revoked', revoke);
      });
    } else empty(users, 'Admin access required.');
    const scripts = $('#script-list'); scripts.replaceChildren();
    if (me.role === 'admin') data.scripts.forEach((item) => {
      const view = node('button', 'secondary', 'View'); view.type = 'button';
      view.addEventListener('click', async () => {
        try { const script = await api(`/scripts/${item.id}`); $('#script-preview').textContent = script.content; $('#script-preview').hidden = false; }
        catch (error) { notice(error.message, true); }
      });
      card(scripts, item.name, `SHA-256 ${item.sha256.slice(0, 16)}… · ${new Date(item.created_at).toLocaleString()}`, 'stored', view);
    });
    if (!data.scripts.length) empty(scripts, me.role === 'admin' ? 'No scripts uploaded.' : 'Admin access required.');
    document.querySelectorAll('.form-card button').forEach((button) => { button.disabled = me.role === 'viewer'; });
    $('#rule-form button').disabled = me.role !== 'admin';
    $('#schedule-form button').disabled = me.role !== 'admin';
    $('#user-form button').disabled = me.role !== 'admin';
    $('#script-form button').disabled = me.role !== 'admin';
    document.querySelectorAll('[data-widget]').forEach((box) => { $(`#widget-${box.dataset.widget}`).hidden = !box.checked; });
  }
  async function refresh() {
    const collections = ['devices', 'groups', 'memberships', 'links', 'events', 'jobs', 'automation_rules', 'schedules', 'config_drafts'];
    if (me.role === 'admin') collections.push('users', 'scripts');
    const results = await Promise.all(collections.map((name) => api(`/${name}`)));
    collections.forEach((name, index) => { data[name] = results[index]; });
    render();
  }
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    key = $('#key').value.trim();
    try {
      me = await api('/me');
      await refresh();
      $('#key').value = '';
      $('#tenant').textContent = me.tenant;
      $('#identity').textContent = `${me.name} · ${me.role}`;
      $('#login').hidden = true;
      $('#workspace').hidden = false;
      $('#lock').hidden = false;
      setAutoRefresh();
      notice('Connected to your tenant workspace.');
    } catch (error) { key = ''; alert(error.message); }
  });
  $('#lock').addEventListener('click', () => {
    if (refreshTimer) clearInterval(refreshTimer);
    key = ''; me = null;
    data = {devices: [], groups: [], memberships: [], links: [], events: [], jobs: [], automation_rules: [], schedules: [], users: [], scripts: [], config_drafts: []};
    $('#generated-key').textContent = ''; $('#one-time-key').hidden = true;
    $('#script-preview').textContent = ''; $('#script-preview').hidden = true;
    $('#workspace').hidden = true;
    $('#login').hidden = false;
    $('#lock').hidden = true;
  });
  $('#refresh').addEventListener('click', () => refresh().then(() => notice('Data refreshed.')).catch((error) => notice(error.message, true)));
  function setAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    const seconds = Number($('#refresh-interval').value);
    localStorage.setItem('netwizzard-refresh', String(seconds));
    if (seconds) refreshTimer = setInterval(() => refresh().catch((error) => notice(error.message, true)), seconds * 1000);
  }
  $('#refresh-interval').value = localStorage.getItem('netwizzard-refresh') || '0';
  $('#refresh-interval').addEventListener('change', setAutoRefresh);
  document.querySelectorAll('[data-widget]').forEach((box) => {
    box.checked = localStorage.getItem(`netwizzard-widget-${box.dataset.widget}`) !== 'false';
    box.addEventListener('change', () => { localStorage.setItem(`netwizzard-widget-${box.dataset.widget}`, String(box.checked)); render(); });
  });
  $('#search').addEventListener('input', render);
  document.querySelectorAll('.tabs button').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((tab) => tab.removeAttribute('aria-current'));
    button.setAttribute('aria-current', 'page');
    document.querySelectorAll('.panel').forEach((panel) => { panel.hidden = panel.id !== button.dataset.tab; });
  }));
  function form(selector, path, map) {
    $(selector).addEventListener('submit', async (event) => {
      event.preventDefault();
      const target = event.currentTarget;
      const fields = Object.fromEntries(new FormData(target));
      try {
        await api(typeof path === 'function' ? path(fields) : path, {method: 'POST', body: map ? map(fields) : fields});
        target.reset();
        notice('Saved successfully.');
        await refresh();
      } catch (error) { notice(error.message, true); }
    });
  }
  form('#device-form', '/devices');
  form('#group-form', '/groups');
  form('#assign-form', (fields) => `/devices/${Number(fields.device_id)}/groups/${Number(fields.group_id)}`, () => ({}));
  form('#link-form', '/links', (fields) => ({...fields, source_id: Number(fields.source_id), target_id: Number(fields.target_id)}));
  form('#event-form', '/events', (fields) => ({...fields, device_id: Number(fields.device_id)}));
  const timestamp = (value) => value ? new Date(value).toISOString() : null;
  form('#job-form', '/jobs', (fields) => ({...fields, device_id: Number(fields.device_id), schedule_at: timestamp(fields.schedule_at)}));
  form('#deployment-form', '/deployments', (fields) => ({...fields, group_id: Number(fields.group_id), schedule_at: timestamp(fields.schedule_at)}));
  form('#rule-form', '/automation_rules', (fields) => ({...fields, group_id: fields.group_id ? Number(fields.group_id) : null}));
  form('#schedule-form', '/schedules', (fields) => ({...fields, group_id: Number(fields.group_id), interval_seconds: Number(fields.interval_seconds), next_run_at: timestamp(fields.next_run_at)}));
  form('#config-form', (fields) => `/devices/${Number(fields.device_id)}/config`, (fields) => ({content: fields.content}));
  $('#user-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const result = await api('/users', {method: 'POST', body: Object.fromEntries(new FormData(event.currentTarget))});
      $('#generated-key').textContent = result.api_key;
      $('#one-time-key').hidden = false;
      event.currentTarget.reset(); await refresh(); notice('User created. Save the key now; it cannot be recovered.');
    } catch (error) { notice(error.message, true); }
  });
  $('#hide-key').addEventListener('click', () => { $('#generated-key').textContent = ''; $('#one-time-key').hidden = true; });
  function parseCsv(text) {
    const lines = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === '"') { if (quoted && text[i + 1] === '"') { field += '"'; i++; } else quoted = !quoted; }
      else if (c === ',' && !quoted) { row.push(field); field = ''; }
      else if ((c === '\n' || c === '\r') && !quoted) {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); if (row.some((value) => value.trim())) lines.push(row);
        row = []; field = '';
      } else field += c;
    }
    if (quoted) throw new Error('Unclosed quoted field in CSV');
    row.push(field); if (row.some((value) => value.trim())) lines.push(row);
    const headers = (lines.shift() || []).map((item) => item.trim().toLowerCase().replace(/^\ufeff/, ''));
    if (!['name', 'address', 'kind'].every((item) => headers.includes(item))) throw new Error('CSV needs name,address,kind headers');
    if (lines.some((item) => item.length !== headers.length)) throw new Error('CSV row has a different column count');
    return lines.map((items) => Object.fromEntries(headers.map((header, index) => [header, items[index]])));
  }
  $('#upload-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const file = event.currentTarget.elements.file.files[0];
      if (!file || file.size > 2000000) throw new Error('Select a CSV file under 2 MB');
      const rows = parseCsv(await file.text());
      const result = await api('/devices/import', {method: 'POST', body: {rows}});
      event.currentTarget.reset(); await refresh(); notice(`${result.imported} devices imported.`);
    } catch (error) { notice(error.message, true); }
  });
  $('#script-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const file = event.currentTarget.elements.file.files[0];
      if (!file || file.size > 131072) throw new Error('Select a script under 128 KB');
      const result = await api('/scripts', {method: 'POST', body: {name: file.name, content: await file.text()}});
      event.currentTarget.reset(); await refresh(); notice(`Script stored for review (${result.sha256.slice(0, 16)}…). It cannot execute.`);
    } catch (error) { notice(error.message, true); }
  });
  const csvCell = (value) => {
    let text = String(value ?? '');
    if (/^[\s]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  document.querySelectorAll('.export').forEach((button) => button.addEventListener('click', async () => {
    try {
      const result = await api(`/exports/${button.dataset.export}`);
      const csv = [result.columns, ...result.rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
      const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
      const link = node('a'); link.href = url; link.download = `netwizzard-${result.name}.csv`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice(`${result.rows.length} ${result.name} exported.`);
    } catch (error) { notice(error.message, true); }
  }));
})();
