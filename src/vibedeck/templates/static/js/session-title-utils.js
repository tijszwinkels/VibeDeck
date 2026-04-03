export function normalizeCustomTitle(title) {
    if (title == null) {
        return null;
    }
    const normalized = String(title).replace(/\s+/g, ' ').trim();
    return normalized || null;
}

export function getEffectiveSessionTitle(session) {
    return normalizeCustomTitle(session?.customTitle)
        || normalizeCustomTitle(session?.summaryTitle)
        || normalizeCustomTitle(session?.firstMessage)
        || normalizeCustomTitle(session?.name)
        || 'Untitled';
}

export function buildDraftSessionTitle(session, draftTitle) {
    return {
        customTitle: normalizeCustomTitle(draftTitle),
        summaryTitle: session?.summaryTitle,
        firstMessage: session?.firstMessage,
        name: session?.name,
    };
}

export function getDisplayTitle(session, maxLength) {
    const title = getEffectiveSessionTitle(session);
    if (!maxLength || title.length <= maxLength) {
        return title;
    }
    return title.substring(0, maxLength) + '...';
}
