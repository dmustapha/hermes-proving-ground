# PROVING GROUND

A wiring test for your Hermes plugin stack: known, harmless test calls go through every installed plugin in a throwaway child Hermes home with the model never called, and each cell shows which plugin blocked, rewrote, changed the result, gated for approval, or passed it through.

```bash
hermes provingground --fixtures
```

Runs the bundled demo plugins against the zero-plugin baseline and prints the matrix. Hermes v0.23.1 on macOS.

What it does on your machine, what it does not defend against, and the invariant tests: [SECURITY.md](SECURITY.md). Every claim and the command that checks it: [CLAIMS.md](CLAIMS.md). Limits: [LIMITATIONS.md](LIMITATIONS.md).
