# identity

ID checks (KYC) through Smile ID, paid by the chama.

- `smile.py`: the Enhanced KYC call (off until `SMILE_ID_PARTNER_ID`/`SMILE_ID_API_KEY` are set).
- `services.py`: starting a check, the name match, one person one ID (keyed hash), rule P.3 (`require_officials_verified`, with `KYC_ENFORCED`).
- `models.py`: `IdentityCheck` (no ID number, no photo; last four digits and a keyed hash).

See [the flow](../flows/identity.md).
