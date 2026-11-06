(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { analysis: null, studies: [], dashboard: null, sources: null, areas: [] };
  const bootStartedAt = performance.now();
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

  async function finishBootScreen() {
    const screen = $('#bootScreen');
    if (!screen) return;
    const remaining = Math.max(0, 520 - (performance.now() - bootStartedAt));
    if (remaining) await new Promise((resolve) => setTimeout(resolve, remaining));
    screen.classList.add('is-ready');
    screen.setAttribute('aria-hidden', 'true');
    setTimeout(() => screen.remove(), 320);
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
      await refreshMarkets();
      setApiStatus(true, 'Connected to FastAPI and the SpotWise database');
      updateSelectionSummary();
      $('#citySelect').addEventListener('change', async () => {
        clearActiveStudy();
        $('#areaSelect').value = '';
        state.areas = [];
        renderAreaSuggestions();
        hideAreaSuggestions();
        updateSelectionSummary();
        updateWorkspaceMarket();
        await refreshAreas();
        await refreshDashboard();
        await refreshCoverage();
      });
      $('#workspaceMarket').addEventListener('click', () => {
        const picker = $('#workspaceMarketPicker');
        picker.hidden = !picker.hidden;
        $('#workspaceMarket').setAttribute('aria-expanded', String(!picker.hidden));
      });
      $('#workspaceMarketPicker').addEventListener('click', (event) => {
        const option = event.target.closest('[data-market]');
        if (!option) return;
        const market = $('#citySelect');
        market.value = option.dataset.market;
        market.dispatchEvent(new Event('change', { bubbles: true }));
        $('#workspaceMarketPicker').hidden = true;
        $('#workspaceMarket').setAttribute('aria-expanded', 'false');
      });
      document.addEventListener('click', (event) => {
        if (event.target.closest('.workspace-picker-wrap')) return;
        $('#workspaceMarketPicker').hidden = true;
        $('#workspaceMarket').setAttribute('aria-expanded', 'false');
      });
      $('#areaSelect').addEventListener('change', () => { clearActiveStudy(); updateSelectionSummary(); refreshDashboard(); });
      $('#areaSelect').addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' && state.areas.length) { event.preventDefault(); showAreaSuggestions(); $('#areaSuggestions button')?.focus(); return; }
        if (event.key === 'Enter') { event.preventDefault(); hideAreaSuggestions(); clearActiveStudy(); updateSelectionSummary(); refreshDashboard(); }
        if (event.key === 'Escape') hideAreaSuggestions();
      });
      $('#areaSelect').addEventListener('input', () => { clearActiveStudy(); renderAreaSuggestions(); updateSelectionSummary(); });
      $('#areaSelect').addEventListener('focus', showAreaSuggestions);
      $('#areaSelect').addEventListener('blur', () => setTimeout(hideAreaSuggestions, 120));
      $('#businessSelect').addEventListener('change', () => { clearActiveStudy(); updateSelectionSummary(); updateWorkforce(); refreshDashboard(); });
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
    } finally {
      await finishBootScreen();
    }
  }

  async function refreshMarkets() {
    const previousCity = $('#citySelect').value;
    const previousBusiness = $('#businessSelect').value;
    const result = await api('/api/v1/markets');
    const coverage = new Map((result.market_coverage || []).map((item) => [item.city, item.signals]));
    $('#citySelect').innerHTML = result.markets.map((city) => {
      const signals = coverage.get(city) || [];
      const shortLabels = signals.map((signal) => signal === 'city-level jobs' ? 'Jobs' : signal === '2015 rail' ? 'Rail ’15' : signal === 'Mumbai-only residential rent' ? 'Rent (res.)' : signal.replace('imported local signal(s)', 'local'));
      return `<option value="${escapeHtml(city)}">${escapeHtml(city)} · ${escapeHtml(shortLabels.join(' · ') || 'local data')}</option>`;
    }).join('');
    $('#citySelect').value = result.markets.includes(previousCity) ? previousCity : result.markets.includes('Mumbai') ? 'Mumbai' : result.markets[0];
    $('#datasetCitySelect').innerHTML = result.markets.map((city) => `<option value="${escapeHtml(city)}">${escapeHtml(city)}</option>`).join('');
    $('#datasetCitySelect').value = result.markets.includes($('#citySelect').value) ? $('#citySelect').value : result.markets[0];
    $('#workspaceMarketPicker').innerHTML = result.markets.map((city) => `<button class="workspace-option" type="button" role="option" data-market="${escapeHtml(city)}" aria-selected="${city === $('#citySelect').value}">${escapeHtml(city)}<span>${city === $('#citySelect').value ? '✓' : ''}</span></button>`).join('');
    updateWorkspaceMarket();
    const datasetTypes = new Set(result.dataset_business_types || []);
    const common = result.business_types.filter((business) => !datasetTypes.has(business));
    const imported = result.business_types.filter((business) => datasetTypes.has(business));
    $('#businessSelect').innerHTML = `<optgroup label="Common business types">${common.map((business) => `<option value="${escapeHtml(business)}">${escapeHtml(business)}</option>`).join('')}</optgroup>${imported.length ? `<optgroup label="From imported competitor data">${imported.map((business) => `<option value="${escapeHtml(business)}">${escapeHtml(business)}</option>`).join('')}</optgroup>` : ''}`;
    const allBusinesses = [...common, ...imported];
    $('#businessSelect').value = allBusinesses.includes(previousBusiness) ? previousBusiness : common[0];
    updateSelectionSummary();
  }

  function updateWorkspaceMarket() {
    const city = $('#citySelect')?.value;
    $('#workspaceMarketLabel').textContent = city ? `${city} · Market workspace` : 'Market workspace';
    document.querySelectorAll('.workspace-option').forEach((option) => {
      const selected = option.dataset.market === city;
      option.setAttribute('aria-selected', String(selected));
      option.querySelector('span').textContent = selected ? '✓' : '';
    });
  }

  function updateSelectionSummary() {
    const city = $('#citySelect').value || 'Choose a market';
    const area = $('#areaSelect').value || 'Citywide';
    const business = $('#businessSelect').value || 'Choose a business';
    const areaNote = state.areas.length ? `${number(state.areas.length)} neighborhood suggestion${state.areas.length === 1 ? '' : 's'}` : 'No preset neighborhoods in the data yet; type one and add matching local evidence';
    const marketNote = city.toLowerCase() === 'mumbai' ? 'Rent here is residential asking-price context only.' : 'No rent file for this market; enter a local commercial quote.';
    $('#selectionSummary').textContent = `${business} · ${area}, ${city}. ${marketNote} ${areaNote}.`;
    $('#marketCrumb').textContent = city;
  }

  async function refreshAreas() {
    const city = $('#citySelect').value;
    const current = $('#areaSelect').value.trim();
    const requestId = (refreshAreas.requestId || 0) + 1;
    refreshAreas.requestId = requestId;
    const result = await api(`/api/v1/areas?city=${encodeURIComponent(city)}`);
    // A slower response for a previously selected city must never replace the
    // neighborhood list for the city currently visible in the selector.
    if (requestId !== refreshAreas.requestId || city !== $('#citySelect').value) return;
    state.areas = result.areas;
    renderAreaSuggestions();
    $('#areaSelect').value = current;
    hideAreaSuggestions();
    updateSelectionSummary();
  }

  function renderAreaSuggestions() {
    const query = $('#areaSelect').value.trim().toLocaleLowerCase();
    const matches = state.areas.filter((area) => !query || area.toLocaleLowerCase().includes(query)).slice(0, 10);
    const list = $('#areaSuggestions');
    list.innerHTML = matches.length
      ? matches.map((area) => `<button type="button" role="option" aria-selected="false" data-area="${escapeHtml(area)}">${escapeHtml(area)}</button>`).join('')
      : `<div class="area-no-results">${state.areas.length ? 'No matching neighborhood. Press Enter to use your own area.' : `No preset areas for ${escapeHtml($('#citySelect').value)}. Type an area and press Enter.`}</div>`;
    list.querySelectorAll('button[data-area]').forEach((button) => {
      button.addEventListener('mousedown', (event) => event.preventDefault());
      button.addEventListener('click', () => {
        $('#areaSelect').value = button.dataset.area;
        hideAreaSuggestions();
        clearActiveStudy();
        updateSelectionSummary();
        refreshDashboard();
      });
      button.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); button.nextElementSibling?.focus(); }
        if (event.key === 'ArrowUp') { event.preventDefault(); (button.previousElementSibling?.matches('button') ? button.previousElementSibling : $('#areaSelect')).focus(); }
        if (event.key === 'Escape') { hideAreaSuggestions(); $('#areaSelect').focus(); }
      });
    });
  }

  function showAreaSuggestions() {
    renderAreaSuggestions();
    $('#areaSuggestions').hidden = false;
    $('#areaSelect').setAttribute('aria-expanded', 'true');
  }

  function hideAreaSuggestions() {
    $('#areaSuggestions').hidden = true;
    $('#areaSelect').setAttribute('aria-expanded', 'false');
  }

  async function refreshDashboard() {
    const city = $('#citySelect').value;
    const area = $('#areaSelect').value;
    const businessType = $('#businessSelect').value;
    const requestId = (refreshDashboard.requestId || 0) + 1;
    refreshDashboard.requestId = requestId;
    const params = new URLSearchParams({ city, business_type: businessType });
    if (area) params.set('area', area);
    const response = await api(`/api/v1/dashboard?${params.toString()}`);
    if (requestId !== refreshDashboard.requestId || city !== $('#citySelect').value || area !== $('#areaSelect').value || businessType !== $('#businessSelect').value) return;
    state.dashboard = response;
    state.analysis = response.analysis;
    renderDashboard(response);
  }

  function renderDashboard(response) {
    const data = response.analysis;
    const city = response.selected_city;
    const area = response.selected_area;
    const hasLocalRent = city.toLowerCase() === 'mumbai';
    $('#rentalRecords').textContent = hasLocalRent ? number(data.rentals.recordCount) : '—';
    $('#rentalRecordsLabel').textContent = hasLocalRent ? 'Mumbai rental listings · 2023' : 'No rent dataset for this city';
    $('#jobRecords').textContent = number(data.jobs.recordCount);
    $('#railRecords').textContent = number(data.rail.recordCount);
    $('#generatedAt').textContent = `AGGREGATES BUILT ${data.generatedAt}`;
    $('#marketRent').textContent = city.toLowerCase() === 'mumbai' ? money(data.rentals.medianMonthlyRent) : 'Mumbai data only';
    $('#validRentCount').textContent = hasLocalRent ? `${number(data.rentals.validRentCount)} listings` : 'Mumbai only';
    $('#validAreaCount').textContent = hasLocalRent ? `${number(data.rentals.validAreaCount)} listings` : 'Mumbai only';
    $('#rentSectionTitle').textContent = hasLocalRent ? 'Where the rent lands.' : 'Local rent evidence needed.';
    $('#rentSectionDescription').textContent = hasLocalRent
      ? 'Neighborhood-level residential asking rents derived from listed properties in Mumbai.'
      : `No neighborhood-level rent dataset is loaded for ${city}. Enter a current commercial quote; Mumbai residential rents do not apply here.`;
    $('#rentSourceBadge').textContent = 'MUMBAI ONLY · 2023 SNAPSHOT';
    $('#rentPanelEyebrow').textContent = hasLocalRent ? 'MUMBAI LOCALITY COMPARISON' : 'NO LOCAL RENT DATA';
    $('#rentInsightNote').textContent = hasLocalRent
      ? 'This source contains residential listings, not commercial leases. Use it for rough neighborhood context, not a shop or cafe rent quote.'
      : `This dataset only covers Mumbai residential listings. It is not shown as a rent estimate for ${city}. Enter a local commercial quote in the planner.`;
    $('#rentWarning').hidden = !hasLocalRent;
    $('#rentCoverage').textContent = city.toLowerCase() === 'mumbai'
      ? `${number(data.rentals.localities.length)} localities · ${number(data.rentals.validRentCount)} valid rent rows`
      : 'Mumbai-only rental file · selected city has no rent observations';
    $('#plannerRentHint').textContent = city.toLowerCase() === 'mumbai'
      ? 'Select a Mumbai rent bar above to use its residential listing median as a starting reference.'
      : `No rental observations for ${city} are included. Replace the starting value with a current commercial rent quote.`;
    renderRents(data.rentals, city);
    renderWorkforce(cityData(data, city), $('#businessSelect').value);
    renderMobility(city, railData(data, city), response.transit_sources || []);
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

  function renderRents(rentals, city) {
    const chart = $('#rentChart');
    if (city.toLowerCase() !== 'mumbai') {
      chart.classList.add('rent-chart-empty');
      chart.innerHTML = `<div class="rent-empty-state"><b>No local rent listings available</b>Mumbai-only residential asking rents are hidden for ${escapeHtml(city)}. Add a current commercial lease quote to compare local feasibility.</div>`;
      $('#rentCoverage').textContent = `No rent rows for ${city}`;
      $('#marketRent').textContent = '—';
      return;
    }
    chart.classList.remove('rent-chart-empty');
    const areas = rentals.localities.slice(0, 9);
    const max = Math.max(...areas.map((item) => item.medianMonthlyRent || 0), 1);
    chart.innerHTML = areas.map((item) => {
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
    if (!cityDataItem) {
      $('#workforceTitle').textContent = `${$('#citySelect').value} · ${business} lens`;
      $('#cityJobs').textContent = 'No job dataset';
      $('#citySalary').textContent = 'Not available';
      $('#salaryDisclosure').textContent = '—';
      $('#salaryDisclosureBar').style.width = '0';
      $('#roleBars').innerHTML = '<div class="empty-card">No workforce records are available in this market yet. Import a local workforce or wage dataset to add evidence.</div>';
      $('#skillCloud').innerHTML = '<span class="skill-tag">No local skill data</span>';
      return;
    }
    $('#workforceTitle').textContent = `${cityDataItem.name} · ${business} lens`;
    $('#cityJobs').textContent = number(cityDataItem.postingCount);
    $('#citySalary').textContent = cityDataItem.medianAnnualSalary ? money(cityDataItem.medianAnnualSalary) : 'Not available';
    $('#salaryDisclosure').textContent = `${Number(cityDataItem.salaryDisclosureRate || 0).toFixed(1)}%`;
    $('#salaryDisclosureBar').style.width = `${Math.min(Number(cityDataItem.salaryDisclosureRate || 0), 100)}%`;
    const normalizedBusiness = business.toLowerCase();
    const priorities = /cafe|restaurant|bakery|dessert|food/.test(normalizedBusiness)
      ? ['Food & hospitality', 'Customer service', 'Operations & logistics', 'Sales']
      : /retail|grocery|convenience|clothing|fashion|pharmacy|electronics|furniture|home goods/.test(normalizedBusiness)
        ? ['Retail', 'Sales', 'Customer service', 'Operations & logistics']
        : /salon|wellness|fitness|health|clinic|pet care|veterinary/.test(normalizedBusiness)
          ? ['Customer service', 'Sales', 'Operations & logistics', 'Other']
          : /tutor|education|coworking|hotel|repair|service/.test(normalizedBusiness)
            ? ['Customer service', 'Operations & logistics', 'Management', 'Sales']
            : ['Customer service', 'Operations & logistics', 'Sales', 'Other'];
    const roles = cityDataItem.topRoles.slice().sort((a, b) => {
      const ai = priorities.indexOf(a.name), bi = priorities.indexOf(b.name);
      return (ai < 0 ? 50 : ai) - (bi < 0 ? 50 : bi) || b.count - a.count;
    }).slice(0, 5);
    const max = Math.max(...roles.map((item) => item.count), 1);
    $('#roleBars').innerHTML = roles.map((item) => `<div class="role-row"><span class="role-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><span class="role-track"><i style="width:${Math.max(4, item.count / max * 100)}%"></i></span><span class="role-value">${number(item.count)}</span></div>`).join('');
    $('#skillCloud').innerHTML = cityDataItem.topSkills.slice(0, 8).map((item) => `<span class="skill-tag">${escapeHtml(item.name)} <b>${number(item.count)}</b></span>`).join('') || '<span class="skill-tag">No skill tags in this market</span>';
  }

  function renderMobility(city, mobility, feeds = []) {
    $('#mobilityTitle').textContent = `${city} · rail service records`;
    $('#stationCount').textContent = number(mobility?.stationCount ?? 0);
    $('#trainCount').textContent = number(mobility?.trainCount ?? 0);
    $('#cityRailRecords').textContent = number(mobility?.scheduleRowCount ?? 0);
    $('#transitFeedCity').textContent = `${city} · current transport sources`;
    $('#transitFeedGrid').innerHTML = feeds.map((feed, index) => `<article class="transit-feed-card"><div class="transit-feed-top"><b>${escapeHtml(feed.name)}</b><span>${escapeHtml(feed.mode)}</span></div><small>${escapeHtml(feed.kind)} · ${escapeHtml(feed.freshness)}</small><p>${escapeHtml(feed.provenance)}</p><div class="transit-feed-actions"><a href="${escapeHtml(feed.url)}" target="_blank" rel="noreferrer">View source ↗</a><button type="button" data-feed-index="${index}">Use in model ↓</button></div></article>`).join('') || '<div class="empty-card">No feed links found for this market. Search the Mobility Database catalogue and check the feed dates.</div>';
    $('#transitFeedGrid').querySelectorAll('[data-feed-index]').forEach((button) => button.addEventListener('click', () => {
      const feed = feeds[Number(button.dataset.feedIndex)];
      importForm.elements.category.value = 'transit';
      importForm.elements.dataset_city.value = city;
      importForm.elements.name.value = `${city} ${feed.name}`.slice(0, 120);
      importForm.elements.source.value = feed.url;
      updateGtfsInputs();
      $('#datasetImporter').scrollIntoView({ behavior: 'smooth', block: 'start' });
      toast('Download the feed ZIP, then select it below');
    }));
  }

  function renderSignals(signals, area) {
    const entries = Object.entries(signals?.categories || {});
    if (!entries.length) {
      $('#signalGrid').innerHTML = `<div class="empty-card">No local datasets are imported for ${escapeHtml(area || $('#citySelect').value)} yet. Use the data importer below to add footfall, POI, traffic, parking, transit, workforce, wage, or local-cost data.</div>`;
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
      const city = $('#citySelect').value || 'Mumbai';
      const response = await api(`/api/v1/coverage?city=${encodeURIComponent(city)}`);
      $('#coverageGrid').innerHTML = response.items.map((item) => {
        const status = item.status.toLowerCase();
        const klass = status.includes('partial') ? 'partial' : status.includes('scenario') ? 'scenario' : status.includes('local data') ? 'imported' : '';
        const recordInfo = item.record_count ? `<small class="coverage-records">${number(item.record_count)} records${item.area_record_count ? ` · ${number(item.area_record_count)} area-level` : ' · citywide'}</small>` : '';
        return `<article class="coverage-item"><div class="coverage-top"><b>${escapeHtml(item.name)}</b><span class="coverage-source-state">SOURCE FOUND</span></div><div class="coverage-status-row"><span class="coverage-state ${klass}">${escapeHtml(item.status.toUpperCase())}</span>${recordInfo}</div><p>${escapeHtml(item.need)}</p><a class="coverage-source-link" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.source_name)} ↗</a><small class="coverage-source-note">${escapeHtml(item.source_note)}</small></article>`;
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
        await refreshMarkets();
        await Promise.all([refreshAreas(), refreshSources(), refreshCoverage(), refreshDashboard()]);
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

  const importForm = $('#importForm');
  const importButton = $('#importButton');
  let uploadScanVersion = 0;
  let uploadReady = false;

  function updateGtfsInputs() {
    const file = importForm.elements.file.files?.[0];
    const isGtfs = file?.name.toLowerCase().endsWith('.zip');
    $('#gtfsCityField').hidden = !isGtfs;
    importForm.elements.dataset_city.required = Boolean(isGtfs);
    if (isGtfs) importForm.elements.category.value = 'transit';
    if (!importForm.elements.dataset_city.value) importForm.elements.dataset_city.value = $('#citySelect').value;
  }

  async function scanUpload() {
    const version = ++uploadScanVersion;
    const file = importForm.elements.file.files?.[0];
    const preview = $('#filePreview');
    const message = $('#importMessage');
    uploadReady = false;
    importButton.disabled = true;
    if (!file) { preview.hidden = true; message.textContent = ''; importButton.textContent = 'Choose a file to scan ↥'; return; }
    preview.hidden = false;
    preview.textContent = `Scanning ${file.name} · ${Math.max(1, Math.ceil(file.size / 1024))} KB…`;
    message.textContent = 'The server is checking file structure, required fields, values, dates, coordinates, duplicates, and row limits.';
    if (file.size > 50 * 1024 * 1024) {
      preview.innerHTML = '<b>File is too large.</b> Choose a supported file under 50 MB.';
      message.textContent = '';
      importButton.textContent = 'File too large';
      return;
    }
    try {
      const scanForm = new FormData();
      scanForm.set('category', importForm.elements.category.value);
      scanForm.set('dataset_city', importForm.elements.dataset_city.value);
      scanForm.set('skip_duplicates', importForm.elements.skip_duplicates.checked);
      scanForm.set('normalize_labels', importForm.elements.normalize_labels.checked);
      scanForm.set('file', file);
      const result = await api('/api/v1/datasets/preview', { method: 'POST', body: scanForm });
      if (version !== uploadScanVersion) return;
      uploadReady = result.clean_rows > 0;
      importButton.disabled = !uploadReady;
      const plural = result.clean_rows === 1 ? '' : 's';
      importButton.innerHTML = uploadReady ? `Import ${number(result.clean_rows)} clean row${plural} <span>↥</span>` : 'Fix scan issues to import <span>↥</span>';
      const sample = result.sample_rows || [];
      const headers = sample.length ? Object.keys(sample[0]) : [];
      const table = sample.length ? `<table><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${sample.map((row) => `<tr>${headers.map((header) => `<td>${escapeHtml(row[header])}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
      const issues = (result.errors || []).map((issue) => `<div class="preview-warn">${escapeHtml(issue)}</div>`).join('');
      const extraErrors = result.skipped_invalid_rows > (result.errors || []).length ? `<div class="preview-warn">Showing first ${number(result.errors.length)} of ${number(result.skipped_invalid_rows)} invalid rows.</div>` : '';
      const quality = [
        result.duplicates_removed ? `${number(result.duplicates_removed)} exact duplicates removed` : '',
        result.geocoded_rows ? `${number(result.geocoded_rows)} geocoded` : '',
        result.timestamped_rows ? `${number(result.timestamped_rows)} timestamped` : '',
      ].filter(Boolean).join(' · ');
      const range = result.minimum_value == null ? 'No usable numeric values' : `Values ${number(result.minimum_value, 1)}–${number(result.maximum_value, 1)} · median ${number(result.median_value, 1)}`;
      preview.innerHTML = `<b>${escapeHtml(result.filename)}</b> · ${escapeHtml(result.format)} · ${number(result.scanned_rows)} scanned · ${number(result.clean_rows)} clean · ${number(result.skipped_invalid_rows)} invalid · ${number(result.duplicates_removed)} duplicate<br>${escapeHtml(range)}${quality ? `<br>${escapeHtml(quality)}` : ''}${issues}${extraErrors}${table}`;
      message.textContent = uploadReady ? 'Review the sample and scan summary, then import. The backend validates every row again before saving.' : 'No valid rows to import. Correct the file and scan it again.';
    } catch (error) {
      if (version !== uploadScanVersion) return;
      preview.innerHTML = `<b>Could not scan file.</b> ${escapeHtml(error.message)}`;
      importButton.textContent = 'Choose a readable data file';
      message.textContent = '';
    }
  }

  importForm.elements.file.addEventListener('change', () => { updateGtfsInputs(); scanUpload(); });
  importForm.elements.category.addEventListener('change', () => { updateGtfsInputs(); if (importForm.elements.file.files?.[0]) scanUpload(); });
  importForm.elements.skip_duplicates.addEventListener('change', () => { if (importForm.elements.file.files?.[0]) scanUpload(); });
  importForm.elements.normalize_labels.addEventListener('change', () => { if (importForm.elements.file.files?.[0]) scanUpload(); });

  importForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!uploadReady) { toast('Scan a valid data file before importing'); return; }
    const formElement = event.currentTarget;
    const message = $('#importMessage');
    importButton.disabled = true;
    importButton.innerHTML = 'Validating and importing…';
    message.textContent = 'The backend is validating the file again before adding normalized records to your local database.';
    try {
      const form = new FormData(formElement);
      const result = await api('/api/v1/datasets/import', { method: 'POST', body: form });
      message.textContent = `${result.name}: ${number(result.row_count)} imported from ${number(result.scanned_rows)} scanned; ${number(result.skipped_invalid_rows)} invalid and ${number(result.duplicates_removed)} duplicate rows removed. Range ${number(result.minimum_value, 1)}–${number(result.maximum_value, 1)}, median ${number(result.median_value, 1)}. ${result.warnings.join(' ')}`;
      formElement.reset();
      importForm.elements.dataset_city.value = $('#citySelect').value;
      updateGtfsInputs();
      $('#filePreview').hidden = true;
      uploadReady = false;
      importButton.disabled = true;
      importButton.textContent = 'Choose a file to scan ↥';
      await refreshMarkets();
      await Promise.all([refreshAreas(), refreshDashboard(), refreshSources(), refreshCoverage()]);
      toast('Dataset imported and analyzed');
    } catch (error) { message.textContent = error.message; }
    finally { if (uploadReady) importButton.disabled = false; }
  });

  $('#salaryEstimateForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const resultBox = $('#salaryEstimateResult');
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    resultBox.textContent = 'Estimating from the trained salary benchmark…';
    try {
      const result = await api('/api/v1/workforce/salary-estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          city: $('#citySelect').value,
          job_title: form.elements.job_title.value,
          skills: form.elements.skills.value,
          experience_years: Number(form.elements.experience_years.value),
        }),
      });
      const [low, high] = result.monthly_range_inr;
      resultBox.innerHTML = `<div class="salary-result-values"><span>Estimated monthly pay</span><b>${money(result.monthly_salary_inr)}</b></div><div class="salary-result-values"><span>Empirical monthly range</span><b>${money(low)}–${money(high)}</b></div><small>${escapeHtml(result.role_group)} · ${escapeHtml(result.note)}</small><button class="text-button" id="useSalaryEstimate" type="button">Use estimate in monthly staff pay ↗</button>`;
      $('#useSalaryEstimate').addEventListener('click', () => {
        $('#inputPay').value = result.monthly_salary_inr;
        updateScenario();
        $('#planner').scrollIntoView({ behavior: 'smooth' });
        toast('Estimated monthly staff pay added to your scenario');
      });
    } catch (error) {
      resultBox.textContent = `Salary estimate unavailable: ${error.message}`;
    } finally { submit.disabled = false; }
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
