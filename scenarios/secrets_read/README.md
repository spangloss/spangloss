# secrets_read

Tests `sec.data_class` = `secrets` by content and by location, and credential access
as `retrieve` plus `secrets` (docs/scenarios-design.md, 6.1).

All data is synthetic. The provider "Tallow" is invented, and every secret value in
`fixtures/` is listed in `scenarios/FAKE_SECRETS.txt`. The fixture files don't say
they are synthetic, on purpose: the taxonomy labels data marked synthetic as `none`.
