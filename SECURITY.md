# Security Policy

## Reporting a vulnerability

If you find a security issue in this repo (a bug in the demo code that could leak a credential or let another site act through it, or a vulnerability in a dependency), report it privately.

Email: `security@moveworks.com`

Do not open a public GitHub issue for security reports.

## Scope

This repo is a demo chat surface for the Moveworks Conversations API. Bugs in its code that could leak an API key, let another website drive the local server, or otherwise compromise authentication are in scope. Issues in the `moveworks_capi` library belong to the [starter](https://github.com/moveworks/conversations-api-starter).

Vulnerabilities in the Moveworks Conversations API itself, or in your production integration, are handled through your Moveworks account team and are out of scope for this repo.

## Disclaimer

This repository is a demo, provided as-is under its license. It is not a supported Moveworks product, carries no SLA, and may trail changes to the Conversations API. Verify against the [official documentation](https://docs.moveworks.com/api-reference/conversations-api) before adopting anything from it.
