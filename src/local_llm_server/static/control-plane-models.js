(() => {
    const REFRESH_MS = 10000;
    let timer = null;
    let refreshing = false;
    let selectedKey = null;
    let latestState = null;
    let capacityPreview = null;
    let capacityRequestVersion = 0;

    async function fetchJson(path, options = {}) {
        const response = await fetch(path, {
            headers: { Accept: 'application/json', ...(options.headers || {}) },
            ...options,
        });
        let payload = null;
        try { payload = await response.json(); } catch (_) { payload = null; }
        if (!response.ok) {
            const detail = payload?.detail?.message || payload?.detail || `${path} returned ${response.status}`;
            const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function ensureSurface() {
        const panel = document.getElementById('registry-tab');
        if (!panel) return null;
        let surface = panel.querySelector('[data-control-plane-models]');
        if (surface) return surface;
        surface = document.createElement('section');
        surface.dataset.controlPlaneModels = 'true';
        surface.className = 'control-plane-models';
        panel.prepend(surface);
        return surface;
    }

    async function refresh() {
        if (refreshing) return;
        refreshing = true;
        const surface = ensureSurface();
        if (!surface) {
            refreshing = false;
            return;
        }

        try {
            const [residentResult, statusResult, catalogResult, resourceResult, evidenceResult, residencyResult] = await Promise.allSettled([
                fetchJson('/v1/models'),
                fetchJson('/status'),
                fetchJson('/api/v1/models/registry'),
                fetchJson('/api/v1/resources'),
                fetchJson('/api/v1/evidence'),
                fetchJson('/api/v1/residency'),
            ]);

            const residentPayload = residentResult.status === 'fulfilled' ? residentResult.value : null;
            const statusPayload = statusResult.status === 'fulfilled' ? statusResult.value : null;
            const catalogPayload = catalogResult.status === 'fulfilled' ? catalogResult.value : null;
            const resourcePayload = resourceResult.status === 'fulfilled' ? resourceResult.value : null;
            const evidencePayload = evidenceResult.status === 'fulfilled' ? evidenceResult.value : null;
            const residencyPayload = residencyResult.status === 'fulfilled' ? residencyResult.value : null;

            latestState = buildState({
                residentPayload,
                statusPayload,
                catalogPayload,
                resourcePayload,
                evidencePayload,
                residencyPayload,
                catalogAvailable: catalogResult.status === 'fulfilled',
                resourceAvailable: resourceResult.status === 'fulfilled',
                evidenceAvailable: evidenceResult.status === 'fulfilled',
                residencyAvailable: residencyResult.status === 'fulfilled',
            });
            render(surface, latestState);
        } finally {
            refreshing = false;
        }
    }

    function buildState(input) {
        const residents = Array.isArray(input.residentPayload?.data) ? input.residentPayload.data : [];
        const catalog = Array.isArray(input.catalogPayload?.models) ? input.catalogPayload.models : null;
        const runtimeStatus = input.statusPayload?.models && typeof input.statusPayload.models === 'object'
            ? input.statusPayload.models
            : {};
        const defaultModel = input.statusPayload?.default_model ?? null;

        const residentByKey = new Map();
        residents.forEach((model) => {
            [model?.key, model?.id, model?.model_id].filter(Boolean).forEach((value) => residentByKey.set(String(value), model));
        });

        const residencyByKey = new Map();
        if (Array.isArray(input.residencyPayload?.runtimes)) {
            input.residencyPayload.runtimes.forEach((runtime) => {
                [runtime?.key, runtime?.model].filter(Boolean).forEach((value) => residencyByKey.set(String(value), runtime));
            });
        }

        const evidenceByKey = new Map();
        if (Array.isArray(input.evidencePayload?.runtimes)) {
            input.evidencePayload.runtimes.forEach((entry) => {
                const runtime = entry?.runtime || {};
                [runtime?.key, runtime?.model_id].filter(Boolean).forEach((value) => evidenceByKey.set(String(value), entry));
            });
        }

        const sourceRows = catalog || residents;
        const models = sourceRows.map((model) => {
            const key = String(model?.key ?? model?.id ?? model?.model_id ?? 'unknown');
            const modelId = String(model?.model_id ?? model?.id ?? key);
            const residentRecord = residentByKey.get(key) || residentByKey.get(modelId) || null;
            const resident = Boolean(model?.resident) || Boolean(residentRecord);
            const status = model?.runtime_status || runtimeStatus[key] || runtimeStatus[residentRecord?.key] || null;
            const residency = residencyByKey.get(key) || residencyByKey.get(modelId) || null;
            const evidence = evidenceByKey.get(key) || evidenceByKey.get(modelId) || null;
            const admission = evidence?.runtime?.resource_admission || status?.resource_admission || null;
            const estimateBytes = finiteNumber(admission?.estimate_bytes);
            const activeRequests = finiteNumber(status?.active_requests ?? evidence?.runtime?.active_requests);
            const runtimeState = String(status?.state ?? evidence?.runtime?.state ?? (resident ? 'resident' : 'cold'));
            const isDefault = Boolean(model?.default) || key === defaultModel || modelId === defaultModel || residentRecord?.default === true;
            return {
                key,
                modelId,
                backend: model?.backend ?? residentRecord?.backend ?? status?.backend ?? evidence?.runtime?.backend ?? null,
                capabilities: model?.capabilities ?? residentRecord?.capabilities ?? null,
                artifactState: model?.downloaded === true ? 'available' : (model?.downloaded === false ? 'missing' : 'unavailable'),
                resident,
                runtimeState,
                isDefault,
                estimateBytes,
                activeRequests,
                residency,
                evidence,
            };
        });

        return {
            ...input,
            models,
            defaultModel,
            resource: input.resourcePayload,
        };
    }

    function render(surface, state) {
        if (selectedKey && !state.models.some((model) => model.key === selectedKey)) {
            selectedKey = null;
            capacityPreview = null;
            capacityRequestVersion += 1;
        }
        const residentCount = state.models.filter((model) => model.resident).length;
        const coldCount = state.models.filter((model) => !model.resident).length;
        const resource = state.resource || {};

        surface.innerHTML = `
            <div class="control-plane-header">
                <div>
                    <span class="control-plane-models__eyebrow">Runtime lifecycle</span>
                    <h2>Models & Runtimes</h2>
                    <p>Decide what can run, what is resident and whether the next lifecycle action fits the current AI resource budget.</p>
                </div>
                ${statusBadge(state.catalogAvailable ? 'Catalog connected' : 'Resident view only', state.catalogAvailable ? 'ready' : 'unavailable')}
            </div>

            <div class="control-plane-models__summary" aria-label="Model and runtime summary">
                ${summaryMetric('Resident', String(residentCount), formatBytes(resource.committed_bytes), 'resident')}
                ${summaryMetric('Cold', String(coldCount), 'Available on demand', 'cold')}
                ${summaryMetric('Default route', state.defaultModel || 'Unavailable', 'Selection is separate from routing', state.defaultModel ? 'ready' : 'unavailable')}
                ${summaryMetric('Available headroom', formatBytes(resource.remaining_bytes), resource.enabled === true ? 'Configured resource policy' : 'Policy unavailable or disabled', resource.enabled === true ? 'ready' : 'unavailable')}
            </div>

            ${resourceBudgetCard(resource, state.resourceAvailable)}

            ${state.catalogAvailable ? '' : '<div class="ds-empty control-plane-models__notice">Configured catalog unavailable. Only resident runtimes are shown; lifecycle actions that require catalog identity may be unavailable.</div>'}

            <div class="control-plane-models__workspace">
                <section class="ds-card control-plane-models__inventory" aria-labelledby="model-inventory-heading">
                    <div class="control-plane-models__section-header">
                        <div>
                            <span class="control-plane-models__eyebrow">Canonical lifecycle surface</span>
                            <h3 id="model-inventory-heading">Model inventory</h3>
                        </div>
                        <small>Artifact, runtime, route and policy are separate states.</small>
                    </div>
                    <div class="ds-table-wrap">
                        <table class="ds-table control-plane-models__table">
                            <thead>
                                <tr>
                                    <th>Model</th>
                                    <th>Task / I/O</th>
                                    <th>Artifact</th>
                                    <th>Runtime</th>
                                    <th>Route</th>
                                    <th>Memory evidence</th>
                                    <th>Activity</th>
                                    <th>Policy</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${state.models.length ? state.models.map(modelRow).join('') : '<tr><td colspan="9">Unavailable — no model source returned data.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </section>

                <aside class="ds-card control-plane-models__detail" data-model-detail aria-live="polite">
                    ${detailMarkup(state)}
                </aside>
            </div>

            <div class="ds-empty control-plane-models__action-status" data-model-action-status aria-live="polite">
                ${state.residencyPayload ? 'Pinning changes automatic-eviction eligibility only. Lifecycle changes are always explicit.' : 'Residency policy source unavailable; pin/unpin controls are disabled.'}
            </div>

            <div data-load-feasibility-host></div>`;

        bindModelActions(surface, state);
        displayCapacityPreview(surface);
    }

    function summaryMetric(label, value, note, status) {
        return `
            <article class="ds-card control-plane-models__metric">
                <span class="ds-status" data-status="${escapeHtml(status)}">${escapeHtml(label)}</span>
                <strong>${escapeHtml(value)}</strong>
                <small>${escapeHtml(note)}</small>
            </article>`;
    }

    function resourceBudgetCard(payload, sourceAvailable) {
        if (!sourceAvailable || !payload) {
            return `
                <article class="ds-card control-plane-models__budget">
                    <div class="control-plane-models__section-header"><h3>Memory & Residency</h3></div>
                    <div class="ds-empty">Unavailable — resource policy source did not respond.</div>
                </article>`;
        }
        const enabled = payload.enabled === true;
        const budget = finiteNumber(payload.usable_budget_bytes);
        const committed = finiteNumber(payload.committed_bytes);
        const reserved = finiteNumber(payload.reserved_bytes);
        const remaining = finiteNumber(payload.remaining_bytes);
        const committedPct = percentage(committed, budget);
        const reservedPct = percentage(reserved, budget);
        const remainingPct = percentage(remaining, budget);
        return `
            <article class="ds-card control-plane-models__budget">
                <div class="control-plane-models__section-header">
                    <div>
                        <span class="control-plane-models__eyebrow">Configured accounting envelope</span>
                        <h3>Memory & Residency</h3>
                    </div>
                    ${statusBadge(enabled ? 'Resource admission enabled' : 'Resource admission disabled', enabled ? 'ready' : 'unavailable')}
                </div>
                <div class="control-plane-models__budget-bar" aria-label="AI resource budget breakdown">
                    ${budgetSegment('Committed', committed, committedPct, 'committed')}
                    ${budgetSegment('Reserved', reserved, reservedPct, 'reserved')}
                    ${budgetSegment('Remaining', remaining, remainingPct, 'remaining')}
                </div>
                <div class="control-plane-models__budget-legend">
                    <span><strong>${escapeHtml(formatBytes(budget))}</strong> usable budget <em>configured</em></span>
                    <span><strong>${escapeHtml(formatBytes(committed))}</strong> committed <em>accounted</em></span>
                    <span><strong>${escapeHtml(formatBytes(reserved))}</strong> reserved <em>accounted</em></span>
                    <span><strong>${escapeHtml(formatBytes(remaining))}</strong> remaining <em>derived</em></span>
                </div>
                <p class="control-plane-models__evidence-note">These values are resource-policy accounting. Per-runtime physical memory is not presented as observed unless a runtime evidence source actually measures it.</p>
            </article>`;
    }

    function budgetSegment(label, value, pct, kind) {
        if (value === null || pct === null || pct <= 0) return '';
        return `<span class="control-plane-models__budget-segment control-plane-models__budget-segment--${kind}" style="flex-basis:${Math.max(pct, 3)}%" title="${escapeHtml(label)} ${escapeHtml(formatBytes(value))}">${escapeHtml(label)}</span>`;
    }

    function modelRow(model) {
        const policy = model.residency;
        const pinned = policy?.pinned === true;
        const evictable = policy?.evictable === true;
        const age = finiteNumber(policy?.last_used_age_seconds);
        const activity = model.activeRequests === null
            ? (age === null ? 'Unavailable' : `idle ${formatDuration(age)}`)
            : `${model.activeRequests} active${age === null ? '' : ` · idle age ${formatDuration(age)}`}`;
        const runtimeLabel = model.resident ? normalizeRuntimeState(model.runtimeState) : 'Cold';
        const runtimeStatus = model.resident ? (runtimeLabel === 'Failed' ? 'error' : 'resident') : 'cold';
        const policyLabel = !model.resident
            ? '—'
            : (!latestState?.residencyAvailable || !policy
                ? 'Unavailable'
                : (pinned ? 'Pinned' : (evictable ? 'Evictable' : 'Protected now')));
        const policyStatus = pinned ? 'warning' : (evictable ? 'ready' : 'unavailable');

        return `
            <tr class="${selectedKey === model.key ? 'control-plane-models__row--selected' : ''}">
                <td>
                    <button type="button" class="control-plane-models__identity-button" data-open-model="${escapeHtml(model.key)}">
                        <strong>${escapeHtml(model.modelId)}</strong><code>${escapeHtml(model.key)}</code>
                    </button>
                </td>
                <td>${escapeHtml(capabilitySummary(model.capabilities))}</td>
                <td>${artifactBadge(model.artifactState)}</td>
                <td>${statusBadge(runtimeLabel, runtimeStatus)}</td>
                <td>${model.isDefault ? statusBadge('Default', 'ready') : '<span>Non-default</span>'}</td>
                <td>${model.estimateBytes === null ? '<span>Unavailable</span>' : `<strong>${escapeHtml(formatBytes(model.estimateBytes))}</strong><br><small>Estimate</small>`}</td>
                <td>${escapeHtml(activity)}</td>
                <td>${model.resident ? `${statusBadge(policyLabel, policyStatus)}${age === null ? '' : `<br><small>${escapeHtml(formatDuration(age))} since use</small>`}` : '—'}</td>
                <td><div class="control-plane-models__row-actions">${rowActions(model)}</div></td>
            </tr>`;
    }

    function rowActions(model) {
        if (!model.resident) {
            const disabled = model.artifactState === 'missing' ? ' disabled title="Artifact is not available locally"' : '';
            return `<button type="button" class="ds-button" data-variant="primary" data-load-model="${escapeHtml(model.key)}"${disabled}>Load</button>
                    <button type="button" class="ds-button" data-open-model="${escapeHtml(model.key)}">Details</button>`;
        }
        const buttons = [];
        if (!model.isDefault) {
            buttons.push(`<button type="button" class="ds-button" data-set-default-model="${escapeHtml(model.key)}">Set default</button>`);
        }
        buttons.push(`<button type="button" class="ds-button" data-open-model="${escapeHtml(model.key)}">Details</button>`);
        return buttons.join('');
    }

    function artifactBadge(state) {
        if (state === 'available') return statusBadge('Available', 'ready');
        if (state === 'missing') return statusBadge('Missing', 'warning');
        return statusBadge('Unavailable', 'unavailable');
    }

    function detailMarkup(state) {
        const model = selectedKey ? state.models.find((item) => item.key === selectedKey) : null;
        if (!model) {
            return `
                <div class="control-plane-models__detail-empty">
                    <span class="control-plane-models__eyebrow">Model detail</span>
                    <h3>Select a model</h3>
                    <p>Open a model to inspect lifecycle state, resource evidence, capabilities and runtime identity without leaving the inventory.</p>
                </div>`;
        }
        const identity = model.evidence?.identity || null;
        const admission = model.evidence?.runtime?.resource_admission || null;
        const age = finiteNumber(model.residency?.last_used_age_seconds);
        const pinned = model.residency?.pinned === true;
        return `
            <div class="control-plane-models__detail-header">
                <div>
                    <span class="control-plane-models__eyebrow">Model detail</span>
                    <h3>${escapeHtml(model.modelId)}</h3>
                    <code>${escapeHtml(model.key)}</code>
                </div>
                ${statusBadge(model.resident ? 'Resident' : 'Cold', model.resident ? 'resident' : 'cold')}
            </div>
            <section>
                <h4>Runtime</h4>
                <dl class="control-plane-models__definition-list">
                    <div><dt>State</dt><dd>${escapeHtml(model.resident ? normalizeRuntimeState(model.runtimeState) : 'Cold')}</dd></div>
                    <div><dt>Route</dt><dd>${model.isDefault ? 'Default' : 'Non-default'}</dd></div>
                    <div><dt>Backend</dt><dd>${escapeHtml(model.backend || 'Unavailable')}</dd></div>
                    <div><dt>Activity</dt><dd>${model.activeRequests === null ? 'Unavailable' : `${model.activeRequests} active`}</dd></div>
                    <div><dt>Last used</dt><dd>${age === null ? 'Unavailable' : `${formatDuration(age)} ago`}</dd></div>
                </dl>
            </section>
            <section>
                <h4>Resources</h4>
                <dl class="control-plane-models__definition-list">
                    <div><dt>Load requirement</dt><dd>${model.estimateBytes === null ? 'Unavailable' : `${formatBytes(model.estimateBytes)} · estimated`}</dd></div>
                    <div><dt>Admission decision</dt><dd>${escapeHtml(admission?.decision || 'Unavailable')}</dd></div>
                    <div><dt>Evidence kind</dt><dd>${model.estimateBytes === null ? 'Unavailable' : 'Estimated'}</dd></div>
                </dl>
            </section>
            <section data-capacity-check>
                <h4>Capacity check</h4>
                <p class="control-plane-models__evidence-note">Preview one warm runtime without loading it. Snapshot estimates are not permission to start.</p>
                <button type="button" class="ds-button" data-check-capacity>Check capacity</button>
                <div class="control-plane-models__capacity-result" data-capacity-result role="status" aria-live="polite"></div>
            </section>
            <section>
                <h4>Capabilities</h4>
                <p>${escapeHtml(capabilitySummary(model.capabilities))}</p>
            </section>
            <details>
                <summary>Identity & diagnostics</summary>
                <dl class="control-plane-models__definition-list">
                    <div><dt>Runtime fingerprint</dt><dd>${identity?.fingerprint ? `<code title="${escapeHtml(identity.fingerprint)}">${escapeHtml(shortFingerprint(identity.fingerprint))}</code>` : 'Exploratory'}</dd></div>
                    <div><dt>Identity evidence</dt><dd>${identity?.fingerprint ? 'Evidence available' : 'Unavailable'}</dd></div>
                    <div><dt>Capability source</dt><dd>${escapeHtml(model.evidence?.runtime?.capability_source || 'Server descriptor')}</dd></div>
                </dl>
            </details>
            <div class="control-plane-models__detail-actions">
                ${model.resident ? `<button type="button" class="ds-button" data-unload-model="${escapeHtml(model.key)}" data-variant="danger">Unload</button>` : `<button type="button" class="ds-button" data-load-model="${escapeHtml(model.key)}" data-variant="primary"${model.artifactState === 'missing' ? ' disabled' : ''}>Load</button>`}
                ${model.resident && state.residencyAvailable && model.residency ? `<button type="button" class="ds-button" data-pin-model="${escapeHtml(model.key)}" data-pin-next="${pinned ? 'false' : 'true'}">${pinned ? 'Unpin' : 'Pin'}</button>` : ''}
                ${model.resident && !model.isDefault ? `<button type="button" class="ds-button" data-set-default-model="${escapeHtml(model.key)}">Set default</button>` : ''}
                <button type="button" class="ds-button" disabled title="No route-preserving reload admin contract is currently exposed">Reload unavailable</button>
            </div>`;
    }


    const PLAN_TASKS = ['chat', 'structured_generation', 'vision_language', 'transcription', 'image_generation'];

    function planningTask(model) {
        const tasks = Array.isArray(model.capabilities?.tasks) ? model.capabilities.tasks : [];
        return tasks.includes('chat') ? 'chat' : tasks.find((task) => PLAN_TASKS.includes(task)) || null;
    }

    function addCapacityRow(list, name, description) {
        const row = document.createElement('div');
        const label = document.createElement('dt');
        const value = document.createElement('dd');
        label.textContent = name;
        value.textContent = description;
        row.append(label, value);
        list.appendChild(row);
    }

    function displayCapacityPreview(surface) {
        const host = surface.querySelector('[data-capacity-result]');
        const button = surface.querySelector('[data-check-capacity]');
        if (!host || !button) return;
        const model = latestState?.models.find((item) => item.key === selectedKey);
        const task = model && planningTask(model);
        const preview = capacityPreview?.key === selectedKey ? capacityPreview : null;
        button.disabled = !task || preview?.status === 'pending';
        button.textContent = preview?.status === 'pending' ? 'Checking…' : 'Check capacity';
        host.replaceChildren();

        const explain = document.createElement('p');
        if (!task) {
            explain.textContent = 'No declared task is available for planning this model.';
        } else if (!preview) {
            explain.textContent = 'Not checked. Request a read-only capacity snapshot.';
        } else if (preview.status === 'pending') {
            explain.textContent = 'Reading configured budget and host observations…';
        } else if (preview.status === 'error') {
            explain.textContent = preview.message;
        } else {
            const plan = preview.plan || {};
            const advisory = plan.capacity_advisory || {};
            const admission = advisory.admission_preview || {};
            const labels = {
                rejected_at_snapshot: 'Rejected at snapshot',
                not_rejected_at_snapshot: 'No admission block observed',
                not_applicable: 'No new residency planned',
                unverified: 'Unverified',
            };
            explain.textContent = (labels[admission.status] || 'Unverified')
                + ' · Checked at ' + new Date(preview.at).toLocaleTimeString()
                + ' · Snapshot only';
            explain.className = 'control-plane-models__capacity-state';
            host.appendChild(explain);

            const list = document.createElement('dl');
            list.className = 'control-plane-models__definition-list';
            addCapacityRow(list, 'Configured budget', (plan.decision || 'uncertain')
                + ' · ' + formatBytes(advisory.configured_remaining_after_bytes) + ' remaining after planned residency');
            addCapacityRow(list, 'Admission reason', admission.reason || 'Unavailable');
            const pressure = advisory.host?.pressure;
            addCapacityRow(list, 'Host pressure', (pressure?.value || 'unknown') + ' · ' + (pressure?.source || 'unavailable'));
            const mem = advisory.host?.available_bytes;
            addCapacityRow(list, 'Host RAM available', mem?.source === 'measured' ? formatBytes(mem.value) + ' · measured' : 'Unavailable');
            const disk = advisory.storage?.available_disk_bytes;
            addCapacityRow(list, 'Model-cache volume free', disk?.source === 'measured' ? formatBytes(disk.value) + ' · measured' : 'Unavailable');
            host.appendChild(list);
            const warning = document.createElement('p');
            warning.className = 'control-plane-models__evidence-note';
            warning.textContent = 'Not permission to load. Disk fit and peak memory remain unverified. Rerun after resource changes.';
            host.appendChild(warning);
            return;
        }
        host.appendChild(explain);
    }

    async function checkCapacity(surface, model) {
        const task = planningTask(model);
        if (!task) return;
        const version = ++capacityRequestVersion;
        capacityPreview = { key: model.key, status: 'pending' };
        displayCapacityPreview(surface);
        try {
            const plan = await fetchJson('/api/v1/applications/profiles/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    schema_version: 'korgis-application-profile-v1',
                    application_id: 'studio-capacity-check',
                    profile_id: 'single-model',
                    roles: { selected: { model: model.key, task, residency: 'warm' } },
                }),
            });
            if (!plan || typeof plan !== 'object' || !plan.capacity_advisory || typeof plan.capacity_advisory !== 'object') {
                throw new Error('Invalid capacity plan response');
            }
            if (version !== capacityRequestVersion || selectedKey !== model.key) return;
            capacityPreview = { key: model.key, status: 'ready', plan, at: Date.now() };
        } catch (error) {
            if (version !== capacityRequestVersion || selectedKey !== model.key) return;
            const message = error?.status === 404
                ? 'Capacity planning requires the local admin API, which is disabled or unavailable.'
                : error?.status === 422
                    ? 'The configured model cannot be planned with its current capability or resource metadata.'
                    : 'Capacity planning is unavailable. No runtime action was taken.';
            capacityPreview = { key: model.key, status: 'error', message };
        }
        if (latestState) render(surface, latestState);
        surface.querySelector('[data-check-capacity]')?.focus();
    }

    function bindModelActions(surface, state) {
        surface.querySelectorAll('[data-open-model]').forEach((button) => {
            button.addEventListener('click', () => {
                if (selectedKey !== button.dataset.openModel) {
                    capacityPreview = null;
                    capacityRequestVersion += 1;
                }
                selectedKey = button.dataset.openModel;
                render(surface, state);
                surface.querySelector('[data-model-detail]')?.scrollIntoView({ block: 'nearest' });
            });
        });

        surface.querySelectorAll('[data-check-capacity]').forEach((button) => {
            button.addEventListener('click', () => {
                const model = state.models.find((item) => item.key === selectedKey);
                if (model) checkCapacity(surface, model);
            });
        });

        surface.querySelectorAll('[data-load-model]').forEach((button) => {
            button.addEventListener('click', () => {
                const model = state.models.find((item) => item.key === button.dataset.loadModel);
                if (model) showLoadFeasibility(surface, state, model);
            });
        });

        surface.querySelectorAll('[data-set-default-model]').forEach((button) => {
            button.addEventListener('click', () => runAction(surface, button, {
                pending: `Setting ${button.dataset.setDefaultModel} as the default route…`,
                success: 'Default route updated.',
                request: () => fetchJson('/api/v1/models/activate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: button.dataset.setDefaultModel }),
                }),
            }));
        });

        surface.querySelectorAll('[data-unload-model]').forEach((button) => {
            button.addEventListener('click', () => {
                const key = button.dataset.unloadModel;
                if (!window.confirm(`Unload ${key}? Active runtimes are protected by the server and will fail closed.`)) return;
                runAction(surface, button, {
                    pending: `Unloading ${key}…`,
                    success: `${key} is cold.`,
                    request: () => unloadModel(key),
                });
            });
        });

        surface.querySelectorAll('[data-pin-model]').forEach((button) => {
            button.addEventListener('click', () => runAction(surface, button, {
                pending: `${button.dataset.pinNext === 'true' ? 'Pinning' : 'Unpinning'} ${button.dataset.pinModel}…`,
                success: 'Residency policy updated.',
                request: () => fetchJson('/api/v1/residency/pin', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: button.dataset.pinModel,
                        pinned: button.dataset.pinNext === 'true',
                    }),
                }),
            }));
        });
    }

    function showLoadFeasibility(surface, state, model) {
        const host = surface.querySelector('[data-load-feasibility-host]');
        if (!host) return;
        const remaining = finiteNumber(state.resource?.remaining_bytes);
        const estimate = model.estimateBytes;
        const deficit = estimate !== null && remaining !== null ? Math.max(estimate - remaining, 0) : null;
        const candidate = deficit !== null && deficit > 0 ? safeUnloadCandidate(state.models, model.key, deficit) : null;
        const canFit = deficit === 0;
        const unknown = estimate === null || remaining === null;

        host.innerHTML = `
            <div class="control-plane-models__modal-backdrop" data-load-modal-backdrop>
                <section class="ds-card control-plane-models__modal" role="dialog" aria-modal="true" aria-labelledby="load-feasibility-title">
                    <div class="control-plane-models__section-header">
                        <div>
                            <span class="control-plane-models__eyebrow">Load feasibility</span>
                            <h3 id="load-feasibility-title">Load ${escapeHtml(model.modelId)}?</h3>
                        </div>
                        <button type="button" class="ds-button" data-close-load-modal aria-label="Close load feasibility">Close</button>
                    </div>
                    <dl class="control-plane-models__definition-list">
                        <div><dt>Estimated requirement</dt><dd>${escapeHtml(formatBytes(estimate))}</dd></div>
                        <div><dt>Available budget</dt><dd>${escapeHtml(formatBytes(remaining))}</dd></div>
                        <div><dt>Additional capacity required</dt><dd>${deficit === null ? 'Unavailable' : escapeHtml(formatBytes(deficit))}</dd></div>
                    </dl>
                    <div class="ds-empty">
                        ${unknown
                            ? 'Feasibility cannot be predicted from current evidence. The server remains the admission authority and will fail closed if capacity is insufficient.'
                            : (canFit
                                ? 'The current accounting envelope indicates enough capacity for this estimated load.'
                                : (candidate
                                    ? `${escapeHtml(candidate.modelId)} is idle and evictable by policy. Unloading it would free an estimated ${escapeHtml(formatBytes(candidate.estimateBytes))}; this is not a physical-memory observation.`
                                    : 'Current estimates exceed available capacity. Unload an idle runtime explicitly or reduce the runtime configuration before loading.'))}
                    </div>
                    <div class="control-plane-actions">
                        ${candidate ? `<button type="button" class="ds-button" data-variant="primary" data-unload-then-load="${escapeHtml(candidate.key)}" data-target-model="${escapeHtml(model.key)}">Unload ${escapeHtml(candidate.modelId)} & continue</button>` : ''}
                        ${(canFit || unknown) ? `<button type="button" class="ds-button" data-variant="primary" data-confirm-load="${escapeHtml(model.key)}">${unknown ? 'Try load' : 'Load model'}</button>` : ''}
                        <button type="button" class="ds-button" data-close-load-modal>Cancel</button>
                    </div>
                </section>
            </div>`;

        const close = () => { host.innerHTML = ''; };
        host.querySelectorAll('[data-close-load-modal]').forEach((button) => button.addEventListener('click', close));
        host.querySelector('[data-load-modal-backdrop]')?.addEventListener('click', (event) => {
            if (event.target === event.currentTarget) close();
        });
        const firstAction = host.querySelector('[data-unload-then-load], [data-confirm-load], [data-close-load-modal]');
        firstAction?.focus();

        host.querySelector('[data-confirm-load]')?.addEventListener('click', async (event) => {
            await runAction(surface, event.currentTarget, {
                pending: `Loading ${model.key}…`,
                success: `${model.key} is resident.`,
                request: () => loadModel(model.key),
            });
            close();
        });

        host.querySelector('[data-unload-then-load]')?.addEventListener('click', async (event) => {
            const button = event.currentTarget;
            const source = button.dataset.unloadThenLoad;
            const target = button.dataset.targetModel;
            await runAction(surface, button, {
                pending: `Unloading ${source}, then loading ${target}…`,
                success: `${target} is resident.`,
                request: async () => {
                    await unloadModel(source);
                    return loadModel(target);
                },
            });
            close();
        });
    }

    function safeUnloadCandidate(models, targetKey, deficit) {
        return models
            .filter((model) => model.key !== targetKey
                && model.resident
                && !model.isDefault
                && model.residency?.evictable === true
                && finiteNumber(model.activeRequests) === 0
                && model.estimateBytes !== null
                && model.estimateBytes >= deficit)
            .sort((a, b) => (finiteNumber(b.residency?.last_used_age_seconds) || 0) - (finiteNumber(a.residency?.last_used_age_seconds) || 0))[0] || null;
    }

    function loadModel(key) {
        return fetchJson('/api/v1/models/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: key }),
        });
    }

    function unloadModel(key) {
        return fetchJson(`/api/v1/models/${encodeURIComponent(key)}`, { method: 'DELETE' });
    }

    async function runAction(surface, button, action) {
        const statusHost = surface.querySelector('[data-model-action-status]');
        button.disabled = true;
        if (statusHost) statusHost.textContent = action.pending;
        try {
            await action.request();
            if (statusHost) statusHost.textContent = action.success;
            refreshing = false;
            await refresh();
        } catch (error) {
            button.disabled = false;
            if (statusHost) statusHost.textContent = `Action failed: ${error?.message || 'unknown error'} Review the current runtime/resource state and try a safe alternative.`;
        }
    }

    function capabilitySummary(capabilities) {
        if (!capabilities || typeof capabilities !== 'object') return 'Unavailable';
        const tasks = Array.isArray(capabilities.tasks) ? capabilities.tasks : [];
        const inputs = Array.isArray(capabilities.input_modalities) ? capabilities.input_modalities : [];
        const outputs = Array.isArray(capabilities.output_modalities) ? capabilities.output_modalities : [];
        if (!tasks.length && !inputs.length && !outputs.length) return 'Unavailable';
        const taskPart = tasks.length ? tasks.join(', ') : 'task unavailable';
        const ioPart = `${inputs.length ? inputs.join(' + ') : 'input unavailable'} → ${outputs.length ? outputs.join(' + ') : 'output unavailable'}`;
        return `${taskPart} · ${ioPart}`;
    }

    function normalizeRuntimeState(value) {
        const text = String(value || '').toLowerCase();
        if (['resident', 'ready', 'running'].includes(text)) return 'Resident';
        if (text === 'loading') return 'Loading';
        if (text === 'draining') return 'Draining';
        if (text === 'failed' || text === 'error') return 'Failed';
        if (text === 'stopped') return 'Stopped';
        return value ? String(value) : 'Unavailable';
    }

    function finiteNumber(value) {
        if (value === null || value === undefined || value === '') return null;
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function percentage(value, total) {
        if (value === null || total === null || total <= 0) return null;
        return Math.max(0, Math.min(100, (value / total) * 100));
    }

    function formatBytes(value) {
        const number = finiteNumber(value);
        if (number === null) return 'Unavailable';
        if (number < 1024) return `${number} B`;
        const units = ['KiB', 'MiB', 'GiB', 'TiB'];
        let current = number;
        let index = -1;
        do {
            current /= 1024;
            index += 1;
        } while (current >= 1024 && index < units.length - 1);
        return `${current.toFixed(current >= 10 ? 1 : 2)} ${units[index]}`;
    }

    function formatDuration(seconds) {
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
        return `${(seconds / 3600).toFixed(1)}h`;
    }

    function shortFingerprint(value) {
        const text = String(value);
        return text.length > 16 ? `${text.slice(0, 8)}…${text.slice(-8)}` : text;
    }

    function statusBadge(label, status) {
        return `<span class="ds-status" data-status="${escapeHtml(status)}">${escapeHtml(label)}</span>`;
    }

    function escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function boot(attempt = 0) {
        if (!ensureSurface()) {
            if (attempt < 20) setTimeout(() => boot(attempt + 1), 50);
            return;
        }
        refresh();
        if (timer) clearInterval(timer);
        timer = setInterval(refresh, REFRESH_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => boot(), { once: true });
    } else {
        boot();
    }
})();
