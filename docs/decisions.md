# Teachify decisions

```mermaid
flowchart LR
  M[Math verdict] -->|authoritative| S[SymPy]
  S -->|safe parsed context| A[Language model]
  A -->|wording only| H[Hint]
```

## 2026 11 08

- Preserve SymPy as the source of truth for correctness. Anthropic may phrase hints but cannot change verdicts.
- Add voice as an enhancement, not a dependency. Missing provider keys must leave typed checking fully usable.
- Keep private credentials server-side. Expose only a provider-designated public Vapi key when required.
- Add `/worksheets` as a distinct judge-friendly route with two printable Letter pages and no answer key.
- Prefer Railway for the final container because the demo uses server-sent events and in-memory single-instance state.
- Use synthetic students only. Real student images, accounts, persistence, and district compliance remain out of scope for this hackathon demo.
