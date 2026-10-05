/* A responsive browser/mobile console. The API key is never persisted. */
(() => {
  'use strict';
  let key = '';
  let me = null;
  let data = {devices: [], groups: [], links: [], events: [], jobs: [], automation_rules: [], schedules: []};
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
      ['Open events', data.events.filter((event) => event.severity !== 'info').length],
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
    shown.forEach((item) => card(devices, item.name, `${item.kind} · ${item.address}${item.notes ? ` · ${item.notes}` : ''}`, item.status));
    if (!shown.length) empty(devices, search ? 'No devices match your search.' : 'No devices yet. Add your first device.');
    const links = $('#link-list');
    links.replaceChildren();
    data.links.forEach((item) => card(links, `${deviceName(item.source_id)} → ${deviceName(item.target_id)}`, item.label || 'Connection', 'link'));
    if (!data.links.length) empty(links, 'No topology links have been recorded.');
    const events = $('#event-list');
    events.replaceChildren();
    data.events.forEach((item) => card(events, item.message, `${deviceName(item.device_id)} · ${new Date(item.created_at).toLocaleString()}`, item.severity));
    if (!data.events.length) empty(events, 'No events have been recorded.');
    const jobs = $('#job-list');
    jobs.replaceChildren();
    data.jobs.forEach((item) => {
      let action;
      if (me.role === 'admin' && item.state === 'pending_approval') {
        action = node('button', 'secondary', 'Approve');
        action.type = 'button';
        action.addEventListener('click', async () => {
          try {
            await api(`/jobs/${item.id}/approve`, {method: 'POST'});
            notice('Request approved. Allowlisted Ansible actions require an enabled worker; review-only actions are never executed.');
            await refresh();
          } catch (error) { notice(error.message, true); }
        });
      }
      card(jobs, `${item.operation.replaceAll('_', ' ')} · ${deviceName(item.device_id)}`,
        `${item.schedule_at || 'No schedule'} · ${new Date(item.created_at).toLocaleString()}`, item.state, action);
    });
    if (!data.jobs.length) empty(jobs, 'No change requests yet.');
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
    document.querySelectorAll('.form-card button').forEach((button) => { button.disabled = me.role === 'viewer'; });
    $('#rule-form button').disabled = me.role !== 'admin';
    $('#schedule-form button').disabled = me.role !== 'admin';
  }
  async function refresh() {
    const collections = ['devices', 'groups', 'links', 'events', 'jobs', 'automation_rules', 'schedules'];
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
      notice('Connected to your tenant workspace.');
    } catch (error) { key = ''; alert(error.message); }
  });
  $('#lock').addEventListener('click', () => {
    key = ''; me = null;
    data = {devices: [], groups: [], links: [], events: [], jobs: [], automation_rules: [], schedules: []};
    $('#workspace').hidden = true;
    $('#login').hidden = false;
    $('#lock').hidden = true;
  });
  $('#refresh').addEventListener('click', () => refresh().then(() => notice('Data refreshed.')).catch((error) => notice(error.message, true)));
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
})();
