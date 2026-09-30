# Spec Delta

## ADDED Requirements

### Requirement: Corrected misroutes can be promoted into the golden set
A command SHALL list the logged corrected misroutes that have a known intended intent and are neither in the golden set nor a router phrase, and SHALL add them only when asked, only to the DEV slice, leaving out any row the owner names. Listing SHALL change nothing, and the command SHALL remind the owner that the golden set is public.

#### Scenario: Listing
- **WHEN** the command is run without `--apply`
- **THEN** the candidate rows are printed and the golden set is unchanged

#### Scenario: Applying with a row left out
- **WHEN** the command is run with `--apply --skip 1`
- **THEN** every listed row except the first is appended to the DEV slice and the held-out slice is unchanged
