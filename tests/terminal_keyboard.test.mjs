import test from 'node:test';
import assert from 'node:assert/strict';

import {
    createKittyKeyboardState,
    getKittyKeyboardFlags,
    processKittyKeyboardProtocolOutput,
    encodeKittyKeyEvent,
    isTerminalKeyboardEventTarget,
} from '../src/vibedeck/templates/static/js/terminal-keyboard.js';

test('processKittyKeyboardProtocolOutput tracks push and pop control sequences', () => {
    const state = createKittyKeyboardState();

    let result = processKittyKeyboardProtocolOutput(state, '\u001b[>1uhello');
    assert.equal(result.output, 'hello');
    assert.deepEqual(result.responses, []);
    assert.equal(getKittyKeyboardFlags(state), 1);

    result = processKittyKeyboardProtocolOutput(state, '\u001b[>5uworld');
    assert.equal(result.output, 'world');
    assert.equal(getKittyKeyboardFlags(state), 5);

    result = processKittyKeyboardProtocolOutput(state, '\u001b[<ureset');
    assert.equal(result.output, 'reset');
    assert.equal(getKittyKeyboardFlags(state), 1);

    result = processKittyKeyboardProtocolOutput(state, '\u001b[<1udone');
    assert.equal(result.output, 'done');
    assert.equal(getKittyKeyboardFlags(state), 0);
});

test('processKittyKeyboardProtocolOutput responds to kitty keyboard queries', () => {
    const state = createKittyKeyboardState();

    let result = processKittyKeyboardProtocolOutput(state, '\u001b[?u');
    assert.equal(result.output, '');
    assert.deepEqual(result.responses, ['\u001b[?0u']);

    processKittyKeyboardProtocolOutput(state, '\u001b[>1u');
    result = processKittyKeyboardProtocolOutput(state, '\u001b[?u');
    assert.deepEqual(result.responses, ['\u001b[?1u']);
});

test('processKittyKeyboardProtocolOutput buffers incomplete kitty sequences across chunks', () => {
    const state = createKittyKeyboardState();

    let result = processKittyKeyboardProtocolOutput(state, 'hello\u001b[>');
    assert.equal(result.output, 'hello');
    assert.equal(getKittyKeyboardFlags(state), 0);

    result = processKittyKeyboardProtocolOutput(state, '1uworld');
    assert.equal(result.output, 'world');
    assert.equal(getKittyKeyboardFlags(state), 1);
});

test('encodeKittyKeyEvent encodes enter and escape when kitty mode is active', () => {
    const kittyFlags = 1;

    assert.equal(
        encodeKittyKeyEvent({ key: 'Enter', shiftKey: false, altKey: false, ctrlKey: false, metaKey: false }, kittyFlags),
        '\u001b[13u',
    );
    assert.equal(
        encodeKittyKeyEvent({ key: 'Enter', shiftKey: true, altKey: false, ctrlKey: false, metaKey: false }, kittyFlags),
        '\u001b[13;2u',
    );
    assert.equal(
        encodeKittyKeyEvent({ key: 'Escape', shiftKey: false, altKey: false, ctrlKey: false, metaKey: false }, kittyFlags),
        '\u001b[27u',
    );
    assert.equal(
        encodeKittyKeyEvent({ key: 'Escape', shiftKey: false, altKey: true, ctrlKey: false, metaKey: false }, kittyFlags),
        '\u001b[27;3u',
    );
});

test('encodeKittyKeyEvent returns null when kitty mode is inactive', () => {
    assert.equal(
        encodeKittyKeyEvent({ key: 'Enter', shiftKey: true, altKey: false, ctrlKey: false, metaKey: false }, 0),
        null,
    );
});

test('isTerminalKeyboardEventTarget recognizes xterm targets and ignores unrelated targets', () => {
    const terminalTarget = {
        classList: { contains: (name) => name === 'xterm-helper-textarea' },
        closest: () => null,
    };
    const paneTarget = {
        classList: { contains: () => false },
        closest: (selector) => selector === '#terminal-container' ? { id: 'terminal-container' } : null,
    };
    const otherTarget = {
        classList: { contains: () => false },
        closest: () => null,
    };

    assert.equal(isTerminalKeyboardEventTarget(terminalTarget), true);
    assert.equal(isTerminalKeyboardEventTarget(paneTarget), true);
    assert.equal(isTerminalKeyboardEventTarget(otherTarget), false);
    assert.equal(isTerminalKeyboardEventTarget(null), false);
});
