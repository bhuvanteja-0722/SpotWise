(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { analysis: null, studies: [], dashboard: null, sources: null, areas: [] };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const number = (value, digits = 0) => Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: digits });
  const money = (value, digits = 1) => {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const amount = Number(value);
    if (amount >= 10_000_000) return `₹${(amount / 10_000_000).toFixed(digits)}Cr`;
    if (amount >= 100_000) return `₹${(amount / 100_000).toFixed(digits)}L`;
    return `₹${Math.round(amount).toLocaleString('en-IN')}`;
  };
  const localTime = (value) => value ? new Date(value).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let payload;
    try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { detail: text || response.statusText }; }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === 'string' ? detail : detail?.message || JSON.stringify(detail || payload);
      throw new Error(message || `Request failed (${response.status})`);
    }
    return payload;
  }

  function toast(message) {
    const element = $('#toast');
    element.textContent = message;
    element.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove('show'), 2600);
  }

  function setApiStatus(connected, label) {
    const badge = $('#apiStatus');
    badge.innerHTML = `<i></i> ${connected ? 'API CONNECTED' : 'API OFFLINE'}`;
    badge.title = label || '';
    badge.classList.toggle('offline', !connected);
  }

  const savedTheme = localStorage.getItem('spotwise-theme');
  if (savedTheme === 'light') document.body.classList.add('light');
  $('.theme-toggle').addEventListener('click', () => {
    document.body.classList.toggle('light');
    localStorage.setItem('spotwise-theme', document.body.classList.contains('light') ? 'light' : 'dark');
  });

  document.querySelectorAll('.nav-link').forEach((link) => link.addEventListener('click', () => {
    document.querySelectorAll('.nav-link').forEach((item) => item.classList.remove('active'));
    link.classList.add('active');
  }));
  document.querySelectorAll('[data-scroll]').forEach((link) => link.addEventListener('click', () => document.querySelector(link.dataset.scroll)?.scrollIntoView({ behavior: 'smooth' })));

  async function start() {
    try {
      const markets = await api('/api/v1/markets');
      $('#citySelect').innerHTML = markets.markets.map((city) => `<option value="${escapeHtml(city)}">${escapeHtml(city)}</option>`).join('');
      $('#citySelect').value = markets.markets.includes('Mumbai') ? 'Mumbai' : markets.markets[0];
      $('#businessSelect').innerHTML = markets.business_types.map((business) => `<option>${escapeHtml(business)}</option>`).join('');
      setApiStatus(true, 'Connected to FastAPI and the SpotWise database');
      $('#citySelect').addEventListener('change', async () => {
        clearActiveStudy();
        $('#areaSelect').value = '';
        await refreshAreas();
        await refreshDashboard();
      });
      $('#areaSelect').addEventListener('change', () => { clearActiveStudy(); refreshDashboard(); });
      $('#businessSelect').addEventListener('change', () => { clearActiveStudy(); updateWorkforce(); });
      await Promise.all([refreshAreas(), refreshDashboard(), refreshStudies(), refreshSources(), refreshCoverage()]);
      initialiseScenario();
      updateScenario();
    } catch (error) {
      setApiStatus(false, error.message);
      $('#rentalRecords').textContent = 'Backend offline';
      $('#jobRecords').textContent = 'Start the API';
      $('#railRecords').textContent = 'See README';
      $('#rentChart').innerHTML = `<div class="error-state">${escapeHtml(error.message)}<br>Start SpotWise with <code>python run.py</code>.</div>`;
      $('#studyList').innerHTML = '<div class="empty-card">The API is offline. Start the backend to load saved studies.</div>';
    }
  }

  async function refreshAreas() {
    const city = $('#citySelect').value;
    const current = $('#areaSelect').value;
    const result = await api(`/api/v1/areas?city=${encodeURIComponent(city)}`);
    state.areas = result.areas;
    $('#areaSelect').innerHTML = '<option value="">Citywide</option>' + result.areas.map((area) => `<option value="${escapeHtml(area)}">${escapeHtml(area)}</option>`).join('');
    if (result.areas.includes(current)) $('#areaSelect').value = current;
  }

  async function refreshDashboard() {
    const city = $('#citySelect').value;
    const area = $('#areaSelect').value;
    const params = new URLSearchParams({ city });
    if (area) params.set('area', area);
    const response = await api(`/api/v1/dashboard?${params.toString()}`);
    state.dashboard = response;
    state.analysis = response.analysis;
    renderDashboard(response);
  }

  function renderDashboard(response) {
    const data = response.analysis;
    const city = response.selected_city;
    const area = response.selected_area;
    $('#rentalRecords').textContent = number(data.rentals.recordCount);
    $('#jobRecords').textContent = number(data.jobs.recordCount);
    $('#railRecords').textContent = number(data.rail.recordCount);
    $('#generatedAt').textContent = `AGGREGATES BUILT ${data.generatedAt}`;
    $('#marketRent').textContent = city.toLowerCase() === 'mumbai' ? money(data.rentals.medianMonthlyRent) : 'Mumbai data only';
    $('#validRentCount').textContent = `${number(data.rentals.validRentCount)} listings`;
    $('#validAreaCount').textContent = `${number(data.rentals.validAreaCount)} listings`;
    $('#rentCoverage').textContent = city.toLowerCase() === 'mumbai'
      ? `${number(data.rentals.localities.length)} localities · ${number(data.rentals.validRentCount)} valid rent rows`
      : 'Mumbai-only rental file · selected city has no rent observations';
    $('#plannerRentHint').textContent = city.toLowerCase() === 'mumbai'
      ? 'Select a Mumbai rent bar above to use its residential listing median as a starting reference.'
      : `No rental observations for ${city} are included. Replace the starting value with a current commercial rent quote.`;
    renderRents(data.rentals);
    renderWorkforce(cityData(data, city), $('#businessSelect').value);
    renderMobility(city, railData(data, city));
    renderSignals(response.uploaded_signals, area);
    updateScenario();
  }

  function cityData(data, city) {
    return data.jobs.cities.find((item) => item.name.toLowerCase() === city.toLowerCase());
  }

  function updateWorkforce() {
    if (!state.analysis) return;
    renderWorkforce(cityData(state.analysis, $('#citySelect').value), $('#businessSelect').value);
  }

  function clearActiveStudy() {
    window.activeStudyId = null;
    $('#updateStudyButton').hidden = true;
  }

  function railData(data, city) {
    return data.rail.cities.find((item) => item.name.toLowerCase() === city.toLowerCase());
  }

  function renderRents(rentals) {
    const areas = rentals.localities.slice(0, 9);
    const max = Math.max(...areas.map((item) => item.medianMonthlyRent || 0), 1);
    $('#rentChart').innerHTML = areas.map((item) => {
      const height = Math.max(7, Math.round(item.medianMonthlyRent / max * 100));
      return `<button class="rent-column" type="button" aria-label="Use ${escapeHtml(item.name)} residential rent median ${money(item.medianMonthlyRent)} as a starting reference" data-area="${escapeHtml(item.name)}"><span class="rent-amount">${money(item.medianMonthlyRent)}</span><span class="rent-bar" style="height:${height}%"></span><span class="rent-locality">${escapeHtml(item.name)}</span></button>`;
    }).join('');
    document.querySelectorAll('.rent-column').forEach((button) => button.addEventListener('click', () => {
      const item = rentals.localities.find((area) => area.name === button.dataset.area);
      if (!item) return;
      $('#inputRent').value = Math.round(item.medianMonthlyRent);
      updateScenario();
      toast(`${item.name} residential rent loaded as a starting reference`);
      $('#planner').scrollIntoView({ behavior: 'smooth' });
    }));
  }

  function renderWorkforce(cityDataItem, business) {
    if (!cityDataItem) return;
    $('#workforceTitle').textContent = `${cityDataItem.name} hiring snapshot`;
    $('#cityJobs').textContent = number(cityDataItem.postingCount);
    $('#citySalary').textContent = cityDataItem.medianAnnualSalary ? money(cityDataItem.medianAnnualSalary) : 'Not available';
    $('#salaryDisclosure').textContent = `${Number(cityDataItem.salaryDisclosureRate || 0).toFixed(1)}%`;
    $('#salaryDisclosureBar').style.width = `${Math.min(Number(cityDataItem.salaryDisclosureRate || 0), 100)}%`;
    const priorities = {
      'Cafe / restaurant': ['Food & hospitality', 'Customer service', 'Operations & logistics', 'Sales'],
      'Retail shop': ['Retail', 'Sales', 'Customer service', 'Operations & logistics'],
      'Salon / wellness': ['Customer service', 'Sales', 'Operations & logistics', 'Other'],
      'Fitness studio': ['Customer service', 'Sales', 'Operations & logistics', 'Other'],
      'Other local service': ['Customer service', 'Operations & logistics', 'Sales', 'Other'],
    }[business] || [];
    const roles = cityDataItem.topRoles.slice().sort((a, b) => {
      const ai = priorities.indexOf(a.name), bi = priorities.indexOf(b.name);
      return (ai < 0 ? 50 : ai) - (bi < 0 ? 50 : bi) || b.count - a.count;
    }).slice(0, 5);
    const max = Math.max(...roles.map((item) => item.count), 1);
    $('#roleBars').innerHTML = roles.map((item) => `<div class="role-row"><span class="role-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><span class="role-track"><i style="width:${Math.max(4, item.count / max * 100)}%"></i></span><span class="role-value">${number(item.count)}</span></div>`).join('');
    $('#skillCloud').innerHTML = cityDataItem.topSkills.slice(0, 8).map((item) => `<span class="skill-tag">${escapeHtml(item.name)} <b>${number(item.count)}</b></span>`).join('') || '<span class="skill-tag">No skill tags in this market</span>';
  }

  function renderMobility(city, mobility) {
    $('#mobilityTitle').textContent = `${city} · rail service records`;
    $('#stationCount').textContent = number(mobility?.stationCount ?? 0);
    $('#trainCount').textContent = number(mobility?.trainCount ?? 0);
    $('#cityRailRecords').textContent = number(mobility?.scheduleRowCount ?? 0);
  }

  function renderSignals(signals, area) {
    const entries = Object.entries(signals?.categories || {});
    if (!entries.length) {
      $('#signalGrid').innerHTML = `<div class="empty-card">No local datasets are imported for ${escapeHtml(area || $('#citySelect').value)} yet. Use the CSV importer below to add footfall, POI, traffic, parking, transit, workforce, wage, or local-cost data.</div>`;
      $('#signalScope').textContent = signals?.warnings?.join(' ') || '';
      return;
    }
    $('#signalGrid').innerHTML = entries.map(([category, item]) => {
      let value = number(item.total, 1);
      let unit = item.unit || 'records';
      if (category === 'transit' && signals.nearest_transit_distance_km != null) {
        value = `${signals.nearest_transit_distance_km.toFixed(2)} km`;
        unit = 'nearest uploaded stop';
      }
      const peak = item.peak_hour == null ? '' : `Peak observed hour · ${String(item.peak_hour).padStart(2, '0')}:00`;
      return `<article class="signal-card"><div class="signal-card-top"><span>${escapeHtml(item.label)}</span><b>${number(item.record_count)} rows</b></div><div class="signal-value">${escapeHtml(value)}</div><div class="signal-unit">${escapeHtml(unit)} · ${escapeHtml(item.metrics && Object.keys(item.metrics).slice(0, 2).join(', ') || 'uploaded metric')}</div><div class="signal-meta">${peak || `Median ${number(item.median, 1)} · average ${number(item.average, 1)}`}</div></article>`;
    }).join('');
    const radius = signals.radius_km ? ` · within ${signals.radius_km} km` : '';
    $('#signalScope').textContent = `${number(signals.record_count)} uploaded records for ${escapeHtml(area || signals.city)}${radius}. ${signals.warnings?.join(' ') || ''}`;
  }

  async function refreshCoverage() {
    try {
      const response = await api('/api/v1/coverage');
      $('#coverageGrid').innerHTML = response.items.map((item) => {
        const status = item.status.toLowerCase();
        const klass = status.includes('partial') ? 'partial' : status.includes('scenario') ? 'scenario' : status.includes('imported') ? 'imported' : '';
        return `<article class="coverage-item"><div class="coverage-top"><b>${escapeHtml(item.name)}</b><span class="coverage-state ${klass}">${escapeHtml(item.status.toUpperCase())}</span></div><p>${escapeHtml(item.need)}</p></article>`;
      }).join('');
    } catch (error) { $('#coverageGrid').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`; }
  }

  async function refreshSources() {
    try {
      state.sources = await api('/api/v1/data-sources');
      const sourceCards = state.sources.kaggle_sources.map((source) => {
        const details = source.key === 'rentals'
          ? 'Mumbai residential listings; rent and area values summarized by locality inferred from listing titles.'
          : source.key === 'jobs'
            ? 'India job postings; city matching, role groups, INR salary summaries, and skill tag counts.'
            : 'Historical Indian train timetable; station names and unique train IDs matched to cities.';
        return `<article class="source-card"><div class="source-card-head"><h4>${escapeHtml(source.name)}</h4><span class="source-year">${escapeHtml(source.lastUpdated)}</span></div><p>${details}</p><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">Open dataset ↗</a><span class="source-license">${escapeHtml(source.license)} · ${escapeHtml(source.records)} source rows</span></article>`;
      });
      $('#sourceList').innerHTML = sourceCards.join('');
      renderUploadedSources();
    } catch (error) { $('#sourceList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`; }
  }

  function renderUploadedSources() {
    const batches = state.sources?.uploaded_sources || [];
    if (!batches.length) {
      $('#uploadedList').innerHTML = '<div class="uploaded-empty">No user datasets imported yet. Uploaded rows are stored in the local SpotWise database.</div>';
      return;
    }
    $('#uploadedList').innerHTML = `<div class="uploaded-heading">LOCAL DATABASE IMPORTS <span>${batches.length}</span></div>` + batches.map((batch) => `<div class="uploaded-row"><div><b>${escapeHtml(batch.name)}</b><small>${escapeHtml(batch.category.replaceAll('_', ' '))} · ${number(batch.row_count)} records · ${escapeHtml(batch.source)} · ${localTime(batch.imported_at)}</small></div><button class="text-button delete-import" data-id="${batch.id}" aria-label="Delete ${escapeHtml(batch.name)}">Remove</button></div>`).join('');
    document.querySelectorAll('.delete-import').forEach((button) => button.addEventListener('click', async () => {
      const batch = batches.find((item) => item.id === Number(button.dataset.id));
      if (!batch || !window.confirm(`Remove “${batch.name}” and its imported rows from the local database?`)) return;
      try {
        await api(`/api/v1/data-sources/${batch.id}`, { method: 'DELETE' });
        await Promise.all([refreshSources(), refreshCoverage(), refreshDashboard()]);
        toast('Imported dataset removed');
      } catch (error) { toast(error.message); }
    }));
  }

  async function refreshStudies() {
    try {
      const response = await api('/api/v1/studies');
      state.studies = response.studies;
      renderStudies();
    } catch (error) { $('#studyList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`; }
  }

  function renderStudies() {
    if (!state.studies.length) {
      $('#studyList').innerHTML = '<div class="empty-card"><span class="empty-mark">＋</span><b>No saved studies yet.</b><span>Create one to persist an area, your assumptions, and available evidence in SQLite.</span></div>';
      $('#compareButton').disabled = true;
      return;
    }
    $('#studyList').innerHTML = state.studies.map((study) => {
      const result = study.results || {};
      const score = result.readiness_score == null ? 'Need more data' : `${result.readiness_score} / 100`;
      const location = [study.area, study.city].filter(Boolean).map(escapeHtml).join(', ');
      const factorCount = result.scored_factor_count || 0;
      return `<article class="study-card"><div class="study-card-head"><label class="compare-toggle"><input class="study-check" type="checkbox" value="${study.id}" aria-label="Select ${escapeHtml(study.name)} for comparison"><span>COMPARE</span></label><span class="study-date">${localTime(study.updated_at)}</span></div><h3>${escapeHtml(study.name)}</h3><div class="study-place">⌖ ${location}</div><div class="study-business">${escapeHtml(study.business_type)} · ${study.radius_km} km radius</div><div class="study-score"><b>${escapeHtml(score)}</b><span>${factorCount} factor${factorCount === 1 ? '' : 's'} scored</span></div><div class="study-card-actions"><button class="text-button revisit-study" data-id="${study.id}">Open planner ↗</button><button class="text-button remove-study" data-id="${study.id}" aria-label="Delete ${escapeHtml(study.name)}">Delete</button></div></article>`;
    }).join('');
    document.querySelectorAll('.study-check').forEach((checkbox) => checkbox.addEventListener('change', updateCompareButton));
    document.querySelectorAll('.revisit-study').forEach((button) => button.addEventListener('click', () => revisitStudy(Number(button.dataset.id))));
    document.querySelectorAll('.remove-study').forEach((button) => button.addEventListener('click', () => removeStudy(Number(button.dataset.id))));
    updateCompareButton();
  }

  function updateCompareButton() {
    const count = document.querySelectorAll('.study-check:checked').length;
    $('#compareButton').disabled = count < 2 || count > 4;
    $('#compareButton').textContent = count > 4 ? 'Choose up to 4 studies' : `Compare selected${count ? ` (${count})` : ''} ⇄`;
  }

  async function revisitStudy(id) {
    const study = state.studies.find((item) => item.id === id);
    if (!study) return;
    $('#citySelect').value = study.city;
    await refreshAreas();
    $('#areaSelect').value = study.area || '';
    await refreshDashboard();
    $('#businessSelect').value = study.business_type;
    updateWorkforce();
    window.activeStudyId = study.id;
    $('#updateStudyButton').hidden = false;
    const assumptions = study.assumptions || {};
    const mapping = { monthly_rent: 'inputRent', staff_count: 'inputStaff', monthly_pay_per_staff: 'inputPay', other_fixed_costs: 'inputOther', average_ticket: 'inputTicket', gross_margin_percent: 'inputMargin', expected_customers_per_day: 'inputCustomers' };
    Object.entries(mapping).forEach(([key, selector]) => { if (assumptions[key] != null) $(`#${selector}`).value = assumptions[key]; });
    updateScenario();
    $('#planner').scrollIntoView({ behavior: 'smooth' });
  }

  async function removeStudy(id) {
    const study = state.studies.find((item) => item.id === id);
    if (!study || !window.confirm(`Delete saved study “${study.name}”?`)) return;
    try {
      await api(`/api/v1/studies/${id}`, { method: 'DELETE' });
      await refreshStudies();
      toast('Saved study deleted');
    } catch (error) { toast(error.message); }
  }

  $('#compareButton').addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('.study-check:checked')].map((input) => Number(input.value));
    if (ids.length < 2 || ids.length > 4) return;
    try {
      const comparison = await api('/api/v1/studies/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ study_ids: ids }) });
      renderComparison(comparison);
    } catch (error) { toast(error.message); }
  });

  $('#updateStudyButton').addEventListener('click', async () => {
    const id = window.activeStudyId;
    if (!id) return;
    const assumptions = {
      ...(state.studies.find((study) => study.id === id)?.assumptions || {}),
      monthly_rent: Number($('#inputRent').value) || 0,
      staff_count: Number($('#inputStaff').value) || 0,
      monthly_pay_per_staff: Number($('#inputPay').value) || 0,
      other_fixed_costs: Number($('#inputOther').value) || 0,
      average_ticket: Number($('#inputTicket').value) || 0,
      gross_margin_percent: Number($('#inputMargin').value) || 0,
      expected_customers_per_day: Number($('#inputCustomers').value) || 0,
    };
    try {
      await api(`/api/v1/studies/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ assumptions }) });
      await refreshStudies();
      toast('Saved study assumptions updated');
    } catch (error) { toast(error.message); }
  });

  function renderComparison(comparison) {
    const factors = comparison.factor_keys;
    const header = comparison.studies.map((study) => `<th>${escapeHtml(study.name)}<small>${escapeHtml(study.area || study.city)}</small></th>`).join('');
    const readiness = comparison.studies.map((study) => `<td>${study.results.readiness_score == null ? 'Not enough data' : `${study.results.readiness_score} / 100`}</td>`).join('');
    const factorRows = factors.map((key) => `<tr><th>${escapeHtml(key.replaceAll('_', ' '))}</th>${comparison.studies.map((study) => {
      const factor = study.results.factor_scores[key];
      return `<td>${factor ? `${factor.score} / 100<small>${number(factor.value, 1)} ${escapeHtml(factor.unit)}</small>` : 'No data'}</td>`;
    }).join('')}</tr>`).join('');
    $('#compareResults').innerHTML = `<div class="comparison-wrap"><div class="comparison-heading"><b>Study comparison</b><button class="text-button" id="closeComparison">Close</button></div><table class="comparison-table"><thead><tr><th>Factor</th>${header}</tr></thead><tbody><tr class="comparison-score"><th>Readiness heuristic</th>${readiness}</tr>${factorRows}</tbody></table><p>Scores are computed from uploaded evidence and saved targets; they are not success predictions.</p></div>`;
    $('#closeComparison').addEventListener('click', () => { $('#compareResults').innerHTML = ''; });
    $('#compareResults').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  const studyDialog = $('#studyDialog');
  const openStudyDialog = () => {
    const form = $('#studyForm');
    form.reset();
    window.activeStudyId = null;
    $('#updateStudyButton').hidden = true;
    form.elements.name.value = `${$('#businessSelect').value} — ${$('#areaSelect').value || $('#citySelect').value}`;
    form.elements.area.value = $('#areaSelect').value;
    studyDialog.showModal();
  };
  $('#saveStudyButton').addEventListener('click', openStudyDialog);
  $('#newStudyButton').addEventListener('click', openStudyDialog);
  $('#closeStudyDialog').addEventListener('click', () => studyDialog.close());
  $('#cancelStudy').addEventListener('click', () => studyDialog.close());

  $('#studyForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const assumptions = {};
    const assumptionKeys = ['monthly_rent', 'rent_budget', 'minimum_daily_footfall', 'maximum_competitors', 'max_transit_distance_km', 'minimum_parking_spaces', 'required_workers', 'maximum_monthly_salary', 'maximum_cost_of_living', 'minimum_traffic_speed_kmh', 'expected_customers_per_day', 'average_ticket', 'gross_margin_percent', 'staff_count', 'monthly_pay_per_staff', 'other_fixed_costs'];
    assumptionKeys.forEach((key) => { const raw = formData.get(key); if (raw !== '') assumptions[key] = Number(raw); });
    const latRaw = formData.get('latitude'), lonRaw = formData.get('longitude');
    const payload = {
      name: String(formData.get('name')).trim(), city: $('#citySelect').value,
      area: String(formData.get('area') || '').trim() || null,
      business_type: $('#businessSelect').value,
      radius_km: Number(formData.get('radius_km')),
      latitude: latRaw === '' ? null : Number(latRaw), longitude: lonRaw === '' ? null : Number(lonRaw),
      assumptions, weights: {},
    };
    try {
      const saved = await api('/api/v1/studies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      studyDialog.close();
      await refreshStudies();
      await refreshDashboard();
      toast(`Saved “${saved.name}” to the database`);
    } catch (error) { toast(error.message); }
  });

  async function initialiseScenario() {
    try {
      const data = state.analysis || (await api('/api/v1/dashboard?city=Mumbai')).analysis;
      const rent = data.rentals.localities.find((item) => item.name === 'Andheri West') || data.rentals.localities[0];
      if (rent && !Number($('#inputRent').value)) $('#inputRent').value = Math.round(rent.medianMonthlyRent);
    } catch (_) { /* Use the visible user-entered defaults while the API is offline. */ }
    ['inputRent', 'inputStaff', 'inputPay', 'inputOther', 'inputTicket', 'inputMargin', 'inputCustomers'].forEach((id) => $(`#${id}`).addEventListener('input', updateScenario));
    try {
      const stored = JSON.parse(localStorage.getItem('spotwise-scenario') || 'null');
      const mapping = { rent: 'inputRent', staff: 'inputStaff', pay: 'inputPay', other: 'inputOther', ticket: 'inputTicket', margin: 'inputMargin', customers: 'inputCustomers' };
      if (stored) Object.entries(mapping).forEach(([key, selector]) => { if (stored[key] != null) $(`#${selector}`).value = stored[key]; });
    } catch (_) { /* Ignore invalid local browser storage. */ }
    $('#resetPlanner').addEventListener('click', () => {
      const localRent = $('#citySelect').value.toLowerCase() === 'mumbai' ? state.analysis?.rentals.localities.find((item) => item.name === 'Andheri West')?.medianMonthlyRent || 0 : 0;
      const values = { inputRent: localRent, inputStaff: 4, inputPay: 22000, inputOther: 60000, inputTicket: 350, inputMargin: 62, inputCustomers: 110 };
      Object.entries(values).forEach(([id, value]) => { $(`#${id}`).value = Math.round(value); });
      updateScenario();
      toast('Scenario assumptions reset');
    });
  }

  function updateScenario() {
    if (!$('#inputRent')) return;
    const rent = Math.max(0, Number($('#inputRent').value) || 0);
    const staff = Math.max(0, Number($('#inputStaff').value) || 0);
    const pay = Math.max(0, Number($('#inputPay').value) || 0);
    const other = Math.max(0, Number($('#inputOther').value) || 0);
    const ticket = Math.max(0, Number($('#inputTicket').value) || 0);
    const margin = Math.min(100, Math.max(1, Number($('#inputMargin').value) || 1)) / 100;
    const customers = Math.max(0, Number($('#inputCustomers').value) || 0);
    const fixed = rent + staff * pay + other;
    const sales = ticket * customers * 30;
    const surplus = sales * margin - fixed;
    $('#netResult').textContent = `${surplus >= 0 ? '+' : '−'}${money(Math.abs(surplus))}`;
    $('#netResult').style.color = surplus >= 0 ? 'var(--lime)' : 'var(--amber)';
    $('#breakevenSales').textContent = money(margin ? fixed / margin : 0);
    $('#breakevenCustomers').textContent = ticket && margin ? `${(fixed / (ticket * margin * 30)).toFixed(1)} / day` : 'Add spend and margin';
    $('#estimatedSales').textContent = money(sales);
    const verdict = $('#scenarioVerdict');
    verdict.className = `result-verdict ${surplus >= 0 ? 'positive' : 'negative'}`;
    verdict.textContent = surplus >= 0 ? `At these assumptions, gross profit covers fixed monthly costs by ${money(surplus)}.` : `At these assumptions, sales fall short of fixed costs by ${money(Math.abs(surplus))}.`;
    localStorage.setItem('spotwise-scenario', JSON.stringify({ rent, staff, pay, other, ticket, margin: margin * 100, customers }));
  }

  $('#importForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = $('#importMessage');
    const button = $('#importButton');
    button.disabled = true;
    button.innerHTML = 'Importing rows…';
    message.textContent = 'The file will be parsed into normalized local records; its raw contents will not be retained.';
    try {
      const form = new FormData(event.currentTarget);
      const result = await api('/api/v1/datasets/import', { method: 'POST', body: form });
      message.textContent = `${result.name}: ${number(result.row_count)} rows imported. ${result.warnings.join(' ')}`;
      event.currentTarget.reset();
      await Promise.all([refreshAreas(), refreshDashboard(), refreshSources(), refreshCoverage()]);
      toast('Dataset imported and analyzed');
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; button.innerHTML = 'Import and analyze <span>↥</span>'; }
  });

  $('#exportButton').addEventListener('click', () => {
    const snapshot = {
      project: 'SpotWise location research', city: $('#citySelect').value, area: $('#areaSelect').value || null,
      businessType: $('#businessSelect').value, marketContext: state.dashboard?.market_context || null,
      uploadedSignals: state.dashboard?.uploaded_signals || null,
      planner: { monthlyRent: Number($('#inputRent').value), staff: Number($('#inputStaff').value), monthlyPayPerStaff: Number($('#inputPay').value), otherFixedCosts: Number($('#inputOther').value), averageTicket: Number($('#inputTicket').value), grossMarginPercent: Number($('#inputMargin').value), expectedCustomersPerDay: Number($('#inputCustomers').value) },
      notice: 'Descriptive analysis and user-entered scenario only. Source coverage and freshness limits apply.',
      generatedAt: new Date().toISOString(),
    };
    const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = `spotwise-${$('#citySelect').value.toLowerCase().replace(/\s+/g, '-')}-snapshot.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    toast('Research snapshot downloaded');
  });

  start();
})();
