// For plain text and quoted attribute values in our HTML templates.
function escapeHtml(value) {
    const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(value ?? '').replace(/[&<>"']/g, character => entities[character]);
}

// Notes are plain text. Only the links and line breaks we generate become HTML.
function linkify(inputText = '') {
    const text = String(inputText ?? '');
    const pattern = /\b(?:https?|ftp):\/\/[^\s<>"']+|\bwww\.[^\s<>"']+|[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\n/gi;
    let html = '', previousEnd = 0;

    text.replace(pattern, (match, offset) => {
        html += escapeHtml(text.slice(previousEnd, offset));
        previousEnd = offset + match.length;
        if (match === '\n') {
            html += '<br>';
            return match;
        }

        // Leave sentence punctuation outside links, but retain balanced URL parentheses.
        let label = match.replace(/[.,;:!?]+$/, '');
        while (label.endsWith(')') && (label.match(/\)/g) || []).length > (label.match(/\(/g) || []).length) {
            label = label.slice(0, -1);
        }
        const email = !/^(?:https?|ftp):\/\//i.test(label) && !/^www\./i.test(label);
        const href = email ? 'mailto:' + label : /^www\./i.test(label) ? 'http://' + label : label;
        const attributes = email ? '' : ' target="_blank" rel="noopener"';
        html += `<a href="${escapeHtml(href)}"${attributes}>${escapeHtml(label)}</a>`;
        html += escapeHtml(match.slice(label.length));
        return match;
    });

    return html + escapeHtml(text.slice(previousEnd));
}
