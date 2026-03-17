// script to inject into page
function tagInteractiveElements() {
    // 1. Remove existing tags if any
    document.querySelectorAll('.ai-browser-tag').forEach(el => el.remove());

    const interactiveSelectors = [
        'a', 'button', 'input', 'select', 'textarea', 
        '[role="button"]', '[role="link"]', '[role="checkbox"]', 
        '[role="menuitem"]', '[role="tab"]', '[tabindex]:not([tabindex="-1"])'
    ].join(', ');

    const elements = document.querySelectorAll(interactiveSelectors);
    const elementMap = {};
    let counter = 0;

    elements.forEach(el => {
        // Skip hidden elements
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0 || el.style.visibility === 'hidden' || el.style.display === 'none') {
            return;
        }
        
        // Skip elements outside viewport (mostly)
        if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) {
            return;
        }

        const id = counter++;
        elementMap[id] = {
            tagName: el.tagName.toLowerCase(),
            type: el.type || null,
            text: el.innerText?.trim() || el.value || el.placeholder || el.getAttribute('aria-label') || el.alt || '',
            role: el.getAttribute('role') || null
        };
        
        // Add a temporary attribute to the element so playright can find it
        el.setAttribute('data-ai-id', id);

        // Create the visible label
        const label = document.createElement('div');
        label.className = 'ai-browser-tag';
        label.textContent = id;
        
        // Style the label to sit exactly on top of the element
        Object.assign(label.style, {
            position: 'absolute',
            left: `${rect.left + window.scrollX}px`,
            top: `${rect.top + window.scrollY}px`,
            backgroundColor: 'yellow',
            color: 'black',
            border: '1px solid black',
            fontSize: '12px',
            fontWeight: 'bold',
            padding: '1px 3px',
            zIndex: '2147483647', // Max z-index
            pointerEvents: 'none' // Don't block clicks to the actual element
        });

        document.body.appendChild(label);
    });

    return elementMap;
}
tagInteractiveElements();
