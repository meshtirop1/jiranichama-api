# Countries

JiraniChama started in Kenya. On 2026-10-20 the owner decided to serve nine countries:
Kenya, Uganda, Tanzania, Rwanda, Burundi, DR Congo, South Sudan, Ethiopia and Somalia. Each
country has its own language and its own law. The full plan, with the legal research for
each country, is in the owner's documents (`JiraniChama_Countries_Plan_2026-10-20.md`).

## Which countries are open

`apps/core/countries.py` lists all nine. Only **Kenya, Uganda and Tanzania** are open:
that is Wave 1, the owner's choice. The rest are listed with `open: false`, so the app can
show them as "Coming soon". Each has its own reason for waiting:

- **Rwanda, Ethiopia and DR Congo:** their data laws want personal data kept in the
  country, or a regulator's approval to keep it abroad.
- **Burundi:** the central bank's 2026 circular lets savings groups use digital tools only
  through a sponsor registered with the bank.
- **Somalia and South Sudan:** they wait for USD and Arabic support.

A phone from a country that is not open cannot sign in. A chama cannot be created there
either.

## Phone numbers

`normalise_phone(raw, country)` reads a number in the chosen country:

- A number written with its own country code (+256…, 256…) is read in that country,
  whatever was chosen.
- A local number (07…) is read in the chosen country. The default is Kenya, so a Kenyan
  number normalises exactly as before. Existing accounts keep the same phone hash, which
  means everyone is still found.

A new person's country is their phone's country. They can change it on their profile.

## Currency

Every amount is still stored as whole hundredths (`*_cents`), whatever the currency. So
the ledger, its hashes and all arithmetic are unchanged. Only how an amount is shown
depends on the currency:

- `apps/core/money.py` formats amounts: `fmt(cents, of)` takes the currency from a chama
  or anything that belongs to one.
- Uganda, Tanzania, Rwanda and Burundi do not use cents, so their amounts show as whole
  numbers ("UGX 28,000").
- A chama's `country` and `currency` are fixed when it is created. They come from the
  founder's country.
- SMS messages, the rule book (`render(rules, lang, currency)`), PDF statements and the
  texts people approve all show the chama's own currency.

New chamas start with amounts that make sense in their currency. `template_for(type,
currency)` scales the Kenyan starting figures by a rough rate and rounds them, so a
Ugandan chama starts at UGX 28,000 a month, not UGX 1,000. Officials change them during
setup anyway.

## Banks

Each open country has its own bank list (`countries.BANKS`):

- **Uganda:** Stanbic, Centenary, dfcu and others.
- **Tanzania:** CRDB, NMB, NBC and others.

A chama's account must be at one of its country's banks, or at "Other bank".

## Languages

People can choose any of 14 languages. Only English and Kiswahili have translations so
far, so server texts in any other language fall back to English. Those languages show as
"Coming soon" in the app until a native speaker has checked a translation. The rule book
matters most here, because it is the group's legal constitution.

## For the app

`GET /api/v1/countries/` is public. It lists the countries (code, name, dialling code,
currency, languages, banks, open or not) and the languages (marking which are translated),
for the sign-up pickers.

## Country rule packs (2026-10-21)

The law of the chama's country sits under its own rule book (`apps/rules/packs.py`).
Officials can be stricter, never weaker.

- **Blocked rule settings.** `validation.validate(rules, country)` refuses these when the
  rules are set during setup, at activation, or in a rule change:
  - Uganda: simple (flat) interest only, and no penalty that grows a loan on default.
  - Somalia: no loan interest; use a fixed service fee instead.
  - Burundi: loans repaid within 3 months.
- **Warnings, not blocks.** Ethiopia warns above 12% a year and South Sudan above 15%,
  because their legal basis is unconfirmed.
- **Group size:**
  - **Smallest:** a chama goes active only with at least our floor of 5 founding
    members, or the country's own minimum if higher: Uganda and Tanzania 10, Burundi 10.
  - **Largest:** joining past the country's maximum is refused (`chama_full`): Uganda
    30, Tanzania 50, Burundi 29.
  - `adoption-status` returns `min_members` and `max_members`.
- **Starting templates already follow the law.** `template_for(type, currency, country)`
  bends the loan settings when the chama is created.
- **Duties** are listed for every member at `GET /chamas/{id}/country-rules/` (en/sw),
  each marked done, to do, or a problem. Examples:
  - Uganda: two money counters, 60% women on a mixed committee, a yearly share-out,
    re-registering after half the members change.
  - Tanzania: registering, "Community Microfinance Group" in the name, a quarterly
    report, an internal-control person, credit-data consent.

  Registration duties show as done once the chama has entered its certificate number.

The findings are from the countries plan (public law and regulators' guidelines). They
are guidance, not legal advice.
