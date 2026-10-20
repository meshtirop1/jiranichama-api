# Registering a chama with the government (2026-10-20)

The owner asked that JiraniChama make it easy for groups to register: explain each
country's law in detail, list the documents, and prepare them from what the app already
knows, reminding the group now and then until it is done.

## The guide

`GET /registration/guide/?country=KE&lang=sw` gives the guide for a country, even before
the person has a chama. `apps/registration/guides.py` holds it:

- **Kenya, Uganda and Tanzania** (open countries) have full guides. Each has three ways to
  register:
  1. **As a community group.** In Kenya this is the Department of Social Development (the
     Community Groups Registration Act, 2022 as it comes in). In Uganda it is the
     sub-county CDO and the district, under the NGO Act 2016 and the Tier 4 Act 2016. In
     Tanzania it is the council's Community Development Officer, under the Microfinance
     Act 2018 and the CMG Regulations 2019.
  2. **Opening the bank account,** with any-two-to-sign written into the resolution.
  3. **Growing into a SACCO or a company.**
- **The other six countries** have a short guide, marked as such.
- Each way lists:
  - the office and the law;
  - who it is for;
  - steps written for a member, not a lawyer;
  - the fee, how long it takes, and renewal;
  - the papers it needs.
- Fees are given as "about" and the guide says to confirm them at the office. It is
  guidance, not legal advice, and the app shows that line.

## The papers

`PAPERS` says who makes each one:

- **We prepare (`app`).** These are the constitution, founding minutes, register of
  members, list of officials, application letter and bank-account resolution.
  `GET /chamas/{id}/registration/papers/{paper}/` renders each one as a PDF from the
  chama's records:
  - the name, type and country;
  - the rules in force, or the draft (via the rule book);
  - members and officials.

  ID numbers, signatures, the date and the place are left blank. The app must not hold
  ID numbers, and it does not know where the group meets. Any member can download the
  papers; members already see each other's names.
- **You bring (`you`).** These are ID copies, photos, the local leader's letter, the tax
  number, the fee and, at the bank, the certificate.

## The checklist

`GET /chamas/{id}/registration/` returns:

- the guide for the chama's country;
- the chosen way (`route`);
- what has been ticked off and what is still missing;
- the certificate;
- `can_edit` (officials only);
- `remind`: true while the chama is active and has no certificate.

Officials `PATCH` it with:

- `route`, to choose a way;
- `tick` or `untick` with a paper id or `step:<route>:<n>`;
- `certificate_no` and `registered_on`.

Entering a certificate ticks it and stops the reminders. The chama's own JSON carries
`registered`, so the app knows without another request.

`RegistrationProgress` is a to-do list, not a money record, so it is updated in place.
Its foreign keys are PROTECT like every row that points at a chama.

## Profile: ID check status

`/auth/me/` now returns `kyc_status` (`not_started` until Smile ID is switched on) and
`date_joined`, for the app's Settings screen.
