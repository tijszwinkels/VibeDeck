import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildDateCategoryCostBreakdown,
} from '../src/vibedeck/templates/static/js/date-category-tooltip.js';

test('buildDateCategoryCostBreakdown aggregates total, backend, and model costs', () => {
    const breakdown = buildDateCategoryCostBreakdown([
        {
            backend: 'Codex',
            model: 'gpt-5.4',
            tokenUsage: { cost: 1.25, input_tokens: 1000, output_tokens: 200, cache_creation_tokens: 0, cache_read_tokens: 100 },
        },
        {
            backend: 'Codex',
            model: 'gpt-5.2-codex',
            tokenUsage: { cost: 0.75, input_tokens: 600, output_tokens: 180, cache_creation_tokens: 0, cache_read_tokens: 50 },
        },
        {
            backend: 'Claude Code',
            tokenUsage: {
                cost: 0.5,
                input_tokens: 400,
                output_tokens: 120,
                cache_creation_tokens: 25,
                cache_read_tokens: 10,
                models: ['claude-sonnet-4-5'],
            },
        },
    ]);

    assert.equal(breakdown.totalCost, 2.5);
    assert.deepEqual(
        breakdown.byBackend.map((entry) => [entry.label, entry.cost, entry.sessionCount]),
        [
            ['Codex', 2.0, 2],
            ['Claude Code', 0.5, 1],
        ],
    );
    assert.deepEqual(
        breakdown.byModel.map((entry) => [entry.label, entry.cost, entry.sessionCount]),
        [
            ['gpt-5.4', 1.25, 1],
            ['gpt-5.2-codex', 0.75, 1],
            ['claude-sonnet-4-5', 0.5, 1],
        ],
    );
});

test('buildDateCategoryCostBreakdown skips model buckets when a session has no model identity', () => {
    const breakdown = buildDateCategoryCostBreakdown([
        {
            backend: 'OpenCode',
            tokenUsage: { cost: 0.3, input_tokens: 100, output_tokens: 50, cache_creation_tokens: 0, cache_read_tokens: 0 },
        },
    ]);

    assert.equal(breakdown.totalCost, 0.3);
    assert.deepEqual(
        breakdown.byBackend.map((entry) => [entry.label, entry.cost, entry.sessionCount]),
        [['OpenCode', 0.3, 1]],
    );
    assert.deepEqual(breakdown.byModel, []);
});
