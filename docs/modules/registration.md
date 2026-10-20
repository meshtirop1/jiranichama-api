# registration

Helps a chama register with its government. Nothing here touches money or the ledger.

- `guides.py`: each country's ways to register (office, law, steps, fee, papers), in
  English and Kiswahili, and the catalogue of papers with who makes each one. Edit the text
  here when a law or office changes; the app shows whatever this returns.
- `render.py`: fills the six papers JiraniChama prepares (ReportLab, like statements),
  from the chama's name, rules, members and officials. Blanks stay for ID numbers,
  signatures, the date and the place.
- `models.py`: `RegistrationProgress`, one row per chama: the chosen way, ticked items,
  certificate number and date.
- `views.py`: the guide (`/registration/guide/`), a chama's checklist
  (`/chamas/{id}/registration/`, officials may change it) and the PDFs
  (`/chamas/{id}/registration/papers/{paper}/`).

See [the flow](../flows/registration.md).
