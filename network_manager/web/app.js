/* A responsive browser/mobile console. The API key is never persisted. */
(() => {
  'use strict';
  let key = '';
  let me = null;
  let refreshTimer = null;
  let data = {devices: [], groups: [], memberships: [], links: [], events: [], jobs: [], automation_rules: [], schedules: [], users: [], scripts: [], agents: [], telemetry: [], alerts: [], alert_policies: [], config_drafts: [], audit: [], schedule_runs: []};
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
  let vpnSession = null;
  let vpnLeaseTimer = null;
  let vpnClockTimer = null;
  let vpnZones = [];
  const vpnNotice = text => { $('#vpn-status').textContent=text; };
  function clearVpn(message='Disconnected') {
    if(vpnLeaseTimer) clearInterval(vpnLeaseTimer);
    if(vpnClockTimer) clearInterval(vpnClockTimer);
    vpnLeaseTimer=null;vpnClockTimer=null;vpnSession=null;$('#vpn-expiry').textContent='';vpnNotice(message);
  }
  async function disconnectVpn() {
    const session=vpnSession;clearVpn();
    if(session)await api('/vpn/disconnect',{method:'POST',body:{session_id:session.session_id}}).catch(()=>{});
  }
  async function refreshVpn() {
    vpnZones=await api('/vpn/zones');
    options($('#vpn-connect-form [name=zone_id]'),vpnZones,'Select an assigned zone');
    options($('#vpn-check-form [name=device_id]'),data.devices,'Select inventory device');
  }
  $('#vpn-refresh').addEventListener('click',()=>refreshVpn().catch(e=>vpnNotice(e.message)));
  $('#vpn-connect-form').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;
    try{
      await disconnectVpn();
      vpnSession=await api('/vpn/connect',{method:'POST',body:Object.fromEntries(new FormData(form))});
      vpnNotice(vpnSession.message);
      vpnLeaseTimer=setInterval(async()=>{if(!vpnSession)return;const id=vpnSession.session_id;try{const lease=await api('/vpn/heartbeat',{method:'POST',body:{session_id:id}});if(vpnSession?.session_id===id)Object.assign(vpnSession,lease);}catch(e){if(vpnSession?.session_id===id)clearVpn(e.message);}},30000);
      vpnClockTimer=setInterval(()=>{if(!vpnSession)return;const seconds=Math.max(0,Math.ceil(vpnSession.expires_at-Date.now()/1000));$('#vpn-expiry').textContent=`${vpnSession.mode==='platform'?'Zone: '+vpnSession.zone:'Local client mode'} · lease ${seconds}s · maximum until ${new Date(vpnSession.deadline*1000).toLocaleTimeString()}`;if(!seconds)clearVpn('Session expired. Connect again.');},1000);
    }catch(e){clearVpn(e.message);}finally{button.disabled=false;}
  });
  $('#vpn-disconnect').addEventListener('click',()=>disconnectVpn());
  $('#vpn-check-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!vpnSession)return vpnNotice('Start a platform VPN session first.');
    const fields=Object.fromEntries(new FormData(event.currentTarget));const button=event.currentTarget.querySelector('button');button.disabled=true;
    try{const result=await api('/vpn/check',{method:'POST',body:{session_id:vpnSession.session_id,device_id:Number(fields.device_id),port:Number(fields.port)}});vpnNotice(`Device TCP port ${result.port} is reachable through ${result.zone}.`);}catch(e){vpnNotice(e.message);}finally{button.disabled=false;}
  });
  window.addEventListener('pagehide',()=>{if(vpnSession&&key)fetch('/vpn/disconnect',{method:'POST',headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:JSON.stringify({session_id:vpnSession.session_id}),keepalive:true}).catch(()=>{});});
  let accounts = [];
  function renderAccounts() {
    const list = $('#account-list'); list.replaceChildren();
    const query = $('#account-search').value.toLowerCase();
    accounts.filter((a) => `${a.name} ${a.email} ${a.tenant_name} ${a.status}`.toLowerCase().includes(query)).forEach((a) => {
      const actions = node('div', 'toolbar');
      const choices = a.platform_id ? [] : a.status === 'pending' ? ['approve','reject'] : a.status === 'active' ? ['suspend'] : a.status === 'suspended' ? ['activate'] : [];
      choices.forEach((action) => {
        const button = node('button', 'secondary', action[0].toUpperCase()+action.slice(1)); button.type='button';
        button.addEventListener('click', async () => {
          if (!confirm(`${action} account for ${a.email}?`)) return;
          button.disabled=true;
          try { await api(`/platform/accounts/${a.id}/${action}`, {method:'POST'}); await refreshPlatform(); $('#platform-notice').textContent='Account updated.'; }
          catch (error) { $('#platform-notice').textContent=error.message; button.disabled=false; }
        }); actions.append(button);
      });
      card(list, a.name, `${a.email} · ${a.tenant_name || 'Existing workspace'} · ${new Date(a.created_at).toLocaleDateString()}`, a.status, actions);
    });
    if (!list.children.length) empty(list, 'No accounts match your search.');
  }
  async function refreshPlatform() {
    const zones=await api('/platform/vpn-zones'); const zoneList=$('#platform-vpn-zones');zoneList.replaceChildren();
    zones.forEach(z=>card(zoneList,z.name,`Tenants: ${z.tenant_ids.join(', ')} · TCP ports: ${z.allowed_ports.join(', ')}`,z.enabled?'available':'disabled'));
    if(!zones.length)empty(zoneList,'No platform VPN zones provisioned. Use deployment/vpn/install.yml with your VPN configuration.');
    accounts = await api('/platform/accounts');
    renderAccounts();
    $('#registration-open').checked = (await api('/platform/settings')).registration_open;
    const tenants = await api('/platform/tenants');
    const metrics = $('#platform-metrics'); metrics.replaceChildren();
    [['Workspaces',tenants.length],['Accounts',accounts.length],['Pending approvals',accounts.filter(a=>a.status==='pending').length],['Active accounts',accounts.filter(a=>a.status==='active').length]].forEach(([label,count])=>{const box=node('div','metric');box.append(node('b','',count),node('span','',label));metrics.append(box);});
    const list = $('#tenant-list'); list.replaceChildren();
    tenants.forEach((item) => card(list, item.name, `Tenant #${item.id} · ${item.users} users`, 'tenant'));
    options($('#tenant-admin-form select[name="tenant_id"]'), tenants, 'Select tenant');
    const admins = $('#platform-admin-list'); admins.replaceChildren();
    for (const tenant of tenants) {
      const entries = await api(`/platform/tenants/${tenant.id}/admins`);
      entries.forEach((entry) => {
        const revoke = node('button', 'secondary', 'Revoke'); revoke.type='button'; revoke.disabled=!entry.active || entries.filter((value)=>value.active).length<=1;
        revoke.addEventListener('click', async () => {
          if (!confirm(`Revoke ${entry.name} from ${tenant.name}?`)) return;
          try { await api(`/platform/tenants/${tenant.id}/admins/${entry.id}/revoke`, {method:'POST'}); await refreshPlatform(); $('#platform-notice').textContent='Tenant administrator revoked.'; }
          catch (error) { $('#platform-notice').textContent=error.message; }
        });
        card(admins, `${entry.name} · ${tenant.name}`, `Admin #${entry.id}`, entry.active?'active':'revoked', revoke);
      });
    }
    if (!tenants.length) empty(list, 'No tenants yet.');
    if (!admins.children.length) empty(admins, 'No tenant administrators.');
    const activity = $('#platform-audit-list'); activity.replaceChildren();
    (await api('/platform/audit')).forEach((item) => card(activity, item.action.replaceAll('_',' '), `Target ${item.target} · ${new Date(item.created_at).toLocaleString()}`));
    if (!activity.children.length) empty(activity, 'No platform activity yet.');
  }
  function platformResult(result) {
    $('#platform-key code').textContent = result.api_key;
    $('#platform-key').hidden = false;
    $('#platform-notice').textContent = 'Save the one-time key securely. It cannot be retrieved later.';
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
      ['Active alerts', data.alerts.filter((alert) => alert.state !== 'resolved').length],
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
    options($('#device-form select[name="owner_id"]'), data.users.filter((user) => user.active), 'Unassigned');
    options($('#ownership-form select[name="owner_id"]'), data.users.filter((user) => user.active), 'Unassigned');
    options($('#telemetry-device'), data.devices, 'All devices');
    options($('#alert-policy-form select[name="group_id"]'), data.groups, 'All devices');
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
      card(devices, item.name, `${item.kind} · ${item.address}${item.owner_id ? ` · owner #${item.owner_id}` : ''}${item.credential_ref ? ' · vault reference set' : ''}${item.notes ? ` · ${item.notes}` : ''}`, item.status, tags);
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
    const eventSearch = $('#event-search').value.toLowerCase().trim();
    data.events.filter((item) => `${item.message} ${deviceName(item.device_id)} ${item.severity} ${item.status}`.toLowerCase().includes(eventSearch)).forEach((item) => {
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
    if (!events.children.length) empty(events, eventSearch ? 'No matching events.' : 'No events have been recorded.');
    const telemetry = $('#telemetry-list'); telemetry.replaceChildren();
    const selectedDevice = Number($('#telemetry-device').value);
    data.telemetry.filter((item) => !selectedDevice || item.device_id === selectedDevice).forEach((item) =>
      card(telemetry, deviceName(item.device_id), `Agent #${item.agent_id} · CPU ${item.cpu_percent ?? '—'}% · memory ${item.memory_percent ?? '—'}% · ${new Date(item.created_at).toLocaleString()}`, item.status));
    if (!telemetry.children.length) empty(telemetry, 'No agent telemetry for this selection.');
    const alerts = $('#alert-list'); alerts.replaceChildren();
    const alertFilter = $('#alert-state').value;
    data.alerts.filter((item) => alertFilter === 'all' || (alertFilter === 'active' ? item.state !== 'resolved' : item.state === 'resolved')).forEach((item) => {
      const actions = node('div', 'tags');
      if (me.role !== 'viewer' && item.state !== 'resolved') {
        const action = item.state === 'open' ? 'acknowledge' : 'resolve';
        const button = node('button', 'secondary', action === 'acknowledge' ? 'Acknowledge' : 'Resolve'); button.type='button';
        button.addEventListener('click', async () => {
          try { await api(`/alerts/${item.id}/${action}`, {method:'POST'}); await refresh(); notice(`Alert ${action === 'acknowledge' ? 'acknowledged' : 'resolved'}.`); }
          catch (error) { notice(error.message,true); }
        }); actions.append(button);
      }
      card(alerts, `${item.policy_name} · ${deviceName(item.device_id)}`, `${item.state} · ${item.occurrences} report(s) · last ${new Date(item.last_seen).toLocaleString()}`, item.severity, actions);
    });
    if (!alerts.children.length) empty(alerts, 'No alerts in this view.');
    const policies = $('#alert-policy-list'); policies.replaceChildren();
    data.alert_policies.forEach((item) => {
      const button = node('button','secondary',item.enabled?'Pause':'Resume'); button.type='button'; button.disabled=me.role!=='admin';
      button.addEventListener('click', async () => {
        try { await api(`/alert_policies/${item.id}/${item.enabled?'pause':'resume'}`, {method:'POST'}); await refresh(); notice('Alert policy updated.'); }
        catch (error) { notice(error.message,true); }
      });
      const group = data.groups.find((entry)=>entry.id===item.group_id)?.name || 'All devices';
      card(policies,item.name,`${item.metric}${item.threshold===null?'':` ≥ ${item.threshold}%`} · ${group} · ${item.severity}`,item.enabled?'active':'paused',button);
    });
    if (!policies.children.length) empty(policies,'No alert policies yet.');
    const jobs = $('#job-list');
    jobs.replaceChildren();
    const jobSearch = $('#job-search').value.toLowerCase().trim();
    data.jobs.filter((item) => `${item.operation} ${deviceName(item.device_id)} ${item.state} ${item.id}`.toLowerCase().includes(jobSearch)).forEach((item) => {
      const actions = node('div', 'tags');
      if (item.can_approve) {
        const choice = node('input'); choice.type = 'checkbox'; choice.className = 'job-select'; choice.value = String(item.id);
        choice.setAttribute('aria-label', `Select job ${item.id} for approval`);
        actions.append(choice);
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
    if (!jobs.children.length) empty(jobs, jobSearch ? 'No matching tasks.' : 'No change requests yet.');
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
    data.automation_rules.forEach((item) => {
      const toggle = node('button', 'secondary', item.enabled ? 'Disable' : 'Enable'); toggle.type = 'button'; toggle.disabled = me.role !== 'admin';
      toggle.addEventListener('click', async () => {
        try { await api(`/automation_rules/${item.id}/${item.enabled ? 'disable' : 'enable'}`, {method: 'POST'}); await refresh(); notice(`Rule ${item.enabled ? 'disabled' : 'enabled'}.`); }
        catch (error) { notice(error.message, true); }
      });
      card(rules, item.name,
        `${item.severity} event · ${item.group_id ? data.groups.find((group) => group.id === item.group_id)?.name || 'Group' : 'All devices'} · ${item.operation.replaceAll('_', ' ')}`,
        item.enabled ? 'enabled' : 'disabled', toggle);
    });
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
    const agents = $('#agent-list'); agents.replaceChildren();
    if (me.role === 'admin') data.agents.forEach((item) => {
      const revoke = node('button', 'secondary', 'Revoke'); revoke.type = 'button'; revoke.disabled = !item.active;
      revoke.addEventListener('click', async () => {
        if (!confirm(`Revoke agent ${item.name}?`)) return;
        try { await api(`/agents/${item.id}/revoke`, {method:'POST'}); await refresh(); notice('Agent revoked.'); }
        catch (error) { notice(error.message, true); }
      });
      card(agents, item.name, `Agent #${item.id}`, item.active ? 'active' : 'revoked', revoke);
    });
    if (!data.agents.length) empty(agents, 'No agents enrolled.');
    const activity = $('#activity-list'); activity.replaceChildren();
    data.audit.forEach((entry) => card(activity, entry.action.replaceAll('_', ' '), `Target ${entry.target} · ${new Date(entry.created_at).toLocaleString()}`));
    data.schedule_runs.forEach((entry) => card(activity, `Schedule #${entry.schedule_id} ran`, `${entry.job_count} jobs queued · ${new Date(entry.created_at).toLocaleString()}`));
    if (!data.audit.length && !data.schedule_runs.length) empty(activity, 'No activity recorded yet.');
    $('#approve-selected').disabled = me.role !== 'admin';
    document.querySelectorAll('#workspace .form-card button').forEach((button) => { button.disabled = me.role === 'viewer'; });
    $('#rule-form button').disabled = me.role !== 'admin';
    $('#schedule-form button').disabled = me.role !== 'admin';
    $('#user-form button').disabled = me.role !== 'admin';
    $('#script-form button').disabled = me.role !== 'admin';
    $('#agent-form button').disabled = me.role !== 'admin';
    $('#ownership-form button').disabled = me.role !== 'admin';
    $('#alert-policy-form button').disabled = me.role !== 'admin';
    $('#device-form select[name="owner_id"]').disabled = me.role !== 'admin';
    $('#device-form input[name="credential_ref"]').disabled = me.role !== 'admin';
    document.querySelectorAll('[data-widget]').forEach((box) => { $(`#widget-${box.dataset.widget}`).hidden = !box.checked; });
  }
  async function refresh() {
    const collections = ['devices', 'groups', 'memberships', 'links', 'events', 'jobs', 'automation_rules', 'schedules', 'config_drafts', 'audit', 'schedule_runs', 'telemetry', 'alerts', 'alert_policies'];
    if (me.role === 'admin') collections.push('users', 'scripts', 'agents');
    const results = await Promise.all(collections.map((name) => api(`/${name}`)));
    collections.forEach((name, index) => { data[name] = results[index]; });
    render();
    await refreshVpn().catch(error=>vpnNotice(error.message));
    document.querySelectorAll('#vpn button').forEach(button=>{button.disabled=false;});
  }
  async function publicAuth(path, body) {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
    const result=await response.json(); if(!response.ok) throw new Error(result.error || 'Request failed'); return result;
  }
  $('#password-login-form').addEventListener('submit', async event=>{
    event.preventDefault(); const form=event.currentTarget; const button=form.querySelector('button');button.disabled=true;
    try {const result=await publicAuth('/auth/login',Object.fromEntries(new FormData(form))); key=result.token; form.reset(); await openConsole();}
    catch(error){$('#auth-notice').textContent=error.message;}finally{button.disabled=false;}
  });
  $('#register-form').addEventListener('submit', async event=>{
    event.preventDefault();const form=event.currentTarget;const values=Object.fromEntries(new FormData(form));
    if(values.password!==values.confirm_password){$('#auth-notice').textContent='Passwords do not match.';return;}
    delete values.confirm_password;const button=form.querySelector('button');button.disabled=true;
    try{const result=await publicAuth('/auth/register',values);form.reset();$('#auth-notice').textContent=result.message;}
    catch(error){$('#auth-notice').textContent=error.message;}finally{button.disabled=false;}
  });
  $('#account-open').addEventListener('click',async()=>{
    try{const a=await api('/auth/me');$('#account-summary').textContent=`${a.name} · ${a.role}${a.email?' · '+a.email:''}`;$('#enroll-form').hidden=a.account_login;$('#change-password-form').hidden=!a.account_login;$('#account-panel').hidden=false;$('#account-notice').textContent='';$('#account-panel').scrollIntoView({behavior:'smooth'});}
    catch(error){notice(error.message,true);}
  });
  $('#account-close').addEventListener('click',()=>{$('#account-panel').hidden=true;$('#rotated-key').textContent='';$('#rotated-key').hidden=true;$('#hide-rotated-key').hidden=true;});
  for(const [id,path] of [['enroll-form','enroll'],['change-password-form','password']]){
    $('#'+id).addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;
      try{const result=await api('/auth/'+path,{method:'POST',body:Object.fromEntries(new FormData(form))});form.reset();if(path==='password'){$('#lock').click();$('#auth-notice').textContent=result.message;}else{$('#account-notice').textContent=result.message;form.hidden=true;}}
      catch(error){$('#account-notice').textContent=error.message;}finally{button.disabled=false;}
    });
  }
  $('#rotate-key').addEventListener('click',async()=>{
    if(!confirm('Replace your API key now? The old key will stop working.'))return;
    try{const result=await api('/auth/rotate-key',{method:'POST'});const account=await api('/auth/me').catch(()=>null);if(!account)key=result.api_key;$('#rotated-key').textContent=result.api_key;$('#rotated-key').hidden=false;$('#hide-rotated-key').hidden=false;$('#account-notice').textContent='Save this key privately. Your old API key is invalid.';}
    catch(error){$('#account-notice').textContent=error.message;}
  });
  $('#hide-rotated-key').addEventListener('click',()=>{$('#rotated-key').textContent='';$('#rotated-key').hidden=true;$('#hide-rotated-key').hidden=true;});
  $('#account-search').addEventListener('input',renderAccounts);
  $('#accounts-refresh').addEventListener('click',()=>refreshPlatform().catch(e=>{$('#platform-notice').textContent=e.message;}));
  $('#save-registration').addEventListener('click',async()=>{try{await api('/platform/settings',{method:'POST',body:{registration_open:$('#registration-open').checked}});$('#platform-notice').textContent='Registration settings saved.';}catch(e){$('#platform-notice').textContent=e.message;}});
  fetch('/auth/config').then(r=>r.json()).then(c=>{if(!c.registration_open){$('#register-form').hidden=true;$('#auth-notice').textContent='Registration is currently closed. Contact your administrator for access.';}}).catch(()=>{});
  async function openConsole() {
    try {
      try { me = await api('/me'); }
      catch (error) {
        if (!error.message.includes('Authentication required')) throw error;
        me = await api('/platform/me');
        await refreshPlatform();
        $('#login').hidden = true; $('#platform').hidden = false; $('#lock').hidden = false;
        $('#identity').textContent = 'Super administrator'; $('#key').value = ''; $('#account-open').hidden=false;
        return;
      }
      const prefs = await api('/preferences');
      $('#refresh-interval').value = String(prefs.refresh_seconds);
      document.querySelectorAll('[data-widget]').forEach((box) => { box.checked = prefs.widgets[box.dataset.widget]; });
      await refresh();
      $('#key').value = '';
      $('#tenant').textContent = me.tenant;
      $('#identity').textContent = `${me.name} · ${me.role}`;
      $('#login').hidden = true;
      $('#workspace').hidden = false;
      $('#lock').hidden = false; $('#account-open').hidden=false;
      setAutoRefresh();
      notice('Connected to your tenant workspace.');
    } catch (error) { key = ''; throw error; }
  }
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); key=$('#key').value.trim();
    try { await openConsole(); } catch(error) { $('#auth-notice').textContent=error.message; }
  });
  $('#lock').addEventListener('click', () => {
    if (refreshTimer) clearInterval(refreshTimer);
    disconnectVpn();
    if (key) api('/auth/logout', {method:'POST'}).catch(()=>{});
    accounts=[]; $('#account-list').replaceChildren();
    key = ''; me = null; $('#identity').textContent='Netwizzard';
    $('#account-panel').hidden=true; $('#account-open').hidden=true; $('#rotated-key').textContent=''; $('#rotated-key').hidden=true; $('#hide-rotated-key').hidden=true;
    document.querySelectorAll('#login input[type=password], #account-panel input[type=password]').forEach(input=>{input.value='';});
    data = {devices: [], groups: [], memberships: [], links: [], events: [], jobs: [], automation_rules: [], schedules: [], users: [], scripts: [], agents: [], telemetry: [], alerts: [], alert_policies: [], config_drafts: [], audit: [], schedule_runs: []};
    $('#generated-key').textContent = ''; $('#one-time-key').hidden = true;
    $('#script-preview').textContent = ''; $('#script-preview').hidden = true;
    $('#workspace').hidden = true;
    $('#platform').hidden = true;
    $('#platform-key code').textContent = ''; $('#platform-key').hidden = true;
    $('#login').hidden = false;
    $('#lock').hidden = true;
  });
  $('#refresh').addEventListener('click', () => refresh().then(() => notice('Data refreshed.')).catch((error) => notice(error.message, true)));
  $('#activity-refresh').addEventListener('click', () => refresh().then(() => notice('Activity refreshed.')).catch((error) => notice(error.message, true)));
  $('#approve-selected').addEventListener('click', async () => {
    const job_ids = [...document.querySelectorAll('.job-select:checked')].map((box) => Number(box.value));
    if (!job_ids.length) return notice('Select pending jobs requested by another administrator.', true);
    try { const result = await api('/jobs/approve-batch', {method: 'POST', body: {job_ids}}); await refresh(); notice(`${result.approved.length} jobs approved.`); }
    catch (error) { notice(error.message, true); }
  });
  async function savePreferences() {
    const widgets = Object.fromEntries([...document.querySelectorAll('[data-widget]')].map((box) => [box.dataset.widget, box.checked]));
    try { await api('/preferences', {method: 'POST', body: {widgets, refresh_seconds: Number($('#refresh-interval').value)}}); }
    catch (error) { notice(error.message, true); }
  }
  function setAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    const seconds = Number($('#refresh-interval').value);
    if (seconds) refreshTimer = setInterval(() => refresh().catch((error) => notice(error.message, true)), seconds * 1000);
  }
  $('#refresh-interval').addEventListener('change', () => { setAutoRefresh(); savePreferences(); });
  document.querySelectorAll('[data-widget]').forEach((box) => {
    box.addEventListener('change', () => { render(); savePreferences(); });
  });
  $('#search').addEventListener('input', render);
  $('#event-search').addEventListener('input', render);
  $('#job-search').addEventListener('input', render);
  $('#telemetry-device').addEventListener('change', render);
  $('#telemetry-refresh').addEventListener('click', () => refresh().then(() => notice('Telemetry refreshed.')).catch((error) => notice(error.message,true)));
  $('#alert-refresh').addEventListener('click', () => refresh().then(() => notice('Alerts refreshed.')).catch((error) => notice(error.message,true)));
  $('#alert-state').addEventListener('change', render);
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
  form('#ownership-form', (fields) => `/devices/${Number(fields.device_id)}/update`, (fields) => ({owner_id:fields.owner_id ? Number(fields.owner_id) : null,credential_ref:fields.credential_ref}));
  form('#alert-policy-form', '/alert_policies', (fields) => ({name:fields.name, metric:fields.metric, severity:fields.severity, group_id:fields.group_id?Number(fields.group_id):null, threshold:fields.metric==='status_down'?null:Number(fields.threshold)}));
  $('#alert-policy-form select[name="metric"]').addEventListener('change', (event) => {
    const threshold = $('#alert-policy-form input[name="threshold"]');
    threshold.disabled = event.target.value === 'status_down'; threshold.required = !threshold.disabled;
  });
  $('#alert-policy-form select[name="metric"]').dispatchEvent(new Event('change'));
  $('#tenant-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { platformResult(await api('/platform/tenants', {method:'POST',body:Object.fromEntries(new FormData(event.currentTarget))})); event.currentTarget.reset(); await refreshPlatform(); }
    catch (error) { $('#platform-notice').textContent = error.message; }
  });
  $('#tenant-admin-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try { platformResult(await api(`/platform/tenants/${Number(fields.tenant_id)}/admins`, {method:'POST',body:{name:fields.name}})); event.currentTarget.reset(); await refreshPlatform(); }
    catch (error) { $('#platform-notice').textContent = error.message; }
  });
  $('#platform-key-hide').addEventListener('click', () => { $('#platform-key code').textContent=''; $('#platform-key').hidden=true; });
  $('#agent-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const result = await api('/agents', {method:'POST', body:Object.fromEntries(new FormData(event.currentTarget))});
      $('#agent-key code').textContent = result.agent_key; $('#agent-key').hidden = false;
      event.currentTarget.reset(); await refresh(); notice('Agent created. Save its one-time key securely.');
    } catch (error) { notice(error.message, true); }
  });
  $('#hide-agent-key').addEventListener('click', () => { $('#agent-key code').textContent=''; $('#agent-key').hidden=true; });
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
