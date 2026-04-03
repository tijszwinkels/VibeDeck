import test from 'node:test';
import assert from 'node:assert/strict';

import {
    normalizeCustomTitle,
    getEffectiveSessionTitle,
    getDisplayTitle,
    buildDraftSessionTitle,
} from '../src/vibedeck/templates/static/js/session-title-utils.js';

test('normalizeCustomTitle trims whitespace and clears blank values', () => {
    assert.equal(normalizeCustomTitle('  Custom   Title  '), 'Custom Title');
    assert.equal(normalizeCustomTitle('   '), null);
    assert.equal(normalizeCustomTitle(null), null);
});

test('getEffectiveSessionTitle prefers custom title over summary and fallback values', () => {
    assert.equal(
        getEffectiveSessionTitle({
            customTitle: 'Pinned Title',
            summaryTitle: 'Generated Title',
            firstMessage: 'First message',
            name: 'session-1',
        }),
        'Pinned Title',
    );

    assert.equal(
        getEffectiveSessionTitle({
            customTitle: null,
            summaryTitle: 'Generated Title',
            firstMessage: 'First message',
            name: 'session-1',
        }),
        'Generated Title',
    );

    assert.equal(
        getEffectiveSessionTitle({
            customTitle: null,
            summaryTitle: null,
            firstMessage: 'First message',
            name: 'session-1',
        }),
        'First message',
    );
});

test('getDisplayTitle normalizes and truncates the effective title', () => {
    assert.equal(
        getDisplayTitle({
            customTitle: '  A renamed session title  ',
            summaryTitle: 'Generated',
            firstMessage: 'Ignored',
            name: 'session-1',
        }, 10),
        'A renamed ...',
    );
});

test('buildDraftSessionTitle overlays a draft custom title onto a session', () => {
    assert.deepEqual(
        buildDraftSessionTitle({
            customTitle: null,
            summaryTitle: 'Generated',
            firstMessage: 'Ignored',
            name: 'session-1',
        }, '  Draft title  '),
        {
            customTitle: 'Draft title',
            summaryTitle: 'Generated',
            firstMessage: 'Ignored',
            name: 'session-1',
        },
    );
});
