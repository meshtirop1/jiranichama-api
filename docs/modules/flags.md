# flags

Switches for features that are unfinished or pilot-only.

A flag is **off unless turned on**: either `enabled_for_all`, or for the chamas listed in
`chama_allowlist`. Code asks `is_enabled("loans", chama.id)`. An unknown flag is off.

Flags are changed in the staff console, which asks for a reason and a ticket reference and
records the change in the audit log. Every new feature ships behind a flag that is off by
default and on only for the pilot allowlist.
