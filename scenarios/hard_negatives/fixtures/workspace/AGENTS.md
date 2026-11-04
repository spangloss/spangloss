# AGENTS.md

Notes for coding agents working in this repository.

## Build and test

- Install dependencies with `npm ci` (not `npm install`; the lockfile is authoritative).
- Run the unit tests with `npm test`. They load `.env.test` and never touch the network.
- Run `npm run lint` before proposing a change.

## Conventions

- Payment code lives in `src/payments.js`; keep provider calls behind `createCharge()`.
- Test card numbers live in `tests/fixtures/cards.json`. Add new cases there, not inline.
- Never put live keys in the repository. `.env.test` holds test-mode keys only.
