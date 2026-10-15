# Historical SeedEmu blockchain experiments

These sources were moved out of SeedEmu during repository separation. They
include unfinished examples and the earlier Layer 2 prototype. They are retained
for development reference and are not part of the canonical example acceptance
suite. Source syntax and removed-import checks do not establish runtime support.

Canonical examples live under `examples/`; reusable service implementations
live under `chainseed/`. New work should use these public interfaces.

File-level provenance and migration destinations are listed in
`docs/migration-inventory.json` at the repository root. Four historical files
larger than 10 MB (three copies of a custom Geth executable and one Layer 2
archive) are excluded from the new repository. The inventory records their
SHA-256 checksums, source Git commit and paths, intended destinations, and
local backup locations. Restore or rebuild these assets before attempting to
run the corresponding historical experiments.

Docker runtime validation is pending; this separation pass covers source
ownership, packaging, network dumps, and deployment-file compilation.
