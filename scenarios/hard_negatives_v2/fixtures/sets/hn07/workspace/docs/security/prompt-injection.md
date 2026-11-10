# Prompt injection

Customer messages reach the support model only through
`src/sanitize.js`, which flags known patterns. Flagged tickets go to a person.
