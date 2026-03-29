const KITTY_SEQUENCE_PATTERN = /\x1b\[(?:>(\d+)u|<(\d*)u|\?u)/g;
const KITTY_SEQUENCE_PREFIX_AT_END = /\x1b\[(?:[><?]\d*)?$/;

export function createKittyKeyboardState() {
    return {
        stack: [],
        pendingSequence: '',
    };
}

export function resetKittyKeyboardState(state) {
    state.stack.length = 0;
    state.pendingSequence = '';
}

export function getKittyKeyboardFlags(state) {
    if (!state || state.stack.length === 0) {
        return 0;
    }
    return state.stack[state.stack.length - 1];
}

export function processKittyKeyboardProtocolOutput(state, data) {
    const input = `${state.pendingSequence}${data ?? ''}`;
    const pendingMatch = input.match(KITTY_SEQUENCE_PREFIX_AT_END);
    const pendingSequence = pendingMatch ? pendingMatch[0] : '';
    const completeInput = pendingSequence ? input.slice(0, -pendingSequence.length) : input;
    const responses = [];

    const output = completeInput.replace(KITTY_SEQUENCE_PATTERN, (_match, pushFlags, popCount) => {
        if (pushFlags !== undefined) {
            state.stack.push(Number.parseInt(pushFlags, 10) || 0);
            return '';
        }

        if (popCount !== undefined) {
            const count = Math.max(1, Number.parseInt(popCount || '1', 10) || 1);
            state.stack.splice(Math.max(0, state.stack.length - count), count);
            return '';
        }

        responses.push(`\u001b[?${getKittyKeyboardFlags(state)}u`);
        return '';
    });

    state.pendingSequence = pendingSequence;

    return { output, responses };
}

export function encodeKittyKeyEvent(event, kittyFlags) {
    if (!kittyFlags || !event) {
        return null;
    }

    let keyCode = null;
    if (event.key === 'Enter') {
        keyCode = 13;
    } else if (event.key === 'Escape') {
        keyCode = 27;
    } else {
        return null;
    }

    const modifier = 1
        + (event.shiftKey ? 1 : 0)
        + (event.altKey ? 2 : 0)
        + (event.ctrlKey ? 4 : 0)
        + (event.metaKey ? 8 : 0);

    return modifier === 1
        ? `\u001b[${keyCode}u`
        : `\u001b[${keyCode};${modifier}u`;
}

export function isTerminalKeyboardEventTarget(target) {
    if (!target) {
        return false;
    }

    if (target.classList?.contains('xterm-helper-textarea')) {
        return true;
    }

    return Boolean(
        target.closest?.('#terminal-container')
        || target.closest?.('.xterm')
    );
}
