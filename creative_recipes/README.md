# Creative Recipe Intelligence samples

This directory contains repository-managed static recipe catalogs for
pre-execution prompt planning.  The catalog loader accepts only the fixed
`catalog/` subtree and never executes, imports or downloads anything.  Add a
closed `*.creative-recipe-catalog.json` descriptor, validate it with the CLI,
and keep runtime/model/process configuration out of the contract.
