'use strict';

const el = id => document.getElementById(id);
const nonce = document.querySelector('meta[name="local-nonce"]').content;
const unavailable = 'Unavailable';
const views = {overview:'Vehicle overview', signals:'Signal explorer', history:'Read history', connection:'Connection'};
const groups = {
  charge_state:'Battery & charge', climate_state:'Climate', tyres:'Tyres',
  vehicle_state:'Vehicle state', vehicle_config:'Configuration', gui_settings:'Vehicle preferences'
};
const steps = {
  configure:'Developer credentials', partner:'Partner authentication', key:'Public key availability',
  import_key:'Public key availability', hosted:'Public key availability', register:'EU application registration',
  verify:'EU application registration', signin:'Personal OAuth authorization', discover:'Vehicle discovery',
  cached:'Vehicle data retrieval', live:'Vehicle data retrieval'
};
const trendUnits = {
  'charge_state.battery_level':'%', 'charge_state.charger_power':'kW',
  'climate_state.inside_temp':'°C', 'vehicle_state.odometer':'mi'
};
let state;
let selectedCar;
let fields = new Map();
let busy = false;
let view = 'overview';

function textNode(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function signalKey(signal) {
  return signal.group + '.' + signal.field;
}

function systemFor(signal) {
  return signal.field.startsWith('tpms_') ? 'tyres' : signal.group;
}

function stamp(time, short = false) {
  if (!Number.isFinite(time) || time <= 0) return unavailable;
  const date = new Date(time);
  if (Number.isNaN(date.getTime())) return unavailable;
  const options = short ? {hour:'2-digit', minute:'2-digit', second:'2-digit'} :
    {year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'};
  return date.toLocaleString(undefined, options);
}

function convert(value, unit, difference = false) {
  const imperial = el('units').value === 'imperial';
  if (!imperial && unit === 'mi') return [value * 1.609344, 'km'];
  if (!imperial && unit === 'mph') return [value * 1.609344, 'km/h'];
  if (imperial && unit === 'bar') return [value * 14.5037738, 'psi'];
  if (imperial && unit === '°C') return [value * 1.8 + (difference ? 0 : 32), '°F'];
  return [value, unit];
}

function formatNumber(value, digits = 2) {
  return value.toLocaleString(undefined, {maximumFractionDigits:digits});
}

function formatted(value, unit = '', difference = false) {
  if (value == null || value === '') return unavailable;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value !== 'number') return String(value);
  if (!Number.isFinite(value)) return unavailable;
  if (unit === 'unix s') return stamp(value * 1000);
  const [number, displayUnit] = convert(value, unit, difference);
  const digits = ['km','mi','psi','°C','°F'].includes(displayUnit) ? 1 : 2;
  return formatNumber(number, digits) + (displayUnit === '%' ? '%' : displayUnit ? ' ' + displayUnit : '');
}

function raw(key) {
  return fields.get(key)?.value ?? null;
}

function reading(key) {
  const field = fields.get(key);
  return field ? formatted(field.value, field.unit) : unavailable;
}

function numberAt(key) {
  const value = raw(key);
  return Number.isFinite(value) ? value : null;
}

function fill(id, value) {
  const node = el(id);
  node.textContent = value;
  node.classList.toggle('unavailable', value === unavailable);
}

function pairs(id, rows) {
  const nodes = rows.map(([label, value]) => {
    const row = document.createElement('div');
    row.append(textNode('dt', label), textNode('dd', value, value === unavailable ? 'unavailable' : ''));
    return row;
  });
  el(id).replaceChildren(...nodes);
}

function flag(key, yes, no) {
  const value = raw(key);
  if (typeof value === 'boolean') return value ? yes : no;
  return reading(key);
}

function firstWord(value) {
  return typeof value === 'string' && value.trim() ? value.trim().split(/\s+/)[0] : unavailable;
}

function toggleBadge(id, value, positive) {
  const badge = el(id);
  badge.textContent = value;
  badge.classList.toggle('positive', positive);
}

function renderOverview() {
  const car = state.cars[selectedCar];
  const model = String(raw('vehicle_config.car_type') ?? '');
  const models = {model3:'Model 3',models:'Model S',modelx:'Model X',modely:'Model Y',cybertruck:'Cybertruck'};
  const niceModel = models[model.toLowerCase()] || model;
  el('model').textContent = niceModel ? 'Tesla / ' + niceModel : 'Tesla';
  el('vehicle-name').textContent = car?.name || 'Vehicle';
  el('vin').textContent = car ? 'VIN ' + car.vin : '';
  const showArt = /^model\s?3$/i.test(model);
  el('car-art').hidden = el('art-credit').hidden = !showArt;
  const vehicleState = car?.state || unavailable;
  toggleBadge('vehicle-state', vehicleState, vehicleState === 'online');
  el('vehicle-state').title = 'Last state returned by vehicle discovery';
  fill('odo', reading('vehicle_state.odometer'));
  fill('range', reading('charge_state.est_battery_range'));
  fill('firmware', firstWord(raw('vehicle_state.car_version')));
  el('firmware').title = reading('vehicle_state.car_version');
  const charging = reading('charge_state.charging_state');
  toggleBadge('charging', charging, charging.toLowerCase() === 'charging');

  const soc = numberAt('charge_state.battery_level');
  const known = soc !== null && soc >= 0 && soc <= 100;
  fill('soc', known ? formatNumber(soc, 1) : unavailable);
  if (known) el('soc').append(textNode('small','%'));
  const meter = el('soc-meter');
  meter.setAttribute('aria-valuetext', known ? soc + '%' : unavailable);
  if (known) meter.setAttribute('aria-valuenow',String(soc));
  else meter.removeAttribute('aria-valuenow');
  el('soc-fill').style.width = (known ? soc : 0) + '%';
  const limit = numberAt('charge_state.charge_limit_soc');
  const hasLimit = limit !== null && limit >= 0 && limit <= 100;
  el('soc-target').hidden = !hasLimit;
  if (hasLimit) el('soc-target').style.left = limit + '%';
  el('target-label').textContent = hasLimit ? 'Limit ' + limit + '%' : '';
  fill('power',reading('charge_state.charger_power'));
  fill('voltage',reading('charge_state.charger_voltage'));
  fill('current',reading('charge_state.charger_actual_current'));
  const timeKey = numberAt('charge_state.minutes_to_full_charge') !== null ?
    'charge_state.minutes_to_full_charge' : 'charge_state.time_to_full_charge';
  pairs('charge-detail',[
    ['Energy added',reading('charge_state.charge_energy_added')],
    ['Time to charge target',reading(timeKey)]
  ]);

  const inside = numberAt('climate_state.inside_temp');
  const outside = numberAt('climate_state.outside_temp');
  fill('inside',reading('climate_state.inside_temp'));
  fill('outside',reading('climate_state.outside_temp'));
  const temperatures = [inside, outside].filter(Number.isFinite);
  const low = Math.floor(Math.min(0, ...temperatures) / 10) * 10;
  const high = Math.ceil(Math.max(30, ...temperatures) / 10) * 10;
  for (const [id, value] of [['temp-inside',inside],['temp-outside',outside]]) {
    el(id).hidden = value === null;
    if (value !== null) el(id).style.left = (value - low) / (high - low) * 100 + '%';
  }
  el('temp-low').textContent = formatted(low,'°C');
  el('temp-high').textContent = formatted(high,'°C');
  const gap = inside !== null && outside !== null ? inside - outside : null;
  pairs('climate-detail',[
    ['Interior − exterior',gap === null ? unavailable : (gap > 0 ? '+' : '') + formatted(gap,'°C',true)],
    ['Driver setpoint',reading('climate_state.driver_temp_setting')]
  ]);
  const pressures = [];
  for (const wheel of ['fl','fr','rl','rr']) {
    const key = 'vehicle_state.tpms_pressure_' + wheel;
    fill(wheel,reading(key));
    if (numberAt(key) !== null) pressures.push(numberAt(key));
    const measured = raw('vehicle_state.tpms_last_seen_pressure_time_' + wheel);
    el(wheel).title = measured ? 'Tyre sensor reported ' + stamp(measured * 1000) : '';
  }
  const spread = pressures.length === 4 ? Math.max(...pressures) - Math.min(...pressures) : null;
  pairs('tyre-detail',[
    ['Highest − lowest',formatted(spread,'bar')],
    ['Cold front target',reading('vehicle_state.tpms_rcp_front_value')]
  ]);
  pairs('state-detail',[
    ['Door locks',flag('vehicle_state.locked','Locked','Unlocked')],
    ['Sentry mode',flag('vehicle_state.sentry_mode','On','Off')],
    ['Charge port',flag('charge_state.charge_port_door_open','Open','Closed')],
    ['Software update',reading('vehicle_state.software_update.status')]
  ]);
}

function renderSignals() {
  const query = el('signal-search').value.trim().toLowerCase();
  const system = el('group-filter').value;
  const signals = state.snapshot?.signals || [];
  const matching = signals.filter(signal => {
    const group = systemFor(signal);
    return (!system || group === system) && (!el('present-only').checked || signal.value != null) &&
      (signal.label + ' ' + signalKey(signal) + ' ' + (groups[group] || group)).toLowerCase().includes(query);
  });
  el('signal-count').textContent = matching.length + ' of ' + signals.length + ' signals';
  const rows = matching.map(signal => {
    const tr = document.createElement('tr');
    const label = textNode('td',signal.label);
    label.append(textNode('small',signalKey(signal)));
    const value = formatted(signal.value,signal.unit);
    const reported = state.snapshot.timestamps[signal.group];
    const time = textNode('td',stamp(reported), 'time');
    tr.append(label,textNode('td',value,value===unavailable?'unavailable':''),textNode('td',groups[systemFor(signal)]),time);
    return tr;
  });
  if (!rows.length) {
    const row = document.createElement('tr');
    const cell = textNode('td',signals.length?'No signals match these filters.':'Read a vehicle snapshot to inspect its signals.');
    cell.colSpan = 4;
    row.append(cell);
    rows.push(row);
  }
  el('signal-body').replaceChildren(...rows);
  el('export').disabled = !state.snapshot;
}

function downloadSnapshot() {
  const snap = state.snapshot;
  if (!snap || snap.car !== selectedCar) return;
  const output = {
    source:snap.cached?'Tesla cached vehicle data':'Tesla live vehicle data',
    read_at:new Date(snap.read_at).toISOString(),
    masked_vin:state.cars[selectedCar].vin,
    block_timestamps_ms:snap.timestamps,
    signals:snap.signals
  };
  const file = new Blob([JSON.stringify(output,null,2)],{type:'application/json'});
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'power-house-' + output.read_at.replace(/[:.]/g,'-') + '.json';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function trendPoints(reads, key) {
  const group = key.split('.')[0];
  const points = new Map();
  for (const sample of reads) {
    const time = sample.timestamps[group];
    const value = sample.readings[key];
    if (Number.isFinite(time) && time > 0 && Number.isFinite(value)) points.set(time,value);
  }
  return [...points].sort((a,b)=>a[0]-b[0]);
}

function drawChart(points, key) {
  const canvas = el('trend-chart');
  canvas.hidden = !points.length;
  if (!points.length) return;
  const width = Math.floor(canvas.getBoundingClientRect().width);
  if (!width) return;
  const height = 200;
  const ratio = Math.min(window.devicePixelRatio || 1,3);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio,ratio);
  const data = points.map(([time,value])=>[time,convert(value,trendUnits[key])[0]]);
  const values = data.map(point=>point[1]);
  const min = Math.min(...values), max = Math.max(...values);
  const pad = Math.max(.5,(max-min)*.18);
  let low = min-pad, high = max+pad;
  if (key === 'charge_state.battery_level') {
    low = Math.max(0,low); high = Math.min(100,high);
  }
  if (high <= low) high = low + 1;
  const first = data[0][0], last = data[data.length-1][0];
  ctx.font = '11px Consolas, monospace';
  const left = Math.ceil(Math.max(ctx.measureText(formatNumber(low,1)).width,ctx.measureText(formatNumber(high,1)).width))+12;
  const right = width-10, top = 12, bottom = height-28;
  const x = time => first===last?(left+right)/2:left+(right-left)*(time-first)/(last-first);
  const y = value => bottom-(bottom-top)*(value-low)/(high-low);
  ctx.lineWidth = 1;
  ctx.textBaseline = 'middle';
  for (let i=0;i<=3;i++) {
    const value = low+(high-low)*i/3;
    ctx.strokeStyle='#333341';
    ctx.beginPath(); ctx.moveTo(left,y(value));ctx.lineTo(right,y(value));ctx.stroke();
    ctx.fillStyle='#aaa8ba';ctx.textAlign='right';ctx.fillText(formatNumber(value,1),left-8,y(value));
  }
  if (data.length>1) {
    const baseline = data.find(([t])=>String(t)===el('baseline').value);
    if (baseline) {
      ctx.setLineDash([4,5]);ctx.strokeStyle='#8d865a';
      ctx.beginPath();ctx.moveTo(left,y(baseline[1]));ctx.lineTo(right,y(baseline[1]));ctx.stroke();ctx.setLineDash([]);
    }
    ctx.beginPath();data.forEach(([t,v],i)=>i?ctx.lineTo(x(t),y(v)):ctx.moveTo(x(t),y(v)));
    ctx.strokeStyle='#b9adff';ctx.lineWidth=2;ctx.stroke();
  }
  for (const [time,value] of data) {
    ctx.beginPath();ctx.arc(x(time),y(value),4,0,Math.PI*2);ctx.fillStyle='#d4f6a3';ctx.fill();
  }
  const label = t => new Date(first).toDateString()===new Date(last).toDateString() ? stamp(t,true) :
    new Date(t).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  ctx.fillStyle='#b1b0c0';ctx.textAlign=first===last?'center':'left';
  ctx.fillText(label(first),first===last?x(first):left,height-9);
  if (last!==first) {ctx.textAlign='right';ctx.fillText(label(last),right,height-9);}
}

function renderHistory() {
  const reads = state.history || [];
  const picker = el('trend-metric');
  const key = picker.value, unit = trendUnits[key];
  const points = trendPoints(reads,key);
  const base = el('baseline');
  const selected = base.value;
  const options = points.slice(0,-1).map(([time],index)=>new Option(index===0?'First · '+stamp(time,true):stamp(time),String(time)));
  base.replaceChildren(...(options.length?options:[new Option('First reading','')]));
  if (options.some(option=>option.value===selected)) base.value=selected;
  base.disabled=points.length<2;
  const name = picker.selectedOptions[0].text;
  el('hist-value-head').textContent = name;
  el('history-count').textContent = reads.length + ' / 32 reads';
  const value = points.length ? points[points.length-1][1] : null;
  fill('trend-value',formatted(value,unit));
  if (points.length>=2) {
    const baseline = points.find(([t])=>String(t)===base.value) || points[0];
    const delta = value - baseline[1];
    const changeUnit = unit==='%'?'pp':unit;
    el('trend-change').textContent = (delta>0?'+':'')+formatted(delta,changeUnit,true)+' since '+stamp(baseline[0]);
  } else el('trend-change').textContent='';
  el('trend-note').textContent = points.length>=2 ?
    points.length+' distinct Tesla timestamps. Points connect manual readings.' :
    points.length?'One timestamped reading. A second distinct timestamp will show a change.':'No timestamped readings for this metric yet.';
  drawChart(points,key);
  const group = key.split('.')[0];
  el('history-body').replaceChildren(...reads.slice().reverse().map(sample=>{
    const row=document.createElement('tr');
    row.append(textNode('td',stamp(sample.read_at),'time'),textNode('td',sample.cached?'Tesla cache':'Live read'),
      textNode('td',stamp(sample.timestamps[group]),'time'),textNode('td',formatted(sample.readings[key],unit)));
    return row;
  }));
}

function renderConnection(previous, op) {
  const checks=state.checks;
  const credentials=checks['Developer credentials'];
  const ready=['Entered for this session','Accepted by Tesla'].includes(credentials);
  if (!previous || previous.checks['Developer credentials']!==credentials) el('credentialPanel').open=!ready;
  const owner=checks['Personal OAuth authorization'], connected=owner==='Authorized';
  el('ownerStatus').textContent = connected?'Signed in with Tesla.':!ready?'Enter your developer credentials to enable sign-in.':
    owner==='Unchecked'?'Ready to sign in with Tesla.':owner;
  el('signin').disabled=!ready;
  el('signin').textContent=connected?'Sign in again with Tesla':'Sign in with Tesla';
  // An authorized owner can also retry a read after a transient token refresh error.
  el('discover').disabled=!connected && !Object.keys(state.cars).length;
  el('public').textContent=state.public;
  el('path').textContent=state.domain?'https://'+state.domain+state.key_path:'';
  if (state.domain && state.domain!==previous?.domain) {
    el('redirect').value='hosted';
    el('domain').value=state.domain;
  }
  el('checks').replaceChildren(...Object.entries(checks).map(([step,result])=>{
    const row=document.createElement('div');
    const ok=/^(Accepted|Partner token issued|Hosted key matches|Registered key verified|Authorized$|[1-9]\d* authorized|Live Tesla|Cached Tesla)/.test(result);
    row.append(textNode('dt',step),textNode('dd',result,result.startsWith('Failed')?'fail':ok?'good':''));
    return row;
  }));
  if (op==='configure' && ready) el('credentialPanel').open=false;
}

function renderView() {
  if (!state) return;
  const requested=location.hash.slice(1);
  const car=state.cars[selectedCar];
  view=Object.hasOwn(views,requested)?requested:'overview';
  if (!car) view='connection';
  for (const key of Object.keys(views)) el(key).hidden=key!==view;
  for (const link of document.querySelectorAll('nav a')) {
    if (link.hash==='#'+view) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  el('view-title').textContent=views[view];
  document.title=views[view]+' · Power House';
  el('vehicle-tools').hidden=el('read-actions').hidden=!car || view==='connection';
  const snap=state.snapshot;
  let sub='Choose a vehicle read to load its instruments.';
  if (snap) {
    const source=snap.cached?'Tesla cached snapshot':'Live vehicle snapshot';
    const times=Object.values(snap.timestamps).filter(time=>Number.isFinite(time) && time>0);
    const oldest=Math.min(...times), newest=Math.max(...times);
    if (!times.length) sub=source+' · Source time unavailable · Read '+stamp(snap.read_at);
    else if (newest-oldest<1000) sub=source+' · Reported '+stamp(newest);
    else sub=source+' · Reported between '+stamp(oldest)+' and '+stamp(newest);
  }
  if (view==='connection') sub='Tesla Fleet API · Europe';
  if (view==='signals') sub='Reported vehicle fields and their source timestamps.';
  if (view==='history') sub='The last 32 manual reads for this vehicle, kept in session memory.';
  el('view-sub').textContent=sub;
  if (view==='overview') renderOverview();
  if (view==='signals') renderSignals();
  if (view==='history') renderHistory();
}

function render(data, op) {
  const previous=state;
  state=data;
  if (!Object.hasOwn(data.cars,selectedCar)) selectedCar=data.snapshot?.car || Object.keys(data.cars)[0];
  fields=new Map((data.snapshot?.signals || []).map(field=>[signalKey(field),field]));
  el('cars').replaceChildren(...Object.entries(data.cars).map(([key,car])=>new Option(car.name+' · '+car.vin,key)));
  if (selectedCar) el('cars').value=selectedCar;
  const connected=data.checks['Personal OAuth authorization']==='Authorized';
  el('auth-label').textContent=connected?'Tesla connected':'Local session';
  el('auth-light').classList.toggle('on',connected);
  const car=data.cars[selectedCar];
  el('cached').disabled=!car;
  el('live').disabled=car?.state!=='online';
  el('live').title=el('live').disabled?'Discover vehicles when the car is online to enable live reads.':'Request one live snapshot';
  renderConnection(previous,op);
  renderView();
}

function message(id, text='') {
  el(id).textContent=text;
  el(id).hidden=!text;
}

async function send(op, fields={}, path='/action') {
  if (busy) return;
  busy=true;
  el('controls').disabled=true;
  message('error');
  message('notice',['cached','live'].includes(op)?'Reading vehicle data…':'');
  try {
    const body={op,...fields};
    if (selectedCar && !body.car && path==='/action') body.car=selectedCar;
    const response=await fetch(path,{method:'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-Local-Nonce':nonce},body:JSON.stringify(body)});
    if (!response.ok) throw Error('Local request failed (HTTP '+response.status+'). Reload Power House and retry.');
    const data=await response.json();
    message('notice');
    if (path==='/test') {
      el('legacyResult').textContent=[data.auth,data.api,data.vehicle,data.detail,...data.cars.map(c=>[c.name,c.state,c.vin].join(' · '))].join(' / ');
      return;
    }
    if (body.car && Object.hasOwn(data.cars,body.car)) selectedCar=body.car;
    render(data,op);
    const outcome=data.checks[steps[op]];
    if (outcome?.startsWith('Failed')) message('error',outcome);
    if (op==='status' && data.checks['Personal OAuth authorization'].startsWith('Failed')) message('error',data.checks['Personal OAuth authorization']);
    if (data.authorize) {
      const url=new URL(data.authorize);
      if (url.origin!=='https://auth.tesla.com' || url.pathname!=='/oauth2/v3/authorize') throw Error('Unexpected Tesla sign-in destination.');
      location.assign(url.href);
    }
  } catch(err) {
    if (selectedCar) el('cars').value=selectedCar;
    message('notice');message('error',err.message);
  } finally {busy=false;el('controls').disabled=false;}
}

function credentials() {
  const data={client_id:el('cid').value.trim(),secret:el('secret').value.trim()};
  el('secret').value='';
  return data;
}

el('credentials').onsubmit=e=>{e.preventDefault();send('configure',credentials());};
el('legacy').onclick=()=>{if(el('credentials').reportValidity())send('test',credentials(),'/test');};
el('useExisting').onclick=async()=>{
  const file=el('existing').files[0];
  if(!file || file.size>1024){message('error','Choose your OpenSSL public-key.pem file.');return;}
  const pem=(await file.text()).trim();
  if(!/^-----BEGIN PUBLIC KEY-----\s+[A-Za-z0-9+/=\s]+-----END PUBLIC KEY-----$/.test(pem)){
    message('error','Choose the PEM labeled BEGIN PUBLIC KEY.');return;
  }
  send('import_key',{domain:el('domain').value,pem});
};
el('key').onclick=()=>{const password=el('password').value;el('password').value='';send('key',{domain:el('domain').value,password});};
el('register').onclick=()=>{if(state?.domain && confirm('Register '+state.domain+' with Tesla Europe? This changes the partner account.'))send('register',{confirm:state.domain});};
el('signin').onclick=()=>{
  if(state?.checks['Personal OAuth authorization']==='Authorized' && !confirm('Start a new Tesla sign-in for this connected session?'))return;
  send('signin',{hosted:el('redirect').value==='hosted'});
};
for (const button of document.querySelectorAll('[data-op]')) button.onclick=()=>send(button.dataset.op);
for (const op of ['discover','cached','live']) el(op).onclick=()=>send(op);
el('cars').onchange=()=>send('status',{car:el('cars').value});
el('units').onchange=renderView;
for (const [key,label] of Object.entries(groups)) el('group-filter').add(new Option(label,key));
el('signal-search').oninput=renderSignals;
el('group-filter').onchange=el('present-only').onchange=renderSignals;
el('trend-metric').onchange=renderHistory;
el('baseline').onchange=renderHistory;
el('export').onclick=downloadSnapshot;
el('show-state').onclick=()=>{el('group-filter').value='vehicle_state';el('signal-search').value='';location.hash='signals';};
window.addEventListener('hashchange',renderView);
window.addEventListener('pageshow',event=>{if(event.persisted)send('status');});
document.addEventListener('keydown',event=>{
  if(event.key==='/' && !event.ctrlKey && !event.metaKey && !event.altKey &&
      !event.target.closest('input, select, textarea, [contenteditable]') && state?.cars[selectedCar]) {
    event.preventDefault();
    location.hash='signals';
    renderView();
    el('signal-search').focus();
  }
});
new ResizeObserver(()=>{if(state && view==='history')drawChart(trendPoints(state.history || [],el('trend-metric').value),el('trend-metric').value);}).observe(el('history'));
send('status');
