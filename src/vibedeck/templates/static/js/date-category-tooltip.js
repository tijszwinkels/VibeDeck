function toNumber(value) {
    const numeric = Number(value || 0);
    return Number.isFinite(numeric) ? numeric : 0;
}

function getSessionModelLabel(session) {
    if (!session) return null;
    if (session.model) return session.model;

    const models = session.tokenUsage && Array.isArray(session.tokenUsage.models)
        ? session.tokenUsage.models.filter(Boolean)
        : [];
    if (models.length === 1) return models[0];
    if (models.length > 1) return 'Multiple models';
    return null;
}

function sortEntries(entries) {
    entries.sort((a, b) => {
        if (b.cost !== a.cost) return b.cost - a.cost;
        return a.label.localeCompare(b.label);
    });
    return entries;
}

export function buildDateCategoryCostBreakdown(sessions) {
    const backendBuckets = new Map();
    const modelBuckets = new Map();
    const totals = {
        totalCost: 0,
        totalInput: 0,
        totalOutput: 0,
        totalCacheCreate: 0,
        totalCacheRead: 0,
    };

    sessions.forEach((session) => {
        const usage = session && session.tokenUsage ? session.tokenUsage : {};
        const cost = toNumber(usage.cost);

        totals.totalCost += cost;
        totals.totalInput += toNumber(usage.input_tokens);
        totals.totalOutput += toNumber(usage.output_tokens);
        totals.totalCacheCreate += toNumber(usage.cache_creation_tokens);
        totals.totalCacheRead += toNumber(usage.cache_read_tokens);

        const backendLabel = session && session.backend ? session.backend : 'Unknown';
        const backendBucket = backendBuckets.get(backendLabel) || {
            label: backendLabel,
            cost: 0,
            sessionCount: 0,
        };
        backendBucket.cost += cost;
        backendBucket.sessionCount += 1;
        backendBuckets.set(backendLabel, backendBucket);

        const modelLabel = getSessionModelLabel(session);
        if (!modelLabel) return;

        const modelBucket = modelBuckets.get(modelLabel) || {
            label: modelLabel,
            cost: 0,
            sessionCount: 0,
        };
        modelBucket.cost += cost;
        modelBucket.sessionCount += 1;
        modelBuckets.set(modelLabel, modelBucket);
    });

    return {
        ...totals,
        byBackend: sortEntries(Array.from(backendBuckets.values())),
        byModel: sortEntries(Array.from(modelBuckets.values())),
    };
}
