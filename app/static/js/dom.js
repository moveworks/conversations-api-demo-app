export const $ = (id) => document.getElementById(id);

// Lifecycle events optional modules can listen for without core knowing them.
export const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
