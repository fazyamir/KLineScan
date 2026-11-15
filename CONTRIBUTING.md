# Contributing to KLineScan

Thanks for helping! Contributions are very welcome, especially:

- verified vehicle captures, traces and DTC/live-data results;
- protocol fixes for K-line, CAN, UDS and KWP;
- new vehicle pack entries;
- UI, documentation and test improvements.

## Workflow

1. Fork the repository.
2. Create a branch with a descriptive name.
3. Make your change and run `pytest -q`.
4. Open a pull request against `main`.
5. The owner reviews every pull request before merging.

## Evidence

For protocol or vehicle changes, include a verified capture or trace. If you
could not verify the change on hardware, say **"untested"** explicitly in the
pull request. Synthetic fixtures belong in `tests/golden/` and must say that they
are not vehicle captures.

## Please do not submit

- manufacturer service-manual text or screenshots;
- entries copied from a paid database;
- firmware, ROM or container images;
- personal data (VINs, plates, owner details) without redaction.

## Code style

- Python 3.12, standard library plus the dependencies in `requirements.txt`.
- Keep hardware-independent logic testable; no network or serial access in unit
  tests.
- Prefer clear names and small functions. Add a test for every bug fix.
- Do not add comments that restate the code.
- Do not edit `klinescan/__version__.py` or `version.txt`; the version workflow
  bumps them on `main`.

## License

By contributing you agree that your contribution is licensed under the MIT
License (see `LICENSE`).
