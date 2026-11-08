# Teachify strict roast

```mermaid
flowchart LR
  B[Focused build] --> R[Roast]
  R --> F[Fix high and medium findings]
  F --> Q[Recheck]
```

| Severity | Area | Flaw | Evidence | Fix |
|---|---|---|---|---|
| High | Security | A browser voice integration could expose a private provider key | Voice providers require different public and private credentials | Added a server-only Deepgram grant route and a Vapi config route that returns only the designated public key and assistant ID |
| High | Logic | A language model could accidentally become the math judge | Existing product promise requires deterministic line checking | Kept SymPy authoritative and passed only its fixed verdict and safe parsed lines to Anthropic |
| Medium | Reliability | Missing voice or AI configuration could block the main demo | Judge environments often lack provider configuration | Added honest unavailable states and preserved typed checking plus verified catalog hints |
| Medium | Visual | The division glyph could resemble a plus at screenshot scale | First desktop render made the symbol ambiguous | Replaced it with the unambiguous slash form in mock and build |
| Medium | Cost | Repeated Deepgram token requests could amplify provider usage | The public demo has no account system | Added same-origin enforcement and a bounded token-request rate limit |
| Low | Dependency | The Vapi browser module is loaded from a CDN | Network loss can stop voice setup | Typed tutoring and browser dictation remain available |

Verdict: the focused patch is suitable to merge after review, but the complete public site should not ship until demo mutation access and the full friendly restyle are finished.

## Re-roast

All high and medium findings introduced by the focused worksheet, Anthropic, Deepgram, and Vapi work are fixed. The remaining public-demo access issue predates this patch and is assigned to the full rebuild before production deployment.
