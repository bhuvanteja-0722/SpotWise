(() => {
  const $ = (selector) => document.querySelector(selector);
  const money = (value, digits = 1) => {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    if (n >= 10_000_000) return `₹${(n / 10_000_000).toFixed(digits)}Cr`;
    if (n >= 100_000) return `₹${(n / 100_000).toFixed(digits)}L`;
    return `₹${Math.round(n).toLocaleString('en-IN')}`;
  };
  const integer = (value) => Number(value || 0).toLocaleString('en-IN');
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

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
  document.querySelectorAll('[data-scroll]').forEach((link) => link.addEventListener('click', () => {
    document.querySelector(link.dataset.scroll)?.scrollIntoView({ behavior: 'smooth' });
  }));

  const toast = (message) => {
    const box = $('#toast');
    box.textContent = message;
    box.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => box.classList.remove('show'), 2500);
  };

  fetch('./data/analysis.json')
    .then((response) => {
      if (!response.ok) throw new Error('Could not load data/analysis.json');
      return response.json();
    })
    .then((data) => initialise(data))
    .catch(() => {
      $('#rentalRecords').textContent = 'Data missing';
      $('#jobRecords').textContent = 'Run pipeline';
      $('#railRecords').textContent = 'See README';
      $('#rentChart').innerHTML = '<div class="error-state">Run the data pipeline described in README.md to build the analysis file.</div>';
      $('#roleBars').innerHTML = '<div class="error-state">No analysis file loaded.</div>';
    });

  function initialise(data) {
    const citySelect = $('#citySelect');
    const jobsByCity = new Map(data.jobs.cities.map((item) => [item.name, item]));
    const railByCity = new Map(data.rail.cities.map((item) => [item.name, item]));
    const available = [...jobsByCity.keys()];
    citySelect.innerHTML = available.map((city) => `<option value="${escapeHtml(city)}">${escapeHtml(city)}</option>`).join('');
    citySelect.value = jobsByCity.has('Mumbai') ? 'Mumbai' : available[0];

    $('#rentalRecords').textContent = integer(data.rentals.recordCount);
    $('#jobRecords').textContent = integer(data.jobs.recordCount);
    $('#railRecords').textContent = integer(data.rail.recordCount);
    $('#marketRent').textContent = money(data.rentals.medianMonthlyRent);
    $('#validRentCount').textContent = `${integer(data.rentals.validRentCount)} listings`;
    $('#validAreaCount').textContent = `${integer(data.rentals.validAreaCount)} listings`;
    $('#rentCoverage').textContent = `${integer(data.rentals.localities.length)} localities · ${integer(data.rentals.validRentCount)} valid rent rows`;
    $('#generatedAt').textContent = `AGGREGATES BUILT ${data.generatedAt}`;

    renderRents(data.rentals);
    const coverage = data.coverageGaps || [];
    $('#coverageGrid').innerHTML = coverage.map((item) => {
      const state = item.status === 'Partial' ? 'partial' : item.status === 'Scenario only' ? 'scenario' : '';
      return `<article class="coverage-item"><div class="coverage-top"><b>${escapeHtml(item.name)}</b><span class="coverage-state ${state}">${escapeHtml(item.status.toUpperCase())}</span></div><p>${escapeHtml(item.need)}</p></article>`;
    }).join('');

    $('#sourceList').innerHTML = data.sources.map((source) => {
      const detail = source.key === 'rentals'
        ? 'Mumbai rental listings; rent and area fields cleaned, then summarized by inferred locality.'
        : source.key === 'jobs'
          ? 'India job postings; location matching, role grouping, INR salary summaries, and skill-tag counts.'
          : 'Indian train timetable; unique train IDs and station names matched to city aliases.';
      return `<article class="source-card"><div class="source-card-head"><h4>${escapeHtml(source.name)}</h4><span class="source-year">${escapeHtml(source.lastUpdated)}</span></div><p>${detail}</p><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">Open dataset ↗</a><span class="source-license">${escapeHtml(source.license)} · ${escapeHtml(source.records)} source rows</span></article>`;
    }).join('');

    const areaRent = new Map();
    document.querySelectorAll('.rent-column').forEach((column) => {
      column.addEventListener('click', () => {
        const item = data.rentals.localities.find((area) => area.name === column.dataset.area);
        if (!item) return;
        areaRent.set('selected', item);
        $('#inputRent').value = Math.round(item.medianMonthlyRent);
        updateScenario();
        toast(`${item.name} median rent added to your scenario`);
      });
      column.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); column.click(); }
      });
    });

    const updateMarket = () => {
      const city = citySelect.value;
      const job = jobsByCity.get(city);
      const rail = railByCity.get(city);
      renderWorkforce(job, $('#businessSelect').value);
      renderMobility(city, rail);
      if (city !== 'Mumbai') {
        $('#rentCoverage').textContent = 'Mumbai-only rental listings · enter this city’s rent manually';
        $('#marketRent').textContent = 'Mumbai data only';
        $('#plannerRentHint').textContent = `No rental observations for ${city} are included. Replace the starting value with a current commercial rent quote.`;
      } else {
        $('#marketRent').textContent = money(data.rentals.medianMonthlyRent);
        $('#rentCoverage').textContent = `${integer(data.rentals.localities.length)} localities · ${integer(data.rentals.validRentCount)} valid rent rows`;
        $('#plannerRentHint').textContent = 'Select a Mumbai rent bar above to use its residential listing median as a starting reference.';
      }
    };
    citySelect.addEventListener('change', updateMarket);
    $('#businessSelect').addEventListener('change', () => renderWorkforce(jobsByCity.get(citySelect.value), $('#businessSelect').value));
    updateMarket();
    initialiseScenario(data);
    updateScenario();
  }

  function renderRents(rentals) {
    const areas = rentals.localities.slice(0, 9);
    const max = Math.max(...areas.map((item) => item.medianMonthlyRent || 0), 1);
    $('#rentChart').innerHTML = areas.map((item) => {
      const height = Math.max(7, Math.round((item.medianMonthlyRent / max) * 100));
      return `<div class="rent-column" role="button" tabindex="0" aria-label="Use ${escapeHtml(item.name)} median rent ${money(item.medianMonthlyRent)} in scenario" data-area="${escapeHtml(item.name)}"><span class="rent-amount">${money(item.medianMonthlyRent)}</span><div class="rent-bar" style="height:${height}%"></div><span class="rent-locality">${escapeHtml(item.name)}</span></div>`;
    }).join('');
  }

  function renderWorkforce(cityData, business) {
    if (!cityData) return;
    $('#workforceTitle').textContent = `${cityData.name} hiring snapshot`;
    $('#cityJobs').textContent = integer(cityData.postingCount);
    $('#citySalary').textContent = cityData.medianAnnualSalary ? money(cityData.medianAnnualSalary) : 'Not available';
    const disclosure = Number(cityData.salaryDisclosureRate || 0);
    $('#salaryDisclosure').textContent = `${disclosure.toFixed(1)}%`;
    $('#salaryDisclosureBar').style.width = `${Math.min(disclosure, 100)}%`;

    const priorities = {
      'Cafe / restaurant': ['Food & hospitality', 'Customer service', 'Operations & logistics', 'Sales'],
      'Retail shop': ['Retail', 'Sales', 'Customer service', 'Operations & logistics'],
      'Salon / wellness': ['Customer service', 'Sales', 'Operations & logistics', 'Other'],
      'Fitness studio': ['Customer service', 'Sales', 'Operations & logistics', 'Other'],
      'Other local service': ['Customer service', 'Operations & logistics', 'Sales', 'Other'],
    }[business] || [];
    const roles = cityData.topRoles.slice().sort((a, b) => {
      const ai = priorities.indexOf(a.name), bi = priorities.indexOf(b.name);
      return (ai < 0 ? 50 : ai) - (bi < 0 ? 50 : bi) || b.count - a.count;
    }).slice(0, 5);
    const max = Math.max(...roles.map((item) => item.count), 1);
    $('#roleBars').innerHTML = roles.map((item) => `<div class="role-row"><span class="role-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><span class="role-track"><i style="width:${Math.max(4, item.count / max * 100)}%"></i></span><span class="role-value">${integer(item.count)}</span></div>`).join('');
    $('#skillCloud').innerHTML = cityData.topSkills.slice(0, 8).map((item) => `<span class="skill-tag">${escapeHtml(item.name)} <b>${integer(item.count)}</b></span>`).join('') || '<span class="skill-tag">No skill tags in this market</span>';
  }

  function renderMobility(city, railData) {
    $('#mobilityTitle').textContent = `${city} · rail service records`;
    $('#stationCount').textContent = integer(railData?.stationCount ?? 0);
    $('#trainCount').textContent = integer(railData?.trainCount ?? 0);
    $('#cityRailRecords').textContent = integer(railData?.scheduleRowCount ?? 0);
  }

  function initialiseScenario(data) {
    const defaults = { rent: 85000, staff: 4, pay: 22000, other: 60000, ticket: 350, margin: 62, customers: 110 };
    const inputs = {
      rent: $('#inputRent'), staff: $('#inputStaff'), pay: $('#inputPay'), other: $('#inputOther'),
      ticket: $('#inputTicket'), margin: $('#inputMargin'), customers: $('#inputCustomers'),
    };
    const mumbai = data.rentals.localities.find((item) => item.name === 'Andheri West') || data.rentals.localities[0];
    if (mumbai) inputs.rent.value = Math.round(mumbai.medianMonthlyRent);
    try {
      const stored = JSON.parse(localStorage.getItem('spotwise-scenario') || 'null');
      if (stored) Object.entries(inputs).forEach(([key, node]) => { if (stored[key] != null) node.value = stored[key]; });
    } catch (_) { /* Ignore stale browser storage. */ }
    Object.values(inputs).forEach((input) => input.addEventListener('input', updateScenario));
    $('#resetPlanner').addEventListener('click', () => {
      Object.entries(inputs).forEach(([key, node]) => {
        node.value = key === 'rent' && mumbai
          ? ($('#citySelect').value === 'Mumbai' ? Math.round(mumbai.medianMonthlyRent) : 0)
          : defaults[key];
      });
      updateScenario();
      toast('Scenario assumptions reset');
    });
    window.spotwiseScenarioInputs = inputs;
  }

  function updateScenario() {
    const input = window.spotwiseScenarioInputs;
    if (!input) return;
    const rent = Math.max(0, Number(input.rent.value) || 0);
    const staff = Math.max(0, Number(input.staff.value) || 0);
    const pay = Math.max(0, Number(input.pay.value) || 0);
    const other = Math.max(0, Number(input.other.value) || 0);
    const ticket = Math.max(0, Number(input.ticket.value) || 0);
    const margin = Math.min(100, Math.max(1, Number(input.margin.value) || 1)) / 100;
    const customers = Math.max(0, Number(input.customers.value) || 0);
    const fixed = rent + staff * pay + other;
    const target = fixed / margin;
    const breakEvenCustomers = ticket > 0 ? fixed / (ticket * margin * 30) : 0;
    const sales = ticket * customers * 30;
    const surplus = sales * margin - fixed;
    $('#netResult').textContent = `${surplus >= 0 ? '+' : '−'}${money(Math.abs(surplus))}`;
    $('#netResult').style.color = surplus >= 0 ? 'var(--lime)' : 'var(--amber)';
    $('#breakevenSales').textContent = money(target);
    $('#breakevenCustomers').textContent = `${breakEvenCustomers.toFixed(1)} / day`;
    $('#estimatedSales').textContent = money(sales);
    const verdict = $('#scenarioVerdict');
    verdict.className = `result-verdict ${surplus >= 0 ? 'positive' : 'negative'}`;
    verdict.textContent = surplus >= 0
      ? `At these assumptions, gross profit covers fixed monthly costs by ${money(surplus)}.`
      : `At these assumptions, sales fall short of fixed costs by ${money(Math.abs(surplus))}.`;
    const values = Object.fromEntries(Object.entries(input).map(([key, node]) => [key, Number(node.value) || 0]));
    localStorage.setItem('spotwise-scenario', JSON.stringify(values));
  }

  $('#exportButton').addEventListener('click', () => {
    const city = $('#citySelect').value;
    const inputs = window.spotwiseScenarioInputs;
    const snapshot = {
      project: 'SpotWise location research',
      market: city,
      businessType: $('#businessSelect').value,
      scenario: inputs ? Object.fromEntries(Object.entries(inputs).map(([key, node]) => [key, Number(node.value) || 0])) : {},
      notice: 'Descriptive research snapshot and user-entered scenario only. The dashboard does not measure footfall, local competition, parking, or profit outcomes.',
      dataFile: 'data/analysis.json',
      generatedAt: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `spotwise-${city.toLowerCase().replace(/\s+/g, '-')}-snapshot.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    toast('Research snapshot downloaded');
  });
})();
